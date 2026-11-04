"""서비스가 내는 OpenAPI 가 docs/contracts/dom0-manager-openapi.yaml 과 같은 경로·메서드·응답 코드·operationId 를 갖는지 확인한다.

yaml 이 기계 판독 기준이므로(계약 1장) 둘 중 하나를 바꾸면 이 테스트가 먼저 깨진다.
"""

from pathlib import Path

import yaml

CONTRACT = Path(__file__).resolve().parents[4] / "docs" / "contracts" / "dom0-manager-openapi.yaml"
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


def _parameters(spec: dict) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """operation 마다 (name, in) 집합. path 수준 파라미터를 합치고 $ref 를 components.parameters 에서 푼다."""
    shared = spec.get("components", {}).get("parameters", {})

    def resolve(param: dict) -> tuple[str, str]:
        if "$ref" in param:
            param = shared[param["$ref"].rsplit("/", 1)[-1]]
        return param["name"], param["in"]

    out = {}
    for path, item in spec["paths"].items():
        common = [resolve(p) for p in item.get("parameters", [])]
        for method in METHODS:
            if method in item:
                out[(method.upper(), path)] = {*common, *(resolve(p) for p in item[method].get("parameters", []))}
    return out


def test_parameters_match(client):
    """경로·질의·헤더 파라미터(요청 예산, Idempotency-Key 포함)가 yaml 과 같다."""
    expected = _parameters(yaml.safe_load(CONTRACT.read_text(encoding="utf-8")))
    actual = _parameters(client.app.openapi())
    assert actual == expected
    budget = ("X-Request-Budget-Ms", "header")
    assert all(budget in params for key, params in actual.items() if key[1] != "/healthz")
    assert budget not in actual[("GET", "/healthz")]
    assert ("Idempotency-Key", "header") in actual[("POST", "/api/v1/vms/{vm_id}/stop")]


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


def test_error_body_fields_match(client):
    """오류 본문 필드(platform_code 포함)와 필수 필드가 yaml 과 같다. 한쪽에서만 빠지거나 타입이 바뀌면 여기서 깨진다."""
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))["components"]["schemas"]["Error"]
    actual = client.app.openapi()["components"]["schemas"]["Error"]
    assert set(actual["properties"]) == set(contract["properties"]) == {
        "code", "message", "detail", "resource", "platform_code", "retryable", "operation"}
    assert set(actual["required"]) == set(contract["required"]) == {"code", "message"}
    assert contract["properties"]["platform_code"]["type"] == ["string", "null"]
    live = actual["properties"]["platform_code"]
    assert live.get("anyOf") == [{"type": "string"}, {"type": "null"}] or live.get("type") == ["string", "null"]
    assert actual["additionalProperties"] is False and contract["additionalProperties"] is False


def test_control_operation_schema_matches(client):
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))["components"]["schemas"]
    actual = client.app.openapi()["components"]["schemas"]
    assert actual["ControlStatus"]["enum"] == contract["ControlStatus"]["enum"]
    assert set(actual["ControlOperation"]["properties"]) == set(contract["ControlOperation"]["properties"])
    assert set(actual["ControlOperation"]["required"]) == set(contract["ControlOperation"]["required"])


def test_cpu_layout_schemas_match(client):
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))["components"]["schemas"]
    actual = client.app.openapi()["components"]["schemas"]
    for name in ("CpuLayout", "CpuHost", "VmCpuLayout", "CpuObserved", "CpuReservation", "CpuIssue", "CpuConflict", "VcpuAffinity"):
        assert set(actual[name]["properties"]) == set(contract[name]["properties"]), name
        assert set(actual[name].get("required", [])) == set(contract[name]["required"]), name

    def enum(prop):
        """열거값 집합. Optional Literal(anyOf 의 enum + null)과 yaml 의 enum(null 포함)을 같은 모양으로 본다."""
        values = set(prop.get("enum", []))
        for option in prop.get("anyOf", []):
            values |= set(option.get("enum", [])) | ({None} if option.get("type") == "null" else set())
        return values

    for schema, field in (("CpuObserved", "presence"), ("VmCpuLayout", "allocation"), ("CpuIssue", "code")):
        live, static = enum(actual[schema]["properties"][field]), enum(contract[schema]["properties"][field])
        assert live and live == static, (schema, field, live, static)


def test_security_scheme_and_401_are_declared(client):
    """인증은 미들웨어라 라우트에 안 보인다. 라이브 OpenAPI 도 yaml 처럼 ServiceToken 을 전역 security 로 선언하고 모든
    엔드포인트가 401 을 명시해야 한다."""
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    spec = client.app.openapi()
    assert spec["security"] == contract["security"] == [{"ServiceToken": []}]
    assert spec["components"]["securitySchemes"] == contract["components"]["securitySchemes"]
    for key, op in _operations(spec).items():
        assert "401" in op["responses"], key
