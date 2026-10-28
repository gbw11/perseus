"""표시 전용 진행 사실. 관리 journal·admission·완료 저장을 대체하지 않는다."""
import json
import os
from pathlib import Path
import re
import time
import uuid


class Progress:
    """한 host/unit invocation/호출의 최신 사실을 best effort로 게시한다.

    fsync나 공통 실행 잠금을 추가하지 않는다. 게시 실패는 원 작업의 반환값,
    stdout, 내구성 저장에 영향을 주지 않는다. 독자는 invocation을 대조해야 한다.
    """
    def __init__(self, kind, runtime=Path('/run/xenova'), *, clock=time.monotonic,
                 host=None, invocation=None):
        self.kind, self.runtime, self.clock = kind, Path(runtime), clock
        self.host = host
        self.invocation = os.environ.get('INVOCATION_ID') if invocation is None else invocation
        self.batch = str(uuid.uuid4())

    def emit(self, phase, **facts):
        temp = None
        try:
            if self.kind not in ('storage', 'registration', 'cpu', 'autostart'):
                return False
            host = self.host or Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            if not re.fullmatch(r'[0-9a-f-]{36}', host):
                return False
            invocation = self.invocation if re.fullmatch(r'[0-9a-f]{32}', self.invocation or '') else None
            folder = self.runtime / 'boot-progress'
            folder.mkdir(mode=0o755, parents=True, exist_ok=True)
            if folder.resolve() != folder or not folder.is_dir():
                return False
            value = {'schema_version': '1.0', 'host_boot_id': host,
                     'invocation_id': invocation, 'batch_id': self.batch,
                     'updated_monotonic_ms': int(self.clock()*1000), 'phase': phase, **facts}
            data = json.dumps(value, ensure_ascii=True).encode('utf-8')
            if len(data) > 131072:
                return False
            temp = folder / ('.' + self.kind + '-' + str(uuid.uuid4()))
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
            os.replace(temp, folder / (self.kind + '.json'))
            return True
        except (OSError, ValueError, TypeError, OverflowError):
            return False
        finally:
            if temp is not None:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
