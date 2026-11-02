"""inventory·metrics 발행 루프: 연결 상태, 끊김 동안 버림, 크기 상한, Spring 수신 규칙."""

from __future__ import annotations

import json

from xenova_mqtt import Identity, envelope

from xenova_agent.processes import ProcessSample
from xenova_agent.reporter import Reporter, payload_size

VM_ID = "11111111-1111-4111-8111-111111111111"
INVENTORY = {"os": {"id": "debian", "version_id": "13", "pretty_name": "Debian 13"}, "kernel_release": "6.12", "hostname": "d",
             "interfaces": [], "agent_version": "0.2.0", "capabilities": {"commands": [], "metrics": []}}
VALUES = [{"name": "cpu_percent_allocated", "value": 12.5, "unit": "percent", "labels": {}, "unavailable_reason": None},
          {"name": "disk_read_bytes_total", "value": None, "unit": "bytes", "labels": {}, "unavailable_reason": "read_failed"}]  # 장치별 값의 실패는 틱 실패가 아니다


class FakeClient:
    def __init__(self):
        self.connected = True
        self.published = []

    def publish(self, topic, message_type, payload, **kwargs):
        self.published.append((topic, message_type, json.loads(json.dumps(payload)), kwargs))
        return {}

    def types(self):
        return [p[1] for p in self.published]


def ok_sample(rows):
    rows = list(rows or [])
    return ProcessSample("ok", rows, discovered=len(rows), read_ok=len(rows), candidates=len(rows))


def make(client, *, inventory=None, processes=None, sample=None, clock=None, **kwargs):
    state = {"inventory": dict(inventory or INVENTORY)}
    t = clock or [100.0]
    reporter = Reporter(
        client, VM_ID,
        collect_inventory=lambda: dict(state["inventory"]),
        collect_metrics=lambda: [dict(v) for v in VALUES],
        collect_processes=lambda: sample if sample is not None else ok_sample(processes),
        interval_s=2.0, inventory_refresh_s=60.0, clock=lambda: t[0], **kwargs,
    )
    return reporter, state, t


def test_inventory_on_connect_then_metrics_every_tick_with_real_window():
    client = FakeClient()
    reporter, _, t = make(client)
    reporter.tick()
    assert client.types() == ["agent.inventory", "agent.metrics"]
    topic, _, payload, kwargs = client.published[1]
    assert topic == f"xenova/v1/vms/{VM_ID}/metrics" and payload["window_ms"] is None and kwargs == {"wait_s": 0}
    assert client.published[0][0] == f"xenova/v1/vms/{VM_ID}/inventory"
    t[0] += 2.3
    reporter.tick()
    assert client.types()[-1] == "agent.metrics" and client.published[-1][2]["window_ms"] == 2300
    assert client.types().count("agent.inventory") == 1  # 바뀌지 않으면 다시 보내지 않는다


def test_disconnected_samples_are_dropped_and_inventory_is_resent_on_reconnect():
    client = FakeClient()
    reporter, _, t = make(client)
    reporter.tick()
    client.connected = False
    for _ in range(5):
        t[0] += 2
        reporter.tick()
    assert reporter.metrics_dropped == 5 and len(client.published) == 2  # 끊긴 동안 쌓지 않는다
    client.connected = True
    t[0] += 2
    reporter.tick()
    assert client.types()[2:] == ["agent.inventory", "agent.metrics"]  # 재연결: 브로커가 retained 를 잃었어도 복구
    assert client.published[-1][2]["window_ms"] == 2000  # 끊긴 동안에도 수집은 이어 와서 새 표본의 구간은 한 주기


def test_inventory_change_is_published_when_rechecked():
    client = FakeClient()
    reporter, state, t = make(client)
    reporter.tick()
    state["inventory"]["hostname"] = "renamed"
    t[0] += 30
    reporter.tick()
    assert client.types().count("agent.inventory") == 1  # 다시 읽을 때가 아님
    t[0] += 31
    reporter.tick()
    assert client.types().count("agent.inventory") == 2 and client.published[-2][2]["hostname"] == "renamed"


def test_oversized_payload_drops_processes_from_the_tail():
    client = FakeClient()
    processes = [{"pid": n, "name": "x" * 200, "cpu_percent": 100 - n, "rss_bytes": 1} for n in range(40)]
    reporter, _, _ = make(client, processes=processes, payload_limit=3000)
    reporter.tick()
    sent = client.published[-1][2]["processes"]
    assert 0 < len(sent) < 40 and sent == processes[: len(sent)]
    assert payload_size(client.published[-1][2]) <= 3000  # Reporter 가 재는 compact 직렬화 기준
    assert client.published[-1][2]["process_collection"]["truncated"] is True  # 크기 상한으로 줄인 것도 잘림이다


def test_process_collection_tells_why_the_list_is_empty():
    client = FakeClient()
    failed = ProcessSample("read_failed", error="PermissionError(EACCES)")
    reporter, _, _ = make(client, sample=failed)
    reporter.tick()
    metrics = client.published[-1][2]
    assert metrics["processes"] == []
    assert metrics["process_collection"] == {"status": "read_failed", "discovered": 0, "read_ok": 0, "vanished": 0, "failed": 0,
                                             "truncated": False, "error": "PermissionError(EACCES)"}

    client2 = FakeClient()
    partial = ProcessSample("partial", [{"pid": 1, "name": "a", "cpu_percent": 1.0, "rss_bytes": 1}], discovered=5, read_ok=3,
                            vanished=1, failed=1, candidates=3)
    reporter2, _, _ = make(client2, sample=partial)
    reporter2.tick()
    collection = client2.published[-1][2]["process_collection"]
    assert (collection["status"], collection["failed"], collection["truncated"], collection["error"]) == ("partial", 1, True, None)


def test_collection_or_publish_errors_skip_the_tick_without_stopping():
    client = FakeClient()
    reporter, _, t = make(client)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("proc gone")
        return [dict(v) for v in VALUES]

    reporter._collect_metrics = flaky  # noqa: SLF001
    reporter.tick()
    t[0] += 2
    reporter.tick()
    assert reporter.errors == 1 and client.types() == ["agent.inventory", "agent.metrics"]


def test_payloads_follow_the_spring_receiver_rules():
    """Spring AgentMessageHandler: inventory 필수값, metric 단위(percent·bytes), null 이면 사유, processes 필드, 봉투 64KB."""
    client = FakeClient()
    processes = [{"pid": 1, "name": "init", "cpu_percent": 0.5, "rss_bytes": 4096}]
    reporter, _, _ = make(client, processes=processes)
    reporter.tick()
    inv = client.published[0][2]
    assert inv["os"]["id"] and inv["kernel_release"] and inv["hostname"]
    metrics = client.published[1][2]
    assert set(metrics) == {"window_ms", "values", "processes", "process_collection"}
    assert set(metrics["process_collection"]) == {"status", "discovered", "read_ok", "vanished", "failed", "truncated", "error"}
    assert metrics["process_collection"]["status"] in ("first_sample", "ok", "partial", "read_failed", "disabled")
    for v in metrics["values"]:
        assert v["name"] and v["unit"] in ("percent", "bytes") and set(v) == {"name", "value", "unit", "labels", "unavailable_reason"}
        assert (v["value"] is None) == (v["unavailable_reason"] is not None)
    assert all(set(p) == {"pid", "name", "cpu_percent", "rss_bytes"} for p in metrics["processes"])
    built = envelope.build(Identity(vm_id=VM_ID), "agent.metrics", metrics, 1)
    assert len(json.dumps(built).encode()) < 64 * 1024


def test_an_unexpected_exception_does_not_end_the_reporter_thread():
    import threading

    client = FakeClient()
    reporter, _, _ = make(client)
    ticks = {"n": 0}
    stop = threading.Event()

    def broken_tick():
        ticks["n"] += 1
        if ticks["n"] >= 3:
            stop.set()
        raise RuntimeError("bug")

    reporter.tick = broken_tick
    reporter._interval = 0.01  # noqa: SLF001
    reporter._clock = __import__("time").monotonic  # noqa: SLF001
    reporter.run(stop)
    assert ticks["n"] >= 3 and reporter.errors >= 3


def test_a_reconnect_between_two_ticks_still_republishes_inventory():
    """브로커가 두 틱 사이에 재시작돼 connected 는 계속 True 였어도, 연결 횟수가 바뀌었으면 retained inventory 를 다시 보낸다."""
    client = FakeClient()
    client.connections = 1
    reporter, _, t = make(client)
    reporter.tick()
    t[0] += 2
    reporter.tick()
    assert client.types().count("agent.inventory") == 1
    client.connections = 2  # 끊김과 재연결이 틱 사이에 끝남
    t[0] += 2
    reporter.tick()
    assert client.types().count("agent.inventory") == 2


def test_values_alone_over_the_limit_are_not_published():
    client = FakeClient()
    reporter, _, _ = make(client, processes=[{"pid": 1, "name": "p", "cpu_percent": 1.0, "rss_bytes": 1}], payload_limit=200)
    reporter.tick()
    assert client.types() == ["agent.inventory"] and reporter.oversized == 1


def test_status_snapshot_tracks_ticks_collection_and_publish_outcomes():
    """telemetry.py 가 publisher_state 를 정하는 근거. metrics 수집 실패만 틱 실패이고 inventory 실패는 수집 오류로만 센다."""
    client = FakeClient()
    t = [100.0]
    state = {"metrics": None, "inventory": None}

    def collect(kind, value):
        def fn():
            if state[kind] is not None:
                raise state[kind]
            return value()
        return fn

    reporter = Reporter(
        client, VM_ID,
        collect_inventory=collect("inventory", lambda: dict(INVENTORY)),
        collect_metrics=collect("metrics", lambda: [dict(v) for v in VALUES]),
        collect_processes=lambda: ok_sample([]),
        interval_s=2.0, inventory_refresh_s=4.0, clock=lambda: t[0],
    )
    s = reporter.status()
    assert (s.interval_s, s.last_tick_at, s.last_tick_ok, s.last_collect_ok_at, s.last_publish_ok_at) == (2.0, None, None, None, None)
    reporter.tick()
    s = reporter.status()
    assert (s.last_tick_at, s.last_tick_ok, s.last_collect_ok_at, s.last_publish_ok_at) == (100.0, True, 100.0, 100.0)
    t[0] += 2
    state["metrics"] = OSError("proc gone")
    reporter.tick()
    s = reporter.status()
    assert (s.last_tick_at, s.last_tick_ok, s.last_collect_ok_at, s.collect_errors, s.last_error) == (102.0, False, 100.0, 1, "OSError")
    t[0] += 2
    state["metrics"] = None
    state["inventory"] = PermissionError("denied")  # 4초 지나 inventory 를 다시 읽다 실패: 틱은 성공이다
    reporter.tick()
    s = reporter.status()
    assert (s.last_tick_ok, s.last_collect_ok_at, s.collect_errors, s.last_error) == (True, 104.0, 2, "PermissionError")
    t[0] += 2
    client.publish = _broken_publish
    reporter.tick()
    s = reporter.status()
    assert (s.last_tick_ok, s.last_collect_ok_at, s.last_publish_ok_at, s.publish_errors, s.last_error) == (True, 106.0, 104.0, 1, "ValueError")


def _broken_publish(topic, message_type, payload, **kwargs):
    raise ValueError("bad envelope")


def test_tick_validity_is_judged_from_returned_values_not_only_exceptions():
    """수집기는 /proc 실패를 예외가 아니라 null + 사유와 ProcessSample.status 로 돌려준다. 필수 원천(CPU·메모리·프로세스 목록)의
    read_failed·parse_error 만 틱 실패이고, first_sample·counter_reset·partial·디스크/네트워크 사유는 아니다. 실패한 표본도 보낸다."""
    client = FakeClient()
    t = [100.0]
    state = {"values": [], "sample": ok_sample([])}
    reporter = Reporter(
        client, VM_ID,
        collect_inventory=lambda: dict(INVENTORY),
        collect_metrics=lambda: [dict(v) for v in state["values"]],
        collect_processes=lambda: state["sample"],
        interval_s=2.0, clock=lambda: t[0],
    )

    def tick(values, sample=None):
        state["values"] = values
        state["sample"] = sample if sample is not None else ok_sample([])
        t[0] += 2
        reporter.tick()
        s = reporter.status()
        return s.last_tick_ok, s.last_collect_ok_at, s.collect_errors, s.last_error

    def value(name, reason, unit="bytes"):
        return {"name": name, "value": None, "unit": unit, "labels": {}, "unavailable_reason": reason}

    assert tick([value("cpu_percent_allocated", "first_sample", "percent")]) == (True, 102.0, 0, None)
    assert tick([value("cpu_percent_allocated", "counter_reset", "percent")]) == (True, 104.0, 0, None)
    assert tick([value("cpu_percent_allocated", "read_failed", "percent")]) == (False, 104.0, 1, "cpu_percent_allocated:read_failed")
    assert tick([value("memory_used_bytes", "parse_error"), value("memory_available_bytes", "parse_error")]) == (False, 104.0, 2, "memory_used_bytes:parse_error")
    assert tick([value("disk_read_bytes_total", "read_failed"), value("net_rx_bytes_total", "device_not_found")]) == (True, 110.0, 2, "memory_used_bytes:parse_error")
    assert tick([], ProcessSample("read_failed", error="PermissionError(EACCES)")) == (False, 110.0, 3, "processes:PermissionError(EACCES)")
    assert tick([], ProcessSample("partial", discovered=3, read_ok=2, failed=1, candidates=3)) == (True, 114.0, 3, "processes:PermissionError(EACCES)")
    assert tick([], ProcessSample("disabled")) == (True, 116.0, 3, "processes:PermissionError(EACCES)")
    assert client.types().count("agent.metrics") == 8  # 실패한 표본도 null + 사유로 나간다
