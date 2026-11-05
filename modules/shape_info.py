# coding: utf-8
"""Independent inspection of the complete OCI ListShapes metadata."""

import shutil
import textwrap

from modules.navigation import read_choice, selection_index
from modules.terminal import plain_text, status_message
from modules.execution import ConsoleOutput
from modules.pager import PageBuffer, show_pages, record_block, table_header
from modules.display import region_header_lines
from modules.results import print_request_error, write_wrapped
from modules.reference_catalog import property_dict
from modules.utils import cyan, red, yellow


def select_info_shape(session, prompt='Shape information'):
    while True:
        value = read_choice(f'\n{prompt}: enter a shape name or number ([B]ack, [Q]uit): ')
        if value is None:
            return None
        if not value:
            continue
        if value.lstrip('+-').isdigit():
            try:
                index = selection_index(value, len(session.displayed_shapes))
                shape = session.shape_for_number(index + 1) if index is not None else None
            except ValueError:
                shape = None
            if shape is None:
                print(red('Invalid shape number. Use a number from the displayed list, or enter a shape name.'))
                continue
            return shape
        return value


def select_info_region(session):
    if len(session.regions) == 1:
        return session.current_region
    print('\nSelect a region for ListShapes metadata:')
    for number, region in enumerate(session.regions, 1):
        print(plain_text(f'  {number:02d}: {region.region_name}'))
    while True:
        value = read_choice('Region number ([B]ack, [Q]uit): ')
        if value is None:
            return None
        index = selection_index(value, len(session.regions))
        if index is not None:
            return session.regions[index]
        print(red('Invalid region number. Choose a region from the list.'))


def scalar_text(value):
    if value is None:
        return 'Not provided (null)'
    if value == []:
        return 'Empty list ([])'
    if value == {}:
        return 'Empty object ({})'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if value == '':
        return 'Empty string ("")'
    return plain_text(value)


def property_leaves(value, path=()):
    """Retain every scalar and empty container, including future API fields."""
    if isinstance(value, dict) and value:
        return {key: leaf for name, item in value.items()
                for key, leaf in property_leaves(item, path + (name,)).items()}
    if isinstance(value, list) and value:
        return {key: leaf for index, item in enumerate(value)
                for key, leaf in property_leaves(item, path + (index,)).items()}
    return {path: value}


def property_path(path):
    text = ''
    for part in path:
        text += f'[{part}]' if isinstance(part, int) else ('.' if text else '') + part
    return plain_text(text) or '(root)'


def wrapped_cells(values, widths):
    """Wrap cells using the same rules in individual sheets and comparisons."""
    cells = []
    for text, size in zip(values, widths):
        text = plain_text(text)
        cells.append([chunk for line in text.splitlines() or ['']
                      for chunk in textwrap.wrap(line, size, break_long_words=True,
                                                 break_on_hyphens=False) or ['']])
    for index in range(max(map(len, cells))):
        yield [cell[index] if index < len(cell) else '' for cell in cells]


def render_properties(data, output):
    """Render the full OCI property paths and values in two aligned columns."""
    width = max(1, shutil.get_terminal_size((120, 24)).columns)
    path_width = min(44, width // 2)
    value_width = max(1, width - path_width - 3)
    if width >= 60:
        table_header(output, 'OCI property'.ljust(path_width) + ' | Value', '─' * width)
    for path, value in property_leaves(data).items():
        label, text = property_path(path), scalar_text(value)
        with record_block(output):
            if width >= 60:
                for left, right in wrapped_cells([label, text], [path_width, value_width]):
                    output.write(left.ljust(path_width) + ' | ' + right.ljust(value_width))
            else:
                write_wrapped(output, label, color=cyan)
                write_wrapped(output, text, prefix='  Value: ')
                output.write()
    if isinstance(output, PageBuffer):
        output.end_table()


def print_shape_info(shape, region_name, output=None, source=None):
    """Display complete raw OCI fields in the same table style as comparisons."""
    paginate = output is None
    output = output if output is not None else PageBuffer()
    header = region_header_lines(region_name=region_name)
    for line in header:
        output.write(line)
    data = property_dict(shape)
    write_wrapped(output, f'Shape information — {data.get("shape", "Not provided")}', color=cyan)
    write_wrapped(output, f'Source: {source or "OCI ListShapes | Region: " + region_name}')
    write_wrapped(output, 'Default OCPUs/memory are OCI defaults; option bounds are shown separately.')
    write_wrapped(output, 'Not provided (null): no value in the source properties. Booleans: Yes / No.')
    output.write()
    render_properties(data, output)
    output.write()

    if paginate:
        show_pages(output.lines)


def inspect_shape(session):
    """Consult one region, reusing raw metadata; never submit a capacity request."""
    try:
        shape_name = select_info_shape(session)
        if shape_name is None:
            return
        region = select_info_region(session)
        if region is None:
            return
        try:
            shape, source = session.property_metadata(region, session.compartment_id, shape_name)
            if shape is None:
                print()
                write_wrapped(ConsoleOutput(), f'OCI API and the local catalog returned no metadata for {shape_name} '
                              f'in {region.region_name}. This does not establish whether the shape exists '
                              'or has available capacity.', color=yellow)
                return
            print_shape_info(shape, region.region_name, source=source)
        except Exception as error:
            print_request_error(f'Shape information failed in {region.region_name}', error,
                                debug=session.debug)
    except KeyboardInterrupt:
        status_message('Shape information cancelled.')
