"""실제 Linux backing의 정확 leaf와 원 관측 수명 경계를 검사한다.

fixture만 fresh UUID 이름으로 exclusive 생성하며 원 identity가 같은 자기 항목만
정리한다. regular fixture는 public SHM/SemLock creator나 전체 cleanup 수락이 아니다.
"""

from contextlib import contextmanager
import copy
from dataclasses import asdict, replace
import errno
import hashlib
import io
import json
import mmap
import os
from pathlib import Path
import pickle
import select
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from xenova_vehicle.runtime import resource_name_observation as names_module
from xenova_vehicle.runtime.process_observation import ProcessObserver
from xenova_vehicle.runtime.resource_manifest import ManifestError, ResourceManifest, RuntimeScope, SLOTS
from xenova_vehicle.runtime.resource_store import ResourceStore, StoreUsageError
from resource_guard_fixtures import encode, identifier, minimal_fields


def birth(pid):
    raw = Path('/proc/' + str(pid) + '/stat').read_bytes()
    return int(raw[:raw.index(b' (')]), int(raw[raw.rfind(b') ') + 2:].split()[19]), raw


def batch_summary(batch):
    def receipt(value):
        return dict(load_id=value.outcome.load_id, nonce=value.lock_nonce, kind=value.outcome.kind,
                    ref=None if value.outcome.manifest is None else asdict(value.outcome.manifest.ref))
    return dict(original_load=receipt(batch.original_load), latest_load=receipt(batch.latest_load),
                guard=asdict(batch.guard), owners=[asdict(item) for item in batch.owners],
                started_ns=batch.started_ns, completed_ns=batch.completed_ns)


@unittest.skipUnless(sys.platform == 'linux', '실제 Linux tmpfs/procfs 시험이며 다른 OS에서 대체하지 않는다')
class ResourceNameObservationTests(unittest.TestCase):
    evidence_directory = None

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='xenova-name-observer-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.directory.chmod(0o700)
        self.data = self.directory / 'resource-set.json'
        self.store = ResourceStore(str(self.directory))
        self.addCleanup(self.store.close)
        self.root_fd = os.open('/dev/shm', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        root = os.fstat(self.root_fd)
        self.root_identity = root.st_dev, root.st_ino
        self.created = {}
        self.leaves = {}
        self.addCleanup(self.cleanup_entries)
        self.service = identifier(800)
        self.observer = names_module.ResourceNameObserver(self.store, self.service)

    def cleanup_entries(self):
        error = None
        try:
            for leaf, identity in reversed(tuple(self.created.items())):
                try:
                    current = os.stat(leaf, dir_fd=self.root_fd, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) != identity:
                        raise RuntimeError('fixture identity changed; replacement is preserved')
                    if stat.S_ISDIR(current.st_mode):
                        os.rmdir(leaf, dir_fd=self.root_fd)
                    else:
                        os.unlink(leaf, dir_fd=self.root_fd)
                except FileNotFoundError as caught:
                    if caught.errno != errno.ENOENT:
                        error = caught
                except BaseException as caught:
                    error = caught
        finally:
            os.close(self.root_fd)
        if error is not None:
            raise error

    def record(self, name, details, payloads=None):
        if self.evidence_directory is None:
            return
        directory = Path(self.evidence_directory) / name
        directory.mkdir(parents=True, exist_ok=False)
        files = {}
        for filename, raw in ({} if payloads is None else payloads).items():
            with (directory / filename).open('xb') as output:
                output.write(raw)
            files[filename] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        report = dict(qualification='TEST_FIXTURES_AND_INDEPENDENT_READS_NOT_CREATOR_OR_ATOMIC_SNAPSHOT',
                      event_scope='FORWARDED_PYTHON_OS_CALLS_NOT_COMPLETE_KERNEL_SYSCALL_AUDIT',
                      details=details, files=files, fixture_root=dict(device=self.root_identity[0],
                                                                    inode=self.root_identity[1]),
                      fixture_entries=[dict(leaf=leaf, device=identity[0], inode=identity[1])
                                       for leaf, identity in self.created.items()])
        with (directory / 'summary.json').open('x', encoding='utf-8', newline='\n') as output:
            output.write(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(',', ':')) + '\n')

    def runtime(self):
        root = os.fstat(self.root_fd)
        raw_info = Path('/proc/self/fdinfo/' + str(self.root_fd)).read_bytes()
        mount_id = int(next(line.split(b':', 1)[1] for line in raw_info.splitlines() if line.startswith(b'mnt_id:')))
        mounts = Path('/proc/self/mountinfo').read_bytes()
        row = next(line for line in mounts.splitlines() if int(line.split(b' ', 1)[0]) == mount_id)
        filesystem = row.split(b' - ', 1)[1].split(b' ', 1)[0].decode('ascii')
        return dict(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text('ascii').strip(),
                    pid_ns_inode=str(os.stat('/proc/self/ns/pid').st_ino),
                    mnt_ns_inode=str(os.stat('/proc/self/ns/mnt').st_ino), shm_mount_id=str(mount_id),
                    shm_dev=str(root.st_dev), shm_root_inode=str(root.st_ino), shm_fs=filesystem)

    def fields(self):
        fields = minimal_fields()
        fields['runtime'] = self.runtime()
        fields['service_instance'] = self.service
        pid, ticks, _ = birth(os.getpid())
        fields['owners'] = [dict(role='S', phase='CURRENT', pid=pid, start_ticks=str(ticks))]
        return fields

    def resource(self, slot='RAW_CURRENT', *, sem_name=None):
        object_id = str(uuid.uuid4())
        shm = slot in SLOTS[:6]
        name = (('/xnv_raw_' if slot.startswith('RAW') else '/xnv_db_') + object_id
                if shm else ('/xnv_test_' + object_id if sem_name is None else sem_name))
        leaf = name[1:] if shm else 'sem.' + name[1:]
        try:
            os.stat(leaf, dir_fd=self.root_fd, follow_symlinks=False)
        except FileNotFoundError as error:
            self.assertEqual(error.errno, errno.ENOENT)
        else:
            self.fail('fresh fixture collided; existing entry is preserved')
        self.leaves[leaf] = slot
        return dict(state='KNOWN', name=name, object_id=object_id if shm else None,
                    size_bytes='32' if shm else None), leaf

    def create(self, leaf, *, kind='regular', payload=b'fixture', target=None, mode=0o600):
        if kind == 'regular':
            descriptor = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 mode, dir_fd=self.root_fd)
            try:
                value = os.fstat(descriptor)
                self.created[leaf] = value.st_dev, value.st_ino
                position = 0
                while position < len(payload):
                    count = os.write(descriptor, payload[position:])
                    if count <= 0:
                        raise RuntimeError('fixture write made no progress')
                    position += count
                os.fchmod(descriptor, mode)
            finally:
                os.close(descriptor)
        else:
            if kind == 'directory':
                os.mkdir(leaf, mode=0o700, dir_fd=self.root_fd)
            elif kind == 'fifo':
                os.mkfifo(leaf, mode=0o600, dir_fd=self.root_fd)
            elif kind == 'symlink':
                os.symlink(target, leaf, dir_fd=self.root_fd)
            else:
                raise ValueError('unsupported fixture kind')
            value = os.stat(leaf, dir_fd=self.root_fd, follow_symlinks=False)
            self.created[leaf] = value.st_dev, value.st_ino

    def seed(self, fields):
        raw = encode(fields)
        self.data.write_bytes(raw)
        self.data.chmod(0o600)
        return raw

    @contextmanager
    def traced(self, *, leaf_error=None, leaf_callback=None, before_endpoint=None):
        actual_stat, actual_open, actual_close = os.stat, os.open, os.close
        actual_read, actual_fstat, actual_io_open = os.read, os.fstat, io.open
        actual_observe = ProcessObserver.observe
        trace = dict(events=[], leaves=[], endpoints=[], observer_instances=[], middle_fds=set(),
                     middle_read_keys={}, raw={}, middle_open_serial=0,
                     endpoint_active=False, harness_active=False)

        def endpoint(observer):
            number = len(trace['endpoints']) + 1
            trace['endpoint_active'] = True
            try:
                if before_endpoint is not None:
                    trace['harness_active'] = True
                    try:
                        before_endpoint(number, trace)
                    finally:
                        trace['harness_active'] = False
                result = actual_observe(observer)
                trace['endpoints'].append(result)
                trace['observer_instances'].append(observer)
                return result
            finally:
                trace['endpoint_active'] = False

        def lookup(path, *args, **kwargs):
            if path in self.leaves:
                self.assertFalse(trace['endpoint_active'])
                self.assertIs(kwargs.get('follow_symlinks'), False)
                descriptor = kwargs.get('dir_fd')
                self.assertIs(type(descriptor), int)
                root = actual_fstat(descriptor)
                self.assertEqual((root.st_dev, root.st_ino), self.root_identity)
                trace['leaves'].append(path)
                event = dict(op='leaf-stat', name=path, dir_fd=descriptor, device=root.st_dev,
                             inode=root.st_ino, controlled=leaf_error is not None)
                trace['events'].append(event)
                try:
                    if leaf_error is not None:
                        raise leaf_error
                    result = actual_stat(path, *args, **kwargs)
                except OSError as error:
                    event.update(result='ERROR', errno=error.errno)
                    raise
                event.update(result='STAT', mode=result.st_mode, size=result.st_size, nlink=result.st_nlink)
                if leaf_callback is not None:
                    trace['harness_active'] = True
                    try:
                        leaf_callback(path)
                    finally:
                        trace['harness_active'] = False
                return result
            return actual_stat(path, *args, **kwargs)

        def opening(path, flags, *args, **kwargs):
            if path in self.leaves or (type(path) is str and path.startswith('/dev/shm/')
                                       and path[len('/dev/shm/'):] in self.leaves):
                raise AssertionError('production opened resource leaf')
            if flags & os.O_CREAT or flags & os.O_ACCMODE != os.O_RDONLY:
                raise AssertionError('production requested writable/create open')
            descriptor = actual_open(path, flags, *args, **kwargs)
            trace['events'].append(dict(op='open', path=os.fsdecode(path), fd=descriptor, flags=flags,
                                        endpoint=trace['endpoint_active']))
            if not trace['endpoint_active']:
                trace['middle_fds'].add(descriptor)
                trace['middle_open_serial'] += 1
                trace['middle_read_keys'][descriptor] = 'middle-read-{:04d}.raw'.format(trace['middle_open_serial'])
            return descriptor

        def closing(descriptor):
            trace['events'].append(dict(op='close', fd=descriptor, endpoint=trace['endpoint_active']))
            trace['middle_fds'].discard(descriptor)
            trace['middle_read_keys'].pop(descriptor, None)
            return actual_close(descriptor)

        def forbidden(*args, **kwargs):
            raise AssertionError('production attempted fixture or IPC mutation')

        def io_opening(*args, **kwargs):
            if not trace['harness_active']:
                raise AssertionError('production bypassed descriptor-only metadata reads')
            return actual_io_open(*args, **kwargs)

        def reading(descriptor, maximum):
            raw = actual_read(descriptor, maximum)
            if trace['harness_active']:
                trace['events'].append(dict(op='read', fd=descriptor, requested=maximum,
                                            returned=len(raw), harness=True))
                return raw
            event = dict(op='read', fd=descriptor, requested=maximum, returned=len(raw),
                         endpoint=trace['endpoint_active'])
            if not trace['endpoint_active']:
                key = trace['middle_read_keys'][descriptor]
                trace['raw'][key] = trace['raw'].get(key, b'') + raw
                event['raw_file'] = key
            trace['events'].append(event)
            return raw

        def fstatting(descriptor):
            value = actual_fstat(descriptor)
            trace['events'].append(dict(op='fstat', fd=descriptor, device=value.st_dev, inode=value.st_ino,
                                        mode=value.st_mode, endpoint=trace['endpoint_active']))
            return value

        with patch.object(ProcessObserver, 'observe', endpoint), patch('os.stat', side_effect=lookup), \
                patch('os.open', side_effect=opening), patch('os.close', side_effect=closing), \
                patch('os.read', side_effect=reading), patch('os.fstat', side_effect=fstatting), \
                patch('builtins.open', side_effect=io_opening), patch('io.open', side_effect=io_opening), \
                patch('mmap.mmap', side_effect=forbidden), \
                patch('os.mkdir', side_effect=forbidden), patch('os.unlink', side_effect=forbidden), \
                patch('os.write', side_effect=forbidden), patch('os.ftruncate', side_effect=forbidden):
            yield trace

    def test_constructor_no_io_wrong_inputs_and_no_caller_root_or_names_argument(self):
        with patch('os.open', side_effect=AssertionError('constructor I/O')), \
                patch.object(ProcessObserver, 'observe', side_effect=AssertionError('constructor observe')):
            observer = names_module.ResourceNameObserver(self.store, self.service)
        self.assertIsNotNone(observer)
        for store, service in ((None, self.service), (object(), self.service),
                               (self.store, identifier(0xabc).upper()), (self.store, True)):
            with self.assertRaises(names_module.NameObservationUsageError):
                names_module.ResourceNameObserver(store, service)
        with self.assertRaises(TypeError):
            names_module.ResourceNameObserver(self.store, self.service, root='/dev/shm')

    def test_valid_zero_known_retains_four_actual_loads_and_final_receipt_can_propose(self):
        raw = self.seed(self.fields())
        loads = []
        actual_load = ResourceStore.load

        def loading(store):
            receipt = actual_load(store)
            loads.append(receipt)
            return receipt

        with patch.object(ResourceStore, 'load', loading), self.traced() as trace:
            result = self.observer.observe()
        self.assertEqual(result.names, ())
        self.assertEqual(trace['leaves'], [])
        self.assertEqual(len(loads), 4)
        self.assertEqual(len({load.outcome.load_id for load in loads}), 4)
        self.assertIs(result.before, trace['endpoints'][0])
        self.assertIs(result.after, trace['endpoints'][1])
        self.assertIs(trace['observer_instances'][0], trace['observer_instances'][1])
        self.assertIs(result.original_load, loads[0])
        self.assertIs(result.latest_load, loads[-1])
        self.assertIs(result.guard, result.before.guard)
        self.assertEqual(result.guard, result.after.guard)
        self.assertEqual([load.outcome.manifest.raw_bytes for load in loads], [raw] * 4)
        self.assertLessEqual(result.before.completed_ns, result.after.started_ns)
        self.assertEqual((result.started_ns, result.completed_ns),
                         (result.before.started_ns, result.after.completed_ns))
        fields = result.original_load.outcome.manifest.fields
        fields['revision'] = '2'
        target = ResourceManifest(encode(fields))
        with self.assertRaises(StoreUsageError):
            self.store.propose(result.before.latest_load, target, identifier(801))
        proposal = self.store.propose(result.latest_load, target, identifier(801))
        self.assertIs(proposal.expected, result.latest_load)
        self.record('zero-known-four-loads', dict(before=batch_summary(result.before),
                                                  after=batch_summary(result.after), events=trace['events']),
                    dict(trace['raw'], **{'B0-L1-manifest.raw': result.before.original_load.outcome.manifest.raw_bytes,
                                         'B0-L2-manifest.raw': result.before.latest_load.outcome.manifest.raw_bytes,
                                         'B1-L1-manifest.raw': result.after.original_load.outcome.manifest.raw_bytes,
                                         'B1-L2-manifest.raw': result.after.latest_load.outcome.manifest.raw_bytes}))

    def test_twelve_absent_exact_leaves_preserve_record_order_and_have_no_leaf_open_or_mutation(self):
        fields = self.fields()
        leaves = []
        for slot in SLOTS:
            resource, leaf = self.resource(slot)
            fields['resources'][slot] = resource
            leaves.append(leaf)
        raw = self.seed(fields)
        with self.traced() as trace:
            result = self.observer.observe()
        self.assertEqual(trace['leaves'], leaves)
        self.assertEqual([item.outcome for item in result.names], ['ABSENT'] * 12)
        self.assertEqual(tuple(item.resource for item in result.names),
                         result.original_load.outcome.manifest.known_resources)
        for item in result.names:
            self.assertEqual(item.ref, result.original_load.outcome.manifest.ref)
            self.assertEqual(item.guard, result.guard)
        self.record('twelve-absent-leaves', dict(names=[asdict(item) for item in result.names],
                                                before=batch_summary(result.before), after=batch_summary(result.after),
                                                events=trace['events']),
                    dict(trace['raw'], **{'original-manifest.raw': raw}))

    def test_regular_occupied_size_mode_and_hardlink_difference_remain_present(self):
        fields = self.fields()
        leaves = []
        for slot, payload, mode in (('RAW_CURRENT', b'', 0o600), ('DB_COPY_0', b'other-size', 0),
                                    ('DB_COPY_1', b'hardlink', 0o600)):
            resource, leaf = self.resource(slot)
            fields['resources'][slot] = resource
            self.create(leaf, payload=payload, mode=mode)
            leaves.append(leaf)
        alias = 'xnv_alias_' + str(uuid.uuid4())
        os.link(leaves[-1], alias, src_dir_fd=self.root_fd, dst_dir_fd=self.root_fd)
        value = os.stat(alias, dir_fd=self.root_fd, follow_symlinks=False)
        self.created[alias] = value.st_dev, value.st_ino
        self.seed(fields)
        with self.traced() as trace:
            result = self.observer.observe()
        self.assertEqual([item.outcome for item in result.names], ['PRESENT'] * 3)
        self.assertEqual(len(trace['leaves']), 3)
        self.assertGreater(os.stat(leaves[-1], dir_fd=self.root_fd).st_nlink, 1)
        self.record('occupied-fixtures', dict(names=[asdict(item) for item in result.names], events=trace['events']),
                    trace['raw'])

    def test_real_symlink_dangling_directory_and_fifo_are_unknown_without_following(self):
        fields = self.fields()
        target_resource, target = self.resource('DB_COPY_3')
        self.create(target, payload=b'private-target')
        for slot, kind, link_target in (('RAW_CURRENT', 'symlink', target),
                                        ('RAW_RETIRED', 'symlink', 'xnv_missing_' + str(uuid.uuid4())),
                                        ('DB_COPY_0', 'directory', None), ('DB_COPY_1', 'fifo', None)):
            resource, leaf = self.resource(slot)
            fields['resources'][slot] = resource
            self.create(leaf, kind=kind, target=link_target)
        self.seed(fields)
        with self.traced() as trace:
            result = self.observer.observe()
        self.assertEqual([item.outcome for item in result.names], ['UNKNOWN'] * 4)
        self.assertEqual(len(trace['leaves']), 4)
        self.record('nonregular-fixtures', dict(names=[asdict(item) for item in result.names], events=trace['events']),
                    trace['raw'])

    def test_exact_errno_not_exception_class_controls_individual_unknown_or_absent(self):
        fields = self.fields()
        resource, _ = self.resource()
        fields['resources']['RAW_CURRENT'] = resource
        self.seed(fields)
        cases = ((OSError(errno.ENOENT, 'controlled absence'), 'ABSENT'),
                 (PermissionError(errno.EACCES, 'controlled denial'), 'UNKNOWN'),
                 (OSError(errno.EPERM, 'controlled denial'), 'UNKNOWN'),
                 (OSError(errno.ENOTDIR, 'controlled kind'), 'UNKNOWN'),
                 (OSError(errno.ELOOP, 'controlled link'), 'UNKNOWN'),
                 (OSError(errno.EIO, 'controlled read'), 'UNKNOWN'),
                 (OSError(errno.ESTALE, 'controlled stale'), 'UNKNOWN'),
                 (FileNotFoundError(errno.EACCES, 'controlled class mismatch'), 'UNKNOWN'),
                 (FileNotFoundError('controlled missing errno'), 'UNKNOWN'))
        for error, outcome in cases:
            with self.subTest(errno=error.errno, error_type=type(error).__name__):
                with self.traced(leaf_error=error):
                    result = self.observer.observe()
                self.assertEqual(result.names[0].outcome, outcome)

    def test_pending_resources_are_preserved_without_invented_names(self):
        fields = self.fields()
        resource, leaf = self.resource('RAW_CURRENT')
        fields['resources']['RAW_CURRENT'] = resource
        object_id = str(uuid.uuid4())
        fields['resources']['DB_COPY_0'] = dict(state='UNKNOWN', name=None, object_id=None, size_bytes=None)
        fields['intents'] = [dict(intent_id=str(uuid.uuid4()), slot='DB_COPY_0', operation='CREATE',
                                  planned_name='/xnv_db_' + object_id, planned_object_id=object_id,
                                  planned_size_bytes='32')]
        self.seed(fields)
        with self.traced() as trace:
            result = self.observer.observe()
        self.assertEqual(trace['leaves'], [leaf])
        self.assertEqual(len(result.names), 1)
        self.assertFalse(result.original_load.outcome.manifest.complete_inventory)
        self.assertEqual(result.original_load.outcome.manifest.fields['intents'], fields['intents'])

    def test_six_representable_old_runtime_variants_keep_unknown_and_leaf_lookup_zero(self):
        for key in ('boot_id', 'pid_ns_inode', 'mnt_ns_inode', 'shm_mount_id', 'shm_dev', 'shm_root_inode'):
            with self.subTest(key=key):
                fields = self.fields()
                resource, _ = self.resource()
                fields['resources']['RAW_CURRENT'] = resource
                fields['runtime'][key] = str(uuid.uuid4()) if key == 'boot_id' else str(int(fields['runtime'][key]) + 1)
                self.seed(fields)
                with self.traced() as trace:
                    result = self.observer.observe()
                self.assertEqual([item.outcome for item in result.names], ['UNKNOWN'])
                self.assertEqual(trace['leaves'], [])
                self.assertEqual(len(trace['endpoints']), 2)
                self.assertTrue(any(not event.get('endpoint', True) for event in trace['events']))

    def test_non_tmpfs_typed_runtime_is_rejected_without_forging_valid_mismatch(self):
        fields = self.fields()
        fields['runtime']['shm_fs'] = 'ext4'
        with self.assertRaises(ManifestError):
            ResourceManifest(encode(fields))

    def test_sem_prefix_is_added_once_to_original_name_and_maximum_basename_is_not_truncated(self):
        fields = self.fields()
        suffix = uuid.uuid4().hex
        examples = (('RAW_0_WRITABLE', '/sem.' + suffix),
                    ('RAW_0_READY', '/' + suffix + 'q' * (127 - len(suffix))))
        leaves = []
        for slot, name in examples:
            resource, leaf = self.resource(slot, sem_name=name)
            fields['resources'][slot] = resource
            leaves.append(leaf)
        self.seed(fields)
        with self.traced() as trace:
            result = self.observer.observe()
        self.assertEqual(trace['leaves'], leaves)
        self.assertTrue(leaves[0].startswith('sem.sem.'))
        self.assertEqual(len(leaves[1].encode('ascii')), 131)
        self.assertEqual([item.outcome for item in result.names], ['ABSENT', 'ABSENT'])

    def test_first_source_missing_corrupt_or_io_error_is_not_empty_success(self):
        for kind in ('missing', 'corrupt', 'io_error'):
            with self.subTest(kind=kind):
                if self.data.exists():
                    self.data.unlink()
                if kind == 'corrupt':
                    self.data.write_bytes(b'{broken\n')
                    self.data.chmod(0o600)
                elif kind == 'io_error':
                    self.seed(self.fields())
                    self.data.chmod(0o640)
                try:
                    with self.traced() as trace:
                        with self.assertRaises(names_module.NameObservationUnavailable):
                            self.observer.observe()
                    self.assertEqual(trace['leaves'], [])
                finally:
                    if self.data.exists():
                        self.data.chmod(0o600)

    def test_cross_batch_source_raw_change_is_not_retargeted_even_if_canonical_values_match(self):
        for same_values in (False, True):
            with self.subTest(same_values=same_values):
                fields = self.fields()
                original = self.seed(fields)

                def before(number, trace):
                    if number == 2:
                        changed = dict(fields)
                        if not same_values:
                            changed['revision'] = '2'
                        raw = (json.dumps(changed, indent=1) + '\n').encode('ascii')
                        self.data.write_bytes(raw)

                with self.traced(before_endpoint=before):
                    with self.assertRaises(names_module.NameObservationUnavailable):
                        self.observer.observe()
                self.assertNotEqual(self.data.read_bytes(), original)

    def test_final_source_missing_or_corrupt_rejects_collected_names(self):
        for final_kind in ('missing', 'corrupt'):
            with self.subTest(final_kind=final_kind):
                self.seed(self.fields())

                def before(number, trace):
                    if number == 2:
                        if final_kind == 'missing':
                            self.data.unlink()
                        else:
                            self.data.write_bytes(b'{bad\n')

                # 출처 변경은 시험 harness만 수행하며 production mutation spy 밖에서 구분한다.
                actual_observe = ProcessObserver.observe
                number = 0

                def endpoint(observer):
                    nonlocal number
                    number += 1
                    before(number, {})
                    return actual_observe(observer)

                with patch.object(ProcessObserver, 'observe', endpoint):
                    with self.assertRaises(names_module.NameObservationUnavailable):
                        self.observer.observe()
                self.assertEqual(number, 2)

    def test_middle_named_same_inode_but_changed_mount_id_is_unavailable(self):
        self.seed(self.fields())
        actual_fdinfo = names_module.parse_fdinfo
        held_identity = None
        reopened = None
        pending = False
        injected = []

        def changed(raw):
            nonlocal pending
            parsed = actual_fdinfo(raw)
            if pending:
                pending = False
                injected.append(parsed)
                return parsed + 1
            return parsed

        with self.traced() as trace:
            actual_open = os.open

            def opening(path, flags, *args, **kwargs):
                nonlocal held_identity, reopened, pending
                descriptor = actual_open(path, flags, *args, **kwargs)
                if not trace['endpoint_active']:
                    if path == '/dev/shm':
                        value = os.fstat(descriptor)
                        identity = value.st_dev, value.st_ino
                        if held_identity is None:
                            held_identity = identity
                        elif reopened is None:
                            self.assertEqual(identity, held_identity)
                            reopened = descriptor
                    if reopened is not None and path == 'fdinfo/' + str(reopened):
                        pending = True
                return descriptor

            with patch('os.open', side_effect=opening), patch.object(names_module, 'parse_fdinfo', side_effect=changed):
                with self.assertRaises(names_module.NameObservationUnavailable):
                    self.observer.observe()
        self.assertEqual(len(trace['endpoints']), 1)
        self.assertEqual(len(injected), 1)

    def test_middle_selected_profile_failure_is_global_and_old_runtime_does_not_skip_it(self):
        actual_mounts = names_module.parse_mountinfo
        for old in (False, True):
            with self.subTest(old=old):
                fields = self.fields()
                resource, _ = self.resource()
                fields['resources']['RAW_CURRENT'] = resource
                if old:
                    fields['runtime']['boot_id'] = str(uuid.uuid4())
                self.seed(fields)

                def mounts(raw):
                    return tuple(replace(item, fs_type=b'ext4') if item.mountpoint == b'/dev/shm' else item
                                 for item in actual_mounts(raw))

                with patch.object(names_module, 'parse_mountinfo', side_effect=mounts), self.traced() as trace:
                    with self.assertRaises(names_module.NameObservationUnavailable):
                        self.observer.observe()
                self.assertEqual(trace['leaves'], [])
                self.assertEqual(len(trace['endpoints']), 1)

    def test_middle_boot_or_namespace_profile_uncertainty_is_not_individual_unknown(self):
        self.seed(self.fields())
        with patch.object(names_module, 'parse_boot_id', return_value=str(uuid.uuid4())), self.traced() as trace:
            with self.assertRaises(names_module.NameObservationUnavailable):
                self.observer.observe()
        self.assertEqual(len(trace['endpoints']), 1)

    def test_middle_fd_close_failure_is_global_close_once_and_never_closes_borrowed_store(self):
        self.seed(self.fields())
        actual_close = os.close
        actual_observe = ProcessObserver.observe
        endpoint_active = False
        endpoint_count = 0
        failed = None
        calls = []
        before = set(os.listdir('/proc/self/fd'))

        def endpoint(observer):
            nonlocal endpoint_active, endpoint_count
            endpoint_active = True
            endpoint_count += 1
            try:
                return actual_observe(observer)
            finally:
                endpoint_active = False

        def closing(descriptor):
            nonlocal failed
            if not endpoint_active:
                calls.append(descriptor)
            actual_close(descriptor)
            if not endpoint_active and failed is None:
                failed = descriptor
                raise OSError(errno.EIO, 'controlled post-close ambiguity')

        with patch.object(ProcessObserver, 'observe', endpoint), patch('os.close', side_effect=closing), \
                patch.object(ResourceStore, 'close', side_effect=AssertionError('borrowed close')):
            with self.assertRaises(names_module.NameObservationUnavailable):
                self.observer.observe()
        self.assertIsNotNone(failed)
        self.assertEqual(calls.count(failed), 1)
        self.assertEqual(endpoint_count, 1)
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
        self.assertEqual(self.store.load().outcome.kind, 'VALID')

    def test_all_middle_fds_are_returned_before_b1_and_success_leaves_no_fd_leak(self):
        self.seed(self.fields())
        before_fds = set(os.listdir('/proc/self/fd'))

        def before(number, trace):
            if number == 2:
                self.assertEqual(trace['middle_fds'], set())

        with self.traced(before_endpoint=before) as trace:
            self.observer.observe()
        self.assertEqual(len(trace['endpoints']), 2)
        self.assertEqual(trace['middle_fds'], set())
        self.assertEqual(set(os.listdir('/proc/self/fd')), before_fds)

    def test_closed_borrowed_store_is_not_reopened_or_replaced(self):
        self.seed(self.fields())
        self.store.close()
        with self.assertRaises(names_module.NameObservationUnavailable):
            self.observer.observe()

    def test_producer_copy_deepcopy_pickle_and_foreign_thread_are_usage_errors(self):
        for operation in (copy.copy, copy.deepcopy, pickle.dumps):
            with self.assertRaises(names_module.NameObservationUsageError):
                operation(self.observer)
        errors = []

        def foreign():
            try:
                self.observer.observe()
            except BaseException as error:
                errors.append(type(error))

        thread = threading.Thread(target=foreign, name='foreign-name-observer')
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [names_module.NameObservationUsageError])

    def test_outer_reentry_during_leaf_is_rejected_when_inner_public_observer_is_idle(self):
        fields = self.fields()
        resource, leaf = self.resource()
        fields['resources']['RAW_CURRENT'] = resource
        self.create(leaf)
        self.seed(fields)
        rejected = []

        def reenter(path):
            try:
                self.observer.observe()
            except names_module.NameObservationUsageError:
                rejected.append(True)

        with self.traced(leaf_callback=reenter):
            result = self.observer.observe()
        self.assertEqual(rejected, [True])
        self.assertEqual(result.names[0].outcome, 'PRESENT')

    @unittest.skipUnless(hasattr(os, 'fork'), '실제 fork 상속 오용은 Linux fork가 필요하다')
    def test_forked_producer_rejects_usage_without_detaching_parent_store(self):
        self.seed(self.fields())
        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            try:
                self.observer.observe()
            except BaseException as error:
                result = type(error).__name__
            else:
                result = 'accepted'
            os.write(write_fd, result.encode('ascii'))
            os.close(write_fd)
            os._exit(0)
        os.close(write_fd)
        try:
            ready, _, _ = select.select([read_fd], [], [], 5)
            if not ready:
                os.kill(child, 9)
                self.fail('fork 자식의 사용 거절이 끝나지 않았다')
            result = os.read(read_fd, 256).decode('ascii')
        finally:
            os.close(read_fd)
            _, status = os.waitpid(child, 0)
        self.assertTrue(os.WIFEXITED(status))
        self.assertEqual(result, 'NameObservationUsageError')
        self.assertEqual(self.store.load().outcome.kind, 'VALID')

    def test_actual_child_reap_between_batches_allows_owner_outcome_change(self):
        child = subprocess.Popen([sys.executable, '-B', '-S', '-c',
                                  "import sys; print('READY',flush=True); sys.stdin.read(1)"],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def reap():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

        self.addCleanup(reap)
        ready, _, _ = select.select([child.stdout], [], [], 5)
        self.assertTrue(ready)
        self.assertEqual(child.stdout.readline().strip(), 'READY')
        fields = self.fields()
        pid, ticks, child_raw = birth(child.pid)
        fields['owners'].append(dict(role='A', phase='CURRENT', pid=pid, start_ticks=str(ticks)))
        resource, leaf = self.resource()
        fields['resources']['RAW_CURRENT'] = resource
        self.create(leaf)
        raw = self.seed(fields)

        def finish(path):
            child.stdin.write('x')
            child.stdin.flush()
            child.communicate(timeout=5)

        with self.traced(leaf_callback=finish) as trace:
            result = self.observer.observe()
        self.assertEqual(child.returncode, 0)
        self.assertEqual([item.outcome for item in result.before.owners], ['ALIVE', 'ALIVE'])
        self.assertEqual([item.outcome for item in result.after.owners], ['ALIVE', 'EXITED'])
        self.assertEqual(result.names[0].outcome, 'PRESENT')
        self.record('actual-child-reaped-between-batches',
                    dict(before=batch_summary(result.before), after=batch_summary(result.after),
                         names=[asdict(item) for item in result.names], child_pid=pid, child_start_ticks=ticks,
                         reap_returncode=child.returncode, events=trace['events']),
                    dict(trace['raw'], **{'child-stat.raw': child_raw, 'source-manifest.raw': raw}))


if __name__ == '__main__':
    unittest.main()
