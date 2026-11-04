"""CPU 배치 조회 (S15P31E110-353) — 플랫폼 파트의 실제 Manager·CPU 모듈을 그 테스트의 가짜 Xen(CPUTest)으로 돌려,
실제 cpu-layout 출력이 dom0-manager 모델·API 를 그대로 통과하는지 본다. 플랫폼 소스가 없거나 fcntl 이 없으면(비 POSIX) 건너뛴다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[4]
PLATFORM_TESTS = REPO / "platform" / "tests"
PLATFORM_FILES = REPO / "platform" / "yocto" / "meta-xenova" / "recipes-core" / "xenova-platform" / "files"

if not (PLATFORM_TESTS / "test_xenova_cpu.py").is_file() or not (PLATFORM_FILES / "xenova_cpu.py").is_file():
    pytest.skip("platform sources are not part of this checkout", allow_module_level=True)
try:
    import fcntl  # noqa: F401 - 플랫폼 모듈의 파일 잠금. Linux 전용
except ImportError:
    pytest.skip("platform modules need fcntl (POSIX); run this file on Linux", allow_module_level=True)

for entry in (PLATFORM_TESTS, PLATFORM_FILES, REPO / "platform" / "first-install", REPO / "platform" / "guests" / "common"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from test_xenova_cpu import CPUTest  # noqa: E402

from dom0_manager.config import Settings  # noqa: E402
from dom0_manager.drivers.platform import PlatformDriver, PlatformProvisioner, SerializedManager  # noqa: E402
from dom0_manager.main import create_app  # noqa: E402


@pytest.fixture
def cpu():
    case = CPUTest("runTest")
    case.setUp()
    try:
        yield case
    finally:
        case.doCleanups()


def client_for(case):
    manager = SerializedManager(case.m, read_fork=True)
    return TestClient(create_app(Settings(driver="platform", log_level="WARNING"), driver=PlatformDriver(manager),
                                 provisioner=PlatformProvisioner(manager)))


def test_real_unconfigured_layout_keeps_nulls(cpu):
    response = client_for(cpu).get("/api/v1/cpu-layout")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configured"] is False and body["policy_revision"] is None and body["host"]["ready"] is False
    assert body["host"]["online_pcpus"] == [0, 1, 2, 3] and body["host"]["scheduler"] == "credit2"
    assert all(vm["affinity_matches"] is None and vm["exclusive_verified"] is None for vm in body["vms"])


def test_real_exclusive_vm_layout_and_conflict(cpu):
    cpu.activate()
    cpu.ok(cpu.control(cpu.z))
    client = client_for(cpu)
    response = client.get(f"/api/v1/vms/{cpu.zid}/cpu-layout")
    assert response.status_code == 200, response.text
    [vm] = response.json()["vms"]
    assert vm["vm_id"] == cpu.zid and vm["observed"]["presence"] == "present" and vm["execution_id"]
    assert (vm["allocation"], vm["affinity_matches"], vm["exclusive_verified"]) == ("exclusive", True, True)
    assert {r["vm_id"] for r in response.json()["reservations"]} == {cpu.zid, cpu.z2["uuid"]}

    domid = vm["observed"]["domid"]
    cpu.affinities[domid][0]["pcpus"] = [0, 1, 3]  # 바깥에서 마스크를 넓혔다
    [vm] = client.get(f"/api/v1/vms/{cpu.zid}/cpu-layout").json()["vms"]
    assert vm["affinity_matches"] is False and vm["exclusive_verified"] is False
    conflict = next(i for i in vm["issues"] if i["code"] == "EXCLUSIVE_CONFLICT")
    assert conflict["conflicts"] and all({"domid", "vcpu", "pcpus"} <= set(c) for c in conflict["conflicts"])


def test_real_unregistered_vm_is_404(cpu):
    response = client_for(cpu).get("/api/v1/vms/cccc0000-1111-4222-8333-123456789abc/cpu-layout")
    assert response.status_code == 404 and response.json()["platform_code"] == "VM_NOT_FOUND"
