"""최종 receipt의 허가 경계에만 지연을 넣는 spawn fixture. SDK는 사용하지 않는다."""

import json
import os
from pathlib import Path
import time
from unittest.mock import patch

from xenova_vehicle.runtime import receipts
from xenova_vehicle.runtime.channel import ControlChannel


_REAL_WORKER = receipts._receipt_worker


def _marker(output, phase):
    path = Path(output) / f"{phase}.marker.json"
    with path.open("x", encoding="ascii") as target:
        json.dump({"pid": os.getpid(), "phase": phase}, target)


def delay_before_prepared(endpoint, output, report, reserve, filename="capture-result.json"):
    """최종화 PREPARED를 보내기 전에 marker를 남기고0.8초 대기한다."""
    if reserve:
        return _REAL_WORKER(endpoint, output, report, reserve, filename)

    class DelayedChannel(ControlChannel):
        marked = False

        def send(self, value):
            if value.get("kind") == "prepared" and not self.marked:
                self.marked = True
                _marker(output, "before-prepared")
                time.sleep(0.8)
            return super().send(value)

    with patch.object(receipts, "ControlChannel", DelayedChannel):
        return _REAL_WORKER(endpoint, output, report, reserve, filename)


def delay_after_commit(endpoint, output, report, reserve, filename="capture-result.json"):
    """COMMIT를 받은 뒤 rename 전에 marker를 남긴다. 이때도 I/O 자식은 살아 있다."""
    if reserve:
        return _REAL_WORKER(endpoint, output, report, reserve, filename)

    class DelayedChannel(ControlChannel):
        marked = False

        def receive(self):
            values = super().receive()
            if not self.marked and any(value.get("kind") == "commit" for value in values):
                self.marked = True
                _marker(output, "after-commit")
                time.sleep(0.8)
            return values

    with patch.object(receipts, "ControlChannel", DelayedChannel):
        return _REAL_WORKER(endpoint, output, report, reserve, filename)


def hang_before_prepared(endpoint, output, report, reserve, filename="capture-result.json"):
    """PREPARED 전 정체를 만들어 원 기한에 따른 강제 회수를 검사한다."""
    class DelayedChannel(ControlChannel):
        def send(self, value):
            if value.get("kind") == "prepared":
                _marker(output, "hung-before-prepared")
                time.sleep(60)
            return super().send(value)

    with patch.object(receipts, "ControlChannel", DelayedChannel):
        return _REAL_WORKER(endpoint, output, report, reserve, filename)


def collide_temporary(endpoint, output, report, reserve, filename="capture-result.json"):
    """최종화 자식의 배타 생성 경로를 먼저 점유해 실제 FileExistsError를 만든다."""
    if not reserve:
        path = Path(output) / (filename + f".tmp.{os.getpid()}")
        with path.open("xb") as target:
            target.write(b"owned-receipt-collision-fixture")
    return _REAL_WORKER(endpoint, output, report, reserve, filename)
