# coding: utf-8

import oci
from modules.utils import yellow, print_error
from modules.identity import get_availability_domains, get_fault_domains

def set_user_shape_name(home_region, config, signer, tenancy_id):

    """
    Prompts the user to select a compute shape from a list of available shapes.

    This function fetches all available compute shapes for a given home region.
    It displays the shapes in a formatted table, then prompts the user to select one.

    Args:
        home_region : The home region from which we update the shape list.
        config (dict): OCI configuration dictionary
        signer (object): OCI signer used for authenticating API requests.
        tenancy_id (str): The OCID of the tenancy.

    Returns:
        The compute shape name selected by the user.
    """

    print(yellow("Get all available shapes at: https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm\n"))

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
    
    config['region']=home_region.region_name
    core_client = oci.core.ComputeClient(config=config, signer=signer)
    
    # Fetch and sort available shapes in the region
    shapes_in_home_region = core_client.list_shapes(tenancy_id).data

    # Update all_shapes list if new shapes found
    for shape in shapes_in_home_region:
        if shape.shape not in all_shapes:
            all_shapes.append(shape.shape)
    
    all_shapes = sorted(all_shapes)

    # Set the maximum column width based on the longest shape name
    max_length = max(len(shape) for shape in all_shapes)
    column_width = max_length + 4

    # Print shape names in 6 columns
    for i in range(0, len(all_shapes), 6):
        row_items = all_shapes[i:i + 6]
        print("".join(f"{shape:<{column_width}}" for shape in row_items))
        #print()

    # Prompt for compute shape input
    user_shape_name = input(yellow("\nEnter the compute shape name you want to use: ")).strip()
    print()

    return user_shape_name

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Define the oCPU amount for each DenseIO Flex shapes
# - - - - - - - - - - - - - - - - - - - - - - - - - -
denseio_flex_shapes = {
    "VM.DenseIO.E4.Flex": ["8", "16", "32"],
    "VM.DenseIO.E5.Flex": ["8", "16", "24", "32", "40", "48"]
}

def set_denseio_shape_ocpus(shape_name):

    """
    Prompts to select the number of oCPUs for the given shape name.
    Validates the input to ensure it is one of the proposed values.
    
    Arg:
        shape_name: The name of the shape for which oCPU options are provided.
    
    Returns:
        user_shape_ocpus: The valid oCPU choice selected by the user.
    """

    # Get the valid oCPU choices for the specified shape name
    allowed_ocpus = denseio_flex_shapes.get(shape_name)
    
    while True:
        user_shape_ocpus = input(f"Select the number of oCPUs {allowed_ocpus}: ").strip()
        
        # Check if the user's input is one of the valid choices
        if user_shape_ocpus in allowed_ocpus:
            return user_shape_ocpus
        else:
            print(f"Invalid input. Please select one of the following values: {allowed_ocpus}")

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Fetch shapes and availability domains
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def fetch_shapes_and_domains(core_client, identity_client, tenancy_id):

    """
    Fetches the availability domains and compute shapes for a given tenancy_id.

    Args:
        core_client: An instance of the OCI ComputeClient used to list shapes.
        identity_client: An instance of the OCI IdentityClient used to retrieve availability domains.
        tenancy_id: The OCID of the tenancy for which to retrieve the information.

    Returns:
        tuple: A tuple containing two elements:
            - availability_domains (list): A list of availability domains in the specified tenancy.
            - shapes_in_region (list): A list of compute shapes available in the specified tenancy's region.
    """

    availability_domains = get_availability_domains(identity_client, tenancy_id)
    shapes_in_region = core_client.list_shapes(tenancy_id).data

    return availability_domains, shapes_in_region

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Determine OCPU/memory configuration for a shape
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def get_shape_config(user_shape_name, shapes_in_region, user_shape_ocpus, user_shape_memory):

    """
    Retrieves the configuration for a specified shape based on user inputs and available shapes in a region.

    Args:
        user_shape_name (str): The name of the shape as specified by the user.
        shapes_in_region (list): A list of shape objects available in the region. Each shape object should have `shape`, `ocpus`, and `memory_in_gbs` attributes.
        user_shape_ocpus (float or None): The number of OCPUs specified by the user for the shape. If None, a default value will be used.
        user_shape_memory (float or None): The amount of memory in GBs specified by the user for the shape. If None, a default value will be used.

    Returns:
        tuple: A tuple containing:
            shape_ocpus (float): The number of OCPUs for the shape, determined from user input or default values.
            shape_memory (float): The amount of memory (in GBs) for the shape, determined from user input or default values.
            shape_is_flex (bool): True if the shape is a flexible shape (contains ".Flex" in its name), otherwise False.

    Notes:
        If the specified shape is a flexible shape, the function will attempt to use user-specified values if provided, or default values if not.
        If the specified shape is not found in `shapes_in_region`, default values of 1.0 OCPUs and 2.0 GBs memory are used.
    """

    shape_is_flex = ".Flex" in user_shape_name
    shape_ocpus = shape_memory = None

    if shape_is_flex:
        shape_info = next((shape for shape in shapes_in_region if shape.shape == user_shape_name), None)
        if shape_info:
            shape_ocpus = user_shape_ocpus if user_shape_ocpus else shape_info.ocpus
            shape_memory = user_shape_memory if user_shape_memory else shape_info.memory_in_gbs
        else:
            # Use default values when the shape is not available in the region
            shape_ocpus = user_shape_ocpus if user_shape_ocpus else 1.0
            shape_memory = user_shape_memory if user_shape_memory else 2.0

    return shape_ocpus, shape_memory, shape_is_flex

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Process region by fetching data and creating report
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def process_region(region, config, signer, tenancy_id, user_shape_name, user_shape_ocpus, user_shape_memory):

    """
    Processes the specified region by fetching and configuring compute shape data, 
    then generates and prints reports for each availability domain and fault domain.

    Args:
        region (object): The region to check.
        config (dict): The OCI config dictionary.
        signer (object): The OCI signer object.
        tenancy_id (str): The OCID of the tenancy where the resources are located.
        user_shape_name (str): The name of the shape to retrieve configuration details for.
        user_shape_ocpus (float): The number of OCPUs for the user-specified shape.
        user_shape_memory (float): The amount of memory in GB for the user-specified shape.

    Returns:
        None

    This function performs the following steps:
        1. Updates the configuration dictionary with the region's name.
        2. Initializes OCI identity and compute clients using the provided configuration and signer.
        3. Fetches availability domains and shapes available in the specified region.
        4. Retrieves the configuration details for the specified shape, including OCPUs, memory, and whether it is flexible.
        5. Iterates over each availability domain and its fault domains to generate and print a report.
    """

    config['region'] = region.region_name

    identity_client = oci.identity.IdentityClient(config=config, signer=signer)
    core_client = oci.core.ComputeClient(config=config, signer=signer)

    # Fetch shapes and availabitity domains
    availability_domains, shapes_in_region = fetch_shapes_and_domains(core_client, identity_client, config['tenancy'])
    
    # Retrieve shape configuration
    shape_ocpus, shape_memory, shape_is_flex = get_shape_config(user_shape_name, shapes_in_region, user_shape_ocpus, user_shape_memory)
    
    # Process each availability domain and fault domain
    for availability_domain in availability_domains:
        fault_domains = get_fault_domains(identity_client, config['tenancy'], availability_domain)

        for fault_domain in fault_domains:
            create_and_print_report(
                region.region_name,
                core_client,
                availability_domain,
                fault_domain,
                tenancy_id,
                user_shape_name,
                shape_ocpus,
                shape_memory,
                shape_is_flex
            )

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Create OCI compute shape report
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def create_and_print_report(region, core_client, availability_domain, fault_domain, tenancy_id, shape_name, shape_ocpus, shape_memory, shape_is_flex):
    
    """
    Creates a compute capacity report for a specific region and shape, and prints the results.

    Args:
        region (str): The region for which the report is generated.
        core_client: An instance of the OCI ComputeClient used to list shapes.
        availability_domain (str): The availability domain to be included in the report.
        fault_domain (str): The fault domain to be included in the report.
        tenancy_id (str): The OCID of the tenancy for the report.
        all_shapes (list): List of available shapes.
        shape_name (str): The name of the instance shape to report on.
        shape_ocpus (float): Number of OCPUs for the instance shape (relevant for flex shapes).
        shape_memory (float): Amount of memory in GBs for the instance shape (relevant for flex shapes).
        shape_is_flex (bool): Indicates if the shape is flexible (can be configured with different OCPUs and memory).
    
    Output:
        print shape availability per fault domains and availability domains in the region
    """

    # Initialize instance shape configuration if the shape is flexible
    # Define the shape configurations using nested dictionaries
    DenseIO_shape_configs = {
        "VM.DenseIO.E4.Flex": {
            "8": {"ocpus": 8.0, "memory_in_gbs": 128.0, "nvmes": 1.0},
            "16": {"ocpus": 16.0, "memory_in_gbs": 256.0, "nvmes": 2.0},
            "32": {"ocpus": 32.0, "memory_in_gbs": 512.0, "nvmes": 4.0}
        },
        "VM.DenseIO.E5.Flex": {
            "8": {"ocpus": 8.0, "memory_in_gbs": 96.0, "nvmes": 1.0},
            "16": {"ocpus": 16.0, "memory_in_gbs": 192.0, "nvmes": 2.0},
            "24": {"ocpus": 24.0, "memory_in_gbs": 288.0, "nvmes": 3.0},
            "32": {"ocpus": 32.0, "memory_in_gbs": 384.0, "nvmes": 4.0},
            "40": {"ocpus": 40.0, "memory_in_gbs": 480.0, "nvmes": 5.0},
            "48": {"ocpus": 48.0, "memory_in_gbs": 576.0, "nvmes": 6.0}
        }
    }

    instance_shape_config = None

    if shape_is_flex:
        # Check if the shape name is in the predefined configurations
        if shape_name in DenseIO_shape_configs:
            # Check if the specific OCPU configuration is defined for the shape
            if shape_ocpus in DenseIO_shape_configs[shape_name]:
                DenseIO_config = DenseIO_shape_configs[shape_name][shape_ocpus]
                instance_shape_config = oci.core.models.CapacityReportInstanceShapeConfig(
                    ocpus=DenseIO_config["ocpus"],
                    memory_in_gbs=DenseIO_config["memory_in_gbs"],
                    nvmes=DenseIO_config["nvmes"]
                )
        else:
            # Use the default configuration if the shape name or OCPU count doesn't match predefined configs
            instance_shape_config = oci.core.models.CapacityReportInstanceShapeConfig(
                ocpus=float(shape_ocpus),
                memory_in_gbs=float(shape_memory)
            )
                
    # Create the details for the compute capacity report
    report_details = oci.core.models.CreateComputeCapacityReportDetails(
        compartment_id=tenancy_id,
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
        # Request to create the compute capacity report
        report = core_client.create_compute_capacity_report(create_compute_capacity_report_details=report_details)

        # Print the availability status of each shape in the report data
        for result in report.data.shape_availabilities:
            print(f"{region:<20} {availability_domain:<30} {fault_domain:<20} {shape_name:<25} {result.availability_status}")

    except oci.exceptions.ServiceError as e:
        if "Invalid shape config" in e.message or "Invalid ratio" in e.message:
            print_error(
                e.message,
                "",
                str(instance_shape_config).replace("\n", ""),
                "",
                "https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm"
            )
        else:
            print_error(
                e.message,
                "",
                "Check available shapes at: ",
                "https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm",
                "or restart this script without '-shape' argument"
            )
        raise SystemExit(1)
