#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "functions/system/update.zsh"
COMMANDS = "sudo uname softwareupdate apt-get brew npm codex codex-standalone-install pnpm yarn corepack pip pip3 gem rustup cargo composer go deno bun flutter updatedb tldr mole rm killall dscacheutil sleep".split()


class UpdateModeTests(unittest.TestCase):
    def run_mode(self, arguments, failure="", brew_owned=False, platform="Darwin"):
        with tempfile.TemporaryDirectory(prefix="update-mode-") as temporary:
            root = Path(temporary)
            log = root / "calls.jsonl"
            stub = root / "stub"
            stub.write_text("#!" + sys.executable + "\n" + """
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
with open(os.environ['UPDATE_TEST_LOG'], 'a') as stream:
    stream.write(json.dumps([name, sys.argv[1:], {key: os.environ.get(key) for key in
        ('HOMEBREW_NO_INSTALL_CLEANUP', 'NEEDRESTART_MODE')}]) + '\\n')
if name == 'uname': print(os.environ['UPDATE_TEST_PLATFORM'])
if name == 'yarn' and sys.argv[1:] == ['--version']: print('1.22.0')
if name == 'brew' and sys.argv[1:2] == ['--cellar']:
    print(str(pathlib.Path(os.environ['UPDATE_TEST_LOG']).parent / 'cellar' / sys.argv[2]))
if name == 'brew' and sys.argv[1:2] in [['--cellar'], ['list']] and os.environ.get('UPDATE_TEST_FAIL') == 'brew-ownership': sys.exit(9)
if name == os.environ.get('UPDATE_TEST_FAIL'): sys.exit(9)
if name == 'apt-get' and os.environ.get('UPDATE_TEST_FAIL') == 'apt-get:' + sys.argv[1]: sys.exit(9)
if name == 'sudo' and sys.argv[1:] != ['-v']:
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
""")
            stub.chmod(0o755)
            (root / "env").symlink_to("/usr/bin/env")
            for name in COMMANDS:
                target = stub
                if brew_owned and name in {'bun', 'deno'}:
                    target = root / 'cellar' / name / '1' / 'bin' / name
                    target.parent.mkdir(parents=True)
                    target.write_bytes(stub.read_bytes())
                    target.chmod(0o755)
                (root / name).symlink_to(target)
            environment = os.environ.copy()
            environment.pop("NEEDRESTART_MODE", None)
            environment.update(PATH=str(root), UPDATE_TEST_LOG=str(log),
                               UPDATE_TEST_FAIL=failure, UPDATE_TEST_PLATFORM=platform,
                               VIRTUAL_ENV=str(root / "venv"),
                               CONDA_PREFIX=str(root / "conda"))
            result = subprocess.run(["/bin/zsh", "-f", "-c",
                                     'source "$1"; shift; up "$@"', "update-test", str(SOURCE), *arguments],
                                    env=environment, cwd=root, text=True, capture_output=True, timeout=10)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, calls

    def test_help_and_bad_arguments_do_not_run_commands(self):
        for arguments, status in [(["--help"], 0), (["--unknown"], 2),
                                  (["--updates-only", "extra"], 2),
                                  (["--system-only", "--updates-only"], 2)]:
            result, calls = self.run_mode(arguments)
            self.assertEqual(result.returncode, status, result.stderr)
            self.assertEqual(calls, [])

    def test_updates_only_has_no_cleanup_project_changes_or_keepalive(self):
        for platform in ("Darwin", "Linux"):
            result, calls = self.run_mode(["--updates-only"], platform=platform)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(any(name == "codex-standalone-install" for name, _, _ in calls))
            self.assertTrue(any(name == ("apt-get" if platform == "Linux" else "softwareupdate")
                                for name, _, _ in calls))
            for name, args, environment in calls:
                self.assertNotIn(name, {"rm", "killall", "dscacheutil", "mole", "sleep", "pip", "pip3", "go", "updatedb"})
                self.assertNotIn("cleanup", args)
                self.assertNotEqual(args, ["-n", "true"])
                self.assertNotEqual((name, args), ("flutter", ["pub", "get"]))
                if name == "brew":
                    self.assertEqual(environment["HOMEBREW_NO_INSTALL_CLEANUP"], "1")

    def test_system_only_runs_interactive_os_update_without_other_work(self):
        for platform in ("Darwin", "Linux"):
            result, calls = self.run_mode(["--system-only"], platform=platform)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls[0][:2], ["sudo", ["-v"]])
            self.assertTrue(all(name in {"sudo", "uname", "softwareupdate", "apt-get"}
                                for name, _, _ in calls))
            if platform == "Linux":
                apt_calls = [(args, environment["NEEDRESTART_MODE"])
                             for name, args, environment in calls if name == "apt-get"]
                self.assertEqual(apt_calls, [(["update", "--error-on=any"], None),
                                            (["upgrade", "--with-new-pkgs", "--no-remove"], "l")])
            else:
                self.assertIn(["softwareupdate", ["-i", "-a"]], [call[:2] for call in calls])

    def test_system_only_propagates_auth_and_apt_failures(self):
        for failure in ("sudo", "apt-get:update", "apt-get:upgrade"):
            result, calls = self.run_mode(["--system-only"], failure, platform="Linux")
            self.assertEqual(result.returncode, 9, result.stderr)
            if failure == "sudo":
                self.assertEqual(len(calls), 1)
            if failure == "apt-get:update":
                self.assertFalse(any(name == "apt-get" and args[0] == "upgrade"
                                     for name, args, _ in calls))

    def test_package_and_auth_failures_propagate(self):
        failures = [(name, "Darwin") for name in ("brew", "npm", "codex-standalone-install", "sudo")]
        failures += [("apt-get:update", "Linux"), ("apt-get:upgrade", "Linux")]
        for failure, platform in failures:
            result, calls = self.run_mode(["--updates-only"], failure, platform=platform)
            self.assertNotEqual(result.returncode, 0)
            if failure == "sudo":
                self.assertEqual(len(calls), 1)

    def test_brew_owned_bun_and_deno_do_not_self_upgrade(self):
        result, calls = self.run_mode(["--updates-only"], brew_owned=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(name in {"bun", "deno"} for name, _, _ in calls))
        for tool in ("bun", "deno"):
            self.assertTrue(any(name == "brew" and args == ["list", "--versions", tool]
                                for name, args, _ in calls))

    def test_failed_brew_ownership_probe_never_falls_back_to_self_upgrade(self):
        result, calls = self.run_mode(["--updates-only"], "brew-ownership", brew_owned=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(name in {"bun", "deno"} for name, _, _ in calls))


if __name__ == "__main__":
    unittest.main()
