import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from xenova_display.mobile_network import Endpoint, bind, resolve


@pytest.fixture
def usb(tmp_path):
    sysfs = tmp_path / "net"
    subsystem = tmp_path / "bus/usb"
    subsystem.mkdir(parents=True)
    device = sysfs / "usb0/device"
    device.mkdir(parents=True)
    (device / "subsystem").symlink_to(subsystem)
    config = {"schema_version": 1, "usb_interface": "usb0", "phone_ipv4": "192.168.42.129", "port": 8084}
    info = [{"ifindex": 7, "ifname": "usb0", "flags": ["UP", "BROADCAST"], "addr_info": [
        {"family": "inet", "scope": "global", "local": "192.168.42.2", "prefixlen": 24}]}]
    return sysfs, config, info


def read(usb):
    sysfs, config, info = usb
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=json.dumps(info))
    endpoint = resolve(config, sysfs=sysfs, run=run)
    return endpoint, calls


def test_usb_address_is_read_without_ip_assignment_or_route_changes(usb):
    endpoint, calls = read(usb)
    assert endpoint == Endpoint("usb0", "192.168.42.2", "192.168.42.129", 8084, 7)
    assert endpoint.origin == "http://192.168.42.2:8084"
    assert calls[0][0] == ["ip", "-j", "-4", "address", "show", "dev", "usb0"]
    assert calls[0][1]["timeout"] == 2


@pytest.mark.parametrize("key,value", [("usb_interface", "../wlan0"), ("usb_interface", "VERIFY_USB_INTERFACE"),
    ("phone_ipv4", "100.87.115.29"), ("phone_ipv4", "8.8.8.8"), ("phone_ipv4", "127.0.0.1"),
    ("phone_ipv4", "192.168.43.1"), ("phone_ipv4", "192.168.42.2"), ("phone_ipv4", "192.168.42.255"),
    ("port", 8081), ("port", 8082), ("port", 8083), ("port", 80), ("port", True), ("schema_version", True)])
def test_unverified_or_unsafe_configuration_is_rejected(usb, key, value):
    usb[1][key] = value
    with pytest.raises((ValueError, OSError)): read(usb)


def test_non_usb_interface_is_not_used_as_a_wireless_fallback(usb, tmp_path):
    sysfs, _, _ = usb
    subsystem = tmp_path / "bus/pci"
    subsystem.mkdir()
    (sysfs / "usb0/device/subsystem").unlink()
    (sysfs / "usb0/device/subsystem").symlink_to(subsystem)
    with pytest.raises(ValueError, match="USB"): read(usb)


@pytest.mark.parametrize("change", ["down", "no-address", "ambiguous", "no-index", "bad-index"])
def test_down_missing_or_ambiguous_link_does_not_guess_an_address(usb, change):
    info = usb[2]
    if change == "down": info[0]["flags"] = []
    if change == "no-index": del info[0]["ifindex"]
    if change == "bad-index": info[0]["ifindex"] = True
    if change == "no-address": info[0]["addr_info"] = []
    if change == "ambiguous": info[0]["addr_info"].append({"family": "inet", "scope": "global", "local": "192.168.42.3", "prefixlen": 24})
    with pytest.raises(ValueError): read(usb)


def test_listener_is_bound_to_the_usb_device_and_specific_address():
    calls = []
    class Socket:
        def setsockopt(self, *args): calls.append(("option", *args))
        def bind(self, *args): calls.append(("bind", *args))
        def listen(self, *args): calls.append(("listen", *args))
        def setblocking(self, *args): calls.append(("blocking", *args))
        def close(self): calls.append(("close",))
    sock = Socket()
    assert bind(Endpoint("usb0", "192.168.42.2", "192.168.42.129", 8084), socket_factory=lambda *_: sock) is sock
    assert ("option", socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"usb0\0") in calls
    assert ("bind", ("192.168.42.2", 8084)) in calls


def test_interface_binding_failure_closes_socket_and_does_not_fall_back():
    calls = []
    class Socket:
        def setsockopt(self, *args): raise PermissionError("no device capability")
        def close(self): calls.append("closed")
    with pytest.raises(PermissionError):
        bind(Endpoint("usb0", "192.168.42.2", "192.168.42.129", 8084), socket_factory=lambda *_: Socket())
    assert calls == ["closed"]
