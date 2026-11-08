"""Explicit X11 connector mapping. Virtual rectangles are opt-in test configuration."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_id: Literal["monitor-1", "monitor-2"]
    connector: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    edid_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    width: int = Field(default=1280, ge=640, le=1920)
    height: int = Field(default=720, ge=480, le=1080)
    x: int = Field(ge=0, le=3840)
    y: int = Field(default=0, ge=0, le=2160)


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend: Literal["x11", "virtual_x11"] = "x11"
    display: str = Field(default=":0", pattern=r"^:[0-9]+(?:\.[0-9]+)?$")
    feed_origin: str = "http://127.0.0.1:8082"
    outputs: list[Output] = Field(min_length=2, max_length=2)
    fps: int = Field(default=5, ge=1, le=15)
    stream_width: int = Field(default=1280, ge=640, le=1920, multiple_of=2)
    stream_height: int = Field(default=720, ge=480, le=1080, multiple_of=2)
    jpeg_quality: int = Field(default=7, ge=2, le=20)
    encoder: Literal['ffmpeg', 'native_jpeg'] = 'ffmpeg'
    native_jpeg_quality: int = Field(default=85, ge=1, le=100)
    native_jpeg_library: str | None = Field(default=None, min_length=1, max_length=4096)
    # 411 Plan B: "guest_frames" shows frames rendered by the display domU instead of
    # running Chromium on dom0. The default keeps the existing Chromium kiosk.
    renderer: Literal['chromium', 'guest_frames'] = 'chromium'
    guest_relay_port: int = Field(default=8084, ge=1, le=65535)
    guest_relay_token_file: str | None = Field(default=None, min_length=1, max_length=4096)
    frame_decoder: Literal['turbojpeg', 'ffmpeg'] = 'turbojpeg'
    guest_stale_ms: int = Field(default=2000, ge=500, le=5000)

    @model_validator(mode="after")
    def validate_guest_frames(self):
        if self.renderer != 'guest_frames':
            return self
        def absolute(value):
            return value and PurePosixPath(value).is_absolute() and '\0' not in value
        if not absolute(self.guest_relay_token_file):
            raise ValueError('guest_frames requires an absolute relay token file path')
        if self.frame_decoder == 'turbojpeg' and not absolute(self.native_jpeg_library):
            raise ValueError('turbojpeg frame decoder requires an absolute native_jpeg_library')
        return self

    @model_validator(mode="after")
    def validate_mapping(self):
        if self.encoder == 'native_jpeg':
            if not self.native_jpeg_library or not PurePosixPath(self.native_jpeg_library).is_absolute() or '\0' in self.native_jpeg_library:
                raise ValueError('native encoder requires an absolute library path')
            if any((o.width,o.height)!=(self.stream_width,self.stream_height) for o in self.outputs):
                raise ValueError('native encoder requires matching source/stream dimensions; use FFmpeg for scaling')
        if {o.output_id for o in self.outputs} != {"monitor-1", "monitor-2"}:
            raise ValueError("two unique logical outputs required")
        if len({o.connector for o in self.outputs}) != 2:
            raise ValueError("connector reuse is forbidden")
        if self.backend == "x11" and any(o.edid_sha256 is None for o in self.outputs):
            raise ValueError("physical connectors require install-time EDID hashes")
        a, b = self.outputs
        if max(a.x, b.x) < min(a.x+a.width, b.x+b.width) and max(a.y, b.y) < min(a.y+a.height, b.y+b.height):
            raise ValueError("output rectangles must not overlap")
        origin = urlparse(self.feed_origin)
        if origin.scheme not in {"http", "https"} or origin.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("local feed origin required")
        if origin.username or origin.password or origin.query or origin.fragment or origin.path not in {"", "/"}:
            raise ValueError("feed origin cannot include credentials or paths")
        self.feed_origin = self.feed_origin.rstrip("/")
        return self

    @classmethod
    def load(cls, path):
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


def parse_xrandr(text):
    rows = {}
    current = None
    edid = []
    collecting = False
    def finish():
        if current is not None and edid:
            rows[current]["edid_sha256"] = hashlib.sha256(bytes.fromhex("".join(edid))).hexdigest()
    for line in text.splitlines():
        header = re.match(r"^(\S+) (connected|disconnected)\b(.*)", line)
        if header:
            finish()
            current, connection, tail = header.groups()
            geometry = re.search(r"(\d+)x(\d+)\+(\d+)\+(\d+)", tail)
            rows[current] = {"connected": connection == "connected", "edid_sha256": None,
                             "geometry": tuple(map(int, geometry.groups())) if geometry else None, "modes": []}
            edid, collecting = [], False
        elif current is not None:
            if collecting and re.fullmatch(r"\s+[0-9a-fA-F]{32}\s*", line):
                edid.append(line.strip())
                continue
            collecting = line.strip() == "EDID:"
            mode = re.match(r"\s+(\d+x\d+)\s+", line)
            if mode:
                rows[current]["modes"].append(mode[1])
    finish()
    return rows


class X11Layout:
    def __init__(self, config, *, allow_virtual=False, runner=subprocess.run):
        if config.backend == "virtual_x11" and not allow_virtual:
            raise ValueError("virtual layout requires --allow-virtual; not a physical HDMI test")
        self.config, self.runner = config, runner
        self.initialised = set()

    def command(self, args):
        result = self.runner(["xrandr", "--display", self.config.display, *args],
                             capture_output=True, text=True, timeout=5, check=False)
        if result.returncode:
            raise RuntimeError("X11 layout query/configuration failed")
        return result.stdout

    def inspect(self):
        return parse_xrandr(self.command(["--query", "--props"]))

    def reconcile(self):
        if self.config.backend == "virtual_x11":
            # The root size still has to contain both configured rectangles.
            text = self.command(["--query"])
            size = re.search(r"current (\d+) x (\d+)", text)
            if not size or any(o.x+o.width > int(size[1]) or o.y+o.height > int(size[2]) for o in self.config.outputs):
                raise RuntimeError("virtual root is smaller than the two test rectangles")
            return {o.output_id: {"status": "mapped", "physical_verified": False} for o in self.config.outputs}
        rows = self.inspect()
        states, args = {}, []
        for o in self.config.outputs:
            row = rows.get(o.connector)
            reason = None
            if not row or not row["connected"]: reason = "disconnected"
            elif row["edid_sha256"] != o.edid_sha256: reason = "edid_mismatch"
            elif f"{o.width}x{o.height}" not in row["modes"]: reason = "mode_unavailable"
            states[o.output_id] = {"status": reason or "mapped", "physical_verified": False}
            if reason: self.initialised.discard(o.output_id)
            if not reason and (o.output_id not in self.initialised or row["geometry"] != (o.width, o.height, o.x, o.y)):
                args += ["--output", o.connector, "--mode", f"{o.width}x{o.height}",
                         "--rotate", "normal", "--scale", "1x1", "--pos", f"{o.x}x{o.y}"]
        if args:
            self.command(args)
            rows = self.inspect()
        for o in self.config.outputs:
            row = rows.get(o.connector)
            valid = row and row["connected"] and row["edid_sha256"] == o.edid_sha256 and row["geometry"] == (o.width,o.height,o.x,o.y)
            if states[o.output_id]["status"] == "mapped":
                states[o.output_id] = {"status": "mapped" if valid else "layout_error", "physical_verified": bool(valid)}
                if valid: self.initialised.add(o.output_id)
        return states


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only X11 connector/EDID inventory")
    parser.add_argument("--display", default=":0")
    args = parser.parse_args()
    if not re.fullmatch(r":[0-9]+(?:\.[0-9]+)?", args.display): parser.error("local X11 display required")
    result = subprocess.run(["xrandr", "--display", args.display, "--query", "--props"], capture_output=True, text=True, timeout=5, check=True)
    print(json.dumps(parse_xrandr(result.stdout), indent=2))
