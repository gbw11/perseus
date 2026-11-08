"""xl 출력 파싱과 XenDriver 조회 경로를 FakeRunner 로 검증한다. 실제 xl 은 호출하지 않는다."""

import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from dom0_manager.config import Settings
from dom0_manager.drivers.mock import MockProvisioner
from dom0_manager.drivers.xen import XenDriver
from dom0_manager.drivers.xl import (
    CommandResult,
    SshRunner,
    Xl,
    map_state,
    parse_xl_info,
    parse_xl_list,
    parse_xl_list_long,
)
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
host                   : xenova
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
        ("-bp---", VmState.paused),  # 실보드: xl pause 한 유휴 도메인은 b 와 p 가 같이 선다
        ("---s--", VmState.stopping),
        ("---sr-", VmState.stopping),  # shutdown reason: reboot
        ("---sw-", VmState.stopping),  # shutdown reason: watchdog
        ("---sS-", VmState.stopping),  # shutdown reason: soft reset
        ("-----d", VmState.stopping),
        ("---s-d", VmState.stopping),
        ("---sc-", VmState.crashed),  # shutdown reason: crash (on_crash=preserve 로 남은 도메인)
    ],
)
def test_map_state(flags, expected):
    assert map_state(flags) == expected


@pytest.mark.parametrize("flags", ["x-----", "rb----x", "-----", "---s?-x", "c-----"])
def test_unknown_state_flag_is_not_silently_running(flags):
    with pytest.raises(DriverError):
        map_state(flags)


def test_board_fixture_xen_4_21_diskless_list():
    """platform/tests/fixtures/xen-4.21/diskless-list.txt (플랫폼 파트 실보드 Xen 4.21 출력) 그대로."""
    rows = parse_xl_list("Name ID Mem VCPUs State Time(s)\nDomain-0 0 1024 4 r----- 0\nzephyr-dev 1 16 1 -b---- 0\n")
    assert [(r.name, r.domid, r.mem_mib, r.vcpus, map_state(r.flags)) for r in rows] == [
        ("Domain-0", 0, 1024, 4, VmState.running),
        ("zephyr-dev", 1, 16, 1, VmState.running),
    ]


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


def test_xen_driver_skips_a_domain_that_vanished_between_list_and_list_long():
    long_without_debian = json.dumps([entry for entry in json.loads(XL_LIST_LONG) if entry["domid"] != 4])
    runner = FakeRunner({("xl", "list"): XL_LIST, ("xl", "list", "-l"): long_without_debian})
    domains = XenDriver(Xl(runner, query_timeout_s=5)).list_domains()
    assert [d.vm_id for d in domains] == [ALPINE]


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


# ---- SSH 러너: 개발 PC 에서 보드의 xl 을 부른다 ----


class RecordingRunner:
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.calls: list[tuple[list[str], float]] = []

    def run(self, args, *, timeout_s):
        self.calls.append(([str(a) for a in args], timeout_s))
        return self.result


def test_ssh_runner_wraps_xl_command_in_batch_mode_ssh():
    inner = RecordingRunner(CommandResult(0, XL_LIST, ""))
    runner = SshRunner("root@100.64.0.1", runner=inner)
    xl = Xl(runner, query_timeout_s=5)
    assert [r.name for r in xl.list()] == ["Domain-0", "alpine-drive", "debian-safety"]
    argv, timeout = inner.calls[0]
    assert argv == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "root@100.64.0.1", "--", "xl", "list"]
    assert timeout == 5


def test_ssh_runner_connection_failure_is_driver_error_not_xl_error():
    inner = RecordingRunner(CommandResult(255, "", "Permission denied (publickey)."))
    xl = Xl(SshRunner("root@100.64.0.1", runner=inner))
    with pytest.raises(DriverError, match="ssh to root@100.64.0.1 failed"):
        xl.info()
    assert XenDriver(xl).health() is False


def test_ssh_runner_timeout_names_the_remote_command():
    class Hanging:
        def run(self, args, *, timeout_s):
            raise DriverTimeout("ssh -o timed out after 3s")

    xl = Xl(SshRunner("root@100.64.0.1", runner=Hanging()), query_timeout_s=3)
    with pytest.raises(DriverTimeout, match="xl info over ssh to root@100.64.0.1 timed out after 3s"):
        xl.info()


def test_ssh_runner_rejects_bad_target():
    with pytest.raises(ValueError):
        SshRunner("")
    with pytest.raises(ValueError):
        SshRunner("root@host extra")


def test_build_driver_uses_ssh_runner_when_configured():
    from dom0_manager.drivers import build_driver

    local = build_driver(Settings(driver="xen"))
    remote = build_driver(Settings(driver="xen", xl_ssh_target="root@100.64.0.1"))
    assert type(local._xl._runner).__name__ == "SubprocessRunner"
    assert isinstance(remote._xl._runner, SshRunner) and remote._xl._runner.target == "root@100.64.0.1"
    assert Settings.from_env({"XENOVA_DRIVER": "xen", "XENOVA_XL_SSH": " root@100.64.0.1 "}).xl_ssh_target == "root@100.64.0.1"


# ---- 실보드 픽스처: RPi4 rev 1.4, Xen 4.21.1-pre, dom0 Linux 6.18 (tests/fixtures/rpi4-xen-4.21, 2026-10-20 수집) ----

BOARD = Path(__file__).parent / "fixtures" / "rpi4-xen-4.21"
DEBIAN_DEV = UUID("4be41d5f-b8ef-408b-9709-7fe5ac11231f")
ZEPHYR_A = UUID("0d950ea2-cc99-49df-9b60-a4ddf2c8e137")
ZEPHYR_B = UUID("cd35a7fc-5f14-456a-a129-f0afc7d91d97")


def _board_text(name: str) -> str:
    return (BOARD / name).read_text(encoding="utf-8")


def _board_runner(list_file: str = "xl-list.txt") -> FakeRunner:
    return FakeRunner(
        {
            ("xl", "list"): _board_text(list_file),
            ("xl", "list", "-l"): _board_text("xl-list-l.json"),
            ("xl", "info"): _board_text("xl-info.txt"),
        }
    )


def test_board_xen_driver_lists_guests_and_skips_dom0_without_uuid():
    """실보드의 `xl list -l` 에서 Domain-0 은 c_info 에 uuid 가 없다. 목록은 그래도 게스트 셋만 UUID 로 낸다."""
    driver = XenDriver(Xl(_board_runner()))
    assert [(d.name, d.domid, d.vm_id, d.state, d.vcpus, d.memory_mib) for d in driver.list_domains()] == [
        ("debian-dev", 1, DEBIAN_DEV, VmState.running, 1, 512),
        ("zephyr-drive-a", 2, ZEPHYR_A, VmState.running, 1, 16),
        ("zephyr-drive-b", 3, ZEPHYR_B, VmState.running, 1, 16),
    ]
    info = driver.host_info()
    assert (info.total_memory_mib, info.free_memory_mib, info.nr_cpus) == (8052, 6347, 4)


def test_board_xl_list_v_uuid_column_matches_list_l():
    """`xl list -v` 의 UUID 열과 `xl list -l` 의 c_info.uuid 는 같다. 헤더에 탭·공백이 섞여도 split 으로 읽힌다."""
    lines = _board_text("xl-list-v.txt").splitlines()[1:]
    verbose = {int(parts[1]): parts[6] for parts in (line.split() for line in lines)}
    assert verbose[0] == "00000000-0000-0000-0000-000000000000"
    for entry in parse_xl_list_long(_board_text("xl-list-l.json")):
        c_info = entry["config"]["c_info"]
        if entry["domid"] == 0:
            assert "uuid" not in c_info
        else:
            assert c_info["uuid"] == verbose[entry["domid"]]
    assert [r.flags for r in parse_xl_list(_board_text("xl-list-v.txt"))] == ["r-----", "-b----", "-b----", "-b----"]


def test_board_paused_guest_is_paused_over_http():
    """`xl pause` 한 유휴 게스트는 `-bp---` 다. b 가 같이 서도 paused 로 내고 나머지는 running 그대로다."""
    driver = XenDriver(Xl(_board_runner("xl-list-paused.txt")))
    client = TestClient(create_app(Settings(driver="xen"), driver=driver, provisioner=MockProvisioner()))
    assert {vm["vm_id"]: vm["state"] for vm in client.get("/api/v1/vms").json()} == {
        str(DEBIAN_DEV): "running",
        str(ZEPHYR_A): "running",
        str(ZEPHYR_B): "paused",
    }
