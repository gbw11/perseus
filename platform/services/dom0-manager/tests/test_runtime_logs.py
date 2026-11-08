from dataclasses import replace
import hashlib
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from dom0_manager.config import Settings
from dom0_manager.main import create_app
from dom0_manager.runtime_logs import LogStore, scrub
from dom0_manager.log_collector import journal_record
from dom0_manager.journal_reader import JournalLines


def record(store, n, **changes):
    value = dict(source_id="dom0", vm_id=None, component="xenova-dom0-manager.service",
                 boot_id="boot-1", observed_at="2026-10-28T00:00:00Z", level="INFO",
                 message=f"message {n}", native_id=str(n))
    value.update(changes)
    return store.append(**value)


def test_retention_duplicate_replay_and_cursor_gap(tmp_path):
    store = LogStore(tmp_path / "logs.db", max_rows=3)
    for n in range(6):
        record(store, n)
    page = store.query(after=1, limit=2)
    assert [r["message"] for r in page["items"]] == ["message 3", "message 4"]
    assert page["history_truncated"] and page["has_more"]
    assert not record(store, 5)
    assert [r["message"] for r in store.query(after=page["next_cursor"])["items"]] == ["message 5"]
    assert store.query(after=100)["next_cursor"] == 100


def test_source_filters_and_store_epoch_survive_restart(tmp_path):
    path = tmp_path / "logs.db"
    store = LogStore(path)
    record(store, 1)
    vm_id = "00000000-0000-4000-8000-000000000001"
    record(store, 2, vm_id=vm_id, source_id="vm:" + vm_id)
    assert [r["message"] for r in store.query(vm_id=vm_id)["items"]] == ["message 2"]
    assert len(store.query(source_id="dom0")["items"]) == 1
    assert LogStore(path).epoch == store.epoch


def test_secret_redaction_happens_before_persistence(tmp_path):
    path = tmp_path / "logs.db"
    store = LogStore(path)
    record(store, 1, message="\x1b[31mAuthorization: Bearer top-secret-token password=hunter2 token=xyz\x1b[0m")
    result = store.query()["items"][0]["message"]
    assert "REDACTED" in result
    for secret in ["top-secret-token", "hunter2", "xyz", "\x1b"]:
        assert secret not in result
    with sqlite3.connect(path) as db:
        assert "hunter2" not in db.execute("SELECT message FROM logs").fetchone()[0]


def test_env_key_names_and_quoted_whitespace_are_redacted():
    value = scrub('MYSQL_PASSWORD="private value" JWT_SECRET=jwt-value XENOVA_LOG_SOURCE_TOKEN=source-key')
    for secret in ["private", "value", "jwt-value", "source-key"]:
        assert secret not in value
    assert value.count("[REDACTED]") == 3


def test_collector_liveness_is_not_inferred_from_last_log(tmp_path):
    now = [10.0]
    store = LogStore(tmp_path / "logs.db", clock=lambda: now[0])
    store.source("dom0", ["app.service"])
    assert store.sources()[0]["state"] == "connected"
    assert store.sources()[0]["last_record_at"] is None
    now[0] += 11
    assert store.sources()[0]["state"] == "disconnected"


def test_age_retention_and_message_size_are_bounded(tmp_path):
    now = [1.0]
    store = LogStore(tmp_path / "logs.db", max_age_s=5, clock=lambda: now[0])
    record(store, 1)
    now[0] = 10
    record(store, 2, message="x" * 20000)
    assert len(store.query()["items"]) == 1
    assert len(store.query()["items"][0]["message"]) == 8192
    now[0] = 20
    assert store.query()["items"] == []


def test_journal_identity_uses_source_boot_and_native_cursor():
    value = journal_record({"__CURSOR": "cursor1", "_SYSTEMD_UNIT": "app.service", "_BOOT_ID": "boot1",
                            "MESSAGE": "hello", "__REALTIME_TIMESTAMP": "1000000", "PRIORITY": "3"})
    assert value["component"] == "app.service" and value["native_id"] == "cursor1"
    assert value["level"] == "ERROR" and value["vm_id"] is None


def test_binary_journal_reader_drains_coalesced_and_partial_lines():
    lines = JournalLines()
    assert lines.feed(b'{"a":1}\n{"a":2}\n{"a":') == [b'{"a":1}', b'{"a":2}']
    assert lines.feed(b'3}\n') == [b'{"a":3}']


def test_oversize_journal_record_does_not_block_the_next_record():
    lines = JournalLines(limit=10)
    assert lines.feed(b'x' * 11) == []
    assert lines.feed(b'oversize-tail\nvalid\n') == [b'valid']


def test_log_queries_require_existing_service_auth_and_report_unconfigured(tmp_path):
    settings = Settings(service_token="test-key", runtime_log_db=str(tmp_path / "logs.db"))
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/logs").status_code == 401
        assert client.get("/api/v1/logs", headers={"Authorization": "Bearer test-key"}).status_code == 503
        store = LogStore(settings.runtime_log_db)
        record(store, 1)
        result = client.get("/api/v1/logs", headers={"Authorization": "Bearer test-key"})
        assert result.status_code == 200 and result.json()["items"][0]["message"] == "message 1"
        assert client.get("/api/v1/logs?vm_id=invalid", headers={"Authorization": "Bearer test-key"}).status_code == 400
        assert client.post("/api/v1/logs", json={"message": "not console input"}, headers={"Authorization": "Bearer test-key"}).status_code == 405


def producer_client(tmp_path):
    config = tmp_path / "producers.json"
    vm = "00000000-0000-4000-8000-000000000003"
    config.write_text(json.dumps({"sources": {"debian-vm3": {"vm_id": vm, "components": ["app.service"],
        "token_sha256": hashlib.sha256(b"vm3-private-key").hexdigest()}}}))
    settings = Settings(service_token="control-private-key", runtime_log_db=str(tmp_path / "logs.db"),
                        runtime_log_ingest_config=str(config))
    db = LogStore(settings.runtime_log_db)
    client = TestClient(create_app(settings))
    record = {"component": "app.service", "boot_id": "boot-three", "observed_at": "2026-10-28T01:00:00Z",
              "level": "ERROR", "message": "drive renderer failed", "native_id": "journal-three"}
    return client, db, record, {"Authorization": "Bearer vm3-private-key", "X-Log-Source": "debian-vm3"}


def test_producer_token_cannot_read_control_api_or_spoof_a_vm(tmp_path):
    client, db, entry, headers = producer_client(tmp_path)
    with client:
        assert client.get("/api/v1/vms", headers=headers).status_code == 401
        assert client.get("/api/v1/logs", headers=headers).status_code == 401
        result = client.post("/api/v1/logs/ingest", json={"records": [entry]}, headers=headers)
        assert result.status_code == 200 and result.json()["accepted"] == 1
        replay = client.post("/api/v1/logs/ingest", json={"records": [entry]}, headers=headers)
        assert replay.json() == {"accepted": 0, "duplicates": 1}
        assert db.query()["items"][0]["vm_id"].endswith("0003")
        assert client.post("/api/v1/logs/ingest", json={"records": [{**entry, "vm_id": "other-vm"}]}, headers=headers).status_code == 400
        assert client.post("/api/v1/logs/ingest", json={"records": [{**entry, "component": "other.service"}]}, headers=headers).status_code == 400
        assert len(db.query()["items"]) == 1


def test_ingest_401_uses_the_error_schema(tmp_path):
    client, db, entry, headers = producer_client(tmp_path)
    with client:
        wrong = client.post("/api/v1/logs/ingest", json={"records": [entry]},
                            headers={**headers, "Authorization": "Bearer wrong"})
        assert wrong.status_code == 401
        assert wrong.json() == {"code": "Unauthorized", "message": "source authentication required"}
        assert db.query()["items"] == []


def test_batches_are_validated_before_writing_and_keys_stay_private(tmp_path):
    client, db, entry, headers = producer_client(tmp_path)
    with client:
        assert client.post("/api/v1/logs/ingest", json={"records": [entry, {**entry, "level": "BAD"}]}, headers=headers).status_code == 400
        assert db.query()["items"] == []
        assert client.post("/api/v1/logs/ingest", json={"records": []}, headers=headers).status_code == 200
        assert client.post("/api/v1/logs/ingest", content=b"x" * 65537, headers=headers).status_code == 400
        sources = client.get("/api/v1/logs/sources", headers={"Authorization": "Bearer control-private-key"})
        assert sources.json()["items"][0]["state"] == "connected"
        assert "token_sha256" not in sources.text and "vm3-private-key" not in sources.text


def test_private_ingest_listener_exposes_no_vm_control(tmp_path):
    from dom0_manager.log_ingest_app import create_app as ingest_app
    client, db, entry, headers = producer_client(tmp_path)
    settings = client.app.state.settings
    with TestClient(ingest_app(settings)) as private:
        assert private.get("/api/v1/vms", headers=headers).status_code == 404
        assert private.get("/api/v1/logs", headers=headers).status_code == 404
        assert private.post("/api/v1/logs/ingest", json={"records": [entry]}, headers=headers).status_code == 200
