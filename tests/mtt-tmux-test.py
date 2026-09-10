#!/usr/bin/env python3
"""Real-PTY fixtures. Run ONLY inside the documented owned resource sandbox.

tmux provides the rendering oracle; this test does not implement a VT parser.
All source processes cooperate with task-owned stop files. No kill-server.
"""

import fcntl
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tty
import unittest


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
loader = importlib.machinery.SourceFileLoader("mtt_live", str(ROOT / "bin/mtt"))
spec = importlib.util.spec_from_loader(loader.name, loader)
mtt = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = mtt
loader.exec_module(mtt)


def wait_for(predicate, description, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.03)
    raise AssertionError("timed out: " + description)


def source(directory, name):
    directory = Path(directory)
    tty.setraw(0)
    screen = ""
    with (directory / (name + ".input")).open("ab", buffering=0) as received:
        while not (directory / (name + ".stop")).exists():
            request = directory / (name + ".screen")
            desired = request.read_text() if request.exists() else "full"
            if desired != screen:
                screen = desired
                if name == "sibling":
                    image = "\x1b[2J\x1b[HPROTECTED-SIBLING"
                elif desired == "blank":
                    image = "\x1b[2J\x1b[H"
                elif desired == "cursor":
                    image = "\x1b[30;85H"
                else:
                    rows = ["SOURCE-{:02d}".format(y) + "." * 75 for y in range(30)]
                    rows[1] = "a" * 18 + "\u754c" + "e\u0301" + "b" * 64
                    rows[2] = "c" * 83 + "\u754c"
                    rows[3] = "d" * 84 + "e\u0301"
                    rows[4] = " "*85
                    image = "\x1b[2J" + "".join(
                        "\x1b[{};1H{}".format(y + 1, row) for y, row in enumerate(rows))
                    image += "\x1b[H"
                os.write(1, image.encode())
            if select.select([0], [], [], 0.02)[0]:
                chunk = os.read(0, 4096)
                if not chunk:
                    break
                received.write(chunk)


def viewer_child(directory, socket_path, target, *options):
    before = termios.tcgetattr(0)
    result = subprocess.run(
        [sys.executable, str(ROOT / "bin/mtt"), "--socket", socket_path, *options]
        + ([target] if target != "PICKER" else []),
        timeout=30,
    )
    after = termios.tcgetattr(0)
    Path(directory, "viewer.result").write_text(json.dumps(
        {"code": result.returncode, "restored": before == after}))
    while not Path(directory, "viewer.stop").exists():
        time.sleep(0.02)


class IsolatedMirror(unittest.TestCase):
    def setUp(self):
        if os.environ.get("MTT_TEST_ISOLATED") != "1":
            self.fail("refusing real tmux fixtures outside the owned resource sandbox")
        self.temp = tempfile.TemporaryDirectory(prefix="mtt-fixture-")
        self.directory = Path(self.temp.name)
        self.socket = str(self.directory / "socket")
        self.client = None
        self.master = self.slave = None
        self.phone = None
        self.addCleanup(self.cleanup)
        self.source = self.tmux(
            "new-session", "-d", "-s", "source", "-x", "171", "-y", "30",
            "-P", "-F", "#{pane_id}",
            sys.executable, str(SELF), "--source", str(self.directory), "source",
            create=True,
        ).strip()
        self.tmux("set-option", "-t", "source", "status", "off")
        self.tmux("set-option", "-w", "-t", "source:0", "window-size", "manual")
        self.sibling = self.tmux(
            "split-window", "-h", "-d", "-t", self.source, "-l", "85",
            "-P", "-F", "#{pane_id}", sys.executable, str(SELF), "--source",
            str(self.directory), "sibling",
        ).strip()
        wait_for(lambda: "PROTECTED-SIBLING" in self.capture(self.sibling), "source startup")
        self.assertEqual(self.tmux("display-message", "-p", "-t", self.source,
                                   "#{pane_width}x#{pane_height}").strip(), "85x30")
        self.before = self.protected_state()

    def tmux(self, *args, create=False):
        command = ["tmux", "-u", "-S", self.socket, "-f", "/dev/null"]
        if not create:
            command.append("-N")
        return mtt.bounded_run(command + list(args)).decode()

    def capture(self, pane):
        return self.tmux("capture-pane", "-p", "-N", "-t", pane)

    def protected_state(self):
        return self.tmux("list-panes", "-t", "source:0", "-F",
                         "#{session_id}|#{window_id}|#{pane_id}|#{pane_pid}|#{pane_tty}|"
                         "#{pane_active}|#{window_active}|#{window_zoomed_flag}|"
                         "#{window_layout}|#{pane_width}|#{pane_height}|"
                         "#{window_width}|#{window_height}")

    def cleanup(self):
        # The fixture owns every path, pane, and client referenced here.
        try:
            roots = [int(pid) for pid in self.tmux(
                "list-panes", "-a", "-F", "#{pane_pid}").splitlines()]
        except mtt.Refused:
            roots = []
        if self.client is not None and not self.result_path.exists():
            self.keys(b"\x1dq")
        for name in ("source", "sibling", "viewer"):
            (self.directory / (name + ".stop")).touch()
        if self.client is not None:
            try:
                self.client.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.client.terminate()
                self.client.wait(timeout=2)
        def stopped():
            for pid in roots:
                try:
                    mtt.process_identity(pid)
                except mtt.Refused:
                    continue
                return False
            return True
        wait_for(stopped, "owned fixture roots stop", timeout=4)
        for fd in (self.master, self.slave):
            if fd is not None:
                os.close(fd)
        self.temp.cleanup()

    def start_viewer(self, cols=20, rows=10, options=(), picker=False):
        target = "PICKER" if picker else self.source
        self.phone = self.tmux(
            "new-session", "-d", "-s", "phone", "-x", str(cols), "-y", str(rows),
            "-P", "-F", "#{pane_id}", sys.executable, str(SELF), "--viewer",
            str(self.directory), self.socket, target, *options,
        ).strip()
        self.tmux("set-option", "-t", "phone", "status", "off")
        self.master, self.slave = pty.openpty()
        self.resize(cols, rows)
        self.client = subprocess.Popen(
            ["tmux", "-N", "-u", "-S", self.socket, "attach-session", "-t", "phone"],
            stdin=self.slave, stdout=self.slave, stderr=self.slave,
            start_new_session=True,
        )
        os.set_blocking(self.master, False)
        if picker:
            wait_for(lambda: "source:0.0" in self.capture(self.phone), "picker")
        else:
            wait_for(lambda: "mtt " in self.capture(self.phone)
                     or self.result_path.exists(), "first frame")
            self.assertFalse(self.result_path.exists(), self.capture(self.phone))
        self.drain()

    @property
    def result_path(self):
        return self.directory / "viewer.result"

    def drain(self):
        data = bytearray()
        while select.select([self.master], [], [], 0)[0]:
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                break
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)

    def resize(self, cols, rows):
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        if self.client is not None:
            self.client.send_signal(signal.SIGWINCH)

    def keys(self, value):
        self.drain()
        os.write(self.master, value)

    def input(self, name="source"):
        path = self.directory / (name + ".input")
        return path.read_bytes() if path.exists() else b""

    def finish(self, expected=0):
        if not self.result_path.exists():
            self.keys(b"\x1dq")
        result = json.loads(wait_for(
            lambda: self.result_path.read_text() if self.result_path.exists() else "",
            "viewer exit"))
        self.assertEqual(result["code"], expected)
        self.assertTrue(result["restored"], result)
        self.assertEqual(self.input("sibling"), b"")
        self.assertEqual(self.protected_state(), self.before)

    def pinned(self):
        mirror = mtt.Mirror(self.socket)
        mirror.pin(mtt.resolve(mirror.inventory(), self.source))
        return mirror

    def test_metadata_exact_and_self(self):
        command = [sys.executable, str(ROOT / "bin/mtt"), "--socket", self.socket, "--check"]
        self.assertEqual(mtt.bounded_run(command + [self.source]).decode(),
                         self.source + " 85x30\n")
        self.assertEqual(mtt.bounded_run(command + ["source:0.0"]).decode(),
                         self.source + " 85x30\n")
        with self.assertRaises(mtt.Refused):
            mtt.bounded_run(command + ["source"])
        server = self.tmux("display-message", "-p", "#{pid}").strip()
        env = dict(os.environ, TMUX=self.socket + "," + server + ",0", TMUX_PANE=self.source)
        with self.assertRaises(mtt.Refused):
            mtt.bounded_run(command + [self.source], env=env)
        self.assertEqual(self.protected_state(), self.before)

    def test_small_large_resize_unicode_blanks(self):
        self.start_viewer()
        initial = self.capture(self.phone)
        self.assertIn("SOURCE-00", initial)
        self.assertNotIn("PROTECTED-SIBLING", initial)
        self.assertIn("\u754c", initial)
        self.keys(b"\x1dp")
        wait_for(lambda: " pan " in self.capture(self.phone), "crop pan")
        self.keys(b"\x1bOC" * 19)
        wait_for(lambda: self.capture(self.phone).splitlines()[1].startswith(" e\u0301"),
                 "wide glyph clipped at left edge")
        self.resize(100, 35)
        wait_for(lambda: "\u0301" in self.capture(self.phone), "larger viewport")
        wait_for(lambda: self.tmux("display-message", "-p", "-t", self.phone,
                                  "#{pane_width}x#{pane_height}").strip() == "100x35", "phone resize")
        source_rows = self.capture(self.source).splitlines()
        phone_rows = self.capture(self.phone).splitlines()
        self.assertEqual([row.rstrip() for row in phone_rows[:30]],
                         [row.rstrip() for row in source_rows])
        (self.directory / "source.screen").write_text("blank")
        wait_for(lambda: not self.capture(self.phone).splitlines()[0].strip(), "clear stale cells")
        self.assertTrue(all(not row.strip() for row in self.capture(self.phone).splitlines()[:30]))
        self.resize(20, 10)
        wait_for(lambda: self.tmux("display-message", "-p", "-t", self.phone,
                                  "#{pane_width}").strip() == "20", "smaller viewport")
        self.finish()

    def test_cursor_follow_pan_and_input(self):
        self.start_viewer(cols=40, rows=10)
        (self.directory / "source.screen").write_text("cursor")
        wait_for(lambda: "45,21" in self.capture(self.phone), "physical cursor follow")
        self.keys(b"\x1dp")
        wait_for(lambda: " pan " in self.capture(self.phone), "pan mode")
        self.keys(b"\x1bOH")
        wait_for(lambda: "0,0" in self.capture(self.phone), "pan home")
        self.keys(b"\x1b[6~")
        wait_for(lambda: "0,9" in self.capture(self.phone), "pan page")
        self.keys(b"\x1bOC")
        wait_for(lambda: "1,9" in self.capture(self.phone), "pan right")
        self.assertEqual(self.input(), b"")
        self.keys(b"\x1df")
        wait_for(lambda: " input " in self.capture(self.phone), "resume input")
        self.keys(b"a\x7f\x1bOA\x03\x1d\x1d")
        wait_for(lambda: self.input().endswith(b"\x03\x1d"), "ordinary input")
        self.assertEqual(self.input(), b"a\x7f\x1b[A\x03\x1d")
        self.finish()

    def test_readonly(self):
        self.start_viewer(cols=40, options=("--read-only",))
        self.keys(b"abc\x03\x1dp")
        wait_for(lambda: " pan " in self.capture(self.phone), "read-only pan")
        self.keys(b"\x1df")
        wait_for(lambda: "read-only" in self.capture(self.phone), "read-only follow")
        self.assertEqual(self.input(), b"")
        self.finish()

    def test_complete_paste_and_local_prefix(self):
        self.start_viewer(cols=40)
        payload = b"\x1b[200~first\n\x1dq second\x1b[A\x1b[201~"
        self.keys(payload)
        wait_for(lambda: self.input() == payload, "literal complete paste")
        self.assertFalse(self.result_path.exists())
        self.finish()

    def test_incomplete_paste_restores_terminal(self):
        self.start_viewer(cols=40)
        self.keys(b"\x1b[200~unfinished\x1dq")
        wait_for(lambda: self.result_path.exists(), "incomplete paste refusal")
        self.assertEqual(self.input(), b"")
        self.finish(expected=1)

    def test_copy_mode_and_sync_guard(self):
        for option in ("copy", "sync"):
            with self.subTest(option=option):
                mirror = self.pinned()
                if option == "copy":
                    self.tmux("copy-mode", "-t", self.source)
                else:
                    self.tmux("set-option", "-w", "-t", "source:0", "synchronize-panes", "on")
                with self.assertRaises(mtt.Refused):
                    mirror.send([b"NEVER"])
                self.assertEqual(self.input(), b"")
                self.assertEqual(self.input("sibling"), b"")
                if option == "copy":
                    self.tmux("send-keys", "-X", "-t", self.source, "cancel")
                else:
                    self.tmux("set-option", "-w", "-t", "source:0", "synchronize-panes", "off")
        self.assertEqual(self.protected_state(), self.before)

    def test_hooks_refuse_capture_and_send(self):
        for hook in ("after-capture-pane", "after-send-keys"):
            for scope in ("global", "session", "window", "pane"):
                with self.subTest(hook=hook, scope=scope):
                    mirror = self.pinned()
                    sender = self.pinned()
                    args = {"global": ["-g"], "session": ["-t", "source"],
                            "window": ["-w", "-t", "source:0"],
                            "pane": ["-p", "-t", self.source]}[scope]
                    self.tmux("set-hook", *args, hook + "[7]",
                              "set-option -g @mtt_hook_ran yes")
                    try:
                        with self.assertRaises(mtt.Refused):
                            mirror.snapshot()
                        with self.assertRaises(mtt.Refused):
                            sender.send([b"NEVER"])
                        self.assertEqual(self.tmux("show-option", "-gqv", "@mtt_hook_ran"), "")
                    finally:
                        self.tmux("set-hook", "-u", *args, hook)
        self.assertEqual(self.input(), b"")
        self.assertEqual(self.protected_state(), self.before)

    def test_unrelated_autosave_hooks_allowed(self):
        hooks = ("after-new-session", "after-new-window", "after-split-window", "after-kill-pane")
        for hook in hooks:
            self.tmux("set-hook", "-g", hook + "[90]", "set-option -g @unrelated_hook yes")
        before = self.tmux("show-hooks", "-g")
        mirror = self.pinned()
        mirror.snapshot()
        mirror.send([b"ok"])
        wait_for(lambda: self.input() == b"ok", "input with unrelated hooks")
        self.assertEqual(self.tmux("show-hooks", "-g"), before)
        self.assertEqual(self.tmux("show-option", "-gqv", "@unrelated_hook"), "")
        self.assertEqual(self.protected_state(), self.before)

    def test_respawn_refuses_old_pin(self):
        mirror = self.pinned()
        sibling_before = self.before.splitlines()[1]
        self.tmux("respawn-pane", "-k", "-t", self.source,
                  sys.executable, str(SELF), "--source", str(self.directory), "source")
        with self.assertRaises(mtt.Refused):
            mirror.send([b"NEVER"])
        with self.assertRaises(mtt.Refused):
            mirror.snapshot()
        self.assertEqual(self.protected_state().splitlines()[1], sibling_before)
        self.assertEqual(self.input(), b"")

    def test_socket_replacement_refuses_old_pin(self):
        mirror = self.pinned()
        os.rename(self.socket, self.socket + ".original")
        import socket
        replacement = socket.socket(socket.AF_UNIX)
        try:
            replacement.bind(self.socket)
            with self.assertRaises(mtt.Refused):
                mirror.send([b"NEVER"])
        finally:
            replacement.close()
            os.unlink(self.socket)
            os.rename(self.socket + ".original", self.socket)
        self.assertEqual(self.input(), b"")
        self.assertEqual(self.protected_state(), self.before)

    def test_picker_metadata_and_startup_flush(self):
        self.start_viewer(cols=60, picker=True)
        rows = self.capture(self.phone).splitlines()
        self.assertNotIn("SOURCE-00", "\n".join(rows))
        index = next(i for i, row in enumerate(rows) if "source:0.0" in row)
        if index:
            self.keys(b"\x1bOB" * index)
            time.sleep(0.1)
        self.keys(b"\rDISCARD-STARTUP")
        wait_for(lambda: "mtt " in self.capture(self.phone), "picker selection")
        time.sleep(0.3)
        self.assertEqual(self.input(), b"")
        self.finish()

    def test_desktop_resize_updates_pad_only(self):
        self.start_viewer(cols=40)
        self.tmux("resize-pane", "-t", self.source, "-x", "81")
        wait_for(lambda: "81x30" in self.capture(self.phone), "source-side dimension update")
        self.assertEqual(self.tmux("display-message", "-p", "-t", self.phone,
                                   "#{pane_width}").strip(), "40")
        self.tmux("resize-pane", "-t", self.source, "-x", "85")
        wait_for(lambda: "85x30" in self.capture(self.phone), "source dimension restore")
        self.finish()

    def viewer_mode_refusal(self, mode):
        self.start_viewer(cols=40)
        if mode == "copy":
            self.tmux("copy-mode", "-t", self.source)
        else:
            self.tmux("set-option", "-w", "-t", "source:0", "synchronize-panes", "on")
        self.keys(b"NEVER")
        wait_for(lambda: self.result_path.exists(), "viewer mode refusal")
        self.assertEqual(self.input(), b"")
        if mode == "copy":
            self.tmux("send-keys", "-X", "-t", self.source, "cancel")
        else:
            self.tmux("set-option", "-w", "-t", "source:0", "synchronize-panes", "off")
        self.finish(expected=1)

    def test_viewer_copy_mode_refusal_restores_terminal(self):
        self.viewer_mode_refusal("copy")

    def test_viewer_sync_refusal_restores_terminal(self):
        self.viewer_mode_refusal("sync")

    def test_server_restart_never_reresolves(self):
        mirror = self.pinned()
        self.assertEqual(self.protected_state(), self.before)
        (self.directory / "source.stop").touch()
        (self.directory / "sibling.stop").touch()
        def old_server_stopped():
            try:
                mtt.process_identity(mirror.server)
            except mtt.Refused:
                return True
            return False
        wait_for(old_server_stopped, "old fixture server exits")
        (self.directory / "source.stop").unlink()
        replacement = self.tmux(
            "new-session", "-d", "-s", "source", "-x", "85", "-y", "30",
            "-P", "-F", "#{pane_id}", sys.executable, str(SELF), "--source",
            str(self.directory), "source", create=True,
        ).strip()
        self.assertEqual(replacement, self.source)
        with self.assertRaises(mtt.Refused):
            mirror.send([b"NEVER"])
        with self.assertRaises(mtt.Refused):
            mirror.inventory()
        self.assertEqual(self.input(), b"")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--source":
        source(*sys.argv[2:])
    elif len(sys.argv) > 1 and sys.argv[1] == "--viewer":
        viewer_child(*sys.argv[2:])
    else:
        unittest.main(verbosity=2)
