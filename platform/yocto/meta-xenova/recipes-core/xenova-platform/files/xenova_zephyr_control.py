"""Zephyr cooperative reboot: one durable control owner, removal then explicit same-asset start."""
from xenova_execution import ExecutionError, fingerprint, refuse
from xenova_runtime import PlatformError
import xenova_vm as vm

PHASES={"request-accepted","shutdown-requested","old-removed","new-launching","new-observed"}


def reboot(e,record,key):
    p=e.p
    request={"vm_id":record["uuid"],"action":"reboot","force":False}
    op=next((q for q in e.requests() if key is not None and q["idempotency_key"]==key),None)
    replay=op is not None
    warnings=[]
    if op and op["request"]!=request:
        refuse("IDEMPOTENCY_CONFLICT","control key has a different request")
    def result():
        return {"operation_id":op["operation_id"],"replayed":replay,
                "original_result":op["result"],"diagnostic_warnings":warnings}
    if op and op["phase"] in ("completed","failed"):
        if op["error"]:
            raise ExecutionError(**{**op["error"],"details":{**op["error"]["details"],"replayed":True}})
        return result()
    if op and op["host_boot_id"]!=e.host:
        refuse("TARGET_CHANGED","unfinished reboot belongs to a previous host boot")
    end=op.get("deadline_monotonic_ms",int((p.clock()+90)*1000))/1000 if op else p.clock()+90

    def save(phase):
        op["reboot_stage"]=phase
        e.save_request(op,phase)

    def conditions(*,check_deadline=True):
        e.gate()
        lifecycle,generation=e.lifecycle(record)
        intent=e.intent(record)
        from xenova_management import Manager
        m=Manager(p);current=m._state(record["uuid"],p.records(validate_assets=False))
        if (lifecycle!="REGISTERED" or generation!=op["lifecycle_generation"] or
                current["record"]!=record or fingerprint(record)!=op["configuration_sha256"]):
            refuse("TARGET_CHANGED","accepted reboot assets/lifecycle changed")
        if (not intent or intent["desired_state"]!="running" or
                intent["intent_revision"]!=op["intent_revision"] or
                intent["accepted_operation_id"]!=op["operation_id"]):
            refuse("INTENT_MISMATCH","accepted reboot intent was superseded")
        if any(q["vm_id"]==record["uuid"] and q["status"]!="SUCCEEDED" for q in m._operations()):
            refuse("OPERATION_IN_PROGRESS","asset operation owns reboot target",retryable=True)
        m._record_at(record,p.data/"instances"/record["id"])
        if check_deadline and p.clock()>=end:refuse("DEADLINE_EXCEEDED","original reboot deadline expired")

    receipt={}
    try:
        if op is None or op["phase"] in ("prepared","accepted"):
            # These phases precede physical submission. Register expected old incarnation first.
            observed=e.observe(record,min(end,p.clock()+15))
            if observed["execution_status"]!="managed":
                refuse("RECONCILE_REQUIRED","cooperative reboot needs a managed current execution")
            vm.validate_record(record,p.data/"instances"/record["id"])
            rows=p.matching(record,p.domains(deadline=end))
            rows=p.observed_target(record,rows,observed,end)
            if not rows:refuse("TARGET_CHANGED","reboot target disappeared")
            p.require_cooperative(record,rows[0],"reboot",end)
            op=e.accept(record,request,key,previous=op,receipt=receipt)
            op.update(configuration_sha256=fingerprint(record),deadline_monotonic_ms=int(end*1000),
                      old_execution=observed,old_domain=rows[0])
            save("request-accepted")
        stage=op.get("reboot_stage",op["phase"])
        if stage=="new-observed" and op.get("new_execution_owner")=={"kind":"user","operation_id":op["operation_id"]}:
            # A durable observed boundary is a historical execution result. A later
            # user stop must not turn it into a failed reboot or repeat the command.
            observation=op["new_execution"]
            if (op["vm_id"]!=record["uuid"] or
                    observation.get("configuration_revision")!=op["configuration_sha256"] or
                    observation.get("lifecycle_generation")!=op["lifecycle_generation"] or
                    observation.get("execution_status")!="managed" or
                    not vm.UUID.fullmatch(observation.get("execution_id","")) or
                    observation.get("host_boot_id")!=e.host):
                refuse("RECONCILE_REQUIRED","durable observed reboot context is invalid")
        elif stage in ("new-launching","new-observed"):
            conditions(check_deadline=False)
            observation=e.observe(record,p.clock()+15)
            run=e.run_record(record)
            owner={"kind":"user","operation_id":op["operation_id"]}
            if observation["execution_status"]!="managed" or not run or run.get("owner")!=owner:
                refuse("RECONCILE_REQUIRED","new launch may exist; no stop/create repeated")
            stage="new-observed"
        elif stage in ("request-accepted","shutdown-requested","old-removed"):
            # An expired submitted request may still be closed after confirmed removal.
            # This bounded read does not extend its permission to launch a replacement.
            conditions(check_deadline=stage!="shutdown-requested")
            if stage=="request-accepted":
                save("shutdown-requested")
                p.control(record,"reboot",op["old_domain"],deadline=end)
                warning=e.event("zephyr-shutdown-requested",record,op["operation_id"])
                if warning:warnings.append(warning)
                stage="shutdown-requested"
            if stage=="shutdown-requested":
                # Journal precedes command submission; replay ONLY observes, never re-sends.
                p.wait_reboot_removed(record,op["old_domain"],max(end,p.clock()+15))
                e.end_run(record)
                save("old-removed")
            conditions()
            current=e.observe(record,end)
            if current["presence"]!="absent":
                refuse("TARGET_CHANGED","domain appeared before explicit reboot start")
            p.prepare_dhcp(p.records(validate_assets=False),deadline=end)
            conditions()
            # Durable phase is written BEFORE launch preparation/create. Absence after a
            # crash in this phase is not evidence that an xl child cannot still create.
            save("new-launching")
            p.start(record,_owner={"kind":"user","operation_id":op["operation_id"]},deadline=end)
            observation=e.observe(record,end)
            run=e.run_record(record)
            if (observation["execution_status"]!="managed" or not run or
                    run.get("owner")!={"kind":"user","operation_id":op["operation_id"]}):
                refuse("RECONCILE_REQUIRED","new execution did not bind to reboot owner")
            op["new_execution"]=observation
            op["new_execution_owner"]=run["owner"]
            save("new-observed")
        else:
            refuse("RECONCILE_REQUIRED","unsupported reboot journal boundary")
        op["result"]={"accepted":True,"intent_revision":op["intent_revision"],
                      "old_execution_id":op["old_execution"]["execution_id"],
                      "execution_id":observation["execution_id"],"observation":observation,
                      "app_readiness":"unknown"}
        op["error"]=None
        try:e.save_request(op,"completed")
        except OSError as error:
            raise ExecutionError("RECONCILE_REQUIRED","reboot completion was not durably confirmed") from error
        warning=e.event("control-completed",record,op["operation_id"])
        if warning:warnings.append(warning)
        return result()
    except (PlatformError,OSError,ValueError) as error:
        if op is None:
            if receipt.get("operation") and isinstance(error,OSError):
                refuse("STORAGE_UNAVAILABLE","intent acceptance unconfirmed; no Xen action issued",
                       details={"operation_id":receipt["operation"]["operation_id"],"acceptance":"unconfirmed"})
            raise
        stage=op.get("reboot_stage")
        code=error.error["code"] if isinstance(error,ExecutionError) else "XEN_EXECUTION_FAILED"
        uncertain=stage in ("shutdown-requested","new-launching","new-observed")
        op["error"]={"code":"RECONCILE_REQUIRED" if uncertain else code,
                     "message":str(error),"retryable":False,
                     "details":{"operation_id":op["operation_id"],"intent_revision":op["intent_revision"],
                                "accepted":True,"reboot_stage":stage,"replayed":replay}}
        try:e.save_request(op,"uncertain" if uncertain else "failed")
        except OSError:
            refuse("RECONCILE_REQUIRED","accepted reboot journal could not be finalized",
                   details={"operation_id":op["operation_id"],"accepted":True})
        raise ExecutionError(**op["error"]) from error
