"""MQTT-04 데모 명령 수신과 MQTT-05 결과 발행 (계약 3.2절).

- 자기 VM 의 `xenova/v1/vms/{vm_id}/command` 만 구독한다. 봉투 검사(토픽과 vm_id 일치)는 공용 클라이언트가 하고, 여기서는 type 이
  `agent.command` 인 것만 처리한다
- paho 네트워크 스레드에서는 큐에 넣기만 하고, 검사·실행은 이 모듈의 스레드에서 한다. heartbeat 스레드와 나눠 명령 처리가 생존
  신호를 밀지 않게 한다
- 같은 command_id 는 다시 실행하지 않고 마지막 결과를 다시 보낸다. 요청 내용(action·parameters·expected_boot_id·operation_id)이
  다르면 IDEMPOTENCY_CONFLICT 로 거절한다. 기록은 RuntimeDirectory(/run, 부팅마다 비워짐)에 두어 에이전트가 같은 부팅 안에서 다시
  시작해도 재전달된 명령을 반복 실행하지 않는다. 다른 부팅의 명령은 expected_boot_id 로 거절된다
- 실행 전에 기록부터 남기고, 남기지 못하면 실행하지 않는다(STATE_UNAVAILABLE). 기록 파일을 읽지 못하거나 손상됐으면 중복을 가를 수
  없으므로 모든 명령을 STATE_UNAVAILABLE 로 거절한다
- 데모 명령 설정(XENOVA_AGENT_DEMO_COMMANDS)이 꺼져 있으면 모든 명령을 DEMO_DISABLED 로 거절한다
- agent_exit 의 종결 결과는 ACCEPTED 하나다. 그 발행의 PUBACK 을 확인한 뒤에만 종료하며, 확인될 때까지 다른 결과를 내지 않고
  기다린다. QoS 1 발행은 시간 초과로 취소되지 않아 늦게라도 전달될 수 있으므로 실패 결과를 따로 내면 서로 모순된다.
  진행 단계(exit_pending → exit_acked)는 공개 결과와 따로 기록에 남긴다. PUBACK 전에 에이전트가 다시 시작하면 exit_pending 을 읽어
  같은 ACCEPTED 를 다시 제출하고 종료한다. exit_acked 뒤 수동으로 다시 시작된 에이전트는 종료하지 않는다
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xenova_mqtt import topics

from .stress import StressRun, mem_available_mib

log = logging.getLogger("xenova_agent.commands")

COMMAND_TYPE = "agent.command"
RESULT_TYPE = "agent.command_result"
ACTIONS = ("stress", "agent_exit")
# agent_exit 의 종료 코드. 유닛의 RestartPreventExitStatus 와 같아야 systemd 가 다시 띄우지 않고 heartbeat 가 끊긴 채로 남는다
EXIT_CODE = 3
# 계약 3.2: 명령 유효 기간 상한, 보낸 시각이 받는 쪽 시계보다 앞서도 되는 범위
MAX_VALIDITY_S = 300
CLOCK_SKEW_S = 30
# stress 파라미터 범위. vCPU 상한은 플랫폼 자원 변경 범위(1..4)와 같다
MAX_CPU_WORKERS = 4
MAX_MEMORY_MIB = 512
MAX_DURATION_S = 120
# 메모리 부하를 잡은 뒤에도 남겨 둘 여유. 에이전트·sshd 가 OOM 에 걸리지 않게 한다
MEMORY_RESERVE_MIB = 64
HISTORY_LIMIT = 256
QUEUE_MAX = 32
POLL_S = 0.2
STATE_FILE = "commands.json"


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    """ISO-8601 UTC(`Z` 또는 오프셋). 시간대가 없거나 형식이 다르면 None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def fingerprint(payload: dict[str, Any]) -> str:
    """같은 command_id 의 재전달인지 가르는 요청 내용. 만료 시각은 재전송 때 바뀔 수 있어 넣지 않는다."""
    request = {name: payload.get(name) for name in ("action", "parameters", "expected_boot_id", "operation_id")}
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"), default=repr).encode("utf-8")).hexdigest()


def _strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class Rejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


RESULT_FIELDS = ("command_id", "operation_id", "status", "started_at", "finished_at", "duration_ms", "result", "error")
# agent_exit 의 내부 진행 단계. 공개 결과(ACCEPTED)와 따로 남겨 PUBACK 전 재시작에도 종료를 이어 간다
EXIT_PENDING = "exit_pending"  # ACCEPTED 전달·종료가 아직 끝나지 않음. 에이전트가 시작하면 이어서 진행한다
EXIT_ACKED = "exit_acked"  # PUBACK 확인 뒤 종료함. 수동으로 다시 시작됐으면 다시 종료하지 않는다
_HEX64 = re.compile(r"[0-9a-f]{64}")


def _is_time(value: Any) -> bool:
    return parse_time(value) is not None


def _is_stress_result(value: Any) -> bool:
    """명령 검사(_stress_parameters)를 통과한 값만 기록에 남으므로 같은 범위를 요구한다."""
    if not (isinstance(value, dict) and set(value) == {"cpu_workers", "memory_mib", "duration_s"}
            and all(_strict_int(v) for v in value.values())):
        return False
    cpu, memory, duration = value["cpu_workers"], value["memory_mib"], value["duration_s"]
    return (0 <= cpu <= MAX_CPU_WORKERS and 0 <= memory <= MAX_MEMORY_MIB and 1 <= duration <= MAX_DURATION_S
            and (cpu > 0 or memory > 0))


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"code", "message"} and all(isinstance(v, str) for v in value.values())


def valid_entry(entry: Any) -> bool:
    """기록 파일의 한 항목이 이 모듈이 쓴 모양 그대로인지. 어긋나면 기록 전체를 믿지 않는다."""
    if not isinstance(entry, dict) or set(entry) != {"fingerprint", "result", "phase"}:
        return False
    fingerprint_value, result, phase = entry["fingerprint"], entry["result"], entry["phase"]
    if not (isinstance(fingerprint_value, str) and _HEX64.fullmatch(fingerprint_value)):
        return False
    if not isinstance(result, dict) or tuple(sorted(result)) != tuple(sorted(RESULT_FIELDS)):
        return False
    command_id, status = result["command_id"], result["status"]
    if not (isinstance(command_id, str) and command_id.strip() and len(command_id) <= 128):
        return False
    if result["operation_id"] is not None and not isinstance(result["operation_id"], str):
        return False
    started, finished, duration = result["started_at"], result["finished_at"], result["duration_ms"]
    duration_ok = _strict_int(duration) and duration >= 0
    if status == "ACCEPTED":
        return (phase in (EXIT_PENDING, EXIT_ACKED) and _is_time(started) and finished is None and duration is None
                and result["result"] is None and result["error"] is None)
    if phase is not None:
        return False
    if status == "REJECTED":
        return (started is None and _is_time(finished) and duration is None and result["result"] is None
                and _is_error(result["error"]))
    if status == "RUNNING":
        return (_is_time(started) and finished is None and duration is None and _is_stress_result(result["result"])
                and result["error"] is None)
    if status == "SUCCEEDED":
        return (_is_time(started) and _is_time(finished) and duration_ok and _is_stress_result(result["result"])
                and result["error"] is None)
    if status == "FAILED":
        # stress 의 실패. 재시작으로 끝난 것(AGENT_STOPPING)은 끝난 시각을 모른다
        return (_is_time(started) and (finished is None or _is_time(finished)) and (duration is None or duration_ok)
                and _is_stress_result(result["result"]) and _is_error(result["error"]))
    return False


class CommandHistory:
    """command_id 별 요청 지문·마지막 결과 payload·agent_exit 진행 단계. 오래된 것부터 HISTORY_LIMIT 개까지 남긴다.

    state_dir 이 None 이면 메모리에만 둔다(시험·개발용). 파일을 읽지 못하거나 손상됐으면 available 이 False 이고 아무것도 쓰지 않는다.
    """

    def __init__(self, state_dir: Path | None) -> None:
        self._path = state_dir / STATE_FILE if state_dir is not None else None
        self._entries: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.available = True
        self._write_failing = False  # 저장 실패가 이어지는 동안 로그를 한 번만 남긴다
        if self._path is None:
            return
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            self.available = False
            log.error("명령 기록을 읽지 못함 %s: %s. 데모 명령을 거절한다", self._path, type(exc).__name__)
            return
        if not self._valid(loaded):
            self.available = False
            log.error("명령 기록이 손상됨 %s. 데모 명령을 거절한다", self._path)
            return
        for entry in loaded:
            result = entry["result"]
            if result["status"] == "RUNNING":
                # 이전 에이전트가 끝나면서 systemd 가 부하 프로세스도 함께 끝냈다
                result = dict(result, status="FAILED", error={
                    "code": "AGENT_STOPPING", "message": "agent restarted while the stress was running"})
            self._entries[result["command_id"]] = dict(entry, result=result)

    @staticmethod
    def _valid(loaded: Any) -> bool:
        if not isinstance(loaded, list) or len(loaded) > HISTORY_LIMIT or not all(valid_entry(entry) for entry in loaded):
            return False
        ids = [entry["result"]["command_id"] for entry in loaded]
        pending = [entry for entry in loaded if entry["phase"] == EXIT_PENDING]
        return len(ids) == len(set(ids)) and len(pending) <= 1

    def get(self, command_id: str) -> dict[str, Any] | None:
        return self._entries.get(command_id)

    def pending_exit(self) -> dict[str, Any] | None:
        """종료를 끝내지 못한 agent_exit 항목. 에이전트가 시작할 때 이어서 진행한다."""
        return next((entry for entry in self._entries.values() if entry["phase"] == EXIT_PENDING), None)

    def put(self, fingerprint_value: str, result: dict[str, Any], *, durable: bool, phase: str | None = None) -> bool:
        """기록을 남기고 파일에 썼는지 돌려준다. durable 이면 파일에 못 쓸 때 메모리 기록도 바꾸지 않는다(실행하지 않을 명령)."""
        candidate = OrderedDict(self._entries)
        candidate[result["command_id"]] = {"fingerprint": fingerprint_value, "result": result, "phase": phase}
        candidate.move_to_end(result["command_id"])
        while len(candidate) > HISTORY_LIMIT:
            candidate.popitem(last=False)
        written = self._write(candidate)
        if written or not durable:
            self._entries = candidate
        return written

    def _write(self, entries: OrderedDict[str, dict[str, Any]]) -> bool:
        if self._path is None:
            return True
        if not self.available:
            return False  # 손상된 기록을 덮어쓰면 남아 있던 중복 근거를 잃는다
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(list(entries.values()), separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            if not self._write_failing:
                log.error("명령 기록을 저장하지 못함 %s: %s", self._path, type(exc).__name__)
                self._write_failing = True
            return False
        if self._write_failing:
            log.info("명령 기록 저장이 다시 됨 %s", self._path)
            self._write_failing = False
        return True


class CommandHandler:
    def __init__(
        self,
        client: Any,
        vm_id: str,
        *,
        boot_id: str,
        enabled: bool,
        history: CommandHistory,
        on_exit: Callable[[], None],
        stress_factory: Callable[[int, int, int], Any] = StressRun,
        memory_available: Callable[[], int | None] = mem_available_mib,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._vm_id = vm_id
        self._topic = topics.command(vm_id)
        self._result_topic = topics.command_result(vm_id)
        self._boot_id = boot_id
        self.enabled = enabled
        self._history = history
        self._on_exit = on_exit
        self._stress_factory = stress_factory
        self._memory_available = memory_available
        self._now = now
        self._clock = clock
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=QUEUE_MAX)
        self._active: tuple[str, dict[str, Any], Any, float] | None = None  # (지문, RUNNING 결과, StressRun, 시작 단조 시각)
        # (지문, ACCEPTED 결과, 제출한 발행의 MQTTMessageInfo 또는 None)
        self._pending_exit: tuple[str, dict[str, Any], Any] | None = None
        self.exit_requested = False
        self.dropped = 0
        pending = history.pending_exit() if history.available else None
        if pending is not None:
            # 이전 에이전트가 ACCEPTED 의 PUBACK 을 보기 전에 끝났다. 같은 결과를 다시 제출하고 확인되면 종료한다
            log.warning("agent_exit %s: 끝나지 않은 종료를 이어서 진행한다", pending["result"]["command_id"])
            self._pending_exit = (pending["fingerprint"], pending["result"], None)

    @property
    def capabilities(self) -> list[str]:
        """inventory capabilities.commands. 꺼져 있으면 빈 목록."""
        return list(ACTIONS) if self.enabled else []

    def subscribe(self) -> None:
        self._client.subscribe(self._topic, self._enqueue, qos=1)

    def _enqueue(self, message: Any) -> None:
        try:
            self._queue.put_nowait(message)
        except queue.Full:
            # 명령은 만료 시각이 있어 보낸 쪽이 결과 없음으로 판단한다. 수신 스레드를 막지 않는다
            self.dropped += 1
            log.warning("명령 버림: 처리 대기열이 가득 참 (%d)", self.dropped)

    def start(self, stop: threading.Event) -> threading.Thread:
        thread = threading.Thread(target=self.run, args=(stop,), name="xenova-agent-commands", daemon=True)
        thread.start()
        return thread

    def run(self, stop: threading.Event) -> None:
        try:
            while not stop.is_set() and not self.exit_requested:
                try:
                    message = self._queue.get(timeout=POLL_S)
                except queue.Empty:
                    message = None
                if message is not None:
                    try:
                        self.handle(message)
                    except Exception:  # 한 명령의 오류로 스레드가 멈추면 이후 명령과 부하 정리가 멈춘다
                        log.exception("명령 처리 오류")
                self.poll()
        finally:
            self._finish_active("FAILED", {"code": "AGENT_STOPPING", "message": "agent stopped before the stress ended"})

    # --- 처리 ---------------------------------------------------------------

    def handle(self, message: Any) -> None:
        if message.type != COMMAND_TYPE:
            log.warning("버림 %s: command 토픽의 %s", message.topic, message.type)
            return
        payload = message.payload
        command_id = payload.get("command_id")
        if not isinstance(command_id, str) or not command_id.strip() or len(command_id) > 128:
            # 결과를 이어 붙일 키가 없어 보고할 수 없다
            log.warning("명령 버림: command_id 없음 %r", command_id)
            return
        operation_id = payload.get("operation_id")
        result = self._result(command_id, operation_id if isinstance(operation_id, str) else None)
        if not self._history.available:
            self._reject(result, Rejected("STATE_UNAVAILABLE", "command history is unreadable; cannot detect duplicates"))
            return
        request = fingerprint(payload)
        previous = self._history.get(command_id)
        if previous is not None:
            if previous["fingerprint"] != request:
                self._reject(result, Rejected("IDEMPOTENCY_CONFLICT", "command_id was already used for a different request"))
                return
            log.info("명령 재전달 %s: 다시 실행하지 않고 마지막 결과(%s)를 다시 보냄", command_id, previous["result"]["status"])
            self._publish(previous["result"])
            return
        try:
            if operation_id is not None and not isinstance(operation_id, str):
                raise Rejected("INVALID_COMMAND", "operation_id must be a string")
            action, parameters = self._check(message)
        except Rejected as rejected:
            self._reject(result, rejected, request)
            return
        if action == "stress":
            self._start_stress(request, result, parameters)
        else:
            self._exit(request, result)

    def _result(self, command_id: str, operation_id: str | None) -> dict[str, Any]:
        return {
            "command_id": command_id,
            "operation_id": operation_id,
            "status": None,
            "started_at": None,
            "finished_at": None,
            "duration_ms": None,
            "result": None,
            "error": None,
        }

    def _reject(self, result: dict[str, Any], rejected: Rejected, request: str | None = None) -> None:
        """REJECTED 를 보낸다. request 를 주면 기록해 같은 요청의 재전달에 같은 거절을 돌려준다(실행이 없어 기록 실패는 무해하다)."""
        log.info("명령 거절 %s: %s", result["command_id"], rejected)
        rejection = dict(result, status="REJECTED", finished_at=utc_iso(self._now()),
                         error={"code": rejected.code, "message": rejected.message})
        if request is not None:
            self._history.put(request, rejection, durable=False)
        self._publish(rejection)

    def _check(self, message: Any) -> tuple[str, dict[str, Any]]:
        payload = message.payload
        if message.retained:
            raise Rejected("INVALID_COMMAND", "retained command")
        action = payload.get("action")
        if action not in ACTIONS:
            raise Rejected("UNSUPPORTED_ACTION", repr(action))
        if not self.enabled:
            raise Rejected("DEMO_DISABLED", "demo commands are disabled on this agent")
        if self._pending_exit is not None:
            raise Rejected("AGENT_EXITING", "agent_exit was accepted; the agent is exiting")
        if payload.get("expected_boot_id") != self._boot_id:
            raise Rejected("BOOT_MISMATCH", "expected_boot_id does not match this boot")
        sent_at = parse_time(message.envelope.get("sent_at"))
        expires_at = parse_time(payload.get("expires_at"))
        if sent_at is None or expires_at is None:
            raise Rejected("INVALID_COMMAND", "sent_at and expires_at must be ISO-8601 UTC")
        validity = (expires_at - sent_at).total_seconds()
        if not 0 < validity <= MAX_VALIDITY_S:
            raise Rejected("INVALID_COMMAND", f"expires_at must be within {MAX_VALIDITY_S}s after sent_at")
        now = self._now()
        if (sent_at - now).total_seconds() > CLOCK_SKEW_S:
            raise Rejected("CLOCK_UNTRUSTED", f"sent_at is more than {CLOCK_SKEW_S}s ahead of the agent clock")
        if now >= expires_at:
            raise Rejected("EXPIRED", "expires_at has passed")
        parameters = payload.get("parameters", {})
        if not isinstance(parameters, dict):
            raise Rejected("INVALID_PARAMETERS", "parameters must be an object")
        if action == "stress":
            return action, self._stress_parameters(parameters)
        if parameters:
            raise Rejected("INVALID_PARAMETERS", "agent_exit takes no parameters")
        return action, {}

    def _stress_parameters(self, parameters: dict[str, Any]) -> dict[str, Any]:
        unknown = set(parameters) - {"cpu_workers", "memory_mib", "duration_s"}
        if unknown:
            raise Rejected("INVALID_PARAMETERS", f"unknown parameters {sorted(unknown)}")
        cpu = parameters.get("cpu_workers", 0)
        memory = parameters.get("memory_mib", 0)
        duration = parameters.get("duration_s")
        if not (_strict_int(cpu) and 0 <= cpu <= MAX_CPU_WORKERS):
            raise Rejected("INVALID_PARAMETERS", f"cpu_workers must be an integer 0..{MAX_CPU_WORKERS}")
        if not (_strict_int(memory) and 0 <= memory <= MAX_MEMORY_MIB):
            raise Rejected("INVALID_PARAMETERS", f"memory_mib must be an integer 0..{MAX_MEMORY_MIB}")
        if not (_strict_int(duration) and 1 <= duration <= MAX_DURATION_S):
            raise Rejected("INVALID_PARAMETERS", f"duration_s must be an integer 1..{MAX_DURATION_S}")
        if cpu == 0 and memory == 0:
            raise Rejected("INVALID_PARAMETERS", "cpu_workers or memory_mib must be positive")
        if self._active is not None:
            raise Rejected("BUSY", f"stress {self._active[1]['command_id']} is running")
        if memory:
            available = self._memory_available()
            if available is None or memory > available - MEMORY_RESERVE_MIB:
                raise Rejected("RESOURCE_UNAVAILABLE", f"memory_mib {memory} exceeds available memory minus {MEMORY_RESERVE_MIB} MiB")
        return {"cpu_workers": cpu, "memory_mib": memory, "duration_s": duration}

    def _start_stress(self, request: str, result: dict[str, Any], parameters: dict[str, Any]) -> None:
        running = dict(result, status="RUNNING", started_at=utc_iso(self._now()), result=parameters)
        # 실행 전에 남겨 재전달·재시작으로 다시 실행하지 않는다. 남기지 못하면 실행하지 않는다
        if not self._history.put(request, running, durable=True):
            self._reject(result, Rejected("STATE_UNAVAILABLE", "could not record the command before running it"))
            return
        try:
            run = self._stress_factory(parameters["cpu_workers"], parameters["memory_mib"], parameters["duration_s"])
        except Exception as exc:  # noqa: BLE001 - 프로세스를 띄우지 못한 이유를 결과로 보고한다
            self._commit(request, self._finished(running, self._clock(), "FAILED", {"code": "STRESS_FAILED", "message": type(exc).__name__}))
            return
        self._active = (request, running, run, self._clock())
        log.info("stress 시작 %s: %s", result["command_id"], parameters)
        self._publish(running)

    def poll(self) -> None:
        """도는 stress 가 끝났으면 결과를 내고, 종료를 기다리는 agent_exit 의 결과 전달을 확인한다."""
        if self._active is not None:
            state = self._active[2].poll()
            if state == "succeeded":
                self._finish_active("SUCCEEDED", None)
            elif state == "failed":
                self._finish_active("FAILED", {"code": "STRESS_FAILED", "message": "a stress process exited abnormally"})
        if self._pending_exit is not None:
            self._advance_exit()

    def _finish_active(self, status: str, error: dict[str, str] | None) -> None:
        if self._active is None:
            return
        request, running, run, started = self._active
        self._active = None
        run.stop()
        log.info("stress 끝 %s: %s", running["command_id"], status)
        self._commit(request, self._finished(running, started, status, error))

    def _finished(self, result: dict[str, Any], started: float, status: str, error: dict[str, str] | None) -> dict[str, Any]:
        return dict(
            result, status=status, finished_at=utc_iso(self._now()),
            duration_ms=max(0, round((self._clock() - started) * 1000)), error=error,
        )

    def _exit(self, request: str, result: dict[str, Any]) -> None:
        accepted = dict(result, status="ACCEPTED", started_at=utc_iso(self._now()))
        if not self._history.put(request, accepted, durable=True, phase=EXIT_PENDING):
            self._reject(result, Rejected("STATE_UNAVAILABLE", "could not record the command before running it"))
            return
        self._finish_active("FAILED", {"code": "AGENT_EXITING", "message": "agent_exit stopped the stress"})
        self._pending_exit = (request, accepted, None)
        self._advance_exit()

    def _advance_exit(self) -> None:
        """ACCEPTED 를 아직 제출하지 못했으면(연결 없음·재시작) 제출하고, 제출한 발행의 PUBACK 이 확인되면 exit_acked 를 남기고
        종료를 요청한다. exit_acked 를 남기지 못하면 종료하지 않고 다음 poll 에서 다시 저장한다(남지 않은 채 종료하면 수동 재시작마다
        다시 종료한다)."""
        request, accepted, info = self._pending_exit  # type: ignore[misc]
        if info is None:
            if not self._client.connected:
                return
            try:
                info = self._client.publish_tracked(self._result_topic, RESULT_TYPE, accepted).info
            except Exception as exc:  # noqa: BLE001 - 제출 전에 끊겼다. 연결되면 다시 제출한다
                log.warning("agent_exit %s: 결과 제출 실패, 연결되면 다시 시도 (%s)", accepted["command_id"], exc)
                return
            self._pending_exit = (request, accepted, info)
            log.info("agent_exit %s: ACCEPTED 제출, PUBACK 을 기다린다", accepted["command_id"])
        try:
            delivered = info.is_published()
        except (ValueError, RuntimeError):
            delivered = False
        if not delivered:
            return
        if not self._history.put(request, accepted, durable=True, phase=EXIT_ACKED):
            return
        log.warning("agent_exit %s: 결과 전달 확인, 에이전트를 종료한다 (종료 코드 %d)", accepted["command_id"], EXIT_CODE)
        self._pending_exit = None
        self.exit_requested = True
        self._on_exit()

    def _commit(self, request: str, result: dict[str, Any]) -> None:
        self._history.put(request, result, durable=False)
        self._publish(result)

    def _publish(self, result: dict[str, Any]) -> None:
        try:
            self._client.publish(self._result_topic, RESULT_TYPE, result)
        except Exception as exc:  # noqa: BLE001 - 결과 발행 실패로 명령 스레드가 멈추지 않는다
            log.warning("명령 결과 발행 실패 %s: %s", result["command_id"], exc)
