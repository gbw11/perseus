#!/usr/bin/env python3
"""Reconstruct source -> submission -> independently received commands.

Exact source/receiver interval statistics use their own monotonic clocks. These
are NOT histogram upper bounds, CPU execution time, or end-to-end latency.
"""
from __future__ import annotations
import argparse
import base64
import collections
import json
import math
from pathlib import Path
import re
import struct

FIELDS = ("begin", "step_end", "submit_begin", "submit_end", "iteration_end", "release",
          "completed", "missed", "skipped", "input_seq", "control_seq", "generation",
          "message", "topic_seq", "rc", "encoded", "v_bits", "steer_bits", "input_age_ms", "iteration_begin", "attempted")


def stats(values):
    if not values:
        return {"samples": 0, "p95_us": None, "p99_us": None, "max_us": None}
    values = sorted(values)
    return {"samples": len(values), "min_us": values[0], "mean_us": sum(values) / len(values),
            "p95_us": values[math.ceil(len(values) * .95) - 1],
            "p99_us": values[math.ceil(len(values) * .99) - 1], "max_us": values[-1],
            "method": "exact nearest-rank from bounded raw records"}


def float32(bits):
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def source(path):
    text = Path(path).read_text()
    # A Zephyr shell can prefix ANSI prompts; data itself is bounded JSON.
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    decoded = [json.loads(line) for line in re.findall(r"XD_ROW (\[[^\r\n]*\])", text)]
    if any(len(row) != len(FIELDS) or any(type(v) not in (str, int) for v in row) for row in decoded):
        raise ValueError("source row version/type mismatch")
    rows = [dict(zip(FIELDS, map(int, row))) for row in decoded]
    sessions = [json.loads(line) for line in re.findall(r"XD_SESSION (\{[^\r\n]*\})", text)]
    summaries = [json.loads(line) for line in re.findall(r"XD_SUMMARY (\{[^\r\n]*\})", text)]
    if len(sessions) != 1 or len(summaries) != 1 or not rows:
        raise ValueError("one explicit app execution/session and complete source capture required")
    if (int(summaries[0]["overflow"]) or len(rows) != summaries[0]["rows"] or
            not summaries[0].get("capture_complete") or summaries[0].get("counter_saturated") or
            summaries[0].get("clock_rc") or summaries[0].get("task_rc")):
        raise ValueError("source capture lost/saturated/truncated")
    return sessions[0], summaries[0], rows


def analyze(directory, lossless=False):
    directory = Path(directory)
    inputs = {}
    for line in (directory / "inputs.jsonl").read_text().splitlines():
        item = json.loads(line)
        message = item["message"]
        inputs[message["type"], message["seq"]] = message["payload"]
    received = collections.defaultdict(list)
    telemetry = collections.defaultdict(list)
    app_heartbeats = collections.defaultdict(set)
    failures = []
    for channel in ("drive", "telemetry"):
        summary = json.loads((directory / (channel + ".summary.json")).read_text())
        if summary["queue_dropped"] or summary["invalid"] or summary["writer_errors"]:
            failures.append(channel + " receiver invalid/drop")
        for line in (directory / (channel + ".jsonl")).read_text().splitlines():
            record = json.loads(line)
            message = json.loads(base64.b64decode(record["payload_base64"]))
            if message["type"].startswith("telemetry.") and record.get("binding_matches", True):
                telemetry[message["vm_id"]].append(message)
            if message["type"] == "app.heartbeat":
                app_heartbeats[(message["vm_id"], message["boot_id"], message["agent_instance_id"])].add(message["payload"]["state"])
            if message["type"] == "drive.command":
                key = (message["vm_id"], message["boot_id"], message["agent_instance_id"], message["message_id"])
                received[key].append((int(record["received_monotonic_ns"]), message))
    result = {}
    for app in ("manual", "autonomous"):
        session, summary, rows = source(directory / (app + ".stdout"))
        vm_messages = telemetry[session["vm_id"]]
        heartbeat_key = (session["vm_id"], session["boot_id"], session["agent_instance_id"])
        streams = sorted({m["type"] for m in vm_messages})
        if (any(m["boot_id"] != session["boot_id"] for m in vm_messages)
                or any(m["publisher_instance_id"] == session["agent_instance_id"] for m in vm_messages)):
            failures.append(app + " app/Publisher execution or independent session mismatch")
        progress = [s for m in vm_messages for s in m["payload"].get("samples", [])
                    if s["name"] == "loop.completed_total" and s["status"] == "ok"]
        if (not {"telemetry.inventory", "telemetry.heartbeat", "telemetry.metrics", "telemetry.events", "telemetry.snapshot"}.issubset(streams)
                or not progress or max(int(s["value"]) for s in progress) < 100
                or "active" not in app_heartbeats[heartbeat_key]):
            failures.append(app + " independent v1/v2 liveness/progress evidence incomplete")
        matched, missing, content_errors, generated_valid, receive_times = 0, 0, 0, 0, []
        input_kind = "drive.input" if app == "manual" else "perception.result"
        for row in rows:
            if not row["iteration_begin"] <= row["begin"] <= row["step_end"] <= row["submit_begin"] <= row["submit_end"] <= row["iteration_end"]:
                failures.append(app + " invalid source stage ordering")
            inp = inputs.get((input_kind, row["input_seq"]))
            control = inputs.get(("vehicle.control", row["control_seq"]))
            if inp and control:
                generation = control["generation"]
                phase = (row["input_seq"] - 1) // 40 % 4
                # Known controlled inputs, independently checked against existing
                # algorithm unit vectors. This never creates output messages.
                expected = ([(1, 0), (1, .4), (-.5, -.4), (0, 0)] if app == "manual" else
                            [(.5, 0), (.425, -.15), (.425, .15), (0, 0)])[phase]
                if row["input_age_ms"] > (200 if app == "manual" else 300):
                    expected = (0, 0)
                valid = generation == row["generation"] and all(abs(float32(row[name]) - value) < 1e-5
                            for name, value in zip(("v_bits", "steer_bits"), expected))
                generated_valid += int(valid)
                if not valid:
                    content_errors += 1
            if row["rc"]:
                continue
            key = (session["vm_id"], session["boot_id"], session["agent_instance_id"], str(row["message"]))
            packets = received.get(key, [])
            if not packets:
                missing += 1
                continue
            matched += 1
            receive_times.append(packets[0][0])
            for _, message in packets:
                payload = message["payload"]
                if (message["seq"] != row["topic_seq"] or payload["generation"] != row["generation"] or
                        abs(payload["v_mps"] - float32(row["v_bits"])) > 0.00011 or
                        abs(payload["steer_rad"] - float32(row["steer_bits"])) > 0.00011):
                    content_errors += 1
        intervals = [b["begin"] - a["begin"] for a, b in zip(rows, rows[1:])]
        if any(b["completed"] != a["completed"] + 1 for a, b in zip(rows, rows[1:])):
            failures.append(app + " noncontiguous calculation completions")
        if content_errors or not generated_valid or matched < 100:
            failures.append(app + " input/calculation/receipt validation incomplete")
        if lossless and missing:
            failures.append(app + " locally accepted commands missing at receiver")
        receive_times.sort()
        result[app] = {"session": session, "source_summary": summary, "capture_rows": len(rows),
                       "v2_streams": streams, "app_heartbeat_states": sorted(app_heartbeats[heartbeat_key]),
                       "known_input_command_checks": generated_valid, "content_errors": content_errors,
                       "captured_locally_accepted": sum(not r["rc"] for r in rows),
                       "window_generated": len(rows), "window_publish_attempted": sum(r["attempted"] for r in rows),
                       "window_locally_failed": sum(r["attempted"] and r["rc"] != 0 for r in rows),
                       "window_dropped_before_attempt": sum(not r["attempted"] for r in rows),
                       "matched_received": matched, "unmatched_locally_accepted": missing,
                       "unmatched_meaning": "not localized; MQTT QoS0 and receiver boundaries remain distinct",
                       "step_elapsed": stats([r["step_end"] - r["begin"] for r in rows]),
                       "loop_interval": stats(intervals), "absolute_jitter": stats([abs(v - 20000) for v in intervals]),
                       "schedule_lateness": stats([r["begin"] - r["release"] for r in rows]),
                       "mqtt_submit_elapsed": stats([r["submit_end"] - r["submit_begin"] for r in rows]),
                       "iteration_including_poll": stats([r["iteration_end"] - r["iteration_begin"] for r in rows]),
                       "receiver_interval": stats([(b - a) / 1000 for a, b in zip(receive_times, receive_times[1:])]),
                       "window_misses": sum(r["missed"] for r in rows),
                       "window_skipped": sum(r["skipped"] for r in rows)}
    return {"accepted": not failures, "failures": failures, "apps": result,
            "boundary": "current real app code plus controlled synthetic inputs; no vehicle/safety acceptance",
            "clock_boundary": "guest and receiver clocks analyzed separately; no one-way latency"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    p.add_argument("--require-lossless", action="store_true")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = analyze(args.directory, args.require_lossless)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"accepted": result["accepted"], "failures": result["failures"]}))
    raise SystemExit(0 if result["accepted"] else 1)


if __name__ == "__main__":
    main()
