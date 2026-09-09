#!/usr/bin/env python3
"""Real tmux recovery fixtures. Run only inside the reviewed isolated runner.

TT_TEST_DISK_TMPDIR must name the runner's private writable disk-backed bind.
No real agent binary, user configuration, credentials, or default socket is used.
"""

import ctypes
import errno
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
THIS = pathlib.Path(__file__).resolve()
SID = "11111111-1111-1111-1111-111111111111"
OTHER_SID = "22222222-2222-2222-2222-222222222222"


def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def process_info(pid):
    try:
        fields = pathlib.Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return {"pid": pid, "start": fields[19], "state": fields[0]}


def process_stopped(identity):
    current = process_info(identity["pid"])
    return current is None or current["start"] != identity["start"] or current["state"] == "Z"


def synthetic_agent(kind, args):
    root = pathlib.Path(os.environ["TT_FIXTURE_ROOT"])
    session_id = ""
    handle = None
    if kind == "codex":
        if len(args) != 3 or args[:2] != ["resume", "--no-alt-screen"]:
            raise RuntimeError(f"unexpected synthetic agent arguments: {args}")
        session_id = args[-1]
        if session_id not in (SID, OTHER_SID):
            raise RuntimeError("not a synthetic session UUID")
        rollout = pathlib.Path(os.environ["CODEX_HOME"]) / "sessions" / f"{session_id}.jsonl"
        handle = rollout.open("a+", encoding="utf-8")
        if rollout.stat().st_size == 0:
            handle.write(json.dumps({"type": "session_meta", "payload": {
                "id": session_id, "source": "cli",
            }}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    name = b"codex" if kind in ("codex", "unresolved") else b"fixture-shell"
    if ctypes.CDLL(None).prctl(15, ctypes.c_char_p(name), 0, 0, 0) != 0:
        raise RuntimeError("could not set the fixture process name")
    pane = os.environ["TMUX_PANE"]
    event = {"pane": pane, "pid": os.getpid(), "kind": kind, "sid": session_id,
             "args": args, "cwd": os.getcwd(), "tmpdir": os.environ.get("TMPDIR"),
             "tempdir": tempfile.gettempdir(), "term": os.environ.get("TERM"),
             "process": process_info(os.getpid())}
    pending = root / "events" / f".{os.getpid()}.json"
    pending.write_text(json.dumps(event))
    pending.replace(root / "events" / f"{os.getpid()}.json")
    deadline = time.monotonic() + 120
    while (
        time.monotonic() < deadline
        and not (root / "stop").exists()
        and not (root / "stops" / pane).exists()
        and not (session_id and (root / "stops" / session_id).exists())
    ):
        time.sleep(0.05)
    if handle:
        handle.close()


def before_autosave():
    root = pathlib.Path(os.environ["TT_FIXTURE_ROOT"])
    marker = root / "autosave-checked.json"
    if marker.exists():
        return
    source = pathlib.Path(os.environ["TT_FIXTURE_SOURCE"])
    if digest(source) != os.environ["TT_FIXTURE_SOURCE_SHA"]:
        raise RuntimeError("mutable recovery source changed before first autosave")
    frozen = list((pathlib.Path(os.environ["XDG_STATE_HOME"]) / "tt/recovery-inputs").glob("*.tsv"))
    if len(frozen) != 1:
        raise RuntimeError("expected exactly one frozen recovery source")
    proof = json.loads(frozen[0].with_suffix(".completed.json").read_text())
    if len(proof["panes"]) != 30:
        raise RuntimeError("autosave started before the cold cockpit was complete")
    text = frozen[0].read_text()
    if SID not in text or OTHER_SID not in text:
        raise RuntimeError("frozen source lost an exact UUID")
    events = [json.loads(path.read_text()) for path in (root / "events").glob("*.json")]
    if {event["sid"] for event in events if event["kind"] == "codex"} != {SID, OTHER_SID}:
        raise RuntimeError("autosave started before both synthetic sessions launched")
    marker.write_text(json.dumps({"frozen": str(frozen[0]), "sha256": digest(frozen[0])}))


class PrivateTmux:
    def __init__(self):
        disk = os.environ.get("TT_TEST_DISK_TMPDIR")
        if not disk:
            raise RuntimeError("set TT_TEST_DISK_TMPDIR to the runner's private disk-backed bind")
        self.temporary = tempfile.TemporaryDirectory(prefix="tt-restore-", dir=disk)
        self.root = pathlib.Path(self.temporary.name)
        self.sockets = tempfile.TemporaryDirectory(prefix="tt-rsock-", dir="/tmp")
        self.socket = pathlib.Path(self.sockets.name) / "socket"
        self.server_identity = None
        self.pane_identities = []
        self.real_tmux = shutil.which("tmux")
        if not self.real_tmux or not shutil.which("lsof"):
            raise RuntimeError("real tmux and lsof are required; this integration must not skip")
        for directory in ("home", "config", "state", "events", "stops", "bin", "work with spaces",
                          "codex/sessions"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.work = self.root / "work with spaces"
        self.bin = self.root / "bin"
        self.env = {
            "PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.root / "home"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_STATE_HOME": str(self.root / "state"), "CODEX_HOME": str(self.root / "codex"),
            "SHELL": "/bin/sh", "TERM": "xterm-256color", "LC_ALL": "C.UTF-8",
            "TT_LOGIN_SHELL": str(self.bin / "fixture-shell"),
            "TT_TMUX_BIN": str(self.bin / "tmux"),
            "TT_REAL_TMUX": self.real_tmux, "TT_FIXTURE_SOCKET": str(self.socket),
            "TT_FIXTURE_ROOT": str(self.root), "TT_FIXTURE_LOG": str(self.root / "tmux.log"),
            "TT_REAL_WRITER": str(self.bin / "tt-codex-snapshot-writer.real"),
            "TT_PYTHON": sys.executable, "TT_TEST_HELPER": str(THIS),
            "TT_CODEX_SNAPSHOT_INTERVAL": "1", "TT_CODEX_SNAPSHOT_INTERVAL_MIN": "1",
            "TT_SNAPSHOT_TOTAL_TIMEOUT_SECONDS": "30", "TT_SNAPSHOT_HISTORY_MAX": "4",
        }
        filesystem = subprocess.run(
            ["stat", "-f", "-c", "%T", str(self.root)], env=self.env,
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
        if filesystem in ("tmpfs", "ramfs"):
            raise RuntimeError("TT_TEST_DISK_TMPDIR is RAM-backed; refusing to bypass the freeze guard")
        self.script("tmux", """#!/bin/sh
printf '%s\\n' "$*" >> "$TT_FIXTURE_LOG"
case "$1" in attach|switch-client) exit 0 ;; esac
case "$1" in -f) exec "$TT_REAL_TMUX" -S "$TT_FIXTURE_SOCKET" "$@" ;; esac
exec "$TT_REAL_TMUX" -S "$TT_FIXTURE_SOCKET" -f /dev/null "$@"
""")
        self.script("tt-codex-snapshot-writer", """#!/bin/sh
if [ "$1" = "--autosave" ] && [ "${TT_FIXTURE_CHECK_AUTOSAVE:-}" = 1 ]; then
  "$TT_PYTHON" "$TT_TEST_HELPER" --before-autosave || exit 1
fi
exec "$TT_PYTHON" "$TT_REAL_WRITER" "$@"
""")
        for name, kind in (("codex", "codex"), ("fixture-shell", "shell"),
                           ("unresolved-agent", "unresolved")):
            self.script(name, "#!/bin/sh\nexec " + shlex.join(
                [sys.executable, str(THIS), "--agent", kind]
            ) + ' "$@"\n')
        shutil.copy2(REPO / "bin/tt", self.bin / "tt")
        shutil.copy2(REPO / "bin/tt-codex-snapshot-writer",
                     self.bin / "tt-codex-snapshot-writer.real")

    def script(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o700)

    def run(self, args, *, ok=True, timeout=15):
        try:
            result = subprocess.run(
                [str(arg) for arg in args], env=self.env, cwd=self.work,
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
            )
        finally:
            if self.server_identity is None:
                self.pin_server()
        if ok and result.returncode:
            raise AssertionError(f"{args}: {result.returncode}\n{result.stdout}\n{result.stderr}")
        return result

    def pin_server(self):
        result = subprocess.run(
            [self.real_tmux, "-N", "-S", str(self.socket), "-f", "/dev/null",
             "display-message", "-p", "#{pid}"],
            env=self.env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=2,
        )
        if result.returncode:
            return None
        current = process_info(int(result.stdout.strip()))
        if current is None:
            raise AssertionError("fixture server exited before its identity could be pinned")
        if self.server_identity is not None and any(
            current[key] != self.server_identity[key] for key in ("pid", "start")
        ):
            raise AssertionError("fixture server identity changed")
        self.server_identity = current
        return current

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

    def resources_stopped(self):
        if self.server_identity is not None and not process_stopped(self.server_identity):
            return False
        if self.socket_accepts_connections():
            return False
        identities = self.pane_identities + [event["process"] for event in self.events()]
        return all(process_stopped(identity) for identity in identities)

    def tmux(self, *args, ok=True):
        return self.run([self.bin / "tmux", *args], ok=ok).stdout.strip()

    def tt(self, *args, ok=True, timeout=45):
        return self.run([self.bin / "tt", *args], ok=ok, timeout=timeout)

    def writer(self, *args, ok=True):
        return self.run([self.bin / "tt-codex-snapshot-writer", *args], ok=ok, timeout=35)

    def events(self, kind=None):
        events = [json.loads(path.read_text()) for path in (self.root / "events").glob("*.json")]
        return [event for event in events if kind is None or event["kind"] == kind]

    def wait(self, predicate, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError("fixture did not reach its expected state within the deadline")

    def start(self, name="anchor", session_id=None, unresolved=False):
        program = [self.bin / "fixture-shell"]
        if unresolved:
            program = [self.bin / "unresolved-agent"]
        elif session_id:
            program = [self.bin / "codex", "resume", "--no-alt-screen", session_id]
        pane = self.tmux("new-session", "-d", "-x", "240", "-y", "80", "-P", "-F",
                         "#{pane_id}", "-s", name, "-c", self.work, *program)
        self.wait(lambda: any(event["pane"] == pane for event in self.events()))
        return pane

    def observe(self, session=None):
        panes = ["-s", "-t", session] if session else ["-a"]
        windows = ["-t", session] if session else ["-a"]
        return (
            self.tmux("list-panes", *panes, "-F",
                      "#{session_id}|#{window_id}|#{pane_id}|#{pane_pid}|#{pane_dead}|"
                      "#{pane_index}|#{pane_active}|#{pane_current_path}|#{pane_title}"),
            self.tmux("list-windows", *windows, "-F",
                      "#{session_id}|#{window_id}|#{window_index}|#{window_layout}|#{window_active}"),
            self.tmux("show-options", "-g"),
            self.tmux("show-hooks", "-g"),
        )

    def close(self):
        if self.pin_server() is not None:
            self.tt("autosave", "off", ok=False, timeout=15)
            panes = self.tmux("list-panes", "-a", "-F",
                              "#{pane_id}\t#{pane_dead}\t#{window_id}\t#{pane_pid}", ok=False)
            for row in panes.splitlines():
                pane, dead, window, pid = row.split("\t")
                if not re.fullmatch(r"%\d+", pane) or not re.fullmatch(r"@\d+", window):
                    raise AssertionError("invalid cleanup pane identity")
                if dead == "0":
                    identity = process_info(int(pid))
                    if identity is not None:
                        self.pane_identities.append(identity)
                self.tmux("set-window-option", "-t", window, "remain-on-exit", "off", ok=False)
                if dead == "1":
                    self.tmux("respawn-pane", "-t", pane, "/bin/true", ok=False)
        (self.root / "stop").touch()
        # tmux can leave a stale socket pathname after exiting. Prove that the
        # pinned server and fixture processes stopped and the endpoint refuses.
        self.wait(self.resources_stopped, seconds=15)
        self.sockets.cleanup()
        self.temporary.cleanup()


class RecoveryIntegration(unittest.TestCase):
    def fixture(self):
        fixture = PrivateTmux()
        self.addCleanup(fixture.close)
        return fixture

    def snapshot(self, fixture, pane):
        path = fixture.root / "saved.tsv"
        fixture.writer(path)
        text = path.read_text()
        target = fixture.tmux("display-message", "-p", "-t", pane,
                              "#{session_name}:#{window_index}.#{pane_index}")
        row = next(line.split("\t") for line in text.splitlines() if line.startswith(target + "\t"))
        self.assertEqual(row[1], "codex")
        self.assertEqual(row[5:7], [SID, "exact"])
        return path, target

    def assert_startup_scratch(self, fixture, events):
        scratch = fixture.root / "home/.cache/cockpit-tmp"
        self.assertEqual(scratch.stat().st_mode & 0o777, 0o700)
        self.assertTrue(events)
        for event in events:
            self.assertEqual(event["tmpdir"], str(scratch))
            self.assertEqual(event["tempdir"], str(scratch))

    def alter(self, source, target, *, session_id=None, stale=False):
        output = source.with_name("altered-" + str(time.monotonic_ns()) + ".tsv")
        lines = []
        for line in source.read_text().splitlines():
            if stale and line.startswith("# restore-identity\t"):
                identity = json.loads(line.split("\t", 1)[1])
                identity["panes"][target]["pane"] = "%999999"
                line = "# restore-identity\t" + json.dumps(identity)
            elif line.startswith(target + "\t"):
                fields = line.split("\t")
                if session_id:
                    fields[5] = session_id
                fields[7] = f"touch {shlex.quote(str(source.parent / 'UNSAFE'))}"
                line = "\t".join(fields)
            lines.append(line)
        output.write_text("\n".join(lines) + "\n")
        return output

    def test_targeted_restore_real_dead_live_wrong_server_and_stale_identity(self):
        fixture = self.fixture()
        fixture.start()
        pane = fixture.start("worker", SID)
        initial_agent_pid = fixture.events("codex")[0]["pid"]
        source, target = self.snapshot(fixture, pane)
        original = digest(source)
        before = fixture.observe()
        live_source = self.alter(source, target, session_id=OTHER_SID)
        refusal = fixture.tt("codex-restore", "pane", target, live_source, "--execute", ok=False)
        self.assertNotEqual(refusal.returncode, 0)
        self.assertIn("refusing live occupant", refusal.stderr)
        self.assertEqual(fixture.observe(), before)

        stale = fixture.tt("codex-restore", "pane", target,
                           self.alter(source, target, stale=True), "--execute", ok=False)
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("pane identity changed", stale.stderr)
        self.assertEqual(fixture.observe(), before)

        other = self.fixture()
        other.start("worker")
        other_before = other.observe()
        wrong = other.tt("codex-restore", "pane", target, source, "--execute", ok=False)
        self.assertNotEqual(wrong.returncode, 0)
        self.assertIn("different host, user, socket or server", wrong.stderr)
        self.assertEqual(other.observe(), other_before)
        self.assertEqual(fixture.observe(), before)

        window = fixture.tmux("display-message", "-p", "-t", pane, "#{window_id}")
        fixture.tmux("set-window-option", "-t", window, "remain-on-exit", "on")
        gate = fixture.root / "stops" / SID
        gate.touch()
        fixture.wait(lambda: fixture.tmux("display-message", "-p", "-t", pane, "#{pane_dead}") == "1")
        gate.unlink()
        unresolved = fixture.start("ambiguous", unresolved=True)
        ambiguous_before = fixture.observe()
        refused = fixture.tt("codex-restore", "pane", target, source, "--execute", ok=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("live agent identities are unresolved", refused.stderr)
        self.assertEqual(fixture.observe(), ambiguous_before)
        (fixture.root / "stops" / unresolved).touch()
        fixture.wait(lambda: "ambiguous" not in
                     fixture.tmux("list-sessions", "-F", "#{session_name}").splitlines())
        anchor = fixture.observe("anchor")
        layout = fixture.tmux("list-windows", "-a", "-F", "#{window_id}|#{window_layout}")
        old_pid = fixture.tmux("display-message", "-p", "-t", pane, "#{pane_pid}")
        fixture.tt("codex-restore", "pane", target, self.alter(source, target), "--execute")
        fixture.wait(lambda: len([event for event in fixture.events("codex") if event["pane"] == pane]) == 2)
        self.assertEqual(fixture.tmux("display-message", "-p", "-t", pane, "#{pane_dead}"), "0")
        self.assertNotEqual(fixture.tmux("display-message", "-p", "-t", pane, "#{pane_pid}"), old_pid)
        self.assertEqual(fixture.observe("anchor"), anchor)
        self.assertEqual(fixture.tmux("list-windows", "-a", "-F", "#{window_id}|#{window_layout}"), layout)
        self.assertFalse((fixture.root / "UNSAFE").exists())
        self.assertEqual(digest(source), original)
        for event in fixture.events("codex"):
            self.assertEqual(event["args"], ["resume", "--no-alt-screen", SID])
        self.assert_startup_scratch(fixture, [
            event for event in fixture.events("codex") if event["pid"] != initial_agent_pid
        ])

    def test_cold_cockpit_public_entry_preserves_source_until_autosave(self):
        fixture = self.fixture()
        source = fixture.root / "state/tt/codex-cockpit.tsv"
        source.parent.mkdir(parents=True)
        source.write_text(
            f"cockpit:1.1\tcodex\t{fixture.work}\tfirst\tcodex\t{SID}\texact\tignored command\n"
            f"cockpit:5.6\tcodex\t{fixture.work}\tlast\tcodex\t{OTHER_SID}\texact\tignored command\n"
        )
        fixture.env.update({
            "TT_FIXTURE_CHECK_AUTOSAVE": "1", "TT_FIXTURE_SOURCE": str(source),
            "TT_FIXTURE_SOURCE_SHA": digest(source),
        })
        fixture.tt("recover", "cockpit", source, timeout=75)
        checked = json.loads((fixture.root / "autosave-checked.json").read_text())
        self.assertEqual(digest(checked["frozen"]), checked["sha256"])
        self.assertEqual(fixture.tmux("list-sessions", "-F", "#{session_name}"), "cockpit")
        pane_map = fixture.tmux("list-panes", "-a", "-F",
                                "#{session_name}:#{window_index}.#{pane_index}\t#{pane_id}")
        self.assertEqual(len(pane_map.splitlines()), 30)
        targets = dict(line.split("\t") for line in pane_map.splitlines())
        agents = {event["sid"]: event for event in fixture.events("codex")}
        self.assertEqual(set(agents), {SID, OTHER_SID})
        self.assertEqual(agents[SID]["pane"], targets["cockpit:1.1"])
        self.assertEqual(agents[OTHER_SID]["pane"], targets["cockpit:5.6"])
        self.assert_startup_scratch(fixture, list(agents.values()))
        self.assertEqual({event["term"] for event in agents.values()}, {"tmux-256color"})
        self.assertEqual(set(fixture.tmux("list-panes", "-a", "-F", "#{history_limit}").splitlines()),
                         {"100000"})
        for option, value in (("history-limit", "100000"), ("mode-keys", "vi"),
                              ("status-keys", "vi"), ("status-position", "top"),
                              ("extended-keys-format", "csi-u"), ("escape-time", "0")):
            self.assertEqual(fixture.tmux("show-options", "-gqv", option), value)
        for key, command in (("h", "select-pane -L"), ("-", "split-window -v"),
                             ("+", "resize-pane -Z"), ("R", "tmux-pane-repair"),
                             ("r", "source-file"), ("Enter", "copy-mode")):
            binding = fixture.tmux("list-keys", "-T", "prefix", key)
            self.assertIn(command, binding)
            self.assertNotIn("_apply_configuration", binding)
        config = pathlib.Path(checked["frozen"]).with_suffix(".tmux.conf")
        self.assertEqual(config.stat().st_mode & 0o777, 0o400)
        self.assertNotIn("_apply_configuration", config.read_text())
        self.assertNotEqual(digest(source), fixture.env["TT_FIXTURE_SOURCE_SHA"])
        fixture.tt("autosave", "off")
        self.assertEqual(digest(checked["frozen"]), checked["sha256"])
        self.assertNotIn("respawn-pane", (fixture.root / "tmux.log").read_text())

    def test_cold_agents_leave_existing_session_and_server_options_unchanged(self):
        fixture = self.fixture()
        fixture.start()
        before = fixture.observe("anchor")
        source = fixture.root / "agents.tsv"
        source.write_text(
            f"factory2\t1\t{fixture.work}\tfirst\tcodex resume --no-alt-screen {SID}\n"
            f"factory2\t6\t{fixture.work}\tlast\tcodex resume --no-alt-screen {OTHER_SID}\n"
        )
        original = digest(source)
        fixture.tt("recover", "agents", source, "factory2")
        fixture.wait(lambda: len(fixture.events("codex")) == 2)
        self.assertEqual(fixture.observe("anchor"), before)
        self.assertEqual(digest(source), original)
        frozen = list((fixture.root / "state/tt/recovery-inputs").glob("*.tsv"))
        self.assertEqual(len(frozen), 1)
        self.assertEqual(frozen[0].stat().st_mode & 0o777, 0o400)
        self.assertTrue(frozen[0].with_suffix(".completed.json").exists())
        self.assertEqual(set(fixture.tmux("list-sessions", "-F", "#{session_name}").splitlines()),
                         {"anchor", "factory2"})
        self.assertEqual(len(fixture.tmux("list-panes", "-a", "-F", "#{pane_id}").splitlines()), 7)
        targets = dict(line.split("\t") for line in fixture.tmux(
            "list-panes", "-a", "-F", "#{session_name}:#{window_index}.#{pane_index}\t#{pane_id}"
        ).splitlines())
        agents = {event["sid"]: event["pane"] for event in fixture.events("codex")}
        self.assertEqual(agents, {SID: targets["factory2:1.1"], OTHER_SID: targets["factory2:1.6"]})
        self.assert_startup_scratch(fixture, fixture.events("codex"))
        self.assertNotIn("respawn-pane", (fixture.root / "tmux.log").read_text())
        self.assertFalse((fixture.root / "state/tt/codex-cockpit.timer.pid").exists())
        captured = fixture.root / "agents-roundtrip.tsv"
        fixture.tt("snapshot", captured, "--quiet")
        rows = [line.split("\t") for line in captured.read_text().splitlines()
                if line and not line.startswith("#")]
        self.assertEqual(len(rows), 6)
        commands = {row[1]: row[4] for row in rows}
        self.assertEqual(commands, {
            "1": f"codex resume --no-alt-screen {SID}",
            "2": "", "3": "", "4": "", "5": "",
            "6": f"codex resume --no-alt-screen {OTHER_SID}",
        })
        validated = pathlib.Path(fixture.writer(
            "--freeze-recovery", "agents", captured, "factory2"
        ).stdout.strip())
        self.assertEqual(validated.stat().st_mode & 0o777, 0o400)
        self.assertEqual([line for line in validated.read_text().splitlines() if not line.startswith("#")],
                         [line for line in captured.read_text().splitlines() if not line.startswith("#")])
        self.assertEqual(fixture.observe("anchor"), before)
        self.assertEqual(digest(source), original)

    def test_cold_coordinate_alias_refuses_before_creating_server(self):
        fixture = self.fixture()
        source = fixture.root / "aliases.tsv"
        source.write_text(
            f"cockpit:1.1\tcodex\t{fixture.work}\tfirst\tcodex\t{SID}\texact\tignored\n"
            f"cockpit:01.01\tcodex\t{fixture.work}\talias\tcodex\t{OTHER_SID}\texact\tignored\n"
        )
        original = digest(source)
        result = fixture.tt("recover", "cockpit", source, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid snapshot target", result.stderr)
        self.assertFalse(fixture.socket.exists())
        self.assertEqual(digest(source), original)
        self.assertFalse((fixture.root / "tmux.log").exists())


if __name__ == "__main__":
    if sys.argv[1:2] == ["--agent"]:
        synthetic_agent(sys.argv[2], sys.argv[3:])
    elif sys.argv[1:] == ["--before-autosave"]:
        before_autosave()
    else:
        unittest.main()
