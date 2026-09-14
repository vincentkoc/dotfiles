#!/usr/bin/env python3
"""Exercise cockpit window bindings on a private real tmux server."""

import fcntl
import os
import pathlib
import pty
import select
import shlex
import shutil
import struct
import subprocess
import tempfile
import termios
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
TMUX = shutil.which("tmux")


class NewWindowTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(TMUX, "real tmux is required")
        self.temporary = tempfile.TemporaryDirectory(prefix="tt-window-", dir="/tmp")
        self.root = pathlib.Path(self.temporary.name).resolve()
        self.socket = self.root / "socket"
        self.work = self.root / "work ' with $spaces"
        for name in ("home/bin", "config", "state"):
            (self.root / name).mkdir(parents=True)
        self.work.mkdir()
        (self.root / "home/bin/tt").symlink_to(REPO / "bin/tt")
        self.env = {
            "PATH": f"{pathlib.Path(TMUX).parent}:/usr/bin:/bin",
            "HOME": str(self.root / "home"), "SHELL": "/bin/sh",
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_STATE_HOME": str(self.root / "state"),
            "TERM": "xterm-256color", "TT_LOGIN_SHELL": "/bin/sh",
            "TT_TMUX_BIN": TMUX,
        }
        self.sessions = ["cockpit"]
        self.addCleanup(self.cleanup)
        self.source = self.tmux(
            "new-session", "-d", "-x", "240", "-y", "80", "-s", "cockpit",
            "-P", "-F", "#{pane_id}", "-c", self.work, "/bin/sh",
        )
        self.tmux("set-option", "-g", "default-shell", "/bin/sh")
        self.tmux("set-option", "-g", "pane-border-status", "off")
        self.tmux("set-option", "-t", "cockpit", "@tt_profile", "ops")
        self.session = self.display(self.source, "#{session_id}")
        self.window = self.display(self.source, "#{window_id}")

    def cleanup(self):
        for session in self.sessions:
            self.run_tmux("kill-session", "-t", session, check=False)
        self.temporary.cleanup()

    def run_tmux(self, *args, check=True):
        return subprocess.run(
            [TMUX, "-N", "-S", str(self.socket), "-f", "/dev/null", *map(str, args)],
            env=self.env, capture_output=True, text=True, timeout=15, check=check,
        )

    def tmux(self, *args):
        # Only the initial creation may start this task's private server.
        if args[0] == "new-session" and not self.socket.exists():
            result = subprocess.run(
                [TMUX, "-S", str(self.socket), "-f", "/dev/null", *map(str, args)],
                env=self.env, capture_output=True, text=True, timeout=15, check=True,
            )
        else:
            result = self.run_tmux(*args)
        return result.stdout.strip()

    def display(self, target, value):
        return self.tmux("display-message", "-p", "-t", target, value)

    def windows(self):
        return self.tmux("list-windows", "-t", self.session, "-F", "#{window_id}").splitlines()

    def panes(self, target):
        return self.tmux(
            "list-panes", "-t", target, "-F",
            "#{pane_id}|#{pane_pid}|#{pane_left}|#{pane_top}|#{pane_width}|"
            "#{pane_height}|#{pane_title}|#{@tt_base_title}|#{pane_current_path}",
        ).splitlines()

    def invoke(self, *args, env=None, check=True):
        return subprocess.run(
            [str(REPO / "bin/tt"), "new-window", "--socket", str(self.socket),
             "--target", self.source, *args],
            env={**self.env, **(env or {})}, capture_output=True, text=True,
            timeout=20, check=check,
        )

    def assert_grid(self, target):
        rows = [row.split("|") for row in self.panes(target)]
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({row[2] for row in rows}), 3)
        self.assertEqual(len({row[3] for row in rows}), 2)
        self.assertEqual({row[-1] for row in rows}, {str(self.work)})
        self.assertEqual([row[6] for row in rows], [f"shell.{i}" for i in range(1, 7)])
        self.assertEqual(self.tmux("show-options", "-wqv", "-t", target, "pane-border-status"), "top")
        self.assertIn("pane_title", self.tmux("show-options", "-wqv", "-t", target, "pane-border-format"))
        self.assertEqual(self.display(self.session, "#{window_id}"), target)

    def test_after_preserves_existing_panes_hooks_and_focus_scope(self):
        other = self.tmux("new-window", "-d", "-P", "-F", "#{window_id}", "-t", self.session)
        self.tmux("split-window", "-d", "-t", other)
        self.tmux("set-option", "-p", "-t", self.source, "@tt_base_title", "keep me")
        self.tmux("select-pane", "-t", self.source, "-T", "existing owner")
        self.tmux("set-hook", "-g", "after-new-window[17]", "display-message fixture-hook")
        before = {window: self.panes(window) for window in self.windows()}
        hook = self.tmux("show-hooks", "-g", "after-new-window")
        self.sessions.append("unrelated")
        self.tmux("new-session", "-d", "-s", "unrelated", "/bin/sh")
        unrelated = self.display("unrelated", "#{window_id}")
        self.invoke("--after")
        windows = self.windows()
        self.assertEqual(windows[::2], [self.window, other])
        self.assert_grid(windows[1])
        self.assertEqual(before, {window: self.panes(window) for window in before})
        self.assertEqual(hook, self.tmux("show-hooks", "-g", "after-new-window"))
        self.assertEqual(self.display("unrelated", "#{window_id}"), unrelated)
        self.assertEqual(self.tmux("show-options", "-gqv", "pane-border-status"), "off")

    def test_end_uses_explicit_source_and_socket_despite_wrong_environment(self):
        self.tmux("new-window", "-d", "-t", self.session)
        self.invoke(env={"TMUX": f"{self.root}/nonexistent,1,0", "TMUX_PANE": "%999"})
        self.assert_grid(self.windows()[-1])

    def test_non_cockpit_and_raw_new_window_stay_single_pane(self):
        native = self.tmux("new-window", "-d", "-P", "-F", "#{window_id}", "-t", self.session)
        native_cwd = self.display(native, "#{pane_current_path}")
        for profile in (None, "plain", "studio", "ops"):
            if profile is None:
                self.tmux("set-option", "-u", "-t", self.session, "@tt_profile")
            else:
                self.tmux("set-option", "-t", self.session, "@tt_profile", profile)
            if profile == "ops":
                created = self.tmux("new-window", "-d", "-P", "-F", "#{window_id}", "-t", self.session)
            else:
                self.invoke()
                created = self.windows()[-1]
            self.assertEqual(self.display(created, "#{window_panes}"), "1")
            self.assertEqual(self.display(created, "#{pane_current_path}"), native_cwd)

    def test_agent_gate_checks_exact_server_session_window_before_mutation(self):
        before = self.windows()
        expected = f"new-window:{self.socket}:{self.session}:{self.window}"
        for scope in ("", f"create:{self.session}", expected + "9"):
            result = self.invoke(env={"CODEX_THREAD_ID": "fixture-agent", "TT_OPERATOR_TMUX_SCOPE": scope}, check=False)
            self.assertEqual(result.returncode, 73, result.stderr)
            self.assertIn("TT_OPERATOR_TMUX_SCOPE=", result.stderr)
            self.assertEqual(self.windows(), before)
        self.invoke(env={"CODEX_THREAD_ID": "fixture-agent", "TT_OPERATOR_TMUX_SCOPE": expected})
        self.assert_grid(self.windows()[-1])

    def test_missing_target_refuses_without_mutation(self):
        before = self.windows()
        result = self.invoke("--target", "%999999", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.windows(), before)

    def test_status_menu_actions_use_the_clicked_window(self):
        # Source only the three production binding blocks, as a local deployment would.
        local = (REPO / ".tmux.conf.local").read_text()
        bindings = local.split("# Cockpit windows use", 1)[1].split("# Recover a pane", 1)[0]
        bindings = bindings[bindings.index("bind c "):]
        subprocess.run(
            [TMUX, "-N", "-S", str(self.socket), "source-file", "-"],
            input=bindings, env=self.env, text=True, capture_output=True, check=True, timeout=10,
        )
        other = self.tmux("new-window", "-P", "-F", "#{window_id}", "-t", self.session)
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 80, 240, 0, 0))
        client = subprocess.Popen(
            [TMUX, "-N", "-S", str(self.socket), "attach-session", "-t", self.session],
            env=self.env, stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
        )
        os.close(slave)
        try:
            self.wait_terminal(master, b"0:")
            for key in ("MouseDown3Status", "M-MouseDown3Status"):
                for choice in ("w", "W"):
                    with self.subTest(binding=key, choice=choice):
                        self.tmux("select-window", "-t", other)
                        before = self.windows()
                        binding = self.tmux("list-keys", "-T", "root", key)
                        command = binding.split(" display-menu ", 1)[1]
                        # Replace the mouse target with its resolved source pane, leaving
                        # all stored menu action strings and their nested formats intact.
                        command = "display-menu " + command.replace("-t = ", f"-t {self.source} ", 1)
                        title = f"{key}-{choice}"
                        command = command.replace(
                            '-T "#[align=centre]#{window_index}:#{window_name}"', f'-T "{title}"',
                        )
                        menu = subprocess.Popen(
                            [TMUX, "-N", "-S", str(self.socket), "run-shell", "-C", command.replace("#{", "##{")],
                            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        )
                        try:
                            self.wait_terminal(master, title.encode())
                            os.write(master, choice.encode())
                            stdout, stderr = menu.communicate(timeout=10)
                            self.assertEqual(menu.returncode, 0, stdout + stderr)
                        finally:
                            if menu.poll() is None:
                                menu.terminate()
                                menu.communicate(timeout=5)
                        deadline = time.monotonic() + 10
                        while time.monotonic() < deadline and self.windows() == before:
                            time.sleep(0.02)
                        created = next(window for window in self.windows() if window not in before)
                        while time.monotonic() < deadline and self.display(self.session, "#{window_id}") != created:
                            time.sleep(0.02)
                        if choice == "w":
                            self.assertEqual(self.windows()[1], created)
                        else:
                            self.assertEqual(self.windows()[-1], created)
                        self.assert_grid(created)
        finally:
            os.close(master)
            try:
                client.wait(timeout=5)
            except subprocess.TimeoutExpired:
                client.terminate()
                client.wait(timeout=5)

    def wait_terminal(self, master, needle):
        output = b""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                output += os.read(master, 65536)
                if needle in output:
                    return
        self.fail(f"fixture terminal never displayed {needle!r}: {output[-500:]!r}")

    def test_full_configuration_retains_all_three_bindings(self):
        home = self.root / "home"
        shutil.copyfile(REPO / ".tmux.conf", home / ".tmux.conf")
        local = (REPO / ".tmux.conf.local").read_text().replace(
            "tmux_conf_new_window_retain_current_path=false",
            "tmux_conf_new_window_retain_current_path=true",
        )
        # Two marker bytes prove the existing async configuration second source finished.
        marker = home / "applied"
        (home / ".tmux.conf.local").write_text(local + f"\nrun-shell 'printf . >> {shlex.quote(str(marker))}'\n")
        self.tmux("source-file", home / ".tmux.conf")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if marker.exists() and marker.read_text() == "..":
                break
            time.sleep(0.1)
        self.assertTrue(marker.exists() and marker.read_text() == "..", "full configuration did not finish")
        # Reapply synchronously too, so the retain-path regex and final overrides are checked.
        self.tmux("run-shell", "cut -c3- ~/.tmux.conf | tmux_conf_new_window_retain_current_path=true sh -s _apply_bindings; tmux source-file ~/.tmux.conf.local")
        keyboard = self.tmux("list-keys", "-T", "prefix", "c")
        self.assertIn("tt", keyboard)
        self.assertIn("new-window --socket", keyboard)
        self.assertNotIn("new-window -c", keyboard)
        for key in ("MouseDown3Status", "M-MouseDown3Status"):
            menu = self.tmux("list-keys", "-T", "root", key)
            self.assertEqual(menu.count("new-window"), 2)
            self.assertIn("new-window --after --socket", menu)
            self.assertIn("new-window --socket", menu)
            self.assertIn("--target #{pane_id}", menu)
            for action in ("Swap Left", "Swap Right", "Swap Marked", "Kill", "Respawn", "Rename"):
                self.assertIn(action, menu)
        # Execute the stored keyboard command through tmux's format expansion.
        command = keyboard.split(" c ", 1)[1]
        self.tmux("select-window", "-t", self.window)
        self.tmux("run-shell", "-C", command)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(self.windows()) == 1:
            time.sleep(0.05)
        created = self.windows()[-1]
        while time.monotonic() < deadline and self.display(self.session, "#{window_id}") != created:
            time.sleep(0.05)
        self.assert_grid(created)


if __name__ == "__main__":
    unittest.main(verbosity=2)
