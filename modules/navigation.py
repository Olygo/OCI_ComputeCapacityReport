# coding: utf-8
"""Shared interactive commands and numbered selections."""

import shutil
import textwrap

from modules.exceptions import check_home, home_prompt
from modules.terminal import plain_text
from modules.utils import yellow


def read_input(prompt, *, back=False, home=False, quit=True, show_commands=True):
    if show_commands:
        if home:
            prompt = home_prompt(prompt)
        missing = [label for key, label, enabled in
                   (('B', '[B]ack', back), ('Q', '[Q]uit', quit))
                   if enabled and f'[{key}]' not in prompt]
        if missing:
            prompt = prompt.rstrip(' >: ') + ' (' + ', '.join(missing) + ') > '
    value = input(yellow(prompt)).strip()
    if home:
        check_home(value)
    if quit and value.upper() in {'Q', 'QUIT'}:
        raise SystemExit('\nQuitting the program as per user request.\n')
    return None if back and value.upper() in {'B', 'BACK'} else value


def read_choice(prompt):
    return read_input(prompt, back=True, home=True)


def command_choice(prompt, commands):
    print()
    if '[H]' not in commands:
        commands += '   [H] Home'
    width = max(1, shutil.get_terminal_size((120, 24)).columns)
    for line in textwrap.wrap(plain_text(commands), width, break_on_hyphens=False) or ['']:
        print(line)
    return read_choice(prompt)


def selection_index(value, count):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number - 1 if 1 <= number <= count else None


def selection_indices(value, count, *, multiple=True):
    parts = value.split(',')
    if not multiple and len(parts) != 1:
        raise ValueError('Choose one number.')
    indices = [selection_index(part.strip(), count) for part in parts]
    if not indices or any(index is None for index in indices):
        raise ValueError('Choose numbers from the displayed list.')
    return list(dict.fromkeys(indices))
