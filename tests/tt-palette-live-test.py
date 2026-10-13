#!/usr/bin/env python3
"""Probe OSC 10/11 on a private, detached tmux server; never launch an agent."""

import json
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import termios
import time
import tty
import unittest


REPO = Path(__file__).resolve().parents[1]


def wait_for(path):
    deadline = time.monotonic() + 5
    while not path.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError(f"fixture did not create {path.name}")
        time.sleep(0.01)


def probe(root):
    (root / "ready").touch()
    wait_for(root / "start")
    fd = os.open("/dev/tty", os.O_RDWR)
    previous = termios.tcgetattr(fd)
    data = b""
    try:
        tty.setraw(fd)
        os.write(fd, b"\x1b]10;?\x1b\\\x1b]11;?\x1b\\")
        # Match Codex's startup palette-probe budget.
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            if select.select([fd], [], [], max(0, deadline - time.monotonic()))[0]:
                data += os.read(fd, 4096)
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, previous)
        os.close(fd)
    (root / "reply.json").write_text(json.dumps(data.decode("utf-8", "replace")))
    wait_for(root / "stop")


class PaletteTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tt-palette-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.socket = self.root / "tmux.sock"
        self.tmux = shutil.which("tmux")
        self.assertIsNotNone(self.tmux, "real tmux is required")
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.root / "home"),
                    "SHELL": "/bin/sh", "TERM": "xterm-256color",
                    "XDG_STATE_HOME": str(self.root / "state"),
                    "XDG_CONFIG_HOME": str(self.root / "config")}
        Path(self.env["HOME"]).mkdir()
        wrapper = self.root / "tmux"
        wrapper.write_text("#!/bin/sh\nexec " + shlex.join(
            [self.tmux, "-S", str(self.socket)]
        ) + ' "$@"\n')
        wrapper.chmod(0o700)
        self.env["TT_TMUX_BIN"] = str(wrapper)

    def run_tmux(self, *args, check=True):
        return subprocess.run([self.tmux, "-S", str(self.socket), *args],
                              env=self.env, capture_output=True, text=True,
                              check=check, timeout=5)

    def start(self, config):
        config_path = self.root / "tmux.conf"
        config_path.write_text(config)
        self.run_tmux("-f", str(config_path), "new-session", "-d", "-s", "palette",
                      sys.executable, str(Path(__file__).resolve()), "--probe", str(self.root))
        self.addCleanup(self.stop)
        wait_for(self.root / "ready")
        self.assertEqual(self.run_tmux("list-clients").stdout, "")

    def stop(self):
        (self.root / "stop").touch()
        deadline = time.monotonic() + 5
        while self.run_tmux("has-session", "-t", "palette", check=False).returncode == 0:
            if time.monotonic() >= deadline:
                self.fail("private palette fixture did not exit through its stop file")
            time.sleep(0.01)

    def replies(self):
        (self.root / "start").touch()
        wait_for(self.root / "reply.json")
        data = json.loads((self.root / "reply.json").read_text())
        return dict(re.findall(r"\x1b\](10|11);rgb:([0-9a-f/]+)", data))

    def recovery_config(self):
        return subprocess.run([str(REPO / "bin/tt"), "recovery-config"], env=self.env,
                              capture_output=True, text=True, check=True, timeout=5).stdout

    def assert_palette(self, replies, background="1a1a/1b1b/2626"):
        self.assertEqual(replies, {"10": "c0c0/caca/f5f5", "11": background})

    def test_detached_cold_recovery_has_rgb_defaults(self):
        self.start(self.recovery_config())
        self.assert_palette(self.replies())

    def test_detached_marked_pane_keeps_rgb_defaults(self):
        self.start(self.recovery_config())
        source = (REPO / "bin/tt").read_text()
        functions = "\n".join(re.findall(
            r"(?ms)^pane_marker_(?:window|active)_style\(\) \{.*?^\}", source))
        result = subprocess.run(["/bin/bash", "-c", functions +
                                 "\npane_marker_window_style green\npane_marker_active_style green"],
                                env=self.env, capture_output=True, text=True, check=True, timeout=5)
        normal, active = result.stdout.splitlines()
        self.run_tmux("set-option", "-pt", "%0", "window-style", normal)
        self.run_tmux("set-option", "-pt", "%0", "window-active-style", active)
        self.assert_palette(self.replies(), "1f1f/2a2a/1f1f")

    def check_profile(self, relative):
        text = (REPO / relative).read_text()
        styles = "\n".join(line for line in text.splitlines()
                           if re.match(r"setw -g window-(?:active-)?style ", line))
        self.start(styles + "\n")
        self.assert_palette(self.replies())

    def test_workstation_defaults(self):
        self.check_profile(".tmux.conf.local")

    def test_server_defaults(self):
        self.check_profile("profiles/linux-server/tmux.conf.local")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--probe":
        probe(Path(sys.argv[2]))
    else:
        unittest.main()
