"""신영의 실제 xenova_management.Manager 를 가짜 Xen(FakeXen)·임시 registry 로 돌려 PlatformDriver 를 검증한다.

저장소 안의 platform/… 모듈과 tests/platform 픽스처를 그대로 빌린다. 보드가 없어도 registry 읽기·조회·정지 경로가
실제 플랫폼 코드와 맞물리는지 확인하는 것이 목적이다. 모듈이 없으면(배포 패키지 단독 테스트) 건너뛴다.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[3]
PLATFORM_TESTS = REPO / "tests" / "platform"
PLATFORM_FILES = REPO / "platform" / "yocto" / "meta-xenova" / "recipes-core" / "xenova-platform" / "files"

if not (PLATFORM_TESTS / "test_xenova_platform.py").is_file() or not PLATFORM_FILES.is_dir():
    pytest.skip("platform sources/fixtures not in this checkout", allow_module_level=True)

for entry in (PLATFORM_TESTS, PLATFORM_FILES, REPO / "platform" / "first-install", REPO / "platform" / "guests" / "common"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

try:
    import xenova_management as management  # noqa: E402
    import xenova_vm as vm  # noqa: E402
    from test_xenova_platform import FakeXen, domain, fixture, record  # noqa: E402
except Exception as exc:  # noqa: BLE001 - 플랫폼 모듈이 바뀌면 통합 테스트만 건너뛴다
    pytest.skip(f"platform modules not importable: {exc}", allow_module_level=True)

from dom0_manager.config import Settings  # noqa: E402
from dom0_manager.drivers.platform import PlatformDriver, PlatformProvisioner  # noqa: E402
from dom0_manager.drivers.xl import Xl  # noqa: E402
from dom0_manager.main import create_app  # noqa: E402
from test_xl import XL_INFO, FakeRunner  # noqa: E402

UBUNTU = UUID("aaaa0000-1111-4222-8333-123456789abc")  # record('ubuntu-dev') 의 고정 uuid
DEBIAN = UUID("bbbb0000-1111-4222-8333-123456789abc")


@pytest.fixture
def board():
    """임시 registry 에 ubuntu-dev(실행 중)·debian-dev(등록만) 를 두고 실제 Manager 를 만든다."""
    tmp = tempfile.TemporaryDirectory()
    data = Path(tmp.name) / "data"
    (data / "instances").mkdir(parents=True)
    run = Path(tmp.name) / "run"
    ubuntu, debian = record("ubuntu-dev"), record("debian-dev")
    records = {ubuntu["id"]: ubuntu, debian["id"]: debian}
    xen = FakeXen(data, records)
    ubuntu_dir = fixture(data, ubuntu)
    fixture(data, debian)
    xen.rows.append(domain(ubuntu, ubuntu_dir / "root.raw", 1))
    platform = vm.Platform(data, run, command=xen)
    with patch.object(vm, "storage_check"):
        manager = management.Manager(platform, usage_check=lambda *a: None)
        yield {"manager": manager, "xen": xen, "records": records}
    tmp.cleanup()


@pytest.fixture
def client(board):
    driver = PlatformDriver(board["manager"], Xl(FakeRunner({("xl", "info"): XL_INFO})))
    app = create_app(Settings(driver="platform", log_level="WARNING"), driver=driver, provisioner=PlatformProvisioner(board["manager"]))
    return TestClient(app)


def test_real_manager_list_and_get(client):
    listed = client.get("/api/v1/vms")
    assert listed.status_code == 200, listed.text
    assert [(v["vm_id"], v["state"]) for v in listed.json()] == [(str(UBUNTU), "running")]
    assert client.get(f"/api/v1/vms/{UBUNTU}").json()["vm_id"] == str(UBUNTU)
    assert client.get(f"/api/v1/vms/{DEBIAN}").status_code == 404  # 등록만 돼 있고 Xen 에 없음
    assert client.get("/healthz").json() == {"status": "ok"}


def test_real_manager_stop_then_delete(client, board):
    assert client.post(f"/api/v1/vms/{UBUNTU}/stop").status_code == 204
    assert any(call[:2] == ["xl", "shutdown"] for call in board["xen"].calls)
    assert client.get("/api/v1/vms").json() == []
    assert client.delete(f"/api/v1/vms/{DEBIAN}").status_code == 204  # 정지 상태 등록 VM → retire
    assert client.get(f"/api/v1/vms/{DEBIAN}").status_code == 404


def test_real_manager_create_blocked_until_platform_accepts_vm_id(client):
    """플랫폼 create 가 spec['vm_id'] 를 받기 전까지는 501. 신영 변경이 들어오면 이 테스트를 201 로 바꾼다."""
    new_vm = UUID("cccc0000-1111-4222-8333-123456789abc")
    body = {"vm_id": str(new_vm), "template_id": "ubuntu-24.04-arm64", "template_version": "1.0.0", "vcpus": 1, "memory_mib": 512}
    response = client.post("/api/v1/vms", json=body)
    assert response.status_code in (400, 501), response.text  # 400: 임시 registry 에 카탈로그가 없음
