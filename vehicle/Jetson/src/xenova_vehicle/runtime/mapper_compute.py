"""M-main의 제어와 non-daemon 계산 owner 하나를 분리한다.

최종 설계의 직접 thread 상한은 의도적으로7→8이며 M에 compute 하나를 추가한다.
application process는 늘리지 않는다. SDK/vendor thread의 실제 inventory는 별도다.
actor/socket/outbox/arena/token은 호출하지 않는다. 입력은 이미 view 반환과 원 RELEASE
실송신이 끝난 owned sample이어야 하며 이 선행 사실은 M-main이 소유한다.

processor_factory(generation)는 계산 thread에서만 호출한다. processor.parse(input,
checkpoint)는 NN 후보를 반환하고 geometry(input,parsed,checkpoint)는 독립 bytes 또는
None을 반환한다. checkpoint()는 취소 확인, checkpoint('완료단계')는 실제 단계 완료다.
processor는 candidate와 accepted 상태를 분리하고 최종 checkpoint 뒤에만 채택한다.
선택 cancelled(input,reason)도 같은 thread에서 호출한다. ordinary expiry는 accepted
상태를 버리지 않는다. source 교체만 새 factory를 직렬 호출하며 새 권한은 만들지 않는다.
선택 accepted(input)는 최종 값/원 기한/세대 검사 뒤 lock 안에서 참조 하나만 교환한다.
이 hook 안에서는 I/O·복사·대기·재진입을 금지한다. heavy 작업은 geometry 안에서 끝낸다.
"""

from collections import Counter, deque
from dataclasses import dataclass, replace
import json
import math
import threading
import time
from uuid import UUID


MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_CONTEXT_BYTES = 65_536
MAX_RESULT_BYTES = 2 * 1024 * 1024
MAX_PHASE_EVENTS = 8
MAX_OWNED_BYTES = 8 * 1024 * 1024
PARSER_BUDGET_US = 500_000
U64_MAX = 2**64 - 1


class MapperComputeError(ValueError):
    """값/수명/handshake가 현재 계산 owner의 계약과 다르다."""


class MapperStillAlive(RuntimeError):
    """bounded join 뒤에도 계산 thread가 남았다. STOPPED 성공으로 바꾸면 안 된다."""


class MapperCancelled(RuntimeError):
    """원 기한/STOP/source fence. processor는 candidate만 버리고 이 예외를 다시 전달한다."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


_Cancelled = MapperCancelled


def _j_us():
    return time.monotonic_ns() // 1000


def _uint(value):
    if type(value) is not int or not 0 <= value <= U64_MAX:
        raise MapperComputeError('expected uint64 microseconds')
    return value


def _generation(value):
    if (type(value) is not str or not 1 <= len(value) <= 256
            or any(not 33 <= ord(c) <= 126 for c in value)):
        raise MapperComputeError('expected bounded original source generation')
    return value


def _clock_value(value):
    if type(value) is str and value.isascii() and value.isdigit() and len(value) <= 20:
        value = int(value)
    return _uint(value)


def _golden_copy(value):
    if value is None:
        return None
    nodes = 0
    characters = 0

    def check(item, depth=0):
        nonlocal nodes, characters
        nodes += 1
        if nodes > 512 or depth > 8:
            raise MapperComputeError('golden metadata exceeds bounded tree')
        if item is None or type(item) is bool or type(item) is int and item.bit_length() <= 64:
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is str:
            characters += len(item)
            if characters <= 8192:
                return
        if type(item) is list and len(item) <= 64:
            for child in item:
                check(child, depth + 1)
            return
        if type(item) is dict and len(item) <= 64 and all(type(key) is str for key in item):
            for key, child in item.items():
                check(key, depth + 1)
                check(child, depth + 1)
            return
        raise MapperComputeError('invalid golden metadata')

    check(value)
    raw = json.dumps(value, allow_nan=False, ensure_ascii=True).encode('ascii')
    if len(raw) > 8192:
        raise MapperComputeError('golden metadata exceeds8192 bytes')
    return json.loads(raw)


def _packet_copy(packet):
    if packet is None:
        return None
    result = object.__new__(type(packet))
    for name in packet.__dataclass_fields__:
        value = getattr(packet, name)
        object.__setattr__(result, name, _golden_copy(value) if name == 'golden' else value)
    return result


def _clock_bytes(clocks, names):
    if type(clocks) is not dict or set(clocks) != names:
        raise MapperComputeError('clock coverage differs from original streams')
    count = 0

    def copy(value, depth=0):
        nonlocal count
        count += 1
        if count > 128 or depth > 5:
            raise MapperComputeError('clock metadata exceeds bounded native tree')
        if value is None or type(value) is bool:
            return value
        if type(value) is int and -U64_MAX <= value <= U64_MAX:
            return value
        if type(value) is str and len(value) <= 128 and value.isascii():
            return value
        if type(value) in (tuple, list) and len(value) <= 4:
            return [copy(v, depth + 1) for v in value]
        if type(value) is dict and len(value) <= 8 and all(type(k) is str and len(k) <= 64 for k in value):
            return {k: copy(v, depth + 1) for k, v in value.items()}
        raise MapperComputeError('clock metadata is not bounded native data')

    result = copy(clocks)
    for clock in result.values():
        if type(clock) is not dict or set(clock) != {'bridge_sample', 'sdk_host_timestamp_j_bracket_us'}:
            raise MapperComputeError('missing original clock evidence fields')
        bracket = clock['sdk_host_timestamp_j_bracket_us']
        if bracket is not None and (type(bracket) is not list or len(bracket) != 2
                or _clock_value(bracket[0]) > _clock_value(bracket[1])):
            raise MapperComputeError('invalid original SDK/J bracket')
    raw = json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')
    if len(raw) > 8192:
        raise MapperComputeError('clock metadata exceeds8192 bytes')
    return raw


@dataclass(frozen=True, init=False)
class MapperInput:
    """원 frame bytes를 공유하고 mutable clock/golden만 분리한다. capture 시각은 만들지 않는다."""

    job_id: str
    source_generation: str
    expires_us: int
    parser_cap_us: int | None
    context_bytes: bytes
    source_key: tuple
    nn_sequence: int | None
    nn_source_key: tuple | None
    byte_length: int
    _sample: object
    _clocks: bytes
    _sdk_lower_bounds: tuple | None
    _available_us: int

    def __init__(self, job_id, source_generation, sample, expires_us, *, parser_cap_us=None, context_bytes=b''):
        from .resident_samples import CaptureSample
        from ..oak.frames import RawFrame
        from ..oak.yolop_frames import NNInputStamp, NNPacket
        try:
            valid_id = type(job_id) is str and str(UUID(job_id)) == job_id
        except ValueError:
            valid_id = False
        if not valid_id:
            raise MapperComputeError('job ID must be a canonical UUID')
        _generation(source_generation)
        _uint(expires_us)
        if parser_cap_us is not None:
            _uint(parser_cap_us)
        if type(context_bytes) is not bytes or len(context_bytes) > MAX_CONTEXT_BYTES:
            raise MapperComputeError('context must be bounded owned bytes')
        if type(sample) is not CaptureSample or type(sample.rgb) is not RawFrame or type(sample.depth) is not RawFrame:
            raise MapperComputeError('expected original owned CaptureSample')
        sample.rgb.validate()
        sample.depth.validate()
        if sample.rgb.stream != 'rgb_video' or sample.depth.stream != 'depth_right':
            raise MapperComputeError('original RGB/RIGHT stream mismatch')
        packet = sample.nn_output
        if (sample.nn_input is None) != (packet is None):
            raise MapperComputeError('NN requires original input and output')
        names = {'rgb_video', 'depth_right'}
        size = len(sample.rgb.payload) + len(sample.depth.payload)
        if packet is not None:
            if type(sample.nn_input) is not NNInputStamp or type(packet) is not NNPacket:
                raise MapperComputeError('expected exact owned NN records')
            sample.nn_input.validate()
            packet.validate()
            original = sample.nn_input
            if (original.sequence, original.device_us, original.sdk_us, original.context_key) != (
                    packet.sequence, packet.device_us, packet.sdk_us, packet.context_key):
                raise MapperComputeError('NN output changed original input identity')
            if (original.sequence, original.device_us, original.sdk_us) != (
                    sample.rgb.sequence, sample.rgb.device_us, sample.rgb.sdk_us):
                raise MapperComputeError('NN input changed original RGB parent')
            # 원 validation 측정값은 보존하고 golden의 mutable tree만 소유 사본으로 만든다.
            packet = _packet_copy(packet)
            names.update(('nn_input', 'nn_output'))
            size += len(packet.payload)
        if size > MAX_INPUT_BYTES:
            raise MapperComputeError('owned frame payload exceeds2MiB')
        clocks = _clock_bytes(sample.clocks, names)
        original_clocks = json.loads(clocks)
        lowers = [_clock_value(value['sdk_host_timestamp_j_bracket_us'][0]) for value in original_clocks.values()
            if value['sdk_host_timestamp_j_bracket_us'] is not None]
        sdk_bounds = None if len(lowers) != len(names) else (min(lowers), max(lowers))
        detached = CaptureSample(sample.rgb, sample.depth, sample.nn_input, packet, {})
        for name, value in dict(job_id=job_id, source_generation=source_generation, expires_us=expires_us,
                parser_cap_us=parser_cap_us, context_bytes=context_bytes,
                source_key=(sample.rgb.stream, sample.rgb.sequence, sample.rgb.device_us, sample.rgb.sdk_us),
                nn_sequence=None if packet is None else packet.sequence,
                nn_source_key=None if packet is None else (sample.nn_input.sequence, sample.nn_input.device_us,
                    sample.nn_input.sdk_us, sample.nn_input.received_j_us),
                byte_length=size + len(clocks) + len(context_bytes) + (0 if packet is None or packet.golden is None
                    else len(json.dumps(packet.golden, ensure_ascii=True).encode('ascii'))),
                _sample=detached, _clocks=clocks, _sdk_lower_bounds=sdk_bounds,
                _available_us=max(sample.rgb.received_j_us, sample.depth.received_j_us,
                    0 if packet is None else packet.received_j_us,
                    0 if sample.nn_input is None else sample.nn_input.received_j_us)).items():
            object.__setattr__(self, name, value)

    @property
    def sample(self):
        from .resident_samples import CaptureSample
        original = self._sample
        # processor 쪽 수정이 main 또는 다른 job의 clock metadata에 영향을 주지 않는다.
        return CaptureSample(original.rgb, original.depth, original.nn_input, original.nn_output, json.loads(self._clocks))

    def fresh(self, now_us):
        """SDK/J admission과 caller의 원 expiry만 검사한다. metric capture 수락이 아니다."""
        _uint(now_us)
        bounds = self._sdk_lower_bounds
        return (bounds is not None and max(bounds[1], self._available_us) <= now_us < self.expires_us
            and now_us - bounds[0] <= PARSER_BUDGET_US)


@dataclass(frozen=True)
class MapperEvent:
    kind: str
    job_id: str | None
    source_generation: str
    generation_serial: int
    source_key: tuple
    nn_sequence: int | None
    started_us: int
    completed_us: int
    expires_us: int
    accepted: bool
    reason: str | None = None
    decode_started_us: int | None = None
    parser_deadline_us: int | None = None
    nn_source_key: tuple | None = None


@dataclass(frozen=True)
class MapperResult:
    job_id: str
    source_generation: str
    generation_serial: int
    source_key: tuple
    started_us: int
    completed_us: int
    expires_us: int
    accepted: bool
    payload: bytes | None
    reason: str | None = None


class MapperCompute:
    """활성1+최신 RAW1/NN1, phase8, 결과1. native 강제 중지는 주장하지 않는다.

    start/submit/poll/ACK/source 교체/STOP은 생성한 M-main thread만 호출한다.
    processor와 그 mutable core는 계산 thread 하나만 만진다. source 교체는 즉시
    결과를 fence하고 원 호출이 끝난 뒤에만 새 factory를 직렬 호출한다.
    """

    def __init__(self, processor_factory, *, source_generation, clock=_j_us):
        if not callable(processor_factory) or not callable(clock):
            raise MapperComputeError('factory and original monotonic clock are required')
        self._factory, self._clock = processor_factory, clock
        self._generation = _generation(source_generation)
        self._serial = 1
        self._owner = threading.current_thread()
        self._condition = threading.Condition()
        self._pending = {'RAW': None, 'NN': None}
        self._last_kind = 'NN'
        self._active = None
        self._events = deque()
        self._result = None
        self._stopping = False
        self._started = False
        self._terminal_error = None
        self._counts = Counter()
        self._thread = threading.Thread(target=self._run, name='M-compute', daemon=False)

    def _main(self):
        if threading.current_thread() is not self._owner:
            raise MapperComputeError('only original M-main owns compute control')

    def _now(self):
        return _uint(self._clock())

    @property
    def is_alive(self):
        return self._thread.is_alive()

    @property
    def terminal_error(self):
        with self._condition:
            return self._terminal_error

    def start(self):
        self._main()
        with self._condition:
            if self._started or self._stopping:
                raise MapperComputeError('compute owner cannot be restarted')
            self._started = True
            self._thread.start()

    def submit(self, item):
        """채택은 queue 예약뿐이다. lease 반환이나 parser/geometry 성공이 아니다."""
        self._main()
        if type(item) is not MapperInput:
            raise MapperComputeError('expected validated MapperInput')
        now = self._now()
        with self._condition:
            if not self._started or self._stopping:
                raise MapperComputeError('compute owner is not accepting inputs')
            if item.source_generation != self._generation or not item.fresh(now):
                self._counts['rejected_stale_or_foreign'] += 1
                return False
            held = [value[0] for value in self._pending.values() if value is not None]
            if self._active is not None:
                held.append(self._active['item'])
            for previous in held:
                if previous.job_id == item.job_id:
                    if previous != item:
                        raise MapperComputeError('held job ID changed original input')
                    self._counts['duplicate_no_effect'] += 1
                    return False
            kind = 'NN' if item.nn_sequence is not None else 'RAW'
            previous = self._pending[kind]
            # 원 seq가 큰 최신 후보를 보존한다. 같은 capture의 RAW와 NN은 다른 칸이다.
            if previous is not None and item.source_key[1:] <= previous[0].source_key[1:]:
                self._counts['older_pending_no_effect'] += 1
                return False
            used = item.byte_length + sum(value[0].byte_length for key, value in self._pending.items()
                if key != kind and value is not None)
            if self._active is not None:
                used += self._active['item'].byte_length
            if used > MAX_OWNED_BYTES:
                self._counts['owned_budget_rejected'] += 1
                return False
            self._counts['pending_replaced'] += int(previous is not None)
            self._pending[kind] = item, self._serial
            self._condition.notify_all()
            return True

    def replace_source(self, source_generation):
        """정상 clock renewal/등록/모드 변경에는 호출하지 않는다. 같은 label 재등장도 새 serial이다."""
        self._main()
        _generation(source_generation)
        with self._condition:
            if self._stopping or self._serial == U64_MAX:
                raise MapperComputeError('source cannot be replaced')
            self._generation = source_generation
            self._serial += 1
            self._pending = {'RAW': None, 'NN': None}
            self._result = None
            self._condition.notify_all()

    def poll(self):
        self._main()
        with self._condition:
            now = self._now()
            result = tuple(replace(event, accepted=False, reason='FENCED_OR_LATE_DELIVERY')
                if event.accepted and (self._stopping or event.generation_serial != self._serial
                    or now >= min(event.expires_us, event.parser_deadline_us
                        if event.kind == 'PARSER_START_REQUIRED' else U64_MAX))
                else event for event in self._events)
            self._events.clear()
            return result

    def take_result(self):
        self._main()
        with self._condition:
            result, self._result = self._result, None
            if result is not None and result.accepted and (self._stopping
                    or result.generation_serial != self._serial or self._now() >= result.expires_us):
                result = replace(result, accepted=False, payload=None, reason='FENCED_OR_LATE_DELIVERY')
            return result

    def acknowledge_parser_start(self, job_id, sent):
        """M-main만 actual SentPacket을 전달한다. 이 값 자체는 OS 송신의 인증 증명이 아니다."""
        self._main()
        from .resident_transport import SentPacket
        from .local_envelope import StructuralPacket
        if type(sent) is not SentPacket or type(sent.packet) is not StructuralPacket:
            raise MapperComputeError('expected original actual SentPacket')
        _uint(sent.started_us)
        _uint(sent.completed_us)
        with self._condition:
            active = self._active
            if active is None or active['item'].job_id != job_id or not active.get('waiting'):
                return False
            now = self._now()
            if self._stopping or active['serial'] != self._serial or now >= active['parser_deadline']:
                return False
            item, packet = active['item'], sent.packet
            payload, fields = packet.payload, packet.fields
            if (packet.kind != 'PROGRESS' or fields['binding_domain'] != 'LIFECYCLE' or fields['binding_revision'] != '0'
                    or fields['request_id'] != job_id
                    or set(payload) != {'stage', 'source_sequence', 'completed_j_us', 'state', 'reason'}
                    or payload.get('stage') != 'PARSER_STARTED' or payload['reason'] is not None
                    or payload.get('source_sequence') != str(item.nn_sequence)
                    or payload.get('completed_j_us') != str(active['parser_requested'])
                    or not active['parser_requested'] <= int(fields['issued_j_us']) <= sent.started_us <= sent.completed_us <= now
                    or sent.completed_us >= active['parser_deadline']):
                raise MapperComputeError('actual parser-start packet differs from original event')
            if active.get('ack') is not None:
                if active['ack'] is not sent:
                    raise MapperComputeError('parser-start acknowledgement cannot be replaced')
                return False
            active['ack'] = sent
            self._condition.notify_all()
            return True

    def skip_parser(self, job_id):
        """이미 처리한 NN의 현재 대기를 건너뛴다. main이 원 NN stamp로 판단하며 성공 사실은 없다."""
        self._main()
        with self._condition:
            active = self._active
            if (active is None or active['item'].job_id != job_id or not active.get('waiting')
                    or active.get('ack') is not None):
                return False
            if (self._stopping or active['serial'] != self._serial
                    or self._now() >= active['parser_deadline'] or not active['item'].fresh(self._now())):
                return False
            active['skip'] = True
            active['waiting'] = False
            self._counts['parser_skipped'] += 1
            self._condition.notify_all()
            return True

    def request_stop(self):
        self._main()
        with self._condition:
            self._stopping = True
            self._pending = {'RAW': None, 'NN': None}
            self._result = None
            self._condition.notify_all()

    def join(self, timeout_s):
        """실제 종료만 True다. timeout은 thread 취소가 아니며 실패 뒤 성공 STOPPED를 만들지 않는다."""
        self._main()
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 0 <= timeout_s <= 2:
            raise MapperComputeError('bounded join requires0..2 seconds')
        if not self._stopping:
            raise MapperComputeError('request_stop must precede join')
        if self._started:
            self._thread.join(timeout_s)
        if self._thread.is_alive():
            raise MapperStillAlive('M-compute remains alive; process exit/reap is still required')
        return True

    def snapshot(self):
        self._main()
        with self._condition:
            active = self._active
            return dict(source_generation=self._generation, generation_serial=self._serial,
                active=None if active is None else active['item'].job_id,
                active_expires_us=None if active is None else active['item'].expires_us,
                active_started_us=None if active is None else active['started'],
                parser_deadline_us=None if active is None else active.get('parser_deadline'),
                waiting_parser_start=False if active is None else bool(active.get('waiting')),
                pending_raw=self._pending['RAW'] is not None, pending_nn=self._pending['NN'] is not None,
                phase_events=len(self._events), result_pending=self._result is not None,
                stopping=self._stopping, is_alive=self.is_alive, terminal_error=self._terminal_error,
                counts=dict(self._counts))

    def _emit(self, event):
        with self._condition:
            if event.kind == 'PROGRESS':
                # 단계 진전은 최신 하나로 합친다. NN/종료 사건 때문에 성공 진전을 위조하지 않는다.
                previous = len(self._events)
                self._events = deque(value for value in self._events if value.kind != 'PROGRESS')
                self._counts['progress_coalesced'] += previous - len(self._events)
                if len(self._events) >= MAX_PHASE_EVENTS:
                    self._counts['progress_not_queued'] += 1
                    return
            elif len(self._events) >= MAX_PHASE_EVENTS:
                self._events = deque(value for value in self._events if value.kind != 'PROGRESS')
            if len(self._events) >= MAX_PHASE_EVENTS:
                self._terminal_error = 'PHASE_BACKLOG_FULL'
                self._stopping = True
                self._pending = {'RAW': None, 'NN': None}
                self._result = None
                self._condition.notify_all()
                raise _Cancelled('PHASE_BACKLOG_FULL')
            self._events.append(event)

    def _event(self, kind, active, now, *, accepted=False, reason=None):
        item = active['item']
        return MapperEvent(kind, item.job_id, item.source_generation, active['serial'], item.source_key,
            item.nn_sequence, active.get('parser_requested', active['started']), now,
            item.expires_us, accepted, reason, active.get('decode_started'), active.get('parser_deadline'), item.nn_source_key)

    def _failed(self, active, error):
        with self._condition:
            self._terminal_error = type(error).__name__
            self._stopping = True
            self._pending = {'RAW': None, 'NN': None}
            self._result = None
        try:
            self._emit(self._event('FAILED', active, self._now(), reason=type(error).__name__))
        except BaseException:
            pass

    def _check(self, active, phase=None):
        now = self._now()
        with self._condition:
            if self._stopping or active['serial'] != self._serial:
                raise _Cancelled('STOP_OR_SOURCE_FENCE')
        if not active['item'].fresh(now):
            raise _Cancelled('SOURCE_EXPIRED')
        if active.get('parsing') and now >= active['parser_deadline']:
            raise _Cancelled('PARSER_DEADLINE')
        if phase is not None:
            if type(phase) is not str or not 1 <= len(phase) <= 64 or not phase.isascii():
                raise MapperComputeError('completed phase must be bounded ASCII')
            self._emit(self._event('PROGRESS', active, now, accepted=True, reason=phase))
        return now

    def _run(self):
        processor = None
        processor_serial = None
        while True:
            with self._condition:
                while not self._stopping and not any(self._pending.values()):
                    self._condition.wait(.02)
                if self._stopping:
                    return
                desired = 'RAW' if self._last_kind == 'NN' else 'NN'
                kind = desired if self._pending[desired] is not None else ('NN' if desired == 'RAW' else 'RAW')
                item, serial = self._pending[kind]
                self._pending[kind] = None
                active = dict(item=item, serial=serial, started=0)
                self._active = active
                self._last_kind = kind
            try:
                active['started'] = self._now()
                active['started'] = self._check(active)
                self._emit(self._event('JOB_STARTED', active, active['started'], accepted=True))
                if processor is None or processor_serial != serial:
                    processor = self._factory(item.source_generation)
                    processor_serial = serial
                    self._check(active)
                parsed = None
                if item.nn_sequence is not None:
                    requested = self._now()
                    if requested > U64_MAX - PARSER_BUDGET_US:
                        raise MapperComputeError('parser deadline overflows original u64 clock')
                    active.update(parser_requested=requested, parser_deadline=min(
                        item.expires_us, requested + PARSER_BUDGET_US,
                        U64_MAX if item.parser_cap_us is None else item.parser_cap_us), waiting=True)
                    self._emit(self._event('PARSER_START_REQUIRED', active, requested, accepted=True))
                    with self._condition:
                        while active.get('ack') is None and not active.get('skip'):
                            self._check(active)
                            if self._now() >= active['parser_deadline']:
                                raise _Cancelled('PARSER_START_EXPIRED')
                            self._condition.wait(.02)
                        active['waiting'] = False
                    if not active.get('skip'):
                        active['parsing'] = True
                        active['decode_started'] = self._check(active)
                        parsed = processor.parse(item, lambda phase=None: self._check(active, phase))
                        completed = self._check(active)
                        active['parsing'] = False
                        # geometry가 느리거나 실패해도 NN의 실제 완료 사실은 먼저 전달한다.
                        self._emit(self._event('NN_COMPLETED', active, completed, accepted=True))
                self._check(active)
                value = processor.geometry(item, parsed, lambda phase=None: self._check(active, phase))
                completed = self._check(active)
                if value is not None and (type(value) is not bytes or len(value) > MAX_RESULT_BYTES):
                    raise MapperComputeError('geometry must return bounded independent bytes or None')
                with self._condition:
                    # bytes 검사 뒤 실제 마지막 원 시각을 읽는다. accepted는 cheap 참조 swap만 한다.
                    completed = self._check(active)
                    callback = getattr(processor, 'accepted', None)
                    if callback is not None:
                        callback(item)
                    self._counts['result_replaced'] += int(self._result is not None)
                    self._result = MapperResult(item.job_id, item.source_generation, serial, item.source_key,
                        active['started'], completed, item.expires_us, True, value)
                self._emit(self._event('GEOMETRY_COMPLETED', active, completed, accepted=True))
            except _Cancelled as error:
                with self._condition:
                    self._counts[str(error)] += 1
                try:
                    callback = getattr(processor, 'cancelled', None)
                    if callback is not None:
                        callback(item, error.reason)
                    self._emit(self._event('CANCELLED', active, self._now(), reason=str(error)))
                except _Cancelled:
                    pass
                except BaseException as callback_error:
                    self._failed(active, callback_error)
            except BaseException as error:
                self._failed(active, error)
            finally:
                with self._condition:
                    self._active = None
                    self._condition.notify_all()
