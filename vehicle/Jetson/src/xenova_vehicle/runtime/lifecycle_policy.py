"""향후 S/worker adapter가 쓰는 순수 준비·원 기한 정책. 장치/시계/I/O는 소유하지 않는다.

시각은 호출자가 주는 J monotonic u64 us다. [start, expiry)의 half-open 기한을
쓰며 now==expiry는 만료다. 원 event와 전달/판정 now를 구분한다. 내부 fact는
검증된 producer metadata이며 wire schema·SDK/lease 검증·실기 준비 증거가 아니다.
수치는 설계 runtime-lifecycle-policy.json의 timing_ms/warmup subset에 근거한다.
watch/tracker는 단일 정책 owner가 소유하며 속성을 외부에서 수정하지 않는다.
타입/시각/미지원 전이는 ValueError, 중복·비현문맥 사실은 False로 거절한다.
poll은 원 만료를 sticky 실패로 기록하며 이미 수락한 초기 준비의 지속 freshness는
별도 adapter가 검사한다. 실제 lifecycle/출력 권위의 실행기는 이 모듈에 없다.
"""

from dataclasses import dataclass
import re
from types import MappingProxyType
from uuid import UUID


U64_MAX = 2**64 - 1
TIMING_US = MappingProxyType({"startup_total": 90_000_000, "asset_check": 15_000_000,
             "device_boot": 30_000_000, "pipeline_start": 30_000_000,
             "warmup": 15_000_000, "mapper_local_setup": 15_000_000,
             "storage_local_setup": 15_000_000, "binding_ack": 5_000_000,
             "active_worker_progress_gap": 500_000})
CAPTURE_PAIR_COUNT = 30
REQUIRED_NN_PAIR_COUNT = 5
ROLE_STATES = MappingProxyType({
    "S": frozenset("DOWN STARTING RUNNING DEGRADED QUIESCING RECOVERING FAILED FAILED_NEEDS_SERVICE STOPPED".split()),
    "A": frozenset("STARTING ASSET_CHECK DEVICE_BOOT PIPELINE_START WARMUP STREAMING DEGRADED DRAINING CLOSING CLOSED FAILED FAILED_CONFIG FAILED_NEEDS_SERVICE".split()),
    "M": frozenset("STARTING WAIT_INPUT WAIT_CLOCK WAIT_ODOM WAIT_STORAGE WARMUP RUNNING DEGRADED QUIESCING RECOVERING FAILED STOPPED".split()),
    "D": frozenset("STARTING IDLE ACTIVE_JOB RECOVERING QUIESCING FAILED STOPPED".split()),
})


def _u64(value, label):
    if type(value) is not int or not 0 <= value <= U64_MAX:
        raise ValueError(label + " must be u64 integer us/counter")
    return value


def _event(event_us, now_us):
    _u64(event_us, "event_us")
    _u64(now_us, "now_us")
    if event_us > now_us:
        raise ValueError("future event stamp")


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("identity must be a canonical lowercase UUID")


def _sha(value):
    if type(value) is not str or re.fullmatch("[0-9a-f]{64}", value) is None:
        raise ValueError("binding must be a lowercase SHA256")


def _label(value):
    if type(value) is not str or not 1 <= len(value) <= 128 or any(ord(c) < 32 for c in value):
        raise ValueError("invalid immutable model/parser identifier")


def validate_state(role: str, state: str) -> None:
    """role별 정확한 allowlist만 검사한다. S/M/D의 미정 전이 graph는 발명하지 않는다."""
    if type(role) is not str or type(state) is not str or state not in ROLE_STATES.get(role, ()):
        raise ValueError("unknown role/state combination")


@dataclass(frozen=True)
class AbsoluteDeadline:
    """원 event에서 한 번 계산한 기한. 반복 poll 자체는 새 기한을 만들지 않는다."""

    started_us: int
    expires_us: int

    def __post_init__(self):
        _u64(self.started_us, "started_us")
        _u64(self.expires_us, "expires_us")

    @classmethod
    def after(cls, event_us: int, duration_us: int, *, now_us: int, cap_us: int | None = None):
        _event(event_us, now_us)
        _u64(duration_us, "duration_us")
        # Python 정수에서 먼저 계산한 뒤 effective min만 u64에 넣는다. 더 짧은
        # 원 cap이 있으면 표현 가능한 기한을 중간 합 overflow로 거절하지 않는다.
        expiry = event_us + duration_us
        if cap_us is not None:
            expiry = min(expiry, _u64(cap_us, "cap_us"))
        return cls(event_us, _u64(expiry, "effective deadline expiry"))

    def expired(self, now_us: int) -> bool:
        _event(self.started_us, now_us)
        return now_us >= self.expires_us


class LocalSetupWatch:
    """M/D spawn→HELLO의 원15초. HELLO 성공 뒤 외부 WAIT에는 적용하지 않는다."""

    def __init__(self, role, worker_instance, spawn_us, *, now_us):
        if role not in ("M", "D"):
            raise ValueError("local setup is M/D only")
        _uuid(worker_instance)
        self.role, self.worker_instance = role, worker_instance
        self.deadline = AbsoluteDeadline.after(spawn_us, TIMING_US["mapper_local_setup" if role == "M" else "storage_local_setup"], now_us=now_us)
        self.hello_us = None
        self.failed = False

    def poll(self, now_us):
        _event(self.deadline.started_us, now_us)
        if self.hello_us is None and self.deadline.expired(now_us):
            self.failed = True
        return self.failed

    def hello(self, worker_instance, event_us, *, now_us):
        """현재 worker의 원 HELLO를 기한 안에서 한 번 수락한다. late는 실패이며 재허가하지 않는다."""
        _event(event_us, now_us)
        if worker_instance != self.worker_instance:
            return False
        if self.hello_us is not None:
            return False
        if self.poll(now_us) or event_us < self.deadline.started_us:
            return False
        self.hello_us = event_us
        return True


class BindWatch:
    """원 BIND 요청 한 개의 issue/send/ACK. EAGAIN 동안 sent를 호출하지 않는다.

    ACK의 identity/원 시각은 lower IPC adapter가 검증해야 한다. 이 객체는 원 request
    bytes를 저장/송신하지 않으며 full envelope/lease의 대체물이 아니다.
    """

    def __init__(self, role, worker_instance, binding_sha256, request_id, issued_us, *, now_us, cap_us=None):
        if role not in ("A", "M", "D"):
            raise ValueError("BIND requires a worker role")
        _uuid(worker_instance)
        _sha(binding_sha256)
        _uuid(request_id)
        self.role, self.worker_instance = role, worker_instance
        self.binding_sha256, self.request_id = binding_sha256, request_id
        self.issued_us = issued_us
        self.deadline = AbsoluteDeadline.after(issued_us, TIMING_US["binding_ack"], now_us=now_us, cap_us=cap_us)
        self.sent_us = self.ack_us = None
        self.failed = False

    def poll(self, now_us):
        _event(self.deadline.started_us, now_us)
        if self.ack_us is None and self.deadline.expired(now_us):
            self.failed = True
        return self.failed

    def sent(self, event_us, *, now_us):
        """실제 첫 송신만 기록한다. EAGAIN/재송신이 원 요청에 새 시간을 주지 않는다."""
        _event(event_us, now_us)
        if self.sent_us is not None:
            return False
        if self.poll(now_us) or event_us < self.deadline.started_us:
            return False
        sent_deadline = AbsoluteDeadline.after(event_us, TIMING_US["binding_ack"], now_us=now_us,
                                               cap_us=self.deadline.expires_us)
        self.deadline = AbsoluteDeadline(self.issued_us, sent_deadline.expires_us)
        self.sent_us = event_us
        return True

    def ack(self, worker_instance, binding_sha256, request_id, event_us, *, now_us):
        """현재 원 요청의 송신 이후 ACK만 한 번 수락한다. foreign는 원 실패 상태도 바꾸지 않는다."""
        _event(event_us, now_us)
        if (worker_instance, binding_sha256, request_id) != (self.worker_instance, self.binding_sha256, self.request_id):
            return False
        if self.ack_us is not None:
            return False
        if self.poll(now_us) or self.sent_us is None or event_us < self.sent_us:
            return False
        self.ack_us = event_us
        return True


def active_progress_expired(role, state, last_completed_us, *, now_us, active_job=False):
    """A STREAMING/활성 M만500ms 검사한다. NN FPS/freshness를 승인하지 않는다."""
    validate_state(role, state)
    if type(active_job) is not bool:
        raise ValueError("active_job must be bool")
    _event(last_completed_us, now_us)
    expected = role == "A" and state == "STREAMING" or role == "M" and active_job
    return expected and now_us - last_completed_us >= TIMING_US["active_worker_progress_gap"]


@dataclass(frozen=True)
class CaptureScope:
    """검증된 RawBinding hash와 stream scope. hash는 pipeline/config/calibration 등을 포함한다.

    lower adapter가14장의 전체 RawBinding canonical hash를 검증한다. 여기서는 해당
    bytes나 SDK/clock/형식을 다시 검사하지 않는다. 일반 관측/clock lease 갱신은 같은 scope다.
    """

    raw_binding_sha256: str
    acquisition_worker: str
    rgb_stream: str
    depth_stream: str
    nn_input_stream: str | None = None
    nn_output_stream: str | None = None

    def __post_init__(self):
        _sha(self.raw_binding_sha256)
        streams = [self.rgb_stream, self.depth_stream]
        if (self.nn_input_stream is None) != (self.nn_output_stream is None):
            raise ValueError("NN stream identities must both be present or absent")
        if self.nn_input_stream is not None:
            streams += [self.nn_input_stream, self.nn_output_stream]
        for value in [self.acquisition_worker] + streams:
            _uuid(value)
        if len(set(streams)) != len(streams):
            raise ValueError("stream UUIDs alias")


@dataclass(frozen=True)
class SessionExitEvidence:
    """S가 실제 session 정리/exit를 확인한 원 사실. A의 CLOSED 문자열은 이 사실이 아니다."""

    scope: CaptureScope
    session_closed_us: int
    process_exited_us: int

    def __post_init__(self):
        if type(self.scope) is not CaptureScope:
            raise ValueError("invalid exit scope")
        _u64(self.session_closed_us, "session_closed_us")
        _u64(self.process_exited_us, "process_exited_us")


class AStageWatch:
    """A의 명시 전이/원 local startup만 감시한다. 실제 자산/graph 증거를 생성하지 않는다."""

    _NEXT = {"STARTING": {"ASSET_CHECK"}, "ASSET_CHECK": {"DEVICE_BOOT"},
             "DEVICE_BOOT": {"PIPELINE_START"}, "PIPELINE_START": {"WARMUP"},
             "WARMUP": {"STREAMING", "DEGRADED"}, "STREAMING": {"DEGRADED", "DRAINING"},
             "DEGRADED": {"STREAMING", "DRAINING"}, "DRAINING": {"CLOSING"},
             "CLOSING": {"CLOSED"}, "FAILED": {"DRAINING"},
             "FAILED_CONFIG": {"DRAINING"}, "FAILED_NEEDS_SERVICE": {"DRAINING"}}
    _CAPS = {"ASSET_CHECK": TIMING_US["asset_check"], "DEVICE_BOOT": TIMING_US["device_boot"],
             "PIPELINE_START": TIMING_US["pipeline_start"], "WARMUP": TIMING_US["warmup"]}

    def __init__(self, scope, attempt_us, *, now_us):
        if type(scope) is not CaptureScope:
            raise ValueError("invalid capture scope")
        self.scope = scope
        self.global_deadline = AbsoluteDeadline.after(attempt_us, TIMING_US["startup_total"], now_us=now_us)
        self.deadline = self.global_deadline
        self.state, self.entered_us = "STARTING", attempt_us
        self.startup_completed = self.failed = False
        self.warmup_deadline = None

    def poll(self, now_us):
        _event(self.entered_us, now_us)
        if self.deadline is not None and self.deadline.expired(now_us):
            self.state, self.failed = "FAILED", True
        return self.failed

    def enter(self, state, event_us, *, now_us, capture=None, exit_evidence=None):
        """근거 있는 A 전이만 검사한다. startup 만료는 sticky이며 cleanup만 뒤따를 수 있다."""
        validate_state("A", state)
        _event(event_us, now_us)
        if state == self.state:
            return False
        cleanup = state in ("DRAINING", "CLOSING", "CLOSED")
        if cleanup and self.deadline is not None and self.deadline.expired(now_us):
            self.failed = True
        if not cleanup and self.poll(now_us):
            return False
        if event_us < self.entered_us:
            return False
        failures = {"FAILED", "FAILED_CONFIG", "FAILED_NEEDS_SERVICE"}
        allowed = self._NEXT.get(self.state, set()) | ({"DRAINING"} if self.state in self._CAPS or self.state == "STARTING" else set())
        if self.state not in ("CLOSED", "FAILED_CONFIG", "FAILED_NEEDS_SERVICE"):
            allowed = allowed | failures
        if state not in allowed:
            raise ValueError("unsupported A state transition")
        if state == "CLOSED":
            if (type(exit_evidence) is not SessionExitEvidence or exit_evidence.scope != self.scope
                    or not self.global_deadline.started_us <= exit_evidence.session_closed_us <= exit_evidence.process_exited_us
                    or max(exit_evidence.session_closed_us, exit_evidence.process_exited_us) > event_us):
                raise ValueError("CLOSED requires current supervisor-confirmed session/exit evidence")
        if state == "STREAMING":
            if (type(capture) is not CaptureWarmup or capture.scope != self.scope
                    or capture.ready_us is None or capture.ready_us > event_us
                    or self.warmup_deadline is None
                    or capture.deadline.started_us != self.warmup_deadline.started_us
                    or capture.deadline.expires_us > self.warmup_deadline.expires_us):
                raise ValueError("STREAMING requires current capture readiness proof")
            if not self.startup_completed and capture.deadline.expired(now_us):
                self.state, self.failed = "FAILED", True
                self.deadline = capture.deadline
                return False
            self.startup_completed = True
        next_deadline = (AbsoluteDeadline.after(event_us, self._CAPS[state], now_us=now_us,
                                                cap_us=self.global_deadline.expires_us)
                         if state in self._CAPS else
                         self.deadline if state == "DEGRADED" and not self.startup_completed else
                         self.global_deadline if not self.startup_completed and not cleanup and state not in failures else None)
        if next_deadline is not None and next_deadline.expired(now_us):
            self.deadline = next_deadline
            self.state, self.failed = "FAILED", True
            return False
        self.state, self.entered_us = state, event_us
        self.failed = self.failed or state in failures
        self.deadline = next_deadline
        if state == "WARMUP":
            self.warmup_deadline = next_deadline
        return True


@dataclass(frozen=True)
class SourceStamp:
    """한 실제 stream의 원 metadata. 다른 clock의 숫자를 서로 비교하지 않는다."""

    stream_instance: str
    sequence: int
    device_us: int
    sdk_us: int
    received_j_us: int

    def __post_init__(self):
        _uuid(self.stream_instance)
        for value in (self.sequence, self.device_us, self.sdk_us, self.received_j_us):
            _u64(value, "source stamp")

    def advances(self, previous):
        return previous is None or (self.stream_instance == previous.stream_instance
            and self.sequence > previous.sequence and self.device_us > previous.device_us
            and self.sdk_us > previous.sdk_us and self.received_j_us >= previous.received_j_us)


@dataclass(frozen=True)
class CapturePairEvidence:
    """A가 형식·동기 후보·보정문맥·local bridge 검사를 마친 개별 pair 사실.

    checks_passed는 lower validator의 결과이며 외부 ready setter가 아니다. 두 stream의
    seq 숫자 일치는 요구하지 않는다. SDK/기하 검사 자체와 bound 수락은 lower 책임이다.
    """

    scope: CaptureScope
    rgb: SourceStamp
    depth: SourceStamp
    completed_us: int
    checks_passed: bool
    input_kind: str = "LIVE"

    def __post_init__(self):
        if (type(self.scope) is not CaptureScope or type(self.rgb) is not SourceStamp
                or type(self.depth) is not SourceStamp or type(self.checks_passed) is not bool
                or type(self.input_kind) is not str or self.input_kind not in ("LIVE", "REPLAY")):
            raise ValueError("invalid individual capture evidence")
        _u64(self.completed_us, "completed_us")
        if max(self.rgb.received_j_us, self.depth.received_j_us) > self.completed_us:
            raise ValueError("capture completion precedes receipt")


class CaptureWarmup:
    """현재 source의 queue 이후 새30pair를 누적한다. payload/history set을 보관하지 않는다.

    queue_sdk_us는 queue 설정 뒤 확보한 원 SDK clock barrier다. 그보다 오래된 SDK
    stamp를 count하지 않는다. source 교체는 새 scope/tracker, M-only 교체는 이 객체 유지다.
    """

    def __init__(self, scope, queue_configured_us, queue_sdk_us, deadline, *, now_us):
        if type(scope) is not CaptureScope or type(deadline) is not AbsoluteDeadline:
            raise ValueError("invalid capture scope/deadline")
        _event(queue_configured_us, now_us)
        _u64(queue_sdk_us, "queue_sdk_us")
        if queue_configured_us < deadline.started_us:
            raise ValueError("queue setup precedes warmup stage")
        self.scope = scope
        self.deadline = AbsoluteDeadline.after(deadline.started_us, TIMING_US["warmup"], now_us=now_us,
                                               cap_us=deadline.expires_us)
        self.queue_configured_us, self.queue_sdk_us = queue_configured_us, queue_sdk_us
        self.count = 0
        self.ready_us = None
        self.failed = False
        self.last_rgb = self.last_depth = None
        self.last_completed_us = None

    @property
    def ready(self):
        return self.ready_us is not None

    def poll(self, now_us):
        _event(self.queue_configured_us, now_us)
        if not self.ready and self.deadline.expired(now_us):
            self.failed = True
        return self.failed

    def observe(self, proof, *, now_us):
        """개별 새 pair만 수락한다. 중복/비현문맥·prequeue·원 완료 역전은 무변경 False다."""
        if type(proof) is not CapturePairEvidence:
            raise ValueError("capture requires individual pair evidence")
        _event(proof.completed_us, now_us)
        if (proof.scope != self.scope or proof.input_kind != "LIVE" or not proof.checks_passed
                or proof.rgb.stream_instance != self.scope.rgb_stream
                or proof.depth.stream_instance != self.scope.depth_stream):
            return False
        if (proof.completed_us < self.queue_configured_us
                or min(proof.rgb.received_j_us, proof.depth.received_j_us) < self.queue_configured_us
                or min(proof.rgb.sdk_us, proof.depth.sdk_us) <= self.queue_sdk_us
                or not proof.rgb.advances(self.last_rgb) or not proof.depth.advances(self.last_depth)
                or self.last_completed_us is not None and proof.completed_us < self.last_completed_us):
            return False
        if self.ready or self.poll(now_us):
            return False
        self.last_rgb, self.last_depth = proof.rgb, proof.depth
        self.last_completed_us = proof.completed_us
        self.count += 1
        if self.count == CAPTURE_PAIR_COUNT:
            self.ready_us = proof.completed_us
        return True


@dataclass(frozen=True)
class ParserContext:
    """현재 M·원 BIND·RawBinding·model/parser 결박. PublicationBinding은 필요하지 않다."""

    capture_scope: CaptureScope
    mapper_worker: str
    bind_request_id: str
    model_id: str
    parser_id: str

    def __post_init__(self):
        if type(self.capture_scope) is not CaptureScope:
            raise ValueError("invalid parser capture scope")
        _uuid(self.mapper_worker)
        _uuid(self.bind_request_id)
        _label(self.model_id)
        _label(self.parser_id)


@dataclass(frozen=True)
class ParserEvidence:
    """M의 개별 실제 output↔passthrough 입력/해석 사실. empty detection도 parser_ok다.

    pairing_and_freshness_validated는 실제 lease/원RGB 연결·원 age/모델 문맥을 lower
    adapter가 수락했다는 뜻이다. parser_ok는 tensor 형식/layout/finite와 후처리의
    성공/실패다. NaN 같은 현재 paired 실패도 원 metadata를 보존해 streak를 끊을 수
    있다. kernel은 NN FPS/500ms 한도를 새로 만들거나 payload를 다시 검사하지 않는다.
    """

    context: ParserContext
    nn_input: SourceStamp
    nn_output: SourceStamp
    completed_us: int
    pairing_and_freshness_validated: bool
    parser_ok: bool
    input_kind: str = "LIVE"

    def __post_init__(self):
        if (type(self.context) is not ParserContext or type(self.nn_input) is not SourceStamp
                or type(self.nn_output) is not SourceStamp
                or type(self.pairing_and_freshness_validated) is not bool
                or type(self.parser_ok) is not bool
                or type(self.input_kind) is not str or self.input_kind not in ("LIVE", "REPLAY")):
            raise ValueError("invalid individual parser evidence")
        _u64(self.completed_us, "completed_us")
        if max(self.nn_input.received_j_us, self.nn_output.received_j_us) > self.completed_us:
            raise ValueError("parser completion precedes receipt")


class ParserWarmup:
    """REQUIRED의 현재 두 prerequisite→원15초/연속5개. 외부 WAIT 상태와 독립이다.

    fresh 입력의 원시각이 stage보다 조금 이른 경우의 age/lease 수락은 lower validator
    책임이다. parser 완료 사건은 stage 시작 이후여야 한다. OPTIONAL 상세 parser와
    post-warmup freshness는 미지원이며 READY 이후 이 tracker로 재승인/철회하지 않는다.
    """

    def __init__(self, context, nn_requirement, *, classical_fallback_allowed=False, cap_us=None):
        if type(context) is not ParserContext:
            raise ValueError("invalid parser context")
        if type(nn_requirement) is not str or nn_requirement not in ("REQUIRED", "OPTIONAL", "ABSENT"):
            raise ValueError("unknown NN requirement")
        if type(classical_fallback_allowed) is not bool:
            raise ValueError("classical approval must be bool")
        if nn_requirement == "REQUIRED" and context.capture_scope.nn_input_stream is None:
            raise ValueError("REQUIRED requires actual NN stream identities")
        if nn_requirement == "ABSENT" and context.capture_scope.nn_input_stream is not None:
            raise ValueError("ABSENT requires a no-NN source scope")
        if cap_us is not None:
            _u64(cap_us, "cap_us")
        self.context, self.nn_requirement = context, nn_requirement
        self.classical_policy_allowed = nn_requirement == "OPTIONAL" and classical_fallback_allowed
        self.cap_us = cap_us
        self.capture_ready_us = self.bind_ack_us = self.ready_us = None
        self.deadline = None
        self.streak = 0
        self.last_input = None
        self.last_completed_us = None
        self.failed = False

    @property
    def status(self):
        if self.nn_requirement == "ABSENT":
            return "ABSENT"
        if self.nn_requirement == "OPTIONAL":
            return "OPTIONAL_PARSER_NOT_IMPLEMENTED"
        return "FAILED" if self.failed else "READY" if self.nn_ready else "PENDING" if self.deadline else "WAIT_PREREQUISITES"

    @property
    def nn_ready(self):
        return self.nn_requirement == "REQUIRED" and self.ready_us is not None and not self.failed

    def _start(self, now_us):
        if (self.nn_requirement == "REQUIRED" and self.deadline is None
                and self.capture_ready_us is not None and self.bind_ack_us is not None):
            self.deadline = AbsoluteDeadline.after(max(self.capture_ready_us, self.bind_ack_us),
                                                   TIMING_US["warmup"], now_us=now_us, cap_us=self.cap_us)
        self.poll(now_us)

    def admit_capture(self, capture, *, now_us):
        """동일 source의 누적30 완료 원 시각을 빌린다. M 교체가 A count를 초기화하지 않는다."""
        _u64(now_us, "now_us")
        if (type(capture) is not CaptureWarmup or capture.scope != self.context.capture_scope
                or not capture.ready or capture.failed or self.capture_ready_us is not None):
            return False
        _event(capture.ready_us, now_us)
        self.capture_ready_us = capture.ready_us
        self._start(now_us)
        return True

    def admit_binding(self, binding, *, now_us):
        """현재 M/원 BIND의 ACK 시각을 빌린다. 두 원 사건이 모두 있으면 NN timer를 시작한다."""
        _u64(now_us, "now_us")
        if (type(binding) is not BindWatch or binding.role != "M" or binding.failed or binding.ack_us is None
                or (binding.worker_instance, binding.binding_sha256, binding.request_id) != (
                    self.context.mapper_worker, self.context.capture_scope.raw_binding_sha256,
                    self.context.bind_request_id) or self.bind_ack_us is not None):
            return False
        _event(binding.ack_us, now_us)
        self.bind_ack_us = binding.ack_us
        self._start(now_us)
        return True

    def poll(self, now_us):
        _u64(now_us, "now_us")
        if self.nn_requirement == "REQUIRED" and self.deadline is not None:
            if not self.nn_ready and self.deadline.expired(now_us):
                self.failed = True
        return self.failed

    def observe(self, proof, *, now_us):
        """current fresh 실패도 처리한 input으로 기억한다. 동일 input의 재시도는 streak를 채우지 않는다."""
        if type(proof) is not ParserEvidence:
            raise ValueError("parser requires individual evidence")
        _event(proof.completed_us, now_us)
        if (proof.context != self.context or proof.input_kind != "LIVE"
                or not proof.pairing_and_freshness_validated):
            return False
        scope = self.context.capture_scope
        if (proof.nn_input.stream_instance != scope.nn_input_stream
                or proof.nn_output.stream_instance != scope.nn_output_stream
                or (proof.nn_input.sequence, proof.nn_input.device_us, proof.nn_input.sdk_us) != (
                    proof.nn_output.sequence, proof.nn_output.device_us, proof.nn_output.sdk_us)):
            return False
        if self.nn_requirement != "REQUIRED" or self.deadline is None or self.nn_ready:
            return False
        if (proof.completed_us < self.deadline.started_us or not proof.nn_input.advances(self.last_input)
                or self.last_completed_us is not None and proof.completed_us < self.last_completed_us):
            return False
        if self.poll(now_us):
            return False
        self.last_input = proof.nn_input
        self.last_completed_us = proof.completed_us
        self.streak = self.streak + 1 if proof.parser_ok else 0
        if self.streak == REQUIRED_NN_PAIR_COUNT:
            self.ready_us = proof.completed_us
        return True
