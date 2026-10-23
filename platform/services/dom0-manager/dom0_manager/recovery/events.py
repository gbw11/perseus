"""복구 이벤트 outbox (계약 2.5·4.7). 엔진이 상태 전이와 함께 레코드에 저장한 이벤트를 여기로 옮기고, 옮겨진 것만 MQTT(MQTT-12)로 낸다.

`XENOVA_RECOVERY_STATE_DIR/recovery-events.jsonl` 에 한 줄에 하나(JSON). Worker 가 쓰고, dom0-manager HTTP 의
`GET /api/v1/recovery/events` 가 같은 파일을 읽어 Spring 이 재시작·단절 뒤 빠진 구간을 채운다.

- append 가 False 를 돌려주면 아무것도 쓰이지 않은 것이다. 엔진은 그 이벤트를 레코드(recoveries.json)에 남겨 두고 매 주기 같은
  event_id 로 다시 부른다. 그래서 같은 event_id 가 두 번 쓰일 수 있고(옮긴 뒤 레코드를 저장하기 전에 죽은 경우) 읽을 때 처음 것만 남긴다
- 읽을 때 계약 스키마(RecoveryEvent)로 검증한다. JSON 이 아니거나 계약에 맞지 않는 줄은 "손상" 으로 두고, 커서 뒤에 손상이 있으면
  그 자리의 이벤트를 잃었을 수 있으니 gap=True 로 알린다. 페이지와 커서는 검증된 이벤트만으로 계산한다
- 최근 KEEP 건만 남기며 넘치면 임시 파일에 다시 써 원자적으로 바꾼다. 보관 범위 안의 손상 자리는 tombstone 줄로 남겨 compaction 뒤에도
  gap 을 알 수 있게 한다. /run 은 tmpfs 라 host 재부팅이면 비워지고, 그때 Spring 은 gap: true 로 안다
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any
from uuid import UUID

from .state import validate_event

log = logging.getLogger(__name__)

KEEP = 2000
COMPACT_SLACK = 200  # KEEP 을 이만큼 넘으면 다시 쓴다(매 건 다시 쓰지 않도록)
TOMBSTONE = {"tombstone": "damaged"}  # compaction 이 손상 줄 자리에 남기는 표시

Entry = dict[str, Any] | None  # None = 손상된 줄(또는 그 자리의 tombstone)


class EventLogUnavailable(Exception):
    """outbox 파일을 읽을 수 없다(권한·I/O 오류). 빈 목록으로 숨기지 않는다: HTTP 는 503, compaction 은 건너뛴다."""


def _line(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"


class EventLog:
    def __init__(self, path: Path | None, *, keep: int = KEEP) -> None:
        self._path = path
        self._keep = keep
        self._lock = threading.Lock()
        self._lines: int | None = None  # 파일의 줄 수(손상·tombstone 포함). compaction 판단용

    @property
    def path(self) -> Path | None:
        return self._path

    def append(self, event: dict[str, Any]) -> bool:
        """저장했으면 True. False 면 아무것도 쓰이지 않았고 호출자가 같은 event_id 로 다시 불러야 한다.
        계약에 맞지 않는 이벤트는 쓰지 않는다(읽을 때 손상 줄이 되어 그 이벤트를 영구히 잃는다)."""
        try:
            validate_event(event)
        except ValueError as exc:
            log.error("recovery event %s not stored: does not match the contract: %s", event.get("event_id") if isinstance(event, dict) else None, exc)
            return False
        if self._path is None:
            return True
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(_line(event))
                    fh.flush()
                    os.fsync(fh.fileno())
            except OSError as exc:
                log.error("recovery event %s not stored in %s: %s", event.get("event_id"), self._path, exc)
                return False
            if self._lines is None:
                try:
                    self._lines = len(self._read())
                except EventLogUnavailable:
                    return True  # 저장은 됐다. 줄 수를 모르면 compaction 하지 않는다
            else:
                self._lines += 1
            if self._lines > self._keep + COMPACT_SLACK:
                self._compact()
        return True

    def events(self) -> list[dict[str, Any]]:
        """보관된(검증된) 이벤트, 오래된 순."""
        with self._lock:
            return [e for e in self._retained(self._read()) if e is not None]

    def after(self, after_event_id: UUID | None, limit: int) -> dict[str, Any]:
        """계약 4.7. after_event_id 다음부터 limit 개. 커서가 보관 범위 밖이거나 커서 뒤에 손상된 줄이 있으면 gap=True."""
        with self._lock:
            entries = self._retained(self._read())
        valid = [e for e in entries if e is not None]
        oldest = valid[0]["event_id"] if valid else None
        gap, start = False, 0
        if after_event_id is not None:
            wanted = str(after_event_id)
            index = next((i for i, e in enumerate(entries) if e is not None and e["event_id"] == wanted), None)
            if index is None:
                gap = True  # 보관 범위 밖: 보관된 가장 오래된 것부터
            else:
                start = index + 1
        page: list[dict[str, Any]] = []
        for entry in entries[start:]:
            if entry is None:
                gap = True  # 커서와 이번 페이지 사이의 손상 줄: 그 자리의 이벤트를 잃었을 수 있다
                continue
            page.append(entry)
            if len(page) == limit:
                break
        next_after = page[-1]["event_id"] if page else None
        return {"events": page, "next_after_event_id": next_after, "oldest_event_id": oldest, "gap": gap}

    # ---- 내부 ----

    def _read(self) -> list[Entry]:
        """파일의 줄을 순서대로. 검증된 이벤트는 dict, 손상된 줄과 tombstone 은 None. 같은 event_id 의 중복은 처음 것만."""
        if self._path is None:
            return []
        result: list[Entry] = []
        seen: set[str] = set()
        try:
            with open(self._path, encoding="utf-8") as fh:
                for number, raw in enumerate(fh, 1):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                        if event == TOMBSTONE:
                            result.append(None)
                            continue
                        validate_event(event)
                    except ValueError:
                        log.warning("recovery event log %s line %d is damaged", self._path, number)
                        result.append(None)
                        continue
                    if event["event_id"] in seen:
                        continue
                    seen.add(event["event_id"])
                    result.append(event)
        except FileNotFoundError:
            return []  # 아직 이벤트가 없거나 host 재부팅으로 비었다. exists() 를 먼저 보지 않는다: 권한 오류를 '없음' 으로 바꿀 수 있다
        except OSError as exc:
            log.error("recovery event log %s unreadable: %s", self._path, exc)
            raise EventLogUnavailable(f"recovery event log unreadable: {exc}") from exc
        return result

    def _retained(self, entries: list[Entry]) -> list[Entry]:
        """최근 keep 건의 검증된 이벤트와 그 사이의 손상 표시만."""
        valid_indexes = [i for i, e in enumerate(entries) if e is not None]
        if len(valid_indexes) <= self._keep:
            return entries
        return entries[valid_indexes[-self._keep]:]

    def _compact(self) -> None:
        try:
            kept = self._retained(self._read())
        except EventLogUnavailable:
            return  # 읽지 못한 채 다시 쓰면 보관된 이벤트를 지운다
        try:
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("".join(_line(TOMBSTONE if entry is None else entry) for entry in kept))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
            self._lines = len(kept)
        except OSError as exc:
            log.warning("recovery event log %s not compacted: %s", self._path, exc)
