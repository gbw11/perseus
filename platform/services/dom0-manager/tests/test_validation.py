"""계약 8.1/9장: 검증 실패는 400 InvalidArgument, 추가 필드 거부, vm_id 는 UUID."""

import pytest

from conftest import VM_A, create_body


def _assert_invalid(response):
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "InvalidArgument"
    assert "detail" in body


def test_missing_field_is_400(client):
    body = create_body()
    del body["template_version"]
    _assert_invalid(client.post("/api/v1/vms", json=body))


def test_extra_field_is_rejected(client):
    _assert_invalid(client.post("/api/v1/vms", json=create_body(unexpected="web-01")))


def test_non_uuid_vm_id_in_body_and_path(client):
    _assert_invalid(client.post("/api/v1/vms", json=create_body() | {"vm_id": "web-01"}))
    _assert_invalid(client.get("/api/v1/vms/not-a-uuid"))
    _assert_invalid(client.post("/api/v1/vms/not-a-uuid/stop"))


def test_range_rules(client):
    _assert_invalid(client.post("/api/v1/vms", json=create_body(memory_mib=8)))  # 공통 하한 16 (Zephyr 프로필)
    assert client.post("/api/v1/vms", json=create_body(memory_mib=16)).status_code == 201
    _assert_invalid(client.post("/api/v1/vms", json=create_body(memory_mib=2048)))
    _assert_invalid(client.post("/api/v1/vms", json=create_body(vcpus=0)))
    _assert_invalid(client.post("/api/v1/vms", json=create_body(vcpus=5)))
    _assert_invalid(client.post("/api/v1/vms", json=create_body(template_id="")))


def test_vcpus_above_host_cpus_is_507(client):
    # 스키마 상한(4)은 통과하지만 호스트 코어가 더 적으면 자원 부족
    from dom0_manager.config import Settings
    from dom0_manager.drivers.mock import MockDriver, MockProvisioner
    from dom0_manager.main import create_app
    from fastapi.testclient import TestClient

    small = TestClient(create_app(Settings(driver="mock"), driver=MockDriver(host_cpus=2), provisioner=MockProvisioner()))
    denied = small.post("/api/v1/vms", json=create_body(VM_A, vcpus=4))
    assert denied.status_code == 507
    assert denied.json()["resource"] == {"name": "vcpus", "requested": 4, "available": 2}


def test_name_is_required_and_keeps_spring_display_text(client):
    body = create_body()
    del body["name"]
    _assert_invalid(client.post("/api/v1/vms", json=body))
    for name in ("", " 앞 공백", "뒤 공백 ", "줄\n바꿈", "제어\x00문자", "줄\u2028구분", "x" * 64):
        _assert_invalid(client.post("/api/v1/vms", json=create_body(name=name)))
    assert client.post("/api/v1/vms", json=create_body(name="주행 A 개발 VM")).status_code == 201


@pytest.mark.parametrize("name", [
    "\u00a0VM", "VM\u3000", "\u2003주행 A VM\u202f",
])
def test_spring_unicode_edge_spaces_are_preserved(client, name):
    response = client.post("/api/v1/vms", json=create_body(name=name))
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("name", [
    " VM", "VM ", "\tVM", "VM\t", "\nVM", "VM\r",
    "VM\x85", "\u2028VM", "VM\u2029",
])
def test_spring_ascii_edge_spaces_and_controls_are_rejected(client, name):
    _assert_invalid(client.post("/api/v1/vms", json=create_body(name=name)))


def test_unclassified_error_uses_driver_error_body():
    """openapi Error 500: 분류되지 않은 예외도 text/plain 이 아니라 {code: DriverError} 로 낸다."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from dom0_manager.api.errors import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise KeyError("registration")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    assert response.json() == {"code": "DriverError", "message": "internal error", "detail": "KeyError"}
