"""411: the MIT-SHM presenter really puts pixels on an X server (Xvfb stand-in for dom0 HDMI)."""
import ctypes as C
import shutil
import subprocess
import time

import pytest

from xenova_display.native_x11 import SharedCapture, SharedPresenter

pytestmark = pytest.mark.skipif(shutil.which("Xvfb") is None or shutil.which("xsetroot") is None,
                                reason="Xvfb not installed")
DISPLAY = ":96"


@pytest.fixture(scope="module")
def xvfb():
    # Abstract socket only (works on read-only /tmp/.X11-unix, e.g. WSLg); -noreset keeps state.
    process = subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", "1280x720x24", "-nolisten", "tcp",
                                "-nolisten", "unix", "-noreset"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic()+10
    while subprocess.run(["xsetroot", "-display", DISPLAY, "-solid", "black"], stderr=subprocess.DEVNULL).returncode:
        if time.monotonic() > deadline or process.poll() is not None:
            process.kill(); pytest.skip("Xvfb did not start")
        time.sleep(.1)
    yield DISPLAY
    process.terminate(); process.wait(timeout=5)


def pixel(display, x, y):
    with SharedCapture(1, 1, x, y, display=display) as capture:
        data = C.string_at(capture.frame(), 4)
    return data[2], data[1], data[0]  # BGRX -> RGB


def test_rejects_invalid_rectangle_before_loading_x11(monkeypatch):
    monkeypatch.setattr(C, "CDLL", lambda *_a, **_k: pytest.fail("loaded native code"))
    with pytest.raises(ValueError): SharedPresenter(0, 720)


def test_presenter_shows_frames_and_status_screen(xvfb):
    with SharedPresenter(640, 360, 320, 180, display=xvfb) as presenter:
        row = bytes([0xe8, 0x73, 0x1a, 0]) * 640  # #1a73e8 as BGRX
        for y in range(360):
            C.memmove(presenter.address + y*presenter.pitch, row, len(row))
        presenter.present()
        assert pixel(xvfb, 640, 360) == (0x1a, 0x73, 0xe8)
        assert pixel(xvfb, 10, 10) == (0, 0, 0)  # outside the output rectangle
        presenter.notice(["GUEST DISPLAY UNAVAILABLE", "reason: test"])
        assert pixel(xvfb, 640, 500) == (0x20, 0x21, 0x24)
        assert pixel(xvfb, 640, 185) == (0xd9, 0x30, 0x25)  # accent bar at the top of the output
    # Closing destroys the window: the root shows through again.
    assert pixel(xvfb, 640, 360) == (0, 0, 0)
