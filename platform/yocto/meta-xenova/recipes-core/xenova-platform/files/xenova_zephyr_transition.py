"""명시적 정지 상태의 Zephyr 활성 프로필 전환. UUID/원본 firmware/주소 예약을 보존한다."""
import copy
import os
from pathlib import Path
from first_install_contract import uuid4
from xenova_execution import fingerprint
from xenova_resources import guards
import xenova_vm as vm
import xenova_zephyr as zephyr

ACTION="firmware-transition"


def validate(spec, normalize_uuid, fail):
    required={"vm_id","bundle","manifest_sha256","expected_lifecycle_generation",
              "expected_configuration_sha256","board_shutdown_policy"}
    if set(spec)!=required:
        fail("INVALID_SPEC","firmware-transition requires bundle/hash, expected context and explicit board policy")
    from xenova_resources import validate as validate_resources
    validate_resources({"vm_id":spec["vm_id"],"vcpus":1,"memory_mib":128,
        "expected_lifecycle_generation":spec["expected_lifecycle_generation"],
        "expected_configuration_sha256":spec["expected_configuration_sha256"]},normalize_uuid,fail)
    if not isinstance(spec["bundle"],str):
        fail("INVALID_SPEC","canonical absolute bundle path required")
    path=Path(spec["bundle"])
    if not path.is_absolute() or str(path)!=spec["bundle"] or ".." in path.parts:
        fail("INVALID_SPEC","canonical absolute bundle path required")
    if not isinstance(spec["manifest_sha256"],str) or not zephyr.DIGEST.fullmatch(spec["manifest_sha256"]):
        fail("INVALID_SPEC","expected manifest SHA256 required")
    if spec["board_shutdown_policy"] not in ("graceful","refuse","force-allowed"):
        fail("INVALID_SPEC","explicit board shutdown policy required")
    return {**spec,"vm_id":normalize_uuid(spec["vm_id"])}


def mutate(m,spec,execute,key,records,context,fail):
    m._asset_guard(context,spec['vm_id'])
    ops=m._operations()
    matches=[q for q in ops if q['idempotency_key']==key]
    if len(matches)>1:fail('RECONCILE_REQUIRED','operation selector is ambiguous')
    op=matches[0] if matches else None
    if op:
        context.update(operation_id=op["operation_id"],replayed=True)
        if op["action"]!=ACTION or op["request"]!=spec:
            fail("IDEMPOTENCY_CONFLICT","same key has a different action or request")
    tomb=m._state(spec["vm_id"],records)
    if op and op["status"]=="SUCCEEDED":
        return {"operation":op,"original_result":op["result"],"current":m._observe_view(tomb)}
    if tomb["lifecycle"]!="REGISTERED" or tomb["generation"]!=spec["expected_lifecycle_generation"]:
        fail("TARGET_CHANGED","firmware lifecycle changed")
    record=tomb["record"]
    if vm.guest_kind(record)!="zephyr":
        fail("RESOURCE_UNSUPPORTED","firmware-transition supports Zephyr only")
    before=op["snapshot"]["record"] if op else record
    if fingerprint(before)!=spec["expected_configuration_sha256"]:
        fail("TARGET_CHANGED","expected firmware configuration changed")
    with m._lifecycle_io(context,records):
        info=zephyr.bundle_info(Path(spec["bundle"]),spec["manifest_sha256"])
    network="hardware_profile" in info
    updated=copy.deepcopy(before)
    updated.update(active_firmware={k:info[k] for k in ("manifest","revision","version","sha256")},
        provisioning_profile=info['provisioning_profile'],
        hardware_profile=copy.deepcopy(zephyr.NET_PROFILE if network else zephyr.PROFILE),
        capabilities=dict(zephyr.NET_CAPABILITIES if network else zephyr.CAPABILITIES),
        shutdown_policy="cooperative-xenstore" if network else "explicit-force",
        board_shutdown_policy=spec["board_shutdown_policy"])
    if network and not updated.get("mac"):
        if op:
            updated.update(op["assigned"])
        else:
            address=m._allocate_ip({},records)
            mac="02:"+":".join(f"{v:02x}" for v in os.urandom(5))
            reserved=list(records.values())+[t["record"] for t in m._tombs().values()]+m._legacy_records()
            if any(row.get("mac")==mac for row in reserved) or any(q.get("assigned",{}).get("mac")==mac for q in ops):
                fail("IP_CONFLICT","allocated MAC collides; no publication")
            updated.update(mac=mac,ipv4=address,hostname=record["name"])
    vm.validate_identity(updated)
    if op and updated!=op["updated_record"]:
        fail("RECOVERY_REQUIRED","transition journal/profile mismatch")
    if record not in (before,updated):
        fail("TARGET_CHANGED","configuration superseded")
    published=bool(op and record==updated and before!=updated)
    if not published:
        guards(m,tomb,[q for q in ops if q is not op],fail,context,records)
    if updated.get('ipv4'):
        current_ops=m._operations()
        _,held=m._ip_inventory(records,excluding=record['uuid'],operations=current_ops)
        if updated['ipv4'] in held:
            fail('IP_CONFLICT','recorded transition reservation conflicts with another owner')
        others=[r for r in records.values() if r['uuid']!=record['uuid']]
        others += [t['record'] for t in m._tombs().values() if t['vm_id']!=record['uuid']]
        if (any(r.get('mac')==updated.get('mac') for r in others) or
                any(q['vm_id']!=record['uuid'] and q.get('assigned',{}).get('mac')==updated.get('mac') for q in current_ops)):
            fail('IP_CONFLICT','transition MAC conflicts with another current owner')
    if not execute:
        return {"plan":True,"action":ACTION,"before":before,"after":updated,
                "address_reserved":False,"starts_guest":False}
    m._writable()
    if op is None:
        op={"schema_version":"1.0","operation_id":uuid4(),"idempotency_key":key,
            "action":ACTION,"vm_id":spec["vm_id"],"request":spec,"initial_spec":spec,
            "spec_hash":fingerprint(spec),"expected_generation":tomb["generation"],
            "snapshot":tomb,"updated_record":updated,
            "assigned":{k:updated[k] for k in ("mac","ipv4","hostname") if k in updated},
            "status":"RUNNING","phase":"recorded","result":None,"error":None}
        context["operation_id"]=op["operation_id"];context["write_operation"]=True
        m._save_op(op)
    else:
        context["write_operation"]=True
    if context.get('asset_lease'):context['asset_lease'].bind(op)
    directory=m.data/"instances"/record["id"]
    if not published:
        m.p.reserve_space(info["logical_bytes"]*2,records)
        with m._lifecycle_io(context,records):
            zephyr.copy_revision(Path(spec["bundle"]),directory/"firmware",spec["manifest_sha256"])
            zephyr.validate_instance(updated,directory)
        m.checkpoint("firmware-copied",op)
        m._asset_guard(context,record['uuid'])
        m._stopped(record)
        if m._read(directory/'instance.json')!=record:
            fail('TARGET_CHANGED','configuration changed before firmware publication')
        from xenova_execution import short_lock
        with short_lock(m.p.runtime,'admission'):
            m._writable()
            m._write(directory/"instance.json",updated)
        m.checkpoint("profile-published",op)
    # A failed reload keeps this journal and its reservation; retry never selects a new address.
    with __import__("xenova_management").tool_output():
        m._prepare_dhcp(context)
    result={"vm_id":record["uuid"],"generation":tomb["generation"],
            "previous_firmware":before["active_firmware"],"active_firmware":updated["active_firmware"],
            "configuration_sha256":fingerprint(updated),"starts_guest":False,
            "app_readiness":"unknown","retained_reservation":op["assigned"]}
    op.update(status="SUCCEEDED",result=result,error=None);m._save_op(op,"complete")
    return {"operation":op,"original_result":result,"current":m._observe_view({**tomb,"record":updated})}
