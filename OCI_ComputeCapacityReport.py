# coding: utf-8

# - - - - - - - - - - - - - - - - - - - - - - - - - - - -
# name: OCI_ComputeCapacityReport.py
#
# Author: Florian Bonneville
# Version: 3.0.0 - September 10, 2024
#
# Disclaimer: 
# This script is an independent tool developed by 
# Florian Bonneville and is not affiliated with or 
# supported by Oracle. It is provided as-is and without 
# any warranty or official endorsement from Oracle
#
# - - - - - - - - - - - - - - - - - - - - - - - - - - - -
version = '3.0.0'

import oci
import os.path
import argparse
from modules.utils import green, clear, print_info
from modules.exceptions import RestartFlowException 
from modules.identity import init_authentication, get_region_subscription_list, validate_region_connectivity, get_home_region, set_user_compartment
from modules.capacity import denseio_flex_shapes, process_region, set_denseio_shape_ocpus, set_user_shape_name, set_user_shape_ocpus, set_user_shape_memory, print_shape_list

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Clear shell screen
# - - - - - - - - - - - - - - - - - - - - - - - - - -
clear()

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Get command line arguments
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def parse_arguments():
    parser = argparse.ArgumentParser()

    parser.add_argument('-auth', default='', dest='user_auth',
                         help='Force an authentication method : cs (cloudshell), cf (config file), ip (instance principals')
    
    parser.add_argument('-config_file', default='~/.oci/config', dest='config_file_path',
                        help='Path to your OCI config file, default: ~/.oci/config')
    
    parser.add_argument('-profile', default='DEFAULT', dest='config_profile',
                        help='Config file section to use, default: DEFAULT')

    parser.add_argument('-su', action='store_true',default=False, dest='su',
                        help='Notify the script that you have tenancy-level admin rights to prevent prompting.')

    parser.add_argument('-comp', default='', dest='compartment',
                        help='Filter on a compartment when you do not have Admin rights at the tenancy level')
    
    parser.add_argument('-region', default='', dest='target_region',
                        help='Region name to analyze, e.g. "eu-frankfurt-1" or "all_regions", default is home region')
 
    parser.add_argument('-shape', default='', dest='shape',
                        help='shape name to search')
    
    parser.add_argument('-ocpus', type=int, dest='ocpus',
                        help='Indicate a specific ocpus amount')
    
    parser.add_argument('-memory', type=int, dest='memory',
                        help='Indicate a specific memory amount')
    
    return parser.parse_args()

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Load command line arguments
# - - - - - - - - - - - - - - - - - - - - - - - - - -
args=parse_arguments()

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Init OCI authentication
# - - - - - - - - - - - - - - - - - - - - - - - - - -
config, signer, tenancy, auth_name, details = init_authentication(
     args.user_auth, 
     args.config_file_path, 
     args.config_profile
     )

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Clear shell screen in case of authentication errors
# - - - - - - - - - - - - - - - - - - - - - - - - - -
clear()

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Start print script info
# - - - - - - - - - - - - - - - - - - - - - - - - - -
script_path = os.path.abspath(__file__)
script_name = (os.path.basename(script_path))[:-3]
script_version = version
print(green(f"\n{'*'*94:94}"))
print_info(green, 'Script', 'started', script_name)
print_info(green, 'Script', 'version', script_version)
print_info(green, 'Login', 'success', auth_name)
print_info(green, 'Login', 'profile', details)
print_info(green, 'Tenancy', tenancy.name, f'home region: {tenancy.home_region_key}')

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Init oci service client
# - - - - - - - - - - - - - - - - - - - - - - - - - -
identity_client=oci.identity.IdentityClient(
     config=config, 
     signer=signer)

tenancy_id=config['tenancy']

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Set target regions
# - - - - - - - - - - - - - - - - - - - - - - - - - -
regions_to_analyze=get_region_subscription_list(
     identity_client,
     tenancy_id,
     args.target_region
     )
regions_validated=validate_region_connectivity(
     regions_to_analyze,
     config,
     signer
     )
home_region=get_home_region(
     identity_client, 
     tenancy_id
     )

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# End print script info
# - - - - - - - - - - - - - - - - - - - - - - - - - -
if args.shape:
    print_info(green, 'Shape', 'analyzed', args.shape)
if args.ocpus:
    print_info(green, 'oCPUs', 'amount', f'{args.ocpus} cores')
if args.memory:
    print_info(green, 'Memory', 'amount', f'{args.memory} gbs')

print(green(f"{'*'*94:94}\n"))

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Set report variables
# - - - - - - - - - - - - - - - - - - - - - - - - - -
user_compartment = set_user_compartment(identity_client, args, tenancy_id)
user_shape_name = args.shape
user_shape_ocpus = args.ocpus
user_shape_memory = args.memory

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Start analysis
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def main(regions_validated, config, signer, user_compartment, user_shape_name, user_shape_ocpus, user_shape_memory):

    """
    Function to initialize and start analysis based on the user shape configuration.
    """

    # If the user shape is not provided, prompt and set it.
    if not user_shape_name:
        if not hasattr(main, "first_execution"):
            main.first_execution = True
            # Print available shapes in the tenancy's home region
            print_shape_list(home_region, config, signer, user_compartment)
        user_shape_name = set_user_shape_name(home_region, config, signer, user_compartment)
            
    # Check if the shape is a DenseIO Flex shape
    if user_shape_name in denseio_flex_shapes:
        user_shape_ocpus = float(set_denseio_shape_ocpus(user_shape_name))
        print()

    # For shapes that are not Flex or Bare Metal unset ocpus and memory
    elif ".Flex" not in user_shape_name or user_shape_name.startswith('BM.'):
        user_shape_ocpus = 0
        user_shape_memory = 0

    # Default will be Flex shapes, set the OCPUs and memory, based on user input or defaults
    else:
        user_shape_ocpus = set_user_shape_ocpus(user_shape_name) if not args.ocpus else args.ocpus
        user_shape_memory = set_user_shape_memory(user_shape_name) if not args.memory else args.memory

    # Print header
    print(f"\n{'REGION':<20} {'AVAILABILITY_DOMAIN':<30} {'FAULT_DOMAIN':<20} {'SHAPE':<25} {'OCPU':10} {'MEMORY':<10} {'AVAILABILITY'}\n")

    for region in regions_validated:
            config['region']=region.region_name
            process_region(region, config, signer, user_compartment, user_shape_name, user_shape_ocpus, user_shape_memory)

# Start a loop to keep the script running until the user decides to quit
while True:
    try:
        main(
            regions_validated, 
            config, 
            signer, 
            user_compartment, 
            user_shape_name, 
            user_shape_ocpus, 
            user_shape_memory, 
        )
        # Reset user shape parameters for the next iteration
        user_shape_ocpus = user_shape_memory = user_shape_name = 0
    except RestartFlowException:
        # Restart the "run" function if an error occurs in another function (e.g., when user submits an invalid shape).
        pass