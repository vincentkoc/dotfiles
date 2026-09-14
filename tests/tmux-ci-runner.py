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
        "LC_ALL": "C",
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
        "import sys; print(sys.executable, sys.version); "
        "sys.exit(0 if sys.version_info >= (3, 9) else 1)"
    )
    probes = [
        ([python, "-B", "-c", python_probe], env),
        ([bash, "--noprofile", "--norc", "-c",
          'echo "bash $BASH_VERSION"; (( BASH_VERSINFO[0] >= 4 ))'], env),
        ([tmux, "-V"], env),
        ([fixture_python, "-B", "-c", python_probe], dict(env, PATH="/usr/bin:/bin")),
        # These two fixture calls deliberately retain the system shell and fake tmux.
        (["/bin/bash", "--noprofile", "--norc", "-c",
          'echo "fixture bash $BASH_VERSION"; test -x /bin/false'],
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


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
    elif sys.argv[1:]:
        sys.exit("usage: tmux-ci-runner.py [--self-test]")
    else:
        sys.exit(main())
