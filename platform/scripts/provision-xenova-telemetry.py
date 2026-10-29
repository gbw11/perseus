#!/usr/bin/env python3
"""Plan/apply execution-bound Telemetry bootstrap; never configures a broker.

The only mutation is a dom0-owned, guest-read-only own-domain XenStore subtree
and a private receipt. Existing Manager identities/lifecycle are read-only.
Credentials are read from a private file and passed through libxenstore memory,
never shell argv, firmware, an environment dump or the public result.
"""
from __future__ import annotations
import argparse
import ctypes as C
import ctypes.util
import errno
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager


def canonical(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value or uuid.UUID(value).int == 0:
        raise ValueError("canonical non-nil UUID required")
    return value


def private_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        st = os.fstat(stream.fileno())
        if st.st_uid != os.geteuid() or st.st_mode & 0o077 or not stat.S_ISREG(st.st_mode):
            raise ValueError("private input ownership/mode invalid")
        return json.load(stream), {"inode": st.st_ino, "mtime_ns": st.st_mtime_ns, "bytes": st.st_size}


def credentials(path, vm_id):
    value, identity = private_json(path)
    if set(value) != {"vm_id", "broker_ipv4", "port", "username", "password", "trusted_internal_plaintext"}:
        raise ValueError("credential input fields invalid")
    if canonical(value["vm_id"]) != vm_id or value["username"] != vm_id:
        raise ValueError("credential VM/account mismatch")
    if str(ipaddress.IPv4Address(value["broker_ipv4"])) != value["broker_ipv4"]:
        raise ValueError("numeric broker IPv4 required")
    if type(value["port"]) is not int or not 1 <= value["port"] <= 65535:
        raise ValueError("broker port invalid")
    if value["trusted_internal_plaintext"] is not True:
        raise ValueError("TLS-required/untrusted deployment unsupported; no plaintext fallback")
    password = value["password"]
    # Runtime provisioning uses printable ASCII, avoiding parser escape ambiguity.
    if not isinstance(password, str) or not 1 <= len(password) <= 128 or any(not 33 <= ord(c) <= 126 or c in '\\"' for c in password):
        raise ValueError("provisioned password encoding unsupported")
    return value, identity


def current(vm_id, *, deadline=None, guard=None):
    from xenova_management import Manager
    manager = Manager()
    for attempt in range(151):
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError('execution observation budget expired')
            # execution-context는 별도 budget 인자가 없으므로 같은 get의 bounded 관측을 사용한다.
            result = manager.call('get', {'vm_id':vm_id, 'timeout_ms':max(1, min(15000, int(left*1000)))},
                                  budget_ms=max(1, min(15000, int(left*1000))))
        else:
            result = manager.call("execution-context", {"vm_id": vm_id})
        error = result.get("error") or {}
        if (result["ok"] or error.get("code") != "OPERATION_IN_PROGRESS"
                or not error.get("retryable") or result.get("operation_id") is not None
                or error.get("details", {}).get("accepted") or attempt == 150):
            break
        # Only an unaccepted read is retried. Store writes/uncertain receipts
        # retain their existing refusal and execution identity checks.
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('execution observation budget expired')
        time.sleep(min(.2, max(0, deadline-time.monotonic())) if deadline is not None else .2)
    if not result["ok"]:
        raise ValueError("execution context unavailable")
    view = result["data"]["vm"]
    context = view.get("execution_context")
    if view["guest_kind"] != "zephyr" or view["shutdown_requested"] or not context or context["phase"] != "observed":
        raise ValueError("observed, non-quiescing Zephyr execution required")
    if deadline is not None and (view.get('desired_state') != 'running' or view.get('intent_applicable') is not True):
        raise ValueError('receipt reconciliation requires current applicable running intent')
    if guard is not None:
        state = {key:view[key] for key in ('lifecycle','generation','intent_revision','desired_state','intent_applicable')}
        if guard and guard != state:
            raise ValueError('intent or lifecycle changed during receipt confirmation')
        guard.update(state)
    if context["host_boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip():
        raise ValueError("host identity changed")
    left = 3 if deadline is None else min(3, deadline-time.monotonic())
    if left <= 0:
        raise TimeoutError('incarnation observation budget expired')
    row = json.loads(subprocess.check_output(["xenova-domain-identity", "--domid", str(view["domid"])], text=True, timeout=left))
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError('incarnation observation budget expired')
    if str(row["xen_unique_id"]) != str(context["xen_unique_id"]) or row["domid"] != context["domid"]:
        raise ValueError("domain incarnation changed")
    return {"vm_id": vm_id, "host_boot_id": canonical(context["host_boot_id"]),
            "execution_id": canonical(context["execution_id"]), "domid": context["domid"],
            "xen_unique_id": str(context["xen_unique_id"])}


class Permission(C.Structure):
    _fields_ = [("id", C.c_uint), ("perms", C.c_uint)]


class XenStore:
    """Small binding to the already installed Xen libxenstore; no new runtime dep."""
    def __init__(self, channel="telemetry"):
        if channel not in ("telemetry", "drive"):
            raise ValueError("unsupported bootstrap channel")
        self.channel = channel
        library = ctypes.util.find_library("xenstore")
        if not library:
            raise ValueError("installed libxenstore unavailable")
        self.lib = C.CDLL(library, use_errno=True)
        self.free = C.CDLL(None).free
        self.free.argtypes = [C.c_void_p]
        signatures = {
            "xs_open": ([C.c_ulong], C.c_void_p), "xs_close": ([C.c_void_p], None),
            "xs_transaction_start": ([C.c_void_p], C.c_uint),
            "xs_transaction_end": ([C.c_void_p, C.c_uint, C.c_bool], C.c_bool),
            "xs_read": ([C.c_void_p, C.c_uint, C.c_char_p, C.POINTER(C.c_uint)], C.c_void_p),
            "xs_mkdir": ([C.c_void_p, C.c_uint, C.c_char_p], C.c_bool),
            "xs_write": ([C.c_void_p, C.c_uint, C.c_char_p, C.c_void_p, C.c_uint], C.c_bool),
            "xs_set_permissions": ([C.c_void_p, C.c_uint, C.c_char_p, C.POINTER(Permission), C.c_uint], C.c_bool),
            "xs_get_permissions": ([C.c_void_p, C.c_uint, C.c_char_p, C.POINTER(C.c_uint)], C.c_void_p),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.lib, name)
            function.argtypes, function.restype = args, result
        self.handle = self.lib.xs_open(0)
        if not self.handle:
            raise OSError("XenStore connection failed")
        self.quiescent = True

    def close(self):
        self.lib.xs_close(self.handle)

    def read(self, tx, path):
        length = C.c_uint()
        value = self.lib.xs_read(self.handle, tx, path.encode(), C.byref(length))
        if not value:
            if C.get_errno() == errno.ENOENT:
                return None
            raise OSError("XenStore read failed")
        try:
            return C.string_at(value, length.value)
        finally:
            self.free(value)

    def permissions(self, tx, path):
        count = C.c_uint()
        value = self.lib.xs_get_permissions(self.handle, tx, path.encode(), C.byref(count))
        if not value:
            raise OSError("XenStore permission observation failed")
        try:
            array = C.cast(value, C.POINTER(Permission))
            return [(array[i].id, array[i].perms) for i in range(count.value)]
        finally:
            self.free(value)

    def apply(self, context, bootstrap, previous=None):
        domid = context["domid"]
        parent = f"/local/domain/{domid}/data/xenova-{self.channel}"
        target = parent + "/bootstrap"
        wanted = [(0, 0), (domid, 1)]  # dom0 owns; only this guest can read.
        wire = json.dumps(bootstrap, ensure_ascii=False, separators=(",", ":")).encode('utf-8')
        if len(wire) > 2048:
            raise ValueError("bootstrap exceeds existing Store client limit")
        for _ in range(4):
            self.quiescent = False
            tx = self.lib.xs_transaction_start(self.handle)
            if not tx:
                raise OSError("Store transaction unavailable")
            transaction_open = True
            try:
                own = self.read(tx, f"/local/domain/{domid}/vm")
                if own != ("/vm/" + context["vm_id"]).encode():
                    raise ValueError("Store VM identity changed")
                existing_parent = self.read(tx, parent)
                if existing_parent is not None and self.permissions(tx, parent) != wanted:
                    raise ValueError("pre-existing bootstrap permissions differ; no overwrite")
                existing = self.read(tx, target)
                if existing is not None:
                    old = json.loads(existing)
                    keys = ("vm_id", "boot_id", "publisher_id", "execution_id", "receipt_id")
                    allowed = [bootstrap] + ([previous] if previous else [])
                    if not any(all(old.get(k) == candidate.get(k) for k in keys) for candidate in allowed):
                        raise ValueError("bootstrap belongs to another receipt/session")
                if not self.lib.xs_mkdir(self.handle, tx, parent.encode()):
                    raise OSError("Store subtree creation failed")
                perms = (Permission * 2)(Permission(0, 0), Permission(domid, 1))
                for path in (parent, target):
                    if path == target and not self.lib.xs_write(self.handle, tx, path.encode(), wire, len(wire)):
                        raise OSError("Store bootstrap write failed")
                    if not self.lib.xs_set_permissions(self.handle, tx, path.encode(), perms, 2):
                        raise OSError("Store permissions failed")
                    if self.permissions(tx, path) != wanted:
                        raise ValueError("Store permission verification failed")
                if self.channel=='telemetry':
                    # Existing one Store receiver is reused for a bounded clock
                    # exchange. No guest may write another VM's response/bootstrap.
                    clock=parent+'/clock'
                    for node,perms_value in ((clock,wanted),(clock+'/request',[(0,0),(domid,3)]),
                                             (clock+'/response',wanted)):
                        if self.read(tx,node) is None:
                            if not self.lib.xs_write(self.handle,tx,node.encode(),b'',0):
                                raise OSError('clock node creation failed')
                        perms=(Permission*2)(*(Permission(*v) for v in perms_value))
                        if not self.lib.xs_set_permissions(self.handle,tx,node.encode(),perms,2):
                            raise OSError('clock permission setup failed')
                        if self.permissions(tx,node)!=perms_value:raise ValueError('clock permissions differ')
                committed = self.lib.xs_transaction_end(self.handle, tx, False)
                transaction_open = False
                self.quiescent = True  # synchronous reply returned; commit success is a separate fact.
                if committed:
                    return
                if C.get_errno() != errno.EAGAIN:
                    raise OSError("Store commit outcome uncertain; inspect receipt/context")
            finally:
                if transaction_open:
                    self.quiescent = bool(self.lib.xs_transaction_end(self.handle, tx, True))
        raise OSError("Store transaction contention; bounded retries exhausted")

    def inspect(self, context, bootstrap):
        """한 read transaction에서 값·권한을 비교한다. 비밀 값이나 그 해시는 반환하지 않는다."""
        if not self.quiescent:
            raise ValueError("Store transaction still unresolved")
        parent = f"/local/domain/{context['domid']}/data/xenova-{self.channel}"
        wanted = [(0, 0), (context['domid'], 1)]
        wire = json.dumps(bootstrap, ensure_ascii=False, separators=(",", ":")).encode('utf-8')
        tx = self.lib.xs_transaction_start(self.handle)
        if not tx:
            raise OSError("Store observation transaction unavailable")
        try:
            checks = {"store_vm": self.read(tx, f"/local/domain/{context['domid']}/vm") ==
                      ("/vm/" + context['vm_id']).encode(),
                      "parent_present": self.read(tx, parent) is not None,
                      "bootstrap_matches": self.read(tx, parent + '/bootstrap') == wire}
            for label, path in (("parent_permissions", parent), ("bootstrap_permissions", parent + '/bootstrap')):
                checks[label] = self.permissions(tx, path) == wanted
            if self.channel == 'telemetry':
                for label, suffix, permissions in (("clock", '/clock', wanted),
                        ("clock_request", '/clock/request', [(0, 0), (context['domid'], 3)]),
                        ("clock_response", '/clock/response', wanted)):
                    path = parent + suffix
                    checks[label + '_present'] = self.read(tx, path) is not None
                    checks[label + '_permissions'] = self.permissions(tx, path) == permissions
        finally:
            if not self.lib.xs_transaction_end(self.handle, tx, True):
                raise OSError("Store observation transaction did not finish")
        return checks


def save_receipt(path, value, create=False):
    if create:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        save_receipt(temp, value, True)
        os.replace(temp, path)
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)


@contextmanager
def receipt_lock(path):
    """기존 CLI/Coordinator와 같은 실행별 잠금. 정체 중인 A가 B의 잠금을 잡지 않는다."""
    path = Path(path)
    parent = path.parent.lstat()
    if (path.parent.resolve() != path.parent or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid() or parent.st_mode & 0o077):
        raise ValueError("canonical private receipt directory required")
    fd = os.open(str(path) + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                or info.st_mode & 0o077):
            raise ValueError("private receipt lock ownership/mode invalid")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def bootstrap_payload(context, credential, receipt, channel):
    value = {**credential, "boot_id": receipt['boot_id'], "publisher_id": receipt['publisher_id'],
             "host_boot_id": context['host_boot_id'], "execution_id": context['execution_id'],
             "receipt_id": receipt['receipt_id']}
    if channel == 'telemetry':
        value.update(receipt.get('metadata', {}))
    elif receipt.get('drive_profile')=='warm-drive-1':
        apps=receipt['drive_apps']
        if set(apps)!={'autonomous','manual'} or len(set(apps.values()))!=2:raise ValueError('invalid app identities')
        value.update(drive_profile='warm-drive-1',auto_instance_id=canonical(apps['autonomous']),
                     manual_instance_id=canonical(apps['manual']))
    return value


def reinitialize_app(context,credential,source_identity,receipt_path,*,app,expected_instance,request_id,store,observe):
    """Root-authorized app identity CAS, not a VM operation or forced thread stop.

    Caller owns the existing receipt lock. Same request retries the same identity;
    uncertain Store effects require existing reconciliation, never another UUID.
    """
    canonical(expected_instance);canonical(request_id)
    if observe()!=context:raise ValueError('current execution required for app update/replay')
    receipt,_=private_json(receipt_path)
    if (app not in ('autonomous','manual') or getattr(store,'channel',None)!='drive' or
        receipt.get('channel')!='drive' or receipt.get('drive_profile')!='warm-drive-1' or
        receipt['context']!=context or receipt['credential_source']!=source_identity):
        raise ValueError('current warm drive receipt required')
    change=receipt.get('app_changes',{}).get(app)
    if change is None and receipt.get('app_change',{}).get('app')==app:change=receipt['app_change']
    if change and change['request_id']==request_id:
        if change['app']!=app or change['expected_instance']!=expected_instance:raise ValueError('request input changed')
        if receipt['phase']=='uncertain':raise ValueError('uncertain receipt requires reconciliation')
        if receipt['phase']=='applied':return {'configured':True,'app':app,'instance_id':receipt['drive_apps'][app],'replayed':True}
    else:
        if receipt['phase']!='applied' or receipt['drive_apps'][app]!=expected_instance:
            raise ValueError('app instance changed or prior update unresolved')
        receipt={**receipt,'drive_apps':dict(receipt['drive_apps']),'phase':'prepared','store_call_returned':False,
                 'pending_app':app,'app_update_base':dict(receipt['drive_apps'])}
        receipt['drive_apps'][app]=str(uuid.uuid4())
        receipt['app_changes']={**receipt.get('app_changes',{}),
            app:{'request_id':request_id,'app':app,'expected_instance':expected_instance}}
        save_receipt(receipt_path,receipt)
    if observe()!=context:raise ValueError('execution changed before app update')
    try:
        store.apply(context,bootstrap_payload(context,credential,receipt,'drive'))
        if observe()!=context:raise ValueError('execution changed during app update')
    except (ValueError,OSError,subprocess.SubprocessError):
        receipt['phase']='uncertain';receipt['store_call_returned']=getattr(store,'quiescent',False) is True
        save_receipt(receipt_path,receipt);raise
    receipt['phase']='applied';receipt['store_call_returned']=getattr(store,'quiescent',False) is True
    save_receipt(receipt_path,receipt)
    return {'configured':True,'app':app,'instance_id':receipt['drive_apps'][app],'replayed':False}


def reconcile(context, credential, source_identity, receipt_path, *, store, observe, execute=False,
              channel='telemetry', guest_boot_receipt=None, timeout_ms=15000, clock=time.monotonic,
              checkpoint=lambda phase: None):
    """원 receipt의 실제 적용을 재주입 없이 확인한다. 이 조회 예산은 새 공급 권한이 아니다.

    원 synchronous Store 호출의 반환 기록과 동일 lock의 배타 소유를 모두 요구한다.
    구형/중단 receipt에 그 근거가 없으면 값 일치만으로 늦은 효과가 없다고 추측하지 않는다.
    C API 호출 자체를 강제 취소하지 않으며 각 호출 뒤 전체 기한을 다시 확인한다.
    """
    if type(execute) is not bool or type(timeout_ms) is not int or not 0 < timeout_ms <= 15000:
        raise ValueError("invalid reconciliation execution/budget")
    if channel not in ('telemetry', 'drive') or getattr(store, 'channel', 'telemetry') != channel:
        raise ValueError("bootstrap channel/store mismatch")
    if channel == 'telemetry' and guest_boot_receipt is not None:
        raise ValueError('telemetry establishes its own guest boot receipt')
    path = Path(receipt_path)
    end = clock() + timeout_ms / 1000
    def budget():
        if clock() >= end:
            raise TimeoutError("receipt observation budget expired")
    with receipt_lock(path):
        receipt, _ = private_json(path)
        if receipt.get('schema_version') != '2.1-bootstrap' or receipt.get('phase') not in ('uncertain', 'applied'):
            raise ValueError("existing uncertain/applied receipt required")
        for field in ('receipt_id', 'boot_id', 'publisher_id'):
            canonical(receipt[field])
        checks = {'receipt_context': receipt.get('context') == context,
                  'credential_source': receipt.get('credential_source') == source_identity,
                  'channel': receipt.get('channel', 'telemetry') == channel,
                  'original_call_returned': receipt.get('store_call_returned') is True or receipt['phase'] == 'applied'}
        if channel == 'drive':
            if guest_boot_receipt is None:
                checks['guest_boot_authority'] = False
            else:
                authority, _ = private_json(guest_boot_receipt)
                checks['guest_boot_authority'] = (authority.get('channel', 'telemetry') == 'telemetry'
                    and authority.get('phase') == 'applied' and authority.get('context') == context
                    and authority.get('boot_id') == receipt['boot_id'])
        budget()
        checks['current_execution'] = observe() == context
        budget()
        expected = bootstrap_payload(context, credential, receipt, channel)
        if all(checks.values()):
            observed = store.inspect(context, expected)
            required = {'store_vm', 'parent_present', 'bootstrap_matches', 'parent_permissions', 'bootstrap_permissions'}
            if channel == 'telemetry':
                required |= {name + suffix for name in ('clock','clock_request','clock_response')
                             for suffix in ('_present','_permissions')}
            if (not isinstance(observed, dict) or set(observed) != required
                    or any(type(v) is not bool for v in observed.values())):
                raise ValueError("complete Store comparison required")
            checks.update(observed)
        budget()
        checks['execution_after_observation'] = observe() == context
        budget()
        result = {'action': 'reconcile', 'context': context, 'receipt_id': receipt['receipt_id'],
                  'checks': checks, 'reasons': [name for name, matches in checks.items() if not matches],
                  'status': 'already_applied' if receipt['phase'] == 'applied' else 'confirmable',
                  'executed': False, 'store_writes': 0}
        if result['reasons']:
            result['status'] = 'blocked'
            return result
        if not execute:
            return result
        checkpoint('before-guard')
        budget()
        current_receipt, _ = private_json(path)
        if current_receipt != receipt or observe() != context:
            raise ValueError("receipt/execution changed before confirmation")
        if channel == 'drive':
            current_authority, _ = private_json(guest_boot_receipt)
            if current_authority != authority:
                raise ValueError("guest boot authority changed before confirmation")
        final_checks = store.inspect(context, expected)
        if final_checks != observed or observe() != context:
            raise ValueError("Store/execution changed before confirmation")
        budget()
        confirmed = {**receipt, 'phase': 'applied'}
        confirmed.setdefault('reconciliation', {'kind': 'observed-applied',
            'original_phase': receipt['phase'],
            'host_boot_id': context['host_boot_id'], 'execution_id': context['execution_id'],
            'observed_monotonic_ms': int(clock() * 1000)})
        save_receipt(path, confirmed)
        checkpoint('recorded')
        return {**result, 'status': 'applied', 'executed': True}


def provision(context, credential, source_identity, receipt_path, previous_path=None, store=None, observe=None,
              channel="telemetry", guest_boot_receipt=None, metadata=None,drive_profile=None):
    if channel not in ("telemetry", "drive") or getattr(store, "channel", "telemetry") != channel:
        raise ValueError("bootstrap channel/store mismatch")
    if drive_profile not in (None,'verified-drive-1','warm-drive-1') or (channel!='drive' and drive_profile is not None):
        raise ValueError('drive profile requires drive channel')
    shared_boot = None
    if channel=='telemetry' and metadata is None and isinstance(store,XenStore):
        if receipt_path.exists():
            # Retry reuses this session's committed metadata. Current collector
            # availability is not a new VM execution or a reason to rotate v1 IDs.
            existing,_=private_json(receipt_path);metadata=existing.get('metadata',{})
        else:
            from xenova_telemetry_support import metadata as execution_metadata
            metadata=execution_metadata(context)
    if channel == "drive":
        if guest_boot_receipt is None:
            raise ValueError("drive channel requires this execution's applied telemetry boot receipt")
        authority, _ = private_json(guest_boot_receipt)
        if (authority.get("channel", "telemetry") != "telemetry" or authority["context"] != context
                or authority["phase"] != "applied"):
            raise ValueError("guest boot receipt belongs to another execution/channel")
        shared_boot = canonical(authority["boot_id"])
    elif guest_boot_receipt is not None:
        raise ValueError("telemetry channel establishes its own guest boot receipt")
    previous = None
    if previous_path:
        previous, _ = private_json(previous_path)
        if previous.get("channel", "telemetry") != channel or previous["context"] != context or previous["phase"] != "applied":
            raise ValueError("previous session is not this execution")
    if receipt_path.exists():
        receipt, _ = private_json(receipt_path)
        if receipt.get("channel", "telemetry") != channel or receipt["context"] != context or receipt["credential_source"] != source_identity:
            raise ValueError("receipt context/input changed")
        if receipt["phase"] not in ("prepared", "applied"):
            raise ValueError("uncertain receipt requires inspection")
        if shared_boot and receipt["boot_id"] != shared_boot:
            raise ValueError("guest boot authority changed")
        if drive_profile is not None and receipt.get('drive_profile','verified-drive-1')!=drive_profile:
            raise ValueError('existing receipt profile cannot be silently changed')
    else:
        receipt = {"schema_version": "2.1-bootstrap", "metadata":metadata or {}, "channel": channel, "context": context, "credential_source": source_identity,
                   "boot_id": shared_boot or (previous["boot_id"] if previous else str(uuid.uuid4())),
                   "publisher_id": str(uuid.uuid4()), "receipt_id": str(uuid.uuid4()), "phase": "prepared"}
        if channel=='drive' and drive_profile=='warm-drive-1':
            receipt.update(drive_profile=drive_profile,drive_apps={app:str(uuid.uuid4()) for app in ('autonomous','manual')})
        save_receipt(receipt_path, receipt, True)
    bootstrap = bootstrap_payload(context, credential, receipt, channel)
    if metadata is not None and receipt.get('metadata',{})!=metadata:
        raise ValueError('execution metadata changed; explicit session refresh required')
    if channel=='telemetry' and receipt['phase']=='prepared' and bootstrap.get('persistent_crash') and isinstance(store,XenStore):
        from xenova_telemetry_support import register_capture
        try:
            register_capture(context,receipt_path)
            receipt['capture_setup']={'status':'configured'}
        except (OSError,ValueError,KeyError) as error:
            # Optional evidence setup must not block the shared guest boot/v1
            # provisioning chain. Keep identities and a durable failure fact.
            receipt['metadata']['persistent_crash']=False
            bootstrap['persistent_crash']=False
            receipt['capture_setup']={'status':'unavailable','error':type(error).__name__,
                'errno':getattr(error,'errno',None)}
            save_receipt(receipt_path,receipt)
    old = {**previous["context"], **{k: previous[k] for k in ("boot_id", "publisher_id", "receipt_id")}} if previous else None
    if observe() != context:
        raise ValueError("execution changed before injection")
    try:
        store.apply(context, bootstrap, old)
        if observe() != context:
            raise ValueError("execution changed during injection; no success assertion")
    except (ValueError, OSError, subprocess.SubprocessError):
        receipt["phase"] = "uncertain"
        receipt['store_call_returned'] = getattr(store, 'quiescent', False) is True
        save_receipt(receipt_path, receipt)
        raise
    receipt["phase"] = "applied"
    receipt['store_call_returned'] = getattr(store, 'quiescent', False) is True
    save_receipt(receipt_path, receipt)
    return {"configured": True, "context": context, "boot_id": receipt["boot_id"],
            "publisher_instance_id": receipt["publisher_id"], "receipt_id": receipt["receipt_id"],
            "credentials": "private Store memory only", "receipt": str(receipt_path)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=("telemetry", "drive"), default="telemetry",
                        help="separate Store node, account/listener and receipt for v1 app or v2 Publisher")
    parser.add_argument("--vm-id", required=True, type=canonical)
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--previous-receipt", type=Path)
    parser.add_argument('--drive-profile',choices=('verified-drive-1','warm-drive-1'))
    parser.add_argument('--reinitialize-app',choices=('autonomous','manual'),help='trusted app identity update; does not force stop')
    parser.add_argument('--expected-app-instance',type=canonical)
    parser.add_argument('--app-request-id',type=canonical,help='stable key reused after response loss')
    parser.add_argument("--guest-boot-receipt", type=Path,
                        help="drive only: applied telemetry receipt of the same guest execution")
    parser.add_argument("--expected-host-boot-id", type=canonical, required=True)
    parser.add_argument("--expected-execution-id", type=canonical, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument('--reconcile', action='store_true',
                        help='inspect an existing uncertain receipt; --execute only records verified application')
    parser.add_argument('--timeout-ms', type=int, default=15000,
                        help='reconciliation observation budget, 1..15000; never extends an original write')
    args = parser.parse_args(argv)
    if args.drive_profile is not None and (args.reconcile or args.reinitialize_app):
        parser.error('--drive-profile selects initial provisioning, not reconciliation/app reset')
    if args.reinitialize_app and (args.channel!='drive' or args.reconcile or args.previous_receipt or
                                  not args.expected_app_instance or not args.app_request_id):
        parser.error('app update requires drive channel, expected instance and stable request ID')
    if not args.reinitialize_app and (args.expected_app_instance or args.app_request_id):parser.error('app update options require --reinitialize-app')
    if os.geteuid() != 0:
        parser.error("run on dom0 as root")
    parent = args.receipt.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        raise ValueError("receipt directory must be private and root-owned")
    if args.reconcile and (args.previous_receipt is not None or not 0 < args.timeout_ms <= 15000):
        raise ValueError('reconciliation requires bounded observation and no session replacement')
    deadline = time.monotonic() + args.timeout_ms / 1000 if args.reconcile else None
    guard = {}
    context = current(args.vm_id, deadline=deadline, guard=guard) if args.reconcile else current(args.vm_id)
    if context["host_boot_id"] != args.expected_host_boot_id or context["execution_id"] != args.expected_execution_id:
        raise ValueError("expected execution changed")
    credential, identity = credentials(args.credentials_file, args.vm_id)
    if args.reconcile:
        store = XenStore(args.channel)
        try:
            remaining = int((deadline-time.monotonic())*1000)
            if remaining <= 0:
                raise TimeoutError('receipt reconciliation budget expired')
            return reconcile(context, credential, identity, args.receipt, store=store,
                observe=lambda:current(args.vm_id, deadline=deadline, guard=guard), execute=args.execute,
                channel=args.channel, guest_boot_receipt=args.guest_boot_receipt, timeout_ms=remaining)
        finally:
            store.close()
    if not args.execute:
        return {"action": "plan", "channel": args.channel, "context": context, "receipt": str(args.receipt),
                "mutation": "own-domain read-only bootstrap; broker/services/VM lifecycle unchanged"}
    lock_fd = os.open(str(args.receipt) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    store = None
    try:
        lock_stat = os.fstat(lock_fd)
        if lock_stat.st_uid != os.geteuid() or lock_stat.st_mode & 0o077 or not stat.S_ISREG(lock_stat.st_mode):
            raise ValueError("private receipt lock ownership/mode invalid")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = XenStore(args.channel)
        if args.reinitialize_app:
            return reinitialize_app(context,credential,identity,args.receipt,app=args.reinitialize_app,
                expected_instance=args.expected_app_instance,request_id=args.app_request_id,store=store,
                observe=lambda:current(args.vm_id))
        return provision(context, credential, identity, args.receipt, args.previous_receipt,
                         store=store, observe=lambda: current(args.vm_id), channel=args.channel,
                         guest_boot_receipt=args.guest_boot_receipt,drive_profile=args.drive_profile)
    finally:
        if store:
            store.close()
        os.close(lock_fd)


if __name__ == "__main__":
    local = Path(__file__).resolve().parents[1] / "yocto/meta-xenova/recipes-core/xenova-platform/files"
    sys.path.insert(0, str(local if local.is_dir() else Path("/usr/lib/xenova")))
    try:
        result = main()
        print(json.dumps(result, ensure_ascii=False))
        if result.get('status') == 'blocked':
            raise SystemExit(3)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        raise SystemExit("Telemetry bootstrap refused; inspect private receipt and current execution; credentials not logged")
