"""서비스가 내는 OpenAPI 가 contracts/dom0-manager-openapi.yaml 과 같은 경로·메서드·응답 코드·operationId 를 갖는지 확인한다.

yaml 이 기계 판독 기준이므로(계약 1장) 둘 중 하나를 바꾸면 이 테스트가 먼저 깨진다.
"""

from pathlib import Path

import yaml

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "dom0-manager-openapi.yaml"
METHODS = ("get", "post", "put", "patch", "delete")


def _operations(spec: dict) -> dict[tuple[str, str], dict]:
    out = {}
    for path, item in spec["paths"].items():
        for method in METHODS:
            if method in item:
                out[(method.upper(), path)] = item[method]
    return out


def test_contract_file_exists():
    assert CONTRACT.is_file(), f"contract not found: {CONTRACT}"


def test_paths_methods_and_response_codes_match(client):
    expected = _operations(yaml.safe_load(CONTRACT.read_text(encoding="utf-8")))
    actual = _operations(client.app.openapi())

    assert set(actual) == set(expected), f"operations differ: {set(actual) ^ set(expected)}"
    for key, op in expected.items():
        assert actual[key].get("operationId") == op.get("operationId"), key
        assert set(actual[key]["responses"]) == set(op["responses"]), f"{key}: {set(actual[key]['responses'])} != {set(op['responses'])}"


def test_no_422_or_http_validation_error_in_schema(client):
    spec = client.app.openapi()
    for item in spec["paths"].values():
        for method in METHODS:
            if method in item:
                assert "422" not in item[method]["responses"]
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    assert "ValidationError" not in spec["components"]["schemas"]


def test_error_codes_and_states_match(client):
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))["components"]["schemas"]
    actual = client.app.openapi()["components"]["schemas"]
    assert actual["ErrorCode"]["enum"] == contract["Error"]["properties"]["code"]["enum"]
    assert actual["VmState"]["enum"] == contract["VmState"]["enum"]
    assert actual["CreateVmRequest"]["required"] == contract["CreateVmRequest"]["required"]
    assert set(actual["Vm"]["required"]) == set(contract["Vm"]["required"])
