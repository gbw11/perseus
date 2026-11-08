"""한 Gateway lease 안의 bounded UDS I/O. Authority·serial은 actor만 변경한다."""
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

from .ipc_contract import ENDPOINT, HEADER, MAX_BYTES, VERSION, decode, encode, request

CONNECTION_LIMIT = 16
IO_BUDGET_S = 1.0


class GatewayServer:
    def __init__(self, runner, lease, endpoint=ENDPOINT, *, uid=0, contract=None,submit=None,connection_limit=CONNECTION_LIMIT):
        self.runner, self.lease, self.path, self.uid = runner, lease, Path(endpoint), uid
        self.stop = threading.Event()
        self.thread = None
        self.identity = None
        self.stats = dict(accepted=0, malformed=0, busy=0, expired=0, high_water=0,
                          decoded=0, encoded=0, peer_denied=0)
        self.failure = None
        self.contract=contract;self.submit=runner.submit_provider if submit is None else submit
        self.connection_limit=connection_limit

    def _error(self,code):
        return self.contract.error(code) if self.contract else dict(version=VERSION,ok=False,error={'code':code})

    def start(self):
        if self.thread is not None or self.lease.fd is None:
            raise RuntimeError('active single Gateway lease required')
        parent = self.path.parent
        if not self.path.is_absolute() or parent.resolve() != parent:
            raise ValueError('canonical endpoint required')
        try: parent.mkdir(mode=0o700)
        except FileExistsError: pass
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != self.uid or info.st_mode & 0o077:
            raise ValueError('private owned runtime directory required')
        try: old = self.path.lstat()
        except FileNotFoundError: old = None
        if old is not None:
            if not stat.S_ISSOCK(old.st_mode) or old.st_uid != self.uid:
                raise ValueError('foreign endpoint; no removal')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(.02)
                try: probe.connect(str(self.path))
                except OSError as exc:
                    if exc.errno != errno.ECONNREFUSED:
                        raise ValueError('endpoint ownership unresolved') from exc
                else: raise ValueError('live endpoint; no removal')
            current = self.path.lstat()
            if (current.st_dev, current.st_ino) != (old.st_dev, old.st_ino):
                raise ValueError('endpoint replaced')
            self.path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.path)); os.chmod(self.path, 0o600)
            info = self.path.lstat(); self.identity = (info.st_dev, info.st_ino)
            listener.listen(CONNECTION_LIMIT); listener.setblocking(False)
            self.listener = listener
            self.thread = threading.Thread(target=self._loop, name='gateway-local-io', daemon=True)
            self.thread.start()
        except BaseException:
            listener.close(); self._unlink(); raise
        return self

    def _unlink(self):
        if self.identity is None: return
        try:
            info = self.path.lstat()
            if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self.identity:
                self.path.unlink()
        except FileNotFoundError: pass

    def _loop(self):
        connections = {}
        with selectors.DefaultSelector() as select:
            select.register(self.listener, selectors.EVENT_READ)
            def close(stream):
                select.unregister(stream); connections.pop(stream, None); stream.close()
            def reply(stream, state, value):
                try: state['out'] = memoryview(encode(value))
                except ValueError:
                    state['out'] = memoryview(encode(self._error('INVALID_FRAME' if self.contract else 'RESPONSE_TOO_LARGE')))
                self.stats['encoded'] += 1
                select.modify(stream, selectors.EVENT_WRITE)
            try:
                while not self.stop.is_set():
                    for key, mask in select.select(.01):
                        stream = key.fileobj
                        if stream is self.listener:
                            stream, _ = self.listener.accept(); stream.setblocking(False)
                            _, peer, _ = struct.unpack('3i', stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                            if peer != self.uid:
                                self.stats['peer_denied'] += 1; stream.close(); continue
                            if len(connections) >= self.connection_limit:
                                self.stats['busy'] += 1; stream.close(); continue
                            state = dict(data=bytearray(), size=None, out=None, result=None,
                                         deadline=time.monotonic()+IO_BUDGET_S)
                            connections[stream] = state; select.register(stream, selectors.EVENT_READ)
                            self.stats['accepted'] += 1
                            self.stats['high_water'] = max(self.stats['high_water'], len(connections))
                            continue
                        state = connections[stream]
                        try:
                            if mask & selectors.EVENT_WRITE:
                                count = stream.send(state['out'])
                                state['out'] = state['out'][count:]
                                if not count or not state['out']: close(stream)
                                continue
                            if state['result'] is not None:
                                # A disconnect never cancels Gateway ownership.
                                if not stream.recv(1, socket.MSG_PEEK): close(stream)
                                else: close(stream)
                                continue
                            chunk = stream.recv(MAX_BYTES+5-len(state['data']))
                            if not chunk: close(stream); continue
                            state['data'].extend(chunk)
                            if state['size'] is None and len(state['data']) >= 4:
                                state['size'] = HEADER.unpack(state['data'][:4])[0]
                                if not 0 < state['size'] <= MAX_BYTES:
                                    raise ValueError('frame size')
                            if state['size'] is not None and len(state['data']) >= state['size']+4:
                                if len(state['data']) != state['size']+4:
                                    raise ValueError('one call per connection')
                                call = (self.contract.decode(state['data'][4:]) if self.contract else
                                    request(decode(state['data'][4:]))); self.stats['decoded'] += 1
                                state['data'].clear()
                                try: state['result'] = self.submit(call)
                                except queue.Full:
                                    self.stats['busy'] += 1
                                    reply(stream, state, self._error('BUSY_NOT_ACCEPTED'))
                                except ValueError:
                                    reply(stream, state, self._error('UNSUPPORTED_PROFILE' if self.contract else 'PROFILE_UNSUPPORTED'))
                        except (OSError, EOFError):
                            close(stream)
                        except ValueError as error:
                            self.stats['malformed'] += 1
                            reply(stream, state, self._error(getattr(error,'code','INVALID_FRAME')))
                    for stream, state in list(connections.items()):
                        if time.monotonic() >= state['deadline']:
                            self.stats['expired'] += 1; close(stream); continue
                        if state['result'] is not None and state['out'] is None:
                            try: value = state['result'].get_nowait()
                            except queue.Empty: continue
                            reply(stream, state, value)
            except BaseException as exc:
                import traceback
                self.failure = ''.join(traceback.format_exception(exc))
            finally:
                for stream in list(connections): close(stream)
                select.unregister(self.listener); self.listener.close()

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(.5)
            if self.thread.is_alive(): raise RuntimeError('Gateway IPC shutdown unconfirmed')
        self._unlink()
