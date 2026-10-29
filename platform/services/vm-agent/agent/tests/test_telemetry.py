"""에이전트 자체 상태의 v2 Telemetry 발행: 메시지가 Zephyr 계약 2.1 검증기(oracle)를 통과하고 Reporter 정체·끊김·종료를 그대로 드러낸다."""

from __future__ import annotations

import importlib.util
import itertools
import json
import threading
import uuid
from pathlib import Path

import pytest

from xenova_agent.reporter import ReporterStatus
from xenova_agent.telemetry import CAPABILITIES, MAX_PAYLOAD_BYTES, TelemetryPublisher, boot_uptime_ms, topic

VM_ID = "11111111-1111-4111-8111-111111111111"
BOOT_ID = "22222222-2222-4222-8222-222222222222"
INVENTORY = {"os": {"id": "debian", "version_id": "13", "pretty_name": "Debian 13"}, "kernel_release": "6.12", "hostname": "d",
             "interfaces": [{"name": "eth0", "mac": "02:00:00:00:00:01", "addresses": [{"address": "192.168.77.10", "family": "ipv4"}]}],
             "agent_version": "0.3.0", "capabilities": {"commands": [], "metrics": []}}
# Zephyr Publisher 와 같은 검증기로 Linux 에이전트의 메시지를 검사한다. 계약의 주인은 platform/guests/zephyr/telemetry 다
ORACLE_PATH = Path(__file__).resolve().parents[5] / "platform/guests/zephyr/telemetry/contract/tools/validate_contract.py"


@pytest.fixture(scope="module")
def oracle():
    pytest.importorskip("jsonschema")
    if not ORACLE_PATH.exists():
        pytest.skip(f"v2 contract oracle missing: {ORACLE_PATH}")
    spec = importlib.util.spec_from_file_location("telemetry_v2_oracle", ORACLE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self):
        self.connected = True
        self.connections = 1
        self.connection_attempts = 1
        self.raw: list[tuple[str, bytes, int, bool, float | None]] = []
        self.fail: Exception | None = None

    def publish_raw(self, topic, data, *, qos, retain, wait_s=None):
        if self.fail is not None:
            raise self.fail
        self.raw.append((topic, bytes(data), qos, retain, wait_s))

    def messages(self, stream=None):
        return [(t, json.loads(d), q, r) for t, d, q, r, _ in self.raw if stream is None or t.endswith("/" + stream)]

    def payloads(self, stream):
        return [m["payload"] for _, m, _, _ in self.messages(stream)]


def status(now, *, interval=2.0, tick_ok=True, last_tick_age=0.0, ok_age=0.0, collect_errors=0, publish_errors=0, oversized=0):
    return ReporterStatus(interval, now - last_tick_age, tick_ok, now - ok_age, now - ok_age, collect_errors, publish_errors, 0, oversized, None)


def make(client, *, inventory=None, alive=True, **kwargs):
    t = [100.0]
    ids = itertools.count(1)
    state = {"status": lambda now: status(now), "alive": alive, "inventory": dict(inventory or INVENTORY)}
    publisher = TelemetryPublisher(
        client, VM_ID,
        boot_id=BOOT_ID,
        agent_version="0.3.0",
        collect_inventory=lambda: dict(state["inventory"]),
        reporter_status=lambda: state["status"](t[0]),
        reporter_alive=lambda: state["alive"],
        uptime_ms=lambda: int(t[0] * 1000),
        clock=lambda: t[0],
        new_id=lambda: uuid.UUID(int=next(ids)),
        **kwargs,
    )
    return publisher, state, t


def validate_all(oracle, client):
    for topic_, data, qos, retain, _ in client.raw:
        oracle.validate(json.loads(data), topic_, qos, retain, len(data))


def test_first_tick_publishes_inventory_heartbeat_and_metrics_that_pass_the_v2_oracle(oracle):
    client = FakeClient()
    publisher, _, _ = make(client)
    publisher.tick()
    validate_all(oracle, client)
    assert [(t, q, r, w) for t, _, q, r, w in client.raw] == [
        (topic(VM_ID, "inventory"), 1, True, 0), (topic(VM_ID, "heartbeat"), 0, False, 0), (topic(VM_ID, "metrics"), 0, False, 0),
    ]
    _, heartbeat, _, _ = client.messages("heartbeat")[0]
    assert heartbeat["schema_version"] == "2.1" and heartbeat["type"] == "telemetry.heartbeat" and heartbeat["seq"] == "1"
    assert heartbeat["message_id"] == f"{VM_ID}/{BOOT_ID}/{publisher.publisher_instance_id}/heartbeat/1"
    assert heartbeat["context"] == {"binding": "unbound", "host_boot_id": None, "execution_id": None, "receipt_id": None}
    assert heartbeat["clock"] == {"sent_uptime_ms": "100000", "sent_at": None, "utc_quality": "unavailable", "utc_source": None, "uncertainty_ms": None}
    payload = heartbeat["payload"]
    assert payload["publisher_state"] == "running" and payload["app_health_asserted"] is False
    assert payload["last_collection_success_uptime_ms"] == payload["collection"]["end_uptime_ms"] == "100000"
    assert {k: payload[k] for k in ("collection_failures_total", "connection_attempts_total", "publish_submit_failures_total", "events_dropped_total")} \
        == {"collection_failures_total": "0", "connection_attempts_total": "1", "publish_submit_failures_total": "0", "events_dropped_total": "0"}
    assert payload["collection"]["status"] == "ok" and payload["collection"]["source_rc"] == 0
    metrics = client.payloads("metrics")[0]
    assert metrics["family"] == "publisher" and [s["name"] for s in metrics["samples"]] == [
        "publisher.collection_failures_total", "publisher.publish_submit_failures_total", "publisher.events_dropped_total",
        "publisher.payload_oversize_total", "publisher.puback_timeouts_total",
    ]
    assert {s["name"]: s["status"] for s in metrics["samples"] if s["status"] != "ok"} \
        == {"publisher.events_dropped_total": "unsupported", "publisher.puback_timeouts_total": "unsupported"}
    # 받는 쪽 참조 구현도 같은 순서로 받아들인다 (바인딩은 외부 검증된 신원을 흉내 낸다)
    consumer = oracle.ReferenceConsumer()
    consumer.bind(VM_ID, BOOT_ID, publisher.publisher_instance_id)
    assert [consumer.ingest(m, 100_000) for _, m, _, _ in client.messages()] == ["INVENTORY_ACCEPTED", "CURRENT_OBSERVATION", "CURRENT_OBSERVATION"]


def test_inventory_describes_the_linux_agent_without_inventing_values(oracle):
    client = FakeClient()
    publisher, _, _ = make(client)
    publisher.tick()
    validate_all(oracle, client)
    inventory = client.payloads("inventory")[0]
    assert inventory["os"] == {"id": "debian", "version": "13", "kernel_release": "6.12"} and inventory["hostname"] == "d"
    assert inventory["display_name"] is None  # 플랫폼 표시 이름은 게스트가 모른다
    assert inventory["firmware"] == {"build_id": "xenova-agent 0.3.0", "artifact_sha256": None, "measurement_profile": "linux_agent"}
    assert inventory["publisher"] == {"name": "xenova.agent", "version": "0.3.0", "metric_catalog": "xenova-telemetry-core-2.1"}
    assert inventory["interfaces"] == [{"name": "eth0", "mac": "02:00:00:00:00:01", "addresses": [{"family": "ipv4", "address": "192.168.77.10"}]}]
    assert inventory["capabilities"] == CAPABILITIES and inventory["capabilities_omitted"] == 0
    assert inventory["clock_domains"]["same_epoch"] is False and inventory["clock_domains"]["frequency_hz"] is None
    assert inventory["limits"] == {"max_payload_bytes": MAX_PAYLOAD_BYTES, "heartbeat_period_ms": 2000, "metrics_period_ms": 5000,
                                   "event_queue_capacity": 0, "diagnostic_stack_scan_default": "off"}


def test_inventory_values_are_sanitized_to_the_contract(oracle):
    many = [{"name": f"veryverylongbridgeinterfacename{i:02d}", "mac": "AA:BB:CC:DD:EE:FF" if i == 0 else "bogus",
             "addresses": [{"address": f"fe80::{j}", "family": "ipv6"} for j in range(6)]} for i in range(20)]
    inventory = {"os": {"id": "Ubuntu Core!", "version_id": None}, "kernel_release": "k" * 200, "hostname": "unknown", "interfaces": many}
    client = FakeClient()
    publisher, _, t = make(client, inventory=inventory, max_payload_bytes=2000)  # 계약 상한 안에서는 inventory 가 4096 을 넘지 않아 상한을 낮춰 본다
    publisher.tick()
    assert publisher.oversized == 1 and client.messages("inventory") == []  # 상한을 넘는 inventory 는 보내지 않는다
    assert client.payloads("metrics")[0]["samples"][3] == {
        "name": "publisher.payload_oversize_total", "unit": "count", "kind": "counter", "encoding": "u64_decimal", "value": "1",
        "status": "ok", "reason": None, "source_rc": 0, "labels": {},
    }
    t[0] += 2
    publisher.tick()
    assert publisher.oversized == 1  # 같은 실패를 heartbeat 주기마다 되풀이하지 않는다
    t[0] += 60
    publisher.tick()
    assert publisher.oversized == 2  # 갱신 주기에 다시 시도한다
    payload = publisher._inventory_payload(inventory)
    assert payload["os"] == {"id": "linux", "version": "unknown", "kernel_release": "k" * 128} and payload["hostname"] is None
    assert len(payload["interfaces"]) == 4 and payload["interfaces_omitted"] == 16
    first = payload["interfaces"][0]
    assert first["name"] == "veryverylongbridgeinterfacename" and first["mac"] == "aa:bb:cc:dd:ee:ff" and len(first["addresses"]) == 4
    assert payload["interfaces"][1]["mac"] is None  # 형식이 아닌 MAC 은 꾸미지 않고 null
    # 줄인 결과는 계약을 지킨다
    inventory["interfaces"] = many[:6]
    client = FakeClient()
    publisher, _, _ = make(client, inventory=inventory)
    publisher.tick()
    validate_all(oracle, client)
    sent = client.payloads("inventory")[0]
    assert len(sent["interfaces"]) == 4 and sent["interfaces_omitted"] == 2 and sent["limits"]["max_payload_bytes"] == MAX_PAYLOAD_BYTES


def test_interfaces_beyond_the_contract_limit_drop_addressless_ones_first(oracle):
    """dom0 처럼 vif 가 많은 호스트: 계약 상한 4개를 IPv4 있는 것부터 채우고 나머지는 interfaces_omitted 로 센다."""
    interfaces = [
        {"name": "eth0", "mac": "d8:3a:dd:a6:b2:77", "addresses": []},
        {"name": "vif1.0", "mac": "fe:ff:ff:ff:ff:ff", "addresses": [{"address": "fe80::1", "family": "ipv6"}]},
        {"name": "vif2.0", "mac": "fe:ff:ff:ff:ff:ff", "addresses": []},
        {"name": "vif3.0", "mac": "fe:ff:ff:ff:ff:ff", "addresses": []},
        {"name": "wlan0", "mac": "d8:3a:dd:a6:b2:78", "addresses": [{"address": "192.168.30.96", "family": "ipv4"}]},
        {"name": "xenbr0", "mac": "0a:b5:fa:4c:30:54", "addresses": [{"address": "192.168.77.1", "family": "ipv4"}]},
    ]
    client = FakeClient()
    publisher, _, _ = make(client, inventory={**INVENTORY, "interfaces": interfaces})
    publisher.tick()
    validate_all(oracle, client)
    inventory = client.payloads("inventory")[0]
    assert [i["name"] for i in inventory["interfaces"]] == ["wlan0", "xenbr0", "vif1.0", "eth0"] and inventory["interfaces_omitted"] == 2


def test_seq_is_per_stream_and_offline_ticks_consume_nothing(oracle):
    client = FakeClient()
    publisher, _, t = make(client)
    for _ in range(4):  # 손으로 2초마다 tick: 100, 102, 104, 106. metrics 예정 105 는 106 틱에서 나간다(run 의 예정은 아래 테스트)
        publisher.tick()
        t[0] += 2
    assert [m["seq"] for _, m, _, _ in client.messages("heartbeat")] == ["1", "2", "3", "4"]
    assert [m["seq"] for _, m, _, _ in client.messages("metrics")] == ["1", "2"]
    assert len(client.messages("inventory")) == 1
    client.connected = False
    for _ in range(3):
        publisher.tick()
        t[0] += 2
    assert publisher.skipped_offline == 3 and len(client.raw) == 7  # 끊긴 동안 보내지도 쌓지도 않는다
    client.connected, client.connections = True, 2
    publisher.tick()
    validate_all(oracle, client)
    assert [m["seq"] for _, m, _, _ in client.messages("inventory")] == ["1", "2"]  # 재연결: retained 를 잃은 브로커를 위해 다시
    assert client.messages("heartbeat")[-1][1]["seq"] == "5"  # 번호는 이어진다


def test_inventory_is_rechecked_after_refresh_and_resent_only_when_changed():
    client = FakeClient()
    publisher, state, t = make(client, inventory_refresh_s=60.0)
    publisher.tick()
    t[0] += 60
    publisher.tick()
    assert len(client.messages("inventory")) == 1
    state["inventory"]["hostname"] = "renamed"
    t[0] += 60
    publisher.tick()
    assert [p["hostname"] for p in client.payloads("inventory")] == ["d", "renamed"]


def test_client_without_connection_counter_sends_inventory_on_first_tick_only():
    client = FakeClient()
    del client.connections
    publisher, _, t = make(client)
    publisher.tick()
    t[0] += 2
    publisher.tick()
    assert len(client.messages("inventory")) == 1 and len(client.messages("heartbeat")) == 2


def test_publisher_state_follows_reporter_liveness_and_last_tick():
    client = FakeClient()
    publisher, state, t = make(client)

    def heartbeat():
        publisher.tick()
        t[0] += 2
        return client.payloads("heartbeat")[-1]["publisher_state"]

    assert heartbeat() == "running"
    state["status"] = lambda now: status(now, last_tick_age=6.5)  # 2초 주기 틱이 3주기 넘게 없다
    assert heartbeat() == "degraded"
    state["status"] = lambda now: status(now, last_tick_age=5.9)
    assert heartbeat() == "running"
    state["status"] = lambda now: status(now, tick_ok=False)  # 마지막 metrics 수집 실패
    assert heartbeat() == "degraded"
    state["status"] = lambda now: status(now)
    assert heartbeat() == "running"  # 다음 정상 수집에서 복귀
    state["alive"] = False  # Reporter 스레드가 죽었다
    assert heartbeat() == "degraded"
    state["alive"] = True
    state["status"] = lambda now: None  # Reporter 가 없다
    assert heartbeat() == "degraded"


def test_reporter_that_has_not_ticked_yet_is_running_until_a_stall_interval_passes():
    client = FakeClient()
    publisher, state, t = make(client)
    state["status"] = lambda now: ReporterStatus(2.0, None, None, None, None, 0, 0, 0, 0, None)
    publisher.tick()
    assert client.payloads("heartbeat")[-1]["publisher_state"] == "running"
    assert client.payloads("heartbeat")[-1]["last_collection_success_uptime_ms"] is None
    t[0] += 7
    publisher.tick()
    assert client.payloads("heartbeat")[-1]["publisher_state"] == "degraded"


def test_last_collection_success_is_moved_to_uptime_and_never_after_collection_end(oracle):
    client = FakeClient()
    publisher, state, t = make(client)
    state["status"] = lambda now: status(now, ok_age=1.5)
    publisher.tick()
    validate_all(oracle, client)
    payload = client.payloads("heartbeat")[-1]
    assert payload["last_collection_success_uptime_ms"] == "98500" and payload["collection"]["end_uptime_ms"] == "100000"


def test_totals_add_reporter_counters_v1_heartbeat_failures_and_own_submit_failures(oracle):
    client = FakeClient()
    publisher, state, t = make(client, v1_publish_failures=lambda: 3)
    state["status"] = lambda now: status(now, collect_errors=2, publish_errors=1, oversized=1)
    client.fail = RuntimeError("no connection")
    publisher.tick()  # inventory·heartbeat·metrics 제출이 모두 실패: 번호는 쓰였다(빠진 번호 = 손실)
    assert publisher.submit_failures == 3 and client.raw == []
    client.fail = None
    t[0] += 5
    publisher.tick()
    validate_all(oracle, client)
    assert client.messages("inventory") == []  # 보내지 못한 inventory 는 heartbeat 주기마다가 아니라 갱신 주기·재연결 때 다시
    t[0] += 60
    publisher.tick()
    assert [m["seq"] for _, m, _, _ in client.messages("inventory")] == ["2"]
    heartbeat = client.payloads("heartbeat")[-1]
    assert heartbeat["publish_submit_failures_total"] == "7" and heartbeat["collection_failures_total"] == "2"
    samples = {s["name"]: s["value"] for s in client.payloads("metrics")[-1]["samples"]}
    assert samples["publisher.publish_submit_failures_total"] == "7" and samples["publisher.collection_failures_total"] == "2"
    assert samples["publisher.payload_oversize_total"] == "1"


def test_inventory_collection_failure_is_counted_and_nothing_is_invented(oracle):
    client = FakeClient()
    publisher, _, _ = make(client)
    publisher._collect_inventory = _raise(PermissionError(13, "denied"))
    publisher.tick()
    validate_all(oracle, client)
    assert client.messages("inventory") == [] and publisher.collection_failures == 1
    assert client.payloads("heartbeat")[-1]["collection_failures_total"] == "1"
    assert client.payloads("heartbeat")[-1]["collection"]["status"] == "ok"  # heartbeat 자체 수집은 됐다


def test_reporter_status_failure_is_a_failed_collection_with_negative_rc(oracle):
    client = FakeClient()
    publisher, state, _ = make(client)
    state["status"] = _raise(RuntimeError("boom"))
    publisher.tick()
    validate_all(oracle, client)
    heartbeat = client.payloads("heartbeat")[-1]
    assert heartbeat["collection"]["status"] == "collection_failed" and heartbeat["collection"]["source_rc"] == -1
    assert heartbeat["publisher_state"] == "degraded" and heartbeat["last_collection_success_uptime_ms"] is None
    assert heartbeat["collection_failures_total"] == "1"  # Reporter 상태를 못 읽은 것도 이 발행기의 수집 실패
    counters = [s for s in client.payloads("metrics")[-1]["samples"] if s["status"] != "unsupported"]
    assert all(s["status"] == "collection_failed" and s["value"] is None and s["source_rc"] == -1 for s in counters)
    # 유닛 테스트: 발행된 metrics 의 collection 블록도 실패다
    assert client.payloads("metrics")[-1]["collection"]["status"] == "collection_failed"


class FakeStop:
    """stop.wait(timeout) 가 가짜 시계를 timeout 만큼 민다. 정해진 시각이 지나면 정지."""

    def __init__(self, t, until):
        self.t = t
        self.until = until
        self.waits: list[float] = []

    def wait(self, timeout):
        self.waits.append(timeout)
        self.t[0] += timeout
        return self.t[0] >= self.until


def test_run_schedules_metrics_every_5s_independently_of_the_2s_heartbeat():
    client = FakeClient()
    publisher, _, t = make(client)
    publisher.run(FakeStop(t, 110.5))
    sent = lambda stream: [int(m["clock"]["sent_uptime_ms"]) // 1000 for _, m, _, _ in client.messages(stream)]  # noqa: E731
    assert sent("heartbeat") == [100, 102, 104, 106, 108, 110, 112]  # 마지막은 stopping
    assert sent("metrics") == [100, 105, 110]  # inventory 의 metrics_period_ms 대로
    assert sent("inventory") == [100]


def test_run_keeps_waiting_while_disconnected_instead_of_spinning():
    client = FakeClient()
    client.connected = False
    publisher, _, t = make(client)
    stop = FakeStop(t, 120.5)
    publisher.run(stop)
    assert client.raw == [] and publisher.skipped_offline > 0
    assert min(stop.waits) >= 1.0  # 예정이 지난 metrics 때문에 0초 대기를 반복하지 않는다


def test_run_sends_a_final_stopping_heartbeat_when_stopped(oracle):
    client = FakeClient()
    publisher, _, _ = make(client)
    stop = threading.Event()
    stop.set()
    publisher.run(stop)
    validate_all(oracle, client)
    states = [p["publisher_state"] for p in client.payloads("heartbeat")]
    assert states == ["running", "stopping"]
    assert client.messages("heartbeat")[-1][1]["seq"] == "2"


def test_boot_uptime_is_milliseconds_since_boot():
    first = boot_uptime_ms()
    assert isinstance(first, int) and 0 <= first <= boot_uptime_ms()


def _raise(exc):
    def fn(*_):
        raise exc
    return fn
