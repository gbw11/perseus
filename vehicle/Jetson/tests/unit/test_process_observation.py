"""실제 Linux proc 관측과 원 store 수명·출처 경계를 검사한다.

실행은 Nano의 전용 임시 store와 시험이 만든 자식에 한정한다. 오류 주입은
가시성·자료 실패 분류를 확인할 뿐 실제 mount/권한 변경이나 종료 증명이 아니다.
"""

from contextlib import contextmanager
from dataclasses import asdict, replace
import errno
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from xenova_vehicle.runtime import process_observation as observations
from xenova_vehicle.runtime.resource_manifest import ResourceManifest
from xenova_vehicle.runtime.resource_store import ResourceStore, StoreUsageError
from resource_guard_fixtures import encode, identifier, minimal_fields


def independent_birth(pid):
    raw = Path('/proc/' + str(pid) + '/stat').read_bytes()
    boundary = raw.rfind(b') ')
    suffix = raw[boundary + 2:].split()
    return int(raw[:raw.index(b' (')]), int(suffix[19]), raw


def independent_runtime():
    descriptor = os.open('/dev/shm', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        actual = os.fstat(descriptor)
        info = Path('/proc/self/fdinfo/' + str(descriptor)).read_bytes()
        mount_id = int(next(line.split(b':', 1)[1] for line in info.splitlines()
                            if line.startswith(b'mnt_id:')))
        raw = Path('/proc/self/mountinfo').read_bytes()
        record = next(line.split(b' - ', 1)[1] for line in raw.splitlines()
                      if int(line.split(b' ', 1)[0]) == mount_id)
        filesystem = record.split(b' ', 1)[0].decode('ascii')
        return dict(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text('ascii').strip(),
                    pid_ns_inode=str(os.stat('/proc/self/ns/pid').st_ino),
                    mnt_ns_inode=str(os.stat('/proc/self/ns/mnt').st_ino),
                    shm_mount_id=str(mount_id), shm_dev=str(actual.st_dev),
                    shm_root_inode=str(actual.st_ino), shm_fs=filesystem)
    finally:
        os.close(descriptor)


def batch_details(batch):
    def load(receipt):
        manifest = receipt.outcome.manifest
        return dict(lock_nonce=receipt.lock_nonce, load_id=receipt.outcome.load_id,
                    kind=receipt.outcome.kind, manifest_ref=None if manifest is None else asdict(manifest.ref))

    return dict(original_load=load(batch.original_load), latest_load=load(batch.latest_load),
                guard=asdict(batch.guard), owners=[asdict(owner) for owner in batch.owners],
                started_ns=batch.started_ns, completed_ns=batch.completed_ns)


@unittest.skipUnless(sys.platform == 'linux', '실제 Linux procfs 시험이며 다른 OS에서 대체 실행하지 않는다')
class ProcessObservationTests(unittest.TestCase):
    evidence_directory = None

    def record(self, name, details, payloads):
        if self.evidence_directory is None:
            return
        directory = Path(self.evidence_directory) / name
        directory.mkdir(parents=True, exist_ok=False)
        files = {}
        for filename, raw in payloads.items():
            with (directory / filename).open('xb') as output:
                output.write(raw)
            files[filename] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        report = dict(qualification='INDEPENDENT_TEST_READS_NOT_ATOMIC_PRODUCT_SNAPSHOT',
                      details=details, files=files)
        with (directory / 'summary.json').open('x', encoding='utf-8', newline='\n') as output:
            output.write(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(',', ':')) + '\n')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='xenova-observer-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.directory.chmod(0o700)
        self.data = self.directory / 'resource-set.json'
        self.store = ResourceStore(str(self.directory))
        self.addCleanup(self.store.close)
        self.service = identifier(600)
        self.observer = observations.ProcessObserver(self.store, self.service)

    def fields(self, *, child=None):
        fields = minimal_fields()
        fields['runtime'] = independent_runtime()
        fields['service_instance'] = self.service
        pid, ticks, _ = independent_birth(os.getpid())
        fields['owners'] = [dict(role='S', phase='CURRENT', pid=pid, start_ticks=str(ticks))]
        if child is not None:
            pid, ticks, _ = independent_birth(child.pid)
            fields['owners'].append(dict(role='A', phase='CURRENT', pid=pid, start_ticks=str(ticks)))
        return fields

    def seed(self, fields=None):
        raw = encode(self.fields() if fields is None else fields)
        self.data.write_bytes(raw)
        self.data.chmod(0o600)
        return raw

    def child(self):
        process = subprocess.Popen([sys.executable, '-B', '-S', '-c',
                                    "import sys; print('READY', flush=True); sys.stdin.read(1)"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)

        def reap():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

        self.addCleanup(reap)
        ready, _, _ = select.select([process.stdout], [], [], 5)
        self.assertTrue(ready, '시험 자식이 시작을 알리지 않았다')
        self.assertEqual(process.stdout.readline().strip(), 'READY')
        return process

    def seed_child_owner(self):
        child = self.child()
        fields = self.fields()
        pid, ticks, raw = independent_birth(child.pid)
        fields['owners'] = [dict(role='S', phase='CURRENT', pid=pid, start_ticks=str(ticks))]
        self.seed(fields)
        return raw

    @contextmanager
    def owner_fault(self, *, stage='stat', error=None, raw=None):
        actual_open, actual_read, actual_close = os.open, os.read, os.close
        owner_directories, replacements = set(), {}

        def open_native(path, flags, *args, **kwargs):
            numeric = type(path) is str and path.isascii() and path.isdecimal()
            owner_stat = path == 'stat' and kwargs.get('dir_fd') in owner_directories
            if error is not None and ((stage == 'directory' and numeric) or (stage == 'stat' and owner_stat)):
                raise error
            descriptor = actual_open(path, flags, *args, **kwargs)
            if numeric:
                owner_directories.add(descriptor)
            if owner_stat:
                replacements[descriptor] = 0
            return descriptor

        def read_native(descriptor, maximum):
            if descriptor in replacements:
                if stage == 'read' and error is not None:
                    raise error
                if raw is not None:
                    position = replacements[descriptor]
                    chunk = raw[position:position + maximum]
                    replacements[descriptor] += len(chunk)
                    return chunk
            return actual_read(descriptor, maximum)

        def close_native(descriptor):
            owner_directories.discard(descriptor)
            replacements.pop(descriptor, None)
            return actual_close(descriptor)

        with patch('os.open', side_effect=open_native), patch('os.read', side_effect=read_native), \
                patch('os.close', side_effect=close_native):
            yield

    def test_constructor_performs_no_file_or_store_io_and_rejects_wrong_inputs(self):
        with patch('os.open', side_effect=AssertionError('constructor file I/O')), \
                patch.object(ResourceStore, 'load', side_effect=AssertionError('constructor load')):
            observer = observations.ProcessObserver(self.store, self.service)
        self.assertIsNotNone(observer)
        for store, service in ((None, self.service), (object(), self.service),
                               (self.store, None), (self.store, identifier(0xabc).upper()), (self.store, True)):
            with self.subTest(store_type=type(store).__name__, service_type=type(service).__name__):
                with self.assertRaises(observations.ObservationUsageError):
                    observations.ProcessObserver(store, service)

    def test_missing_has_two_actual_distinct_receipts_and_no_install_or_owner_claim(self):
        batch = self.observer.observe()
        self.assertEqual(batch.original_load.outcome.kind, 'MISSING')
        self.assertEqual(batch.latest_load.outcome.kind, 'MISSING')
        self.assertIsNot(batch.original_load, batch.latest_load)
        self.assertNotEqual(batch.original_load.outcome.load_id, batch.latest_load.outcome.load_id)
        self.assertEqual(batch.owners, ())
        self.assertEqual(batch.guard.guard_id, self.store.lock_nonce)
        self.assertEqual(batch.guard.service_instance, self.service)
        self.assertLessEqual(batch.started_ns, batch.completed_ns)
        with self.assertRaises((AttributeError, TypeError)):
            batch.completed_ns = 0

    def test_actual_scope_and_current_birth_match_independent_raw_sources(self):
        expected = independent_runtime()
        pid, ticks, independent_stat = independent_birth(os.getpid())
        batch = self.observer.observe()
        for key, value in expected.items():
            observed = getattr(batch.guard.runtime, key)
            self.assertEqual(observed, value if key in ('boot_id', 'shm_fs') else int(value))
        self.assertEqual((batch.guard.birth.pid, batch.guard.birth.start_ticks), (pid, ticks))
        if self.evidence_directory is not None:
            payloads = {name: Path(path).read_bytes() for name, path in (
                ('self-stat.raw', '/proc/self/stat'), ('self-status.raw', '/proc/self/status'),
                ('boot-id.raw', '/proc/sys/kernel/random/boot_id'), ('mountinfo.raw', '/proc/self/mountinfo'))}
            payloads['self-stat-before.raw'] = independent_stat
            namespaces = {}
            for name in ('pid', 'mnt'):
                path = '/proc/self/ns/' + name
                value = os.stat(path)
                namespaces[name] = dict(device=value.st_dev, inode=value.st_ino, link=os.readlink(path))
            descriptor = os.open('/dev/shm', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                value = os.fstat(descriptor)
                shm = dict(descriptor=descriptor, device=value.st_dev, inode=value.st_ino, mode=value.st_mode)
                payloads['shm-root-fdinfo.raw'] = Path('/proc/self/fdinfo/' + str(descriptor)).read_bytes()
            finally:
                os.close(descriptor)
            self.record('actual-scope', dict(expected_runtime=expected,
                                             expected_birth=dict(pid=pid, start_ticks=ticks),
                                             namespaces=namespaces, shm_root=shm, batch=batch_details(batch)), payloads)

    def test_same_raw_source_keeps_original_owner_target_and_latest_receipt_can_propose(self):
        raw = self.seed()
        loads = []
        actual_load = ResourceStore.load

        def loading(store):
            receipt = actual_load(store)
            loads.append(receipt)
            return receipt

        with patch.object(ResourceStore, 'load', loading):
            batch = self.observer.observe()
        self.assertEqual(len(loads), 2)
        self.assertIs(batch.original_load, loads[0])
        self.assertIs(batch.latest_load, loads[-1])
        self.assertEqual(batch.original_load.outcome.manifest.raw_bytes, raw)
        self.assertEqual(batch.latest_load.outcome.manifest.raw_bytes, raw)
        self.assertEqual(len(batch.owners), 1)
        self.assertEqual(batch.owners[0].outcome, 'ALIVE')
        self.assertIs(batch.owners[0].owner, batch.original_load.outcome.manifest.known_owners[0])
        self.assertEqual(batch.owners[0].ref, batch.original_load.outcome.manifest.ref)
        self.assertIs(batch.owners[0].guard, batch.guard)
        fields = batch.original_load.outcome.manifest.fields
        fields['revision'] = '2'
        target = ResourceManifest(encode(fields))
        with self.assertRaises(StoreUsageError):
            self.store.propose(batch.original_load, target, identifier(601))
        proposal = self.store.propose(batch.latest_load, target, identifier(601))
        self.assertIs(proposal.expected, batch.latest_load)

    def test_owned_child_is_alive_then_exited_only_after_actual_wait_and_reap(self):
        child = self.child()
        self.seed(self.fields(child=child))
        if self.evidence_directory is not None:
            child_pid, child_ticks, child_raw = independent_birth(child.pid)
        alive = self.observer.observe()
        self.assertEqual([item.outcome for item in alive.owners], ['ALIVE', 'ALIVE'])
        child.stdin.write('x')
        child.stdin.flush()
        child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0)
        exited = self.observer.observe()
        self.assertEqual([item.outcome for item in exited.owners], ['ALIVE', 'EXITED'])
        self.assertEqual(alive.original_load.outcome.manifest.raw_bytes,
                         exited.original_load.outcome.manifest.raw_bytes)
        if self.evidence_directory is not None:
            self.record('child-alive-reaped-exited',
                        dict(child_birth=dict(pid=child_pid, start_ticks=child_ticks),
                             reap_returncode=child.returncode, alive=batch_details(alive), exited=batch_details(exited)),
                        {'child-before-reap-stat.raw': child_raw,
                         'alive-L1-manifest.raw': alive.original_load.outcome.manifest.raw_bytes,
                         'alive-L2-manifest.raw': alive.latest_load.outcome.manifest.raw_bytes,
                         'exited-L1-manifest.raw': exited.original_load.outcome.manifest.raw_bytes,
                         'exited-L2-manifest.raw': exited.latest_load.outcome.manifest.raw_bytes})

    def test_batch_replace_rejects_missing_reordered_or_retargeted_owner_statements(self):
        child = self.child()
        self.seed(self.fields(child=child))
        batch = self.observer.observe()
        self.assertEqual(replace(batch), batch)
        wrong_guard = replace(batch.guard, service_instance=identifier(702))
        wrong_ref = replace(batch.original_load.outcome.manifest.ref,
                            revision=batch.original_load.outcome.manifest.revision + 1)
        variants = (
            dict(latest_load=batch.original_load),
            dict(guard=replace(batch.guard, guard_id=identifier(703))),
            dict(owners=()),
            dict(owners=tuple(reversed(batch.owners))),
            dict(owners=(batch.owners[0], batch.owners[0])),
            dict(owners=(replace(batch.owners[0], ref=wrong_ref), batch.owners[1])),
            dict(owners=(replace(batch.owners[0], guard=wrong_guard), batch.owners[1])),
            dict(guard=wrong_guard),
        )
        for changed in variants:
            with self.subTest(fields=tuple(changed)):
                with self.assertRaises(observations.ObservationUsageError):
                    replace(batch, **changed)
        self.data.unlink()
        missing = self.observer.observe()
        self.assertEqual(missing.owners, ())
        with self.assertRaises(observations.ObservationUsageError):
            replace(missing, owners=batch.owners)

    def test_pending_owner_is_not_filled_in_or_added_to_known_observations(self):
        fields = self.fields()
        fields['owners'].append(dict(role='M', phase='CURRENT', pid=None, start_ticks=None))
        self.seed(fields)
        batch = self.observer.observe()
        self.assertFalse(batch.original_load.outcome.manifest.complete_inventory)
        self.assertEqual(len(batch.owners), 1)
        self.assertIsNone(batch.original_load.outcome.manifest.owners[1].pid)

    def test_old_boot_or_pid_namespace_is_unknown_without_numeric_pid_lookup(self):
        for key in ('boot_id', 'pid_ns_inode'):
            with self.subTest(key=key):
                fields = self.fields()
                fields['runtime'][key] = identifier(999) if key == 'boot_id' else str(int(fields['runtime'][key]) + 1)
                self.seed(fields)
                with self.owner_fault(stage='directory', error=AssertionError('old view PID lookup')):
                    batch = self.observer.observe()
                self.assertEqual([item.outcome for item in batch.owners], ['UNKNOWN'])

    def test_mount_namespace_difference_does_not_change_process_birth(self):
        fields = self.fields()
        fields['runtime']['mnt_ns_inode'] = str(int(fields['runtime']['mnt_ns_inode']) + 1)
        self.seed(fields)
        self.assertEqual(self.observer.observe().owners[0].outcome, 'ALIVE')

    def test_other_start_ticks_classifies_original_birth_exited_without_registering_new_owner(self):
        fields = self.fields()
        fields['owners'][0]['start_ticks'] = str(int(fields['owners'][0]['start_ticks']) + 1)
        self.seed(fields)
        batch = self.observer.observe()
        self.assertEqual(batch.owners[0].outcome, 'EXITED')
        self.assertEqual(batch.owners[0].owner.start_ticks, int(fields['owners'][0]['start_ticks']))

    def test_same_birth_zombie_or_dead_state_still_blocks_cleanup_as_alive(self):
        raw = self.seed_child_owner()
        boundary = raw.rfind(b') ') + 2
        for state in (b'Z', b'X'):
            with self.subTest(state=state):
                replacement = raw[:boundary] + state + raw[boundary + 1:]
                with self.owner_fault(raw=replacement):
                    batch = self.observer.observe()
                self.assertEqual(batch.owners[0].outcome, 'ALIVE')

    def test_initial_numeric_directory_absence_is_distinct_from_opened_stat_disappearance(self):
        self.seed_child_owner()
        for stage, outcome in (('directory', 'EXITED'), ('stat', 'UNKNOWN'), ('read', 'UNKNOWN')):
            with self.subTest(stage=stage):
                with self.owner_fault(stage=stage, error=FileNotFoundError(errno.ENOENT, 'injected absence')):
                    batch = self.observer.observe()
                self.assertEqual(batch.owners[0].outcome, outcome)

    def test_permission_io_esrch_and_malformed_owner_stat_keep_unknown_row(self):
        self.seed_child_owner()
        cases = (('directory', PermissionError(errno.EACCES, 'injected denial'), None),
                 ('directory', FileNotFoundError(errno.EACCES, 'injected mismatched errno'), None),
                 ('directory', FileNotFoundError('injected missing errno'), None),
                 ('stat', PermissionError(errno.EPERM, 'injected denial'), None),
                 ('stat', ProcessLookupError(errno.ESRCH, 'injected disappearance'), None),
                 ('read', OSError(errno.EIO, 'injected read failure'), None),
                 ('stat', None, b'truncated\n'), ('stat', None, b'x' * 4097))
        for stage, error, raw in cases:
            with self.subTest(stage=stage, error_type=type(error).__name__, size=None if raw is None else len(raw)):
                with self.owner_fault(stage=stage, error=error, raw=raw):
                    batch = self.observer.observe()
                self.assertEqual(len(batch.owners), 1)
                self.assertEqual(batch.owners[0].outcome, 'UNKNOWN')

    def test_confirmed_current_birth_is_not_reported_exited_from_numeric_lookup_fault(self):
        self.seed()
        with self.owner_fault(stage='directory', error=FileNotFoundError(errno.ENOENT, 'injected self lookup')):
            batch = self.observer.observe()
        self.assertEqual(batch.owners[0].outcome, 'ALIVE')
        self.assertEqual(batch.owners[0].owner.birth, batch.guard.birth)

    def test_bad_first_source_is_unavailable_before_any_runtime_root_is_opened(self):
        self.data.write_bytes(b'{broken\n')
        self.data.chmod(0o600)
        actual_open = os.open

        def opening(path, *args, **kwargs):
            if path in ('/proc', '/dev/shm'):
                raise AssertionError('runtime read after corrupt source')
            return actual_open(path, *args, **kwargs)

        with patch('os.open', side_effect=opening):
            with self.assertRaises(observations.ObservationUnavailable):
                self.observer.observe()

    def test_changed_raw_at_final_load_is_not_retargeted_to_new_ref(self):
        fields = self.fields()
        self.seed(fields)
        actual_load = ResourceStore.load
        count = 0

        def loading(store):
            nonlocal count
            count += 1
            if count == 2:
                fields['revision'] = '2'
                self.seed(fields)
            return actual_load(store)

        with patch.object(ResourceStore, 'load', loading):
            with self.assertRaises(observations.ObservationUnavailable):
                self.observer.observe()
        self.assertEqual(count, 2)
        self.assertEqual(self.store.load().outcome.manifest.revision, 2)

    def test_missing_valid_transition_in_either_direction_is_unavailable(self):
        for initial_valid in (False, True):
            with self.subTest(initial_valid=initial_valid):
                if self.data.exists():
                    self.data.unlink()
                fields = self.fields()
                if initial_valid:
                    self.seed(fields)
                actual_load = ResourceStore.load
                count = 0

                def loading(store):
                    nonlocal count
                    count += 1
                    if count == 2:
                        if initial_valid:
                            self.data.unlink()
                        else:
                            self.seed(fields)
                    return actual_load(store)

                with patch.object(ResourceStore, 'load', loading):
                    with self.assertRaises(observations.ObservationUnavailable):
                        self.observer.observe()
                self.assertEqual(count, 2)

    def test_closed_borrowed_store_is_unavailable_and_observer_never_closes_it(self):
        with patch.object(ResourceStore, 'close', side_effect=AssertionError('borrowed close')):
            self.observer.observe()
        self.assertEqual(self.store.load().outcome.kind, 'MISSING')
        self.store.close()
        with self.assertRaises(observations.ObservationUnavailable):
            self.observer.observe()

    def test_runtime_boot_change_or_selected_mount_profile_error_is_unavailable(self):
        actual_boot = observations.parse_boot_id
        calls = 0

        def boot(raw):
            nonlocal calls
            calls += 1
            return actual_boot(raw) if calls == 1 else identifier(888)

        with patch.object(observations, 'parse_boot_id', side_effect=boot):
            with self.assertRaises(observations.ObservationUnavailable):
                self.observer.observe()
        actual_mounts = observations.parse_mountinfo

        def mounts(raw):
            return tuple(replace(item, super_options=item.super_options + (b'hidepid=2',))
                         if item.fs_type == b'proc' and item.mountpoint == b'/proc' else item
                         for item in actual_mounts(raw))

        with patch.object(observations, 'parse_mountinfo', side_effect=mounts):
            with self.assertRaises(observations.ObservationUnavailable):
                self.observer.observe()

    def test_wrong_self_status_and_missing_selected_mount_are_unavailable(self):
        with patch.object(observations, 'validate_self_status', side_effect=observations.ProcFormatError('injected view')):
            with self.assertRaises(observations.ObservationUnavailable):
                self.observer.observe()
        with patch.object(observations, 'parse_mountinfo', return_value=()):
            with self.assertRaises(observations.ObservationUnavailable):
                self.observer.observe()

    def test_reopened_root_same_inode_but_different_mount_id_is_unavailable(self):
        actual_open = os.open
        actual_fdinfo = observations.parse_fdinfo
        held_identity = None
        reopened = None
        pending = False
        injected = []

        def opening(path, flags, *args, **kwargs):
            nonlocal held_identity, reopened, pending
            descriptor = actual_open(path, flags, *args, **kwargs)
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

        def fdinfo(raw):
            nonlocal pending
            mount_id = actual_fdinfo(raw)
            if pending:
                pending = False
                injected.append(mount_id)
                return mount_id + 1
            return mount_id

        with patch('os.open', side_effect=opening), patch.object(observations, 'parse_fdinfo', side_effect=fdinfo):
            with self.assertRaises(observations.ObservationUnavailable):
                self.observer.observe()
        self.assertEqual(len(injected), 1)
        self.assertIsNotNone(reopened)

    def test_native_short_reads_accumulate_and_proc_zero_st_size_is_not_rejected(self):
        self.seed()
        actual_read = os.read

        def short_read(descriptor, maximum):
            return actual_read(descriptor, min(maximum, 7))

        with patch('os.read', side_effect=short_read):
            batch = self.observer.observe()
        self.assertEqual(batch.owners[0].outcome, 'ALIVE')
        self.assertEqual(os.stat('/proc/self/stat').st_size, 0)

    def test_kernel_comm_truncates_requested_sixteen_bytes_and_public_parser_accepts_output(self):
        script = r'''
import json
import os
from pathlib import Path
records = []
for requested in (b'abcdefghijklmno', b'ABCDEFGHIJKLMNOP'):
    descriptor = os.open('/proc/self/comm', os.O_WRONLY | os.O_CLOEXEC)
    try:
        written = os.write(descriptor, requested)
    finally:
        os.close(descriptor)
    records.append(dict(pid=os.getpid(), requested=requested.hex(), written=written,
                        stat=Path('/proc/self/stat').read_bytes().hex(),
                        comm=Path('/proc/self/comm').read_bytes().hex()))
print(json.dumps(records), flush=True)
'''
        process = subprocess.Popen([sys.executable, '-B', '-S', '-c', script],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            output, error = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            raise
        self.assertEqual(process.returncode, 0, error)
        rows = json.loads(output)
        requested_names = (b'abcdefghijklmno', b'ABCDEFGHIJKLMNOP')
        self.assertEqual(len(rows), len(requested_names))
        payloads, details = {}, []
        original_birth = None
        for requested, row in zip(requested_names, rows):
            stat_raw = bytes.fromhex(row['stat'])
            comm_raw = bytes.fromhex(row['comm'])
            self.assertEqual(bytes.fromhex(row['requested']), requested)
            self.assertEqual(row['pid'], process.pid)
            boundary = stat_raw.rfind(b') ')
            comm = stat_raw[stat_raw.index(b' (') + 2:boundary]
            self.assertEqual(comm, requested[:15])
            self.assertEqual(len(comm), 15)
            self.assertEqual(comm_raw, requested[:15] + b'\n')
            parsed = observations.parse_proc_stat(stat_raw, process.pid)
            birth = parsed.pid, parsed.start_ticks
            if original_birth is None:
                original_birth = birth
            self.assertEqual(birth, original_birth)
            label = str(len(requested))
            payloads['requested-' + label + '.raw'] = requested
            payloads['kernel-stat-' + label + '.raw'] = stat_raw
            payloads['kernel-comm-' + label + '.raw'] = comm_raw
            details.append(dict(requested_bytes=len(requested), write_return=row['written'],
                                kernel_comm_bytes=len(comm), parsed=asdict(parsed)))
        self.record('kernel-comm-limits', dict(child_pid=process.pid, reap_returncode=process.returncode,
                                               cases=details), payloads)

    def test_regular_file_reader_4096_and_4097_caps_are_separate_from_proc_stat_schema(self):
        actual_read = os.read
        cases = []
        for length in (4096, 4097):
            basename = 'bounded-reader-' + str(length) + '.bin'
            payload = b'x' * length
            (self.directory / basename).write_bytes(payload)
            before = set(os.listdir('/proc/self/fd'))
            owned = observations._OwnedFds()
            calls = []

            def reading(descriptor, maximum):
                raw = actual_read(descriptor, maximum)
                calls.append(dict(requested=maximum, returned=len(raw)))
                return raw

            try:
                parent = owned.open(str(self.directory), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                with patch('os.read', side_effect=reading):
                    if length == 4096:
                        self.assertEqual(observations._read_at(owned, parent, basename, 4096), payload)
                    else:
                        with self.assertRaises(observations.ObservationUnavailable):
                            observations._read_at(owned, parent, basename, 4096)
            finally:
                owned.close_all()
            expected = ([dict(requested=4097, returned=4096), dict(requested=1, returned=0)]
                        if length == 4096 else [dict(requested=4097, returned=4097)])
            self.assertEqual(calls, expected)
            self.assertEqual(set(os.listdir('/proc/self/fd')), before)
            cases.append(dict(file_bytes=length, read_calls=calls,
                              outcome='RETURNED_BYTES' if length == 4096 else 'UNAVAILABLE'))
        self.assertEqual(self.store.load().outcome.kind, 'MISSING')
        self.record('stat-read-cap',
                    dict(qualification='REGULAR_FILE_READER_PRIMITIVE_NOT_KERNEL_STAT_OR_SCHEMA', cases=cases), {})

    def test_success_and_unknown_calls_return_all_owned_descriptors(self):
        self.seed_child_owner()
        before = set(os.listdir('/proc/self/fd'))
        for _ in range(3):
            self.observer.observe()
        with self.owner_fault(stage='read', error=OSError(errno.EIO, 'injected failure')):
            batch = self.observer.observe()
        self.assertEqual(batch.owners[0].outcome, 'UNKNOWN')
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_owned_descriptor_close_error_is_unavailable_without_retrying_closed_number(self):
        actual_close = os.close
        failed = None
        calls = []
        before = set(os.listdir('/proc/self/fd'))

        def closing(descriptor):
            nonlocal failed
            calls.append(descriptor)
            actual_close(descriptor)
            if failed is None:
                failed = descriptor
                raise OSError(errno.EIO, 'injected post-close failure')

        with patch('os.close', side_effect=closing):
            with self.assertRaises(observations.ObservationUnavailable):
                self.observer.observe()
        self.assertIsNotNone(failed)
        self.assertEqual(calls.count(failed), 1)
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
        self.assertEqual(self.store.load().outcome.kind, 'MISSING')

    def test_monotonic_clock_regression_or_out_of_range_is_unavailable(self):
        for times in ((2, 1), (-1, 2), (True, 2), (1, 2**64)):
            with self.subTest(times=times):
                with patch.object(observations.time, 'monotonic_ns', side_effect=times):
                    with self.assertRaises(observations.ObservationUnavailable):
                        self.observer.observe()

    def test_foreign_thread_and_reentrant_call_are_usage_errors_before_io(self):
        errors = []

        def foreign():
            try:
                self.observer.observe()
            except BaseException as error:
                errors.append(type(error))

        thread = threading.Thread(target=foreign, name='foreign-observer')
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [observations.ObservationUsageError])
        actual_load = ResourceStore.load
        entered = False
        rejected = []

        def loading(store):
            nonlocal entered
            if not entered:
                entered = True
                try:
                    self.observer.observe()
                except observations.ObservationUsageError:
                    rejected.append(True)
            return actual_load(store)

        with patch.object(ResourceStore, 'load', loading):
            self.observer.observe()
        self.assertEqual(rejected, [True])

    @unittest.skipUnless(hasattr(os, 'fork'), '실제 fork 상속 오용은 Linux fork가 필요하다')
    def test_forked_observer_is_rejected_without_closing_parent_store(self):
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
            os.write(write_fd, json.dumps(result).encode('ascii'))
            os.close(write_fd)
            os._exit(0)
        os.close(write_fd)
        try:
            ready, _, _ = select.select([read_fd], [], [], 5)
            if not ready:
                os.kill(child, 9)
                self.fail('fork 자식의 오용 거절이 끝나지 않았다')
            result = json.loads(os.read(read_fd, 4096))
        finally:
            os.close(read_fd)
            _, status = os.waitpid(child, 0)
        self.assertTrue(os.WIFEXITED(status))
        self.assertEqual(result, 'ObservationUsageError')
        self.assertEqual(self.store.load().outcome.kind, 'MISSING')


if __name__ == '__main__':
    unittest.main()
