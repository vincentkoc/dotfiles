#!/usr/bin/env python3
import errno
import json
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import subprocess
import tempfile
import unittest


QUICKSSH = Path(__file__).resolve().parents[1] / "bin" / "quickssh"
KEEPALIVE = ["-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
TMUX = shutil.which("tmux")
STUB = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["CALL_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
if name == "tmux" and args[0] == "display-message":
    print("@17" if args[-1] == "#{window_id}" else os.environ["PANE_COUNT"])
elif name == "fzf":
    print(sys.stdin.read().splitlines()[0])
elif name in ("ssh", "mosh"):
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
        for name in ("ssh", "mosh", "tmux", "fzf"):
            stub = self.bin / name
            stub.write_text(STUB)
            stub.chmod(0o755)
        (self.extras / "quickssh_hosts.sh").write_text(
            "quickssh_hosts=(testbox mobilebox)\n"
            "quickssh_hosts_testbox='tester:node.example.invalid:2222:'\n"
            "quickssh_hosts_mobilebox='tester:mobile.example.invalid:22:'\n"
            "quickssh_mosh_hosts=(mobilebox)\n"
        )
        self.env = {
            **os.environ,
            "HOME": str(self.root),
            "EXTRASPATH": str(self.extras),
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "CALL_LOG": str(self.log),
            "PANE_COUNT": "1",
            "TERM": "xterm-256color",
        }
        for key in ("TMUX", "TMUX_PANE", "TRANSPORT_EXIT"):
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
        self.assertEqual((code, output), (0, b"\x1b]2;testbox\x07"))
        self.assertEqual(
            calls, [["ssh", *KEEPALIVE, "-p", "2222", "tester@node.example.invalid"]]
        )

    def test_picker_title_uses_selected_alias(self):
        code, output, calls = self.run_quickssh(terminal=True)
        self.assertEqual((code, output), (0, b"\x1b]2;testbox\x07"))
        self.assertEqual(calls[-1][-1], "tester@node.example.invalid")

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
        self.assertEqual((code, output), (0, b"\x1b]2;mobilebox\x07"))
        self.assertEqual(
            calls,
            [[
                "mosh", "-4", "-a", "--bind-server=any",
                "--ssh=ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -p 22",
                "--", "tester@mobile.example.invalid",
            ]],
        )

    def test_forwarding_falls_back_to_ssh_with_title(self):
        code, output, calls = self.run_quickssh(
            "-l", "8080", "-d", "80", "mobilebox", terminal=True
        )
        self.assertEqual((code, output), (0, b"\x1b]2;mobilebox\x07"))
        self.assertEqual(
            calls,
            [["ssh", *KEEPALIVE, "-p", "22", "-L", "8080:localhost:80",
              "tester@mobile.example.invalid"]],
        )

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
        self.assertEqual((code, output), (0, f"\x1b]2;{expected}\x07".encode()))
        self.assertEqual(calls[-1][-1], alias)
        code, output, calls = self.run_quickssh(
            alias, terminal=True, TMUX_PANE="%7"
        )
        self.assertEqual((code, output), (0, b""))
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
                self.assertEqual((code, output), (0, b""))
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
        self.assertEqual((code, output), (0, b""))
        self.assertEqual(
            calls[:-1],
            [
                ["tmux", "set-option", "-pt", "%7", "@tt_base_title", "testbox"],
                ["tmux", "select-pane", "-t", "%7", "-T", "testbox"],
                ["tmux", "display-message", "-p", "-t", "%7", "#{window_id}"],
                ["tmux", "display-message", "-p", "-t", "@17", "#{window_panes}"],
            ],
        )

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
