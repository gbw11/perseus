"""/proc 수집기: metrics values[], processes[], inventory."""

from __future__ import annotations

import pytest
from fakeproc import cpu_stat, diskstats, make_roots, meminfo, net_dev, process, write

from xenova_agent import inventory
from xenova_agent.metrics import MetricsCollector
from xenova_agent.processes import ProcessSampler


def by_name(values, name, **labels):
    return [v for v in values if v["name"] == name and all(v["labels"].get(k) == x for k, x in labels.items())]


@pytest.fixture
def roots(tmp_path):
    r = make_roots(tmp_path)
    cpu_stat(r, user=100, system=50, idle=850)
    meminfo(r)
    diskstats(r, {"xvda": (2048, 1024)}, partitions={"xvda1": (2000, 1000), "loop0": (9, 9)})
    net_dev(r, {"lo": (999, 999), "eth0": (1048576, 262144)})
    return r


# ---- metrics ----


def test_first_sample_has_no_cpu_rate_and_the_second_is_normalised_to_all_vcpus(roots):
    collector = MetricsCollector(roots)
    first = by_name(collector.collect(), "cpu_percent_allocated")[0]
    assert first["value"] is None and first["unavailable_reason"] == "first_sample" and first["unit"] == "percent"
    cpu_stat(roots, user=150, system=75, idle=925, steal=50)  # busy +75, total +200 (steal 50 은 사용으로 세지 않음)
    second = by_name(collector.collect(), "cpu_percent_allocated")[0]
    assert second["value"] == 37.5 and second["unavailable_reason"] is None


def test_memory_and_counters_use_bytes_and_skip_partitions_loop_and_lo(roots):
    values = MetricsCollector(roots).collect()
    assert by_name(values, "memory_used_bytes")[0]["value"] == (512000 - 300000) * 1024
    assert by_name(values, "memory_available_bytes")[0]["value"] == 300000 * 1024
    assert [v["labels"]["device"] for v in by_name(values, "disk_read_bytes_total")] == ["xvda"]  # xvda1·loop0 제외
    assert by_name(values, "disk_read_bytes_total", device="xvda")[0]["value"] == 2048 * 512
    assert by_name(values, "disk_write_bytes_total", device="xvda")[0]["value"] == 1024 * 512
    assert [v["labels"]["iface"] for v in by_name(values, "net_rx_bytes_total")] == ["eth0"]
    assert by_name(values, "net_tx_bytes_total", iface="eth0")[0]["value"] == 262144
    assert {v["unit"] for v in values} <= {"percent", "bytes"}


def test_unreadable_or_missing_sources_are_null_with_a_reason_never_zero(tmp_path):
    roots = make_roots(tmp_path)
    meminfo(roots, available_kb=None)  # 오래된 커널: MemAvailable 없음
    values = MetricsCollector(roots).collect()
    assert all(v["value"] is None and v["unavailable_reason"] for v in values)
    reasons = {v["name"]: v["unavailable_reason"] for v in values}
    assert reasons["cpu_percent_allocated"] == "read_failed" and reasons["memory_used_bytes"] == "not_supported"
    assert reasons["disk_read_bytes_total"] == "read_failed" and reasons["net_rx_bytes_total"] == "read_failed"


def test_unreadable_sys_block_does_not_fall_back_to_counting_partitions(roots):
    import shutil

    shutil.rmtree(roots.sys / "block")
    values = MetricsCollector(roots).collect()
    disk = by_name(values, "disk_read_bytes_total") + by_name(values, "disk_write_bytes_total")
    assert [(v["value"], v["unavailable_reason"]) for v in disk] == [(None, "read_failed"), (None, "read_failed")]


def test_diskless_vm_reports_device_not_found(roots):
    diskstats(roots, {})
    values = MetricsCollector(roots).collect()
    assert by_name(values, "disk_read_bytes_total")[0]["unavailable_reason"] == "device_not_found"


# ---- processes ----


def test_processes_are_ranked_by_one_core_cpu_and_capped(tmp_path):
    roots = make_roots(tmp_path)
    clock = [100.0]
    for pid in range(1, 15):
        process(roots, pid, f"p{pid}", utime=0)
    sampler = ProcessSampler(roots, top_n=10, clk_tck=100, page_bytes=4096, clock=lambda: clock[0])
    assert sampler.sample() == []  # 첫 표본은 구간이 없다
    for pid in range(1, 15):
        process(roots, pid, f"p{pid}", utime=pid * 10)
    process(roots, 7, "stress-ng (worker)", utime=700, stime=100)  # 2 초에 800 틱 = 1 코어 기준 400%
    clock[0] = 102.0
    rows = sampler.sample()
    assert len(rows) == 10 and rows[0] == {"pid": 7, "name": "stress-ng (worker)", "cpu_percent": 400.0, "rss_bytes": 256 * 4096}
    assert [r["pid"] for r in rows[1:3]] == [14, 13] and rows[1]["cpu_percent"] == 70.0


def test_vanished_and_reused_pids_are_not_reported_as_cpu_spikes(tmp_path):
    roots = make_roots(tmp_path)
    clock = [0.0]
    process(roots, 10, "old", utime=5000, starttime=1)
    process(roots, 11, "gone", utime=10)
    sampler = ProcessSampler(roots, clk_tck=100, page_bytes=4096, clock=lambda: clock[0])
    sampler.sample()
    process(roots, 10, "new", utime=9000, starttime=999)  # 같은 pid 를 새 프로세스가 재사용
    (roots.proc / "11" / "stat").unlink()
    clock[0] = 2.0
    assert sampler.sample() == []


# ---- inventory ----


def test_inventory_reads_the_running_guest(tmp_path):
    roots = make_roots(tmp_path)
    write(roots.etc / "os-release", 'ID=debian\nVERSION_ID="13"\nPRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n')
    write(roots.proc / "sys" / "kernel" / "osrelease", "6.12.0-arm64\n")
    write(roots.sys / "class" / "net" / "eth0" / "address", "02:bb:06:62:42:53\n")
    write(roots.sys / "class" / "net" / "lo" / "address", "00:00:00:00:00:00\n")
    write(roots.proc / "net" / "if_inet6", "fe80000000000000000000fffe624253 02 40 20 80     eth0\n")
    inv = inventory.collect(roots, hostname=lambda: "debian-dev", ipv4_of=lambda name: "192.168.77.20" if name == "eth0" else None)
    assert inv["os"] == {"id": "debian", "version_id": "13", "pretty_name": "Debian GNU/Linux 13 (trixie)"}
    assert inv["kernel_release"] == "6.12.0-arm64" and inv["hostname"] == "debian-dev"
    assert inv["interfaces"] == [{"name": "eth0", "mac": "02:bb:06:62:42:53", "addresses": [
        {"address": "192.168.77.20", "family": "ipv4"}, {"address": "fe80::ff:fe62:4253", "family": "ipv6"}]}]
    assert inv["capabilities"]["metrics"][0] == "cpu_percent_allocated" and inv["capabilities"]["commands"] == []


def test_inventory_required_fields_are_never_blank(tmp_path):
    roots = make_roots(tmp_path)  # os-release·osrelease·net 모두 없음
    inv = inventory.collect(roots, hostname=lambda: "", kernel_release=lambda: "", ipv4_of=lambda name: None)
    assert inv["os"]["id"] == "linux" and inv["kernel_release"] == "unknown" and inv["hostname"] == "unknown"
    assert inv["interfaces"] == []
