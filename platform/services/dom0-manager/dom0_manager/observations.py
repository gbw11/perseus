"""Share bounded, read-only VM observations within dom0; never cache control checks.

Opt-in: XENOVA_OBSERVATIONS_PATH. One producer lives in the existing API process.
The display consumer reads this private tmpfs file without invoking Xen/Manager.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict, replace
import json
import logging
import math
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
from uuid import UUID, uuid4

from .drivers.base import Domain
from .errors import PlatformUnavailable
from .models import VmState
from .drivers.platform import view_to_domain

MAX_AGE = 3.0
MAX_BYTES = 262144
log = logging.getLogger(__name__)


def host_boot_id():
    return str(UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()))


def verified_platform_batch(driver, boot_id, started, clock=time.monotonic):
    """Reuse metadata from that exact verified list; do not requery each VM.

    The platform forwards its already-verified observation clock/incarnation.
    Separate snapshot calls can race subsequent observations and add contention.
    """
    views = driver._views()
    rows, times = [], []
    for view in views:
        identity = UUID(view['vm_id'])
        observed = view.get('observed_monotonic_ms')
        if (not identity.int or view.get('host_boot_id') != boot_id or type(observed) is not int
                or not 0 <= clock() - observed / 1000 < MAX_AGE):
            raise PlatformUnavailable('verified list observation is missing, expired or foreign')
        times.append(observed / 1000)
        domain = view_to_domain(view)
        if domain is not None:
            # Include the actual Xen incarnation even for an unmanaged execution.
            execution = view.get('execution_id') or view.get('xen_unique_id')
            if not isinstance(execution, str) or not 1 <= len(execution) <= 512:
                raise PlatformUnavailable('verified list lacks an execution identity')
            rows.append(replace(domain, observation_id=execution))
    return rows, min(times) if times else started


class ObservationFile:
    """No fallback: absent, failed, old, foreign-boot or unsafe data is unavailable."""
    def __init__(self, path, *, clock=time.monotonic, boot_id=None, owner_uid=0):
        self.path = Path(path)
        self.clock = clock
        self.boot_id = boot_id or host_boot_id()
        self.owner_uid = owner_uid

    def read(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.owner_uid
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_BYTES):
                    raise ValueError('unsafe observation file')
                raw = stream.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError('oversized observation')
                value = json.loads(raw)
            if value['schema_version'] != 1 or value['host_boot_id'] != self.boot_id or value['status'] != 'ok':
                raise ValueError('unavailable observation')
            observed = value['observed_mono']
            if type(observed) not in (int, float) or not math.isfinite(observed):
                raise ValueError('invalid observation clock')
            if not 0 <= self.clock() - observed < MAX_AGE:
                raise ValueError('expired observation')
            epoch = str(UUID(value['producer_epoch']))
            rows = value['domains']
            if not isinstance(rows, list) or len(rows) > 1024:
                raise ValueError('invalid domain list')
            domains = []
            seen = set()
            for row in rows:
                vm_id = UUID(row['vm_id'])
                if vm_id.int == 0 or vm_id in seen:
                    raise ValueError('invalid or duplicate guest UUID')
                seen.add(vm_id)
                for field in ('vcpus', 'memory_mib', 'domid'):
                    if type(row[field]) is not int or row[field] < 1:
                        raise ValueError('invalid domain resource/identity')
                name = row['name']
                execution = row.get('observation_id')
                if name is not None and (not isinstance(name, str) or len(name) > 256):
                    raise ValueError('invalid domain name')
                if execution is not None and (not isinstance(execution, str) or len(execution) > 512):
                    raise ValueError('invalid execution identity')
                domains.append(Domain(vm_id, VmState(row['state']), row['vcpus'], row['memory_mib'],
                                      row['domid'], name, f'{epoch}:{execution or row["domid"]}'))
            return domains, observed
        except (OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError) as error:
            raise PlatformUnavailable('shared VM observation unavailable') from error

    def list_domains(self):
        return self.read()[0]


class ObservationHub:
    def __init__(self, driver, path, *, clock=time.monotonic, boot_id=None):
        self.driver = driver
        self.path = Path(path)
        self.clock = clock
        self.boot_id = boot_id or host_boot_id()
        self.epoch = str(uuid4())
        self.stop_event = threading.Event()
        self.publish_lock = threading.Lock()
        self.thread = None
        self.query_unavailable = False
        self.reader = ObservationFile(path, clock=clock, boot_id=self.boot_id, owner_uid=os.geteuid())

    def _publish(self, rows, observed):
        value = {'schema_version': 1, 'host_boot_id': self.boot_id, 'producer_epoch': self.epoch,
                 'status': 'ok' if rows is not None else 'unavailable', 'observed_mono': observed,
                 'domains': [asdict(row) for row in rows] if rows is not None else []}
        data = json.dumps(value, default=str).encode()
        if len(data) > MAX_BYTES:
            raise ValueError('observation exceeds size limit')
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o022 or parent.resolve() != parent):
            raise ValueError('unsafe observation directory')
        fd, temporary = tempfile.mkstemp(prefix='.vm-observation-', dir=parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
            # Runtime tmpfs observation, not durable configuration: no fsync per tick.
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def refresh(self):
        started = self.clock()
        try:
            observed = started
            if self.driver.name == 'platform':
                rows, observed = verified_platform_batch(self.driver, self.boot_id, started, self.clock)
            else:
                rows = self.driver.list_domains()
            if not 0 <= self.clock() - observed < MAX_AGE:
                rows = None
            self.query_unavailable = rows is None
        except Exception as error:
            rows = None
            observed = started
            if not self.query_unavailable:
                log.warning('shared VM query unavailable (%s)', type(error).__name__)
            self.query_unavailable = True
        with self.publish_lock:
            if not self.stop_event.is_set():
                self._publish(rows, observed)

    def _run(self):
        while not self.stop_event.is_set():
            started = self.clock()
            try:
                self.refresh()
            except Exception as error:
                log.warning('shared VM publication unavailable (%s)', type(error).__name__)
            # A slow query already consumed this period. Adding a full extra
            # second after it can expire an otherwise valid platform snapshot.
            self.stop_event.wait(max(0.05, 1.0 - (self.clock() - started)))

    def start(self):
        self._publish(None, self.clock())
        self.thread = threading.Thread(target=self._run, name='vm-observations', daemon=True)
        self.thread.start()

    def stop(self):
        with self.publish_lock:
            self.stop_event.set()
            self._publish(None, self.clock())
        # A blocked platform operation cannot publish after shutdown.
        if self.thread:
            self.thread.join(timeout=0.1)

    def list_domains(self):
        return self.reader.list_domains()


def install_observations(app):
    path = os.environ.get('XENOVA_OBSERVATIONS_PATH')
    if not path:
        return
    if not Path(path).is_absolute():
        raise ValueError('XENOVA_OBSERVATIONS_PATH must be absolute')
    hub = ObservationHub(app.state.driver, path)
    app.state.service.observations = hub
    app.state.observations = hub
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous(application):
            hub.start()
            try:
                yield
            finally:
                hub.stop()

    app.router.lifespan_context = lifespan
