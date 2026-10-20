"""Domain-0 보호: 목록에서 빠지고, nil UUID 대상 호출은 어떤 엔드포인트·드라이버에서도 400 이다."""

import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from conftest import create_body
from dom0_manager.config import Settings
from dom0_manager.drivers.xen import XenDriver
from dom0_manager.drivers.xl import Xl
from dom0_manager.errors import VMNotFound
from dom0_manager.main import create_app
from dom0_manager.service import DOM0_UUID
from test_xl import FakeRunner, XL_INFO

ALPINE = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _runner(xl_list: str, long_rows: list[dict]):
    return FakeRunner({("xl", "list"): xl_list, ("xl", "list", "-l"): json.dumps(long_rows), ("xl", "info"): XL_INFO})


def test_xen_driver_excludes_dom0_by_id_even_if_renamed_and_by_name_even_with_other_id():
    xl_list = (
        "Name ID Mem VCPUs State Time(s)\n"
        "control 0 1024 4 r----- 0\n"  # domid 0 인데 이름이 바뀐 경우
        "Domain-0 7 512 1 -b---- 0\n"  # 이름은 Domain-0 인데 domid 가 0 이 아닌 경우
        "alpine-drive 3 256 1 r----- 0\n"
    )
    long_rows = [
        {"domid": 0, "config": {"c_info": {"name": "control", "uuid": str(DOM0_UUID)}}},
        {"domid": 7, "config": {"c_info": {"name": "Domain-0", "uuid": "77777777-7777-4777-8777-777777777777"}}},
        {"domid": 3, "config": {"c_info": {"name": "alpine-drive", "uuid": str(ALPINE)}}},
    ]
    driver = XenDriver(Xl(_runner(xl_list, long_rows)))
    assert [d.vm_id for d in driver.list_domains()] == [ALPINE]
    with pytest.raises(VMNotFound):
        driver.get_domain(DOM0_UUID)
    assert driver.has_domain(UUID("77777777-7777-4777-8777-777777777777")) is False


@pytest.mark.parametrize("method,path", [
    ("GET", f"/api/v1/vms/{DOM0_UUID}"),
    ("POST", f"/api/v1/vms/{DOM0_UUID}/stop"),
    ("DELETE", f"/api/v1/vms/{DOM0_UUID}"),
])
def test_nil_uuid_is_rejected_on_every_endpoint(client, driver, method, path):
    before = list(driver.events)
    rejected = client.request(method, path)
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["code"] == "InvalidArgument" and "Domain-0" in rejected.json()["message"]
    assert driver.events == before  # 드라이버에 닿지 않는다


def test_nil_uuid_create_is_rejected_before_resources_are_checked(client, driver, provisioner):
    rejected = client.post("/api/v1/vms", json=create_body(DOM0_UUID))
    assert rejected.status_code == 400 and rejected.json()["code"] == "InvalidArgument"
    assert not provisioner.instance_exists(DOM0_UUID) and driver.events == []


def test_platform_mode_never_reaches_the_manager_for_dom0():
    from dom0_manager.drivers.platform import PlatformDriver, PlatformProvisioner
    from test_platform_driver import FakeManager

    manager = FakeManager()
    app = create_app(Settings(driver="platform", log_level="WARNING"),
                     driver=PlatformDriver(manager, Xl(FakeRunner({("xl", "info"): XL_INFO}))), provisioner=PlatformProvisioner(manager))
    client = TestClient(app)
    assert client.post(f"/api/v1/vms/{DOM0_UUID}/stop").status_code == 400
    assert client.delete(f"/api/v1/vms/{DOM0_UUID}").status_code == 400
    assert client.get(f"/api/v1/vms/{DOM0_UUID}").status_code == 400
    assert manager.calls == []
