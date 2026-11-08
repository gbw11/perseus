"""411 Plan B: dom0 HDMI frame output shows domU frames and never keeps a stale frame as live."""
import asyncio
from contextlib import suppress
import ctypes as C
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from types import SimpleNamespace

import pytest

from guest_jpeg import fake_jpeg
from test_outputs import config
from xenova_display.frame_presenter import (FFmpegDecoder, FrameOutput, RelayFrames, TurboDecoder,
                                            notice_lines, read_secret)
from xenova_display.kiosk import Kiosk
from xenova_display.native_jpeg import JPEGDecoder
from xenova_display.outputs import RuntimeConfig


class Function:
    def __init__(self, fn): self.fn = fn
    def __call__(self, *args): return self.fn(*args)


def fake_turbo(width=1280, height=720):
    calls = []
    def header(_h, _data, _size, w, h, _s, _c):
        C.cast(w, C.POINTER(C.c_int)).contents.value = width
        C.cast(h, C.POINTER(C.c_int)).contents.value = height
        return 0
    def decompress(*args):
        calls.append(args[3:])
        return 0
    lib = SimpleNamespace(tjInitDecompress=Function(lambda: 7), tjDecompressHeader3=Function(header),
                          tjDecompress2=Function(decompress), tjDestroy=Function(lambda h: calls.append(("destroy", h))))
    return lib, calls


def test_decoder_writes_bgrx_into_caller_buffer_and_checks_size():
    lib, calls = fake_turbo()
    decoder = JPEGDecoder("/opt/native/libturbojpeg.so", 1280, 720, loader=lambda _: lib)
    decoder.decode(b"\xff\xd8jpeg\xff\xd9", 4096, 5120)
    assert calls[0] == (4096, 1280, 5120, 720, 3, 0)  # target, width, pitch, height, TJPF_BGRX, flags
    with pytest.raises(ValueError): decoder.decode(b"", 4096, 5120)
    with pytest.raises(ValueError): decoder.decode(b"x"*(2*1024*1024+1), 4096, 5120)
    with pytest.raises(ValueError): decoder.decode(b"jpeg", 4096, 100)
    decoder.close(); decoder.close()
    assert calls[-1] == ("destroy", 7)
    with pytest.raises(RuntimeError): decoder.decode(b"jpeg", 4096, 5120)
    small, _ = fake_turbo(640, 480)
    with pytest.raises(ValueError, match="size"):
        JPEGDecoder("/opt/native/libturbojpeg.so", 1280, 720, loader=lambda _: small).decode(b"jpeg", 4096, 5120)


def test_decoder_rejects_relative_library_before_loading():
    with pytest.raises(ValueError):
        JPEGDecoder("libturbojpeg.so", 1280, 720, loader=lambda _: pytest.fail("loaded native code"))


class Clock:
    def __init__(self): self.now = 50.0
    def __call__(self): return self.now


class Relay:
    def __init__(self): self.value = (0, None, 0.0, "waiting")
    def latest(self): return self.value


class Decoder:
    def __init__(self, fail=False): self.submitted, self.pending, self.fail = [], None, fail
    def submit(self, jpeg):
        if self.fail: raise RuntimeError("bad jpeg")
        self.submitted.append(jpeg); self.pending = jpeg
    def take_into(self, address, pitch):
        jpeg, self.pending = self.pending, None
        return jpeg is not None


class Presenter:
    address, pitch = 1024, 5120
    def __init__(self): self.events = []
    def present(self): self.events.append("frame")
    def notice(self, lines): self.events.append(("notice", lines[1]))


def output(decoder=None):
    clock, relay, presenter, reports = Clock(), Relay(), Presenter(), []
    out = FrameOutput(relay, decoder or Decoder(), presenter, clock=clock, report=reports.append)
    return out, clock, relay, presenter, reports


def test_waiting_then_live_then_stale_never_keeps_a_stale_frame():
    out, clock, relay, presenter, reports = output()
    out.step()
    assert presenter.events == [("notice", "reason: waiting")]
    assert reports[-1]["content"] == "unavailable" and reports[-1]["presented_monotonic"] == clock.now
    relay.value = (1, b"frame-1", clock.now, None)
    clock.now += 0.1; out.step()
    assert presenter.events[-1] == "frame" and out.mode == "live" and out.shown == 1
    assert reports[-1]["content"] == "live"  # mode change is reported at once, not after 0.5 s
    clock.now += 0.3; out.step()                       # same frame, no refresh due yet
    assert presenter.events.count("frame") == 1
    clock.now += 0.8; out.step()                       # >1 s since present: refresh proves liveness
    assert presenter.events.count("frame") == 2
    clock.now += 1.0; out.step()                       # frame now 2.2 s old: withdrawn
    assert presenter.events[-1] == ("notice", "reason: frame_stale")
    assert out.mode == "unavailable" and reports[-1]["error"] == "frame_stale"
    events = len(presenter.events)
    clock.now += 0.2; out.step()
    assert len(presenter.events) == events             # no repaint loop while unavailable


def test_relay_error_code_is_shown_and_decode_failure_is_explicit():
    out, clock, relay, presenter, _ = output()
    relay.value = (0, None, 0.0, "relay_auth_failed")
    out.step()
    assert presenter.events == [("notice", "reason: relay_auth_failed")]
    out, clock, relay, presenter, _ = output(Decoder(fail=True))
    relay.value = (1, b"broken", clock.now, None)
    out.step()
    assert presenter.events == [("notice", "reason: frame_decode_failed")]


def test_turbo_decoder_decodes_each_submitted_frame_once():
    lib, calls = fake_turbo()
    decoder = TurboDecoder("/opt/native/libturbojpeg.so", 1280, 720,
                           factory=lambda *a: JPEGDecoder(*a, loader=lambda _: lib))
    assert decoder.take_into(4096, 5120) is False
    decoder.submit(b"\xff\xd8a\xff\xd9")
    assert decoder.take_into(4096, 5120) is True and decoder.take_into(4096, 5120) is False
    assert len([c for c in calls if c[0] != "destroy"]) == 1


def test_notice_text_is_ascii_and_names_the_reason():
    lines = notice_lines("relay_stream_stalled")
    assert all(line.isascii() for line in lines) and "relay_stream_stalled" in lines[1]


def test_relay_token_file_may_be_group_readable_but_not_writable(tmp_path):
    path = tmp_path/"relay-token"
    path.write_text("relay-secret\n")
    for mode in (0o644, 0o660, 0o604):
        path.chmod(mode)
        with pytest.raises(ValueError): read_secret(path)
    path.chmod(0o640)
    assert read_secret(path) == "relay-secret"


class FakeResponse:
    def __init__(self, status=200, chunks=()):
        self.status, self.chunks = status, list(chunks)
    def getheader(self, *_): return "multipart/x-mixed-replace; boundary=frame"
    def read1(self, _):
        return self.chunks.pop(0) if self.chunks else b""


class FakeConnection:
    def __init__(self, response): self.response, self.headers = response, None
    def __call__(self, host, port, timeout):
        assert host == "127.0.0.1"
        return self
    def request(self, _method, _path, headers): self.headers = headers
    def getresponse(self): return self.response
    def close(self): pass


def test_relay_frames_accepts_only_output_sized_frames():
    connection = FakeConnection(FakeResponse(chunks=[fake_jpeg(640, 480), fake_jpeg(tag=b"ok")]))
    relay = RelayFrames(8084, "relay-token", width=1280, height=720, connect=connection)
    assert relay.session() is True
    assert connection.headers == {"Authorization": "Bearer relay-token"}
    sequence, frame, _, error = relay.latest()
    assert sequence == 1 and frame is None and error == "relay_stream_closed"
    denied = RelayFrames(8084, "wrong", width=1280, height=720, connect=FakeConnection(FakeResponse(status=401)))
    assert denied.session() is False and denied.latest()[3] == "relay_auth_failed"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_ffmpeg_fallback_decodes_real_jpeg_with_one_frame_lag():
    def jpeg(colour):
        return subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                               f"color=c={colour}:s=640x480", "-frames:v", "1", "-vf", "format=yuvj420p",
                               "-c:v", "mjpeg", "-f", "mjpeg", "pipe:1"], capture_output=True, check=True).stdout
    decoder = FFmpegDecoder(640, 480)
    target = C.create_string_buffer(640*480*4)
    try:
        frames = [jpeg(c) for c in ("0x1a73e8", "0xd93025", "0x188038", "0xf9ab00")]
        seen = []
        for frame in frames:
            decoder.submit(frame)
            deadline = time.monotonic()+1
            while time.monotonic() < deadline:
                if decoder.take_into(C.addressof(target), 640*4):
                    seen.append(tuple(target.raw[:3]))
                    break
                time.sleep(.02)
        assert len(seen) >= 2  # the lag hides the first frame(s) but later frames do arrive
        assert any(abs(b-0x25) < 24 and abs(r-0xd9) < 24 for b, _g, r in seen)
    finally:
        decoder.close()


def test_runtime_config_guest_frames_is_opt_in_and_validated():
    raw = config().model_dump()
    assert RuntimeConfig.model_validate(raw).renderer == "chromium"
    raw["renderer"] = "guest_frames"
    with pytest.raises(ValueError): RuntimeConfig.model_validate(raw)
    raw["guest_relay_token_file"] = "/etc/xenova/display-relay.token"
    with pytest.raises(ValueError): RuntimeConfig.model_validate(raw)  # turbojpeg needs the library
    raw["native_jpeg_library"] = "/opt/xenova-stream/native/libturbojpeg.so.0.2.0"
    assert RuntimeConfig.model_validate(raw).frame_decoder == "turbojpeg"
    raw.update(native_jpeg_library=None, frame_decoder="ffmpeg")
    assert RuntimeConfig.model_validate(raw).frame_decoder == "ffmpeg"
    raw["guest_relay_token_file"] = "relative.token"
    with pytest.raises(ValueError): RuntimeConfig.model_validate(raw)


FAKE_PRESENTER = textwrap.dedent("""
    import json, signal, sys, time
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    while True:
        print(json.dumps({"schema_version": 1, "alive": True, "renderer": "guest_frames",
                          "presented_monotonic": time.monotonic(), "content": "live",
                          "frame_sequence": 3, "frame_age_ms": 120, "error": None}), flush=True)
        time.sleep(.1)
""")


def test_kiosk_runs_frame_presenter_per_output_and_reports_the_same_status_shape(tmp_path):
    async def case():
        raw = config().model_dump()
        raw.update(renderer="guest_frames", guest_relay_token_file="/unused/token", frame_decoder="ffmpeg")
        kiosk = Kiosk(RuntimeConfig.model_validate(raw), "test-only", tmp_path/"runtime.json", allow_virtual=True)
        script = tmp_path/"presenter.py"
        script.write_text(FAKE_PRESENTER)
        commands = []
        def command(output):
            commands.append(output.output_id)
            return [sys.executable, str(script)]
        kiosk.frame_command = command
        mapping = {o.output_id: {"status": "mapped", "physical_verified": True} for o in kiosk.config.outputs}
        kiosk.layout.reconcile = lambda: {k: dict(v) for k, v in mapping.items()}
        task = asyncio.create_task(kiosk._supervise(None, interval=.05))
        try:
            deadline = time.monotonic()+5
            while time.monotonic() < deadline:
                data = json.loads((tmp_path/"runtime.json").read_text()) if (tmp_path/"runtime.json").exists() else {}
                outputs = data.get("outputs", {})
                if outputs and all(o.get("renderer_alive") for o in outputs.values()): break
                await asyncio.sleep(.05)
            else: pytest.fail(f"frame presenters never became alive: {data}")
            one = outputs["monitor-1"]
            assert one["status"] == "mapped" and one["physical_verified"] is True
            assert one["renderer_kind"] == "guest_frames" and one["content_status"] == "live"
            assert sorted(commands) == ["monitor-1", "monitor-2"]
            # Unplugging one output stops only its presenter.
            process = kiosk.frame_procs["monitor-2"]
            mapping["monitor-2"]["status"] = "disconnected"
            deadline = time.monotonic()+5
            while "monitor-2" in kiosk.frame_procs and time.monotonic() < deadline: await asyncio.sleep(.05)
            assert process.returncode is not None and "monitor-1" in kiosk.frame_procs
            # A crashed presenter is restarted.
            first = kiosk.frame_procs["monitor-1"]
            first.kill(); await first.wait()
            deadline = time.monotonic()+5
            while kiosk.frame_procs.get("monitor-1") in (None, first) and time.monotonic() < deadline:
                await asyncio.sleep(.05)
            assert kiosk.frame_procs["monitor-1"] is not first
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError): await task
            for identity in list(kiosk.frame_procs): await kiosk.close_output(identity)
    asyncio.run(case())


def test_frame_command_never_carries_tokens():
    raw = config().model_dump()
    raw.update(renderer="guest_frames", guest_relay_token_file="/etc/xenova/display-relay.token",
               native_jpeg_library="/opt/native/libturbojpeg.so")
    kiosk = Kiosk(RuntimeConfig.model_validate(raw), "service-token-value", "/unused", allow_virtual=True)
    command = kiosk.frame_command(kiosk.config.outputs[1])
    assert "service-token-value" not in " ".join(command)
    assert command[command.index("--x")+1] == "1280" and command[command.index("--decoder")+1] == "turbojpeg"
    assert command[command.index("--relay-token-file")+1] == "/etc/xenova/display-relay.token"
