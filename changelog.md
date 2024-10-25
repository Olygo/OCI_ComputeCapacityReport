# Changelog

Version 3.0.3
- add 'available_count' value for DRCC customers and whitelisted tenancies

Version 3.0.2
- Handles specific use case exceptions with flexible shapes 
- Increases region connectivity timeout and improves exceptions

Version 3.0.1
- minor fixes

Version 3.0.0
## New Features

- Add interactive support for admin & non-admin:
    admins can bypass the control using -su

- Add oCPU and Memory filters & display

- Add a loop to the script so that it automatically relaunches after completing the first query or encountering an error

- Add re-authentication function
	Attempts to reauthenticate by prompting the user to specify a config file if all prior authentication methods have failed

- Enhance code and errors management

Version 2.0.1
## New Features

- Add support for non-admin users:

    add the set_user_compartment function in ./modules/identity.py.
	Include the -su argument to enable administrators to bypass the script prompt.
    Include the -comp argument to allow non-admin users to specify their compartment and bypass the script prompt.

- Implement a compartment state check.

- Enhance the error log message in the create_and_print_report function

Version 2.0.0
## New Features

- Automated Authentication Testing: 

	Now automatically tests all available authentication methods by default, removing the need to specify command line arguments for authentication.
	
- Manual Authentication Selection: 
	
	Users can now force a specific authentication method using the -auth command line argument with options cs, cf, or ip.
	
- Dynamic Region Handling:

	The script now checks connectivity to a region before executing any requests against it.

- Improve region(s) management:

	By default, the script analyzes the home_region.
	Users can specify a different target region using the -region option or run against all subscribed regions 	with -region all_regions.

- Shape Verification and Input Handling:

   Now verifies if a given shape_name exists before running the capacity_report.
   If no shape is provided via command line arguments, the script prompts the user for input and displays a 	list of available shapes.
   
- Support for DenseIO Flexible VM Shapes:
	
	Now supports specific configurations of DenseIO Flexible VM shapes, allowing more customized resource management.

- Custom oCPUs and Memory: 

	Users can now force specific configurations of oCPUs and memory for requested shapes, providing more flexibility in defining VM resources.

- Automatic Shape List Update:

	Now automatically updates its list of available shapes, ensuring users always have the most current 	options displayed.