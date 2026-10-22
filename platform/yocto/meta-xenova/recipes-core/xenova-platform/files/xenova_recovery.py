"""조건을 다시 확인하는 일회성 복구 실행. 감시·정책·반복 실행 서비스는 아니다."""
from first_install_contract import uuid4
from xenova_execution import ExecutionError, fingerprint, refuse
from xenova_runtime import PlatformError
import xenova_catalog as catalog
import xenova_vm as vm


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

    def complete(self, op, observation):
        run = self.e.run_record({"uuid":op["request"]["vm_id"]})
        if (observation["execution_status"] != "managed" or not run or
                run.get("owner") != {"kind":"recovery", "recovery_attempt_id":op["recovery_attempt_id"]}):
            refuse("RECONCILE_REQUIRED", "new launch cannot be correlated to this attempt")
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
                record = self.m._state(spec["vm_id"], self.p.records(validate_assets=False))["record"]
                current = self.e.observe(record, self.p.clock()+15)
                self.event("recovery-replayed", record, op["operation_id"], {"recovery_attempt_id":identity})
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
                record, _, observation = self.conditions(spec, self.p.clock()+15, target=False, gate=False)
                run = self.e.run_record(record)
                if (observation["execution_status"] == "managed" and run and
                        run.get("owner") == {"kind":"recovery", "recovery_attempt_id":identity}):
                    result = self.complete(op, observation)
                    return {"attempt":op, "original_result":result, "current_observation":observation}
                refuse("RECONCILE_REQUIRED", "a previous launch may still exist; no stop/create is repeated",
                       details={"operation_id":op["operation_id"], "replayed":True})
        if self.p.clock() >= deadline or deadline-self.p.clock() > 120:
            refuse("DEADLINE_EXCEEDED", "same-host recovery deadline must be within the next 120 seconds")
        resume_removed = op is not None and op["phase"] in ("stopping", "removed")
        record, records, observation = self.conditions(spec, deadline, target=not resume_removed)
        self.mode_allowed(spec, record)
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
            op = {"schema_version":"1.0", "operation_id":uuid4(), "recovery_attempt_id":identity,
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
                self.save(op, "stopping")
                self.p.stop_many([record], force=spec["mode"] == "force-restart", deadline=deadline,
                                 _expected_runs={record["uuid"]:observation["xen_unique_id"]})
            self.save(op, "removed")
            record, records, absent = self.conditions(spec, deadline, target=False)
            if absent["presence"] != "absent":
                refuse("TARGET_CHANGED", "a domain appeared before recovery launch")
            if vm.has_network(record):
                self.p.prepare_dhcp(records, deadline=deadline)
            self.save(op, "launching")
            owner = {"kind":"recovery", "recovery_attempt_id":identity}
            self.p.start(record, _owner=owner, deadline=deadline)
            observed = self.e.observe(record, deadline)
            self.save(op, "observed")
            result = self.complete(op, observed)
            return {"attempt":op, "original_result":result, "current_observation":observed}
        except (PlatformError, OSError, ValueError) as error:
            code = error.error["code"] if isinstance(error, ExecutionError) else "XEN_EXECUTION_FAILED"
            uncertain = op["phase"] in ("launching", "observed")
            op["error"] = {"code":"RECONCILE_REQUIRED" if uncertain else code, "message":str(error),
                           "retryable":False, "details":{"operation_id":op["operation_id"],
                           "recovery_attempt_id":identity, "accepted":True}}
            try:
                self.save(op, "uncertain" if uncertain else "failed")
            except OSError:
                refuse("RECONCILE_REQUIRED", "accepted recovery journal could not be finalized",
                       details={"operation_id":op["operation_id"], "recovery_attempt_id":identity, "accepted":True})
            raise ExecutionError(**op["error"]) from error
