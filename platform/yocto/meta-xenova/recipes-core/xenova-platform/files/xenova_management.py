"""UUID 기반 공통 VM 관리 계약. CLI/백엔드가 같은 잠금·작업 기록·생성 함수를 사용한다.

동기 작업만 제공한다. 잠금을 얻은 재호출만 중단 기록을 조정하며, 부분 준비 디스크는
자동 재초기화/정리하지 않는다. root 권한의 외부 xl/파일 변경까지 원자적으로 통제하지 않는다.
"""
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import sys

from artifact_io import publish
from first_install_contract import uuid4
import xenova_catalog as catalog
from xenova_runtime import PlatformError, atomic_write, operation_lock, sync_directory
import xenova_vm as vm
import xenova_zephyr as zephyr

MUTATIONS = {'create', 'retire', 'restore', 'purge'}
EXIT_CODES = {'success': 0, 'validation': 2, 'conflict': 3, 'execution': 4}
CONFLICTS = {'VM_NAME_CONFLICT', 'IP_CONFLICT', 'IDEMPOTENCY_CONFLICT', 'OPERATION_IN_PROGRESS',
             'VM_NOT_STOPPED', 'TARGET_CHANGED', 'ASSET_IN_USE', 'RESTORE_CONFLICT', 'VM_PURGED'}
VALIDATION = {'INVALID_SPEC', 'VM_NOT_FOUND', 'TEMPLATE_NOT_FOUND', 'TEMPLATE_VERSION_NOT_FOUND', 'RESOURCE_UNSUPPORTED'}


class ManagementError(PlatformError):
    def __init__(self, code, message, *, retryable=False, details=None):
        self.error = {'code': code, 'message': message, 'retryable': retryable, 'details': details or {}}
        super().__init__(message)


def fail(code, message, **kwargs):
    raise ManagementError(code, message, **kwargs)


def canonical(value):
    """해시 규약 v1: UTF-8 JSON, 정렬된 key, 공백 없음, ASCII escape 없음. NaN은 거부."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def normalize_uuid(value):
    """추가 UUID 모듈 없이 표기만 정규화한다. 신원은 외부 UUID 그대로다."""
    if not isinstance(value, str):
        fail('INVALID_SPEC', 'vm_id must be a UUID')
    lowered = value.lower()
    if re.fullmatch(r'[0-9a-f]{32}', lowered):
        lowered = '-'.join((lowered[:8], lowered[8:12], lowered[12:16], lowered[16:20], lowered[20:]))
    if not vm.UUID.fullmatch(lowered) or lowered == '00000000-0000-0000-0000-000000000000':
        fail('INVALID_SPEC', 'vm_id must be a nonzero UUID')
    return lowered


def internal_name(identity):
    return 'vm-' + identity.replace('-', '')


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


@contextmanager
def tool_output():
    """하위 Python 출력과 상속된 subprocess stdout을 stderr로 보낸다."""
    saved = os.dup(1)
    try:
        sys.stdout.flush()
        os.dup2(2, 1)
        with redirect_stdout(sys.stderr):
            yield
    finally:
        sys.stderr.flush()
        os.dup2(saved, 1)
        os.close(saved)


class Manager:
    """call(action, spec, execute=False)는 envelope를 반환한다. 변경은 명시적 execute에서만 한다."""
    def __init__(self, platform=None, *, preparer=None, usage_check=None, checkpoint=None,
                 defaults_path=None):
        self.p = platform or vm.Platform()
        self.data = self.p.data
        self.defaults_path = Path(defaults_path) if defaults_path is not None else self.p.defaults_path
        self.preparer = preparer or self._prepare
        self.usage_check = usage_check or self._usage
        self.checkpoint = checkpoint or (lambda phase, operation: None)

    def _plain_dir(self, path, *, create=False):
        if create and not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o700)
            sync_directory(path.parent)
        return catalog.plain(path, directory=True)

    def _read(self, path):
        catalog.plain(path)
        value=json.loads(path.read_text())
        if not isinstance(value,dict):
            raise ValueError('persistent JSON object required')
        return value

    def _write(self, path, value):
        self._plain_dir(path.parent, create=True)
        if path.exists() or path.is_symlink():
            catalog.plain(path)
        atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False) + '\n')

    def _operations(self):
        path = self.data / 'operations'
        if not path.exists() and not path.is_symlink():
            return []
        self._plain_dir(path)
        result = []
        for entry in sorted(path.iterdir()):
            if entry.name.startswith('.') or re.fullmatch(r'[0-9a-f-]{36}\.json\.tmp-[0-9]+-[0-9a-f]{12}',entry.name):
                catalog.plain(entry)
                continue
            if not entry.name.endswith('.json') or not vm.UUID.fullmatch(entry.stem):
                fail('RECOVERY_REQUIRED', 'invalid operation record path')
            op = self._read(entry)
            if (op.get('operation_id') != entry.stem or op.get('schema_version') != '1.0'
                    or op.get('spec_hash') != digest(op.get('initial_spec'))):
                fail('RECOVERY_REQUIRED', 'operation integrity mismatch')
            if not vm.UUID.fullmatch(op.get('vm_id','')) or op.get('action') not in MUTATIONS:
                fail('RECOVERY_REQUIRED','invalid operation identity/action')
            if op['action']=='create':
                assigned = op.get('assigned', {})
                initial = op['initial_spec']
                if not isinstance(assigned, dict) or not isinstance(initial, dict):
                    fail('RECOVERY_REQUIRED', 'invalid creation identity record')
                if 'vm_id' in initial:
                    if initial['vm_id'] != op['vm_id'] or assigned.get('internal_name') != internal_name(op['vm_id']):
                        fail('RECOVERY_REQUIRED', 'creation internal identity mismatch')
                elif not isinstance(initial.get('name'), str) or not vm.ID.fullmatch(initial['name']):
                    fail('RECOVERY_REQUIRED', 'invalid legacy creation path')
                if op.get('owned_workspace')!='instances/.operation-'+op['operation_id']:
                    fail('RECOVERY_REQUIRED','invalid owned workspace')
            else:
                snap=op.get('snapshot',{})
                vm.validate_identity(snap.get('record',{}))
                if snap.get('vm_id')!=op['vm_id'] or snap.get('generation')!=op.get('expected_generation'):
                    fail('RECOVERY_REQUIRED','invalid operation lifecycle snapshot')
                source='instances/'+snap['name'] if op['action']=='retire' else str(self._archive(snap).relative_to(self.data))
                target=f"instances/.retired-{snap['name']}-{op['vm_id']}-g{snap['generation']+1}" if op['action']=='retire' else 'instances/'+snap['name']
                if op.get('source')!=source or op.get('target')!=target:
                    fail('RECOVERY_REQUIRED','operation paths do not match owned lifecycle')
            result.append(op)
        return result

    def _save_op(self, op, phase=None):
        if phase is not None:
            op['phase'] = phase
        self._write(self.data / 'operations' / (op['operation_id'] + '.json'), op)
        self.checkpoint(op['phase'], op)

    def _tombs(self):
        try:
            return vm.read_tombstones(self.data)
        except PlatformError as exc:
            fail('RECOVERY_REQUIRED', str(exc))

    def _writable(self):
        """종료 gate는 조회/완료 재전달과 실제 쓰기를 구분한다. 공통 잠금 안에서 호출한다."""
        marker = self.p.runtime / 'shutdown-requested'
        if marker.exists() or marker.is_symlink():
            fail('OPERATION_IN_PROGRESS', 'board shutdown is in progress', retryable=True)

    @staticmethod
    def _text_token(value):
        if not isinstance(value, str) or any(ord(c) < 32 for c in value):
            return False
        try:
            return 1 <= len(value.encode('utf-8')) <= 200
        except UnicodeError:
            return False

    def _validate_call(self, action, spec, execute, key, request_id):
        """외부 호출의 타입/허용 필드를 읽기 전에 검사한다. 내부 예외는 입력 오류로 덮지 않는다."""
        actions = MUTATIONS | {'templates', 'list', 'get', 'operation', 'start', 'stop',
                               'reboot', 'autostart-set', 'board-shutdown-set', 'config'}
        if not isinstance(action, str) or action not in actions:
            fail('INVALID_SPEC', 'unsupported management action')
        if type(execute) is not bool:
            fail('INVALID_SPEC', 'execute must be a boolean')
        if request_id is not None and not self._text_token(request_id):
            fail('INVALID_SPEC', 'request_id must be 1..200 UTF-8 bytes without control characters')
        if key is not None and not self._text_token(key):
            fail('INVALID_SPEC', 'invalid idempotency_key')
        if spec is None and action not in MUTATIONS:
            spec = {}
        if not isinstance(spec, dict):
            fail('INVALID_SPEC', 'request must be an object')
        if action in MUTATIONS:
            if action != 'create' and not self._text_token(key):
                fail('INVALID_SPEC', 'idempotency_key of 1..200 UTF-8 bytes required')
            return self._normalize(action, spec)
        if action == 'templates':
            allowed = set()
        elif action == 'list':
            allowed = {'lifecycle'}
        elif action == 'operation':
            allowed = {'operation_id', 'idempotency_key'}
        else:
            allowed = {'vm_id'} | ({'force'} if action in ('stop', 'reboot')
                                  else {'value'} if action.endswith('-set') else set())
        if set(spec) - allowed:
            fail('INVALID_SPEC', 'unexpected specification fields')
        if action == 'list':
            if spec.get('lifecycle', 'REGISTERED') not in ('REGISTERED', 'RETIRED', 'PURGING', 'PURGED', 'all'):
                fail('INVALID_SPEC', 'invalid lifecycle filter')
        elif action == 'operation':
            selected = [name for name in allowed if spec.get(name) is not None]
            if len(selected) != 1:
                fail('INVALID_SPEC', 'exactly one operation_id or idempotency_key selector required')
            name = selected[0]
            value = spec[name]
            if (name == 'operation_id' and (not isinstance(value, str) or not vm.UUID.fullmatch(value))
                    or name == 'idempotency_key' and not self._text_token(value)):
                fail('INVALID_SPEC', 'invalid operation selector')
        elif action != 'templates':
            spec = {**spec, 'vm_id': normalize_uuid(spec.get('vm_id'))}
            if 'force' in spec and type(spec['force']) is not bool:
                fail('INVALID_SPEC', 'force must be a boolean')
            if action == 'autostart-set' and spec.get('value') not in ('on', 'off'):
                fail('INVALID_SPEC', 'autostart-set requires on or off')
            if action == 'board-shutdown-set' and spec.get('value') not in ('graceful', 'refuse', 'force-allowed'):
                fail('INVALID_SPEC', 'invalid board shutdown policy')
        return spec

    def _state(self, identity, records):
        if not isinstance(identity, str) or not vm.UUID.fullmatch(identity):
            fail('INVALID_SPEC', 'vm_id must be the existing permanent UUID')
        active = [r for r in records.values() if r['uuid'] == identity]
        tomb = self._tombs().get(identity)
        if active:
            if tomb and tomb['lifecycle'] != 'REGISTERED':
                fail('RECOVERY_REQUIRED', 'registry and tombstone disagree')
            record = active[0]
            if tomb:
                return {**tomb, 'record':record}
            return {'schema_version':'1.0', 'vm_id':identity, 'name':record['name'],
                            'generation':0, 'lifecycle':'REGISTERED', 'record':record, 'history':[]}
        if tomb:
            if tomb['lifecycle']=='REGISTERED':
                fail('RECOVERY_REQUIRED','registered VM directory is absent; inspect operation journal')
            return tomb
        # 이전 delete archive는 출처/UUID를 바꾸지 않고 명시적 restore/retire에서 수용한다.
        for path in (self.data / 'instances').glob('.deleted-*'):
            self._plain_dir(path)
            record = self._read(path / 'instance.json')
            vm.validate_identity(record)
            if path.name != '.deleted-' + record['id'] + '-' + record['uuid']:
                fail('RECOVERY_REQUIRED', 'invalid legacy archive identity')
            if record['uuid'] == identity:
                return {'schema_version':'1.0','vm_id':identity,'name':record['name'],'generation':0,
                        'lifecycle':'RETIRED','record':record,'archive':str(path.relative_to(self.data)),
                        'history':[], 'legacy_archive':True}
        fail('VM_NOT_FOUND', 'VM UUID not found')

    def _legacy_records(self):
        result=[]
        for path in (self.data/'instances').glob('.deleted-*'):
            self._plain_dir(path)
            record=self._read(path/'instance.json')
            vm.validate_identity(record)
            if path.name!='.deleted-'+record['id']+'-'+record['uuid']:
                fail('RECOVERY_REQUIRED','invalid legacy archive identity')
            result.append(record)
        return result

    def _archive(self, tomb):
        rel = Path(tomb.get('archive', ''))
        expected = f".retired-{tomb['name']}-{tomb['vm_id']}-g{tomb['generation']}"
        legacy = '.deleted-' + tomb['name'] + '-' + tomb['vm_id']
        if len(rel.parts) != 2 or rel.parts[0] != 'instances' or rel.name not in (expected, legacy):
            fail('RECOVERY_REQUIRED', 'archive path/identity mismatch')
        return self.data / rel

    def _metadata_at(self, record, directory):
        self._plain_dir(directory)
        on_disk=self._read(directory/'instance.json')
        if on_disk!=record:
            fail('TARGET_CHANGED','persistent VM configuration changed')
        return on_disk

    def _record_at(self, record, directory):
        on_disk=self._metadata_at(record,directory)
        vm.validate_record(record, directory, allow_archive=True)
        if vm.guest_kind(record) == 'linux':
            disk = directory / 'root.raw'
            catalog.plain(disk)
            with disk.open('rb') as stream:
                stream.seek(1080)
                if stream.read(2) != b'\x53\xef':
                    fail('ARTIFACT_INVALID', 'writable root disk lacks ext4 superblock')
        return on_disk

    def _stopped(self, record):
        try:
            domains = self.p.domains()
            if self.p.matching(record, domains):
                fail('VM_NOT_STOPPED', 'VM must be stopped; no shutdown is performed')
            return domains
        except ManagementError:
            raise
        except PlatformError:
            fail('TARGET_CHANGED', 'cannot verify current Xen identity', retryable=True)

    def _ownership(self, directory):
        """실제 inode/device 목록. mount/link/special file은 삭제 전에 거부한다."""
        self._plain_dir(directory)
        dev = directory.stat().st_dev
        result = {}
        for base, dirs, files in os.walk(directory, followlinks=False):
            for name in dirs + files:
                path = Path(base) / name
                st = path.lstat()
                if (path.resolve() != path or st.st_dev != dev or path.is_symlink()
                        or not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode))
                        or (stat.S_ISREG(st.st_mode) and st.st_nlink != 1)):
                    fail('ASSET_IN_USE', 'linked/shared/mounted/special asset refused')
                result[str(path.relative_to(directory))] = {'dev':st.st_dev,'ino':st.st_ino,
                    'directory':stat.S_ISDIR(st.st_mode), 'logical_bytes':0 if path.is_dir() else st.st_size,
                    'allocated_bytes':0 if path.is_dir() else st.st_blocks * 512}
        return result

    def _usage(self, directory, domains):
        """Xen 디스크, 모든 mountpoint, loop backing file와 프로세스 fd를 확인한다."""
        root = str(directory) + '/'
        inodes = {(v['dev'],v['ino']) for v in self._ownership(directory).values() if not v['directory']}
        def owns(path):
            target = str(Path(path).resolve())
            return target == str(directory) or target.startswith(root)
        for domain in domains:
            for disk in domain.get('config', {}).get('disks', []):
                path = disk.get('pdev_path')
                if path and (owns(path) or (Path(path).exists() and (Path(path).stat().st_dev, Path(path).stat().st_ino) in inodes)):
                    fail('ASSET_IN_USE', 'VM asset attached to a Xen domain')
        mounts = Path('/proc/self/mountinfo').read_text().splitlines()
        for row in mounts:
            fields = row.split(' - ', 1)
            if len(fields) != 2 or len(fields[0].split()) < 6:
                fail('RECOVERY_REQUIRED', 'cannot parse mount inventory')
            target = fields[0].split()[4]
            for encoded, decoded in [(r'\040',' '),(r'\011','\t'),(r'\012','\n'),(r'\134','\\')]:
                target = target.replace(encoded, decoded)
            if owns(target):
                fail('ASSET_IN_USE', 'nested mount in VM assets')
        loops = json.loads(self.p.command(['losetup','--json','--list','--output','NAME,BACK-FILE']))
        if not isinstance(loops.get('loopdevices'), list):
            fail('RECOVERY_REQUIRED', 'cannot verify loop ownership')
        for row in loops['loopdevices']:
            backing = row.get('back-file')
            if not isinstance(backing, str):
                fail('RECOVERY_REQUIRED', 'unverifiable loop backing file')
            if owns(backing.removesuffix(' (deleted)')):
                fail('ASSET_IN_USE', 'VM asset is a loop backing file')
        for process in Path('/proc').iterdir():
            if not process.name.isdigit():
                continue
            try:
                for descriptor in (process / 'fd').iterdir():
                    try:
                        st = descriptor.stat()
                        if (st.st_dev,st.st_ino) in inodes:
                            fail('ASSET_IN_USE', 'VM asset is held open by a process')
                    except FileNotFoundError:
                        pass
            except FileNotFoundError:
                pass

    def _normalize(self, action, spec):
        if not isinstance(spec, dict):
            fail('INVALID_SPEC', 'request must be an object')
        if action == 'create':
            required = {'vm_id','name','template_id','template_version','vcpus','memory_mib'}
            if set(spec) - (required | {'ipv4'}) or not required <= set(spec):
                fail('INVALID_SPEC', 'vm_id, name, exact template ID/version and resources required')
            name = spec['name']
            if (not isinstance(name, str) or not name.strip() or len(name) > 63
                    or any(ord(c) < 32 or 0x7f <= ord(c) <= 0x9f or 0xd800 <= ord(c) <= 0xdfff for c in name)):
                fail('INVALID_SPEC', 'display name must be 1..63 characters without controls')
            try:
                catalog.identity(spec['template_id'], spec['template_version'])
            except PlatformError:
                fail('INVALID_SPEC', 'invalid template ID or explicit version')
            if any(type(spec[k]) is not int or spec[k] <= 0 for k in ('vcpus','memory_mib')):
                fail('INVALID_SPEC', 'vcpus and memory_mib must be positive integers')
            return {**{k:spec[k] for k in sorted(spec)}, 'vm_id':normalize_uuid(spec['vm_id'])}
        required = {'vm_id', 'confirm_vm_id'} if action == 'purge' else {'vm_id'}
        if set(spec) != required:
            fail('INVALID_SPEC', 'UUID target required')
        result = {**spec, 'vm_id':normalize_uuid(spec.get('vm_id'))}
        if action == 'purge' and normalize_uuid(spec['confirm_vm_id']) != result['vm_id']:
            fail('INVALID_SPEC', 'purge requires matching confirm_vm_id')
        if action == 'purge':
            result['confirm_vm_id'] = result['vm_id']
        return result

    def _reserve_space(self, additional_bytes, records, *, restored=None):
        """기존 정책으로 복원 후 등록 집합과 미완료 생성의 남은 약정량을 함께 검사한다."""
        future = dict(records)
        disk_paths = {}
        if restored is not None:
            record, directory = restored
            self._plain_dir(directory)
            future[record['id']] = record
            if vm.guest_kind(record) == 'linux':
                disk_paths[record['id']] = catalog.plain(directory / 'root.raw')
        active = {record['uuid'] for record in future.values()}
        for op in self._operations():
            if op['action'] != 'create' or op['status'] == 'SUCCEEDED' or op['vm_id'] in active:
                continue
            selected = catalog.describe(self.data, {key: op['template'][key] for key in (
                'schema_version', 'template_id', 'template_version', 'guest_kind',
                'asset_directory', 'manifest_sha256', 'source_template_id')})
            name = op.get('assigned', {}).get('internal_name', op['initial_spec'].get('name'))
            if not isinstance(name, str) or not vm.ID.fullmatch(name):
                fail('RECOVERY_REQUIRED', 'invalid pending creation name')
            workspace = self.data / op['owned_workspace']
            stage = workspace / name
            for directory in (workspace, stage):
                if directory.exists() or directory.is_symlink():
                    self._plain_dir(directory)
            if selected['guest_kind'] == 'zephyr':
                stage = stage / 'firmware' / selected['manifest_sha256']
            for source in Path(selected['directory']).iterdir():
                catalog.plain(source)
                copied = stage / source.name
                allocated = 0
                if copied.exists() or copied.is_symlink():
                    allocated = catalog.plain(copied).stat().st_blocks * 512
                # 다른 임시 파일의 블록으로 미복사 payload의 약정 공간을 상쇄하지 않는다.
                additional_bytes += max(0, source.stat().st_size - allocated)
        try:
            return self.p.reserve_space(additional_bytes, future, disk_paths=disk_paths)
        except PlatformError:
            fail('INSUFFICIENT_STORAGE', 'insufficient capacity including registered and pending VM growth reserve')

    def _selection(self, spec):
        all_entries = catalog.entries(self.data)
        matches = [e for e in all_entries if e['template_id'] == spec['template_id']]
        if not matches:
            fail('TEMPLATE_NOT_FOUND', 'normalized template ID not published')
        selected = next((e for e in matches if e['template_version'] == spec['template_version']), None)
        if selected is None:
            fail('TEMPLATE_VERSION_NOT_FOUND', 'exact template version not published')
        for k in ('vcpus','memory_mib'):
            lo,hi = selected['resource_limits'][k]
            if not lo <= spec[k] <= hi:
                fail('RESOURCE_UNSUPPORTED', 'requested resources exceed template/platform limits')
        if selected['guest_kind'] == 'zephyr':
            if 'ipv4' in spec:
                fail('INVALID_SPEC', 'Zephyr has no IP field')
        else:
            ip = spec.get('ipv4')
            if 'ipv4' in spec and not self._valid_ip(ip):
                fail('INVALID_SPEC', 'explicit Linux IP must be 192.168.77.2..99')
        descriptor = {k:selected[k] for k in ('schema_version','template_id','template_version','guest_kind',
                                             'asset_directory','manifest_sha256','source_template_id')}
        return catalog.describe(self.data, descriptor, full=True)

    @staticmethod
    def _valid_ip(value):
        return isinstance(value, str) and re.fullmatch(r'192\.168\.77\.(?:[2-9]|[1-9][0-9])', value) is not None

    def _factory_owned_ip(self, record, defaults):
        """예시 MAC 대신 완료된 factory 신원 계획과 실제 등록 신원으로 자체 예약을 확인한다."""
        if record is None or vm.guest_kind(record) != 'linux':
            return set()
        candidates = [row for row in defaults if row.get('id') == record['id']
                      and row.get('name') == record['name'] and row['ipv4'] == record['ipv4']]
        if not candidates:
            return set()
        state_path = self.data / '.first-install-state.json'
        factory_path = self.data / '.xenova-install.json'
        if any(not path.exists() and not path.is_symlink() for path in (state_path, factory_path)):
            return set()
        state, factory = self._read(state_path), self._read(factory_path)
        marker = self._read(self.data / '.xenova-storage.json')
        if (state.get('schema') != 1 or state.get('phase') != 'complete'
                or factory.get('schema') != 1 or not isinstance(state.get('factory_id'), str) or not state['factory_id']
                or state['factory_id'] != factory.get('factory_id')
                or marker.get('schema') != 1 or not isinstance(state.get('storage_uuid'), str)
                or not vm.UUID.fullmatch(state['storage_uuid']) or state['storage_uuid'] != marker.get('uuid')
                or not isinstance(state.get('identities'), dict)
                or not isinstance(state.get('registered'), list)
                or not isinstance(factory.get('instances'), list)):
            fail('RECOVERY_REQUIRED', 'cannot verify factory reservation ownership')
        planned = state['identities'].get(record['id'])
        if (not isinstance(planned, dict) or record['id'] not in state['registered']
                or any(planned.get(key) != record[key] for key in ('uuid', 'mac'))):
            return set()
        sources = [item.get('record') for item in factory['instances'] if isinstance(item, dict)
                   and isinstance(item.get('record'), dict) and item['record'].get('id') == record['id']]
        if len(sources) != 1 or any(sources[0].get(key) != record[key] for key in ('id', 'name', 'ipv4')):
            return set()
        return {record['ipv4']}

    def _ip_inventory(self, records, *, excluding=None):
        """현재 소유권만 예약으로 읽는다. 성공한 과거 create 이력은 주소를 점유하지 않는다."""
        settings = self._read(self.defaults_path)
        network = settings.get('network', {})
        if not isinstance(network, dict):
            fail('ARTIFACT_INVALID', 'internal network configuration must be an object')
        factory = settings.get('instances')
        if (not isinstance(factory, list) or any(not isinstance(row, dict)
                or not self._valid_ip(row.get('ipv4')) or not isinstance(row.get('mac'), str)
                or not vm.MAC.fullmatch(row['mac']) for row in factory)):
            fail('ARTIFACT_INVALID', 'invalid factory IP reservations')
        pool = network.get('ipv4_pool')
        reserved = network.get('reserved_ipv4')
        if (not isinstance(pool, list) or len(pool) != 2 or not all(self._valid_ip(ip) for ip in pool)
                or int(pool[0].split('.')[-1]) > int(pool[1].split('.')[-1])
                or not isinstance(reserved, list) or not all(self._valid_ip(ip) for ip in reserved)):
            fail('ARTIFACT_INVALID', 'invalid configured internal IP pool/reservations')
        held = set(reserved)
        own_records = list(records.values()) + [t['record'] for t in self._tombs().values()]
        own_record = next((r for r in own_records if r['uuid'] == excluding), None)
        own_mac = own_record.get('mac') if own_record else None
        if own_mac is None:
            own_mac = next((o.get('assigned', {}).get('mac') for o in self._operations()
                            if o['action'] == 'create' and o['vm_id'] == excluding), None)
        held.update(r['ipv4'] for r in settings['instances'] if r.get('guest_kind', 'linux') == 'linux')
        if excluding:
            held -= self._factory_owned_ip(own_record, factory)
        owned = list(records.values()) + self._legacy_records()
        owned += [t['record'] for t in self._tombs().values() if t['lifecycle'] != 'PURGED']
        held.update(r['ipv4'] for r in owned if r['uuid'] != excluding and vm.guest_kind(r) == 'linux')
        for op in self._operations():
            if op['action'] == 'create' and op['status'] != 'SUCCEEDED' and op['vm_id'] != excluding:
                ip = op.get('assigned', {}).get('ipv4', op['initial_spec'].get('ipv4'))
                if ip is not None:
                    if not self._valid_ip(ip):
                        fail('RECOVERY_REQUIRED', 'invalid pending IP reservation')
                    held.add(ip)
        # dnsmasq의 고정 예약/현재 lease는 플랫폼 저장소와 별도의 외부 관측이다.
        for name, field in [('dhcp-hosts', 1), ('dnsmasq.leases', 2)]:
            path = self.p.runtime / name
            if not path.exists() and not path.is_symlink():
                continue
            catalog.plain(path)
            for row in path.read_text().splitlines():
                if not row.strip() or row.lstrip().startswith('#'):
                    continue
                fields = row.split(',') if name == 'dhcp-hosts' else row.split()
                if len(fields) < (4 if name == 'dhcp-hosts' else 5):
                    fail('RECOVERY_REQUIRED', 'cannot parse DHCP address inventory')
                ip = fields[field]
                if not re.fullmatch(r'192\.168\.77\.[0-9]{1,3}', ip) or not 0 <= int(ip.split('.')[-1]) <= 255:
                    fail('RECOVERY_REQUIRED', 'invalid DHCP address inventory')
                mac = fields[0] if name == 'dhcp-hosts' else fields[1]
                if not own_mac or mac.lower() != own_mac:
                    held.add(ip)
        return [f'192.168.77.{i}' for i in range(int(pool[0].split('.')[-1]), int(pool[1].split('.')[-1])+1)], held

    def _restore_ip(self, record, records):
        if vm.guest_kind(record) == 'linux':
            _, held = self._ip_inventory(records, excluding=record['uuid'])
            if record['ipv4'] in held:
                fail('RESTORE_CONFLICT', 'retained IP conflicts with another current reservation/lease')

    def _apply_dhcp(self, op):
        if vm.guest_kind(op['snapshot']['record']) == 'linux':
            self._save_op(op, 'dhcp-pending')
            with tool_output():
                self.p.prepare_dhcp(self.p.records(validate_assets=False))
            self._save_op(op, 'dhcp-applied')

    def _allocate_ip(self, spec, records):
        pool, held = self._ip_inventory(records)
        if 'ipv4' in spec:
            if spec['ipv4'] in held:
                fail('IP_CONFLICT', 'IP is owned or reserved')
            return spec['ipv4']
        chosen = next((ip for ip in pool if ip not in held), None)
        if chosen is None:
            fail('IP_POOL_EXHAUSTED', 'no unreserved internal Linux address remains')
        return chosen

    def _conflicts(self, spec, records):
        identity = spec['vm_id']
        tomb = self._tombs().get(identity)
        if tomb and tomb['lifecycle'] != 'REGISTERED':
            fail('VM_PURGED' if tomb['lifecycle'] == 'PURGED' else 'TARGET_CHANGED',
                 'VM UUID is retired/purged; restore or use a new UUID')
        if (any(r['uuid'] == identity for r in records.values()) or tomb
                or any(r['uuid'] == identity for r in self._legacy_records())
                or any(o['vm_id'] == identity for o in self._operations())):
            fail('IDEMPOTENCY_CONFLICT', 'UUID is already reserved by another origin/operation')
        key = internal_name(identity)
        if key in records or vm.retired_instance(self.data, key):
            fail('VM_NAME_CONFLICT', 'internal execution name is already owned')

    def _prepare(self, op):
        spec, selected = op['initial_spec'], op['template']
        descriptor={k:selected[k] for k in ('schema_version','template_id','template_version',
                    'guest_kind','asset_directory','manifest_sha256','source_template_id')}
        selected=catalog.describe(self.data,descriptor,full=True)
        name = op.get('assigned', {}).get('internal_name', spec['name'])
        metadata = {'display_name':spec['name'], 'creation_spec':spec, 'creation_operation_id':op['operation_id'],
                    'template_id':spec['template_id'], 'template_version':spec['template_version'],
                    'template_manifest_sha256':selected['manifest_sha256']}
        records = self.p.records(validate_assets=False)
        workspace = self.data / op['owned_workspace']
        if selected['guest_kind'] == 'zephyr':
            zephyr.new_instance(self.data, name, Path(selected['directory']), records,
                                identity=op['vm_id'], workspace_path=workspace, metadata=metadata,
                                autostart=spec['autostart'], board_shutdown_policy=spec['board_shutdown_policy'])
        else:
            from prepare_instance import make_instance
            boot = json.loads((Path(selected['directory']) / 'boot.json').read_text())
            record = {'schema':1,'guest_kind':'linux','id':name,'name':name,'hostname':name,
                      'uuid':op['vm_id'],'mac':op['assigned']['mac'],'ipv4':op['assigned'].get('ipv4', spec.get('ipv4')),
                      'vcpus':spec['vcpus'],'memory_mib':spec['memory_mib'],'autostart':spec['autostart'],'board_shutdown_policy':spec['board_shutdown_policy'],
                      'template':selected['source_template_id'],'kernel_release':boot['kernel_release'],**metadata}
            make_instance(Namespace(data_root=self.data,id=name,spec=None,template_root=None,deployment_seed=False),
                          vm=vm, defaults_path=self.defaults_path, record_override=record,
                          identity=op['vm_id'], workspace_path=workspace, template_directory=Path(selected['directory']))

    def _view(self, tomb, *, observe=False):
        record = tomb['record']
        result = {'vm_id':tomb['vm_id'],'name':record.get('display_name', tomb['name']),
                  'template_id':record.get('template_id'), 'template_version':record.get('template_version'),
                  'guest_kind':vm.guest_kind(record), 'vcpus':record['vcpus'], 'memory_mib':record['memory_mib'],
                  'ipv4':record.get('ipv4'), 'lifecycle':tomb['lifecycle'],
                  'generation':tomb['generation'],'registered':tomb['lifecycle']=='REGISTERED',
                  'initial_spec':record.get('creation_spec'), 'configuration':record,
                  'xen_state':None,'domid':None,'app_readiness':'unknown'}
        if observe:
            rows = self.p.matching(record, self.p.domains())
            result.update(xen_state=rows[0]['xen_state'] if rows else 'stopped',
                          domid=rows[0]['domid'] if rows else None)
        return result

    def _finish(self, op, tomb):
        result = self._view(tomb)
        op.update(status='SUCCEEDED',result=result,error=None)
        self._save_op(op, 'complete')
        return result

    def _save_tomb(self, tomb, op, lifecycle, generation, **extra):
        updated = {**tomb, **extra, 'lifecycle':lifecycle,'generation':generation,'last_operation_id':op['operation_id'],
                   'history':tomb['history'] + [{'operation_id':op['operation_id'],'action':op['action'],
                                               'generation':generation,'lifecycle':lifecycle}]}
        self._write(self.data / 'tombstones' / (tomb['vm_id']+'.json'), updated)
        return updated

    def _create(self, op):
        if op['initial_spec']['guest_kind'] == 'linux':
            records = self.p.records(validate_assets=False)
            _, held = self._ip_inventory(records, excluding=op['vm_id'])
            assigned_ip = op['assigned'].get('ipv4', op['initial_spec'].get('ipv4'))
            if not self._valid_ip(assigned_ip):
                fail('RECOVERY_REQUIRED', 'invalid recorded IP; reservation is not regenerated')
            if assigned_ip in held:
                fail('IP_CONFLICT', 'recorded IP conflicts with another current reservation/lease')
        target = self.data / 'instances' / op.get('assigned', {}).get('internal_name', op['initial_spec']['name'])
        if not target.exists() and not target.is_symlink():
            workspace = self.data / op['owned_workspace']
            if workspace.exists() or workspace.is_symlink():
                fail('RECOVERY_REQUIRED', 'partial preparation retained; no automatic disk reset',
                     details={'operation_id':op['operation_id'],'owned_workspace':op['owned_workspace']})
            self._save_op(op, 'preparing')
            with tool_output():
                self.preparer(op)
        self._save_op(op, 'published')
        record = self._read(target / 'instance.json')
        if (record['uuid'] != op['vm_id'] or record.get('creation_operation_id') != op['operation_id']
                or record.get('creation_spec') != op['initial_spec']
                or record.get('template_manifest_sha256') != op['template']['manifest_sha256']):
            fail('RECOVERY_REQUIRED', 'published instance does not match recorded creation identity')
        vm.validate_record(record, target)
        tomb = {'schema_version':'1.0','vm_id':record['uuid'],'name':record['name'],'generation':0,
                'lifecycle':'REGISTERED','record':record,'history':[]}
        tomb = self._save_tomb(tomb, op, 'REGISTERED', 0)
        if vm.guest_kind(record) == 'linux':
            self._save_op(op, 'dhcp-pending')
            with tool_output():
                self.p.prepare_dhcp(self.p.records(validate_assets=False))
            self._save_op(op, 'dhcp-applied')
        return self._finish(op, tomb)

    def _transition(self, op, records):
        tomb = self._tombs().get(op['vm_id'], op['snapshot'])
        expected = op['expected_generation']
        action = op['action']
        desired = 'RETIRED' if action=='retire' else 'REGISTERED' if action=='restore' else 'PURGED'
        if tomb.get('last_operation_id') == op['operation_id']:
            if tomb['lifecycle'] == desired:
                if action == 'restore':
                    directory = self.data / op['target']
                    self._record_at(tomb['record'], directory)
                    self._restore_ip(tomb['record'], records)
                    self._reserve_space(0, records, restored=(tomb['record'], directory))
                if action in ('retire', 'restore'):
                    self._apply_dhcp(op)
                return self._finish(op, tomb)
        if tomb['generation'] != expected:
            fail('TARGET_CHANGED', 'lifecycle generation changed; stale operation cannot mutate assets')
        tomb = {**tomb, 'record':op['snapshot']['record']}
        record = tomb['record']
        if action == 'restore':
            target_record = records.get(record['name'])
            if target_record and target_record != record:
                fail('RESTORE_CONFLICT', 'restore name is already registered')
        self._stopped(record)
        source = self.data / op['source']
        target = self.data / op['target'] if action!='purge' else None
        if action == 'purge':
            return self._purge(op, tomb)
        if action == 'restore':
            directory = source if source.exists() or source.is_symlink() else target
            self._record_at(record, directory)
            self._restore_ip(record, records)
            self._reserve_space(0, records, restored=(record, directory))
        if source.exists() or source.is_symlink():
            if target.exists() or target.is_symlink():
                fail('RECOVERY_REQUIRED', 'both lifecycle source and destination exist')
            self._record_at(record, source)
            self.usage_check(source, self._stopped(record))
            if action == 'restore':
                for other in records.values():
                    if any(other.get(k)==record.get(k) for k in vm.identity_fields(record)):
                        fail('RESTORE_CONFLICT', 'identity/name/MAC/IP conflicts with a registered VM')
            if action=='restore' and vm.guest_kind(record)=='linux':
                self.p.command(['e2fsck','-fn',str(source/'root.raw')],timeout=120)
            self._save_op(op, 'moving')
            publish(source, target, allow_hidden=action=='retire')
        self._record_at(record, target)
        self._save_op(op, 'moved')
        tomb = self._save_tomb(tomb, op, desired, expected+1,
                              archive=op['target'] if action=='retire' else None)
        self._apply_dhcp(op)
        return self._finish(op, tomb)

    def _purge(self, op, tomb):
        directory = self.data / op['source']
        self._plain_dir(directory.parent)
        if op.get('inventory') is None:
            self._metadata_at(tomb['record'], directory)
            self.usage_check(directory, self._stopped(tomb['record']))
            op['inventory'] = self._ownership(directory)
            stats = os.statvfs(self.data)
            op['space_before'] = {'free_bytes':stats.f_bavail*stats.f_frsize,
                'logical_bytes':sum(v['logical_bytes'] for v in op['inventory'].values()),
                'allocated_bytes':sum(v['allocated_bytes'] for v in op['inventory'].values())}
            op['remaining_assets'] = sorted(op['inventory'])
            self._save_op(op, 'purge-inventory')
        if tomb['lifecycle'] != 'PURGING':
            tomb = self._save_tomb(tomb, op, 'PURGING', tomb['generation'])
        if directory.exists() or directory.is_symlink():
            self._plain_dir(directory)
            current = self._ownership(directory)
            if any(k not in op['inventory'] or any(v[x]!=op['inventory'][k][x] for x in ('dev','ino','directory'))
                   for k,v in current.items()):
                fail('RECOVERY_REQUIRED', 'purge asset ownership changed')
            self.usage_check(directory, self._stopped(tomb['record']))
            op['remaining_assets'] = sorted(current)
            self._save_op(op, 'deleting')
            for relative in sorted(current, key=lambda x:(x.count('/'),x), reverse=True):
                # 부모의 각 구성요소를 O_NOFOLLOW로 열어 외부 링크를 따라 unlink하지 않는다.
                parts = Path(relative).parts
                fd = os.open(directory, os.O_DIRECTORY|os.O_NOFOLLOW)
                try:
                    for part in parts[:-1]:
                        child = os.open(part, os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=fd)
                        os.close(fd); fd=child
                    st = os.stat(parts[-1],dir_fd=fd,follow_symlinks=False)
                    expected = op['inventory'][relative]
                    if (st.st_dev,st.st_ino)!=(expected['dev'],expected['ino']) or stat.S_ISLNK(st.st_mode):
                        fail('TARGET_CHANGED', 'purge target inode changed')
                    if expected['directory']:
                        os.rmdir(parts[-1],dir_fd=fd)
                    else:
                        if st.st_nlink!=1:
                            fail('ASSET_IN_USE', 'purge target acquired another hardlink')
                        os.unlink(parts[-1],dir_fd=fd)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                op['remaining_assets'] = [name for name in op['remaining_assets'] if name!=relative]
                self._save_op(op)
                self.checkpoint('asset-deleted',op)
            directory.rmdir()
            sync_directory(directory.parent)
        op['remaining_assets'] = []
        stats = os.statvfs(self.data)
        op['space_result'] = {**op['space_before'], 'free_bytes_after':stats.f_bavail*stats.f_frsize,
                              'free_bytes_delta':stats.f_bavail*stats.f_frsize-op['space_before']['free_bytes']}
        tomb = self._save_tomb(tomb, op, 'PURGED', tomb['generation']+1, archive=None)
        result = self._finish(op,tomb)
        result['space'] = op['space_result']
        op['result']=result
        self._save_op(op)
        return result

    def _mutate(self, action, spec, execute, key, records, context):
        previous = next((o for o in self._operations() if o['idempotency_key']==key),None)
        if previous:
            context['operation_id']=previous['operation_id']
            if previous['action']!=action or previous['request']!=spec:
                fail('IDEMPOTENCY_CONFLICT','same key has a different action or request')
            context['replayed']=True
            tomb = self._tombs().get(previous['vm_id'])
            if previous['status']=='SUCCEEDED':
                if action=='create' and tomb and tomb['lifecycle']!='REGISTERED':
                    fail('VM_PURGED' if tomb['lifecycle']=='PURGED' else 'TARGET_CHANGED',
                         'the original VM is retired/purged; create cannot restore it')
                current = self._view(self._state(previous['vm_id'],records),observe=True)
                return {**(current if action == 'create' else previous['result']),
                        'operation':previous,'original_result':previous['result'], 'current':current}
            if not execute:
                if action == 'restore':
                    if tomb and tomb['generation'] == previous['expected_generation'] and tomb['lifecycle'] == 'RETIRED':
                        directory = self.data / previous['source']
                        if not directory.exists() and not directory.is_symlink():
                            directory = self.data / previous['target']
                    elif tomb and tomb.get('last_operation_id') == previous['operation_id'] and tomb['lifecycle'] == 'REGISTERED':
                        directory = self.data / previous['target']
                    else:
                        fail('TARGET_CHANGED', 'restore has a newer lifecycle')
                    self._record_at(previous['snapshot']['record'], directory)
                    self._reserve_space(0, records, restored=(previous['snapshot']['record'], directory))
                return {'plan':True,'operation':previous}
            self._writable()
            context['write_operation'] = True
            # 잠금을 획득했으므로 이전 동기 실행자는 없다. PID만으로 작업 소유권을 추정하지 않는다.
            if action=='create':
                if tomb and tomb['lifecycle']!='REGISTERED':
                    fail('TARGET_CHANGED','creation has a newer lifecycle')
                self._reserve_space(0, records)
                return self._create(previous)
            return self._transition(previous,records)
        if action=='create':
            self._conflicts(spec,records)
            selected=self._selection(spec)
            assigned_ip = self._allocate_ip(spec, records) if selected['guest_kind']=='linux' else None
            self._reserve_space(sum(p.stat().st_size for p in Path(selected['directory']).iterdir()), records)
            tomb=None
        else:
            tomb=self._state(spec['vm_id'],records)
            state=tomb['lifecycle']
            if state=='PURGED':
                fail('VM_PURGED','VM assets have been purged')
            if (action=='retire' and state!='REGISTERED') or (action in ('restore','purge') and state!='RETIRED'):
                fail('INVALID_SPEC','lifecycle does not permit this operation')
            if any(o['vm_id']==tomb['vm_id'] and o['status']!='SUCCEEDED' for o in self._operations()):
                fail('OPERATION_IN_PROGRESS','an incomplete operation owns this VM; retry its original key',retryable=True)
            directory = self.data/'instances'/tomb['name'] if action=='retire' else self._archive(tomb)
            if action=='purge':
                self._metadata_at(tomb['record'],directory)
            else:
                self._record_at(tomb['record'],directory)
            self.usage_check(directory,self._stopped(tomb['record']))
            if action=='restore':
                if vm.guest_kind(tomb['record'])=='linux':
                    self.p.command(['e2fsck','-fn',str(directory/'root.raw')],timeout=120)
                for other in records.values():
                    if any(other.get(k)==tomb['record'].get(k) for k in vm.identity_fields(tomb['record'])):
                        fail('RESTORE_CONFLICT','identity/name/MAC/IP already registered')
                self._restore_ip(tomb['record'], records)
                self._reserve_space(0, records, restored=(tomb['record'], directory))
        if not execute:
            return {'plan':True,'action':action,'spec':spec,'starts_guest':False,
                    'assets_deleted':False}
        self._writable()
        opid=uuid4();identity=spec['vm_id']
        operation_path=self.data/'operations'/(opid+'.json')
        if operation_path.exists() or operation_path.is_symlink():
            fail('TARGET_CHANGED','allocated operation ID already exists')
        if action=='create' and (identity in self._tombs() or any(o['vm_id']==identity for o in self._operations())
                                 or any(r['uuid']==identity for r in self._legacy_records())):
            fail('VM_NAME_CONFLICT','allocated UUID is already reserved')
        op={'schema_version':'1.0','operation_id':opid,'idempotency_key':key,'action':action,
            'vm_id':identity,'request':spec,'initial_spec':dict(spec),'spec_hash':digest(spec),
            'status':'RUNNING','phase':'recorded','result':None,'error':None}
        if action=='create':
            effective={**spec,'guest_kind':selected['guest_kind'],'autostart':False,
                       'board_shutdown_policy':'graceful' if selected['guest_kind']=='linux' else 'refuse'}
            op.update(initial_spec=effective,spec_hash=digest(effective))
            assigned_mac='02:'+':'.join(f'{b:02x}' for b in os.urandom(5))
            reserved_records=list(records.values())+[t['record'] for t in self._tombs().values()]+self._legacy_records()
            if any(r['uuid']==identity or r.get('mac')==assigned_mac for r in reserved_records) or any(
                    o.get('assigned',{}).get('mac')==assigned_mac for o in self._operations()):
                fail('VM_NAME_CONFLICT','allocated identity collides; no assets created')
            op.update(template=selected,assigned={'internal_name':internal_name(identity), **({'mac':assigned_mac, 'ipv4':assigned_ip} if selected['guest_kind']=='linux' else {})},
                      owned_workspace='instances/.operation-'+opid)
        else:
            op['expected_generation']=tomb['generation']
            op['source']=str((self.data/'instances'/tomb['name'] if action=='retire' else self._archive(tomb)).relative_to(self.data))
            op['target']=f"instances/.retired-{tomb['name']}-{identity}-g{tomb['generation']+1}" if action=='retire' else 'instances/'+tomb['name']
            op['snapshot']=tomb
        context['operation_id']=opid
        context['write_operation'] = True
        self._save_op(op)
        return self._create(op) if action=='create' else self._transition(op,records)

    def call(self, action, spec=None, *, execute=False, idempotency_key=None, request_id=None):
        """HTTP를 가정하지 않는다. 모든 응답은 동일 JSON envelope이고 stdout을 쓰지 않는다."""
        context = {'operation_id': None, 'replayed': False, 'write_operation': False}
        response={'schema_version':'1.0','ok':False,'request_id':request_id if self._text_token(request_id) else uuid4(),
                  'operation_id':None,'replayed':False,'data':None,'error':None}
        try:
            spec = self._validate_call(action, spec, execute, idempotency_key, request_id)
            with operation_lock(self.p.runtime):
                try:
                    records=self.p.records(validate_assets=False)
                except (PlatformError,OSError,ValueError):
                    fail('STORAGE_UNAVAILABLE','persistent storage or registry unavailable')
                if action in MUTATIONS:
                    result=self._mutate(action,spec,execute,
                                        'create:'+spec['vm_id'] if action=='create' else idempotency_key,
                                        records,context)
                elif action=='templates':
                    result={'templates':catalog.entries(self.data)}
                elif action in ('list','get'):
                    states=[self._state(r['uuid'],records) for r in records.values()]
                    if action=='get':
                        states=[self._state(spec.get('vm_id'),records)]
                    else:
                        lifecycle=spec.get('lifecycle','REGISTERED')
                        if lifecycle in ('RETIRED','all'):
                            for path in (self.data/'instances').glob('.deleted-*'):
                                rec=self._read(path/'instance.json')
                                if rec['uuid'] not in {s['vm_id'] for s in states}:
                                    states.append(self._state(rec['uuid'],records))
                        states += [t for t in self._tombs().values() if t['vm_id'] not in {s['vm_id'] for s in states}]
                        states=[s for s in states if lifecycle=='all' or s['lifecycle']==lifecycle]
                    result={'vm':self._view(states[0],observe=True)} if action=='get' else {'vms':[self._view(s,observe=True) for s in states]}
                elif action in ('start','stop','reboot','autostart-set','board-shutdown-set','config'):
                    tomb=self._state(spec.get('vm_id'),records)
                    if tomb['lifecycle']!='REGISTERED':
                        fail('VM_PURGED' if tomb['lifecycle']=='PURGED' else 'INVALID_SPEC','VM is not registered')
                    record=tomb['record']
                    if action in ('autostart-set', 'board-shutdown-set', 'config'):
                        self._writable()
                    with tool_output():
                        if action=='start':
                            self.p.check_xen()
                            if vm.guest_kind(record)=='linux':
                                self.p.prepare_dhcp(records)
                            self.p.start(record)
                        elif action=='stop':
                            self.p.stop_many([record],force=bool(spec.get('force',False)))
                        elif action=='reboot':
                            self.p.reboot(record,force=bool(spec.get('force',False)))
                        elif action=='config':
                            vm.validate_record(record,self.data/'instances'/record['id'])
                            vm.ensure_runtime(self.p.runtime)
                            atomic_write(self.p.runtime/(record['id']+'.cfg'),self.p.config(record))
                        else:
                            updated=dict(record)
                            if action=='autostart-set':
                                updated['autostart']=spec['value']=='on'
                            else:
                                updated['board_shutdown_policy']=spec.get('value')
                            vm.validate_identity(updated)
                            self._write(self.data/'instances'/record['id']/'instance.json',updated)
                            tomb={**tomb,'record':updated}
                    result={'vm':self._view(tomb,observe=True)}
                elif action=='operation':
                    found=[o for o in self._operations() if
                           (spec.get('operation_id') is not None and o['operation_id']==spec['operation_id']) or
                           (spec.get('idempotency_key') is not None and o['idempotency_key']==spec['idempotency_key'])]
                    if len(found)!=1:
                        fail('VM_NOT_FOUND','operation not found')
                    result={'operation':found[0], 'recovery_required':found[0]['status']!='SUCCEEDED',
                            'effective_status':'SUCCEEDED' if found[0]['status']=='SUCCEEDED' else 'RECOVERY_REQUIRED'}
                    context['operation_id']=found[0]['operation_id']
                else:
                    fail('INVALID_SPEC','unsupported management action')
                response.update(ok=True,data=result)
        except ManagementError as exc:
            response['error']=exc.error
        except vm.DomainIdentityError as exc:
            response['error']={'code':'TARGET_CHANGED', 'message':str(exc),
                               'retryable':False, 'details':{}}
        except PlatformError as exc:
            busy='operation is in progress' in str(exc)
            response['error']={'code':'OPERATION_IN_PROGRESS' if busy else 'ARTIFACT_INVALID',
                               'message':'another platform operation is in progress' if busy else str(exc),
                               'retryable':busy,'details':{}}
        except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
            cleanup = action=='purge' and context['operation_id'] is not None
            response['error']={'code':'CLEANUP_INCOMPLETE' if cleanup else 'RECOVERY_REQUIRED' if context['operation_id'] else 'ARTIFACT_INVALID',
                               'message':type(exc).__name__+': management operation could not be completed',
                               'retryable':cleanup,'details':{'operation_id':context['operation_id']} if cleanup else {}}
        response.update(operation_id=context['operation_id'],replayed=context['replayed'])
        if (not response['ok'] and context['write_operation'] and execute
                and response['error']['code'] not in ('IDEMPOTENCY_CONFLICT','TARGET_CHANGED','VM_PURGED')):
            # 이전 성공 기록은 오래된 요청의 오류로 덮어쓰지 않는다.
            try:
                path=self.data/'operations'/(context['operation_id']+'.json')
                with operation_lock(self.p.runtime):
                    self._writable()
                    op=self._read(path)
                    if op['status']!='SUCCEEDED':
                        op.update(status='RECOVERY_REQUIRED',error=response['error'])
                        self._save_op(op)
            except (OSError,PlatformError,ValueError):
                pass
        return response


def exit_code(response):
    if response['ok']:
        return 0
    code=response['error']['code']
    return 2 if code in VALIDATION else 3 if code in CONFLICTS else 4
