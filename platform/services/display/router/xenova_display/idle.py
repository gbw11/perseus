"""Scoped idle prevention for a dedicated X11 display, with normal-exit restoration."""
import re
import subprocess


class X11IdleGuard:
    def __init__(self, display, runner=None):
        self.display = display
        self.runner = runner or subprocess.run
        self.original = None

    def command(self, *args):
        return self.runner(["xset", "-display", self.display, *args], check=True,
                           capture_output=True, text=True, timeout=8).stdout

    def settings(self):
        text = self.command("q")
        saver = re.search(r"prefer blanking:\s+(yes|no).*?timeout:\s+(\d+)\s+cycle:\s+(\d+)", text, re.S)
        dpms = re.search(r"Standby:\s+(\d+)\s+Suspend:\s+(\d+)\s+Off:\s+(\d+)", text)
        if not saver:
            raise RuntimeError("cannot verify X11 screen saver settings")
        return {"blank": saver[1] == "yes", "timeout": int(saver[2]), "cycle": int(saver[3]),
                "dpms": None if not dpms else {"timeouts": list(map(int, dpms.groups())),
                                                "enabled": "DPMS is Enabled" in text}}

    def enable(self):
        self.original = self.settings()
        try:
            self.command("s", "off")
            self.command("s", "noblank")
            if self.original["dpms"] is not None:
                self.command("-dpms")
            current = self.settings()
            if current["timeout"] or current["blank"] or (current["dpms"] and current["dpms"]["enabled"]):
                raise RuntimeError("X11 idle prevention verification failed")
            return current
        except Exception:
            self.restore()
            raise

    def restore(self):
        if self.original is None:
            return
        original = self.original
        self.command("s", str(original["timeout"]), str(original["cycle"]))
        self.command("s", "blank" if original["blank"] else "noblank")
        if original["dpms"] is not None:
            self.command("dpms", *map(str, original["dpms"]["timeouts"]))
            self.command("+dpms" if original["dpms"]["enabled"] else "-dpms")
        self.original = None
