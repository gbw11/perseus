"""Collect display prerequisites without changing the board or starting a GUI.

Standard library only; run inside the board's graphical user session when possible.
Prints JSON to stdout. A successful exit means collection succeeded, not HDMI acceptance.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess


def read(path):
    try: return Path(path).read_bytes().replace(b"\x00",b"").decode("utf-8",errors="replace").strip()
    except OSError: return None


def command(args):
    try:
        result=subprocess.run(args,capture_output=True,text=True,timeout=8,check=False)
        return {"returncode":result.returncode,"stdout":result.stdout[:20000],"stderr":result.stderr[:2000]}
    except (OSError,subprocess.TimeoutExpired) as error:
        return {"returncode":None,"error":type(error).__name__}


def module_available(name):
    try: return importlib.util.find_spec(name) is not None
    except (ImportError,ValueError): return False


def collect(display=None):
    display=display or os.environ.get("DISPLAY")
    if display is not None and not re.fullmatch(r":[0-9]+(?:\.[0-9]+)?",display):
        raise ValueError("only a local X11 DISPLAY such as :0 is accepted")
    paths={name:shutil.which(name) for name in ["xrandr","chromium","chromium-browser","ffmpeg","xl","fc-list"]}
    browser=paths["chromium"] or paths["chromium-browser"]
    devices=command([paths["ffmpeg"],"-hide_banner","-devices"]) if paths["ffmpeg"] else None
    encoders=command([paths["ffmpeg"],"-hide_banner","-encoders"]) if paths["ffmpeg"] else None
    xrandr=command([paths["xrandr"],"--display",display,"--query","--props"]) if paths["xrandr"] and display else None
    connectors=[]
    for entry in sorted(Path("/sys/class/drm").glob("card*-*")):
        if not (entry/"status").exists(): continue
        try: edid=(entry/"edid").read_bytes()
        except OSError: edid=b""
        connectors.append({"name":entry.name,"status":read(entry/"status"),"modes":read(entry/"modes"),
                           "edid_sha256":hashlib.sha256(edid).hexdigest() if edid else None})
    font=command([paths["fc-list"],":lang=ko","family"]) if paths["fc-list"] else None
    checks={"linux":platform.system()=="Linux","xrandr_installed":bool(paths["xrandr"]),
        "chromium_installed":bool(browser),"ffmpeg_installed":bool(paths["ffmpeg"]),
        "x11grab_listed":bool(devices and devices["returncode"]==0 and re.search(r"\bx11grab\b",devices["stdout"])),
        "mjpeg_encoder_listed":bool(encoders and encoders["returncode"]==0 and re.search(r"\bmjpeg\s",encoders["stdout"])),
        "x11_query_succeeded":bool(xrandr and xrandr["returncode"]==0),
        "korean_font_found":bool(font and font["returncode"]==0 and font["stdout"].strip()),
        "playwright_importable":module_available("playwright"),"xenova_display_importable":module_available("xenova_display")}
    return {"schema_version":1,"read_only":True,"collected_at":datetime.now(timezone.utc).isoformat(),
        "system":{"kernel":platform.release(),"architecture":platform.machine(),
                  "model":read("/proc/device-tree/model"),"os_release":read("/etc/os-release"),
                  "memory":{line.split(':')[0]:line.split(':',1)[1].strip() for line in (read('/proc/meminfo') or '').splitlines() if line.startswith(('MemTotal:','MemAvailable:'))}},
        "session":{"display":display,"session_type":os.environ.get("XDG_SESSION_TYPE"),
                   "xauthority_environment_set":bool(os.environ.get("XAUTHORITY"))},
        "tools":paths,"browser_version":command([browser,"--version"]) if browser else None,
        "drm_connectors":connectors,"xrandr":xrandr,"prerequisite_checks":checks,
        "assessment":"prerequisites_found_runtime_unverified" if all(checks.values()) else "missing_or_unverified_prerequisites",
        "physical_hdmi_verified":False,
        "limits":["No mode/position changes, packages, services, VM or graphics launch performed.",
                  "Installed tools do not prove ARM/Xen GPU access, window placement, capture or physical HDMI.",
                  "Run the hardware acceptance procedure to validate two real outputs and performance."]}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display",help="existing local X11 display; defaults to DISPLAY if set")
    args=parser.parse_args()
    try: report=collect(args.display)
    except ValueError as error: parser.error(str(error))
    print(json.dumps(report,ensure_ascii=False,indent=2))
