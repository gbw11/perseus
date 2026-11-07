"""계약 v1.1: XENOVA_SERVICE_TOKEN 이 설정되면 /healthz 를 포함한 모든 내부 API 가 Bearer 토큰을 요구한다."""

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


def test_correct_token_passes():
    c = _client("s3cret")
    ok = {"Authorization": "Bearer s3cret"}
    assert c.get("/healthz", headers=ok).status_code == 200
    assert c.post("/api/v1/vms", json=create_body(), headers=ok).status_code == 201


DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


def test_api_docs_are_off_by_default_even_with_a_token():
    """계약: 모든 엔드포인트가 토큰을 검사한다. 문서 경로는 기본으로 열지 않는다 (2026-11-06 결정)."""
    for token in ("", "s3cret"):
        c = _client(token)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        for path in DOC_PATHS:
            assert c.get(path, headers=headers).status_code in (401, 404), (token, path)
            if token:
                assert c.get(path).status_code == 401, path  # 토큰 없이는 경로 존재 여부도 알 수 없다
                assert c.get(path, headers=headers).status_code == 404, path


def test_dev_api_docs_still_require_the_service_token():
    app = create_app(Settings(driver="mock", service_token="s3cret", api_docs=True), driver=MockDriver(), provisioner=MockProvisioner())
    c = TestClient(app)
    for path in DOC_PATHS:
        assert c.get(path).status_code == 401, path
        assert c.get(path, headers={"Authorization": "Bearer s3cret"}).status_code == 200, path
    assert Settings.from_env({"XENOVA_DRIVER": "mock"}).api_docs is False
    assert Settings.from_env({"XENOVA_DRIVER": "mock", "XENOVA_DOM0_API_DOCS": "1"}).api_docs is True


def test_contract_schema_is_available_without_the_docs_route():
    """계약 시험은 경로가 아니라 app.openapi() 를 직접 부른다. 경로를 꺼도 스키마는 만들어진다."""
    app = _client("s3cret").app
    assert app.openapi_url is None
    assert "/api/v1/vms" in app.openapi()["paths"]


def test_settings_reads_token_from_env():
    s = Settings.from_env({"XENOVA_SERVICE_TOKEN": "abc", "XENOVA_DRIVER": "mock"})
    assert s.service_token == "abc"
    assert Settings.from_env({"XENOVA_DRIVER": "mock"}).service_token == ""
