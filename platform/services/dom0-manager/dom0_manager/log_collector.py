"""Explicit journal sources -> bounded log DB. Never attaches to a guest console."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

from .runtime_logs import IDENTIFIER, LogStore
from .journal_reader import JournalLines

LEVELS = {"0": "FATAL", "1": "FATAL", "2": "ERROR", "3": "ERROR", "4": "WARN", "5": "INFO", "6": "INFO", "7": "DEBUG"}
DEFAULT_UNITS = ["xenova-dom0-manager.service", "xenova-recovery-worker.service",
                 "xenova-display-feed.service", "xenova-display-graphics.service", "xenova-display-kiosk.service"]


def journal_record(row, source_id="dom0", vm_id=None):
    cursor = row.get("__CURSOR")
    unit = row.get("_SYSTEMD_UNIT")
    message = row.get("MESSAGE")
    if not isinstance(cursor, str) or not isinstance(unit, str) or not isinstance(message, str):
        raise ValueError("journal record lacks text, unit or cursor")
    observed = datetime.fromtimestamp(int(row["__REALTIME_TIMESTAMP"]) / 1_000_000, timezone.utc).isoformat()
    return {"source_id": source_id, "vm_id": vm_id, "component": unit, "boot_id": row["_BOOT_ID"],
            "observed_at": observed, "level": LEVELS.get(str(row.get("PRIORITY")), "INFO"),
            "message": message, "native_id": cursor}


def follow(store, units, *, stopped, executable="journalctl"):
    if not units or len(units) > 32 or any(not IDENTIFIER.fullmatch(u) or not u.endswith(".service") for u in units):
        raise ValueError("explicit systemd service allowlist required")
    checkpoint = store.checkpoint("dom0")
    args = [executable, "--no-pager", "--output=json", "--follow", "--lines=200"]
    for unit in units:
        args += ["--unit", unit]
    if checkpoint:
        args += ["--after-cursor", checkpoint]
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    framing = JournalLines()
    try:
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        next_status = 0
        while not stopped():
            if process.poll() is not None:
                # A vacuumed/missing cursor must not prevent future log delivery forever.
                if checkpoint:
                    with store.connect() as db:
                        db.execute("UPDATE sources SET checkpoint=NULL WHERE source_id='dom0'")
                raise RuntimeError("journal reader stopped; collector restart required")
            if time.monotonic() >= next_status:
                store.source("dom0", units)
                next_status = time.monotonic() + 2
            for key, _ in selector.select(timeout=.5):
                for raw in framing.feed(os.read(key.fileobj.fileno(), 65536)):
                    try:
                        row = json.loads(raw)
                        if row.get("_SYSTEMD_UNIT") not in units:
                            continue
                        record = journal_record(row)
                        store.append(**record)
                        store.source("dom0", units, checkpoint=record["native_id"], recorded=True)
                    except (ValueError, KeyError, TypeError, OverflowError):
                        continue
    finally:
        store.source("dom0", units, state="disconnected")
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if process.stdout:
            process.stdout.close()
        if "selector" in locals():
            selector.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.environ.get("XENOVA_RUNTIME_LOG_DB"))
    parser.add_argument("--unit", action="append")
    args = parser.parse_args()
    if not args.db:
        parser.error("--db or XENOVA_RUNTIME_LOG_DB required")
    stop = False
    def stopping(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, stopping)
    signal.signal(signal.SIGINT, stopping)
    follow(LogStore(args.db), args.unit or DEFAULT_UNITS, stopped=lambda: stop)


if __name__ == "__main__":
    main()
