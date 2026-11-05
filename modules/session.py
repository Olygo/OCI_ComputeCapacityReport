# coding: utf-8

from dataclasses import dataclass, field
from collections import deque
from copy import copy
from datetime import datetime, timezone
from functools import wraps
from threading import Lock, RLock

import oci
from contextlib import closing

from modules.execution import build_retry_strategy, client_options, region_tasks, check_cancelled, cancellable_call
from modules.results import describe_error, diagnostic_lines, print_request_error


def region_locked(method):
    """Serialize cache/client operations in one region, not across regions."""
    @wraps(method)
    def locked(self, region, *args, **kwargs):
        with self._region_lock(region):
            check_cancelled()
            return method(self, region, *args, **kwargs)
    return locked


@dataclass
class ShapeCatalog:
    shape_names: set
    successful_regions: list
    requested_regions: tuple
    discovery_performed: bool = True
    regions_by_shape: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SearchRequest:
    shape: str
    ocpus: object
    memory: object
    regions: tuple
    compartment_id: str
    all_regions: bool
    drcc: bool


@dataclass
class HistoryEntry:
    request: SearchRequest
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    outcome: str = 'RUNNING'


class ReportSession:
    """State and metadata owned by one authenticated interactive session.

    Metadata is cached; the last report is retained only for export, never reuse.
    Compartment IDs in cache keys are the IDs actually used by the caller.
    """

    def __init__(self, config, signer, regions, home_region, compartment_id,
                 all_regions=False, skip_discovery=False, debug=False, subscribed_region_count=None,
                 reference_catalog=None):
        self.config = dict(config)
        self.signer = signer
        self.regions = list(regions)
        self.subscribed_region_count = (len(self.regions) if subscribed_region_count is None
                                        else subscribed_region_count)
        self.home_region = home_region
        self.compartment_id = compartment_id
        self.all_regions = all_regions
        self.debug = debug
        self.reference_catalog = reference_catalog
        self.discovery_deferred = all_regions and skip_discovery
        self.catalog_shown = False
        self.displayed_shapes = ()
        self.displayed_shapes_scope = None
        self.shape_prompt_shown = False
        self.capacity_notice_pending = True
        self.history = deque(maxlen=20)
        self.last_report = None
        self._identity_clients = {}
        self._compute_clients = {}
        self._shapes = {}
        self._availability_domains = {}
        self._fault_domains = {}
        self._catalogs = {}
        self._limit_name_catalogs = {}
        self._lock_guard = Lock()
        self._region_locks = {}

    def _region_lock(self, region):
        with self._lock_guard:
            name = region.region_name
            if name not in self._region_locks:
                self._region_locks[name] = RLock()
            return self._region_locks[name]

    def cached_limit_names(self, region, service_name):
        """Only complete, tenancy-scoped name catalogs are reusable."""
        with self._lock_guard:
            names = self._limit_name_catalogs.get((region.region_name, service_name))
            return None if names is None else list(names)

    def cache_limit_names(self, region, service_name, names):
        with self._lock_guard:
            self._limit_name_catalogs[(region.region_name, service_name)] = tuple(sorted(set(names)))

    @property
    def current_region(self):
        return self.regions[0]

    def select_region(self, region):
        self.select_regions([region])

    def select_regions(self, regions):
        if self.all_regions:
            raise ValueError("Region changes are unavailable in all_regions mode.")
        if not regions:
            raise ValueError('Select at least one region.')
        self.regions = list({region.region_name: region for region in regions}.values())
        self.capacity_notice_pending = True
        self.clear_shape_numbers()
        self.catalog_shown = False

    def clear_shape_numbers(self):
        self.displayed_shapes = ()
        self.displayed_shapes_scope = None

    def use_all_regions(self, regions, subscribed_region_count):
        """Commit a validated all-regions scope, retaining history and exports."""
        self.select_regions(regions)
        self.all_regions = True
        self.subscribed_region_count = subscribed_region_count
        self.discovery_deferred = False

    def use_single_region(self, region):
        """Leave all-regions mode after a single region has been validated."""
        self.regions = [region]
        self.capacity_notice_pending = True
        self.all_regions = False
        self.discovery_deferred = False
        self.clear_shape_numbers()
        self.catalog_shown = False

    def set_displayed_shapes(self, shapes):
        self.displayed_shapes = tuple(shapes)
        self.displayed_shapes_scope = (
            tuple(region.region_name for region in self.regions), self.compartment_id
        )

    def shape_for_number(self, number):
        scope = (tuple(region.region_name for region in self.regions), self.compartment_id)
        if self.displayed_shapes_scope != scope:
            self.clear_shape_numbers()
            return None
        if not 1 <= number <= len(self.displayed_shapes):
            return None
        return self.displayed_shapes[number - 1]

    def region_config(self, region):
        config = dict(self.config)
        config['region'] = region.region_name
        return config

    @region_locked
    def identity_client(self, region):
        name = region.region_name
        if name not in self._identity_clients:
            self._identity_clients[name] = oci.identity.IdentityClient(
                config=self.region_config(region), signer=self.signer, **client_options()
            )
        return self._identity_clients[name]

    @region_locked
    def compute_client(self, region):
        name = region.region_name
        if name not in self._compute_clients:
            self._compute_clients[name] = oci.core.ComputeClient(
                config=self.region_config(region), signer=self.signer, **client_options()
            )
        return self._compute_clients[name]

    @region_locked
    def shapes(self, region, compartment_id, availability_domain=None, shape_name=None):
        key = ((region.region_name, compartment_id, availability_domain, shape_name) if shape_name is not None else
               (region.region_name, compartment_id) if availability_domain is None else
               (region.region_name, compartment_id, availability_domain))
        if key not in self._shapes:
            options = {'retry_strategy': build_retry_strategy()}
            if availability_domain is not None:
                options['availability_domain'] = availability_domain
            if shape_name is not None:
                options['shape'] = shape_name
            self._shapes[key] = oci.pagination.list_call_get_all_results(
                lambda *args, **kwargs: cancellable_call(self.compute_client(region).list_shapes, *args, **kwargs),
                compartment_id, **options
            ).data
        return self._shapes[key]

    @region_locked
    def shape_metadata(self, region, compartment_id, shape_name, availability_domain=None):
        """Reuse full catalogs when possible, otherwise query only this shape."""
        full_key = ((region.region_name, compartment_id) if availability_domain is None else
                    (region.region_name, compartment_id, availability_domain))
        full_metadata = self._shapes.get(full_key, ())
        existing = next((shape for shape in full_metadata if shape.shape == shape_name), None)
        if existing is not None:
            return existing
        filtered = self.shapes(region, compartment_id, availability_domain, shape_name)
        return next((shape for shape in filtered if shape.shape == shape_name), None)

    def reference_shape(self, shape_name):
        return (self.reference_catalog or {}).get('shapes', {}).get(shape_name)

    def reference_source(self):
        version = (self.reference_catalog or {}).get('catalog_version', 'unknown')
        return f'Local OCI reference catalog ({version}); not regional availability'

    def property_metadata(self, region, compartment_id, shape_name):
        """Live OCI properties first; retain raw reference fields when unavailable."""
        try:
            shape = self.shape_metadata(region, compartment_id, shape_name)
        except Exception as error:
            if self.reference_shape(shape_name) is None:
                raise
            if self.debug:
                print_request_error(f'OCI properties unavailable; using local reference for {shape_name}',
                                    error, debug=True)
            shape = None
        if shape is not None:
            return shape, f'OCI ListShapes | Region: {region.region_name}'
        return self.reference_shape(shape_name), self.reference_source()

    @region_locked
    def availability_domains(self, region, compartment_id):
        key = (region.region_name, compartment_id)
        if key not in self._availability_domains:
            response = oci.pagination.list_call_get_all_results(
                lambda *args, **kwargs: cancellable_call(self.identity_client(region).list_availability_domains, *args, **kwargs), compartment_id,
                retry_strategy=build_retry_strategy()
            )
            self._availability_domains[key] = [ad.name for ad in response.data]
        return self._availability_domains[key]

    @region_locked
    def probe_shape(self, region, shape_name, compartment_id=None):
        """Silently check an unfamiliar name using OCI defaults, before input.

        Omit custom shape resources and the FD filter. A successful capacity
        status establishes that the probe works; it is never reused as a report.
        A generic configuration/access error cannot establish shape absence.
        """
        availability_domains = self.availability_domains(region, self.config['tenancy'])
        outcomes = []
        for availability_domain in availability_domains:
            details = oci.core.models.CreateComputeCapacityReportDetails(
                compartment_id=self.compartment_id if compartment_id is None else compartment_id,
                availability_domain=availability_domain,
                shape_availabilities=[oci.core.models.CreateCapacityReportShapeAvailabilityDetails(
                    instance_shape=shape_name,
                    fault_domain=None,
                    instance_shape_config=None
                )]
            )
            try:
                report = cancellable_call(self.compute_client(region).create_compute_capacity_report,
                    create_compute_capacity_report_details=details,
                    retry_strategy=build_retry_strategy()
                )
            except oci.exceptions.ServiceError as e:
                detail = describe_error(e)
                message = f"{detail.summary} (HTTP {e.status} | {e.code})"
                if self.debug:
                    message += '\n' + '\n'.join(diagnostic_lines(detail))
                if e.status == 404 and e.code == 'NotAuthorizedOrNotFound':
                    outcomes.append(('NOT_FOUND', message))
                else:
                    outcomes.append(('ERROR', message))
                continue

            statuses = [result.availability_status for result in report.data.shape_availabilities]
            if any(status in {'AVAILABLE', 'OUT_OF_HOST_CAPACITY'} for status in statuses):
                return 'FOUND', ''
            if statuses and all(status == 'HARDWARE_NOT_SUPPORTED' for status in statuses):
                outcomes.append(('NOT_FOUND', 'HARDWARE_NOT_SUPPORTED'))
            else:
                outcomes.append(('ERROR', f"Unexpected capacity statuses: {statuses}"))

        for status, message in outcomes:
            if status == 'ERROR':
                return status, message
        if outcomes:
            return 'NOT_FOUND', outcomes[-1][1]
        return 'ERROR', 'No availability domain was returned by OCI.'

    @region_locked
    def fault_domains(self, region, compartment_id, availability_domain):
        key = (region.region_name, compartment_id, availability_domain)
        if key not in self._fault_domains:
            response = oci.pagination.list_call_get_all_results(
                lambda *args, **kwargs: cancellable_call(self.identity_client(region).list_fault_domains, *args, **kwargs), compartment_id,
                availability_domain, retry_strategy=build_retry_strategy()
            )
            self._fault_domains[key] = [fd.name for fd in response.data]
        return self._fault_domains[key]

    def refresh_metadata(self):
        """Invalidate metadata in the current scope, retaining reusable clients."""
        self.clear_shape_numbers()
        names = {region.region_name for region in self.regions}
        for cache in (self._shapes, self._availability_domains, self._fault_domains):
            for key in list(cache):
                if key[0] in names:
                    del cache[key]
        # Catalog snapshots depend on region metadata, even when their scopes differ.
        for key in list(self._catalogs):
            if names.intersection(key[0]):
                del self._catalogs[key]

    def is_discovered_shape(self, shape_name):
        """Check the displayed scope's snapshot without triggering discovery."""
        names = tuple(region.region_name for region in self.regions)
        catalog = self._catalogs.get((names, self.compartment_id))
        return catalog is not None and shape_name in catalog.shape_names

    def discovery_regions_for_shape(self, shape_name):
        """Retain actual origins across cached scopes, including region changes."""
        origins = set()
        for (_, compartment_id), catalog in self._catalogs.items():
            if compartment_id == self.compartment_id:
                origins.update(catalog.regions_by_shape.get(shape_name, ()))
        return origins

    def catalog(self, refresh=False):
        """Discover once per scope; subsequent displays perform no network calls."""
        if refresh:
            # Stage new cache dictionaries while reusing clients and locks.
            # Workers drain before a failure/cancellation can discard this stage.
            staged = copy(self)
            for attribute in ('_shapes', '_availability_domains', '_fault_domains', '_catalogs'):
                setattr(staged, attribute, dict(getattr(self, attribute)))
            staged.refresh_metadata()
            staged.discovery_deferred = False
            catalog = staged.catalog()
            if set(catalog.successful_regions) != set(catalog.requested_regions):
                raise RuntimeError('Shape discovery is incomplete; the previous catalog was not replaced.')
            for attribute in ('_shapes', '_availability_domains', '_fault_domains', '_catalogs'):
                setattr(self, attribute, getattr(staged, attribute))
            self.discovery_deferred = False
            return catalog
        names = tuple(region.region_name for region in self.regions)
        key = (names, self.compartment_id)
        if key in self._catalogs and (self._catalogs[key].discovery_performed or self.discovery_deferred):
            return self._catalogs[key]

        if self.discovery_deferred:
            catalog = ShapeCatalog(set(), [], names, discovery_performed=False)
            self._catalogs[key] = catalog
            return catalog

        shape_names = set()
        regions_by_shape = {}
        successful_regions = []
        def discover(region):
            return self.shapes(region, self.compartment_id)

        with closing(region_tasks(
                self.regions, discover, progress='Discovering new shapes in region')) as tasks:
            for region, shapes, error in tasks:
                if error is not None:
                    if len(self.regions) == 1:
                        raise error
                    print_request_error(f"Shape discovery failed in {region.region_name}", error, debug=self.debug)
                    continue
                for shape in shapes:
                    shape_names.add(shape.shape)
                    regions_by_shape.setdefault(shape.shape, set()).add(region.region_name)
                successful_regions.append(region.region_name)

        if not successful_regions:
            raise RuntimeError("Unable to discover shapes in any requested region.")

        successful_regions = [name for name in names if name in successful_regions]
        catalog = ShapeCatalog(shape_names, successful_regions, names, regions_by_shape=regions_by_shape)
        self._catalogs[key] = catalog
        return catalog
