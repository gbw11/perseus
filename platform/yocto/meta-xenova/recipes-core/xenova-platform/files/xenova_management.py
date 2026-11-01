"""UUID 기반 공통 VM 관리 계약. CLI/백엔드가 같은 잠금·작업 기록·생성 함수를 사용한다.

동기 작업만 제공한다. Linux 준비는 원 실행 소유권을 유지하며 공통 잠금 밖에서 수행한다. 부분 준비 디스크는
자동 재초기화/정리하지 않는다. root 권한의 외부 xl/파일 변경까지 원자적으로 통제하지 않는다.
"""
from argparse import Namespace
from _thread import allocate_lock
from contextlib import contextmanager
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
from xenova_runtime import PlatformError, OperationBusy, atomic_write, operation_lock, sync_directory, observation_slot
import xenova_vm as vm
import xenova_zephyr as zephyr
import xenova_creation as creation
from xenova_execution import ExecutionError, ReadChanged, short_lock
from xenova_preparation import request_output, LinuxSlot, owner_state, AssetLease, guard_asset, asset_state, lifecycle_io, lifecycle_slot
from xenova_trace import bind as trace_bind, traced, span

MUTATIONS = {'create', 'retire', 'restore', 'purge', 'update-resources', 'firmware-transition'}
EXIT_CODES = {'success': 0, 'validation': 2, 'conflict': 3, 'execution': 4}
CONFLICTS = {'VM_NAME_CONFLICT', 'IP_CONFLICT', 'IDEMPOTENCY_CONFLICT', 'OPERATION_IN_PROGRESS',
             'VM_NOT_STOPPED', 'TARGET_CHANGED', 'ASSET_IN_USE', 'RESTORE_CONFLICT', 'VM_PURGED',
             'HOST_QUIESCING', 'BOOT_ADMISSION_REQUIRED', 'INTENT_MISMATCH'}
VALIDATION = {'INVALID_SPEC', 'VM_NOT_FOUND', 'TEMPLATE_NOT_FOUND', 'TEMPLATE_VERSION_NOT_FOUND', 'RESOURCE_UNSUPPORTED', 'RECOVERY_MODE_UNSUPPORTED'}


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
    """하위 진단은 요청별 제한 sink로 보낸다. 프로세스 전역 FD/stdout은 바꾸지 않는다."""
    with request_output():
        yield


class NeedLinuxSlot(Exception):
    """내부 제어 흐름: 공통 잠금 밖에서 미수락 슬롯을 얻고 전체 수락 조건을 재검사한다."""


class PrivateCreate:
    def __init__(self, operation):
        self.operation=operation


class ReadContext:
    """한 조회 회차의 구성·실행 근거. 원자 파일을 검증하며 잠금 밖에서 외부 관측한다.

    실제 제어에는 전달하지 않는다. 변경을 발견하면 호출자가 최대 한 번 새 회차를 만들며,
    다른 요청·다른 host나 새 실행에 이 문맥을 복사하지 않는다.
    """
    def __init__(self, manager, action, spec, deadline):
        self.m,self.action,self.spec,self.deadline=manager,action,spec,deadline
        self.started_ms=int(manager.p.clock()*1000)
        self.observed_ms={}
        self.e=manager.p.execution()
        self.check()
        self.states,self.tombs=manager._read_states(action,spec,deadline)
        self.by_id={s['vm_id']:s for s in self.states}
        self.lifecycles={s['vm_id']:(s['lifecycle'],s['generation']) for s in self.states}
        self.inputs={s['vm_id']:self.e.observation_inputs(s['record'],self.lifecycles[s['vm_id']]) for s in self.states}
        self.check()

    def check(self):
        if self.m.p.clock()>=self.deadline:
            fail('DEADLINE_EXCEEDED','read observation budget expired')
        if self.m.p.boot_id_reader()!=self.e.host:raise ReadChanged()

    @staticmethod
    def revision(states):
        return {s['vm_id']:(s['lifecycle'],s['generation'],s['record']) for s in states}

    def optional(self, path):
        try:path.lstat()
        except FileNotFoundError:return None
        return self.m._read(path)

    def validate_one(self, identity):
        """관측 게시 직전 해당 VM만 대조한다. 외부 helper나 전체 등록부 검색을 하지 않는다."""
        self.check()
        state=self.by_id[identity]
        if self.optional(self.m.data/'tombstones'/(identity+'.json'))!=self.tombs.get(identity):
            raise ReadChanged()
        record=state['record']
        if state['lifecycle']=='REGISTERED':
            directory=self.m.data/'instances'/record['id']
        elif state['lifecycle']=='RETIRED':
            directory=self.m._archive(state)
        else:directory=None
        if directory is not None:
            if self.optional(directory/'instance.json')!=record:raise ReadChanged()
        creation=record.get('creation_operation_id')
        if creation and state['lifecycle']=='REGISTERED':
            op=self.optional(self.m.data/'operations'/(creation+'.json'))
            if not op or op.get('vm_id')!=identity or op.get('status')!='SUCCEEDED':raise ReadChanged()
        if self.e.observation_inputs(record,self.lifecycles[identity])!=self.inputs[identity]:
            raise ReadChanged()
        self.check()

    def validate(self):
        self.check()
        if self.action=='list':
            states,_=self.m._read_states(self.action,self.spec,self.deadline)
            if self.revision(states)!=self.revision(self.states):raise ReadChanged()
        # 각 항목은 바로 이어지는 관측 게시 잠금 안에서 한 번 대조한다. 같은 검사를 중복하지 않는다.




class Manager:
    """call(action, spec, execute=False)는 envelope를 반환한다. 변경은 명시적 execute에서만 한다."""
    def __init__(self, platform=None, *, preparer=None, usage_check=None, checkpoint=None,
                 defaults_path=None, trace_sink=None):
        self.p = platform or vm.Platform()
        self.data = self.p.data
        self.defaults_path = Path(defaults_path) if defaults_path is not None else self.p.defaults_path
        self.preparer = preparer or self._prepare
        self.usage_check = usage_check or self._usage
        self.checkpoint = checkpoint or (lambda phase, operation: None)
        self.trace_sink = trace_sink
        self.last_trace = None
        self._request_lock=allocate_lock()

    def fork(self):
        """같은 저장소·잠금·guard를 쓰는 독립 요청 객체. 가변 예산/trace를 공유하지 않는다.

        Linux private 준비·느린 조회와 다른 VM의 조회/제어를 병행할 수 있다. 같은 객체의
        동시 call은 무대기 거절한다. 모든 물리 변경의 병렬 실행을 허용하는 계약은 아니다.
        """
        from copy import copy
        request_platform=copy(self.p)
        request_platform.__dict__.pop('response_deadline',None)
        default=getattr(self.preparer,'__func__',None) is type(self)._prepare
        default_usage=getattr(self.usage_check,'__func__',None) is type(self)._usage
        return type(self)(request_platform, preparer=None if default else self.preparer, usage_check=None if default_usage else self.usage_check,
                          checkpoint=self.checkpoint, defaults_path=self.defaults_path,
                          trace_sink=self.trace_sink)

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

    @traced('manager.required-write')
    def _write(self, path, value):
        self._plain_dir(path.parent, create=True)
        if path.exists() or path.is_symlink():
            catalog.plain(path)
        atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False) + '\n')

    def _operations(self, *, deadline=None):
        if deadline is None:deadline=getattr(self.p,'response_deadline',None)
        path = self.data / 'operations'
        if not path.exists() and not path.is_symlink():
            return []
        self._plain_dir(path)
        result = []
        for entry in sorted(path.iterdir()):
            if deadline is not None and self.p.clock()>=deadline:
                fail('DEADLINE_EXCEEDED','asset operation scan budget expired')
            if entry.name.startswith('.') or re.fullmatch(r'[0-9a-f-]{36}\.json\.tmp-[0-9]+-[0-9a-f]{12}',entry.name):
                catalog.plain(entry)
                continue
            if not entry.name.endswith('.json') or not vm.UUID.fullmatch(entry.stem):
                fail('RECOVERY_REQUIRED', 'invalid operation record path')
            op = self._read(entry)
            if deadline is not None and self.p.clock()>=deadline:
                fail('DEADLINE_EXCEEDED','asset operation read budget expired')
            if (op.get('operation_id') != entry.stem or op.get('schema_version') not in ('1.0','1.1','1.2')
                    or op.get('spec_hash') != digest(op.get('initial_spec'))):
                fail('RECOVERY_REQUIRED', 'operation integrity mismatch')
            if not vm.UUID.fullmatch(op.get('vm_id','')) or op.get('action') not in MUTATIONS:
                fail('RECOVERY_REQUIRED','invalid operation identity/action')
            if op['schema_version']=='1.2' and op['action']!='create':
                fail('RECOVERY_REQUIRED','optional-input journal version is only valid for create')
            if op['action']=='create':
                creation.validate(op)
                if op['schema_version']=='1.2':
                    # 당시 저장한 작은 원천으로만 대조한다. 현재 카탈로그는 읽지 않는다.
                    source=op['template']
                    try:
                        submitted=self._normalize('create',op['request'])
                        if submitted!=op['request'] or {'vcpus','memory_mib'}<=set(submitted):
                            raise ValueError('optional-input journal requires the original omitted keys')
                        resolved=self._resolve_create(submitted,source)
                        if self._creation_spec(resolved,source)!=op['initial_spec']:
                            raise ValueError('resolved creation differs from its stored source')
                    except (ManagementError,KeyError,TypeError,ValueError):
                        fail('RECOVERY_REQUIRED','creation input/default resolution mismatch')
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
            elif op['action'] in ('update-resources','firmware-transition'):
                snap=op.get('snapshot',{})
                before=snap.get('record',{})
                after=op.get('updated_record',{})
                vm.validate_identity(before)
                vm.validate_identity(after)
                allowed=('vcpus','memory_mib') if op['action']=='update-resources' else (
                    'active_firmware','hardware_profile','capabilities','shutdown_policy',
                    'board_shutdown_policy','mac','ipv4','hostname')
                if (snap.get('vm_id')!=op['vm_id'] or snap.get('generation')!=op.get('expected_generation')
                        or before['uuid']!=op['vm_id']
                        or {**before,**{k:after[k] for k in allowed if k in after}}!=after
                        or digest(before)!=op['request'].get('expected_configuration_sha256')):
                    fail('RECOVERY_REQUIRED','invalid configuration operation snapshot')
                if (op['request']!=op['initial_spec'] or
                        op['expected_generation']!=op['request'].get('expected_lifecycle_generation')):
                    fail('RECOVERY_REQUIRED','configuration operation request/context mismatch')
                if op['action']=='update-resources':
                    if (vm.guest_kind(before)!='linux' or
                            any(after[k]!=op['request'].get(k) for k in ('vcpus','memory_mib'))):
                        fail('RECOVERY_REQUIRED','resource journal differs from its request')
                elif (vm.guest_kind(before)!='zephyr' or
                        op.get('assigned')!={k:after[k] for k in ('mac','ipv4','hostname') if k in after}):
                    fail('RECOVERY_REQUIRED','firmware transition reservation differs from metadata')
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

    def _tombs(self, *, deadline=None):
        if deadline is None:deadline=getattr(self.p,'response_deadline',None)
        try:
            return vm.read_tombstones(self.data,deadline=deadline,clock=self.p.clock)
        except ExecutionError:
            raise
        except PlatformError as exc:
            fail('RECOVERY_REQUIRED', str(exc))

    def _writable(self):
        """종료 gate는 조회/완료 재전달과 실제 쓰기를 구분한다. 공통 잠금 안에서 호출한다."""
        marker = self.p.runtime / 'shutdown-requested'
        if marker.exists() or marker.is_symlink():
            fail('OPERATION_IN_PROGRESS', 'board shutdown is in progress', retryable=True)
        self.p.execution().gate()

    def _asset_guard(self, context, identity):
        """내부 수행 문맥의 실제 lease만 사용한다. 독립/중첩 writer는 무대기 검사한다."""
        lease=context.get('asset_lease')
        if lease is None:
            guard_asset(self.p,identity)
        else:
            if lease.p is not self.p or lease.identity!=identity:
                fail('TARGET_CHANGED','asset execution context belongs to a different target')
            lease.guard()

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
                               'reboot', 'destroy', 'autostart-set', 'board-shutdown-set', 'config',
                               'recover_once', 'recovery-operation', 'recovery-reconcile', 'control-operation', 'snapshot', 'probe',
                               'execution-context', 'host-resume', 'cpu-layout', 'cpu-policy-set', 'cpu-host-prepare'}
        actions.add('operation-reconcile')
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
        if action=='operation-reconcile':return creation.validate_request(self,spec,key)
        if action in MUTATIONS:
            if action != 'create' and not self._text_token(key):
                fail('INVALID_SPEC', 'idempotency_key of 1..200 UTF-8 bytes required')
            return self._normalize(action, spec)
        if action == 'cpu-policy-set':
            from xenova_cpu import validate_spec
            if not self._text_token(key):
                fail('INVALID_SPEC', 'cpu-policy-set requires idempotency_key')
            return validate_spec(spec)
        if action in ('cpu-layout', 'cpu-host-prepare'):
            allowed = {'vm_id', 'timeout_ms'} if action == 'cpu-layout' else {'timeout_ms', 'restore'}
            if set(spec)-allowed or ('restore' in spec and type(spec['restore']) is not bool):
                fail('INVALID_SPEC', 'unexpected CPU specification fields')
            if 'vm_id' in spec:
                spec = {**spec, 'vm_id':normalize_uuid(spec['vm_id'])}
            value = spec.get('timeout_ms', 15000)
            if type(value) is not int or not 0 < value <= 15000:
                fail('INVALID_SPEC', 'timeout_ms must be 1..15000')
            return {**spec, 'timeout_ms':value}
        if action == 'recover_once':
            from xenova_recovery import validate_request
            return validate_request(spec)
        if action == 'templates':
            allowed = set()
        elif action == 'host-resume':
            if set(spec) != {'barrier_id'} or not isinstance(spec['barrier_id'], str) or not vm.UUID.fullmatch(spec['barrier_id']):
                fail('INVALID_SPEC', 'host-resume requires the current barrier_id')
            return spec
        elif action == 'control-operation':
            allowed = {'operation_id', 'idempotency_key', 'vm_id', 'timeout_ms'}
            selectors = [n for n in ('operation_id', 'idempotency_key') if n in spec]
            if set(spec)-allowed or len(selectors)!=1:
                fail('INVALID_SPEC', 'exactly one control operation selector required')
            selected=selectors[0]
            if (not self._text_token(spec[selected]) or
                    selected=='operation_id' and not vm.UUID.fullmatch(spec[selected])):
                fail('INVALID_SPEC', 'invalid control operation selector')
            if 'vm_id' in spec:spec={**spec,'vm_id':normalize_uuid(spec['vm_id'])}
            timeout=spec.get('timeout_ms',15000)
            if type(timeout) is not int or not 0<timeout<=15000:
                fail('INVALID_SPEC','timeout_ms must be 1..15000')
            return {**spec,'timeout_ms':timeout}
        elif action == 'recovery-reconcile':
            if key is not None:
                fail('INVALID_SPEC', 'reconciliation uses the original attempt; no new idempotency key')
            if set(spec) - {'recovery_attempt_id', 'timeout_ms'} or 'recovery_attempt_id' not in spec:
                fail('INVALID_SPEC', 'recovery-reconcile requires an original recovery_attempt_id')
            identity = spec['recovery_attempt_id']
            timeout = spec.get('timeout_ms', 15000)
            if (not isinstance(identity, str) or not vm.UUID.fullmatch(identity)
                    or type(timeout) is not int or not 0 < timeout <= 15000):
                fail('INVALID_SPEC', 'canonical attempt and timeout_ms 1..15000 required')
            return {**spec, 'timeout_ms':timeout}
        elif action == 'recovery-operation':
            field = 'recovery_attempt_id'
            if set(spec) != {field} or not isinstance(spec[field], str) or not vm.UUID.fullmatch(spec[field]):
                fail('INVALID_SPEC', 'canonical operation selector required')
            return spec
        elif action == 'list':
            allowed = {'lifecycle'}
        elif action == 'operation':
            allowed = {'operation_id', 'idempotency_key'}
        else:
            allowed = {'vm_id'} | ({'force'} if action in ('stop', 'reboot', 'destroy')
                                  else {'value'} if action.endswith('-set') else set())
        if action in ('snapshot', 'probe'):
            allowed |= {'max_age_ms'} if action == 'snapshot' else {'timeout_ms'}
        if action in ('get','list'):
            allowed.add('timeout_ms')
            if 'timeout_ms' in spec and (type(spec['timeout_ms']) is not int or not 0<spec['timeout_ms']<=15000):
                fail('INVALID_SPEC','timeout_ms must be 1..15000')
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
            if action in ('snapshot', 'probe'):
                field = 'max_age_ms' if action == 'snapshot' else 'timeout_ms'
                value = spec.get(field)
                if type(value) is not int or not 0 < value <= (120000 if field == 'max_age_ms' else 15000):
                    fail('INVALID_SPEC', field+' is outside the supported range')
            if action == 'destroy' and spec.get('force') is not True:
                fail('INVALID_SPEC', 'destroy requires explicit force')
            if 'force' in spec and type(spec['force']) is not bool:
                fail('INVALID_SPEC', 'force must be a boolean')
            if action == 'autostart-set' and spec.get('value') not in ('on', 'off'):
                fail('INVALID_SPEC', 'autostart-set requires on or off')
            if action == 'board-shutdown-set' and spec.get('value') not in ('graceful', 'refuse', 'force-allowed'):
                fail('INVALID_SPEC', 'invalid board shutdown policy')
        return spec

    @traced('manager.registry-state')
    def _state(self, identity, records, *, tombstones=None):
        if not isinstance(identity, str) or not vm.UUID.fullmatch(identity):
            fail('INVALID_SPEC', 'vm_id must be the existing permanent UUID')
        active = [r for r in records.values() if r['uuid'] == identity]
        tomb = (self._tombs() if tombstones is None else tombstones).get(identity)
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
        for path in self._legacy_paths():
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
        for path in self._legacy_paths():
            self._plain_dir(path)
            record=self._read(path/'instance.json')
            vm.validate_identity(record)
            if path.name!='.deleted-'+record['id']+'-'+record['uuid']:
                fail('RECOVERY_REQUIRED','invalid legacy archive identity')
            result.append(record)
        return result

    def _legacy_paths(self):
        """glob이 숨길 수 있는 열람 실패를 VM/주소 부재로 해석하지 않는다."""
        directory=self.data/'instances'
        self._plain_dir(directory)
        return [path for path in sorted(directory.iterdir()) if path.name.startswith('.deleted-')]

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
        if action == 'firmware-transition':
            from xenova_zephyr_transition import validate
            return validate(spec, normalize_uuid, fail)
        if action == 'update-resources':
            from xenova_resources import validate
            return validate(spec, normalize_uuid, fail)
        if action == 'create':
            required = {'vm_id','name','template_id','template_version'}
            if set(spec) - (required | {'ipv4','vcpus','memory_mib'}) or not required <= set(spec):
                fail('INVALID_SPEC', 'vm_id, name and exact template ID/version required')
            name = spec['name']
            if (not isinstance(name, str) or not name.strip() or len(name) > 63
                    or any(ord(c) < 32 or 0x7f <= ord(c) <= 0x9f or 0xd800 <= ord(c) <= 0xdfff for c in name)):
                fail('INVALID_SPEC', 'display name must be 1..63 characters without controls')
            try:
                catalog.identity(spec['template_id'], spec['template_version'])
            except PlatformError:
                fail('INVALID_SPEC', 'invalid template ID or explicit version')
            if any(k in spec and (type(spec[k]) is not int or spec[k] <= 0) for k in ('vcpus','memory_mib')):
                fail('INVALID_SPEC', 'vcpus and memory_mib must be positive integers')
            if 'ipv4' in spec and not self._valid_ip(spec['ipv4']):
                fail('INVALID_SPEC','explicit IP must be 192.168.77.2..99')
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

    def _reserve_space(self, additional_bytes, records, *, restored=None, pending=None):
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
        if pending is None:
            pending=[op for op in self._operations() if op['action']=='create' and creation.holds(self,op)]
        for op in pending:
            if op['action'] != 'create' or op['vm_id'] in active:
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

    def _selection(self, spec, *, full=True):
        all_entries = catalog.entries(self.data)
        matches = [e for e in all_entries if e['template_id'] == spec['template_id']]
        if not matches:
            fail('TEMPLATE_NOT_FOUND', 'normalized template ID not published')
        selected = next((e for e in matches if e['template_version'] == spec['template_version']), None)
        if selected is None:
            fail('TEMPLATE_VERSION_NOT_FOUND', 'exact template version not published')
        if selected['guest_kind'] == 'zephyr' and not selected['capabilities'].get('dhcp'):
            if 'ipv4' in spec:
                fail('INVALID_SPEC', 'Zephyr has no IP field')
        else:
            ip = spec.get('ipv4')
            if 'ipv4' in spec and not self._valid_ip(ip):
                fail('INVALID_SPEC', 'explicit Linux IP must be 192.168.77.2..99')
        descriptor = {k:selected[k] for k in ('schema_version','template_id','template_version','guest_kind',
                                             'asset_directory','manifest_sha256','source_template_id')}
        return catalog.describe(self.data, descriptor, full=full)

    @staticmethod
    def _resolve_create(submitted, selected):
        """신규 선택/저장한 원천의 자원만 채운다. 생략과 명시값의 의미를 합치지 않는다."""
        resolved=dict(submitted)
        for key in ('vcpus','memory_mib'):
            if key not in resolved:
                defaults=selected.get('default_resources')
                if not isinstance(defaults,dict) or type(defaults.get(key)) is not int or defaults[key]<=0:
                    fail('ARTIFACT_INVALID','selected template resource defaults are unavailable or invalid')
                resolved[key]=defaults[key]
            lo,hi=selected['resource_limits'][key]
            if not lo<=resolved[key]<=hi:
                fail('RESOURCE_UNSUPPORTED' if key in submitted else 'ARTIFACT_INVALID',
                     'resource selection exceeds template/platform limits')
        return resolved

    @staticmethod
    def _creation_spec(resolved,selected):
        return {**resolved,'guest_kind':selected['guest_kind'],'autostart':False,
                'board_shutdown_policy':'graceful' if selected['capabilities'].get('graceful_shutdown') else 'refuse'}

    @staticmethod
    def _same_create_request(operation, submitted):
        """검증한 원 생성의 두 자원만 보충한다. 현재 구성/카탈로그나 원 기록은 바꾸지 않는다."""
        stored=operation.get('request');initial=operation['initial_spec']
        if not isinstance(stored,dict):fail('RECOVERY_REQUIRED','invalid original creation request')
        expected=dict(stored);incoming=dict(submitted)
        for key in ('vcpus','memory_mib'):
            value=initial.get(key)
            if (type(value) is not int or value<=0
                    or key in stored and (type(stored[key]) is not int or stored[key]!=value)
                    or key not in stored and operation['schema_version']!='1.2'):
                fail('RECOVERY_REQUIRED','original creation resources are inconsistent')
            expected[key]=value
            if key not in incoming:incoming[key]=value
        return incoming==expected

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

    def _ip_inventory(self, records, *, excluding=None, operations=None, pending=None):
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
        # 한 inventory는 같은 검증된 이력/보관 집합으로 조립한다. 외부 lease는 별도로 읽는다.
        operations=self._operations() if operations is None else operations
        tombs=self._tombs()
        held = set(reserved)
        own_records = list(records.values()) + [t['record'] for t in tombs.values()]
        own_record = next((r for r in own_records if r['uuid'] == excluding), None)
        own_mac = own_record.get('mac') if own_record else None
        if own_mac is None:
            own_mac = next((o.get('assigned', {}).get('mac') for o in operations
                            if o['action'] in ('create','firmware-transition') and o['vm_id'] == excluding), None)
        held.update(r['ipv4'] for r in settings['instances'] if r.get('guest_kind', 'linux') == 'linux')
        if excluding:
            held -= self._factory_owned_ip(own_record, factory)
        owned = list(records.values()) + self._legacy_records()
        owned += [t['record'] for t in tombs.values() if t['lifecycle'] != 'PURGED']
        held.update(r['ipv4'] for r in owned if r['uuid'] != excluding and r.get('ipv4'))
        if pending is None:
            pending=[op for op in operations if op['action'] in ('create','firmware-transition') and creation.holds(self,op)]
        for op in pending:
            if op['vm_id'] != excluding:
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
        if record.get('ipv4'):
            _, held = self._ip_inventory(records, excluding=record['uuid'])
            if record['ipv4'] in held:
                fail('RESTORE_CONFLICT', 'retained IP conflicts with another current reservation/lease')

    def _apply_dhcp(self, op, context=None):
        if vm.has_network(op['snapshot']['record']):
            self._save_op(op, 'dhcp-pending')
            with tool_output():
                self._prepare_dhcp(context)
            self._save_op(op, 'dhcp-applied')

    def _prepare_dhcp(self, context=None):
        records=self.p.records(validate_assets=False)
        # DHCP는 service 대기 뒤 직접 최신 registry를 얻는다. reload 뒤에는 더 소비하지 않는다.
        outside=(lambda:self._lifecycle_io(context,records,refresh_records=False)) if context and context.get('asset_lease') else None
        if outside is None:self.p.prepare_dhcp(records)
        else:self.p.prepare_dhcp(records,_outside=outside)

    def _allocate_ip(self, spec, records, *, operations=None, pending=None):
        pool, held = self._ip_inventory(records,operations=operations,pending=pending)
        if 'ipv4' in spec:
            if spec['ipv4'] in held:
                fail('IP_CONFLICT', 'IP is owned or reserved')
            return spec['ipv4']
        chosen = next((ip for ip in pool if ip not in held), None)
        if chosen is None:
            fail('IP_POOL_EXHAUSTED', 'no unreserved internal Linux address remains')
        return chosen

    def _conflicts(self, spec, records, *, operations=None):
        identity = spec['vm_id']
        tomb = self._tombs().get(identity)
        if tomb and tomb['lifecycle'] != 'REGISTERED':
            fail('VM_PURGED' if tomb['lifecycle'] == 'PURGED' else 'TARGET_CHANGED',
                 'VM UUID is retired/purged; restore or use a new UUID')
        if (any(r['uuid'] == identity for r in records.values()) or tomb
                or any(r['uuid'] == identity for r in self._legacy_records())
                or any(o['vm_id'] == identity for o in (self._operations() if operations is None else operations))):
            fail('IDEMPOTENCY_CONFLICT', 'UUID is already reserved by another origin/operation')
        key = internal_name(identity)
        if key in records or vm.retired_instance(self.data, key):
            fail('VM_NAME_CONFLICT', 'internal execution name is already owned')

    def _prepare(self, op, *, private=False):
        spec, selected = op['initial_spec'], op['template']
        descriptor={k:selected[k] for k in ('schema_version','template_id','template_version',
                    'guest_kind','asset_directory','manifest_sha256','source_template_id')}
        # Linux payload는 실제 복사 stream 전체를 검증한다. 이 단계의 manifest guard는 유지한다.
        selected=catalog.describe(self.data,descriptor,full=descriptor['guest_kind'] != 'linux')
        name = op.get('assigned', {}).get('internal_name', spec['name'])
        metadata = {'display_name':spec['name'], 'creation_spec':spec, 'creation_operation_id':op['operation_id'],
                    'template_id':spec['template_id'], 'template_version':spec['template_version'],
                    'template_manifest_sha256':selected['manifest_sha256']}
        records = self.p.records(validate_assets=False)
        workspace = self.data / op['owned_workspace']
        if selected['guest_kind'] == 'zephyr':
            if selected['capabilities'].get('dhcp'):
                metadata.update(mac=op['assigned']['mac'],ipv4=op['assigned']['ipv4'],hostname=name)
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
            return make_instance(Namespace(data_root=self.data,id=name,spec=None,template_root=None,deployment_seed=False),
                          vm=vm, defaults_path=self.defaults_path, record_override=record,
                          identity=op['vm_id'], workspace_path=workspace, template_directory=Path(selected['directory']),private=private)

    @traced('manager.view')
    def _view(self, tomb, *, observation=None):
        """검증된 관측을 VM 응답으로 조립한다. 이 함수는 Xen을 조회하지 않는다."""
        record = tomb['record']
        result = {'vm_id':tomb['vm_id'],'name':record.get('display_name', tomb['name']),
                  'template_id':record.get('template_id'), 'template_version':record.get('template_version'),
                  'guest_kind':vm.guest_kind(record), 'vcpus':record['vcpus'], 'memory_mib':record['memory_mib'],
                  'ipv4':record.get('ipv4'), 'lifecycle':tomb['lifecycle'],
                  'generation':tomb['generation'],'registered':tomb['lifecycle']=='REGISTERED',
                  'initial_spec':record.get('creation_spec'), 'configuration':record,
                  'configuration_sha256':digest(record),
                  'xen_state':None,'domid':None,'app_readiness':'unknown'}
        if observation is not None:
            result.update(xen_state=observation['xen_state'], domid=observation['domid'],
                          observed_monotonic_ms=observation.get('observed_monotonic_ms'),
                          xen_unique_id=observation.get('xen_unique_id'))
        description = observation.get('_description') if observation is not None else None
        result.update(description if description is not None else
                      self.p.execution().describe(record, tomb['generation'], tomb['lifecycle'], observation))
        return result

    def _control_view(self, tomb, control_result, deadline):
        """내구성 있게 완료된 결과와 선택적인 재전달 현재 조회를 분리한다."""
        original = control_result['original_result']
        result = {'vm':None, 'original_result':original}
        try:
            observation = (self.p.execution().observe(tomb['record'], deadline)
                           if control_result['replayed'] else original['observation'])
            result['vm'] = self._view(tomb, observation=observation)
            result['current_observation'] = observation
        except (PlatformError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            result['current_observation'] = None
            result['diagnostic_warnings'] = [{'code':'DIAGNOSTIC_UNAVAILABLE',
                'sink':'current-observation', 'cause_type':type(error).__name__}]
        return result

    def _completed_busy_replay(self, action, spec, key, context):
        """실행 잠금 충돌은 검증된 과거 완료 결과를 뒤집지 않는다. 현재 조회는 생략한다."""
        # 원 기한 만료 뒤에도 완료 기록만 읽는 제한 예산이다. 새 실행/수락은 하지 않는다.
        read_end=min(self.p.clock()+2,getattr(self.p,'response_deadline',float('inf')))
        def complete(op, request):
            return (op and op.get('request')==request and op.get('phase')=='completed'
                    and op.get('error') is None and isinstance(op.get('result'),dict))
        if action in ('start','stop','reboot','destroy'):
            key=context.get('stop_key',key)
            if key is None:return None
            request={'vm_id':spec['vm_id'],'action':action,'force':bool(spec.get('force',False))}
            op=context.get('stop_intent')
            if not complete(op,request):
                with short_lock(self.p.runtime,'admission',deadline=read_end,
                                clock=self.p.clock,sleep=self.p.sleep):
                    tomb=self._state(spec['vm_id'],self.p.records(validate_assets=False))
                    if tomb['lifecycle']!='REGISTERED':return None
                    op=next((q for q in self.p.execution().requests(deadline=read_end) if q['idempotency_key']==key),None)
                    if not complete(op,request):return None
            data={'vm':None,'original_result':op['result'],'current_observation':None}
        elif action=='recover_once':
            if spec['expected_host_boot_id']!=self.p.boot_id_reader():return None
            with short_lock(self.p.runtime,'admission',deadline=read_end,
                            clock=self.p.clock,sleep=self.p.sleep):
                from xenova_recovery import Recovery
                op=Recovery(self).read_attempt(spec['recovery_attempt_id'])
                if not complete(op,spec):return None
            data={'attempt':op,'original_result':op['result'],'current_observation':None}
        elif action in ('retire','restore','purge','update-resources','firmware-transition'):
            matches=[q for q in self._operations(deadline=read_end) if q['idempotency_key']==key]
            if len(matches)>1:fail('RECONCILE_REQUIRED','operation selector is ambiguous')
            if not matches:return None
            op=matches[0]
            if op['request']!=spec or op['action']!=action:
                fail('IDEMPOTENCY_CONFLICT','same key has a different action or request')
            context.update(operation_id=op['operation_id'],replayed=True)
            if op['status']=='SUCCEEDED':
                return {**(op['result'] if action in ('retire','restore','purge') else {}),
                        'operation':op,'original_result':op['result'],'current':None}
            if asset_state(self.p,op)=='active':return self._operation_view(op,read_end)
            return None
        else:return None
        context.update(operation_id=op['operation_id'],replayed=True)
        data['diagnostic_warnings']=[{'code':'DIAGNOSTIC_UNAVAILABLE',
                                     'sink':'current-observation','cause_type':'OperationBusy'}]
        return data

    def _accept_stop(self, spec, key, deadline, context):
        """긴 실행 잠금 없이 짧게 의도를 수락한다. 정상 완료 응답을 만들지는 않는다."""
        execution = self.p.execution()
        receipt = {}
        admission_end=min(deadline,self.p.clock()+2)
        try:
            with short_lock(self.p.runtime, 'admission', deadline=admission_end,
                            clock=self.p.clock, sleep=self.p.sleep):
                records = self.p.records(validate_assets=False)
                tomb = self._state(spec['vm_id'], records)
                if tomb['lifecycle'] != 'REGISTERED':
                    fail('VM_PURGED' if tomb['lifecycle']=='PURGED' else 'INVALID_SPEC','VM is not registered')
                record = tomb['record']
                guard_asset(self.p, record['uuid'])
                if self.p.boot_id_reader() != execution.host:
                    fail('TARGET_CHANGED', 'stop acceptance host changed')
                if any(q['vm_id']==record['uuid'] and creation.holds(self,q) for q in self._operations()):
                    fail('OPERATION_IN_PROGRESS','asset operation owns stop target',retryable=True)
                request = {'vm_id':record['uuid'],'action':'stop','force':bool(spec.get('force',False))}
                intent = execution.intent(record)
                previous = next((q for q in execution.requests(deadline=admission_end) if key is not None and q['idempotency_key']==key),None)
                if key is None and intent and intent['desired_state']=='stopped':
                    candidate=execution.read(execution._path(execution.data,'control-requests',intent['accepted_operation_id']))
                    if candidate and candidate['request']==request and candidate['phase'] in ('prepared','accepted','executing','uncertain'):
                        previous=candidate
                if previous:
                    context.update(operation_id=previous['operation_id'],replayed=True)
                    context['stop_key']=previous['idempotency_key']
                    if previous['request']!=request:
                        fail('IDEMPOTENCY_CONFLICT','control key has a different request')
                    if (previous['phase']!='prepared' or
                            (intent and intent['accepted_operation_id']==previous['operation_id'])):
                        context['stop_intent']=previous
                    if previous['phase'] in ('completed','failed','executing','uncertain'):
                        return previous
                    if previous['host_boot_id']!=execution.host:
                        fail('TARGET_CHANGED','unfinished stop belongs to a previous host boot')
                    deadline=min(deadline,previous.get('deadline_monotonic_ms',0)/1000)
                op=execution._accept_locked(record,request,key or ('stop-intent:'+uuid4()),previous=previous,receipt=receipt,deadline=deadline)
                context.update(operation_id=op['operation_id'],stop_intent=op,stop_key=op['idempotency_key'])
                return op
        except ExecutionError as error:
            if (error.error['code']=='DEADLINE_EXCEEDED' and
                    error.error['details'].get('lock')=='admission' and self.p.clock()<deadline):
                fail('OPERATION_IN_PROGRESS','stop admission lock is busy; acceptance has not been checked',
                     retryable=True,details={'lock':'admission','wait_limit_s':2,'acceptance':'not-checked'})
            raise
        except OSError as error:
            op=receipt.get('operation')
            fail('STORAGE_UNAVAILABLE','stop intent acceptance was not durably confirmed',
                 details={'operation_id':op['operation_id'] if op else None,'acceptance':'unconfirmed'})

    def _observe_view(self, tomb, deadline=None):
        """자산 수명주기 호출자의 명시적인 현재 조회. 포맷 조립과 분리한다."""
        deadline = self.p.clock()+15 if deadline is None else deadline
        return self._view(tomb, observation=self.p.execution().observe(tomb['record'], deadline))

    def _finish(self, op, tomb):
        if op['action'] == 'create':
            self.p.execution().initialize(tomb['record'], 'stopped', 'create', op['operation_id'])
        result = self._view(tomb)
        if op['action']=='create' and op.get('error') is not None:
            op.setdefault('first_error',op['error'])
        op.update(status='SUCCEEDED',result=result,error=None)
        self._save_op(op, 'complete')
        return result

    def _save_tomb(self, tomb, op, lifecycle, generation, **extra):
        updated = {**tomb, **extra, 'lifecycle':lifecycle,'generation':generation,'last_operation_id':op['operation_id'],
                   'history':tomb['history'] + [{'operation_id':op['operation_id'],'action':op['action'],
                                               'generation':generation,'lifecycle':lifecycle}]}
        # 실행 잠금 -> admission 순서. stop의 lifecycle 확인과 전환 게시를 직렬화한다.
        with short_lock(self.p.runtime, 'admission'):
            self._write(self.data / 'tombstones' / (tomb['vm_id']+'.json'), updated)
        return updated

    def _owner_state(self, op):
        try:
            return owner_state(self.p,op)
        except (PlatformError,OSError,ValueError,KeyError,TypeError) as error:
            fail('OBSERVATION_UNAVAILABLE','original preparation ownership cannot be verified',
                 details={'operation_id':op['operation_id'],'cause_type':type(error).__name__})

    def _operation_view(self, op, deadline=None):
        if deadline is not None and self.p.clock()>=deadline:
            fail('DEADLINE_EXCEEDED','asset operation lookup budget expired')
        effective=op['status'] if op['status']=='SUCCEEDED' else 'RECOVERY_REQUIRED'
        if op['action']=='create' and creation.released(self,op):effective='FAILED'
        if (op['action']=='create' and op['template']['guest_kind']=='linux' and op['status']=='RUNNING'
                and self._owner_state(op)=='active'):
            effective='RUNNING'
        if (op['action']=='create' and op['status']!='SUCCEEDED' and effective!='FAILED'
                and op['template']['guest_kind']=='linux' and self._owner_state(op)=='reconciling'):
            effective='RECONCILING'
        if op['action'] in ('retire','restore','purge','update-resources','firmware-transition') and op['status']!='SUCCEEDED':
            try:
                if asset_state(self.p,op)=='active':effective='RUNNING'
            except (OSError,ValueError,PlatformError):
                fail('OBSERVATION_UNAVAILABLE','lifecycle execution owner cannot be verified')
        if deadline is not None and self.p.clock()>=deadline:
            fail('DEADLINE_EXCEEDED','asset operation ownership lookup budget expired')
        return {'operation':op,'recovery_required':effective=='RECOVERY_REQUIRED','effective_status':effective}

    @staticmethod
    def _preparation_failed(error, op):
        cause = str(error) if isinstance(error,(PlatformError,ValueError)) else (
            'errno='+str(error.errno) if isinstance(error,OSError) else
            'returncode='+str(error.returncode) if isinstance(error,subprocess.CalledProcessError) else '')
        fail('RECOVERY_REQUIRED',type(error).__name__+': Linux preparation failed'+(' ('+cause+')' if cause else '')+
             '; original operation and any partial assets retained',details={'operation_id':op['operation_id']})

    def _complete_private(self, pending, context):
        op=pending.operation
        try:
            with creation.work(self,op,context):prepared=self._prepare(op,private=True)
            from prepare_instance import PreparedInstance
            if not isinstance(prepared,PreparedInstance):
                fail('RECOVERY_REQUIRED','private preparation returned no verified result',
                     details={'operation_id':op['operation_id']})
            self.checkpoint('private-prepared',op)
            with operation_lock(self.p.runtime):
                current=next((o for o in self._operations() if o['operation_id']==op['operation_id']),None)
                if current!=op or self._owner_state(op)!='active':
                    fail('TARGET_CHANGED','original preparation operation or owner changed')
                records=self.p.records(validate_assets=False)
                self._reserve_space(0,records)
                _,held=self._ip_inventory(records,excluding=op['vm_id'])
                if op['assigned'].get('ipv4') in held:
                    fail('IP_CONFLICT','recorded preparation address is no longer exclusively reserved')
                descriptor={k:op['template'][k] for k in ('schema_version','template_id','template_version',
                    'guest_kind','asset_directory','manifest_sha256','source_template_id')}
                catalog.describe(self.data,descriptor,full=False)
                if (prepared.record.get('creation_operation_id')!=op['operation_id']
                        or prepared.record.get('creation_spec')!=op['initial_spec']
                        or prepared.record.get('template_manifest_sha256')!=op['template']['manifest_sha256']
                        or prepared.record.get('uuid')!=op['vm_id']
                        or prepared.output!=self.data/'instances'/op['assigned']['internal_name']
                        or prepared.stage!=self.data/op['owned_workspace']/op['assigned']['internal_name']
                        or any(prepared.record.get(k)!=op['assigned'][k] for k in ('mac','ipv4'))
                        or any(prepared.record.get(k)!=op['template'][k] for k in ('template_id','template_version','guest_kind'))):
                    fail('TARGET_CHANGED','private preparation result does not match the original operation')
                self.checkpoint('before-publish',op)
                with short_lock(self.p.runtime,'admission'):
                    self._writable()
                    if self.p.execution().barrier()!=context['linux_barrier']:
                        fail('TARGET_CHANGED','host admission context changed during preparation')
                    if self.p.execution().read(self.data/'control'/(op['vm_id']+'.json')) is not None:
                        fail('TARGET_CHANGED','VM intent appeared before private creation was adopted')
                    if op['vm_id'] in self._tombs():
                        fail('TARGET_CHANGED','VM lifecycle appeared before private creation was adopted')
                    with creation.work(self,op,context):prepared.publish()
                return self._create(op,context)
        except (ManagementError,ExecutionError):
            raise
        except (PlatformError,OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
            self._preparation_failed(error,op)

    def _create(self, op, context=None):
        context=context if context is not None else {}
        dhcp_applied=op.get('phase') in ('dhcp-applied','complete')
        trace_bind(operation_id=op['operation_id'],vm_id=op['vm_id'])
        if op.get('assigned',{}).get('ipv4'):
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
            if op['template']['guest_kind']=='linux':
                if 'linux_slot' not in context:
                    raise NeedLinuxSlot()
                context['linux_slot'].claim(op)
                context['linux_barrier']=self.p.execution().barrier()
                # workspace 전 실패의 명시적 재개다. 조회가 상태를 바꾸는 것은 아니다.
                op['status']='RUNNING'
            self._save_op(op, 'preparing')
            if (op['template']['guest_kind']=='linux'
                    and getattr(self.preparer,'__func__',None) is type(self)._prepare):
                return PrivateCreate(op)
            try:
                with tool_output():
                    self.preparer(op)
            except ManagementError:
                raise
            except (PlatformError,OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
                if op['template']['guest_kind']!='linux':
                    raise
                self._preparation_failed(error,op)
        self._save_op(op, 'published')
        record = self._read(target / 'instance.json')
        if (record['uuid'] != op['vm_id'] or record.get('creation_operation_id') != op['operation_id']
                or record.get('creation_spec') != op['initial_spec']
                or record.get('template_manifest_sha256') != op['template']['manifest_sha256']
                or any(record.get(key) != op['template'][key] for key in ('template_id', 'template_version', 'guest_kind'))):
            fail('RECOVERY_REQUIRED', 'published instance does not match recorded creation identity')
        vm.validate_record(record, target)
        tomb = {'schema_version':'1.0','vm_id':record['uuid'],'name':record['name'],'generation':0,
                'lifecycle':'REGISTERED','record':record,'history':[]}
        tomb = self._save_tomb(tomb, op, 'REGISTERED', 0)
        if vm.has_network(record):
            if not dhcp_applied:
                if record['guest_kind']=='linux':
                    # DHCP 재제출은 결과 조회가 아닌 외부 효과다. 원 생성 기한 밖에서 반복하지 않는다.
                    with creation.work(self,op,context) as end:
                        self._save_op(op, 'dhcp-pending')
                        with tool_output():self.p.prepare_dhcp(self.p.records(validate_assets=False),deadline=end)
                else:
                    self._save_op(op, 'dhcp-pending')
                    with tool_output():self.p.prepare_dhcp(self.p.records(validate_assets=False))
            self._save_op(op, 'dhcp-applied')
        return self._finish(op, tomb)

    @staticmethod
    def _filesystem_revision(path):
        info=catalog.plain(path).stat()
        return (str(path),info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)

    @contextmanager
    def _lifecycle_io(self, context, records, *, refresh_records=True):
        """불변 대상 보호 아래 긴 I/O. 재진입 뒤 registry/의도/원 기록은 새로 대조한다."""
        lease = context.get('asset_lease')
        if lease is None:
            yield
            return
        path = self.data/'tombstones'/(lease.identity+'.json')
        before = self._read(path) if path.exists() or path.is_symlink() else None
        record = before['record'] if before else next(r for r in records.values() if r['uuid']==lease.identity)
        intent = self.p.execution().intent(record)
        configuration_path=self.data/'instances'/record['id']/'instance.json'
        configuration=(self._read(configuration_path) if lease.action in ('update-resources','firmware-transition','firmware-replace') else None)
        opid = context.get('operation_id')
        original = self._read(self.data/'operations'/(opid+'.json')) if opid else None
        with lifecycle_io(self.p, context['common_fd'], lease):
            yield
        after = self._read(path) if path.exists() or path.is_symlink() else None
        if (after != before or self.p.execution().intent(record) != intent or
                (configuration is not None and self._read(configuration_path)!=configuration) or
                (opid and self._read(self.data/'operations'/(opid+'.json')) != original)):
            fail('TARGET_CHANGED', 'lifecycle inputs changed during protected inspection')
        if refresh_records and lease.action in ('restore','update-resources','firmware-transition','firmware-replace'):
            # 다음 주소/용량 채택에는 다른 VM의 최신 등록도 필요하다. purge 항목마다
            # 전체 registry를 재열람할 이유는 없으며 대상 tomb/의도/원 기록만 대조한다.
            current = self.p.records(validate_assets=False)
            records.clear(); records.update(current)

    def _transition(self, op, records, *, filesystem_check=None, context=None):
        context = context or {}
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
                    self._apply_dhcp(op,context)
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
            return self._purge(op, tomb, records=records, context=context)
        if action == 'restore':
            directory = source if source.exists() or source.is_symlink() else target
            self._record_at(record, directory)
            self._restore_ip(record, records)
            self._reserve_space(0, records, restored=(record, directory))
        if source.exists() or source.is_symlink():
            if target.exists() or target.is_symlink():
                fail('RECOVERY_REQUIRED', 'both lifecycle source and destination exist')
            self._record_at(record, source)
            domains = self._stopped(record)
            with self._lifecycle_io(context, records):
                self.usage_check(source, domains)
            if action == 'restore':
                for other in records.values():
                    if any(other.get(k)==record.get(k) for k in vm.identity_fields(record)):
                        fail('RESTORE_CONFLICT', 'identity/name/MAC/IP conflicts with a registered VM')
            if action=='restore' and vm.guest_kind(record)=='linux':
                # 같은 잠금 구간에서 이미 검사한 바로 그 파일만 재사용한다. 원장 저장 callback
                # 등으로 내용/신원이 바뀌었거나 재전달로 들어오면 새 fsck가 필요하다.
                if filesystem_check is None or self._filesystem_revision(source/'root.raw')!=filesystem_check:
                    checked_revision = self._filesystem_revision(source/'root.raw')
                    with self._lifecycle_io(context, records):
                        self.p.command(['e2fsck','-fn',str(source/'root.raw')],timeout=120)
                    if self._filesystem_revision(source/'root.raw')!=checked_revision:
                        fail('TARGET_CHANGED','restore root changed during filesystem inspection')
                    filesystem_check = checked_revision
            self._stopped(record)
            self._writable()
            revision = filesystem_check if action=='restore' and vm.guest_kind(record)=='linux' else None
            self._save_op(op, 'moving')
            with short_lock(self.p.runtime,'admission'):
                if context.get('asset_lease'):context['asset_lease'].guard()
                self._writable()
                self._record_at(record,source)
                if revision is not None and self._filesystem_revision(source/'root.raw')!=revision:
                    fail('TARGET_CHANGED','restore root changed before publication')
                publish(source, target, allow_hidden=action=='retire')
        self._record_at(record, target)
        self._save_op(op, 'moved')
        tomb = self._save_tomb(tomb, op, desired, expected+1,
                              archive=op['target'] if action=='retire' else None)
        self._apply_dhcp(op,context)
        return self._finish(op, tomb)

    def _purge(self, op, tomb, *, records=None, context=None):
        records, context = records or {}, context or {}
        directory = self.data / op['source']
        self._plain_dir(directory.parent)
        if op.get('inventory') is None:
            self._metadata_at(tomb['record'], directory)
            domains = self._stopped(tomb['record'])
            with self._lifecycle_io(context, records):
                self.usage_check(directory, domains)
                inventory = self._ownership(directory)
            op['inventory'] = inventory
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
            with self._lifecycle_io(context, records):
                current = self._ownership(directory)
            if any(k not in op['inventory'] or any(v[x]!=op['inventory'][k][x] for x in ('dev','ino','directory'))
                   for k,v in current.items()):
                fail('RECOVERY_REQUIRED', 'purge asset ownership changed')
            domains = self._stopped(tomb['record'])
            with self._lifecycle_io(context, records):
                self.usage_check(directory, domains)
            op['remaining_assets'] = sorted(current)
            self._save_op(op, 'deleting')
            for relative in sorted(current, key=lambda x:(x.count('/'),x), reverse=True):
                with self._lifecycle_io(context, records):
                    self._unlink_owned(directory, relative, op['inventory'][relative])
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

    @staticmethod
    def _unlink_owned(directory, relative, expected):
        """lease가 보호한 항목만 삭제한다. 부모마다 nofollow·마지막 inode/link를 재검사한다."""
        parts = Path(relative).parts
        fd = os.open(directory, os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd=child
            st = os.stat(parts[-1],dir_fd=fd,follow_symlinks=False)
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

    def _mutate(self, action, spec, execute, key, records, context):
        filesystem_check=None
        if action == 'firmware-transition':
            from xenova_zephyr_transition import mutate
            return mutate(self,spec,execute,key,records,context,fail)
        if action == 'update-resources':
            from xenova_resources import mutate
            return mutate(self,spec,execute,key,records,context,fail)
        operations=self._operations()
        matches=[o for o in operations if o['idempotency_key']==key]
        if len(matches)>1:fail('RECONCILE_REQUIRED','operation selector is ambiguous')
        previous=matches[0] if matches else None
        # 새 create만 후속 판정에 전체 사본을 쓴다. 장기 restore/purge가 이력을 붙잡지 않는다.
        if action!='create':operations=None
        if previous:
            context['operation_id']=previous['operation_id']
            same_request=(previous['action']==action and
                          (self._same_create_request(previous,spec) if action=='create' else previous['request']==spec))
            if not same_request:
                fail('IDEMPOTENCY_CONFLICT','same key has a different action or request')
            context['replayed']=True
            tomb = self._tombs().get(previous['vm_id'])
            if action=='create' and creation.released(self,previous,durable=True):
                error=previous['error']
                fail(error['code'],error['message'],retryable=False,
                     details={**error.get('details',{}),'operation_id':previous['operation_id'],
                              'replayed':True,'original_create_succeeded':False,'reconciliation':previous['reconciliation']})
            if (action=='create' and previous['template']['guest_kind']=='linux' and previous['status']!='SUCCEEDED'):
                state=self._owner_state(previous)
                if state in ('active','reconciling'):
                    return self._operation_view(previous)
                if state=='residual':
                    fail('RECOVERY_REQUIRED','owned preparation child may remain; inspect the original operation',
                         details={'operation_id':previous['operation_id']})
            if previous['status']=='SUCCEEDED':
                if action=='create' and tomb and tomb['lifecycle']!='REGISTERED':
                    fail('VM_PURGED' if tomb['lifecycle']=='PURGED' else 'TARGET_CHANGED',
                         'the original VM is retired/purged; create cannot restore it')
                try:
                    current = self._observe_view(self._state(previous['vm_id'],records))
                except (PlatformError,OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
                    return {**previous['result'],'operation':previous,'original_result':previous['result'],
                            'current':None,'diagnostic_warnings':[{'code':'OBSERVATION_UNAVAILABLE',
                                                                 'cause_type':type(error).__name__}]}
                return {**(current if action == 'create' else previous['result']),
                        'operation':previous,'original_result':previous['result'], 'current':current}
            if action=='create' and previous['status']=='RECOVERY_REQUIRED':
                target=self.data/'instances'/previous.get('assigned',{}).get('internal_name',previous['initial_spec']['name'])
                workspace=self.data/previous['owned_workspace']
                if not target.exists() and not target.is_symlink() and (workspace.exists() or workspace.is_symlink()):
                    # 실패가 확정된 부분 준비의 재전달은 최초 오류·부분 파일을 다시 쓰지 않는다.
                    if not execute:
                        return {'plan':True,'operation':previous}
                    fail('RECOVERY_REQUIRED','original preparation failure and partial workspace retained; inspect the original operation',
                         details={'operation_id':previous['operation_id'],'owned_workspace':previous['owned_workspace']})
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
            if action=='create':
                if tomb and tomb['lifecycle']!='REGISTERED':
                    fail('TARGET_CHANGED','creation has a newer lifecycle')
                self._reserve_space(0, records)
                target=self.data/'instances'/previous.get('assigned',{}).get('internal_name',previous['initial_spec']['name'])
                if previous['template']['guest_kind']=='linux' and not target.exists():
                    with creation.work(self,previous,context):pass
                return self._create(previous,context)
            if context.get('asset_lease'):context['asset_lease'].bind(previous)
            return self._transition(previous,records,context=context)
        if action=='create':
            # 이 공통 잠금 구간에서는 아직 원장을 쓰거나 callback/준비에 진입하지 않았다.
            # 반환 확인도 한 번 수행해 주소/용량 판정에 전달한다. 슬롯 대기·private 준비 뒤에는
            # 이 지역 사본을 넘기지 않고 새 잠금 구간에서 원장과 내구 근거를 다시 확보한다.
            self._conflicts(spec,records,operations=operations)
            selected=self._selection(spec, full=False)
            resolved=self._resolve_create(spec,selected)
            pending=[o for o in operations if o['action'] in ('create','firmware-transition') and creation.holds(self,o)]
            assigned_ip = self._allocate_ip(spec, records,operations=operations,pending=pending) if selected['capabilities'].get('dhcp') else None
            # 명백한 용량 부족은 payload 전체 읽기 전에 거절한다. 이 결과는 예약이 아니다.
            self._reserve_space(sum(p.stat().st_size for p in Path(selected['directory']).iterdir()), records,pending=pending)
            if selected['guest_kind']!='linux':
                descriptor={k:selected[k] for k in ('schema_version','template_id','template_version',
                            'guest_kind','asset_directory','manifest_sha256','source_template_id')}
                selected=catalog.describe(self.data,descriptor,full=True)
                if self._resolve_create(spec,selected)!=resolved:
                    fail('TARGET_CHANGED','template defaults changed during selection; request not accepted')
                self._reserve_space(sum(p.stat().st_size for p in Path(selected['directory']).iterdir()), records,pending=pending)
            # Linux 내용 검증은 원 작업/예약을 저장한 뒤 실제 복사 바이트에 대해 한 번 수행한다.
            tomb=None
        else:
            tomb=self._state(spec['vm_id'],records)
            state=tomb['lifecycle']
            if state=='PURGED':
                fail('VM_PURGED','VM assets have been purged')
            if (action=='retire' and state!='REGISTERED') or (action in ('restore','purge') and state!='RETIRED'):
                fail('INVALID_SPEC','lifecycle does not permit this operation')
            if any(o['vm_id']==tomb['vm_id'] and creation.holds(self,o) for o in self._operations()):
                fail('OPERATION_IN_PROGRESS','an incomplete operation owns this VM; retry its original key',retryable=True)
            directory = self.data/'instances'/tomb['name'] if action=='retire' else self._archive(tomb)
            if action=='purge':
                self._metadata_at(tomb['record'],directory)
            else:
                self._record_at(tomb['record'],directory)
            domains = self._stopped(tomb['record'])
            with self._lifecycle_io(context, records):
                self.usage_check(directory, domains)
            if action=='restore':
                if vm.guest_kind(tomb['record'])=='linux':
                    before=self._filesystem_revision(directory/'root.raw')
                    with self._lifecycle_io(context, records):
                        self.p.command(['e2fsck','-fn',str(directory/'root.raw')],timeout=120)
                    if before==self._filesystem_revision(directory/'root.raw'):filesystem_check=before
                for other in records.values():
                    if any(other.get(k)==tomb['record'].get(k) for k in vm.identity_fields(tomb['record'])):
                        fail('RESTORE_CONFLICT','identity/name/MAC/IP already registered')
                self._restore_ip(tomb['record'], records)
                self._reserve_space(0, records, restored=(tomb['record'], directory))
        if not execute:
            return {'plan':True,'action':action,'spec':resolved if action=='create' else spec,
                    **({'request':spec} if action=='create' else {}),'starts_guest':False,
                    'assets_deleted':False}
        if action=='create' and selected['guest_kind']=='linux' and 'linux_slot' not in context:
            raise NeedLinuxSlot()
        self._writable()
        opid=uuid4();identity=spec['vm_id']
        operation_path=self.data/'operations'/(opid+'.json')
        if operation_path.exists() or operation_path.is_symlink():
            fail('TARGET_CHANGED','allocated operation ID already exists')
        if action=='create' and (identity in self._tombs() or any(o['vm_id']==identity for o in operations)
                                 or any(r['uuid']==identity for r in self._legacy_records())):
            fail('VM_NAME_CONFLICT','allocated UUID is already reserved')
        op={'schema_version':'1.0','operation_id':opid,'idempotency_key':key,'action':action,
            'vm_id':identity,'request':spec,'initial_spec':dict(spec),'spec_hash':digest(spec),
            'status':'RUNNING','phase':'recorded','result':None,'error':None}
        if action=='create':
            effective=self._creation_spec(resolved,selected)
            if any(k not in spec for k in ('vcpus','memory_mib')):op['schema_version']='1.2'
            op.update(initial_spec=effective,spec_hash=digest(effective))
            assigned_mac='02:'+':'.join(f'{b:02x}' for b in os.urandom(5))
            reserved_records=list(records.values())+[t['record'] for t in self._tombs().values()]+self._legacy_records()
            if any(r['uuid']==identity or r.get('mac')==assigned_mac for r in reserved_records) or any(
                    o.get('assigned',{}).get('mac')==assigned_mac for o in operations):
                fail('VM_NAME_CONFLICT','allocated identity collides; no assets created')
            op.update(template=selected,assigned={'internal_name':internal_name(identity), **({'mac':assigned_mac, 'ipv4':assigned_ip} if selected['capabilities'].get('dhcp') else {})},
                      owned_workspace='instances/.operation-'+opid)
            if selected['guest_kind']=='linux':creation.accept(self,op,context)
        else:
            op['expected_generation']=tomb['generation']
            op['source']=str((self.data/'instances'/tomb['name'] if action=='retire' else self._archive(tomb)).relative_to(self.data))
            op['target']=f"instances/.retired-{tomb['name']}-{identity}-g{tomb['generation']+1}" if action=='retire' else 'instances/'+tomb['name']
            op['snapshot']=tomb
        context['operation_id']=opid
        context['write_operation'] = True
        self._save_op(op)
        if context.get('asset_lease'):context['asset_lease'].bind(op)
        if action=='create' and op['template']['guest_kind']=='linux':
            with creation.work(self,op,context):pass
        return self._create(op,context) if action=='create' else self._transition(op,records,filesystem_check=filesystem_check,context=context)

    def call(self, action, spec=None, *, execute=False, idempotency_key=None, request_id=None, budget_ms=None):
        """같은 요청 객체의 중첩/동시 사용은 무대기 거절한다. 병행 호출은 fork 객체를 사용한다."""
        if not self._request_lock.acquire(False):
            if action=='snapshot':
                # 기존 lock-free 파생 snapshot 조회를 보존하되 진행 요청의 예산/진단은 공유하지 않는다.
                return self.fork().call(action,spec,execute=execute,idempotency_key=idempotency_key,
                                        request_id=request_id,budget_ms=budget_ms)
            return {'schema_version':'1.0','ok':False,
                'request_id':request_id if self._text_token(request_id) else uuid4(),
                'operation_id':None,'replayed':False,'data':None,
                'error':{'code':'OPERATION_IN_PROGRESS','message':'request object is already in use; use an independent fork',
                         'retryable':True,'details':{}}}
        try:
            return self._request(action,spec,execute=execute,idempotency_key=idempotency_key,request_id=request_id,budget_ms=budget_ms)
        finally:self._request_lock.release()

    def _request(self, action, spec=None, *, execute=False, idempotency_key=None, request_id=None, budget_ms=None):
        """동기 envelope. budget_ms는 이번 호출의 잔여 기간이며 원 spec/기한을 갱신하지 않는다.

        생략 시 기존 예산을 유지한다. 부가 trace 저장 실패는 원 완료를 바꾸지 않는다.
        """
        from xenova_trace import Trace, span
        trace = Trace(clock=self.p.clock, context={'action':action, 'request_id':request_id})
        with trace.activate(), span('manager.call'), request_output() as output:
            had_budget=hasattr(self.p,'response_deadline')
            previous_budget=getattr(self.p,'response_deadline',None)
            try:
                response = self._call(action, spec, execute=execute,
                                      idempotency_key=idempotency_key, request_id=request_id, budget_ms=budget_ms,_output=output)
            finally:
                if had_budget:self.p.response_deadline=previous_budget
                else:self.p.__dict__.pop('response_deadline',None)
            self.last_output={'bytes':bytes(output.content),'dropped':output.dropped}
        try:
            self.last_trace = trace.export()
            if self.trace_sink is not None:
                self.trace_sink(self.last_trace)
        except Exception as error:
            warning = {'code':'DIAGNOSTIC_UNAVAILABLE', 'sink':'management-trace',
                       'cause_type':type(error).__name__}
            destination = response['data'] if response['ok'] else response['error']['details']
            destination.setdefault('diagnostic_warnings', []).append(warning)
        def public(value):
            if isinstance(value, dict):
                return {key:public(item) for key,item in value.items() if key != '_description'}
            if isinstance(value, list):
                return [public(item) for item in value]
            return value
        return public(response)

    def _dispatch_locked(self,action,spec,execute,idempotency_key,context,deadline,cpu_deadline,entered):
        """기존 공통 잠금 안의 metadata/제어 dispatch. private Linux 준비 결과만 밖으로 돌려준다."""
        if spec.get('vm_id') and action not in ('probe','execution-context','recovery-operation'):
            self._asset_guard(context,spec['vm_id'])
        try:
            records=self.p.records(validate_assets=False)
        except ExecutionError:
            raise
        except (PlatformError,OSError,ValueError):
            fail('STORAGE_UNAVAILABLE','persistent storage or registry unavailable')
        if action == 'cpu-layout':
            result = self.p.cpu().layout(records, cpu_deadline, spec.get('vm_id'))
        elif action == 'cpu-policy-set':
            self._writable()
            result = self.p.cpu().set_policy(spec, records, execute=execute, key=idempotency_key, context=context,
                                             deadline=deadline)
        elif action == 'cpu-host-prepare':
            if execute:
                self._writable()
            result = self.p.cpu().prepare(records, cpu_deadline, execute=execute, restore=spec.get('restore',False), context=context)
        elif action in MUTATIONS:
            result=self._mutate(action,spec,execute,
                                'create:'+spec['vm_id'] if action=='create' else idempotency_key,
                                records,context)
        elif action=='templates':
            result={'templates':catalog.entries(self.data)}
        elif action in ('recover_once', 'recovery-operation', 'recovery-reconcile'):
            from xenova_recovery import Recovery
            recovery = Recovery(self)
            if action == 'recovery-operation':
                result = {'attempt':recovery.read_attempt(spec['recovery_attempt_id'])}
                context['operation_id'] = result['attempt']['operation_id']
            elif action == 'recovery-reconcile':
                if execute:
                    self._writable()
                result = recovery.reconcile(spec['recovery_attempt_id'], execute=execute,
                                            deadline=deadline, context=context)
            else:
                with tool_output():
                    result = recovery.once(spec, execute=execute, context=context)
        elif action == 'host-resume':
            result = {'host_admission':self.p.execution().resume_host(spec['barrier_id'])}
        elif action in ('probe', 'execution-context'):
            tomb = self._state(spec['vm_id'], records)
            if action == 'probe':
                observation = self.p.execution().observe(tomb['record'], deadline)
                result = {'observation':observation}
            else:
                observation = self.p.execution().observe(tomb['record'], deadline)
                result = {'vm':self._view(tomb, observation=observation)}
        elif action in ('start','stop','reboot','destroy','autostart-set','board-shutdown-set','config'):
            tomb=self._state(spec.get('vm_id'),records)
            if tomb['lifecycle']!='REGISTERED':
                fail('VM_PURGED' if tomb['lifecycle']=='PURGED' else 'INVALID_SPEC','VM is not registered')
            record=tomb['record']
            if action in ('autostart-set', 'board-shutdown-set', 'config'):
                self._writable()
            with tool_output():
                if action in ('start', 'stop', 'reboot', 'destroy'):
                    control_result = self.p.request_control(action, record, force=bool(spec.get('force',False)),
                                                            idempotency_key=context.get('stop_key',idempotency_key), deadline=deadline)
                    context['operation_id'] = control_result['operation_id']
                    context['replayed'] = control_result['replayed']
                    context['diagnostic_warnings'] = control_result.get('diagnostic_warnings', [])
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
            if action in ('start','stop','reboot','destroy'):
                result = self._control_view(tomb, control_result, min(deadline, entered+15) if control_result['replayed'] else deadline)
            else:
                observation = self.p.execution().observe(tomb['record'], deadline)
                result = {'vm':self._view(tomb, observation=observation)}
        else:
            fail('INVALID_SPEC','unsupported management action')
        return result

    def _read_states(self, action, spec, deadline):
        """원자 등록부/수명 기록을 읽는다. 부분 등록을 정상 전체 목록으로 만들지 않으며 Xen은 호출하지 않는다."""
        if self.p.clock()>=deadline:fail('DEADLINE_EXCEEDED','registry read budget expired')
        try:records=self.p.records(validate_assets=False)
        except ExecutionError:
            raise
        except (PlatformError,OSError,ValueError):
            fail('STORAGE_UNAVAILABLE','persistent storage or registry unavailable')
        tombs=self._tombs(deadline=deadline)
        # 전체 신원/디스크 중복은 위에서 검증했다. builder가 VM마다 같은 전체 dict를 재검색하지 않는다.
        by_uuid={record['uuid']:{key:record} for key,record in records.items()}
        if action=='get':
            states=[self._state(spec['vm_id'],by_uuid.get(spec['vm_id'],{}),tombstones=tombs)]
        else:
            # 기존 정렬된 등록 경로의 표시 순서를 유지하고 과거 수명 항목만 뒤에 붙인다.
            ids=list(dict.fromkeys([r['uuid'] for r in records.values()]+list(tombs)))
            lifecycle=spec.get('lifecycle','REGISTERED')
            if lifecycle in ('RETIRED','all'):
                for path in self._legacy_paths():
                    self._plain_dir(path)
                    record=self._read(path/'instance.json')
                    vm.validate_identity(record)
                    if record['uuid'] not in ids:ids.append(record['uuid'])
            states=[self._state(identity,by_uuid.get(identity,{}),tombstones=tombs) for identity in ids]
            states=[state for state in states if lifecycle=='all' or state['lifecycle']==lifecycle]
        if self.p.clock()>=deadline:fail('DEADLINE_EXCEEDED','registry read budget expired')
        return states,tombs

    def _query(self, action, spec, context, deadline):
        """읽기만 수행한다. 완료/소유권 관측은 재실행·정리·원 기한 갱신의 허가가 아니다."""
        for attempt in range(2):
            with span('read.round',count=attempt+1):
                if self.p.clock()>=deadline:fail('DEADLINE_EXCEEDED','query budget expired')
                try:
                    if action in ('get','list'):
                        read=ReadContext(self,action,spec,deadline)
                        records=[s['record'] for s in read.states]
                        if action=='get':
                            observed=read.e.observe(records[0],deadline,_read_context=read)
                            result={'vm':self._view(read.states[0],observation=observed)}
                        else:
                            observed=read.e.observe_many(records,deadline,_read_context=read)
                            # 빈 목록에서도 대상 집합·host 확인을 생략하지 않는다.
                            if not records:read.validate()
                            result={'vms':[self._view(s,observation=observed[s['vm_id']]) for s in read.states]}
                    else:
                        try:vm.storage_check(self.data)
                        except (OSError,PlatformError,ValueError):
                            fail('STORAGE_UNAVAILABLE','persistent storage unavailable')
                        selector='operation_id' if 'operation_id' in spec else 'idempotency_key'
                        selected=spec[selector]
                        def search():
                            if self.p.clock()>=deadline:fail('DEADLINE_EXCEEDED','operation search budget expired')
                            rows=(self._operations(deadline=deadline) if action=='operation' else
                                  self.p.execution().requests(deadline=deadline))
                            if self.p.clock()>=deadline:fail('DEADLINE_EXCEEDED','operation search budget expired')
                            found=[item for item in rows if item.get(selector)==selected]
                            if len(found)>1:fail('RECONCILE_REQUIRED','operation selector is ambiguous')
                            return found
                        first=search()
                        view=self._operation_view(first[0],deadline) if first and action=='operation' else None
                        # 별도 회차의 완전한 재확인이다. 첫 일치로 검색을 중단하지 않는다.
                        if search()!=first:raise ReadChanged()
                        if first and action=='operation' and self._operation_view(first[0],deadline)!=view:
                            raise ReadChanged()
                        if not first or ('vm_id' in spec and first[0]['vm_id']!=spec['vm_id']):
                            fail('VM_NOT_FOUND','operation not found')
                        op=first[0]
                        context['operation_id']=op['operation_id']
                        trace_bind(operation_id=op['operation_id'],vm_id=op['vm_id'])
                        if action=='operation':result=view
                        else:
                            try:host=self.p.boot_id_reader()
                            except (OSError,PlatformError,ValueError):host=None
                            same=(host==op['host_boot_id']) if host is not None else None
                            sampled=int(self.p.clock()*1000)
                            original_end=op.get('deadline_monotonic_ms')
                            result={'operation':op,'deadline_observation':{'host_boot_id':host,
                                'observed_monotonic_ms':sampled,'original_host_matches':same,
                                'execution_deadline_expired':(sampled>=original_end) if same and type(original_end) is int else None}}
                    if self.p.clock()>=deadline:fail('DEADLINE_EXCEEDED','query completed after budget')
                    return result
                except ReadChanged:
                    if attempt:raise
                except ManagementError as error:
                    if error.error['code']=='VM_NOT_FOUND' and self.p.clock()>=deadline:
                        fail('DEADLINE_EXCEEDED','query absence was not verified within budget')
                    raise
        raise AssertionError('bounded query round exhausted')

    def _call(self, action, spec=None, *, execute=False, idempotency_key=None, request_id=None, budget_ms=None, _output=None):
        """HTTP를 가정하지 않는다. 모든 응답은 동일 JSON envelope이고 stdout을 쓰지 않는다."""
        entered = self.p.clock()
        context = {'operation_id': None, 'replayed': False, 'write_operation': False,'entered':entered,'budget_ms':budget_ms}
        response={'schema_version':'1.0','ok':False,'request_id':request_id if self._text_token(request_id) else uuid4(),
                  'operation_id':None,'replayed':False,'data':None,'error':None}
        trace_bind(request_id=response['request_id'])
        try:
            if budget_ms is not None and (type(budget_ms) is not int or not 0<budget_ms<=(creation.MAX_CALL_MS if action=='create' else 105000)):
                fail('INVALID_SPEC','budget_ms must be 1..'+str(creation.MAX_CALL_MS if action=='create' else 105000))
            if budget_ms is not None and action not in ('create','operation-reconcile','start','stop','reboot','destroy','get','list','operation','control-operation','recovery-reconcile'):
                fail('INVALID_SPEC','budget_ms is supported only for control and bounded reads')
            spec = self._validate_call(action, spec, execute, idempotency_key, request_id)
            trace_bind(vm_id=spec.get('vm_id'), recovery_attempt_id=spec.get('recovery_attempt_id'))
            try:
                trace_bind(host_boot_id=self.p.boot_id_reader())
            except Exception:
                pass  # Optional trace identity does not replace required host guards.
            if action == 'snapshot':
                response.update(ok=True, data={'snapshot':self.p.execution().snapshot(spec['vm_id'], spec['max_age_ms'])})
                return response
            deadline = (spec['deadline_monotonic_ms']/1000 if action == 'recover_once' else
                        entered + (90 if action in ('start','stop','reboot','destroy') else
                                   spec.get('timeout_ms',15000)/1000))
            if action=='create':
                configured=creation.policy(self,required=False)
                deadline=entered+(budget_ms or (configured['call_ms'] if configured else 15000))/1000
            if budget_ms is not None:
                deadline=min(deadline,entered+budget_ms/1000)
                # 호출 예산은 spec/hash에 섞지 않고 원 물리 deadline도 갱신하지 않는다.
                self.p.response_deadline=entered+budget_ms/1000
            else:
                self.p.response_deadline=float('inf')
            if action=='create':self.p.response_deadline=deadline
            cpu_deadline = deadline
            trace_bind(deadline_monotonic_ms=int(deadline*1000))
            if action=='operation-reconcile':
                result=creation.reconcile(self,spec,execute,context,deadline,_output)
                return {**response,'ok':True,'data':result,'operation_id':context['operation_id']}
            if action in ('get','list','operation','control-operation'):
                self.p.response_deadline=min(self.p.response_deadline,deadline)
                if action in ('get','list'):
                    with observation_slot(self.p.runtime,deadline=deadline,clock=self.p.clock) as fd:
                        previous_fds=_output.fds
                        _output.fds=(*previous_fds,fd)
                        try:result=self._query(action,spec,context,deadline)
                        finally:_output.fds=previous_fds
                else:result=self._query(action,spec,context,deadline)
                return {**response,'ok':True,'data':result,'operation_id':context['operation_id']}
            if action == 'stop':
                self._accept_stop(spec,idempotency_key,deadline,context)
            if action in ('retire','restore','purge','update-resources','firmware-transition'):
                context['asset_lease'] = _output.stack.enter_context(
                    AssetLease(self.p, spec['vm_id'], action, idempotency_key))
                _output.stack.enter_context(lifecycle_slot(self.p))
            while True:
                try:
                    with operation_lock(self.p.runtime) as common_fd:
                        context['common_fd'] = common_fd
                        result=self._dispatch_locked(action,spec,execute,idempotency_key,context,deadline,cpu_deadline,entered)
                    break
                except NeedLinuxSlot:
                    context['write_operation']=False
                    if 'linux_slot' in context:
                        fail('RECOVERY_REQUIRED','preparation slot did not bind the original request')
                    if self.p.clock()>=deadline:
                        fail('DEADLINE_EXCEEDED','Linux preparation admission budget expired')
                    try:
                        context['linux_slot']=_output.stack.enter_context(LinuxSlot(self.p))
                    except OperationBusy:
                        # 동일 key가 사전 확인 직후 수락됐을 수 있다. 미수락을 확정하기 전에 재검사한다.
                        with operation_lock(self.p.runtime):
                            try:
                                result=self._dispatch_locked(action,spec,execute,idempotency_key,context,deadline,cpu_deadline,entered)
                            except NeedLinuxSlot:
                                context['write_operation']=False
                                details=({'operation_id':context['operation_id']} if context['operation_id'] else {'accepted':False})
                                fail('OPERATION_IN_PROGRESS','Linux preparation slot is busy; no new request accepted',
                                     retryable=True,details=details)
                        break
            if isinstance(result,PrivateCreate):
                result=self._complete_private(result,context)
            response.update(ok=True,data=result)
        except ManagementError as exc:
            response['error']=exc.error
        except ExecutionError as exc:
            response['error'] = exc.error
            context['observation_error_handled'] = getattr(exc, 'observation_error_handled', False)
            context['operation_id'] = exc.error['details'].get('operation_id', context['operation_id'])
            context['replayed'] = exc.error['details'].get('replayed', context['replayed'])
        except vm.DomainIdentityError as exc:
            response['error']={'code':'TARGET_CHANGED', 'message':str(exc),
                               'retryable':False, 'details':{}}
        except PlatformError as exc:
            busy='operation is in progress' in str(exc)
            completed=None
            replay_error=None
            if isinstance(exc,OperationBusy):
                try:completed=self._completed_busy_replay(action,spec,idempotency_key,context)
                except ManagementError as error:replay_error=error.error
                except (PlatformError,OSError,ValueError,KeyError,TypeError):pass
            if replay_error is not None:
                response['error']=replay_error
            elif completed is not None:
                response.update(ok=True,data=completed,error=None)
            else:
                response['error']={'code':'OPERATION_IN_PROGRESS' if busy else 'ARTIFACT_INVALID',
                                   'message':'another platform operation is in progress' if busy else str(exc),
                                   'retryable':busy,'details':{}}
        except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
            cleanup = action=='purge' and context['operation_id'] is not None
            response['error']={'code':'CLEANUP_INCOMPLETE' if cleanup else 'RECOVERY_REQUIRED' if context['operation_id'] else 'ARTIFACT_INVALID',
                               'message':type(exc).__name__+': management operation could not be completed',
                               'retryable':cleanup,'details':{'operation_id':context['operation_id']} if cleanup else {}}
        if not response['ok'] and context.get('stop_intent'):
            op=context['stop_intent']
            try:
                latest=self.p.execution().read(self.p.execution()._path(self.data,'control-requests',op['operation_id'])) or op
                current=self.p.execution().read(self.data/'control'/(op['vm_id']+'.json'))
                superseded=bool(current and current.get('accepted_operation_id')!=op['operation_id'])
            except (OSError,PlatformError,ValueError):
                latest=op;superseded=False
            pending=not superseded and latest['phase'] not in ('completed','failed')
            recorded_deadline=op.get('deadline_monotonic_ms')
            expired=self.p.clock()*1000>=recorded_deadline if type(recorded_deadline) is int else None
            response['error']['details'].update(accepted=True,completion='superseded' if superseded else 'pending' if pending else latest['phase'],
                operation_id=op['operation_id'],intent_revision=op['intent_revision'],phase=latest['phase'],
                host_boot_id=op['host_boot_id'],lifecycle_generation=op['lifecycle_generation'],
                deadline_monotonic_ms=recorded_deadline,
                execution_deadline_expired=expired,
                next_action=('inspect_original_operation_and_explicitly_request_stop_if_still_present' if pending and expired is not False else
                             'replay_original_stop_after_execution_lock_is_available' if pending else
                             'inspect_original_operation_and_current_intent'))
        if (action == 'probe' and not response['ok'] and not context.get('observation_error_handled')
                and isinstance(spec, dict) and isinstance(spec.get('vm_id'), str) and vm.UUID.fullmatch(spec['vm_id'])):
            try:
                self.p.execution().note_probe_error(spec['vm_id'], response['error'])
            except (PlatformError, OSError, ValueError) as diagnostic:
                response['error']['details'].setdefault('diagnostic_warnings', []).append(
                    {'code':'DIAGNOSTIC_UNAVAILABLE', 'sink':'observations', 'cause_type':type(diagnostic).__name__})
        if not response['ok'] and action in ('start', 'stop', 'reboot', 'destroy', 'recover_once') and isinstance(spec, dict):
            identity = spec.get('vm_id')
            if isinstance(identity, str) and vm.UUID.fullmatch(identity):
                try:
                    category = ('control-unconfirmed' if response['error']['code'] == 'RECONCILE_REQUIRED' else
                                'control-failed' if context['operation_id'] else 'request-rejected')
                    warning = self.p.execution().event(category, {'uuid':identity}, context['operation_id'],
                                             {'action':action, 'error_code':response['error']['code'],
                                              'recovery_attempt_id':spec.get('recovery_attempt_id'),
                                              'replayed':context['replayed']})
                    if warning:
                        response['error']['details'].setdefault('diagnostic_warnings', []).append(warning)
                except (OSError, PlatformError):
                    response['error']['details']['diagnostic_recording'] = 'unavailable'
        if response['ok'] and context.get('diagnostic_warnings'):
            response['data'].setdefault('diagnostic_warnings', []).extend(context['diagnostic_warnings'])
        response.update(operation_id=context['operation_id'],replayed=context['replayed'])
        trace_bind(operation_id=context['operation_id'])
        try:
            data = response['data'] if response['ok'] else {}
            view = data.get('vm') or {}
            observation = data.get('current_observation') or data.get('observation') or view
            trace_bind(execution_id=observation.get('execution_id'),
                       intent_revision=observation.get('intent_revision'),
                       lifecycle_generation=observation.get('lifecycle_generation',view.get('generation')))
        except Exception:
            pass  # Response diagnostics cannot reinterpret a confirmed operation.
        if (not response['ok'] and context['write_operation'] and execute
                and response['error']['code'] not in ('IDEMPOTENCY_CONFLICT','TARGET_CHANGED','VM_PURGED')):
            # 이전 성공 기록은 오래된 요청의 오류로 덮어쓰지 않는다.
            try:
                path=self.data/'operations'/(context['operation_id']+'.json')
                with operation_lock(self.p.runtime):
                    op=self._read(path)
                    owner=context.get('linux_slot')
                    if not (action=='create' and owner is not None and owner.owner_fd is not None
                            and self._owner_state(op)=='active'):
                        self._writable()
                    if op['status']!='SUCCEEDED':
                        op.update(status='RECOVERY_REQUIRED',error=op.get('error') or response['error'])
                        self._save_op(op)
            except (OSError,PlatformError,ValueError):
                if action=='create':
                    response['error']['message'] += '; operation failure was not confirmed durable; inspect the original operation'
        return response


def exit_code(response):
    if response['ok']:
        return 0
    code=response['error']['code']
    return 2 if code in VALIDATION else 3 if code in CONFLICTS else 4
