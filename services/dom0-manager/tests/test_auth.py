"""계약 v1.1: PERSEUS_SERVICE_TOKEN 이 설정되면 /healthz 를 포함한 모든 내부 API 가 Bearer 토큰을 요구한다."""

from fastapi.testclient import TestClient

from conftest import create_body
from dom0_manager.config import Settings
from dom0_manager.drivers.mock import MockDriver, MockProvisioner
from dom0_manager.main import create_app


def _client(token: str) -> TestClient:
    app = create_app(Settings(driver="mock", service_token=token), driver=MockDriver(), provisioner=MockProvisioner())
    return TestClient(app)


def test_without_token_setting_everything_is_open(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/v1/vms").status_code == 200


def test_missing_or_wrong_token_is_401():
    c = _client("s3cret")
    for path in ("/healthz", "/api/v1/vms"):
        missing = c.get(path)
        assert missing.status_code == 401
        assert missing.json()["code"] == "Unauthorized"
        assert c.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get(path, headers={"Authorization": "Basic s3cret"}).status_code == 401
    assert c.post("/api/v1/vms", json=create_body()).status_code == 401


def test_correct_token_passes_and_docs_stay_public():
    c = _client("s3cret")
    ok = {"Authorization": "Bearer s3cret"}
    assert c.get("/healthz", headers=ok).status_code == 200
    assert c.post("/api/v1/vms", json=create_body(), headers=ok).status_code == 201
    assert c.get("/openapi.json").status_code == 200
    assert c.get("/docs").status_code == 200


def test_settings_reads_token_from_env():
    s = Settings.from_env({"PERSEUS_SERVICE_TOKEN": "abc", "PERSEUS_DRIVER": "mock"})
    assert s.service_token == "abc"
    assert Settings.from_env({"PERSEUS_DRIVER": "mock"}).service_token == ""
