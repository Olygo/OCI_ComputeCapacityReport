# coding: utf-8
"""Small terminal pager; redirected output is always written in full."""

import os
import shutil
import sys
from bisect import bisect_right
from contextlib import contextmanager, nullcontext

from modules.exceptions import check_home
from modules.terminal import clear_status, interactive_terminal


class PageLines(list):
    """Ordinary printable lines with optional table/record pagination hints."""
    def __init__(self, values=()):
        super().__init__()
        self.headers = {}
        self.blocks = []
        self.extend(values)

    def extend(self, values):
        offset = len(self)
        if isinstance(values, PageLines):
            self.headers.update({offset + position: header for position, header in values.headers.items()})
            self.blocks.extend((offset + start, offset + end) for start, end in values.blocks)
        super().extend(values)

    def __add__(self, other):
        combined = PageLines(self)
        combined.extend(other)
        return combined


class PageBuffer:
    def __init__(self):
        self.lines = PageLines()

    def write(self, text=''):
        self.lines.extend(str(text).splitlines() or [''])

    def table_header(self, *lines):
        start = len(self.lines)
        for line in lines:
            self.write(line)
        self.lines.headers[start] = tuple(self.lines[start:])
        self.lines.blocks.append((start, len(self.lines)))

    def end_table(self):
        self.lines.headers[len(self.lines)] = ()

    @contextmanager
    def record(self):
        start = len(self.lines)
        yield
        if len(self.lines) > start:
            self.lines.blocks.append((start, len(self.lines)))


def record_block(output):
    return output.record() if isinstance(output, PageBuffer) else nullcontext()


def table_header(output, *lines):
    if isinstance(output, PageBuffer):
        output.table_header(*lines)
    else:
        for line in lines:
            output.write(line)


def read_key():
    """Read one key without Enter, restoring terminal settings even on Ctrl+C."""
    if os.name == 'nt':
        import msvcrt
        key = msvcrt.getwch()
        if key == '\x03':
            raise KeyboardInterrupt
        return key
    import termios
    import tty
    descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(descriptor)
    try:
        tty.setcbreak(descriptor)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)


def page_action():
    width = max(1, shutil.get_terminal_size((120, 24)).columns)
    prompt = '-- More -- Space: page | Enter: line | Q: back | H: home'
    if len(prompt) >= width:
        prompt = '-- More -- [Space/Enter/Q/H]'
    sys.stdout.write(prompt[:max(1, width - 1)])
    sys.stdout.flush()
    try:
        while True:
            key = read_key().lower()
            check_home(key)
            if key in {'', 'q', 'b', '\x04', '\x1b'}:
                return 'quit'
            if key in {'\r', '\n'}:
                return 'line'
            if key == ' ':
                return 'page'
    finally:
        clear_status()


def show_pages(lines, header=None):
    header = header or []
    if not interactive_terminal():
        for line in [*header, *lines]:
            print(line)
        return
    position = 0
    changes = getattr(lines, 'headers', {})
    header_positions = sorted(changes)
    blocks = sorted(getattr(lines, 'blocks', []))
    block_starts = [start for start, _ in blocks]
    new_page = True

    def active_header(index):
        match = bisect_right(header_positions, index) - 1
        if match < 0:
            return ()
        start = header_positions[match]
        return changes[start] if index >= start + len(changes[start]) else ()

    while position < len(lines):
        capacity = max(1, shutil.get_terminal_size((120, 24)).lines - len(header) - 2)
        if new_page:
            for line in header:
                print(line)
            repeated = active_header(position)
            # Always leave room for data, even in a very short terminal.
            if repeated and len(repeated) < capacity:
                for line in repeated:
                    print(line)
                capacity -= len(repeated)
            count = capacity
        else:
            count = 1
        end = min(len(lines), position + count)
        if new_page and end < len(lines):
            match = bisect_right(block_starts, end - 1) - 1
            if match >= 0:
                start, stop = blocks[match]
                repeated = active_header(start)
                fresh_capacity = max(1, shutil.get_terminal_size((120, 24)).lines - len(header) - 2)
                if repeated and len(repeated) < fresh_capacity:
                    fresh_capacity -= len(repeated)
                if position < start < end < stop and stop - start <= fresh_capacity:
                    end = start
                    # Move a table's initial header with its first record when
                    # both fit on the next page, rather than orphaning it.
                    header_match = bisect_right(header_positions, start) - 1
                    if header_match >= 0:
                        header_start = header_positions[header_match]
                        if (header_start > position and changes[header_start]
                                and header_start + len(changes[header_start]) == start):
                            end = header_start
        for line in lines[position:end]:
            print(line)
        position = end
        if position == len(lines):
            return
        action = page_action()
        if action == 'quit':
            return
        new_page = action == 'page'
