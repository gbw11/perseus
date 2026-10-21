"""Linux dom0의 xl PV 콘솔 세션. 인증/VM UUID 조회는 호출 계층의 책임이다.

open()은 프로세스 생성만 확인한다. 접속 실패/게스트 종료는 read()의 EOF로
전달한다. 터미널 바이트를 그대로 중계하며 게스트 로그인/암호는 취급하지 않는다.
"""

from __future__ import annotations

import asyncio
import errno
import os
import re
import signal
from collections.abc import Sequence


class ConsoleError(RuntimeError):
    """콘솔 생성 또는 입출력 실패."""


class ConsoleExited(ConsoleError):
    def __init__(self, returncode: int | None) -> None:
        self.returncode = returncode
        super().__init__(f"console process exited (returncode={returncode})")


class ConsoleSession:
    """단일 이벤트 루프에서 사용하는 재사용 불가 PTY 세션.

    xl_command는 서버가 지정한 실행 파일 인자이며 사용자 입력을 받으면 안 된다.
    domain은 관리 계층이 확인한 게스트 이름/현재 domid를 전달한다. 공개 API의
    VM UUID를 여기에 직접 넘기지 않는다. read/write는 각각 한 번에 하나만 실행된다.
    """

    def __init__(self, domain: str | int, *, xl_command: Sequence[str] = ("xl",),
                 close_timeout: float = 2.0) -> None:
        domain = str(domain)
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", domain)
                or domain.lower() in {"dom0", "domain-0"}
                or (domain.isdecimal() and int(domain) == 0)):
            raise ValueError("a non-dom0 guest name or domid is required")
        if not xl_command or isinstance(xl_command, str) or close_timeout <= 0:
            raise ValueError("xl_command must be an argv sequence; close_timeout must be positive")
        self.domain = domain
        self._argv = [*xl_command, "console", "-t", "pv", domain]
        self._close_timeout = close_timeout
        self._fd: int | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._opened = False
        self._closing = False
        self._read_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._pending: set[asyncio.Future] = set()

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc else None

    async def open(self) -> None:
        if self._opened or self._closing:
            raise ConsoleError("session cannot be reopened")
        if os.name != "posix":
            raise ConsoleError("PTY console requires Linux dom0 (or WSL for tests)")
        import tty

        self._opened = True
        master, slave = os.openpty()
        try:
            # No local echo, line buffering, or Ctrl+C interception by this PTY.
            tty.setraw(slave)
            os.set_blocking(master, False)
            self._proc = await asyncio.create_subprocess_exec(
                *self._argv, stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True,
            )
            self._fd = master
        except BaseException as exc:
            os.close(master)
            if isinstance(exc, OSError):
                raise ConsoleError("cannot start console process") from exc
            raise
        finally:
            os.close(slave)

    def _active_fd(self) -> int:
        if self._fd is None or self._closing:
            raise ConsoleError("console is not open")
        return self._fd

    async def _ready(self, fd: int, *, writing: bool = False) -> None:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending.add(future)
        add = loop.add_writer if writing else loop.add_reader
        remove = loop.remove_writer if writing else loop.remove_reader

        def ready() -> None:
            if not future.done():
                future.set_result(None)

        add(fd, ready)
        try:
            await future
        finally:
            remove(fd)
            self._pending.discard(future)

    async def read(self, size: int = 65536, *, timeout: float | None = None) -> bytes:
        """임의 크기의 바이트 조각. idle timeout은 TimeoutError, EOF는 ConsoleExited.

        문자/ANSI 시퀀스는 조각 경계에 걸칠 수 있다. 디코딩은 수신 측에서 한다.
        """
        if not 1 <= size <= 65536:
            raise ValueError("read size must be 1..65536")
        async with asyncio.timeout(timeout):
            async with self._read_lock:
                while True:
                    fd = self._active_fd()
                    try:
                        data = os.read(fd, size)
                    except BlockingIOError:
                        await self._ready(fd)
                        continue
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise ConsoleError("console read failed") from exc
                        data = b""
                    if data:
                        return data
                    if self._proc:
                        try:
                            await asyncio.wait_for(self._proc.wait(), self._close_timeout)
                        except TimeoutError:
                            pass
                    raise ConsoleExited(self.returncode)

    async def write(self, data: bytes, *, timeout: float = 5.0) -> None:
        if not isinstance(data, bytes) or len(data) > 65536:
            raise ValueError("write requires at most 65536 bytes")
        async with asyncio.timeout(timeout):
            async with self._write_lock:
                self._active_fd()
                remaining = memoryview(data)
                while remaining:
                    fd = self._active_fd()
                    try:
                        count = os.write(fd, remaining)
                    except BlockingIOError:
                        await self._ready(fd, writing=True)
                        continue
                    except OSError as exc:
                        raise ConsoleError("console write failed") from exc
                    if count == 0:
                        raise ConsoleError("console write made no progress")
                    remaining = remaining[count:]

    async def close(self) -> None:
        """Ctrl+]로 detach 후 필요하면 콘솔 프로세스 그룹만 종료한다. VM은 종료하지 않는다."""
        cleanup = asyncio.create_task(self._close())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise

    async def _close(self) -> None:
        async with self._close_lock:
            self._closing = True
            proc = self._proc
            try:
                if proc is not None and proc.returncode is None:
                    try:
                        if self._fd is not None:
                            os.write(self._fd, b"\x1d")
                    except OSError:
                        pass
                    for sig in (None, signal.SIGTERM, signal.SIGKILL):
                        if sig is not None:
                            try:
                                os.killpg(proc.pid, sig)
                            except ProcessLookupError:
                                pass
                        try:
                            await asyncio.wait_for(proc.wait(), self._close_timeout)
                            break
                        except TimeoutError:
                            if sig == signal.SIGKILL:
                                raise ConsoleError("console process did not exit after SIGKILL")
            finally:
                # Wake blocked readers/writers before closing the descriptor.
                fd, self._fd = self._fd, None
                if fd is not None:
                    loop = asyncio.get_running_loop()
                    loop.remove_reader(fd)
                    loop.remove_writer(fd)
                    for future in tuple(self._pending):
                        if not future.done():
                            future.set_exception(ConsoleError("console closed"))
                    os.close(fd)

    async def __aenter__(self) -> ConsoleSession:
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
