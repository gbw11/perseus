from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import time

from fastapi.testclient import TestClient
import pytest

from xenova_display.kiosk import write_status
from xenova_display.outputs import RuntimeConfig, X11Layout, parse_xrandr
from xenova_display.stream import Capture, JpegParser, MAX_JPEG, capture_command, create_stream_app


def config(backend="virtual_x11"):
    return RuntimeConfig.model_validate({"backend":backend,"display":":99","outputs":[
        {"output_id":"monitor-1","connector":"HDMI-1","edid_sha256":"0"*64,"x":0},
        {"output_id":"monitor-2","connector":"HDMI-2","edid_sha256":"1"*64,"x":1280}]})


def test_virtual_layout_needs_explicit_opt_in():
    with pytest.raises(ValueError): X11Layout(config())


@pytest.mark.parametrize("change", ["overlap","duplicate","nonlocal","missing_edid"])
def test_rejects_ambiguous_mapping_and_credential_routes(change):
    raw = config("x11").model_dump()
    if change == "overlap": raw["outputs"][1]["x"] = 0
    if change == "duplicate": raw["outputs"][1]["connector"] = "HDMI-1"
    if change == "nonlocal": raw["feed_origin"] = "https://example.com"
    if change == "missing_edid": raw["outputs"][1]["edid_sha256"] = None
    with pytest.raises(ValueError): RuntimeConfig.model_validate(raw)


def test_edid_parser_and_one_disconnected_connector_do_not_hide_other():
    edid = "00"*16
    text = f"HDMI-1 connected primary 1280x720+0+0 (normal left inverted right x axis y axis)\n\tEDID:\n\t\t{edid}\n   1280x720 60.00*\nHDMI-2 disconnected (normal left inverted right x axis y axis)\n"
    rows = parse_xrandr(text)
    assert rows["HDMI-1"]["edid_sha256"] == hashlib.sha256(bytes.fromhex(edid)).hexdigest()
    c = config("x11")
    c.outputs[0].edid_sha256 = rows["HDMI-1"]["edid_sha256"]
    calls=[]
    def run(args, **kwargs): calls.append(args); return subprocess.CompletedProcess(args,0,text,"")
    states = X11Layout(c, runner=run).reconcile()
    assert states["monitor-1"]["physical_verified"] is True
    assert states["monitor-2"]["status"] == "disconnected"
    assert all("--off" not in cmd for cmd in calls)


def test_wrong_edid_never_applies_layout():
    calls=[]
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args,0,"HDMI-1 connected 1280x720+0+0\n   1280x720 60*\n","")
    state = X11Layout(config("x11"), runner=run).reconcile()
    assert state["monitor-1"]["status"] == "edid_mismatch"
    assert len(calls) == 1


def test_jpeg_parser_handles_partial_multiple_frames_and_bounded_garbage():
    parser = JpegParser()
    assert parser.push(b"garbage\xff") == []
    assert parser.push(b"\xd8one\xff") == []
    assert parser.push(b"\xd9\xff\xd8two\xff\xd9") == [b"\xff\xd8one\xff\xd9", b"\xff\xd8two\xff\xd9"]
    parser.push(b"\xff\xd8" + b"x"*(MAX_JPEG+1))
    assert len(parser.buffer) <= MAX_JPEG


def test_capture_uses_real_root_rectangle_not_independent_web_render():
    c = config()
    cmd = capture_command(c,c.outputs[1])
    assert "x11grab" in cmd
    assert ":99+1280,0" in cmd
    assert "1280x720" in cmd
    assert all("http" not in arg for arg in cmd)


def test_stale_or_wrong_output_mapping_never_exposes_frame(tmp_path):
    c = config()
    p = tmp_path/"runtime.json"
    capture = Capture(c,c.outputs[0],p)
    layout={"schema_version":1,"layout_epoch":"epoch","backend":"virtual_x11","updated_monotonic":time.monotonic(),
        "outputs":{"monitor-1":{"status":"mapped","renderer_alive":True,"physical_verified":False,
                                  "connector":"HDMI-1","rectangle":[0,0,1280,720]}}}
    write_status(p,layout)
    capture.frame=b"jpeg";capture.sequence=1;capture.captured_mono=time.monotonic();capture.error=None
    assert capture.snapshot()[1] == b"jpeg"
    layout["outputs"]["monitor-1"]["rectangle"][0]=1280
    write_status(p,layout)
    assert capture.snapshot()[1] is None
    layout["outputs"]["monitor-1"]["rectangle"][0]=0
    layout["updated_monotonic"]-=10
    write_status(p,layout)
    assert capture.snapshot()[1] is None


def test_stream_auth_is_separate_read_only_and_never_a_query_token(tmp_path):
    class Fake:
        def __init__(self,c,o,p): self.o=o
        def start(self): pass
        def stop(self): pass
        def snapshot(self): return {"output_id":self.o.output_id,"status":"unavailable"},None
    app = create_stream_app(config(), tmp_path/"status", "demo-test-token", allow_virtual=True, capture_factory=Fake)
    with TestClient(app) as client:
        assert client.get("/demo").status_code == 200
        assert client.get("/api/v1/streams?token=demo-test-token").status_code == 401
        assert client.post("/auth",json={"token":"wrong"}).status_code == 401
        r=client.post("/auth",json={"token":"demo-test-token"})
        assert r.status_code == 204 and "HttpOnly" in r.headers["set-cookie"]
        assert "demo-test-token" not in r.headers["set-cookie"]
        assert client.get("/api/v1/streams").status_code == 200
        assert client.post("/api/v1/display/selection",json={}).status_code == 404


def test_stream_scales_full_source_without_changing_capture_rectangle():
    c = config()
    c.outputs[0].width, c.outputs[0].height = 1824, 984
    args = capture_command(c, c.outputs[0])
    assert args[args.index("-video_size")+1] == "1824x984"
    assert args[args.index("-filter_threads")+1] == "1"
    assert "scale=1280:720:" in args[args.index("-vf")+1]
    assert "pad=1280:720:" in args[args.index("-vf")+1]
    assert "format=yuvj420p" in args[args.index("-vf")+1]
    metadata, _ = Capture(c, c.outputs[0], "/missing-status").snapshot()
    assert metadata["rectangle"] == [0, 0, 1824, 984]
    assert (metadata["width"], metadata["height"]) == (1280, 720)


def test_stream_420_dimensions_must_be_even():
    raw = config().model_dump()
    raw["stream_width"] = 1279
    with pytest.raises(ValueError): RuntimeConfig.model_validate(raw)
