"""xentop 배치 출력 파싱, 통계 수집, GET /api/v1/stats (S15P31E110-112). 실제 xentop 은 부르지 않는다."""

import json
from pathlib import Path
from datetime import datetime, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from dom0_manager.config import Settings
from dom0_manager.drivers.mock import MockProvisioner
from dom0_manager.drivers.xen import XenDriver
from dom0_manager.drivers.xentop import XENTOP_ARGS, Xentop, collect_stats, map_xentop_state, parse_xentop
from dom0_manager.drivers.xl import CommandResult, CommandUnavailable, Xl
from dom0_manager.errors import DriverError, DriverTimeout, PlatformUnavailable
from dom0_manager.main import create_app
from dom0_manager.models import VmState
from test_xl import FakeRunner

DEBIAN = UUID("4be41d5f-b8ef-408b-9709-7fe5ac11231f")
ZEPHYR = UUID("0d950ea2-cc99-49df-9b60-a4ddf2c8e137")
BROKEN = UUID("cd35a7fc-5f14-456a-a129-f0afc7d91d97")
SAMPLED = datetime(2026, 10, 21, 1, 23, 45, 120000, tzinfo=timezone.utc)

# xentop.c fields[] 순서. Domain-0 은 MAXMEM 이 "no limit"/"n/a"(토큰 수가 다름), broken-disk 는 VBD 오류('-').
XENTOP = """\
      NAME  STATE   CPU(sec) CPU(%)     MEM(k) MEM(%)  MAXMEM(k) MAXMEM(%) VCPUS NETS NETTX(k) NETRX(k) VBDS   VBD_OO   VBD_RD   VBD_WR  VBD_RSECT  VBD_WSECT SSID
  Domain-0 -----r        123    0.0    1048576   12.7   no limit       n/a     4    0        0        0    0        0        0        0          0          0    0
debian-dev --b---         11    0.0     524288    6.4     524288       6.4     1    1      123      456    1        0      100      200       1600       3200    0
Net0 RX:   466944bytes     1234pkts        0err        0drop  TX:   125952bytes      987pkts        0err        0drop
VBD BlkBack 51712 [ca: 0] OO:        0   RD:      100   WR:      200   RSECT:       1600   WSECT:       3200
zephyr-drive-a --b---      0    0.0      16384    0.2      16384       0.2     1    0        0        0    0        0        0        0          0          0    0
broken-disk ---c--          5    0.0     262144    3.2     262144       3.2     1    1       10       20    1        -        -        -          -          -    0
Net0 RX:    20480bytes       20pkts        0err        0drop  TX:    10240bytes       10pkts        0err        0drop
VBD BlkBack 51728 [ca:10] OO:        -   RD:        -   WR:        -   RSECT:          -   WSECT:          -
manual-vm -----r            1    0.0      65536    0.8      65536       0.8     1    0        0        0    0        0        0        0          0          0    0
ghost-vm -----r             2    0.0      65536    0.8      65536       0.8     1    0        0        0    0        0        0        0          0          0    0
"""
GHOST = UUID("99999999-9999-4999-8999-999999999999")  # xl 로 직접 띄워 uuid 는 있지만 registry 에 없는 도메인

XL_LIST_LONG = json.dumps(
    [
        {"domid": 0, "config": {"c_info": {"name": "Domain-0"}}},
        {"domid": 1, "config": {"c_info": {"name": "debian-dev", "uuid": str(DEBIAN)}}},
        {"domid": 2, "config": {"c_info": {"name": "zephyr-drive-a", "uuid": str(ZEPHYR)}}},
        {"domid": 3, "config": {"c_info": {"name": "broken-disk", "uuid": str(BROKEN)}}},
        {"domid": 4, "config": {"c_info": {"name": "manual-vm"}}},  # uuid 없음 (비정상 입력)
        {"domid": 5, "config": {"c_info": {"name": "ghost-vm", "uuid": str(GHOST)}}},  # Xen 이 자동 발급한 uuid, registry 없음
    ]
)
XL_INFO = "host : x\nnr_cpus : 4\ntotal_memory : 8052\nfree_memory : 6347\n"


def _runner(xentop_text: str = XENTOP, *, fail=None) -> FakeRunner:
    return FakeRunner(
        {XENTOP_ARGS: xentop_text, ("xl", "list", "-l"): XL_LIST_LONG, ("xl", "info"): XL_INFO},
        fail=fail,
    )


def _driver(runner: FakeRunner) -> XenDriver:
    return XenDriver(Xl(runner, query_timeout_s=5), xentop=Xentop(runner, timeout_s=5, clock=lambda: SAMPLED))


# ---- 파서 ----


@pytest.mark.parametrize(
    "flags,expected",
    [
        ("-----r", VmState.running),
        ("--b---", VmState.running),
        ("------", VmState.running),
        ("----p-", VmState.paused),
        ("--b-p-", VmState.paused),
        ("-s----", VmState.stopping),
        ("d-----", VmState.stopping),
        ("---c--", VmState.crashed),
        ("-s-c--", VmState.crashed),  # crashed 가 shutdown 보다 우선
    ],
)
def test_map_xentop_state(flags, expected):
    assert map_xentop_state(flags) == expected


@pytest.mark.parametrize("flags", ["r-----", "-b----", "-----", "x-----", "--b---x"])
def test_xl_order_or_unknown_flags_are_rejected(flags):
    """xentop 은 d s b c p r 순서다. xl list 순서(r b p s …)를 그대로 넣으면 조용히 running 이 되지 않고 실패한다."""
    with pytest.raises(DriverError):
        map_xentop_state(flags)


def test_parse_rows_devices_and_no_limit():
    rows = parse_xentop(XENTOP)
    assert [r.name for r in rows] == ["Domain-0", "debian-dev", "zephyr-drive-a", "broken-disk", "manual-vm", "ghost-vm"]
    dom0, debian, zephyr, broken, _, _ = rows
    assert (dom0.maxmem_k, dom0.maxmem_pct, dom0.vcpus, dom0.cpu_sec) == (None, None, 4, 123)
    assert (debian.flags, debian.mem_k, debian.maxmem_k, debian.nets, debian.nettx_k, debian.netrx_k) == ("--b---", 524288, 524288, 1, 123, 456)
    assert (debian.vbd_rd, debian.vbd_wr, debian.vbd_rsect, debian.vbd_wsect) == (100, 200, 1600, 3200)
    assert [(d.rx_bytes, d.tx_bytes) for d in debian.net_devices] == [(466944, 125952)]
    assert [(d.rd, d.wr, d.rsect, d.wsect) for d in debian.vbd_devices] == [(100, 200, 1600, 3200)]
    assert (zephyr.nets, zephyr.vbds, zephyr.net_devices, zephyr.vbd_devices) == (0, 0, [], [])
    assert (broken.vbd_oo, broken.vbd_rsect, broken.vbd_wsect) == (None, None, None)
    assert broken.vbd_devices[0].rsect is None and broken.net_devices[0].rx_bytes == 20480


def test_last_iteration_wins_when_header_repeats():
    second = XENTOP.replace("debian-dev --b---         11", "debian-dev --b---         12")
    rows = parse_xentop(XENTOP + second)
    assert [r.cpu_sec for r in rows if r.name == "debian-dev"] == [12]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "debian-dev --b--- 11 0.0 524288 6.4 524288 6.4 1 1 123 456 1 0 100 200 1600 3200 0\n",  # 헤더 없음
        "NAME STATE\ndebian-dev --b--- 11 0.0\n",  # 열 부족
        "NAME STATE\ndebian-dev --b--- x 0.0 524288 6.4 524288 6.4 1 1 123 456 1 0 100 200 1600 3200 0\n",  # 숫자 아님
        "NAME STATE\nNet0 RX: 1bytes 1pkts 0err 0drop  TX: 1bytes 1pkts 0err 0drop\n",  # 도메인 전 장치 줄
    ],
)
def test_malformed_output_is_driver_error(text):
    with pytest.raises(DriverError):
        parse_xentop(text)


# ---- 수집: 이름 ↔ vm_id 대조, unavailable 사유 ----


def test_collect_stats_maps_names_to_vm_ids_and_marks_unavailable():
    runner = _runner()
    sample = collect_stats(Xentop(runner, clock=lambda: SAMPLED), Xl(runner))
    assert sample.sampled_at == SAMPLED
    by_id = {d.vm_id: d for d in sample.domains}
    assert set(by_id) == {DEBIAN, ZEPHYR, BROKEN, GHOST}  # 진단 모드: uuid 있는 도메인 전부. Domain-0 과 uuid 없는 manual-vm 은 없다

    debian = by_id[DEBIAN]
    assert (debian.domid, debian.state, debian.vcpus, debian.cpu_time_s, debian.memory_mib, debian.max_memory_mib) == (1, VmState.running, 1, 11, 512, 512)
    assert (debian.net_rx_bytes, debian.net_tx_bytes) == (466944, 125952)  # 장치 줄의 정확한 바이트 (KiB 열 456*1024 가 아님)
    assert (debian.disk_rd_bytes, debian.disk_wr_bytes) == (1600 * 512, 3200 * 512)
    assert debian.unavailable == {}

    zephyr = by_id[ZEPHYR]
    assert (zephyr.memory_mib, zephyr.max_memory_mib, zephyr.net_rx_bytes, zephyr.disk_rd_bytes) == (16, 16, None, None)
    assert zephyr.unavailable == {k: "NO_DEVICE" for k in ("net_rx_bytes", "net_tx_bytes", "disk_rd_bytes", "disk_wr_bytes")}

    broken = by_id[BROKEN]
    assert broken.state == VmState.crashed
    assert (broken.net_rx_bytes, broken.net_tx_bytes, broken.disk_rd_bytes, broken.disk_wr_bytes) == (20480, 10240, None, None)
    assert broken.unavailable == {"disk_rd_bytes": "DEVICE_ERROR", "disk_wr_bytes": "DEVICE_ERROR"}


@pytest.mark.parametrize(
    "line,rsect",
    [
        ("VBD BlkBack 51712 [ca: 0] OO:        0   RD:      100   WR:      200   RSECT:       1600   WSECT:       3200", 1600),
        ("VBD BlkBack 51728 [ca:10] OO:        0   RD:      100   WR:      200   RSECT:       1600   WSECT:       3200", 1600),
        ("VBD BlkTap 51712  OO:        0   RD:      100   WR:      200   RSECT:       1600   WSECT:       3200", 1600),  # details 없음
        ("VBD Unidentified 51712 [ca: 0] OO:        -   RD:        -   WR:        -   RSECT:          -   WSECT:          -", None),
    ],
)
def test_vbd_line_details_may_contain_spaces_or_be_empty(line, rsect):
    """Linux 의 장치 정보는 "[%2x:%2x] " 라 minor 가 한 자리면 대괄호 안에 공백이 있다. 토큰 하나로 읽으면 도메인 행으로 오인해 깨진다."""
    header = XENTOP.splitlines()[0]
    row = "debian-dev --b--- 11 0.0 524288 6.4 524288 6.4 1 0 0 0 1 0 100 200 1600 3200 0"
    rows = parse_xentop("\n".join([header, row, line]) + "\n")
    assert len(rows) == 1 and rows[0].vbd_devices[0].rsect == rsect


def _records(*pairs):
    return {name: {"schema": 1, "id": name, "name": name, "uuid": str(vm_id)} for vm_id, name in pairs}


class _Manager:
    data = Path("/srv/xenova")


def test_platform_mode_keeps_only_registered_vms():
    """platform 모드는 플랫폼 load_records 가 돌려준 등록 VM 만 관리 대상이다. uuid 가 있어도 거기 없으면(ghost-vm) 뺀다."""
    from dom0_manager.drivers.platform import PlatformDriver

    records = _records((DEBIAN, "debian-dev"), (ZEPHYR, "zephyr-drive-a"), (BROKEN, "broken-disk"))
    runner = _runner()
    driver = PlatformDriver(_Manager(), Xl(runner), xentop=Xentop(runner, clock=lambda: SAMPLED), records_loader=lambda: records)
    assert {d.vm_id for d in driver.collect_stats().domains} == {DEBIAN, ZEPHYR, BROKEN}
    assert driver.registered_vm_ids() == {DEBIAN, ZEPHYR, BROKEN}

    with pytest.raises(PlatformUnavailable, match="record loader is not configured"):
        PlatformDriver(_Manager(), Xl(runner), xentop=Xentop(runner)).collect_stats()


@pytest.mark.parametrize(
    "failure,match",
    [
        (RuntimeError("instance directory/id mismatch"), "registry is invalid; platform recovery required"),
        (RuntimeError("linked/noncanonical asset path"), "registry is invalid"),
        (FileNotFoundError("instance.json"), "registry is invalid"),
        ({"corrupt": {"schema": 1, "id": "corrupt", "name": "corrupt"}}, "record corrupt has no valid uuid"),
    ],
)
def test_registry_rejected_by_platform_is_503_not_partial_200(failure, match):
    """플랫폼 load_records 가 거부하는 상태(심볼릭 링크·id 불일치·중복·누락)는 복구 필요다. 그 VM 만 빼고 200 을 내지 않는다."""
    from dom0_manager.drivers.platform import PlatformDriver

    def loader():
        if isinstance(failure, Exception):
            raise failure
        return failure

    runner = _runner()
    driver = PlatformDriver(_Manager(), Xl(runner), xentop=Xentop(runner), records_loader=loader)
    with pytest.raises(PlatformUnavailable, match=match) as info:
        driver.collect_stats()
    if isinstance(failure, Exception):
        assert info.value.detail.startswith(type(failure).__name__)
    assert runner.calls == []  # Xen 명령 전에 끝난다


def test_platform_stats_deadline_includes_registry_scan():
    """registry 조회가 2초 걸리면 xentop 과 xl list -l 에는 3초만 남는다. 5초를 다 쓰면 Xen 명령 없이 504 다."""
    from dom0_manager.drivers.platform import PlatformDriver

    clock = [100.0]
    scan_s = [2.0]

    def slow_loader():
        clock[0] += scan_s[0]
        return _records((DEBIAN, "debian-dev"))

    runner = _runner()
    driver = PlatformDriver(
        _Manager(), Xl(runner), xentop=Xentop(runner, timeout_s=5, clock=lambda: SAMPLED), records_loader=slow_loader,
        monotonic=lambda: clock[0],
    )
    assert [d.vm_id for d in driver.collect_stats().domains] == [DEBIAN]
    assert [(args, timeout) for args, timeout in runner.calls] == [(XENTOP_ARGS, 3.0), (("xl", "list", "-l"), 3.0)]

    scan_s[0] = 5.0
    slow = PlatformDriver(_Manager(), Xl(_runner()), xentop=Xentop(_runner(), timeout_s=5), records_loader=slow_loader, monotonic=lambda: clock[0])
    with pytest.raises(DriverTimeout, match="exhausted by registry scan"):
        slow.collect_stats()


def test_load_records_loader_calls_platform_library_read_only(monkeypatch):
    """조립 경로: 플랫폼 xenova_vm.load_records 를 data 경로와 validate_assets=False 로 부른다.
    (모듈 부재는 xenova_management 가 먼저 import 하므로 서비스 시작 단계에서 load_manager 가 503 으로 거부한다.)"""
    import sys
    import types

    from dom0_manager.drivers.platform import load_records_loader

    calls = []
    fake = types.ModuleType("xenova_vm")

    def load_records(data, *, validate_assets=True):
        calls.append((data, validate_assets))
        return _records((DEBIAN, "debian-dev"))

    fake.load_records = load_records
    monkeypatch.setitem(sys.modules, "xenova_vm", fake)
    loader = load_records_loader("", _Manager())
    assert set(loader()) == {"debian-dev"} and calls == [(Path("/srv/xenova"), False)]


def test_stats_request_shares_one_deadline_between_xentop_and_xl():
    """조회 한도 5초는 요청 전체다. xentop 이 3초 썼으면 xl list -l 에는 2초만 준다. 다 쓰면 xl 을 부르지 않고 504 다."""
    clock = [100.0]

    class SlowXentop(FakeRunner):
        def __init__(self, xentop_s: float):
            super().__init__({XENTOP_ARGS: XENTOP, ("xl", "list", "-l"): XL_LIST_LONG})
            self.xentop_s = xentop_s

        def run(self, args, *, timeout_s):
            if tuple(args) == XENTOP_ARGS:
                clock[0] += self.xentop_s
            return super().run(args, timeout_s=timeout_s)

    runner = SlowXentop(3.0)
    collect_stats(Xentop(runner, timeout_s=5), Xl(runner), monotonic=lambda: clock[0])
    assert [(args, timeout) for args, timeout in runner.calls] == [(XENTOP_ARGS, 5), (("xl", "list", "-l"), 2.0)]

    shorter = SlowXentop(1.0)  # 상위가 준 예산이 자체 시한보다 짧으면 xentop 도 그만큼만 기다린다
    collect_stats(Xentop(shorter, timeout_s=5), Xl(shorter), budget_s=3.0, monotonic=lambda: clock[0])
    assert [(args, timeout) for args, timeout in shorter.calls] == [(XENTOP_ARGS, 3.0), (("xl", "list", "-l"), 2.0)]

    exhausted = SlowXentop(5.0)
    with pytest.raises(DriverTimeout, match="deadline of 5s exhausted"):
        collect_stats(Xentop(exhausted, timeout_s=5), Xl(exhausted), monotonic=lambda: clock[0])
    assert [args for args, _ in exhausted.calls] == [XENTOP_ARGS]


def test_net_falls_back_to_kib_columns_without_device_lines():
    text = "\n".join(line for line in XENTOP.splitlines() if not line.startswith(("Net", "VBD"))) + "\n"
    runner = _runner(text)
    sample = collect_stats(Xentop(runner, clock=lambda: SAMPLED), Xl(runner))
    debian = next(d for d in sample.domains if d.vm_id == DEBIAN)
    assert (debian.net_rx_bytes, debian.net_tx_bytes) == (456 * 1024, 123 * 1024)
    assert (debian.disk_rd_bytes, debian.disk_wr_bytes) == (1600 * 512, 3200 * 512)  # 열은 장치 줄 없이도 섹터 합계다


# ---- HTTP ----


def test_stats_endpoint_in_xen_mode():
    runner = _runner()
    client = TestClient(create_app(Settings(driver="xen"), driver=_driver(runner), provisioner=MockProvisioner()))
    response = client.get("/api/v1/stats")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sampled_at"] == "2026-10-21T01:23:45.120000Z"
    vms = {vm["vm_id"]: vm for vm in body["vms"]}
    assert set(vms) == {str(DEBIAN), str(ZEPHYR), str(BROKEN), str(GHOST)}  # xen 진단 모드는 registry 없이 전부
    debian = vms[str(DEBIAN)]
    assert debian == {
        "vm_id": str(DEBIAN),
        "domid": 1,
        "source": "xen",
        "sampled_at": "2026-10-21T01:23:45.120000Z",
        "state": "running",
        "vcpus": 1,
        "cpu_time_s": 11,
        "memory_mib": 512,
        "max_memory_mib": 512,
        "net_rx_bytes": 466944,
        "net_tx_bytes": 125952,
        "disk_rd_bytes": 819200,
        "disk_wr_bytes": 1638400,
        "unavailable": {},
    }
    assert vms[str(ZEPHYR)]["net_rx_bytes"] is None and vms[str(ZEPHYR)]["unavailable"]["net_rx_bytes"] == "NO_DEVICE"
    assert vms[str(BROKEN)]["state"] == "crashed" and vms[str(BROKEN)]["unavailable"]["disk_wr_bytes"] == "DEVICE_ERROR"
    assert [args for args, _ in runner.calls] == [XENTOP_ARGS, ("xl", "list", "-l")]
    (_, xentop_timeout), (_, xl_timeout) = runner.calls
    assert xentop_timeout == 5 and 0 < xl_timeout <= 5  # xl list -l 은 xentop 이 쓰고 남은 시간만 받는다


def test_stats_endpoint_in_mock_mode():
    from dom0_manager.drivers.mock import MockDriver

    driver = MockDriver(host_cpus=4, total_memory_mib=4096)
    driver.seed_samples()
    client = TestClient(create_app(Settings(driver="mock", log_level="WARNING"), driver=driver, provisioner=MockProvisioner()))
    body = client.get("/api/v1/stats").json()
    assert len(body["vms"]) == 3
    assert {vm["source"] for vm in body["vms"]} == {"xen"}
    assert all(isinstance(vm["cpu_time_s"], int) and vm["unavailable"] == {} for vm in body["vms"])


@pytest.mark.parametrize(
    "failure,status,code",
    [
        ({"fail": {XENTOP_ARGS: CommandUnavailable("cannot run xentop", detail="No such file")}}, 503, "PlatformUnavailable"),
        ({"fail": {XENTOP_ARGS: DriverTimeout("xentop -b timed out after 5s")}}, 504, "DriverTimeout"),
        ({"text": "garbage\n"}, 500, "DriverError"),
    ],
)
def test_stats_endpoint_errors(failure, status, code):
    runner = _runner(failure.get("text", XENTOP), fail=failure.get("fail"))
    client = TestClient(create_app(Settings(driver="xen"), driver=_driver(runner), provisioner=MockProvisioner()))
    response = client.get("/api/v1/stats")
    assert (response.status_code, response.json()["code"]) == (status, code)


def test_xentop_nonzero_exit_is_503():
    class Failing(FakeRunner):
        def run(self, args, *, timeout_s):
            if tuple(args) == XENTOP_ARGS:
                return CommandResult(1, "", "xentop: unable to initialize xenstat")
            return super().run(args, timeout_s=timeout_s)

    runner = Failing({("xl", "list", "-l"): XL_LIST_LONG})
    with pytest.raises(PlatformUnavailable, match="xentop failed \\(exit 1\\)"):
        collect_stats(Xentop(runner), Xl(runner))


# ---- 실보드 픽스처: RPi4 rev 1.4, Xen 4.21.1-pre (tests/fixtures/rpi4-xen-4.21/xentop.txt, 2026-10-22 수집, `xentop -b -i 2 -n -x -f`) ----

BOARD = Path(__file__).parent / "fixtures" / "rpi4-xen-4.21"


def test_rpi4_board_capture_parses_every_domain_and_device_line():
    from dom0_manager.drivers.xentop import NetDevice, VbdDevice, row_to_stats

    rows = {row.name: row for row in parse_xentop((BOARD / "xentop.txt").read_text(encoding="utf-8"))}
    assert set(rows) == {"Domain-0", "debian-dev", "zephyr-drive-a", "zephyr-drive-b"}
    debian = rows["debian-dev"]
    assert (debian.flags, debian.vcpus, debian.mem_k, debian.maxmem_k, debian.nets, debian.vbds) == ("--b---", 1, 524304, 525312, 1, 1)
    assert debian.net_devices == [NetDevice(rx_bytes=8024, tx_bytes=10698)]  # 두 번째 반복의 값: 마지막 반복이 이긴다
    assert debian.vbd_devices == [VbdDevice(rd=3218, wr=759, rsect=146058, wsect=14712)]  # "VBD Unidentified 51712 [ca: 0]" (세부에 공백)
    dom0 = rows["Domain-0"]
    assert (dom0.flags, dom0.cpu_pct, dom0.nets, dom0.net_devices, dom0.vbd_devices) == ("-----r", 12.3, 0, [], [])
    zephyr = rows["zephyr-drive-a"]
    assert (zephyr.vbds, zephyr.vbd_devices, zephyr.net_devices) == (0, [], [NetDevice(rx_bytes=589, tx_bytes=4447)])
    assert map_xentop_state(debian.flags) == VmState.running and map_xentop_state(dom0.flags) == VmState.running

    stats = row_to_stats(debian, vm_id=DEBIAN, domid=1, sampled_at=SAMPLED)
    assert (stats.cpu_time_s, stats.memory_mib, stats.max_memory_mib) == (14, 512, 513)
    assert (stats.net_rx_bytes, stats.net_tx_bytes, stats.disk_rd_bytes, stats.disk_wr_bytes) == (8024, 10698, 146058 * 512, 14712 * 512)
    assert stats.unavailable == {}
    zephyr_stats = row_to_stats(zephyr, vm_id=ZEPHYR, domid=2, sampled_at=SAMPLED)
    assert (zephyr_stats.memory_mib, zephyr_stats.disk_rd_bytes, zephyr_stats.unavailable) == (16, None, {"disk_rd_bytes": "NO_DEVICE", "disk_wr_bytes": "NO_DEVICE"})


def test_xen_driver_without_xentop_is_platform_unavailable_not_501():
    """계약 4.6: xentop 을 실행할 수 없으면 503 PlatformUnavailable (platform 드라이버와 같은 의미)."""
    with pytest.raises(PlatformUnavailable):
        XenDriver(Xl(FakeRunner({}))).collect_stats()
