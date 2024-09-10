# coding: utf-8

import oci
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from modules.utils import clear, green, yellow, red, print_error, print_info

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# set custom retry strategy
# - - - - - - - - - - - - - - - - - - - - - - - - - -
custom_retry_strategy = oci.retry.RetryStrategyBuilder(
                            max_attempts_check=True,
                            max_attempts=3,
                            total_elapsed_time_check=True,
                            total_elapsed_time_seconds=10,
                            retry_max_wait_between_calls_seconds=5,
                            retry_base_sleep_time_seconds=2,
                            ).get_retry_strategy()


# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Manage OCI authentication
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def retry_auth():

    """
    This function attempts to reauthenticate using the specified config_file if all previous authentication methods have failed. 
    It prompts the user to provide a config_file custom path and a config_profile section.
    """
    print(yellow("\n-- All authentication methods have failed -- \n"))
    retry_cf=input(yellow("Do you want to specify another config file path ? [Y/N]")).strip().upper()
    print()
    
    if retry_cf in ("Y","YES"):
        config_file_path=input(yellow("What is the path of your OCI config file: ")).strip()
        print()
        config_profile=input(yellow("What is the profile section name to use in this config file: ")).strip()
        config, signer, tenancy_name, auth_name, details =init_authentication('cf', config_file_path, config_profile)

        if config:
            return config, signer, tenancy_name, auth_name, details
        else:
            raise SystemExit("Retrying config_file authentication failed. Please check your file and relaunch the process.")
    else:
        raise SystemExit("\nAll authentication methods have failed...\n")

def init_authentication(user_auth, config_file_path, config_profile):

    """
    Initializes authentication based on user preference or tries multiple methods.
    """
    authentication_errors = {}

    # Define the mapping of user_auth values to authentication functions and arguments
    auth_methods = {
        'cs': (authenticate_cloud_shell, []),
        'cf': (authenticate_config_file, [config_file_path, config_profile]),
        'ip': (authenticate_instance_principals, [])
    }

    # If user_auth is specified, limit the methods to the chosen one; otherwise, try all
    methods_to_try = [auth_methods[user_auth]] if user_auth else list(auth_methods.values())

    # Try each authentication method until one succeeds
    for auth_method, args in methods_to_try:
        config, signer, tenancy_name, auth_name, details = auth_method(authentication_errors, *args)

        if config:
            return config, signer, tenancy_name, auth_name, details

    # If all methods fail, print the errors and exit
    print("\r", end=' ' * 100 + '\r', flush=True)
    clear()
    for auth, error in authentication_errors.items():
        print_error(auth, error)
        print()
    
    config, signer, tenancy_name, auth_name, details= retry_auth()
    if config:
        return config, signer, tenancy_name, auth_name, details
    else:
        raise SystemExit(1)

def authenticate_cloud_shell(authentication_errors):

    """
    Attempts to authenticate using OCI CloudShell.
    Validate the config by trying to get the tenancy_name.
    """

    try:
        print(yellow("\r => Trying CloudShell authentication..."), end=' ' * 50 + '\r', flush=True)

        # Retrieve environment variables for OCI configuration
        env_config_file = os.environ.get('OCI_CONFIG_FILE')
        env_config_section = os.environ.get('OCI_CONFIG_PROFILE')

        if env_config_file is None or env_config_section is None:
            # Store error in authentication_errors if environment variables are not set
            authentication_errors['CloudShell_authentication'] = (
                f"Not a CloudShell session: $OCI_CONFIG_FILE={env_config_file}, $OCI_CONFIG_PROFILE={env_config_section}"
            )
            return None, None, None, None, None

        # Load OCI configuration from file
        config = oci.config.from_file(env_config_file, env_config_section)

        # Validate the loaded configuration
        oci.config.validate_config(config)

        # Read the delegation token from the specified file
        delegation_token_location = config.get('delegation_token_file')
        with open(delegation_token_location, 'r') as delegation_token_file:
            delegation_token = delegation_token_file.read().strip()

        # Initialize the signer using the delegation token
        signer = oci.auth.signers.InstancePrincipalsDelegationTokenSigner(delegation_token=delegation_token)

        # Validate the config by trying to get the tenancy_name
        identity = oci.identity.IdentityClient(config=config, signer=signer)
        tenancy = identity.get_tenancy(config['tenancy']).data

        return config, signer, tenancy, 'delegation_token', delegation_token_location

    except Exception as e:
        authentication_errors['CloudShell_authentication'] = str(e).replace("\n", "")
        return None, None, None, None, None

def authenticate_config_file(authentication_errors, config_file_path, config_profile):

    """
    Attempts to authenticate using OCI configuration file.
    Validate the config by trying to get the tenancy_name.
    """

    try:
        print(yellow(f"\r => Trying Config File authentication..."), end=' ' * 50 + '\r', flush=True)

        # Load OCI configuration from file
        config = oci.config.from_file(file_location=config_file_path, profile_name=config_profile)

        # Validate the loaded configuration
        oci.config.validate_config(config)

        # Initialize the signer using the config file
        signer = oci.signer.Signer(
            tenancy=config['tenancy'],
            user=config['user'],
            fingerprint=config['fingerprint'],
            private_key_file_location=config.get('key_file'),
            pass_phrase=oci.config.get_config_value_or_default(config, 'pass_phrase'),
            private_key_content=config.get('key_content')
        )

        # Validate the config by trying to get the tenancy_name
        identity = oci.identity.IdentityClient(config=config, signer=signer)
        tenancy = identity.get_tenancy(config['tenancy']).data

        return config, signer, tenancy, 'config_file', config_profile

    except Exception as e:
        authentication_errors['Config_File_authentication'] = str(e).replace("\n", "")
        return None, None, None, None, None

def authenticate_instance_principals(authentication_errors):

    """
    Attempts to authenticate using OCI instance principals.
    Validate the config by trying to get the tenancy_name.
    """

    try:
        print(yellow(f"\r => Trying Instance Principals authentication..."), end=' ' * 50 + '\r', flush=True)

        # Initialize the signer using the instance principals token
        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner(retry_strategy=custom_retry_strategy)
        config = {'region': signer.region, 'tenancy': signer.tenancy_id}

        # Validate the config by trying to get the tenancy_name
        identity = oci.identity.IdentityClient(config=config, signer=signer)
        tenancy = identity.get_tenancy(config['tenancy']).data

        return config, signer, tenancy, 'instance_principals', ''

    except Exception as e:
        authentication_errors['Instance_Principals_authentication'] = str(e).replace("\n", "")
        return None, None, None, None, None

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Check connectivity to OCI regions
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def check_region_connectivity(region, config, signer, custom_retry_strategy):

    """
    Checks the connectivity to regions and returns the region if successful.
    """

    try:
        config['region'] = region.region_name
        print(yellow(f"\r => Checking connectivity to region {region.region_name}..."),end=' '*50+'\r', flush=True)

        # Validate the connecivity by trying to get the tenancy_name
        identity = oci.identity.IdentityClient(config=config, signer=signer)
        identity.get_tenancy(config['tenancy'],retry_strategy=custom_retry_strategy).data

        return region, True
        
    except oci.exceptions.RequestException:
        return region, False

def validate_region_connectivity(regions, config, signer):

    """
    Validates the connectivity to multiple regions concurrently.
    """

    custom_retry_strategy = oci.retry.RetryStrategyBuilder(
        max_attempts_check=True,
        max_attempts=3,
        total_elapsed_time_check=True,
        total_elapsed_time_seconds=10,
        retry_max_wait_between_calls_seconds=2,
        retry_base_sleep_time_seconds=2,
    ).get_retry_strategy()

    regions_validated = []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        # Submit all region connectivity checks concurrently
        futures = {executor.submit(check_region_connectivity, region, config, signer, custom_retry_strategy): region for region in regions}

        # Process results as they complete
        for future in as_completed(futures):
            region, success = future.result()
            
            if success:
                regions_validated.append(region)
            else:
                #print(f"\r", end=' ' * 50, flush=True)
                print_info(red, 'Region', 'error', region.region_name)
                print_info(red, 'Region', 'status', region.status)
                print_info(red, 'Region', 'ignored', 'check domain replication')

    if regions_validated:
        return regions_validated
    else:
        print_error(
            "No available region found",
            f"{region.region_name} - {region.region_key} - {region.status}",
            "check domain replication"
            )
        raise SystemExit(1)

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Check compartment state
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def check_compartment_state(identity_client, compartment_id):

    """
    Check if a compartment is ACTIVE
    """

    try:
        # Retrieve compartment details from OCI using the provided compartment_id
        compartment = identity_client.get_compartment(compartment_id).data

        # Check if the compartment's lifecycle state is 'ACTIVE'
        if compartment.lifecycle_state == 'ACTIVE':
            # Print compartment information if it is active
            print_info(green, 'Compartment', 'analyzed', compartment.name)
            print_info(green, 'Compartment', 'state', compartment.lifecycle_state)
            return
        else:
            # Print an error message if the compartment is not active and exit the program
            print_error(
                "Compartment:", 
                compartment.name, 
                "is in an unexpected state:", 
                compartment.lifecycle_state
            )
            raise SystemExit(1)

    except oci.exceptions.ServiceError as e:
        # Catch any ServiceError exceptions raised by the OCI SDK
        # Print a detailed error message and exit the program
        print_error(
            "Compartment_id error:", 
            compartment_id, 
            e.code, 
            e.message
        )
        raise SystemExit(1)

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# get compartment name
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def get_compartment_name(identity_client, compartment_id):

    """
    retrieve compartment name from compartment ocid
    """

    try:
        compartment_name = identity_client.get_compartment(compartment_id).data.name
        return compartment_name

    except oci.exceptions.ServiceError as e:
        print_error("Compartment_id error:", compartment_id, e.code, e.message)
        raise SystemExit(1)
    
# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Set target compartment for capacity report query
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def validate_compartment(identity_client, compartment_id):

    """
    Validate if the compartment is active.
    """
    
    try:
        compartment = identity_client.get_compartment(compartment_id).data
        if compartment.lifecycle_state == 'ACTIVE':
            return compartment_id
        else:
            print(red(f"\nCompartment state error: {compartment.name} is {compartment.lifecycle_state}"))
    except oci.exceptions.ServiceError as e:
        print(red(f"\nCompartment error: {compartment_id} => {e.code} - {e.message}"))
    return None


def set_user_compartment(identity_client, args, tenancy_id):

    """
    Determines and returns the user compartment based on their administrative rights and input.
    Verifies the compartment provided by the user. If there is an error, 
    prompts the user to specify whether they have Administrator rights at the tenancy level. 
    Based on the user's response, the function either returns the tenancy ID, requests the user 
    to input their compartment OCID, or exits the application.
    """

    valid_inputs = {'Y', 'YES', 'N', 'NO', 'Q', 'QUIT'}

    # If super user mode is enabled, return the tenancy ID directly.
    if args.su:
        return tenancy_id

    # If a compartment is provided, validate it.
    if args.compartment:
        valid_compartment = validate_compartment(identity_client, args.compartment)
        if valid_compartment:
            return valid_compartment

    # Interactive input for determining the compartment
    while True:
        user_input = input(yellow("Do you have Administrator rights at the tenancy level? [Y]es, [N]o, [Q]uit: ")).strip().upper()

        if user_input not in valid_inputs:
            print(red("\nInvalid input. Please enter 'Y', 'Yes', 'N', 'No', 'Q', or 'Quit'\n"))
            continue

        if user_input in {'Y', 'YES'}:
            print()
            return tenancy_id

        if user_input in {'N', 'NO'}:
            while True:
                user_compartment = input(yellow("\nEnter a compartment OCID to which you have access or [Q]uit: ")).strip().lower()

                if user_compartment in {'q', 'quit'}:
                    raise SystemExit("\nQuitting the program as per user request.\n")

                valid_compartment = validate_compartment(identity_client, user_compartment)
                if valid_compartment:
                    print()
                    return valid_compartment

        if user_input in {'Q', 'QUIT'}:
            raise SystemExit("\nQuitting the program as per user request.\n")

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Get home region
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def get_home_region(identity_client, tenancy_id):

    """
    Fetches the the home region for the given tenancy.
    """

    try:
        print(yellow(f"\r => Fectching home region..."),end=' '*50+'\r', flush=True)
        subscribed_regions = identity_client.list_region_subscriptions(tenancy_id).data

        # Return home region
        home_region = next((region for region in subscribed_regions if region.is_home_region), None)            
        return home_region

    except oci.exceptions.ServiceError as e:
        print_error("Fetching home region:", e)
        raise SystemExit(1)
     
# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Get all subscribed region in the tenancy
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def get_region_subscription_list(identity_client, tenancy_id, target_region):

    """
    Fetches the list of subscribed regions for a given tenancy.
    """

    try:
        print(yellow(f"\r => Loading regions..."),end=' '*50+'\r', flush=True)
        subscribed_regions = identity_client.list_region_subscriptions(tenancy_id).data

        # Return home region only if no target region is specified
        if not target_region:
            home_region = next((region for region in subscribed_regions if region.is_home_region), None)            
            print_info(green, 'Region', 'analyzed', home_region.region_name)
            return [home_region]

        # return all subscribed regions if specified
        if target_region.lower() == "all_regions":
            print_info(green, 'Region', 'analyzed', "all subscribed regions")
            return subscribed_regions

        # create a dictionary mapping region names to region objects
        region_map = {region.region_name.lower(): region for region in subscribed_regions}

        # attempt to get the specified region
        region = region_map.get(target_region.lower())

        if region:
            print_info(green, 'Region', 'analyzed', target_region)
            return [region]  # return the specified subscribed region as a list

        # fetch all available OCI regions if target_region is not found in subscribed regions
        oci_regions = {region.name.lower() for region in identity_client.list_regions().data}

        if target_region.lower() in oci_regions:
            print_error("Region error:", f"{target_region} is not subscribed")
        else:
            print_error("Region error:", f"{target_region} does not exist")

        raise SystemExit(1)

    except oci.exceptions.ServiceError as e:
        print_error("Region error:", target_region, e)
        raise SystemExit(1)

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Get Availablity Domains in the region
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def get_availability_domains(identity_client, tenancy_id):

    """
    Fetches the list of availability domains for a given region.
    """
    
    try:
        # Initialize an empty list to store availability domain names
        oci_ads = []

        # Use OCI pagination to retrieve all availability domains for the specified compartment
        availability_domains = oci.pagination.list_call_get_all_results(
            identity_client.list_availability_domains,
            tenancy_id
        ).data

        # Extract the 'name' attribute of each availability domain and add it to the list
        for ad in availability_domains:
            oci_ads.append(ad.name)

    except oci.exceptions.ServiceError as e:
        print_error("Error in get_availability_domains:", e)
        raise SystemExit(1) 

    return oci_ads

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Get Fault Domains in an Availability Domain
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def get_fault_domains(identity_client, tenancy_id, availability_domain):

    """
    Fetches the list of fault domains for a given availability domain.
    """

    try:
        # Initialize an empty list to store fault domain names
        oci_fds=[]

        # Use OCI pagination to retrieve all fault domains for the specified availability domain
        fault_domains = oci.pagination.list_call_get_all_results(
            identity_client.list_fault_domains,
            tenancy_id,
            availability_domain
            ).data

        # Extract the 'name' attribute of each fault domain and add it to the list
        for fd in fault_domains:
            oci_fds.append(fd.name)
    
    except oci.exceptions.ServiceError as e:
        print_error("Error in get_fault_domains:", e)
        raise SystemExit(1)

    return oci_fds