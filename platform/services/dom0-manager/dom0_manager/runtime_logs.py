"""Bounded execution logs, separate from interactive console bytes and VM events."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from uuid import UUID, uuid4

MAX_MESSAGE = 8192
MAX_ROWS = 20000
MAX_AGE_S = 86400
IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:@/-]{1,128}$")
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")
SECRETS = re.compile(
    r'''(?ix)(\b(?:[a-z0-9_]*(?:password|passwd|secret|token|api_key)|authorization)\b["']?\s*[:=]\s*)
        (?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:Bearer\s+)?[^\s,;]+)'''
)
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")


def scrub(message: str) -> str:
    """Defence in depth; producers must never log secrets in the first place."""
    value = ANSI.sub("", message)
    value = "".join(c for c in value if c in "\n\t" or ord(c) >= 32)
    return BEARER.sub("Bearer [REDACTED]", SECRETS.sub(r"\1[REDACTED]", value))[:MAX_MESSAGE]


class LogStore:
    def __init__(self, path, *, max_rows=MAX_ROWS, max_age_s=MAX_AGE_S, clock=time.time):
        self.path = Path(path)
        self.max_rows, self.max_age_s, self.clock = max_rows, max_age_s, clock
        if not 1 <= max_rows <= MAX_ROWS or not 1 <= max_age_s <= MAX_AGE_S:
            raise ValueError("invalid log retention")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                    source_id TEXT NOT NULL, vm_id TEXT, component TEXT NOT NULL,
                    boot_id TEXT NOT NULL, observed_at TEXT NOT NULL, received_at REAL NOT NULL,
                    level TEXT NOT NULL, message TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS logs_vm_id ON logs(vm_id,id);
                CREATE INDEX IF NOT EXISTS logs_source_id ON logs(source_id,id);
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY, vm_id TEXT, components TEXT NOT NULL,
                    state TEXT NOT NULL, updated_at REAL NOT NULL, last_record_at REAL,
                    checkpoint TEXT);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO metadata VALUES('epoch',?)", (str(uuid4()),))
            self.epoch = db.execute("SELECT value FROM metadata WHERE key='epoch'").fetchone()[0]
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=2000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def append(self, *, source_id, vm_id, component, boot_id, observed_at, level, message, native_id):
        for value in (source_id, component, boot_id):
            if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
                raise ValueError("invalid log identity")
        if vm_id is not None:
            vm_id = str(UUID(vm_id))
        if not isinstance(message, str) or not isinstance(native_id, str) or not 1 <= len(native_id) <= 512:
            raise ValueError("invalid log record")
        if level not in {"DEBUG", "INFO", "WARN", "ERROR", "FATAL"}:
            raise ValueError("invalid log level")
        if not isinstance(observed_at, str) or len(observed_at) > 64:
            raise ValueError("invalid log timestamp")
        event_id = hashlib.sha256(json.dumps([source_id, boot_id, native_id]).encode()).hexdigest()
        now = self.clock()
        with self.connect() as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO logs(event_id,source_id,vm_id,component,boot_id,observed_at,received_at,level,message) VALUES(?,?,?,?,?,?,?,?,?)",
                (event_id, source_id, vm_id, component, boot_id, observed_at, now, level, scrub(message)),
            ).rowcount
            # The monotonic integer cursor is not reused after retention removes older rows.
            db.execute("DELETE FROM logs WHERE received_at < ?", (now - self.max_age_s,))
            db.execute("DELETE FROM logs WHERE id <= COALESCE((SELECT id FROM logs ORDER BY id DESC LIMIT 1 OFFSET ?),0)", (self.max_rows,))
        return bool(inserted)

    def source(self, source_id, components, *, vm_id=None, state="connected", checkpoint=None, recorded=False):
        if state not in {"connected", "disconnected", "unavailable"}:
            raise ValueError("invalid source state")
        now = self.clock()
        with self.connect() as db:
            db.execute("""INSERT INTO sources VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(source_id) DO UPDATE SET vm_id=excluded.vm_id,components=excluded.components,
                state=excluded.state,updated_at=excluded.updated_at,
                last_record_at=COALESCE(excluded.last_record_at,sources.last_record_at),
                checkpoint=COALESCE(excluded.checkpoint,sources.checkpoint)""",
                (source_id, vm_id, json.dumps(components), state, now, now if recorded else None, checkpoint))

    def checkpoint(self, source_id):
        with self.connect() as db:
            row = db.execute("SELECT checkpoint FROM sources WHERE source_id=?", (source_id,)).fetchone()
        return row[0] if row else None

    def query(self, *, after=None, source_id=None, vm_id=None, component=None, level=None, limit=200):
        if after is not None and (type(after) is not int or after < 0):
            raise ValueError("after must be a non-negative integer")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be 1..500")
        if vm_id is not None:
            vm_id = str(UUID(vm_id))
        cutoff = self.clock() - self.max_age_s
        clauses, values = ["received_at>=?"], [cutoff]
        for key, value in [("source_id", source_id), ("vm_id", vm_id), ("component", component), ("level", level)]:
            if value is not None:
                if len(value) > 128:
                    raise ValueError("invalid log filter")
                clauses.append(key + "=?")
                values.append(value)
        with self.connect() as db:
            first, last = db.execute("SELECT MIN(id),MAX(id) FROM logs WHERE received_at>=?", (cutoff,)).fetchone()
            if after is not None:
                clauses.append("id>?")
                values.append(after)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = db.execute("SELECT * FROM logs" + where + " ORDER BY id " + ("DESC" if after is None else "ASC") + " LIMIT ?", (*values, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        if after is None:
            rows.reverse()
        items = [dict(row) for row in rows]
        return {"epoch": self.epoch, "items": items, "next_cursor": items[-1]["id"] if items else max(after or 0, last or 0),
                "has_more": more if after is not None else False,
                "history_truncated": after is not None and first is not None and after < first - 1,
                "oldest_cursor": first, "latest_cursor": last or 0}

    def sources(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM sources ORDER BY source_id").fetchall()
        return [{"source_id": r["source_id"], "vm_id": r["vm_id"], "components": json.loads(r["components"]),
                 "state": r["state"] if 0 <= self.clock() - r["updated_at"] < 10 else "disconnected",
                 "updated_at": r["updated_at"], "last_record_at": r["last_record_at"]} for r in rows]
