# coding: utf-8

from dataclasses import dataclass, field
from collections import Counter
from datetime import datetime, timezone
import shutil
import textwrap

import oci

from modules.utils import red, green, yellow, cyan
from modules.terminal import plain_text


REQUEST_ERROR = 'REQUEST_ERROR'


@dataclass(frozen=True)
class ShapeResources:
    ocpus: object = None
    memory_in_gbs: object = None
    nvmes: object = None


@dataclass(frozen=True)
class RequestError:
    category: str
    summary: str
    message: str
    exception_type: str
    http_status: object = None
    code: object = None
    request_id: object = None
    sdk_version: str = ''
    sent_configuration: object = None


@dataclass(frozen=True)
class CapacityResult:
    region: str
    availability_domain: object
    fault_domain: object
    shape: str
    requested: ShapeResources
    applied: object
    status: str
    stage: str = 'capacity'
    available_count: object = None
    error: object = None


@dataclass
class ReportSnapshot:
    """Last report for export only; never used to answer another search."""
    shape: str
    requested_regions: tuple
    queried_regions: tuple
    compartment_id: str
    requested: ShapeResources
    all_regions: bool
    drcc: bool
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: object = None
    results: tuple = ()
    outcome: str = 'RUNNING'
    interrupted: bool = False
    is_partial: bool = False
    completed_regions: tuple = ()
    requests_count: int = 0
    elapsed_seconds: float = 0.0

    def finish(self, results, completed_regions, requests_count, elapsed_seconds,
               interrupted=False):
        self.results = tuple(results)
        self.completed_regions = tuple(completed_regions)
        self.requests_count = requests_count
        self.elapsed_seconds = elapsed_seconds
        self.finished_at = datetime.now(timezone.utc)
        self.interrupted = interrupted
        self.is_partial = interrupted or len(self.completed_regions) != len(self.queried_regions)
        self.outcome = ('INTERRUPTED' if interrupted else
                        'ERROR' if any(result.error is not None for result in self.results) else 'COMPLETED')


def describe_error(error, sent_configuration=None):
    """Keep full diagnostics in data; normal output uses a bounded description."""
    code = getattr(error, 'code', None)
    status = getattr(error, 'status', None)
    if code == 'InvalidParameter':
        category, summary = 'CONFIGURATION', 'Configuration rejected by OCI'
    elif code == 'NotAuthorizedOrNotFound':
        category = 'ACCESS_OR_NOT_FOUND'
        summary = 'OCI could not authorize the request or find the resource; check resource and permissions'
    elif status in (401, 403):
        category, summary = 'ACCESS', 'Access denied by OCI; check permissions'
    elif isinstance(error, ValueError):
        category, summary = 'CONFIGURATION', 'Configuration validation failed'
    elif isinstance(error, (TimeoutError, ConnectionError, OSError)) or any(
            cls.__name__ in {'RequestException', 'ConnectionError', 'ConnectTimeout',
                             'ReadTimeout', 'Timeout', 'MaxRetryError'}
            for cls in type(error).__mro__):
        category, summary = 'TRANSPORT', 'Failed to connect'
    elif status is not None or code is not None:
        category, summary = 'SERVICE', 'OCI request failed'
    else:
        category, summary = 'UNEXPECTED', 'Request failed'
    return RequestError(
        category, summary, str(getattr(error, 'message', None) or error),
        type(error).__name__, status, code, getattr(error, 'request_id', None),
        getattr(oci, '__version__', 'unknown'), sent_configuration
    )


def error_result(region, ad, fd, shape, requested, error, *, applied=None,
                 stage='capacity', sent_configuration=None):
    return CapacityResult(region, ad, fd, shape, requested, applied, REQUEST_ERROR,
                          stage=stage, error=describe_error(error, sent_configuration))


def diagnostic_lines(error):
    return (
        f'Exception: {error.exception_type}',
        f'OCI SDK version: {error.sdk_version}',
        f'HTTP status: {error.http_status} | OCI code: {error.code}',
        f'opc-request-id: {error.request_id or "not provided"}',
        f'Shape config: {error.sent_configuration}',
        f'Details: {error.message}',
    )


def print_request_error(context, error, *, debug=False, output=None):
    """Compact diagnostics for operations outside a capacity report."""
    write = output.write if output is not None else print
    detail = describe_error(error)
    code = f' ({detail.code})' if detail.code else ''
    write(red(f'{context}: {detail.summary}{code}'))
    if debug:
        for line in diagnostic_lines(detail):
            write(plain_text(line))


def status_color(status):
    if status == 'AVAILABLE':
        return green
    if status == 'OUT_OF_HOST_CAPACITY':
        return yellow
    if status in {'HARDWARE_NOT_SUPPORTED', REQUEST_ERROR}:
        return red
    return cyan


def write_wrapped(output, text, *, prefix='', color=None):
    text, prefix = plain_text(text), plain_text(prefix)
    width = max(1, shutil.get_terminal_size((120, 24)).columns)
    for original in str(text).splitlines() or ['']:
        if len(prefix) >= width:
            for line in textwrap.wrap(prefix, width, break_long_words=True, break_on_hyphens=False):
                output.write(color(line) if color else line)
            indent = ''
        else:
            indent = prefix
        lines = textwrap.wrap(original, max(1, width - len(indent)),
                              break_long_words=True, break_on_hyphens=False) or ['']
        for index, line in enumerate(lines):
            line = (indent if index == 0 else ' ' * len(indent)) + line
            output.write(color(line) if color else line)


class ReportRenderer:
    """Use columns when they fit; retain full values in a narrow terminal."""
    column_separator = '   '

    def __init__(self, regions=(), shape=None, requested=None):
        # Reserve column widths once, without fetching any extra OCI metadata.
        region_width = max([len('REGION'), *(len(name) for name in regions)]) if regions else 20
        requested = requested or ShapeResources()
        self.widths = {
            'REGION': region_width,
            'AVAILABILITY_DOMAIN': max(len('AVAILABILITY_DOMAIN'), region_width + 14),
            'FAULT_DOMAIN': len('FAULT-DOMAIN-1'),
            'SHAPE': max(len('SHAPE'), len(shape) if shape is not None else 32),
            'OCPU': max(8, len(str(requested.ocpus))),
            'MEMORY': max(10, len(str(requested.memory_in_gbs))),
            'AVAILABLE_COUNT': len('AVAILABLE_COUNT'),
            'AVAILABILITY': max(map(len, (
            'AVAILABILITY', 'AVAILABLE', 'OUT_OF_HOST_CAPACITY',
            'HARDWARE_NOT_SUPPORTED', REQUEST_ERROR
        )))}
        self.header_printed = False

    def render(self, result, output, drcc=False):
        values = result.applied or ShapeResources()
        fields = [
            ('REGION', result.region),
            ('AVAILABILITY_DOMAIN', result.availability_domain or '-'),
            ('FAULT_DOMAIN', result.fault_domain or '-'),
            ('SHAPE', result.shape),
            ('OCPU', values.ocpus if values.ocpus is not None else '-'),
            ('MEMORY', values.memory_in_gbs if values.memory_in_gbs is not None else '-'),
        ]
        if drcc:
            # Keep zero displayed as "-", as in the original report.
            fields.append(('AVAILABLE_COUNT', result.available_count if result.available_count else '-'))
        fields.append(('AVAILABILITY', result.status))
        fields = [(label, plain_text(value)) for label, value in fields]
        width = max(1, shutil.get_terminal_size((120, 24)).columns)
        wide = (sum(self.widths[label] for label, _ in fields)
                + len(self.column_separator) * (len(fields) - 1) <= width)
        if wide:
            if not self.header_printed:
                output.write('\n' + self.column_separator.join(label.ljust(self.widths[label]) for label, _ in fields))
                self.header_printed = True
            # An unexpectedly long AD, number or future status wraps inside its
            # existing cell instead of shifting columns or losing characters.
            cells = [textwrap.wrap(value, self.widths[label], break_long_words=True,
                                   break_on_hyphens=False) or [''] for label, value in fields]
            for line_index in range(max(map(len, cells))):
                parts = []
                for (label, value), cell in zip(fields, cells):
                    text = cell[line_index] if line_index < len(cell) else ''
                    padded = text.ljust(self.widths[label])
                    parts.append(status_color(value)(padded) if label == 'AVAILABILITY' else padded)
                output.write(self.column_separator.join(parts))
        else:
            output.write('-' * min(width, 80))
            for label, value in fields:
                write_wrapped(output, value, prefix=label + ': ',
                              color=status_color(value) if label == 'AVAILABILITY' else None)
        if result.error:
            code = f'/{result.error.code}' if result.error.code else ''
            write_wrapped(output, f'ERROR [{result.stage}]: {result.error.category}{code}', color=red)


def emit_result(result, output, drcc=False, debug=False):
    if hasattr(output, 'result'):
        output.result(result, drcc, debug)
    else:
        render_result(result, output, drcc, debug)


def render_result(result, output, drcc=False, debug=False, renderer=None):
    renderer = renderer if renderer is not None else ReportRenderer()
    renderer.render(result, output, drcc)
    if debug and result.error:
        write_wrapped(output, f'Debug: {result.region} / {result.availability_domain or "-"} / '
                      f'{result.fault_domain or "-"} / {result.shape} / {result.stage}')
        write_wrapped(output, f'Requested: {result.requested}; applied: {result.applied}')
        for line in diagnostic_lines(result.error):
            write_wrapped(output, line)


def render_error_summary(results, output):
    failures = [result for result in results if result.error is not None]
    if not failures:
        return
    write_wrapped(output, f'\nErrors: {len(failures)} failed operation(s). REQUEST_ERROR is a local error, not an OCI capacity status.')
    for result in failures:
        location = f'{result.region} / {result.availability_domain or "-"} / {result.fault_domain or "-"}'
        code = f' ({result.error.code})' if result.error.code else ''
        write_wrapped(output, f'- {location} / {result.shape} [{result.stage}]: {result.error.summary}{code}')


def render_query_summary(results, output, elapsed, requests, interrupted=False,
                         completed_regions=None, total_regions=None):
    statuses = Counter(result.status for result in results if result.error is None)
    errors = sum(result.error is not None for result in results)
    available = statuses.pop('AVAILABLE', 0)
    exhausted = statuses.pop('OUT_OF_HOST_CAPACITY', 0)
    label = 'Partial summary (interrupted)' if interrupted else 'Summary'
    fields = ([f'regions {completed_regions}/{total_regions}'] if total_regions is not None else [])
    fields += [f'available {available}', f'out of host capacity {exhausted}',
               f'other OCI statuses {sum(statuses.values())}', f'errors {errors}',
               f'requests {requests}', f'elapsed {elapsed:.1f}s']
    width = max(1, shutil.get_terminal_size((120, 24)).columns)
    output.write('')
    line = f'{label}: '
    for field in fields:
        separator = '' if line.endswith(': ') or not line else ' | '
        if len(line + separator + field) > width:
            write_wrapped(output, line.rstrip())
            line = field
        else:
            line += separator + field
    if line:
        write_wrapped(output, line)
    if statuses:
        write_wrapped(output, 'Other OCI statuses: ' + ', '.join(
            f'{status}={count}' for status, count in sorted(statuses.items())))
