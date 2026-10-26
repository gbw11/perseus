"""시험용 가짜 /proc·/sys·/etc 트리."""

from __future__ import annotations

from pathlib import Path

from xenova_agent.procfs import Roots


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_roots(tmp_path: Path) -> Roots:
    roots = Roots(proc=tmp_path / "proc", sys=tmp_path / "sys", etc=tmp_path / "etc")
    roots.proc.mkdir(parents=True)
    return roots


def cpu_stat(roots: Roots, user: int, system: int, idle: int, *, iowait: int = 0, steal: int = 0) -> None:
    write(roots.proc / "stat", f"cpu  {user} 0 {system} {idle} {iowait} 0 0 {steal} 0 0\ncpu0 {user} 0 {system} {idle} {iowait} 0 0 {steal} 0 0\n")


def meminfo(roots: Roots, total_kb: int = 512000, available_kb: int | None = 300000) -> None:
    lines = [f"MemTotal:       {total_kb} kB", "MemFree:        100000 kB"]
    if available_kb is not None:
        lines.append(f"MemAvailable:   {available_kb} kB")
    write(roots.proc / "meminfo", "\n".join(lines) + "\n")


def diskstats(roots: Roots, disks: dict[str, tuple[int, int]], partitions: dict[str, tuple[int, int]] | None = None) -> None:
    rows = []
    for name, (read_s, write_s) in {**disks, **(partitions or {})}.items():
        rows.append(f" 202       0 {name} 100 0 {read_s} 10 50 0 {write_s} 20 0 30 40")
    write(roots.proc / "diskstats", "\n".join(rows) + "\n")
    for name in disks:
        (roots.sys / "block" / name).mkdir(parents=True, exist_ok=True)


def net_dev(roots: Roots, ifaces: dict[str, tuple[int, int]]) -> None:
    head = "Inter-|   Receive                                                |  Transmit\n face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
    rows = [f"{name:>6}: {rx} 10 0 0 0 0 0 0 {tx} 8 0 0 0 0 0 0" for name, (rx, tx) in ifaces.items()]
    write(roots.proc / "net" / "dev", head + "\n".join(rows) + "\n")


def process(roots: Roots, pid: int, name: str, *, utime: int, stime: int = 0, starttime: int = 1000, rss_pages: int = 256) -> None:
    # pid (comm) state ppid pgrp session tty tpgid flags minflt cminflt majflt cmajflt utime(14) stime(15) cutime cstime priority nice
    # threads itreal starttime(22) vsize(23) rss(24) rsslim ...
    fields = ["S", "1", "1", "1", "0", "-1", "0", "0", "0", "0", "0", str(utime), str(stime), "0", "0", "20", "0", "1", "0",
              str(starttime), "4096000", str(rss_pages), "18446744073709551615"]
    write(roots.proc / str(pid) / "stat", f"{pid} ({name}) " + " ".join(fields) + "\n")
