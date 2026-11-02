"""xl 명령 실행과 출력 파싱. 실제 subprocess 는 SubprocessRunner 하나로 모아 테스트에서 FakeRunner 로 바꾼다.

안전 규칙
- 인자 리스트로만 실행한다. 문자열 명령·shell=True 금지.
- 모든 호출에 타임아웃을 둔다. 타임아웃은 DriverTimeout 으로 바꿔 서비스 전체가 멈추지 않게 한다.
  요청 예산(budget.py)이 있으면 자체 한도와 남은 예산 중 짧은 쪽이고, 남은 예산이 없으면 실행하지 않는다.
- stderr 전체를 응답에 싣지 않는다. 잘라서 detail 에만 넣고 message 는 짧게 유지한다.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple, Protocol

from .. import budget as request_budget
from ..errors import DriverError, DriverTimeout
from ..models import VmState
from .base import HostInfo

STDERR_DETAIL_LIMIT = 500


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(self, args: Sequence[str], *, timeout_s: float) -> CommandResult: ...


class CommandUnavailable(DriverError):
    """실행 파일 자체를 못 찾거나 실행할 수 없음(OSError). xentop 처럼 선택적 도구는 503 으로 바꿔 낸다."""


class SubprocessRunner:
    def run(self, args: Sequence[str], *, timeout_s: float) -> CommandResult:
        argv = [str(a) for a in args]
        try:
            proc = subprocess.run(  # noqa: S603 - 인자 리스트, shell 없음
                argv,
                shell=False,
                text=True,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DriverTimeout(f"{' '.join(argv[:2])} timed out after {timeout_s:g}s") from exc
        except OSError as exc:
            raise CommandUnavailable(f"cannot run {argv[0]}", detail=exc.strerror) from exc
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)


SSH_EXIT_CONNECTION_FAILED = 255  # OpenSSH: 연결·인증 실패는 원격 명령의 종료 코드가 아니라 255 다


class SshRunner:
    """보드 밖(개발 PC)에서 dom0 의 xl 을 SSH 로 부른다. 조회 전용 xen 드라이버를 보드에 설치하지 않고 실보드 출력에 대조할 때 쓴다.

    XENOVA_XL_SSH=root@<dom0 주소> 로 켠다. BatchMode 라 키 인증만 되고 비밀번호 프롬프트로 멈추지 않는다.
    원격 명령은 인자 리스트 그대로 넘기고(`--` 뒤), 타임아웃은 ssh 프로세스 전체에 건다.
    """

    def __init__(self, target: str, *, ssh: Sequence[str] = ("ssh",), connect_timeout_s: int = 5,
                 runner: CommandRunner | None = None) -> None:
        if not target or any(c.isspace() for c in target):
            raise ValueError(f"XENOVA_XL_SSH must be like root@host, got {target!r}")
        self.target = target
        self._prefix = [*ssh, "-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout_s}", target, "--"]
        self._runner = runner or SubprocessRunner()

    def run(self, args: Sequence[str], *, timeout_s: float) -> CommandResult:
        remote = [str(a) for a in args]
        try:
            result = self._runner.run([*self._prefix, *remote], timeout_s=timeout_s)
        except DriverTimeout as exc:
            raise DriverTimeout(f"{' '.join(remote[:2])} over ssh to {self.target} timed out after {timeout_s:g}s") from exc
        if result.returncode == SSH_EXIT_CONNECTION_FAILED:
            raise DriverError(
                f"ssh to {self.target} failed (connection or key authentication)",
                detail=result.stderr.strip()[:STDERR_DETAIL_LIMIT] or None,
            )
        return result


@dataclass(frozen=True)
class XlRow:
    """`xl list` 한 줄: Name ID Mem VCPUs State Time(s)."""

    name: str
    domid: int
    mem_mib: int
    vcpus: int
    flags: str


# 계약 3.2 장. 열은 libxl list_domains 가 찍는 순서대로 고정이다 (xl.c, 저장소 픽스처 xl-list-source.txt):
#   [0] r running  [1] b blocked  [2] p paused  [3] s shutdown  [4] shutdown reason  [5] d dying
# 다섯째 열은 독립 crash 플래그가 아니라 shutdown reason 이다 ('-' 없음, 'r' reboot, 's' suspend,
# 'c' crash, 'w' watchdog, 'S' soft reset). shutdown 이 아니면 항상 '-' 다.
# b(blocked) 는 I/O 대기 중인 정상 상태라 running 이다. 플래그가 하나도 없는 '------' 는 실행 가능하지만
# 지금 pCPU 에 올라가 있지 않은 도메인이라 running 으로 본다. 그 외 문자는 조용히 넘기지 않고 DriverError 다.
_SHUTDOWN_REASONS = "-rscwS?"


def map_state(flags: str) -> VmState:
    if len(flags) != 6:
        raise DriverError(f"unexpected xl state column {flags!r}")
    running, blocked, paused, shutdown, reason, dying = flags
    for ch, allowed in ((running, "r-"), (blocked, "b-"), (paused, "p-"), (shutdown, "s-"), (dying, "d-")):
        if ch not in allowed:
            raise DriverError(f"unknown xl state flag {ch!r} in {flags!r}")
    if reason not in _SHUTDOWN_REASONS:
        raise DriverError(f"unknown xl shutdown reason {reason!r} in {flags!r}")
    if shutdown == "s" and reason == "c":
        return VmState.crashed
    if shutdown == "s" or dying == "d":
        return VmState.stopping
    if paused == "p":
        return VmState.paused
    return VmState.running


def parse_xl_list(text: str) -> list[XlRow]:
    rows: list[XlRow] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("Name"):
            continue
        parts = line.split()
        if len(parts) < 5:
            raise DriverError("unexpected xl list line", detail=line[:200])
        try:
            rows.append(XlRow(name=parts[0], domid=int(parts[1]), mem_mib=int(parts[2]), vcpus=int(parts[3]), flags=parts[4]))
        except ValueError as exc:
            raise DriverError("cannot parse xl list line", detail=line[:200]) from exc
    return rows


def parse_xl_list_long(text: str) -> list[dict[str, Any]]:
    """`xl list -l` 은 libxl JSON 배열이다. 각 항목: {"domid": int, "config": {"c_info": {"name", "uuid", ...}, ...}}."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DriverError("xl list -l returned invalid JSON", detail=str(exc)[:200]) from exc
    if not isinstance(data, list):
        raise DriverError("xl list -l returned unexpected schema")
    return data


def parse_xl_info(text: str) -> HostInfo:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        values[key.strip()] = value.strip()
    try:
        return HostInfo(
            free_memory_mib=int(values["free_memory"]),
            total_memory_mib=int(values["total_memory"]),
            nr_cpus=int(values["nr_cpus"]),
        )
    except (KeyError, ValueError) as exc:
        raise DriverError("cannot parse xl info", detail=str(exc)[:200]) from exc


class Xl:
    """xl 서브커맨드 래퍼. 조회와 명령의 타임아웃을 분리한다(계약 10장)."""

    def __init__(self, runner: CommandRunner, *, query_timeout_s: float = 5.0, command_timeout_s: float = 30.0) -> None:
        self._runner = runner
        self.query_timeout_s = query_timeout_s
        self.command_timeout_s = command_timeout_s

    def _run(self, *args: str, timeout_s: float) -> str:
        timeout_s = request_budget.clamp(timeout_s, f"xl {args[0]}")
        result = self._runner.run(["xl", *args], timeout_s=timeout_s)
        if result.returncode != 0:
            raise DriverError(
                f"xl {args[0]} failed (exit {result.returncode})",
                detail=result.stderr.strip()[:STDERR_DETAIL_LIMIT] or None,
            )
        return result.stdout

    # 조회
    def list(self) -> list[XlRow]:
        return parse_xl_list(self._run("list", timeout_s=self.query_timeout_s))

    def list_long(self, *, timeout_s: float | None = None) -> list[dict[str, Any]]:
        """timeout_s 는 상위 요청이 deadline 을 나눠 줄 때만 넘긴다(통계 수집)."""
        return parse_xl_list_long(self._run("list", "-l", timeout_s=self.query_timeout_s if timeout_s is None else timeout_s))

    def info(self) -> HostInfo:
        return parse_xl_info(self._run("info", timeout_s=self.query_timeout_s))

    # 명령 (S15P31E110-115 에서 사용)
    def create(self, config_path: str) -> None:
        self._run("create", config_path, timeout_s=self.command_timeout_s)

    def shutdown(self, domain: str) -> None:
        self._run("shutdown", domain, timeout_s=self.command_timeout_s)

    def destroy(self, domain: str) -> None:
        self._run("destroy", domain, timeout_s=self.command_timeout_s)
