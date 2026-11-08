"""keyboard-source-1의 유한 SOCK_SEQPACKET I/O. driver/STOP 권위는 actor가 소유한다.

고정 I/O thread1, connection2, peer별 ordinary1/FENCE1 원 요청과 TX4만 보유한다.
actor.tick을 호출하거나 Gateway/키보드의 계산·clock·serial을 대신하지 않는다.
"""
from dataclasses import dataclass, field
import errno
import os
from pathlib import Path
import queue
import selectors
import socket
import stat
import struct
import threading
import time
from uuid import uuid4

from . import keyboard_contract as contract
from .authority_journal import canonical

REQUESTS = frozenset(('HELLO', 'ATTACH', 'FRAME', 'ARM', 'FENCE', 'DETACH'))


@dataclass(frozen=True, slots=True)
class KeyboardPeer:
    pid: int
    uid: int
    gid: int
    connection_id: str


@dataclass(slots=True)
class _Connection:
    stream: object
    peer: KeyboardPeer
    pending: dict = field(default_factory=dict)
    outgoing: list = field(default_factory=list)


class KeyboardServer:
    """actor.submit(packet,peer)→Queue1, lost(peer), take_outgoing()만 사용한다."""
    def __init__(self, actor, lease, endpoint=contract.ENDPOINT, *, uid=0):
        self.actor, self.lease, self.path, self.uid = actor, lease, Path(endpoint), uid
        self.thread = self.listener = self.identity = None
        self.stop = threading.Event()
        self.failure = None
        self.stats = dict(accepted=0, denied=0, closed=0, received=0, sent=0, malformed=0, busy=0)

    def _unlink(self):
        if self.identity is None:
            return
        try:
            current = self.path.lstat()
            if stat.S_ISSOCK(current.st_mode) and (current.st_dev, current.st_ino) == self.identity:
                self.path.unlink()
        except FileNotFoundError:
            pass

    def start(self):
        if self.thread is not None or self.lease.fd is None:
            raise RuntimeError('active single Gateway lease required')
        parent = self.path.parent
        if not self.path.is_absolute() or parent.resolve() != parent:
            raise ValueError('canonical keyboard endpoint required')
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != self.uid or info.st_mode & 0o077:
            raise ValueError('private owned keyboard directory required')
        try:
            old = self.path.lstat()
        except FileNotFoundError:
            old = None
        if old is not None:
            if not stat.S_ISSOCK(old.st_mode) or old.st_uid != self.uid or old.st_nlink != 1:
                raise ValueError('foreign keyboard endpoint; no removal')
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as probe:
                probe.settimeout(.02)
                try:
                    probe.connect(str(self.path))
                except OSError as error:
                    if error.errno != errno.ECONNREFUSED:
                        raise ValueError('keyboard endpoint ownership unresolved') from error
                else:
                    raise ValueError('live keyboard endpoint; no removal')
            current = self.path.lstat()
            if (current.st_dev, current.st_ino) != (old.st_dev, old.st_ino):
                raise ValueError('keyboard endpoint replaced')
            self.path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            listener.bind(str(self.path))
            os.chmod(self.path, 0o600)
            info = self.path.lstat()
            self.identity = (info.st_dev, info.st_ino)
            listener.listen(2)
            listener.setblocking(False)
            self.listener = listener
            self.thread = threading.Thread(target=self._entry, name='gateway-keyboard-io', daemon=True)
            self.thread.start()
        except BaseException:
            listener.close()
            self._unlink()
            raise
        return self

    def _entry(self):
        try:
            self._loop()
        except BaseException as error:
            self.failure = self.failure or error
        finally:
            try:
                self.listener.close()
            except OSError as error:
                self.failure = self.failure or error

    def _loop(self):
        connections = {}
        with selectors.DefaultSelector() as selector:
            selector.register(self.listener, selectors.EVENT_READ, None)

            def close(peer):
                state = connections.pop(peer, None)
                if state is None:
                    return
                try:
                    selector.unregister(state.stream)
                finally:
                    state.stream.close()
                    self.stats['closed'] += 1
                    self.actor.lost(peer)

            def output(peer, packet):
                state = connections.get(peer)
                if state is None:
                    return
                try:
                    contract.validate(packet)
                    raw = canonical(packet)
                    sequence = int(packet['sequence'])
                    for held_sequence, held in state.outgoing:
                        if sequence == held_sequence:
                            if raw != held:
                                raise ValueError('conflicting keyboard response')
                            return
                    # normal reply, FENCE_ACK, STOP_READY, generation STATE may
                    # coexist in one normal handover/release actor iteration.
                    if len(state.outgoing) >= 4:
                        raise ValueError('bounded keyboard response staging full')
                    state.outgoing.append((sequence, raw))
                    state.outgoing.sort(key=lambda value: value[0])
                    selector.modify(state.stream, selectors.EVENT_READ | selectors.EVENT_WRITE, peer)
                except (ValueError, TypeError, KeyError):
                    self.stats['malformed'] += 1
                    close(peer)

            def results():
                for peer, state in tuple(connections.items()):
                    for lane, (raw, result, expiry) in tuple(state.pending.items()):
                        try:
                            packet = result.get_nowait()
                        except queue.Empty:
                            if time.monotonic_ns()//1000 >= expiry:
                                close(peer)
                                break
                        else:
                            del state.pending[lane]
                            output(peer, packet)

            try:
                while not self.stop.is_set():
                    # All response seqs are issued by one actor. Re-poll Queue1
                    # after asynchronous output: an older original response
                    # cannot be left behind an already-created later packet.
                    results()
                    for _ in range(8):
                        item = self.actor.take_outgoing()
                        if item is None:
                            break
                        output(*item)
                    results()
                    for key, mask in selector.select(.01):
                        if self.stop.is_set():
                            break
                        peer = key.data
                        if peer is None:
                            for _ in range(3):
                                try:
                                    stream, _ = self.listener.accept()
                                except BlockingIOError:
                                    break
                                try:
                                    pid, uid, gid = struct.unpack('3i', stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                                    if uid != self.uid or pid <= 0:
                                        self.stats['denied'] += 1
                                        stream.close()
                                        continue
                                    if len(connections) >= 2:
                                        self.stats['busy'] += 1
                                        stream.close()
                                        continue
                                    stream.setblocking(False)
                                    peer = KeyboardPeer(pid, uid, gid, str(uuid4()))
                                    connections[peer] = _Connection(stream, peer)
                                    selector.register(stream, selectors.EVENT_READ, peer)
                                    self.stats['accepted'] += 1
                                except BaseException:
                                    stream.close()
                                    raise
                            continue
                        state = connections.get(peer)
                        if state is None:
                            continue
                        if mask & selectors.EVENT_READ:
                            for _ in range(2):
                                try:
                                    raw, ancillary, flags, _ = state.stream.recvmsg(2048, 0, socket.MSG_DONTWAIT)
                                    if not raw:
                                        close(peer)
                                        break
                                    if ancillary or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                                        raise ValueError('partial keyboard packet')
                                    packet = contract.decode(raw)
                                    if packet['kind'] not in REQUESTS:
                                        raise ValueError('keyboard request kind required')
                                    lane = 'fence' if packet['kind'] in ('FENCE', 'DETACH') else 'ordinary'
                                    if lane in state.pending:
                                        if raw == state.pending[lane][0]:
                                            continue
                                        raise ValueError('one original keyboard request per lane')
                                    response = self.actor.submit(packet, peer)
                                    state.pending[lane] = (raw, response, int(packet['expires_pi_us']))
                                    self.stats['received'] += 1
                                except BlockingIOError:
                                    break
                                except queue.Full:
                                    self.stats['busy'] += 1
                                    close(peer)
                                    break
                                except (OSError, ValueError, TypeError):
                                    self.stats['malformed'] += 1
                                    close(peer)
                                    break
                        state = connections.get(peer)
                        if state is not None and mask & selectors.EVENT_WRITE and state.outgoing:
                            try:
                                _, raw = state.outgoing[0]
                                if state.stream.send(raw) != len(raw):
                                    raise ValueError('partial seqpacket write')
                                state.outgoing.pop(0)
                                self.stats['sent'] += 1
                                if not state.outgoing:
                                    selector.modify(state.stream, selectors.EVENT_READ, peer)
                            except BlockingIOError:
                                pass  # The same immutable packet remains in the slot.
                            except (OSError, ValueError):
                                close(peer)
            except BaseException as error:
                self.failure = error
            finally:
                for peer in tuple(connections):
                    try:
                        close(peer)
                    except BaseException as error:
                        self.failure = self.failure or error
                self.listener.close()

    def close(self, timeout=1.0):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout)
            if self.thread.is_alive():
                raise RuntimeError('keyboard I/O did not quiesce')
        self._unlink()
        if self.failure is not None:
            raise RuntimeError('keyboard I/O failed') from self.failure
