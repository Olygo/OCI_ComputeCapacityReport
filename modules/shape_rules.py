# coding: utf-8

from dataclasses import dataclass
from math import floor, isfinite
from types import SimpleNamespace


SHAPE_LIMITS_SOURCE = 'https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm'
SHAPE_LIMITS_CHECKED = '2026-10-21'

# Each row defines OCPUs, GB and NVMe drives for both the menu and API payload.
DENSEIO_PROFILES = {
    'VM.DenseIO.E4.Flex': ((8, 128, 1), (16, 256, 2), (32, 512, 4)),
    'VM.DenseIO.E5.Flex': ((8, 96, 1), (16, 192, 2), (24, 288, 3),
                         (32, 384, 4), (40, 480, 5), (48, 576, 6)),
    'VM.DenseIO.E6.Ax.Flex': ((8, 96, 1), (16, 192, 2), (24, 288, 3),
                            (32, 384, 4), (40, 480, 5), (48, 576, 6)),
}

# Only fill limits missing from OCI metadata. A4 AX's 2 GB/OCPU minimum follows
# the capacity API's observed validation, including its reported ratio of 0.5
# for 1 OCPU / 1 GB. It is not a claim that the public table states that minimum.
AX_FLEX_FALLBACK_LIMITS = {
    'VM.Standard.E6.Ax.Flex': {'max_ocpus': 94, 'max_memory': 712, 'min_memory_per_ocpu': 1, 'max_memory_per_ocpu': 64},
    'VM.Standard4.Ax.Flex': {'max_ocpus': 39, 'max_memory': 360, 'min_memory_per_ocpu': 1, 'max_memory_per_ocpu': 64},
    'VM.Standard.A4.Ax.Flex': {'max_ocpus': 45, 'max_memory': 720, 'min_memory_per_ocpu': 2, 'max_memory_per_ocpu': 64},
}

MIN_MEMORY_PER_OCPU = {
    'VM.Standard.A2.Flex': 2,
    'VM.Standard.A4.Flex': 2,
    'VM.Standard.A4.Ax.Flex': 2,
}


@dataclass(frozen=True)
class DenseIOConfig:
    ocpus: float
    memory_in_gbs: float
    nvmes: int


@dataclass(frozen=True)
class ShapeConfiguration:
    ocpus: float
    memory_in_gbs: float
    is_flex: bool
    used_fallback: bool = False
    upper_limits_known: bool = True


def denseio_ocpu_choices(shape_name):
    return [str(cpu) for cpu, _, _ in DENSEIO_PROFILES[shape_name]]


def denseio_config(shape_name, ocpus):
    if shape_name not in DENSEIO_PROFILES:
        return None
    for cpu, memory, nvmes in DENSEIO_PROFILES[shape_name]:
        if cpu == ocpus:
            return DenseIOConfig(float(cpu), float(memory), nvmes)
    raise ValueError(f'Invalid OCPU amount for {shape_name}; valid values: {denseio_ocpu_choices(shape_name)}')


def _denseio_with_metadata(shape_name, ocpus, shape_info):
    requested = denseio_config(shape_name, ocpus)
    cpu_options = getattr(shape_info, 'ocpu_options', None)
    memory_options = getattr(shape_info, 'memory_options', None)
    limits = [(cpu_options, 'min', lambda item: item.ocpus, False),
              (cpu_options, 'max', lambda item: item.ocpus, True),
              (memory_options, 'min_in_g_bs', lambda item: item.memory_in_gbs, False),
              (memory_options, 'max_in_g_bs', lambda item: item.memory_in_gbs, True),
              (memory_options, 'min_per_ocpu_in_gbs', lambda item: item.memory_in_gbs / item.ocpus, False),
              (memory_options, 'max_per_ocpu_in_gbs', lambda item: item.memory_in_gbs / item.ocpus, True)]
    candidates = [denseio_config(shape_name, cpu) for cpu, _, _ in DENSEIO_PROFILES[shape_name]]
    for options, attribute, get_value, is_max in limits:
        value = _number(getattr(options, attribute, None), attribute)
        if value is not None:
            candidates = [item for item in candidates
                          if (get_value(item) <= value if is_max else get_value(item) >= value)]
    if not candidates:
        raise ValueError(f'No documented DenseIO preset matches OCI metadata for {shape_name}')
    return min(candidates, key=lambda item: (abs(item.ocpus - requested.ocpus), item.ocpus))


def _number(value, label):
    if value is None:
        return None
    if not isinstance(value, (int, float)) or (isinstance(value, float) and not isfinite(value)):
        raise ValueError(f'Invalid numeric value for {label}: {value}')
    return value


def merge_shape_metadata(preferred, regional):
    """Use AD fields first and fill absent fields from regional metadata.

    A null option object accompanied by a fixed AD value remains fixed, as
    documented by the SDK. Neither original SDK object is modified.
    """
    if preferred is None:
        return regional
    if regional is None:
        return preferred

    def value(attribute):
        first = getattr(preferred, attribute, None)
        return first if first is not None else getattr(regional, attribute, None)

    def options(attribute, fixed_attribute, fields):
        first = getattr(preferred, attribute, None)
        if first is None and getattr(preferred, fixed_attribute, None) is not None:
            return None
        second = getattr(regional, attribute, None)
        if first is None:
            return second
        return SimpleNamespace(**{
            field: (getattr(first, field, None) if getattr(first, field, None) is not None
                    else getattr(second, field, None)) for field in fields
        })

    return SimpleNamespace(
        shape=value('shape'), ocpus=value('ocpus'), memory_in_gbs=value('memory_in_gbs'),
        ocpu_options=options('ocpu_options', 'ocpus', ('min', 'max')),
        memory_options=options('memory_options', 'memory_in_gbs',
                               ('min_in_g_bs', 'max_in_g_bs', 'min_per_ocpu_in_gbs', 'max_per_ocpu_in_gbs'))
    )


def normalize_shape_config(shape_name, shape_info, ocpus, memory):
    """Apply one set of CPU/memory rules, with OCI metadata taking precedence."""
    is_flex = '.Flex' in shape_name and not shape_name.startswith('BM.')
    if not is_flex:
        return ShapeConfiguration(ocpus, memory, False)

    if shape_name in DENSEIO_PROFILES:
        preset = _denseio_with_metadata(shape_name, ocpus, shape_info)
        return ShapeConfiguration(preset.ocpus, preset.memory_in_gbs, True)

    fallback = AX_FLEX_FALLBACK_LIMITS.get(shape_name, {})
    cpu_options = getattr(shape_info, 'ocpu_options', None)
    memory_options = getattr(shape_info, 'memory_options', None)
    fixed_cpu = _number(getattr(shape_info, 'ocpus', None), 'ocpus') if cpu_options is None else None
    fixed_memory = _number(getattr(shape_info, 'memory_in_gbs', None), 'memory_in_gbs') if memory_options is None else None
    used_fallback = False

    def limit(options, attribute, fallback_key=None):
        nonlocal used_fallback
        value = _number(getattr(options, attribute, None), attribute)
        if value is None and fallback_key in fallback:
            used_fallback = True
            value = fallback[fallback_key]
        return value

    cpu_min = limit(cpu_options, 'min')
    cpu_max = limit(cpu_options, 'max', 'max_ocpus' if fixed_cpu is None else None)
    memory_min = limit(memory_options, 'min_in_g_bs')
    memory_max = limit(memory_options, 'max_in_g_bs', 'max_memory' if fixed_memory is None else None)
    per_cpu_min = limit(memory_options, 'min_per_ocpu_in_gbs', 'min_memory_per_ocpu')
    per_cpu_max = limit(memory_options, 'max_per_ocpu_in_gbs', 'max_memory_per_ocpu' if fixed_memory is None else None)

    # Null option objects represent fixed CPU or memory values in the SDK.
    if fixed_cpu is not None:
        cpu_min = cpu_max = fixed_cpu
    if fixed_memory is not None:
        memory_min = memory_max = fixed_memory

    cpu_max_known = cpu_max is not None

    cpu_min = max(1, cpu_min if cpu_min is not None else 1)
    memory_min = max(1, memory_min if memory_min is not None else 1)
    per_cpu_min = max(MIN_MEMORY_PER_OCPU.get(shape_name, 1), per_cpu_min if per_cpu_min is not None else 1)
    if per_cpu_max is not None and per_cpu_max < per_cpu_min:
        raise ValueError(f'Inconsistent minimum/maximum memory ratio for {shape_name}')

    # A total-memory maximum can also limit how many OCPUs can be provisioned.
    if memory_max is not None:
        memory_cpu_max = floor(memory_max / per_cpu_min)
        cpu_max = min(cpu_max, memory_cpu_max) if cpu_max is not None else memory_cpu_max
    if cpu_max is not None and cpu_max < cpu_min:
        raise ValueError(f'Inconsistent OCPU/memory limits for {shape_name}')

    ocpus = _number(ocpus, 'requested OCPUs')
    ocpus = max(cpu_min, ocpus if ocpus is not None else cpu_min)
    cpu_exceeds_maximum = cpu_max is not None and ocpus > cpu_max
    if cpu_max is not None:
        ocpus = min(ocpus, cpu_max)
    low = max(memory_min, ocpus * per_cpu_min)
    ceilings = [value for value in (memory_max, ocpus * per_cpu_max if per_cpu_max is not None else None)
                if value is not None]
    high = min(ceilings) if ceilings else None
    if high is not None and low > high:
        raise ValueError(f'Inconsistent memory limits for {shape_name} at {ocpus} OCPUs')

    memory = _number(memory, 'requested memory')
    memory = max(low, memory if memory is not None else low)
    if high is not None:
        # Preserve the CLI's saturation rule: exceeding the CPU maximum selects
        # both maximum resources, regardless of the requested memory. Otherwise
        # only cap memory at the ceiling for the requested number of OCPUs.
        memory = high if cpu_exceeds_maximum else min(memory, high)
    return ShapeConfiguration(
        ocpus, memory, True, used_fallback,
        upper_limits_known=cpu_max_known and memory_max is not None and (per_cpu_max is not None or fixed_memory is not None)
    )
