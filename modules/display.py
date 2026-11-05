# coding: utf-8
"""Shared regional heading for interactive views."""

import shutil
import textwrap

from modules.execution import ConsoleOutput
from modules.pager import PageBuffer, record_block
from modules.results import write_wrapped
from modules.utils import cyan
from modules.terminal import plain_text


def info_box(output, title, messages, color=cyan):
    """Separate contextual information from tables without changing their data."""
    width = max(1, min(110, shutil.get_terminal_size((120, 24)).columns))
    if isinstance(messages, str):
        messages = [messages]
    output.write()
    with record_block(output):
        if width < 8:
            write_wrapped(output, title, color=color)
            for message in messages:
                write_wrapped(output, message)
        else:
            inner = width - 4
            output.write(color('┌' + '─' * (width - 2) + '┐'))
            for line in textwrap.wrap(plain_text(title), inner, break_on_hyphens=False) or ['']:
                output.write(color('│ ' + line.ljust(inner) + ' │'))
            output.write(color('├' + '─' * (width - 2) + '┤'))
            for message in messages:
                for paragraph in plain_text(message).splitlines() or ['']:
                    for line in textwrap.wrap(paragraph, inner, break_on_hyphens=False) or ['']:
                        output.write(color('│') + ' ' + line.ljust(inner) + ' ' + color('│'))
            output.write(color('└' + '─' * (width - 2) + '┘'))
    output.write()


def separator_line():
    width = max(1, shutil.get_terminal_size((120, 24)).columns)
    return cyan('=' * min(width, 76))


def region_header_lines(region_name=None, subscribed_region_count=None, multiple_regions=False):
    output = PageBuffer()
    label = (f'ALL SUBSCRIBED REGIONS ({subscribed_region_count})'
             if subscribed_region_count is not None else str(region_name).upper())
    title = 'Analyzed regions' if multiple_regions or subscribed_region_count is not None else 'Analyzed region'
    write_wrapped(output, f'{title} : {label}', color=cyan)
    output.write(separator_line())
    return output.lines


def print_region_header(session, output=None, regions=None, all_regions=None):
    output = output if output is not None else ConsoleOutput()
    regions = session.regions if regions is None else regions
    all_regions = session.all_regions if all_regions is None else all_regions
    for line in region_header_lines(
            region_name=(regions[0].region_name if len(regions) == 1 else
                         f'SELECTED REGIONS ({len(regions)}): ' + ', '.join(region.region_name for region in regions)),
            subscribed_region_count=session.subscribed_region_count if all_regions else None,
            multiple_regions=len(regions) > 1):
        output.write(line)
