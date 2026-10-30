"""MQTT-02 inventory: 실행 중인 OS·커널·호스트 이름·네트워크 인터페이스와 에이전트 버전·지원 메트릭.

Spring 이 필수로 검사하는 값은 os.id·kernel_release·hostname 이다(빈 값이면 메시지 전체 거부). 그래서 셋은 읽지 못해도 빈 문자열로
보내지 않고 알 수 있는 대체값을 쓴다. 배포 manifest 를 복사하지 않고 실행 중인 게스트에서 직접 읽는다.
"""

from __future__ import annotations

import ipaddress
import logging
import platform
import socket
import struct
from collections.abc import Callable
from typing import Any

from . import __version__
from .procfs import Roots, read_text

log = logging.getLogger("xenova_agent.inventory")

# capabilities.metrics: 이 에이전트가 MQTT-03 으로 보내는 메트릭 이름(계약 3장). 명령(MQTT-04)은 아직 받지 않는다
METRIC_NAMES = (
    "cpu_percent_allocated",
    "memory_used_bytes",
    "memory_available_bytes",
    "disk_read_bytes_total",
    "disk_write_bytes_total",
    "net_rx_bytes_total",
    "net_tx_bytes_total",
)


def parse_os_release(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def ipv4_address(name: str) -> str | None:
    """SIOCGIFADDR 로 인터페이스의 IPv4 주소. 없거나 지원하지 않으면 None."""
    try:
        import fcntl  # Linux 전용
    except ImportError:
        return None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            packed = fcntl.ioctl(sock.fileno(), 0x8915, struct.pack("256s", name.encode()[:15]))  # SIOCGIFADDR
    except OSError:
        return None
    return socket.inet_ntoa(packed[20:24])


def ipv6_addresses(roots: Roots) -> dict[str, list[str]]:
    """/proc/net/if_inet6: 인터페이스별 IPv6 주소."""
    result: dict[str, list[str]] = {}
    try:
        text = read_text(roots.proc / "net" / "if_inet6")
    except OSError:
        return result
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 6 or len(parts[0]) != 32:
            continue
        try:
            address = str(ipaddress.IPv6Address(bytes.fromhex(parts[0])))
        except ValueError:
            continue
        result.setdefault(parts[5], []).append(address)
    return result


def interfaces(roots: Roots, ipv4_of: Callable[[str], str | None] = ipv4_address) -> list[dict[str, Any]]:
    """/sys/class/net 의 인터페이스(lo 제외). MAC 은 address 파일, 주소는 IPv4(ioctl)·IPv6(if_inet6)."""
    net = roots.sys / "class" / "net"
    try:
        names = sorted(path.name for path in net.iterdir())
    except OSError as exc:
        log.warning("network interfaces unreadable: %s", exc)
        return []
    v6 = ipv6_addresses(roots)
    result = []
    for name in names:
        if name == "lo":
            continue
        try:
            mac = read_text(net / name / "address").strip() or None
        except OSError:
            mac = None
        addresses = []
        v4 = ipv4_of(name)
        if v4:
            addresses.append({"address": v4, "family": "ipv4"})
        addresses.extend({"address": address, "family": "ipv6"} for address in v6.get(name, []))
        result.append({"name": name, "mac": mac, "addresses": addresses})
    return result


def collect(
    roots: Roots = Roots(),
    *,
    hostname: Callable[[], str] = socket.gethostname,
    kernel_release: Callable[[], str] = platform.release,
    ipv4_of: Callable[[str], str | None] = ipv4_address,
) -> dict[str, Any]:
    try:
        os_release = parse_os_release(read_text(roots.etc / "os-release"))
    except OSError:
        os_release = {}
    try:
        kernel = read_text(roots.proc / "sys" / "kernel" / "osrelease").strip()
    except OSError:
        kernel = ""
    return {
        "os": {
            "id": os_release.get("ID") or "linux",  # 필수값. os-release 가 없으면 커널 이름
            "version_id": os_release.get("VERSION_ID") or None,
            "pretty_name": os_release.get("PRETTY_NAME") or None,
        },
        "kernel_release": kernel or kernel_release() or "unknown",
        "hostname": hostname() or "unknown",
        "interfaces": interfaces(roots, ipv4_of),
        "agent_version": __version__,
        "capabilities": {"commands": [], "metrics": list(METRIC_NAMES)},
    }
