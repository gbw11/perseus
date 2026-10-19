"""플랫폼 파트의 실제 xenova_management.Manager 를 가짜 Xen(FakeXen)·임시 registry·임시 카탈로그로 돌려 PlatformDriver 를 검증한다.

저장소(모노레포) 안의 platform/… 모듈과 tests/platform 픽스처를 그대로 빌린다. 보드가 없어도 registry 읽기·조회·시작·정지·보관·생성
경로가 실제 플랫폼 코드와 맞물리는지 확인하는 것이 목적이다.

건너뛰는 경우는 플랫폼 소스 디렉터리가 없을 때(배포 패키지 단독 테스트)와 fcntl 이 없는 비 POSIX 환경뿐이다.
소스가 있는데 import 가 깨지면 테스트 실패다.
"""

from __future__ import annotations

import json
import os
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

if not (PLATFORM_TESTS / "test_xenova_platform.py").is_file() or not (PLATFORM_FILES / "xenova_management.py").is_file():
    pytest.skip("platform sources are not part of this checkout", allow_module_level=True)
try:
    import fcntl  # noqa: F401 - 플랫폼 모듈의 파일 잠금. Linux 전용
except ImportError:
    # Windows 에서는 돌릴 수 없다. CI(Linux)·WSL 에서 돈다. 그 밖의 import 오류는 숨기지 않고 실패로 둔다.
    pytest.skip("platform modules need fcntl (POSIX); run this file on Linux", allow_module_level=True)

for entry in (PLATFORM_TESTS, PLATFORM_FILES, REPO / "platform" / "first-install", REPO / "platform" / "guests" / "common"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import xenova_catalog as catalog  # noqa: E402
import xenova_management as management  # noqa: E402
import xenova_vm as vm  # noqa: E402
from test_xenova_platform import FakeXen, domain, fixture, record  # noqa: E402

from dom0_manager.config import Settings  # noqa: E402
from dom0_manager.drivers.platform import PlatformDriver, PlatformProvisioner, SerializedManager  # noqa: E402
from dom0_manager.drivers.xl import Xl  # noqa: E402
from dom0_manager.main import create_app  # noqa: E402
from test_xl import XL_INFO, FakeRunner  # noqa: E402

UBUNTU = UUID("aaaa0000-1111-4222-8333-123456789abc")  # record('ubuntu-dev') 의 고정 uuid
DEBIAN = UUID("bbbb0000-1111-4222-8333-123456789abc")
NEW_VM = UUID("cccc0000-1111-4222-8333-123456789abc")  # Spring 이 발급했다고 가정하는 UUID
TEMPLATE_ID, TEMPLATE_VERSION = "ubuntu-24.04-arm64", "1.0.0"


def _publish_template(data: Path) -> None:
    """플랫폼 파트 테스트의 setUp 과 같은 최소 템플릿·카탈로그. create 가 통과하는 데 필요한 것만 둔다."""
    source = data / "templates" / "source"
    source.mkdir(parents=True)
    for name in ("root.raw", "Image", "initrd", "kernel.config"):
        (source / name).write_bytes(b"fixture")
    with (source / "root.raw").open("r+b") as stream:
        stream.seek(1080)
        stream.write(b"\x53\xef")  # ext4 매직
    h = catalog.sha(source / "Image")
    boot = {"source": {"distribution": "ubuntu"}, "kernel_release": "test", "root_device": "/dev/xvda",
            "kernel_sha256": h, "initrd_sha256": h, "config_sha256": h}
    (source / "boot.json").write_text(json.dumps(boot))
    meta = {"id": "source", "version": "1.0.0", "source": boot["source"], "root_sha256": catalog.sha(source / "root.raw"),
            "files": {p.name: {"sha256": catalog.sha(p), "bytes": p.stat().st_size} for p in source.iterdir()}}
    (source / "template.json").write_text(json.dumps(meta))
    for path in source.iterdir():
        path.chmod(0o444)
    descriptor = {"schema_version": "1.0", "template_id": TEMPLATE_ID, "template_version": TEMPLATE_VERSION,
                  "guest_kind": "linux", "asset_directory": "templates/source", "source_template_id": "source",
                  "manifest_sha256": catalog.sha(source / "template.json")}
    d = data / "catalog" / TEMPLATE_ID
    d.mkdir(parents=True)
    (d / f"{TEMPLATE_VERSION}.json").write_text(json.dumps(descriptor))


class Board:
    """임시 registry 에 ubuntu-dev(실행 중)·debian-dev(등록만) 를 두고 실제 Manager 를 만든다."""

    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        (self.data / "instances").mkdir(parents=True)
        self.run = Path(self.tmp.name) / "run"
        ubuntu, debian = record("ubuntu-dev"), record("debian-dev")
        self.records = {ubuntu["id"]: ubuntu, debian["id"]: debian}
        self.xen = FakeXen(self.data, self.records)
        ubuntu_dir = fixture(self.data, ubuntu)
        fixture(self.data, debian)
        self.xen.rows.append(domain(ubuntu, ubuntu_dir / "root.raw", 1))
        _publish_template(self.data)
        self.platform = vm.Platform(self.data, self.run, command=self.xen)
        self.manager = management.Manager(self.platform, preparer=self._prepare, usage_check=lambda *a: None)

    def _prepare(self, op):
        """플랫폼 파트 테스트의 prepare() 와 같은 대역: 디스크 복사 대신 작은 픽스처 파일을 만든다."""
        spec = op["initial_spec"]
        r = record()
        r.update(id=spec["name"], name=spec["name"], hostname=spec["name"], uuid=op["vm_id"], mac=op["assigned"]["mac"],
                 ipv4=spec["ipv4"], memory_mib=spec["memory_mib"], vcpus=spec["vcpus"], autostart=False,
                 creation_spec=spec, creation_operation_id=op["operation_id"],
                 template_manifest_sha256=op["template"]["manifest_sha256"])
        directory = fixture(self.data, r)
        with (directory / "root.raw").open("wb") as stream:
            stream.write(b"\0" * 2048)
            stream.seek(1080)
            stream.write(b"\x53\xef")
        self.records[r["id"]] = r
        return directory

    def close(self) -> None:
        self.tmp.cleanup()


@pytest.fixture
def board():
    with patch.object(vm, "storage_check"):
        b = Board()
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def client(board):
    manager = SerializedManager(board.manager)  # 실제 조립 경로(load_manager)와 같은 포장
    driver = PlatformDriver(manager, Xl(FakeRunner({("xl", "info"): XL_INFO})))
    app = create_app(Settings(driver="platform", log_level="WARNING"), driver=driver, provisioner=PlatformProvisioner(manager))
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
    assert any(call[:2] == ["xl", "shutdown"] for call in board.xen.calls)
    assert client.get("/api/v1/vms").json() == []
    assert client.delete(f"/api/v1/vms/{DEBIAN}").status_code == 204  # 정지 상태 등록 VM → retire
    assert client.get(f"/api/v1/vms/{DEBIAN}").status_code == 404


def test_real_manager_restart_registered_vm(client, board):
    """등록만 된 VM 에 POST /vms 가 오면 create 없이 start 만 한다 (디스크 재사용)."""
    body = {"vm_id": str(DEBIAN), "template_id": TEMPLATE_ID, "template_version": TEMPLATE_VERSION, "vcpus": 1, "memory_mib": 512}
    debian = board.records["debian-dev"]
    body.update(vcpus=debian["vcpus"], memory_mib=debian["memory_mib"])
    restarted = client.post("/api/v1/vms", json=body)
    assert restarted.status_code == 201, restarted.text
    assert restarted.json()["vm_id"] == str(DEBIAN)
    assert any(call[:2] == ["xl", "create"] for call in board.xen.calls)


def _platform_accepts_vm_id(manager) -> bool:
    """create 를 계획 모드(execute=False)로 불러 spec['vm_id'] 를 받는지 본다. 작업 기록·디스크를 만들지 않는다.
    변경 동작은 실행 여부와 무관하게 idempotency_key 를 검사하므로 유효한 키를 준다. 키가 없으면 지원 여부와 상관없이
    INVALID_SPEC 이 나와 판정이 틀어진다."""
    spec = {"vm_id": str(NEW_VM), "name": "probe", "template_id": TEMPLATE_ID, "template_version": TEMPLATE_VERSION,
            "vcpus": 1, "memory_mib": 512, "ipv4": "192.168.77.90"}
    probe = manager.call("create", spec, execute=False, idempotency_key=f"probe-vm-id-{NEW_VM}")
    error = probe.get("error") or {}
    assert probe.get("ok") or error.get("code") == "INVALID_SPEC", probe  # 그 밖의 실패는 픽스처 문제다
    return bool(probe.get("ok"))


def test_real_manager_create_with_spring_issued_vm_id(client, board):
    """팀 결정: Spring 발급 vm_id 가 registry·Xen UUID 가 된다. 플랫폼 create 가 spec['vm_id'] 를 받기 전까지는 501 이고
    그 상태를 xfail 로 드러낸다. 플랫폼이 지원하면 201 과 UUID 일치를 요구한다."""
    body = {"vm_id": str(NEW_VM), "template_id": TEMPLATE_ID, "template_version": TEMPLATE_VERSION, "vcpus": 1, "memory_mib": 512}
    if not _platform_accepts_vm_id(board.manager):
        blocked = client.post("/api/v1/vms", json=body)
        assert blocked.status_code == 501, blocked.text
        assert blocked.json()["code"] == "NotSupported"
        pytest.xfail("platform create does not accept a caller-supplied vm_id yet (request sent to platform part)")
    created = client.post("/api/v1/vms", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["vm_id"] == str(NEW_VM)
    registered = board.manager.call("get", {"vm_id": str(NEW_VM)})
    assert registered["ok"] and registered["data"]["vm"]["vm_id"] == str(NEW_VM)
    assert registered["data"]["vm"]["configuration"]["uuid"] == str(NEW_VM)
