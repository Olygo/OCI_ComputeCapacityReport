# coding: utf-8

import csv
import io
import json
from pathlib import Path
import re
from collections import Counter

from modules.navigation import read_choice
from modules.terminal import plain_text, status_message
from modules.utils import green, red, yellow


CSV_FIELDS = (
    'row_type', 'schema_version', 'started_at', 'finished_at', 'outcome',
    'interrupted', 'is_partial', 'region_mode', 'requested_regions', 'queried_regions',
    'completed_regions', 'compartment_id', 'drcc', 'requests_count', 'elapsed_seconds',
    'report_result_count', 'shape', 'region', 'availability_domain', 'fault_domain',
    'requested_ocpus', 'requested_memory_in_gbs', 'requested_nvmes',
    'applied_ocpus', 'applied_memory_in_gbs', 'applied_nvmes',
    'status', 'stage', 'available_count', 'available_count_display',
    'error_category', 'error_message', 'error_http_status', 'error_code',
    'error_request_id', 'error_sdk_version', 'error_exception_type', 'error_sent_configuration',
)


class ReportSerializationError(ValueError):
    pass


def clean_text(value):
    if value is None:
        return None
    return plain_text(value)


def csv_cell(value):
    """Protect text cells without coercing actual numeric values or JSON data."""
    if not isinstance(value, str):
        return value
    value = clean_text(value)
    if value.strip() != '-' and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def numeric_value(value):
    if value is None or isinstance(value, (int, float)) or value == '-':
        return value
    raise ValueError('The report contains an unsupported numeric value.')


def resources_data(resources):
    if resources is None:
        return None
    return {name: numeric_value(getattr(resources, name))
            for name in ('ocpus', 'memory_in_gbs', 'nvmes')}


def error_data(error):
    if error is None:
        return None
    # Only explicit diagnostic fields and shape resources are exported. Raw
    # exception text may contain headers or authentication material.
    configuration = error.sent_configuration
    sent = ({name: numeric_value(configuration[name])
             for name in ('ocpus', 'memory_in_gbs', 'nvmes') if name in configuration}
            if isinstance(configuration, dict) else None)
    return dict(category=clean_text(error.category), message=clean_text(error.summary),
                http_status=error.http_status, code=clean_text(error.code),
                request_id=clean_text(error.request_id), sdk_version=clean_text(error.sdk_version),
                exception_type=clean_text(error.exception_type), sent_configuration=sent)


def timestamp(value):
    return value.isoformat().replace('+00:00', 'Z') if value is not None else None


def report_data(snapshot):
    """Serialize a whitelist of report fields, never the session or SDK clients."""
    if snapshot is None:
        raise ValueError('No report is available. Run a capacity search first.')
    if snapshot.finished_at is None:
        raise ValueError('The report is still running.')
    records = []
    for result in snapshot.results:
        records.append(dict(
            region=clean_text(result.region), availability_domain=clean_text(result.availability_domain),
            fault_domain=clean_text(result.fault_domain), shape=clean_text(result.shape),
            requested=resources_data(result.requested), applied=resources_data(result.applied),
            status=clean_text(result.status), stage=clean_text(result.stage),
            available_count=numeric_value(result.available_count),
            available_count_display=(clean_text(result.available_count) if result.available_count else '-'),
            error=error_data(result.error),
        ))
    statuses = Counter(record['status'] for record in records if record['error'] is None)
    return dict(
        schema_version=1,
        report=dict(
            started_at=timestamp(snapshot.started_at), finished_at=timestamp(snapshot.finished_at),
            outcome=snapshot.outcome, interrupted=snapshot.interrupted, is_partial=snapshot.is_partial,
            shape=clean_text(snapshot.shape), requested=resources_data(snapshot.requested),
            region_mode='all_regions' if snapshot.all_regions else 'selected_regions',
            requested_regions=[clean_text(region) for region in snapshot.requested_regions],
            queried_regions=[clean_text(region) for region in snapshot.queried_regions],
            completed_regions=[clean_text(region) for region in snapshot.completed_regions],
            compartment_id=clean_text(snapshot.compartment_id), drcc=snapshot.drcc,
            requests_count=snapshot.requests_count, elapsed_seconds=snapshot.elapsed_seconds,
        ),
        summary=dict(
            result_count=len(records), available=statuses.pop('AVAILABLE', 0),
            out_of_host_capacity=statuses.pop('OUT_OF_HOST_CAPACITY', 0),
            other_statuses=dict(statuses), errors=sum(record['error'] is not None for record in records),
        ),
        results=records,
    )


def csv_text(data):
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS)
    writer.writeheader()
    report = data['report']
    # Retain date/context/interruption even when cancellation yielded no rows.
    for record in data['results'] or [None]:
        row = {key: report[key] for key in (
            'started_at', 'finished_at', 'outcome', 'region_mode', 'compartment_id',
            'requests_count', 'elapsed_seconds', 'shape')}
        row.update(row_type='result' if record is not None else 'report',
                   schema_version=data['schema_version'], report_result_count=data['summary']['result_count'])
        for key in ('interrupted', 'is_partial', 'drcc'):
            row[key] = str(report[key]).lower()
        for key in ('requested_regions', 'queried_regions', 'completed_regions'):
            row[key] = json.dumps(report[key], ensure_ascii=False)
        for group in ('requested', 'applied'):
            values = record[group] if record is not None else report['requested'] if group == 'requested' else None
            if values is not None:
                row.update({group + '_' + name: value for name, value in values.items()})
        if record is not None:
            row.update({key: record[key] for key in (
                'shape', 'region', 'availability_domain', 'fault_domain', 'status', 'stage',
                'available_count', 'available_count_display')})
            if record['error'] is not None:
                for key, value in record['error'].items():
                    row['error_' + key] = (json.dumps(value, ensure_ascii=False) if key == 'sent_configuration'
                                           and value is not None else value)
        writer.writerow({key: csv_cell(value) for key, value in row.items()})
    return buffer.getvalue()


def export_report(snapshot, path, file_format):
    file_format = file_format.lower()
    if file_format not in {'csv', 'json'}:
        raise ValueError('Choose CSV or JSON.')
    try:
        data = report_data(snapshot)
        # Validate all scalar values before creating a file, including CSV data.
        json_content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        content = csv_text(data) if file_format == 'csv' else json_content
    except (ValueError, TypeError) as error:
        raise ReportSerializationError(str(error)) from error
    return write_export(content, path)


def write_export(content, path):
    """Create exclusively and clean only our own incomplete file on failure."""
    target = Path(path).expanduser().absolute()
    created = False
    try:
        with target.open('x', encoding='utf-8', newline='') as stream:
            created = True
            stream.write(content)
    except BaseException:
        if created:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                raise OSError(f'Could not remove incomplete export {target}: {error}') from error
        raise
    return target


def default_export_name(snapshot, file_format):
    shape = re.sub(r'[^A-Za-z0-9._-]+', '_', snapshot.shape).strip('._-')[:80] or 'shape'
    suffix = '_partial' if snapshot.is_partial else ''
    return f'capacity_{shape}_{snapshot.started_at:%Y%m%dT%H%M%S%fZ}{suffix}.{file_format}'


def export_last_report(session):
    snapshot = session.last_report
    if snapshot is None:
        print(yellow('No report is available. Run a capacity search first.'))
        return
    try:
        while True:
            choice = read_choice('\nExport last report: [C]SV, [J]SON, [B]ack or [Q]uit: ')
            if choice is None:
                return
            choice = choice.upper()
            formats = {'C': 'csv', 'CSV': 'csv', '1': 'csv', 'J': 'json', 'JSON': 'json', '2': 'json'}
            if choice not in formats:
                print(red('Invalid input. Choose C for CSV or J for JSON.'))
                continue
            file_format = formats[choice]
            default = default_export_name(snapshot, file_format)
            while True:
                path = read_choice(f'Output path [{default}] ([B]ack, [Q]uit): ')
                if path is None:
                    break
                try:
                    target = export_report(snapshot, path or default, file_format)
                except FileExistsError:
                    print(red('File already exists. Choose another path; no file was overwritten.'))
                    continue
                except ReportSerializationError as error:
                    print(red(f'Unable to serialize report: {error}'))
                    return
                except (OSError, ValueError, RuntimeError) as error:
                    print(red(f'Export failed: {error}. Choose another path or B to return.'))
                    continue
                print(green(f'{file_format.upper()} report exported to {target}'))
                return
    except KeyboardInterrupt:
        status_message('Export cancelled.')
