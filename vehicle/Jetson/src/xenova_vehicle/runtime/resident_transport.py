"""상주 worker의 유한 raw control 통로. SDK 객체·큰 payload·권위를 만들지 않는다.

Linux seqpacket의 원 bytes를 기존 envelope parser에 넘긴다. 메시지별 binding,
lease 및 원 기한의 최종 수락은 각 actor가 담당한다. 이 통로는 sequenced pending
하나만 소유한다. actor의 유한 draft/mailbox는 순번 발급 전에 우선순위를 선택한다.
"""

from collections import Counter, deque
from dataclasses import dataclass
import socket
import time
import uuid

from .lifecycle_messages import LifecyclePeer
from .local_envelope import MAX_PACKET_BYTES as MAX_PACKET, StructuralPacket, decode_structure, encode_structure


def j_us():
    """같은 host의 원 monotonic microsecond. 수신 시각으로 producer 시각을 바꾸지 않는다."""
    return time.monotonic_ns() // 1000


class TransportError(ValueError):
    """등록 peer·원 packet·유한 통로의 계약 위반."""


@dataclass(frozen=True)
class SentPacket:
    packet: StructuralPacket
    completed_us: int
    started_us: int


class ResidentLink:
    """등록된 socket 하나의 단일 owner. control actor와 worker가 각각 자기 끝을 소유한다."""

    def __init__(self, endpoint, peer, owner_role, *, track_sent=False):
        if type(peer) is not LifecyclePeer or owner_role not in ('S', peer.role):
            raise TransportError('expected registered lifecycle peer and local role')
        if endpoint.family != socket.AF_UNIX or endpoint.type != getattr(socket, 'SOCK_SEQPACKET', -1):
            raise TransportError('resident control requires Linux AF_UNIX SOCK_SEQPACKET')
        endpoint.setblocking(False)
        self.endpoint, self.peer, self.owner_role = endpoint, peer, owner_role
        self._pending = None
        self._next_sequence = 1
        self._highwater, self._last_raw = 0, None
        self._track_sent = track_sent
        self._sent = deque()
        self.counts = Counter()
        self.closed = False

    @staticmethod
    def _cleanup(packet):
        return packet.kind == 'STOPPED' or packet.kind == 'SLOT' and packet.payload.get('op') == 'RELEASE'

    def send(self, kind, payload, *, domain='LIFECYCLE', revision=0, request_id=None,
             issued_us=None, expires_us=None):
        """선택된 pending에만 순번을 부여한다. busy None은 새 draft의 수락이 아니다."""
        if self.closed:
            raise EOFError('control endpoint is closed')
        self.flush()
        if self._pending is not None:
            self.counts['busy_no_effect'] += 1
            return None
        if self._next_sequence > 2**64 - 1:
            raise TransportError('channel sequence exhausted')
        now = j_us()
        issued_us = now if issued_us is None else issued_us
        expires_us = issued_us + 5_000_000 if expires_us is None else expires_us
        packet = encode_structure(dict(version=1, service_instance=self.peer.service_instance,
            channel_epoch=self.peer.channel_epoch,
            sender_instance=self.peer.service_instance if self.owner_role == 'S' else self.peer.worker_instance,
            message_sequence=str(self._next_sequence), kind=kind, binding_domain=domain,
            binding_revision=str(revision), request_id=str(uuid.uuid4()) if request_id is None else request_id,
            issued_j_us=str(issued_us), expires_j_us=str(expires_us), payload=payload))
        self._next_sequence += 1
        self._pending = packet
        self.flush()
        return packet

    def flush(self):
        """pending 하나를 시도한다. EAGAIN은 원 bytes·sequence·기한을 보존한다."""
        if self.closed or self._pending is None:
            return
        packet = self._pending
        if not self._cleanup(packet) and j_us() >= int(packet.fields['expires_j_us']):
            # 원 grant를 조용히 버리면 actor의 예약과 실제 송신 사실이 갈라진다.
            # packet을 보존한 채 통로를 실패시켜 actor가 fence/정리를 수행한다.
            raise TransportError('original pending packet expired before actual send')
        if self._track_sent and len(self._sent) >= 32:
            raise TransportError('owner must consume actual send completions')
        started = j_us()
        try:
            size = self.endpoint.send(packet.raw_bytes)
        except BlockingIOError:
            return
        if size != len(packet.raw_bytes):
            raise TransportError('partial seqpacket send')
        self._pending = None
        self.counts['sent'] += 1
        if self._track_sent:
            self._sent.append(SentPacket(packet, j_us(), started))

    def take_sent(self):
        """S가 원 BIND/STOP의 실제 최초 송신을 수락한다. 호출 자체가 기한을 갱신하지 않는다."""
        result = tuple(self._sent)
        self._sent.clear()
        return result

    def poll(self):
        """회차당 최대16개 원 packet. sender/channel 검사 뒤 domain actor가 수명을 판단한다."""
        result = []
        for _ in range(16):
            try:
                raw, _, flags, _ = self.endpoint.recvmsg(MAX_PACKET + 1)
            except BlockingIOError:
                break
            if not raw:
                if result:
                    break
                raise EOFError('registered control peer closed')
            if flags & socket.MSG_TRUNC or len(raw) > MAX_PACKET:
                raise TransportError('truncated or oversized control packet')
            packet = decode_structure(raw)
            fields = packet.fields
            sender = self.peer.worker_instance if self.owner_role == 'S' else self.peer.service_instance
            if (fields['service_instance'], fields['channel_epoch'], fields['sender_instance']) != (
                    self.peer.service_instance, self.peer.channel_epoch, sender):
                raise TransportError('control source differs from registered peer')
            sequence = int(fields['message_sequence'])
            if sequence <= self._highwater:
                if sequence == self._highwater and raw == self._last_raw:
                    self.counts['duplicate_no_effect'] += 1
                    continue
                raise TransportError('control sequence regressed or changed original bytes')
            self._highwater, self._last_raw = sequence, raw
            self.counts['received'] += 1
            result.append(packet)
        return result

    def close(self):
        if not self.closed:
            self.closed = True
            self.endpoint.close()
            self._sent.clear()
            self._pending = None

    def abort(self):
        """실패 통로만 닫고 미송신 원 packet을 보존한다. lease/token을 되돌리지 않는다."""
        if not self.closed:
            self.closed = True
            self.endpoint.close()
        return self._pending
