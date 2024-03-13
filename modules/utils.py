# coding: utf-8

import csv
import os 
from os import system, name

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# def colors
# - - - - - - - - - - - - - - - - - - - - - - - - - -

class Color:
    ESCAPE_SEQ_START = '\033[{}m'
    ESCAPE_SEQ_END = '\033[0m'

    def __init__(self, code):
        self.code = code

    def __call__(self, text):
        try:
            return f'{self.ESCAPE_SEQ_START.format(self.code)}{text}{self.ESCAPE_SEQ_END}'
        except Exception:
            return text

# Color instances
default_c = Color(0)
white = Color(97)
cyan = Color(96)
magenta = Color(95)
blue = Color(94)
yellow = Color(93)
green = Color(92)
red = Color(91)
black = Color(90)
white_b = Color(47)
cyan_b = Color(46)
magenta_b = Color(45)
blue_b = Color(44)
yellow_b = Color(43)
green_b = Color(42)
red_b = Color(41)
black_b = Color(40)

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# clear shell screen
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def clear():
    try:
        if name == 'nt':  # Windows
            system('cls')
        else:  # Mac and Linux
            system('clear')
    except Exception:
        pass

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# expand local path
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def path_expander(path):

    try:
        expanded_path = os.path.expanduser(path)
        return expanded_path
    
    except (ValueError, TypeError, OSError) as e:
        print_error("check_folder error:", e)
        raise SystemExit(1)

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# check if local folder already exists
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def check_folder(folder, **output):

    try:
        if not os.path.exists(folder):
            os.mkdir(folder)
            if output:
                print_info(yellow, 'Folder', 'creating', folder[:33])
        else:
            if output:
                print_info(green, 'Folder', 'found', folder[:33])

    except Exception as e:
        print_error("check_folder error:", e)
        raise SystemExit(1)


# - - - - - - - - - - - - - - - - - - - - - - - - - -
# check if file size > 300 bytes 
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def check_file_size(file_path):

    file_size = True if (os.path.getsize(file_path)) > 300 else False

    return file_size

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# print script info
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def print_info(color, v1, v2, v3):
    align = '<35' if isinstance(v3, int) else '35'
    print(color(f"{'*'*5:10} {v1:20} {v2:20} {v3:{align}} {'*'*5:5}"))

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# print script error
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def print_error(*args, color=red, level='ERROR'):

    color = yellow if level == 'INFO' else color 
    max_length = min(max(len(str(error_message)) for error_message in args) + 6, 98)
    error_box_width = max_length + 4
    error_message_width = max_length + 2
    blank_line = color("║" + " " * error_box_width + "║")

    print(color("\n╔" + "=" * error_box_width + "╗"))
    print(blank_line)
    print(color("║"), color(f"{level}!".center(error_message_width)), color("║"))
    print(blank_line)

    for error_message in args:
        error_message = str(error_message)
        if len(error_message) > 98:
            split_messages = [error_message[i:i + 98] for i in range(0, len(error_message), 98)]
            for split_message in split_messages:
                print(color("║"), color(split_message.center(error_message_width)), color("║"))
        else:
            print(color("║"), color(error_message.center(error_message_width)), color("║"))

    print(blank_line)
    print(blank_line)
    print(color("╚" + "=" * error_box_width + "╝\n"))

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# init csv file
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def init_csv_report(csv_report):

    try:
        fieldnames = [
            'region',
            'compartment',
            'resource_name',
            'resource_type',
            'resource_ocid',
            'deleted'
            ]

        with open(csv_report, mode='w') as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()

    except Exception as e:
        print_error("init_csv_report error:", e)
        raise SystemExit(1)

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# write output to csv file
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def write_to_csv(csv_report, data):

    try:
        # # format short date for csv
        # backup_time_created = str(data.get('backup_time_created', ''))[:19]
        # backup_expiration = str(data.get('backup_expiration', ''))[:19]

        # # replace missing data with blank strings for csv
        # backup_age = data.get('backup_age', '').replace(' d', '')
        # volume_group_name = data.get('volume_group_name', '').replace(' - ', '')
        # volume_group_id = data.get('volume_group_id', '').replace(' - ', '')
        # is_volume_group_backup = data.get('is_volume_group_backup', '').replace(' - ', '')
        # attached_instance = data.get('attached_instance', '').replace(' - ', '')
        # attached_instance_id = data.get('attached_instance_id', '').replace(' - ', '')

        with open(csv_report, mode='a', newline='') as csvfile:
            writer = csv.DictWriter(
                csvfile,
                fieldnames=[
                    'region',
                    'compartment',
                    'resource_name',
                    'resource_type',
                    'resource_ocid',
                    'deleted'
                ])

            writer.writerow({
                'region': data.get('region'),
                'compartment': data.get('compartment'),
                'resource_name': data.get('resource_name'),
                'resource_type': data.get('resource_type'),
                'resource_ocid': data.get('resource_ocid'),
                'deleted': data.get('deleted')
            })
            csvfile.close
            
    except Exception as e:
        print_error("write_to_csv error:", e)
        raise SystemExit(1)

# - - - - - - - - - - - - - - - - - - - - - - - - - -
# calculate time delta
# - - - - - - - - - - - - - - - - - - - - - - - - - -

def strfdelta(td):
    days = td.days
    hours, rem = divmod(td.seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{days} days, {hours} hours, {minutes} minutes, {seconds} seconds"