#!/usr/bin/env python3
"""Bounded CPU-only load INSIDE the explicitly approved Linux test guest.

No network/disk workload, resource resizing, scheduler or affinity changes.
Actual process CPU time and completed work are returned; requested worker count
alone is not treated as proof of load. Run ID/PIDs identify only owned children.
"""
import argparse
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import signal
import time


def worker(end, connection):
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    started = time.process_time()
    iterations = 0
    block = b"xenova-fixed-cpu-load-v1" * 128
    while time.monotonic() < end:
        for _ in range(128):
            block = hashlib.sha256(block).digest() * 128
        iterations += 128
    connection.send({"pid": os.getpid(), "iterations": iterations,
                     "process_cpu_s": time.process_time() - started,
                     "guest_linux_affinity": sorted(os.sched_getaffinity(0))})
    connection.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--seconds", type=float, required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--execute", action="store_true", required=True)
    args = p.parse_args()
    if not 1 <= args.workers <= 4 or not 1 <= args.seconds <= 300:
        p.error("bounded 1..4 workers, 1..300 seconds required")
    if args.output.exists():
        p.error("new owned output path required")
    started = time.monotonic()
    end = started + args.seconds
    context = mp.get_context("fork")
    children, pipes, result = [], [], []
    interrupted = False

    def stop(signum, frame):
        nonlocal interrupted
        interrupted = True
        for child in children:
            if child.is_alive():
                child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    for _ in range(args.workers):
        parent, child_pipe = context.Pipe(duplex=False)
        child = context.Process(target=worker, args=(end, child_pipe))
        child.start()
        child_pipe.close()
        children.append(child)
        pipes.append(parent)
    # Main sends no progress I/O during the measurement window.
    for child, pipe in zip(children, pipes):
        child.join(timeout=max(0, end + 5 - time.monotonic()))
        if child.is_alive():
            child.terminate()
            child.join(timeout=2)
        row = {"pid": child.pid, "exitcode": child.exitcode}
        if pipe.poll():
            try:
                row.update(pipe.recv())
            except EOFError:
                pass
        pipe.close()
        result.append(row)
    receipt = {"run_id": args.run_id, "workload": "sha256-fixed-block-v1", "workers": args.workers,
               "requested_s": args.seconds, "elapsed_monotonic_s": time.monotonic() - started,
               "interrupted": interrupted, "children": result,
               "clock_boundary": "guest Linux process CPU time; affinity here is guest vCPU, not Xen pCPU"}
    with args.output.open("x") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    print(json.dumps(receipt))
    return 1 if interrupted or any(r["exitcode"] or not r.get("iterations") for r in result) else 0


if __name__ == "__main__":
    raise SystemExit(main())
