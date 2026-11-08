"""명시 개발용 POSIX byte FD의 단일 소유 사본. serial/장치 승인은 하지 않는다.

호출자는 full-duplex stream socket 또는 raw PTY와 nonblocking 설정을 먼저
준비하고, 이 owner가 살아 있는 동안 shared open-file flags를 바꾸지 않는다.
dup 한 개만 닫으며 caller FD, tty 설정, 세션·clock·READY는 변경하지 않는다.
"""

import errno
import os
import select
import stat


class DevelopmentFDTransport:
    """최대4096B read/write를 한 syscall로 제출한다. retry/flush 대기는 없다."""

    MAX_BYTES = 4096

    def __init__(self, fd):
        if os.name != 'posix' or type(fd) is not int or fd < 0:
            raise ValueError('development transport requires an explicit POSIX integer FD')
        import fcntl

        before = os.fstat(fd)
        if not (stat.S_ISSOCK(before.st_mode) or stat.S_ISCHR(before.st_mode) and os.isatty(fd)):
            raise ValueError('development FD must be a duplex byte-stream socket or raw tty')
        if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR:
            raise ValueError('development FD must be full duplex')
        if os.get_blocking(fd):
            raise ValueError('caller must set development FD nonblocking before admission')
        owned = os.dup(fd)
        try:
            self._identity = self._key(before)
            if self._key(os.fstat(owned)) != self._identity or os.get_blocking(owned):
                raise ValueError('development FD changed during duplication')
        except BaseException:
            os.close(owned)
            raise
        self._fd = owned

    @staticmethod
    def _key(info):
        return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)

    def check_open(self):
        fd = self._fd
        if fd is None:
            raise OSError(errno.EBADF, 'development transport is closed')
        if self._key(os.fstat(fd)) != self._identity:
            raise OSError(errno.ESTALE, 'owned development FD identity changed')
        if os.get_blocking(fd):
            raise OSError(errno.EINVAL, 'shared development FD became blocking')
        return fd

    def fileno(self):
        return self.check_open()

    def read(self, size):
        if type(size) is not int or not 1 <= size <= self.MAX_BYTES:
            raise ValueError('development read exceeds4096B bound')
        fd = self.check_open()
        # Direct poll has no owner's readable hint. A bounded zero-time select
        # distinguishes raw tty idle-empty from EOF; OutputOwner uses its own
        # selector and FeedbackSerialSink._read_owned instead of this method.
        ready = bool(select.select([fd], [], [], 0)[0])
        try:
            data = os.read(fd, size)
        except OSError as error:
            if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                return b''
            raise
        if not data and ready:
            raise OSError(errno.EPIPE, 'development byte transport returned EOF')
        return data

    def write(self, data):
        if type(data) is not bytes or not 1 <= len(data) <= self.MAX_BYTES:
            raise ValueError('development write requires owned bytes within4096B')
        accepted = os.write(self.check_open(), data)
        if accepted != len(data):
            raise OSError(errno.EIO, 'partial development write; device application unknown')
        return accepted

    def close(self):
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            same = self._key(os.fstat(fd)) == self._identity
        except OSError as error:
            if error.errno == errno.EBADF:
                return
            raise
        if not same:
            raise OSError(errno.ESTALE, 'refusing to close a replaced development FD')
        os.close(fd)
