"""xentop 배치 출력 파싱과 통계 수집 (S15P31E110-112). 계약 2.4·4.6.

`xentop -b -i 1 -n -x -f` 한 번의 출력에서 도메인별 누적 카운터·현재값만 읽는다. 사용률(CPU %, bytes/s)은 싣지 않는다.
열 순서는 xentop.c 의 fields[] 그대로다:

  NAME STATE CPU(sec) CPU(%) MEM(k) MEM(%) MAXMEM(k) MAXMEM(%) VCPUS NETS NETTX(k) NETRX(k) VBDS VBD_OO VBD_RD VBD_WR VBD_RSECT VBD_WSECT SSID

- MAXMEM(k) 는 상한이 없으면 "no limit"(토큰 2개), MAXMEM(%) 는 "n/a" 다. 그래서 오른쪽 11개 열을 먼저 고정하고 나머지를 왼쪽에서 읽는다.
- STATE 글자 순서는 d s b c p r (dying, shutdown, blocked, crashed, paused, running) 로 xl list(r b p s <reason> d) 와 다르다.
- -n 은 "Net%d RX: ...bytes ... TX: ...bytes" 줄을, -x 는 "VBD <type> <dev> <details> OO: RD: WR: RSECT: WSECT:" 줄을 도메인 행 뒤에 붙인다.
  바이트는 장치 줄의 값이 정확하다(열의 NETTX(k)·NETRX(k) 는 KiB 로 잘린다). VBD 오류면 값이 '-' 다.
  Linux 의 <details> 는 "[%2x:%2x] " 라 minor 가 한 자리면 "[ca: 0]" 처럼 대괄호 안에 공백이 있다. 토큰 하나로 읽지 않고 OO: 앞까지 통째로 읽는다.
- 두 명령(xentop, xl list -l)은 요청 전체 deadline 하나를 나눠 쓴다. xentop 이 쓴 시간을 뺀 나머지만 xl list -l 에 준다(계약 조회 한도 5초).
- 이름은 -f 가 없으면 10자로 잘려 xl list 와 대조할 수 없다. 항상 -f 로 부른다.
- 헤더는 반복마다 다시 찍힌다. -i 2 이상이면 마지막 반복만 쓴다.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from ..errors import DriverError, DriverTimeout, PlatformUnavailable
from ..models import UnavailableReason, VmState
from .base import DomainStats, StatsSample
from .xl import CommandRunner, CommandUnavailable, Xl

log = logging.getLogger(__name__)

XENTOP_ARGS = ("xentop", "-b", "-i", "1", "-n", "-x", "-f")
DOM0_NAME = "Domain-0"
SECTOR_BYTES = 512
KIB = 1024

_STATE_RE = re.compile(r"^[d-][s-][b-][c-][p-][r-]$")
_NET_RE = re.compile(
    r"^Net(\d+)\s+RX:\s*(\d+)bytes\s+(\d+)pkts\s+(\d+)err\s+(\d+)drop\s+TX:\s*(\d+)bytes\s+(\d+)pkts\s+(\d+)err\s+(\d+)drop\s*$"
)
# VBD <type> <dev> <details> OO: … — details 는 "[%2x:%2x] "(공백 포함 가능) 또는 Linux 외에서는 빈 문자열
_VBD_RE = re.compile(r"^VBD\s+(\S+)\s+(\d+)\s+(.*?)\s*OO:\s*(\S+)\s+RD:\s*(\S+)\s+WR:\s*(\S+)\s+RSECT:\s*(\S+)\s+WSECT:\s*(\S+)\s*$")
_RIGHT_COLUMNS = 11  # VCPUS NETS NETTX(k) NETRX(k) VBDS VBD_OO VBD_RD VBD_WR VBD_RSECT VBD_WSECT SSID


@dataclass(frozen=True)
class NetDevice:
    rx_bytes: int
    tx_bytes: int


@dataclass(frozen=True)
class VbdDevice:
    """OO/RD/WR/RSECT/WSECT. 장치 오류면 xentop 이 '-' 를 찍으므로 None 이다."""

    rd: int | None
    wr: int | None
    rsect: int | None
    wsect: int | None


@dataclass
class XentopRow:
    name: str
    flags: str
    cpu_sec: int
    cpu_pct: float
    mem_k: int
    mem_pct: float | None
    maxmem_k: int | None  # "no limit" 이면 None
    maxmem_pct: float | None  # "n/a" 이면 None
    vcpus: int
    nets: int
    nettx_k: int
    netrx_k: int
    vbds: int
    vbd_oo: int | None
    vbd_rd: int | None
    vbd_wr: int | None
    vbd_rsect: int | None
    vbd_wsect: int | None
    net_devices: list[NetDevice] = field(default_factory=list)
    vbd_devices: list[VbdDevice] = field(default_factory=list)


def map_xentop_state(flags: str) -> VmState:
    """d s b c p r 순서. c 가 있으면 crashed, s·d 는 stopping, p 는 paused, 그 외(b·r·없음)는 running."""
    if not _STATE_RE.match(flags):
        raise DriverError(f"unexpected xentop state column {flags!r}")
    dying, shutdown, _blocked, crashed, paused, _running = flags
    if crashed == "c":
        return VmState.crashed
    if shutdown == "s" or dying == "d":
        return VmState.stopping
    if paused == "p":
        return VmState.paused
    return VmState.running


def _int_or_dash(token: str, *, line: str) -> int | None:
    if token == "-":
        return None
    try:
        return int(token)
    except ValueError as exc:
        raise DriverError("cannot parse xentop counter", detail=line[:200]) from exc


def _float_or_na(token: str, *, line: str) -> float | None:
    if token == "n/a":
        return None
    try:
        return float(token)
    except ValueError as exc:
        raise DriverError("cannot parse xentop percentage", detail=line[:200]) from exc


def _parse_row(line: str) -> XentopRow:
    parts = line.split()
    if len(parts) < _RIGHT_COLUMNS + 7:
        raise DriverError("unexpected xentop line", detail=line[:200])
    right = parts[-_RIGHT_COLUMNS:]
    head = parts[:-_RIGHT_COLUMNS]
    state_index = next((i for i in range(1, len(head)) if _STATE_RE.match(head[i])), None)
    if state_index is None:
        raise DriverError("xentop line has no state column", detail=line[:200])
    name = " ".join(head[:state_index])
    rest = head[state_index + 1 :]
    try:
        cpu_sec, cpu_pct, mem_k, mem_pct = int(rest[0]), float(rest[1]), int(rest[2]), _float_or_na(rest[3], line=line)
        if rest[4] == "no" and len(rest) >= 6 and rest[5] == "limit":
            maxmem_k, maxmem_pct, consumed = None, _float_or_na(rest[6], line=line), 7
        else:
            maxmem_k, maxmem_pct, consumed = int(rest[4]), _float_or_na(rest[5], line=line), 6
        if len(rest) != consumed:
            raise ValueError("column count")
        vcpus, nets, nettx_k, netrx_k, vbds = (int(right[i]) for i in range(5))
    except (ValueError, IndexError) as exc:
        raise DriverError("cannot parse xentop line", detail=line[:200]) from exc
    vbd = [_int_or_dash(right[i], line=line) for i in range(5, 10)]
    return XentopRow(
        name=name,
        flags=head[state_index],
        cpu_sec=cpu_sec,
        cpu_pct=cpu_pct,
        mem_k=mem_k,
        mem_pct=mem_pct,
        maxmem_k=maxmem_k,
        maxmem_pct=maxmem_pct,
        vcpus=vcpus,
        nets=nets,
        nettx_k=nettx_k,
        netrx_k=netrx_k,
        vbds=vbds,
        vbd_oo=vbd[0],
        vbd_rd=vbd[1],
        vbd_wr=vbd[2],
        vbd_rsect=vbd[3],
        vbd_wsect=vbd[4],
    )


def parse_xentop(text: str) -> list[XentopRow]:
    """배치 출력 전체 → 마지막 반복의 도메인 행(장치 줄 포함)."""
    rows: list[XentopRow] = []
    seen_header = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.split()[0] == "NAME":
            if seen_header:
                rows = []  # 새 반복: 마지막 반복만 남긴다
            seen_header = True
            continue
        net = _NET_RE.match(line)
        if net:
            if not rows:
                raise DriverError("xentop device line before any domain", detail=line[:200])
            rows[-1].net_devices.append(NetDevice(rx_bytes=int(net.group(2)), tx_bytes=int(net.group(6))))
            continue
        vbd = _VBD_RE.match(line)
        if vbd:
            if not rows:
                raise DriverError("xentop device line before any domain", detail=line[:200])
            rd, wr, rsect, wsect = (_int_or_dash(vbd.group(i), line=line) for i in (5, 6, 7, 8))
            rows[-1].vbd_devices.append(VbdDevice(rd=rd, wr=wr, rsect=rsect, wsect=wsect))
            continue
        if not seen_header:
            raise DriverError("xentop output has no header", detail=line[:200])
        rows.append(_parse_row(line))
    if not seen_header:
        raise DriverError("xentop output has no header")
    return rows


def row_to_stats(row: XentopRow, *, vm_id: UUID, domid: int, sampled_at: datetime) -> DomainStats:
    """계약 2.4 통계 객체. 측정하지 못한 필드는 0 이 아니라 None 과 unavailable 사유다."""
    unavailable: dict[str, str] = {}
    max_memory_mib: int | None
    if row.maxmem_k is None:
        max_memory_mib = None
        unavailable["max_memory_mib"] = UnavailableReason.NOT_REPORTED.value
    else:
        max_memory_mib = row.maxmem_k // KIB

    net_rx: int | None
    net_tx: int | None
    if row.net_devices:
        net_rx = sum(d.rx_bytes for d in row.net_devices)
        net_tx = sum(d.tx_bytes for d in row.net_devices)
    elif row.nets == 0:
        net_rx = net_tx = None
        unavailable["net_rx_bytes"] = unavailable["net_tx_bytes"] = UnavailableReason.NO_DEVICE.value
    else:  # 장치 줄이 없으면 KiB 열로 대신한다 (정밀도 손실)
        net_rx, net_tx = row.netrx_k * KIB, row.nettx_k * KIB

    disk_rd: int | None
    disk_wr: int | None
    if row.vbds == 0:
        disk_rd = disk_wr = None
        unavailable["disk_rd_bytes"] = unavailable["disk_wr_bytes"] = UnavailableReason.NO_DEVICE.value
    elif row.vbd_rsect is None or row.vbd_wsect is None:
        disk_rd = disk_wr = None
        unavailable["disk_rd_bytes"] = unavailable["disk_wr_bytes"] = UnavailableReason.DEVICE_ERROR.value
    else:
        disk_rd, disk_wr = row.vbd_rsect * SECTOR_BYTES, row.vbd_wsect * SECTOR_BYTES

    return DomainStats(
        vm_id=vm_id,
        domid=domid,
        name=row.name,
        sampled_at=sampled_at,
        state=map_xentop_state(row.flags),
        vcpus=row.vcpus,
        cpu_time_s=row.cpu_sec,
        memory_mib=row.mem_k // KIB,
        max_memory_mib=max_memory_mib,
        net_rx_bytes=net_rx,
        net_tx_bytes=net_tx,
        disk_rd_bytes=disk_rd,
        disk_wr_bytes=disk_wr,
        unavailable=unavailable,
    )


class Xentop:
    """xentop 실행기. xl 과 같은 CommandRunner 를 쓰므로 XENOVA_XL_SSH 로 실보드에도 붙는다."""

    def __init__(
        self,
        runner: CommandRunner,
        *,
        timeout_s: float = 5.0,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._runner = runner
        self.timeout_s = timeout_s
        self._clock = clock

    def sample(self, *, timeout_s: float | None = None) -> tuple[list[XentopRow], datetime]:
        """timeout_s 는 상위 요청이 남은 예산을 줄 때만 넘긴다. 기본은 자체 시한."""
        try:
            result = self._runner.run(list(XENTOP_ARGS), timeout_s=self.timeout_s if timeout_s is None else timeout_s)
        except CommandUnavailable as exc:
            raise PlatformUnavailable("xentop is not available on this host", detail=exc.detail) from exc
        if result.returncode != 0:
            raise PlatformUnavailable(
                f"xentop failed (exit {result.returncode})", detail=result.stderr.strip()[:500] or None
            )
        return parse_xentop(result.stdout), self._clock()


def collect_stats(
    xentop: Xentop,
    xl: Xl,
    *,
    allowed: Callable[[UUID], bool] | None = None,
    budget_s: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> StatsSample:
    """xentop 행을 `xl list -l` 의 이름→(domid, uuid) 로 대조해 vm_id 를 붙인다. Domain-0 과 uuid 없는 도메인은 뺀다.

    allowed 가 있으면 그 vm_id 만 남긴다(platform 모드: registry 등록 여부). 없으면 uuid 가 있는 모든 도메인(xen 진단 모드).
    budget_s(기본 xentop.timeout_s)는 요청 전체 deadline 이다. xentop 이 쓴 시간을 뺀 나머지를 xl list -l 에 준다.
    """
    budget = xentop.timeout_s if budget_s is None else budget_s
    if budget <= 0:
        raise DriverTimeout("stats deadline already exhausted before xentop")
    started = monotonic()
    rows, sampled_at = xentop.sample(timeout_s=min(xentop.timeout_s, budget))
    remaining = budget - (monotonic() - started)
    if remaining <= 0:
        raise DriverTimeout(f"stats deadline of {budget:g}s exhausted by xentop")
    identities: dict[str, tuple[int, str | None]] = {}
    for entry in xl.list_long(timeout_s=remaining):
        c_info = ((entry.get("config") or {}).get("c_info") or {})
        name, domid = c_info.get("name"), entry.get("domid")
        if not name or domid == 0 or name == DOM0_NAME:
            continue
        identities[name] = (int(domid), c_info.get("uuid"))
    domains: list[DomainStats] = []
    for row in rows:
        if row.name == DOM0_NAME:
            continue
        identity = identities.get(row.name)
        if identity is None:
            log.debug("xentop domain %s not in xl list (appeared or vanished between calls); skipped", row.name)
            continue
        domid, uuid_text = identity
        if not uuid_text:
            log.warning("domain %s (domid %d) has no uuid; not a managed VM, skipped from stats", row.name, domid)
            continue
        try:
            vm_id = UUID(uuid_text)
        except ValueError:
            log.warning("domain %s has non-UUID uuid %r; skipped from stats", row.name, uuid_text[:80])
            continue
        if allowed is not None and not allowed(vm_id):
            log.debug("domain %s (%s) is not a registered VM; skipped from stats", row.name, vm_id)
            continue
        domains.append(row_to_stats(row, vm_id=vm_id, domid=domid, sampled_at=sampled_at))
    return StatsSample(sampled_at=sampled_at, domains=domains)
