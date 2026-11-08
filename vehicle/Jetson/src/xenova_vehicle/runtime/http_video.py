"""S-http 한 thread의 읽기 전용 JPEG 경로. 영상 외의 제어/상태 API는 없다.

원 immutable bytes를 latest1와 viewer2가 참조한다. stream EOF/교체/느린 client는
영상 참조만 회수하며 raw/NN/model/source 소유자에게 명령을 보내지 않는다.
"""

from dataclasses import dataclass, field
import ipaddress
import math
import selectors
import socket
import threading

from .jpeg_stream import JpegReceiver, JpegStreamError, monotonic_us, validate_scope, _u64


REQUEST_US = 250_000
WRITE_US = 250_000
# result03/10의 HTTP5Hz 배포 상한 초기값이다. 실물 계측이나 A encoder/USB 절감이 아니다.
FRAME_PERIOD_US = 200_000
REQUEST_CAP = 4096
VIEWERS = 2
JPEG_OWNED_CAP = 1024 * 1024
BOUNDARY = b'xenova-frame'


@dataclass(frozen=True)
class _Channel:
    endpoint: object
    scope: tuple | None


@dataclass
class _Client:
    endpoint: object
    request_deadline: int
    request: bytearray = field(default_factory=bytearray)
    kind: str | None = None
    parts: tuple = ()
    index: int = 0
    offset: int = 0
    frame: object = None
    deadline: int = 0
    next_frame: int = 0
    sequence: int = -1
    image_expiry: int = 0
    slot_index: int | None = None


class VideoHttpServer:
    """명시 address와 선택 채널을 단일 non-daemon thread로 소유한다.

    replace_channel은 socket 소유를 넘기는 latest1 명령이다. ACK나 준비 승인이
    아니며 같은 endpoint를 다른 thread에서 사용하면 안 된다. join은 종료를 꾸미지 않는다.
    """

    def __init__(self, host, port, clock=monotonic_us, *, video_socket=None, expected_scope=None):
        if (type(host) is not str or str(ipaddress.IPv4Address(host)) != host
                or type(port) is not int or not 0 <= port <= 65535 or not callable(clock)):
            raise ValueError('numeric IPv4 address, bounded port and clock required')
        if (video_socket is None) != (expected_scope is None):
            raise ValueError('video socket and scope must be supplied together')
        self.host, self.port, self.clock = host, port, clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name='S-http', daemon=False)
        self._started = False
        self._command = None
        self._failure = None
        self._address = None
        self._snapshot = dict(alive=False, clients=0, viewer_inflight=0, latest=False, ingress_bytes=0,
            owned_jpeg_bytes=0, video_available=False, video_failure=None, counts={})
        self._wake_read, self._wake_write = socket.socketpair()
        for endpoint in (self._wake_read, self._wake_write):
            endpoint.setblocking(False)
        if video_socket is not None:
            self.replace_channel(video_socket, expected_scope)

    @property
    def is_alive(self):
        return self._thread.is_alive()

    @property
    def failure(self):
        with self._lock:
            return self._failure

    @property
    def address(self):
        with self._lock:
            return self._address

    def snapshot(self):
        with self._lock:
            result = dict(self._snapshot)
            result['counts'] = dict(result['counts'])
        result['alive'] = self.is_alive
        return result

    def replace_channel(self, endpoint, scope):
        if endpoint is not None:
            if not isinstance(endpoint, socket.socket) or endpoint.type & 15 != socket.SOCK_STREAM:
                raise ValueError('owned JPEG stream socket required')
            scope = validate_scope(scope)
        elif scope is not None:
            raise ValueError('detached video channel requires null scope')
        command = _Channel(endpoint, scope)
        with self._lock:
            if (self._stop.is_set() or self._failure is not None
                    or self._started and not self._thread.is_alive()):
                raise RuntimeError('HTTP video server is stopping; caller retains new socket')
            old, self._command = self._command, command
        if old is not None and old.endpoint is not None and old.endpoint is not endpoint:
            old.endpoint.close()
        self._wake()

    def _wake(self):
        try:
            self._wake_write.send(b'x')
        except (BlockingIOError, OSError):
            pass

    def start(self):
        if self._started or self._stop.is_set():
            raise RuntimeError('HTTP video server starts once')
        self._started = True
        try:
            self._thread.start()
        except BaseException:
            self._started = False
            self.stop()
            raise

    def wait_ready(self, timeout=1.):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 5:
            raise ValueError('bounded startup wait required')
        return self._ready.wait(timeout) and self.address is not None and self.failure is None

    def stop(self):
        self._stop.set()
        self._wake()
        if not self._started or not self._thread.is_alive():
            with self._lock:
                command, self._command = self._command, None
            if command is not None and command.endpoint is not None:
                command.endpoint.close()
            self._wake_read.close()
            self._wake_write.close()

    def join(self, timeout=5.):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 5:
            raise ValueError('join timeout must be within5 seconds')
        if self._started:
            self._thread.join(timeout)
        return not self.is_alive

    def _clear_latest(self):
        if self._latest is not None:
            self._counts['latest_released'] += 1
        self._latest = None

    def _before_complete(self):
        held = {id(frame.payload): len(frame.payload) for frame in
            [self._latest, *(c.frame for c in self._clients.values())] if frame is not None}
        ingress = self._receiver.buffered_bytes
        before = sum(held.values()) + ingress
        self._clear_latest()
        frames = {id(c.frame.payload): len(c.frame.payload) for c in self._clients.values() if c.frame is not None}
        # bytearray→bytes가 잠깐 겹쳐도 viewer2+ingress2는 최대1MiB다.
        peak = max(before, sum(frames.values()) + 2 * ingress)
        if peak > JPEG_OWNED_CAP:
            raise RuntimeError('JPEG freeze ownership exceeded')
        self._counts['owned_peak'] = max(self._counts['owned_peak'], peak)

    def _receive(self):
        if self._receiver is None:
            return
        try:
            frame = self._receiver.step(before_complete=self._before_complete)
            if frame is not None:
                self._latest = frame
                self._counts['frames_received'] += 1
        except JpegStreamError as error:
            self._video_failure = str(error)[:256]
            self._counts['video_faults'] += 1
            self._drop_channel()

    def _close_client(self, client):
        if self._clients.pop(client.endpoint, None) is None:
            return
        try:
            self._selector.unregister(client.endpoint)
        except (KeyError, ValueError):
            pass
        client.endpoint.close()
        if client.frame is not None:
            self._counts['viewer_frames_released'] += 1
        client.frame, client.parts = None, ()
        self._counts['clients_closed'] += 1

    def _drop_channel(self):
        self._clear_latest()
        for client in tuple(self._clients.values()):
            self._close_client(client)
        if self._receiver is not None:
            try:
                # receiver는 fault 때 먼저 close한다. 그 전 원 fd로 selector key도 회수한다.
                self._selector.unregister(self._video_fd)
            except (KeyError, ValueError):
                pass
            self._receiver.close()
            self._receiver = None
            self._video_fd = None

    def _commands(self):
        with self._lock:
            command, self._command = self._command, None
        if command is None:
            return
        self._drop_channel()
        self._video_failure = None
        if command.endpoint is not None:
            try:
                self._receiver = JpegReceiver(command.endpoint, command.scope, self.clock)
                self._selector.register(command.endpoint, selectors.EVENT_READ, 'video')
                self._video_fd = command.endpoint.fileno()
            except BaseException:
                command.endpoint.close()
                self._receiver = None
                raise
        self._counts['channel_replaced'] += 1

    def _record(self):
        frames = {id(frame.payload): len(frame.payload) for frame in
            [self._latest, *(c.frame for c in self._clients.values())] if frame is not None}
        ingress = 0 if self._receiver is None else self._receiver.buffered_bytes
        owned = sum(frames.values()) + ingress
        if owned > JPEG_OWNED_CAP:
            raise RuntimeError('bounded JPEG ownership exceeded')
        self._counts['owned_peak'] = max(self._counts['owned_peak'], owned)
        snapshot = dict(clients=len(self._clients), viewer_inflight=sum(c.frame is not None for c in self._clients.values()),
            latest=self._latest is not None,
            ingress_bytes=ingress, owned_jpeg_bytes=owned, video_available=self._receiver is not None,
            video_failure=self._video_failure, counts=dict(self._counts))
        with self._lock:
            self._snapshot = snapshot

    def _accept(self):
        for _ in range(3):
            try:
                endpoint, _ = self._listener.accept()
            except BlockingIOError:
                return
            endpoint.setblocking(False)
            if len(self._clients) >= VIEWERS:
                try:
                    endpoint.send(b'HTTP/1.0 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
                except OSError:
                    pass
                endpoint.close()
                self._counts['busy_clients'] += 1
                continue
            client = _Client(endpoint, _u64(self.clock()) + REQUEST_US)
            self._clients[endpoint] = client
            self._selector.register(endpoint, selectors.EVENT_READ, client)

    def _events(self, client):
        self._selector.modify(client.endpoint, selectors.EVENT_READ |
            (selectors.EVENT_WRITE if client.parts else 0), client)

    def _error(self, client, status):
        client.kind = 'close'
        client.parts = ((b'HTTP/1.0 ' + status + b'\r\nConnection: close\r\nContent-Length: 0\r\n\r\n'),)
        client.deadline = client.request_deadline
        self._events(client)

    def _assign(self, client, now):
        frame = self._latest
        if frame is None or now >= frame.expires_j_us or frame.sequence <= client.sequence:
            return False
        slot = client.slot_index
        if slot is None:
            used = {c.slot_index for c in self._clients.values() if c is not client and c.slot_index is not None}
            slot = next((i for i in range(VIEWERS) if i not in used and now >= self._slot_next_start[i]), None)
            if slot is None:
                return False
        elif now < self._slot_next_start[slot]:
            return False
        # 원 write 시작 예약에 과금한다. close/재접속/채널 교체가 이 시각을 환불하지 않는다.
        self._slot_next_start[slot] = now + FRAME_PERIOD_US
        client.slot_index = slot
        client.frame = frame
        client.sequence = frame.sequence
        client.image_expiry = frame.expires_j_us
        client.deadline = min(frame.expires_j_us, now + WRITE_US)
        client.next_frame = self._slot_next_start[slot]
        length = str(len(frame.payload)).encode('ascii')
        if client.kind == 'snapshot':
            header = b'HTTP/1.0 200 OK\r\nCache-Control: no-store\r\nContent-Type: image/jpeg\r\nConnection: close\r\nContent-Length: ' + length + b'\r\n\r\n'
            client.parts = (header, frame.payload)
        else:
            first = b'' if client.request is None else (
                b'HTTP/1.0 200 OK\r\nConnection: close\r\nCache-Control: no-store\r\nContent-Type: multipart/x-mixed-replace; boundary=' + BOUNDARY + b'\r\n\r\n')
            part = b'--' + BOUNDARY + b'\r\nContent-Type: image/jpeg\r\nContent-Length: ' + length + b'\r\n\r\n'
            client.parts = (first, part, frame.payload, b'\r\n') if first else (part, frame.payload, b'\r\n')
        client.request = None
        client.index = client.offset = 0
        self._events(client)
        return True

    def _read_client(self, client):
        if client.kind is None and _u64(self.clock()) >= client.request_deadline:
            self._close_client(client)
            return
        if client.kind is not None:
            try:
                client.endpoint.recv(1)
            except BlockingIOError:
                return
            self._close_client(client)
            return
        try:
            raw = client.endpoint.recv(REQUEST_CAP + 1 - len(client.request))
        except BlockingIOError:
            return
        if not raw:
            self._close_client(client)
            return
        client.request.extend(raw)
        if len(client.request) > REQUEST_CAP:
            self._close_client(client)
            return
        if b'\r\n\r\n' not in client.request:
            return
        try:
            header = bytes(client.request).decode('ascii')
            lines = header.split('\r\n')
            method, path, version = lines[0].split(' ')
            if version not in ('HTTP/1.0', 'HTTP/1.1') or header.count('\r\n\r\n') != 1 or not header.endswith('\r\n\r\n'):
                raise ValueError('one HTTP request required')
            for line in lines[1:-2]:
                key, value = line.split(':', 1)
                if (not key or any(ord(c) < 33 or ord(c) > 126 for c in key)
                        or any(ord(c) < 32 and c != '\t' or ord(c) > 126 for c in value)
                        or key.lower() == 'transfer-encoding'
                        or key.lower() == 'content-length' and value.strip() != '0'):
                    raise ValueError('unsupported HTTP request body/header')
        except (ValueError, UnicodeError):
            self._error(client, b'400 Bad Request')
            return
        if method != 'GET':
            self._error(client, b'405 Method Not Allowed')
        elif path not in ('/camera/stream.mjpg', '/camera/snapshot.jpg'):
            self._error(client, b'404 Not Found')
        else:
            client.kind = 'stream' if path.endswith('.mjpg') else 'snapshot'
            if self._receiver is None or not self._assign(client, _u64(self.clock())):
                self._error(client, b'503 Service Unavailable')

    def _write_client(self, client):
        for _ in range(6):
            if _u64(self.clock()) >= client.deadline:
                self._close_client(client)
                return
            part = client.parts[client.index]
            try:
                count = client.endpoint.send(memoryview(part)[client.offset:client.offset + 65536])
            except BlockingIOError:
                return
            if count <= 0:
                self._close_client(client)
                return
            client.offset += count
            if client.offset == len(part):
                client.index += 1
                client.offset = 0
                if client.index == len(client.parts):
                    if _u64(self.clock()) >= client.deadline:
                        self._close_client(client)
                        return
                    client.parts = ()
                    if client.frame is not None:
                        self._counts['viewer_frames_released'] += 1
                        client.frame = None
                    if client.kind != 'stream':
                        self._close_client(client)
                    else:
                        self._events(client)
                    return

    def _run(self):
        from collections import Counter
        self._counts = Counter()
        self._clients = {}
        self._slot_next_start = [0, 0]
        self._latest = self._receiver = None
        self._video_fd = None
        self._video_failure = None
        self._selector = None
        self._listener = None
        try:
            self._selector = selectors.DefaultSelector()
            self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._listener.setblocking(False)
            self._listener.bind((self.host, self.port))
            self._listener.listen(VIEWERS)
            self._selector.register(self._listener, selectors.EVENT_READ, 'listener')
            self._selector.register(self._wake_read, selectors.EVENT_READ, 'wake')
            with self._lock:
                self._address = self._listener.getsockname()
            self._ready.set()
            while not self._stop.is_set():
                self._commands()
                self._receive()
                now = _u64(self.clock())
                if self._latest is not None and now >= self._latest.expires_j_us:
                    self._clear_latest()
                for client in tuple(self._clients.values()):
                    if ((client.kind is None and now >= client.request_deadline)
                            or client.parts and now >= client.deadline
                            or client.kind == 'stream' and now >= client.image_expiry):
                        self._close_client(client)
                    elif client.kind == 'stream' and not client.parts and now >= client.next_frame:
                        self._assign(client, now)
                for key, mask in self._selector.select(.02):
                    if key.data == 'wake':
                        try:
                            self._wake_read.recv(4096)
                        except BlockingIOError:
                            pass
                        self._commands()
                    elif key.data == 'listener':
                        self._accept()
                    elif key.data == 'video':
                        if self._receiver is None or key.fileobj is not self._receiver.endpoint:
                            continue
                        self._receive()
                    else:
                        client = key.data
                        if client.endpoint not in self._clients:
                            continue
                        try:
                            if mask & selectors.EVENT_READ:
                                self._read_client(client)
                            if client.endpoint in self._clients and mask & selectors.EVENT_WRITE and client.parts:
                                self._write_client(client)
                        except OSError:
                            self._close_client(client)
                self._record()
        except BaseException as error:
            with self._lock:
                self._failure = (type(error).__name__ + ': ' + str(error))[:256]
        finally:
            with self._lock:
                self._stop.set()
                command, self._command = self._command, None
            if command is not None and command.endpoint is not None:
                command.endpoint.close()
            self._drop_channel()
            if self._listener is not None:
                self._listener.close()
            if self._selector is not None:
                self._selector.close()
            self._wake_read.close()
            self._wake_write.close()
            self._record()
            self._ready.set()
