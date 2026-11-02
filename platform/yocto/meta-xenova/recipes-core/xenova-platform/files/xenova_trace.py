"""요청 안에서만 수집하는 제한된 비핵심 진단. 인자·경로·payload는 기록하지 않는다.

시간은 호출자가 주입한 단조 시계의 초 단위이며, 중첩 span의 exclusive 시간은
하위 span을 제외한다. 저장·stdout·작업 권한은 이 모듈의 책임이 아니다.
"""
from _thread import _local
from contextlib import contextmanager
from functools import wraps
import re
import time

_LOCAL = _local()
_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_TEXT_FIELDS = frozenset(("request_id", "operation_id", "recovery_attempt_id", "attempt_id",
    "host_boot_id", "vm_id", "execution_id", "action", "phase", "command_kind", "kind", "outcome",
    "record_sha256", "configuration_sha256", "policy_sha256", "cpu_policy_sha256"))
_NUMBER_FIELDS = frozenset(("count", "size_bytes", "remaining_seconds", "deadline_monotonic_ms",
    "deadline_seconds", "remaining_ms", "policy_revision", "intent_revision", "lifecycle_generation", "domid",
    "full", "target", "required", "blocking", "timeout_seconds"))


def _fields(values):
    result = {}
    for key, value in values.items():
        if key in _TEXT_FIELDS and type(value) is str and _NAME.fullmatch(value):
            result[key] = value
        elif key in _NUMBER_FIELDS and type(value) in (int, float, bool):
            # Do not retain NaN/infinity or arbitrary objects from a caller.
            if type(value) is not float or (value == value and abs(value) != float("inf")):
                result[key] = value
    return result


class Trace:
    """한 동기 관리 요청의 bounded trace. 스레드 간 전파·전역 캐시가 아니다."""
    def __init__(self, clock=time.monotonic, context=None, max_events=512):
        self.clock = clock
        self.context = _fields(context or {})
        self.max_events = min(4096, max(0, int(max_events)))
        self.entries = []
        self.summary = {}
        self.command_summary = {}
        self.dropped = 0
        self.summary_dropped = 0
        self._stack = []
        self._last_time = 0.0
        self._origin = self._now()

    def _now(self):
        try:
            value = float(self.clock())
            if value == value and abs(value) != float("inf"):
                self._last_time = max(value, self._last_time)
        except Exception:
            pass  # Clock diagnostics may fail without changing an operation.
        return self._last_time

    @contextmanager
    def activate(self):
        previous = getattr(_LOCAL, "trace", None)
        _LOCAL.trace = self
        try:
            yield self
        finally:
            _LOCAL.trace = previous

    def bind(self, **values):
        """현재 요청의 공개 식별자만 연결한다. 호출 인자·임의 문자열은 버린다."""
        self.context.update(_fields(values))

    def _append(self, row):
        if len(self.entries) < self.max_events:
            self.entries.append(row)
        else:
            self.dropped += 1

    def event(self, name, **values):
        if type(name) is str and _NAME.fullmatch(name):
            if len(self.entries)>=self.max_events:
                self.dropped+=1
                return
            self._append({"kind": "event", "name": name,
                          "at_seconds": max(0.0, self._now()-self._origin),
                          "context": dict(self.context), "fields": _fields(values)})

    @contextmanager
    def span(self, name, **values):
        if type(name) is not str or not _NAME.fullmatch(name) or len(self._stack) >= 64:
            self.dropped += 1
            yield
            return
        frame = {"start": self._now(), "child": 0.0}
        safe = _fields(values)
        self._stack.append(frame)
        outcome = "complete"
        try:
            yield
        except BaseException:
            outcome = "error"
            raise
        finally:
            elapsed = max(0.0, self._now()-frame["start"])
            exclusive = max(0.0, elapsed-frame["child"])
            self._stack.pop()
            if self._stack:
                self._stack[-1]["child"] += elapsed
            if name in self.summary or len(self.summary) < 128:
                aggregate = self.summary.setdefault(name, {"calls": 0, "errors": 0,
                    "inclusive_seconds": 0.0, "exclusive_seconds": 0.0})
                aggregate["calls"] += 1
                aggregate["errors"] += int(outcome == "error")
                aggregate["inclusive_seconds"] += elapsed
                aggregate["exclusive_seconds"] += exclusive
            else:
                self.summary_dropped += 1
            command = safe.get('command_kind') if name == 'external.command' else None
            if command and (command in self.command_summary or len(self.command_summary) < 64):
                total = self.command_summary.setdefault(command, {'calls':0, 'errors':0,
                    'inclusive_seconds':0.0})
                total['calls'] += 1
                total['errors'] += int(outcome == 'error')
                total['inclusive_seconds'] += elapsed
            if len(self.entries)>=self.max_events:self.dropped+=1
            else:
                self._append({"kind": "span", "name": name, "outcome": outcome,
                    "start_seconds": max(0.0, frame["start"]-self._origin),
                    "inclusive_seconds": elapsed, "exclusive_seconds": exclusive,
                    "context": dict(self.context), "fields": safe})

    def export(self):
        """복사본만 반환한다. 중첩 inclusive 합을 전체 소요시간으로 해석하지 않는다."""
        return {"schema_version": "1.0", "clock": "monotonic_seconds",
            "context": dict(self.context), "elapsed_seconds": max(0.0, self._now()-self._origin),
            "entries": [{**row, "context": dict(row["context"]), "fields": dict(row["fields"])}
                        for row in self.entries],
            "summary": {name: dict(value) for name, value in self.summary.items()},
            "command_summary": {name:dict(value) for name,value in self.command_summary.items()},
            "dropped_entries": self.dropped, "dropped_summary_spans": self.summary_dropped}


def current():
    return getattr(_LOCAL, "trace", None)


def bind(**values):
    """진단 식별자 연결 실패는 호출자의 수락·저장·실행 결과를 바꾸지 않는다."""
    trace = current()
    if trace is not None:
        try:
            trace.bind(**values)
        except Exception:
            pass


def event(name, **values):
    trace = current()
    if trace is not None:
        try:
            trace.event(name, **values)
        except Exception:
            pass


@contextmanager
def span(name, **values):
    trace = current()
    if trace is None:
        yield
        return
    # A broken optional collector must not prevent executing the wrapped work.
    try:
        manager = trace.span(name, **values)
        manager.__enter__()
    except Exception:
        yield
        return
    try:
        yield
    except BaseException as exc:
        try:
            manager.__exit__(type(exc), exc, exc.__traceback__)
        except BaseException:
            pass
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception:
            pass


def traced(name):
    """기존 동기 함수의 반환값·예외·서명을 유지하며 span만 덧붙인다."""
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with span(name):
                return function(*args, **kwargs)
        return call
    return decorate
