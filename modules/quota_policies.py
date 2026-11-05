# coding: utf-8
"""Read existing quota policy text; never infer effective quotas from rules."""

from contextlib import closing
from collections import deque
import csv
import io
import json
from types import SimpleNamespace
from threading import local

import oci

from modules.execution import build_retry_strategy, cancellable_call, check_cancelled, client_options, region_tasks
from modules.exports import write_export, csv_cell
from modules.pager import PageBuffer, show_pages
from modules.results import print_request_error, write_wrapped
from modules.tenancy_limits import (LimitsProgress, error_record, export_interactive,
                                   loading, paginated, select_usage_scope, usage_context, utc_now)
from modules.utils import cyan, red, yellow
from modules.navigation import command_choice
from modules.identity import inspect_compartment


def quota_error(stage, error, compartment_id=None, quota_id=None):
    result = error_record(stage, 'quota-policies', error)
    result.update(compartment_id=compartment_id, quota_id=quota_id)
    if result['summary'] != 'Access denied':
        result['summary'] = ('OCI Identity request failed' if stage in
                             {'list_compartments', 'get_compartment', 'get_tenancy'} else
                             'OCI Quotas request failed')
    return result


def policy_storage_compartments(session, target, report, progress):
    """Discover only the selected policy-storage source and active descendants."""
    root = session.config['tenancy']
    names = {}
    compartments = [target]

    def failed(stage, error, compartment_id):
        report['errors'].append(quota_error(stage, error, compartment_id))
        if session.debug:
            print_request_error(f'Quota policy compartments / {stage}', error, debug=True)

    with loading('Checking quota source compartment with OCI Identity...'):
        identity = session.identity_client(session.home_region)
        progress.request_started()
        item = inspect_compartment(identity, target, root)
        names[target] = getattr(item, 'name', None)

    pending = deque([target])
    visited = set(compartments)
    while pending:
        parent = pending.popleft()
        check_cancelled()
        try:
            with loading(f'Discovering quota policy storage compartments in {parent}...'):
                options = dict(access_level='ANY', lifecycle_state='ACTIVE')
                # OCI supports the recursive flag only at the tenancy root.
                if parent == root:
                    options['compartment_id_in_subtree'] = True
                items = paginated(identity.list_compartments, parent, progress=progress, **options)
            for item in sorted(items, key=lambda item: (getattr(item, 'name', '') or '', item.id)):
                if item.id in visited:
                    continue
                visited.add(item.id)
                names[item.id] = getattr(item, 'name', None)
                compartments.append(item.id)
                if target != root:
                    pending.append(item.id)
        except Exception as error:
            failed('list_compartments', error, parent)
    return compartments, names


def collect_policies(session, policy_compartment_id=None):
    region = session.home_region
    if region is None or not getattr(region, 'region_name', None):
        raise ValueError('The tenancy home region is required to read quota policies.')
    # This is a policy-storage inventory, not a claim to identify every rule
    # affecting a compartment through ancestors or other storage compartments.
    target = session.compartment_id if policy_compartment_id is None else policy_compartment_id
    usage_context(session, target)
    report = dict(schema_version=1, report_type='quota_policies', region=region.region_name,
                  tenancy_id=session.config['tenancy'], source_compartment_id=target,
                  collected_at=utc_now(), finished_at=None, is_partial=False, policies=[], errors=[])
    progress = LimitsProgress(0, label='Reading quota policies', unit='policies')
    compartments, names = policy_storage_compartments(session, target, report, progress)
    report.update(policy_compartment_ids=compartments, policy_compartment_names=names,
                  source_compartment_name=names.get(target))
    clients = local()

    def client():
        if not hasattr(clients, 'quota'):
            clients.quota = oci.limits.QuotasClient(config=session.region_config(region),
                                                   signer=session.signer, **client_options())
        return clients.quota

    summaries = {}
    listing_progress = LimitsProgress(len(compartments), label='Reading quota policy lists', unit='compartments')
    listing_jobs = [SimpleNamespace(region_name=names.get(compartment_id) or compartment_id,
                                    compartment_id=compartment_id) for compartment_id in compartments]

    def list_policies(job):
        items = paginated(client().list_quotas, job.compartment_id, lifecycle_state='ACTIVE',
                          sort_by='NAME', progress=listing_progress)
        values = [oci.util.to_dict(item) for item in items]
        if any(not isinstance(item, dict) or not item.get('id') for item in values):
            raise ValueError('OCI returned a quota summary without an ID')
        return values

    listed = {}
    with closing(region_tasks(listing_jobs, list_policies, progress=listing_progress, max_workers=4)) as tasks:
        for job, values, error in tasks:
            if error is not None:
                report['errors'].append(quota_error('list_quotas', error, job.compartment_id))
                if session.debug:
                    print_request_error('Quota policies / list', error, debug=True)
            else:
                listed[job.compartment_id] = values
    progress.requests += listing_progress.requests
    for compartment_id in compartments:
        for summary in listed.get(compartment_id, ()):
            summaries.setdefault(summary['id'], (compartment_id, summary))
    jobs = [SimpleNamespace(region_name=summary.get('name') or quota_id, quota_id=quota_id,
                            compartment_id=compartment_id, summary=summary)
            for quota_id, (compartment_id, summary) in sorted(summaries.items(),
            key=lambda item: (item[1][1].get('name') or '', item[0]))]
    progress.total = len(jobs)

    def details(job):
        progress.request_started()
        policy = oci.util.to_dict(cancellable_call(client().get_quota, job.quota_id,
                                                 retry_strategy=build_retry_strategy()).data)
        if not isinstance(policy, dict) or not policy:
            raise ValueError('OCI returned no quota policy detail')
        return policy

    outcomes = {}
    with closing(region_tasks(jobs, details, progress=progress, max_workers=4)) as tasks:
        for job, policy, error in tasks:
            if error is not None:
                report['errors'].append(quota_error('get_quota', error, job.compartment_id, job.quota_id))
                if session.debug:
                    print_request_error(f'Quota policies / {job.region_name}', error, debug=True)
            outcomes[job.quota_id] = policy if error is None else None
    for job in jobs:
        policy = outcomes[job.quota_id]
        source = policy or {}
        row = {key: source.get(key) if source.get(key) is not None else job.summary.get(key)
               for key in ('id', 'name', 'description', 'compartment_id', 'lifecycle_state', 'time_created')}
        row['compartment_id'] = row['compartment_id'] or job.compartment_id
        row['compartment_name'] = names.get(row['compartment_id'])
        statements = source.get('statements')
        row.update(statements=statements, statements_status='Provided' if statements is not None else 'Not returned',
                   summary=job.summary, policy=policy)
        report['policies'].append(row)
    report.update(finished_at=utc_now(), is_partial=bool(report['errors']), requests_count=progress.requests)
    return report


def policy_lines(report):
    output = PageBuffer()
    write_wrapped(output, 'QUOTA POLICIES', color=cyan)
    write_wrapped(output, f"Source: OCI Quotas API | Home region: {report['region']}")
    write_wrapped(output, f"Collected: {report['collected_at']} | Finished: {report['finished_at']}")
    write_wrapped(output, 'Search includes only the selected source compartment and its active descendants. '
                  'Other storage branches are not scanned. Rules may target other compartments or regions.')
    if not report['policies']:
        write_wrapped(output, 'No quota policies returned.' if not report['errors'] else
                      'No quota policies returned; see collection errors below.')
    for number, policy in enumerate(report['policies'], 1):
        output.write()
        write_wrapped(output, f"{number:02d}: {policy['name'] or policy['id']}", color=cyan)
        for label, field in (('Description', 'description'), ('Policy ID', 'id'),
                             ('Stored in compartment', 'compartment_id'), ('State', 'lifecycle_state'),
                             ('Created', 'time_created')):
            value = policy[field] if policy[field] is not None else 'Not provided'
            if field == 'compartment_id':
                value = f"{policy.get('compartment_name') or 'Name unavailable'} ({value})"
            write_wrapped(output, f'{label}: {value}')
        write_wrapped(output, 'Statements:')
        if policy['statements'] is None:
            write_wrapped(output, 'Not returned by OCI; see errors if the detail request failed.', prefix='  ')
        elif not policy['statements']:
            write_wrapped(output, 'Empty list returned by OCI.', prefix='  ')
        else:
            for index, statement in enumerate(policy['statements'], 1):
                write_wrapped(output, statement, prefix=f'  {index:02d}. ')
    if report['errors']:
        output.write()
        write_wrapped(output, f"PARTIAL REPORT — {len(report['errors'])} failed operation(s)", color=yellow)
        for error in report['errors']:
            write_wrapped(output, f"{error['stage']} / {error['quota_id'] or error['compartment_id']}: "
                          f"{error['summary']} (HTTP {error['http_status'] or 'N/A'})")
    return output.lines


def export_policies(report, path, file_format):
    if file_format == 'json':
        content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    elif file_format == 'csv':
        fields = ('collected_at', 'finished_at', 'region', 'tenancy_id', 'is_partial', 'record_type',
                  'id', 'name', 'description', 'compartment_id', 'compartment_name', 'lifecycle_state', 'time_created',
                  'statements_status', 'statement_number', 'statement', 'stage', 'quota_id',
                  'http_status', 'code', 'request_id', 'error_summary',
                  'source_compartment_id', 'source_compartment_name')
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        common = {field: report[field] for field in fields[:5]}
        common.update(source_compartment_id=report.get('source_compartment_id'),
                      source_compartment_name=report.get('source_compartment_name') or
                      report.get('policy_compartment_names', {}).get(report.get('source_compartment_id')))

        def write(row):
            writer.writerow({key: csv_cell(value) for key, value in row.items()})

        for policy in report['policies']:
            base = dict(common, record_type='policy', **{key: policy.get(key) for key in fields[6:14]})
            if policy['statements']:
                for index, statement in enumerate(policy['statements'], 1):
                    write(dict(base, statement_number=index, statement=statement))
            else:
                write(base)
        for error in report['errors']:
            row = dict(common, record_type='error', error_summary=error['summary'])
            row.update({key: error[key] for key in ('stage', 'quota_id', 'compartment_id', 'http_status', 'code', 'request_id')})
            write(row)
        if not report['policies'] and not report['errors']:
            write(dict(common, record_type='report'))
        content = stream.getvalue()
    else:
        raise ValueError('Choose JSON or CSV.')
    return write_export(content, path)


def show_quota_policies(session, policy_compartment_id=None):
    try:
        if policy_compartment_id is None:
            context = select_usage_scope(session)
            if context is None:
                return
            policy_compartment_id = context['usage_compartment_id']
        while True:
            report = collect_policies(session, policy_compartment_id=policy_compartment_id)
            show_pages(policy_lines(report))
            while True:
                action = command_choice('Action > ', '[R] Refresh policies   [E] Export policies   '
                                        '[B] Back to main menu   [Q] Quit')
                if action is None:
                    return
                if action.upper() in {'E', 'EXPORT'}:
                    export_interactive(report, exporter=export_policies, prefix='quota_policies', title='Quota policies')
                elif action.upper() in {'R', 'REFRESH'}:
                    break
                else:
                    print(red('Choose R, E, B or Q.'))
    except Exception as error:
        print_request_error('Quota policies failed', error, debug=session.debug)
