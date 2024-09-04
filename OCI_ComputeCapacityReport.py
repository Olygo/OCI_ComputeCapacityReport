# coding: utf-8

# - - - - - - - - - - - - - - - - - - - - - - - - - - - -
# name: OCI_ComputeCapacityReport.py
#
# Author: Florian Bonneville
# Version: 2.0.1 - September 4, 2024
#
# Disclaimer: 
# This script is an independent tool developed by 
# Florian Bonneville and is not affiliated with or 
# supported by Oracle. It is provided as-is and without 
# any warranty or official endorsement from Oracle
#
# - - - - - - - - - - - - - - - - - - - - - - - - - - - -

import oci
import time
import os.path
import argparse
from modules.identity import *
from modules.utils import green, clear, print_info, format_duration
from modules.capacity import denseio_flex_shapes, process_region, set_denseio_shape_ocpus, set_user_shape_name

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
    
    parser.add_argument('-ocpus', default='', dest='ocpus',
                        help='Indicate a specific ocpus amount')
    
    parser.add_argument('-memory', default='', dest='memory',
                        help='Indicate a specific memory amount')
    
    return parser.parse_args()

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Load command line arguments
# - - - - - - - - - - - - - - - - - - - - - - - - - -
args=parse_arguments()

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Start script duration counter
# - - - - - - - - - - - - - - - - - - - - - - - - - -
analysis_start = time.perf_counter()

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Start print script info
# - - - - - - - - - - - - - - - - - - - - - - - - - -
script_path = os.path.abspath(__file__)
script_name = (os.path.basename(script_path))[:-3]
print(green(f"\n{'*'*94:94}"))
print_info(green, 'Analysis', 'started', script_name)

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Init OCI authentication
# - - - - - - - - - - - - - - - - - - - - - - - - - -
config, signer, tenancy_name = init_authentication(
     args.user_auth, 
     args.config_file_path, 
     args.config_profile
     )

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

# Prompt for a shape name if none is provided
if not user_shape_name:
   user_shape_name = set_user_shape_name(home_region, config, signer, user_compartment) 

# Specific request for DenseIO Flex shapes
if user_shape_name in denseio_flex_shapes:
    user_shape_ocpus = set_denseio_shape_ocpus(user_shape_name)
    print()

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Print output header
# - - - - - - - - - - - - - - - - - - - - - - - - - -
print(f"{'REGION':<20} {'AVAILABILITY_DOMAIN':<30} {'FAULT_DOMAIN':<20} {'SHAPE':<25} {'AVAILABILITY'}\n")

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Start analysis
# - - - - - - - - - - - - - - - - - - - - - - - - - -
for region in regions_validated:
        config['region']=region.region_name
        process_region(region, config, signer, user_compartment, user_shape_name, user_shape_ocpus, user_shape_memory)

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# End script duration counter
# - - - - - - - - - - - - - - - - - - - - - - - - - -
analysis_end = time.perf_counter()
execution_time = analysis_end - analysis_start
print(green(f"\nExecution time: {format_duration(execution_time)}\n"))