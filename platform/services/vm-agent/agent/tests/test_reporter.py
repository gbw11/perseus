"""inventory·metrics 발행 루프: 연결 상태, 끊김 동안 버림, 크기 상한, Spring 수신 규칙."""

from __future__ import annotations

import json

from xenova_mqtt import Identity, envelope

from xenova_agent.processes import ProcessSample
from xenova_agent.reporter import Reporter

VM_ID = "11111111-1111-4111-8111-111111111111"
INVENTORY = {"os": {"id": "debian", "version_id": "13", "pretty_name": "Debian 13"}, "kernel_release": "6.12", "hostname": "d",
             "interfaces": [], "agent_version": "0.2.0", "capabilities": {"commands": [], "metrics": []}}
VALUES = [{"name": "cpu_percent_allocated", "value": 12.5, "unit": "percent", "labels": {}, "unavailable_reason": None},
          {"name": "memory_used_bytes", "value": None, "unit": "bytes", "labels": {}, "unavailable_reason": "read_failed"}]


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
    assert len(json.dumps(client.published[-1][2]).encode()) <= 3000
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
