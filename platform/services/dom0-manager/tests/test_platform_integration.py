"""플랫폼 파트의 실제 xenova_management.Manager 를 가짜 Xen(FakeXen)·임시 registry·임시 카탈로그로 돌려 PlatformDriver 를 검증한다.

저장소(모노레포) 안의 platform/… 모듈과 platform/tests 픽스처를 그대로 빌린다. 보드가 없어도 registry 읽기·조회·시작·정지·보관·생성
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

REPO = Path(__file__).resolve().parents[4]
PLATFORM_TESTS = REPO / "platform" / "tests"
PLATFORM_FILES = REPO / "platform" / "yocto" / "meta-xenova" / "recipes-core" / "xenova-platform" / "files"
DEFAULTS = PLATFORM_FILES / "defaults.json"  # 공장 VM 예약 IP·내부 IP 풀. 플랫폼이 create 때 IP 를 고르는 근거

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
        debian_dir = fixture(self.data, debian)
        for directory in (ubuntu_dir, debian_dir):
            with (directory / "root.raw").open("r+b") as stream:
                stream.seek(1080)
                stream.write(b"\x53\xef")
        self.xen.rows.append(domain(ubuntu, ubuntu_dir / "root.raw", 1))
        _publish_template(self.data)
        self.platform = vm.Platform(self.data, self.run, command=self.xen, defaults_path=DEFAULTS)
        # 명시적 로컬 시험 프로필이며 서비스/운영 기본 정책으로 배포하지 않는다.
        self.platform.creation_policy_path=self.data/'creation-policy-test.json'
        self.platform.creation_policy_path.write_text(json.dumps({'schema_version':'1.0','call_ms':3600000,
                                                                 'execution_ms':3500000,'cleanup_ms':5000}))
        self.manager = management.Manager(self.platform, preparer=self._prepare, usage_check=lambda *a: None, defaults_path=DEFAULTS)

    def _prepare(self, op):
        """플랫폼 파트 테스트의 prepare() 와 같은 대역: 디스크 복사 대신 작은 픽스처 파일을 만든다.
        내부 실행 이름·MAC·IP 는 플랫폼이 op['assigned'] 로 넘겨준 값을 그대로 쓴다."""
        spec = op["initial_spec"]
        name = op["assigned"]["internal_name"]
        r = record()
        r.update(guest_kind="linux", id=name, name=name, hostname=name, display_name=spec["name"], uuid=op["vm_id"],
                 mac=op["assigned"]["mac"], ipv4=op["assigned"]["ipv4"], memory_mib=spec["memory_mib"], vcpus=spec["vcpus"],
                 autostart=False, creation_spec=spec, creation_operation_id=op["operation_id"],
                 template_manifest_sha256=op["template"]["manifest_sha256"],
                 template_id=spec["template_id"], template_version=spec["template_version"])
        directory = fixture(self.data, r)
        with (directory / "root.raw").open("wb") as stream:
            stream.write(b"\0" * 2048)
            stream.seek(1080)
            stream.write(b"\x53\xef")
        self.records[r["id"]] = r
        return r

    def close(self) -> None:
        self.tmp.cleanup()


@pytest.fixture
def board():
    with patch.object(vm, "storage_check"), patch.object(vm.Platform, "check_network"), patch.object(vm.Platform, "check_xen"):
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
    body = {"vm_id": str(DEBIAN), "name": "다시 요청한 표시 이름", "template_id": TEMPLATE_ID, "template_version": TEMPLATE_VERSION, "vcpus": 1, "memory_mib": 512}
    debian = board.records["debian-dev"]
    body.update(vcpus=debian["vcpus"], memory_mib=debian["memory_mib"])
    restarted = client.post("/api/v1/vms", json=body)
    assert restarted.status_code == 201, restarted.text
    assert restarted.json()["vm_id"] == str(DEBIAN)
    assert any(call[:2] == ["xl", "create"] for call in board.xen.calls)


@pytest.mark.parametrize('display_name', ['주행 실험용 Ubuntu', '../경로가 아닌 표시 이름'])
def test_real_manager_create_with_spring_issued_vm_id(client, board, display_name):
    """팀 결정: Spring 발급 vm_id 가 registry·Xen UUID 가 된다. 플랫폼 create 는 vm_id 를 필수로 받고 내부 실행 이름·MAC·IP 를
    스스로 발급한다. 지원 여부 probe 나 조건부 건너뜀 없이 항상 201 과 UUID 일치를 요구한다 (회귀를 숨기지 않는다)."""
    body = {"vm_id": str(NEW_VM), "name": display_name, "template_id": TEMPLATE_ID, "template_version": TEMPLATE_VERSION, "vcpus": 1, "memory_mib": 512}
    created = client.post("/api/v1/vms", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["vm_id"] == str(NEW_VM)
    assert any(c[:2] == ["xl", "create"] for c in board.xen.calls), board.xen.calls
    registered = board.manager.call("get", {"vm_id": str(NEW_VM)})
    assert registered["ok"], registered
    view = registered["data"]["vm"]
    assert view["vm_id"] == str(NEW_VM) and view["configuration"]["uuid"] == str(NEW_VM)
    assert view["name"] == body["name"]
    assert view["configuration"]["display_name"] == body["name"]
    assert view["configuration"]["id"] == management.internal_name(str(NEW_VM))  # 디렉터리 = 플랫폼 내부 실행 이름
    assert view["configuration"]["ipv4"] not in (None, "192.168.77.10", "192.168.77.20")  # 플랫폼이 공장 예약을 피해 할당
    assert any(row["config"]["c_info"]["uuid"] == str(NEW_VM) for row in board.xen.rows)  # Xen 도메인 UUID 도 요청 UUID
    assert client.get(f"/api/v1/vms/{NEW_VM}").json()["vm_id"] == str(NEW_VM)

def test_create_retransmission_does_not_rename_existing_instance(client, board):
    body = {"vm_id": str(NEW_VM), "name": "최초 표시 이름", "template_id": TEMPLATE_ID,
            "template_version": TEMPLATE_VERSION, "vcpus": 1, "memory_mib": 512}
    assert client.post("/api/v1/vms", json=body).status_code == 201
    saved = board.manager.call("get", {"vm_id": str(NEW_VM)})["data"]["vm"]
    directory = board.data / "instances" / saved["configuration"]["id"]
    disk_inode = (directory / "root.raw").stat().st_ino
    initial = saved["initial_spec"].copy()
    assert client.post(f"/api/v1/vms/{NEW_VM}/stop").status_code == 204
    assert client.post("/api/v1/vms", json={**body, "name": "변경을 요청하지 않는 재전달"}).status_code == 201
    current = board.manager.call("get", {"vm_id": str(NEW_VM)})["data"]["vm"]
    assert current["name"] == body["name"]
    assert current["configuration"]["display_name"] == body["name"]
    assert current["initial_spec"] == initial
    assert (directory / "root.raw").stat().st_ino == disk_inode


# ---- 정지 요청 추적 (S15P31E110-305): 실제 Manager.fork·budget_ms·control-operation ----


def keyed_stop(client, vm_id, key, **headers):
    return client.post(f"/api/v1/vms/{vm_id}/stop", headers={"Idempotency-Key": key, **headers})


def lookup(client, vm_id, key):
    return client.get(f"/api/v1/vms/{vm_id}/control-operations", params={"request_key": key})


def shutdowns(board):
    return [call for call in board.xen.calls if call[:2] == ["xl", "shutdown"]]


def test_real_keyed_stop_replay_lookup_and_conflict(client, board):
    first = keyed_stop(client, UBUNTU, "stop-ubuntu-1", **{"X-Request-Budget-Ms": "20000"})  # 실제 Manager 가 budget_ms 를 받는다
    assert first.status_code == 200, first.text
    original = first.json()
    assert (original["status"], original["accepted"]) == ("completed", True) and original["operation_id"]
    assert len(shutdowns(board)) == 1

    again = keyed_stop(client, UBUNTU, "stop-ubuntu-1")  # 응답을 잃었다고 보고 같은 키로 재전달
    assert again.status_code == 200, again.text
    assert (again.json()["status"], again.json()["operation_id"]) == ("completed", original["operation_id"])
    assert len(shutdowns(board)) == 1  # 완료 replay 는 물리 동작을 반복하지 않는다

    found = lookup(client, UBUNTU, "stop-ubuntu-1")
    assert found.status_code == 200, found.text
    assert (found.json()["status"], found.json()["operation_id"], found.json()["action"]) == ("completed", original["operation_id"], "stop")

    conflict = keyed_stop(client, DEBIAN, "stop-ubuntu-1")  # 같은 키를 다른 VM 에
    assert conflict.status_code == 409 and conflict.json()["platform_code"] == "IDEMPOTENCY_CONFLICT"
    assert lookup(client, UBUNTU, "never-sent").status_code == 404


def test_real_keyed_stop_is_accepted_while_a_long_operation_holds_the_execution_lock(client, board):
    """긴 start·복구가 플랫폼 실행 잠금을 쥔 동안: 정지 의도는 내구 수락(202)되고 물리 정지는 없다. 잠금이 풀린 뒤 같은 키로 이행한다."""
    from xenova_runtime import operation_lock

    with operation_lock(board.manager.p.runtime):
        pending = keyed_stop(client, UBUNTU, "stop-during-long")
        assert pending.status_code == 202, pending.text
        accepted_op = pending.json()
        assert (accepted_op["status"], accepted_op["accepted"], accepted_op["phase"]) == ("pending", True, "accepted")
        assert accepted_op["operation_id"] and accepted_op["intent_revision"]
        assert shutdowns(board) == []
        found = lookup(client, UBUNTU, "stop-during-long")  # 조회는 실행 잠금을 기다리지 않는다
        assert found.status_code == 200 and (found.json()["status"], found.json()["operation_id"]) == ("pending", accepted_op["operation_id"])
    done = keyed_stop(client, UBUNTU, "stop-during-long")
    assert done.status_code == 200, done.text
    assert (done.json()["status"], done.json()["operation_id"]) == ("completed", accepted_op["operation_id"])
    assert len(shutdowns(board)) == 1


def test_real_budget_exhausted_submits_nothing(board):
    manager = SerializedManager(board.manager)
    driver = PlatformDriver(manager, Xl(FakeRunner({("xl", "info"): XL_INFO})))
    app = create_app(Settings(driver="platform", log_level="WARNING", response_reserve_s=0.5), driver=driver,
                     provisioner=PlatformProvisioner(manager))
    response = keyed_stop(TestClient(app), UBUNTU, "too-late", **{"X-Request-Budget-Ms": "100"})
    assert response.status_code == 504, response.text
    assert shutdowns(board) == []
    assert list((board.data / "control-requests").glob("*.json")) == []  # 의도 수락 기록도 없다

@pytest.mark.parametrize("name", ["개발용 Ubuntu", "\u00a0VM", "VM\u3000", "\u2003주행 A VM\u202f"])
def test_spring_dto_name_uses_actual_manager_metadata_preparer(client, board, name):
    """DTO 필드·실제 HTTP·Manager 메타데이터를 연결하고 Linux 디스크 복사 경계만 대체한다."""
    import re
    import prepare_instance
    source = (REPO / "backend/src/main/java/com/xenova/backend/vm/infrastructure/Dom0LaunchRequest.java").read_text()
    record_fields = re.search(r"record Dom0LaunchRequest\((.*?)\)", source, re.S).group(1)
    names = {re.sub(r"([A-Z])", lambda m: "_" + m[1].lower(), name)
             for name in re.findall(r"(?:String|int)\s+(\w+)", record_fields)}
    body = json.loads((REPO / "platform/tests/contracts/spring-create-request.json").read_text())
    body["vm_id"] = str(NEW_VM)
    body["name"] = name
    assert set(body) == names
    board.manager.preparer = board.manager._prepare
    def copy_disk_boundary(args, **kwargs):
        record = kwargs["record_override"]
        assert record["display_name"] == body["name"]
        from test_xenova_platform import private_fixture
        assert kwargs['private'] is True
        prepared=private_fixture(board.data,record,kwargs['workspace_path'])
        board.records[record["id"]] = record
        return prepared
    with patch.object(prepare_instance, "make_instance", side_effect=copy_disk_boundary) as copy:
        response = client.post("/api/v1/vms", json=body)
    assert response.status_code == 201, response.text
    assert copy.call_count == 1
    directory = board.data / "instances" / management.internal_name(str(NEW_VM))
    persisted = json.loads((directory / "instance.json").read_text())
    assert persisted["display_name"] == body["name"]
    assert persisted["name"] == persisted["id"] == "vm-" + NEW_VM.hex
    assert persisted["uuid"] == body["vm_id"]
    view = board.manager.call("get", {"vm_id": body["vm_id"]})["data"]["vm"]
    assert view["name"] == view["configuration"]["display_name"] == body["name"]
