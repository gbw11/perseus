"""복구 이벤트 outbox 와 GET /api/v1/recovery/events (S15P31E110-125, 계약 4.7)."""

import json
import os
import stat
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from dom0_manager.config import Settings
from dom0_manager.main import create_app
from dom0_manager.recovery.events import EventLog, EventLogUnavailable


def event(n, event_type="recovery.started"):
    terminal = {
        "recovery.started": {},
        "recovery.completed": {"attempt_duration_ms": 500, "recovery_duration_ms": 1000, "execution_id": "exec-new"},
        "recovery.failed": {"attempt_duration_ms": 500, "recovery_duration_ms": 1000, "final": True,
                            "error": {"code": "XEN_EXECUTION_FAILED", "platform_code": "XEN_EXECUTION_FAILED", "message": "xl failed"}},
        "recovery.recovered": {"attempt_duration_ms": 500, "recovery_duration_ms": 1000},
        "recovery.cancelled": {"attempt_duration_ms": 500, "recovery_duration_ms": 1000,
                               "error": {"code": "TARGET_NOT_ELIGIBLE", "platform_code": None, "message": "desired_state=stopped"}},
    }[event_type]
    return {**_base(n, event_type), **terminal}


def _base(n, event_type):
    return {
        "event_id": str(UUID(int=n)), "type": event_type, "vm_id": str(UUID(int=7)), "recovery_id": str(UUID(int=1)),
        "recovery_attempt_id": str(UUID(int=50 + n)), "attempt": 1, "reason": "HEARTBEAT_LOST", "mode": "graceful-restart",
        "detected_at": "2026-10-23T01:00:00.000Z", "occurred_at": f"2026-10-23T01:00:{n % 60:02d}.000Z",
        "attempt_duration_ms": None, "recovery_duration_ms": None, "execution_id": None, "final": None, "next_retry_at": None, "error": None,
    }


def ids(events):
    return [e["event_id"] for e in events]


def test_event_log_appends_in_order_and_pages_after_a_cursor(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    for n in range(1, 6):
        assert log.append(event(n)) is True
    page = log.after(None, 2)
    assert ids(page["events"]) == [str(UUID(int=1)), str(UUID(int=2))]
    assert page["next_after_event_id"] == str(UUID(int=2)) and page["oldest_event_id"] == str(UUID(int=1)) and page["gap"] is False
    page = log.after(UUID(int=2), 10)
    assert ids(page["events"]) == [str(UUID(int=n)) for n in (3, 4, 5)]
    page = log.after(UUID(int=5), 10)  # 새 이벤트 없음: 빈 페이지는 커서 null, gap 아님 (계약 4.7)
    assert page == {"events": [], "next_after_event_id": None, "oldest_event_id": str(UUID(int=1)), "gap": False}


def test_unknown_cursor_is_a_gap_and_restarts_from_the_oldest(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(event(1))
    log.append(event(2))
    page = log.after(UUID(int=99), 10)
    assert page["gap"] is True and ids(page["events"]) == [str(UUID(int=1)), str(UUID(int=2))]
    assert page["oldest_event_id"] == str(UUID(int=1)) and page["next_after_event_id"] == str(UUID(int=2))
    empty = EventLog(tmp_path / "none.jsonl").after(UUID(int=99), 10)
    assert empty == {"events": [], "next_after_event_id": None, "oldest_event_id": None, "gap": True}


def test_damaged_lines_after_the_cursor_are_reported_as_a_gap_and_never_move_the_cursor(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append(event(1))
    log.append(event(2))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
        fh.write(json.dumps({"event_id": str(UUID(int=3)), "type": "recovery.started"}) + "\n")  # 계약 필수 필드 없음
    log.append(event(4))
    log.append(event(5))

    page = log.after(None, 2)  # 손상은 2번 뒤: 이 페이지 범위 밖이라 아직 gap 아님
    assert ids(page["events"]) == [str(UUID(int=1)), str(UUID(int=2))] and page["gap"] is False
    page = log.after(UUID(int=2), 1)  # 커서와 다음 이벤트 사이에 손상: gap, 커서는 돌려준 마지막 이벤트
    assert ids(page["events"]) == [str(UUID(int=4))] and page["gap"] is True and page["next_after_event_id"] == str(UUID(int=4))
    page = log.after(UUID(int=4), 10)  # 손상을 지난 뒤에는 gap 아님
    assert ids(page["events"]) == [str(UUID(int=5))] and page["gap"] is False
    assert ids(log.events()) == [str(UUID(int=n)) for n in (1, 2, 4, 5)]


def test_event_log_keeps_only_recent_events(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", keep=5)
    for n in range(1, 10):
        log.append(event(n))
    assert ids(log.events()) == [str(UUID(int=n)) for n in range(5, 10)]
    assert log.after(None, 100)["oldest_event_id"] == str(UUID(int=5))


def test_compaction_rewrites_the_file_atomically_without_damaged_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path, keep=3)
    log.append(event(1))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    for n in range(2, 3 + 200 + 2):  # keep + slack 를 넘기면 다시 쓴다
        log.append(event(n))
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 3 and json.loads(lines[-1])["event_id"] == str(UUID(int=204))
    assert not path.with_suffix(".jsonl.tmp").exists()


@pytest.mark.parametrize("broken", [
    lambda: {**event(1), "occurred_at": None},
    lambda: {k: v for k, v in event(1).items() if k != "attempt"},
    lambda: {**event(1), "final": True},  # started 에 terminal 필드
    lambda: {**event(1, "recovery.completed"), "execution_id": None},  # 새 실행 ID 없는 completed
    lambda: {**event(1, "recovery.failed"), "error": None},  # error 없는 failed
    lambda: {**event(1, "recovery.failed"), "final": False},  # 재시도 실패인데 next_retry_at 없음(recovery_duration_ms 도 있음)
    lambda: {**event(1, "recovery.failed"), "recovery_duration_ms": None},  # 최종 실패인데 장애 시간 없음
    lambda: {**event(1, "recovery.recovered"), "error": {"code": "TARGET_HEALTHY", "platform_code": None, "message": "x"}},  # 회복은 사유 없음
    lambda: {**event(1, "recovery.recovered"), "final": True},
    lambda: {**event(1, "recovery.cancelled"), "error": None},  # 취소 사유 없음
    lambda: {**event(1, "recovery.cancelled"), "recovery_duration_ms": None},
    lambda: {**event(1, "recovery.failed"), "recovery_attempt_id": None},  # 시도 ID 없는 failed
    lambda: {**event(1, "recovery.recovered"), "recovery_attempt_id": None},  # 장애 단위인데 attempt_duration_ms 가 있음
])
def test_append_refuses_an_event_that_does_not_match_the_contract(tmp_path, broken):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert log.append(broken()) is False
    assert not path.exists() and log.events() == []
    assert log.append(event(3)) is True


@pytest.mark.parametrize("event_type", ["recovery.recovered", "recovery.cancelled"])
def test_end_events_other_than_completed_and_failed_are_accepted(tmp_path, event_type):
    log = EventLog(tmp_path / "events.jsonl")
    assert log.append(event(1, event_type)) is True and [e["type"] for e in log.events()] == [event_type]
    recovery_level = {**event(2, event_type), "recovery_attempt_id": None, "attempt_duration_ms": None}
    assert log.append(recovery_level) is True


def test_unreadable_log_is_reported_not_hidden_as_empty(tmp_path):
    path = tmp_path / "events.jsonl"
    path.mkdir()  # 있지만 읽을 수 없는 경로(권한·I/O 오류 흉내)
    log = EventLog(path)
    with pytest.raises(EventLogUnavailable):
        log.after(None, 10)
    with pytest.raises(EventLogUnavailable):
        log.events()
    assert EventLog(tmp_path / "missing.jsonl").after(None, 10)["events"] == []  # 아직 없는 파일은 오류가 아니다


@pytest.mark.parametrize("error, unavailable", [(FileNotFoundError(2, "No such file"), False), (PermissionError(13, "Permission denied"), True)])
def test_only_a_missing_file_reads_as_empty(tmp_path, monkeypatch, error, unavailable):
    import dom0_manager.recovery.events as events_module

    path = tmp_path / "events.jsonl"
    EventLog(path).append(event(1))

    def failing_open(*args, **kwargs):
        raise error

    monkeypatch.setattr(events_module, "open", failing_open, raising=False)  # exists() 는 True 여도 open 결과로만 판단한다
    log = EventLog(path)
    if unavailable:
        with pytest.raises(EventLogUnavailable):
            log.after(None, 10)
    else:
        assert log.after(None, 10)["events"] == []


def test_compaction_never_rewrites_a_log_it_could_not_read(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    log = EventLog(path, keep=1)
    for n in range(1, 4):
        log.append(event(n))
    before = path.read_text(encoding="utf-8")

    def unreadable(self):
        raise EventLogUnavailable("simulated read failure")

    monkeypatch.setattr(EventLog, "_read", unreadable)
    log._compact()  # noqa: SLF001
    assert path.read_text(encoding="utf-8") == before


def test_unwritable_file_reports_unconfirmed_append_then_same_id_retry(tmp_path):
    if os.name != "nt" and os.geteuid() == 0:
        pytest.skip("root ignores file permissions")
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert log.append(event(1)) is True
    os.chmod(path, stat.S_IREAD)  # 쓸 수 없는 상태(권한·디스크 문제)
    try:
        assert log.append(event(2)) is False  # 엔진이 레코드에 남겨 두고 같은 event_id 로 다시 부른다
    finally:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    assert log.append(event(2)) is True
    assert ids(log.events()) == [str(UUID(int=1)), str(UUID(int=2))]


def test_compaction_keeps_a_tombstone_for_damage_inside_the_retained_window(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path, keep=3)
    for n in range(1, 203):
        log.append(event(n))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    log.append(event(203))
    log.append(event(204))  # keep + slack 를 넘겨 다시 쓴다
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert [line.get("event_id") for line in lines] == [str(UUID(int=202)), None, str(UUID(int=203)), str(UUID(int=204))]
    assert lines[1] == {"tombstone": "damaged"}
    assert log.after(UUID(int=202), 10)["gap"] is True  # compaction 뒤에도 손상 자리를 안다
    assert log.after(UUID(int=203), 10)["gap"] is False


def test_complete_duplicate_lines_are_deduplicated_by_event_id(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append(event(1))
    with open(path, "a", encoding="utf-8") as fh:  # 재시도 중 같은 이벤트가 두 번 기록된 파일
        fh.write(json.dumps(event(1)) + "\n")
    log.append(event(2))
    assert ids(log.events()) == [str(UUID(int=1)), str(UUID(int=2))] and log.after(None, 10)["gap"] is False


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("XENOVA_DRIVER", "mock")
    settings = Settings.from_env({"XENOVA_DRIVER": "mock", "XENOVA_RECOVERY_STATE_DIR": str(tmp_path)})
    log = EventLog(tmp_path / "recovery-events.jsonl")
    client = TestClient(create_app(settings))
    return client, log


def test_recovery_events_endpoint_pages_and_reports_gap(api):
    client, log = api
    assert client.get("/api/v1/recovery/events").json() == {"events": [], "next_after_event_id": None, "oldest_event_id": None, "gap": False}
    for n in range(1, 4):
        log.append(event(n, "recovery.completed" if n == 3 else "recovery.started"))
    body = client.get("/api/v1/recovery/events", params={"limit": 2}).json()
    assert ids(body["events"]) == [str(UUID(int=1)), str(UUID(int=2))] and body["gap"] is False
    assert body["events"][0]["type"] == "recovery.started" and body["events"][0]["vm_id"] == str(UUID(int=7))
    body = client.get("/api/v1/recovery/events", params={"after_event_id": body["next_after_event_id"]}).json()
    assert [e["type"] for e in body["events"]] == ["recovery.completed"] and body["next_after_event_id"] == str(UUID(int=3))
    body = client.get("/api/v1/recovery/events", params={"after_event_id": body["next_after_event_id"]}).json()
    assert body["events"] == [] and body["next_after_event_id"] is None and body["gap"] is False  # 조회 끝 (계약 4.7)
    body = client.get("/api/v1/recovery/events", params={"after_event_id": str(UUID(int=77))}).json()
    assert body["gap"] is True and body["oldest_event_id"] == str(UUID(int=1)) and len(body["events"]) == 3


def test_recovery_events_endpoint_rejects_bad_arguments_with_400(api):
    client, _ = api
    assert client.get("/api/v1/recovery/events", params={"after_event_id": "nope"}).status_code == 400
    assert client.get("/api/v1/recovery/events", params={"limit": 0}).status_code == 400
    assert client.get("/api/v1/recovery/events", params={"limit": 1001}).status_code == 400


def test_recovery_events_endpoint_returns_503_when_the_log_cannot_be_read(tmp_path, monkeypatch):
    monkeypatch.setenv("XENOVA_DRIVER", "mock")
    (tmp_path / "recovery-events.jsonl").mkdir()
    client = TestClient(create_app(Settings.from_env({"XENOVA_DRIVER": "mock", "XENOVA_RECOVERY_STATE_DIR": str(tmp_path)})))
    response = client.get("/api/v1/recovery/events")
    body = response.json()
    assert response.status_code == 503 and body["code"] == "PlatformUnavailable"
    assert body["message"] == "recovery event log is unavailable; retry later" and "recovery-events.jsonl" in body["detail"]  # 경로·OS 오류는 detail 에만


def test_recovery_events_endpoint_reports_damaged_lines_as_a_gap(api):
    client, log = api
    log.append(event(1))
    with open(log.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event_id": str(UUID(int=2)), "type": "recovery.started"}) + "\n")  # 필수 필드 없음
    log.append(event(3))
    body = client.get("/api/v1/recovery/events").json()
    assert ids(body["events"]) == [str(UUID(int=1)), str(UUID(int=3))] and body["gap"] is True
    body = client.get("/api/v1/recovery/events", params={"after_event_id": str(UUID(int=3))}).json()
    assert body["events"] == [] and body["gap"] is False
