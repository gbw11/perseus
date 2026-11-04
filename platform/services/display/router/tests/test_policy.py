from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from xenova_display.main import create_app
from xenova_display.policy import DisplayPolicy, RenderAck, Selection, decide_vm
from test_feed import VM1, VM2, VM3, app, domains, settings, store  # noqa: F401 - pytest fixtures


@pytest.mark.parametrize("xen,source,app_state,expected", [
    (None, "unknown", "unknown", "waiting"),
    ("running", "stale", "ready", "stale"),
    ("crashed", "fresh", "ready", "crashed"),
    ("stopped", "fresh", "ready", "stopped"),
    ("stopping", "fresh", "ready", "stopping"),
    ("paused", "fresh", "ready", "paused"),
    ("running", "fresh", "failed", "app_error"),
    ("running", "fresh", "unknown", "app_waiting"),
    ("running", "fresh", "ready", "status"),
])
def test_policy_priorities(store, xen, source, app_state, expected):
    vm = store.snapshot()["vms"][0]
    vm.update(xen_state=xen, app_state=app_state)
    vm["sources"]["xen"]["status"] = source
    vm["sources"]["app"]["status"] = "fresh"
    assert decide_vm(vm)["kind"] == expected


def test_default_and_explicit_follow_do_not_conflate_mode_and_vm(settings, store):
    policy = DisplayPolicy(settings, store.epoch)
    state = store.snapshot()
    state["drive"].update(active_vm_id=VM2, mode="MANUAL", source={"status": "fresh"})
    default = policy.presentation(state)
    assert default["outputs"][0]["view"] == "overview"
    assert [o["screen"] for o in default["outputs"]] == ["instrument", "infotainment"]
    assert default["outputs"][1]["vm_id"] == VM1  # pinned
    policy.select(Selection(policy_epoch=policy.epoch, revision=1, selection="follow_active"))
    followed = policy.presentation(state)
    assert followed["outputs"][1]["vm_id"] == VM2
    state["drive"].update(mode="AUTO", active_vm_id=VM2)
    assert policy.presentation(state)["outputs"][1]["vm_id"] == VM2
    state["drive"]["source"]["status"] = "stale"
    state["drive"]["active_vm_id"] = VM1
    assert policy.presentation(state)["outputs"][1]["vm_id"] == VM2  # retain last confirmed target


def test_vm_fault_alone_never_switches_selection(settings, store):
    policy = DisplayPolicy(settings, store.epoch)
    policy.select(Selection(policy_epoch=policy.epoch, revision=1, selection="follow_active"))
    state = store.snapshot()
    state["vms"][0]["xen_state"] = "crashed"
    assert policy.presentation(state)["outputs"][1]["vm_id"] == VM1
    assert [o["screen"] for o in policy.presentation(state)["outputs"]] == ["instrument", "infotainment"]


def test_os_mismatch_and_unrecognized_xen_state_are_not_normal(store):
    vm = store.snapshot()["vms"][2]
    vm["os"]["observed"] = "ubuntu"
    assert decide_vm(vm)["kind"] == "os_mismatch"
    vm["xen_state"] = "not-a-known-state"
    assert decide_vm(vm)["kind"] == "waiting"


def test_selection_revision_and_renderer_ack(settings, store):
    policy = DisplayPolicy(settings, store.epoch)
    state = store.snapshot()
    current = policy.presentation(state)
    ack = RenderAck(**{key: current[key] for key in ("policy_epoch", "revision", "render_revision")})
    policy.acknowledge("monitor-1", ack, state)
    assert policy.presentation(state)["outputs"][0]["renderer_ack"]["stage"] == "dom_rendered"
    assert policy.presentation(state)["outputs"][1]["renderer_ack"] is None
    policy.select(Selection(policy_epoch=policy.epoch, revision=1, selection="pinned", vm_id=VM3))
    assert policy.presentation(state)["outputs"][1]["vm_id"] == VM3
    with pytest.raises(ValueError): policy.acknowledge("monitor-1", ack, state)
    with pytest.raises(ValueError): policy.select(Selection(policy_epoch="old", revision=2, selection="pinned", vm_id=VM1))


def test_render_revision_changes_for_new_run_and_not_for_age_only(settings, store):
    policy = DisplayPolicy(settings, store.epoch)
    state = store.snapshot()
    old = policy.presentation(state)["render_revision"]
    state["vms"][0]["sources"]["xen"]["age_ms"] += 100
    assert policy.presentation(state)["render_revision"] == old
    state["vms"][0]["run_id"] = "new-run"
    assert policy.presentation(state)["render_revision"] != old


def test_closed_renderer_ack_expires_even_when_content_has_not_changed(settings, store):
    policy = DisplayPolicy(settings, store.epoch, clock=store.clock)
    state = store.snapshot()
    current = policy.presentation(state)
    ack = RenderAck(**{key: current[key] for key in ("policy_epoch", "revision", "render_revision")})
    policy.acknowledge("monitor-1", ack, state)
    assert policy.presentation(state)["outputs"][0]["renderer_ack"] is not None
    store.now[0] += 3
    assert policy.presentation(state)["outputs"][0]["renderer_ack"] is None


def test_browser_cookie_cannot_change_policy_or_reveal_service_state(settings, store, tmp_path):
    class Driver:
        def list_domains(self): return domains()
    class NoMqtt:
        def __init__(self, s): pass
        def start(self): pass
        def stop(self): pass
    (tmp_path / "index.html").write_text("<html>display</html>")
    (tmp_path / "assets").mkdir()
    service = create_app(settings, driver=Driver(), store=store, mqtt_factory=NoMqtt, web_root=tmp_path)
    with TestClient(service) as client:
        assert client.get("/display/monitor-1").status_code == 401
        assert client.get("/display/mobile").status_code == 401
        assert client.post("/api/v1/display/session").status_code == 401
        headers = {"Authorization": "Bearer test-only-token"}
        session = client.post("/api/v1/display/session", headers=headers)
        assert session.status_code == 204
        assert "HttpOnly" in session.headers["set-cookie"]
        assert "test-only-token" not in session.headers["set-cookie"]
        assert client.get("/display/monitor-1").status_code == 200
        assert client.get("/display/mobile").status_code == 200
        p = client.get("/api/v1/display/presentation").json()
        assert [o["output_id"] for o in p["outputs"]] == ["monitor-1", "monitor-2"]
        assert "test-only-token" not in str(p)
        assert client.get("/api/v1/display/state").status_code == 401
        select = {"policy_epoch": p["policy_epoch"], "revision": p["revision"], "selection": "pinned", "vm_id": VM2}
        assert client.post("/api/v1/display/selection", json=select).status_code == 401
        ack = {key: p[key] for key in ("policy_epoch", "revision", "render_revision")}
        assert client.post("/api/v1/display/outputs/mobile/ack", json=ack).status_code == 401
        assert client.post("/api/v1/display/outputs/mobile/ack", headers=headers, json=ack).status_code == 404
        assert client.post("/api/v1/display/outputs/monitor-1/ack", json=ack).status_code == 204
        changed = client.post("/api/v1/display/selection", headers=headers, json=select)
        assert changed.status_code == 200
        assert client.post("/api/v1/display/outputs/monitor-1/ack", json=ack).status_code == 409
        assert client.post("/api/v1/display/selection", headers=headers, json=select).status_code == 409
