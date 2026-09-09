#!/usr/bin/env python3
"""Real tmux regression; run only inside the approved isolated test runner."""

import os
import errno
import importlib.machinery
import importlib.util
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKER = r"""#!/bin/bash
set -euo pipefail
set -m
requests="$1"
completed="$2"
release="$3"
exec 3<>"$requests"
printf 'ready\n' >> "$completed"
deadline=$((SECONDS + 45))
while ((SECONDS < deadline)); do
  if IFS= read -r -t 0.2 action <&3; then
    case "$action" in
      waiting|approve|reset)
        /bin/bash "$ATTENTION_SCRIPT" "$action" codex
        printf '%s\n' "$action" >> "$completed"
        ;;
      foreground)
        cat "$release"
        printf '%s\n' "$action" >> "$completed"
        ;;
      exit) exit 0 ;;
      *) exit 2 ;;
    esac
  fi
done
"""


@unittest.skipUnless(
    os.environ.get("TMUX_TEST_ALLOW_FIXTURE_SERVER") == "1",
    "requires an approved isolated runner with TMUX_TEST_ALLOW_FIXTURE_SERVER=1",
)
class TmuxFixture(unittest.TestCase):
    def setUp(self):
        for name in ("TMUX", "TMUX_PANE", "SSH_AUTH_SOCK"):
            self.assertFalse(os.environ.get(name), f"inherited {name} is forbidden")
        self.tmux_binary = shutil.which("tmux")
        self.assertIsNotNone(self.tmux_binary)
        self.temporary = tempfile.TemporaryDirectory(prefix="attn-tmux-")
        self.root = Path(self.temporary.name)
        self.socket = self.root / "socket"
        self.workers = []
        self.started = False
        self.server_identity = None
        self.addCleanup(self.cleanup_server)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.root / "worker").write_text(WORKER)
        # Only hook-originated client calls enter this log; the server is real.
        wrapper = self.bin / "tmux"
        wrapper.write_text(
            "#!/bin/bash\nprintf '%s\\n' \"$*\" >> \"$ATTENTION_LOG\"\nexec "
            + shlex.quote(self.tmux_binary) + ' "$@"\n'
        )
        wrapper.chmod(0o700)
        self.env = {
            "HOME": str(self.root), "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_STATE_HOME": str(self.root / "state"), "TMPDIR": str(self.root),
            "PATH": f"{self.bin}:/usr/bin:/bin", "SHELL": "/bin/bash",
            "TERM": "xterm-256color", "LC_ALL": "C",
            "ATTENTION_SCRIPT": str(ROOT / "bin/agent-attn-set"),
            "ATTENTION_LOG": str(self.root / "calls"),
        }
        self.owner = self.start_worker("owner")
        self.other = self.start_worker("other", self.owner["pane"])
        self.tmux("set-window-option", "-g", "window-style", "fg=default,bg=#1a1b26")
        self.tmux("set-window-option", "-g", "window-active-style", "fg=default,bg=#1a1b26")
        self.tmux("select-pane", "-t", self.owner["pane"], "-T", "owner title")
        self.tmux("select-pane", "-t", self.other["pane"], "-T", "unrelated title")
        self.tmux("set-option", "-pt", self.other["pane"], "@tt_marker", "green")
        self.tmux("set-option", "-pt", self.other["pane"], "window-style", "bg=red")
        self.tmux("set-option", "-pt", self.other["pane"], "window-active-style", "bg=blue")
        self.other_before = self.pane_state(self.other["pane"])
        self.owner_before = self.pane_state(self.owner["pane"])

    def tmux(self, *args, starting=False):
        command = [self.tmux_binary, "-S", str(self.socket), "-f", "/dev/null"]
        if not starting:
            command.append("-N")
        return subprocess.run(
            command + list(args), env=self.env, text=True,
            capture_output=True, check=True, timeout=5,
        ).stdout.rstrip("\n")

    def start_worker(self, name, target=None):
        requests = self.root / f"{name}.requests"
        completed = self.root / f"{name}.completed"
        release = self.root / f"{name}.release"
        os.mkfifo(requests, 0o600)
        os.mkfifo(release, 0o600)
        worker = {"requests": requests, "completed": completed, "release": release}
        self.workers.append(worker)
        args = ["new-session", "-d", "-s", "fixture", "-P", "-F", "#{pane_id}"]
        if target is not None:
            args = ["split-window", "-d", "-t", target, "-P", "-F", "#{pane_id}"]
        else:
            self.started = True
        worker["pane"] = self.tmux(
            *args, "/bin/bash", str(self.root / "worker"), str(requests), str(completed), str(release),
            starting=target is None,
        )
        self.wait_for(lambda: completed.exists() and "ready" in completed.read_text())
        server_pid, pane_pid = self.tmux(
            "display-message", "-p", "-t", worker["pane"], "#{pid}|#{pane_pid}"
        ).split("|")
        worker["identity"] = self.process_info(int(pane_pid))
        self.assertIsNotNone(worker["identity"])
        if self.server_identity is None:
            self.server_identity = self.process_info(int(server_pid))
            self.assertIsNotNone(self.server_identity)
        return worker

    @staticmethod
    def process_info(pid):
        try:
            text = Path(f"/proc/{pid}/stat").read_text()
        except (FileNotFoundError, ProcessLookupError):
            return None
        fields = text.rsplit(")", 1)[1].split()
        return {
            "pid": pid, "start": fields[19], "state": fields[0],
            "ppid": int(fields[1]), "pgid": int(fields[2]),
            "tty": int(fields[4]), "foreground": int(fields[5]),
            "command": text.split("(", 1)[1].rsplit(")", 1)[0],
        }

    def process_stopped(self, identity):
        current = self.process_info(identity["pid"])
        return current is None or current["start"] != identity["start"] or current["state"] == "Z"

    def socket_accepts_connections(self):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(0.2)
            try:
                connection.connect(str(self.socket))
            except OSError as error:
                if error.errno in (errno.ENOENT, errno.ECONNREFUSED):
                    return False
                raise
        return True

    def release_foreground(self, worker):
        try:
            fd = os.open(worker["release"], os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            if error.errno == errno.ENXIO:
                return
            raise
        try:
            os.write(fd, b"release\n")
        except BrokenPipeError:
            pass
        finally:
            os.close(fd)

    def resources_stopped(self):
        # Retry release if teardown raced with the foreground child's FIFO open.
        for worker in self.workers:
            self.release_foreground(worker)
        if self.server_identity is None:
            return not self.socket_accepts_connections()
        return (
            all(self.process_stopped(worker["identity"])
                for worker in self.workers if "identity" in worker)
            and self.process_stopped(self.server_identity)
            and not self.socket_accepts_connections()
        )

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail("fixture did not reach the expected state")

    def send(self, worker, action):
        # O_RDWR avoids blocking if setup failed before the shell opened its FIFO.
        fd = os.open(worker["requests"], os.O_RDWR | os.O_NONBLOCK)
        try:
            os.write(fd, (action + "\n").encode())
        finally:
            os.close(fd)

    def invoke(self, action):
        before = len(self.owner["completed"].read_text().splitlines())
        self.send(self.owner, action)
        self.wait_for(
            lambda: len(self.owner["completed"].read_text().splitlines()) == before + 1
        )
        self.assertEqual(self.owner["completed"].read_text().splitlines()[-1], action)

    def option(self, pane, option):
        return self.tmux("show-option", "-pt", pane, "-qv", option)

    def pane_state(self, pane):
        fields = ("pane_id", "pane_pid", "pane_title", "@tt_marker", "window-style",
                  "window-active-style", "@agent_attention_state")
        return self.tmux(
            "display-message", "-p", "-t", pane,
            "|".join("#{" + field + "}" for field in fields),
        )

    def mutations(self):
        log = self.root / "calls"
        return [
            line for line in log.read_text().splitlines() if " if-shell " in line
        ] if log.exists() else []

    def cleanup_server(self):
        try:
            for worker in self.workers:
                if worker["requests"].exists():
                    self.send(worker, "exit")
            if self.started:
                # A residual socket pathname is not a live server. Require both
                # the pinned processes to have stopped and the endpoint to refuse.
                self.wait_for(self.resources_stopped, timeout=50)
        finally:
            self.temporary.cleanup()


class LiveAttentionTests(TmuxFixture):
    def test_real_style_guards_and_inheritance(self):
        pane = self.owner["pane"]
        self.assertEqual(self.option(pane, "window-style"), "")
        self.assertEqual(self.option(pane, "window-active-style"), "")
        self.invoke("waiting")
        self.assertEqual(self.option(pane, "window-style"), "fg=default,bg=#241d12")
        self.assertEqual(self.option(pane, "window-active-style"), "fg=default,bg=#2f2517")
        self.assertEqual(self.option(pane, "@agent_attention_state"), "waiting")
        applied = len(self.mutations())
        self.assertEqual(applied, 1)

        self.invoke("waiting")
        self.assertEqual(len(self.mutations()), applied, "unchanged state rewrote options")
        self.invoke("approve")
        self.assertEqual(self.option(pane, "window-style"), "fg=default,bg=#2b1720")
        self.assertEqual(self.option(pane, "@agent_attention_state"), "approve")
        self.invoke("reset")
        self.assertEqual(self.option(pane, "window-style"), "")
        self.assertEqual(self.option(pane, "window-active-style"), "")
        self.assertEqual(self.option(pane, "@agent_attention_state"), "")
        self.assertEqual(self.pane_state(pane), self.owner_before)
        self.assertEqual(self.pane_state(self.other["pane"]), self.other_before)

        # A newly customized local pane is not mistaken for inherited styling.
        self.tmux("set-option", "-pt", pane, "window-style", "bg=yellow")
        custom = self.pane_state(pane)
        applied = len(self.mutations())
        self.invoke("waiting")
        self.assertEqual(len(self.mutations()), applied)
        self.assertEqual(self.pane_state(pane), custom)
        self.assertEqual(self.pane_state(self.other["pane"]), self.other_before)

        # A coincidental attention palette, including an inherited state option,
        # is not pane-local ownership.
        self.tmux("set-option", "-g", "@agent_attention_state", "waiting")
        self.tmux("set-option", "-pt", pane, "window-style", "fg=default,bg=#241d12")
        self.tmux("set-option", "-pt", pane, "window-active-style", "fg=default,bg=#2f2517")
        self.assertEqual(self.option(pane, "@agent_attention_state"), "")
        custom = self.pane_state(pane)
        applied = len(self.mutations())
        self.invoke("approve")
        self.invoke("reset")
        self.assertEqual(len(self.mutations()), applied)
        self.assertEqual(self.pane_state(pane), custom)

    def test_repair_guard_rejects_new_foreground_with_same_pane_pid(self):
        loader = importlib.machinery.SourceFileLoader(
            "repair", str(ROOT / "bin/tmux-pane-repair")
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        repair = importlib.util.module_from_spec(spec)
        loader.exec_module(repair)
        pane = self.owner["pane"]
        values = self.tmux(
            "display-message", "-p", "-t", pane,
            "#{pid}|#{pane_pid}|#{session_id}|#{window_id}",
        ).split("|")
        target = {
            "server": {"pid": int(values[0])}, "pane": pane,
            "process": {"pid": int(values[1]), "command": "bash"},
            "session": values[2], "window": values[3],
        }
        guard = repair.final_guard(target)
        self.assertEqual(self.tmux("display-message", "-p", "-t", pane, guard), "1")
        shell = self.owner["identity"]
        self.assertNotEqual(shell["tty"], 0)
        self.assertEqual(shell["foreground"], shell["pgid"])
        self.send(self.owner, "foreground")
        foreground = {}

        def child_owns_terminal():
            current = self.process_info(shell["pid"])
            if (current is None or current["start"] != shell["start"]
                    or current["foreground"] == shell["pgid"]):
                return False
            child = self.process_info(current["foreground"])
            if (child is None or child["ppid"] != shell["pid"]
                    or child["pgid"] != child["pid"] or child["tty"] != shell["tty"]
                    or child["command"] != "cat"):
                return False
            foreground.update(child)
            return True

        self.wait_for(child_owns_terminal)
        self.assertEqual(
            self.tmux("display-message", "-p", "-t", pane, "#{pane_current_command}"), "cat"
        )
        self.assertEqual(self.tmux("display-message", "-p", "-t", pane, "#{pane_pid}"), values[1])
        self.tmux(
            "if-shell", "-F", "-t", pane, guard,
            f"set-option -pt {pane} @repair_guard_allowed yes",
        )
        self.assertEqual(self.option(pane, "@repair_guard_allowed"), "")
        def foreground_stopped():
            self.release_foreground(self.owner)
            return self.process_stopped(foreground)

        self.wait_for(foreground_stopped)


if __name__ == "__main__":
    unittest.main()
