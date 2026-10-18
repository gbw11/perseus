"""xl 출력 파싱과 XenDriver 조회 경로를 FakeRunner 로 검증한다. 실제 xl 은 호출하지 않는다."""

import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from dom0_manager.config import Settings
from dom0_manager.drivers.mock import MockProvisioner
from dom0_manager.drivers.xen import XenDriver
from dom0_manager.drivers.xl import CommandResult, Xl, map_state, parse_xl_info, parse_xl_list
from dom0_manager.errors import DriverError, DriverTimeout
from dom0_manager.main import create_app
from dom0_manager.models import VmState

ALPINE = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DEBIAN = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

XL_LIST = """\
Name                                        ID   Mem VCPUs      State   Time(s)
Domain-0                                     0  1024     4     r-----     123.4
alpine-drive                                 3   256     1     -b----      10.2
debian-safety                                4   512     2     --p---       3.0
"""

XL_LIST_LONG = json.dumps(
    [
        {"domid": 0, "config": {"c_info": {"name": "Domain-0", "uuid": "00000000-0000-0000-0000-000000000000"}}},
        {"domid": 3, "config": {"c_info": {"name": "alpine-drive", "uuid": str(ALPINE)}}},
        {"domid": 4, "config": {"c_info": {"name": "debian-safety", "uuid": str(DEBIAN)}}},
    ]
)

XL_INFO = """\
host                   : perseus
release                : 6.6.0
nr_cpus                : 4
total_memory           : 7936
free_memory            : 6433
xen_version            : 4.18
"""


class FakeRunner:
    def __init__(self, outputs: dict[tuple[str, ...], str], *, fail: dict[tuple[str, ...], Exception] | None = None):
        self.outputs = outputs
        self.fail = fail or {}
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def run(self, args, *, timeout_s):
        key = tuple(args)
        self.calls.append((key, timeout_s))
        if key in self.fail:
            raise self.fail[key]
        if key not in self.outputs:
            return CommandResult(1, "", f"fake: no output for {key}")
        return CommandResult(0, self.outputs[key], "")


def _healthy_runner(fail: dict[tuple[str, ...], Exception] | None = None):
    return FakeRunner(
        {("xl", "list"): XL_LIST, ("xl", "list", "-l"): XL_LIST_LONG, ("xl", "info"): XL_INFO},
        fail=fail,
    )


@pytest.mark.parametrize(
    "flags,expected",
    [
        ("r-----", VmState.running),
        ("-b----", VmState.running),
        ("------", VmState.running),
        ("--p---", VmState.paused),
        ("---s--", VmState.stopping),
        ("-----d", VmState.stopping),
        ("----c-", VmState.crashed),
        ("-b--c-", VmState.crashed),
    ],
)
def test_map_state(flags, expected):
    assert map_state(flags) == expected


def test_unknown_state_flag_is_not_silently_running():
    with pytest.raises(DriverError):
        map_state("x-----")


def test_parse_xl_list_and_info():
    rows = parse_xl_list(XL_LIST)
    assert [(r.name, r.domid, r.mem_mib, r.vcpus, r.flags) for r in rows] == [
        ("Domain-0", 0, 1024, 4, "r-----"),
        ("alpine-drive", 3, 256, 1, "-b----"),
        ("debian-safety", 4, 512, 2, "--p---"),
    ]
    info = parse_xl_info(XL_INFO)
    assert (info.free_memory_mib, info.total_memory_mib, info.nr_cpus) == (6433, 7936, 4)


def test_xen_driver_lists_domains_without_dom0_and_uses_uuid_as_vm_id():
    runner = _healthy_runner()
    driver = XenDriver(Xl(runner, query_timeout_s=5, command_timeout_s=30))
    domains = driver.list_domains()
    assert [(d.vm_id, d.state, d.vcpus, d.memory_mib, d.domid) for d in domains] == [
        (ALPINE, VmState.running, 1, 256, 3),
        (DEBIAN, VmState.paused, 2, 512, 4),
    ]
    assert driver.get_domain(DEBIAN).name == "debian-safety"
    assert all(timeout == 5 for _, timeout in runner.calls)


def test_xen_driver_health_false_on_timeout_or_failure():
    timed_out = XenDriver(Xl(_healthy_runner(fail={("xl", "info"): DriverTimeout("xl info timed out")})))
    assert timed_out.health() is False
    failing = XenDriver(Xl(FakeRunner({})))
    assert failing.health() is False
    assert XenDriver(Xl(_healthy_runner())).health() is True


def test_xen_driver_over_http_read_only():
    driver = XenDriver(Xl(_healthy_runner()))
    client = TestClient(create_app(Settings(driver="xen"), driver=driver, provisioner=MockProvisioner()))

    assert client.get("/healthz").status_code == 200
    assert [vm["vm_id"] for vm in client.get("/api/v1/vms").json()] == [str(ALPINE), str(DEBIAN)]
    assert client.get(f"/api/v1/vms/{ALPINE}").json()["state"] == "running"
    assert client.get("/api/v1/vms/cccccccc-cccc-4ccc-8ccc-cccccccccccc").status_code == 404

    not_yet = client.post(f"/api/v1/vms/{ALPINE}/stop")
    assert not_yet.status_code == 501
    assert "S15P31E110-115" in not_yet.json()["message"]


def test_xen_driver_503_when_xl_hangs():
    driver = XenDriver(Xl(_healthy_runner(fail={("xl", "info"): DriverTimeout("xl info timed out")})))
    client = TestClient(create_app(Settings(driver="xen"), driver=driver, provisioner=MockProvisioner()))
    assert client.get("/healthz").status_code == 503


def test_xl_failure_is_driver_error_with_trimmed_detail():
    runner = FakeRunner({})
    runner.outputs = {}
    xl = Xl(runner)
    with pytest.raises(DriverError) as exc:
        xl.list()
    assert "exit 1" in exc.value.message
    assert exc.value.detail.startswith("fake: no output")
