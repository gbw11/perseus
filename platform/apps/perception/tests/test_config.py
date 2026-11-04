from __future__ import annotations

import pytest

from xenova_perception.__main__ import parse_args
from xenova_perception.config import PerceptionSettings


def test_stream_source_needs_url():
    with pytest.raises(ValueError):
        PerceptionSettings(source="stream")


def test_stream_source_from_command_line():
    s = parse_args(["--source", "stream", "--url", "http://127.0.0.1:8090/camera/stream.mjpg"], env={})
    assert s.source == "stream" and s.url == "http://127.0.0.1:8090/camera/stream.mjpg"
