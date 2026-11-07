"""D 단독 SQLite owner의 discovery/binding과 정상 종료를 연결한다.

현재 local schema1은 bootstrap metadata와 빈 checkpoint 표를 지원한다. 실제
checkpoint payload/복원·range 예약은 아직 처리하지 않는다. 알 수 없는 DB나
checkpoint가 있는 DB를 빈 새 지도로 바꾸지 않는다. SQLite는 D backend를 열 때만
import하며 S는 작은 profile·원 discovery 결과의 의미 검사만 재사용한다.
"""

from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import uuid

from .lifecycle_messages import LifecyclePeer, LifecycleReceiver, parse_lifecycle
from .local_envelope import StructuralPacket
from .resident_transport import ResidentLink, j_us


_APPLICATION_ID = 0x584E5631
_FORMAT = 'xenova-storage-bootstrap-1'
_JOB_US = 5_000_000


class StorageProtocolError(ValueError):
    """원 peer/profile/nonce/기한 또는 지원하지 않는 storage 메시지."""


class StorageIncompatible(RuntimeError):
    """기존 파일은 현재 지원하는 DB가 아니다. 새 DB로 덮지 않는다."""


class StorageUnknown(RuntimeError):
    """내구 완료·native 작업·정리를 확정하지 못했다."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def _uuid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value:
        raise StorageProtocolError('expected canonical UUID')
    return value


def _decimal(value, *, positive=False):
    if (type(value) is not str or not 1 <= len(value) <= 20 or not value.isascii() or not value.isdecimal()
            or len(value) > 1 and value[0] == '0'):
        raise StorageProtocolError('expected canonical u64 string')
    number = int(value)
    if number > 2**64 - 1 or positive and number == 0:
        raise StorageProtocolError('u64 is outside its range')
    return number


def _keys(value, names):
    if type(value) is not dict or set(value) != set(names.split()):
        raise StorageProtocolError('storage fields differ from the exact variant')


def _highwater(value):
    number = _decimal(value, positive=True)
    if number > 2**63 - 1:
        raise StorageProtocolError('durable provenance highwater is not signed-safe')
    return number


@dataclass(frozen=True)
class StorageProfile:
    database_path: str
    database_open_policy: str = 'EXISTING_ONLY'
    schema_version: int = 1
    selection_policy: str = 'LATEST_COMPLETE'

    def __post_init__(self):
        if (type(self.database_path) is not str or not Path(self.database_path).is_absolute()
                or '\x00' in self.database_path or len(self.database_path) > 1024):
            raise ValueError('storage database path must be explicit and absolute')
        if self.database_open_policy not in ('EXISTING_ONLY', 'CREATE_IF_ABSENT'):
            raise ValueError('unsupported database open policy')
        if type(self.schema_version) is not int or self.schema_version != 1 or self.selection_policy != 'LATEST_COMPLETE':
            raise ValueError('only schema1/LATEST_COMPLETE is supported')

    @property
    def fields(self):
        return dict(database_path=self.database_path, database_open_policy=self.database_open_policy,
                    schema_version=self.schema_version, selection_policy=self.selection_policy)

    @property
    def sha256(self):
        return hashlib.sha256(_canonical(self.fields)).hexdigest()


@dataclass(frozen=True)
class StorageBoot:
    peer: LifecyclePeer
    startup_deadline_us: int
    storage_profile: StorageProfile
    copy_handles: object = None
    diagnostic_directory: str | None = None

    def __post_init__(self):
        if type(self.peer) is not LifecyclePeer or self.peer.role != 'D' or type(self.storage_profile) is not StorageProfile:
            raise ValueError('expected registered D and storage profile')
        if type(self.startup_deadline_us) is not int or not 0 <= self.startup_deadline_us <= 2**64 - 1:
            raise ValueError('startup deadline must be u64 microseconds')


def discovery_payload(profile, nonce):
    if type(profile) is not StorageProfile:
        raise StorageProtocolError('expected pinned storage profile')
    return dict(op='DISCOVER', body=dict(discovery_nonce=_uuid(nonce), storage_profile_sha256=profile.sha256))


def _packet(packet, peer, kind, revision, *, child_sender):
    if type(packet) is not StructuralPacket or type(peer) is not LifecyclePeer or peer.role != 'D':
        raise StorageProtocolError('expected structural packet and registered D')
    fields = packet.fields
    if (fields['kind'], fields['binding_domain'], fields['binding_revision'], fields['service_instance'],
            fields['channel_epoch'], fields['sender_instance']) != (kind, 'STORAGE', str(revision),
                peer.service_instance, peer.channel_epoch, peer.worker_instance if child_sender else peer.service_instance):
        raise StorageProtocolError('storage packet differs from its registered source/domain/revision')
    issued, expires = int(fields['issued_j_us']), int(fields['expires_j_us'])
    if not issued < expires or expires - issued > _JOB_US:
        raise StorageProtocolError('storage operation must retain its original five-second bound')
    return fields


def parse_discover(packet, peer, profile, *, now_us):
    fields = _packet(packet, peer, 'DB_JOB', 0, child_sender=False)
    _keys(fields['payload'], 'op body')
    if fields['payload']['op'] != 'DISCOVER':
        raise StorageProtocolError('only DISCOVER is supported before storage binding')
    body = fields['payload']['body']
    _keys(body, 'discovery_nonce storage_profile_sha256')
    _uuid(body['discovery_nonce'])
    if body['storage_profile_sha256'] != profile.sha256:
        raise StorageProtocolError('discovery profile differs from the pinned profile')
    if not int(fields['issued_j_us']) <= now_us < int(fields['expires_j_us']):
        raise StorageProtocolError('discovery is outside its original lifetime')
    return body


def _bootstrap_body(body, profile):
    if type(profile) is not StorageProfile:
        raise StorageProtocolError('expected pinned storage profile')
    _keys(body, 'discovery_nonce storage_profile_sha256 status database_uuid schema_version allocator_highwater allocator_ready selected_checkpoint')
    _uuid(body['discovery_nonce'])
    if body['storage_profile_sha256'] != profile.sha256 or type(body['allocator_ready']) is not bool:
        raise StorageProtocolError('bootstrap profile or allocator report differs')
    if body['status'] not in ('EMPTY', 'COMPLETE', 'INCOMPATIBLE', 'FAILED', 'UNKNOWN'):
        raise StorageProtocolError('unknown bootstrap status')
    if len(_canonical(body)) > 2048:
        raise StorageProtocolError('bootstrap body exceeds 2048 bytes')
    if body['status'] == 'EMPTY':
        _uuid(body['database_uuid'])
        _highwater(body['allocator_highwater'])
        if (type(body['schema_version']) is not int or body['schema_version'] != 1
                or not body['allocator_ready'] or body['selected_checkpoint'] is not None):
            raise StorageProtocolError('EMPTY needs actual schema/allocator and no selected checkpoint')
    elif body['status'] == 'COMPLETE':
        # 실제 checkpoint payload/closure 검증이 연결되기 전에는 수락 범위를 넓히지 않는다.
        raise StorageProtocolError('checkpoint bootstrap is not implemented by this runtime profile')
    elif (any(body[key] is not None for key in ('database_uuid', 'schema_version', 'allocator_highwater', 'selected_checkpoint'))
          or body['allocator_ready']):
        raise StorageProtocolError('failed discovery cannot supply successful metadata')
    return body


def parse_bootstrap_result(packet, peer, profile, original_discover, *, now_us):
    original = parse_discover(original_discover, peer, profile, now_us=now_us)
    fields = _packet(packet, peer, 'DB_JOB', 0, child_sender=True)
    _keys(fields['payload'], 'op body')
    if fields['payload']['op'] != 'BOOTSTRAP_RESULT' or fields['request_id'] != original_discover.fields['request_id']:
        raise StorageProtocolError('bootstrap result is not the original discovery reply')
    if (not int(original_discover.fields['issued_j_us']) <= int(fields['issued_j_us']) <= now_us
            or int(fields['expires_j_us']) != int(original_discover.fields['expires_j_us'])):
        raise StorageProtocolError('bootstrap reply renewed or changed the original lifetime')
    body = _bootstrap_body(fields['payload']['body'], profile)
    if body['discovery_nonce'] != original['discovery_nonce']:
        raise StorageProtocolError('bootstrap nonce differs from original discovery')
    return body


def storage_binding(peer, profile, bootstrap_body):
    if type(peer) is not LifecyclePeer or peer.role != 'D':
        raise StorageProtocolError('storage binding requires the registered D')
    body = _bootstrap_body(bootstrap_body, profile)
    if body['status'] != 'EMPTY':
        raise StorageProtocolError('storage binding requires an admitted actual bootstrap')
    return dict(service_instance=peer.service_instance, storage_worker=peer.worker_instance,
                storage_profile_sha256=profile.sha256, database_uuid=body['database_uuid'], db_schema_version=body['schema_version'])


def binding_payload(binding, revision=1):
    if type(revision) is not int or revision != 1:
        raise StorageProtocolError('normal storage profile supports binding revision one')
    _keys(binding, 'service_instance storage_worker storage_profile_sha256 database_uuid db_schema_version')
    for field in ('service_instance', 'storage_worker', 'database_uuid'):
        _uuid(binding[field])
    reference = binding['storage_profile_sha256']
    if (type(reference) is not str or len(reference) != 64 or any(c not in '0123456789abcdef' for c in reference)
            or type(binding['db_schema_version']) is not int or binding['db_schema_version'] != 1):
        raise StorageProtocolError('unsupported storage binding references/schema')
    return dict(binding_revision=str(revision), binding_sha256=hashlib.sha256(_canonical(binding)).hexdigest(), binding=dict(binding))


def parse_storage_bind_ack(packet, peer, original_bind, *, now_us):
    original = _packet(original_bind, peer, 'BIND', 1, child_sender=False)
    _keys(original['payload'], 'binding_revision binding_sha256 binding')
    if (original['payload'] != binding_payload(original['payload']['binding'])
            or original['payload']['binding']['service_instance'] != peer.service_instance
            or original['payload']['binding']['storage_worker'] != peer.worker_instance):
        raise StorageProtocolError('original storage binding is not the registered canonical body')
    fields = _packet(packet, peer, 'BIND_ACK', 1, child_sender=True)
    payload = fields['payload']
    _keys(payload, 'binding_revision binding_sha256 accepted state reason')
    if (fields['request_id'] != original['request_id'] or payload['binding_revision'] != '1'
            or payload['binding_sha256'] != original['payload']['binding_sha256']
            or type(payload['accepted']) is not bool or not payload['accepted']
            or payload['state'] != 'IDLE' or payload['reason'] is not None
            or not int(original['issued_j_us']) <= int(fields['issued_j_us']) <= now_us < int(fields['expires_j_us']) <= int(original['expires_j_us'])):
        raise StorageProtocolError('storage ACK differs from its original binding or lifetime')
    return payload


class _Database:
    """D 내부의 실제 writer. 기존 파일은 검증하기 전 schema/pragma로 고치지 않는다."""

    def __init__(self, profile, expires_us):
        import fcntl
        import sqlite3

        self.profile, self.expires_us = profile, expires_us
        self.connection = self.lock_fd = None
        self.sqlite_version = sqlite3.sqlite_version
        self.identity = None
        self.created = False
        self.fcntl = fcntl
        try:
            parent = Path(profile.database_path).parent
            parent_stat = parent.lstat()
            if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != os.geteuid() or parent_stat.st_mode & 0o022:
                raise StorageIncompatible('DB directory must be owned and protected')
            lock_path = profile.database_path + '.owner.lock'
            self.lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            lock = os.fstat(self.lock_fd)
            self._protected_file(lock)
            if (lock.st_dev, lock.st_ino) != self._identity(os.stat(lock_path, follow_symlinks=False)):
                raise StorageUnknown('DB owner lock changed identity')
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                current = os.stat(profile.database_path, follow_symlinks=False)
            except FileNotFoundError as error:
                if error.errno != errno.ENOENT or profile.database_open_policy != 'CREATE_IF_ABSENT':
                    raise
                descriptor = os.open(profile.database_path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                try:
                    current = os.fstat(descriptor)
                finally:
                    os.close(descriptor)
                self.created = True
            self._protected_file(current)
            self.identity = self._identity(current)
            self._deadline()
            self.connection = sqlite3.connect(Path(profile.database_path).as_uri() + '?mode=rw', uri=True,
                                             timeout=0, isolation_level=None)
            self.connection.set_progress_handler(lambda: int(j_us() >= self.expires_us), 1000)
            if self.created:
                self._create()
                directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            self.metadata = self._validate()
            self.connection.execute('PRAGMA synchronous=FULL')
            if self.connection.execute('PRAGMA synchronous').fetchone() != (2,):
                raise StorageUnknown('SQLite FULL synchronous setting was not retained')
            self._deadline()
        except BaseException as error:
            try:
                self.close()
            except BaseException as close_error:
                raise StorageUnknown('failed DB setup also left cleanup unconfirmed') from close_error
            if isinstance(error, sqlite3.DatabaseError):
                code = getattr(error, 'sqlite_errorcode', None)
                code = code & 0xff if type(code) is int else 0
                error_type = StorageIncompatible if code in (1, 11, 17, 26) else StorageUnknown
                mapped = error_type('SQLite rejected the existing format or discovery operation: ' + str(error)[:256])
                mapped.cleanup_confirmed = True
                raise mapped from error
            error.cleanup_confirmed = True
            raise

    @staticmethod
    def _identity(value):
        return value.st_dev, value.st_ino

    @staticmethod
    def _protected_file(value):
        if (not stat.S_ISREG(value.st_mode) or value.st_uid != os.geteuid()
                or stat.S_IMODE(value.st_mode) != 0o600 or value.st_nlink != 1):
            raise StorageIncompatible('DB/owner lock must be an owned protected regular file')

    def _deadline(self):
        if j_us() >= self.expires_us:
            raise StorageUnknown('original DB discovery deadline expired')
        current = os.stat(self.profile.database_path, follow_symlinks=False)
        self._protected_file(current)
        if self.identity != self._identity(current):
            raise StorageUnknown('DB pathname changed identity')

    def _create(self):
        connection = self.connection
        if connection.execute('PRAGMA journal_mode=DELETE').fetchone() != ('delete',):
            raise StorageUnknown('rollback journal mode was not selected')
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('BEGIN IMMEDIATE')
        try:
            connection.execute('CREATE TABLE storage_metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), format_tag TEXT NOT NULL, database_uuid TEXT NOT NULL, schema_version INTEGER NOT NULL, allocator_highwater TEXT NOT NULL)')
            connection.execute('CREATE TABLE checkpoints (checkpoint_job_id TEXT PRIMARY KEY, complete INTEGER NOT NULL CHECK(complete IN (0,1)), metadata_json TEXT NOT NULL)')
            connection.execute('INSERT INTO storage_metadata VALUES (1,?,?,1,?)', (_FORMAT, str(uuid.uuid4()), '1'))
            connection.execute('PRAGMA application_id=' + str(_APPLICATION_ID))
            connection.execute('PRAGMA user_version=1')
            connection.execute('COMMIT')
        except BaseException:
            if connection.in_transaction:
                connection.execute('ROLLBACK')
            raise

    def _validate(self):
        connection = self.connection
        if (connection.execute('PRAGMA application_id').fetchone() != (_APPLICATION_ID,)
                or connection.execute('PRAGMA user_version').fetchone() != (1,)
                or connection.execute('PRAGMA journal_mode').fetchone() != ('delete',)):
            raise StorageIncompatible('unsupported SQLite format/schema/journal mode')
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != {'storage_metadata', 'checkpoints'}:
            raise StorageIncompatible('unsupported local schema tables')
        metadata = list(connection.execute('SELECT singleton,format_tag,database_uuid,schema_version,allocator_highwater FROM storage_metadata'))
        if len(metadata) != 1 or metadata[0][:2] != (1, _FORMAT) or metadata[0][3] != 1:
            raise StorageIncompatible('invalid singleton DB metadata')
        try:
            _uuid(metadata[0][2])
            _highwater(metadata[0][4])
        except (ValueError, TypeError) as error:
            raise StorageIncompatible('invalid DB UUID or durable highwater') from error
        if connection.execute('SELECT COUNT(*) FROM checkpoints').fetchone() != (0,):
            raise StorageIncompatible('checkpoint restore is not implemented; existing records are preserved')
        if connection.execute('PRAGMA quick_check(1)').fetchone() != ('ok',):
            raise StorageIncompatible('SQLite integrity check failed')
        return dict(database_uuid=metadata[0][2], schema_version=1, allocator_highwater=metadata[0][4])

    def close(self):
        errors = []
        connection, self.connection = self.connection, None
        if connection is not None:
            try:
                connection.close()
            except BaseException as error:
                errors.append(error)
        descriptor, self.lock_fd = self.lock_fd, None
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise StorageUnknown('DB connection or writer lock close was not confirmed') from errors[0]


def _diagnostic(boot, value):
    if boot.diagnostic_directory is None:
        return
    directory = Path(boot.diagnostic_directory)
    directory.mkdir(mode=0o700, exist_ok=True)
    target = directory / ('storage-' + boot.peer.worker_instance + '.json')
    with target.open('xb') as output:
        output.write(_canonical(value) + b'\n')


def storage_main(endpoint, boot):
    """spawn된 D entry. S는 actual DB_JOB 결과와 process sentinel을 별도로 판단한다."""
    if type(boot) is not StorageBoot:
        raise ValueError('expected immutable storage boot configuration')
    link = ResidentLink(endpoint, boot.peer, 'D', track_sent=True)
    receiver = LifecycleReceiver(boot.peer, 'D')
    database = None
    state, stop, failure = 'IDLE', None, None
    original_discover = original_bind = cached_body = None
    pending = []
    progress_sequence, progress_us = 0, None
    last_heartbeat = 0
    report = dict(worker_instance=boot.peer.worker_instance, service_instance=boot.peer.service_instance,
                  profile_sha256=boot.storage_profile.sha256, sqlite_version=None, database_created=False,
                  database_closed=True, owner_lock_closed=True, error=None, discoveries=0, bound=False,
                  stopped_report_sent=False, copy_leases_acquired=0)

    def send(kind, payload, **kwargs):
        if kwargs.get('request_id') is None:
            kwargs['request_id'] = str(uuid.uuid4())
        item = dict(kind=kind, payload=payload, **kwargs)
        packet = link.send(**item)
        if packet is None and kind != 'HEARTBEAT':
            for held in pending:
                if held['kind'] == kind and held['request_id'] == item['request_id']:
                    if held != item:
                        raise StorageProtocolError('pending reply changed original bytes or lifetime')
                    return None
            if len(pending) >= 2:
                raise StorageProtocolError('D reply staging is full')
            pending.append(item)
        return packet

    try:
        from .resident_profile import release_sha256

        actual_release = release_sha256()
        if actual_release != boot.peer.release_sha256 or j_us() >= boot.startup_deadline_us:
            raise StorageProtocolError('actual D release differs or local startup expired')
        report['release_sha256'] = actual_release
        issued = j_us()
        hello = send('HELLO', dict(role='D', worker_instance=boot.peer.worker_instance,
                     release_sha256=actual_release, capability_mask='0'),
                     issued_us=issued, expires_us=min(boot.startup_deadline_us, issued + 15_000_000))
        if hello is not None:
            parse_lifecycle(hello, boot.peer, 'S')
        while stop is None:
            link.flush()
            link.take_sent()
            while pending:
                if pending[0]['kind'] != 'STOPPED' and j_us() >= pending[0]['expires_us']:
                    raise StorageProtocolError('original staged storage reply expired')
                if link.send(**pending[0]) is None:
                    break
                pending.pop(0)
            for packet in link.poll():
                fields = packet.fields
                if packet.kind == 'STOP':
                    stop = receiver.receive(packet, now_us=j_us()).message
                    state = 'QUIESCING'
                    break
                if packet.kind == 'DB_JOB':
                    body = parse_discover(packet, boot.peer, boot.storage_profile, now_us=j_us())
                    if original_discover is not None:
                        original = original_discover.fields
                        if any(fields[k] != original[k] for k in ('request_id', 'issued_j_us', 'expires_j_us', 'payload')):
                            raise StorageProtocolError('a second discovery cannot replace the original pending operation')
                    else:
                        original_discover = packet
                        state = 'ACTIVE_JOB'
                        report['discoveries'] += 1
                        status = 'EMPTY'
                        try:
                            report['database_closed'] = report['owner_lock_closed'] = False
                            database = _Database(boot.storage_profile, int(fields['expires_j_us']))
                            report['sqlite_version'] = database.sqlite_version
                            report['database_created'] = database.created
                        except StorageIncompatible as error:
                            status, report['error'] = 'INCOMPATIBLE', str(error)
                            report['database_closed'] = report['owner_lock_closed'] = getattr(error, 'cleanup_confirmed', False)
                        except StorageUnknown as error:
                            status, report['error'] = 'UNKNOWN', str(error)
                            report['database_closed'] = report['owner_lock_closed'] = getattr(error, 'cleanup_confirmed', False)
                        except Exception as error:
                            status, report['error'] = 'FAILED', type(error).__name__ + ': ' + str(error)
                            report['database_closed'] = report['owner_lock_closed'] = getattr(error, 'cleanup_confirmed', False)
                        completed = j_us()
                        if completed >= int(fields['expires_j_us']):
                            status = 'UNKNOWN'
                        cached_body = dict(discovery_nonce=body['discovery_nonce'], storage_profile_sha256=boot.storage_profile.sha256,
                            status=status, database_uuid=None, schema_version=None, allocator_highwater=None,
                            allocator_ready=False, selected_checkpoint=None)
                        if status == 'EMPTY':
                            cached_body.update(database.metadata, allocator_ready=True)
                        state = 'IDLE' if status == 'EMPTY' else 'FAILED'
                        progress_sequence += 1
                        progress_us = completed
                    _bootstrap_body(cached_body, boot.storage_profile)
                    now = j_us()
                    if now < int(fields['expires_j_us']):
                        send('DB_JOB', dict(op='BOOTSTRAP_RESULT', body=dict(cached_body)), domain='STORAGE', revision=0,
                             request_id=fields['request_id'], issued_us=now, expires_us=int(fields['expires_j_us']))
                elif packet.kind == 'BIND':
                    fields = _packet(packet, boot.peer, 'BIND', 1, child_sender=False)
                    if cached_body is None or not int(fields['issued_j_us']) <= j_us() < int(fields['expires_j_us']):
                        raise StorageProtocolError('storage BIND requires current successful discovery and lifetime')
                    expected = binding_payload(storage_binding(boot.peer, boot.storage_profile, cached_body))
                    if fields['payload'] != expected or original_bind is not None:
                        raise StorageProtocolError('storage BIND differs from actual discovery or was already accepted')
                    original_bind = packet
                    report['bound'] = True
                    ack = send('BIND_ACK', dict(binding_revision='1', binding_sha256=expected['binding_sha256'],
                               accepted=True, state='IDLE', reason=None), domain='STORAGE', revision=1,
                               request_id=fields['request_id'], issued_us=j_us(), expires_us=int(fields['expires_j_us']))
                    if ack is not None:
                        parse_storage_bind_ack(ack, boot.peer, original_bind, now_us=j_us())
                else:
                    raise StorageProtocolError('unsupported D control kind')
            now = j_us()
            if stop is None and now - last_heartbeat >= 200_000:
                send('HEARTBEAT', dict(worker_state=state,
                     last_progress_sequence=None if progress_us is None else str(progress_sequence),
                     last_progress_j_us=None if progress_us is None else str(progress_us)),
                     issued_us=now, expires_us=now + 1_000_000)
                last_heartbeat = now
            time.sleep(0.005)
    except EOFError:
        report['error'] = report['error'] or 'parent EOF; new storage work revoked'
    except BaseException as error:
        failure = error
        report['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        if database is not None:
            try:
                database.close()
                report['database_closed'] = report['owner_lock_closed'] = True
            except BaseException as error:
                failure = failure or error
                report['error'] = type(error).__name__ + ': ' + str(error)
        if not report['database_closed'] or not report['owner_lock_closed']:
            failure = failure or StorageUnknown('D setup or close left a native owner unconfirmed')
        if stop is not None:
            try:
                pending.clear()
                issued = j_us()
                stopped = send('STOPPED', dict(stop_revision=stop.payload['stop_revision'], leases_released=failure is None,
                    device_closed=None, checkpoint_job=None, status='STOPPED' if failure is None else 'FAILED'),
                    request_id=stop.fields['request_id'], issued_us=issued,
                    expires_us=stop.expires_us)
                if stopped is not None:
                    parse_lifecycle(stopped, boot.peer, 'S')
                end = j_us() + 500_000
                while j_us() < end:
                    link.flush()
                    if any(sent.packet.kind == 'STOPPED' and sent.packet.fields['request_id'] == stop.fields['request_id']
                           for sent in link.take_sent()):
                        report['stopped_report_sent'] = True
                        break
                    if pending and link.send(**pending[0]) is not None:
                        pending.pop(0)
                    time.sleep(0.005)
                if not report['stopped_report_sent']:
                    failure = failure or StorageUnknown('STOPPED did not complete its bounded control send')
            except (OSError, ValueError, EOFError) as error:
                failure = failure or error
        report['transport_counts'] = dict(link.counts)
        link.close()
        _diagnostic(boot, report)
    if failure is not None:
        raise failure
