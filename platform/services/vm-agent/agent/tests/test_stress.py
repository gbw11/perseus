"""stress 부하 프로세스: 워커 수만큼 띄우고, 시간이 지나거나 일찍 죽으면 남은 프로세스를 정리한다."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from xenova_agent import stress
from xenova_agent.stress import StressRun, mem_available_mib

AGENT_ROOT = Path(__file__).resolve().parents[1]


class FakeProc:
    def __init__(self, args) -> None:
        self.args = list(args)
        self.code: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -15

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.killed = True
        self.code = -9


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make(cpu, memory, duration):
    procs: list[FakeProc] = []
    clock = Clock()

    def launcher(args):
        procs.append(FakeProc(args))
        return procs[-1]

    return StressRun(cpu, memory, duration, launcher=launcher, clock=clock), procs, clock


def test_one_process_per_cpu_worker_and_one_for_memory():
    _, procs, _ = make(2, 64, 10)
    assert [p.args for p in procs] == [["cpu", "10"], ["cpu", "10"], ["memory", "64", "10"]]


def test_runs_until_all_processes_end_cleanly():
    run, procs, _ = make(1, 0, 5)
    assert run.poll() is None
    procs[0].code = 0
    assert run.poll() == "succeeded"


def test_early_abnormal_exit_fails_and_stops_the_rest():
    run, procs, _ = make(2, 32, 5)
    procs[2].code = -9  # OOM
    assert run.poll() == "failed"
    assert procs[0].terminated and procs[1].terminated


def test_processes_left_after_deadline_are_stopped():
    run, procs, clock = make(1, 0, 5)
    clock.t = 5 + stress.GRACE_S
    assert run.poll() == "succeeded"
    assert procs[0].terminated


def test_launch_failure_stops_already_started_processes():
    procs: list[FakeProc] = []

    def launcher(args):
        if procs:
            raise OSError("fork failed")
        procs.append(FakeProc(args))
        return procs[-1]

    with pytest.raises(OSError):
        StressRun(2, 0, 5, launcher=launcher)
    assert procs[0].terminated


def test_mem_available_reads_meminfo(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:  1024000 kB\nMemAvailable:  524288 kB\n", encoding="ascii")
    assert mem_available_mib(meminfo) == 512
    meminfo.write_text("MemTotal:  1024000 kB\n", encoding="ascii")
    assert mem_available_mib(meminfo) is None
    assert mem_available_mib(tmp_path / "missing") is None


def test_bad_arguments_exit_2():
    assert stress.main(["cpu"]) == 2
    assert stress.main(["memory", "x", "1"]) == 2


def test_real_processes_end_on_their_own():
    """실제 python -m xenova_agent.stress 를 띄워 정해진 시간에 스스로 끝나는지 본다."""

    def launcher(args):
        return subprocess.Popen([sys.executable, "-m", "xenova_agent.stress", *args], cwd=AGENT_ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    run = StressRun(1, 1, 1, launcher=launcher)
    try:
        codes = [proc.wait(timeout=20) for proc in run._procs]
    finally:
        run.stop()
    assert codes == [0, 0]
    assert run.poll() == "succeeded"
