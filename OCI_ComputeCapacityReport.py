import oci
import os.path
import os
import argparse
from modules.identity import create_signer, get_region_subscription_list, get_compartment_name, get_availability_domains, get_fault_domains
from modules.utils import clear, print_error, green, print_info

script_path = os.path.abspath(__file__)
script_name = (os.path.basename(script_path))[:-3]

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# clear shell screen
# - - - - - - - - - - - - - - - - - - - - - - - - - -
clear()

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# print header
# - - - - - - - - - - - - - - - - - - - - - - - - - -
print(green(f"\n{'*'*94:94}"))
print_info(green, 'Analysis', 'started', script_name)

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# get command line arguments
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def parse_arguments():
    parser = argparse.ArgumentParser()

    parser.add_argument('-cs', action='store_true', default=False, dest='is_delegation_token',
                        help='Use CloudShell Delegation Token for authentication')
    parser.add_argument('-cf', action='store_true', default=False, dest='is_config_file',
                        help='Use local OCI config file for authentication')
    parser.add_argument('-cfp', default='~/.oci/config', dest='config_file_path',
                        help='Path to your OCI config file, default: ~/.oci/config')
    parser.add_argument('-cp', default='DEFAULT', dest='config_profile',
                        help='Config file section to use, default: DEFAULT')
    parser.add_argument('-rg', default='', dest='target_region',
                        help='Define regions to analyze, default is all regions')
    parser.add_argument('-tcl', default='', dest='target_comp',
                        help='Define compartment to use')
    parser.add_argument('-shape', default='', dest='shape',required=True,
                        help='Specify shape name')
            
    return parser.parse_args()

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# retrieve arguments
# - - - - - - - - - - - - - - - - - - - - - - - - - -
cmd = parse_arguments()

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# create OCI shape report
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def create_and_print_report(core_client, compartment_id, availability_domain, fault_domain, shape, is_flex=False):

    # Create report details based on whether it's a flex shape or not
    report_details = oci.core.models.CreateComputeCapacityReportDetails(
        compartment_id=compartment_id,
        availability_domain=availability_domain,
        shape_availabilities=[
            oci.core.models.CreateCapacityReportShapeAvailabilityDetails(
                instance_shape=shape,
                fault_domain=fault_domain,
                instance_shape_config=oci.core.models.CapacityReportInstanceShapeConfig(
                    ocpus=1.0,
                    memory_in_gbs=1.0) if is_flex else None)])

    # Create and print the report
    try:
        report = core_client.create_compute_capacity_report(create_compute_capacity_report_details=report_details)
    except oci.exceptions.ServiceError as e:
        print_error("error:", cmd.shape, e.message)
        print_error("please check shape names:", "https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm")
        raise SystemExit(1)
    
    for result in report.data.shape_availabilities:
        print(f"{region.region_name:<20} {oci_ad:<30} {oci_fd:<20} {cmd.shape:<25} {result.availability_status}")

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# oci authentication
# - - - - - - - - - - - - - - - - - - - - - - - - - -

config, signer, oci_tname=create_signer(cmd.config_file_path, 
                                        cmd.config_profile, 
                                        cmd.is_delegation_token, 
                                        cmd.is_config_file)

tenancy_id=config['tenancy']

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# init oci service clients
# - - - - - - - - - - - - - - - - - - - - - - - - - -

identity_client=oci.identity.IdentityClient(
                config=config, 
                signer=signer)

obj_storage_client=oci.object_storage.ObjectStorageClient(
                    config=config, 
                    signer=signer)

namespace=obj_storage_client.get_namespace().data

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# set target regions
# - - - - - - - - - - - - - - - - - - - - - - - - - -

my_regions=get_region_subscription_list(
                                        identity_client, 
                                        tenancy_id, 
                                        cmd.target_region)

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# set target compartments
# - - - - - - - - - - - - - - - - - - - - - - - - - -

compartment_id = cmd.target_comp or tenancy_id
compartment_level = 'child' if cmd.target_comp else 'root'
compartment_name = get_compartment_name(identity_client, compartment_id)

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# print header
# - - - - - - - - - - - - - - - - - - - - - - - - - -
print_info(green, 'Shape', 'analyzed', cmd.shape)
print(green(f"{'*'*94:94}\n"))
print(f"{'REGION':<20} {'AVAILABILITY_DOMAIN':<30} {'FAULT_DOMAIN':<20} {'SHAPE':<25} {'AVAILABILITY'}\n")

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# start analysis
# - - - - - - - - - - - - - - - - - - - - - - - - - -

for region in my_regions:

    config['region'] = region.region_name

    identity_client = oci.identity.IdentityClient(config=config, signer=signer)
    core_client = oci.core.ComputeClient(config=config, signer=signer)

    oci_ads = get_availability_domains(identity_client, compartment_id)

    for oci_ad in oci_ads:
        oci_fds = get_fault_domains(identity_client, compartment_id, oci_ad)

        for oci_fd in oci_fds:
            is_flex = "Flex" in cmd.shape
            create_and_print_report(core_client, compartment_id, oci_ad, oci_fd, cmd.shape, is_flex=is_flex)

print()