"""Native display transitions with a bounded fake service manager, no host mutations."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from xenova_display import display_mode as mode


class FakeSystem:
    def __init__(self, guard):
        self.info = {
            mode.HDMI: dict(LoadState="loaded", ActiveState="active", ExecStart="python3 -m xenova_display.native_session", DropInPaths=str(guard)),
            mode.MOBILE: dict(LoadState="loaded", ActiveState="inactive", ExecStart="python3 -m xenova_display.mobile_access"),
            mode.FEED: dict(LoadState="loaded", ActiveState="active"),
        }
        self.calls, self.fail = [], set()

    def query(self):
        return {key: dict(value) for key, value in self.info.items()}

    def change(self, action, unit):
        assert unit in (mode.HDMI, mode.MOBILE)
        self.calls.append((action, unit))
        if (action, unit) in self.fail:
            raise RuntimeError("must-not-leak-credential")
        self.info[unit]["ActiveState"] = "active" if action == "start" else "inactive"


@pytest.fixture
def selector(tmp_path, monkeypatch):
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    guard = tmp_path / "guard.conf"
    guard.write_text("[Unit]\nConditionPathExists=!/run/xenova-display-mode/mobile\n")
    endpoint = tmp_path / "endpoint.json"
    endpoint.write_text(json.dumps({"url": "http://192.0.2.2:8085/display/mobile", "interface": "usb0"}))
    endpoint.chmod(0o600)
    system = FakeSystem(guard)
    now = [100.0]
    value = mode.Selector(system, runtime=runtime, guard=guard, legacy=tmp_path / "legacy",
        endpoint=endpoint, render_status=tmp_path / "renderer.json",
        clock=lambda: now[0], sleep=lambda delta: now.__setitem__(0, now[0] + delta))
    # Test ownership checks as a root installation even when pytest runs unprivileged.
    original = Path.lstat
    def metadata(path):
        info = original(path)
        return SimpleNamespace(st_uid=0, st_mode=info.st_mode, st_size=info.st_size)
    monkeypatch.setattr(Path, "lstat", metadata)
    value.fake, value.now = system, now
    return value


def ready(selector, ids=("monitor-1", "monitor-2"), **changes):
    output = dict(status="mapped", renderer_alive=True, physical_verified=True, renderer_verified_monotonic=selector.clock())
    output.update(changes)
    selector.render_status.write_text(json.dumps(dict(schema_version=1, backend="x11",
        updated_monotonic=selector.clock(), outputs={i: output for i in ids})))


def test_single_hdmi_output_is_ready_on_its_own(selector):
    # 412: one HDMI (monitor-1) is the target layout.
    selector.switch("mobile")
    ready(selector, ids=("monitor-1",))
    assert selector.switch("hdmi")["hdmi_renderer_ready"]


@pytest.mark.parametrize("ids", [(), ("monitor-2",), ("monitor-1", "monitor-3")])
def test_unknown_or_partial_output_sets_are_not_ready(selector, ids):
    ready(selector, ids=ids)
    assert not selector.hdmi_ready()


def test_mobile_then_hdmi_then_mobile_preserves_feed_and_selects_each_path(selector):
    assert selector.selected() == "hdmi"
    report = selector.switch("mobile")
    assert report["selected"] == "mobile" and not report["phone_connection_verified"]
    assert selector.fake.calls == [("start", mode.MOBILE), ("stop", mode.HDMI)]
    ready(selector)
    report = selector.switch("hdmi")
    assert report["selected"] == "hdmi" and report["hdmi_renderer_ready"]
    assert not report["physical_display_verified"]
    assert selector.fake.calls[-2:] == [("start", mode.HDMI), ("stop", mode.MOBILE)]
    selector.switch("mobile")
    assert selector.fake.info[mode.FEED]["ActiveState"] == "active"
    assert {u for _, u in selector.fake.calls} == {mode.HDMI, mode.MOBILE}


def test_repeated_mobile_does_not_restart_or_revoke_phone_session(selector):
    selector.switch("mobile")
    selector.fake.calls.clear()
    selector.switch("mobile")
    assert selector.fake.calls == [("stop", mode.HDMI)]


@pytest.mark.parametrize("problem", ["inactive-feed", "legacy", "missing-guard", "unloaded-guard", "legacy-hdmi", "transition"])
def test_preflight_failure_never_changes_services_or_marker(selector, problem):
    if problem == "inactive-feed": selector.fake.info[mode.FEED]["ActiveState"] = "inactive"
    elif problem == "legacy": selector.legacy.touch()
    elif problem == "missing-guard": selector.guard.unlink()
    elif problem == "unloaded-guard": selector.fake.info[mode.HDMI]["DropInPaths"] = ""
    elif problem == "legacy-hdmi": selector.fake.info[mode.HDMI]["ExecStart"] = "other-launcher"
    elif problem == "transition": selector.fake.info[mode.HDMI]["ActiveState"] = "activating"
    with pytest.raises((mode.ModeError, OSError)):
        selector.switch("mobile")
    assert selector.fake.calls == [] and not selector.marker.exists()


def test_failed_mobile_start_never_stops_hdmi(selector):
    selector.fake.fail.add(("start", mode.MOBILE))
    with pytest.raises(mode.ModeError, match="DISPLAY_MODE_FAILED_RESTORED"):
        selector.switch("mobile")
    assert ("stop", mode.HDMI) not in selector.fake.calls
    assert selector.selected() == "hdmi"
    assert selector.fake.info[mode.HDMI]["ActiveState"] == "active"


def test_invalid_endpoint_restores_new_listener_without_stopping_hdmi(selector):
    selector.endpoint.write_text('{"url":"http://user:password@192.0.2.2/display/mobile","interface":"usb0"}')
    with pytest.raises(mode.ModeError, match="MOBILE_ENDPOINT_NOT_READY"):
        selector.switch("mobile")
    assert selector.fake.calls == [("start", mode.MOBILE), ("stop", mode.MOBILE)]


def test_simple_service_active_does_not_race_endpoint_publication(selector):
    content = selector.endpoint.read_text()
    selector.endpoint.unlink()
    def publish_after_start(delta):
        assert ("stop", mode.HDMI) not in selector.fake.calls
        selector.now[0] += delta
        if selector.clock() >= 100.5:
            selector.endpoint.write_text(content)
            selector.endpoint.chmod(0o600)
    selector.sleep = publish_after_start
    assert selector.switch("mobile")["applied"]


def test_mobile_without_endpoint_times_out_without_stopping_hdmi(selector):
    selector.endpoint.unlink()
    with pytest.raises(mode.ModeError, match="MOBILE_ENDPOINT_NOT_READY"):
        selector.switch("mobile")
    assert ("stop", mode.HDMI) not in selector.fake.calls


def test_failed_hdmi_stop_restores_old_selection_and_services(selector):
    selector.fake.fail.add(("stop", mode.HDMI))
    with pytest.raises(mode.ModeError, match="DISPLAY_MODE_FAILED_RESTORED"):
        selector.switch("mobile")
    assert selector.selected() == "hdmi"
    assert selector.fake.info[mode.HDMI]["ActiveState"] == "active"
    assert selector.fake.info[mode.MOBILE]["ActiveState"] == "inactive"


@pytest.mark.parametrize("changes", [dict(status="renderer_error"), dict(renderer_alive=False),
    dict(physical_verified=False), dict(renderer_verified_monotonic=0)])
def test_hdmi_without_fresh_two_output_evidence_keeps_mobile_and_rolls_back(selector, changes):
    selector.switch("mobile")
    selector.fake.calls.clear()
    ready(selector, **changes)
    with pytest.raises(mode.ModeError, match="HDMI_RENDERER_NOT_READY"):
        selector.switch("hdmi")
    assert selector.selected() == "mobile"
    assert selector.fake.info[mode.MOBILE]["ActiveState"] == "active"
    assert selector.fake.info[mode.HDMI]["ActiveState"] == "inactive"
    assert ("stop", mode.MOBILE) not in selector.fake.calls


def test_rollback_failure_is_explicit_not_a_false_success(selector):
    selector.fake.fail.update({("stop", mode.HDMI), ("stop", mode.MOBILE)})
    with pytest.raises(mode.ModeError, match="ROLLBACK_INCOMPLETE"):
        selector.switch("mobile")


@pytest.mark.parametrize("kind", ["foreign", "writable", "symlink"])
def test_marker_never_overwrites_foreign_or_unsafe_file(selector, kind):
    if kind == "symlink": selector.marker.symlink_to(selector.runtime / "absent")
    else:
        selector.marker.write_bytes(b"not-ours" if kind == "foreign" else b"mobile\n")
        selector.marker.chmod(0o666 if kind == "writable" else 0o600)
    with pytest.raises((mode.ModeError, OSError)):
        selector.switch("mobile")
    assert selector.fake.calls == []


def test_status_is_read_only_and_default_returns_after_run_is_cleared(selector):
    selector.switch("mobile")
    selector.fake.calls.clear()
    assert selector.status()["selected"] == "mobile"
    assert not selector.fake.calls
    selector.marker.unlink()  # Simulate /run being cleared during reboot.
    assert selector.status()["selected"] == "hdmi"


def test_malformed_renderer_is_unready_instead_of_status_crash(selector):
    ready(selector)
    value = json.loads(selector.render_status.read_text())
    value["outputs"]["monitor-1"] = None
    selector.render_status.write_text(json.dumps(value))
    assert not selector.status()["hdmi_renderer_ready"]


def test_cli_errors_never_print_exception_secrets(monkeypatch, capsys):
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    def broken(): raise RuntimeError("secret-must-not-appear")
    monkeypatch.setattr(mode, "Selector", broken)
    assert mode.main(["status"]) == 1
    assert "secret-must-not-appear" not in capsys.readouterr().out


def test_nonroot_cli_rejected_without_query_or_mutation(monkeypatch, capsys):
    monkeypatch.setattr(os, "geteuid", lambda: 1234, raising=False)
    assert mode.main(["mobile"]) == 1
    assert json.loads(capsys.readouterr().out) == {"code": "ROOT_REQUIRED"}


def test_systemd_mutations_are_fixed_allowlist():
    for action, unit in [("restart", mode.HDMI), ("stop", mode.FEED), ("stop", "mosquitto.service")]:
        with pytest.raises(mode.ModeError):
            mode.Systemd().change(action, unit)


@pytest.mark.skipif(os.name != "posix", reason="Linux flock")
def test_concurrent_switches_are_rejected(selector):
    with mode.locked(selector.runtime):
        with pytest.raises(mode.ModeError, match="DISPLAY_MODE_BUSY"):
            with mode.locked(selector.runtime):
                pytest.fail("second switch acquired lock")
