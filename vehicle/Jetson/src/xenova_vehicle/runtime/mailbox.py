"""단일 owner의 RX staging과 별도 TX pending1. 전송/peer/admission/기한/lease는 소유하지 않는다.

실행 adapter는 peer·sequence·typed payload admission 뒤 peer별 RX를 사용해야 한다.
구조 packet의 단위 enqueue는 자료구조 시험이며 실제 메시지 허가가 아니다.
"""

from collections import deque
from dataclasses import dataclass
from enum import Enum

from .local_envelope import StructuralPacket


class PutResult(Enum):
    ACCEPTED = 'ACCEPTED'
    REPLACED_HEARTBEAT = 'REPLACED_HEARTBEAT'
    BUSY_GENERAL = 'BUSY_GENERAL'
    BUSY_TERMINAL = 'BUSY_TERMINAL'

    @property
    def accepted(self):
        return self in (PutResult.ACCEPTED, PutResult.REPLACED_HEARTBEAT)

    def __bool__(self):
        return self.accepted


def _packet(value):
    if type(value) is not StructuralPacket:
        raise ValueError('mailbox requires a structurally checked packet')


class RxMailbox:
    """peer당 general8/latestHB1/terminal2. priority는 RX 처리순서이며 TX wire순서가 아니다.

    FAULT/STOP/STOPPED가 terminal이다. full이면 해당 class만 BUSY로 거절하고 다른
    slot을 침범하지 않는다. 중복/old sequence 거절은 이 staging 이전 admission 책임이다.
    """

    def __init__(self):
        self._general = deque(maxlen=8)
        self._terminal = deque(maxlen=2)
        self._heartbeat = None

    def put(self, packet):
        """접수 상태를 반환한다. 구조 오류/잘못된 타입은 포화 결과와 구분해 예외로 낸다."""
        _packet(packet)
        if packet.kind == 'HEARTBEAT':
            outcome = PutResult.ACCEPTED if self._heartbeat is None else PutResult.REPLACED_HEARTBEAT
            self._heartbeat = packet
            return outcome
        terminal = packet.kind in ('FAULT', 'STOP', 'STOPPED')
        target = self._terminal if terminal else self._general
        if len(target) == target.maxlen:
            return PutResult.BUSY_TERMINAL if terminal else PutResult.BUSY_GENERAL
        target.append(packet)
        return PutResult.ACCEPTED

    def pop(self):
        """terminal FIFO→general FIFO→latest heartbeat로 한 개 꺼낸다. 빈 경우 None이다."""
        if self._terminal:
            return self._terminal.popleft()
        if self._general:
            return self._general.popleft()
        packet, self._heartbeat = self._heartbeat, None
        return packet

    @property
    def counts(self):
        return {'general': len(self._general), 'heartbeat': int(self._heartbeat is not None),
                'terminal': len(self._terminal)}

    @property
    def held_bytes(self):
        return sum(len(p.raw_bytes) for p in (*self._general, *self._terminal)) + (
            len(self._heartbeat.raw_bytes) if self._heartbeat is not None else 0)


@dataclass(frozen=True)
class PendingTransmission:
    """한 접수의 local identity. 원 immutable packet이며 wire ACK/작업 완료 증거는 아니다."""

    packet: StructuralPacket

    def __post_init__(self):
        _packet(self.packet)

    @property
    def raw_bytes(self):
        return self.packet.raw_bytes


class PendingTx:
    """이미 encode한 packet1개를 보유한다. EAGAIN/priority/expiry로 바꾸거나 재발급하지 않는다."""

    def __init__(self):
        self._pending = None

    def accept(self, packet):
        """빈 경우만 접수한다. True는 접수이며 실제 송신 완료가 아니다."""
        _packet(packet)
        if self._pending is not None:
            return False
        self._pending = PendingTransmission(packet)
        return True

    def peek(self):
        """반복 관찰은 동일 handle/bytes를 돌려준다. 이 함수가 전송을 시도하지 않는다."""
        return self._pending

    def finish(self, expected_handle):
        """상위 owner의 실제 송신 완료 뒤 정확한 접수 handle만 해제한다. 과거 handle은 False다."""
        if self._pending is None or expected_handle is not self._pending:
            return False
        self._pending = None
        return True

    @property
    def held_bytes(self):
        return len(self._pending.raw_bytes) if self._pending is not None else 0
