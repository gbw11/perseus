"""412: one HDMI output (monitor-1) is a complete display configuration; two outputs stay valid."""
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from test_outputs import config
from xenova_display.kiosk import Kiosk
from xenova_display.outputs import RuntimeConfig
from xenova_display.stream import create_stream_app

EXAMPLE = Path(__file__).resolve().parents[1]/"examples/single-hdmi-runtime.json"


def single(**output):
    return RuntimeConfig.model_validate({"backend": "virtual_x11", "display": ":99", "outputs": [
        {"output_id": "monitor-1", "connector": "HDMI-1", "edid_sha256": "0"*64, "x": 0, **output}]})


def test_single_hdmi_output_is_valid_and_legacy_two_outputs_still_are():
    assert [o.output_id for o in single().outputs] == ["monitor-1"]
    assert len(config().outputs) == 2
    example = RuntimeConfig.load(EXAMPLE)
    assert [o.output_id for o in example.outputs] == ["monitor-1"] and example.backend == "x11"


@pytest.mark.parametrize("outputs", [
    [],                                                                   # nothing to show
    [{"output_id": "monitor-2", "connector": "HDMI-2", "x": 0}],         # one output must be monitor-1
    [{"output_id": "monitor-1", "connector": "HDMI-1", "x": 0},
     {"output_id": "monitor-1", "connector": "HDMI-2", "x": 1280}],      # duplicate id
    [{"output_id": "monitor-1", "connector": "HDMI-1", "x": 0},
     {"output_id": "monitor-2", "connector": "HDMI-2", "x": 1280},
     {"output_id": "monitor-2", "connector": "HDMI-3", "x": 2560}],      # more than two
])
def test_ambiguous_output_sets_are_rejected(outputs):
    with pytest.raises(ValueError):
        RuntimeConfig.model_validate({"backend": "virtual_x11", "display": ":99", "outputs": outputs})


def test_page_is_limited_to_screens_the_feed_serves():
    assert single().outputs[0].page_path == "/display/monitor-1"
    assert single(page="/display/mobile").outputs[0].page_path == "/display/mobile"
    assert single(page="/display/combined").outputs[0].page_path == "/display/combined"
    for page in ("/admin", "/display/../api", "http://example.com/display/mobile", "/display/mobile?token=x"):
        with pytest.raises(ValueError): single(page=page)


def test_single_output_kiosk_status_has_exactly_monitor_1(tmp_path):
    kiosk = Kiosk(single(), "test-only", tmp_path/"runtime.json", allow_virtual=True)
    now = time.monotonic()
    kiosk.mapping, kiosk.mapping_at = {"monitor-1": {"status": "mapped", "physical_verified": True}}, now
    kiosk.states = {"monitor-1": {"status": "mapped", "renderer_alive": True, "renderer_verified_monotonic": now}}
    status = kiosk._runtime_status(now)
    assert list(status["outputs"]) == ["monitor-1"]
    assert status["outputs"]["monitor-1"]["physical_verified"] is True


def test_remote_stream_relay_reports_one_stream_for_one_hdmi(tmp_path):
    class Capture:
        def __init__(self, _config, output, _status): self.output_id = output.output_id
        def start(self): pass
        def stop(self): pass
        def snapshot(self): return {"output_id": self.output_id, "status": "unavailable"}, None
    app = create_stream_app(single(), tmp_path/"unused", "test-key", allow_virtual=True, capture_factory=Capture)
    with TestClient(app) as client:
        streams = client.get("/api/v1/streams", headers={"Authorization": "Bearer test-key"}).json()["streams"]
        assert [s["output_id"] for s in streams] == ["monitor-1"]
        assert client.get("/streams/monitor-2.mjpeg", headers={"Authorization": "Bearer test-key"}).status_code == 404
        viewer = client.get("/demo").text
    # The viewer draws only reported streams, so one HDMI is not shown next to an empty "monitor 2".
    assert "show((await s.json()).streams" in viewer and "active.length===1" in viewer
