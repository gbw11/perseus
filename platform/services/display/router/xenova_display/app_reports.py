"""Display-only parsing of the VM-scoped drive report contract.

The old MQTT library on the 253 branch does not parse VM/app topics. Compare
both IDs here in addition to its envelope validation. This also works with the
new shared parser, without upgrading unrelated gateway/Agent code.
"""
import json
import math

READINESS_REASONS = frozenset({"CONTROL_UNAPPLIED", "INPUT_UNAVAILABLE", "INPUT_STALE",
    "INPUT_INVALID", "STATE_PREPARING", "CALCULATION_FAILED", "OUTPUT_INVALID"})


def report_app(binding, topic, envelope, *, retained=False, qos=None, wire_size=None):
    """Return configured app ID, or None. Never asserts trusted execution/authority."""
    app_id = next((app for app in binding.report_app_ids if binding.app_topic(app) == topic), None)
    if app_id is None or envelope.get("app_id") != app_id or envelope.get("vm_id") != str(binding.vm_id):
        return None
    kind, payload = envelope["type"], envelope["payload"]
    if retained or kind not in {"app.heartbeat", "app.readiness"}:
        return None
    if kind == "app.heartbeat":
        if payload.get("state") not in ("active", "standby", "idle", "error"):
            return None
        if binding.app_topic_scope == "vm_scoped":
            uptime = payload.get("uptime_s")
            if type(uptime) not in (int, float) or (type(uptime) is float and not math.isfinite(uptime)) or uptime < 0:
                return None
    if binding.app_topic_scope == "vm_scoped":
        if qos is not None and (type(qos) is not int or qos != 0):
            return None
    elif kind != "app.heartbeat":
        return None
    if kind == "app.readiness":
        if wire_size is not None and wire_size > 1024:
            return None
        try:
            if len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()) > 1024:
                return None
        except (TypeError, ValueError):
            return None
        if not valid_readiness(payload):
            return None
    return app_id


def valid_readiness(payload):
    """MQTT v1 §6.2 closed payload, exact integer/decimal-string bounds."""
    if set(payload) != {"applied_generation", "progress_seq", "readiness", "reason"}:
        return False
    generation, progress = payload["applied_generation"], payload["progress_seq"]
    if type(generation) is not int or not 0 <= generation <= 0xffffffff:
        return False
    if (not isinstance(progress, str) or not progress.isascii() or not progress.isdecimal()
            or len(progress) > 20 or (len(progress) > 1 and progress[0] == "0")
            or int(progress) > 0xffffffffffffffff):
        return False
    state, reason = payload["readiness"], payload["reason"]
    if state not in ("PREPARING", "READY", "NOT_READY"):
        return False
    if state == "READY":
        return reason is None and generation > 0 and int(progress) > 0
    return isinstance(reason, str) and reason in READINESS_REASONS
