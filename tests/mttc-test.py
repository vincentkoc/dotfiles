#!/usr/bin/env python3
"""Recording-only mosh/SSH fixtures; execute remote payloads with dash and Bash."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
SHELLS = [path for path in (shutil.which("dash"), BASH) if path]


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mttc-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.local = self.root / "local"
        self.remote = self.root / "remote"
        self.local.mkdir()
        self.remote.mkdir()
        shutil.copy2(ROOT / "bin/mttc", self.local / "mttc")
        self.log = self.root / "calls.jsonl"
        self.env = dict(HOME=str(self.root), EXTRASPATH=str(self.root),
                        PATH=str(self.local) + os.pathsep + os.defpath,
                        CALLS=str(self.log), REMOTE_PATH=str(self.remote))
        self.stub(self.local / "tt", """
record("guard", sys.argv[1:])
sys.exit(int(os.environ.get("GUARD_EXIT", "0")))
""")
        self.stub(self.local / "mosh", """
record("mosh", sys.argv[1:])
args = sys.argv[1:]
index = args.index("--")
assert args[index + 2:index + 4] == ["sh", "-lc"]
env = dict(os.environ, PATH=os.environ["REMOTE_PATH"],
           SHELL=os.environ.get("REMOTE_SHELL", ""))
sys.exit(subprocess.run([os.environ["TEST_SHELL"], "-c", args[index + 4]],
                        env=env, stdin=subprocess.DEVNULL).returncode)
""")

    def stub(self, path, body):
        path.write_text("#!" + sys.executable + "\n"
                        "import json, os, subprocess, sys\n"
                        "def record(kind, args):\n"
                        "    with open(os.environ['CALLS'], 'a') as f:\n"
                        "        f.write(json.dumps([kind, args]) + '\\n')\n" + body)
        path.chmod(0o700)

    def run_cli(self, args, tools=("mtt", "tt", "tmux"), **env):
        for tool in tools:
            self.stub(self.remote / tool, "record(%r, sys.argv[1:])\n" % tool)
        result = subprocess.run([BASH, str(self.local / "mttc"), *args],
                                env=dict(self.env, **env), capture_output=True,
                                text=True, timeout=5)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] \
            if self.log.exists() else []
        return result, calls

    def test_mobile_exact_and_picker_under_each_shell(self):
        for shell in SHELLS:
            for target in ("%4", "cockpit:2.1", "work-tree.dev:0.0", None):
                with self.subTest(shell=shell, target=target):
                    args = ["-p", "mobile"]
                    if target:
                        args += ["-s", target]
                    result, calls = self.run_cli(args + ["host.example"], TEST_SHELL=shell)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(calls[-1], ["mtt", [target] if target else []])
                    self.assertIn(["guard", ["topology-authorize", "remote-create",
                                            "host.example:" + (target or "mobile")]], calls)

    def test_invalid_or_ambiguous_mobile_target_never_connects(self):
        for target in ("cockpit", "%x", "cockpit:2", "a:1.1;id", "$(id)", "x y:1.2"):
            with self.subTest(target=target):
                result, calls = self.run_cli(["-p", "mobile", "-s", target, "host.example"])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("mobile target", result.stderr)
                self.assertEqual(calls, [])

    def test_missing_mobile_tool_never_falls_back(self):
        for shell in SHELLS:
            for target in (None, "%4"):
                args = ["-p", "mobile"] + (["-s", target] if target else [])
                result, calls = self.run_cli(args + ["host.example"], tools=("tt", "tmux"),
                                            TEST_SHELL=shell)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("requires remote mtt", result.stderr)
                self.assertFalse(any(call[0] in ("tt", "tmux") for call in calls))

    def test_shell_and_ai_dispatch(self):
        for shell in SHELLS:
            for profile, session in (("shell", "main"), ("ai", "studio")):
                result, calls = self.run_cli(["-p", profile, "host.example"], TEST_SHELL=shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls[-1], ["tt", [profile, session]])

    def test_tmux_fallback(self):
        for shell in SHELLS:
            result, calls = self.run_cli(["-s", "work", "host.example"], tools=("tmux",),
                                        TEST_SHELL=shell)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls[-1], ["tmux", ["new-session", "-A", "-s", "work"]])

    def test_zsh_fallback(self):
        for shell in SHELLS:
            result, calls = self.run_cli(["host.example"], tools=("zsh",), TEST_SHELL=shell)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls[-1], ["zsh", ["-il"]])

    def test_shell_env_fallback(self):
        self.stub(self.remote / "login-shell", 'record("shell", sys.argv[1:])\n')
        for shell in SHELLS:
            result, calls = self.run_cli(["host.example"], tools=(), TEST_SHELL=shell,
                                        REMOTE_SHELL=str(self.remote / "login-shell"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls[-1], ["shell", ["-il"]])

    def test_plain_sh_fallback_with_closed_input(self):
        for shell in SHELLS:
            result, calls = self.run_cli(["host.example"], tools=(), TEST_SHELL=shell)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls[-1][0], "mosh")
            self.assertIn("exec /bin/sh -l", calls[-1][1][-1])

    def test_alias_port_and_explicit_precedence(self):
        (self.root / "quickssh_hosts.sh").write_text(
            "quickssh_hosts_testbox='operator:host.example:2222:'\n")
        for args, port in (([], "2222"), (["-P", "2200"], "2200")):
            result, calls = self.run_cli(args + ["testbox"], TEST_SHELL=BASH,
                                        MOSH_TMUX_SSH="ssh -o Compression=yes")
            self.assertEqual(result.returncode, 0, result.stderr)
            mosh = [call[1] for call in calls if call[0] == "mosh"][-1]
            self.assertIn("--ssh=ssh -o Compression=yes -p " + port, mosh)
            self.assertEqual(mosh[mosh.index("--") + 1], "operator@host.example")
            self.assertIn(["guard", ["topology-authorize", "remote-create",
                                    "operator@host.example:main"]], calls)

    def test_bad_alias_port_fails_before_connect(self):
        (self.root / "quickssh_hosts.sh").write_text(
            "quickssh_hosts_testbox='operator:host.example:invalid:'\n")
        result, calls = self.run_cli(["testbox"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-numeric port", result.stderr)
        self.assertEqual(calls, [])
        result, _ = self.run_cli(["-P", "2222", "testbox"], TEST_SHELL=BASH)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_guard_denial_never_connects(self):
        result, calls = self.run_cli(["host.example"], GUARD_EXIT="23")
        self.assertEqual(result.returncode, 23)
        self.assertEqual([call[0] for call in calls], ["guard"])

    def test_direct_hosts_do_not_use_indirect_variable_expansion(self):
        (self.root / "quickssh_hosts.sh").write_text(
            "quickssh_hosts_testbox='operator:host.example:2222:'\n")
        for host in ("operator@host.example", "host.example", "2001:db8::1",
                     "operator@[2001:db8::1]", "unknown_alias"):
            with self.subTest(host=host):
                result, calls = self.run_cli([host], TEST_SHELL=BASH)
                self.assertEqual(result.returncode, 0, result.stderr)
                mosh = [call[1] for call in calls if call[0] == "mosh"][-1]
                self.assertEqual(mosh[mosh.index("--") + 1], host)


if __name__ == "__main__":
    unittest.main()
