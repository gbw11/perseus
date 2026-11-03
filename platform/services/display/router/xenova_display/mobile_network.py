"""Resolve a verified USB interface without assigning IPs or changing routes."""
from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess

PRIVATE = tuple(ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def private_ip(value):
    address = ipaddress.IPv4Address(value)
    if not any(address in block for block in PRIVATE):
        raise ValueError("RFC1918 IPv4 required")
    return address


@dataclass(frozen=True)
class Endpoint:
    interface: str
    local_ip: str
    phone_ip: str
    port: int

    @property
    def host(self): return f"{self.local_ip}:{self.port}"

    @property
    def origin(self): return f"http://{self.host}"


def resolve(config, *, sysfs=Path("/sys/class/net"), run=subprocess.run):
    if (not isinstance(config, dict) or set(config) != {"schema_version", "usb_interface", "phone_ipv4", "port"}
            or type(config["schema_version"]) is not int or config["schema_version"] != 1):
        raise ValueError("invalid mobile config")
    interface, port = config["usb_interface"], config["port"]
    if (not isinstance(interface, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface)
            or type(port) is not int or not 1024 <= port <= 65535
            or port in (8081, 8082, 8083)):
        raise ValueError("verified USB interface and separate unprivileged port required")
    if (sysfs / interface / "device/subsystem").resolve(strict=True).name != "usb":
        raise ValueError("configured interface is not a USB device")
    phone = private_ip(config["phone_ipv4"])
    result = run(["ip", "-j", "-4", "address", "show", "dev", interface],
                 capture_output=True, text=True, timeout=2, check=True)
    if len(result.stdout) > 65536:
        raise ValueError("address response too large")
    rows = json.loads(result.stdout)
    if (not isinstance(rows, list) or len(rows) != 1 or rows[0].get("ifname") != interface
            or "UP" not in rows[0].get("flags", [])):
        raise ValueError("configured USB link is not up")
    matches = []
    for value in rows[0].get("addr_info", []):
        if value.get("family") != "inet" or value.get("scope") != "global":
            continue
        address = private_ip(value["local"])
        network = ipaddress.IPv4Network(f"{address}/{value['prefixlen']}", strict=False)
        if address != phone and phone in network and phone not in (network.network_address, network.broadcast_address):
            matches.append(str(address))
    if len(matches) != 1:
        raise ValueError("one USB address on the verified phone subnet required")
    return Endpoint(interface, matches[0], str(phone), port)


def bind(endpoint, *, socket_factory=socket.socket):
    sock = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Do not let an address later appearing on Wi-Fi/Tailscale expose this socket.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, endpoint.interface.encode() + b"\0")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((endpoint.local_ip, endpoint.port))
        sock.listen(16)
        sock.setblocking(False)
        return sock
    except Exception:
        sock.close()
        raise
