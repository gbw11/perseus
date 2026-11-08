"""최대4KiB의 비차단 제어 사본. raw payload·SDK 객체·pickle은 보내지 않는다."""

import json
import socket
import struct
import sys

MAX_PACKET = 4096


def control_pair() -> tuple[socket.socket, socket.socket]:
    """Linux는 seqpacket, 로컬 Windows 검증은 길이 framing을 쓴다."""
    kind = socket.SOCK_SEQPACKET if sys.platform == "linux" else socket.SOCK_STREAM
    pair = socket.socketpair(type=kind)
    try:
        for endpoint in pair:
            endpoint.setblocking(False)
    except BaseException:
        for endpoint in pair:
            try:
                endpoint.close()
            except OSError:
                pass
        raise
    return pair


class ControlChannel:
    def __init__(self, endpoint: socket.socket):
        self.endpoint = endpoint
        self.packet_mode = endpoint.type == getattr(socket, "SOCK_SEQPACKET", -1)
        self.pending = b""
        self.incoming = bytearray()

    def send(self, value: dict) -> bool:
        """송신 중packet1개만 보유한다. 상태 drop이 원 작업 기한을 연장하지 않는다."""
        data = json.dumps(value, ensure_ascii=True, allow_nan=False,
                          separators=(",", ":")).encode("ascii")
        if len(data) > MAX_PACKET:
            raise ValueError("control packet exceeds 4096 bytes")
        self.flush()
        if self.pending:
            return False
        self.pending = data if self.packet_mode else struct.pack("<I", len(data)) + data
        self.flush()
        return not self.pending

    def flush(self) -> None:
        if not self.pending:
            return
        try:
            count = self.endpoint.send(self.pending)
        except BlockingIOError:
            return
        if self.packet_mode and count != len(self.pending):
            raise ValueError("partial seqpacket send")
        self.pending = self.pending[count:]

    @staticmethod
    def _decode(data: bytes) -> dict:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("control message must be an object")
        return value

    def receive(self) -> list[dict]:
        """회차당 최대16packet. EOF는 부모/자식 소유자의 소실로 처리한다."""
        result = []
        for _ in range(16):
            if not self.packet_mode:
                while len(self.incoming) >= 4 and len(result) < 16:
                    length = struct.unpack_from("<I", self.incoming)[0]
                    if not 0 < length <= MAX_PACKET:
                        raise ValueError("invalid control frame length")
                    if len(self.incoming) < length + 4:
                        break
                    result.append(self._decode(bytes(self.incoming[4:4 + length])))
                    del self.incoming[:4 + length]
                if len(result) == 16:
                    break
            try:
                data = self.endpoint.recv(MAX_PACKET + (1 if self.packet_mode else 4))
            except BlockingIOError:
                break
            except ConnectionResetError as exc:
                # Windows의 로컬 stream 종료도 RST일 수 있다. 이미 받은 final은 보존한다.
                if result:
                    break
                raise EOFError("control peer reset") from exc
            if not data:
                if result:
                    break
                raise EOFError("control peer closed")
            if self.packet_mode:
                if len(data) > MAX_PACKET:
                    raise ValueError("oversized control packet")
                result.append(self._decode(data))
                continue
            self.incoming.extend(data)
            if len(self.incoming) > 2 * (MAX_PACKET + 4):
                raise ValueError("control buffer exceeds its bound")
        return result

    def close(self) -> None:
        self.endpoint.close()
