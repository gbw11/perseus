"""MQTT-03 metrics 의 values[]: /proc 기반 CPU·메모리·디스크·네트워크.

- cpu_percent_allocated: /proc/stat 의 cpu 합계 줄. 두 표본 사이 (busy 증가 ÷ 전체 증가) × 100 이라 할당된 vCPU 전체를 100% 로
  정규화한다. 첫 표본은 null + first_sample
- memory_*: /proc/meminfo 의 MemTotal − MemAvailable, MemAvailable (kB × 1024)
- disk_*_bytes_total: /proc/diskstats 의 섹터 × 512. /sys/block 에 있는 디스크 단위만 세어 파티션(xvda1)을 겹쳐 세지 않는다.
  loop·ram 장치는 뺀다. 디스크가 없으면 null + device_not_found. /sys/block 을 읽지 못하면 디스크와 파티션을 가를 수 없어 겹쳐 셀 수
  있으므로 추측하지 않고 null + read_failed
- net_*_bytes_total: /proc/net/dev 의 인터페이스별 누적 바이트. lo 는 뺀다
- 단위는 계약이 허용하는 percent·bytes 만 쓴다. 읽거나 해석하지 못한 값은 0 이 아니라 null + 사유다
"""

from __future__ import annotations

import logging
from typing import Any

from .procfs import DEVICE_NOT_FOUND, FIRST_SAMPLE, NOT_SUPPORTED, PARSE_ERROR, READ_FAILED, Roots, read_text

log = logging.getLogger("xenova_agent.metrics")

SECTOR_BYTES = 512  # /proc/diskstats 의 섹터는 장치와 무관하게 512 바이트
SKIP_DISK_PREFIXES = ("loop", "ram", "zram")


def value(name: str, amount: float | int | None, unit: str, labels: dict[str, str] | None = None, reason: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "value": None if amount is None else amount,
        "unit": unit,
        "labels": labels or {},
        "unavailable_reason": reason if amount is None else None,
    }


class MetricsCollector:
    def __init__(self, roots: Roots = Roots()) -> None:
        self._roots = roots
        self._cpu: tuple[int, int] | None = None  # (busy, total) 직전 표본

    def collect(self) -> list[dict[str, Any]]:
        return [*self._cpu_values(), *self._memory_values(), *self._disk_values(), *self._net_values()]

    # ---- CPU ----

    def _cpu_values(self) -> list[dict[str, Any]]:
        name = "cpu_percent_allocated"
        try:
            line = read_text(self._roots.proc / "stat").splitlines()[0]
        except (OSError, IndexError):
            return [value(name, None, "percent", reason=READ_FAILED)]
        fields = line.split()
        try:
            if fields[0] != "cpu":
                raise ValueError(fields[0])
            ticks = [int(v) for v in fields[1:]] + [0] * 8
            if len(fields) < 5:
                raise ValueError("too few cpu fields")
            # user nice system idle iowait irq softirq steal (guest·guest_nice 는 user·nice 에 이미 들어 있어 더하지 않는다)
            user, nice, system, _idle, _iowait, irq, softirq, _steal = ticks[:8]
            total = sum(ticks[:8])
            # steal 은 하이퍼바이저가 가져간 시간이라 게스트 사용으로 세지 않는다(분모에는 들어간다)
            busy = user + nice + system + irq + softirq
        except (ValueError, IndexError):
            return [value(name, None, "percent", reason=PARSE_ERROR)]
        previous, self._cpu = self._cpu, (busy, total)
        if previous is None:
            return [value(name, None, "percent", reason=FIRST_SAMPLE)]
        d_total = total - previous[1]
        d_busy = busy - previous[0]
        if d_total <= 0 or d_busy < 0:  # 같은 틱이거나 카운터가 되돌아감: 이번 구간은 계산하지 않는다
            return [value(name, None, "percent", reason=FIRST_SAMPLE)]
        return [value(name, round(min(100.0, 100.0 * d_busy / d_total), 2), "percent")]

    # ---- 메모리 ----

    def _memory_values(self) -> list[dict[str, Any]]:
        try:
            text = read_text(self._roots.proc / "meminfo")
        except OSError:
            return [value("memory_used_bytes", None, "bytes", reason=READ_FAILED),
                    value("memory_available_bytes", None, "bytes", reason=READ_FAILED)]
        info: dict[str, int] = {}
        for line in text.splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                info[key.strip()] = int(parts[0]) * 1024
        total, available = info.get("MemTotal"), info.get("MemAvailable")
        if total is None or available is None:
            reason = NOT_SUPPORTED if total is not None else PARSE_ERROR
            return [value("memory_used_bytes", None, "bytes", reason=reason),
                    value("memory_available_bytes", None, "bytes", reason=reason)]
        return [value("memory_used_bytes", max(0, total - available), "bytes"), value("memory_available_bytes", available, "bytes")]

    # ---- 디스크 ----

    def _whole_disks(self) -> set[str] | None:
        try:
            return {p.name for p in (self._roots.sys / "block").iterdir() if not p.name.startswith(SKIP_DISK_PREFIXES)}
        except OSError:
            return None

    def _disk_values(self) -> list[dict[str, Any]]:
        names = ("disk_read_bytes_total", "disk_write_bytes_total")
        disks = self._whole_disks()
        if disks is None:
            return [value(n, None, "bytes", reason=READ_FAILED) for n in names]
        try:
            text = read_text(self._roots.proc / "diskstats")
        except OSError:
            return [value(n, None, "bytes", reason=READ_FAILED) for n in names]
        result = []
        for line in text.splitlines():
            fields = line.split()
            if len(fields) < 10:
                continue
            device = fields[2]
            if device.startswith(SKIP_DISK_PREFIXES) or device not in disks:
                continue
            try:
                read_sectors, write_sectors = int(fields[5]), int(fields[9])
            except ValueError:
                result.extend(value(n, None, "bytes", {"device": device}, PARSE_ERROR) for n in names)
                continue
            result.append(value(names[0], read_sectors * SECTOR_BYTES, "bytes", {"device": device}))
            result.append(value(names[1], write_sectors * SECTOR_BYTES, "bytes", {"device": device}))
        if not result:
            return [value(n, None, "bytes", reason=DEVICE_NOT_FOUND) for n in names]
        return result

    # ---- 네트워크 ----

    def _net_values(self) -> list[dict[str, Any]]:
        names = ("net_rx_bytes_total", "net_tx_bytes_total")
        try:
            text = read_text(self._roots.proc / "net" / "dev")
        except OSError:
            return [value(n, None, "bytes", reason=READ_FAILED) for n in names]
        result = []
        for line in text.splitlines()[2:]:  # 머리줄 두 개
            iface, sep, rest = line.partition(":")
            iface = iface.strip()
            if not sep or iface == "lo":
                continue
            fields = rest.split()
            try:
                rx, tx = int(fields[0]), int(fields[8])
            except (ValueError, IndexError):
                result.extend(value(n, None, "bytes", {"iface": iface}, PARSE_ERROR) for n in names)
                continue
            result.append(value(names[0], rx, "bytes", {"iface": iface}))
            result.append(value(names[1], tx, "bytes", {"iface": iface}))
        if not result:
            return [value(n, None, "bytes", reason=DEVICE_NOT_FOUND) for n in names]
        return result
