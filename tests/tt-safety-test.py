#!/usr/bin/env python3
"""Fixture-only snapshot and recovery checks; run in the reviewed test sandbox."""

import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
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
            "TT_TMUX_BIN": "/bin/false", "LC_ALL": "C.UTF-8",
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
        with self.assertRaises(writer.SnapshotError):
            writer.run(["/bin/sh", "-c", "sleep 10 & wait"])
        self.assertEqual(output.read_text(), "unchanged\n")

    def test_failed_agent_collection_preserves_published_file(self):
        output = self.root / "agents.tsv"
        output.write_text("unchanged\n")
        with patch.object(writer, "run", side_effect=writer.SnapshotError("collector failed")):
            with self.assertRaises(writer.SnapshotError):
                writer.publish_agent_snapshot(output)
        self.assertEqual(output.read_text(), "unchanged\n")


if __name__ == "__main__":
    unittest.main()
