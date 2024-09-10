# coding: utf-8

import re
import oci
from modules.utils import yellow,red, print_error
from modules.identity import get_availability_domains, get_fault_domains, get_compartment_name
from modules.exceptions import RestartFlowException 

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Request user to set an oCPU value
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def set_user_shape_ocpus(user_shape_name):

    while True:
        try:
            user_shape_ocpus = input(yellow(f"\nAmount of OCPUs needed for shape {user_shape_name}: ")).strip()
            user_shape_ocpus = int(user_shape_ocpus)
            if user_shape_ocpus == 0:
                raise ValueError
            return user_shape_ocpus
        except ValueError:
            print(red("\nInvalid input. Please enter a valid value (integer)"))

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Request user to set a memory value
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def set_user_shape_memory(user_shape_name):

    while True:
        try:
            user_shape_memory = input(yellow(f"\nSpecify the amount of memory (in GB) needed for shape {user_shape_name}: ")).strip()
            user_shape_memory = int(user_shape_memory)
            return user_shape_memory
        except ValueError:
            print(red("\nInvalid input. Please enter a valid value (integer)."))

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Print the list of available compute shapes
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def print_shape_list(home_region, config, signer, compartment_id):

    """
    Update and return the list of compute shapes.
    The following list was updated on September 9th.
    This list is automatically refreshed when a new shape is detected in the connected region.
    """

    all_shapes = [
    'BM.DenseIO1.36', 'BM.DenseIO2.52', 'BM.DenseIO.E4.128', 'BM.DenseIO.E5.128', 'BM.GPU2.2', 'BM.GPU3.8', 'BM.GPU4.8', 'BM.GPU.A10.4', 
    'BM.GPU.A100-v2.8', 'BM.GPU.H100.8', 'BM.GPU.L40S.4', 'BM.HPC2.36', 'BM.HPC.E5.144', 'BM.Optimized3.36', 'BM.Standard1.36', 'BM.Standard2.52', 
    'BM.Standard3.64', 'BM.Standard.A1.160', 'BM.Standard.B1.44', 'BM.Standard.E2.64', 'BM.Standard.E3.128', 'BM.Standard.E4.128', 'BM.Standard.E5.192', 
    'VM.DenseIO1.16', 'VM.DenseIO1.4', 'VM.DenseIO1.8', 'VM.DenseIO2.16', 'VM.DenseIO2.24', 'VM.DenseIO2.8', 'VM.DenseIO.E4.Flex', 'VM.DenseIO.E5.Flex', 
    'VM.GPU2.1', 'VM.GPU3.1', 'VM.GPU3.2', 'VM.GPU3.4', 'VM.GPU.A10.1', 'VM.GPU.A10.2', 'VM.Optimized3.Flex', 'VM.Standard1.1', 'VM.Standard1.16', 
    'VM.Standard1.2', 'VM.Standard1.4', 'VM.Standard1.8', 'VM.Standard2.1', 'VM.Standard2.16', 'VM.Standard2.2', 'VM.Standard2.24', 'VM.Standard2.4', 
    'VM.Standard2.8', 'VM.Standard3.Flex', 'VM.Standard.A1.Flex', 'VM.Standard.A2.Flex', 'VM.Standard.B1.1', 'VM.Standard.B1.16', 'VM.Standard.B1.2', 
    'VM.Standard.B1.4', 'VM.Standard.B1.8', 'VM.Standard.E2.1', 'VM.Standard.E2.1.Micro', 'VM.Standard.E2.2', 'VM.Standard.E2.4', 'VM.Standard.E2.8', 
    'VM.Standard.E3.Flex', 'VM.Standard.E4.Flex', 'VM.Standard.E5.Flex'
    ]

    try:

        config['region']=home_region.region_name
        core_client = oci.core.ComputeClient(config=config, signer=signer)
    
        # Fetch and sort available shapes in the region
        shapes_in_home_region = core_client.list_shapes(compartment_id).data

        # Update all_shapes list if new shapes found
        for shape in shapes_in_home_region:
            if shape.shape not in all_shapes:
                all_shapes.append(shape.shape)
        
        all_shapes = sorted(all_shapes)

        print(yellow("\nGet all available shapes at: https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm\n"))

        # Set the maximum column width based on the longest shape name
        max_length = max(len(shape) for shape in all_shapes)
        column_width = max_length + 4

        # Print shape names in 6 columns
        for i in range(0, len(all_shapes), 6):
            row_items = all_shapes[i:i + 6]
            print("".join(f"{shape:<{column_width}}" for shape in row_items))

        return all_shapes

    except Exception as e:
        print_error(e)
        raise SystemExit(1)

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Define target shape name to analyze 
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def set_user_shape_name(home_region, config, signer, compartment_id):

    if not hasattr(set_user_shape_name, "first_execution"):
        set_user_shape_name.first_execution = True
        user_input = input(yellow("\nEnter the name of a shape to discover its regional capacity or [Q]uit: ")).strip()

        if user_input in {'Q', 'QUIT', 'q', 'quit'}:
            raise SystemExit("\nQuitting the program as per user request.\n")
        else:
            return user_input
    else:
        user_input = input(yellow("\nEnter another shape name, [P]rint shapes list or [Q]uit: ")).strip()

        if user_input in {'P', 'PRINT', 'p', 'print'}:
            print_shape_list(home_region, config, signer, compartment_id)
            user_input = input(yellow("\nEnter a shape name or [Q]uit: ")).strip()

            if user_input in {'Q', 'QUIT', 'q', 'quit'}:
                raise SystemExit("\nQuitting the program as per user request.\n")
            else:
                return user_input

        elif user_input in {'Q', 'QUIT', 'q', 'quit'}:
            raise SystemExit("\nQuitting the program as per user request.\n")
        else:
            return user_input

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Define the shape config for each DenseIO Flex shapes
# - - - - - - - - - - - - - - - - - - - - - - - - - -
denseio_flex_shapes = {
    "VM.DenseIO.E4.Flex": ["8", "16", "32"],
    "VM.DenseIO.E5.Flex": ["8", "16", "24", "32", "40", "48"]
}

def set_denseio_shape_ocpus(shape_name):

    """
    Prompts to select the number of oCPUs for the given shape name.
    Validates the input to ensure it is one of the proposed values.
    """

    # Get the valid oCPU choices for the specified shape name
    allowed_ocpus = denseio_flex_shapes.get(shape_name)
    
    while True:
        user_shape_ocpus = input(yellow(f"Select the amount of OCPUs {allowed_ocpus}: ")).strip()
        
        # Check if the user's input is one of the valid choices
        if user_shape_ocpus in allowed_ocpus:
            return user_shape_ocpus
        else:
            print(red(f"Invalid input. Please select one of the following values: {allowed_ocpus}"))

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Fetch available shapes and availability domains
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def fetch_shapes_and_domains(core_client, identity_client, compartment_id):

    """
    Fetches the availability domains and compute shapes for a given compartment_id.
    """
    try:
        availability_domains = get_availability_domains(identity_client, compartment_id)
        shapes_in_region = core_client.list_shapes(compartment_id).data
        return availability_domains, shapes_in_region
    except Exception as e:
        print_error(e)
        raise SystemExit(1)
    
# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Determine OCPU/memory configuration for a shape
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def get_shape_config(user_shape_name, shapes_in_region, user_shape_ocpus, user_shape_memory):

    """
    Retrieves the configuration for a specified shape based on user inputs and available shapes in a region.
    """

    # Check if the given shape name corresponds to a Flex shape.
    shape_is_flex = ".Flex" in user_shape_name

    # Check if the given shape name corresponds to a DenseIO Flex shape.
    DenseIOflex_pattern = r"^VM\.DenseIO\..{2}\.Flex$"
    if re.search(DenseIOflex_pattern, user_shape_name):
        shape_is_DenseIOflex = True
    else:
        shape_is_DenseIOflex = False

    # Reset values for multiple runs
    shape_ocpus = shape_memory = None

    # shape_is_DenseIOflex: Shape-Specific Configuration
    if shape_is_DenseIOflex:
        shape_ocpus = user_shape_ocpus
        shape_memory = ''
        shape_info = ''
        return shape_ocpus, shape_memory, shape_is_flex, shape_info
    
    # A2.Flex: Shape-Specific configuration
    elif user_shape_name == "VM.Standard.A2.Flex":
        shape_info = next((shape for shape in shapes_in_region if shape.shape == user_shape_name), None)

        # Ensure the ocpu is at least 1 and doesn't exceed max allowed OCPU
        user_shape_ocpus = min(user_shape_ocpus, shape_info.ocpu_options.max)

        # Ensure the memory is at least 1 GB and at least twice the oCPU amount
        user_shape_memory = max(1, user_shape_ocpus * 2, user_shape_memory)

        # Ensure the memory doesn't exceed max allowed per OCPU and total max
        user_shape_memory = min(user_shape_memory, user_shape_ocpus * shape_info.memory_options.max_per_ocpu_in_gbs)
        user_shape_memory = min(user_shape_memory, shape_info.memory_options.max_in_g_bs)

    # Flex: Shape-Specific configuration
    elif shape_is_flex:
        shape_info = next((shape for shape in shapes_in_region if shape.shape == user_shape_name), None)

        # Ensure user_shape_ocpus doesn't exceed the max limit
        user_shape_ocpus = min(user_shape_ocpus, shape_info.ocpu_options.max)

        # Apply the memory constraints
        user_shape_memory = max(1, user_shape_memory)  # Minimum memory should be 1
        user_shape_memory = max(user_shape_ocpus, user_shape_memory)  # Minimum memory should be at least amount of oCPU
        user_shape_memory = min(user_shape_memory, user_shape_ocpus * shape_info.memory_options.max_per_ocpu_in_gbs)  # Max memory based on OCPU limit
        user_shape_memory = min(user_shape_memory, shape_info.memory_options.max_in_g_bs)  # Max memory based on total max memory

    # Configuration of all other compute shapes
    else:
        shape_info = next((shape for shape in shapes_in_region if shape.shape == user_shape_name), None)

    return user_shape_ocpus, user_shape_memory, shape_is_flex, shape_info

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Process region by fetching data and creating report
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def process_region(region, config, signer, compartment_id, user_shape_name, user_shape_ocpus, user_shape_memory):

    """
    Processes the specified region by fetching and configuring compute shape data, 
    then generates and prints reports for each availability domain and fault domain.
    """

    config['region'] = region.region_name
    identity_client = oci.identity.IdentityClient(config=config, signer=signer)
    core_client = oci.core.ComputeClient(config=config, signer=signer)

    # Fetch shapes and availabitity domains
    availability_domains, shapes_in_region = fetch_shapes_and_domains(core_client, identity_client, config['tenancy'])
    
    # Retrieve shape configuration
    shape_ocpus, shape_memory, shape_is_flex, shape_info = get_shape_config(user_shape_name, shapes_in_region, user_shape_ocpus, user_shape_memory)

    # Process each availability domain and fault domain
    for availability_domain in availability_domains:
        fault_domains = get_fault_domains(identity_client, config['tenancy'], availability_domain)

        for fault_domain in fault_domains:
            create_and_print_report(
                region.region_name,
                identity_client,
                core_client,
                availability_domain,
                fault_domain,
                compartment_id,
                shape_info,
                user_shape_name,
                shape_ocpus,
                shape_memory,
                shape_is_flex
            )

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Create OCI compute shape report
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def create_and_print_report(region, identity_client, core_client, availability_domain, fault_domain, compartment_id, shape_info, shape_name, shape_ocpus=None, shape_memory=None, shape_is_flex=False):

    """
    Creates a compute capacity report for a specific region and shape, and prints the results.
    """
    
    # Predefined DenseIO flexible shape configurations
    DENSEIO_SHAPE_CONFIGS = {
        "VM.DenseIO.E4.Flex": {
            8.0: {"ocpus": 8.0, "memory_in_gbs": 128.0, "nvmes": 1.0},
            16.0: {"ocpus": 16.0, "memory_in_gbs": 256.0, "nvmes": 2.0},
            32.0: {"ocpus": 32.0, "memory_in_gbs": 512.0, "nvmes": 4.0}
        },
        "VM.DenseIO.E5.Flex": {
            8.0: {"ocpus": 8.0, "memory_in_gbs": 96.0, "nvmes": 1.0},
            16.0: {"ocpus": 16.0, "memory_in_gbs": 192.0, "nvmes": 2.0},
            24.0: {"ocpus": 24.0, "memory_in_gbs": 288.0, "nvmes": 3.0},
            32.0: {"ocpus": 32.0, "memory_in_gbs": 384.0, "nvmes": 4.0},
            40.0: {"ocpus": 40.0, "memory_in_gbs": 480.0, "nvmes": 5.0},
            48.0: {"ocpus": 48.0, "memory_in_gbs": 576.0, "nvmes": 6.0}
        }
    }

    def get_denseio_shape_config(shape_name, shape_ocpus):
        """Retrieve the shape configuration for DenseIO shapes."""
        if shape_name in DENSEIO_SHAPE_CONFIGS and shape_ocpus in DENSEIO_SHAPE_CONFIGS[shape_name]:
            return DENSEIO_SHAPE_CONFIGS[shape_name][shape_ocpus]
        return None

    def get_default_shape_values(shape_info):
        """If not provided, attempt to retrieve the default OCPU and memory values from the shape_info.."""
        return {
            'ocpus': shape_ocpus if shape_ocpus else shape_info.ocpus if shape_info and shape_info.ocpus else '-',
            'memory_in_gbs': shape_memory if shape_memory else shape_info.memory_in_gbs if shape_info and shape_info.memory_in_gbs else '-'
        }

    # Step 1: Initialize instance shape config based on shape type
    instance_shape_config = None

    if shape_name.startswith('BM.'):
        # Bare Metal shape, use provided or default values
        shape_defaults = get_default_shape_values(shape_info)
        shape_ocpus, shape_memory = shape_defaults['ocpus'], shape_defaults['memory_in_gbs']
    
    elif shape_is_flex:
        # Flexible shape, retrieve config from predefined values
        config = get_denseio_shape_config(shape_name, shape_ocpus)
        if config:
            instance_shape_config = oci.core.models.CapacityReportInstanceShapeConfig(
                ocpus=config["ocpus"],
                memory_in_gbs=config["memory_in_gbs"],
                nvmes=config["nvmes"]
            )
            # Update values for printing
            shape_ocpus, shape_memory = config["ocpus"], config["memory_in_gbs"]
        else:
            # Use provided values if no match in predefined configs
            instance_shape_config = oci.core.models.CapacityReportInstanceShapeConfig(
                ocpus=float(shape_ocpus),
                memory_in_gbs=float(shape_memory)
            )
    else:
        # Other shape type, use provided or default values
        shape_defaults = get_default_shape_values(shape_info)
        shape_ocpus, shape_memory = shape_defaults['ocpus'], shape_defaults['memory_in_gbs']

    # Step 2: Create compute capacity report
    report_details = oci.core.models.CreateComputeCapacityReportDetails(
        compartment_id=compartment_id,
        availability_domain=availability_domain,
        shape_availabilities=[
            oci.core.models.CreateCapacityReportShapeAvailabilityDetails(
                instance_shape=shape_name,
                fault_domain=fault_domain,
                instance_shape_config=instance_shape_config
            )
        ]
    )

    try:
        report = core_client.create_compute_capacity_report(create_compute_capacity_report_details=report_details)

        # Step 3: Print capacity results
        for result in report.data.shape_availabilities:
            print(f"{region:<20} {availability_domain:<30} {fault_domain:<20} {shape_name:<25} {shape_ocpus:<10} {shape_memory:<10} {result.availability_status}")

    except oci.exceptions.ServiceError as e:
        if "Authorization failed" in e.message:
            compartment_name = get_compartment_name(identity_client, compartment_id)
            print_error(
                e.message,
                f"Please verify that you have the appropriate access to {compartment_name}",
                "You can restart the script without Admin rights",
                "or, use the '-compartment' argument."
                )
            raise SystemExit(1)
        else:
            raise RestartFlowException
