from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from dom0_manager.config import Settings
from dom0_manager.drivers.mock import MockDriver, MockProvisioner
from dom0_manager.main import create_app

VM_A = UUID("11111111-1111-4111-8111-111111111111")
VM_B = UUID("22222222-2222-4222-8222-222222222222")


def create_body(vm_id: UUID = VM_A, **overrides):
    body = {
        "vm_id": str(vm_id),
        "name": "개발용 VM",
        "template_id": "alpine",
        "template_version": "3.20",
        "vcpus": 1,
        "memory_mib": 256,
    }
    body.update(overrides)
    return body


@pytest.fixture
def driver() -> MockDriver:
    return MockDriver(host_cpus=4, total_memory_mib=1024)


@pytest.fixture
def provisioner(driver: MockDriver) -> MockProvisioner:
    return MockProvisioner(running=driver.has_domain)


@pytest.fixture
def client(driver: MockDriver, provisioner: MockProvisioner) -> TestClient:
    app = create_app(Settings(driver="mock", log_level="WARNING"), driver=driver, provisioner=provisioner)
    return TestClient(app)
