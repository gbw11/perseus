"""Spring 등록 POST 1회. DNS/HTTP는 호출 소유 자식에서 수행하고 종료 때 회수한다.

송신 slot은 worker 프로세스 사이에도 공유한다. 자식의 stdin에만 인증값을 넘기며
stdout은 제한된 결과뿐이다. redirect/retry/polling과 요청 body 로그는 없다.
"""
from __future__ import annotations

import fcntl
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import signal
import ssl
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

MAX_CHILDREN = 4
MAX_RESPONSE = 4096


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("non-finite JSON")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def configuration(settings):
    endpoint = urlsplit(settings.registration_url)
    try:
        local = ipaddress.ip_address(endpoint.hostname).is_loopback
    except ValueError:
        local = False
    if (not endpoint.hostname or endpoint.username is not None or endpoint.password is not None
            or endpoint.query or endpoint.fragment or endpoint.path != "/api/v1/vms/registrations"
            or endpoint.scheme != "https" and not (endpoint.scheme == "http" and local)
            or endpoint.port is not None and not 1 <= endpoint.port <= 65535):
        raise ValueError("registration endpoint requires HTTPS (numeric loopback HTTP for isolated use)")
    token = settings.registration_token
    if not isinstance(token, str) or not 1 <= len(token) <= 2048 or any(not 33 <= ord(c) <= 126 for c in token):
        raise ValueError("registration token is missing or invalid")
    timeout = settings.registration_timeout_s
    if isinstance(timeout, str):
        timeout = float(timeout)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 15:
        raise ValueError("registration timeout must be finite and at most 15 seconds")
    return timeout


def result(state, **fields):
    return {"state": state, "confirmed": state == "confirmed", **fields}


def send_once(value):
    """전용 자식의 단일 송신. HTTP 응답 코드와 대상 confirmed 본문을 함께 검증한다."""
    endpoint = urlsplit(value["url"])
    connection = None
    phase = "connect"
    try:
        cls = http.client.HTTPSConnection if endpoint.scheme == "https" else http.client.HTTPConnection
        connection = cls(endpoint.hostname, endpoint.port, timeout=value["timeout"])
        connection.connect()
        phase = "request"
        connection.request("POST", endpoint.path, json.dumps(value["body"], separators=(",", ":")).encode(),
                           {"Content-Type": "application/json", "Authorization": "Bearer " + value["token"]})
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE + 1)
        status = response.status
        if len(raw) > MAX_RESPONSE:
            return result("response-invalid", http_status=status)
        if 300 <= status < 400:
            return result("redirect-refused", http_status=status)
        try:
            body = decode(raw)
        except (ValueError, UnicodeError):
            body = None
        if status >= 400:
            # 원 응답에서 code만 허용한다. detail/stack/raw body는 되돌리지 않는다.
            code = body.get("code") if isinstance(body, dict) else None
            safe = isinstance(code, str) and 0 < len(code) <= 80 and all(c.isascii() and (c.isalnum() or c == "_") for c in code)
            return result("rejected", http_status=status, **({"upstream_code": code} if safe else {}))
        expected = {"vm_id": value["body"]["data"]["registration"]["vm_id"], "registration": "confirmed"}
        if status in (200, 201) and body == expected:
            return result("confirmed", http_status=status)
        return result("response-invalid" if body is None else "response-unconfirmed", http_status=status)
    except ssl.SSLCertVerificationError:
        return result("peer-verification-failed")
    except (OSError, ValueError, http.client.HTTPException) as error:
        return result("connection-unavailable" if phase == "connect" else "delivery-unknown", cause_type=type(error).__name__)
    finally:
        if connection is not None:
            connection.close()


class RegistrationSender:
    """최대 4개 활성 송신. 포화는 미송신 거절이며 앱 종료는 소유 자식만 kill/wait한다."""
    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._children = set()
        self._closed = False

    def _slot(self):
        directory = Path(self.settings.registration_slot_dir)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("invalid sender slot directory")
        for index in range(MAX_CHILDREN):
            fd = os.open(directory / str(index), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
                os.close(fd)
                raise ValueError("invalid sender slot")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                os.close(fd)
        return None

    def send(self, body, deadline):
        try:
            limit = configuration(self.settings)
            fd = self._slot()
        except (OSError, ValueError, TypeError):
            return result("not-configured" if not self.settings.registration_url or not self.settings.registration_token else "configuration-invalid")
        if fd is None:
            return result("capacity-rejected")
        child = None
        try:
            end = min(deadline, time.monotonic() + limit)
            remaining = end - time.monotonic()
            if remaining <= 0:
                return result("budget-exhausted")
            wire = json.dumps({"url": self.settings.registration_url, "token": self.settings.registration_token,
                               "timeout": remaining, "body": body}, separators=(",", ":")).encode()
            if len(wire) > 16384:
                return result("payload-invalid")
            with self._lock:
                if self._closed:
                    return result("sender-closed")
                child = subprocess.Popen([sys.executable, "-m", "dom0_manager.registration_sender", str(os.getpid())],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         close_fds=True, pass_fds=(fd,))
                self._children.add(child)
            try:
                raw, _ = child.communicate(wire, timeout=max(.001, end - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()
                return result("delivery-unknown", cause_type="CallDeadline")
            if child.returncode or len(raw) > MAX_RESPONSE:
                return result("delivery-unknown", cause_type="SenderExit")
            return decode(raw)
        except (OSError, ValueError, subprocess.SubprocessError):
            return result("delivery-unknown" if child is not None else "launch-unavailable")
        finally:
            if child is not None:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
                with self._lock:
                    self._children.discard(child)
            os.close(fd)

    def close(self):
        with self._lock:
            self._closed = True
            children = list(self._children)
            for child in children:
                if child.poll() is None:
                    child.kill()
        for child in children:
            child.wait()


def main():
    # Popen 뒤 부모가 먼저 사라진 경우도 확인한다. fork 전 Python 콜백은 사용하지 않는다.
    import ctypes
    parent = int(sys.argv[1])
    if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
        return 1
    raw = sys.stdin.buffer.read(16385)
    if len(raw) > 16384:
        return 2
    sys.stdout.write(json.dumps(send_once(decode(raw))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
