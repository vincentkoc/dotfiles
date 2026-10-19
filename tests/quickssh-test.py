#!/usr/bin/env python3
import errno
import json
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import signal
import subprocess
import tempfile
import termios
import time
import unittest


QUICKSSH = Path(__file__).resolve().parents[1] / "bin" / "quickssh"
KEEPALIVE = ["-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
TMUX = shutil.which("tmux")
MOSH = shutil.which("mosh")
TERMINAL_CLEANUP = b"\x1b[?9;1000;1001;1002;1003;1004;1005;1006;1015;1016;2004l"
STUB = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
if name == "fleet-credentials":
    import atexit, signal, termios
    state = termios.tcgetattr(0) if os.isatty(0) else None
    def restore():
        if state is not None:
            termios.tcsetattr(0, termios.TCSANOW, state)
        if os.isatty(1) and os.environ.get("TERM") not in (None, "", "dumb"):
            os.write(1, b"\\x1b[?9;1000;1001;1002;1003;1004;1005;1006;1015;1016;2004l")
    def interrupted(sig, frame):
        sys.exit(128 + sig)
    atexit.register(restore)
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupted)
with open(os.environ["CALL_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
if name == "tmux" and args[0] == "display-message":
    print("@17" if args[-1] == "#{window_id}" else os.environ["PANE_COUNT"])
elif name == "fzf":
    lines = sys.stdin.read().splitlines()
    Path(os.environ["PICKER_LOG"]).write_text("\\n".join(lines) + "\\n")
    print(lines[0])
elif name in ("ssh", "mosh", "fleet-credentials"):
    if os.environ.get("DIRTY_TERMINAL"):
        import termios
        attrs = termios.tcgetattr(0)
        attrs[3] &= ~(termios.ECHO | termios.ICANON)
        termios.tcsetattr(0, termios.TCSANOW, attrs)
        os.write(1, b"\\x1b[?1003h\\x1b[?1006h")
    if os.environ.get("TRANSPORT_SIGNAL"):
        import signal
        # Signal the foreground process group, as a terminal interrupt does.
        os.killpg(os.getpgrp(), int(os.environ["TRANSPORT_SIGNAL"]))
    sys.exit(int(os.environ.get("TRANSPORT_EXIT", "0")))
"""


class QuicksshTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.extras = self.root / "extras"
        self.extras.mkdir()
        self.log = self.root / "calls.jsonl"
        self.picker_log = self.root / "picker.txt"
        self.tmux_tmpdir = self.root / "tmux-root"
        self.tmux_tmpdir.mkdir()
        self.tmux_tmpdir.chmod(0o700)
        for name in ("ssh", "mosh", "tmux", "fzf", "fleet-credentials", "op", "pbcopy"):
            stub = self.bin / name
            stub.write_text(STUB)
            stub.chmod(0o755)
        (self.extras / "quickssh_hosts.sh").write_text(
            "quickssh_hosts=(testbox mobilebox)\n"
            "quickssh_hosts_testbox='tester:node.example.invalid:2222:'\n"
            "quickssh_hosts_mobilebox='tester:mobile.example.invalid:22:'\n"
            "quickssh_descriptions_testbox='shared infra · test host'\n"
            "quickssh_mosh_hosts=(mobilebox)\n"
        )
        self.env = {
            **os.environ,
            "HOME": str(self.root),
            "EXTRASPATH": str(self.extras),
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "CALL_LOG": str(self.log),
            "PICKER_LOG": str(self.picker_log),
            "PANE_COUNT": "1",
            "TERM": "xterm-256color",
            "TT_ISOLATED_FIXTURE": "1",
            "TT_TMUX_BIN": str(self.bin / "tmux"),
            "TMUX_TMPDIR": str(self.tmux_tmpdir),
        }
        for key in ("TMUX", "TMUX_PANE", "TRANSPORT_EXIT", "DIRTY_TERMINAL", "TRANSPORT_SIGNAL"):
            self.env.pop(key, None)

    def run_quickssh(self, *args, terminal=False, **changes):
        env = self.env | changes
        if not terminal:
            result = subprocess.run(
                [str(QUICKSSH), *args], env=env, capture_output=True, timeout=10
            )
            output = result.stdout
            code = result.returncode
        else:
            master, slave = pty.openpty()
            process = subprocess.Popen(
                [str(QUICKSSH), *args],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=slave,
                stderr=subprocess.DEVNULL,
            )
            os.close(slave)
            output = b""
            try:
                while select.select([master], [], [], 10)[0]:
                    try:
                        chunk = os.read(master, 4096)
                    except OSError as error:
                        if error.errno != errno.EIO:
                            raise
                        break
                    if not chunk:
                        break
                    output += chunk
                code = process.wait(timeout=10)
            finally:
                os.close(master)
                if process.poll() is None:
                    process.kill()
                    process.wait()
        calls = (
            [json.loads(line) for line in self.log.read_text().splitlines()]
            if self.log.exists()
            else []
        )
        return code, output, calls

    def test_direct_ssh_title_uses_alias_not_private_target(self):
        code, output, calls = self.run_quickssh("testbox", terminal=True)
        self.assertEqual((code, output), (0, b"\x1b]2;testbox\x07" + TERMINAL_CLEANUP))
        self.assertEqual(
            calls, [["ssh", *KEEPALIVE, "-p", "2222", "tester@node.example.invalid"]]
        )

    def service_alias(self, alias="testbox"):
        with (self.extras / "quickssh_hosts.sh").open("a") as hosts:
            hosts.write(f"quickssh_service_targets_{alias}='approved-target'\n")
            hosts.write(f"quickssh_hosts_{alias}='tester:node.example.invalid:22:$1password'\n")

    def test_service_alias_bypasses_desktop_password_and_preserves_failure(self):
        self.service_alias()
        code, output, calls = self.run_quickssh("testbox", terminal=True, TRANSPORT_EXIT="41")
        self.assertEqual(code, 41)
        self.assertEqual(output, b"\x1b]2;testbox\x07" + TERMINAL_CLEANUP)
        self.assertEqual(calls, [["fleet-credentials", "ssh", "approved-target", "--"]])

    def test_service_alias_screen_forwarding_and_mosh(self):
        self.service_alias("mobilebox")
        with (self.extras / "quickssh_hosts.sh").open("a") as hosts:
            hosts.write("quickssh_mosh_ports_mobilebox='60000:60010'\n")
        code, _, calls = self.run_quickssh("-s", "mobilebox")
        self.assertEqual(code, 0)
        self.assertEqual(calls, [["fleet-credentials", "ssh", "approved-target", "--mosh",
                                 "--mosh-ports", "60000:60010", "--", "screen", "-xRR"]])
        self.log.unlink()
        code, _, calls = self.run_quickssh("-s", "-l", "8080", "-d", "80", "mobilebox")
        self.assertEqual(code, 0)
        self.assertEqual(calls, [["fleet-credentials", "ssh", "approved-target", "--local-forward",
                                 "8080:80", "--tty", "--", "screen", "-xRR"]])

    def test_missing_service_helper_never_falls_back(self):
        self.service_alias()
        # An installed-but-unavailable helper is still an opted-in alias.
        (self.bin / "fleet-credentials").write_text("#!/bin/sh\nexit 127\n")
        code, _, calls = self.run_quickssh("testbox")
        self.assertEqual(code, 127)
        self.assertEqual(calls, [])

    def test_picker_title_uses_selected_alias(self):
        code, output, calls = self.run_quickssh(terminal=True)
        self.assertEqual((code, output), (0, b"\x1b]2;testbox\x07" + TERMINAL_CLEANUP))
        self.assertEqual(calls[-1][-1], "tester@node.example.invalid")
        self.assertEqual(
            self.picker_log.read_text().splitlines(),
            [
                "testbox                      shared infra · test host",
                "mobilebox                    ssh · tester@mobile.example.invalid:22",
            ],
        )

    def test_dotted_host_bypasses_legacy_variable_lookup(self):
        target = "node.example.invalid"
        result = subprocess.run(
            [str(QUICKSSH), target],
            env=self.env | {"TRANSPORT_EXIT": "43"},
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 43)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            [json.loads(line) for line in self.log.read_text().splitlines()],
            [["ssh", *KEEPALIVE, target]],
        )

    def test_mosh_title_and_exec_arguments(self):
        code, output, calls = self.run_quickssh("mobilebox", terminal=True)
        self.assertEqual((code, output), (0, b"\x1b]2;mobilebox\x07" + TERMINAL_CLEANUP))
        self.assertEqual(
            calls,
            [[
                "mosh", "-4", "-a", "--bind-server=any",
                "--ssh=ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -p 22",
                "--", "tester@mobile.example.invalid",
            ]],
        )

    def test_forwarding_falls_back_to_ssh_with_title(self):
        with (self.extras / "quickssh_hosts.sh").open("a") as hosts:
            hosts.write("quickssh_mosh_ports_mobilebox='60000:60010'\n")
        code, output, calls = self.run_quickssh(
            "-l", "8080", "-d", "80", "mobilebox", terminal=True
        )
        self.assertEqual((code, output), (0, b"\x1b]2;mobilebox\x07" + TERMINAL_CLEANUP))
        self.assertEqual(
            calls,
            [["ssh", *KEEPALIVE, "-p", "22", "-L", "8080:localhost:80",
              "tester@mobile.example.invalid"]],
        )

    def test_configured_mosh_ports_follow_hyphenated_alias(self):
        with (self.extras / "quickssh_hosts.sh").open("a") as hosts:
            hosts.write(
                "quickssh_hosts_test_box='tester:node.example.invalid:2222:'\n"
                "quickssh_mosh_hosts+=(test-box)\n"
                "quickssh_mosh_ports_test_box='60000:60010'\n"
            )
        code, _, calls = self.run_quickssh("test-box")
        self.assertEqual(code, 0)
        self.assertEqual(
            calls,
            [[
                "mosh", "-4", "-a", "--bind-server=any",
                "--ssh=ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -p 2222",
                "--port=60000:60010", "--", "tester@node.example.invalid",
            ]],
        )

    def test_explicit_mosh_dotted_host_has_no_configured_ports(self):
        code, _, calls = self.run_quickssh("-m", "node.example.invalid")
        self.assertEqual(code, 0)
        self.assertEqual(calls[-1][-2:], ["--", "node.example.invalid"])
        self.assertFalse(any(arg.startswith("--port") for arg in calls[-1]))

    @unittest.skipUnless(MOSH, "mosh is not installed")
    def test_malformed_mosh_ports_fail_before_ssh(self):
        (self.bin / "mosh").unlink()
        (self.bin / "mosh").symlink_to(MOSH)
        for ports in ("60010:60000", "60000:65536", "60000\t60010", "60000\n"):
            with self.subTest(ports=ports):
                with (self.extras / "quickssh_hosts.sh").open("a") as hosts:
                    hosts.write(f"quickssh_mosh_ports_mobilebox={shlex.quote(ports)}\n")
                code, _, calls = self.run_quickssh("mobilebox")
                self.assertNotEqual(code, 0)
                self.assertEqual(calls, [])

    def test_invalid_arguments_and_help_do_not_mutate_titles(self):
        cases = (
            (("-l", "8080", "testbox"), 2),
            (("-m", "-l", "8080", "-d", "80", "testbox"), 2),
            (("-h",), 0),
            (("testbox", "extra"), 2),
            (("-z",), 2),
        )
        for args, expected in cases:
            with self.subTest(args=args):
                code, output, calls = self.run_quickssh(
                    *args, terminal=True, TMUX_PANE="%7"
                )
                self.assertEqual(code, expected)
                self.assertNotIn(b"\x1b]", output)
                self.assertEqual(calls, [])

    def test_remote_commands_and_transport_exit_are_preserved(self):
        for option, command in (
            ("-t", ["sh", "-lc", "tmux attach || tmux new-session"]),
            ("-s", ["screen", "-xRR"]),
        ):
            for transport in ((), ("-m",)):
                with self.subTest(option=option, transport=transport):
                    code, _, calls = self.run_quickssh(
                        *transport, option, "testbox", TRANSPORT_EXIT="43"
                    )
                    self.assertEqual(code, 43)
                    self.assertEqual(calls[-1][-len(command):], command)
                    self.assertEqual(calls[-1][0], "mosh" if transport else "ssh")

    def test_non_terminal_and_dumb_terminal_stay_quiet(self):
        for terminal, term in ((False, "xterm-256color"), (True, "dumb"), (True, "")):
            with self.subTest(terminal=terminal, term=term):
                code, output, _ = self.run_quickssh(
                    "testbox", terminal=terminal, TERM=term
                )
                self.assertEqual((code, output), (0, b""))

    def test_controls_are_removed_without_changing_transport_input(self):
        self.env["EXTRASPATH"] = str(self.root / "missing")
        alias = "literal%name#{session_name}\x1b]2;bad\x07\n\r\t\x7f"
        expected = "literal%name#{session_name}]2;bad"
        code, output, calls = self.run_quickssh(alias, terminal=True)
        self.assertEqual((code, output), (0, f"\x1b]2;{expected}\x07".encode() + TERMINAL_CLEANUP))
        self.assertEqual(calls[-1][-1], alias)
        code, output, calls = self.run_quickssh(
            alias, terminal=True, TMUX_PANE="%7"
        )
        self.assertEqual((code, output), (0, TERMINAL_CLEANUP))
        self.assertIn(
            ["tmux", "select-pane", "-t", "%7", "-T", expected.replace("#", "##")],
            calls,
        )

    def test_tmux_title_applies_to_ssh_and_mosh_single_pane(self):
        for alias in ("testbox", "mobilebox"):
            with self.subTest(alias=alias):
                code, output, calls = self.run_quickssh(
                    alias, terminal=True, TMUX_PANE="%7"
                )
                self.assertEqual((code, output), (0, TERMINAL_CLEANUP))
                expected = [
                    ["tmux", "set-option", "-pt", "%7", "@tt_base_title", alias],
                    ["tmux", "select-pane", "-t", "%7", "-T", alias],
                    ["tmux", "display-message", "-p", "-t", "%7", "#{window_id}"],
                    ["tmux", "display-message", "-p", "-t", "@17", "#{window_panes}"],
                    ["tmux", "set-window-option", "-t", "@17", "automatic-rename", "off"],
                    ["tmux", "rename-window", "-t", "@17", alias],
                ]
                self.assertEqual(calls[-7:-1], expected)

    def test_tmux_multi_pane_preserves_window_and_other_panes(self):
        code, output, calls = self.run_quickssh(
            "testbox", terminal=True, TMUX_PANE="%7", PANE_COUNT="3"
        )
        self.assertEqual((code, output), (0, TERMINAL_CLEANUP))
        self.assertEqual(
            calls[:-1],
            [
                ["tmux", "set-option", "-pt", "%7", "@tt_base_title", "testbox"],
                ["tmux", "select-pane", "-t", "%7", "-T", "testbox"],
                ["tmux", "display-message", "-p", "-t", "%7", "#{window_id}"],
                ["tmux", "display-message", "-p", "-t", "@17", "#{window_panes}"],
            ],
        )

    def test_disconnect_restores_termios_and_mouse_modes(self):
        self.service_alias("servicebox")
        for alias in ("testbox", "mobilebox", "servicebox"):
            for status, signum in ((0, None), (43, None), (255, None),
                                   (129, signal.SIGHUP), (130, signal.SIGINT),
                                   (143, signal.SIGTERM)):
                with self.subTest(alias=alias, status=status):
                    master, slave = pty.openpty()
                    before = termios.tcgetattr(slave)
                    env = self.env | {"DIRTY_TERMINAL": "1", "TRANSPORT_EXIT": str(status)}
                    if signum is not None:
                        env["TRANSPORT_SIGNAL"] = str(signum)
                    process = subprocess.Popen(
                        [str(QUICKSSH), alias], env=env, stdin=slave,
                        stdout=slave, stderr=slave, process_group=0,
                    )
                    try:
                        output = b""
                        deadline = time.monotonic() + 10
                        # Drain concurrently: tty restoration can wait for pending
                        # output, so waiting for exit before reading deadlocks.
                        while process.poll() is None:
                            if time.monotonic() > deadline:
                                raise subprocess.TimeoutExpired(process.args, 10)
                            if select.select([master], [], [], 0.1)[0]:
                                output += os.read(master, 4096)
                        while select.select([master], [], [], 0)[0]:
                            chunk = os.read(master, 4096)
                            if not chunk:
                                break
                            output += chunk
                        code = process.wait()
                        after = termios.tcgetattr(slave)
                        # Darwin sets PENDIN when canonical input is restored;
                        # this kernel retype marker is not a caller setting.
                        before[3] &= ~getattr(termios, "PENDIN", 0)
                        after[3] &= ~getattr(termios, "PENDIN", 0)
                        self.assertEqual(after, before)
                        self.assertEqual(code, status)
                        self.assertIn(b"\x1b[?1003h\x1b[?1006h", output)
                        self.assertTrue(output.endswith(TERMINAL_CLEANUP), output)
                        self.assertEqual(output.count(TERMINAL_CLEANUP), 1)
                    finally:
                        if slave is not None:
                            os.close(slave)
                        os.close(master)
                        if process.poll() is None:
                            process.kill()
                            process.wait()

    @unittest.skipUnless(TMUX, "tmux is not installed")
    def test_real_tmux_literal_titles_and_sibling_preservation(self):
        tmux = [TMUX, "-S", str(self.root / "tmux.sock")]
        (self.bin / "tmux").write_text(
            "#!/bin/sh\nexec " + shlex.join(tmux) + ' "$@"\n'
        )

        def run(*args):
            return subprocess.check_output(
                [*tmux, *args], env=self.env, text=True, timeout=10
            ).strip()

        run("-f", "/dev/null", "new-session", "-d", "-s", "fixture", "/bin/sleep 60")
        try:
            pane = run("display-message", "-p", "-t", "fixture:0", "#{pane_id}")
            self.env["EXTRASPATH"] = str(self.root / "missing")
            alias = "literal%name#{session_name}"
            code, _, _ = self.run_quickssh(alias, TMUX_PANE=pane)
            self.assertEqual(code, 0)
            self.assertEqual(
                run("display-message", "-p", "-t", pane,
                    "#{pane_title}|#{window_name}|#{@tt_base_title}"),
                f"{alias}|{alias}|{alias}",
            )

            sibling = run(
                "split-window", "-d", "-P", "-F", "#{pane_id}",
                "-t", pane, "/bin/sleep 60",
            )
            run("select-pane", "-t", sibling, "-T", "sibling")
            run("rename-window", "-t", "fixture:0", "shared")
            before = run(
                "display-message", "-p", "-t", sibling,
                "#{pane_title}|#{window_name}|#{automatic-rename}|#{pane_active}",
            )
            code, _, _ = self.run_quickssh("another", TMUX_PANE=pane)
            self.assertEqual(code, 0)
            self.assertEqual(
                run("display-message", "-p", "-t", sibling,
                    "#{pane_title}|#{window_name}|#{automatic-rename}|#{pane_active}"),
                before,
            )
        finally:
            run("kill-session", "-t", "fixture")


if __name__ == "__main__":
    unittest.main()
