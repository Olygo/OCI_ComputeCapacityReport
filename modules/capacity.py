# coding: utf-8

import oci
import re
import shutil
import textwrap
from contextlib import closing
from dataclasses import replace
from modules.utils import yellow,red, green, cyan, clear, Color
from modules.identity import get_availability_domains, get_fault_domains
from modules.exceptions import RestartFlowException
from modules.navigation import read_choice, read_input, selection_index
from modules.terminal import plain_text
from modules.session import SearchRequest
from modules.exports import export_last_report
from modules.shape_info import inspect_shape
from modules.shape_tools import compare_shapes, save_shape_properties
from modules.tenancy_limits import show_tenancy_limits
from modules.quota_policies import show_quota_policies
from modules.display import separator_line
from modules.help_guide import show_help
from modules.shape_rules import (
    AX_FLEX_FALLBACK_LIMITS, DENSEIO_PROFILES,
    denseio_config, denseio_ocpu_choices, merge_shape_metadata, normalize_shape_config,
)
from modules.results import (
    CapacityResult, ShapeResources, error_result, print_request_error,
    emit_result, render_error_summary, render_query_summary, ReportRenderer, write_wrapped,
)
from modules.execution import (
    BufferedOutput, ConsoleOutput, build_retry_strategy, client_options, region_tasks,
    cancellable_call, check_cancelled, track_query, QueuedOutput,
)


def positive_integer(value):
    value = int(value)
    if value <= 0:
        raise ValueError('Please enter a positive integer greater than zero.')
    return value


def navigation_input(prompt):
    value = read_choice(prompt)
    if value is None:
        raise RestartFlowException
    return value


def region_label(regions, all_regions=False):
    return (f'all_regions ({len(regions)} regions)' if all_regions else
            ', '.join(region.region_name for region in regions))


def select_history(session):
    entries = list(reversed(session.history))
    if not entries:
        print(yellow('No search in session history yet.'))
        return None
    print(yellow(f'\nSession history ({len(entries)}/{session.history.maxlen}, newest first):'))
    for index, entry in enumerate(entries, 1):
        request = entry.request
        cpu = request.ocpus if request.ocpus else 'default'
        memory = request.memory if request.memory else 'automatic'
        print(plain_text(f'{index}: {request.shape} | OCPU: {cpu} | Memory: {memory} | '
              f'{region_label(request.regions, request.all_regions)} | '
              f'{entry.started_at.astimezone():%H:%M:%S} | {entry.outcome}'))
    while True:
        value = read_choice('\nReplay a search number, [B]ack or [Q]uit: ')
        if value is None:
            return None
        index = selection_index(value, len(entries))
        if index is not None:
            return entries[index].request
        print(red('Invalid input. Please enter a number from the list.'))

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Request user to set an oCPU value
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def set_user_shape_ocpus(user_shape_name):

    while True:
        try:
            return positive_integer(navigation_input(f"\nAmount of OCPUs needed for shape {user_shape_name} ([B]ack, [Q]uit): "))
        except ValueError:
            print(red("\nInvalid input. Please enter a positive integer greater than zero."))

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Request user to set a memory value
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def set_user_shape_memory(user_shape_name):

    while True:
        try:
            return positive_integer(navigation_input(f"\nSpecify the amount of memory (in GB) needed for shape {user_shape_name} ([B]ack, [Q]uit): "))
        except ValueError:
            print(red("\nInvalid input. Please enter a positive integer greater than zero."))

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Print the list of available compute shapes
# - - - - - - - - - - - - - - - - - - - - - - - - - -

PREDEFINED_SHAPES = frozenset([
    'BM.DenseIO1.36', 'BM.DenseIO2.52', 'BM.DenseIO.E4.128', 'BM.DenseIO.E5.128', 'BM.GPU2.2', 'BM.GPU3.8', 'BM.GPU4.8', 'BM.GPU.A10.4', 'BM.GPU.A100-v2.8',
    'BM.GPU.H100.8', 'BM.GPU.L40S.4', 'BM.GPU.MI300X.8', 'BM.GPU.MI355X.8', 'BM.GPU.H200.8', 'BM.GPU.B200.8', 'BM.GPU.GB200.4', 'BM.GPU.GB300.4', 'BM.HPC2.36',
    'BM.HPC.E5.144', 'BM.Optimized3.36', 'BM.Standard1.36', 'BM.Standard2.52', 'BM.Standard3.64', 'BM.Standard.A1.160', 'BM.Standard.A4.48', 'BM.Standard.B1.44',
    'BM.Standard.E2.64', 'BM.Standard.E3.128', 'BM.Standard.E4.128', 'BM.Standard.E5.192', 'BM.Standard.E6.256', 'VM.DenseIO1.16', 'VM.DenseIO1.4', 'VM.DenseIO1.8',
    'VM.DenseIO2.16', 'VM.DenseIO2.24', 'VM.DenseIO2.8', 'VM.DenseIO.E4.Flex', 'VM.DenseIO.E5.Flex', 'VM.GPU2.1', 'VM.GPU3.1', 'VM.GPU3.2', 'VM.GPU3.4', 'VM.GPU.A10.1',
    'VM.GPU.A10.2', 'VM.Optimized3.Flex', 'VM.Standard1.1', 'VM.Standard1.16', 'VM.Standard1.2', 'VM.Standard1.4', 'VM.Standard1.8', 'VM.Standard2.1', 'VM.Standard2.16',
    'VM.Standard2.2', 'VM.Standard2.24', 'VM.Standard2.4', 'VM.Standard2.8', 'VM.Standard3.Flex', 'VM.Standard.A1.Flex', 'VM.Standard.A2.Flex', 'VM.Standard.A4.Flex',
    'VM.Standard.B1.1', 'VM.Standard.B1.16', 'VM.Standard.B1.2', 'VM.Standard.B1.4', 'VM.Standard.B1.8', 'VM.Standard.E2.1', 'VM.Standard.E2.1.Micro', 'VM.Standard.E2.2',
    'VM.Standard.E2.4', 'VM.Standard.E2.8', 'VM.Standard.E3.Flex', 'VM.Standard.E4.Flex', 'VM.Standard.E5.Flex', 'VM.Standard.E6.Flex', 'VM.Standard4.Ax.Flex', 'VM.Standard.E6.Ax.Flex',
    'VM.Standard.A4.Ax.Flex', 'VM.DenseIO.E6.Ax.Flex', 'BM.Standard4.Ax.120', 'BM.Standard.E6.Ax.192', 'BM.Standard.A4.Ax.48', 'BM.DenseIO.E6.Ax.192'
])


def print_shape_list(session, refresh=False):

    """
    Merge predefined shapes with discoveries from the selected region or all
    validated regions. Discovery markers are for display only.
    """

    try:

        catalog = session.catalog(refresh=refresh)

        predefined_shapes = PREDEFINED_SHAPES
        discovered_shapes = catalog.shape_names - predefined_shapes
        all_shapes = sorted(predefined_shapes | discovered_shapes)

        if not catalog.discovery_performed:
            print(yellow("Automatic shape discovery is disabled. Use [U]pdate catalog to discover new shapes in all validated regions."))

        terminal_width = max(1, shutil.get_terminal_size((120, 24)).columns)

        number_width = max(2, len(str(len(all_shapes))))
        displayed_shapes = []
        shape_numbers = {}
        shape_column_width = max((len(shape) for shape in all_shapes), default=0) + number_width + 5
        bold_name = Color('1;39')
        normal_name = Color('22;39')

        def numbered_shape(shape):
            if shape not in shape_numbers:
                displayed_shapes.append(shape)
                shape_numbers[shape] = len(displayed_shapes)
            number = f'{shape_numbers[shape]:02d}'.rjust(number_width) + ': '
            style = bold_name if shape.startswith('BM.') else normal_name
            return number, style

        def print_shape_columns(label, shapes):
            if not shapes:
                return
            prefix = f'{label:<2} | ' if terminal_width > len(label) + 3 else ''
            indent = ' ' * len(prefix)
            available_width = max(1, terminal_width - len(prefix))
            columns = min(4, max(1, available_width // shape_column_width))
            for start in range(0, len(shapes), columns):
                row = []
                for index, shape in enumerate(shapes[start:start + columns]):
                    number, style = numbered_shape(shape)
                    entry = number + shape
                    if columns == 1 and len(entry) > available_width:
                        # Preserve complete text and styling on narrow terminals.
                        offset = 0
                        for line in textwrap.wrap(entry, available_width, break_on_hyphens=False,
                                                  replace_whitespace=False, drop_whitespace=False):
                            first_name = max(0, len(number) - offset)
                            rendered = line[:first_name] + style(line[first_name:])
                            print((prefix if start == 0 and offset == 0 else indent) + rendered)
                            offset += len(line)
                        break
                    rendered = number + style(shape)
                    if index + 1 < len(shapes[start:start + columns]):
                        rendered += ' ' * (shape_column_width - len(entry))
                    row.append(rendered)
                if row:
                    print((prefix if start == 0 else indent) + ''.join(row))

        def print_category(title, shapes):
            if not shapes:
                return
            print()
            for line in textwrap.wrap(title.upper(), terminal_width, break_on_hyphens=False):
                print(yellow(line))
            print_shape_columns('BM', sorted(shape for shape in shapes if shape.startswith('BM.')))
            print_shape_columns('VM', sorted(shape for shape in shapes if shape.startswith('VM.')))
            print_shape_columns('Other', sorted(shape for shape in shapes if not shape.startswith(('BM.', 'VM.'))))

        def is_denseio(shape):
            return ".DenseIO" in shape

        def is_ax(shape):
            return ".Ax" in shape

        def is_gpu_or_hpc(shape):
            return ".GPU" in shape or ".HPC" in shape

        def is_standard_or_optimized(shape):
            return (
                (".Standard" in shape or ".Optimized" in shape)
                and not is_ax(shape)
                and not is_denseio(shape)
                and not is_gpu_or_hpc(shape)
            )

        categories = [
            ("Standard & Optimized", [shape for shape in all_shapes if is_standard_or_optimized(shape)]),
            ("ACCELERON", [shape for shape in all_shapes if is_ax(shape) and not is_denseio(shape)]),
            ("GPU & HPC", [shape for shape in all_shapes if is_gpu_or_hpc(shape)]),
            ("DenseIO", [shape for shape in all_shapes if is_denseio(shape)]),
        ]

        categorized_shapes = set()

        for title, shapes in categories:
            shapes = [shape for shape in shapes if shape not in categorized_shapes]
            print_category(title, shapes)
            categorized_shapes.update(shapes)

        other_shapes = [shape for shape in all_shapes if shape not in categorized_shapes]

        if other_shapes:
            print_category("Other Shapes", other_shapes)

        session.set_displayed_shapes(displayed_shapes)
        session.catalog_shown = True
        return all_shapes

    except Exception as e:
        print_request_error("Unable to display shapes", e, debug=getattr(session, 'debug', False))
        if session.displayed_shapes:
            print(yellow('Keeping the previous shape catalog and codes. Use P to print it again.'))
        print(yellow("Enter a shape name or use [U]pdate catalog to retry discovery."))
        session.catalog_shown = True
        return []

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Define target shape name to analyze 
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def resolve_shape_regions(session, shape_name, regions=None, compartment_id=None, all_regions=None):
    """Probe unfamiliar names before asking for OCPUs/memory or printing a report."""
    regions = session.regions if regions is None else regions
    if shape_name in PREDEFINED_SHAPES or session.is_discovered_shape(shape_name):
        return regions
    regions = list(regions)

    found_names = set()

    def probe(region):
        if compartment_id is None or compartment_id == session.compartment_id:
            return session.probe_shape(region, shape_name)
        return session.probe_shape(region, shape_name, compartment_id=compartment_id)

    with closing(region_tasks(regions, probe, progress='Validating shape in region')) as tasks:
        for region, outcome, error in tasks:
            if error is not None:
                print_request_error(f"\nUnable to validate shape {shape_name} in region {region.region_name}",
                                    error, debug=getattr(session, 'debug', False))
                continue
            status, detail = outcome
            if status == 'FOUND':
                found_names.add(region.region_name)
            elif status == 'NOT_FOUND':
                print(yellow(f"\nShape {shape_name} was not found in region {region.region_name}."))
                if 'NotAuthorizedOrNotFound' in detail:
                    print(yellow("OCI returned NotAuthorizedOrNotFound; check if this shape name really exists."))
                if getattr(session, 'debug', False) and detail:
                    print(plain_text(detail))
            else:
                print(red(f"\nUnable to validate shape {shape_name} in region {region.region_name}: {detail}"))

    found_regions = [region for region in regions if region.region_name in found_names]
    if not found_regions:
        raise RestartFlowException
    return found_regions


def print_capacity_notice(output=None):
    """Render the capacity guidance once at startup or after a region change."""
    output = output if output is not None else ConsoleOutput()
    output.write()
    write_wrapped(output,
                  'CAPACITY INFORMATION IS FOR HIGH-LEVEL GUIDANCE ONLY, ALWAYS CONTACT YOUR ACCOUNT TEAM TO REVIEW YOUR REQUIREMENTS.',
                  color=yellow)
    output.write()

def print_shape_menu(can_change_region, can_use_all_regions=False, can_use_single_region=False,
                     show_notice=False):
    """Present four compact columns, adapting to narrower terminals."""
    output = ConsoleOutput()
    if show_notice:
        print_capacity_notice(output)
    else:
        output.write()

    session_commands = [('R', 'Change region(s)')] if can_change_region else []
    if can_use_all_regions:
        session_commands.append(('A', 'Analyze all subscribed regions'))
    if can_use_single_region:
        session_commands.append(('M', 'Move to a single region'))
    session_commands.extend([('?', 'Help and examples'), ('Q', 'Quit')])
    catalog_commands = [('P', 'Print shape catalog'), ('U', 'Update shape catalog')]
    sections = [
        ('CAPACITY REPORTS', [('L', 'Launch last search again'), ('V', 'View search history'),
                              ('E', 'Export last report')]),
        ('SHAPE CATALOG', catalog_commands),
        ('RESOURCE INSIGHTS', [('I', 'Inspect shape properties'), ('C', 'Compare two shapes'),
                               ('S', 'Save shape properties'), ('T', 'Tenancy limits'),
                               ('O', 'Quota policies')]),
        ('SESSION', session_commands),
    ]
    width = max(1, shutil.get_terminal_size((120, 24)).columns)
    columns = 4 if width >= 108 else 2 if width >= 56 else 1
    for start in range(0, len(sections), columns):
        group = sections[start:start + columns]
        cells = [[title, *(f'[{key}] {label}' for key, label in entries)] for title, entries in group]
        widths = [max(map(len, cell)) for cell in cells]
        while sum(widths) + 3 * (len(widths) - 1) > width:
            widest = max(range(len(widths)), key=widths.__getitem__)
            widths[widest] -= 1
        for row in range(max(map(len, cells))):
            wrapped = [textwrap.wrap(cell[row], size, break_long_words=True, break_on_hyphens=False)
                       if row < len(cell) else [] for cell, size in zip(cells, widths)]
            for line in range(max(map(len, wrapped))):
                parts = []
                for cell, size in zip(wrapped, widths):
                    text = (cell[line] if line < len(cell) else '').ljust(size)
                    parts.append(cyan(text) if row == 0 else text)
                output.write('   '.join(parts).rstrip())
        if start + columns < len(sections):
            output.write()


def set_user_shape_name(session, change_region=None, switch_all_regions=None):
    """Read a shape or handle interactive menu actions before returning it."""
    session.shape_prompt_shown = True

    while True:
        print_shape_menu(can_change_region=change_region is not None and not session.all_regions,
                         can_use_all_regions=switch_all_regions is not None and not session.all_regions,
                         can_use_single_region=change_region is not None and session.all_regions,
                         show_notice=session.capacity_notice_pending)
        session.capacity_notice_pending = False
        prompt = 'Enter a shape name/code or a [command] > '
        if len(prompt) >= shutil.get_terminal_size((120, 24)).columns:
            prompt = 'Selection > '
        user_input = read_input('\n' + prompt, show_commands=False)
        command = user_input.upper()

        if not user_input:
            raise RestartFlowException
        if command in {'B', 'BACK'}:
            continue
        if command in {'Q', 'QUIT'}:
            raise SystemExit("\nQuitting the program as per user request.\n")
        if command in {'?', 'HELP'}:
            show_help()
            continue
        # A double press belongs to the catalog command, not shape validation.
        if command in {'P', 'PP', 'PRINT', 'H', 'HOME'}:
            clear()
            print_shape_list(session)
            continue
        if command in {'U', 'UPDATE'}:
            clear()
            print_shape_list(session, refresh=True)
            continue
        if command in {'A', 'ALL', 'ALL_REGIONS'}:
            if switch_all_regions is None or session.all_regions:
                print(yellow('Already in all_regions mode.' if session.all_regions else
                             'Switching to all_regions is unavailable here.'))
                continue
            switch_all_regions()
            continue
        if command in {'R', 'REGION', 'M', 'MOVE'}:
            if change_region is None:
                print(red("Region selection is unavailable here."))
                continue
            change_region()
            continue
        if command in {'L', 'LAST'}:
            if not session.history:
                print(yellow('No search in session history yet.'))
                continue
            return session.history[-1].request
        if command in {'V', 'HISTORY'}:
            request = select_history(session)
            if request is not None:
                return request
            continue
        if command in {'E', 'EXPORT'}:
            export_last_report(session)
            continue
        if command in {'I', 'INFO'}:
            inspect_shape(session)
            continue
        if command in {'C', 'COMPARE'}:
            compare_shapes(session)
            continue
        if command in {'S', 'SAVE'}:
            save_shape_properties(session)
            continue
        if command in {'T', 'LIMITS'}:
            show_tenancy_limits(session)
            continue
        if command in {'O', 'POLICIES', 'QUOTAS'}:
            show_quota_policies(session)
            continue
        if user_input.lstrip('+-').isdigit():
            try:
                shape = session.shape_for_number(positive_integer(user_input))
            except ValueError:
                shape = None
            if shape is not None:
                return shape
            if session.displayed_shapes:
                print(red(f'Invalid shape number. Choose 01 to {len(session.displayed_shapes):02d}, '
                          'or enter a shape name. Use P to redisplay the list.'))
            else:
                print(red('No numbered shapes list is available. Use P to display it, or enter a shape name.'))
            continue
        return user_input

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Define the shape config for each DenseIO Flex shapes
# - - - - - - - - - - - - - - - - - - - - - - - - - -
denseio_flex_shapes = {
    name: denseio_ocpu_choices(name) for name in DENSEIO_PROFILES
}

def set_denseio_shape_ocpus(shape_name):

    """
    Prompts to select the number of oCPUs for the given shape name.
    Validates the input to ensure it is one of the proposed values.
    """

    # Get the valid oCPU choices for the specified shape name
    allowed_ocpus = denseio_flex_shapes.get(shape_name)
    
    while True:
        user_shape_ocpus = navigation_input(f"Select the amount of OCPUs {allowed_ocpus} ([B]ack, [Q]uit): ")
        
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
    availability_domains = get_availability_domains(identity_client, compartment_id)
    shapes_in_region = oci.pagination.list_call_get_all_results(
        core_client.list_shapes, compartment_id, retry_strategy=build_retry_strategy()
    ).data
    return availability_domains, shapes_in_region

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Determine OCPU/memory configuration for a shape
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def get_shape_config(user_shape_name, shapes_in_region, user_shape_ocpus, user_shape_memory, output=None,
                     reference_shape=None):
    """Resolve CPU/memory using the central rules and the chosen metadata."""
    output = output if output is not None else ConsoleOutput()
    shape_info = next((shape for shape in shapes_in_region if shape.shape == user_shape_name), None)
    if reference_shape is not None:
        from modules.reference_catalog import property_object
        shape_info = merge_shape_metadata(shape_info, property_object(reference_shape))
    config = normalize_shape_config(user_shape_name, shape_info, user_shape_ocpus, user_shape_memory)
    return config.ocpus, config.memory_in_gbs, config.is_flex, shape_info

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# Process region by fetching data and creating report
# - - - - - - - - - - - - - - - - - - - - - - - - - -
def process_region(region, config, signer, compartment_id, user_shape_name, user_shape_ocpus, user_shape_memory, drcc, session=None, output=None, results=None, debug=False):
    """Collect structured results; a recoverable failure affects only its scope."""
    output = output if output is not None else ConsoleOutput()
    results = results if results is not None else []
    requested = ShapeResources(user_shape_ocpus, user_shape_memory)
    check_cancelled()

    def failed(error, ad=None, fd=None, stage='capacity', applied=None):
        result = error_result(region.region_name, ad, fd, user_shape_name,
                              requested, error, applied=applied, stage=stage)
        results.append(result)
        emit_result(result, output, drcc, debug)

    try:
        if session is None:
            region_config = dict(config)
            region_config['region'] = region.region_name
            identity_client = oci.identity.IdentityClient(config=region_config, signer=signer, **client_options())
            core_client = oci.core.ComputeClient(config=region_config, signer=signer, **client_options())
            availability_domains = get_availability_domains(identity_client, config['tenancy'])
        else:
            identity_client = session.identity_client(region)
            core_client = session.compute_client(region)
            availability_domains = session.availability_domains(region, config['tenancy'])
        if not availability_domains:
            raise RuntimeError('No availability domain was returned by OCI.')
    except Exception as e:
        failed(e, stage='availability_domains')
        return results

    try:
        if session is None:
            shapes_in_region = oci.pagination.list_call_get_all_results(
                core_client.list_shapes, config['tenancy'], shape=user_shape_name,
                retry_strategy=build_retry_strategy()
            ).data
        else:
            info = session.shape_metadata(region, config['tenancy'], user_shape_name)
            shapes_in_region = [info] if info is not None else []
    except Exception as e:
        failed(e, stage='regional_metadata')
        shapes_in_region = []

    regional_info = next((shape for shape in shapes_in_region if shape.shape == user_shape_name), None)
    for availability_domain in availability_domains:
        check_cancelled()
        configuration_shapes = shapes_in_region
        if '.Flex' in user_shape_name or regional_info is None:
            try:
                if session is not None:
                    ad_info = session.shape_metadata(region, config['tenancy'], user_shape_name, availability_domain)
                else:
                    ad_shapes = oci.pagination.list_call_get_all_results(
                        core_client.list_shapes, config['tenancy'],
                        availability_domain=availability_domain, shape=user_shape_name,
                        retry_strategy=build_retry_strategy()
                    ).data
                    ad_info = next((shape for shape in ad_shapes if shape.shape == user_shape_name), None)
                if ad_info is not None:
                    configuration_shapes = [merge_shape_metadata(ad_info, regional_info)]
            except Exception as e:
                failed(e, availability_domain, stage='ad_metadata')
                # Still query capacity using regional metadata or known fallback.

        try:
            # Always start with the original request: limits can differ per AD.
            shape_ocpus, shape_memory, shape_is_flex, shape_info = get_shape_config(
                user_shape_name, configuration_shapes, user_shape_ocpus, user_shape_memory,
                output=output,
                reference_shape=session.reference_shape(user_shape_name) if session is not None else None
            )
        except ValueError as e:
            failed(e, availability_domain, stage='configuration')
            continue

        try:
            fault_domains = (get_fault_domains(identity_client, config['tenancy'], availability_domain)
                             if session is None else
                             session.fault_domains(region, config['tenancy'], availability_domain))
            if not fault_domains:
                raise RuntimeError('No fault domain was returned by OCI.')
        except Exception as e:
            failed(e, availability_domain, stage='fault_domains',
                   applied=ShapeResources(shape_ocpus, shape_memory))
            continue

        for fault_domain in fault_domains:
            check_cancelled()
            results.extend(create_and_print_report(
                region.region_name, identity_client, core_client,
                availability_domain, fault_domain, compartment_id, shape_info,
                user_shape_name, drcc, shape_ocpus, shape_memory, shape_is_flex,
                output=output, debug=debug, requested=requested
            ))
    return results


def process_regions(session, regions, user_shape_name, user_shape_ocpus, user_shape_memory, drcc, compartment_id=None, snapshot=None):
    """Return fresh results and render each region only in the main thread."""
    debug = getattr(session, 'debug', False)
    requested = ShapeResources(user_shape_ocpus, user_shape_memory)
    compartment_id = session.compartment_id if compartment_id is None else compartment_id
    regional_results_by_name = {region.region_name: [] for region in regions}
    regional_outputs = {}
    completed_regions = []

    def execute(region, output):
        results = regional_results_by_name[region.region_name]
        try:
            process_region(
                region, session.region_config(region), session.signer,
                compartment_id, user_shape_name, user_shape_ocpus,
                user_shape_memory, drcc, session=session, output=output,
                results=results, debug=debug
            )
        except Exception as e:
            result = error_result(region.region_name, None, None, user_shape_name,
                                  requested, e, stage='region')
            results.append(result)
            emit_result(result, output, drcc, debug)
        return results

    shown = []
    interrupted = False
    with track_query(len(regions)) as progress:
        console = ConsoleOutput(renderer=ReportRenderer(
            regions=[region.region_name for region in regions], shape=user_shape_name,
            requested=requested), progress=progress, results=shown)
        stream = QueuedOutput() if len(regions) == 1 else None

        def collect(region):
            output = regional_outputs[region.region_name]
            return execute(region, output), output

        for region in regions:
            regional_outputs[region.region_name] = stream if stream is not None else BufferedOutput()

        try:
            with closing(region_tasks(
                    regions, collect, progress=progress,
                    on_tick=(lambda: stream.render(console)) if stream is not None else None)) as tasks:
                for region, collected, error in tasks:
                    completed_regions.append(region.region_name)
                    if error is not None:
                        result = error_result(region.region_name, None, None, user_shape_name,
                                              requested, error, stage='region')
                        regional_results_by_name[region.region_name].append(result)
                        emit_result(result, console, drcc, debug)
                    else:
                        _, output = collected
                        output.render(console)
        except KeyboardInterrupt:
            interrupted = True
            raise
        finally:
            progress.clear()
            # The task generator has drained workers before this block. Include
            # completed rows from interrupted regional buffers, without caching
            # or replaying capacity calls.
            retained = list(shown)
            seen = {id(result) for result in retained}
            for region in regions:
                for result in regional_results_by_name[region.region_name]:
                    if id(result) not in seen:
                        retained.append(result)
                        seen.add(id(result))
            if snapshot is not None:
                snapshot.finish(retained, completed_regions, progress.requests, progress.elapsed,
                                interrupted=interrupted)
            for region in regions:
                regional_outputs[region.region_name].render(console)
            render_error_summary(shown, console)
            render_query_summary(shown, console, progress.elapsed, progress.requests, interrupted,
                                 progress.completed, progress.total)
    return retained


def create_and_print_report(region, identity_client, core_client, availability_domain, fault_domain, compartment_id, shape_info, shape_name, drcc, shape_ocpus=None, shape_memory=None, shape_is_flex=False, output=None, debug=False, requested=None):
    """Request capacity and return structured data independently of rendering."""
    output = output if output is not None else ConsoleOutput()
    requested = requested if requested is not None else ShapeResources(shape_ocpus, shape_memory)
    instance_shape_config = None
    sent_configuration = None
    applied = None
    try:
        if shape_is_flex and not shape_name.startswith('BM.'):
            preset = denseio_config(shape_name, shape_ocpus)
            if preset is not None:
                shape_ocpus, shape_memory = preset.ocpus, preset.memory_in_gbs
                sent_configuration = dict(ocpus=shape_ocpus, memory_in_gbs=shape_memory, nvmes=preset.nvmes)
            else:
                sent_configuration = dict(ocpus=float(shape_ocpus), memory_in_gbs=float(shape_memory))
            instance_shape_config = oci.core.models.CapacityReportInstanceShapeConfig(**sent_configuration)
            # Keep existing numeric display; the sent payload remains floating point.
            applied = ShapeResources(shape_ocpus, shape_memory, sent_configuration.get('nvmes'))
        else:
            shape_ocpus = shape_ocpus if shape_ocpus else getattr(shape_info, 'ocpus', None) or '-'
            shape_memory = shape_memory if shape_memory else getattr(shape_info, 'memory_in_gbs', None) or '-'
            applied = ShapeResources(shape_ocpus, shape_memory)

        report_details = oci.core.models.CreateComputeCapacityReportDetails(
            compartment_id=compartment_id, availability_domain=availability_domain,
            shape_availabilities=[oci.core.models.CreateCapacityReportShapeAvailabilityDetails(
                instance_shape=shape_name, fault_domain=fault_domain,
                instance_shape_config=instance_shape_config
            )]
        )
        report = cancellable_call(core_client.create_compute_capacity_report,
            create_compute_capacity_report_details=report_details,
            retry_strategy=build_retry_strategy()
        )
        results = [CapacityResult(
            region, availability_domain, fault_domain, shape_name, requested,
            applied, result.availability_status, available_count=result.available_count
        ) for result in report.data.shape_availabilities]
        if not results:
            raise RuntimeError('OCI returned an empty capacity report.')
    except Exception as e:
        failure = error_result(region, availability_domain, fault_domain, shape_name,
                               requested, e, applied=applied, sent_configuration=sent_configuration)
        if (shape_name == 'VM.DenseIO.E5.Flex' and getattr(e, 'status', None) == 400
                and getattr(e, 'code', None) == 'InvalidParameter'
                and re.search(r'Valid\s+Configurations\s+for\s+shape:\s*\)\s*$',
                              failure.error.message, re.IGNORECASE)):
            # Preserve the rejection and raw OCI diagnostics, including the
            # request ID. An empty server list does not establish capacity.
            failure = replace(failure, error=replace(failure.error, summary=
                'OCI returned an empty valid-configuration list for this shape in this AD'))
        results = [failure]
    for result in results:
        emit_result(result, output, drcc, debug)
    return results
