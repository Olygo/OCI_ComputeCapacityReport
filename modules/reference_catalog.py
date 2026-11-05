# coding: utf-8
"""Best-effort GitHub refresh and local OCI metadata reference. No OCI calls."""

from datetime import datetime, timezone
from functools import lru_cache
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from urllib.request import Request, urlopen
from modules.terminal import plain_text, terminal_output, status_line, clear_status


CATALOG_URL = 'https://raw.githubusercontent.com/Olygo/OCI_ComputeCapacityReport/main/shape_catalog.json'
CATALOG_PATH = Path(__file__).resolve().parent.parent / 'shape_catalog.json'
MAX_CATALOG_BYTES = 16 * 1024 * 1024
DOWNLOAD_TIMEOUT = 5


def catalog_date(value):
    if not isinstance(value, str):
        raise ValueError('catalog_version must be a UTC timestamp')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('catalog_version must include a timezone')
    return stamp.astimezone(timezone.utc)


def parse_catalog(content):
    """Validate the exact serialized bytes consumed or about to be written."""
    if not isinstance(content, (str, bytes, bytearray)):
        raise ValueError('Catalog content must be JSON text or bytes')
    size = len(content.encode('utf-8')) if isinstance(content, str) else len(content)
    if size > MAX_CATALOG_BYTES:
        raise ValueError('Catalog exceeds the size limit')

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result

    data = json.loads(content, object_pairs_hook=unique_object)
    return validate_catalog(data)


def validate_catalog(data):
    """Shared metadata rules; validate without normalizing or dropping fields."""
    def valid_values(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError('Catalog object keys must be strings')
                valid_values(item)
        elif isinstance(value, list):
            for item in value:
                valid_values(item)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError('Catalog contains a non-finite number')
        elif value is not None and not isinstance(value, (str, int, bool)):
            raise ValueError('Catalog contains a non-JSON value')

    valid_values(data)
    if not isinstance(data, dict) or set(data) != {'catalog_version', 'shapes'}:
        raise ValueError('Expected catalog_version and shapes')
    catalog_date(data['catalog_version'])
    shapes = data['shapes']
    if not isinstance(shapes, dict) or not shapes:
        raise ValueError('Catalog has no shape properties')
    for name, properties in shapes.items():
        if not name.strip() or not isinstance(properties, dict) or properties.get('shape') != name:
            raise ValueError(f'Invalid shape entry: {name}')
        for field, fields in (('ocpu_options', ('min', 'max')),
                              ('memory_options', ('min_in_g_bs', 'max_in_g_bs',
                                                  'min_per_ocpu_in_gbs', 'max_per_ocpu_in_gbs'))):
            options = properties.get(field)
            if options is not None and not isinstance(options, dict):
                raise ValueError(f'Invalid {field} for {name}')
            for key in fields:
                value = (options or {}).get(key)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0):
                    raise ValueError(f'Invalid {field}.{key} for {name}')
        for field in ('ocpus', 'memory_in_gbs'):
            value = properties.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
                raise ValueError(f'Invalid {field} for {name}')
        cpu = properties.get('ocpu_options') or {}
        memory = properties.get('memory_options') or {}
        for options, lower, upper, label in (
                (cpu, 'min', 'max', 'OCPU bounds'),
                (memory, 'min_in_g_bs', 'max_in_g_bs', 'memory bounds'),
                (memory, 'min_per_ocpu_in_gbs', 'max_per_ocpu_in_gbs', 'memory/OCPU ratio')):
            if (options.get(lower) is not None and options.get(upper) is not None
                    and options[lower] > options[upper]):
                raise ValueError(f'Inconsistent {label} for {name}: {lower} exceeds {upper}')
        # Check only contradictions demonstrated by provided option bounds.
        # SDK default values are not assumed to be a valid launch configuration.
        # Decimal representations avoid float-product rounding/overflow here.
        for cpu_bound, ratio_bound, memory_bound, exceeds in (
                (cpu.get('min'), memory.get('min_per_ocpu_in_gbs'), memory.get('max_in_g_bs'), True),
                (cpu.get('max'), memory.get('max_per_ocpu_in_gbs'), memory.get('min_in_g_bs'), False)):
            if all(value is not None for value in (cpu_bound, ratio_bound, memory_bound)):
                product = Fraction(str(cpu_bound)) * Fraction(str(ratio_bound))
                bound = Fraction(str(memory_bound))
                if (product > bound if exceeds else product < bound):
                    raise ValueError(f'Inconsistent CPU/memory bounds for {name}: no feasible configuration')
    return data


def write_catalog(content, path):
    parse_catalog(content)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('wb', dir=path.parent, prefix='.' + path.name + '.',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_reference_catalog(debug=False, path=None, url=None):
    """Refresh once per launch. Any failure preserves the loaded local reference."""
    path = Path(path) if path is not None else CATALOG_PATH
    url = url or CATALOG_URL
    local = None

    def status(message):
        if debug:
            print(plain_text(f'[Shape reference] {message}'), flush=True)
        elif terminal_output():
            status_line(message)

    try:
        try:
            with path.open('rb') as stream:
                local = parse_catalog(stream.read(MAX_CATALOG_BYTES + 1))
            status(f"Local shape reference: {local['catalog_version']} ({len(local['shapes'])} shapes)")
        except Exception as error:
            status(f'Local shape reference unavailable: {error}')
        try:
            status(f'Checking shape reference updates: {url}')
            if not url.startswith('https://'):
                raise ValueError('Catalog download requires HTTPS')
            request = Request(url, headers={'User-Agent': 'OCI-ComputeCapacityReport', 'Accept': 'application/json'})
            with urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
                if not response.geturl().startswith('https://'):
                    raise ValueError('Catalog redirect requires HTTPS')
                content = response.read(MAX_CATALOG_BYTES + 1)
            remote = parse_catalog(content)
            if local is None or catalog_date(remote['catalog_version']) > catalog_date(local['catalog_version']):
                write_catalog(content, path)
                local = remote
                status(f"Shape reference updated: {local['catalog_version']} ({len(local['shapes'])} shapes)")
            else:
                status('Local shape reference is up to date')
        except Exception as error:
            status(f'Shape reference update failed; keeping local file: {error}')
        if debug and local is None:
            status('No valid local reference; continuing with OCI and existing configuration rules')
        return local
    finally:
        if not debug:
            clear_status()


@lru_cache(maxsize=None)
def sdk_fields(model_name):
    """Read the installed SDK's field order without deserializing source data."""
    try:
        import oci
        model_class = getattr(oci.core.models, model_name, None)
        if model_class is None:
            return ()
        model = model_class()
        return tuple(getattr(model, 'swagger_types', {}).items())
    except (ImportError, AttributeError, TypeError):
        return ()


def sdk_order(value, model_name='Shape'):
    if isinstance(value, list):
        item_type = model_name[5:-1] if model_name.startswith('list[') else ''
        return [sdk_order(item, item_type) for item in value]
    if not isinstance(value, dict):
        return value
    fields = dict(sdk_fields(model_name))
    names = [name for name in fields if name in value]
    names.extend(name for name in value if name not in fields)
    return {name: sdk_order(value[name], fields.get(name, '')) for name in names}


def property_dict(shape):
    """Use SDK field order for either source, retaining all unknown JSON fields."""
    if not isinstance(shape, dict):
        import oci
        shape = oci.util.to_dict(shape)
    return sdk_order(shape)


def property_order_key(path):
    """Order a union of comparison paths, even when either side lacks fields."""
    model_name = 'Shape'
    key = []
    for part in path:
        if isinstance(part, int):
            key.append((0, part))
            model_name = model_name[5:-1] if model_name.startswith('list[') else ''
        else:
            fields = dict(sdk_fields(model_name))
            names = list(fields)
            key.append((0, names.index(part)) if part in fields else (1, part))
            model_name = fields.get(part, '')
    return tuple(key)


def property_object(properties):
    if isinstance(properties, dict):
        return SimpleNamespace(**{key: property_object(value) for key, value in properties.items()})
    if isinstance(properties, list):
        return [property_object(value) for value in properties]
    return properties
