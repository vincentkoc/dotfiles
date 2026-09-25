#!/usr/bin/env python3
"""Fixture-only snapshot and recovery checks; run in the reviewed test sandbox."""

import contextlib
import copy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

REPO = pathlib.Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader("tt_snapshot_writer", str(REPO / "bin/tt-codex-snapshot-writer"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
writer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = writer
LOADER.exec_module(writer)
SID = "11111111-1111-1111-1111-111111111111"
OTHER_SID = "22222222-2222-2222-2222-222222222222"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.cwd = self.root / "work with spaces"
        self.cwd.mkdir()
        self.source = self.root / "snapshot.tsv"
        self.state = self.root / "state"
        self.server = {"host": "fixture", "uid": os.getuid(), "socket": "/fixture/socket",
                       "device": 1, "inode": 2, "process": {"pid": 100, "start": "3"}}
        self.identity = {"version": 1, "server": self.server, "panes": {
            "cockpit:1.1": {"session": "$1", "window": "@2", "pane": "%3",
                            "pid": 200, "dead": "1", "process": None},
        }}
        self.env = {
            "PATH": "/usr/bin:/bin", "HOME": str(self.root / "home"),
            "XDG_STATE_HOME": str(self.state), "XDG_CONFIG_HOME": str(self.root / "config"),
            "CODEX_HOME": str(self.root / "codex"), "TT_LOGIN_SHELL": "/bin/sh",
            "TT_TMUX_BIN": "/usr/bin/false", "LC_ALL": "C.UTF-8",
        }
        pathlib.Path(self.env["HOME"]).mkdir()
        self.patch("STATE_HOME", self.state)
        self.patch("LOCK_PATH", self.state / "tt/codex-cockpit.lock")
        self.patch("OUTPUT", self.source)
        self.patch("DEADLINE", time.monotonic() + 30)

    def patch(self, name, value):
        operation = patch.object(writer, name, value)
        operation.start()
        self.addCleanup(operation.stop)

    def row(self, **changes):
        row = {"target": "cockpit:1.1", "kind": "codex", "cwd": str(self.cwd),
               "title": "worker", "current": "codex", "sid": SID, "status": "exact",
               "command": "never execute this; touch /unsafe"}
        row.update(changes)
        return list(row.values())

    def document(self, rows=None, identity=True):
        text = ""
        if identity:
            text += "# restore-identity\t" + json.dumps(self.identity) + "\n"
        text += "".join("\t".join(row) + "\n" for row in (rows or [self.row()]))
        self.source.write_text(text)
        return text


class ParserTests(Fixture):
    def test_eight_and_ten_columns_are_accepted_without_replaying_commands(self):
        for suffix in ([], ["running", ""], ["exited", "137"]):
            with self.subTest(suffix=suffix):
                row = writer.validate_recovery(
                    self.document([self.row() + suffix]), "codex"
                )[0]
                self.assertEqual(row[7], f"codex resume --no-alt-screen {SID}")
                self.assertEqual(len(row), 8 + len(suffix))

    def test_malformed_eight_and_ten_column_rows_are_rejected(self):
        cases = [
            self.row(sid="not-a-uuid"), self.row(kind="unknown"),
            self.row(status="unresolved"), self.row(kind="shell"),
            self.row(target="cockpit:01.1"),
            self.row() + ["exited"], self.row() + ["running", "", "extra"],
            self.row() + ["unknown", ""], self.row() + ["shell", ""],
            self.row() + ["running", "1"], self.row() + ["exited", "-1"],
            self.row() + ["exited", "256"], self.row() + ["exited", "01"],
            self.row() + ["exited", "1; touch /unsafe"],
        ]
        for row in cases:
            with self.subTest(row=row), self.assertRaises(writer.SnapshotError):
                writer.validate_recovery(self.document([row]), "codex")

    def test_unselected_picker_metadata_allows_exact_selection(self):
        for suffix in ([], ["running", ""]):
            for kind in ("codex", "claude"):
                with self.subTest(columns=8 + len(suffix), kind=kind):
                    text = self.document([
                        self.row() + suffix,
                        self.row(target="cockpit:1.2", kind=kind, sid="", status="picker") + suffix,
                    ])
                    rows = writer.validate_recovery(text, "codex", "pane", "cockpit:1.1")
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0][5], SID)
                    self.assertEqual(rows[0][7], f"codex resume --no-alt-screen {SID}")

    def test_selected_picker_metadata_is_never_executable(self):
        for suffix in ([], ["running", ""]):
            for cold in (False, True):
                text = self.document([
                    self.row(),
                    self.row(target="cockpit:1.2", sid="", status="picker") + suffix,
                ])
                with self.subTest(columns=8 + len(suffix), cold=cold), patch.object(
                    writer, "run"
                ) as run, self.assertRaisesRegex(writer.SnapshotError, "exact saved UUID"):
                    writer.validate_recovery(text, "codex", "pane", "cockpit:1.2", cold=cold)
                run.assert_not_called()

    def test_unselected_picker_still_validates_all_rows(self):
        picker = self.row(target="cockpit:1.2", sid="", status="picker")
        malformed = [
            self.row(target="cockpit:1.2", sid=OTHER_SID, status="picker"),
            self.row(target="cockpit:1.2", sid="bad-uuid", status="picker"),
            self.row(target="cockpit:1.2", kind="shell", sid="", status="picker"),
            self.row(target="cockpit:01.2", sid="", status="picker"),
        ]
        for suffix in ([], ["running", ""]):
            for row in malformed:
                with self.subTest(row=row, suffix=suffix), self.assertRaises(writer.SnapshotError):
                    writer.validate_recovery(
                        self.document([self.row(), row + suffix]), "codex", "pane", "cockpit:1.1"
                    )
        for row in (picker + ["running"], picker + ["running", "1"],
                    picker + ["exited", "bad"], picker + ["running", "", "extra"]):
            with self.subTest(row=row), self.assertRaises(writer.SnapshotError):
                writer.validate_recovery(
                    self.document([self.row(), row]), "codex", "pane", "cockpit:1.1"
                )

    def test_empty_fields_are_preserved(self):
        self.assertEqual(writer.parse_tsv("a\t\tc\t\t\n", (5,)), [["a", "", "c", "", ""]])

    def test_reserved_and_malformed_fields_are_rejected(self):
        for text in ("a\tb\n", "a\tb\tc\td\te\textra\n", "a\tb\tc\td\t\x1c\n",
                     "a\tb\tc\td\tbad\rvalue\n", "a\tb\tc\td\tbad\0value\n"):
            with self.subTest(text=repr(text)), self.assertRaises(writer.SnapshotError):
                writer.parse_tsv(text, (5,))

    def test_resume_is_built_from_exact_uuid_not_shell_column(self):
        rows = writer.validate_recovery(self.document(), "codex")
        self.assertEqual(rows[0][7], f"codex resume --no-alt-screen {SID}")

    def test_ambiguous_and_duplicate_records_fail_whole_selection(self):
        cases = [
            [self.row(sid=""), self.row(target="cockpit:1.2", sid=OTHER_SID)],
            [self.row(), self.row()],
            [self.row(), self.row(target="cockpit:1.2")],
            [self.row(), self.row(target="cockpit:1.2", sid=OTHER_SID, status="unresolved")],
            [self.row(sid=SID + "; false")],
            [self.row(cwd=str(self.root / "missing"))],
        ]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(writer.SnapshotError):
                writer.validate_recovery(self.document(rows), "codex")

    def test_claude_requires_exact_uuid(self):
        self.assertEqual(
            writer.validate_recovery(self.document([self.row(kind="claude")]), "codex")[0][7],
            f"claude --resume {SID}",
        )
        with self.assertRaises(writer.SnapshotError):
            writer.validate_recovery(self.document([self.row(kind="claude", sid="", status="resume")]), "codex")

    def test_manifest_commands_are_normalized_or_refused(self):
        command, session_id = writer.manifest_resume_command(
            f"codex resume --dangerously-bypass-approvals-and-sandbox --no-alt-screen {SID}"
        )
        self.assertEqual(command, f"codex resume --no-alt-screen {SID}")
        self.assertEqual(session_id, SID)
        for command in ("codex", "codex resume --last", "claude resume",
                        f"codex resume {SID}; touch /unsafe", "printf ready"):
            with self.subTest(command=command), self.assertRaises(writer.SnapshotError):
                writer.manifest_resume_command(command)

    def test_agent_empty_title_and_command_are_not_shifted(self):
        text = f"factory2\t1\t{self.cwd}\t\t\n"
        rows = writer.validate_recovery(text, "agents", cold=True)
        self.assertEqual(rows[0], ["factory2", "1", str(self.cwd), "", ""])

    def test_freeze_keeps_both_schema_inputs_outside_retention(self):
        for schema, text, selector in (
            ("codex", self.document(identity=False), "cockpit"),
            ("agents", f"factory2\t1\t{self.cwd}\t\tcodex resume {SID}\n", "factory2"),
        ):
            self.source.write_text(text)
            with patch.object(writer, "run", return_value="ext2/ext3\n"):
                frozen = pathlib.Path(writer.freeze_recovery(schema, self.source, selector))
            self.source.write_text("replacement\n")
            self.assertIn(SID, frozen.read_text())
            self.assertNotIn("replacement", frozen.read_text())
            self.assertEqual(frozen.stat().st_mode & 0o777, 0o400)
            self.assertEqual(frozen.parent, self.state / "tt/recovery-inputs")

    def test_ram_backed_freeze_fails_before_server_lookup(self):
        self.document(identity=False)
        with patch.object(writer.sys, "platform", "linux"), patch.object(
            writer, "run", return_value="tmpfs\n"
        ) as run, self.assertRaises(writer.SnapshotError):
            writer.freeze_recovery("codex", self.source, "cockpit")
        self.assertEqual(run.call_args.args[0][0], "stat")

    def test_startup_environment_validates_private_disk_scratch(self):
        with patch.dict(os.environ, self.env, clear=True), patch.object(
            writer, "run", return_value="ext2/ext3\n"
        ):
            arguments = writer.recovery_environment()
        scratch = pathlib.Path(self.env["HOME"]) / ".cache/cockpit-tmp"
        self.assertEqual(scratch.stat().st_mode & 0o777, 0o700)
        self.assertIn(f"TMPDIR={scratch}", arguments)
        self.assertIn(f"PATH={self.env['HOME']}/.local/bin:{self.env['HOME']}/bin:"
                      f"{self.env['HOME']}/.local/share/pnpm:/usr/bin:/bin", arguments)
        with patch.dict(os.environ, self.env, clear=True), patch.object(
            writer.sys, "platform", "linux"
        ), patch.object(writer, "run", return_value="tmpfs\n"), self.assertRaises(writer.SnapshotError):
            writer.recovery_environment()
        scratch.chmod(0o755)
        with patch.dict(os.environ, self.env, clear=True), patch.object(
            writer, "run"
        ) as run, self.assertRaises(writer.SnapshotError):
            writer.recovery_environment()
        run.assert_not_called()

    def test_noncanonical_coordinates_fail_before_any_cold_server_lookup(self):
        for target in ("cockpit:01.01", "cockpit:01.1", "cockpit:1.01", "cockpit:1.+1"):
            self.document([self.row(), self.row(target=target, sid=OTHER_SID)], identity=False)
            with self.subTest(target=target), patch.object(
                writer, "run"
            ) as run, self.assertRaises(writer.SnapshotError):
                writer.recover_cold("codex", self.source, "cockpit", "0")
            run.assert_not_called()

    def test_cold_grid_bounds_fail_before_any_server_lookup(self):
        for target in ("cockpit:0.1", "cockpit:100.1", "cockpit:999999.1",
                       "cockpit:6.0", "cockpit:6.7"):
            self.document([self.row(), self.row(target=target, sid=OTHER_SID)], identity=False)
            with self.subTest(target=target), patch.object(
                writer, "run"
            ) as run, self.assertRaisesRegex(writer.SnapshotError, "outside the recovery grid"):
                writer.recover_cold("codex", self.source, "cockpit", "0")
            run.assert_not_called()


class RecoveryTests(Fixture):
    def setUp(self):
        super().setUp()
        self.patch("recovery_environment", lambda: ["-e", f"TMPDIR={self.cwd}"])

    def execute(self, identities=None, live=None):
        with patch.object(writer, "restore_identity", side_effect=identities or [
            copy.deepcopy(self.identity), copy.deepcopy(self.identity)
        ]), patch.object(writer, "build_rows", return_value=live or []), patch.object(
            writer, "run", return_value=""
        ) as run, contextlib.redirect_stdout(io.StringIO()):
            writer.restore_dead(self.source, "all", "")
        return run

    def test_dead_restore_has_no_force_or_ui_commands(self):
        self.document()
        run = self.execute()
        self.assertEqual(run.call_count, 1)
        args = run.call_args.args[0]
        self.assertEqual(args[1:4], ["respawn-pane", "-t", "%3"])
        self.assertNotIn("-k", args)
        self.assertLess(args.index(f"TMPDIR={self.cwd}"), args.index("/bin/sh"))
        self.assertIn(f"codex resume --no-alt-screen {SID}", args[-1])
        self.assertNotIn("unsafe", args[-1])

    def test_legacy_is_preview_only(self):
        self.document(identity=False)
        with patch.object(writer, "run") as run, self.assertRaises(writer.SnapshotError):
            writer.restore_dead(self.source, "all", "")
        run.assert_not_called()

    def test_wrong_host_socket_server_and_reused_coordinates_refuse(self):
        self.document()
        for section, key, value in (
            ("server", "host", "elsewhere"), ("server", "inode", 9),
            ("server", "uid", -1), ("pane", "pane", "%99"), ("pane", "pid", 999),
            ("pane", "window", "@99"), ("pane", "session", "$99"),
            ("pane", "process", {"pid": 200, "uid": os.getuid(), "start": "reused"}),
        ):
            changed = copy.deepcopy(self.identity)
            destination = changed["server"] if section == "server" else changed["panes"]["cockpit:1.1"]
            destination[key] = value
            with self.subTest(key=key), self.assertRaises(writer.SnapshotError):
                self.execute(identities=[changed])

    def test_live_shell_and_unresolved_agent_are_never_replaced(self):
        self.document()
        live = copy.deepcopy(self.identity)
        live["panes"]["cockpit:1.1"]["dead"] = "0"
        for rows in ([], [self.row(status="unresolved", sid="")]):
            with self.subTest(rows=rows), self.assertRaises(writer.SnapshotError):
                self.execute(identities=[live], live=rows)

    def test_rerun_skips_exact_live_session_but_rejects_duplicate_elsewhere(self):
        self.document()
        live = copy.deepcopy(self.identity)
        live["panes"]["cockpit:1.1"].update(pid=201, dead="0")
        run = self.execute(identities=[live, live], live=[self.row()])
        run.assert_not_called()
        with self.assertRaises(writer.SnapshotError):
            self.execute(live=[self.row(target="cockpit:2.1")])

    def test_complete_preflight_prevents_partial_restore_on_invalid_second_row(self):
        self.document([self.row(), self.row(target="cockpit:1.2", sid=OTHER_SID)])
        with patch.object(writer, "restore_identity", return_value=self.identity), patch.object(
            writer, "build_rows", return_value=[]
        ), patch.object(writer, "run") as run, self.assertRaises(writer.SnapshotError):
            writer.restore_dead(self.source, "all", "")
        run.assert_not_called()

    def test_last_moment_identity_change_stops_batch(self):
        self.document()
        changed = copy.deepcopy(self.identity)
        changed["panes"]["cockpit:1.1"]["dead"] = "0"
        with self.assertRaises(writer.SnapshotError):
            self.execute(identities=[self.identity, changed])

    def test_unresolved_agent_elsewhere_refuses_dead_target(self):
        self.document()
        with patch.object(writer, "restore_identity", return_value=self.identity), patch.object(
            writer, "build_rows",
            return_value=[self.row(target="unrelated:2.1", status="unresolved", sid="")],
        ), patch.object(writer, "run") as run, self.assertRaisesRegex(writer.SnapshotError, "unresolved"):
            writer.restore_dead(self.source, "all", "")
        run.assert_not_called()

    def test_uuid_and_unresolved_evidence_is_refreshed_immediately_before_execution(self):
        self.document()
        for late_row in (
            self.row(target="unrelated:2.1"),
            self.row(target="unrelated:2.1", status="unresolved", sid=""),
            self.row(),
        ):
            with self.subTest(row=late_row), patch.object(
                writer, "restore_identity", return_value=self.identity
            ), patch.object(writer, "build_rows", side_effect=[[], [late_row]]) as collect, patch.object(
                writer, "run"
            ) as run, self.assertRaises(writer.SnapshotError):
                writer.restore_dead(self.source, "all", "")
            self.assertEqual(collect.call_count, 2)
            run.assert_not_called()

    def test_tmux_active_pane_refusal_is_not_retried_with_force(self):
        self.document()
        with patch.object(writer, "restore_identity", return_value=self.identity), patch.object(
            writer, "build_rows", return_value=[]
        ), patch.object(writer, "run", side_effect=writer.SnapshotError("active")) as run:
            with self.assertRaises(writer.SnapshotError):
                writer.restore_dead(self.source, "all", "")
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("-k", run.call_args.args[0])

    def test_cold_creation_uses_only_new_resources_and_exact_commands(self):
        self.source.write_text(f"factory2\t1\t{self.cwd}\tworker\tcodex resume {SID}\n")
        calls, panes = [], []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[1] == "list-sessions":
                return "unaffected\n"
            if "new-session" in args:
                panes.append("%1")
                return "$2\t@3\t%1\n"
            if args[1] == "display-message":
                return "0\n"
            if args[1] == "list-panes":
                return "\n".join(panes) + "\n"
            if args[1] == "split-window":
                pane = f"%{len(panes) + 1}"
                panes.append(pane)
                return pane + "\n"
            return ""

        def identity():
            return {"server": self.server, "panes": {
                f"factory2:1.{index}": {
                    "session": "$2", "window": "@3", "pane": pane,
                    "pid": 200 + index, "dead": "0", "process": {"start": str(index)},
                } for index, pane in enumerate(panes, 1)
            }}

        with patch.object(writer, "server_identity", return_value=self.server), patch.object(
            writer, "restore_identity", side_effect=identity
        ), patch.object(
            writer, "build_rows", return_value=[]
        ), patch.object(
            writer, "run", side_effect=fake_run
        ), contextlib.redirect_stdout(io.StringIO()) as output:
            writer.recover_cold("agents", self.source, "factory2", "1")
        self.assertEqual(len(panes), 6)
        self.assertTrue(pathlib.Path(output.getvalue().strip()).is_file())
        self.assertTrue(any("new-session" in args and SID in args[-1] for args in calls))
        for args in calls:
            if "new-session" in args or "split-window" in args:
                self.assertLess(args.index(f"TMPDIR={self.cwd}"), args.index("/bin/sh"))
        self.assertFalse(any("respawn-pane" in args or "-k" in args or "-g" in args for args in calls))
        self.assertFalse(any("unaffected" in args for args in calls))
        self.assertEqual(calls[0][1:], ["topology-authorize", "recover", "factory2"])

    def test_cold_cockpit_preserves_saved_windows_and_exact_commands(self):
        for saved_windows in ([1], list(range(1, 7)), [99]):
            with self.subTest(saved_windows=saved_windows):
                rows = []
                for window in saved_windows:
                    for pane in range(1, 7):
                        values = {"target": f"cockpit:{window}.{pane}",
                                  "sid": f"11111111-1111-1111-1111-{window * 6 + pane:012d}"}
                        if window == 1 and pane <= 3:
                            values.update(kind="shell", sid="", status="shell")
                        rows.append(self.row(**values))
                self.document(rows, identity=False)
                calls, windows, commands = [], {}, {}

                def fake_run(args, **kwargs):
                    calls.append(args)
                    if args[1] == "list-sessions":
                        return "unaffected\n"
                    if "new-session" in args or args[1] == "new-window":
                        number = 1 if "new-session" in args else int(args[args.index("-t") + 1].split(":")[1])
                        pane = f"%{len(commands) + 1}"
                        window = f"@{number}"
                        windows[window] = [pane]
                        commands[pane] = args[-1]
                        prefix = "$2\t" if "new-session" in args else ""
                        return f"{prefix}{window}\t{pane}\n"
                    if args[1] == "display-message":
                        return "1\n"
                    if args[1] == "list-panes":
                        return "\n".join(windows[args[args.index("-t") + 1]]) + "\n"
                    if args[1] == "split-window":
                        parent = args[args.index("-t") + 1]
                        panes = next(panes for panes in windows.values() if parent in panes)
                        pane = f"%{len(commands) + 1}"
                        panes.insert(panes.index(parent) + 1, pane)
                        commands[pane] = args[-1]
                        return pane + "\n"
                    return ""

                def identity():
                    return {"server": self.server, "panes": {
                        f"cockpit:{window[1:]}.{index}": {
                            "session": "$2", "window": window, "pane": pane,
                            "pid": 200 + int(pane[1:]), "dead": "0", "process": {"start": pane[1:]},
                        } for window, panes in windows.items() for index, pane in enumerate(panes, 1)
                    }}

                with patch.object(writer, "server_identity", return_value=self.server), patch.object(
                    writer, "restore_identity", side_effect=identity
                ), patch.object(writer, "build_rows", return_value=[]), patch.object(
                    writer, "run", side_effect=fake_run
                ), contextlib.redirect_stdout(io.StringIO()) as output:
                    writer.recover_cold("codex", self.source, "cockpit", "1")
                expected_windows = {1, 2, 3, 4, 5} | set(saved_windows)
                self.assertEqual(set(windows), {f"@{window}" for window in expected_windows})
                self.assertEqual(len(commands), len(expected_windows) * 6)
                proof_path = pathlib.Path(output.getvalue().strip())
                proof = json.loads(proof_path.read_text())
                self.assertEqual(set(proof["panes"]), set(commands))
                self.assertEqual(proof["identities"], identity()["panes"])
                expected = {row[0]: row[5] for row in rows}
                for target, pane in proof["identities"].items():
                    sid = expected.get(target, "")
                    program = (f"codex resume --no-alt-screen {sid}; " if sid else "")
                    self.assertEqual(commands[pane["pane"]], program + f"exec {writer.LOGIN_SHELL} -il")
                self.assertEqual(calls[0][1:], ["topology-authorize", "recover", "cockpit"])
                self.assertFalse(any("respawn-pane" in args or "-k" in args or "-g" in args for args in calls))
                self.assertFalse(any("unaffected" in args for args in calls))
                proof_path.unlink()

    def test_direct_cold_helper_denial_on_second_session_has_zero_tmux_calls(self):
        self.source.write_text(
            f"first\t1\t{self.cwd}\t\tcodex resume {SID}\n"
            f"second\t1\t{self.cwd}\t\tcodex resume {OTHER_SID}\n"
        )
        calls = []

        def authorize(args, **kwargs):
            calls.append(args)
            self.assertEqual(args[1:3], ["topology-authorize", "recover"])
            if args[-1] == "second":
                raise writer.SnapshotError("second session denied")
            return ""

        with patch.object(writer, "run", side_effect=authorize), self.assertRaisesRegex(
            writer.SnapshotError, "second session denied"
        ):
            writer.recover_cold("agents", self.source, "first", "0")
        self.assertEqual([args[-1] for args in calls], ["first", "second"])
        self.assertFalse(self.source.with_suffix(".tmux.conf").exists())

    def test_direct_cold_helper_rejects_unavailable_older_authorizer(self):
        self.document(identity=False)
        with patch.object(writer, "run", side_effect=writer.SnapshotError("unknown command")) as run:
            with self.assertRaises(writer.SnapshotError):
                writer.recover_cold("codex", self.source, "cockpit", "0")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][1:], ["topology-authorize", "recover", "cockpit"])

    def test_cold_conflict_and_malformed_input_never_create_anything(self):
        for text in (
            f"factory2\t1\t{self.cwd}\tworker\tcodex resume {SID}\n",
            f"factory2\t1\t{self.cwd}\tworker\tcodex\n",
        ):
            self.source.write_text(text)
            with patch.object(writer, "server_identity", return_value=self.server), patch.object(
                writer, "run", return_value="factory2\n"
            ) as run, self.assertRaises(writer.SnapshotError):
                writer.recover_cold("agents", self.source, "factory2", "1")
            self.assertFalse(any("new-session" in call.args[0] for call in run.call_args_list))


class SnapshotTests(Fixture):
    def test_dead_agent_is_not_unresolved_live_evidence(self):
        panes = {"%1": ["cockpit:1.1", "%1", "100", "/dev/pts/1", "worker",
                        "codex", str(self.cwd), "1", "137"]}
        with patch.object(writer, "collect_panes", return_value=panes), patch.object(
            writer, "collect_processes", return_value={}
        ), patch.object(writer, "foreground_job", return_value=[]), patch.object(
            writer, "collect_commands", return_value={}
        ), patch.object(writer, "collect_open_rollouts", return_value={}), patch.object(
            writer, "collect_metadata", return_value={}
        ):
            row = writer.build_rows()[0]
            self.assertEqual(row[6], "unresolved")
            self.assertEqual(row[8:], ["exited", "137"])
            self.assertEqual(writer.live_session_evidence(), {})

    def test_current_nine_field_pane_contract(self):
        line = f"cockpit:1.1\t%1\t100\t/dev/pts/1\tworker\tcodex\t{self.cwd}\t0\t\n"
        with patch.object(writer, "run", return_value=line):
            self.assertEqual(writer.collect_panes()["%1"][2], "100")
        with patch.object(writer, "run", return_value=f"cockpit:1.1\t100\tworker\tcodex\t{self.cwd}\n"):
            with self.assertRaises(writer.SnapshotError):
                writer.collect_panes()

    def test_exited_identity_retention_requires_root_process_start_evidence(self):
        saved = copy.deepcopy(self.identity)
        saved["panes"]["cockpit:1.1"].update(
            dead="0", process={"pid": 200, "uid": os.getuid(), "start": "1234"}
        )
        current = copy.deepcopy(saved)
        current["panes"]["cockpit:1.1"]["dead"] = "1"
        for suffix in ([], ["running", ""], ["exited", "137"]):
            with self.subTest(suffix=suffix):
                self.source.write_text(
                    "# restore-identity\t" + json.dumps(saved) + "\n"
                    + "\t".join(self.row() + suffix) + "\n"
                )
                self.assertEqual(writer.previous_exit_rows(current)["cockpit:1.1"][5], SID)

    def test_exited_retention_rejects_coordinate_pid_and_server_reuse(self):
        saved = copy.deepcopy(self.identity)
        saved["panes"]["cockpit:1.1"]["process"] = {
            "pid": 200, "uid": os.getuid(), "start": "1234"
        }
        self.source.write_text(
            "# restore-identity\t" + json.dumps(saved) + "\n"
            + "\t".join(self.row() + ["exited", "137"]) + "\n"
        )
        cases = [
            ("server", "host", "another-host"), ("server", "inode", 99),
            ("server", "process", {"pid": 100, "start": "reused"}),
            ("pane", "session", "$99"), ("pane", "window", "@99"),
            ("pane", "pane", "%99"), ("pane", "pid", 201),
            ("pane", "dead", "0"), ("pane", "process", None),
            ("pane", "process", {"pid": 200, "uid": os.getuid(), "start": "reused"}),
            ("pane", "process", {"pid": 200, "uid": -1, "start": "1234"}),
        ]
        for section, key, value in cases:
            changed = copy.deepcopy(saved)
            target = changed["server"] if section == "server" else changed["panes"]["cockpit:1.1"]
            target[key] = value
            with self.subTest(section=section, key=key):
                self.assertEqual(writer.previous_exit_rows(changed), {})
        for process in (None, {}, {"pid": 200, "uid": os.getuid()}, {"start": "1234"}):
            current = copy.deepcopy(saved)
            current["panes"]["cockpit:1.1"]["process"] = process
            self.source.write_text(
                "# restore-identity\t" + json.dumps(current) + "\n"
                + "\t".join(self.row()) + "\n"
            )
            self.assertEqual(writer.previous_exit_rows(current), {})

    def test_exited_retention_rejects_missing_and_malformed_prior_evidence(self):
        for text in (
            "\t".join(self.row()) + "\n",
            "# restore-identity\t{}\n" + "\t".join(self.row()) + "\n",
            "# restore-identity\t" + json.dumps(self.identity) + "\n"
            + "\t".join(self.row() + ["exited", "bad"]) + "\n",
            "# restore-identity\t" + json.dumps(self.identity) + "\n"
            + "\t".join(self.row(sid="not-a-uuid")) + "\n",
        ):
            with self.subTest(text=text):
                self.source.write_text(text)
                self.assertEqual(writer.previous_exit_rows(self.identity), {})

    def test_exited_commands_are_rebuilt_never_replayed(self):
        pane = ["cockpit:1.1", "%3", "200", "", "", "", "", "1", "137"]
        with patch.object(writer, "collect_panes", return_value={"%3": pane}), patch.object(
            writer, "collect_processes", return_value={}
        ), patch.object(writer, "previous_exit_rows", return_value={"cockpit:1.1": self.row()}):
            row = writer.build_rows(identity=self.identity)[0]
        self.assertEqual(row[5:7], [SID, "exact"])
        self.assertEqual(row[8:], ["exited", "137"])
        self.assertIn(f"codex resume --no-alt-screen {SID}", row[7])
        self.assertNotIn("unsafe", row[7])
        self.assertEqual(row[2], str(self.cwd))

    def test_status_reports_exits_separately_from_live_work(self):
        rows = [
            self.row() + ["exited", "137"],
            self.row(target="cockpit:1.2", sid=OTHER_SID) + ["running", ""],
        ]
        output = self.state / "tt/codex-cockpit.tsv"
        output.parent.mkdir(parents=True)
        output.write_text(self.document(rows))
        result = subprocess.run(
            ["/bin/bash", str(REPO / "bin/tt"), "status"],
            env=self.env, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("owner exits: 1 exited, 1 failed", result.stdout)
        self.assertIn("work state: 1 waiting, 0 spinning", result.stdout)

    def test_exact_metadata_ignores_picker_handles_and_rejects_ambiguity(self):
        job = [writer.Process(100, 1, 100, 100, "S", "pts/1", "codex")]
        opens = {100: {"current", "subagent"}}
        metadata = {"current": {"id": SID, "source": "cli"},
                    "subagent": {"id": OTHER_SID, "source": {"subagent": {}}}}
        self.assertEqual(writer.exact_session(job, opens, metadata), SID)
        metadata["subagent"]["source"] = "cli"
        self.assertEqual(writer.exact_session(job, opens, metadata), "")
        with patch.object(writer, "run", return_value="p100\nf4\nar\nG0x0;0x0\nn/current.jsonl\n"):
            self.assertEqual(dict(writer.collect_open_rollouts({100})), {})

    def test_agent_publication_uses_one_lock_and_preserves_empty_fields(self):
        output = self.state / "tt/agent-cockpit.tsv"
        lock = writer.acquire_snapshot_lock()
        self.addCleanup(lock.close)
        shell = self.row(target="factory2:1.1", kind="shell", status="shell", sid="")
        with patch.object(writer, "build_rows", return_value=[shell]), patch.object(
            writer, "run", return_value=f"factory2\t1\t{self.cwd}\t\t\n"
        ) as run:
            writer.publish_agent_snapshot(output)
        self.assertEqual(run.call_args.args[0][-1], "snapshot-records")
        self.assertEqual(writer.parse_tsv(output.read_text(), (5,))[0][3:], ["", ""])
        self.assertIsNone(writer.acquire_snapshot_lock())
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        history = self.state / "tt/history/agent-cockpit"
        self.assertEqual(len(list(history.glob("*.tsv"))), 1)
        with patch.object(writer, "build_rows", return_value=[shell]), patch.object(
            writer, "run", return_value=f"factory2\t1\t{self.cwd}\t\t\n"
        ):
            writer.publish_agent_snapshot(output)
        self.assertEqual(len(list(history.glob("*.tsv"))), 1)

    def test_agent_publication_canonical_roundtrip_ignores_launch_wrappers(self):
        output = self.root / "agents.tsv"
        collector = (f"factory2\t1\t{self.cwd}\tworker\t/bin/sh -c 'touch /unsafe'\n"
                     f"factory2\t2\t{self.cwd}\t\t/bin/sh -c 'exec zsh -il'\n")
        live = [self.row(target="factory2:1.1"),
                self.row(target="factory2:1.2", kind="shell", status="shell", sid="")]
        with patch.object(writer, "run", return_value=collector), patch.object(
            writer, "build_rows", return_value=live
        ):
            writer.publish_agent_snapshot(output)
        rows = writer.validate_recovery(output.read_text(), "agents", cold=True)
        self.assertEqual(rows[0][4], f"codex resume --no-alt-screen {SID}")
        self.assertEqual(rows[1][3:], ["", ""])
        self.assertNotIn("/bin/sh", output.read_text())
        self.assertNotIn("unsafe", output.read_text())

    def test_unresolved_agent_publication_preserves_prior_evidence(self):
        output = self.root / "agents.tsv"
        output.write_text("preserve me\n")
        with patch.object(writer, "run", return_value=f"factory2\t1\t{self.cwd}\tworker\t\n"), patch.object(
            writer, "build_rows",
            return_value=[self.row(target="factory2:1.1", status="unresolved", sid="")],
        ), self.assertRaisesRegex(writer.UnresolvedAgentEvidence, "unresolved"):
            writer.publish_agent_snapshot(output)
        self.assertEqual(output.read_text(), "preserve me\n")

    def test_combined_autosave_continues_across_persistent_unresolved_agent_cycles(self):
        agent = self.state / "tt/agent-cockpit.tsv"
        agent_history = self.state / "tt/history/agent-cockpit"
        agent_history.mkdir(parents=True)
        previous = agent_history / "previous.tsv"
        agent.write_text("preserve manifest\n")
        previous.write_text("preserve history\n")
        preserved = {path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                     for path in (agent, previous)}
        codex = self.state / "tt/codex-cockpit.tsv"
        codex_history = self.state / "tt/history/codex-cockpit"
        write_snapshot, record_history = writer.write_snapshot, writer.record_snapshot_history

        def publish(rows, output=codex, header=None):
            self.assertIsNone(writer.acquire_snapshot_lock())
            self.assertEqual(output, codex)
            write_snapshot(rows, output, header)

        def archive(output=codex, history_dir=codex_history):
            self.assertIsNone(writer.acquire_snapshot_lock())
            self.assertEqual((output, history_dir), (codex, codex_history))
            record_history(output, history_dir)

        unresolved = self.row(target="factory2:1.1", kind="claude", sid="", status="unresolved")
        with patch.object(writer, "AUTOSAVE", True), patch.object(
            writer, "AGENT_ONLY", False
        ), patch.object(writer, "OUTPUT", codex), patch.object(
            writer, "run", return_value=f"factory2\t1\t{self.cwd}\tworker\t\n"
        ), patch.object(writer, "restore_identity", return_value=self.identity), patch.object(
            writer, "write_snapshot", side_effect=publish
        ), patch.object(writer, "record_snapshot_history", side_effect=archive), patch.object(
            writer.signal, "signal"
        ), patch.object(writer.signal, "setitimer"), contextlib.redirect_stderr(io.StringIO()) as errors:
            for cycle in range(2):
                rows = [unresolved + ["running", ""],
                        self.row(title=f"cycle-{cycle}") + ["running", ""]]
                with patch.object(writer, "build_rows", return_value=rows):
                    self.assertEqual(writer.main(), 0)
                self.assertEqual(writer.parse_tsv(codex.read_text(), (10,)), rows)
                history = list(codex_history.glob("*.tsv"))
                self.assertEqual(len(history), cycle + 1)
                self.assertTrue(any(path.read_bytes() == codex.read_bytes() for path in history))
                self.assertEqual(list(agent_history.iterdir()), [previous])
                self.assertEqual(
                    {path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                     for path in preserved}, preserved,
                )
        self.assertEqual(errors.getvalue().count("prior manifest/history preserved"), 2)
        with writer.acquire_snapshot_lock():
            pass

    def test_combined_collection_failure_and_agent_only_ambiguity_still_fail(self):
        for agent_only, error in (
            (False, writer.SnapshotError("collector failed")),
            (True, writer.UnresolvedAgentEvidence("unresolved")),
        ):
            with self.subTest(agent_only=agent_only), patch.object(
                writer, "AUTOSAVE", not agent_only
            ), patch.object(writer, "AGENT_ONLY", agent_only), patch.object(
                writer, "publish_agent_snapshot", side_effect=error
            ), patch.object(writer, "restore_identity") as identity, patch.object(
                writer, "write_snapshot"
            ) as publish, patch.object(writer, "record_snapshot_history") as history, patch.object(
                writer.signal, "signal"
            ), patch.object(writer.signal, "setitimer"), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(writer.main(), 1)
            identity.assert_not_called()
            publish.assert_not_called()
            history.assert_not_called()

    def test_old_writer_new_tt_returns_busy_without_changing_evidence(self):
        output = self.state / "tt/agent-cockpit.tsv"
        lock = writer.acquire_snapshot_lock()
        self.addCleanup(lock.close)
        output.write_text("preserve me\n")
        inode = writer.LOCK_PATH.stat().st_ino
        started = time.monotonic()
        result = subprocess.run(
            ["/bin/bash", str(REPO / "bin/tt"), "snapshot-capture"],
            env=self.env, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(output.read_text(), "preserve me\n")
        self.assertEqual(writer.LOCK_PATH.stat().st_ino, inode)

    def test_new_writer_old_tt_fails_without_publishing(self):
        staged = self.root / "old-tt"
        staged.mkdir()
        shutil.copy2(REPO / "bin/tt-codex-snapshot-writer", staged / "tt-codex-snapshot-writer")
        (staged / "tt").write_text("#!/bin/sh\nexit 2\n")
        (staged / "tt").chmod(0o700)
        output = self.state / "tt/codex-cockpit.tsv"
        output.parent.mkdir(parents=True)
        output.write_text("preserve me\n")
        result = subprocess.run(
            [sys.executable, str(staged / "tt-codex-snapshot-writer"), "--autosave", str(output)],
            env=self.env, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(output.read_text(), "preserve me\n")

    def test_stale_flock_metadata_does_not_replace_lock_inode(self):
        writer.LOCK_PATH.parent.mkdir(parents=True)
        writer.LOCK_PATH.write_text("999999\t0\n")
        inode = writer.LOCK_PATH.stat().st_ino
        with writer.acquire_snapshot_lock():
            self.assertEqual(writer.LOCK_PATH.stat().st_ino, inode)

    def test_symlink_lock_is_rejected(self):
        writer.LOCK_PATH.parent.mkdir(parents=True)
        evidence = self.root / "evidence"
        evidence.write_text("unchanged\n")
        writer.LOCK_PATH.symlink_to(evidence)
        with self.assertRaises(OSError):
            writer.acquire_snapshot_lock()
        self.assertEqual(evidence.read_text(), "unchanged\n")

    def test_collection_timeout_preserves_output_and_reaps_owned_collector(self):
        output = self.root / "output"
        output.write_text("unchanged\n")
        self.patch("COMMAND_TIMEOUT_SECONDS", 0.05)
        self.patch("DEADLINE", time.monotonic() + 1)
        children = []
        original = subprocess.Popen
        def launch(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        diagnostic = io.StringIO()
        with patch.object(writer.subprocess, "Popen", side_effect=launch), contextlib.redirect_stderr(
            diagnostic
        ), self.assertRaisesRegex(writer.SnapshotError, "timed out collecting"):
            writer.run(["/bin/sh", "-c", "sleep 10 & wait"])
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].returncode)
        self.assertTrue(children[0].stdout.closed)
        if diagnostic.getvalue():
            # macOS rejects a final group signal when only zombies remain;
            # retain that diagnostic, but always reap the direct child.
            self.assertEqual(sys.platform, "darwin")
            self.assertIn("collector cleanup incomplete: [Errno 1]", diagnostic.getvalue())
        self.assertEqual(output.read_text(), "unchanged\n")

    def test_failed_agent_collection_preserves_published_file(self):
        output = self.root / "agents.tsv"
        output.write_text("unchanged\n")
        with patch.object(writer, "run", side_effect=writer.SnapshotError("collector failed")):
            with self.assertRaises(writer.SnapshotError):
                writer.publish_agent_snapshot(output)
        self.assertEqual(output.read_text(), "unchanged\n")


class CollectorCancellationTests(Fixture):
    def setUp(self):
        super().setUp()
        self.staged_writer = self.root / "tt-codex-snapshot-writer"
        shutil.copy2(REPO / "bin/tt-codex-snapshot-writer", self.staged_writer)
        self.collector = self.root / "tt"
        self.collector.write_text(f"#!{sys.executable}\n" + textwrap.dedent("""\
            import json, os, pathlib, signal, time
            root = pathlib.Path(__file__).parent
            def publish(name, value):
                temporary = root / (name + '.tmp')
                temporary.write_text(value)
                temporary.replace(root / name)
            publish('collector-pid', str(os.getpid()))
            child = os.fork()
            if child == 0:
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                for fd in (0, 2):
                    os.close(fd)
                publish('grandchild-ready', str(os.getpid()))
                while True:
                    time.sleep(1)
            def terminate(signum, frame):
                publish('term-seen', 'ready')
                os._exit(0)
            signal.signal(signal.SIGTERM, terminate)
            while not (root / 'grandchild-ready').exists():
                time.sleep(.005)
            publish('collector-ready', json.dumps([os.getpid(), child]))
            while True:
                time.sleep(1)
            """))
        self.collector.chmod(0o700)
        self.launcher = self.root / 'launch-writer.py'
        self.launcher.write_text(textwrap.dedent("""\
            import importlib.machinery, importlib.util, json, os, pathlib
            import signal, subprocess, sys, time
            root = pathlib.Path(__file__).parent
            source = root / 'tt-codex-snapshot-writer'
            sys.argv = [str(source), *sys.argv[1:]]
            loader = importlib.machinery.SourceFileLoader('fixture_writer', str(source))
            spec = importlib.util.spec_from_loader(loader.name, loader)
            writer = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = writer
            loader.exec_module(writer)
            original = subprocess.Popen
            children = []
            original_defer = writer.defer_interrupts
            deferrals = 0
            def defer():
                global deferrals
                deferrals += 1
                if deferrals == 2 and os.environ.get('FIXTURE_CLEANUP_SIGNAL'):
                    os.kill(os.getpid(), int(os.environ['FIXTURE_CLEANUP_SIGNAL']))
                return original_defer()
            writer.defer_interrupts = defer
            original_deliver = writer.deliver_interrupt
            deliveries = 0
            def deliver():
                global deliveries
                deliveries += 1
                original_deliver()
                if deliveries == 2 and os.environ.get('FIXTURE_OPTIONAL_MISSING') == 'checkpoint':
                    os.kill(os.getpid(), signal.SIGTERM)
            writer.deliver_interrupt = deliver
            def ready():
                deadline = time.monotonic() + 3
                while not (root / 'test-admitted').exists():
                    if time.monotonic() >= deadline:
                        raise RuntimeError('fixture readiness timed out')
                    time.sleep(.005)
            def launch(*args, **kwargs):
                try:
                    process = original(*args, **kwargs)
                except OSError:
                    if os.environ.get('FIXTURE_OPTIONAL_MISSING') == 'pending':
                        os.kill(os.getpid(), signal.SIGTERM)
                    raise
                children.append(process)
                if os.environ.get('FIXTURE_LAUNCH_SIGNALS'):
                    ready()
                    for signum in os.environ['FIXTURE_LAUNCH_SIGNALS'].split(','):
                        os.kill(os.getpid(), int(signum))
                if os.environ.get('FIXTURE_EXCEPTION'):
                    def communicate(*args, **kwargs):
                        ready()
                        errors = {
                            'runtime': RuntimeError('fixture communication failed'),
                            'timeout': subprocess.TimeoutExpired('fixture', 1),
                            'unicode': UnicodeError('fixture decoder failed'),
                            'keyboard': KeyboardInterrupt(),
                            'system': SystemExit(23),
                        }
                        raise errors[os.environ['FIXTURE_EXCEPTION']]
                    process.communicate = communicate
                return process
            subprocess.Popen = launch
            if os.environ.get('FIXTURE_OPTIONAL_MISSING'):
                def optional():
                    writer.run([str(root / 'missing')], required=False)
                    (root / 'optional-returned').touch()
                    return 0
                writer.dispatch = optional
            if os.environ.get('FIXTURE_SWALLOWED_ERROR'):
                def swallowed():
                    try:
                        raise ValueError('fixture read error')
                    except ValueError:
                        os.kill(os.getpid(), signal.SIGTERM)
                    return 0
                writer.dispatch = swallowed
            try:
                raise SystemExit(writer.cli())
            finally:
                (root / 'wait-result').write_text(json.dumps([
                    {'pid': child.pid, 'returncode': child.returncode,
                     'pipes_closed': all(stream is None or stream.closed for stream in
                                         (child.stdin, child.stdout, child.stderr))}
                    for child in children
                ]))
            """))
        self.env['TT_TMUX_BIN'] = str(self.collector)
        self.source.write_text('previous snapshot\n')
        self.history = self.state / 'tt/history/agent-cockpit/previous.tsv'
        self.history.parent.mkdir(parents=True)
        self.history.write_text('previous history\n')
        with writer.acquire_snapshot_lock():
            self.lock_inode = writer.LOCK_PATH.stat().st_ino
        self.preserved = {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
                          for p in (self.source, self.history)}
        self.fixture_pids = []
        self.addCleanup(self.cleanup_collectors)
        self.sentinel = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(60)'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        def finish_sentinel():
            if self.sentinel.poll() is None:
                self.sentinel.terminate()
            self.sentinel.wait(timeout=3)
        self.addCleanup(finish_sentinel)

    def wait_for(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail('owned fixture did not reach its expected state')

    def running(self, pid):
        result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'stat='],
                                capture_output=True, text=True, timeout=1)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode == 0 and not result.stdout.strip().startswith('Z')

    def cleanup_collectors(self):
        grandchild = self.root / 'grandchild-ready'
        if grandchild.exists() and int(grandchild.read_text()) not in self.fixture_pids:
            self.fixture_pids.append(int(grandchild.read_text()))
        if self.fixture_pids and any(self.running(pid) for pid in self.fixture_pids):
            try:
                os.killpg(self.fixture_pids[0], signal.SIGKILL)
            except ProcessLookupError:
                pass
            for pid in self.fixture_pids:
                self.wait_for(lambda: not self.running(pid))

    def start_writer(self, arguments=None, **environment):
        process = subprocess.Popen(
            [sys.executable, '-B', str(self.launcher),
             *(arguments or ['--agent-snapshot', str(self.source)])],
            env={**self.env, **environment}, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
        def finish():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)
        self.addCleanup(finish)
        leader = self.root / 'collector-pid'
        self.wait_for(leader.exists)
        self.fixture_pids = [int(leader.read_text())]
        ready = self.root / 'collector-ready'
        self.wait_for(ready.exists)
        self.fixture_pids = json.loads(ready.read_text())
        self.assertEqual(os.getpgid(self.fixture_pids[0]), self.fixture_pids[0])
        self.assertEqual(os.getpgid(self.fixture_pids[1]), self.fixture_pids[0])
        (self.root / 'test-admitted').write_text('ready')
        return process

    def assert_preserved(self):
        self.assertEqual({p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
                          for p in self.preserved}, self.preserved)
        self.assertEqual(writer.LOCK_PATH.stat().st_ino, self.lock_inode)
        with writer.acquire_snapshot_lock():
            pass

    def assert_finished(self, process, status, diagnostic):
        stdout, stderr = process.communicate(timeout=6)
        self.assertEqual(stdout, '')
        observation = json.loads((self.root / 'wait-result').read_text())
        self.assertEqual(len(observation), 1)
        self.assertEqual(observation[0]['pid'], self.fixture_pids[0])
        self.assertIsNotNone(observation[0]['returncode'], observation)
        self.assertTrue(observation[0]['pipes_closed'], observation)
        for pid in self.fixture_pids:
            self.wait_for(lambda: not self.running(pid))
        self.assertEqual(process.returncode, status, stderr)
        self.assertIn(diagnostic, stderr)
        self.assertIsNone(self.sentinel.poll())
        self.assert_preserved()

    def test_sigterm_reaps_collector_and_stops_its_grandchild(self):
        process = self.start_writer()
        process.send_signal(signal.SIGTERM)
        self.assert_finished(process, 143, 'SIGTERM')

    def test_sigint_reaps_collector_and_stops_its_grandchild(self):
        process = self.start_writer()
        process.send_signal(signal.SIGINT)
        self.assert_finished(process, 130, 'SIGINT')

    def test_control_query_uses_the_same_cancellation_boundary(self):
        process = self.start_writer(['--control-query', 'server-pid'])
        process.send_signal(signal.SIGTERM)
        self.assert_finished(process, 143, 'SIGTERM')

    def test_recovery_query_uses_the_same_cancellation_boundary(self):
        proof = self.root / 'completion.json'
        proof.write_text('{}')
        process = self.start_writer(['--verify-cold-completion', str(proof)])
        process.send_signal(signal.SIGINT)
        self.assert_finished(process, 130, 'SIGINT')

    def test_repeated_signals_during_cleanup_keep_first_status(self):
        process = self.start_writer()
        process.send_signal(signal.SIGINT)
        self.wait_for((self.root / 'term-seen').exists)
        for signum in (signal.SIGTERM, signal.SIGALRM, signal.SIGINT):
            process.send_signal(signum)
        self.assert_finished(process, 130, 'SIGINT')

    def test_sigterm_during_constructor_handoff_waits_for_ownership(self):
        process = self.start_writer(FIXTURE_LAUNCH_SIGNALS=f'{signal.SIGTERM},{signal.SIGINT}')
        self.assert_finished(process, 143, 'SIGTERM')

    def test_sigint_during_constructor_handoff_waits_for_ownership(self):
        process = self.start_writer(FIXTURE_LAUNCH_SIGNALS=str(signal.SIGINT))
        self.assert_finished(process, 130, 'SIGINT')

    def test_alarm_during_constructor_handoff_waits_for_ownership(self):
        process = self.start_writer(FIXTURE_LAUNCH_SIGNALS=str(signal.SIGALRM))
        self.assert_finished(process, 1, 'timed out collecting')

    def test_total_deadline_stops_descendant_after_leader_exits(self):
        process = self.start_writer(TT_SNAPSHOT_TOTAL_TIMEOUT_SECONDS='1')
        self.assert_finished(process, 1, 'timed out collecting')

    def test_command_timeout_stops_descendant_after_leader_exits(self):
        process = self.start_writer(['--control-query', 'server-pid'],
                                    TT_SNAPSHOT_COMMAND_TIMEOUT_SECONDS='1')
        self.assert_finished(process, 2, 'timed out collecting')

    def test_signal_during_timeout_cleanup_keeps_timeout_diagnostic(self):
        process = self.start_writer(['--control-query', 'server-pid'],
                                    TT_SNAPSHOT_COMMAND_TIMEOUT_SECONDS='1')
        self.wait_for((self.root / 'term-seen').exists)
        process.send_signal(signal.SIGTERM)
        process.send_signal(signal.SIGALRM)
        self.assert_finished(process, 2, 'timed out collecting')

    def test_unexpected_communication_exception_cleans_up(self):
        process = self.start_writer(FIXTURE_EXCEPTION='runtime')
        self.assert_finished(process, 1, 'fixture communication failed')

    def test_signal_at_cleanup_entry_preserves_failure_and_reaps(self):
        process = self.start_writer(FIXTURE_EXCEPTION='runtime',
                                    FIXTURE_CLEANUP_SIGNAL=str(signal.SIGTERM))
        self.assert_finished(process, 1, 'fixture communication failed')

    def test_alarm_at_cleanup_entry_preserves_timeout_and_reaps(self):
        process = self.start_writer(FIXTURE_EXCEPTION='timeout',
                                    FIXTURE_CLEANUP_SIGNAL=str(signal.SIGALRM))
        self.assert_finished(process, 1, 'timed out collecting')

    def test_sigint_at_cleanup_entry_preserves_decoder_failure_and_reaps(self):
        process = self.start_writer(FIXTURE_EXCEPTION='unicode',
                                    FIXTURE_CLEANUP_SIGNAL=str(signal.SIGINT))
        self.assert_finished(process, 1, 'collector returned invalid UTF-8')

    def test_optional_launch_error_does_not_swallow_pending_cancellation(self):
        self.assert_cli_cancellation(FIXTURE_OPTIONAL_MISSING='pending')

    def test_optional_launch_return_is_outside_exception_handler(self):
        self.assert_cli_cancellation(FIXTURE_OPTIONAL_MISSING='checkpoint')

    def test_successful_dispatch_delivers_cancellation_after_swallowed_error(self):
        self.assert_cli_cancellation(FIXTURE_SWALLOWED_ERROR='1')

    def assert_cli_cancellation(self, **environment):
        result = subprocess.run(
            [sys.executable, '-B', str(self.launcher)],
            env={**self.env, **environment},
            capture_output=True, text=True, timeout=3,
        )
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertIn('SIGTERM', result.stderr)
        self.assertFalse((self.root / 'optional-returned').exists())
        self.assertEqual(json.loads((self.root / 'wait-result').read_text()), [])
        self.assert_preserved()

    def test_early_decoder_exception_cleans_up(self):
        process = self.start_writer(FIXTURE_EXCEPTION='unicode')
        self.assert_finished(process, 1, 'collector returned invalid UTF-8')

    def test_system_exit_during_communication_cleans_up(self):
        process = self.start_writer(FIXTURE_EXCEPTION='system')
        self.assert_finished(process, 23, '')

    def test_keyboard_interrupt_during_communication_cleans_up(self):
        process = self.start_writer(FIXTURE_EXCEPTION='keyboard')
        self.assert_finished(process, -signal.SIGINT, 'KeyboardInterrupt')

    def test_native_utf8_failure_does_not_signal_a_reaped_group(self):
        children = []
        original = subprocess.Popen
        def launch(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        with patch.object(writer.subprocess, 'Popen', side_effect=launch), patch.object(
            writer.os, 'killpg', wraps=os.killpg
        ) as killpg, self.assertRaisesRegex(writer.SnapshotError, 'invalid UTF-8'):
            writer.run([sys.executable, '-c', "import os; os.write(1, b'\\xff')"])
        self.assertEqual(children[0].returncode, 0)
        self.assertTrue(children[0].stdout.closed)
        killpg.assert_not_called()

    def test_normal_text_and_optional_collector_behavior_is_preserved(self):
        output = writer.run([sys.executable, '-c',
                             "import sys; data=sys.stdin.read(); "
                             "sys.stdout.write(data); sys.stdout.write('a\\r\\nb\\rc\\n')"],
                            input_text='hello\n')
        self.assertEqual(output, 'hello\na\nb\nc\n')
        self.assertEqual(writer.run([str(self.root / 'missing')], required=False), '')
        self.assertEqual(writer.run(['/usr/bin/false'], required=False), '')
        with self.assertRaisesRegex(writer.SnapshotError, 'missing collector'):
            writer.run([str(self.root / 'missing')])


class ShellRecordFeedTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.manifest = self.root / "manifest.tsv"
        self.manifest.write_text("")
        self.source = (REPO / "bin/tt").read_text()
        self.bash = os.environ.get("TMUX_CI_BASH") or shutil.which("bash") or "/bin/bash"

    def shell(self, names, script, **values):
        functions = []
        for name in names:
            prefix = f"{name}() {{\n"
            self.assertEqual(self.source.count("\n" + prefix), 1, name)
            functions.append(prefix + self.source.split("\n" + prefix, 1)[1].split("\n}\n", 1)[0] + "\n}\n")
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.root), "LC_ALL": "C",
               "MANIFEST": str(self.manifest), **values}
        code = "set -euo pipefail\n" + "\n".join(functions) + "\n" + script
        with subprocess.Popen(
            [self.bash, "--noprofile", "--norc", "-c", code], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        ) as child:
            try:
                stdout, stderr = child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate(timeout=1)
                self.fail("synthetic record reader timed out")
        return subprocess.CompletedProcess([], child.returncode, stdout, stderr)

    def assert_ok(self, result, expected):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)
        self.assertEqual(result.stderr, "")

    def collector(self, rows):
        return self.shell(
            ["tsv_records", "manifest_value_for", "fallback_title_for_path",
             "collect_agent_cockpit_state"],
            """
agent_cockpit_manifest() { printf '%s\\n' "$MANIFEST"; }
agent_cockpit_state_file() { printf '%s\\n' /fixture/unused; }
fake_tmux() { [[ "$1" == list-panes ]]; printf '%s' "$PANE_ROWS"; }
TMUX_BIN=fake_tmux
collect_agent_cockpit_state
""", PANE_ROWS=rows,
        )

    def test_menu_exposes_supported_cyan_and_violet_markers(self):
        result = self.shell(["show_pane_menu"], """
tt_self_command() { printf 'tt'; }
shell_quote() { printf '%s' "$1"; }
fake_tmux() { printf '%s\\n' "$@"; }
TMUX_BIN=fake_tmux
show_pane_menu %7 P P fixture-client
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        menu = result.stdout.splitlines()
        for color, key in (("cyan", "5"), ("violet", "6")):
            offset = menu.index("Marker " + color)
            self.assertEqual(menu[offset + 1], key)
            self.assertIn("tt marker set #{pane_id} " + color, menu[offset + 2])
        result = self.shell(
            ["pane_marker_color", "pane_marker_window_style",
             "pane_marker_active_style", "apply_pane_marker_style"], """
fake_tmux() { printf '%s\\n' "$*"; }
TMUX_BIN=fake_tmux
apply_pane_marker_style %7 cyan
apply_pane_marker_style %7 violet
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        for marker, color in (("cyan", "#7dcfff"), ("violet", "#bb9af7")):
            self.assertIn("set-option -pt %7 @tt_marker " + marker, result.stdout)
            self.assertIn("set-option -pt %7 @tt_marker_color " + color, result.stdout)

    def test_recovery_config_keeps_literal_shell_placeholders(self):
        result = self.shell(["recovery_server_config"], "recovery_server_config\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.encode()), 2546)
        # This native recovery config must retain every quote and shell placeholder.
        self.assertEqual(hashlib.sha256(result.stdout.encode()).hexdigest(),
                         "94438dc50c3e948784a1f00d3bdf9758206eacb07ede1cacf2f74c8274eb2832")

    def test_collector_preserves_large_rows_empty_fields_and_filters(self):
        title = "worker " + "x" * 4096
        selected = ["fixture", "studio", "1", "1", "2", "/fixture/work dir",
                    "never execute; false", "zsh", "live", title]
        rows = [selected, ["empty", "", "1", "2", "6", "/fixture/empty", "", "sh", "", ""]]
        for field, value in ((0, "ttm-hidden"), (1, "ops"), (1, "mobile"), (2, "2")):
            excluded = selected.copy()
            excluded[field] = value
            rows.append(excluded)
        expected = "# session\tpane\tdir\ttitle\tcommand\n"
        expected += f"fixture\t1\t/fixture/work dir\t{title}\t\nempty\t2\t/fixture/empty\tempty\t\n"
        self.assert_ok(self.collector("".join("\t".join(row) + "\n" for row in rows)), expected)

    def test_collector_rejects_malformed_late_row_before_emitting_header(self):
        valid = "fixture\tstudio\t1\t1\t6\t/fixture\t\tsh\t" + "x" * 4096 + "\t\n"
        for invalid in ("bad\trow\n", "bad\rfield\tstudio\t1\t1\t6\t/fixture\t\tsh\t\t\n",
                        "bad\x1cfield\tstudio\t1\t1\t6\t/fixture\t\tsh\t\t\n"):
            with self.subTest(invalid=invalid):
                result = self.collector(valid + invalid)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn("invalid snapshot fields", result.stderr)

    def test_manifest_lookup_keeps_empty_fields_and_early_return(self):
        title = "seed " + "x" * 4096
        self.manifest.write_text(f"fixture\t1\t/fixture/work dir\t{title}\t\n" * 8)
        for field, expected in (("dir", "/fixture/work dir\n"), ("title", title + "\n"), ("command", "\n")):
            with self.subTest(field=field):
                result = self.shell(
                    ["tsv_records", "manifest_value_for"],
                    'manifest_value_for "$MANIFEST" fixture 1 "$FIELD"\n', FIELD=field,
                )
                self.assert_ok(result, expected)

    def test_manifest_metadata_keeps_assignments_in_current_shell(self):
        rows = [f"fixture\t{i}\t/fixture/work dir\t{'x' * 100}\t" for i in range(1, 41)]
        self.manifest.write_text("\n".join(rows) + "\n")
        result = self.shell(["tsv_records", "apply_agent_manifest_metadata"], """
calls=0
agent_kind_for_command() { printf '%s\\n' shell; }
fake_tmux() { calls=$((calls + 1)); }
set_pane_git_label() { calls=$((calls + 1)); }
TMUX_BIN=fake_tmux
apply_agent_manifest_metadata "$MANIFEST"
printf '%s\\n' "$calls"
""")
        self.assert_ok(result, "160\n")

    def test_dry_restore_keeps_match_count_and_never_executes_saved_commands(self):
        rows = [f"fixture:1.{i}\tcodex\t/fixture\t{'x' * 100}\tsh\tid\texact\tnever execute"
                for i in range(1, 41)]
        self.manifest.write_text("\n".join(rows) + "\n")
        result = self.shell(["tsv_records", "snapshot_records", "codex_restore_matches",
                             "codex_restore_execute"], 'codex_restore_execute all "" "$MANIFEST"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 40)
        self.assertEqual(result.stderr, "dry run; add --execute to respawn matching panes\n")

    def test_recovery_guards_deduplicate_and_stop_on_denial(self):
        sessions = ["first", "second", "third"]
        self.manifest.write_text("".join(
            f"{session}\t{i}\t/fixture\t{'x' * 100}\t\n"
            for session in sessions for i in range(1, 11)
        ))
        for name, dependency in (
            ("require_recovery_manifest_sessions_absent", "require_recovery_session_absent"),
            ("require_operator_manifest_scopes", "require_operator_topology_scope"),
        ):
            with self.subTest(name=name):
                result = self.shell(["tsv_records", name], f"""
{dependency}() {{ local session="${{@: -1}}"; printf '%s\\n' "$session"; [[ "$session" != second ]]; }}
{name} "$MANIFEST"
""")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "first\nsecond\n")
                self.assertEqual(result.stderr, "")

    def test_recovery_completion_feeds_every_verified_window(self):
        fake_writer = self.root / "tt-codex-snapshot-writer"
        fake_writer.write_text(
            "#!/bin/sh\nif [ \"$#\" -eq 2 ]; then printf '1\\n'; else printf '%s' \"$WINDOWS\"; fi\n"
        )
        fake_writer.chmod(0o700)
        windows = "".join(f"@{i}\n" for i in range(1, 161))
        result = self.shell(["finish_recovery_setup"], """
TMUX_BIN=unused
count=0
apply_recovery_theme() { return 99; }
install_agent_autosave_hooks() { return 99; }
apply_studio_pane_titles() { count=$((count + 1)); }
finish_recovery_setup unused
printf '%s\\n' "$count"
""", SCRIPT_DIR=str(self.root), WINDOWS=windows)
        self.assert_ok(result, "160\n")

    def test_long_scope_list_keeps_exact_match(self):
        scopes = ",".join(f"recover:fixture-{i}" for i in range(100))
        result = self.shell(["topology_scope_matches"], """
topology_scope_matches recover:fixture-99
if topology_scope_matches recover:fixture; then exit 99; fi
printf 'matched\\n'
""", TT_OPERATOR_TMUX_SCOPE=scopes)
        self.assert_ok(result, "matched\n")

    def test_long_timer_identity_keeps_owner_checks_and_stop_order(self):
        token = "token-" + "x" * 4096
        identity = f"1\t2\t42\t{token}\t100\t30"
        names = [
            "codex_snapshot_timer_success_fresh", "codex_snapshot_timer_owner_alive",
            "codex_snapshot_timer_identity_valid", "codex_snapshot_timer_state_available",
            "stop_codex_snapshot_timer_locked", "codex_snapshot_timer_stop_identity",
            "codex_snapshot_timer_same_owner",
        ]
        result = self.shell(names, """
stopping=0
id() { printf '42\\n'; }
date() { printf '110\\n'; }
kill() { if [[ "$1" == -TERM ]]; then stopping=1; else (( stopping == 0 )); fi; }
codex_snapshot_timer_process_uid() { printf '42\\n'; }
codex_snapshot_timer_process_command() { printf 'bash tt codex-snapshot-loop %s 2 42\\n' "$TOKEN"; }
codex_snapshot_timer_has_server_ancestor() { [[ "$1 $2" == "1 2" ]]; }
codex_snapshot_timer_controls_match() { [[ "$1" == 2 && "$2" == "$TOKEN" ]]; }
codex_snapshot_timer_server_pid() { printf '2\\n'; }
codex_snapshot_timer_desired_token() { printf '%s\\n' "$TOKEN"; }
codex_snapshot_timer_read_state() { printf '%s\\n' "$IDENTITY"; }
codex_snapshot_timer_unset_token_if_matches() { [[ "$1" == "$TOKEN" ]]; }
codex_snapshot_timer_success_fresh "$IDENTITY"
[[ "$TT_TIMER_SUCCESS_AGE" == 10 ]]
codex_snapshot_timer_owner_alive "$IDENTITY"
codex_snapshot_timer_identity_valid "$IDENTITY"
codex_snapshot_timer_state_available
[[ "$(stop_codex_snapshot_timer_locked)" == "$IDENTITY" ]]
codex_snapshot_timer_same_owner "$IDENTITY" "$IDENTITY"
if codex_snapshot_timer_same_owner "$IDENTITY" "${IDENTITY/token-/different-}"; then exit 99; fi
codex_snapshot_timer_stop_identity "$IDENTITY"
[[ "$stopping" == 1 ]]
printf 'verified\\n'
""", IDENTITY=identity, TOKEN=token)
        self.assert_ok(result, "verified\n")


if __name__ == "__main__":
    unittest.main()
