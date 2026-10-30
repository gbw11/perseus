"""VM 별 생존 신호 수신 이력(armed)을 Worker 재시작 뒤에도 기억한다.

- Zephyr `armed`(vm_id → app_id): app.heartbeat 를 받은 적이 있다
- Linux `agent_delivered`(vm_id 목록): agent.heartbeat 를 받은 적이 있다

이력은 "이 VM 의 신원·ACL·전달 경로가 실제로 동작한 적이 있다"는 뜻이다. 손실 판정의 자격일 뿐 현재 실행이 건강하다는 근거가
아니다(현재 신호는 감시기가 부팅마다 따로 본다). 이력이 없는 VM 은 계정·ACL 오류, 배포 누락, 부팅 정체를 구별할 수 없어 판정하지 않는다.
다른 VM 의 수신은 이 VM 의 이력을 만들지 않는다.

"이 VM 이 app.heartbeat 를 보내는 펌웨어인가"는 본래 템플릿 capability(플랫폼 카탈로그)로 알아야 한다. 그것이 없는 동안은
한 번이라도 신호를 받은 사실로 대상 여부도 정한다.
저장소는 /run/xenova/recovery(tmpfs) 라 host 재부팅이면 비워진다. 재부팅 뒤에는 VM 이 다시 떠서 신호를 보내므로 다시 arm 된다.
그 사이(다시 신호를 보내기 전)에 멈춘 VM 은 자동 복구하지 않는다.

파일이 깨졌으면 "대상 아님"으로 조용히 시작하지 않는다. 원본을 `.corrupt` 로 보존한 뒤 history_lost 를 세워
capability 선언도 armed 기록도 없는 Zephyr VM 을 관측 불가(OBSERVATION_UNAVAILABLE)로 보고한다. history_lost 는 파일에 같이
남긴다. 차단 저장이 실패해도 남은 원본/격리 흔적으로 Worker 재시작 때 차단을 복원한다.
손상 증거 삭제를 정상 수습으로 취급하지 않는다.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from .store_files import preserve_corrupt, read_state

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ArmingState:
    armed: dict[UUID, str] = field(default_factory=dict)  # Zephyr: vm_id → app_id
    history_lost: bool = False  # 저장 파일이 깨져 이전 armed 를 잃었다
    agent_delivered: frozenset[UUID] = frozenset()  # Linux: agent.heartbeat 를 받은 적이 있는 VM


def _parse(text: str) -> tuple[dict[UUID, str], bool, frozenset[UUID]]:
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
    raw_agents = data.get("agent_delivered", [])  # 이 필드가 없던 파일은 Linux 이력이 비어 있는 것과 같다
    if not isinstance(raw_agents, list):
        raise ValueError("'agent_delivered' must be a list")
    try:
        agents = frozenset(UUID(vm_id) for vm_id in raw_agents)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"invalid vm_id in 'agent_delivered': {exc}") from exc
    return armed, lost, agents


class ArmingStore:
    def __init__(self, path: Path | None) -> None:
        self._path = path
        self.history_lost = False
        self._write_blocked = False

    @property
    def path(self) -> Path | None:
        return self._path

    def load(self) -> ArmingState:
        """최초 부재만 빈 상태. 손상·읽기 실패·고립된 증거는 이력 상실이다."""
        if self._path is None:
            return ArmingState()
        try:
            text = read_state(self._path)
            if text is None:
                return ArmingState(history_lost=self.history_lost)
            armed, lost, agents = _parse(text)
        except OSError as exc:
            return self._lost(f"unreadable: {exc}", preserve=False)
        except (ValueError, TypeError) as exc:
            return self._lost(str(exc), preserve=True)
        self.history_lost = self.history_lost or lost
        return ArmingState(armed, self.history_lost, agents)

    def _lost(self, why: str, *, preserve: bool) -> ArmingState:
        self.history_lost = True
        log.error(
            "arming store %s corrupt (%s): previously armed VMs are not judged for signal loss until they signal again; "
            "undeclared Zephyr VMs are reported OBSERVATION_UNAVAILABLE; preserve state and evidence for investigation", self._path, why,
        )
        if preserve:
            try:
                preserve_corrupt(self._path)
            except OSError as exc:
                self._write_blocked = True
                log.warning("arming store %s could not be set aside: %s", self._path, exc)
        else:
            self._write_blocked = True
        if not self._write_blocked:
            self.save({})
        return ArmingState({}, history_lost=True)

    def save(self, armed: dict[UUID, str], agent_delivered: frozenset[UUID] | set[UUID] = frozenset()) -> bool:
        """로컬 기록 확인 여부. 실패는 Watcher에 전달하며 감시 이력을 정상으로 꾸미지 않는다."""
        if self._path is None:
            return True
        if self._write_blocked:
            log.error("arming store %s not saved: unresolved state/evidence", self._path)
            return False
        body = {
            "version": SCHEMA_VERSION,
            "armed": {str(k): v for k, v in sorted(armed.items(), key=lambda kv: str(kv[0]))},
            "history_lost": self.history_lost,
            "agent_delivered": sorted(str(vm_id) for vm_id in agent_delivered),
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(body, indent=0))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self._path)
        except OSError as exc:
            self.history_lost = True
            log.warning("arming store %s not saved: %s", self._path, exc)
            return False
        return True
