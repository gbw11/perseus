"""정지한 Linux 자원 변경. 기존 Manager 잠금·자산 journal을 사용하며 hotplug하지 않는다."""
import copy
import re
from first_install_contract import uuid4
from xenova_execution import fingerprint
import xenova_vm as vm

ACTION = "update-resources"


def validate(spec, normalize_uuid, fail):
    fields = {"vm_id", "vcpus", "memory_mib", "expected_lifecycle_generation",
              "expected_configuration_sha256"}
    if set(spec) != fields:
        fail("INVALID_SPEC", "exact resource values and expected lifecycle/configuration required")
    if type(spec["expected_lifecycle_generation"]) is not int or spec["expected_lifecycle_generation"] < 0:
        fail("INVALID_SPEC", "expected_lifecycle_generation must be a nonnegative integer")
    if any(type(spec[k]) is not int for k in ("vcpus", "memory_mib")):
        fail("INVALID_SPEC", "resource values must be integers, not booleans")
    if not 1 <= spec["vcpus"] <= 4 or not 128 <= spec["memory_mib"] <= 8192:
        fail("RESOURCE_UNSUPPORTED", "Linux resources require 1..4 vCPUs and 128..8192 MiB")
    digest = spec["expected_configuration_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        fail("INVALID_SPEC", "expected_configuration_sha256 must be a lowercase SHA256")
    return {**spec, "vm_id":normalize_uuid(spec["vm_id"])}


def guards(manager, tomb, operations, fail):
    """잠금을 얻었어도 남아 있는 물리 실행의 불확실성을 시간/PID만으로 없애지 않는다."""
    record = tomb["record"]
    e = manager.p.execution()
    intent = e.intent(record)
    if (not intent or intent["desired_state"] != "stopped" or
            intent["lifecycle_generation"] != tomb["generation"]):
        fail("INTENT_MISMATCH", "resource change requires applicable stopped intent")
    if any(o["vm_id"] == record["uuid"] and o["status"] != "SUCCEEDED" for o in operations):
        fail("OPERATION_IN_PROGRESS", "an incomplete asset operation owns this VM", retryable=True)
    from xenova_recovery import Recovery
    if any(q["request"]["vm_id"] == record["uuid"] and q["phase"] not in ("completed", "failed")
           and q["request"]["expected_host_boot_id"] == e.host
           and q["request"]["expected_lifecycle_generation"] == tomb["generation"]
           for q in Recovery(manager).attempts()):
        fail("RECONCILE_REQUIRED", "an unresolved recovery attempt owns this VM")
    if any(q["vm_id"] == record["uuid"] and q["phase"] not in ("completed", "failed")
           and q["host_boot_id"] == e.host and q["lifecycle_generation"] == tomb["generation"]
           for q in e.requests()):
        fail("RECONCILE_REQUIRED", "an unresolved control request owns this VM")
    run = e.run_record(record)
    if run and run.get("phase") in ("launching", "uncertain"):
        fail("RECONCILE_REQUIRED", "an unresolved launch owns this VM")
    directory = manager.data/"instances"/record["id"]
    manager._record_at(record, directory)
    domains = manager._stopped(record)
    manager.usage_check(directory, domains)


def mutate(manager, spec, execute, key, records, context, fail):
    operations = manager._operations()
    previous = next((o for o in operations if o["idempotency_key"] == key), None)
    if previous:
        context.update(operation_id=previous["operation_id"], replayed=True)
        if previous["action"] != ACTION or previous["request"] != spec:
            fail("IDEMPOTENCY_CONFLICT", "same key has a different action or request")
    tomb = manager._state(spec["vm_id"], records)
    if previous and previous["status"] == "SUCCEEDED":
        return {"operation":previous, "original_result":previous["result"],
                "current":manager._view(tomb, observe=True)}
    if tomb["lifecycle"] != "REGISTERED" or tomb["generation"] != spec["expected_lifecycle_generation"]:
        fail("TARGET_CHANGED", "resource target lifecycle changed")
    record = tomb["record"]
    if vm.guest_kind(record) != "linux":
        fail("RESOURCE_UNSUPPORTED", "this operation supports stopped Linux guests only")
    current = fingerprint(record)
    before = previous["snapshot"]["record"] if previous else record
    after = previous["updated_record"] if previous else {**copy.deepcopy(record),
                "vcpus":spec["vcpus"], "memory_mib":spec["memory_mib"]}
    if fingerprint(before) != spec["expected_configuration_sha256"]:
        fail("TARGET_CHANGED", "expected configuration does not match")
    if current not in (fingerprint(before), fingerprint(after)):
        fail("TARGET_CHANGED", "configuration changed outside this operation")
    vm.validate_identity(after)
    # instance.json is authoritative. If publication already happened, only close the journal;
    # a newer intent/start does not cause a second metadata write.
    published = previous and current == fingerprint(after) and before != after
    if not published:
        guards(manager, tomb, [o for o in operations if o is not previous], fail)
    if not execute:
        return {"plan":True, "action":ACTION, "before":before, "after":after,
                "configuration_sha256":fingerprint(after), "starts_guest":False}
    manager._writable()
    if previous is None:
        previous = {"schema_version":"1.0", "operation_id":uuid4(), "vm_id":spec["vm_id"],
                    "idempotency_key":key, "action":ACTION, "request":spec, "initial_spec":spec,
                    "spec_hash":fingerprint(spec), "expected_generation":tomb["generation"],
                    "snapshot":tomb, "updated_record":after, "status":"RUNNING",
                    "phase":"recorded", "result":None, "error":None}
        context["operation_id"] = previous["operation_id"]
        context["write_operation"] = True
        manager._save_op(previous)
    else:
        context["write_operation"] = True
    if not published:
        manager._write(manager.data/"instances"/record["id"]/"instance.json", after)
        manager.checkpoint("resources-published", previous)
    result = {"vm_id":record["uuid"], "generation":tomb["generation"],
              "before":{"vcpus":before["vcpus"], "memory_mib":before["memory_mib"]},
              "after":{"vcpus":after["vcpus"], "memory_mib":after["memory_mib"]},
              "configuration_sha256":fingerprint(after), "starts_guest":False}
    previous.update(status="SUCCEEDED", result=result, error=None)
    manager._save_op(previous, "complete")
    return {"operation":previous, "original_result":result,
            "current":manager._view({**tomb, "record":after}, observe=True)}
