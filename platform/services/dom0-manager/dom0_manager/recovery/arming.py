"""Zephyr VM 의 app.heartbeat 대상 여부(armed)를 Worker 재시작 뒤에도 기억한다.

"이 VM 이 app.heartbeat 를 보내는 펌웨어인가"는 본래 템플릿 capability(플랫폼 카탈로그)로 알아야 한다. 그것이 없는 동안은
한 번이라도 신호를 받은 사실을 dom0 에 남겨, Worker 가 재시작된 뒤 앱이 이미 죽어 있어도 감시 대상에서 빠지지 않게 한다.
저장소는 /run/xenova/recovery(tmpfs) 라 host 재부팅이면 비워진다. 재부팅 뒤에는 VM 이 다시 떠서 신호를 보내므로 다시 arm 된다.

파일이 깨졌으면 "대상 아님"으로 조용히 시작하지 않는다. 깨진 파일은 `.corrupt` 로 옮겨 두고 history_lost 를 세워
capability 선언도 armed 기록도 없는 Zephyr VM 을 관측 불가(OBSERVATION_UNAVAILABLE)로 보고한다. history_lost 는 파일에 같이
남겨 Worker 재시작으로는 사라지지 않고, 운영자가 파일을 지워야 풀린다.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ArmingState:
    armed: dict[UUID, str] = field(default_factory=dict)  # vm_id → app_id
    history_lost: bool = False  # 저장 파일이 깨져 이전 armed 를 잃었다


def _parse(text: str) -> tuple[dict[UUID, str], bool]:
    data = json.loads(text)
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema (expected version {SCHEMA_VERSION})")
    raw = data.get("armed")
    if not isinstance(raw, dict):
        raise ValueError("'armed' must be an object")
    armed: dict[UUID, str] = {}
    for vm_id, app_id in raw.items():
        if not isinstance(app_id, str) or not app_id:
            raise ValueError(f"app_id for {vm_id!r} must be a non-empty string")
        try:
            armed[UUID(vm_id)] = app_id
        except ValueError as exc:
            raise ValueError(f"invalid vm_id {vm_id!r}") from exc
    lost = data.get("history_lost", False)
    if not isinstance(lost, bool):
        raise ValueError("'history_lost' must be a boolean")
    return armed, lost


class ArmingStore:
    def __init__(self, path: Path | None) -> None:
        self._path = path
        self.history_lost = False

    @property
    def path(self) -> Path | None:
        return self._path

    def load(self) -> ArmingState:
        """파일이 없으면 빈 상태. 깨졌으면 `.corrupt` 로 보존하고 history_lost 를 세운 빈 상태(파일에도 남긴다)."""
        if self._path is None or not self._path.exists():
            return ArmingState()
        try:
            armed, lost = _parse(self._path.read_text(encoding="utf-8"))
        except OSError as exc:
            return self._lost(f"unreadable: {exc}", preserve=False)
        except (ValueError, TypeError) as exc:
            return self._lost(str(exc), preserve=True)
        self.history_lost = lost
        return ArmingState(armed, lost)

    def _lost(self, why: str, *, preserve: bool) -> ArmingState:
        self.history_lost = True
        log.error(
            "arming store %s corrupt (%s): previously armed Zephyr VMs are unknown until they signal again; "
            "undeclared ones are reported OBSERVATION_UNAVAILABLE. Delete the file to clear history_lost", self._path, why,
        )
        if preserve:
            try:
                os.replace(self._path, self._path.with_suffix(self._path.suffix + ".corrupt"))
            except OSError as exc:
                log.warning("arming store %s could not be set aside: %s", self._path, exc)
        self.save({})
        return ArmingState({}, history_lost=True)

    def save(self, armed: dict[UUID, str]) -> None:
        if self._path is None:
            return
        body = {
            "version": SCHEMA_VERSION,
            "armed": {str(k): v for k, v in sorted(armed.items(), key=lambda kv: str(kv[0]))},
            "history_lost": self.history_lost,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(body, indent=0), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            log.warning("arming store %s not saved: %s", self._path, exc)
