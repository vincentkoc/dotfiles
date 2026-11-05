"""Menu safety on a disposable server, HOME and PTY; never use the default socket."""
import fcntl
import os
from pathlib import Path
import pty
import re
import select
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
TMUX = shutil.which("tmux")


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="tt-menu-")
        self.root = Path(self.tmp.name)
        self.socket = str(self.root / "socket")
        self.env = {"HOME": str(self.root), "PATH": "/usr/bin:/bin",
                    "TERM": "xterm-256color",
                    "LC_ALL": "en_US.UTF-8" if sys.platform == "darwin" else "C.UTF-8",
                    "TT_TMUX_BIN": TMUX}
        self.children = []
        self.fds = []
        self.addCleanup(self.cleanup)
        self.pane = self.tmux("new-session", "-d", "-s", "fixture", "-x", "100",
                              "-y", "40", "-P", "-F", "#{pane_id}", "sleep 120",
                              create=True).strip()
        self.other = self.tmux("new-window", "-d", "-t", "fixture",
                               "-P", "-F", "#{pane_id}", "sleep 120").strip()
        self.tmux("bind-key", "-n", "F12", "set-option", "-g", "@fixture_ack", "1")
        self.master, slave = pty.openpty()
        self.fds.extend((self.master, slave))
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
        self.children.append(subprocess.Popen(
            [TMUX, "-N", "-S", self.socket, "attach-session", "-t", "fixture"],
            env=self.env, stdin=slave, stdout=slave, stderr=slave,
            start_new_session=True))
        self.client = self.wait(lambda: self.tmux("list-clients", "-F", "#{client_name}").strip())
        self.env["TMUX"] = self.socket + ",0,0"

    def cleanup(self):
        self.tmux("kill-session", "-t", "=fixture", check=False)
        for child in self.children:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
            if child.stdout:
                child.stdout.close()
            if child.stderr:
                child.stderr.close()
        for fd in self.fds:
            os.close(fd)
        self.tmp.cleanup()

    def tmux(self, *args, create=False, check=True):
        result = subprocess.run([TMUX, "-S", self.socket, "-f", "/dev/null",
                                 *([] if create else ["-N"]), *args],
                                env=self.env, text=True, capture_output=True, timeout=5)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def wait(self, predicate):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            value = predicate()
            if value:
                return value
            time.sleep(0.02)
        self.fail("fixture condition timed out")

    def output(self, text):
        data = bytearray()
        def received():
            if select.select([self.master], [], [], 0.03)[0]:
                data.extend(os.read(self.master, 65536))
            return text.encode() in data
        self.wait(received)

    def drain(self):
        while select.select([self.master], [], [], 0)[0]:
            os.read(self.master, 65536)

    def panes(self):
        return self.tmux("list-panes", "-a", "-F", "#{pane_id}:#{pane_pid}").splitlines()

    def menu(self, target, key):
        self.drain()
        child = subprocess.Popen(
            [str(ROOT / "bin/tt"), "pane-menu", target, "P", "P", self.client],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.children.append(child)
        self.output("Respawn")
        os.write(self.master, key)
        self.output("? (y/n)")
        return child

    def test_pane_kill_and_respawn_cancel_leave_every_process(self):
        for key in (b"X", b"R"):
            before = self.panes()
            child = self.menu(self.pane, key)
            os.write(self.master, b"n")
            child.wait(timeout=3)
            self.assertEqual(self.panes(), before)

    def test_pane_confirmation_keeps_id_after_focus_change(self):
        before_other = [row for row in self.panes() if row.startswith(self.other + ":")]
        self.menu(self.pane, b"X")
        self.tmux("select-pane", "-t", self.other)
        os.write(self.master, b"y")
        self.wait(lambda: not any(row.startswith(self.pane + ":") for row in self.panes()))
        self.assertEqual(self.panes(), before_other)

    def test_stale_pane_never_kills_replacement(self):
        self.menu(self.pane, b"X")
        self.tmux("kill-pane", "-t", self.pane)
        replacement = self.tmux("split-window", "-d", "-t", self.other,
                                "-P", "-F", "#{pane_id}", "sleep 120").strip()
        before = self.panes()
        os.write(self.master, b"y\x1b[24~")
        self.wait(lambda: self.tmux("show-options", "-gqv", "@fixture_ack").strip() == "1")
        self.assertEqual(self.panes(), before)
        self.assertNotEqual(replacement, self.pane)

    def test_respawn_keeps_clicked_id_after_focus_change(self):
        before = self.panes()
        self.menu(self.pane, b"R")
        self.tmux("select-pane", "-t", self.other)
        os.write(self.master, b"y")
        self.wait(lambda: self.panes() != before)
        self.assertEqual([row for row in before if row.startswith(self.other + ":")],
                         [row for row in self.panes() if row.startswith(self.other + ":")])
        self.assertTrue(any(row.startswith(self.pane + ":") for row in self.panes()))

    def bindings(self, relative):
        text = (ROOT / relative).read_text()
        return [line for line in text.replace("\\\n", " ").splitlines()
                if re.match(r"bind -T root (?:M-)?MouseDown3(?:Pane|Status) ", line)]

    def test_owned_config_and_installer_target_mouse_socket_and_client(self):
        for relative in (".tmux.conf.local", "profiles/linux-server/tmux.conf.local"):
            bindings = self.bindings(relative)
            self.assertEqual(len(bindings), 4)
            for binding in bindings:
                parsed = subprocess.run([TMUX, "-N", "-S", self.socket, "source-file", "-"],
                                        input=binding + "\n", text=True, capture_output=True,
                                        env=self.env, timeout=5)
                self.assertEqual(parsed.returncode, 0, parsed.stderr)
            for key in ("MouseDown3Pane",):
                actual = self.tmux("list-keys", "-T", "root", key)
                self.assertIn("if-shell -F -t =", actual)
                self.assertIn("run-shell -b -t =", actual)
                self.assertIn("TMUX=#{q:socket_path},0,0", actual)
                self.assertIn("#{q:client_name}", actual)
                self.assertIn("mosh-client", actual)
                self.assertIn("ssh", actual)
                self.assertIn("send-keys -M", actual)
            alternate = self.tmux("list-keys", "-T", "root", "M-MouseDown3Pane")
            for retained in ("history-top", "history-bottom", "search-backward",
                             "copy-mode -q", "send-keys -l", "set-buffer",
                             "mouse_word", "mouse_line", "mouse_hyperlink"):
                self.assertIn(retained, alternate)
            self.assertEqual(alternate.count("confirm-before"), 2)
            if relative.startswith("profiles/"):
                for key in ("MouseDown3Status", "M-MouseDown3Status"):
                    actual = self.tmux("list-keys", "-T", "root", key)
                    self.assertIn('"New After" w { new-window -a }', actual)
                    self.assertIn('"New At End" W { new-window }', actual)
        for key in ("MouseDown1StatusRight", "MouseDown3StatusRight"):
            self.tmux("bind-key", "-T", "root", key, "display-message", "fixture CX SAVE")
        before = [self.tmux("list-keys", "-T", "root", key) for key in
                  ("MouseDown1StatusRight", "MouseDown3StatusRight")]
        result = subprocess.run([str(ROOT / "bin/tt"), "pane-menu-bind", self.socket],
                                env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        for key in ("MouseDown3Pane",):
            actual = self.tmux("list-keys", "-T", "root", key)
            self.assertIn("if-shell -F -t =", actual)
            self.assertIn("TMUX=#{q:socket_path},0,0", actual)
        self.assertEqual(alternate, self.tmux("list-keys", "-T", "root", "M-MouseDown3Pane"))
        self.assertEqual(before, [self.tmux("list-keys", "-T", "root", key) for key in
                                ("MouseDown1StatusRight", "MouseDown3StatusRight")])

    def test_mouse_click_on_inactive_pane_keeps_its_socket_and_id(self):
        for directory in (self.root / "bin", self.root / ".local/bin"):
            directory.mkdir(parents=True)
            (directory / "tt").symlink_to(ROOT / "bin/tt")
        self.tmux("set-option", "-g", "mouse", "on")
        self.tmux("select-window", "-t", self.pane)
        sibling = self.tmux("split-window", "-h", "-t", self.pane, "-P", "-F",
                            "#{pane_id}", "sleep 120").strip()
        before = self.panes()
        binding = next(line for line in self.bindings("profiles/linux-server/tmux.conf.local")
                       if line.startswith("bind -T root MouseDown3Pane "))
        result = subprocess.run([TMUX, "-N", "-S", self.socket, "source-file", "-"],
                                input=binding + "\n", env=self.env, text=True,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.drain()
        os.write(self.master, b"\x1b[<2;2;2M")
        self.output("Respawn")
        os.write(self.master, b"X")
        self.output("Kill pane " + self.pane)
        os.write(self.master, b"y")
        self.wait(lambda: not any(row.startswith(self.pane + ":") for row in self.panes()))
        self.assertEqual(self.panes(), [row for row in before if not row.startswith(self.pane + ":")])
        self.assertTrue(any(row.startswith(sibling + ":") for row in self.panes()))

    def test_missing_pane_is_refused_without_a_menu(self):
        result = subprocess.run(
            [str(ROOT / "bin/tt"), "pane-menu", "%99999", "P", "P", self.client],
            env=self.env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("can't find pane", result.stderr)

    def test_confirmation_stays_on_requested_client(self):
        other_master, other_slave = pty.openpty()
        self.fds.extend((other_master, other_slave))
        fcntl.ioctl(other_slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
        self.children.append(subprocess.Popen(
            [TMUX, "-N", "-S", self.socket, "attach-session", "-t", "fixture"],
            env={key: value for key, value in self.env.items() if key != "TMUX"},
            stdin=other_slave, stdout=other_slave, stderr=other_slave,
            start_new_session=True))
        self.wait(lambda: len(self.tmux("list-clients", "-F", "#{client_name}").splitlines()) == 2)
        before = self.panes()
        child = self.menu(self.pane, b"R")
        os.write(self.master, b"n")
        child.wait(timeout=3)
        output = bytearray()
        while select.select([other_master], [], [], 0)[0]:
            output.extend(os.read(other_master, 65536))
        self.assertNotIn(b"Respawn pane", output)
        self.assertEqual(self.panes(), before)

    def test_window_actions_confirm_exact_id_on_the_requested_client(self):
        window = self.tmux("display-message", "-p", "-t", self.pane, "#{window_id}").strip()
        for relative in (".tmux.conf.local", "profiles/linux-server/tmux.conf.local"):
            text = (ROOT / relative).read_text()
            commands = [cmd for cmd in re.findall(r'"(confirm-before [^"\n]+)"', text)
                        if "window #{window_id}" in cmd]
            self.assertEqual(len(commands), 4)
            for command in commands:
                expanded = self.tmux("display-message", "-p", "-c", self.client,
                                     "-t", self.pane, command).strip()
                self.assertIn("-t " + window, expanded)
                self.assertIn(self.client, expanded)
                before = self.panes()
                self.drain()
                process = subprocess.Popen([TMUX, "-N", "-S", self.socket, *shlex.split(expanded)],
                                           env=self.env, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
                self.children.append(process)
                self.output("? (y/n)")
                self.tmux("select-window", "-t", self.other)
                os.write(self.master, b"n")
                process.wait(timeout=3)
                self.assertEqual(self.panes(), before)

    def window_confirmation(self, action):
        text = (ROOT / ".tmux.conf.local").read_text()
        command = next(cmd for cmd in re.findall(r'"(confirm-before [^"\n]+)"', text)
                       if action in cmd)
        expanded = self.tmux("display-message", "-p", "-c", self.client,
                             "-t", self.pane, command).strip()
        self.drain()
        process = subprocess.Popen([TMUX, "-N", "-S", self.socket, *shlex.split(expanded)],
                                   env=self.env, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        self.children.append(process)
        self.output("? (y/n)")
        return process

    def test_window_kill_keeps_id_after_focus_change(self):
        before = self.panes()
        process = self.window_confirmation("kill-window")
        self.tmux("select-window", "-t", self.other)
        os.write(self.master, b"y")
        process.wait(timeout=3)
        self.assertEqual(self.panes(), [row for row in before if row.startswith(self.other + ":")])

    def test_window_respawn_keeps_id_after_focus_change(self):
        before = self.panes()
        process = self.window_confirmation("respawn-window")
        self.tmux("select-window", "-t", self.other)
        os.write(self.master, b"y")
        process.wait(timeout=3)
        self.assertNotEqual(self.panes(), before)
        self.assertEqual([row for row in self.panes() if row.startswith(self.other + ":")],
                         [row for row in before if row.startswith(self.other + ":")])

    def test_stale_window_never_kills_replacement(self):
        process = self.window_confirmation("kill-window")
        self.tmux("kill-window", "-t", self.pane)
        self.tmux("new-window", "-d", "-t", "fixture", "sleep 120")
        before = self.panes()
        os.write(self.master, b"y\x1b[24~")
        self.wait(lambda: self.tmux("show-options", "-gqv", "@fixture_ack").strip() == "1")
        process.wait(timeout=3)
        self.assertEqual(self.panes(), before)


if __name__ == "__main__":
    unittest.main()
