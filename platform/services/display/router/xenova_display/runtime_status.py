"""Share one existing kiosk status observation across capture consumers."""
import json
from pathlib import Path
import threading
import time


class RuntimeStatus:
    """Bounded stat cadence, parse only on file identity/version changes.

    Never replace the producer's timestamp with cache time. Each consumer must
    still check the original timestamp and per-output state on every access.
    """
    def __init__(self, path, *, interval=.1, clock=time.monotonic):
        if not 0 < interval <= .1: raise ValueError('status check interval must be <=100ms')
        self.path, self.interval, self.clock = Path(path), interval, clock
        self.lock = threading.Lock()
        self.next_check, self.signature, self.value = float('-inf'), None, None

    def read(self):
        with self.lock:
            now = self.clock()
            if now < self.next_check: return self.value
            self.next_check = now+self.interval
            try:
                stat = self.path.stat()
                signature = (stat.st_dev,stat.st_ino,stat.st_mtime_ns,stat.st_size)
                if signature != self.signature:
                    value = json.loads(self.path.read_text())
                    if not isinstance(value, dict): raise ValueError('invalid status object')
                    self.value, self.signature = value, signature
            except (OSError,ValueError):
                self.value, self.signature = None, None
            return self.value
