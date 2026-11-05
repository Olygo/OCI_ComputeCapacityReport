# coding: utf-8

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from threading import Event, Lock
from time import monotonic
from queue import Queue, Empty
import shutil

import oci

from modules.utils import print_error
from modules.terminal import terminal_output, clear_status, status_line, plain_text


MAX_REGION_WORKERS = 4
REQUEST_TIMEOUT = (3, 10)
MAX_REQUEST_ATTEMPTS = 3
RETRY_BUDGET_SECONDS = 30
_cancellation = ContextVar('capacity_cancellation', default=None)
_query_progress = ContextVar('capacity_query_progress', default=None)


class QueryProgress:
    def __init__(self, total):
        self.total = total
        self.completed = 0
        self.requests = 0
        self.started = monotonic()
        self.visible = False
        self.lock = Lock()

    @property
    def elapsed(self):
        return monotonic() - self.started

    def request_started(self):
        with self.lock:
            self.requests += 1

    def region_finished(self):
        with self.lock:
            self.completed += 1

    def show(self):
        if not terminal_output():
            return
        with self.lock:
            completed, requests = self.completed, self.requests
        width = max(1, shutil.get_terminal_size((120, 24)).columns)
        line = f'Checking capacity: {completed}/{self.total} regions | {requests} requests | {self.elapsed:.1f}s'
        if len(line) > width:
            line = f'Regions {completed}/{self.total} | Req {requests} | {self.elapsed:.1f}s'
        if len(line) > width:
            line = f'{completed}/{self.total} R {requests} Q {self.elapsed:.1f}s'
        if len(line) > width:
            self.clear()
            return
        status_line(line)
        self.visible = True

    def clear(self):
        if self.visible:
            clear_status()
            self.visible = False


@contextmanager
def track_query(total):
    progress = QueryProgress(total)
    token = _query_progress.set(progress)
    try:
        yield progress
    finally:
        progress.clear()
        _query_progress.reset(token)


class SearchCancelled(KeyboardInterrupt):
    """Cooperative cancellation bypasses ordinary request-error handling."""


@contextmanager
def cancellation_scope():
    existing = _cancellation.get()
    token = None
    event = existing
    if event is None:
        event = Event()
        token = _cancellation.set(event)
    try:
        yield event
    except KeyboardInterrupt:
        event.set()
        raise
    finally:
        if token is not None:
            _cancellation.reset(token)


def check_cancelled():
    event = _cancellation.get()
    if event is not None and event.is_set():
        raise SearchCancelled


def cancellable_call(function, *args, **kwargs):
    check_cancelled()
    progress = _query_progress.get()
    if progress is not None:
        progress.request_started()
    result = function(*args, **kwargs)
    check_cancelled()
    return result


def build_retry_strategy():
    """Retry only transient failures, never invalid configuration or access."""
    return oci.retry.RetryStrategyBuilder(
        max_attempts_check=True,
        max_attempts=MAX_REQUEST_ATTEMPTS,
        total_elapsed_time_check=True,
        total_elapsed_time_seconds=RETRY_BUDGET_SECONDS,
        service_error_check=True,
        service_error_retry_on_any_5xx=False,
        service_error_retry_config={429: [], 500: [], 502: [], 503: [], 504: []},
        retry_base_sleep_time_seconds=1,
        retry_max_wait_between_calls_seconds=4,
        backoff_type='full_jitter_with_equal_on_throttle',
    ).get_retry_strategy()


def client_options():
    # Separate strategies avoid sharing mutable SDK retry callbacks across clients.
    return {'timeout': REQUEST_TIMEOUT, 'retry_strategy': build_retry_strategy()}


class ConsoleOutput:
    def __init__(self, renderer=None, progress=None, results=None):
        self.renderer = renderer
        self.progress = progress
        self.results = results

    def write(self, *args, **kwargs):
        if self.progress is not None:
            self.progress.clear()
        print(*args, **kwargs)

    def error(self, *args, **kwargs):
        if self.progress is not None:
            self.progress.clear()
        print_error(*args, **kwargs)

    def result(self, result, drcc=False, debug=False):
        from modules.results import render_result
        if self.results is not None:
            self.results.append(result)
        render_result(result, self, drcc, debug, renderer=self.renderer)


class BufferedOutput:
    """A worker's output, replayed only by the coordinating thread."""
    def __init__(self):
        self.events = []
        self.position = 0

    def write(self, *args, **kwargs):
        self.events.append(('write', args, kwargs))

    def error(self, *args, **kwargs):
        self.events.append(('error', args, kwargs))

    def result(self, *args, **kwargs):
        self.events.append(('result', args, kwargs))

    def render(self, console=None):
        console = console if console is not None else ConsoleOutput()
        while self.position < len(self.events):
            kind, args, kwargs = self.events[self.position]
            self.position += 1
            getattr(console, kind)(*args, **kwargs)


class QueuedOutput:
    """Stream a single region's rows while only the main thread writes."""
    def __init__(self):
        self.events = Queue()

    def write(self, *args, **kwargs):
        self.events.put(('write', args, kwargs))

    def error(self, *args, **kwargs):
        self.events.put(('error', args, kwargs))

    def result(self, *args, **kwargs):
        self.events.put(('result', args, kwargs))

    def render(self, console):
        while True:
            try:
                kind, args, kwargs = self.events.get_nowait()
            except Empty:
                return
            getattr(console, kind)(*args, **kwargs)


def region_tasks(regions, worker, progress=None, max_workers=MAX_REGION_WORKERS, on_tick=None):
    """Share cancellation with workers and drain active calls before returning."""
    with cancellation_scope() as event:
        yield from _region_tasks(regions, worker, progress, max_workers, event, on_tick)


def _region_tasks(regions, worker, progress, max_workers, cancellation, on_tick):
    """Yield completed regions with results/errors; all terminal writes stay here.

    One job per region and one sequential execution path for a single region.
    Completed output can be rendered by the caller between progress updates.
    """
    regions = list(regions)
    count = len(regions)
    workers = max(1, min(max_workers, count))

    def show_progress(completed, name):
        if not terminal_output():
            return
        if isinstance(progress, QueryProgress):
            progress.show()
        elif progress:
            width = max(1, shutil.get_terminal_size((120, 24)).columns)
            line = f'{progress} {name} ({completed}/{count} completed; up to {workers} concurrent)'
            if len(line) > width:
                line = f'{progress} {name} ({completed}/{count})'
            if len(line) > width:
                line = f'{name} ({completed}/{count})'
            if len(line) > width:
                line = f'{completed}/{count}'
            status_line(line)

    def clear_progress():
        if isinstance(progress, QueryProgress):
            progress.clear()
        elif progress:
            clear_status()

    if not regions:
        return
    if progress and not terminal_output():
        label = getattr(progress, 'label', 'Checking capacity') if isinstance(progress, QueryProgress) else progress
        unit = getattr(progress, 'unit', 'regions') if isinstance(progress, QueryProgress) else 'regions'
        print(plain_text(f'{label}: {count} {unit}'))
    try:
        show_progress(0, regions[0].region_name)
        if workers == 1 and not isinstance(progress, QueryProgress):
            for index, region in enumerate(regions, start=1):
                check_cancelled()
                try:
                    result, error = worker(region), None
                except Exception as e:
                    result, error = None, e
                clear_progress()
                yield region, result, error
                if index < count:
                    show_progress(index, regions[index].region_name)
            return

        executor = ThreadPoolExecutor(max_workers=workers)
        futures = {}
        try:
            def invoke(region):
                check_cancelled()
                result = worker(region)
                check_cancelled()
                return result

            for region in regions:
                check_cancelled()
                futures[executor.submit(copy_context().run, invoke, region)] = region
            pending = set(futures)
            completed = 0
            while pending:
                done, pending = wait(pending, timeout=0.15, return_when=FIRST_COMPLETED)
                if on_tick is not None:
                    on_tick()
                for future in done:
                    region = futures[future]
                    try:
                        result, error = future.result(), None
                    except Exception as e:
                        result, error = None, e
                    completed += 1
                    if isinstance(progress, QueryProgress):
                        progress.region_finished()
                    clear_progress()
                    yield region, result, error
                if pending and (done or isinstance(progress, QueryProgress)):
                    active = next((item.region_name for task, item in futures.items()
                                   if not task.done()), region.region_name)
                    show_progress(completed, active)
        except BaseException:
            cancellation.set()
            clear_progress()
            for future in futures:
                future.cancel()
            if any(not future.done() for future in futures):
                print('Cancelling search; waiting for in-flight OCI requests to finish...')
            raise
        finally:
            # A running HTTP call cannot be killed safely. Join it under the
            # configured SDK timeouts before allowing another search/cache use.
            while True:
                try:
                    executor.shutdown(wait=True, cancel_futures=True)
                    break
                except KeyboardInterrupt:
                    cancellation.set()
                    for future in futures:
                        future.cancel()
    finally:
        clear_progress()
