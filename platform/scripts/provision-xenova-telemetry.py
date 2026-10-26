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


def current(vm_id):
    from xenova_management import Manager
    manager = Manager()
    for attempt in range(151):
        result = manager.call("execution-context", {"vm_id": vm_id})
        error = result.get("error") or {}
        if (result["ok"] or error.get("code") != "OPERATION_IN_PROGRESS"
                or not error.get("retryable") or result.get("operation_id") is not None
                or error.get("details", {}).get("accepted") or attempt == 150):
            break
        # Only an unaccepted read is retried. Store writes/uncertain receipts
        # retain their existing refusal and execution identity checks.
        time.sleep(0.2)
    if not result["ok"]:
        raise ValueError("execution context unavailable")
    view = result["data"]["vm"]
    context = view.get("execution_context")
    if view["guest_kind"] != "zephyr" or view["shutdown_requested"] or not context or context["phase"] != "observed":
        raise ValueError("observed, non-quiescing Zephyr execution required")
    if context["host_boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip():
        raise ValueError("host identity changed")
    row = json.loads(subprocess.check_output(["xenova-domain-identity", "--domid", str(view["domid"])], text=True, timeout=3))
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
        wire = json.dumps(bootstrap, separators=(",", ":")).encode()
        if len(wire) > 2048:
            raise ValueError("bootstrap exceeds existing Store client limit")
        for _ in range(4):
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
                committed = self.lib.xs_transaction_end(self.handle, tx, False)
                transaction_open = False
                if committed:
                    return
                if C.get_errno() != errno.EAGAIN:
                    raise OSError("Store commit outcome uncertain; inspect receipt/context")
            finally:
                if transaction_open:
                    self.lib.xs_transaction_end(self.handle, tx, True)
        raise OSError("Store transaction contention; bounded retries exhausted")


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


def provision(context, credential, source_identity, receipt_path, previous_path=None, store=None, observe=None,
              channel="telemetry", guest_boot_receipt=None):
    if channel not in ("telemetry", "drive") or getattr(store, "channel", "telemetry") != channel:
        raise ValueError("bootstrap channel/store mismatch")
    shared_boot = None
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
    else:
        receipt = {"schema_version": "2.0-bootstrap", "channel": channel, "context": context, "credential_source": source_identity,
                   "boot_id": shared_boot or (previous["boot_id"] if previous else str(uuid.uuid4())),
                   "publisher_id": str(uuid.uuid4()), "receipt_id": str(uuid.uuid4()), "phase": "prepared"}
        save_receipt(receipt_path, receipt, True)
    bootstrap = {**credential, "boot_id": receipt["boot_id"], "publisher_id": receipt["publisher_id"],
                 "host_boot_id": context["host_boot_id"], "execution_id": context["execution_id"],
                 "receipt_id": receipt["receipt_id"]}
    old = {**previous["context"], **{k: previous[k] for k in ("boot_id", "publisher_id", "receipt_id")}} if previous else None
    if observe() != context:
        raise ValueError("execution changed before injection")
    try:
        store.apply(context, bootstrap, old)
        if observe() != context:
            raise ValueError("execution changed during injection; no success assertion")
    except (ValueError, OSError, subprocess.SubprocessError):
        receipt["phase"] = "uncertain"
        save_receipt(receipt_path, receipt)
        raise
    receipt["phase"] = "applied"
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
    parser.add_argument("--guest-boot-receipt", type=Path,
                        help="drive only: applied telemetry receipt of the same guest execution")
    parser.add_argument("--expected-host-boot-id", type=canonical, required=True)
    parser.add_argument("--expected-execution-id", type=canonical, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error("run on dom0 as root")
    parent = args.receipt.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        raise ValueError("receipt directory must be private and root-owned")
    context = current(args.vm_id)
    if context["host_boot_id"] != args.expected_host_boot_id or context["execution_id"] != args.expected_execution_id:
        raise ValueError("expected execution changed")
    credential, identity = credentials(args.credentials_file, args.vm_id)
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
        return provision(context, credential, identity, args.receipt, args.previous_receipt,
                         store=store, observe=lambda: current(args.vm_id), channel=args.channel,
                         guest_boot_receipt=args.guest_boot_receipt)
    finally:
        if store:
            store.close()
        os.close(lock_fd)


if __name__ == "__main__":
    local = Path(__file__).resolve().parents[1] / "yocto/meta-xenova/recipes-core/xenova-platform/files"
    sys.path.insert(0, str(local if local.is_dir() else Path("/usr/lib/xenova")))
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        raise SystemExit("Telemetry bootstrap refused; inspect private receipt and current execution; credentials not logged")
