# coding: utf-8
"""OCI-only shape comparisons and standalone property exports."""

from datetime import datetime, timezone
import json
import re
import shutil

from modules.display import region_header_lines
from modules.exports import clean_text, write_export
from modules.pager import PageBuffer, show_pages, record_block, table_header
from modules.results import print_request_error, write_wrapped
from modules.reference_catalog import property_dict, property_order_key
from modules.navigation import read_choice
from modules.terminal import status_message
from modules.shape_info import (print_shape_info, scalar_text,
                                select_info_region, select_info_shape,
                                property_leaves, property_path, wrapped_cells)
from modules.utils import cyan, green, red, yellow


def lookup_shape(session, region, name):
    shape, source = session.property_metadata(region, session.compartment_id, name)
    if shape is None:
        print()
        print(yellow(f'OCI API and the local catalog returned no metadata for {name} in {region.region_name}. '
                     'This does not establish whether the shape exists or has available capacity.'))
    return shape, source


def export_shape_properties(shape, region_name, path, file_format, source=None):
    if file_format == 'json':
        # Raw SDK Shape fields only; no authentication or script fallback data.
        content = json.dumps(property_dict(shape), ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    elif file_format == 'txt':
        output = PageBuffer()
        print_shape_info(shape, region_name, output=output, source=source)
        content = '\n'.join(clean_text(line) for line in output.lines) + '\n'
    else:
        raise ValueError('Choose JSON or TXT.')
    return write_export(content, path)


def save_shape_properties(session):
    try:
        name = select_info_shape(session, 'Save OCI properties')
        if name is None:
            return
        region = select_info_region(session)
        if region is None:
            return
        shape, source = lookup_shape(session, region, name)
        if shape is None:
            return
        while True:
            choice = read_choice('Format: [J]SON (raw OCI), [T]ext (readable), [B]ack or [Q]uit: ')
            if choice is None:
                return
            formats = {'J': 'json', 'JSON': 'json', 'T': 'txt', 'TXT': 'txt', 'TEXT': 'txt'}
            file_format = formats.get(choice.upper())
            if file_format is None:
                print(red('Choose J or T.'))
                continue
            safe = re.sub(r'[^A-Za-z0-9._-]+', '_', f'{name}_{region.region_name}')[:140]
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            default = f'shape_{safe}_{stamp}.{file_format}'
            while True:
                path = read_choice(f'Output path [{default}] ([B]ack, [Q]uit): ')
                if path is None:
                    break
                try:
                    target = export_shape_properties(shape, region.region_name, path or default, file_format, source=source)
                except (OSError, ValueError, RuntimeError) as error:
                    print(red(f'Export failed: {error}. Choose another path or B to return.'))
                    continue
                print(green(f'Shape properties exported to {target}'))
                return
    except KeyboardInterrupt:
        status_message('Shape export cancelled.')
    except Exception as error:
        print_request_error('Shape export failed', error, debug=session.debug)


def compare_text(value, missing):
    if value is missing:
        return 'Field not returned'
    return scalar_text(value)


def comparison_lines(left, right):
    """Full union of API properties; wide table or narrow stacked records."""
    output = PageBuffer()
    a, b = property_leaves(left), property_leaves(right)
    paths = sorted(dict.fromkeys([*a, *b]), key=property_order_key)
    missing = object()
    width = max(1, shutil.get_terminal_size((120, 24)).columns)
    wide = width >= 90
    path_width = min(44, width // 3)
    value_width = max(1, (width - path_width - 8) // 2)
    if wide:
        table_header(output, '  ' + 'OCI property'.ljust(path_width) + ' | ' + 'A'.ljust(value_width) + ' | B',
                     '─' * min(width, path_width + value_width * 2 + 8))
    for path in paths:
        av, bv = a.get(path, missing), b.get(path, missing)
        # JSON scalar types are retained: null, false and zero remain distinct.
        equal = (av is not missing and bv is not missing
                 and json.dumps(av, ensure_ascii=False) == json.dumps(bv, ensure_ascii=False))
        marker = '=' if equal else '!'
        values = [property_path(path), compare_text(av, missing), compare_text(bv, missing)]
        with record_block(output):
            if wide:
                for index, row in enumerate(wrapped_cells(values, [path_width, value_width, value_width])):
                    line = ((marker if index == 0 else ' ') + ' ' + row[0].ljust(path_width)
                            + ' | ' + row[1].ljust(value_width) + ' | ' + row[2].ljust(value_width))
                    output.write(line if equal else yellow(line))
            else:
                write_wrapped(output, marker + ' ' + values[0], color=cyan if equal else yellow)
                write_wrapped(output, values[1], prefix='  A: ', color=None if equal else yellow)
                write_wrapped(output, values[2], prefix='  B: ', color=None if equal else yellow)
                output.write()
    output.end_table()
    return output.lines


def compare_shapes(session):
    try:
        left_name = select_info_shape(session, 'Compare shape A')
        if left_name is None:
            return
        right_name = select_info_shape(session, 'Compare shape B')
        if right_name is None:
            return
        region = select_info_region(session)
        if region is None:
            return
        left, left_source = lookup_shape(session, region, left_name)
        right, right_source = lookup_shape(session, region, right_name)
        if left is None or right is None:
            return
        header = PageBuffer()
        header.lines.extend(region_header_lines(region_name=region.region_name))
        write_wrapped(header, f'A: {left_name}', color=cyan)
        write_wrapped(header, f'B: {right_name}', color=cyan)
        write_wrapped(header, f'A source: {left_source}')
        write_wrapped(header, f'B source: {right_source}')
        write_wrapped(header, '= identical | ! different or field not returned')
        show_pages(header.lines + comparison_lines(property_dict(left), property_dict(right)))
    except KeyboardInterrupt:
        status_message('Shape comparison cancelled.')
    except Exception as error:
        print_request_error('Shape comparison failed', error, debug=session.debug)
