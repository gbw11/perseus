"""One-shot, read-only phone-first deployment check. No resident sampler.

Run in a trusted environment with the existing feed token (never an argv token).
Only local HTTP GET and a bounded systemctl show are used. No control requests,
session minting, service start/stop, raw Xen polling or extra MQTT connection.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import re
import stat
import subprocess

MARKER = Path("/etc/xenova/display-mobile-first")
RUNTIME_MARKER = Path("/run/xenova-display-mode/mobile")
UNITS = (
    "xenova-display-feed.service", "xenova-display-hdmi.service", "xenova-display.target",
    "xenova-display-graphics.service", "xenova-display-kiosk.service", "xenova-display-stream.service",
)
PROPERTIES = "Id,LoadState,ActiveState,SubState,PartOf,ExecStart,DropInPaths"
LIMIT = 262144


def option(command, name, value):
    return re.search(r"(?:^|\s)" + re.escape(name) + r"\s+" + re.escape(value) + r"(?=\s|;|$)", command) is not None


def guard_installed(paths):
    for item in paths.split():
        guards = {"50-mobile-first.conf": MARKER, "60-display-mode.conf": RUNTIME_MARKER}
        name = Path(item).name
        if name not in guards or not item.endswith("/xenova-display-hdmi.service.d/" + name):
            continue
        path = Path(item)
        try:
            metadata = path.lstat()
            if (not path.is_absolute() or not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
                    or metadata.st_mode & 0o022 or metadata.st_size > 4096):
                continue
            lines = [s.strip() for s in path.read_text().splitlines()
                     if s.strip() and not s.lstrip().startswith(("#", ";"))]
            if lines == ["[Unit]", f"ConditionPathExists=!{guards[name]}"]:
                return True
        except (OSError, UnicodeError):
            pass
    return False


def units(*, run=subprocess.run):
    result = run(["systemctl", "show", "--no-pager", f"--property={PROPERTIES}", "--", *UNITS],
                 capture_output=True, text=True, timeout=4, check=False)
    if len(result.stdout) > LIMIT:
        raise ValueError("unit response too large")
    parsed = {}
    for block in result.stdout.strip().split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if fields.get("Id") in UNITS:
            parsed[fields["Id"]] = fields
    if not parsed:
        raise ValueError("unit information unavailable")
    return parsed


def local_get(port, path, token, *, connect=http.client.HTTPConnection):
    # Fixed IPv4 loopback: no proxy, DNS, redirects or remote-token forwarding.
    connection = connect("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", path, headers={"Authorization": f"Bearer {token}"})
        response = connection.getresponse()
        body = response.read(LIMIT + 1)
        if response.status != 200 or len(body) > LIMIT:
            raise ValueError("local feed request rejected")
        return response.getheader("Content-Type", ""), body
    finally:
        connection.close()


def snapshot_summary(value):
    if not isinstance(value, dict) or value.get("type") != "display.presentation" or value.get("schema_version") != 1:
        raise ValueError("not a presentation snapshot")
    vms, outputs = value.get("state", {}).get("vms"), value.get("outputs")
    if (not isinstance(vms, list) or len(vms) != 3 or not all(isinstance(v, dict) for v in vms)
            or {v.get("role") for v in vms} != {"primary_drive", "backup_drive", "interaction"}
            or not isinstance(outputs, list) or len(outputs) != 2
            or not all(isinstance(o, dict) for o in outputs)
            or {o.get("output_id") for o in outputs} != {"monitor-1", "monitor-2"}):
        raise ValueError("unexpected presentation bindings")
    statuses = {"fresh", "stale", "disconnected", "unknown"}
    xen = [v.get("sources", {}).get("xen", {}).get("status") for v in vms]
    drive = value["state"].get("drive", {}).get("source", {}).get("status")
    if any(s not in statuses for s in xen) or drive not in statuses:
        raise ValueError("invalid source status")
    # Report existing evidence only. No inference about vehicle movement/readiness.
    return {"vm_xen_sources": dict(zip((v["role"] for v in vms), xen)), "drive_source": drive}


def inspect(*, port=8082, environment=None, marker=MARKER, query_units=units, get=local_get,
            check_guard=guard_installed):
    env = os.environ if environment is None else environment
    runtime_selection = marker == MARKER and (RUNTIME_MARKER.exists() or RUNTIME_MARKER.is_symlink())
    if runtime_selection:
        marker = RUNTIME_MARKER
    checks = {}
    report = {"schema_version": 1, "read_only": True, "checks": checks,
              "physical_phone_verified": False, "usb_verified": False}
    try:
        metadata = marker.lstat()
        checks["phone_first_marker"] = (stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0
            and not metadata.st_mode & (0o077 if runtime_selection else 0o022)
            and (marker.read_bytes() == b"mobile\n" if runtime_selection else metadata.st_size == 0))
    except OSError:
        checks["phone_first_marker"] = False
    observations = env.get("XENOVA_OBSERVATIONS_PATH", "")
    checks["shared_observations_configured"] = bool(observations and Path(observations).is_absolute())
    try:
        info = query_units()
        feed = info.get(UNITS[0], {})
        hdmi = info.get(UNITS[1], {})
        target = info.get(UNITS[2], {})
        checks["native_shared_feed"] = (feed.get("LoadState") == "loaded"
            and "uvicorn xenova_display.main:create_app" in feed.get("ExecStart", "")
            and option(feed.get("ExecStart", ""), "--host", "127.0.0.1")
            and option(feed.get("ExecStart", ""), "--port", "8082")
            and option(feed.get("ExecStart", ""), "--workers", "1")
            and "xenova-display.target" not in feed.get("PartOf", "").split())
        checks["feed_active"] = feed.get("ActiveState") == "active"
        checks["hdmi_guard_installed"] = (hdmi.get("LoadState") == "loaded"
            and check_guard(" ".join(p for p in hdmi.get("DropInPaths", "").split()
                if p.endswith("/60-display-mode.conf") == runtime_selection))
            and "xenova_display.native_session" in hdmi.get("ExecStart", ""))
        # Also fail for activating/deactivating/failed instead of calling a transition successful.
        checks["hdmi_inactive"] = hdmi.get("ActiveState") == "inactive"
        checks["legacy_graphics_inactive"] = all(
            info.get(u, {}).get("LoadState") == "not-found"
            or info.get(u, {}).get("ActiveState") == "inactive" for u in UNITS[3:])
        report["display_units"] = {u: {"load": info.get(u, {}).get("LoadState", "unknown"),
                                       "active": info.get(u, {}).get("ActiveState", "unknown")}
                                   for u in UNITS}
        # A target being active is never evidence of pixels/browser freshness.
        report["display_target_active"] = target.get("ActiveState") == "active"
    except Exception:
        checks["unit_query"] = False
    token = env.get("XENOVA_DISPLAY_TOKEN", "")
    checks["service_credential_available"] = bool(token.strip())
    if token.strip():
        try:
            content_type, body = get(port, "/display/mobile", token)
            checks["mobile_html_available"] = "text/html" in content_type and b'id="root"' in body
        except Exception:
            checks["mobile_html_available"] = False
        try:
            content_type, body = get(port, "/api/v1/display/presentation", token)
            if "application/json" not in content_type:
                raise ValueError("JSON required")
            report["sources"] = snapshot_summary(json.loads(body))
            checks["presentation_available"] = True
        except Exception:
            checks["presentation_available"] = False
    else:
        checks["mobile_html_available"] = checks["presentation_available"] = False
    report["local_runtime_ready"] = bool(checks) and all(checks.values())
    report["next_step"] = "349 USB/session path and 352 physical phone acceptance remain separate"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    # No token/URL/mode/apply arguments, no raw exception text or environment dump.
    report = inspect()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["local_runtime_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
