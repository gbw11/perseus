import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


spec=importlib.util.spec_from_file_location("board_probe",Path(__file__).parents[1]/"tools/probe_board_environment.py")
probe=importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_remote_display_rejected_before_any_command(monkeypatch):
    monkeypatch.setattr(probe,"command",lambda args:pytest.fail("must not execute"))
    with pytest.raises(ValueError): probe.collect("example.org:0")


def test_missing_tools_are_reported_without_claiming_hardware_success(monkeypatch):
    monkeypatch.delenv("DISPLAY",raising=False)
    monkeypatch.setattr(probe.shutil,"which",lambda name:None)
    monkeypatch.setattr(probe,"module_available",lambda name:False)
    result=probe.collect()
    assert result["assessment"]=="missing_or_unverified_prerequisites"
    assert result["xrandr"] is None
    assert not result["physical_hdmi_verified"]


def test_commands_are_read_only_and_installed_tools_are_not_physical_proof(monkeypatch):
    commands=[]
    monkeypatch.setenv("XENOVA_DISPLAY_TOKEN","never-report-this-secret")
    monkeypatch.setattr(probe.shutil,"which",lambda name:"/test/"+name)
    monkeypatch.setattr(probe,"module_available",lambda name:True)
    monkeypatch.setattr(probe.platform,"system",lambda:"Linux")
    def run(args,**kwargs):
        commands.append(args)
        text="x11grab" if "-devices" in args else " V mjpeg description" if "-encoders" in args else "Noto CJK"
        assert not kwargs.get("shell")
        return subprocess.CompletedProcess(args,0,text,"")
    monkeypatch.setattr(probe.subprocess,"run",run)
    result=probe.collect(":0")
    assert result["assessment"]=="prerequisites_found_runtime_unverified"
    assert not result["physical_hdmi_verified"]
    assert "/test/xrandr" in commands[2]
    assert commands[2][1:]==["--display",":0","--query","--props"]
    assert "never-report-this-secret" not in json.dumps(result)


def test_unavailable_graphics_command_is_a_reported_timeout(monkeypatch):
    def timeout(args,**kwargs): raise subprocess.TimeoutExpired(args,8)
    monkeypatch.setattr(probe.subprocess,"run",timeout)
    assert probe.command(["xrandr","--query"])["error"]=="TimeoutExpired"
