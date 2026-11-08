"""Pi clock owner의 원 probe와 mapper clock admission. transport/권한/시각 재발급은 하지 않는다.

offset은 Pi-J다. 두 같은 source의 일관된 실제 왕복 뒤에만 원 expiry의 WorldTrust를
만든다. source revision과 time lease revision을 분리하며 observation deadline은 만지지 않는다.
"""

from dataclasses import dataclass
import json
import math
import os
import struct
from uuid import UUID, uuid4

from .codec import schema, encode, decode


TIMEOUT_US = 200000
PERIOD_US = 200000
MAX_LEASE_US = 1000000
U63_MAX = 2**63 - 1
CANDIDATE = struct.Struct('<HHQ')
PROBE_CAP = 1024
PLAN_CAP = 2048
PLAN_KEYS = frozenset(('major', 'vehicle', 'device', 'pi_host_boot', 'pi_client_instance', 'probe_seq',
    'nonce', 'expected_mapper_boot', 'expected_mapper_instance', 'expected_clock_epoch', 'admission_revision', 'prior_trust'))


class MapperClockError(ValueError):
    """원 요청/출처/시각/오차 profile이 맞지 않는 clock 후보."""


def integer(value, minimum=0, maximum=U63_MAX):
    if type(value) is not int or not minimum <= value <= maximum:
        raise MapperClockError('CLOCK_INTEGER')
    return value


def canonical_uuid(value):
    if type(value) is not str or len(value) != 36 or str(UUID(value)) != value:
        raise MapperClockError('CLOCK_UUID')
    return value


def ascii_id(value, maximum=64):
    if type(value) is not str or not 1 <= len(value) <= maximum or any(not 33 <= ord(c) <= 126 for c in value):
        raise MapperClockError('CLOCK_ID')
    return value


def strict_json(raw, cap=1024):
    if type(raw) is not bytes or not 0 < len(raw) <= cap:
        raise MapperClockError('CLOCK_JSON_SIZE')
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise MapperClockError('CLOCK_DUPLICATE_KEY')
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('ascii'), object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(MapperClockError('CLOCK_NONFINITE')))
    except (UnicodeError, RecursionError) as error:
        raise MapperClockError('CLOCK_JSON') from error
    if type(value) is not dict:
        raise MapperClockError('CLOCK_JSON_OBJECT')
    return value


def decimal(value):
    if (type(value) is not str or not value.isascii() or not value.isdigit()
            or len(value) > 19 or str(int(value)) != value):
        raise MapperClockError('CLOCK_DECIMAL')
    return integer(int(value))


@dataclass(frozen=True)
class ClockPolicy:
    vehicle: str
    device: str
    configuration: str
    calibration: str
    primitive: str
    timestamp_error_us: int
    drift_ppm: int
    max_offset_width_us: int
    lease_us: int
    vehicle_max_age_us: int
    lookback_us: int

    def __post_init__(self):
        import re
        for name in ('vehicle', 'configuration', 'calibration', 'primitive'):
            ascii_id(getattr(self, name))
        if type(self.device) is not str or re.fullmatch('[a-z0-9][a-z0-9_-]{0,31}', self.device) is None:
            raise MapperClockError('CLOCK_DEVICE')
        if len(self.primitive) != 64 or any(c not in '0123456789abcdef' for c in self.primitive):
            raise MapperClockError('CLOCK_PRIMITIVE')
        integer(self.timestamp_error_us, maximum=1000000)
        integer(self.drift_ppm, maximum=999999)
        integer(self.max_offset_width_us, 1, 2**31 - 1)
        integer(self.lease_us, 1, MAX_LEASE_US)
        integer(self.vehicle_max_age_us, 1, 1000000)
        integer(self.lookback_us, 1, 1000000)
        if self.lookback_us < self.vehicle_max_age_us:
            raise MapperClockError('CLOCK_LOOKBACK_PROFILE')


def drift_pad(ppm, pi_duration_us):
    """|dP/dJ-1|<=ppm/1e6에서 Pi 기간을 J 기간으로 보수 변환한다."""
    integer(ppm, maximum=999999)
    integer(pi_duration_us)
    denominator = 1000000 - ppm
    return (ppm * pi_duration_us + denominator - 1) // denominator


@dataclass(frozen=True)
class ProbePlan:
    request_id: str
    issued_us: int
    expires_us: int
    vehicle: str
    device: str
    pi_host_boot: str
    pi_client_instance: str
    probe_seq: int
    nonce: bytes
    admission_revision: int
    expected: tuple | None = None
    prior_trust_bytes: bytes | None = None

    def __post_init__(self):
        for name in ('request_id', 'pi_host_boot', 'pi_client_instance'):
            canonical_uuid(getattr(self, name))
        for name in ('vehicle', 'device'):
            ascii_id(getattr(self, name))
        for name in ('issued_us', 'expires_us', 'probe_seq', 'admission_revision'):
            integer(getattr(self, name), 1)
        if not self.issued_us < self.expires_us <= self.issued_us + TIMEOUT_US or type(self.nonce) is not bytes or len(self.nonce) != 16:
            raise MapperClockError('CLOCK_PLAN_LIFETIME')
        if self.expected is not None:
            if type(self.expected) is not tuple or len(self.expected) != 3:
                raise MapperClockError('CLOCK_EXPECTED_PEER')
            for value in self.expected:
                canonical_uuid(value)
        if self.prior_trust_bytes is not None and (type(self.prior_trust_bytes) is not bytes or len(self.prior_trust_bytes) > 1024):
            raise MapperClockError('CLOCK_PRIOR_TRUST')


def plan_json(plan):
    expected = (None, None, None) if plan.expected is None else plan.expected
    value = dict(major=1, vehicle=plan.vehicle, device=plan.device, pi_host_boot=plan.pi_host_boot,
        pi_client_instance=plan.pi_client_instance, probe_seq=str(plan.probe_seq), nonce=plan.nonce.hex(),
        admission_revision=str(plan.admission_revision),
        prior_trust=None if plan.prior_trust_bytes is None else plan.prior_trust_bytes.hex(), **dict(zip(
            ('expected_mapper_boot', 'expected_mapper_instance', 'expected_clock_epoch'), expected)))
    # tag13 결정에 따라 plan JSON cap도2048로 명시 변경했다. 전체 local4096 cap은 별도다.
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')
    if len(raw) > PLAN_CAP:
        raise MapperClockError('CLOCK_PLAN_SIZE')
    return raw


def parse_plan(raw, *, request_id, issued_us, expires_us):
    value = strict_json(raw, PLAN_CAP)
    if set(value) != PLAN_KEYS or type(value['major']) is not int or value['major'] != 1:
        raise MapperClockError('CLOCK_PLAN_FIELDS')
    expected = tuple(value[name] for name in ('expected_mapper_boot', 'expected_mapper_instance', 'expected_clock_epoch'))
    if all(item is None for item in expected):
        expected = None
    if type(value['nonce']) is not str or len(value['nonce']) != 32 or any(c not in '0123456789abcdef' for c in value['nonce']):
        raise MapperClockError('CLOCK_NONCE')
    prior = value['prior_trust']
    if prior is not None and (type(prior) is not str or len(prior) > 2048 or len(prior) % 2
            or any(c not in '0123456789abcdef' for c in prior)):
        raise MapperClockError('CLOCK_PRIOR_TRUST')
    prior = None if prior is None else bytes.fromhex(prior)
    return ProbePlan(request_id, issued_us, expires_us, value['vehicle'], value['device'], value['pi_host_boot'],
        value['pi_client_instance'], decimal(value['probe_seq']), bytes.fromhex(value['nonce']),
        decimal(value['admission_revision']), expected, prior)


def build_probe(plan, p1_us, *, prior_trust_bytes=None):
    integer(p1_us, 1)
    if not plan.issued_us <= p1_us < plan.expires_us:
        raise MapperClockError('CLOCK_PLAN_EXPIRED')
    probe = schema.MapperClockProbe(major=1, vehicle=plan.vehicle, device=plan.device,
        pi_host_boot=plan.pi_host_boot, pi_client_instance=plan.pi_client_instance, probe_seq=plan.probe_seq,
        nonce=plan.nonce, pi_send_us=p1_us, admission_revision=plan.admission_revision)
    if plan.expected is not None:
        for name, value in zip(('expected_mapper_boot', 'expected_mapper_instance', 'expected_clock_epoch'), plan.expected):
            setattr(probe, name, value)
    if prior_trust_bytes is not None and prior_trust_bytes != plan.prior_trust_bytes:
        raise MapperClockError('CLOCK_PRIOR_TRUST_NOT_ORIGINAL')
    prior_trust_bytes = plan.prior_trust_bytes
    if prior_trust_bytes is not None:
        if 'mapper_trust' not in probe.DESCRIPTOR.fields_by_name:
            raise MapperClockError('CLOCK_PRIOR_TRUST_UNSUPPORTED')
        trust = decode(prior_trust_bytes, schema.WorldTrust)
        if (trust.expires_pi_us <= p1_us or plan.expected is None
                or (trust.mapper_boot, trust.mapper_instance, trust.clock_epoch) != plan.expected
                or trust.vehicle != plan.vehicle):
            raise MapperClockError('CLOCK_PRIOR_TRUST_CONTEXT')
        probe.mapper_trust.CopyFrom(trust)
    raw = encode(probe)
    if len(raw) > PROBE_CAP:
        raise MapperClockError('CLOCK_PROBE_SIZE')
    return raw


def candidate_body(probe_bytes, reply_bytes, p4_us):
    integer(p4_us, 1)
    if (type(probe_bytes) is not bytes or type(reply_bytes) is not bytes or not 0 < len(probe_bytes) <= PROBE_CAP
            or not 0 < len(reply_bytes) <= 1024 or CANDIDATE.size + len(probe_bytes) + len(reply_bytes) > 2048):
        raise MapperClockError('CLOCK_CANDIDATE_SIZE')
    return CANDIDATE.pack(len(probe_bytes), len(reply_bytes), p4_us) + probe_bytes + reply_bytes


def parse_candidate(raw):
    if type(raw) is not bytes or len(raw) < CANDIDATE.size:
        raise MapperClockError('CLOCK_CANDIDATE_SIZE')
    a, b, p4 = CANDIDATE.unpack_from(raw)
    if not 0 < a <= PROBE_CAP or not 0 < b <= 1024 or len(raw) != CANDIDATE.size + a + b or len(raw) > 2048:
        raise MapperClockError('CLOCK_CANDIDATE_LENGTH')
    integer(p4, 1)
    return raw[12:12 + a], raw[12 + a:], p4


@dataclass(frozen=True)
class ClockLease:
    trust_bytes: bytes
    source_revision: int
    time_lease_revision: int | None
    completed_pi_us: int | None
    probe_pi_send_us: int
    basis_pi_us: int
    pi_support_lower_us: int
    expires_pi_us: int
    max_offset_width_us: int
    raw_probe_bytes: bytes
    lookback_us: int
    lease_us: int

    def __post_init__(self):
        if (type(self.trust_bytes) is not bytes or not 0 < len(self.trust_bytes) <= 1024
                or type(self.raw_probe_bytes) is not bytes or not 0 < len(self.raw_probe_bytes) <= PROBE_CAP):
            raise MapperClockError('CLOCK_LEASE_BYTES')
        trust = decode(self.trust_bytes, schema.WorldTrust)
        probe = decode(self.raw_probe_bytes, schema.MapperClockProbe)
        integer(self.source_revision, 1)
        integer(self.basis_pi_us, 1)
        integer(self.lookback_us, 1, 1000000)
        integer(self.lease_us, 1, MAX_LEASE_US)
        integer(self.max_offset_width_us, 1, 2**31 - 1)
        if (self.expires_pi_us != trust.expires_pi_us or self.basis_pi_us + self.lease_us != self.expires_pi_us
                or self.pi_support_lower_us != max(0, self.basis_pi_us - self.lookback_us)
                or self.probe_pi_send_us != probe.pi_send_us or trust.vehicle != probe.vehicle
                or not self.basis_pi_us <= self.probe_pi_send_us < self.expires_pi_us
                or trust.lower_offset_us > trust.upper_offset_us
                or trust.upper_offset_us - trust.lower_offset_us > min(trust.max_error_us, self.max_offset_width_us)):
            raise MapperClockError('CLOCK_LEASE_BASIS')
        if (self.time_lease_revision is None) != (self.completed_pi_us is None):
            raise MapperClockError('CLOCK_LEASE_COMPLETION')
        if self.time_lease_revision is not None:
            integer(self.time_lease_revision, 1)
            integer(self.completed_pi_us, self.basis_pi_us, self.expires_pi_us - 1)
        elif not probe.HasField('mapper_trust') or encode(probe.mapper_trust) != self.trust_bytes:
            raise MapperClockError('CLOCK_LEASE_RETURNED_BYTES')

    @property
    def pi_host_boot(self):
        return decode(self.raw_probe_bytes, schema.MapperClockProbe).pi_host_boot

    @property
    def source(self):
        """독립 scalar dict. clock_epoch는 J clock이며 원 sensor clock을 바꾸지 않는다."""
        trust = decode(self.trust_bytes, schema.WorldTrust)
        probe = decode(self.raw_probe_bytes, schema.MapperClockProbe)
        return dict(device=probe.device, **{name: getattr(trust, name) for name in ('vehicle', 'mapper_boot',
            'mapper_instance', 'clock_epoch', 'map_epoch', 'frame_epoch', 'odom_epoch',
            'configuration', 'calibration', 'primitive')})

    def pi_interval_to_j(self, interval, now_j_us):
        """고정 지원 window와 원 lease 안에서만 변환한다. arrival로 관측 시각을 만들지 않는다."""
        integer(now_j_us)
        if type(interval) not in (tuple, list) or len(interval) != 2:
            raise MapperClockError('CLOCK_SOURCE_INTERVAL')
        lo, hi = (integer(value) for value in interval)
        if not self.pi_support_lower_us <= lo <= hi < self.expires_pi_us:
            raise MapperClockError('CLOCK_LOOKBACK_UNSUPPORTED')
        trust = decode(self.trust_bytes, schema.WorldTrust)
        if (trust.lower_offset_us > trust.upper_offset_us
                or trust.upper_offset_us - trust.lower_offset_us > min(self.max_offset_width_us, trust.max_error_us)
                or not self.probe_pi_send_us <= now_j_us + trust.upper_offset_us < self.expires_pi_us):
            raise MapperClockError('CLOCK_RETURNED_LEASE_EXPIRED')
        return integer(lo - trust.upper_offset_us), integer(hi - trust.lower_offset_us)

    def j_interval_to_pi(self, interval, now_j_us):
        """원 Pi−J offset으로 J 구간 전체를 Pi hull로 옮긴다. 시각/lease를 재발급하지 않는다.

        원 bound는 이미 승인 window 전체의 drift를 포함한다. 현재 S source와의
        일치는 caller가 대조하며, 지원 window·현재 lease·왕복 U63 범위는 같은
        pi_interval_to_j 정책으로 검산한다. 수신시각을 관측구간으로 대신하지 않는다.
        """
        integer(now_j_us)
        if type(interval) not in (tuple, list) or len(interval) != 2:
            raise MapperClockError('CLOCK_SOURCE_INTERVAL')
        lo, hi = (integer(value) for value in interval)
        if lo > hi:
            raise MapperClockError('CLOCK_SOURCE_INTERVAL')
        trust = decode(self.trust_bytes, schema.WorldTrust)
        result = integer(lo + trust.lower_offset_us), integer(hi + trust.upper_offset_us)
        back = self.pi_interval_to_j(result, now_j_us)
        if not back[0] <= lo <= hi <= back[1]:
            raise MapperClockError('CLOCK_SOURCE_INTERVAL')
        return result


def restore_returned_probe(probe, policy, expected_source, pi_host_boot, now_j_us):
    """승인 Pi가 tag13으로 돌려준 원 trust를 검증한다. 없는 P4/완료시각은 복원하지 않는다.

    source의 인증·등록은 caller 책임이다. J는 P4를 다시 계산하지 않고 고정 profile의
    lease/H와 원 expiry에서 지원 window를 복원한다. Pi/S revision의 숫자는 비교하지 않는다.
    """
    if type(policy) is not ClockPolicy:
        raise MapperClockError('CLOCK_POLICY')
    if type(probe) not in (bytes, schema.MapperClockProbe):
        raise MapperClockError('CLOCK_PROBE_TYPE')
    raw = probe if type(probe) is bytes else encode(probe)
    if len(raw) > PROBE_CAP:
        raise MapperClockError('CLOCK_PROBE_SIZE')
    probe = decode(raw, schema.MapperClockProbe)
    integer(now_j_us, 1)
    canonical_uuid(pi_host_boot)
    canonical_uuid(probe.pi_client_instance)
    if (probe.pi_host_boot != pi_host_boot or (probe.vehicle, probe.device) != (policy.vehicle, policy.device)
            or not probe.admission_revision):
        raise MapperClockError('CLOCK_RETURNED_ORIGIN')
    if not probe.HasField('mapper_trust'):
        return None
    trust = probe.mapper_trust
    keys = {'vehicle', 'device', 'mapper_boot', 'mapper_instance', 'clock_epoch', 'map_epoch', 'frame_epoch',
        'odom_epoch', 'configuration', 'calibration', 'primitive'}
    if type(expected_source) is not dict or set(expected_source) != keys:
        raise MapperClockError('CLOCK_RETURNED_SOURCE_FIELDS')
    if any(type(value) is not str or not 1 <= len(value) <= 64 or not value.isascii() for value in expected_source.values()):
        raise MapperClockError('CLOCK_RETURNED_SOURCE_TYPES')
    actual = dict(device=probe.device, **{name: getattr(trust, name) for name in keys if name != 'device'})
    if (actual != expected_source or (trust.vehicle, trust.configuration, trust.calibration, trust.primitive) !=
            (policy.vehicle, policy.configuration, policy.calibration, policy.primitive)):
        raise MapperClockError('CLOCK_RETURNED_SOURCE')
    for name in ('mapper_boot', 'mapper_instance', 'clock_epoch', 'map_epoch', 'frame_epoch', 'odom_epoch'):
        canonical_uuid(getattr(trust, name))
    for name in ('mapper_boot', 'mapper_instance', 'clock_epoch'):
        field = 'expected_' + name
        if not probe.HasField(field) or getattr(probe, field) != getattr(trust, name):
            raise MapperClockError('CLOCK_RETURNED_EXPECTED_PEER')
    basis = trust.expires_pi_us - policy.lease_us
    if (basis <= 0 or not basis <= probe.pi_send_us < trust.expires_pi_us
            or trust.lower_offset_us > trust.upper_offset_us
            or trust.upper_offset_us - trust.lower_offset_us > min(policy.max_offset_width_us, trust.max_error_us)
            or not probe.pi_send_us <= now_j_us + trust.upper_offset_us < trust.expires_pi_us):
        raise MapperClockError('CLOCK_RETURNED_WINDOW')
    return ClockLease(encode(trust), probe.admission_revision, None, None, probe.pi_send_us, basis,
        max(0, basis - policy.lookback_us), trust.expires_pi_us, policy.max_offset_width_us, raw, policy.lookback_us, policy.lease_us)


class MapperClockOwner:
    """Gateway actor 전용. 원 Vehicle/plan 하나와 마지막 source/interval만 유지한다."""

    def __init__(self, policy, host_boot):
        if type(policy) is not ClockPolicy:
            raise MapperClockError('CLOCK_POLICY')
        self.policy, self.host_boot = policy, canonical_uuid(host_boot)
        self.pending = None
        self.vehicle = None
        self.sequence = 0
        self.last_plan_us = None
        self.source_revision = 1
        self.time_lease_revision = 0
        self._previous = None
        self._source = None
        self._blocked = None
        self._streak = 0
        self._lease = None

    def set_vehicle(self, raw, now_us):
        sample = decode(raw, schema.VehicleSample)
        p = self.policy
        if (sample.vehicle != p.vehicle or sample.host_boot != self.host_boot or sample.calibration != p.calibration
                or sample.quality & 3 != 3 or not sample.pi_sample_us <= now_us
                or now_us - sample.pi_sample_us + sample.clock_error_us > p.vehicle_max_age_us):
            raise MapperClockError('CLOCK_VEHICLE_CONTEXT')
        canonical_uuid(sample.device_session)
        canonical_uuid(sample.odom_epoch)
        if sample.HasField('pi_session'):
            canonical_uuid(sample.pi_session)
        if self.vehicle is not None and (self.vehicle.device_session, self.vehicle.odom_epoch) == (sample.device_session, sample.odom_epoch):
            if sample.sequence < self.vehicle.sequence or sample.sequence == self.vehicle.sequence and encode(sample) != encode(self.vehicle):
                raise MapperClockError('CLOCK_VEHICLE_REGRESSION')
        if self.vehicle is not None and self.vehicle.odom_epoch != sample.odom_epoch:
            self._lease = self._previous = None
            self._streak = 0
        self.vehicle = sample

    def trust(self, now_us):
        if self._lease is None:
            return None
        value = decode(self._lease.trust_bytes, schema.WorldTrust)
        return value if now_us < value.expires_pi_us else None

    def plan(self, now_us, pi_client_instance):
        integer(now_us, 1)
        if self.pending is not None:
            if now_us < self.pending.expires_us:
                return None
            self.pending = None
            self._streak = 0
        if self.last_plan_us is not None and now_us - self.last_plan_us < PERIOD_US:
            return None
        if (self.vehicle is None or now_us < self.vehicle.pi_sample_us or
                now_us - self.vehicle.pi_sample_us + self.vehicle.clock_error_us > self.policy.vehicle_max_age_us):
            return None
        self.sequence = integer(self.sequence + 1, 1)
        current = self.trust(now_us)
        expected = None if current is None else (current.mapper_boot, current.mapper_instance, current.clock_epoch)
        self.pending = ProbePlan(str(uuid4()), now_us, integer(now_us + TIMEOUT_US), self.policy.vehicle,
            self.policy.device, self.host_boot, canonical_uuid(pi_client_instance), self.sequence, os.urandom(16),
            self.source_revision, expected, None if current is None else self._lease.trust_bytes)
        self.last_plan_us = now_us
        return self.pending

    def accept(self, plan, body, now_us):
        if plan is not self.pending:
            raise MapperClockError('CLOCK_NOT_ORIGINAL_PLAN')
        try:
            return self._accept(plan, body, now_us)
        except ValueError:
            self._streak = 0
            raise
        finally:
            self.pending = None

    def _accept(self, plan, body, now_us):
        integer(now_us, 1)
        raw_probe, raw_reply, p4 = parse_candidate(body)
        probe, reply = decode(raw_probe, schema.MapperClockProbe), decode(raw_reply, schema.MapperClockReply)
        p = self.policy
        if (not plan.issued_us <= probe.pi_send_us <= p4 <= now_us < plan.expires_us
                or plan.admission_revision != self.source_revision
                or (probe.vehicle, probe.device, probe.pi_host_boot, probe.pi_client_instance, probe.probe_seq, probe.nonce,
                    probe.admission_revision) != (p.vehicle, p.device, self.host_boot, plan.pi_client_instance,
                    plan.probe_seq, plan.nonce, plan.admission_revision)):
            raise MapperClockError('CLOCK_ORIGINAL_PLAN')
        expected = tuple(getattr(probe, name) if probe.HasField(name) else None for name in
            ('expected_mapper_boot', 'expected_mapper_instance', 'expected_clock_epoch'))
        if expected != ((None, None, None) if plan.expected is None else plan.expected):
            raise MapperClockError('CLOCK_EXPECTED_CHANGED')
        if probe.HasField('mapper_trust') != (plan.prior_trust_bytes is not None) or (
                plan.prior_trust_bytes is not None and encode(probe.mapper_trust) != plan.prior_trust_bytes):
            raise MapperClockError('CLOCK_PRIOR_TRUST_CHANGED')
        if ((reply.pi_host_boot, reply.pi_client_instance, reply.probe_seq, reply.nonce, reply.pi_send_us, reply.device)
                != (self.host_boot, plan.pi_client_instance, plan.probe_seq, plan.nonce, probe.pi_send_us, p.device)
                or (reply.configuration, reply.calibration, reply.primitive) != (p.configuration, p.calibration, p.primitive)
                or self.vehicle is None or reply.odom_epoch != self.vehicle.odom_epoch
                or reply.j2 > reply.j3):
            raise MapperClockError('CLOCK_REPLY_CONTEXT')
        source = tuple(getattr(reply, name) for name in ('mapper_boot', 'mapper_instance', 'clock_epoch',
            'map_epoch', 'frame_epoch', 'odom_epoch', 'configuration', 'calibration', 'primitive'))
        for value in source[:6]:
            canonical_uuid(value)
        if plan.expected is not None and source[:3] != plan.expected:
            raise MapperClockError('CLOCK_EXPECTED_REPLY')
        if source == self._blocked:
            raise MapperClockError('CLOCK_EPOCH_BLOCKED')
        drift = drift_pad(p.drift_ppm, p4 - probe.pi_send_us + max(p.lease_us, p.lookback_us) + 2 * p.timestamp_error_us)
        error = 2 * p.timestamp_error_us + drift
        lo, hi = probe.pi_send_us - reply.j2 - error, p4 - reply.j3 + error
        if lo > hi or hi - lo > p.max_offset_width_us or not -U63_MAX <= lo <= hi <= U63_MAX:
            raise MapperClockError('CLOCK_OFFSET_WIDTH')
        if self._source is not None and self._source != source and self._lease is not None:
            # 실제 다른 source를 보았으면 이전 자격은 먼저 철회한다. 새 source는 여전히 두 번 필요하다.
            self._lease = None
            self.source_revision = integer(self.source_revision + 1, 1)
        if self._previous is not None and self._previous[0] == source:
            _, previous_lo, previous_hi, previous_p4 = self._previous
            growth = drift_pad(p.drift_ppm, p4 - previous_p4)
            if p4 < previous_p4 or max(lo, previous_lo - growth) > min(hi, previous_hi + growth):
                self._blocked, self._lease, self._streak = source, None, 0
                self.source_revision = integer(self.source_revision + 1, 1)
                raise MapperClockError('CLOCK_DISCONTINUITY')
            self._streak = min(2, self._streak + 1)
        else:
            self._streak = 1
        self._previous = source, lo, hi, p4
        if self._streak < 2:
            return None
        self._source = source
        self.time_lease_revision = integer(self.time_lease_revision + 1, 1)
        trust = schema.WorldTrust(vehicle=p.vehicle, **dict(zip(
            ('mapper_boot', 'mapper_instance', 'clock_epoch', 'map_epoch', 'frame_epoch', 'odom_epoch',
             'configuration', 'calibration', 'primitive'), source)), lower_offset_us=lo, upper_offset_us=hi,
            expires_pi_us=integer(probe.pi_send_us + p.lease_us), max_error_us=p.max_offset_width_us)
        if trust.expires_pi_us <= now_us:
            raise MapperClockError('CLOCK_LEASE_ALREADY_EXPIRED')
        self._lease = ClockLease(encode(trust), self.source_revision, self.time_lease_revision, p4, probe.pi_send_us,
            probe.pi_send_us, max(0, probe.pi_send_us - p.lookback_us), trust.expires_pi_us,
            p.max_offset_width_us, raw_probe, p.lookback_us, p.lease_us)
        return self._lease
