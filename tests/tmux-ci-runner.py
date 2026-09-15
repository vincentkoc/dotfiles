#!/usr/bin/env python3
"""Hosted-only tmux fixtures. Local use is limited to --self-test."""

import contextlib
import io
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
OUTPUT_LIMIT = 2 * 1024 * 1024
TAIL_LIMIT = 16 * 1024
TESTS = (
    ("tt-safety-test.py", 60),
    ("tt-topology-gate-test.sh", 60),
    ("tt-codex-snapshot-live-test.sh", 60),
    ("tt-autosave-timer-test.sh", 240),
)


def require_hosted():
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise RuntimeError("live fixtures refused locally; use --self-test")


def environment(root, python, bash):
    return {
        "PATH": f"{bash.parent}:{python.parent}:/opt/homebrew/bin:/usr/local/bin:{SYSTEM_PATH}",
        "HOME": str(root / "h"),
        "TMPDIR": str(root / "t"),
        "TMUX_TMPDIR": str(root / "s"),
        "SHELL": str(bash),
        "TT_LOGIN_SHELL": "/bin/sh",
        # tmux sanitizes TSV separators for non-UTF-8 clients outside TMUX.
        "LC_ALL": "en_US.UTF-8" if sys.platform == "darwin" else "C.UTF-8",
        "TERM": "xterm-256color",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def terminate(process):
    # Popen creates this process group; never discover or signal detached servers.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            time.sleep(1)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        print("  cleanup incomplete; hosted VM teardown must finish it", flush=True)


def run_test(argv, timeout, env, cwd):
    started = time.monotonic()
    tail, size, failure = b"", 0, None
    process = subprocess.Popen(
        [str(arg) for arg in argv], cwd=cwd, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    failure = "timeout"
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    size += len(chunk)
                    tail = (tail + chunk)[-TAIL_LIMIT:]
                    if size > OUTPUT_LIMIT:
                        failure = "output limit"
                        break
                if failure:
                    break
    except BaseException:
        terminate(process)
        raise
    finally:
        process.stdout.close()
    if failure:
        terminate(process)
    status = process.returncode
    print(f"{Path(argv[-1]).name}: status={status} "
          f"elapsed={time.monotonic() - started:.2f}s bytes={size}"
          f" reason={failure or 'exit'}", flush=True)
    for line in tail.decode("utf-8", errors="replace").splitlines():
        print(f"  | {line}", flush=True)
    return 1 if failure or status != 0 else 0


def dependencies(python, bash, env, cwd):
    for executable in (python, bash):
        if not executable.is_absolute() or not os.access(executable, os.X_OK):
            raise RuntimeError(f"missing absolute executable: {executable}")
    tmux = shutil.which("tmux", path=env["PATH"])
    fixture_python = shutil.which("python3", path="/usr/bin:/bin")
    if not tmux or not fixture_python:
        raise RuntimeError("tmux and fixture PATH=/usr/bin:/bin python3 are required")
    python_probe = (
        "import locale, sys; print(sys.executable, sys.version, locale.nl_langinfo(locale.CODESET)); "
        "sys.exit(0 if sys.version_info >= (3, 9) and "
        "locale.nl_langinfo(locale.CODESET).upper().replace('-', '') == 'UTF8' else 1)"
    )
    probes = [
        ([python, "-B", "-c", python_probe], env),
        ([bash, "--noprofile", "--norc", "-c",
          'echo "bash $BASH_VERSION"; (( BASH_VERSINFO[0] >= 4 ))'], env),
        ([tmux, "-V"], env),
        ([fixture_python, "-B", "-c", python_probe], dict(env, PATH="/usr/bin:/bin")),
        # These two fixture calls deliberately retain the system shell and fake tmux.
        (["/bin/bash", "--noprofile", "--norc", "-c",
          'echo "fixture bash $BASH_VERSION"; test -x /usr/bin/false'],
         dict(env, PATH="/usr/bin:/bin")),
    ]
    for argv, probe_env in probes:
        if run_test(argv, 10, probe_env, cwd):
            raise RuntimeError("dependency preflight failed")


def suite(python, bash, env, cwd):
    for name, timeout in TESTS:
        command = [python, "-B"] if name.endswith(".py") else [bash, "--noprofile", "--norc"]
        if run_test([*command, REPO / "tests" / name], timeout, env, cwd):
            return 1
    return 0


def main():
    try:
        require_hosted()
        if sys.version_info < (3, 9):
            raise RuntimeError("Python >= 3.9 is required")
        bash = Path(os.environ.get("TMUX_CI_BASH", ""))
        python = Path(sys.executable).resolve()
        previous_umask = os.umask(0o077)
        try:
            # Avoid Linux tmpfs and macOS's long per-user temporary paths.
            with tempfile.TemporaryDirectory(prefix="tc", dir=(
                    "/var/tmp" if sys.platform == "linux" else "/tmp")) as temporary:
                root = Path(temporary)
                env = environment(root, python, bash)
                cwd = root / "w"
                for name in ("h", "t", "s", "w"):
                    (root / name).mkdir(mode=0o700)
                if sys.platform == "linux":
                    disk = subprocess.run(
                        ["/usr/bin/stat", "-f", "-c", "%T", str(root)], env=env,
                        cwd=cwd, capture_output=True, text=True, timeout=5, check=True,
                    ).stdout.strip()
                    if not disk or disk in ("tmpfs", "ramfs", "devtmpfs"):
                        raise RuntimeError(f"disk-backed scratch required, got {disk!r}")
                dependencies(python, bash, env, cwd)
                return suite(python, bash, env, cwd)
        finally:
            os.umask(previous_umask)
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"tmux CI refused: {error}", file=sys.stderr)
        return 1


class RunnerTests(unittest.TestCase):
    def test_environment_is_allowlisted(self):
        with mock.patch.dict(os.environ, {
            "TMUX": "operator", "SSH_AUTH_SOCK": "agent", "BASH_ENV": "hook",
            "GITHUB_TOKEN": "secret", "XDG_STATE_HOME": "operator",
            "TT_ISOLATED_FIXTURE": "1", "PATH": "/untrusted",
        }, clear=True):
            env = environment(Path("/fixture"), Path("/tools/python3"), Path("/tools/bash"))
        self.assertEqual(set(env), {
            "PATH", "HOME", "TMPDIR", "TMUX_TMPDIR", "SHELL", "TT_LOGIN_SHELL",
            "LC_ALL", "TERM", "PYTHONDONTWRITEBYTECODE",
        })
        self.assertEqual(env["HOME"], "/fixture/h")
        self.assertNotIn("/untrusted", env["PATH"])

    def test_local_and_self_hosted_refused_before_any_work(self):
        for env in ({}, {"GITHUB_ACTIONS": "true"},
                    {"RUNNER_ENVIRONMENT": "github-hosted"},
                    {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted"}):
            with self.subTest(env=env), mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch("subprocess.Popen") as popen, \
                    mock.patch("tempfile.TemporaryDirectory") as temporary, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(), 1)
                popen.assert_not_called()
                temporary.assert_not_called()

    def test_platform_locale_preserves_tmux_tsv_and_checks_native_dependencies(self):
        for platform, expected in (("linux", "C.UTF-8"), ("darwin", "en_US.UTF-8")):
            with self.subTest(platform=platform), mock.patch.object(sys, "platform", platform):
                env = environment(Path("/fixture"), Path("/tools/python3"), Path("/tools/bash"))
            self.assertEqual(env["LC_ALL"], expected)
            with mock.patch("os.access", return_value=True), \
                    mock.patch("shutil.which", return_value="/usr/bin/tool"), \
                    mock.patch(__name__ + ".run_test", return_value=0) as run:
                dependencies(Path("/tools/python3"), Path("/tools/bash"), env, Path("/fixture"))
            self.assertEqual(run.call_args_list[0].args[1], 10)
            self.assertIn("locale.nl_langinfo(locale.CODESET)", run.call_args_list[0].args[0][-1])
            self.assertIn("test -x /usr/bin/false", run.call_args_list[-1].args[0][-1])
            self.assertTrue(all(call.args[2]["LC_ALL"] == expected for call in run.call_args_list))

    def test_dependency_absence_and_version_failure(self):
        for found, result in ((None, 0), ("/usr/bin/tool", 1)):
            with self.subTest(found=found), mock.patch("os.access", return_value=True), \
                    mock.patch("shutil.which", return_value=found), \
                    mock.patch(__name__ + ".run_test", return_value=result) as run, \
                    self.assertRaises(RuntimeError):
                dependencies(Path("/usr/bin/python3"), Path("/bin/bash"),
                             {"PATH": SYSTEM_PATH}, Path("/fixture"))
            self.assertEqual(run.call_count, 0 if found is None else 1)

    def test_timeout_and_overflow_abort_suite_with_bounded_group_cleanup(self):
        for reason in ("timeout", "overflow"):
            process = mock.Mock(pid=12345, returncode=None)
            process.wait.side_effect = subprocess.TimeoutExpired("fixture", 1)
            selector = mock.MagicMock()
            selector.select.return_value = [(mock.Mock(fileobj=process.stdout), None)]
            clock = [0, 61, 62] if reason == "timeout" else [0, 0, 1]
            with self.subTest(reason=reason), \
                    mock.patch("subprocess.Popen", return_value=process) as popen, \
                    mock.patch("selectors.DefaultSelector") as select, \
                    mock.patch("os.set_blocking"), mock.patch("os.read", return_value=b"x" * 9), \
                    mock.patch("os.killpg") as killpg, mock.patch("time.sleep") as sleep, \
                    mock.patch("time.monotonic", side_effect=clock), \
                    mock.patch(__name__ + ".OUTPUT_LIMIT", 8), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                select.return_value.__enter__.return_value = selector
                self.assertEqual(suite(Path("/python"), Path("/bash"), {}, Path("/fixture")), 1)
            self.assertEqual(popen.call_count, 1)
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertEqual(killpg.call_args_list, [
                mock.call(12345, signal.SIGTERM), mock.call(12345, signal.SIGKILL),
            ])
            sleep.assert_called_once_with(1)
            process.wait.assert_called_once_with(timeout=1)
            process.stdout.close.assert_called_once_with()
            self.assertIn("cleanup incomplete", output.getvalue())

    def test_suite_order_and_failure_stop(self):
        with mock.patch(__name__ + ".run_test", return_value=0) as run:
            self.assertEqual(suite(Path("/python"), Path("/bash"), {}, Path("/fixture")), 0)
        self.assertEqual([call.args[0][-1].name for call in run.call_args_list],
                         [name for name, _ in TESTS])
        self.assertEqual([call.args[1] for call in run.call_args_list], [60, 60, 60, 240])
        with mock.patch(__name__ + ".run_test", return_value=1) as run:
            self.assertEqual(suite(Path("/python"), Path("/bash"), {}, Path("/fixture")), 1)
        run.assert_called_once()


class StatusDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Extract definitions only: sourcing the fixture would start real tmux servers.
        source = (REPO / "tests/tt-autosave-timer-test.sh").read_text()
        functions = []
        for name in ("status_diagnostics", "failure_diagnostics", "wait_until", "status_has"):
            start = name + "() {\n"
            if source.count(start) != 1:
                raise AssertionError(f"expected one fixture function: {name}")
            offset = source.index(start)
            end = source.index("\n}\n", offset) + 3
            functions.append(source[offset:end])
        cls.functions = "\n".join(functions)
        cls.bash = shutil.which("bash")
        if not cls.bash:
            raise RuntimeError("bash is required for mocked status diagnostics")

    def run_helpers(self, body, values=None):
        script = "set -Eeuo pipefail\n" + self.functions + "\n"
        script += 'trap \'failure_diagnostics "$?" "$LINENO" "$BASH_COMMAND"\' ERR\n'
        script += body
        env = {"PATH": SYSTEM_PATH, "HOME": "/fixture", "LC_ALL": "C",
               "CASE_STATE": "/fixture/state"}
        env.update(values or {})
        result = subprocess.run(
            [self.bash, "--noprofile", "--norc", "-c", script], env=env,
            stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
        )
        return result, script

    def test_matching_status_is_quiet(self):
        result, _ = self.run_helpers('case_tt() { printf "timer: running\\n"; }\n'
                                     'status_has "timer: running"\n')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))

    def test_direct_mismatch_retains_actual_and_caller(self):
        result, script = self.run_helpers('''case_tt() { printf 'timer: degraded\\n'; }
direct_scenario() {
  status_has running
}
direct_scenario
''')
        lines = script.splitlines()
        callers = (f"direct_scenario:{lines.index('  status_has running') + 1}\\ "
                   f"main:{lines.index('direct_scenario') + 1}\\ main:0")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertIn(f"rc=0 expected=running callers={callers}\n".encode(), result.stderr)
        self.assertIn(b"last_status_actual bytes=15 shown=15\ntimer: degraded\n", result.stderr)
        self.assertEqual(result.stderr.count(b"last_status_observation"), 1)

    def test_command_failure_retains_original_rc_and_combined_output(self):
        result, _ = self.run_helpers('''case_tt() {
  printf 'status output\\n'
  printf 'status error\\n' >&2
  return 7
}
status_has running
''')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"last_status_observation rc=7 expected=running", result.stderr)
        self.assertIn(b"status output\nstatus error\n", result.stderr)
        self.assertNotIn(b"tt status failed unexpectedly", result.stderr)
        self.assertEqual(result.stderr.count(b"status error"), 1)

    def test_transient_errors_and_mismatches_stay_quiet_when_polling_recovers(self):
        result, _ = self.run_helpers('''status_attempt=0
sleep() { status_attempt=$((status_attempt + 1)); }
case_tt() {
  case "$status_attempt" in
    0) printf unavailable; return 7 ;;
    1) printf degraded ;;
    *) printf running ;;
  esac
}
wait_until 3 status_has running
printf 'attempt=%s\\n' "$status_attempt"
''')
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (0, b"attempt=2\n", b""))

    def test_polling_exhaustion_retains_last_observation_and_outer_caller(self):
        result, script = self.run_helpers('''status_attempt=0
sleep() { status_attempt=$((status_attempt + 1)); }
case_tt() { printf 'degraded-%s' "$status_attempt"; }
polling_scenario() {
  wait_until 3 status_has running
}
polling_scenario
''')
        lines = script.splitlines()
        poll_line = lines.index('    if "$@"; then') + 1
        callers = (f"wait_until:{poll_line}\\ "
                   f"polling_scenario:{lines.index('  wait_until 3 status_has running') + 1}\\ "
                   f"main:{lines.index('polling_scenario') + 1}")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertIn(f"rc=0 expected=running callers={callers}\n".encode(), result.stderr)
        self.assertIn(b"last_status_actual bytes=10 shown=10\ndegraded-2\n", result.stderr)
        self.assertNotIn(b"degraded-0", result.stderr)
        self.assertNotIn(b"degraded-1", result.stderr)
        self.assertEqual(result.stderr.count(b"last_status_observation"), 1)

    def test_diagnostic_fields_are_byte_bounded(self):
        name = "scenario_" + "s" * 300
        actual = "\u03b1" * 3000 + "unprinted_actual"
        result, _ = self.run_helpers(
            'case_tt() { printf "%s" "$FAKE_STATUS"; }\n'
            f'{name}() {{ status_has "$FAKE_EXPECTED"; }}\n{name}\n',
            {"FAKE_STATUS": actual, "FAKE_EXPECTED": "E" * 300 + "unprinted_expected"},
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(("expected=" + "E" * 256 + " callers=" + name[:256] + "\n").encode(),
                      result.stderr)
        header = f"last_status_actual bytes={len(actual.encode())} shown=4096\n".encode()
        self.assertEqual(result.stderr.split(header)[1], actual.encode()[:4096] + b"\n")
        self.assertNotIn(b"unprinted_", result.stderr)
        self.assertLess(len(result.stderr), 6500)

    def test_status_matching_uses_full_capture_not_printed_prefix(self):
        result, _ = self.run_helpers('case_tt() { printf "%s" "$FAKE_STATUS"; }\n'
                                     'status_has final-marker\n',
                                     {"FAKE_STATUS": "x" * 5000 + "final-marker"})
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))

    def test_recent_events_and_status_survive_the_hosted_output_tail(self):
        with tempfile.TemporaryDirectory(prefix="tt-status-test-") as temporary:
            events = Path(temporary) / "events"
            events.write_text("oldest-event\n" + "".join(
                f"event-{index:04d} " + "x" * 80 + "\n" for index in range(1000)
            ) + "latest-event\n")
            result, _ = self.run_helpers('''case_tt() { printf degraded; }
fake_tmux() { printf '%20000s\\n' process-rows; }
tmux_bin=fake_tmux
CASE_SOCKET=fixture
status_has running
''', {"CASE_EVENTS": str(events)})
        self.assertEqual(result.returncode, 1)
        self.assertGreater(len(result.stderr), TAIL_LIMIT)
        retained = result.stderr[-TAIL_LIMIT:]
        self.assertIn(b"latest-event\n", retained)
        self.assertIn(b"last_status_observation rc=0 expected=running", retained)
        self.assertIn(b"last_status_actual bytes=8 shown=8\ndegraded\n", retained)
        self.assertNotIn(b"oldest-event", result.stderr)
        recent = retained.split(b"== recent events (last 8192 bytes, at most 80 lines) ==\n")[1]
        recent = recent.split(b"last_status_observation")[0]
        self.assertLessEqual(len(recent), 8192)
        self.assertLessEqual(len(recent.splitlines()), 80)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
    elif sys.argv[1:]:
        sys.exit("usage: tmux-ci-runner.py [--self-test]")
    else:
        sys.exit(main())
