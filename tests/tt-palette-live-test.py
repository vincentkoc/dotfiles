#!/usr/bin/env python3
"""Probe OSC 10/11 on a private, detached tmux server; never launch an agent."""

import importlib.machinery
import importlib.util
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
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
COLOR_ENV = ("NO_COLOR", "TERMINFO", "COLORTERM", "CLICOLOR", "CLICOLOR_FORCE",
             "FORCE_COLOR", "TERMINFO_DIRS")


def wait_for(path, timeout=5):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError(f"fixture did not create {path.name}")
        time.sleep(0.01)


def probe(root):
    (root / "environment.json").write_text(json.dumps(
        {key: os.environ.get(key) for key in COLOR_ENV}))
    (root / "ready").touch()
    wait_for(root / "start", 30)
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
    wait_for(root / "stop", 30)


class PaletteTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tt-palette-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.socket = self.root / "tmux.sock"
        self.tmux = shutil.which("tmux")
        self.assertIsNotNone(self.tmux, "real tmux is required")
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.root / "home"),
                    "SHELL": "/bin/sh", "TERM": "xterm-256color", "LC_ALL": "C.UTF-8",
                    "XDG_STATE_HOME": str(self.root / "state"),
                    "XDG_CONFIG_HOME": str(self.root / "config")}
        self.env.update({"NO_COLOR": "1", "TERMINFO": str(self.root / "invalid-terminfo"),
                         "COLORTERM": "", "CLICOLOR": "0", "CLICOLOR_FORCE": "0",
                         "FORCE_COLOR": "0", "TERMINFO_DIRS": "/invalid-terminfo"})
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
        (self.root / "start").touch()
        (self.root / "stop").touch()
        deadline = time.monotonic() + 5
        while self.run_tmux("has-session", "-t", "palette", check=False).returncode == 0:
            if time.monotonic() >= deadline:
                self.fail("private palette fixture did not exit through its stop file")
            time.sleep(0.01)

    def replies(self, root=None):
        root = root or self.root
        (root / "start").touch()
        wait_for(root / "reply.json")
        data = json.loads((root / "reply.json").read_text())
        return dict(re.findall(r"\x1b\](10|11);rgb:([0-9a-f/]+)", data))

    def recovery_config(self):
        return subprocess.run([str(REPO / "bin/tt"), "recovery-config"], env=self.env,
                              capture_output=True, text=True, check=True, timeout=5).stdout

    def assert_palette(self, replies, background="1a1a/1b1b/2626"):
        self.assertEqual(replies, {"10": "c0c0/caca/f5f5", "11": background})

    def assert_color_environment(self, root=None, force=None):
        environment = json.loads(((root or self.root) / "environment.json").read_text())
        self.assertEqual(environment, {
            "NO_COLOR": None, "TERMINFO": None, "COLORTERM": "truecolor",
            "CLICOLOR": "1", "CLICOLOR_FORCE": "1", "FORCE_COLOR": force,
            "TERMINFO_DIRS": f"{self.env['HOME']}/.terminfo:/usr/share/terminfo",
        })

    def test_detached_cold_recovery_has_rgb_defaults(self):
        self.start(self.recovery_config())
        self.assert_color_environment()
        self.assert_palette(self.replies())

    def test_public_snapshot_and_live_marker_repair_preserve_process(self):
        self.start(self.recovery_config())
        env = dict(self.env, CODEX_HOME=str(self.root / "codex"))

        def tt(*args):
            result = subprocess.run([str(REPO / "bin/tt"), *args], env=env,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            return result

        identity = self.run_tmux("display-message", "-p", "-t", "%0",
                                 "#{pane_id} #{pane_pid} #{pane_start_command}").stdout
        exit_file = self.root / "exit-unrelated-fixture"
        dead = self.run_tmux(
            "new-window", "-d", "-P", "-F", "#{pane_id}", "-t", "palette", "-n", "exited",
            "/bin/sh", "-c", 'while [ ! -f "$1" ]; do sleep 0.01; done; exit 7',
            "fixture", str(exit_file),
        ).stdout.strip()
        self.addCleanup(self.run_tmux, "kill-pane", "-t", dead, check=False)
        self.run_tmux("set-option", "-pt", dead, "remain-on-exit", "on")
        exit_file.touch()
        deadline = time.monotonic() + 3
        while self.run_tmux("display-message", "-p", "-t", dead, "#{pane_dead}").stdout.strip() != "1":
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        tt("marker", "set", "%0", "green")
        snapshot = self.root / "live.tsv"
        subprocess.run([str(REPO / "bin/tt-codex-snapshot-writer"), str(snapshot)],
                       env=env, capture_output=True, text=True, check=True, timeout=15)
        self.assertIn('"marker": "green"', snapshot.read_text())
        self.assertIn("green", tt("restore-preview", str(snapshot)).stdout)
        tt("marker", "clear", "%0")
        self.assertIn("would restore", tt("marker", "restore", "palette", str(snapshot)).stdout)
        self.assertEqual(self.run_tmux("show-option", "-pt", "%0", "-qv", "@tt_marker").stdout, "")
        tt("marker", "restore", "palette", str(snapshot), "--execute")
        tt("marker", "verify", "%0", "green")
        self.assertEqual(self.run_tmux("display-message", "-p", "-t", "%0",
                                      "#{pane_id} #{pane_pid} #{pane_start_command}").stdout, identity)
        tt("marker", "set", "%0", "amber")
        self.assertIn("current amber; preserved",
                      tt("marker", "restore", "palette", str(snapshot), "--execute").stdout)
        tt("marker", "verify", "%0", "amber")

    def test_cold_recovery_preserves_partial_window_and_marks_before_agent_probe(self):
        self.check_partial_recovery(False)

    def test_snapshot_markers_survive_freeze_without_local_marker_files(self):
        self.check_partial_recovery(True)

    def check_partial_recovery(self, embedded):
        # The only "codex" on this fixture's PATH is our OSC probe. Every pane
        # exits through the same stop file, without signaling a real agent.
        home_bin = Path(self.env["HOME"]) / "bin"
        home_bin.mkdir()
        fake = home_bin / "codex"
        fake.write_text("#!/bin/sh\nexec " + shlex.join([
            sys.executable, str(Path(__file__).resolve()), "--probe", str(self.root)
        ]) + "\n")
        fake.chmod(0o700)
        shell = self.root / "fixture-shell"
        shell.write_text("#!/bin/sh\nwhile [ ! -f " + shlex.quote(str(self.root / "stop")) +
                         " ]; do sleep 0.05; done\n")
        shell.chmod(0o700)
        sid = "11111111-1111-1111-1111-111111111111"
        rows = [
            ["palette:12.1", "codex", str(self.root), "marked", "codex", sid, "exact", ""],
            ["palette:12.2", "codex", str(self.root), "unknown", "codex", "", "unresolved", ""],
            ["palette:12.3", "shell", str(self.root), "shell3", "sh", "", "shell", ""],
            ["palette:12.4", "shell", str(self.root), "shell4", "sh", "", "shell", ""],
        ]
        snapshot = self.root / "snapshot.tsv"
        text = "".join("\t".join(row) + "\n" for row in rows)
        colors = ["green", "default", "blue", "violet"]
        if embedded:
            metadata = {"version": 1, "panes": {
                row[0]: {"kind": row[1], "cwd": row[2], "session_id": row[5], "marker": color}
                for row, color in zip(rows, colors)
            }}
            text = "# pane-markers\t" + json.dumps(metadata) + "\n" + text
        snapshot.write_text(text)
        marker = Path(self.env["XDG_STATE_HOME"]) / "tt/pane-markers/saved.md"
        marker.parent.mkdir(parents=True)
        marker.write_text('---\nsession: "palette"\nwindow: "12"\npane: "1"\nmarker: "amber"\n---\n')
        env = dict(self.env, TT_OPERATOR_TMUX_SCOPE="recover:palette", TT_LOGIN_SHELL=str(shell),
                   PATH=f"{home_bin}:/usr/bin:/bin:/usr/sbin:/sbin")
        self.addCleanup(self.stop)
        recovery_input = snapshot
        if embedded:
            # An explicit default must also clear an old marker at a reused slot.
            (marker.parent / "stale.md").write_text(
                '---\nsession: "palette"\nwindow: "12"\npane: "2"\nmarker: "amber"\n---\n')
            frozen = subprocess.run([
                str(REPO / "bin/tt-codex-snapshot-writer"), "--freeze-recovery", "codex",
                str(snapshot), "palette",
            ], env=env, capture_output=True, text=True, check=True, timeout=5)
            recovery_input = Path(frozen.stdout.strip())
        result = subprocess.run([
            str(REPO / "bin/tt-codex-snapshot-writer"), "--recover-cold", "codex",
            str(recovery_input), "palette", "0",
        ], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            wait_for(self.root / "ready")
        except RuntimeError:
            self.fail(self.run_tmux("capture-pane", "-p", "-t", "palette:12.1").stdout[-3000:] +
                      self.run_tmux("show-environment", "-t", "palette", "PATH").stdout)
        self.assert_color_environment(force="3")
        self.assert_palette(self.replies(), "1f1f/2a2a/1f1f" if embedded else "2929/2222/1818")
        if embedded:
            observed = self.run_tmux("list-panes", "-t", "palette:12", "-F",
                                     "#{pane_index} #{@tt_marker}").stdout.splitlines()
            self.assertEqual(observed, ["1 green", "2 ", "3 blue", "4 violet"])
            for index, color in enumerate(colors, 1):
                subprocess.run([str(REPO / "bin/tt"), "marker", "verify",
                                f"palette:12.{index}", color],
                               env=env, capture_output=True, text=True, check=True, timeout=5)
        panes = self.run_tmux("list-panes", "-a", "-F",
                              "#{window_index} #{pane_index} #{pane_left} #{pane_top} #{@tt_base_title}").stdout.splitlines()
        self.assertEqual(len(panes), 4)
        self.assertTrue(all(line.startswith("12 ") for line in panes))
        self.assertEqual(len({line.split()[2] for line in panes}), 2)
        self.assertEqual(len({line.split()[3] for line in panes}), 2)
        self.assertIn("[unresolved codex] unknown", panes[1])
        self.assertEqual(snapshot.read_text(), text)

    def test_native_recovery_scrubs_existing_server_environment(self):
        self.start(self.recovery_config() + "\nset-environment -g NO_COLOR 1\n"
                   "set-environment -g TERMINFO /invalid-terminfo\n")
        loader = importlib.machinery.SourceFileLoader(
            "palette_snapshot_writer", str(REPO / "bin/tt-codex-snapshot-writer"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        writer = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = writer
        self.addCleanup(sys.modules.pop, spec.name, None)
        loader.exec_module(writer)
        root = self.root / "native"
        root.mkdir()
        self.addCleanup(lambda: (root / "stop").touch())
        command = shlex.join([sys.executable, str(Path(__file__).resolve()),
                              "--probe", str(root)])
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(
            writer, "LOGIN_SHELL", "/bin/true"
        ):
            self.run_tmux("new-window", "-d", *writer.recovery_environment(),
                          *writer.recovery_shell_command(command))
        wait_for(root / "ready")
        self.assert_color_environment(root, force="3")
        self.assert_palette(self.replies(root))
        self.assertEqual(self.run_tmux("show-environment", "-g", "NO_COLOR").stdout.strip(),
                         "NO_COLOR=1")
        self.assertEqual(self.run_tmux("show-environment", "-g", "TERMINFO").stdout.strip(),
                         "TERMINFO=/invalid-terminfo")
        (root / "stop").touch()

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
                           if re.match(r"setw -g window-(?:active-)?style |set-environment ", line))
        self.start(styles + "\n")
        self.assert_color_environment()
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
