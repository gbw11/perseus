"""RAW write pause/restart의 작은 control 진술과 실제 복구 사실 경계.

PAUSED는 앞선 write/FILLED 실송신 완료 뒤에만 A가 보고한다. codec은 원 packet과
parent를 검사할 뿐 실제 send·worker exit·token 정규화를 만들어 내지 않는다.
"""

from dataclasses import dataclass
from uuid import UUID

from .local_envelope import StructuralPacket
from .raw_binding_messages import RawBindMessage
from .raw_leases import RawOwnerExitBarrier, RawOwnerExitEvidence, RawRecoveryBarrier, RawRecoveryEvidence


class RawControlError(ValueError):
    """현재 A 문맥/원 pause parent/독립 resume 기한이 맞지 않는다."""


def _u64(value):
    if type(value) is not int or not 0 <= value < 2**64:
        raise RawControlError('expected exact monotonic u64 microseconds')
    return value


def _decimal(value):
    if (type(value) is not str or not 1 <= len(value) <= 20 or not value.isascii()
            or not value.isdigit() or str(int(value)) != value):
        raise RawControlError('expected canonical u64 decimal string')
    return _u64(int(value))


@dataclass(frozen=True, init=False)
class RawControlMessage:
    """PAUSE→PAUSED→별도 원 TTL의 RESUME. expiry는 pause 상태의 자동 해제가 아니다."""

    packet: StructuralPacket
    writer_bind: RawBindMessage
    parent: object
    op: str
    request_id: str
    pause_request_id: str
    issued_us: int
    expires_us: int

    def __init__(self, packet, writer_bind, parent=None):
        if type(packet) is not StructuralPacket or type(writer_bind) is not RawBindMessage:
            raise RawControlError('expected original structural control and A binding')
        peer = writer_bind.context.peer
        fields, payload = packet.fields, packet.payload
        if (peer.role != 'A' or type(payload) is not dict
                or set(payload) != {'op', 'raw_binding_sha256', 'pause_request_id'}):
            raise RawControlError('expected exact registered A control payload')
        op = payload['op']
        if type(op) is not str or op not in ('PAUSE', 'PAUSED', 'RESUME'):
            raise RawControlError('unsupported raw recovery control')
        expected = dict(kind='RAW_CONTROL', binding_domain='RAW', service_instance=peer.service_instance,
            channel_epoch=peer.channel_epoch, binding_revision=str(writer_bind.binding_revision),
            sender_instance=peer.worker_instance if op == 'PAUSED' else peer.service_instance)
        if any(type(fields[name]) is not str or fields[name] != value for name, value in expected.items()):
            raise RawControlError('raw recovery control differs from the original A channel/binding')
        pause_id = payload['pause_request_id']
        try:
            valid_id = type(pause_id) is str and str(UUID(pause_id)) == pause_id
        except ValueError:
            valid_id = False
        if not valid_id:
            raise RawControlError('expected canonical original pause UUID')
        if payload['raw_binding_sha256'] != writer_bind.binding.sha256:
            raise RawControlError('raw recovery control changed its binding')
        issued, expires = (_decimal(fields[name]) for name in ('issued_j_us', 'expires_j_us'))
        if issued >= expires:
            raise RawControlError('control needs a nonempty original lifetime')
        if op == 'PAUSE':
            if parent is not None or pause_id != fields['request_id']:
                raise RawControlError('PAUSE must own its original request UUID')
        else:
            if (type(parent) is not RawControlMessage or parent.writer_bind.raw != writer_bind.raw
                    or parent.pause_request_id != pause_id or parent.op != ('PAUSE' if op == 'PAUSED' else 'PAUSED')):
                raise RawControlError('control lacks its original exact pause parent')
            if op == 'PAUSED':
                if fields['request_id'] != parent.request_id or not parent.issued_us <= issued < expires <= parent.expires_us:
                    raise RawControlError('PAUSED must remain inside the original PAUSE request')
            elif fields['request_id'] == pause_id or issued < parent.issued_us:
                raise RawControlError('RESUME needs a distinct new command and the original PAUSED parent')
        for name, value in (('packet', packet), ('writer_bind', writer_bind), ('parent', parent), ('op', op),
                            ('request_id', fields['request_id']), ('pause_request_id', pause_id),
                            ('issued_us', issued), ('expires_us', expires)):
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

    def check_live(self, now_us):
        _u64(now_us)
        if not self.issued_us <= now_us < self.expires_us:
            raise RawControlError('control is outside its original half-open lifetime')


def control_payload(op, writer_bind, pause_request_id):
    return dict(op=op, raw_binding_sha256=writer_bind.binding.sha256, pause_request_id=pause_request_id)
