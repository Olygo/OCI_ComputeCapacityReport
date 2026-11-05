# coding: utf-8
"""Read-only service limits with explicit tenancy/compartment usage scope."""

from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import csv
import io
import json
from math import isfinite
import re
import shutil
from types import SimpleNamespace
from threading import local

import oci

from modules.display import info_box
from modules.identity import compartment_paths, inspect_compartment
from modules.exceptions import RestartFlowException
from modules.execution import (ConsoleOutput, QueryProgress, build_retry_strategy, cancellable_call,
                               check_cancelled, client_options, region_tasks)
from modules.exports import clean_text, write_export, csv_cell
from modules.pager import PageBuffer, show_pages, record_block, table_header
from modules.results import print_request_error, write_wrapped
from modules.shape_info import wrapped_cells
from modules.navigation import read_choice, command_choice, selection_index
from modules.terminal import plain_text, status_line, status_message, temporary_status, terminal_output
from modules.utils import Color, cyan, green, red, yellow


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


@contextmanager
def loading(message):
    with temporary_status(message):
        yield


class LimitsProgress(QueryProgress):
    def __init__(self, total, label='Reading tenancy limits', unit='services'):
        super().__init__(total)
        self.label = label
        self.unit = unit

    def show(self):
        if not terminal_output():
            return
        with self.lock:
            completed, requests = self.completed, self.requests
        width = max(1, shutil.get_terminal_size((120, 24)).columns - 1)
        text = (f'{self.label}: {completed}/{self.total} {self.unit} | '
                f'{requests} requests | {self.elapsed:.1f}s')
        status_line(text[:width])
        self.visible = True


def paginated(function, *args, progress=None, **kwargs):
    def call(*positional, **options):
        if progress is not None:
            progress.request_started()
        return cancellable_call(function, *positional, **options)
    return oci.pagination.list_call_get_all_results(
        call, *args, retry_strategy=build_retry_strategy(), **kwargs).data


def services_in_region(session, region):
    with loading(f'Reading OCI Limits services in {region.region_name}...'):
        client = oci.limits.LimitsClient(config=session.region_config(region),
                                       signer=session.signer, **client_options())
        services = paginated(client.list_services, session.config['tenancy'])
    # Service names, not labels, are the API identifiers.
    return sorted({service.name: service for service in services}.values(), key=lambda item: item.name)


def select_region(session):
    if len(session.regions) == 1:
        return session.current_region
    output = PageBuffer()
    write_wrapped(output, 'Select one region for tenancy limits (current session scope is unchanged):', color=cyan)
    for number, region in enumerate(session.regions, 1):
        write_wrapped(output, f'{number:02d}: {region.region_name}')
    show_pages(output.lines)
    while True:
        choice = read_choice('Region number ([B]ack, [Q]uit): ')
        if choice is None:
            return None
        index = selection_index(choice, len(session.regions))
        if index is not None:
            return session.regions[index]
        print(red('Choose a region number from the list.'))


LIMIT_LOOKUP = object()


def usage_context(session, compartment_id=None):
    compartment_id = session.config['tenancy'] if compartment_id is None else compartment_id
    if compartment_id not in {session.config['tenancy'], session.compartment_id}:
        parts = compartment_id.split('.')
        if (not compartment_id.startswith(('ocid1.compartment.', 'ocid1.tenancy.')) or len(parts) < 5 or not parts[2]
                or not parts[-1] or not re.fullmatch(r'[A-Za-z0-9_.-]+', compartment_id)):
            raise ValueError('Enter a valid compartment OCID (ocid1.compartment....) or the current tenancy root OCID (ocid1.tenancy....).')
        if compartment_id.startswith('ocid1.tenancy.'):
            raise ValueError('Enter the root OCID of the current tenancy, not another tenancy.')
    return dict(usage_scope='entire_tenancy' if compartment_id == session.config['tenancy'] else 'selected_compartment',
                usage_compartment_id=compartment_id)


def usage_label(context):
    return ('entire tenancy' if context['usage_scope'] == 'entire_tenancy' else
            f"selected compartment ({context['usage_compartment_id']})")


def validated_usage_context(session, compartment_id):
    """Reject an unverified selection before any Limits/Quotas search."""
    try:
        context = usage_context(session, compartment_id)
        with loading('Checking compartment with OCI Identity...'):
            client = session.identity_client(session.home_region or session.current_region)
            inspect_compartment(client, compartment_id, session.config['tenancy'])
        return context
    except ValueError as error:
        print(red(str(error)))
    except Exception as error:
        print_request_error(f'Cannot validate compartment {compartment_id}; '
                            'it may not exist or you may not have access', error, debug=session.debug)
    return None


def select_compartment_by_name(session):
    while True:
        name = read_choice('Exact compartment name (case-sensitive; [B]ack, [Q]uit): ')
        if name is None:
            return None
        if not name.strip():
            print(red('Enter a compartment name.'))
            continue
        try:
            with loading('Searching OCI compartments...'):
                client = session.identity_client(session.home_region or session.current_region)
                matches = paginated(client.list_compartments, session.config['tenancy'],
                                    name=name, compartment_id_in_subtree=True,
                                    access_level='ACCESSIBLE', lifecycle_state='ACTIVE')
            matches = sorted({item.id: item for item in matches if item.name == name}.values(),
                             key=lambda item: item.id)
        except Exception as error:
            print_request_error('Compartment name lookup failed; you can use an OCID instead',
                                error, debug=session.debug)
            return None
        if not matches:
            print(yellow(f'No accessible active compartment found with the exact name {name}.'))
            continue
        if len(matches) == 1:
            context = validated_usage_context(session, matches[0].id)
            if context is not None:
                write_wrapped(ConsoleOutput(), f'Selected compartment: {matches[0].name} ({matches[0].id})')
                return context
            continue
        paths = compartment_paths(client, matches, session.config['tenancy'], debug=session.debug)
        output = PageBuffer()
        write_wrapped(output, 'Several compartments have this name. Select one:', color=cyan)
        for number, item in enumerate(matches, 1):
            write_wrapped(output, f'{number:02d}: {item.name} | {item.id}')
            write_wrapped(output, f'Path: {paths[item.id]}', prefix='    ')
        show_pages(output.lines)
        while True:
            choice = read_choice('Compartment number ([B]ack to name search, [Q]uit): ')
            if choice is None:
                break
            index = selection_index(choice, len(matches))
            if index is not None:
                context = validated_usage_context(session, matches[index].id)
                if context is not None:
                    return context
                continue
            print(red('Choose a compartment number from the list.'))


def select_usage_scope(session):
    print(cyan('\nUSAGE SCOPE'))
    current = usage_context(session, session.compartment_id)
    while True:
        write_wrapped(ConsoleOutput(), f'[1] Keep current selection: {usage_label(current)}')
        print('[2] Enter a compartment OCID')
        print('[3] Find a compartment by name (case sensitive)')
        choice = read_choice('Usage scope [1] ([B]ack, [Q]uit): ')
        if choice is None:
            return None
        if choice in {'', '1'}:
            context = validated_usage_context(session, current['usage_compartment_id'])
            if context is not None:
                return context
            continue
        if choice == '3':
            selected = select_compartment_by_name(session)
            if selected is not None:
                return selected
            continue
        if choice == '2':
            while True:
                compartment_id = read_choice('Compartment OCID ([B]ack, [Q]uit): ')
                if compartment_id is None:
                    break
                context = validated_usage_context(session, compartment_id)
                if context is not None:
                    return context
            continue
        print(red('Choose 1, 2 or 3.'))


def selector_columns(output, entries, *, bold_indices=()):
    """Alphabetical selectors read down each column, with stable input codes."""
    width = max(1, shutil.get_terminal_size((120, 24)).columns)
    columns = 3 if width >= 90 else 2 if width >= 56 else 1
    columns = min(columns, max(1, len(entries)))
    usable_width = width - 3 * (columns - 1)
    widths = [usable_width // columns + (column < usable_width % columns) for column in range(columns)]
    row_count = (len(entries) + columns - 1) // columns
    bold = Color('1;39')
    for index in range(row_count):
        row = [entries[column * row_count + index]
               if column * row_count + index < len(entries) else ''
               for column in range(columns)]
        with record_block(output):
            for cells in wrapped_cells(row, widths):
                padded = [cell.ljust(size) for cell, size in zip(cells, widths)]
                output.write('   '.join(
                    bold(cell) if column * row_count + index in bold_indices else cell
                    for column, cell in enumerate(padded)).rstrip())


def select_services(services, region, context=None):
    output = PageBuffer()
    label = usage_label(context) if context is not None else 'entire tenancy'
    info_box(output, 'TENANCY LIMITS', f'Region: {region.region_name} | Usage scope: {label}')
    entries = []
    for number, service in enumerate(services, 1):
        description = clean_text(getattr(service, 'description', None)) or service.name
        entries.append(f'{number:02d}: {description} ({service.name})')
    selector_columns(output, entries, bold_indices={index for index, service in enumerate(services)
                                                  if service.name == 'compute'})
    commands = ('[L] List/search limit names   [A] All services   '
                '[D] Dynamic limits only (all services)   ') if services else ''
    show_pages(output.lines)
    while True:
        choice = command_choice('Service number / name / command > ',
                                commands + '[B] Back   [Q] Quit')
        if choice is None:
            return None
        if services and choice.upper() in {'L', 'LIMITS', 'NAMES'}:
            return LIMIT_LOOKUP
        if services and choice.upper() in {'A', 'ALL', 'D', 'DYNAMIC'}:
            return services, choice.upper() in {'D', 'DYNAMIC'}, True
        index = selection_index(choice, len(services))
        if index is not None:
            return [services[index]], False, False
        found = next((service for service in services if service.name.casefold() == choice.casefold()), None)
        if found is not None:
            return [found], False, False
        print(red('Choose a service number/name, L, A, D or B.' if services else 'Choose B or Q.'))


def error_record(stage, service, error, name=None, scope=None, ad=None):
    status = getattr(error, 'status', None)
    return dict(stage=stage, service=service, limit_name=name, scope_type=scope,
                availability_domain=ad, http_status=status, code=clean_text(getattr(error, 'code', None)),
                request_id=clean_text(getattr(error, 'request_id', None)),
                summary='Access denied' if status in (401, 403) else 'OCI Limits request failed')


def numeric(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError('OCI Limits returned a non-finite or nonnumeric value')
    return value


def collect_service_metadata(session, region, service, progress, dynamic_only=False,
                             limit_name=None, include_hidden_usage=False):
    """Collect metadata only; usage runs later in one shared, bounded pool."""
    tenancy_id = session.config['tenancy']
    client = oci.limits.LimitsClient(config=session.region_config(region), signer=session.signer, **client_options())
    errors, diagnostics = [], []

    def failed(stage, error, name=None, scope=None, ad=None):
        errors.append(error_record(stage, service.name, error, name, scope, ad))
        diagnostics.append((f'{service.name} / {stage}' + (f' / {name}' if name else ''), error))

    definitions, values = [], []
    for stage, function in (('definitions', client.list_limit_definitions), ('values', client.list_limit_values)):
        try:
            options = {'name': limit_name} if limit_name is not None else {}
            response = paginated(function, tenancy_id, service_name=service.name, progress=progress, **options)
            if limit_name is not None:
                response = [item for item in response if item.name == limit_name]
            if stage == 'definitions':
                definitions = [oci.util.to_dict(item) for item in response]
            else:
                values = [oci.util.to_dict(item) for item in response]
        except Exception as error:
            failed(stage, error)
    by_name = {item['name']: item for item in definitions}
    contexts = {}
    for value in values:
        key = (value['name'], value.get('scope_type'), value.get('availability_domain'))
        contexts.setdefault(key, value)
    represented = {key[0] for key in contexts}
    # Definitions without a value remain visible, but no AD is invented.
    for name, definition in by_name.items():
        if name not in represented:
            contexts[(name, definition.get('scope_type'), None)] = None
    rows = []
    for (name, scope, ad), value in sorted(contexts.items(), key=lambda item: tuple(part or '' for part in item[0])):
        check_cancelled()
        definition = by_name.get(name, {})
        dynamic = definition.get('is_dynamic')
        if dynamic_only and dynamic is not True:
            continue
        row = dict(service=service.name, service_description=clean_text(getattr(service, 'description', None)),
                   region=region.region_name, limit_name=name, description=definition.get('description'),
                   scope_type=scope, availability_domain=ad, is_dynamic=dynamic,
                   current_limit=None, used=None, available=None, effective_quota_value=None,
                   usage_status='Pending', definition=definition or None,
                   limit_value=value, resource_availability=None, metadata_collected_at=utc_now(),
                   usage_attempted_at=None, usage_collected_at=None)
        try:
            row['current_limit'] = numeric(value.get('value')) if value is not None else None
        except ValueError as error:
            failed('value', error, name, scope, ad)
        rows.append(row)
        if limit_name is None and not include_hidden_usage and is_hidden_limit(row):
            row['usage_status'] = 'Not collected (zero/deprecated limit)'
            continue
        prepare_usage(row)
    names = {item['name'] for item in definitions + values}
    return dict(rows=rows, errors=errors, diagnostics=diagnostics,
                names=names, names_complete=not any(error['stage'] in {'definitions', 'values'} for error in errors))


def prepare_usage(row):
    if (row.get('definition') or {}).get('is_resource_availability_supported') is False:
        row['usage_status'] = 'Not supported'
    elif row['scope_type'] not in {'GLOBAL', 'REGION', 'AD'} or (
            row['scope_type'] == 'AD' and not row['availability_domain']):
        row['usage_status'] = 'Scope not provided'
    else:
        row['usage_status'] = 'Pending'


def collect_usage_rows(session, region, report, rows):
    """One pool across all services, with a private client per worker thread."""
    jobs = [SimpleNamespace(region_name=f"{row['service']} / {row['limit_name']}", row=row)
            for row in rows if row['usage_status'] == 'Pending']
    progress = LimitsProgress(len(jobs), label='Reading limit usage', unit='limits')
    clients = local()

    def worker(job):
        row = job.row
        result = dict(usage_attempted_at=utc_now(), usage_collected_at=None)
        try:
            if not hasattr(clients, 'client'):
                clients.client = oci.limits.LimitsClient(config=session.region_config(region),
                                                       signer=session.signer, **client_options())
            options = {'availability_domain': row['availability_domain']} if row['scope_type'] == 'AD' else {}
            progress.request_started()
            availability = cancellable_call(clients.client.get_resource_availability, row['service'],
                                            row['limit_name'], report['usage_compartment_id'],
                                            retry_strategy=build_retry_strategy(), **options).data
            data = oci.util.to_dict(availability) or {}
            result.update(resource_availability=data, usage_collected_at=utc_now())
            result.update(used=numeric(data.get('fractional_usage') if data.get('fractional_usage') is not None else data.get('used')),
                          available=numeric(data.get('fractional_availability') if data.get('fractional_availability') is not None else data.get('available')),
                          effective_quota_value=numeric(data.get('effective_quota_value')))
            result['usage_status'] = ('Provided' if result['used'] is not None or result['available'] is not None
                                      else 'Not available')
            return result, None
        except Exception as error:
            if getattr(error, 'status', None) == 404:
                result['usage_status'] = 'Not available (404)'
                return result, None
            else:
                result['usage_status'] = 'Access denied' if getattr(error, 'status', None) in (401, 403) else 'Request error'
                return result, error

    with closing(region_tasks(jobs, worker, progress=progress, max_workers=4)) as tasks:
        for job, outcome, error in tasks:
            row = job.row
            if error is None:
                update, error = outcome
                row.update(update)
            else:
                row['usage_status'] = 'Request error'
            if error is not None:
                report['errors'].append(error_record('usage', row['service'], error, row['limit_name'],
                                                      row['scope_type'], row['availability_domain']))
                if session.debug:
                    print_request_error(f"Tenancy limits / {job.region_name} / usage", error, debug=True)
    return progress.requests


def complete_hidden_usage(session, region, report):
    """Supplement only intentionally omitted usage; preserve the original snapshot."""
    if report.get('include_hidden_usage', False):
        return report
    updated = deepcopy(report)
    rows = [row for row in updated['rows'] if row['usage_status'] == 'Not collected (zero/deprecated limit)']
    for row in rows:
        prepare_usage(row)
    requests = collect_usage_rows(session, region, updated, rows)
    updated.update(include_hidden_usage=True, usage_supplemented_at=utc_now(),
                   supplement_requests_count=requests,
                   requests_count=report['requests_count'] + requests,
                   is_partial=report['is_partial'] or bool(updated['errors']))
    return updated


def collect_limits(session, region, services, dynamic_only=False, usage_compartment_id=None, limit_name=None,
                   include_hidden_usage=False):
    context = usage_context(session, usage_compartment_id)
    report = dict(schema_version=1, region=region.region_name, tenancy_id=session.config['tenancy'],
                  **context, collected_at=utc_now(), finished_at=None,
                  dynamic_only=dynamic_only, requested_limit_name=limit_name,
                  include_hidden_usage=include_hidden_usage, services=[dict(name=service.name,
                  description=clean_text(getattr(service, 'description', None))) for service in services],
                  is_partial=False, rows=[], errors=[], usage_supplemented_at=None, supplement_requests_count=0)
    progress = LimitsProgress(len(services), label='Reading limit metadata')
    jobs = [SimpleNamespace(region_name=service.name, service=service) for service in services]

    def worker(job):
        return collect_service_metadata(session, region, job.service, progress, dynamic_only=dynamic_only,
                                        limit_name=limit_name, include_hidden_usage=include_hidden_usage)

    outcomes = {}
    with closing(region_tasks(jobs, worker, progress=progress, max_workers=4)) as tasks:
        for job, outcome, error in tasks:
            if error is not None:
                outcome = dict(rows=[], errors=[error_record('service', job.service.name, error)],
                               diagnostics=[(job.service.name, error)], names_complete=False)
            outcomes[job.service.name] = outcome
            if limit_name is None and outcome['names_complete']:
                session.cache_limit_names(region, job.service.name, outcome['names'])
            if session.debug:
                for error_context, failure in outcome['diagnostics']:
                    print_request_error(f'Tenancy limits / {error_context}', failure, debug=True)
    for service in services:
        outcome = outcomes[service.name]
        report['rows'].extend(row for row in outcome['rows'] if not dynamic_only or row['is_dynamic'] is True)
        report['errors'].extend(outcome['errors'])
    report['requests_count'] = progress.requests + collect_usage_rows(session, region, report, report['rows'])
    report['is_partial'] = bool(report['errors'])
    report['finished_at'] = utc_now()
    return report


def value_text(value):
    return 'N/A' if value is None else str(value)


def limit_cells(row):
    scope = row['availability_domain'] if row['scope_type'] == 'AD' else row['scope_type']
    if row['scope_type'] == 'AD' and not scope:
        scope = 'AD (not provided)'
    kind = 'Dynamic' if row['is_dynamic'] is True else 'Fixed' if row['is_dynamic'] is False else 'N/A'
    return [plain_text(row['limit_name']), plain_text(scope or 'N/A'), kind, *(value_text(row[field]) for field in
            ('current_limit', 'used', 'available', 'effective_quota_value'))]


def limit_column_widths(headers, cells, terminal_width):
    """Fit real service data; keep AD names whole whenever space permits."""
    widths = [max([len(header), *(len(line) for row in cells for line in row[index].splitlines())])
              for index, header in enumerate(headers)]
    widths[0] = min(widths[0], 40)
    minimum = [max(len(headers[0]), min(widths[0], 24)), min(widths[1], 12), widths[2],
               *(len(header) for header in headers[3:])]
    gap_width = 3 * (len(headers) - 1)
    if sum(minimum) + gap_width > terminal_width:
        return None
    excess = max(0, sum(widths) + gap_width - terminal_width)
    for index in (3, 4, 5, 6, 0, 1):
        shrink = min(excess, widths[index] - minimum[index])
        widths[index] -= shrink
        excess -= shrink
    return widths


def limit_table_line(cells, widths):
    return '   '.join(text.rjust(size) if index >= 3 else text.ljust(size)
                      for index, (text, size) in enumerate(zip(cells, widths))).rstrip()


def is_hidden_limit(row):
    return row['current_limit'] == 0 or (row.get('definition') or {}).get('is_deprecated') is True


def visible_limit_rows(report, show_hidden=False):
    if show_hidden or report.get('requested_limit_name') is not None:
        return report['rows']
    return [row for row in report['rows'] if not is_hidden_limit(row)]


def report_lines(report, show_hidden=False):
    output = PageBuffer()
    info = PageBuffer()
    info.write( f"Region: {report['region']} | Usage scope: {usage_label(report)}")
    info.write( 'CURRENT LIMIT: tenancy service limit. USED / AVAILABLE / QUOTA: selected usage scope.')
    info.write( f"Collected: {report['collected_at']} | Finished: {report['finished_at']}")
    if report.get('usage_supplemented_at'):
        info.write( f"Hidden-limit usage collected separately: {report['usage_supplemented_at']}. "
                      'Original metadata and previously collected usage were retained.')
    if report.get('requested_limit_name') is not None:
        info.write( f"Requested limit: {report['requested_limit_name']}")
    if report['dynamic_only']:
        info.write( 'Filter: dynamic limits only')
    info.write( 'Current limits are an API snapshot. Available values reflect limits/quotas, not hardware capacity.')
    info.write( 'N/A: OCI did not provide a value. A missing quota value does not mean unlimited.')
    visible_rows = visible_limit_rows(report, show_hidden)
    if report.get('requested_limit_name') is None:
        hidden_count = len(report['rows']) - len(visible_rows)
        info.write( 'Display: ' + ('all collected limits.' if show_hidden else
                      f'zero/deprecated limits hidden ({hidden_count} entries). Use [V] to show them.'))
        if not report.get('include_hidden_usage', False):
            info.write( 'Usage for zero/deprecated limits was not queried. Use [V] to retrieve it.')
        info.write( 'Exports include all metadata and collected usage; skipped usage is identified.')
    info_box(output, 'TENANCY LIMITS', info.lines)
    width = max(1, shutil.get_terminal_size((120, 24)).columns)
    headers = ['LIMIT NAME', 'SCOPE', 'TYPE', 'CURRENT LIMIT', 'USED', 'AVAILABLE', 'QUOTA']
    for service in report['services']:
        output.write()
        write_wrapped(output, (service['description'] or service['name']).upper() + f" ({service['name']})", color=cyan)
        rows = [row for row in visible_rows if row['service'] == service['name']]
        if not rows:
            if any(row['service'] == service['name'] for row in report['rows']):
                write_wrapped(output, 'All returned limits are hidden (zero or deprecated). Use [V] to show them.')
                continue
            write_wrapped(output, 'No matching limits returned.' if not any(error['service'] == service['name'] for error in report['errors'])
                          else 'No matching limit rows; see collection errors below.')
            continue
        service_cells = [limit_cells(row) for row in rows]
        for row, cells in zip(rows, service_cells):
            if row['definition'] and row['definition'].get('is_deprecated') is True:
                cells[0] += '\n  Deprecated limit'
            if row['usage_status'] != 'Provided':
                cells[0] += f"\n  Usage: {row['usage_status']}"
        widths = limit_column_widths(headers, service_cells, width)
        if widths is not None:
            table_header(output, limit_table_line(headers, widths), '─' * (sum(widths) + 18))
        for row, values in zip(rows, service_cells):
            with record_block(output):
                if widths is not None:
                    for cells in wrapped_cells(values, widths):
                        output.write(limit_table_line(cells, widths))
                else:
                    for header, value in zip(headers, values):
                        write_wrapped(output, f'{header}: {value}')
        output.end_table()
        output.write()
    if report['errors']:
        output.write()
        write_wrapped(output, f"PARTIAL REPORT — {len(report['errors'])} failed operation(s)", color=yellow)
        for error in report['errors']:
            context = ' / '.join(str(error[key]) for key in ('service', 'limit_name', 'availability_domain', 'stage') if error[key])
            write_wrapped(output, f"{context}: {error['summary']} (HTTP {value_text(error['http_status'])})")
    return output.lines


CSV_FIELDS = ('collected_at', 'finished_at', 'region', 'tenancy_id', 'usage_scope', 'dynamic_only', 'is_partial',
              'record_type', 'service', 'service_description', 'limit_name', 'description', 'scope_type',
              'availability_domain', 'is_dynamic', 'current_limit', 'used', 'available', 'effective_quota_value',
              'usage_status', 'stage', 'http_status', 'code', 'request_id', 'summary', 'usage_compartment_id',
              'requested_limit_name', 'metadata_collected_at', 'usage_attempted_at', 'usage_collected_at',
              'usage_supplemented_at', 'supplement_requests_count', 'requests_count')


def export_limits(report, path, file_format):
    if file_format == 'json':
        content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    elif file_format == 'csv':
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        common = {field: report[field] for field in CSV_FIELDS[:7]}
        common['usage_compartment_id'] = report['usage_compartment_id']
        common['requested_limit_name'] = report.get('requested_limit_name')
        for field in ('usage_supplemented_at', 'supplement_requests_count', 'requests_count'):
            common[field] = report.get(field)
        for kind, records in (('limit', report['rows']), ('error', report['errors'])):
            for record in records:
                values = dict(common, record_type=kind)
                values.update({key: record[key] for key in CSV_FIELDS if key in record})
                writer.writerow({key: csv_cell(value) for key, value in values.items()})
        if not report['rows'] and not report['errors']:
            writer.writerow({key: csv_cell(value) for key, value in dict(common, record_type='report').items()})
        content = stream.getvalue()
    else:
        raise ValueError('Choose JSON or CSV.')
    return write_export(content, path)


def export_interactive(report, exporter=export_limits, prefix='tenancy_limits', title='Tenancy limits'):
    while True:
        choice = read_choice('Export format: [J]SON / [C]SV ([B]ack, [Q]uit): ')
        if choice is None:
            return
        formats = {'J': 'json', 'JSON': 'json', 'C': 'csv', 'CSV': 'csv'}
        file_format = formats.get(choice.upper())
        if file_format is None:
            print(red('Choose J or C.'))
            continue
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        region = re.sub(r'[^A-Za-z0-9._-]+', '_', report['region'])[:80]
        default = f'{prefix}_{region}_{stamp}.{file_format}'
        while True:
            path = read_choice(f'Output path [{default}] ([B]ack, [Q]uit): ')
            if path is None:
                return
            try:
                target = exporter(report, path or default, file_format)
            except (OSError, ValueError, RuntimeError) as error:
                print(red(f'Export failed: {error}. Choose another path or B to return.'))
                continue
            print(green(f'{title} exported to {target}'))
            return


def limit_name_catalog(session, region, service, refresh=False):
    """Discover all names, including names outside a dynamic-only report."""
    cached = session.cached_limit_names(region, service.name)
    if cached is not None and not refresh:
        return cached
    names, failures = set(), []
    client = oci.limits.LimitsClient(config=session.region_config(region), signer=session.signer, **client_options())
    with loading(f'Reading all limit names for {service.name} in {region.region_name}...'):
        for function in (client.list_limit_definitions, client.list_limit_values):
            try:
                items = paginated(function, session.config['tenancy'], service_name=service.name)
                names.update(item.name for item in items if getattr(item, 'name', None))
            except Exception as error:
                failures.append(error)
    for error in failures:
        print_request_error(f'Limit name catalog incomplete / {service.name}', error, debug=session.debug)
    if not failures:
        session.cache_limit_names(region, service.name, names)
    elif cached is not None:
        print(yellow('Keeping the previous complete name catalog; refresh did not complete.'))
        return cached
    return sorted(names)


def limit_search_service(services, show_catalog=True):
    if show_catalog:
        output = PageBuffer()
        write_wrapped(output, 'SELECT A SERVICE CATEGORY FOR LIMIT LOOKUP', color=cyan)
        selector_columns(output, [f'{index:02d}: {clean_text(getattr(service, "description", None)) or service.name} ({service.name})'
                                 for index, service in enumerate(services, 1)],
                         bold_indices={index for index, service in enumerate(services)
                                       if service.name == 'compute'})
        show_pages(output.lines)
    while True:
        choice = read_choice('Which service category? Enter its number or name ([B]ack, [Q]uit): ')
        if choice is None:
            return None
        index = selection_index(choice, len(services))
        if index is not None:
            return services[index]
        service = next((item for item in services if item.name.casefold() == choice.casefold()
                        or (getattr(item, 'description', None) or '').casefold() == choice.casefold()), None)
        if service is not None:
            return service
        print(red('Choose a service from the displayed list.'))


def print_limit_names(names, service, region, context):
    output = PageBuffer()
    info_box(output, f'LIMIT NAMES — {service.name}', [
        f'Region: {region.region_name} | Usage scope: {usage_label(context)}',
        'All names returned by OCI for this service; each name appears once across ADs.'])
    selector_columns(output, [f'{index:02d}: {name}' for index, name in enumerate(names, 1)])
    if not names:
        write_wrapped(output, 'No names returned. You can still enter an exact limit name to query OCI.')
    show_pages(output.lines)


def search_service_limits(session, region, service, context):
    names = limit_name_catalog(session, region, service)
    print_limit_names(names, service, region, context)
    report = None
    while True:
        info_box(ConsoleOutput(), 'LIMIT LOOKUP',
                 'To check a specific limit, enter its name or code from the list. '
                 'OCI will return its current limit, used and available values for the selected usage scope.')
        commands = '[P] Print names   [U] Refresh names'
        if report is not None:
            commands += '   [R] Refresh last limit   [E] Export last limit'
        choice = command_choice('Limit name / code / command > ', commands +
                                '   [B] Back to service categories   [Q] Quit')
        if choice is None:
            return
        if choice.upper() in {'P', 'PRINT'}:
            print_limit_names(names, service, region, context)
            continue
        if choice.upper() in {'U', 'UPDATE'}:
            names = limit_name_catalog(session, region, service, refresh=True)
            print_limit_names(names, service, region, context)
            continue
        if report is not None and choice.upper() in {'E', 'EXPORT'}:
            export_interactive(report)
            continue
        if report is not None and choice.upper() in {'R', 'REFRESH'}:
            report = collect_limits(session, region, [service], usage_compartment_id=context['usage_compartment_id'],
                                    limit_name=report['requested_limit_name'])
            show_pages(report_lines(report))
            continue
        if not choice:
            continue
        if choice.lstrip('+-').isdigit():
            index = selection_index(choice, len(names))
            if index is None:
                print(red('Invalid limit code. Choose a displayed code or enter a limit name.'))
                continue
            name = names[index]
        else:
            # Unknown names still go to OCI, as with capacity shape searches.
            name = next((item for item in names if item.casefold() == choice.casefold()), choice)
        report = collect_limits(session, region, [service], usage_compartment_id=context['usage_compartment_id'],
                                limit_name=name)
        show_pages(report_lines(report))


def search_limits(session, region, services, context, show_catalog=True):
    try:
        while services:
            service = limit_search_service(services, show_catalog=show_catalog)
            if service is None:
                return
            search_service_limits(session, region, service, context)
            show_catalog = False
    except Exception as error:
        print_request_error('Limit lookup failed; returning to the previous menu', error, debug=session.debug)


def show_tenancy_limits(session):
    """Keep region selection, capacity history and the last capacity report intact."""
    try:
        region = select_region(session)
        if region is None:
            return
        context = select_usage_scope(session)
        if context is None:
            return
        try:
            available_services = services_in_region(session, region)
        except Exception as error:
            print_request_error('Limits service list unavailable',
                                error, debug=session.debug)
            return
        if not available_services:
            print(yellow('No Limits services are available.'))
            return
        while True:
            selection = select_services(available_services, region, context)
            if selection is None:
                return
            if selection is LIMIT_LOOKUP:
                search_limits(session, region, available_services, context, show_catalog=False)
                continue
            services, dynamic_only, all_services = selection
            show_hidden = False
            while True:
                report = collect_limits(session, region, services, dynamic_only,
                                        usage_compartment_id=context['usage_compartment_id'],
                                        include_hidden_usage=show_hidden)
                show_pages(report_lines(report, show_hidden=show_hidden))
                while True:
                    visibility = '[V] Hide zero/deprecated limits' if show_hidden else '[V] Show zero/deprecated limits'
                    action = command_choice('Action > ', '[L] List/search limit names   ' + visibility +
                                            '   [R] Refresh   [E] Export report   [B] Back to services   [Q] Quit')
                    if action is None:
                        break
                    if action.upper() in {'E', 'EXPORT'}:
                        export_interactive(report)
                    elif action.upper() in {'V', 'VISIBILITY'}:
                        show_hidden = not show_hidden
                        if show_hidden and not report.get('include_hidden_usage', False):
                            report = complete_hidden_usage(session, region, report)
                        show_pages(report_lines(report, show_hidden=show_hidden))
                    elif action.upper() in {'L', 'LIMITS', 'NAMES'}:
                        search_limits(session, region, available_services, context)
                    elif action.upper() in {'R', 'REFRESH'}:
                        if all_services:
                            available_services = services_in_region(session, region)
                            services = available_services
                        break
                    else:
                        print(red('Choose L, V, R, E, B or Q.'))
                if action is None:
                    break
    except KeyboardInterrupt:
        status_message('Tenancy limits cancelled. Returning to the menu.')
        # Workers have drained; return to a fresh cancellation scope in main.
        raise RestartFlowException
    except Exception as error:
        print_request_error('Tenancy limits failed', error, debug=session.debug)
