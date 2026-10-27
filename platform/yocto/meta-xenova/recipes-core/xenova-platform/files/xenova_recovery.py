"""조건을 다시 확인하는 일회성 복구 실행. 감시·정책·반복 실행 서비스는 아니다."""
from first_install_contract import uuid4
from xenova_execution import ExecutionError, fingerprint, refuse, short_lock
from xenova_runtime import PlatformError
import xenova_catalog as catalog
import xenova_vm as vm
from xenova_trace import bind as trace_bind, traced


def validate_request(spec):
    from xenova_management import normalize_uuid
    fields = {"vm_id", "recovery_attempt_id", "expected_host_boot_id", "expected_lifecycle_generation",
              "expected_intent_revision", "expected_execution", "mode", "deadline_monotonic_ms"}
    if not isinstance(spec, dict) or set(spec) != fields:
        refuse("INVALID_SPEC", "recover_once requires the exact documented specification")
    spec = dict(spec)
    for name in ("vm_id", "recovery_attempt_id", "expected_host_boot_id"):
        spec[name] = normalize_uuid(spec[name])
    for name, low in (("expected_lifecycle_generation", 0), ("expected_intent_revision", 1),
                      ("deadline_monotonic_ms", 1)):
        if type(spec[name]) is not int or spec[name] < low:
            refuse("INVALID_SPEC", name+" must be an integer")
    expected = spec["expected_execution"]
    if not isinstance(expected, dict):
        refuse("INVALID_SPEC", "expected_execution must be a versioned object")
    if set(expected) == {"execution_id"}:
        spec["expected_execution"] = {"execution_id":normalize_uuid(expected["execution_id"])}
    elif set(expected) != {"absent_observation_revision"} or type(expected.get("absent_observation_revision")) is not int or expected["absent_observation_revision"] < 1:
        refuse("INVALID_SPEC", "execution_id or absent_observation_revision is required")
    if spec["mode"] not in ("start-if-absent", "graceful-restart", "force-restart"):
        refuse("INVALID_SPEC", "unsupported recovery mode")
    if (spec["mode"] == "start-if-absent") != ("absent_observation_revision" in expected):
        refuse("INVALID_SPEC", "recovery mode and expected presence disagree")
    return spec


class Recovery:
    def __init__(self, manager):
        self.m = manager
        self.p = manager.p
        self.e = self.p.execution()
        self.diagnostics = []

    def path(self, identity):
        return self.e._path(self.p.data, "recovery-attempts", identity)

    def read_attempt(self, identity):
        value = self.e.read(self.path(identity))
        if value is None:
            refuse("VM_NOT_FOUND", "recovery attempt not found")
        if (value.get("recovery_attempt_id") != identity or value.get("request_hash") != fingerprint(value.get("request"))
                or not vm.UUID.fullmatch(value.get("operation_id", ""))):
            refuse("RECONCILE_REQUIRED", "recovery attempt integrity mismatch")
        return value

    def attempts(self):
        root = self.p.data/"recovery-attempts"
        if not root.exists() and not root.is_symlink():
            return []
        catalog.plain(root, directory=True)
        result = []
        for path in root.glob("*.json"):
            if not vm.UUID.fullmatch(path.stem):
                refuse("RECONCILE_REQUIRED", "invalid recovery journal filename")
            result.append(self.read_attempt(path.stem))
        return result

    def save(self, op, phase):
        trace_bind(operation_id=op['operation_id'], recovery_attempt_id=op['recovery_attempt_id'],
                   host_boot_id=self.e.host, vm_id=op['request']['vm_id'], phase=phase,
                   deadline_monotonic_ms=op['request']['deadline_monotonic_ms'],
                   intent_revision=op['request']['expected_intent_revision'],
                   lifecycle_generation=op['request']['expected_lifecycle_generation'],
                   policy_revision=(op.get('cpu_policy') or {}).get('policy_revision'))
        op["phase"] = phase
        op.setdefault("events", []).append({"phase":phase, "host_boot_id":self.e.host,
                                          "monotonic_ms":int(self.p.clock()*1000)})
        self.e.write(self.path(op["recovery_attempt_id"]), op)
        self.e.checkpoint("recovery-"+phase, op)

    def conditions(self, spec, deadline, *, target=True, gate=True):
        if spec["expected_host_boot_id"] != self.e.host:
            refuse("TARGET_CHANGED", "recovery request belongs to another host boot", details={"reason":"HOST_BOOT_CHANGED"})
        if gate:
            self.e.gate()
        records = self.p.records(validate_assets=False)
        tomb = self.m._state(spec["vm_id"], records)
        if tomb["lifecycle"] != "REGISTERED" or tomb["generation"] != spec["expected_lifecycle_generation"]:
            refuse("TARGET_CHANGED", "recovery lifecycle changed", details={"reason":"LIFECYCLE_CHANGED"})
        record = tomb["record"]
        intent = self.e.intent(record)
        if (not intent or intent["desired_state"] != "running" or intent["intent_revision"] != spec["expected_intent_revision"]
                or intent["lifecycle_generation"] != tomb["generation"]):
            refuse("INTENT_MISMATCH", "current applicable intent is not the expected running revision")
        admission = self.e.admission(record)
        if (not admission or not admission["allowed"] or admission["intent_revision"] != intent["intent_revision"]
                or admission["lifecycle_generation"] != tomb["generation"]):
            refuse("BOOT_ADMISSION_REQUIRED", "this host boot has not admitted this intent")
        if any(q["vm_id"] == record["uuid"] and q["status"] != "SUCCEEDED" for q in self.m._operations()):
            refuse("OPERATION_IN_PROGRESS", "an incomplete asset operation owns this VM", retryable=True)
        self.m._record_at(record, self.p.data/"instances"/record["id"])
        if self.p.clock() >= deadline:
            refuse("DEADLINE_EXCEEDED", "recovery deadline expired")
        observation = self.e.observe(record, deadline)
        if target:
            expected = spec["expected_execution"]
            if "absent_observation_revision" in expected:
                if (observation["presence"] != "absent" or
                        observation["observation_revision"] != expected["absent_observation_revision"]):
                    refuse("TARGET_CHANGED", "expected absent observation was superseded")
            elif observation["execution_status"] != "managed" or observation["execution_id"] != expected["execution_id"]:
                refuse("TARGET_CHANGED", "expected managed execution changed", details={"reason":"EXECUTION_CHANGED"})
        return record, records, observation

    def mode_allowed(self, spec, record):
        if spec["mode"] == "graceful-restart" and not vm.cooperative(record):
            refuse("RECOVERY_MODE_UNSUPPORTED", "this guest has no cooperative shutdown")
        if spec["mode"] == "force-restart" and record["uuid"] not in self.e.policy()["force_restart_vm_ids"]:
            refuse("RECOVERY_MODE_UNSUPPORTED", "force recovery has no per-VM administrator approval")

    @traced('recovery.complete')
    def complete(self, op, observation):
        record = self.m._state(op['request']['vm_id'], self.p.records(validate_assets=False))['record']
        run = self.e.run_record({"uuid":op["request"]["vm_id"]})
        proof = observation.get('_description', {}).get('execution_context')
        if (observation["execution_status"] != "managed" or not run or
                run.get("owner") != {"kind":"recovery", "recovery_attempt_id":op["recovery_attempt_id"]} or
                proof != run or run.get('cpu_verification') != 'complete' or
                observation.get('configuration_revision') != fingerprint(record) or
                self.p.cpu().context(record) != op.get('cpu_policy', {'configured':False})):
            refuse("RECONCILE_REQUIRED", "new launch cannot be correlated to this attempt")
        intent = self.e.intent(record)
        if (self.e.lifecycle(record) != ('REGISTERED',op['request']['expected_lifecycle_generation']) or
                not intent or intent['desired_state'] != 'running' or
                intent['intent_revision'] != op['request']['expected_intent_revision']):
            refuse('INTENT_MISMATCH', 'recovery completion intent or lifecycle changed')
        op["result"] = {"vm_id":op["request"]["vm_id"], "host_boot_id":self.e.host,
                        "lifecycle_generation":op["request"]["expected_lifecycle_generation"],
                        "intent_revision":op["request"]["expected_intent_revision"],
                        "execution_id":observation["execution_id"], "observation":observation,
                        "platform_elapsed_ms":int(self.p.clock()*1000)-op["accepted_monotonic_ms"],
                        "app_readiness":"unknown"}
        op["error"] = None
        try:
            self.save(op, "completed")
        except OSError as error:
            op["phase"] = "observed"
            raise ExecutionError("RECONCILE_REQUIRED", "required completion journal was not durably confirmed") from error
        self.event("recovery-completed", {"uuid":op["request"]["vm_id"]}, op["operation_id"],
                     {"recovery_attempt_id":op["recovery_attempt_id"], "execution_id":observation["execution_id"]})
        return op["result"]

    def event(self, *args, **kwargs):
        warning = self.e.event(*args, **kwargs)
        if warning:
            self.diagnostics.append(warning)

    def once(self, spec, *, execute, context):
        self.diagnostics = []
        try:
            result = self._once(spec, execute=execute, context=context)
        except ExecutionError as error:
            if self.diagnostics:
                error.error["details"].setdefault("diagnostic_warnings", []).extend(self.diagnostics)
            raise
        if self.diagnostics:
            result["diagnostic_warnings"] = self.diagnostics
        return result

    def unresolved(self, op, message):
        refuse("RECONCILE_REQUIRED", message, details={
            "blocking_attempt_id":op["recovery_attempt_id"], "blocking_operation_id":op["operation_id"],
            "phase":op["phase"], "next_action":"inspect_original_attempt",
            "allowed_actions":["query_original_attempt", "replay_original_to_finalize_observed_launch",
                               "review_planned_host_restart"], "automatic_retry":False})

    def reconcile(self, identity, *, execute, deadline, context):
        """원 attempt만 진단/종결한다. 이 경로는 VM 제어·새 수락·기한 연장을 하지 않는다.

        execution flock은 Manager가 소유한다. 외부 관측 뒤 짧은 admission lock 안에서
        원 journal/intent/run을 다시 대조하며, 과거 PID나 부재 하나를 종료 증명으로 쓰지 않는다.
        """
        op = self.read_attempt(identity)
        context.update(operation_id=op['operation_id'], replayed=True)
        spec = validate_request(op['request'])
        if op.get('schema_version') != '1.0' or op.get('host_boot_id') != spec['expected_host_boot_id']:
            refuse('RECONCILE_REQUIRED', 'recovery journal context invalid')
        if spec['expected_host_boot_id'] != self.e.host:
            refuse('TARGET_CHANGED', 'original attempt belongs to another host; no cross-host deadline comparison',
                   details={'operation_id':op['operation_id'], 'reason':'HOST_BOOT_CHANGED'})
        if self.p.clock() >= deadline:
            refuse('DEADLINE_EXCEEDED', 'recovery reconciliation budget expired')
        if op['phase'] in ('completed', 'failed'):
            if execute and op.get('reconciliation'):
                # 이전 replace 뒤 parent fsync 응답을 잃었을 수도 있어 같은 결과를 재확정한다.
                self.e.write(self.path(identity), op)
            return {'attempt':op, 'reconciliation':{'status':'recorded', 'executed':bool(execute and op.get('reconciliation')),
                    'physical_commands':0}, 'original_result':op.get('result')}
        record, _, observed = self.conditions(spec, deadline, target=False, gate=False)
        run = self.e.run_record(record)
        intent = self.e.intent(record)
        snapshot = {'record':fingerprint(record), 'run':run, 'intent':intent}
        phase = op['phase']
        failed_phase = (op.get('error') or {}).get('details', {}).get('failed_phase')
        stopping = phase == 'stopping' or phase == 'uncertain' and failed_phase == 'stopping'
        owner = {'kind':'recovery', 'recovery_attempt_id':identity}
        checks = {'cpu_policy':self.p.cpu().context(record) == op.get('cpu_policy', {'configured':False})}
        outcome = None
        if phase in ('accepted', 'removed'):
            # 이 phase 이후의 create는 먼저 launching을 내구 저장해야 한다.
            checks['no_unresolved_launch'] = not run or run.get('phase') not in ('launching', 'uncertain')
            checks['no_owned_launch'] = not run or run.get('owner') != owner
            checks['removed_is_absent'] = phase != 'removed' or observed['presence'] == 'absent'
            outcome = 'not_submitted' if phase == 'accepted' else 'stopped_without_replacement'
        elif stopping:
            proof = op.get('stop_submission') or {}
            target = proof.get('target') or {}
            checks['stop_target'] = (target.get('execution_id') == spec['expected_execution'].get('execution_id')
                and target.get('host_boot_id') == self.e.host and type(target.get('domid')) is int
                and isinstance(target.get('xen_unique_id'), str))
            checks['no_replacement_launch'] = not run or (run.get('execution_id') == target.get('execution_id')
                and run.get('phase') not in ('launching', 'uncertain'))
            state = proof.get('state')
            checks['original_call_finished'] = state in ('not-submitted', 'returned')
            checks['target_state'] = (observed['presence'] == 'absent' or state == 'not-submitted'
                and observed['execution_status'] == 'managed' and observed['execution_id'] == target.get('execution_id')
                and observed['domid'] == target.get('domid') and observed['xen_unique_id'] == target.get('xen_unique_id'))
            outcome = 'not_submitted' if state == 'not-submitted' else 'stopped_without_replacement'
        elif phase in ('launching', 'observed', 'uncertain'):
            checks['correlated_execution'] = bool(run and run.get('owner') == owner
                and run.get('phase') == 'observed' and run.get('cpu_verification') == 'complete'
                and observed['execution_status'] == 'managed' and observed['execution_id'] == run.get('execution_id')
                and observed.get('_description', {}).get('execution_context') == run)
            if checks['correlated_execution']:
                self.p.cpu().require(record, deadline, expected=op.get('cpu_policy', {'configured':False}),
                                     present=True, expected_identity=run)
                outcome = 'launch_observed'
            elif run and run.get('owner') == owner and run.get('launch_aborted_before_submit') is True:
                checks['correlated_execution'] = observed['presence'] == 'absent' and run.get('phase') == 'stopped'
                outcome = 'not_submitted'
        else:
            checks['supported_phase'] = False
        if outcome != 'launch_observed' and observed['presence'] == 'absent' and self.p.process_isolation:
            # observe_only는 신호나 scope stop을 보내지 않는다. 살아 있는 backend/helper는 차단한다.
            from xenova_guest_process import cleanup
            if not run or not run.get('process_scope'):
                if stopping or phase not in ('accepted', 'removed'):
                    checks['process_ownership'] = False
            else:
                try:
                    cleanup(self.p, record, run, deadline, observe_only=True)
                except (PlatformError, OSError, ValueError):
                    checks['process_quiescent'] = False
        reasons = [key for key, passed in checks.items() if not passed]
        if outcome is None:
            reasons.append('original_effect_unconfirmed')
        result = {'status':'blocked' if reasons else 'confirmable', 'outcome':outcome,
                  'checks':checks, 'reasons':reasons, 'executed':False, 'physical_commands':0,
                  'host_boot_id':self.e.host, 'observed_monotonic_ms':int(self.p.clock()*1000)}
        if self.p.clock() >= deadline:
            refuse('DEADLINE_EXCEEDED', 'recovery reconciliation observation budget expired')
        if execute and reasons:
            refuse('RECONCILE_REQUIRED', 'original effects or ownership not fully confirmed; no record closed',
                   details={'operation_id':op['operation_id'], 'recovery_attempt_id':identity,
                            'reconciliation':result, 'automatic_retry':False})
        if not execute:
            return {'attempt':op, 'reconciliation':result, 'original_result':op.get('result')}
        self.e.checkpoint('recovery-reconcile-before-guard', op)
        # 실제 identity는 다시 관측하고, 그 뒤의 저장된 상태 guard는 admission 안에서 수행한다.
        final = self.e.observe(record, deadline)
        fields = ('presence','execution_status','execution_id','domid','xen_unique_id','configuration_revision')
        if any(final.get(key) != observed.get(key) for key in fields):
            refuse('TARGET_CHANGED', 'execution changed after reconciliation diagnostic')
        with short_lock(self.p.runtime, 'admission', deadline=deadline, clock=self.p.clock, sleep=self.p.sleep):
            current = self.m._state(spec['vm_id'], self.p.records(validate_assets=False))
            if (self.read_attempt(identity) != op or self.p.boot_id_reader() != self.e.host
                    or current['lifecycle'] != 'REGISTERED' or current['generation'] != spec['expected_lifecycle_generation']
                    or fingerprint(current['record']) != snapshot['record'] or self.e.intent(record) != snapshot['intent']
                    or self.e.run_record(record) != snapshot['run']
                    or self.p.cpu().context(record) != op.get('cpu_policy', {'configured':False})):
                refuse('TARGET_CHANGED', 'original attempt, intent or execution changed before durable confirmation')
            if self.p.clock() >= deadline:
                refuse('DEADLINE_EXCEEDED', 'recovery reconciliation guard budget expired')
            op['reconciliation'] = {**result, 'status':'confirmed', 'executed':True,
                                    'original_phase':phase, 'original_error':op.get('error')}
            try:
                if outcome == 'launch_observed':
                    self.complete(op, final)
                else:
                    expired = self.p.clock()*1000 >= spec['deadline_monotonic_ms']
                    code = 'DEADLINE_EXCEEDED' if expired else 'XEN_EXECUTION_FAILED'
                    op['error'] = {'code':code, 'message':'original recovery ended without a replacement launch',
                        'retryable':False, 'details':{'operation_id':op['operation_id'],
                        'recovery_attempt_id':identity, 'reconciliation_outcome':outcome,
                        'next_action':'observe_current_intent_before_any_new_request'}}
                    op['result'] = None
                    self.save(op, 'failed')
            except OSError as error:
                refuse('RECONCILE_REQUIRED', 'reconciliation durability not confirmed; inspect same original attempt',
                       details={'operation_id':op['operation_id'], 'recovery_attempt_id':identity})
        return {'attempt':op, 'reconciliation':op['reconciliation'], 'original_result':op.get('result')}

    def expire_prelaunch(self, op):
        """기존 소유 잠금 안에서, launch 전임을 재확인한 만료 시도만 닫는다."""
        if op["phase"] not in ("accepted", "removed"):
            self.unresolved(op, "physical action may be unresolved; expiration cannot release this owner")
        try:
            record, _, current = self.conditions(op["request"], self.p.clock()+15, target=False, gate=False)
        except (PlatformError, OSError, ValueError):
            self.unresolved(op, "expired attempt could not be reconciled against current assets and Xen")
        run = self.e.run_record(record)
        if run and run.get("phase") in ("launching", "uncertain"):
            self.unresolved(op, "an unresolved launch prevents expiration cleanup")
        if op["phase"] == "removed" and current["presence"] != "absent":
            self.unresolved(op, "domain appeared after recorded removal; expiration needs reconciliation")
        op["error"] = {"code":"DEADLINE_EXCEEDED", "message":"recovery attempt expired before launch",
                       "retryable":False, "details":{"operation_id":op["operation_id"],
                       "recovery_attempt_id":op["recovery_attempt_id"], "expired_before_launch":True,
                       "next_action":"use_new_attempt_after_fresh_observation"}}
        previous_phase = op["phase"]
        try:
            self.save(op, "failed")
        except OSError:
            op["phase"] = previous_phase
            self.unresolved(op, "expiration journal could not be durably closed")

    @traced('recovery.once')
    def _once(self, spec, *, execute, context):
        spec = validate_request(spec)
        identity = spec["recovery_attempt_id"]
        deadline = spec["deadline_monotonic_ms"]/1000
        if spec["expected_host_boot_id"] != self.e.host:
            refuse("TARGET_CHANGED", "previous host boot recovery is not executable")
        op = self.read_attempt(identity) if self.path(identity).exists() or self.path(identity).is_symlink() else None
        if op:
            context.update(operation_id=op["operation_id"], replayed=True)
            if op["request"] != spec:
                refuse("IDEMPOTENCY_CONFLICT", "recovery attempt ID has a different request")
            if op["phase"] in ("completed", "failed"):
                current = None
                try:
                    record = self.m._state(spec["vm_id"], self.p.records(validate_assets=False))["record"]
                    current = self.e.observe(record, self.p.clock()+15)
                except (PlatformError, OSError, ValueError, KeyError, TypeError) as error:
                    self.diagnostics.append({'code':'DIAGNOSTIC_UNAVAILABLE',
                        'sink':'current-observation','cause_type':type(error).__name__})
                self.event("recovery-replayed", {'uuid':spec['vm_id']}, op["operation_id"], {"recovery_attempt_id":identity})
                if op.get("error"):
                    refuse(op["error"]["code"], op["error"]["message"],
                           details={**op["error"]["details"], "operation_id":op["operation_id"], "replayed":True,
                                    "current_observation":current})
                return {"attempt":op, "original_result":op["result"], "current_observation":current}
            if self.p.clock() >= deadline and op["phase"] in ("accepted", "removed"):
                if execute:
                    self.expire_prelaunch(op)
                refuse("DEADLINE_EXCEEDED", "recovery attempt deadline expired",
                       details={"operation_id":op["operation_id"], "recovery_attempt_id":identity,
                                "replayed":True, "next_action":"use_new_attempt_after_fresh_observation" if execute else "execute_original_attempt_to_reconcile_expiration"})
            if not execute:
                return {"plan":True, "attempt":op}
            # A recorded launch observed under this exact owner can be finalized without issuing Xen again.
            if op["phase"] in ("launching", "observed", "uncertain"):
                # 만료 후의 15초는 기존 실행을 확인·종결하는 읽기 예산이다.
                # 이 분기에는 start/stop/create/repin 경로가 없다.
                reconcile_end = self.p.clock()+15
                record, _, observation = self.conditions(spec, reconcile_end, target=False, gate=False)
                run = self.e.run_record(record)
                if (observation["execution_status"] == "managed" and run and
                        run.get("owner") == {"kind":"recovery", "recovery_attempt_id":identity}):
                    self.p.cpu().require(record, reconcile_end, expected=op.get('cpu_policy', {'configured':False}),
                                         present=True, expected_identity=run)
                    if run.get('cpu_verification') != 'complete':
                        # An interrupted bind may have stored identity before CPU
                        # verification. Only this fresh, successful check can close
                        # that gap; observed by itself is insufficient.
                        run = {**run, 'cpu_verification':'complete'}
                        run.pop('cpu_error', None)
                        self.e.write(self.e._path(self.p.runtime, 'executions', record['uuid']), run)
                        row = {k:observation[k] for k in ('domid', 'xen_state', 'xen_state_flags')}
                        observation = self.e.publish_observation(record, row, observation['xen_unique_id'], reconcile_end)
                    result = self.complete(op, observation)
                    return {"attempt":op, "original_result":result, "current_observation":observation}
                refuse("RECONCILE_REQUIRED", "a previous launch may still exist; no stop/create is repeated",
                       details={"operation_id":op["operation_id"], "replayed":True})
        if self.p.clock() >= deadline or deadline-self.p.clock() > 120:
            refuse("DEADLINE_EXCEEDED", "same-host recovery deadline must be within the next 120 seconds")
        resume_removed = op is not None and op["phase"] in ("stopping", "removed")
        record, records, observation = self.conditions(spec, deadline, target=not resume_removed)
        self.mode_allowed(spec, record)
        cpu_context = self.p.cpu().require(record, deadline, expected=op.get('cpu_policy', {'configured':False}) if op else None)
        if resume_removed and observation["presence"] != "absent":
            refuse("RECONCILE_REQUIRED", "old shutdown result is uncertain; request is not repeated",
                   details={"operation_id":op["operation_id"], "replayed":True})
        for other in self.attempts():
            request = other["request"]
            if (other["recovery_attempt_id"] != identity and request["vm_id"] == spec["vm_id"] and
                    other["phase"] not in ("completed", "failed") and
                    request["expected_host_boot_id"] == self.e.host and
                    request["expected_lifecycle_generation"] == spec["expected_lifecycle_generation"] and
                    request["expected_intent_revision"] == spec["expected_intent_revision"]):
                if other["phase"] in ("launching", "observed", "uncertain", "stopping"):
                    self.unresolved(other, "an earlier physical action needs reconciliation")
                if self.p.clock()*1000 >= request["deadline_monotonic_ms"]:
                    if execute:
                        self.expire_prelaunch(other)
                        continue
                    self.unresolved(other, "expired pre-launch owner must be reconciled by an explicit execution request")
                refuse("OPERATION_IN_PROGRESS", "another unresolved recovery owns this intent", retryable=True,
                       details={"blocking_attempt_id":other["recovery_attempt_id"]})
        if not execute:
            return {"plan":True, "request":spec, "current_observation":observation}
        if op is None:
            op = {"cpu_policy":cpu_context, "schema_version":"1.0", "operation_id":uuid4(), "recovery_attempt_id":identity,
                  "request":spec, "request_hash":fingerprint(spec), "host_boot_id":self.e.host,
                  "accepted_monotonic_ms":int(self.p.clock()*1000), "result":None, "error":None, "events":[]}
            context["operation_id"] = op["operation_id"]
            # Admission publication and host barrier are serialized by the same short lock.
            from xenova_execution import short_lock
            with short_lock(self.p.runtime, "admission", deadline=deadline,
                            clock=self.p.clock, sleep=self.p.sleep):
                self.e.gate()
                self.save(op, "accepted")
        try:
            if not resume_removed and observation["presence"] == "present":
                op['stop_submission'] = {'state':'not-submitted', 'target':{
                    'host_boot_id':self.e.host, **{name:observation[name] for name in
                    ('execution_id','domid','xen_unique_id')}}}
                self.save(op, "stopping")
                op['stop_submission']['state'] = 'submitted'
                self.save(op, 'stopping')
                try:
                    self.p.stop_many([record], force=spec["mode"] == "force-restart", deadline=deadline,
                                     _expected_runs={record["uuid"]:observation["xen_unique_id"]})
                except (PlatformError, OSError, ValueError):
                    op['stop_submission']['state'] = 'returned'
                    raise
                op['stop_submission']['state'] = 'returned'
                self.save(op, 'stopping')
                self.e.checkpoint('recovery-stop-returned', op)
            self.save(op, "removed")
            record, records, absent = self.conditions(spec, deadline, target=False)
            if absent["presence"] != "absent":
                refuse("TARGET_CHANGED", "a domain appeared before recovery launch")
            if vm.has_network(record):
                self.p.prepare_dhcp(records, deadline=deadline)
            if self.p.cpu().context(record) != op.get('cpu_policy', {'configured':False}):
                refuse('TARGET_CHANGED', 'accepted recovery CPU policy changed')
            self.save(op, "launching")
            owner = {"kind":"recovery", "recovery_attempt_id":identity}
            confirmed = {}
            self.p.start(record, _owner=owner, deadline=deadline, _result=confirmed)
            observed = confirmed['observation']
            self.save(op, "observed")
            result = self.complete(op, observed)
            return {"attempt":op, "original_result":result, "current_observation":observed}
        except (PlatformError, OSError, ValueError) as error:
            code = error.error["code"] if isinstance(error, ExecutionError) else "XEN_EXECUTION_FAILED"
            # A submitted shutdown can remove the old domain before its response
            # is lost. Keep that owner unresolved just like a submitted launch.
            uncertain = op["phase"] in ("stopping", "launching", "observed")
            op["error"] = {"code":"RECONCILE_REQUIRED" if uncertain else code, "message":str(error),
                           "retryable":False, "details":{"operation_id":op["operation_id"],
                           "recovery_attempt_id":identity, "accepted":True,
                           "failed_phase":op["phase"],
                           **({'cause':error.error} if isinstance(error, ExecutionError) else {})}}
            try:
                self.save(op, "uncertain" if uncertain else "failed")
            except OSError:
                refuse("RECONCILE_REQUIRED", "accepted recovery journal could not be finalized",
                       details={"operation_id":op["operation_id"], "recovery_attempt_id":identity, "accepted":True})
            raise ExecutionError(**op["error"]) from error
