"""MQTT-04 데모 명령: 검사·거절 사유, stress 수명, agent_exit 의 PUBACK 확인, 같은 command_id 재실행 방지와 기록 실패 시 실행 거부."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from xenova_mqtt import Message

from xenova_agent.commands import EXIT_CODE, CommandHandler, CommandHistory

VM_ID = "11111111-1111-4111-8111-111111111111"
BOOT_ID = "b0000000-0000-4000-8000-000000000001"
NOW = datetime(2026, 11, 4, 3, 0, 0, tzinfo=timezone.utc)
COMMAND_TOPIC = f"xenova/v1/vms/{VM_ID}/command"
RESULT_TOPIC = f"xenova/v1/vms/{VM_ID}/command-result"


def iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class FakeInfo:
    def __init__(self) -> None:
        self.acked = False

    def is_published(self) -> bool:
        return self.acked


class FakeTicket:
    def __init__(self, info) -> None:
        self.info = info


class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, dict, dict]] = []
        self.subscriptions: list[tuple[str, int]] = []
        self.handler = None
        self.connected = True
        self.tracked: list[FakeInfo] = []

    def subscribe(self, topic_filter, handler=None, *, qos=1):
        self.subscriptions.append((topic_filter, qos))
        self.handler = handler

    def publish(self, topic, message_type, payload, **kwargs):
        self.published.append((topic, message_type, dict(payload), kwargs))
        return {}

    def publish_tracked(self, topic, message_type, payload):
        if not self.connected:
            raise RuntimeError("publisher disconnected before submission")
        self.published.append((topic, message_type, dict(payload), {"tracked": True}))
        self.tracked.append(FakeInfo())
        return FakeTicket(self.tracked[-1])

    def results(self) -> list[dict]:
        assert all(topic == RESULT_TOPIC and kind == "agent.command_result" for topic, kind, _, _ in self.published)
        return [payload for _, _, payload, _ in self.published]


class FakeStress:
    def __init__(self, cpu: int, memory: int, duration: int) -> None:
        self.args = (cpu, memory, duration)
        self.state: str | None = None
        self.stopped = 0

    def poll(self):
        return self.state

    def stop(self):
        self.stopped += 1


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def command(command_id="c-1", *, action="stress", parameters=None, boot_id=BOOT_ID, sent=NOW, expires=None, retained=False,
            message_type="agent.command", **extra):
    payload = {
        "command_id": command_id,
        "operation_id": "op-1",
        "expected_boot_id": boot_id,
        "expires_at": iso(expires or sent + timedelta(seconds=60)),
        "action": action,
        "parameters": {"cpu_workers": 1, "duration_s": 10} if parameters is None and action == "stress" else (parameters or {}),
    }
    payload.update(extra)
    envelope = {"schema_version": "1.0", "message_id": "m", "vm_id": VM_ID, "type": message_type, "sent_at": iso(sent), "payload": payload}
    return Message(COMMAND_TOPIC, envelope, retained=retained)


@pytest.fixture
def harness(tmp_path):
    client = FakeClient()
    runs: list[FakeStress] = []
    exits: list[bool] = []
    clock = Clock()

    def stress(cpu, memory, duration):
        runs.append(FakeStress(cpu, memory, duration))
        return runs[-1]

    def make(*, enabled=True, available=1024, state_dir=tmp_path):
        return CommandHandler(
            client, VM_ID, boot_id=BOOT_ID, enabled=enabled, history=CommandHistory(state_dir),
            on_exit=lambda: exits.append(True), stress_factory=stress, memory_available=lambda: available,
            now=lambda: NOW, clock=clock,
        )

    return make, client, runs, exits, clock


def test_subscribes_only_own_command_topic_with_qos1(harness):
    make, client, *_ = harness
    make().subscribe()
    assert client.subscriptions == [(COMMAND_TOPIC, 1)]


def test_stress_runs_then_succeeds_and_releases_load(harness):
    make, client, runs, _, clock = harness
    handler = make()
    handler.handle(command(parameters={"cpu_workers": 2, "memory_mib": 128, "duration_s": 30}))
    assert runs[0].args == (2, 128, 30)
    running = client.results()[0]
    assert running["status"] == "RUNNING" and running["command_id"] == "c-1" and running["operation_id"] == "op-1"
    assert running["started_at"] == iso(NOW) and running["finished_at"] is None
    assert running["result"] == {"cpu_workers": 2, "memory_mib": 128, "duration_s": 30}

    handler.poll()
    assert len(client.results()) == 1  # 아직 도는 중
    clock.t += 30.0
    runs[0].state = "succeeded"
    handler.poll()
    done = client.results()[1]
    assert done["status"] == "SUCCEEDED" and done["duration_ms"] == 30_000 and done["error"] is None
    assert done["finished_at"] == iso(NOW) and done["started_at"] == running["started_at"]
    assert runs[0].stopped == 1


def test_stress_process_failure_is_reported(harness):
    make, client, runs, *_ = harness
    handler = make()
    handler.handle(command())
    runs[0].state = "failed"
    handler.poll()
    assert client.results()[-1]["status"] == "FAILED"
    assert client.results()[-1]["error"]["code"] == "STRESS_FAILED"


def test_second_stress_while_running_is_busy(harness):
    make, client, runs, *_ = harness
    handler = make()
    handler.handle(command("c-1"))
    handler.handle(command("c-2"))
    assert len(runs) == 1
    assert client.results()[-1]["command_id"] == "c-2"
    assert client.results()[-1]["error"]["code"] == "BUSY"


@pytest.mark.parametrize(
    ("message", "code"),
    [
        (command(action="kernel_panic"), "UNSUPPORTED_ACTION"),
        (command(boot_id="b0000000-0000-4000-8000-000000000002"), "BOOT_MISMATCH"),
        (command(sent=NOW - timedelta(seconds=120)), "EXPIRED"),
        (command(sent=NOW + timedelta(seconds=31)), "CLOCK_UNTRUSTED"),
        (command(expires=NOW + timedelta(seconds=301)), "INVALID_COMMAND"),
        (command(expires=NOW - timedelta(seconds=1)), "INVALID_COMMAND"),
        (command(expires_at="2026-11-04T03:01:00"), "INVALID_COMMAND"),
        (command(retained=True), "INVALID_COMMAND"),
        (command(parameters={"cpu_workers": 1}), "INVALID_PARAMETERS"),
        (command(parameters={"cpu_workers": 5, "duration_s": 10}), "INVALID_PARAMETERS"),
        (command(parameters={"cpu_workers": True, "duration_s": 10}), "INVALID_PARAMETERS"),
        (command(parameters={"cpu_workers": 1, "duration_s": 10.0}), "INVALID_PARAMETERS"),
        (command(parameters={"cpu_workers": 0, "memory_mib": 0, "duration_s": 10}), "INVALID_PARAMETERS"),
        (command(parameters={"memory_mib": 513, "duration_s": 10}), "INVALID_PARAMETERS"),
        (command(parameters={"cpu_workers": 1, "duration_s": 121}), "INVALID_PARAMETERS"),
        (command(parameters={"cpu_workers": 1, "duration_s": 10, "level": 3}), "INVALID_PARAMETERS"),
        (command(action="agent_exit", parameters={"delay_s": 1}), "INVALID_PARAMETERS"),
        (command(operation_id=7), "INVALID_COMMAND"),
    ],
)
def test_rejections_report_code_and_run_nothing(harness, message, code):
    make, client, runs, exits, _ = harness
    make().handle(message)
    [result] = client.results()
    assert result["status"] == "REJECTED" and result["error"]["code"] == code
    assert result["finished_at"] == iso(NOW) and result["started_at"] is None
    assert runs == [] and exits == []


def test_disabled_agent_rejects_every_command(harness):
    make, client, runs, exits, _ = harness
    handler = make(enabled=False)
    assert handler.capabilities == []
    handler.handle(command())
    handler.handle(command("c-2", action="agent_exit"))
    assert [r["error"]["code"] for r in client.results()] == ["DEMO_DISABLED", "DEMO_DISABLED"]
    assert runs == [] and exits == []


def test_memory_beyond_available_minus_reserve_is_rejected(harness):
    make, client, runs, *_ = harness
    make(available=192).handle(command(parameters={"memory_mib": 129, "duration_s": 10}))
    assert client.results()[-1]["error"]["code"] == "RESOURCE_UNAVAILABLE" and runs == []
    make(available=None).handle(command("c-2", parameters={"memory_mib": 1, "duration_s": 10}))
    assert client.results()[-1]["error"]["code"] == "RESOURCE_UNAVAILABLE" and runs == []


def test_missing_command_id_is_dropped_without_result(harness):
    make, client, *_ = harness
    make().handle(command(""))
    make().handle(command(None))
    assert client.published == []


def test_other_message_types_on_the_command_topic_are_not_executed(harness):
    make, client, runs, exits, _ = harness
    make().handle(command(message_type="agent.heartbeat"))
    make().handle(command("x-1", action="agent_exit", message_type="agent.command_result"))
    assert client.published == [] and runs == [] and exits == []


def test_reused_command_id_with_a_different_request_is_a_conflict(harness):
    make, client, runs, exits, _ = harness
    handler = make()
    handler.handle(command("c-1"))
    handler.handle(command("c-1", action="agent_exit"))
    handler.handle(command("c-1", parameters={"cpu_workers": 2, "duration_s": 10}))
    handler.handle(command("c-1", boot_id="b0000000-0000-4000-8000-000000000002"))
    handler.handle(command("c-1", operation_id="op-2"))
    assert len(runs) == 1 and exits == []
    assert [r["error"]["code"] for r in client.results()[1:]] == ["IDEMPOTENCY_CONFLICT"] * 4
    # 충돌한 요청이 원래 기록을 덮지 않는다. 만료 시각만 다른 재전송은 같은 요청이다
    handler.handle(command("c-1", expires=NOW + timedelta(seconds=90)))
    assert client.results()[-1]["status"] == "RUNNING"


def test_redelivered_command_is_not_run_again(harness):
    make, client, runs, *_ = harness
    handler = make()
    handler.handle(command())
    handler.handle(command())
    assert len(runs) == 1
    assert [r["status"] for r in client.results()] == ["RUNNING", "RUNNING"]


def test_history_survives_agent_restart_in_same_boot(harness, tmp_path):
    make, client, runs, exits, _ = harness
    first = make()
    first.handle(command("x-1", action="agent_exit"))
    client.tracked[0].acked = True
    first.poll()
    assert exits == [True]
    restarted = make()  # 같은 RuntimeDirectory 를 읽는 새 에이전트
    restarted.handle(command("x-1", action="agent_exit"))
    assert exits == [True]  # 다시 종료하지 않는다
    assert [r["status"] for r in client.results()] == ["ACCEPTED", "ACCEPTED"]
    restarted.handle(command("s-1"))
    make().handle(command("s-1"))
    assert len(runs) == 1
    # 도는 중에 에이전트가 다시 시작됐다: 부하는 함께 끝났으므로 RUNNING 대신 FAILED 를 돌려준다
    assert client.results()[-1]["status"] == "FAILED" and client.results()[-1]["error"]["code"] == "AGENT_STOPPING"


def test_unwritable_history_refuses_to_run(harness, tmp_path):
    make, client, runs, exits, _ = harness
    handler = make(state_dir=tmp_path / "missing")
    handler.handle(command())
    handler.handle(command("x-1", action="agent_exit"))
    assert [r["error"]["code"] for r in client.results()] == ["STATE_UNAVAILABLE", "STATE_UNAVAILABLE"]
    assert runs == [] and exits == []
    handler.handle(command())  # 기록이 없으므로 같은 명령도 다시 거절될 뿐 실행되지 않는다
    assert runs == [] and client.results()[-1]["error"]["code"] == "STATE_UNAVAILABLE"


@pytest.mark.parametrize("content", ["{not json", '{"a": 1}', '[{"result": {"status": "RUNNING"}}]'])
def test_corrupt_history_disables_commands_and_is_not_overwritten(harness, tmp_path, content):
    make, client, runs, exits, _ = harness
    (tmp_path / "commands.json").write_text(content, encoding="utf-8")
    handler = make()
    handler.handle(command())
    handler.handle(command("x-1", action="agent_exit"))
    assert [r["error"]["code"] for r in client.results()] == ["STATE_UNAVAILABLE", "STATE_UNAVAILABLE"]
    assert runs == [] and exits == []
    assert (tmp_path / "commands.json").read_text(encoding="utf-8") == content


def test_agent_exit_exits_only_after_puback_and_never_reports_a_second_outcome(harness):
    make, client, runs, exits, _ = harness
    handler = make()
    handler.handle(command("s-1"))
    handler.handle(command("x-1", action="agent_exit"))
    stopped, accepted = client.results()[-2:]
    assert stopped["command_id"] == "s-1" and stopped["status"] == "FAILED" and stopped["error"]["code"] == "AGENT_EXITING"
    assert runs[0].stopped == 1
    assert accepted["command_id"] == "x-1" and accepted["status"] == "ACCEPTED" and accepted["finished_at"] is None
    assert client.published[-1][3] == {"tracked": True}
    for _ in range(3):  # PUBACK 이 늦어도 다른 결과를 내지 않고 기다린다
        handler.poll()
    assert exits == [] and not handler.exit_requested and len(client.published) == 3
    handler.handle(command("c-9"))  # 종료를 기다리는 동안 새 명령은 받지 않는다
    assert client.results()[-1]["error"]["code"] == "AGENT_EXITING" and len(runs) == 1
    client.tracked[0].acked = True
    handler.poll()
    assert exits == [True] and handler.exit_requested
    assert [r["status"] for r in client.results() if r["command_id"] == "x-1"] == ["ACCEPTED"]


def test_agent_exit_while_disconnected_submits_once_connected(harness):
    make, client, runs, exits, _ = harness
    client.connected = False
    handler = make()
    handler.handle(command("x-1", action="agent_exit"))
    handler.poll()
    assert client.published == [] and exits == []
    client.connected = True
    handler.poll()
    assert [r["status"] for r in client.results()] == ["ACCEPTED"] and len(client.tracked) == 1
    client.tracked[0].acked = True
    handler.poll()
    assert exits == [True]


def test_run_loop_processes_queue_and_stops_stress_on_shutdown(harness):
    make, client, runs, *_ = harness
    handler = make()
    stop = threading.Event()
    thread = handler.start(stop)
    handler._enqueue(command())
    for _ in range(100):
        if runs:
            break
        threading.Event().wait(0.01)
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert [r["status"] for r in client.results()] == ["RUNNING", "FAILED"]
    assert client.results()[-1]["error"]["code"] == "AGENT_STOPPING" and runs[0].stopped == 1


def test_exit_code_matches_unit_restart_prevention():
    from pathlib import Path

    unit = (Path(__file__).resolve().parents[1] / "deploy/xenova-agent.service").read_text(encoding="utf-8")
    assert f"RestartPreventExitStatus={EXIT_CODE}" in unit.splitlines()
    assert f"SuccessExitStatus={EXIT_CODE}" in unit.splitlines()
    assert "RuntimeDirectory=xenova-agent" in unit.splitlines()
    assert "RuntimeDirectoryPreserve=yes" in unit.splitlines()


def _phases(tmp_path):
    import json

    return {e["result"]["command_id"]: e["phase"] for e in json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))}


def test_restart_before_puback_resumes_exit_after_reconnect(harness, tmp_path):
    make, client, runs, exits, _ = harness
    client.connected = False
    make().handle(command("x-1", action="agent_exit"))  # 연결 없이 수락만 하고 죽었다
    assert client.published == [] and exits == [] and _phases(tmp_path) == {"x-1": "exit_pending"}
    restarted = make()
    restarted.poll()
    assert client.published == [] and exits == []
    restarted.handle(command("c-2"))  # 종료를 이어 가는 동안 새 명령은 받지 않는다
    assert client.results()[-1]["error"]["code"] == "AGENT_EXITING" and runs == []
    client.connected = True
    restarted.poll()
    assert [r["status"] for r in client.results() if r["command_id"] == "x-1"] == ["ACCEPTED"]
    client.tracked[-1].acked = True
    restarted.poll()
    assert exits == [True] and _phases(tmp_path)["x-1"] == "exit_acked"


def test_restart_after_submit_without_puback_resubmits(harness, tmp_path):
    make, client, runs, exits, _ = harness
    make().handle(command("x-1", action="agent_exit"))  # 제출했지만 PUBACK 전에 죽었다
    assert len(client.tracked) == 1 and exits == []
    restarted = make()
    restarted.poll()
    assert len(client.tracked) == 2  # 같은 ACCEPTED 를 다시 제출(결과 중복은 계약상 허용)
    assert client.published[-1][2] == client.published[0][2]
    client.tracked[-1].acked = True
    restarted.poll()
    assert exits == [True]


def test_manual_restart_after_acked_exit_does_not_exit_again(harness, tmp_path):
    make, client, runs, exits, _ = harness
    first = make()
    first.handle(command("x-1", action="agent_exit"))
    client.tracked[0].acked = True
    first.poll()
    assert exits == [True] and _phases(tmp_path)["x-1"] == "exit_acked"
    restarted = make()
    restarted.poll()
    restarted.handle(command("x-1", action="agent_exit"))
    restarted.poll()
    assert exits == [True] and not restarted.exit_requested
    assert [r["status"] for r in client.results()] == ["ACCEPTED", "ACCEPTED"]
    restarted.handle(command("s-1"))  # 다른 명령은 정상으로 받는다
    assert len(runs) == 1


def _entry(**overrides):
    result = {"command_id": "c-1", "operation_id": None, "status": "SUCCEEDED", "started_at": iso(NOW), "finished_at": iso(NOW),
              "duration_ms": 10, "result": {"cpu_workers": 1, "memory_mib": 0, "duration_s": 10}, "error": None}
    entry = {"fingerprint": "a" * 64, "result": result, "phase": None}
    for key, value in overrides.items():
        if key in result:
            result[key] = value
        else:
            entry[key] = value
    return entry


@pytest.mark.parametrize(
    "entries",
    [
        [{"fingerprint": "x", "result": {"command_id": "c-1", "status": "UNKNOWN"}}],
        [_entry(fingerprint="x")],
        [_entry(fingerprint="A" * 64)],
        [_entry(status="UNKNOWN")],
        [_entry(duration_ms=None)],
        [_entry(result={"cpu_workers": 999, "memory_mib": 0, "duration_s": 10})],
        [_entry(result={"cpu_workers": 1, "memory_mib": 0, "duration_s": 0})],
        [_entry(result={"cpu_workers": 0, "memory_mib": 0, "duration_s": 10})],
        [_entry(finished_at="yesterday")],
        [_entry(error={"code": "X"})],
        [_entry(status="ACCEPTED")],  # ACCEPTED 에 phase 가 없다
        [_entry(phase="exit_pending")],  # SUCCEEDED 에 phase 가 있다
        [_entry(), _entry()],  # 같은 command_id 두 번
        [{k: v for k, v in _entry().items() if k != "phase"}],
    ],
)
def test_malformed_history_entries_disable_commands(harness, tmp_path, entries):
    import json

    make, client, runs, *_ = harness
    (tmp_path / "commands.json").write_text(json.dumps(entries), encoding="utf-8")
    make().handle(command("c-9"))
    assert client.results()[-1]["error"]["code"] == "STATE_UNAVAILABLE" and runs == []


def test_two_pending_exits_are_corrupt(harness, tmp_path):
    import json

    make, client, *_ = harness
    accepted = dict(status="ACCEPTED", finished_at=None, duration_ms=None, result=None, phase="exit_pending")
    first, second = _entry(**accepted), _entry(**accepted)
    second["result"]["command_id"] = "c-2"
    (tmp_path / "commands.json").write_text(json.dumps([first, second]), encoding="utf-8")
    make().handle(command("c-9"))
    assert client.results()[-1]["error"]["code"] == "STATE_UNAVAILABLE"


def test_every_record_this_module_writes_reads_back_as_valid(harness, tmp_path):
    make, client, runs, *_ = harness
    first = make()
    first.handle(command("r-1", boot_id="b0000000-0000-4000-8000-000000000002"))  # REJECTED
    first.handle(command("s-1"))
    runs[-1].state = "succeeded"
    first.poll()  # SUCCEEDED
    first.handle(command("s-2"))
    runs[-1].state = "failed"
    first.poll()  # FAILED(STRESS_FAILED)
    first.handle(command("s-3"))  # RUNNING 인 채로 재시작 → 읽을 때 FAILED(AGENT_STOPPING)
    second = make()
    second.handle(command("r-1", boot_id="b0000000-0000-4000-8000-000000000002"))
    second.handle(command("s-4"))  # 바뀐 기록을 다시 써도 다음 에이전트가 읽을 수 있다
    third = make()
    for command_id in ("r-1", "s-1", "s-2", "s-3"):
        third.handle(command(command_id, boot_id="b0000000-0000-4000-8000-000000000002") if command_id == "r-1" else command(command_id))
    assert [r["status"] for r in client.results()[-4:]] == ["REJECTED", "SUCCEEDED", "FAILED", "FAILED"]
    assert len(runs) == 4


def test_exit_waits_until_exit_acked_is_recorded(harness, tmp_path, monkeypatch, caplog):
    import os as _os

    make, client, runs, exits, _ = harness
    handler = make()
    handler.handle(command("x-1", action="agent_exit"))
    client.tracked[0].acked = True
    real_replace = _os.replace
    monkeypatch.setattr("xenova_agent.commands.os.replace", lambda *a: (_ for _ in ()).throw(OSError("read-only")))
    with caplog.at_level("ERROR", logger="xenova_agent.commands"):
        for _ in range(3):
            handler.poll()
    assert exits == [] and not handler.exit_requested and _phases(tmp_path)["x-1"] == "exit_pending"
    assert len([r for r in caplog.records if "저장하지 못함" in r.getMessage()]) == 1  # 반복 실패는 한 번만 남긴다
    monkeypatch.setattr("xenova_agent.commands.os.replace", real_replace)
    handler.poll()
    assert exits == [True] and _phases(tmp_path)["x-1"] == "exit_acked"
    assert [r["status"] for r in client.results()] == ["ACCEPTED"]  # 저장을 기다리는 동안 다른 결과를 내지 않는다
