"""xl 명령 실행과 출력 파싱. 실제 subprocess 는 SubprocessRunner 하나로 모아 테스트에서 FakeRunner 로 바꾼다.

안전 규칙
- 인자 리스트로만 실행한다. 문자열 명령·shell=True 금지.
- 모든 호출에 타임아웃을 둔다. 타임아웃은 DriverTimeout 으로 바꿔 서비스 전체가 멈추지 않게 한다.
- stderr 전체를 응답에 싣지 않는다. 잘라서 detail 에만 넣고 message 는 짧게 유지한다.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple, Protocol

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
            raise DriverError(f"cannot run {argv[0]}", detail=exc.strerror) from exc
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)


@dataclass(frozen=True)
class XlRow:
    """`xl list` 한 줄: Name ID Mem VCPUs State Time(s)."""

    name: str
    domid: int
    mem_mib: int
    vcpus: int
    flags: str


# 계약 3.2 장. b(blocked) 는 I/O 대기 중인 정상 상태라 running 이다.
# 플래그가 하나도 없는 '------' 는 실행 가능하지만 지금 pCPU 에 올라가 있지 않은 도메인이라 running 으로 본다.
# 그 외 알 수 없는 문자는 조용히 running 으로 넘기지 않고 DriverError 로 올린다.
_KNOWN_FLAGS = set("rbpsdc")


def map_state(flags: str) -> VmState:
    letters = {ch for ch in flags if ch != "-"}
    unknown = letters - _KNOWN_FLAGS
    if unknown:
        raise DriverError(f"unknown xl state flag(s) {''.join(sorted(unknown))!r} in {flags!r}")
    if "c" in letters:
        return VmState.crashed
    if letters & {"s", "d"}:
        return VmState.stopping
    if "p" in letters:
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

    def list_long(self) -> list[dict[str, Any]]:
        return parse_xl_list_long(self._run("list", "-l", timeout_s=self.query_timeout_s))

    def info(self) -> HostInfo:
        return parse_xl_info(self._run("info", timeout_s=self.query_timeout_s))

    # 명령 (S15P31E110-115 에서 사용)
    def create(self, config_path: str) -> None:
        self._run("create", config_path, timeout_s=self.command_timeout_s)

    def shutdown(self, domain: str) -> None:
        self._run("shutdown", domain, timeout_s=self.command_timeout_s)

    def destroy(self, domain: str) -> None:
        self._run("destroy", domain, timeout_s=self.command_timeout_s)
