# coding: utf-8
"""Terminal capabilities and safe presentation of external text. No OCI calls."""

from contextlib import contextmanager
import builtins
import os
import re
import shutil
import sys


_no_color = False
_SEQUENCES = re.compile(
    r'(?:\x1b\]|\x9d).*?(?:\x07|\x1b\\|\x9c|$)'
    r'|(?:\x1b[P^_X]|[\x90\x98\x9e\x9f]).*?(?:\x1b\\|\x9c|$)'
    r'|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]'
    r'|\x1b[ -/]*[@-Z\\-_]', re.DOTALL)
_CONTROLS = re.compile(r'[\x00-\x08\x0b-\x1f\x7f-\x9f]')


def plain_text(value):
    """Keep Unicode, line feeds and tabs; remove terminal commands/controls."""
    return _CONTROLS.sub('', _SEQUENCES.sub('', str(value)))


def configure_terminal(no_color=False):
    global _no_color
    _no_color = bool(no_color)


def terminal_output():
    return bool(getattr(sys.stdout, 'isatty', lambda: False)()) and os.environ.get('TERM') != 'dumb'


def interactive_terminal():
    return terminal_output() and bool(getattr(sys.stdin, 'isatty', lambda: False)())


def colors_enabled():
    return terminal_output() and not _no_color and 'NO_COLOR' not in os.environ


def clear_status():
    if terminal_output():
        print('\r\033[2K', end='', flush=True)


def status_line(message):
    text = plain_text(message)
    if terminal_output():
        width = max(1, shutil.get_terminal_size((120, 24)).columns - 1)
        print('\r\033[2K' + text[:width], end='', flush=True)
    else:
        print(text, flush=True)


def status_message(message):
    clear_status()
    print(plain_text(message))


@contextmanager
def temporary_status(message, *, quiet_redirect=False):
    if terminal_output() or not quiet_redirect:
        status_line(message)
    try:
        yield
    finally:
        clear_status()


def plain_print(*args, **kwargs):
    builtins.print(*(plain_text(value) for value in args), **kwargs)
