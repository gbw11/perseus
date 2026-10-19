"""계약 7장 상태 전이표를 Mock 드라이버로 끝에서 끝까지 확인한다."""

from conftest import VM_A, VM_B, create_body


def test_create_list_get(client):
    created = client.post("/api/v1/vms", json=create_body())
    assert created.status_code == 201
    assert created.json() == {"vm_id": str(VM_A), "state": "running", "vcpus": 1, "memory_mib": 256}

    listed = client.get("/api/v1/vms")
    assert listed.status_code == 200
    assert [vm["vm_id"] for vm in listed.json()] == [str(VM_A)]

    got = client.get(f"/api/v1/vms/{VM_A}")
    assert got.status_code == 200
    assert got.json()["state"] == "running"


def test_create_twice_is_vm_exists(client):
    assert client.post("/api/v1/vms", json=create_body()).status_code == 201
    again = client.post("/api/v1/vms", json=create_body())
    assert again.status_code == 409
    assert again.json()["code"] == "VMExists"


def test_stop_is_idempotent_and_restart_reuses_instance(client, driver, provisioner):
    client.post("/api/v1/vms", json=create_body())

    assert client.post(f"/api/v1/vms/{VM_A}/stop").status_code == 204
    assert client.get("/api/v1/vms").json() == []
    assert client.get(f"/api/v1/vms/{VM_A}").status_code == 404
    assert client.post(f"/api/v1/vms/{VM_A}/stop").status_code == 204  # 없는 VM 도 204

    assert provisioner.instance_exists(VM_A)  # 디스크 보존
    restarted = client.post("/api/v1/vms", json=create_body())
    assert restarted.status_code == 201
    assert [e[0] for e in driver.events] == ["create", "shutdown", "create"]


def test_delete_requires_stopped_vm_and_retires_instance(client, provisioner):
    """계약 v1.1: 실행 중 409, 정지 후 204(숨김 보관), 등록 없음 404."""
    client.post("/api/v1/vms", json=create_body())
    running = client.delete(f"/api/v1/vms/{VM_A}")
    assert running.status_code == 409
    assert running.json()["code"] == "InvalidState"
    assert provisioner.instance_exists(VM_A)

    assert client.post(f"/api/v1/vms/{VM_A}/stop").status_code == 204
    assert client.delete(f"/api/v1/vms/{VM_A}").status_code == 204
    assert not provisioner.instance_exists(VM_A)
    assert client.get(f"/api/v1/vms/{VM_A}").status_code == 404

    gone = client.delete(f"/api/v1/vms/{VM_A}")
    assert gone.status_code == 404
    assert gone.json()["code"] == "VMNotFound"


def test_insufficient_memory_is_507_with_resource(client):
    assert client.post("/api/v1/vms", json=create_body(VM_A, memory_mib=1024)).status_code == 201
    denied = client.post("/api/v1/vms", json=create_body(VM_B, memory_mib=256))
    assert denied.status_code == 507
    body = denied.json()
    assert body["code"] == "InsufficientResources"
    assert body["resource"] == {"name": "memory_mib", "requested": 256, "available": 0}


def test_crashed_domain_is_reported_and_stop_forces(client, driver):
    client.post("/api/v1/vms", json=create_body())
    driver.inject_crash(VM_A)
    assert client.get(f"/api/v1/vms/{VM_A}").json()["state"] == "crashed"
    assert client.post(f"/api/v1/vms/{VM_A}/stop").status_code == 204
    assert driver.events[-1] == ("shutdown", VM_A, {"forced": True, "grace_s": 90.0})


def test_hanging_domain_is_not_forced(client, driver):
    """계약 v1.1: grace(90초) 안에 안 꺼져도 자동 강제 종료하지 않는다. 도메인은 남고 호출은 실패한다."""
    client.post("/api/v1/vms", json=create_body())
    driver.inject_hang(VM_A)
    failed = client.post(f"/api/v1/vms/{VM_A}/stop")
    assert failed.status_code == 504
    assert failed.json()["code"] == "DriverError"
    assert driver.events[-1][2] == {"forced": False, "grace_s": 90.0, "timed_out": True}
    assert client.get(f"/api/v1/vms/{VM_A}").json()["state"] == "running"


def test_driver_timeout_does_not_take_service_down(client, driver):
    driver.inject_next_create("timeout")
    failed = client.post("/api/v1/vms", json=create_body())
    assert failed.status_code == 504
    assert failed.json()["code"] == "DriverError"
    assert client.get("/api/v1/vms").status_code == 200


def test_healthz(client, driver):
    assert client.get("/healthz").json() == {"status": "ok"}
    driver.set_healthy(False)
    unhealthy = client.get("/healthz")
    assert unhealthy.status_code == 503
    assert unhealthy.json() == {"status": "xen_unavailable"}


def test_mock_samples_and_stats():
    from dom0_manager.drivers.mock import MockDriver

    driver = MockDriver()  # 기본 6500 MiB. 샘플 3종이 1280 MiB 를 쓴다
    driver.seed_samples()
    names = {d.name for d in driver.list_domains()}
    assert names == {"alpine-drive", "debian-safety", "ubuntu-infotainment"}
    vm_id = driver.list_domains()[0].vm_id
    stats = driver.get_stats(vm_id)
    assert stats["vm_id"] == str(vm_id) and "cpu_time_s" in stats
