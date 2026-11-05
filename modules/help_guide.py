# coding: utf-8
"""Render the project's README as the offline, paginated help guide."""

from pathlib import Path
import re
import shutil
import textwrap

from modules.pager import PageBuffer, show_pages
from modules.results import write_wrapped
from modules.terminal import status_message
from modules.utils import cyan


README_PATH = Path(__file__).resolve().parent.parent / 'README.md'


def _inline(text):
    """Keep link destinations and readable text without Markdown decoration."""
    def link(match):
        label, target = match.groups()
        return label if target.startswith('#') else f'{label} ({target})'

    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link, text)
    return text.replace('**', '').replace('`', '')


def _table(output, rows):
    """Keep tables aligned when they fit, otherwise show complete labelled rows."""
    headers, *records = rows
    widths = [max(len(row[index]) for row in rows) for index in range(len(headers))]
    width = max(1, shutil.get_terminal_size((120, 24)).columns)
    if sum(widths) + 3 * (len(widths) - 1) <= width:
        def line(row):
            return ' | '.join(cell.ljust(size) for cell, size in zip(row, widths)).rstrip()

        output.table_header(cyan(line(headers)), '─' * (sum(widths) + 3 * (len(widths) - 1)))
        for row in records:
            with output.record():
                output.write(line(row))
        output.end_table()
    else:
        for row in records:
            with output.record():
                for label, value in zip(headers, row):
                    write_wrapped(output, f'{label}: {value}')
                output.write()


def help_lines():
    """Use README content only; omit web-only images and screenshot reminders."""
    source = README_PATH.read_text(encoding='utf-8')
    source = re.sub(r'<!--.*?-->', '', source, flags=re.DOTALL)
    lines = source.splitlines()
    output = PageBuffer()
    paragraph = []

    def flush():
        if paragraph:
            write_wrapped(output, _inline(' '.join(paragraph)))
            paragraph.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith('```'):
            flush()
            index += 1
            block = []
            while index < len(lines) and not lines[index].strip().startswith('```'):
                block.append(lines[index])
                index += 1
            # Preserve command indentation, line breaks and literal shell syntax.
            width = max(1, shutil.get_terminal_size((120, 24)).columns)
            for code_line in block:
                for part in textwrap.wrap(code_line, width, expand_tabs=False,
                                          replace_whitespace=False, drop_whitespace=False,
                                          break_on_hyphens=False) or ['']:
                    output.write(part)
            index += 1
            continue
        if (stripped.startswith('![')
                or stripped.startswith('> **Screenshot to add')):
            flush()
            index += 1
            continue
        if stripped.startswith('|'):
            flush()
            rows = []
            while index < len(lines) and lines[index].strip().startswith('|'):
                cells = [cell.strip() for cell in lines[index].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?', cell) for cell in cells):
                    rows.append([_inline(cell) for cell in cells])
                index += 1
            if rows:
                _table(output, rows)
            continue
        heading = re.match(r'^#{1,6}\s+(.+)$', stripped)
        if heading:
            flush()
            write_wrapped(output, _inline(heading.group(1)), color=cyan)
        elif not stripped:
            flush()
            if output.lines and output.lines[-1] != '':
                output.write()
        elif stripped.startswith('- '):
            flush()
            write_wrapped(output, _inline(stripped[2:]), prefix='- ')
        elif stripped.startswith('> '):
            flush()
            write_wrapped(output, _inline(stripped[2:]))
        else:
            paragraph.append(stripped)
        index += 1
    flush()
    return output.lines


def show_help():
    try:
        show_pages(help_lines())
    except OSError as error:
        status_message(f'Unable to read README.md: {error}')
    except KeyboardInterrupt:
        status_message('Help closed.')
