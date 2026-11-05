# coding: utf-8

import oci
from contextlib import closing
import os
from modules.utils import clear, green, yellow, red, print_error, print_info
from modules.navigation import read_choice, read_input, selection_indices
from modules.terminal import status_line, clear_status, plain_text, temporary_status
from modules.results import print_request_error, write_wrapped
from modules.execution import build_retry_strategy, client_options, region_tasks, cancellable_call
from modules.pager import PageBuffer, show_pages

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# set custom retry strategy
# - - - - - - - - - - - - - - - - - - - - - - - - - -
custom_retry_strategy = oci.retry.RetryStrategyBuilder(
                            max_attempts_check=True,
                            max_attempts=5,
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
    retry_cf=read_input("Do you want to specify another config file path ? [Y/N]: ").upper()
    print()
    
    if retry_cf in ("Y","YES"):
        config_file_path=read_input("What is the path of your OCI config file: ")
        print()
        config_profile=read_input("What is the profile section name to use in this config file: ")
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
    clear_status()
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
        status_line(' => Trying CloudShell authentication...')

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
        identity = oci.identity.IdentityClient(config=config, signer=signer, **client_options())
        tenancy = cancellable_call(identity.get_tenancy, config['tenancy'],
                                   retry_strategy=build_retry_strategy()).data

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
        status_line(' => Trying Config File authentication...')

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
        identity = oci.identity.IdentityClient(config=config, signer=signer, **client_options())
        tenancy = cancellable_call(identity.get_tenancy, config['tenancy'],
                                   retry_strategy=build_retry_strategy()).data

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
        status_line(' => Trying Instance Principals authentication...')

        # Initialize the signer using the instance principals token
        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner(retry_strategy=custom_retry_strategy)
        config = {'region': signer.region, 'tenancy': signer.tenancy_id}

        # Validate the config by trying to get the tenancy_name
        identity = oci.identity.IdentityClient(config=config, signer=signer, **client_options())
        tenancy = cancellable_call(identity.get_tenancy, config['tenancy'],
                                   retry_strategy=build_retry_strategy()).data

        return config, signer, tenancy, 'instance_principals', ''

    except Exception as e:
        authentication_errors['Instance_Principals_authentication'] = str(e).replace("\n", "")
        return None, None, None, None, None

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Check connectivity to OCI regions
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def region_connectivity_retry_strategy():
    """Original regional probe policy; independent of authentication settings."""
    return oci.retry.RetryStrategyBuilder(
        max_attempts_check=True,
        max_attempts=10,
        total_elapsed_time_check=True,
        total_elapsed_time_seconds=10,
        retry_max_wait_between_calls_seconds=2,
        retry_base_sleep_time_seconds=2,
    ).get_retry_strategy()


def check_region_connectivity(region, config, signer, custom_retry_strategy):

    """
    Check regional Identity access using the historical GetTenancy probe.
    """

    try:
        region_config = dict(config)
        region_config['region'] = region.region_name
        # This probe deliberately tests Identity GetTenancy only. Compute/AD
        # permissions are separate from the historical region-access criterion.
        identity = oci.identity.IdentityClient(
            config=region_config,
            signer=signer
        )

        cancellable_call(identity.get_tenancy,
            region_config['tenancy'],
            retry_strategy=custom_retry_strategy
        ).data

        return region, True, "OK"
        
    except oci.exceptions.ServiceError as e:
        error_message = f"{e.status} {e.code}: {e.message}".replace("\n", " ")
        request_id = getattr(e, "request_id", None)
        if request_id:
            error_message += f" | opc-request-id: {request_id}"
        return region, False, error_message
    except (oci.exceptions.RequestException, oci.exceptions.ClientError, ValueError, TimeoutError) as e:
        return region, False, str(e).replace("\n", " ")
    except Exception as e:
        return region, False, str(e).replace("\n", " ")

def validate_region_connectivity(regions, config, signer, fatal=True):

    """
    Validates the connectivity to multiple regions concurrently.
    """

    region_results = {}

    def check(region):
        # Keep the original region-probe policy independent of authentication
        # and capacity-call settings. A separate strategy belongs to each job.
        return check_region_connectivity(region, config, signer, region_connectivity_retry_strategy())

    with closing(region_tasks(
            regions, check, progress='Checking connectivity to region')) as tasks:
        for region, result, error in tasks:
            if error is not None:
                success, message = False, str(error)
            else:
                _, success, message = result
            region_results[region.region_name] = (success, message)
            if not success:
                print_info(red, 'Region', 'error', region.region_name)
                print_info(red, 'Region', 'status', region.status)
                print_info(red, 'Region', 'ignored', 'check domain replication')

    regions_validated = [
        region for region in regions
        if region_results.get(region.region_name, (False, None))[0]
    ]

    if regions_validated:
        return regions_validated
    else:
        region_errors = [
            f"{region.region_name} - {region.region_key} - {region.status}: "
            "Failed to connect"
            for region in regions
        ]
        print_error(
            "No available region found",
            *region_errors,
            "check domain replication"
            )
        if fatal:
            raise SystemExit(1)
        return []

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Check compartment state
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def check_compartment_state(identity_client, compartment_id):
    """Check compartment state without deciding whether the caller must exit."""
    compartment = identity_client.get_compartment(compartment_id).data
    if compartment.lifecycle_state != 'ACTIVE':
        raise ValueError(f'Compartment {compartment.name} is {compartment.lifecycle_state}')
    print_info(green, 'Compartment', 'analyzed', compartment.name)
    print_info(green, 'Compartment', 'state', compartment.lifecycle_state)

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# get compartment name
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def get_compartment_name(identity_client, compartment_id):
    """Retrieve a name; request errors remain recoverable by the caller."""
    return identity_client.get_compartment(compartment_id).data.name

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Set target compartment for capacity report query
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def inspect_compartment(identity_client, compartment_id, tenancy_id=None):
    """Confirm Identity read access and an active child, or the current root."""
    is_root = tenancy_id is not None and compartment_id == tenancy_id
    function = identity_client.get_tenancy if is_root else identity_client.get_compartment
    item = cancellable_call(function, compartment_id, retry_strategy=build_retry_strategy()).data
    if item is None:
        raise ValueError('OCI Identity returned no compartment information.')
    if not is_root and getattr(item, 'lifecycle_state', None) != 'ACTIVE':
        raise ValueError(f'Compartment {compartment_id} is not ACTIVE '
                         f"(state: {getattr(item, 'lifecycle_state', None) or 'not returned'}).")
    return item


def validate_compartment(identity_client, compartment_id, tenancy_id=None):

    """
    Validate if the compartment is active.
    """
    
    try:
        inspect_compartment(identity_client, compartment_id, tenancy_id)
        return compartment_id
    except ValueError as error:
        print(red(str(error)))
    except Exception as error:
        print_request_error(f'Cannot validate compartment {compartment_id}; '
                            'it may not exist or you may not have access', error)
    return None


def compartment_paths(identity_client, compartments, tenancy_id, debug=False):
    """Resolve ancestor names once per listing, retaining partial paths on denial."""
    cache = {item.id: item for item in compartments}
    paths = {}
    with temporary_status('Reading compartment paths...', quiet_redirect=True):
        for compartment in compartments:
            current = compartment.id
            seen, parts = set(), []
            while current:
                if current in seen:
                    parts.append(f'[ancestor cycle: {current}]')
                    break
                seen.add(current)
                if current not in cache:
                    try:
                        function = identity_client.get_tenancy if current == tenancy_id else identity_client.get_compartment
                        cache[current] = cancellable_call(function, current,
                                                          retry_strategy=build_retry_strategy()).data
                    except Exception as error:
                        cache[current] = None
                        if debug:
                            print_request_error(f'Compartment path / {current}', error, debug=True)
                item = cache[current]
                if item is None:
                    parts.append(f'[unavailable ancestor: {current}]')
                    break
                parts.append(getattr(item, 'name', None) or f'[name unavailable: {current}]')
                if current == tenancy_id:
                    break
                current = getattr(item, 'compartment_id', None)
                if not current:
                    parts.append('[ancestor path unavailable]')
            paths[compartment.id] = ' / '.join(reversed(parts))
    return paths


def resolve_compartment(identity_client, value, tenancy_id, *, interactive=True):
    """Resolve an exact compartment name or validate an explicit OCID."""
    value = value.strip()
    if value.startswith('ocid1.'):
        return validate_compartment(identity_client, value, tenancy_id)
    if not value:
        print(red('Enter a compartment name or OCID.'))
        return None
    try:
        with temporary_status('Searching OCI compartments...', quiet_redirect=True):
            matches = oci.pagination.list_call_get_all_results(
                lambda *args, **kwargs: cancellable_call(identity_client.list_compartments, *args, **kwargs),
                tenancy_id, name=value, compartment_id_in_subtree=True,
                access_level='ACCESSIBLE', lifecycle_state='ACTIVE',
                retry_strategy=build_retry_strategy()).data
        matches = sorted({item.id: item for item in matches if item.name == value}.values(),
                         key=lambda item: item.id)
    except Exception as error:
        print_request_error('Compartment name lookup failed; use an OCID instead', error)
        return None
    if not matches:
        print(red(f'No accessible active compartment found with the exact name {value} (case sensitive).'))
        return None
    if len(matches) == 1:
        selected = matches[0]
    else:
        paths = compartment_paths(identity_client, matches, tenancy_id)
        output = PageBuffer()
        output.write('Several compartments have this name. Select one:')
        for number, item in enumerate(matches, 1):
            write_wrapped(output, f'{number:02d}: {item.name} ({item.id})')
            write_wrapped(output, f'Path: {paths[item.id]}', prefix='    ')
        show_pages(output.lines)
        if not interactive:
            print(red('Use a compartment OCID to disambiguate this name.'))
            return None
        while True:
            choice = read_input('Compartment number ([B]ack, [Q]uit): ', back=True)
            if choice is None:
                return None
            try:
                index = selection_indices(choice, len(matches), multiple=False)[0]
            except ValueError:
                print(red('Choose a compartment number from the list.'))
                continue
            selected = matches[index]
            break
    compartment_id = validate_compartment(identity_client, selected.id, tenancy_id)
    if compartment_id:
        print_info(green, 'Compartment', 'selected', f'{selected.name} ({compartment_id})')
    return compartment_id


def set_user_compartment(identity_client, args, tenancy_id):

    """
    Honor an explicit compartment before -su. Retry invalid explicit targets
    without falling back to tenancy scope. Otherwise keep the admin question.
    """

    valid_inputs = {'Y', 'YES', 'N', 'NO', 'Q', 'QUIT'}

    # An explicit target takes precedence over the admin-prompt shortcut.
    if args.compartment:
        value = args.compartment
        while True:
            valid_compartment = resolve_compartment(identity_client, value, tenancy_id)
            if valid_compartment:
                return valid_compartment
            value = read_input('\nEnter another compartment name or OCID, or [Q]uit: ')

    if args.su:
        return tenancy_id

    # Interactive input for determining the compartment
    while True:
        user_input = read_input("Do you have Administrator rights at the tenancy level? [Y]es, [N]o, [Q]uit: ").upper()

        if user_input not in valid_inputs:
            print(red("\nInvalid input. Please enter 'Y', 'Yes', 'N', 'No', 'Q', or 'Quit'\n"))
            continue

        if user_input in {'Y', 'YES'}:
            print()
            return tenancy_id

        if user_input in {'N', 'NO'}:
            while True:
                user_compartment = read_input("\nEnter a compartment name or OCID to which you have access or [Q]uit: ")

                if user_compartment in {'q', 'quit'}:
                    raise SystemExit("\nQuitting the program as per user request.\n")

                valid_compartment = resolve_compartment(identity_client, user_compartment, tenancy_id)
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
        with temporary_status(' => Fetching home region...', quiet_redirect=True):
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
def select_region(identity_client, tenancy_id, current_region, config, signer, debug=False, single_only=False):
    """Choose one or more ready subscriptions, checking connectivity first."""
    try:
        regions = sorted(
            (region for region in identity_client.list_region_subscriptions(tenancy_id).data
             if region.status == 'READY'),
            key=lambda region: region.region_name
        )
    except Exception as e:
        print_request_error("Unable to list subscribed regions", e, debug=debug)
        return None

    if not regions:
        print_error("No ready subscribed region found.")
        return None

    clear()
    print(yellow("\nSubscribed regions:"))
    for index, region in enumerate(regions, start=1):
        current = " (current)" if region.region_name == current_region.region_name else ""
        print(plain_text(f"{index}: {region.region_name}{current}"))

    while True:
        prompt = ("\nSelect one region number, [B]ack, [H]ome or [Q]uit: " if single_only else
                  "\nSelect region number(s), e.g. 1,3,4, [B]ack, [H]ome or [Q]uit: ")
        choice = read_choice(prompt)
        if choice is None:
            return None
        try:
            indices = selection_indices(choice, len(regions), multiple=not single_only)
        except ValueError:
            print(red("Invalid input. Enter one region number." if single_only else
                      "Invalid input. Enter region numbers separated by commas."))
            continue

        selected = []
        for index in indices:
            region = regions[index]
            _, success, message = check_region_connectivity(region, config, signer,
                                                            region_connectivity_retry_strategy())
            if not success:
                print(red(f"Cannot connect to region {region.region_name}: Failed to connect"))
                if debug:
                    print(plain_text(message))
                continue
            print_info(green, 'Region', 'selected', region.region_name)
            selected.append(region)
        if not selected:
            continue
        return selected[0] if len(selected) == 1 else selected


def get_region_subscription_list(identity_client, tenancy_id, target_region):

    """
    Fetches the list of subscribed regions for a given tenancy.
    """

    try:
        with temporary_status(' => Loading regions...', quiet_redirect=True):
            subscribed_regions = identity_client.list_region_subscriptions(tenancy_id).data

        if ',' in target_region:
            names = list(dict.fromkeys(part.strip().lower() for part in target_region.split(',')))
            region_map = {region.region_name.lower(): region for region in subscribed_regions}
            if any(not name or name == 'all_regions' or name not in region_map for name in names):
                print_error('Region error:', 'Use comma-separated subscribed region names; all_regions must be used alone.')
                raise SystemExit(1)
            return [region_map[name] for name in names]

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
    """Retrieve AD names; let the report handle recoverable request failures."""
    availability_domains = oci.pagination.list_call_get_all_results(
        identity_client.list_availability_domains, tenancy_id,
        retry_strategy=build_retry_strategy()
    ).data
    return [ad.name for ad in availability_domains]


def get_fault_domains(identity_client, tenancy_id, availability_domain):
    """Retrieve FD names without exiting the interactive session on failure."""
    fault_domains = oci.pagination.list_call_get_all_results(
        identity_client.list_fault_domains, tenancy_id, availability_domain,
        retry_strategy=build_retry_strategy()
    ).data
    return [fd.name for fd in fault_domains]
