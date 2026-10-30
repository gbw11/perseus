"""플랫폼 실행 의도·수락 journal·현재 host의 실행/관측 문맥.

물리 실행·자산 변경은 operation_lock을 사용한다. 의도 수락과 최종 명령 제출은
별도의 짧은 admission 잠금으로 순서를 정한다. snapshot 읽기는 잠금/Xen을 호출하지 않는다.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time

from first_install_contract import uuid4
from xenova_runtime import PlatformError, atomic_write, ensure_runtime, sync_directory
import xenova_catalog as catalog
import xenova_vm as vm
from xenova_trace import bind as trace_bind, event as trace_event, traced


class ExecutionError(PlatformError):
    def __init__(self, code, message, *, retryable=False, details=None):
        self.error = {"code": code, "message": message, "retryable": retryable, "details": details or {}}
        super().__init__(message)


class ReadChanged(ExecutionError):
    """요청 안에서 한 번만 새 관측 회차로 재확인할 수 있는 메타데이터 변경."""
    def __init__(self):
        super().__init__('TARGET_CHANGED','read configuration or execution changed during observation')


def refuse(code, message, **kwargs):
    raise ExecutionError(code, message, **kwargs)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def host_boot_id():
    value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if not vm.UUID.fullmatch(value):
        refuse("OBSERVATION_UNAVAILABLE", "host boot identity unavailable")
    return value


@contextmanager
def short_lock(runtime, name, *, deadline=None, clock=time.monotonic, sleep=time.sleep):
    """장기 operation_lock 안에서는 이 잠금만 추가한다. 반대 순서로 기다리지 않는다."""
    root = ensure_runtime(runtime)/"locks"
    root.mkdir(mode=0o700, exist_ok=True)
    catalog.plain(root, directory=True)
    fd = os.open(root/(name+".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        import stat
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            refuse("RECONCILE_REQUIRED", "invalid admission/observation lock")
        while True:
            if deadline is not None and clock() >= deadline:
                refuse("DEADLINE_EXCEEDED", "short lock deadline expired", details={"lock":name})
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if deadline is None:
                    refuse("OPERATION_IN_PROGRESS", "short lock is busy", retryable=True, details={"lock":name})
                sleep(min(.005, max(0, deadline-clock())))
                continue
            if deadline is not None and clock() >= deadline:
                refuse("DEADLINE_EXCEEDED", "short lock acquired after deadline", details={"lock":name})
            break
        yield
    finally:
        os.close(fd)


class Execution:
    def __init__(self, platform):
        self.p = platform
        self.data, self.runtime = platform.data, platform.runtime
        self.host = platform.boot_id_reader()
        if not isinstance(self.host, str) or not vm.UUID.fullmatch(self.host):
            refuse("OBSERVATION_UNAVAILABLE", "host boot identity is not available")

    def _path(self, root, folder, key):
        if not isinstance(key, str) or not vm.UUID.fullmatch(key):
            refuse("INVALID_SPEC", "canonical UUID required")
        return root/folder/(key+".json")

    def read(self, path):
        if not path.exists() and not path.is_symlink():
            return None
        try:
            catalog.plain(path.parent, directory=True)
            catalog.plain(path)
            value = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            refuse("RECONCILE_REQUIRED", "invalid execution record", details={"record":path.name})
        if not isinstance(value, dict) or value.get("schema_version") != "1.0":
            refuse("RECONCILE_REQUIRED", "execution record schema mismatch")
        return value

    def write(self, path, value):
        # Never make a replacement /srv data root. Callers already checked its mount.
        catalog.plain(path.parent.parent, directory=True)
        if not path.parent.exists() and not path.parent.is_symlink():
            path.parent.mkdir(mode=0o700)
            sync_directory(path.parent.parent)
        catalog.plain(path.parent, directory=True)
        if path.exists() or path.is_symlink():
            catalog.plain(path)
        atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2)+"\n")

    def checkpoint(self, phase, value):
        self.p.execution_checkpoint(phase, value)

    def lifecycle(self, record):
        tomb = vm.read_tombstones(self.data).get(record["uuid"])
        return (tomb["lifecycle"], tomb["generation"]) if tomb else ("REGISTERED", 0)

    def intent(self, record):
        value = self.read(self._path(self.data, "control", record["uuid"]))
        if value is not None and (
                value.get("vm_id") != record["uuid"] or value.get("desired_state") not in ("running", "stopped")
                or type(value.get("intent_revision")) is not int or value["intent_revision"] < 1
                or type(value.get("lifecycle_generation")) is not int or value["lifecycle_generation"] < 0
                or not isinstance(value.get("accepted_operation_id"), str)
                or not vm.UUID.fullmatch(value["accepted_operation_id"])):
            refuse("RECONCILE_REQUIRED", "intent identity/revision mismatch")
        return value

    def initialize(self, record, desired, source, operation_id=None):
        with short_lock(self.runtime, 'admission'):
            return self._initialize_locked(record, desired, source, operation_id)

    def _initialize_locked(self, record, desired, source, operation_id):
        current = self.intent(record)
        if current is not None:
            return current
        lifecycle, generation = self.lifecycle(record)
        if lifecycle != "REGISTERED" or desired not in ("running", "stopped") or source not in ("create", "factory"):
            refuse("INVALID_SPEC", "invalid intent initialization")
        value = {"schema_version":"1.0", "vm_id":record["uuid"], "desired_state":desired,
                 "intent_revision":1, "lifecycle_generation":generation, "accepted_host_boot_id":self.host,
                 "accepted_operation_id":operation_id or uuid4(), "source":source}
        self.write(self._path(self.data, "control", record["uuid"]), value)
        self.checkpoint("intent-initialized", value)
        return value

    def barrier(self):
        value = self.read(self.runtime/"admission"/"host.json")
        if value is not None and (
                not vm.UUID.fullmatch(value.get("host_boot_id", "")) or
                not vm.UUID.fullmatch(value.get("barrier_id", "")) or
                value.get("state") not in ("quiescing", "open")):
            refuse("RECONCILE_REQUIRED", "invalid host admission barrier")
        return value if value and value["host_boot_id"] == self.host else None

    def gate(self):
        barrier = self.barrier()
        if (self.runtime/"shutdown-requested").exists() or (self.runtime/"shutdown-requested").is_symlink():
            refuse("HOST_QUIESCING", "board shutdown is in progress; VM start refused", retryable=True)
        if barrier and barrier["state"] == "quiescing":
            refuse("HOST_QUIESCING", "host admission is closed", retryable=True,
                   details={"barrier_id":barrier["barrier_id"], "host_boot_id":self.host})

    def initialize_host(self):
        with short_lock(self.runtime, "admission"):
            current = self.barrier()
            if current:
                return current
            value = {"schema_version":"1.0", "host_boot_id":self.host, "barrier_id":uuid4(),
                     "state":"open", "action":"host-boot", "monotonic_ms":int(self.p.clock()*1000)}
            self.write(self.runtime/"admission"/"host.json", value)
            return value

    def quiesce(self, action):
        with short_lock(self.runtime, "admission"):
            current = self.barrier()
            if current and current["state"] == "quiescing":
                return current
            value = {"schema_version":"1.0", "host_boot_id":self.host, "barrier_id":uuid4(),
                     "state":"quiescing", "action":action, "monotonic_ms":int(self.p.clock()*1000)}
            self.write(self.runtime/"admission"/"host.json", value)
        warning = self.event("host-quiescing", details=value)
        return {**value, **({"diagnostic_warnings":[warning]} if warning else {})}

    def resume_host(self, barrier_id):
        with short_lock(self.runtime, "admission"):
            value = self.barrier()
            if not value or value["state"] != "quiescing" or value["barrier_id"] != barrier_id:
                refuse("TARGET_CHANGED", "host barrier changed")
            if (self.runtime/"shutdown-requested").exists() or (self.runtime/"shutdown-requested").is_symlink():
                refuse("HOST_QUIESCING", "finish or investigate the existing shutdown request first")
            value = {**value, "state":"open", "monotonic_ms":int(self.p.clock()*1000)}
            self.write(self.runtime/"admission"/"host.json", value)
        warning = self.event("host-admission-resumed", details=value)
        return {**value, **({"diagnostic_warnings":[warning]} if warning else {})}

    def admission(self, record):
        value = self.read(self._path(self.runtime, "boot-admission", record["uuid"]))
        if value and (value.get("vm_id") != record["uuid"] or type(value.get("allowed")) is not bool):
            refuse("RECONCILE_REQUIRED", "invalid VM admission")
        return value if value and value.get("host_boot_id") == self.host else None

    def set_admission(self, record, allowed, source, reason):
        lifecycle, generation = self.lifecycle(record)
        intent = self.intent(record)
        value = {"schema_version":"1.0", "vm_id":record["uuid"], "host_boot_id":self.host,
                 "lifecycle_generation":generation, "intent_revision":intent["intent_revision"] if intent else None,
                 "allowed":allowed, "source":source, "reason":reason, "lifecycle":lifecycle}
        self.write(self._path(self.runtime, "boot-admission", record["uuid"]), value)
        return value

    def policy(self):
        file = self.p.execution_policy_path
        if not file.exists() and not file.is_symlink():
            # Absence does not approve either unresolved operational policy.
            return {"schema_version":"1.0", "autostart_stopped":None, "manual_running":None,
                    "force_restart_vm_ids":[]}
        value = self.read(file)
        if set(value) != {"schema_version", "autostart_stopped", "manual_running", "force_restart_vm_ids"}:
            refuse("RECONCILE_REQUIRED", "invalid execution policy fields")
        if file.stat().st_mode & 0o022:
            refuse("RECONCILE_REQUIRED", "execution policy must not be group/world writable")
        for key in ("autostart_stopped", "manual_running"):
            if value[key] not in (None, "start", "keep-stopped"):
                refuse("RECONCILE_REQUIRED", "invalid boot policy decision")
        ids = value["force_restart_vm_ids"]
        if (not isinstance(ids, list) or any(not isinstance(i, str) or not vm.UUID.fullmatch(i) for i in ids)
                or len(set(ids)) != len(ids)):
            refuse("RECONCILE_REQUIRED", "invalid force-restart approval list")
        return value

    def boot_decision(self, record):
        """현재 intent와 이번 host admission을 대조하고 승인된 boot policy를 적용한다."""
        intent = self.intent(record)
        lifecycle, generation = self.lifecycle(record)
        applicable = intent is not None and intent["lifecycle_generation"] == generation and lifecycle == "REGISTERED"
        prior = self.admission(record)
        explicit = applicable and intent.get("source") == "user" and intent.get("accepted_host_boot_id") == self.host
        if explicit:
            allowed = intent["desired_state"] == "running"
            matching = (prior and prior.get("source") in ("user-start", "user-stop", "user-reboot")
                        and prior.get("lifecycle_generation") == generation
                        and prior.get("intent_revision") == intent["intent_revision"]
                        and prior["allowed"] == allowed)
            if not matching:
                journal = self.read(self._path(self.data, "control-requests", intent["accepted_operation_id"]))
                request = journal.get("request", {}) if journal else {}
                valid = (journal and journal.get("operation_id") == intent["accepted_operation_id"]
                         and journal.get("request_hash") == fingerprint(request)
                         and journal.get("vm_id") == record["uuid"] and request.get("vm_id") == record["uuid"]
                         and journal.get("host_boot_id") == self.host
                         and journal.get("lifecycle_generation") == generation
                         and journal.get("intent_revision") == intent["intent_revision"]
                         and journal.get("desired_state") == intent["desired_state"]
                         and request.get("action") in ("start", "stop", "destroy", "reboot"))
                if not valid:
                    prior = self.set_admission(record, False, "intent-reconciliation", "INTENT_RECONCILIATION_REQUIRED")
                    return {"start":False, "admission":prior, "reason":prior["reason"]}
                source = "user-stop" if not allowed else "user-reboot" if request["action"] == "reboot" else "user-start"
                prior = self.set_admission(record, allowed, source, "COMMITTED_INTENT_RECONCILED")
            return {"start":allowed and record["autostart"], "admission":prior,
                    "reason":"same-host-explicit-intent"}
        if not applicable:
            admission = self.set_admission(record, False, "boot-policy", "INTENT_UNSET_OR_INAPPLICABLE")
            return {"start":record["autostart"], "admission":admission, "reason":admission["reason"]}
        desired = intent["desired_state"]
        conflict = ("autostart_stopped" if record["autostart"] and desired == "stopped"
                    else "manual_running" if not record["autostart"] and desired == "running" else None)
        decision = self.policy()[conflict] if conflict else None
        if conflict and decision is None:
            admission = self.set_admission(record, False, "boot-policy", "BOOT_POLICY_PENDING")
            return {"start":record["autostart"], "admission":admission, "reason":"BOOT_POLICY_PENDING"}
        start = record["autostart"] if not conflict else decision == "start"
        if start and desired != "running":
            # This branch is reachable only through an explicit root policy choice.
            request = {"vm_id":record["uuid"], "action":"start", "force":False}
            approved = self.accept(record, request, "approved-boot:"+self.host+":"+record["uuid"], source="boot-policy")
            approved["result"] = {"accepted":True, "boot_policy_decision":"start", "physical_execution":"autostart"}
            self.save_request(approved, "completed")
        admission = self.set_admission(record, start, "boot-policy", "APPROVED" if conflict else "POLICY_MATCH")
        return {"start":start, "admission":admission, "reason":admission["reason"]}

    def run_record(self, record):
        value = self.read(self._path(self.runtime, "executions", record["uuid"]))
        if value and (value.get("vm_id") != record["uuid"] or not vm.UUID.fullmatch(value.get("execution_id", ""))):
            refuse("RECONCILE_REQUIRED", "invalid execution identity")
        return value if value and value.get("host_boot_id") == self.host else None

    def unique(self, row, deadline, *, allow_dom0=False):
        output = self.p.timed_command(["xenova-domain-identity", "--domid", str(row["domid"])]+(["--allow-dom0"] if allow_dom0 else []), deadline)
        try:
            value = json.loads(output)
        except ValueError:
            refuse("OBSERVATION_UNAVAILABLE", "domain incarnation response is not JSON")
        unique = value.get("xen_unique_id") if isinstance(value, dict) else None
        if (not isinstance(unique, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", unique)
                or int(unique) > 2**64-1 or value.get("domid") != row["domid"]
                or type(value.get("state_bits")) is not int or not value["state_bits"] & 1):
            refuse("OBSERVATION_UNAVAILABLE", "domain incarnation response is invalid")
        return unique

    def require_launch_available(self, record):
        previous = self.run_record(record)
        if previous and previous.get("phase") in ("launching", "uncertain"):
            refuse("RECONCILE_REQUIRED", "an earlier launch may still exist; administrator reconciliation required")

    @traced('execution.prepare_launch')
    def prepare_launch(self, record, owner=None, *, deadline=None, expected_cpu=None, cpu_evidence=None,
                       reuse_process_scope=False):
        self.require_launch_available(record)
        _, generation = self.lifecycle(record)
        intent = self.intent(record)
        if owner:
            if owner.get('kind') == 'user':
                accepted = self.read(self._path(self.data, 'control-requests', owner['operation_id']))
            elif owner.get('kind') == 'recovery':
                accepted = self.read(self._path(self.data, 'recovery-attempts', owner['recovery_attempt_id']))
            else:
                accepted = None
            if accepted:
                expected_cpu = accepted.get('cpu_policy', {'configured':False})
        end = deadline if deadline is not None else self.p.clock()+15
        cpu = self.p.cpu()
        cpu_context = (cpu.consume(record, end, cpu_evidence, expected=expected_cpu) if cpu_evidence is not None
                       else cpu.require(record, end, expected=expected_cpu))
        value = {"cpu_policy":cpu_context, "schema_version":"1.0", "vm_id":record["uuid"], "host_boot_id":self.host,
                 "execution_id":uuid4(), "lifecycle_generation":generation,
                 "intent_revision":intent["intent_revision"] if intent else None,
                 'intent_sha256':fingerprint(intent), 'record_sha256':fingerprint(record),
                 "phase":"launching", "owner":owner, "prepared_monotonic_ms":int(self.p.clock()*1000),
                 "configuration_sha256":fingerprint(self.p.config(record)), "domid":None, "xen_unique_id":None}
        if vm.guest_kind(record) == 'zephyr':
            # Capture the verified loader input before submission, never hash the
            # latest file later and label it as an already running execution.
            from xenova_zephyr import active_directory, bundle_info
            directory=self.data/'instances'/record['id']
            info=bundle_info(active_directory(directory,record),record['active_firmware']['revision'])
            value['loaded_artifact']={'file':'zephyr.bin','artifact_sha256':info['sha256'],
                'manifest_sha256':info['revision'],'build_id':info['source'].get('build_id'),
                'display_name':record.get('display_name'),'hostname':record.get('hostname'),
                'internal_name':record['name']}
        if self.p.process_isolation:
            from xenova_guest_process import scope_name
            previous=self.run_record(record)
            # toolstack의 Linux reboot는 기존 monitor를 쓴다. 구형 실행에 scope가
            # 없었다면 새 scope에 들어갔다고 기록하지 않고 다음 명시적 create까지 유지한다.
            value['process_scope']=((previous or {}).get('process_scope') if reuse_process_scope
                                    else scope_name(record['uuid'],value['execution_id']))
        trace_bind(vm_id=record['uuid'], host_boot_id=self.host, execution_id=value['execution_id'],
                   intent_revision=value['intent_revision'], lifecycle_generation=generation,
                   configuration_sha256=value['configuration_sha256'],
                   policy_revision=cpu_context.get('policy_revision'))
        self.write(self._path(self.runtime, "executions", record["uuid"]), value)
        self.checkpoint("launch-prepared", value)
        return value

    @traced('execution.guard_launch')
    def guard_launch(self, record, value, deadline):
        """직전 조치 guard. 관측 근거와 별개로 현재 의도·구성·정책을 재확인한다."""
        if self.p.clock() >= deadline:
            refuse('DEADLINE_EXCEEDED', 'launch permission expired before physical submission')
        if self.p.boot_id_reader() != self.host or value.get('host_boot_id') != self.host:
            refuse('TARGET_CHANGED', 'launch host changed')
        self.gate()
        records = self.p.records(validate_assets=False)
        current = records.get(record['id'])
        lifecycle, generation = self.lifecycle(record)
        intent = self.intent(record)
        owner = value.get('owner') or {}
        # 기존 설치의 BOOT_POLICY_PENDING 호환 실행은 유지한다. 이번 host의 사용자
        # stop과 소유 작업의 running 조건은 별개이며, 새 revision은 아래에서 항상 거절한다.
        running_required=bool(owner) or bool(intent and intent.get('source')=='user' and
                                            intent.get('accepted_host_boot_id')==self.host)
        if owner.get('kind') == 'user' and (not intent or intent['accepted_operation_id'] != owner.get('operation_id')):
            refuse('INTENT_MISMATCH', 'launch owner no longer owns the accepted intent')
        if (current != record or lifecycle != 'REGISTERED' or generation != value['lifecycle_generation'] or
                (intent['intent_revision'] if intent else None) != value['intent_revision'] or
                ('intent_sha256' in value and fingerprint(intent) != value['intent_sha256']) or
                ('record_sha256' in value and fingerprint(record) != value['record_sha256']) or
                (running_required and intent is not None and intent['desired_state'] != 'running') or self.run_record(record) != value):
            refuse('TARGET_CHANGED', 'launch record, lifecycle, intent or owner changed')
        if fingerprint(self.p.config(record)) != value['configuration_sha256']:
            refuse('TARGET_CHANGED', 'launch configuration changed')
        if value.get('loaded_artifact'):
            from xenova_zephyr import active_directory,bundle_info
            info=bundle_info(active_directory(self.data/'instances'/record['id'],record),record['active_firmware']['revision'])
            loaded=value['loaded_artifact']
            if info['sha256']!=loaded['artifact_sha256'] or info['revision']!=loaded['manifest_sha256']:
                refuse('TARGET_CHANGED','verified loader artifact changed before submission/binding')
        if self.p.cpu().context(record) != value.get('cpu_policy', {'configured':False}):
            refuse('TARGET_CHANGED', 'launch CPU policy changed')
        if self.p.clock() >= deadline:
            refuse('DEADLINE_EXCEEDED', 'launch validation exhausted the original execution deadline')

    @traced('execution.bind_launch')
    def bind_launch(self, record, value, row, deadline):
        if value["host_boot_id"] != self.host:
            refuse("TARGET_CHANGED", "launch belongs to a previous host boot")
        current = self.p.resolve_domain(record, expected=row, deadline=deadline)
        unique = self.unique(current, deadline)
        self.p.resolve_domain(record, expected={**row, 'xen_unique_id':unique}, deadline=deadline)
        value = {**value, "phase":"observed", "domid":current["domid"], "xen_unique_id":unique,
                 "observed_monotonic_ms":int(self.p.clock()*1000)}
        self.write(self._path(self.runtime, "executions", record["uuid"]), value)
        self.checkpoint("launch-observed", value)
        try:
            self.guard_launch(record, value, deadline)
            self.p.cpu().require(record, deadline, expected=value.get('cpu_policy', {'configured':False}), present=True, expected_identity=value)
            self.guard_launch(record, value, deadline)
            # A complete CPU observation checks incarnation too, but an unconfigured
            # policy deliberately does not query Xen. Keep this target guard in both
            # modes before publishing the execution as the final operation evidence.
            current = self.p.resolve_domain(record, expected={**current, 'xen_unique_id':unique}, deadline=deadline)
            value['cpu_verification'] = 'complete'
        except (ExecutionError, vm.DomainIdentityError) as error:
            cause = getattr(error, 'error', {'code':'TARGET_CHANGED', 'message':str(error),
                                             'retryable':False, 'details':{}})
            value.update(cpu_verification='incomplete', cpu_error=cause)
            self.write(self._path(self.runtime, 'executions', record['uuid']), value)
            raise ExecutionError('CPU_APPLY_INCOMPLETE', 'domain exists; CPU verification incomplete; no automatic repeat',
                                 details={'cause':cause, 'domid':value['domid'], 'execution_id':value['execution_id']}) from error
        self.write(self._path(self.runtime, 'executions', record['uuid']), value)
        # The owner consumes this phase's mandatory identity/CPU observation. Building
        # its response must not start another complete Xen observation after binding.
        observation = self.publish_observation(record, current, unique, deadline)
        return {**value, 'observation':observation}

    def abort_unsubmitted_launch(self, record, value, deadline):
        """실행 잠금 소유 경로에서 명령 exec 이전 거절에만 사용하는 종결 기록이다."""
        if self.run_record(record) != value or value.get('phase') != 'launching':
            refuse('RECONCILE_REQUIRED','unsubmitted launch owner changed')
        # 만료 뒤에는 원 실행의 부재 확인·기록 종결만 한다. 제출 권한을 연장하지 않는다.
        end=min(deadline if self.p.clock()<deadline else self.p.clock()+15,
                getattr(self.p,'response_deadline',float('inf')))
        if self.p.matching(record,self.p.domains(deadline=end)):
            refuse('RECONCILE_REQUIRED','domain exists after pre-submission rejection')
        self.write(self._path(self.runtime,'executions',record['uuid']),
                   {**value,'phase':'stopped','launch_aborted_before_submit':True,
                    'stopped_monotonic_ms':int(self.p.clock()*1000)})

    def end_run(self, record):
        value = self.run_record(record)
        if value:
            # Absence does not prove that an interrupted xl create child has finished.
            phase = "uncertain" if value.get("phase") in ("launching", "uncertain") else "stopped"
            self.write(self._path(self.runtime, "executions", record["uuid"]),
                       {**value, "phase":phase, "stopped_monotonic_ms":int(self.p.clock()*1000)})

    def note_probe_error(self, identity, error, *, _started_ms=None):
        """진단 sink의 실패는 원래 오류를 가리지 않는다. 추가 잠금 대기는 하지 않는다."""
        try:
            if _started_ms is None:self.probe_error(identity, error["code"])
            else:self.probe_error(identity,error['code'],_started_ms=_started_ms)
        except (PlatformError, OSError, ValueError) as diagnostic:
            error["details"].setdefault("diagnostic_warnings", []).append(
                {"code":"DIAGNOSTIC_UNAVAILABLE", "sink":"observations", "cause_type":type(diagnostic).__name__})

    @traced('execution.observe')
    def observe(self, record, deadline, *, _read_context=None):
        """실패를 보존하고 두 목록/unique_id를 대조한다. 실패는 부재가 아니다."""
        try:
            return self._observe(record, deadline) if _read_context is None else self._observe_many([record],deadline,_read_context=_read_context)[record['uuid']]
        except ExecutionError as error:
            failure = error
        except vm.DomainIdentityError as error:
            failure = ExecutionError("TARGET_CHANGED", str(error))
        except (PlatformError, OSError, ValueError) as error:
            expired=_read_context is not None and self.p.clock()>=deadline
            failure = ExecutionError('DEADLINE_EXCEEDED' if expired else "OBSERVATION_UNAVAILABLE", "Xen observation could not be verified",
                                     details={"cause_type":type(error).__name__})
        if _read_context is None:self.note_probe_error(record['uuid'],failure.error)
        else:self.note_probe_error(record['uuid'],failure.error,_started_ms=_read_context.started_ms)
        failure.observation_error_handled = True
        raise failure

    def _observe(self, record, deadline):
        return self._observe_many([record], deadline)[record['uuid']]

    @traced('execution.observe_many')
    def observe_many(self, records, deadline, *, _lifecycles=None, _read_context=None):
        """명시 목록 조회의 전체 예산과 두 목록을 공유한다. 부분 성공은 반환하지 않는다."""
        records = list(records)
        try:
            return self._observe_many(records, deadline, _lifecycles=_lifecycles,_read_context=_read_context)
        except ExecutionError as error:
            failure = error
        except vm.DomainIdentityError as error:
            failure = ExecutionError('TARGET_CHANGED', str(error))
        except (PlatformError, OSError, ValueError) as error:
            expired=_read_context is not None and self.p.clock()>=deadline
            failure = ExecutionError('DEADLINE_EXCEEDED' if expired else 'OBSERVATION_UNAVAILABLE', 'Xen list observation could not be verified',
                                     details={'cause_type':type(error).__name__})
        for record in records:
            if _read_context is None:self.note_probe_error(record['uuid'],failure.error)
            else:self.note_probe_error(record['uuid'],failure.error,_started_ms=_read_context.started_ms)
        failure.observation_error_handled = True
        raise failure

    def _observe_many(self, records, deadline, *, _lifecycles=None, _read_context=None):
        if not records:
            return {}
        # 기존 내부 호출의 명시 수명 근거를 검증한다. 일반 조회는 별도 ReadContext를 사용하며,
        # 제어 경로는 조회 문맥을 받지 않고 현재 수명/실행 guard를 유지한다.
        if _lifecycles is not None and (not isinstance(_lifecycles, dict)
                or set(_lifecycles) != {r['uuid'] for r in records}
                or any(not isinstance(v, tuple) or len(v) != 2
                       or v[0] not in ('REGISTERED','RETIRED','PURGING','PURGED')
                       or type(v[1]) is not int or v[1] < 0 for v in _lifecycles.values())):
            refuse('RECONCILE_REQUIRED', 'invalid internal list lifecycle snapshot')
        if self.p.boot_id_reader() != self.host:
            refuse('TARGET_CHANGED', 'observation belongs to a previous host boot')
        before = self.p.removal_snapshot(deadline)
        # 화면 조회는 완전한 UUID/name/state 목록을 공유한다. 효과 제출 guard의 domains는 별개다.
        domains = ([{**row,'config':{'c_info':{'uuid':row['uuid'],'name':row['name']}}}
                    for row in before.values()] if _read_context is not None else self.p.domains(deadline=deadline))
        targets = []
        for record in records:
            rows = self.p.matching(record, domains)
            row = rows[0] if rows else None
            targets.append((record, row, self.unique(row, deadline) if row else None))
        after = self.p.removal_snapshot(deadline)
        absence_verified_ms=int(self.p.clock()*1000) if _read_context is not None else None
        verified = []
        for record, row, unique in targets:
            expected = (row['domid'], record['uuid'], record['name']) if row else None
            for snapshot in (before, after):
                candidates = [item for item in snapshot.values() if item['uuid'] == record['uuid'] or item['name'] == record['name']]
                actual = (candidates[0]['domid'], candidates[0]['uuid'], candidates[0]['name']) if len(candidates) == 1 else None
                if len(candidates) > 1 or actual != expected:
                    refuse('TARGET_CHANGED', 'domain changed during observation')
            if row and self.unique(row, deadline) != unique:
                refuse('TARGET_CHANGED', 'domain incarnation changed during observation')
            if row:
                row = {**row, 'xen_state':after[row['domid']]['xen_state'],
                       'xen_state_flags':after[row['domid']]['xen_state_flags']}
            if _read_context is not None:
                _read_context.observed_ms[record['uuid']]=int(self.p.clock()*1000) if row else absence_verified_ms
            verified.append((record, row, unique))
        if self.p.boot_id_reader() != self.host:
            refuse('TARGET_CHANGED', 'host boot changed during observation')
        if _read_context is not None:
            _read_context.validate()
            return {record['uuid']:self.publish_observation(record,row,unique,deadline,
                    _lifecycle=_read_context.lifecycles[record['uuid']],_read_context=_read_context)
                    for record,row,unique in verified}
        if _lifecycles is None:
            return {record['uuid']:self.publish_observation(record, row, unique, deadline)
                    for record, row, unique in verified}
        return {record['uuid']:self.publish_observation(record, row, unique, deadline,
                                                       _lifecycle=_lifecycles[record['uuid']])
                for record, row, unique in verified}

    @traced('execution.publish_observation')
    def observation_inputs(self, record, lifecycle):
        """외부 관측과 분리한 원자 JSON 입력. 공유 memo나 새 권위 원본이 아니다."""
        return {'lifecycle':lifecycle,'run':self.run_record(record),'intent':self.intent(record),
                'admission':self.admission(record),'barrier':self.barrier(),
                'shutdown_requested':(self.runtime/'shutdown-requested').exists()}

    def publish_observation(self, record, row, unique, deadline, *, _lifecycle=None, _read_context=None):
        """확인된 한 단계의 사실을 게시한다. 이 함수는 Xen을 조회하지 않는다."""
        lifecycle, generation = self.lifecycle(record) if _lifecycle is None else _lifecycle
        inputs=_read_context.inputs[record['uuid']] if _read_context is not None else None
        run = inputs['run'] if inputs is not None else self.run_record(record)
        managed = bool(row and run and run.get("phase") == "observed" and
                       run.get("domid") == row["domid"] and run.get("xen_unique_id") == unique and
                       run.get("lifecycle_generation") == generation and lifecycle == "REGISTERED")
        intent = inputs['intent'] if inputs is not None else self.intent(record)
        admission = inputs['admission'] if inputs is not None else self.admission(record)
        observation = {"configuration_revision":fingerprint(record),
                       "desired_state":intent["desired_state"] if intent else "unknown",
                       "intent_revision":intent["intent_revision"] if intent else None,
                       "intent_applicable":bool(intent and lifecycle == "REGISTERED" and intent["lifecycle_generation"] == generation),
                       "boot_admission":admission, "presence":"present" if row else "absent", "xen_state":row["xen_state"] if row else "stopped",
                        "xen_state_flags":row["xen_state_flags"] if row else None,
                       "domid":row["domid"] if row else None, "xen_unique_id":unique,
                       "execution_id":run["execution_id"] if managed else None,
                       "execution_status":"managed" if managed else "unmanaged" if row else "absent",
                       "lifecycle_generation":generation, "app_readiness":"unknown"}
        path = self._path(self.runtime, "observations", record["uuid"])
        with short_lock(self.runtime, "observation-"+record["uuid"], deadline=deadline,
                        clock=self.p.clock, sleep=self.p.sleep):
            if _read_context is not None:
                _read_context.validate_one(record['uuid'])
            previous = self.read(path)
            last = previous.get("last_success") if previous and previous.get("host_boot_id") == self.host else None
            revision = previous["observation_revision"] if last else 0
            sampled=_read_context.observed_ms.get(record['uuid']) if _read_context is not None else int(self.p.clock()*1000)
            if type(sampled) is not int:raise ValueError('verified observation clock missing')
            newer=False
            if _read_context is not None and previous and previous.get('host_boot_id')==self.host:
                point=(previous.get('last_attempt_monotonic_ms') if previous.get('last_error') is not None
                       else last.get('monotonic_ms') if last else None)
                newer=type(point) is int and point>sampled
                if newer and (not last or last['value']!=observation or previous.get('last_error') is not None):
                    raise ReadChanged()
            if last is None or fingerprint(last["value"]) != fingerprint(observation):
                revision += 1
            value = {"schema_version":"1.0", "vm_id":record["uuid"], "host_boot_id":self.host,
                     "observation_revision":revision, "last_success":{"monotonic_ms":sampled,
                     "value":observation}, "last_error":None, "last_attempt_monotonic_ms":int(self.p.clock()*1000)}
            if self.p.clock() >= deadline:
                refuse("DEADLINE_EXCEEDED", "observation publication deadline expired")
            if not newer:self.write(path, value)
        if self.p.clock() >= deadline:
            refuse("DEADLINE_EXCEEDED", "observation completed after deadline")
        description = {"desired_state":intent['desired_state'] if intent else 'unknown',
                       'intent_revision':intent['intent_revision'] if intent else None,
                       'intent_applicable':observation['intent_applicable'],
                       'intent_lifecycle_generation':intent['lifecycle_generation'] if intent else None,
                       'host_boot_id':self.host, 'boot_admission':admission,
                       'host_admission':inputs['barrier'] if inputs is not None else self.barrier(),
                       'shutdown_requested':inputs['shutdown_requested'] if inputs is not None else (self.runtime/'shutdown-requested').exists(),
                       'execution_id':observation['execution_id'],
                       'execution_context':run if managed else None, 'observation_revision':revision}
        return {**observation, "observation_revision":revision, "host_boot_id":self.host,
                "observed_monotonic_ms":value["last_success"]["monotonic_ms"], '_description':description}

    def probe_error(self, identity, code, *, _started_ms=None):
        path = self._path(self.runtime, "observations", identity)
        with short_lock(self.runtime, "observation-"+identity):
            previous = self.read(path)
            if previous and previous.get('host_boot_id')==self.host and _started_ms is not None:
                last=previous.get('last_success')
                point=(previous.get('last_attempt_monotonic_ms') if previous.get('last_error') is not None
                       else last.get('monotonic_ms') if last else None)
                if type(point) is int and point>_started_ms:return
            value = previous if previous and previous.get("host_boot_id") == self.host else {
                "schema_version":"1.0", "vm_id":identity, "host_boot_id":self.host,
                "observation_revision":0, "last_success":None}
            self.write(path, {**value, "last_error":{"code":code},
                             "last_attempt_monotonic_ms":int(self.p.clock()*1000)})

    def snapshot(self, identity, max_age_ms):
        if type(max_age_ms) is not int or not 0 < max_age_ms <= 120000:
            refuse("INVALID_SPEC", "max_age_ms must be 1..120000")
        value = self.read(self._path(self.runtime, "observations", identity))
        last = value.get("last_success") if value else None
        if value and (value.get("vm_id") != identity or
                      type(value.get("observation_revision")) is not int or
                      value["observation_revision"] < 0 or
                      not vm.UUID.fullmatch(value.get("host_boot_id", ""))):
            refuse("RECONCILE_REQUIRED", "invalid observation snapshot identity")
        if last is not None and (not isinstance(last, dict) or type(last.get("monotonic_ms")) is not int
                                 or not isinstance(last.get("value"), dict)):
            refuse("RECONCILE_REQUIRED", "invalid observation snapshot payload")
        age = int(self.p.clock()*1000)-last["monotonic_ms"] if last else None
        stale = not last or value.get("host_boot_id") != self.host or age < 0 or age > max_age_ms
        unknown = stale or value.get("last_error") is not None
        return {"vm_id":identity, "host_boot_id":self.host, "observation_revision":value.get("observation_revision") if value else None,
                "host_admission":self.barrier(), "shutdown_requested":(self.runtime/"shutdown-requested").exists(),
                "stale":bool(stale), "unknown":bool(unknown), "age_ms":age,
                "last_success":last, "last_error":value.get("last_error") if value else None,
                "last_attempt_monotonic_ms":value.get("last_attempt_monotonic_ms") if value else None,
                "current":None if unknown else last["value"]}

    def describe(self, record, generation, lifecycle, observation=None):
        intent = self.intent(record)
        applicable = bool(intent and lifecycle == "REGISTERED" and intent["lifecycle_generation"] == generation)
        run = self.run_record(record)
        return {"desired_state":intent["desired_state"] if intent else "unknown",
                "intent_revision":intent["intent_revision"] if intent else None,
                "intent_applicable":applicable, "intent_lifecycle_generation":intent["lifecycle_generation"] if intent else None,
                "host_boot_id":self.host, "boot_admission":self.admission(record),
                "host_admission":self.barrier(), "shutdown_requested":(self.runtime/"shutdown-requested").exists(),
                "execution_id":observation["execution_id"] if observation else None,
                "execution_context":run if observation and observation["execution_status"] == "managed" else None,
                "observation_revision":observation["observation_revision"] if observation else None}

    def requests(self, *, deadline=None):
        """Complete journal scan within the caller's existing observation budget.

        A partial scan never proves absence or uniqueness. This deadline is not
        written into any original operation and grants no physical execution.
        """
        from xenova_management import Manager
        def check():
            if deadline is not None and self.p.clock()>=deadline:
                refuse('DEADLINE_EXCEEDED','control request scan budget expired')
        check()
        root = self.data/"control-requests"
        try:
            root.lstat()
        except FileNotFoundError:
            check()
            return []
        except OSError:
            refuse('OBSERVATION_UNAVAILABLE', 'control request directory could not be inspected')
        catalog.plain(root, directory=True)
        # Path.glob suppresses enumeration OSError on supported Python versions.
        # A partially read directory cannot establish absence or uniqueness.
        try:
            with os.scandir(root) as entries:
                names=[]
                for entry in entries:
                    check()
                    if entry.name.endswith('.json'):names.append(entry.name)
        except OSError:
            refuse('OBSERVATION_UNAVAILABLE', 'control request enumeration is incomplete')
        result = []
        check()
        for name in sorted(names):
            check()
            path = root/name
            if not vm.UUID.fullmatch(path.stem):
                refuse("RECONCILE_REQUIRED", "invalid control request filename")
            value = self.read(path)
            check()
            if value is None:
                refuse('OBSERVATION_UNAVAILABLE', 'enumerated control request disappeared')
            if (value.get("operation_id") != path.stem or value.get("request_hash") != fingerprint(value.get("request"))
                    or not vm.UUID.fullmatch(value.get("vm_id", "")) or 'idempotency_key' not in value
                    or value['idempotency_key'] is not None and not Manager._text_token(value['idempotency_key'])):
                refuse("RECONCILE_REQUIRED", "invalid control request identity")
            result.append(value)
        check()
        return result

    @traced('execution.save_request')
    def save_request(self, op, phase):
        trace_bind(operation_id=op['operation_id'], vm_id=op['vm_id'], host_boot_id=self.host,
                   phase=phase, deadline_monotonic_ms=op.get('deadline_monotonic_ms'),
                   intent_revision=op.get('intent_revision'), lifecycle_generation=op.get('lifecycle_generation'),
                   policy_revision=(op.get('cpu_policy') or {}).get('policy_revision'))
        trace_event('operation.phase', phase=phase, operation_id=op['operation_id'],
                    remaining_seconds=(op['deadline_monotonic_ms']/1000-self.p.clock())
                    if type(op.get('deadline_monotonic_ms')) is int else None)
        op["phase"] = phase
        op.setdefault("events", []).append({"phase":phase, "host_boot_id":self.host,
                                          "monotonic_ms":int(self.p.clock()*1000)})
        self.write(self._path(self.data, "control-requests", op["operation_id"]), op)
        self.checkpoint("intent-"+phase, op)

    @traced('execution.accept')
    def accept(self, record, request, key=None, *, source="user", previous=None, receipt=None, deadline=None):
        # operation_lock -> admission 순서만 허용한다. 짧은 수락 경로는 admission을
        # 해제한 뒤에만 긴 실행 잠금을 시도하므로 서로 기다리는 순환을 만들지 않는다.
        with short_lock(self.runtime, 'admission'):
            return self._accept_locked(record, request, key, source=source, previous=previous,
                                       receipt=receipt, deadline=deadline)

    def _accept_locked(self, record, request, key=None, *, source="user", previous=None, receipt=None, deadline=None):
        if deadline is not None and self.p.clock() >= deadline:
            refuse('DEADLINE_EXCEEDED', 'control deadline expired before intent acceptance')
        lifecycle, generation = self.lifecycle(record)
        if lifecycle != "REGISTERED":
            refuse("TARGET_CHANGED", "control target is no longer registered")
        old = self.intent(record)
        base = old["intent_revision"] if old else 0
        cpu_context = self.p.cpu().context(record) if request['action'] in ('start', 'reboot') else None
        if previous and cpu_context is not None and previous.get('cpu_policy', {'configured':False}) != cpu_context:
            refuse('TARGET_CHANGED', 'accepted CPU policy changed before intent acceptance')
        op = previous or {"cpu_policy":cpu_context, "schema_version":"1.0", "operation_id":uuid4(), "vm_id":record["uuid"],
                          "request":request, "request_hash":fingerprint(request), "idempotency_key":key,
                          "host_boot_id":self.host, "lifecycle_generation":generation, "base_revision":base,
                          "intent_revision":base+1, "desired_state":"stopped" if request["action"] in ("stop", "destroy") else "running",
                          "source":source, "result":None, "error":None, "events":[]}
        if previous is None and deadline is not None:
            op['deadline_monotonic_ms'] = int(deadline*1000)
        if op["lifecycle_generation"] != generation:
            refuse("TARGET_CHANGED", "prepared control belongs to a previous lifecycle")
        if receipt is not None:
            receipt["operation"] = op
        if deadline is not None and self.p.clock() >= deadline:
            refuse('DEADLINE_EXCEEDED', 'control deadline expired before intent publication')
        if request["action"] not in ("stop", "destroy"):
            self.gate()
        elif (self.runtime/"shutdown-requested").exists():
            refuse("HOST_QUIESCING", "storage shutdown has already been requested")
        if previous is None:
            self.save_request(op, "prepared")
        committed = old and old["accepted_operation_id"] == op["operation_id"] and old["intent_revision"] == op["intent_revision"]
        if not committed:
            if generation != op["lifecycle_generation"] or base != op["base_revision"]:
                refuse("TARGET_CHANGED", "prepared intent was superseded")
            value = {"schema_version":"1.0", "vm_id":record["uuid"], "desired_state":op["desired_state"],
                     "intent_revision":op["intent_revision"], "lifecycle_generation":generation,
                     "accepted_host_boot_id":self.host, "accepted_operation_id":op["operation_id"], "source":source}
            self.write(self._path(self.data, "control", record["uuid"]), value)
            self.checkpoint("intent-published", op)
        else:
            sync_directory(self.data/"control")
        self.save_request(op, "accepted")
        self.checkpoint("intent-admission-pending", op)
        self.set_admission(record, op["desired_state"] == "running",
                           "user-stop" if op["desired_state"] == "stopped" else "user-reboot" if request["action"] == "reboot" else "user-start",
                           "EXPLICIT_INTENT" if source == "user" else "APPROVED_BOOT_POLICY")
        return op

    @traced('execution.control')
    def control(self, action, record, *, force=False, key=None, deadline=None):
        end = deadline if deadline is not None else self.p.clock()+90
        diagnostics = []
        def event(*args, **kwargs):
            warning = self.event(*args, **kwargs)
            if warning:
                diagnostics.append(warning)
        if action not in ("start", "stop", "reboot", "destroy"):
            refuse("INVALID_SPEC", "invalid control action")
        from xenova_management import Manager
        if any(q["vm_id"] == record["uuid"] and q["status"] != "SUCCEEDED"
               for q in Manager(self.p)._operations()):
            refuse("OPERATION_IN_PROGRESS", "an incomplete asset operation owns this VM", retryable=True)
        if action == "reboot" and vm.guest_kind(record) == "zephyr" and vm.cooperative(record) and not force:
            from xenova_zephyr_control import reboot
            return reboot(self,record,key,deadline=end)
        request = {"vm_id":record["uuid"], "action":action, "force":force}
        previous = next((q for q in self.requests() if key is not None and q["idempotency_key"] == key), None)
        if previous and previous["request"] != request:
            refuse("IDEMPOTENCY_CONFLICT", "control key has a different request")
        reconciliable = previous and previous['phase'] == 'uncertain'
        if previous and previous["phase"] in ("completed", "failed", "uncertain") and not reconciliable:
            event("control-replayed", record, previous["operation_id"])
            if previous["error"]:
                refuse(previous["error"]["code"], previous["error"]["message"],
                       details={**previous["error"]["details"], "operation_id":previous["operation_id"], "replayed":True,
                                **({"diagnostic_warnings":diagnostics} if diagnostics else {})})
            return {"operation_id":previous["operation_id"], "replayed":True, "original_result":previous["result"], "diagnostic_warnings":diagnostics}
        if previous and previous["host_boot_id"] != self.host:
            refuse("TARGET_CHANGED", "unfinished control request belongs to a previous host boot")
        if previous:
            stored = previous.get('deadline_monotonic_ms')
            if type(stored) is not int and previous['phase'] not in ('executing', 'uncertain'):
                refuse('RECONCILE_REQUIRED', 'legacy unfinished control has no physical execution deadline',
                       details={'operation_id':previous['operation_id'], 'replayed':True})
            if type(stored) is int:
                end = min(end, stored/1000)
        if previous and (previous["phase"] == "executing" or reconciliable):
            if self.lifecycle(record) != ("REGISTERED", previous["lifecycle_generation"]):
                refuse("TARGET_CHANGED", "control lifecycle changed")
            intent = self.intent(record)
            if not intent or intent["accepted_operation_id"] != previous["operation_id"]:
                refuse("TARGET_CHANGED", "control request intent was superseded")
            readonly_end = min(self.p.clock()+15,getattr(self.p,'response_deadline',float('inf')))
            observation = self.observe(record, readonly_end)
            run = self.run_record(record)
            confirmed = action in ("stop", "destroy") and observation["presence"] == "absent"
            start_owner = bool(run and run.get('owner') == {'kind':'user', 'operation_id':previous['operation_id']})
            prior_observation = (previous.get('result') or {}).get('observation')
            historical_noop = bool(prior_observation and
                                   prior_observation.get('execution_id') == observation.get('execution_id'))
            confirmed |= action == 'start' and observation['execution_status'] == 'managed' and (start_owner or historical_noop)
            confirmed |= bool(action == "reboot" and run and run.get("owner") == {"kind":"user", "operation_id":previous["operation_id"]} and observation["execution_status"] == "managed")
            if not confirmed:
                refuse("RECONCILE_REQUIRED", "physical control result is uncertain; command not repeated",
                       details={"operation_id":previous["operation_id"], "replayed":True})
            if action in ('start', 'reboot'):
                self.p.cpu().require(record, readonly_end, expected=previous.get('cpu_policy', {'configured':False}),
                                     present=True, expected_identity=observation)
                if run.get('cpu_verification') != 'complete':
                    run = {**run, 'cpu_verification':'complete'}
                    run.pop('cpu_error', None)
                    self.write(self._path(self.runtime, 'executions', record['uuid']), run)
                    row = {k:observation[k] for k in ('domid', 'xen_state', 'xen_state_flags')}
                    observation = self.publish_observation(record, row, observation['xen_unique_id'], readonly_end)
            if action in ("stop", "destroy"):
                if self.p.process_isolation:
                    from xenova_guest_process import cleanup
                    try:
                        cleanup(self.p,record,run,readonly_end,observe_only=True)
                    except (PlatformError,OSError,ValueError):
                        refuse('RECONCILE_REQUIRED','domain absent but owned process cleanup is unconfirmed; no signal sent',
                               details={'operation_id':previous['operation_id'],'replayed':True,
                                        'process_scope':(run or {}).get('process_scope')})
                self.end_run(record)
            previous["result"] = {"accepted":True, "observation":observation, "intent_revision":previous["intent_revision"]}
            previous["error"] = None
            try:
                self.save_request(previous, "completed")
            except OSError:
                refuse("RECONCILE_REQUIRED", "control completion could not be durably reconciled",
                       details={"operation_id":previous["operation_id"], "replayed":True})
            return {"operation_id":previous["operation_id"], "replayed":True, "original_result":previous["result"], "diagnostic_warnings":diagnostics}
        accepted = None
        receipt = {}

        def admit():
            nonlocal accepted
            accepted = self.accept(record, request, key, previous=previous, receipt=receipt, deadline=end)
            if action in ('start', 'reboot'):
                # Actual layout is checked by the launch primitive immediately before
                # preparation; acceptance only binds the durable policy context.
                accepted['cpu_policy'] = self.p.cpu().context(record)
            self.save_request(accepted, "executing")
            event("xen-action-requested", record, accepted["operation_id"], {"action":action})
            if action == "start" and vm.has_network(record):
                self.p.prepare_dhcp(records, deadline=end)
            return {"kind":"user", "operation_id":accepted["operation_id"]}

        try:
            if self.p.clock() >= end:
                refuse('DEADLINE_EXCEEDED', 'original control execution deadline expired')
            confirmed = {}
            if action == "start":
                self.p.check_xen(deadline=end)
                records = self.p.records(validate_assets=False)
                if vm.has_network(record):
                    self.p.check_network(deadline=end)
                physical = self.p.start(record, _accept=admit, deadline=end, _result=confirmed)
            elif action in ("stop", "destroy"):
                observed = self.observe(record, end)
                physical = self.p.stop_many([record], force=force or action == "destroy", _accept=admit,
                                            _observed={record["uuid"]:observed}, deadline=end, _result=confirmed)
            else:
                observed = self.observe(record, end)
                physical = self.p.reboot(record, force=force, _accept=admit, _observed=observed,
                                         deadline=end, _result=confirmed)
            observation = confirmed.get('observation') or self.observe(record, end)
            if action in ("stop", "destroy") and observation["presence"] == "absent":
                self.end_run(record)
            result = {"accepted":True, "physical_result":physical, "observation":observation,
                      "intent_revision":accepted["intent_revision"], "app_readiness":"unknown"}
            accepted["result"] = result
            try:
                self.save_request(accepted, "completed")
            except OSError as error:
                raise ExecutionError("RECONCILE_REQUIRED", "required control completion was not durably confirmed") from error
            event("control-completed", record, accepted["operation_id"])
            return {"operation_id":accepted["operation_id"], "replayed":previous is not None, "original_result":result, "diagnostic_warnings":diagnostics}
        except (PlatformError, OSError, ValueError) as error:
            if accepted is None:
                prepared = receipt.get("operation")
                if prepared and isinstance(error, OSError):
                    refuse("STORAGE_UNAVAILABLE", "intent acceptance was not durably confirmed; no Xen action issued",
                           details={"operation_id":prepared["operation_id"], "acceptance":"unconfirmed"})
                event("control-rejected", record, details={"error":getattr(error, "error", {}).get("code", "PREFLIGHT_REFUSED")})
                raise
            code = error.error["code"] if isinstance(error, ExecutionError) else "XEN_EXECUTION_FAILED"
            accepted["error"] = {"code":code, "message":str(error), "retryable":False,
                                 "details":{"accepted":True, "intent_revision":accepted["intent_revision"],
                                            "operation_id":accepted["operation_id"],
                                            **({'cause':error.error} if isinstance(error, ExecutionError) else {})}}
            run = self.run_record(record) if action in ('start', 'reboot') else None
            owned_launch = bool(run and run.get('owner') == {'kind':'user', 'operation_id':accepted['operation_id']}
                                and run.get('phase') in ('launching', 'uncertain', 'observed'))
            # A timed-out child may still have delivered its command to Xen. Preserve
            # ownership even when no physical result was returned; replay cannot issue
            # another command merely because result is null.
            uncertain = bool(confirmed.get('physical_submitted') or owned_launch or accepted.get('result'))
            if uncertain and code in ('XEN_EXECUTION_FAILED', 'DEADLINE_EXCEEDED'):
                accepted['error']['code'] = 'RECONCILE_REQUIRED'
                accepted['error']['details'].setdefault('cause', {'code':code, 'message':str(error),
                                                               'retryable':False, 'details':{}})
            try:
                self.save_request(accepted, 'uncertain' if uncertain else 'failed')
            except OSError as journal_error:
                refuse("RECONCILE_REQUIRED", "accepted control journal could not be finalized",
                       details={"operation_id":accepted["operation_id"], "accepted":True,
                                "intent_revision":accepted["intent_revision"]})
            raise ExecutionError(**accepted["error"]) from error

    def event(self, event, record=None, operation_id=None, details=None):
        """비핵심 runtime 진단. 필수 intent/launch/journal 저장에는 사용하지 않는다."""
        try:
            value = {"schema_version":"1.0", "event":event, "host_boot_id":self.host,
                     "monotonic_ms":int(self.p.clock()*1000), "vm_id":record["uuid"] if record else None,
                     "operation_id":operation_id, "details":details or {}}
            self.write(self.runtime/"execution-events"/(uuid4()+".json"), value)
        except (PlatformError, OSError, ValueError) as diagnostic:
            return {"code":"DIAGNOSTIC_UNAVAILABLE", "sink":"execution-events",
                    "event":event, "cause_type":type(diagnostic).__name__}
        return None
