"""Linux/Jetson journal forwarder using a source-scoped token, never a console."""
import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.parse import urlparse

from .log_collector import journal_record
from .runtime_logs import IDENTIFIER, scrub
from .journal_reader import JournalLines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--unit", action="append", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--trusted-internal-http", action="store_true")
    args = parser.parse_args()
    url = urlparse(args.endpoint)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path != "/api/v1/logs/ingest"
            or (url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"} and not args.trusted_internal_http)):
        parser.error("HTTPS or explicitly trusted internal HTTP ingest endpoint required")
    if not IDENTIFIER.fullmatch(args.source) or any(not IDENTIFIER.fullmatch(u) or not u.endswith(".service") for u in args.unit):
        parser.error("invalid source or unit allowlist")
    token = os.environ.get("XENOVA_LOG_SOURCE_TOKEN", "")
    if not token:
        parser.error("XENOVA_LOG_SOURCE_TOKEN required in private service environment")
    path = Path(args.checkpoint)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    cursor = path.read_text().strip() if path.exists() else ""
    command = ["journalctl", "--output=json", "--follow", "--no-pager", "--lines=200"]
    for unit in args.unit:
        command += ["--unit", unit]
    if cursor:
        command += ["--after-cursor", cursor]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    framing = JournalLines()
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = build_opener(ProxyHandler({}), NoRedirect())
    def send(records):
        request = Request(args.endpoint, data=json.dumps({"records": records}).encode(), method="POST",
                          headers={"Authorization": "Bearer " + token, "X-Log-Source": args.source,
                                   "Content-Type": "application/json"})
        with opener.open(request, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError("log sink unavailable")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    next_heartbeat = 0
    try:
        while not stopped:
            if process.poll() is not None:
                if cursor:
                    path.unlink(missing_ok=True)
                raise RuntimeError("journal reader stopped; restart with bounded replay")
            if time.monotonic() >= next_heartbeat:
                send([])
                next_heartbeat = time.monotonic() + 2
            for key, _ in selector.select(.5):
                for raw in framing.feed(os.read(key.fileobj.fileno(), 65536)):
                    try:
                        row = json.loads(raw)
                        if row.get("_SYSTEMD_UNIT") not in args.unit:
                            continue
                        record = journal_record(row)
                        record.pop("source_id")
                        record.pop("vm_id")
                        record["message"] = scrub(record["message"])[:4096]
                    except (ValueError, KeyError, TypeError, OverflowError):
                        continue
                    # Do not advance the checkpoint on network failure; systemd retries from it.
                    send([record])
                    temporary = path.with_suffix(".next")
                    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
                    with os.fdopen(fd, "w") as file:
                        file.write(record["native_id"])
                    os.replace(temporary, path)
    finally:
        selector.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        process.stdout.close()


if __name__ == "__main__":
    main()
