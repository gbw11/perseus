"""PlatformDriver / PlatformProvisioner 를 플랫폼 봉투를 흉내 내는 FakeManager 로 검증한다 (S15P31E110-115)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from conftest import VM_A, VM_B, create_body
from dom0_manager.config import Settings
from dom0_manager.drivers.platform import PlatformDriver, PlatformProvisioner, map_platform_state, view_to_domain
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
    """xenova_management.Manager.call 의 봉투·동작을 최소로 흉내 낸다.

    실제 플랫폼처럼 create 는 vm_id 를 필수로 받고, 내부 실행 이름(vm-<hex>)과 Linux IP(192.168.77.2..pool_end) 를 스스로 고르며,
    IP 가 바닥나면 IP_POOL_EXHAUSTED 를 낸다. fail_create 는 다음 create 한 번만 그 오류로 실패시킨다."""

    data = Path(tempfile.mkdtemp(prefix="dom0-fake-registry-"))  # /healthz 가 보는 registry 저장소 (instances/ 가 있어야 정상)
    (data / "instances").mkdir()

    def __init__(self, *, templates=(TEMPLATE, ZEPHYR), pool_end=99):
        self.templates = list(templates)
        self.pool_end = pool_end
        self.fail_create: dict | None = None
        self.registry: dict[str, dict] = {}  # vm_id -> view
        self.calls: list[tuple] = []
        self.fail_next: dict | None = None
        self.next_domid = 1

    def register(self, vm_id, *, name="vm", vcpus=1, memory_mib=512, ipv4="192.168.77.30", guest_kind="linux", running=False, xen_state=None, lifecycle="REGISTERED"):
        view = {"vm_id": str(vm_id), "name": name, "lifecycle": lifecycle, "generation": 0, "registered": lifecycle == "REGISTERED",
                "configuration": {"id": f"vm-{UUID(str(vm_id)).hex}", "vcpus": vcpus, "memory_mib": memory_mib, "ipv4": ipv4,
                                  "guest_kind": guest_kind},
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
        if action in ("create", "retire", "restore", "purge"):
            # 실제 Manager._mutate 순서: 명세 정규화(필수 키·모르는 키) → idempotency_key 검사 → 계획/실행
            required = {"vm_id", "name", "template_id", "template_version", "vcpus", "memory_mib"}
            if action == "create" and (set(spec) - (required | {"ipv4"}) or not required <= set(spec)):
                return _err("INVALID_SPEC", "vm_id, name, exact template ID/version and resources required")
            if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key.encode("utf-8")) <= 200:
                return _err("INVALID_SPEC", "idempotency_key of 1..200 UTF-8 bytes required")
        if action == "create":
            if self.fail_create:
                failure, self.fail_create = self.fail_create, None
                return _err(**failure)
            template = next((e for e in self.templates if e["template_id"] == spec["template_id"]
                             and str(e["template_version"]) == str(spec["template_version"])), None)
            if template is None:
                return _err("TEMPLATE_NOT_FOUND", "normalized template ID not published")
            linux = template.get("guest_kind", "linux") == "linux"
            if not linux and "ipv4" in spec:
                return _err("INVALID_SPEC", "Zephyr has no IP field")
            if not execute:
                return _ok({"plan": True, "action": action, "spec": spec})
            ipv4 = spec.get("ipv4")
            if linux and ipv4 is None:  # 플랫폼이 전역 잠금 안에서 빈 내부 주소를 고른다
                used = {v["configuration"].get("ipv4") for v in self.registry.values()}
                ipv4 = next((f"192.168.77.{h}" for h in range(2, self.pool_end + 1) if f"192.168.77.{h}" not in used), None)
                if ipv4 is None:
                    return _err("IP_POOL_EXHAUSTED", "no unreserved internal Linux address remains")
            view = self.register(spec["vm_id"], name=spec["name"], vcpus=spec["vcpus"], memory_mib=spec["memory_mib"], ipv4=ipv4,
                                 guest_kind=template.get("guest_kind", "linux"))
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


def test_health_requires_xl_and_registry_storage(driver, manager, tmp_path):
    """health 는 xl info 와 registry 디렉터리 존재만 본다. Manager 는 부르지 않으므로 registry 내용 오류는 목록 호출이 알린다."""
    assert driver.health() is True
    manager.fail_next = {"code": "STORAGE_UNAVAILABLE", "message": "registry unavailable"}
    assert driver.health() is True and manager.fail_next is not None  # Manager 를 부르지 않았다
    manager.fail_next = None
    manager.data = tmp_path  # instances/ 없음 = 저장소가 마운트되지 않음
    assert driver.health() is False
    manager.data = FakeManager.data
    broken = PlatformDriver(manager, Xl(FakeRunner({})))
    assert broken.health() is False


def test_platform_error_codes_map_to_http(client, manager):
    manager.register(VM_A, running=True)
    for code, status in [("STORAGE_UNAVAILABLE", 503), ("OPERATION_IN_PROGRESS", 503), ("INVALID_SPEC", 400), ("ARTIFACT_INVALID", 500)]:
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
    assert create_call[1]["vm_id"] == str(VM_A) and "ipv4" not in create_call[1]  # IP 는 플랫폼이 고른다
    assert manager.registry[str(VM_A)]["configuration"]["ipv4"] == "192.168.77.2"
    assert create_call[1]["name"] == "개발용 VM"
    assert create_call[2] is True and create_call[3] == f"create-{VM_A}"


def test_restart_reuses_registration(client, manager):
    manager.register(VM_A, name="web", vcpus=1, memory_mib=256)
    restarted = client.post("/api/v1/vms", json=create_body(VM_A, template_id=TEMPLATE["template_id"], template_version="1.0.0"))
    assert restarted.status_code == 201
    assert [c[0] for c in manager.calls if c[0] in ("create", "start")] == ["start"]


def test_create_running_vm_is_409(client, manager):
    manager.register(VM_A, running=True)
    assert client.post("/api/v1/vms", json=create_body(VM_A, template_id=TEMPLATE["template_id"], template_version="1.0.0")).status_code == 409


def test_platform_assigns_ip_without_dom0_manager_hint(client, manager):
    """IP 정책은 플랫폼이 단독 소유한다. dom0-manager 는 ipv4 를 보내지 않고 플랫폼이 쓰이지 않은 주소를 고른다."""
    manager.register(VM_B, ipv4="192.168.77.2")
    created = client.post("/api/v1/vms", json=create_body(VM_A, template_id=TEMPLATE["template_id"], template_version="1.0.0"))
    assert created.status_code == 201
    assert "ipv4" not in next(c for c in manager.calls if c[0] == "create")[1]
    assert manager.registry[str(VM_A)]["configuration"]["ipv4"] == "192.168.77.3"


def test_platform_ip_pool_exhausted_is_507_ipv4():
    """플랫폼 IP_POOL_EXHAUSTED → 507 InsufficientResources(resource.name=ipv4)."""
    manager = FakeManager(pool_end=4)
    for host in range(2, 5):
        manager.register(UUID(int=host), ipv4=f"192.168.77.{host}")
    app = create_app(Settings(driver="platform"), driver=PlatformDriver(manager, Xl(FakeRunner({("xl", "info"): XL_INFO}))),
                     provisioner=PlatformProvisioner(manager))
    denied = TestClient(app).post("/api/v1/vms", json=create_body(VM_A, template_id=TEMPLATE["template_id"], template_version="1.0.0"))
    assert denied.status_code == 507, denied.text
    assert denied.json()["resource"] == {"name": "ipv4", "requested": 1, "available": 0}


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


def test_platform_invalid_spec_is_400(client, manager):
    """플랫폼의 INVALID_SPEC 은 400 InvalidArgument 다 (플랫폼은 vm_id 를 필수로 받으므로 501 로 바꿀 이유가 없다)."""
    manager.fail_create = {"code": "INVALID_SPEC", "message": "invalid VM name"}
    rejected = client.post("/api/v1/vms", json=create_body(VM_A, template_id=TEMPLATE["template_id"], template_version="1.0.0"))
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["code"] == "InvalidArgument" and "invalid VM name" in rejected.json()["message"]


def test_instance_paths_use_platform_internal_name(manager):
    """디렉터리는 configuration.id(vm-<hex>)다. 표시 이름(view['name'])으로 경로를 만들지 않고, id 가 없으면 플랫폼 응답 오류다."""
    manager.register(VM_A, name="web", vcpus=1, memory_mib=256)
    provisioner = PlatformProvisioner(manager)
    paths = provisioner.ensure_instance(VM_A, TEMPLATE["template_id"], "1.0.0", name="전달한 표시 이름", vcpus=1, memory_mib=256)
    assert paths.root == Path(manager.data) / "instances" / f"vm-{VM_A.hex}"
    del manager.registry[str(VM_A)]["configuration"]["id"]
    with pytest.raises(DriverError):
        provisioner.ensure_instance(VM_A, TEMPLATE["template_id"], "1.0.0", name="전달한 표시 이름", vcpus=1, memory_mib=256)


def test_health_does_not_wait_for_manager_or_slow_list():
    """xl info 5초 + Manager 잠금 대기 + xl list 두 번은 Spring 의 6초 대기를 넘길 수 있다. health 는 Manager 를 부르지 않으므로
    다른 요청이 잠금을 쥐고 있어도, 잠금을 얻은 뒤의 list 가 느려도 바로 돌아온다."""
    import threading
    import time

    from dom0_manager.drivers.platform import SerializedManager

    inner = FakeManager()
    inner.register(VM_A, running=True)
    entered, release = threading.Event(), threading.Event()
    original = inner.call

    def slow_call(action, spec=None, **kwargs):
        if action == "stop":
            entered.set()
            release.wait(5)
        if action == "list":
            time.sleep(2)  # 잠금을 얻은 뒤의 xl list 지연
        return original(action, spec, **kwargs)

    inner.call = slow_call
    wrapped = SerializedManager(inner, busy_timeout_s=5.0)
    driver = PlatformDriver(wrapped, Xl(FakeRunner({("xl", "info"): XL_INFO})))
    holder = threading.Thread(target=wrapped.call, args=("stop", {"vm_id": str(VM_A), "force": False}), kwargs={"execute": True})
    holder.start()
    try:
        assert entered.wait(2)
        started = time.monotonic()
        assert driver.health() is True
        assert time.monotonic() - started < 1.0
    finally:
        release.set()
        holder.join(5)
    started = time.monotonic()
    assert driver.health() is True  # 잠금이 비어 있어도 느린 list 를 기다리지 않는다
    assert time.monotonic() - started < 1.0


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


def test_load_manager_without_module_is_503(monkeypatch):
    from dom0_manager.drivers.platform import load_manager

    def missing_module(name):
        raise ImportError(name)
    monkeypatch.setattr("dom0_manager.drivers.platform.importlib.import_module", missing_module)
    with pytest.raises(PlatformUnavailable):
        load_manager("Z:/definitely/not/here")


def test_serialized_manager_never_overlaps_calls():
    """Manager 는 요청별 상태를 인스턴스 변수에 두므로 call 이 겹치면 안 된다. 스레드 8개가 동시에 불러도 내부 동시성은 1 이다."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from dom0_manager.drivers.platform import SerializedManager

    class Inner:
        data = Path("/srv/xenova")

        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.calls = 0
            self._lock = threading.Lock()

        def call(self, action, spec=None, **kwargs):
            with self._lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.002)
            with self._lock:
                self.active -= 1
                self.calls += 1
            return _ok({"action": action})

    inner = Inner()
    wrapped = SerializedManager(inner)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: wrapped.call("list", {"i": i}), range(40)))
    assert inner.calls == 40
    assert inner.max_active == 1
    assert wrapped.data == inner.data


def test_build_driver_wraps_manager_and_provisioner_reuses_it(monkeypatch):
    """실제 조립 경로: load_manager 가 SerializedManager 로 감싸고, build_provisioner 는 드라이버의 Manager 를 그대로 쓴다."""
    import sys
    import types

    from dom0_manager.drivers import build_driver, build_provisioner
    from dom0_manager.drivers.platform import SerializedManager

    fake_module = types.ModuleType("xenova_management")
    fake_module.Manager = FakeManager
    monkeypatch.setitem(sys.modules, "xenova_management", fake_module)

    settings = Settings(driver="platform", platform_lib="", platform_busy_timeout_s=0.5)
    driver = build_driver(settings)
    provisioner = build_provisioner(settings, driver)
    assert isinstance(driver.manager, SerializedManager)
    assert provisioner.manager is driver.manager
    assert driver.manager.busy_timeout_s == 0.5


def test_serialized_manager_busy_timeout_is_retryable_503():
    """정지가 최대 90초 잠금을 쥐는 동안 다른 요청이 무한정 줄을 서면 안 된다. 대기 한도를 넘기면 플랫폼 잠금
    충돌과 같은 OPERATION_IN_PROGRESS 봉투 → 503 PlatformUnavailable(재시도 가능). health 는 바쁜 것을 장애로 보지 않는다."""
    import threading

    from dom0_manager.drivers.platform import SerializedManager

    inner = FakeManager()
    inner.register(VM_A, running=True)
    entered, release = threading.Event(), threading.Event()
    original = inner.call

    def slow_call(action, spec=None, **kwargs):
        if action == "stop":
            entered.set()
            release.wait(5)
        return original(action, spec, **kwargs)

    inner.call = slow_call
    wrapped = SerializedManager(inner, busy_timeout_s=0.05)
    app = create_app(Settings(driver="platform", log_level="WARNING"),
                     driver=PlatformDriver(wrapped, Xl(FakeRunner({("xl", "info"): XL_INFO}))), provisioner=PlatformProvisioner(wrapped))
    client = TestClient(app)
    holder = threading.Thread(target=wrapped.call, args=("stop", {"vm_id": str(VM_A), "force": False}), kwargs={"execute": True})
    holder.start()
    try:
        assert entered.wait(2)
        busy = client.get("/api/v1/vms")
        assert (busy.status_code, busy.json()["code"]) == (503, "PlatformUnavailable")
        assert "in progress" in busy.json()["message"]
        assert client.get("/healthz").status_code == 200  # xl 은 응답하고 플랫폼은 바쁠 뿐이다
    finally:
        release.set()
        holder.join(5)
    assert client.get("/api/v1/vms").status_code == 200  # 잠금이 풀리면 정상
