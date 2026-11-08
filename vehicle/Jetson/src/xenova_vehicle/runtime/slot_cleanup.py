"""원 ACQUIRED에 연결한 RELEASE의 정적 진술. 실제 peer/반환/token/전송은 승인하지 않는다.

완료보고 C와 cleanup 전송기한은 원 read 사용기한과 분리한다. C를 local의 두
관측 시각으로 복제하지 않으며 실제 now/채널 순번·retired 수락은 actor가 맡는다.
"""

from dataclasses import dataclass
import re

from .local_envelope import StructuralPacket
from .raw_leases import ReadLease, RemoteReturnStatement
from .slot_messages import ReaderSlotContext, SlotMessage


U64_MAX = 2**64 - 1


class SlotCleanupError(ValueError):
    """원 ACQUIRED·동일 payload/방향 또는 cleanup의 원 시각·예산이 맞지 않는다."""


def _fail(message):
    raise SlotCleanupError(message)


def _exact(value, expected):
    if type(value) is not expected:
        _fail('expected exact ' + expected.__name__)


def _u64(value, *, positive=False):
    if type(value) is not int or not (1 if positive else 0) <= value <= U64_MAX:
        _fail('expected exact u64 integer')
    return value


def _decimal(value):
    if (type(value) is not str or not 1 <= len(value) <= 20
            or re.fullmatch('0|[1-9][0-9]*', value) is None):
        _fail('expected canonical u64 decimal string')
    return _u64(int(value))


@dataclass(frozen=True)
class CleanupTimeLimits:
    """독립 공급한 전송 예산. 실제 배포 숫자나 암묵 기본값은 제공하지 않는다."""

    transport_budget_us: int

    def __post_init__(self):
        _u64(self.transport_budget_us, positive=True)


@dataclass(frozen=True, init=False)
class ReleaseMessage:
    """원 packet과 최대4단계 parent를 보유한다. 사본/정적 record는 실반환 증명이 아니다."""

    packet: StructuralPacket
    parent: SlotMessage
    context: ReaderSlotContext
    limits: CleanupTimeLimits
    request_id: str
    issued_us: int
    expires_us: int
    record: RemoteReturnStatement

    def __init__(self, packet, original_acquired, *, limits):
        _exact(packet, StructuralPacket)
        _exact(original_acquired, SlotMessage)
        _exact(limits, CleanupTimeLimits)
        if original_acquired.op != 'ACQUIRED' or type(original_acquired.record) is not ReadLease:
            _fail('RELEASE requires the original exact ACQUIRED parent')
        fields = packet.fields
        original = original_acquired.fields
        for name in ('kind', 'binding_domain', 'service_instance', 'channel_epoch', 'binding_revision', 'request_id'):
            if type(fields[name]) is not type(original[name]) or fields[name] != original[name]:
                _fail('RELEASE differs from its original header')
        if fields['sender_instance'] != original_acquired.context.reader.worker_instance:
            _fail('RELEASE sender must be the original registered M')
        expected = original_acquired.payload
        expected['op'] = 'RELEASE'
        if fields['payload'] != expected:
            _fail('RELEASE must preserve the full original thirteen-field payload')
        # JSON bool과 int는 값이 같을 수 있으므로 원 필드의 exact 타입도 대조한다.
        if any(type(fields['payload'][name]) is not type(value) for name, value in expected.items()):
            _fail('RELEASE payload types differ from the original payload')
        completed = _decimal(fields['issued_j_us'])
        expires = _decimal(fields['expires_j_us'])
        end = _u64(completed + limits.transport_budget_us)
        if expires != end:
            _fail('cleanup expiry must equal original report plus explicit transport budget')
        try:
            statement = RemoteReturnStatement(original_acquired.record, completed)
        except ValueError as error:
            raise SlotCleanupError('invalid remote completion statement') from error
        for name, value in (('packet', packet), ('parent', original_acquired), ('context', original_acquired.context),
                            ('limits', limits), ('request_id', fields['request_id']), ('issued_us', completed),
                            ('expires_us', expires), ('record', statement)):
            object.__setattr__(self, name, value)

    @property
    def raw(self):
        return self.packet.raw_bytes

    @property
    def fields(self):
        return self.packet.fields

    @property
    def payload(self):
        return self.packet.payload


def parse_release_message(packet, original_acquired, *, limits):
    return ReleaseMessage(packet, original_acquired, limits=limits)
