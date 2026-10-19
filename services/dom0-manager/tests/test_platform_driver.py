"""PlatformDriver / PlatformProvisioner 를 플랫폼 봉투를 흉내 내는 FakeManager 로 검증한다 (S15P31E110-115)."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from conftest import VM_A, VM_B, create_body
from dom0_manager.config import Settings
from dom0_manager.drivers.platform import PlatformDriver, PlatformProvisioner, instance_name, map_platform_state, view_to_domain
from dom0_manager.drivers.xl import Xl
from dom0_manager.errors import DriverError, DriverTimeout, InvalidArgument, PlatformUnavailable, VMNotFound
from dom0_manager.main import create_app
from dom0_manager.models import VmState
from test_xl import XL_INFO, FakeRunner

TEMPLATE = {"template_id": "ubuntu-24.04-arm64", "template_version": "1.0.0", "guest_kind": "linux"}
ZEPHYR = {"template_id": "zephyr-xenvm-starter-arm64", "template_version": "1.0.0", "guest_kind": "zephyr"}


def _ok(data, **extra):
    return {"schema_version": "1.0", "ok": True, "request_id": "r", "operation_id": None, "replayed": False, "data": data, "error": None, **extra}


def _err(code, message="", retryable=False):
    return {"schema_version": "1.0", "ok": False, "request_id": "r", "operation_id": None, "replayed": False, "data": None,
            "error": {"code": code, "message": message, "retryable": retryable, "details": {}}}


class FakeManager:
    """xenova_management.Manager.call 의 봉투·동작을 최소로 흉내 낸다. accept_vm_id=False 면 지금의 플랫폼처럼 create 가 vm_id 를 거부한다."""

    data = Path("/srv/xenova")

    def __init__(self, *, accept_vm_id=True, templates=(TEMPLATE, ZEPHYR)):
        self.accept_vm_id = accept_vm_id
        self.templates = list(templates)
        self.registry: dict[str, dict] = {}  # vm_id -> view
        self.calls: list[tuple] = []
        self.fail_next: dict | None = None
        self.next_domid = 1

    def register(self, vm_id, *, name="vm", vcpus=1, memory_mib=512, ipv4="192.168.77.30", guest_kind="linux", running=False, xen_state=None, lifecycle="REGISTERED"):
        view = {"vm_id": str(vm_id), "name": name, "lifecycle": lifecycle, "generation": 0, "registered": lifecycle == "REGISTERED",
                "configuration": {"vcpus": vcpus, "memory_mib": memory_mib, "ipv4": ipv4, "guest_kind": guest_kind},
                "xen_state": xen_state or ("running" if running else "stopped"), "domid": None, "app_readiness": "unknown"}
        if running or (xen_state and xen_state != "stopped"):
            view["domid"] = self.next_domid
            self.next_domid += 1
        self.registry[str(vm_id)] = view
        return view

    def call(self, action, spec=None, *, execute=False, idempotency_key=None, request_id=None):
        self.calls.append((action, dict(spec or {}), execute, idempotency_key))
        if self.fail_next:
            fail, self.fail_next = self.fail_next, None
            return _err(**fail)
        spec = spec or {}
        if action == "templates":
            return _ok({"templates": self.templates})
        if action == "list":
            lifecycle = spec.get("lifecycle", "REGISTERED")
            views = [v for v in self.registry.values() if lifecycle == "all" or v["lifecycle"] == lifecycle]
            return _ok({"vms": views})
        if action == "get":
            view = self.registry.get(spec.get("vm_id"))
            return _ok({"vm": view}) if view else _err("VM_NOT_FOUND", "registered VM not found")
        if action == "create":
            if "vm_id" in spec and not self.accept_vm_id:
                return _err("INVALID_SPEC", "name, template_id, template_version, vcpus and memory_mib required")
            if not execute:
                return _ok({"plan": spec})
            if any(v["configuration"].get("ipv4") == spec.get("ipv4") for v in self.registry.values() if spec.get("ipv4")):
                return _err("IP_CONFLICT", "address already registered")
            vm_id = spec.get("vm_id") or "99999999-9999-4999-8999-999999999999"
            view = self.register(vm_id, name=spec["name"], vcpus=spec["vcpus"], memory_mib=spec["memory_mib"], ipv4=spec.get("ipv4"))
            return _ok({"vm": view}, operation_id="op-1")
        if action in ("start", "stop"):
            view = self.registry.get(spec.get("vm_id"))
            if not view:
                return _err("VM_NOT_FOUND", "registered VM not found")
            if action == "start":
                if view["domid"] is None:
                    view["domid"] = self.next_domid
                    self.next_domid += 1
                    view["xen_state"] = "running"
            else:
                if view["xen_state"] == "hang" and not spec.get("force"):
                    return _err("ARTIFACT_INVALID", "guest operation deadline exceeded")
                view["domid"] = None
                view["xen_state"] = "stopped"
            return _ok({"vm": view})
        if action == "retire":
            view = self.registry.get(spec.get("vm_id"))
            if not view:
                return _err("VM_NOT_FOUND", "registered VM not found")
            if view["domid"] is not None:
                return _err("VM_NOT_STOPPED", "VM must be stopped; no shutdown is performed")
            if execute:
                view["lifecycle"] = "RETIRED"
                view["registered"] = False
            return _ok({"vm": view}, operation_id="op-2")
        return _err("INVALID_SPEC", "unsupported management action")


@pytest.fixture
def manager():
    return FakeManager()


@pytest.fixture
def driver(manager):
    runner = FakeRunner({("xl", "info"): XL_INFO})
    return PlatformDriver(manager, Xl(runner))


@pytest.fixture
def client(manager, driver):
    app = create_app(Settings(driver="platform", log_level="WARNING"), driver=driver, provisioner=PlatformProvisioner(manager))
    return TestClient(app)


# ---- 순수 함수 ----


@pytest.mark.parametrize("xen,expected", [
    ("running", VmState.running), ("blocked", VmState.running), ("unknown", VmState.running),
    ("paused", VmState.paused), ("shutdown", VmState.stopping), ("dying", VmState.stopping),
    ("crashed", VmState.crashed), ("stopped", None), (None, None),
])
def test_map_platform_state(xen, expected):
    assert map_platform_state(xen) == expected


def test_unknown_platform_state_raises():
    with pytest.raises(DriverError):
        map_platform_state("weird")


def test_view_without_domid_is_not_a_domain(manager):
    assert view_to_domain(manager.register(VM_A)) is None
    d = view_to_domain(manager.register(VM_B, running=True, vcpus=2, memory_mib=256, name="x"))
    assert (d.vm_id, d.state, d.vcpus, d.memory_mib, d.domid, d.name) == (VM_B, VmState.running, 2, 256, 1, "x")


@pytest.mark.parametrize("template,expected", [
    ("ubuntu-24.04-arm64", "ubuntu-24-04-arm64-111111111111"),
    ("zephyr-xenvm-starter-arm64", "zephyr-xenvm-starter-arm64-111111111111"),
    ("UBUNTU", "ubuntu-111111111111"),
    ("24.04", "vm-24-04-111111111111"),
    ("a" * 60, "a" * 27 + "-111111111111"),
])
def test_instance_name_follows_registry_rule(template, expected):
    name = instance_name(template, VM_A)
    assert name == expected and len(name) <= 40


# ---- Driver 조회·명령 ----


def test_list_only_returns_live_domains(driver, manager):
    manager.register(VM_A)  # 등록만, Xen 에 없음
    manager.register(VM_B, running=True)
    assert [d.vm_id for d in driver.list_domains()] == [VM_B]
    assert driver.has_domain(VM_B) and not driver.has_domain(VM_A)
    with pytest.raises(VMNotFound):
        driver.get_domain(VM_A)


def test_stop_is_idempotent_and_maps_timeout(driver, manager):
    driver.shutdown(VM_A, grace_s=90)  # 미등록 → 그냥 성공
    manager.register(VM_A, running=True, xen_state="hang")
    with pytest.raises(DriverTimeout):
        driver.shutdown(VM_A, grace_s=90)
    assert manager.calls[-1][:2] == ("stop", {"vm_id": str(VM_A), "force": False})
    driver.destroy(VM_A)
    assert manager.calls[-1][1]["force"] is True


def test_health_requires_xl_and_registry(driver, manager):
    assert driver.health() is True
    manager.fail_next = {"code": "STORAGE_UNAVAILABLE", "message": "registry unavailable"}
    assert driver.health() is False
    broken = PlatformDriver(manager, Xl(FakeRunner({})))
    assert broken.health() is False


def test_platform_error_codes_map_to_http(client, manager):
    manager.register(VM_A, running=True)
    for code, status in [("STORAGE_UNAVAILABLE", 503), ("OPERATION_IN_PROGRESS", 409), ("INVALID_SPEC", 400), ("ARTIFACT_INVALID", 500)]:
        manager.fail_next = {"code": code, "message": "x"}
        assert client.get("/api/v1/vms").status_code == status, code
    manager.fail_next = {"code": "ARTIFACT_INVALID", "message": "guest operation deadline exceeded"}
    assert client.post(f"/api/v1/vms/{VM_A}/stop").status_code == 504


# ---- API 끝에서 끝 (registry + start/stop/retire) ----


def test_create_registers_then_starts(client, manager):
    created = client.post("/api/v1/vms", json=create_body(VM_A, template_id=TEMPLATE["template_id"], template_version="1.0.0"))
    assert created.status_code == 201, created.text
    assert created.json() == {"vm_id": str(VM_A), "state": "running", "vcpus": 1, "memory_mib": 256}
    actions = [c[0] for c in manager.calls]
    assert actions.index("create") < actions.index("start")
    create_call = next(c for c in manager.calls if c[0] == "create")
    assert create_call[1]["vm_id"] == str(VM_A) and create_call[1]["ipv4"] == "192.168.77.2"
    assert create_call[1]["name"] == "ubuntu-24-04-arm64-111111111111"
    assert create_call[2] is True and create_call[3] == f"create-{VM_A}"


def test_restart_reuses_registration(client, manager):
    manager.register(VM_A, name="web", vcpus=1, memory_mib=256)
    restarted = client.post("/api/v1/vms", json=create_body(VM_A, template_id=TEMPLATE["template_id"], template_version="1.0.0"))
    assert restarted.status_code == 201
    assert [c[0] for c in manager.calls if c[0] in ("create", "start")] == ["start"]


def test_create_running_vm_is_409(client, manager):
    manager.register(VM_A, running=True)
    assert client.post("/api/v1/vms", json=create_body(VM_A, template_id=TEMPLATE["template_id"], template_version="1.0.0")).status_code == 409


def test_ip_allocation_skips_used_and_retries_on_conflict(client, manager):
    manager.register(VM_B, ipv4="192.168.77.2")
    manager.fail_next = None
    created = client.post("/api/v1/vms", json=create_body(VM_A, template_id=TEMPLATE["template_id"], template_version="1.0.0"))
    assert created.status_code == 201
    assert next(c for c in manager.calls if c[0] == "create")[1]["ipv4"] == "192.168.77.3"


def test_ip_pool_exhausted_is_507():
    manager = FakeManager()
    for host in range(2, 5):
        manager.register(UUID(int=host), ipv4=f"192.168.77.{host}")
    app = create_app(Settings(driver="platform"), driver=PlatformDriver(manager, Xl(FakeRunner({("xl", "info"): XL_INFO}))),
                     provisioner=PlatformProvisioner(manager, ip_pool=range(2, 5)))
    denied = TestClient(app).post("/api/v1/vms", json=create_body(VM_A, template_id=TEMPLATE["template_id"], template_version="1.0.0"))
    assert denied.status_code == 507 and denied.json()["resource"]["name"] == "ipv4"


def test_zephyr_template_gets_no_ip_and_16mib(client, manager):
    created = client.post("/api/v1/vms", json=create_body(VM_A, template_id=ZEPHYR["template_id"], template_version="1.0.0", memory_mib=16))
    assert created.status_code == 201
    create_call = next(c for c in manager.calls if c[0] == "create")[1]
    assert "ipv4" not in create_call and create_call["memory_mib"] == 16


def test_restart_with_different_spec_is_409(client, manager):
    """계약 4.2 Spring 불변조건: 같은 vm_id 에 다른 생성 명세 → 409."""
    view = manager.register(VM_A, name="web", vcpus=1, memory_mib=256)
    view["initial_spec"] = {"template_id": TEMPLATE["template_id"], "template_version": "1.0.0", "vcpus": 1, "memory_mib": 256}
    same = client.post("/api/v1/vms", json=create_body(VM_A, template_id=TEMPLATE["template_id"], template_version="1.0.0"))
    assert same.status_code == 201
    client.post(f"/api/v1/vms/{VM_A}/stop")
    for change in ({"memory_mib": 512}, {"vcpus": 2}, {"template_version": "1.1.0"}, {"template_id": ZEPHYR["template_id"], "memory_mib": 16}):
        body = create_body(VM_A, template_id=TEMPLATE["template_id"], template_version="1.0.0") | change
        different = client.post("/api/v1/vms", json=body)
        assert different.status_code == 409, change
        assert different.json()["code"] == "VMExists"


def test_error_bodies_carry_distinct_codes(client, manager):
    manager.register(VM_A, running=True, xen_state="hang")
    timed_out = client.post(f"/api/v1/vms/{VM_A}/stop")
    assert (timed_out.status_code, timed_out.json()["code"]) == (504, "DriverTimeout")
    manager.fail_next = {"code": "STORAGE_UNAVAILABLE", "message": "x"}
    unavailable = client.get("/api/v1/vms")
    assert (unavailable.status_code, unavailable.json()["code"]) == (503, "PlatformUnavailable")


def test_unknown_template_is_400(client):
    assert client.post("/api/v1/vms", json=create_body(VM_A, template_id="nope", template_version="9")).status_code == 400


def test_platform_without_vm_id_support_is_501():
    manager = FakeManager(accept_vm_id=False)
    app = create_app(Settings(driver="platform"), driver=PlatformDriver(manager, Xl(FakeRunner({("xl", "info"): XL_INFO}))),
                     provisioner=PlatformProvisioner(manager))
    blocked = TestClient(app).post("/api/v1/vms", json=create_body(VM_A, template_id=TEMPLATE["template_id"], template_version="1.0.0"))
    assert blocked.status_code == 501
    assert "vm_id" in blocked.json()["message"]


def test_delete_requires_stopped_and_retires(client, manager):
    manager.register(VM_A, running=True)
    assert client.delete(f"/api/v1/vms/{VM_A}").status_code == 409
    assert client.post(f"/api/v1/vms/{VM_A}/stop").status_code == 204
    assert client.delete(f"/api/v1/vms/{VM_A}").status_code == 204
    assert manager.registry[str(VM_A)]["lifecycle"] == "RETIRED"
    assert manager.calls[-1][:2] == ("retire", {"vm_id": str(VM_A)}) and manager.calls[-1][2] is True
    assert client.delete(f"/api/v1/vms/{VM_A}").status_code == 404  # RETIRED 는 등록 아님


def test_stop_missing_registration_is_204(client):
    assert client.post(f"/api/v1/vms/{VM_B}/stop").status_code == 204


def test_load_manager_without_module_is_503():
    from dom0_manager.drivers.platform import load_manager

    with pytest.raises(PlatformUnavailable):
        load_manager("Z:/definitely/not/here")
