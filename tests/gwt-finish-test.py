#!/usr/bin/env python3
import contextlib
import errno
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
from pathlib import Path
import runpy
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
import uuid
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "bin/agent-worktree-ops/agent-worktree-finish"
SPEC = importlib.util.spec_from_loader(
    "gwt_finish", SourceFileLoader("gwt_finish", str(TOOL))
)
FINISH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FINISH)


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = mock.patch.dict(os.environ, {
            "HOME": str(self.home),
            "CODEX_HOME": str(self.home / ".codex"),
            "CODEX_THREAD_ID": "",
            "GWT_OWNER_ID": "owner-1",
        })
        self.env.start()

        self.repo = self.base / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Fixture")
        git(self.repo, "config", "user.email", "fixture@example.test")
        git(self.repo, "config", "commit.gpgsign", "false")
        git(self.repo, "remote", "add", "origin", "https://github.com/example/repo.git")
        (self.repo / ".gitignore").write_text("ignored\n")
        (self.repo / "file").write_text("original\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "fixture")

        self.root = self.base / "worktrees"
        self.root.mkdir()
        self.wt = self.root / "task"
        git(self.repo, "worktree", "add", "-b", "feature", str(self.wt))
        self.state = self.base / "state"
        self.head = git(self.wt, "rev-parse", "HEAD")
        self.url = "https://github.com/example/repo/pull/1"
        self.proofs = {self.url: self.proof(self.url)}
        self.proof_patch = mock.patch.object(
            FINISH, "pr", side_effect=lambda url: dict(self.proofs[url])
        )
        self.proof_patch.start()
        self.target_patch = mock.patch.object(
            FINISH, "default_target", return_value="main"
        )
        self.target_patch.start()
        self.storage_patch = mock.patch.object(FINISH, "storage_guard")
        self.storage = self.storage_patch.start()
        self.call("enroll")

    def tearDown(self):
        self.storage_patch.stop()
        self.target_patch.stop()
        self.proof_patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def proof(self, url, **kwargs):
        return {
            "url": url,
            "repo": "example/repo",
            "head": self.head,
            "target": "main",
            "merged": True,
            "state": "closed",
            "merge": "a" * 40,
            **kwargs,
        }

    def call(self, command, *args):
        from contextlib import redirect_stdout

        output = io.StringIO()
        with redirect_stdout(output):
            result = FINISH.main([
                command,
                "--worktree", str(self.wt),
                "--state-dir", str(self.state),
                "--managed-root", str(self.root),
                *args,
            ])
        return result, json.loads(output.getvalue() or "null")

    def finish(self, *args):
        return self.call("finish", "--pr", self.url, *args)

    def check_reason(self):
        return self.call("check")[1][0]["reason"]

    def test_finish_and_check_confirm_completion_but_retain_checkout(self):
        result = self.finish()[1][0]
        self.assertEqual(result["state"], "finished")
        self.assertEqual(result["checkout"], "retained")
        self.assertFalse(result["release_available"])
        self.assertFalse(result["removal_available"])
        self.assertEqual(self.check_reason(), "completion-confirmed-checkout-retained")
        self.assertTrue(self.wt.exists())

    def test_cancel_without_pr_retains_dirty_files_and_pins_without_proof(self):
        self.call("pin", "--reason", "recovery")
        (self.wt / "unpublished").write_text("keep me\n")
        self.storage.reset_mock()
        with mock.patch.object(FINISH, "pr") as remote, \
                mock.patch.object(FINISH, "evaluate_release") as removal:
            result = self.call("cancel", "--reason", "superseded upstream")[1][0]
            checked = self.call("check")[1][0]
        remote.assert_not_called()
        removal.assert_not_called()
        self.storage.assert_not_called()
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(checked["reason"], "owner-cancelled-checkout-retained")
        self.assertEqual(result["checkout"], "retained")
        self.assertIsNone(result["pr"])
        self.assertFalse(result["release_available"])
        self.assertEqual(result["pins"], [{"owner": "owner-1", "reason": "recovery"}])
        self.assertEqual((self.wt / "unpublished").read_text(), "keep me\n")

    def test_cancellation_is_owner_local_and_resume_invalidates_it(self):
        self.call("resume", "--owner", "owner-2")
        result = self.call("cancel", "--reason", "no longer needed")[1][0]
        self.assertEqual(result["state"], "active")
        self.assertEqual([item["owner"] for item in result["cancelled_owners"]], ["owner-1"])
        self.call("cancel", "--reason", "done", "--owner", "owner-2")
        self.assertEqual(self.call("status")[1][0]["state"], "cancelled")
        result = self.call("resume")[1][0]
        self.assertEqual(result["state"], "active")
        self.assertEqual([item["owner"] for item in result["cancelled_owners"]], ["owner-2"])

    def test_changed_head_invalidates_cancellation(self):
        self.finish()
        self.call("cancel", "--reason", "superseded")
        git(self.wt, "commit", "--allow-empty", "-m", "new work")
        result = self.call("check")[1][0]
        self.assertNotIn("cancelled_owners", result)
        self.assertEqual(result["state"], "active")

    def test_cancelled_batch_rotates_past_the_first_sixteen_entries(self):
        self.call("cancel", "--reason", "superseded")
        with FINISH.ledger(self.state) as db:
            original = dict(FINISH.get_row(db, self.wt))
            with db:
                for index in range(1, 17):
                    row = {**original, "id": str(uuid.uuid4()),
                           "path": str(self.root / f"task-{index:02d}")}
                    db.execute("INSERT INTO worktrees (" + ",".join(row) + ") VALUES ("
                               + ",".join("?" for _ in row) + ")", tuple(row.values()))
                    db.execute("INSERT INTO owners(worktree_id, owner, completed) VALUES (?, ?, 0)",
                               (row["id"], "owner-1"))
                    db.execute("INSERT INTO owner_cancellations VALUES (?, ?, ?, ?, ?)",
                               (row["id"], "owner-1", self.head, "superseded", 0))
        # Exercise both early-return paths without creating sixteen real clones.
        def probe(row):
            if row["path"].endswith("task-01"):
                raise FINISH.Retain("worktree-identity-changed")
            return {"head": self.head}
        with mock.patch.object(FINISH, "revalidate", side_effect=probe):
            first = self.call("check", "--all")[1]
            second = self.call("check", "--all")[1]
        self.assertEqual(len(first), 16)
        self.assertEqual(second[0]["worktree"], str(self.root / "task-16"))

    def test_unavailable_cancelled_checkout_does_not_abort_batch(self):
        self.call("cancel", "--reason", "superseded")
        first = self.wt
        self.wt = self.root / "task-2"
        git(self.repo, "worktree", "add", "-b", "feature-2", str(self.wt))
        self.call("enroll")
        first.rename(self.root / "moved")
        result = self.call("check", "--all")[1]
        self.assertEqual(len(result), 2)
        unavailable = next(item for item in result if item["worktree"] == str(first))
        self.assertEqual(unavailable["state"], "cancelled")
        self.assertNotEqual(unavailable["reason"], "owner-cancelled-checkout-retained")
        self.assertEqual(unavailable["checkout"], "retained")

    def test_legacy_owner_schema_migrates_before_new_enrollment(self):
        database = self.state / "lifecycle.sqlite"
        with contextlib.closing(sqlite3.connect(database)) as db:
            db.executescript("""
                ALTER TABLE owners RENAME TO owners_current;
                CREATE TABLE owners (
                    worktree_id TEXT NOT NULL REFERENCES worktrees(id),
                    owner TEXT NOT NULL,
                    released INTEGER NOT NULL DEFAULT 0,
                    recovery_reviewed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (worktree_id, owner));
                INSERT INTO owners(worktree_id, owner, released, recovery_reviewed)
                    SELECT worktree_id, owner, 1, 1 FROM owners_current;
                DROP TABLE owners_current;
            """)
        prior = self.wt
        self.wt = self.root / "second"
        git(self.repo, "worktree", "add", "-b", "second", str(self.wt))
        self.call("enroll", "--owner", "owner-2")
        with contextlib.closing(sqlite3.connect(database)) as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(owners)")]
            rows = db.execute(
                "SELECT owner, released, recovery_reviewed, completed "
                "FROM owners ORDER BY owner"
            ).fetchall()
        self.assertIn("completed", columns)
        self.assertEqual(rows, [
            ("owner-1", 1, 1, 0),
            ("owner-2", 0, 0, 0),
        ])
        self.assertTrue(prior.exists())
        self.assertTrue(self.wt.exists())

    def test_resume_invalidates_prior_completion(self):
        self.finish()
        self.call("resume")
        result = self.call("status")[1][0]
        self.assertEqual(result["state"], "active")
        self.assertEqual(result["reason"], "owner-resumed")
        self.assertIsNone(result["pr"])

    def test_every_known_owner_must_finish_the_same_proof(self):
        self.call("resume", "--owner", "owner-2")
        first = self.finish()[1][0]
        self.assertEqual(first["state"], "active")
        self.assertEqual(first["reason"], "awaiting-owner-completion")
        second = self.finish("--owner", "owner-2")[1][0]
        self.assertEqual(second["state"], "finished")
        self.assertEqual(
            [(item["owner"], item["completed"]) for item in second["owners"]],
            [("owner-1", 1), ("owner-2", 1)],
        )

    def test_changed_dependency_proof_resets_prior_owner_completion(self):
        upper = "https://github.com/example/repo/pull/2"
        self.proofs[upper] = self.proof(upper, head="b" * 40)
        self.call("resume", "--owner", "owner-2")
        self.finish()
        changed = self.finish("--owner", "owner-2", "--wait-for", upper)[1][0]
        self.assertEqual(changed["state"], "active")
        self.assertEqual(
            [(item["owner"], item["completed"]) for item in changed["owners"]],
            [("owner-1", 0), ("owner-2", 1)],
        )

    def test_pin_preserves_a_visible_completion_blocker(self):
        self.call("pin", "--reason", "saved recovery")
        self.finish()
        self.assertEqual(self.check_reason(), "recovery-pin-present")
        self.call("unpin", "--reason", "saved recovery")
        self.assertEqual(self.check_reason(), "completion-confirmed-checkout-retained")

    def test_open_and_closed_unmerged_prs_are_distinct(self):
        self.finish()
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        self.assertEqual(self.check_reason(), "pr-not-merged")
        self.proofs[self.url]["state"] = "closed"
        self.assertEqual(self.check_reason(), "pr-closed-unmerged")

    def test_stack_tracks_exact_heads_and_final_target(self):
        upper = "https://github.com/example/repo/pull/2"
        self.proofs[upper] = self.proof(upper, head="b" * 40)
        self.finish("--wait-for", upper)
        self.assertEqual(self.check_reason(), "completion-confirmed-checkout-retained")
        self.proofs[upper]["head"] = "c" * 40
        self.assertEqual(self.check_reason(), "stack-proof-changed-repeat-finish")
        self.call("resume")
        self.proofs[upper].update(head="b" * 40, target="temporary-stack")
        with self.assertRaisesRegex(FINISH.Retain, "stack-pr-not-targeting-final"):
            self.finish("--wait-for", upper)

    def test_wrong_primary_target_and_head_are_rejected(self):
        self.proofs[self.url]["target"] = "stack-base"
        with self.assertRaisesRegex(FINISH.Retain, "final-branch"):
            self.finish()
        self.proofs[self.url].update(target="main", head="b" * 40)
        with self.assertRaisesRegex(FINISH.Retain, "head-mismatch"):
            self.finish()

    def test_head_or_registration_identity_change_is_reported(self):
        self.finish()
        (self.wt / "file").write_text("changed\n")
        git(self.wt, "commit", "-am", "change")
        self.assertEqual(self.check_reason(), "unfinished-or-head-changed")
        git(self.repo, "worktree", "remove", str(self.wt))
        git(self.repo, "worktree", "add", str(self.wt), "feature")
        self.assertEqual(self.check_reason(), "worktree-identity-changed")

    def test_dirty_untracked_and_git_operation_state_are_reported(self):
        self.finish()
        (self.wt / "untracked").write_text("keep")
        self.assertEqual(self.check_reason(), "dirty-or-untracked-worktree")
        (self.wt / "untracked").unlink()
        lock = Path(git(self.wt, "rev-parse", "--absolute-git-dir")) / "index.lock"
        lock.write_text("held")
        self.assertEqual(self.check_reason(), "git-lock-present")

    def test_github_request_is_explicit_uncached_and_shell_free(self):
        self.proof_patch.stop()
        payload = {
            "number": 1,
            "head": {"sha": self.head},
            "base": {"ref": "main", "repo": {"full_name": "example/repo"}},
            "merged": True,
            "merged_at": "2026-09-14T00:00:00Z",
            "state": "closed",
            "merge_commit_sha": "a" * 40,
        }
        completed = subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b"")
        with mock.patch.object(FINISH, "run", return_value=completed) as run:
            self.assertTrue(FINISH.pr(self.url)["merged"])
            run.assert_called_once_with([
                "ghx", "--no-cache", "api", "repos/example/repo/pulls/1"
            ])
        with self.assertRaises(FINISH.Retain):
            FINISH.pr(self.url + ";touch bad")
        self.proof_patch.start()

    def test_final_revalidation_catches_a_late_write(self):
        self.finish()
        original = FINISH.local_checks
        calls = 0

        def write_before_final_check(row):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.wt / "late evidence").write_text("keep")
            return original(row)

        with mock.patch.object(
            FINISH, "local_checks", side_effect=write_before_final_check
        ):
            self.assertEqual(self.check_reason(), "dirty-or-untracked-worktree")
        self.assertTrue((self.wt / "late evidence").exists())

    def test_runtime_thread_identity_overrides_ambient_owner(self):
        args = FINISH.parser().parse_args(["status"])
        with mock.patch.dict(os.environ, {
            "CODEX_THREAD_ID": "live-thread",
            "GWT_OWNER_ID": "stale-shell",
        }):
            self.assertEqual(FINISH.owner_id(args), "live-thread")
            args.owner = "another-thread"
            with self.assertRaisesRegex(FINISH.Retain, "conflicts-with-live-thread"):
                FINISH.owner_id(args)

    def test_inherited_git_context_cannot_redirect_proof(self):
        self.finish()
        env = {
            "GIT_DIR": str(self.repo / ".git"),
            "GIT_WORK_TREE": str(self.repo),
            "GIT_INDEX_FILE": str(self.base / "foreign-index"),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.worktree",
            "GIT_CONFIG_VALUE_0": str(self.repo),
        }
        (self.wt / "file").write_text("dirty in actual target")
        with mock.patch.dict(os.environ, env):
            self.assertEqual(self.check_reason(), "dirty-or-untracked-worktree")
        self.assertFalse((self.base / "foreign-index").exists())

    def test_git_proofs_deny_transport(self):
        with mock.patch.dict(os.environ, {"GIT_ALLOW_PROTOCOL": "file"}):
            result = FINISH.run(
                ["git", "-c", "protocol.file.allow=always", "ls-remote", str(self.repo)],
                allowed=(128,),
            )
        self.assertIn(b"transport 'file' not allowed", result.stderr)

    def test_symlink_replacement_and_api_failure_retain(self):
        self.finish()
        self.proof_patch.stop()
        with mock.patch.object(
            FINISH, "pr", side_effect=FINISH.Retain("ghx-failed")
        ):
            self.assertEqual(self.check_reason(), "ghx-failed")
        self.proof_patch.start()
        moved = self.root / "moved"
        self.wt.rename(moved)
        self.wt.symlink_to(moved, target_is_directory=True)
        self.assertEqual(self.check_reason(), "registration-missing-or-locked")
        self.assertTrue((moved / "file").exists())

    def test_policy_fifo_is_rejected_before_open_and_swap_is_nonblocking(self):
        policy = self.base / "fifo-policy"
        os.mkfifo(policy, 0o600)
        with mock.patch.object(FINISH.os, "open") as opened:
            with self.assertRaisesRegex(FINISH.Retain, "private-report-policy"):
                FINISH.report_policy(policy)
            opened.assert_not_called()
        policy.unlink()
        policy.write_text("{}")
        policy.chmod(0o600)
        original = os.open
        def swapped(path, flags, *args, **kwargs):
            policy.unlink()
            os.mkfifo(policy, 0o600)
            self.assertTrue(flags & os.O_NONBLOCK)
            return original(path, flags, *args, **kwargs)
        with mock.patch.object(FINISH.os, "open", side_effect=swapped):
            with self.assertRaisesRegex(FINISH.Retain, "file-replaced"):
                FINISH.report_policy(policy)

    def test_policy_reader_inherits_operation_deadline(self):
        policy = self.base / "deadline-policy"
        policy.write_text("{}")
        policy.chmod(0o600)
        with mock.patch.object(FINISH.safety(), "DEADLINE", 0), mock.patch.object(FINISH.os, "open") as opened:
            with self.assertRaisesRegex(FINISH.Retain, "deadline"):
                FINISH.report_policy(policy)
            opened.assert_not_called()

    def test_report_policy_filters_check_all_without_apply_authority(self):
        self.finish()
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            recorded = json.loads(row["identity"])
        policy = self.base / "policy.json"

        def write_policy(repository, mode="report-only"):
            policy.write_text(json.dumps({
                "schema": FINISH.POLICY_SCHEMA,
                "host": "fixture-host",
                "mode": mode,
                "repositories": [repository],
            }))
            policy.chmod(0o600)

        matching = {
            "owner": recorded["owner"],
            "root": recorded["root"],
            "common_id": recorded["common_id"],
        }
        output = io.StringIO()
        from contextlib import redirect_stdout
        with mock.patch.object(FINISH, "host_key", return_value="fixture-host"):
            write_policy(matching)
            with redirect_stdout(output):
                self.assertEqual(FINISH.main([
                    "check", "--all",
                    "--state-dir", str(self.state),
                    "--policy", str(policy),
                ]), 0)
            self.assertEqual(
                json.loads(output.getvalue())[0]["reason"],
                "completion-confirmed-checkout-retained",
            )

            output = io.StringIO()
            write_policy({**matching, "owner": str(self.base / "other-repo")})
            with redirect_stdout(output):
                self.assertEqual(FINISH.main([
                    "check", "--all",
                    "--state-dir", str(self.state),
                    "--policy", str(policy),
                ]), 0)
            self.assertEqual(json.loads(output.getvalue()), [])

            write_policy(matching, mode="apply")
            with mock.patch.object(FINISH, "check") as check:
                with self.assertRaisesRegex(FINISH.Retain, "invalid-report-policy"):
                    FINISH.main([
                        "check", "--all",
                        "--state-dir", str(self.state),
                        "--policy", str(policy),
                    ])
                check.assert_not_called()


class WrapperTests(unittest.TestCase):
    def test_unavailable_release_remove_and_apply_need_no_state(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            for args, reason in (
                (("release",), "not-enrolled"),
                (("remove",), "use-explicit-owner-release"),
                (("check", "--apply"), "not-enrolled"),
            ):
                result = subprocess.run(
                    [str(TOOL), *args, "--state-dir", str(state)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn(reason, result.stderr)
                self.assertFalse(state.exists())
            result = subprocess.run(
                [
                    str(TOOL), "check",
                    "--policy", str(Path(temp) / "policy.json"),
                    "--state-dir", str(state),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("report-policy-requires-check-all", result.stderr)
            self.assertFalse(state.exists())

    def test_holder_qualification_is_explicitly_unavailable_and_probe_free(self):
        from contextlib import redirect_stdout

        output = io.StringIO()
        with mock.patch.object(FINISH, "ledger") as ledger, \
                mock.patch.object(FINISH, "run") as run, redirect_stdout(output):
            self.assertEqual(FINISH.main(["holder-qualification"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {
            "schema": FINISH.HOLDER_SCHEMA,
            "backend": "darwin-libproc-cooperative.v1",
            "qualified": False,
            "reason": "holder-mapping-coverage-unqualified",
            "mapping_coverage": "unqualified",
            "platform": FINISH.safety().platform_key(),
        })
        ledger.assert_not_called()
        run.assert_not_called()

    def test_absent_status_and_check_are_side_effect_free(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            for args in (("status", "--all"), ("check", "--all")):
                result = subprocess.run(
                    [str(TOOL), *args, "--state-dir", str(state)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "[]\n")
                self.assertFalse(state.exists())

    def test_gwt_wrapper_creates_private_locked_entry_and_parks_only_its_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            repo = base / "repo"
            repo.mkdir()
            git(repo, "init", "-b", "main")
            git(repo, "config", "user.name", "Fixture")
            git(repo, "config", "user.email", "fixture@example.test")
            (repo / "file").write_text("fixture\n")
            git(repo, "add", ".")
            git(repo, "commit", "-m", "fixture")
            root = base / "worktrees"
            calls = base / "calls"
            fake = base / "finish"
            fake.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$CALLS\"\n"
                "if [ \"$1\" = creation-token ]; then printf '11111111-1111-4111-8111-111111111111\\n'; exit; fi\n"
                "printf '[{\"checkout\":\"retained\","
                "\"release_available\":false,\"removal_available\":false}]\\n'\n"
            )
            fake.chmod(0o755)
            script = r'''
source "$1"
_gwt_require_worktree_storage() { return 0; }
_gwt_finish_tool() { "$FAKE_FINISH" "$@"; }
_gwt_claim_if_enrolled() { _gwt_finish_tool resume --if-enrolled --worktree "$1"; }
_gwt_tmux_sync_context() { return 0; }
cd "$2"
gwt new feature main --full --finish-managed || exit
[[ "$PWD" == "$3" ]] || exit 91
gwt finish --pr https://github.com/example/repo/pull/1 || exit
_gwt_finish_tool() { return 1; }
_gwt_tmux_sync_context() { exit 98; }
if gwt cancel --release --reason invalid; then exit 97; fi
[[ "$PWD" == "$3" ]] || exit 99
_gwt_finish_tool() { "$FAKE_FINISH" "$@"; }
_gwt_tmux_sync_context() { return 0; }
gwt release --worktree "$3" || exit 92
[[ "$PWD" == "$2" ]] || exit 96
cd "$2"
gwt cd feature || exit
[[ "$PWD" == "$3" ]] || exit 94
cd "$2"
gwt new feature main --full || exit
[[ "$PWD" == "$3" ]] || exit 95
cd "$2"
if gwt new feature main --full --finish-managed; then exit 93; fi
'''
            env = dict(
                os.environ,
                HOME=str(base / "home"),
                CALLS=str(calls),
                FAKE_FINISH=str(fake),
                DOTFILES_WORKTREES_ROOT=str(root),
                TMUX="",
            )
            target = root / "repo" / "feature"
            result = subprocess.run(
                [
                    "zsh", "-f", "-c", script, "zsh",
                    str(ROOT / "functions/gwt/gwt.zsh"),
                    str(repo),
                    str(target),
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            recorded = calls.read_text()
            self.assertIn("enroll --worktree", recorded)
            self.assertIn("finish --pr", recorded)
            self.assertGreaterEqual(recorded.count("resume --if-enrolled"), 2)
            self.assertIn("release --worktree", recorded)
            self.assertTrue(target.exists())
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            admin = Path(git(target, "rev-parse", "--absolute-git-dir"))
            self.assertEqual(admin.stat().st_mode & 0o777, 0o700)
            self.assertEqual((admin / "locked").read_text().strip(),
                             "gwt-finish.v2:11111111-1111-4111-8111-111111111111")


class ReleaseTests(unittest.TestCase):
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    finish = LifecycleTests.finish
    check_reason = LifecycleTests.check_reason

    def setUp(self):
        LifecycleTests.setUp(self)
        self.legacy = self.wt
        self.wt = self.root / "released-task"
        self.token = str(uuid.uuid4())
        old_mask = os.umask(0o077)
        try:
            git(self.repo, "worktree", "add", "--lock", "--reason", "gwt-finish.v2:" + self.token,
                "-b", "released-feature", str(self.wt))
        finally:
            os.umask(old_mask)
        self.call("enroll", "--new-token", self.token)
        self.admin = Path(git(self.wt, "rev-parse", "--absolute-git-dir"))
        (self.home / ".codex").mkdir(mode=0o700)
        self.native = mock.patch.object(FINISH.safety(), "Darwin")
        self.observer = self.native.start().return_value
        # Complete coverage is synthetic lifecycle proof, never native qualification.
        self.observer.observe.return_value = {
            "holders": [], "processes": 1, "mapping_coverage": "complete"}
        self.capability = mock.patch.object(FINISH, "holder_qualification", return_value={
            "qualified": True, "mapping_coverage": "complete"})
        self.capability.start()
        self.boundary = mock.patch.object(FINISH.safety(), "access_boundary", return_value=[])
        self.boundary.start()
        self.policy = mock.patch.object(FINISH, "qualified_policy", return_value={
            "git": {"path": str(Path(shutil.which("git")).resolve())}, "known_processes": []})
        self.policy.start()

    def tearDown(self):
        self.capability.stop()
        self.policy.stop()
        self.boundary.stop()
        self.native.stop()
        LifecycleTests.tearDown(self)

    def row(self):
        with FINISH.ledger(self.state) as db:
            return dict(FINISH.get_row(db, self.wt))

    def test_explicit_release_removes_fresh_merged_task_and_preserves_branch_and_sibling(self):
        result = self.finish("--release")[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.admin.exists())
        self.assertTrue(self.legacy.exists())
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertEqual(result["retirement_state"], "removed")
        with self.assertRaisesRegex(FINISH.Retain, "intent"):
            self.call("resume")

    def test_plain_finish_retains_until_owner_explicitly_releases(self):
        result = self.finish()[1][0]
        self.assertEqual(result["reason"], "awaiting-owner-release")
        self.assertTrue(self.wt.exists())
        self.assertEqual(self.call("release")[1][0]["checkout"], "removed")

    def test_unqualified_apply_preserves_release_without_admission_work(self):
        self.capability.stop()
        with mock.patch.object(FINISH.safety(), "maintainer_lock") as lock, \
                mock.patch.object(FINISH, "completion_checks") as completion, \
                mock.patch.object(FINISH, "deletion_admission") as admission:
            for result in (self.finish("--release")[1][0], self.call("check", "--apply")[1][0]):
                self.assertEqual(result["reason"], "holder-mapping-coverage-unqualified")
                self.assertEqual(result["checkout"], "retained")
                self.assertEqual(result["released_owners"], ["owner-1"])
                self.assertTrue(result["owners"][0]["completed"])
                self.assertEqual(result["retirement_state"], "enrolled")
            lock.assert_not_called()
            completion.assert_not_called()
            admission.assert_not_called()
            self.observer.observe.assert_not_called()
        with FINISH.ledger(self.state) as db:
            item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
            self.assertIsNotNone(item["inventory"])
            self.assertIsNone(item["intent"])
            self.assertIsNone(item["result"])
        with mock.patch.object(FINISH, "completion_checks", wraps=FINISH.completion_checks) as completion:
            self.assertEqual(self.call("check")[1][0]["reason"],
                             "released-completion-confirmed-checkout-retained")
            completion.assert_called_once()
        self.assertTrue(self.wt.exists())
        self.assertTrue((self.admin / "locked").exists())

    def test_cancel_invalidates_old_client_release_and_is_idempotent(self):
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        self.finish("--release")
        result = self.call("cancel", "--reason", "superseded")[1][0]
        self.assertEqual(result["released_owners"], [])
        self.assertFalse(result["owners"][0]["completed"])
        again = self.call("cancel", "--reason", "superseded")[1][0]
        self.assertEqual(result, again)
        self.proofs[self.url].update(merged=True, state="closed", merge="a" * 40)
        self.assertEqual(self.call("check", "--apply")[1][0]["checkout"], "retained")
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            # An old client cannot see cancellations but uses this same SQL.
            with db:
                db.execute("UPDATE owners SET completed=0 WHERE worktree_id=?", (row["id"],))
            self.assertEqual(FINISH.cancellation_rows(db, row["id"]), [])

    def test_cancel_refuses_pending_intent_and_completion_options(self):
        for arguments in (("--release",), ("--pr", self.url), ("--apply",)):
            with self.assertRaisesRegex(FINISH.Retain, "completion-or-removal"):
                self.call("cancel", "--reason", "superseded", *arguments)
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            with db:
                db.execute("UPDATE retirement SET state='incomplete' WHERE worktree_id=?", (row["id"],))
        with self.assertRaisesRegex(FINISH.Retain, "removal-intent"):
            self.call("cancel", "--reason", "superseded")
        self.assertNotIn("cancelled_owners", self.call("status")[1][0])

    def test_pending_merge_is_revisited_by_same_consumer_without_age(self):
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        self.assertEqual(self.finish("--release")[1][0]["reason"], "pr-not-merged")
        self.assertTrue(self.wt.exists())
        self.proofs[self.url].update(merged=True, state="closed", merge="a" * 40)
        self.assertEqual(self.call("check", "--apply")[1][0]["checkout"], "removed")

    def test_actual_parent_holder_blocks_but_alive_outside_parent_need_not_exit(self):
        self.observer.observe.return_value = {
            "holders": [{"kind": "cwd"}], "processes": 2, "mapping_coverage": "unqualified"}
        self.assertEqual(self.finish("--release")[1][0]["reason"], "pending-departure")
        self.assertTrue(self.wt.exists())
        self.observer.observe.return_value = {
            "holders": [], "processes": 2, "mapping_coverage": "complete"}
        self.assertEqual(self.call("check", "--apply")[1][0]["checkout"], "removed")

    def test_external_qualification_cannot_override_incomplete_mapping_observation(self):
        sources = {name: hashlib.sha256((TOOL.parent / name).read_bytes()).hexdigest()
                   for name in ("agent-worktree-finish", "gwt_finish_safety.py", "agent-worktree-clean",
                                "agent-worktree-maintain", "agent-worktree-purge", "worktree-storage-guard")}
        native = str(Path(shutil.which("git")).resolve())
        platform = FINISH.safety().platform_key()
        receipt = self.base / "synthetic-activation.json"
        # This deliberately asserts external completeness. It is fixture data,
        # not a native control; only the reader can attest actual coverage.
        receipt.write_text(json.dumps({
            "schema": "gwt-finish-activation.v2", "host": "fixture-host",
            "sources": sources, "platform": platform, "configured_consumers_reviewed": True,
            "old_loaded_consumers": [], "native_control_passed": True, "mapping_coverage": "complete"}))
        receipt.chmod(0o600)
        qualification = {
            "platform": platform, "sources": sources, "known_processes": [],
            "git": {"path": native, "sha256": hashlib.sha256(Path(native).read_bytes()).hexdigest(),
                    "version": "git version 2.55.0"},
            "activation_receipt": {"path": str(receipt), "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()}}
        recorded = json.loads(self.row()["identity"])
        policy = self.base / "synthetic-policy.json"
        policy.write_text(json.dumps({
            "schema": FINISH.APPLY_POLICY_SCHEMA, "host": "fixture-host", "mode": "explicit-release",
            "repositories": [{key: recorded[key] for key in ("owner", "root", "common_id")}],
            "qualification": qualification}))
        policy.chmod(0o600)
        real_run, real_supervise = FINISH.run, FINISH.safety().supervise
        def qualified_version(command, **kwargs):
            if command == [native, "--version"]:
                return subprocess.CompletedProcess(command, 0, b"git version 2.55.0\n", b"")
            return real_run(command, **kwargs)
        def no_removal(command, **kwargs):
            self.assertFalse("worktree" in command and "remove" in command,
                             "incomplete native coverage reached removal")
            return real_supervise(command, **kwargs)
        self.policy.stop()
        try:
            with mock.patch.object(FINISH, "host_key", return_value="fixture-host"), \
                    mock.patch.object(FINISH, "run", side_effect=qualified_version), \
                    mock.patch.object(FINISH.safety(), "supervise", side_effect=no_removal):
                args = FINISH.parser().parse_args(["finish", "--policy", str(policy)])
                self.assertEqual(FINISH.qualified_policy(args, self.row()), qualification)
                for observation, reason in (
                        ({"holders": []}, "holder-mapping-coverage-unqualified"),
                        ({"holders": [], "mapping_coverage": "unqualified"}, "holder-mapping-coverage-unqualified"),
                        ({"holders": [], "mapping_coverage": True}, "holder-mapping-coverage-unqualified"),
                        ({"holders": [{"kind": "fd"}], "mapping_coverage": "unqualified"}, "pending-departure")):
                    with self.subTest(observation=observation):
                        self.observer.observe.return_value = observation
                        result = self.finish("--release", "--policy", str(policy))[1][0]
                        self.assertEqual(result["reason"], reason, result)
                        self.assertEqual(result["checkout"], "retained")
                        self.assertTrue(self.wt.exists())
                        self.assertTrue((self.admin / "locked").exists())
                        with FINISH.ledger(self.state) as db:
                            item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
                            self.assertEqual(item["state"], "enrolled")
                            self.assertIsNone(item["intent"])
                            self.assertIsNone(item["result"])
        finally:
            self.policy.start()

    def test_every_owner_release_and_creator_claim_are_preserved(self):
        self.call("resume", "--owner", "owner-2")
        self.assertEqual(self.finish("--release")[1][0]["checkout"], "retained")
        second = self.finish("--owner", "owner-2")[1][0]
        self.assertEqual(second["released_owners"], ["owner-1"])
        self.assertEqual(self.call("release", "--owner", "owner-2")[1][0]["checkout"], "removed")

    def test_unclassified_ignored_payload_is_never_deleted(self):
        (self.wt / "ignored").write_text("synthetic recovery")
        with self.assertRaisesRegex(FINISH.Retain, "unclassified"):
            self.finish("--release")
        self.assertEqual((self.wt / "ignored").read_text(), "synthetic recovery")

    def test_released_inventory_change_requires_new_signoff(self):
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        self.finish("--release")
        (self.wt / "late").write_text("retain")
        self.proofs[self.url].update(merged=True, state="closed", merge="a" * 40)
        self.assertEqual(self.call("check", "--apply")[1][0]["checkout"], "retained")
        self.assertTrue((self.wt / "late").exists())

    def test_dependency_symlink_is_removed_without_touching_donor(self):
        (self.repo / ".git/info/exclude").write_text("node_modules\n")
        donor = self.base / "dependency-donor"
        donor.mkdir()
        (donor / "keep").write_text("dependency")
        (self.wt / "node_modules").symlink_to(donor, target_is_directory=True)
        self.assertEqual(self.finish("--release")[1][0]["checkout"], "removed")
        self.assertEqual((donor / "keep").read_text(), "dependency")

    def test_old_claim_then_identical_finish_cannot_revive_release(self):
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        self.finish("--release")
        old = self.row()
        with FINISH.ledger(self.state) as db:
            # Exact legacy claim mutation followed by an identical legacy
            # finish proof: no new client generation code participates here.
            with db:
                db.execute("INSERT INTO owners(worktree_id, owner, completed) VALUES (?, ?, 0) "
                           "ON CONFLICT(worktree_id, owner) DO UPDATE SET completed = 0",
                           (old["id"], "owner-1"))
                db.execute("UPDATE worktrees SET state = 'active', finish_head = NULL, primary_pr = NULL, "
                           "target = NULL, dependencies = NULL, reason = 'owner-resumed' WHERE id = ?", (old["id"],))
                db.execute("UPDATE owners SET completed = 1 WHERE worktree_id = ? AND owner = ?", (old["id"], "owner-1"))
                db.execute("UPDATE worktrees SET finish_head = ?, primary_pr = ?, target = ?, dependencies = ?, "
                           "state = 'finished', reason = 'completion-recorded-checkout-retained' WHERE id = ?",
                           (old["finish_head"], old["primary_pr"], old["target"], old["dependencies"], old["id"]))
        self.assertEqual(self.call("status")[1][0]["released_owners"], [])
        self.proofs[self.url].update(merged=True, state="closed", merge="a" * 40)
        self.assertEqual(self.call("check", "--apply")[1][0]["reason"], "awaiting-owner-release")

    def test_crash_after_unlock_holds_old_sql_and_updated_manual_consumers(self):
        original = FINISH.git

        def unlock_then_fail(path, *args):
            result = original(path, *args)
            if args[:2] == ("worktree", "unlock"):
                raise FINISH.Retain("injected-after-unlock")
            return result

        with mock.patch.object(FINISH, "git", side_effect=unlock_then_fail):
            result = self.finish("--release")[1][0]
        self.assertEqual(result["checkout"], "unknown")
        self.assertFalse((self.admin / "locked").exists())
        self.assertTrue(self.wt.exists())
        with self.assertRaisesRegex(FINISH.Retain, "finish-lifecycle"):
            self.call("guard")
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "intent"):
                db.execute("UPDATE owners SET completed=0 WHERE worktree_id=?", (row["id"],))
            with db:
                db.execute("UPDATE worktrees SET reason='old-check-projection' WHERE id=?", (row["id"],))
            self.assertIsNotNone(FINISH.retirement(db, row)["intent"])
        with mock.patch.object(FINISH, "remove_released") as remove:
            self.call("check", "--apply")
            remove.assert_not_called()

    def test_native_partial_removal_is_unknown_and_never_retried(self):
        original = FINISH.safety().supervise

        def partially_remove(command, **kwargs):
            if "remove" in command and "worktree" in command:
                shutil.rmtree(self.admin)
                result = subprocess.CompletedProcess(command, 1, b"", b"fixture failure")
                result.proof, result.failure = {"returncode": 1}, None
                return result
            return original(command, **kwargs)

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=partially_remove):
            result = self.finish("--release")[1][0]
        self.assertEqual(result["checkout"], "unknown")
        self.assertTrue(self.wt.exists())
        self.assertFalse(self.admin.exists())
        self.assertEqual(self.call("check", "--apply")[1][0]["retirement_state"], "incomplete")

    def test_filter_configuration_retains_without_starting_filter_or_removing(self):
        marker = self.base / "filter-ran"
        git(self.repo, "config", "filter.fixture.clean", "touch " + str(marker))
        result = self.finish("--release")[1][0]
        self.assertEqual(result["reason"], "git-conversion-filter-unqualified")
        self.assertTrue(self.wt.exists())
        self.assertFalse(marker.exists())
        self.assertTrue((self.admin / "locked").exists())

    def test_missing_activation_records_release_but_never_dispatches_removal(self):
        self.policy.stop()
        with mock.patch.object(FINISH, "remove_released") as remove:
            result = self.finish("--release")[1][0]
        remove.assert_not_called()
        self.assertEqual(result["reason"], "explicit-release-policy-not-installed")
        self.assertEqual(result["released_owners"], ["owner-1"])
        self.assertTrue(self.wt.exists())

    def test_after_unlock_all_updated_consumers_honor_durable_intent(self):
        original = FINISH.git
        def unlock_then_fail(path, *args):
            result = original(path, *args)
            if args[:2] == ("worktree", "unlock"):
                raise FINISH.Retain("injected-after-unlock")
            return result
        with mock.patch.object(FINISH, "git", side_effect=unlock_then_fail):
            self.assertEqual(self.finish("--release")[1][0]["checkout"], "unknown")
        (self.wt / "dist").mkdir()
        (self.wt / "dist/keep").write_text("recover")
        with mock.patch.dict(os.environ, {"DOTFILES_GWT_FINISH_STATE": str(self.state)}):
            clean = runpy.run_path(str(TOOL.with_name("agent-worktree-clean")))
            self.assertTrue(clean["finish_lifecycle_holds"](str(self.wt)))
            self.assertFalse(clean["trim_worktree_artifacts"](str(self.wt), ""))
            purge = runpy.run_path(str(TOOL.with_name("agent-worktree-purge")))
            with self.assertRaises(subprocess.CalledProcessError):
                purge["delete_path"](self.wt)
            for command in ("rm", "prune"):
                result = subprocess.run([
                    "zsh", "-f", "-c",
                    'source "$1"; _gwt_require_worktree_storage() { return 0; }; '
                    '_gwt_tmux_sync_context() { return 0; }; '
                    '_gwt_finish_tool() { "$FINISH_TOOL" "$@"; }; '
                    'if [[ "$2" == rm ]]; then gwt rm "$3" --force; else gwt prune; fi',
                    "fixture", str(ROOT / "functions/gwt/gwt.zsh"), command, str(self.wt)],
                    cwd=self.repo, env={**os.environ, "FINISH_TOOL": str(TOOL)},
                    capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("finish-lifecycle", result.stderr)
        self.assertEqual((self.wt / "dist/keep").read_text(), "recover")
        shutil.rmtree(self.admin)
        with self.assertRaisesRegex(FINISH.Retain, "finish-lifecycle"):
            self.call("guard", "--all", "--worktree", str(self.repo))

    def test_late_payload_after_first_admission_keeps_intent_and_target(self):
        count = 0
        def observe(*args):
            nonlocal count
            count += 1
            if count == 1:
                (self.wt / "late-recovery").write_text("retain")
            return {"holders": [], "mapping_coverage": "complete"}
        self.observer.observe.side_effect = observe
        result = self.finish("--release")[1][0]
        self.assertEqual(result["checkout"], "unknown")
        self.assertEqual(result["retirement_state"], "incomplete")
        self.assertEqual((self.wt / "late-recovery").read_text(), "retain")
        self.assertTrue((self.admin / "locked").exists())

    def test_default_sparse_wrapper_then_release_preserves_omitted_tracked_source(self):
        for name in ("src/keep.txt", "apps/macos/omitted.txt"):
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("source\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "sparse fixture")
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.proofs[self.url] = self.proof(self.url)
        profiles = self.base / "profiles/example-repo"
        profiles.mkdir(parents=True)
        for name in ("core.patterns", "default.profile"):
            shutil.copyfile(ROOT / "git-sparse/openclaw-openclaw" / name, profiles / name)
        fake = self.base / "enrollment-dispatch"
        self.token = str(uuid.uuid4())
        fake.write_text("#!/bin/sh\nif [ \"$1\" = creation-token ]; then printf '%s\\n' \"$TOKEN\"; fi\n")
        fake.chmod(0o700)
        result = subprocess.run([
            "zsh", "-f", "-c",
            'source "$1"; _gwt_require_worktree_storage() { return 0; }; '
            '_gwt_tmux_sync_context() { return 0; }; '
            '_gwt_finish_tool() { "$DISPATCH" "$@"; }; '
            'gwt new wrapper-task main --finish-managed',
            "fixture", str(ROOT / "functions/gwt/gwt.zsh")], cwd=self.repo,
            env={**os.environ, "DISPATCH": str(fake), "TOKEN": self.token,
                 "DOTFILES_WORKTREES_ROOT": str(self.root),
                 "DOTFILES_GIT_SPARSE_ROOT": str(profiles.parent)},
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.wt = self.root / "example-repo/wrapper-task"
        self.admin = Path(git(self.wt, "rev-parse", "--absolute-git-dir"))
        # Run the same deferred enrollment in-process with only storage/PR/
        # native census mocked; no installed host guard or policy is invoked.
        self.call("enroll", "--new-token", self.token)
        self.assertFalse((self.wt / "apps/macos/omitted.txt").exists())
        self.assertTrue((self.admin / "config.worktree").is_file())
        self.assertTrue((self.admin / "info/sparse-checkout").is_file())
        self.assertIn("S apps/macos/omitted.txt", git(self.wt, "ls-files", "-t"))
        result = self.finish("--release")[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertEqual(git(self.repo, "show", "wrapper-task:apps/macos/omitted.txt"), "source")

    def test_full_profile_config_is_supported_but_unowned_worktree_config_holds(self):
        git(self.repo, "config", "extensions.worktreeConfig", "true")
        git(self.wt, "sparse-checkout", "disable")
        git(self.wt, "config", "--worktree", "dotfiles.sparseProfile", "full")
        git(self.wt, "config", "--worktree", "user.fixtureNote", "preserve")
        with self.assertRaisesRegex(FINISH.Retain, "worktree-config-recovery-required"):
            self.finish("--release")
        self.assertTrue(self.wt.exists())
        git(self.wt, "config", "--worktree", "--unset", "user.fixtureNote")
        self.call("resume")
        self.assertEqual(self.finish("--release")[1][0]["checkout"], "removed")

    def test_post_release_admin_profile_change_requires_new_signoff(self):
        git(self.repo, "config", "extensions.worktreeConfig", "true")
        git(self.wt, "config", "--worktree", "dotfiles.sparseProfile", "full")
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        self.finish("--release")
        git(self.wt, "config", "--worktree", "dotfiles.sparseProfile", "custom")
        self.proofs[self.url].update(merged=True, state="closed", merge="a" * 40)
        result = self.call("check", "--apply")[1][0]
        self.assertEqual(result["reason"], "released-admin-changed-resume-first")
        self.assertTrue(self.wt.exists())

    def test_present_skip_worktree_file_is_not_exempt_from_bytes_proof(self):
        git(self.wt, "update-index", "--skip-worktree", "file")
        (self.wt / "file").write_text("must retain\n")
        with self.assertRaisesRegex(FINISH.Retain, "working-bytes"):
            self.finish("--release")
        self.assertTrue(self.wt.exists())

    def test_inventory_supports_more_than_old_32mib_and_bounds_the_actual_next_read(self):
        # Five distinct, compressible 7MiB blobs exercise the former failure
        # without a large object store or a real checkout read.
        for number in range(5):
            (self.wt / f"bulk-{number}").write_bytes(bytes([65 + number]) * (7 * 1024 * 1024))
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-m", "large fixture")
        self.head = git(self.wt, "rev-parse", "HEAD")
        self.proofs[self.url] = self.proof(self.url)
        snap = FINISH.revalidate(self.row())
        import time
        module = FINISH.safety()
        self.assertGreater(module.SOURCE_LIMIT, module.PASS_LIMIT)
        with mock.patch.object(module, "PASS_LIMIT", 32 * 1024 * 1024):
            contents = module.inventory(snap, FINISH.git, time.monotonic() + 10)
            self.assertTrue(all(f"bulk-{number}" in contents["entries"] for number in range(5)))
        with mock.patch.object(module, "SOURCE_LIMIT", 32 * 1024 * 1024):
            with self.assertRaisesRegex(FINISH.Retain, "size"):
                module.inventory(snap, FINISH.git, time.monotonic() + 10)
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        self.assertEqual(self.finish("--release")[1][0]["reason"], "pr-not-merged")

    def rebased_task(self):
        (self.wt / "feature-file").write_text("feature\n")
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-m", "feature message")
        old_tip = git(self.wt, "rev-parse", "HEAD")
        (self.repo / "upstream-file").write_text("upstream\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "upstream message")
        git(self.wt, "rebase", "main")
        self.head = git(self.wt, "rev-parse", "HEAD")
        self.assertNotEqual(old_tip, self.head)
        self.proofs[self.url] = self.proof(self.url)
        return old_tip, self.repo / ".git/logs/refs/heads/released-feature"

    def test_rebase_then_merged_release_preserves_old_tip_in_branch_reflog(self):
        old_tip, reflog = self.rebased_task()
        before = reflog.read_bytes()
        with self.assertRaises(subprocess.CalledProcessError):
            git(self.repo, "merge-base", "--is-ancestor", old_tip, self.head)
        result = self.finish("--release")[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertEqual(reflog.read_bytes(), before)
        self.assertEqual(git(self.repo, "rev-parse", "released-feature@{1}"), old_tip)
        self.assertEqual(git(self.repo, "show", old_tip + ":feature-file"), "feature")

    def test_expired_branch_reflog_does_not_discard_uncovered_rebase_recovery(self):
        old_tip, reflog = self.rebased_task()
        git(self.repo, "reflog", "expire", "--expire=now", "refs/heads/released-feature")
        self.assertNotIn(old_tip.encode(), reflog.read_bytes())
        result = self.finish("--release")[1][0]
        self.assertEqual(result["checkout"], "retained", result)
        self.assertTrue((self.admin / "ORIG_HEAD").exists())
        self.assertTrue((self.admin / "locked").exists())

    def test_divergent_auto_merge_tree_is_retained(self):
        old_tip, _ = self.rebased_task()
        tree = git(self.repo, "rev-parse", old_tip + "^{tree}")
        (self.admin / "AUTO_MERGE").write_text(tree + "\n")
        with self.assertRaisesRegex(FINISH.Retain, "auto-merge-tree-recovery"):
            self.finish("--release")
        self.assertTrue(self.wt.exists())

    def test_corrupt_or_linked_branch_reflog_cannot_attest_recovery(self):
        old_tip, reflog = self.rebased_task()
        before = reflog.read_bytes()
        reflog.write_bytes(old_tip.encode() + b" " + self.head.encode() + b" corrupt\n")
        self.assertEqual(self.finish("--release")[1][0]["reason"], "branch-reflog-proof-invalid")
        self.assertTrue((self.admin / "locked").exists())
        reflog.write_bytes(before)
        real = reflog.parent
        moved = real.with_name("saved-heads")
        real.rename(moved)
        real.symlink_to(moved, target_is_directory=True)
        try:
            self.assertEqual(self.call("check", "--apply")[1][0]["reason"], "branch-reflog-parent-unqualified")
            self.assertTrue(self.wt.exists())
        finally:
            real.unlink()
            moved.rename(real)

    def test_post_remove_branch_reflog_drift_is_unknown(self):
        _, reflog = self.rebased_task()
        original = FINISH.safety().supervise
        def changed_log(command, **kwargs):
            result = original(command, **kwargs)
            if "worktree" in command and "remove" in command:
                with reflog.open("ab") as stream:
                    stream.write(reflog.read_bytes().splitlines(keepends=True)[-1])
            return result
        with mock.patch.object(FINISH.safety(), "supervise", side_effect=changed_log):
            result = self.finish("--release")[1][0]
        self.assertEqual(result["checkout"], "unknown", result)
        self.assertEqual(result["retirement_state"], "incomplete")

    def editor_commit(self):
        editor = self.base / "fixture-editor.py"
        editor.write_text("from pathlib import Path\nimport sys\np=Path(sys.argv[1])\n"
                          "p.write_text('editor message\\n' + p.read_text())\n")
        (self.wt / "file").write_text("editor change\n")
        git(self.wt, "add", "file")
        with mock.patch.dict(os.environ, {"GIT_EDITOR": sys.executable + " " + str(editor)}):
            git(self.wt, "commit")
        self.head = git(self.wt, "rev-parse", "HEAD")
        self.proofs[self.url] = self.proof(self.url)
        self.assertIn(b"#", (self.admin / "COMMIT_EDITMSG").read_bytes())

    def test_editor_commit_uses_canonical_cleanup_before_release(self):
        self.editor_commit()
        result = self.finish("--release")[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertEqual(git(self.repo, "show", "-s", "--format=%B", "released-feature"), "editor message")

    def test_divergent_editor_draft_and_unknown_cleanup_mode_retain(self):
        self.editor_commit()
        message = self.admin / "COMMIT_EDITMSG"
        original = message.read_bytes()
        message.write_bytes(original + b"substantive uncommitted draft\n")
        with self.assertRaisesRegex(FINISH.Retain, "commit-message-recovery"):
            self.finish("--release")
        self.assertTrue(message.read_bytes().endswith(b"substantive uncommitted draft\n"))
        message.write_bytes(original)
        git(self.repo, "config", "commit.cleanup", "scissors")
        self.call("resume")
        with self.assertRaisesRegex(FINISH.Retain, "cleanup-mode-unqualified"):
            self.finish("--release")
        self.assertTrue(self.wt.exists())

    @unittest.skipUnless(sys.platform == "darwin", "native no-follow xattr fixture")
    def test_native_unknown_metadata_retains_checkout_and_admin_objects(self):
        for path in (self.wt, self.wt / "file", self.admin, self.admin / "index"):
            with self.subTest(kind=path.name):
                subprocess.run(["/usr/bin/xattr", "-w", "com.example.finish-fixture", "synthetic", str(path)], check=True)
                try:
                    self.call("resume")
                    with self.assertRaisesRegex(FINISH.Retain, "extended-attributes"):
                        self.finish("--release")
                    self.assertTrue(path.exists())
                    self.assertTrue((self.admin / "locked").exists())
                finally:
                    subprocess.run(["/usr/bin/xattr", "-d", "com.example.finish-fixture", str(path)], check=True)
        self.assertTrue(self.wt.exists())


class FinalizedRemovalTests(unittest.TestCase):
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    finish = LifecycleTests.finish
    setUp = ReleaseTests.setUp
    tearDown = ReleaseTests.tearDown

    def remove(self, *roots):
        args = ["--finalized"]
        for root in roots:
            args.extend(("--discard-ignored", root))
        with mock.patch.object(FINISH, "manual_holders"):
            return self.call("remove", *args)

    def artifacts(self):
        (self.repo / ".git/info/exclude").write_text(".crabbox\nnode_modules\n.env\n")
        evidence = self.wt / ".crabbox"
        evidence.mkdir()
        (evidence / "proof.json").write_text('{"result":"passed"}\n')
        (evidence / "test.log").write_text("finalized test output\n")
        return evidence

    def inventory(self, *roots, compact=True, until=None):
        with FINISH.ledger(self.state) as db:
            snap = FINISH.revalidate(FINISH.get_row(db, self.wt))
        return FINISH.safety().inventory(
            snap, FINISH.git, time.monotonic() + 120 if until is None else until,
            discard_ignored=roots, compact_discarded=compact)

    def test_real_dependency_tree_above_source_limit_removes_with_compact_holder_proof(self):
        (self.repo / ".git/info/exclude").write_text("node_modules\n")
        modules = self.wt / "node_modules"
        # Physical pnpm-like packages, not mocked scandir rows or a smaller limit.
        for package in range(129):
            directory = modules / ".pnpm" / f"package-{package}" / "node_modules" / "package"
            directory.mkdir(parents=True)
            for leaf in range(1024):
                (directory / f"file-{leaf}.js").write_bytes(b"module.exports = 1;\n")
        expected = 2 + 129 * (3 + 1024)
        contents = self.inventory("node_modules")
        self.assertGreater(expected, FINISH.safety().ENTRY_LIMIT)
        self.assertEqual(contents["discarded_roots"]["node_modules"]["count"], expected)
        self.assertEqual(len(contents["discarded_inodes"]), expected)
        self.assertLess(len(contents["entries"]), 10)
        self.assertLess(len(json.dumps(contents)), 4 * 1024 * 1024)
        late_repository = modules / ".pnpm/package-0/node_modules/package/.git"
        late_repository.mkdir()
        with self.assertRaisesRegex(FINISH.Retain, "repository-or-mount"):
            self.inventory("node_modules")
        late_repository.rmdir()
        with self.assertRaisesRegex(FINISH.Retain, "inventory-entry-limit"):
            self.inventory("node_modules", compact=False)
        if shutil.which("lsof"):
            code, rows = self.call("remove", "--finalized", "--discard-ignored", "node_modules")
        else:
            code, rows = self.remove("node_modules")
        self.assertEqual(code, 0, rows)
        self.assertEqual(rows[0]["checkout"], "removed")
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertFalse(self.wt.exists())

    def test_discard_ceiling_deadline_and_tracked_overlap_still_refuse(self):
        self.artifacts()
        with mock.patch.object(FINISH.safety(), "DISCARDED_ENTRY_LIMIT", 2):
            with self.assertRaisesRegex(FINISH.Retain, "discarded-inventory-entry-limit"):
                self.inventory(".crabbox")
        with self.assertRaisesRegex(FINISH.Retain, "admission-deadline"):
            self.inventory(".crabbox", until=time.monotonic() - 1)
        with self.assertRaisesRegex(FINISH.Retain, "overlaps-tracked-source"):
            self.inventory("file")
        self.assertTrue(self.wt.exists())

    def test_compact_call_requires_matching_holder_consumer_during_upgrade(self):
        self.artifacts()
        legacy = self.inventory(".crabbox", compact=False)
        self.assertIn(".crabbox/test.log", legacy["entries"])
        self.assertNotIn("discarded_inodes", legacy)
        original = FINISH.safety().inventory

        def old_module(snap, git, until, *, discard_ignored=()):
            return original(snap, git, until, discard_ignored=discard_ignored)

        with mock.patch.object(FINISH.safety(), "inventory", side_effect=old_module):
            with self.assertRaisesRegex(TypeError, "compact_discarded"):
                self.remove(".crabbox")
        self.assertTrue(self.wt.exists())
        self.assertTrue((self.admin / "locked").exists())
        with FINISH.ledger(self.state) as db:
            item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
            self.assertEqual(item["state"], "enrolled")
            self.assertIsNone(item["intent"])

    def test_compact_digest_detects_same_population_rename_and_replacement(self):
        evidence = self.artifacts()
        first = self.inventory(".crabbox")
        (evidence / "test.log").rename(evidence / "renamed.log")
        renamed = self.inventory(".crabbox")
        (evidence / "renamed.log").unlink()
        (evidence / "renamed.log").write_text("different output\n")
        replaced = self.inventory(".crabbox")
        summaries = [item["discarded_roots"][".crabbox"] for item in (first, renamed, replaced)]
        self.assertEqual({item["count"] for item in summaries}, {3})
        self.assertEqual(len({item["sha256"] for item in summaries}), 3)

    @unittest.skipUnless(shutil.which("lsof"), "native lsof unavailable")
    def test_compact_discard_inode_blocks_external_hardlink_holder(self):
        evidence = self.artifacts()
        alias = self.base / "external-alias"
        os.link(evidence / "test.log", alias)
        contents = self.inventory(".crabbox")
        self.assertNotIn(".crabbox/test.log", contents["entries"])
        with alias.open(), self.assertRaisesRegex(FINISH.Retain, "pending-departure"):
            FINISH.manual_holders({"path": str(self.wt), "gitdir": str(self.admin)},
                                  contents, {}, time.monotonic() + 30)
        self.assertTrue(self.wt.exists())

    def test_concurrent_sibling_commit_and_new_registration_do_not_break_closeout(self):
        real = FINISH.safety().supervise
        new = self.root / "concurrent-task"

        def remove_with_other_work(command, **kwargs):
            if "worktree" in command and "remove" in command:
                git(self.legacy, "commit", "--allow-empty", "-m", "other owner progress")
                git(self.repo, "worktree", "add", "-b", "concurrent", str(new))
            return real(command, **kwargs)

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=remove_with_other_work):
            code, rows = self.remove()
        self.assertEqual(code, 0, rows)
        self.assertEqual(rows[0]["checkout"], "removed")
        self.assertTrue(new.exists())
        self.assertNotEqual(git(self.legacy, "rev-parse", "HEAD"), self.head)

    def test_real_sibling_detach_and_branch_checkout_preserve_physical_identity(self):
        native = FINISH.safety().supervise
        transitions = []

        def concurrent_checkout(command, **kwargs):
            if "worktree" in command and "remove" in command:
                git(self.legacy, "checkout", "--detach")
                transitions.append(FINISH.records(str(self.repo)))
                git(self.legacy, "checkout", "-b", "sibling-progress")
                transitions.append(FINISH.records(str(self.repo)))
            return native(command, **kwargs)

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=concurrent_checkout):
            code, rows = self.remove()
        self.assertEqual(code, 0, rows)
        self.assertEqual(rows[0]["checkout"], "removed")
        self.assertEqual(len(transitions), 2)
        with FINISH.ledger(self.state) as db:
            result = json.loads(FINISH.retirement(db, FINISH.get_row(db, self.wt))["result"])
        self.assertTrue(result["siblings_preserved"])
        self.assertTrue(result["siblings_after"])
        self.assertTrue(result["sibling_identities_after"])
        self.assertEqual(result["sibling_comparison"]["changes"][0]["kind"], "operational")

    def test_known_absent_sibling_root_preserves_locked_native_registration(self):
        admin = Path(git(self.legacy, "rev-parse", "--absolute-git-dir"))
        git(self.repo, "worktree", "lock", "--reason", "other owner", str(self.legacy))
        admin_id = FINISH.identity(admin)
        lock = (admin / "locked").read_bytes()
        shutil.rmtree(self.legacy)
        with FINISH.ledger(self.state) as db:
            snap = FINISH.revalidate(FINISH.get_row(db, self.wt))
        initial = FINISH.sibling_identity(snap, str(self.legacy), time.monotonic() + 30)
        code, rows = self.remove()
        self.assertEqual(code, 0, rows)
        self.assertEqual(rows[0]["checkout"], "removed")
        self.assertFalse(self.legacy.exists())
        self.assertEqual(FINISH.identity(admin), admin_id)
        self.assertEqual((admin / "locked").read_bytes(), lock)
        with FINISH.ledger(self.state) as db:
            item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
            final = json.loads(item["result"])["sibling_identities_after"][str(self.legacy)]
        self.assertEqual(initial, final)
        self.assertEqual(initial["path_absence"]["errno"], errno.ENOENT)
        self.assertEqual(initial["pointer_absence"]["errno"], errno.ENOENT)

    def test_known_absent_sibling_pointer_preserves_existing_root_and_admin(self):
        admin = Path(git(self.legacy, "rev-parse", "--absolute-git-dir"))
        root_id, admin_id = FINISH.identity(self.legacy), FINISH.identity(admin)
        (self.legacy / ".git").unlink()
        code, rows = self.remove()
        self.assertEqual(code, 0, rows)
        self.assertEqual(rows[0]["checkout"], "removed")
        self.assertEqual(FINISH.identity(self.legacy), root_id)
        self.assertEqual(FINISH.identity(admin), admin_id)
        self.assertFalse((self.legacy / ".git").exists())
        with FINISH.ledger(self.state) as db:
            result = json.loads(FINISH.retirement(db, FINISH.get_row(db, self.wt))["result"])
        saved = result["sibling_identities_after"][str(self.legacy)]
        self.assertNotIn("path_absence", saved)
        self.assertEqual(saved["pointer_absence"]["parent_id"], root_id)
        self.assertTrue(result["siblings_preserved"])

    def test_known_absent_sibling_admin_index_is_shared_once_per_pass(self):
        second = self.root / "second-absent"
        git(self.repo, "worktree", "add", "-b", "second", str(second))
        shutil.rmtree(self.legacy)
        (second / ".git").unlink()
        with mock.patch.object(FINISH, "sibling_admin_index", wraps=FINISH.sibling_admin_index) as index:
            code, rows = self.remove()
        self.assertEqual(code, 0, rows)
        self.assertEqual(index.call_count, 2)

    def test_known_absent_sibling_appearance_is_not_preservation(self):
        original = self.base / "absent-source"
        self.legacy.rename(original)
        native = FINISH.safety().supervise

        def reappear(command, **kwargs):
            result = native(command, **kwargs)
            if "worktree" in command and "remove" in command:
                original.rename(self.legacy)
            return result

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=reappear):
            code, rows = self.remove()
        self.assertEqual(code, 1, rows)
        with FINISH.ledger(self.state) as db:
            result = json.loads(FINISH.retirement(db, FINISH.get_row(db, self.wt))["result"])
        self.assertFalse(result["siblings_preserved"])
        self.assertIn({"path": str(self.legacy), "kind": "identity"}, result["sibling_comparison"]["changes"])

    def test_new_sibling_pointer_loss_is_not_known_prior_absence(self):
        native = FINISH.safety().supervise

        def lose_pointer(command, **kwargs):
            result = native(command, **kwargs)
            if "worktree" in command and "remove" in command:
                (self.legacy / ".git").unlink()
            return result

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=lose_pointer):
            code, rows = self.remove()
        self.assertEqual(code, 1, rows)
        with FINISH.ledger(self.state) as db:
            result = json.loads(FINISH.retirement(db, FINISH.get_row(db, self.wt))["result"])
        self.assertFalse(result["siblings_preserved"])

    def test_known_absent_sibling_rejects_ambiguous_admin_and_permission_unknown(self):
        admin = Path(git(self.legacy, "rev-parse", "--absolute-git-dir"))
        pointer = self.legacy / ".git"
        with FINISH.ledger(self.state) as db:
            snap = FINISH.revalidate(FINISH.get_row(db, self.wt))
        read = FINISH.safety().read_file

        def denied(path, *args):
            if path == pointer:
                raise PermissionError(errno.EACCES, "fixture permission")
            return read(path, *args)

        with mock.patch.object(FINISH.safety(), "read_file", side_effect=denied):
            with self.assertRaises(PermissionError):
                FINISH.sibling_identity(snap, str(self.legacy), time.monotonic() + 30)
        pointer.unlink()
        shutil.copytree(admin, admin.parent / "ambiguous")
        with self.assertRaisesRegex(FINISH.Retain, "registration-ambiguous"):
            self.remove()
        with FINISH.ledger(self.state) as db:
            self.assertIsNone(FINISH.retirement(db, FINISH.get_row(db, self.wt))["intent"])
        self.assertTrue(self.wt.exists())

    def test_known_absent_sibling_parent_admin_and_backlink_drift_are_not_preserved(self):
        admin = Path(git(self.legacy, "rev-parse", "--absolute-git-dir"))
        with FINISH.ledger(self.state) as db:
            snap = FINISH.revalidate(FINISH.get_row(db, self.wt))
        pointer = self.legacy / ".git"
        pointer.unlink()

        def capture():
            return FINISH.sibling_identity(snap, str(self.legacy), time.monotonic() + 30)

        initial = capture()
        prior = self.base / "prior-admin"
        admin.rename(prior)
        shutil.copytree(prior, admin)
        self.assertNotEqual(capture(), initial)
        (admin / "commondir").write_text(str(self.base) + "\n")
        with self.assertRaisesRegex(FINISH.Retain, "backlink-changed"):
            capture()
        (admin / "commondir").write_bytes((prior / "commondir").read_bytes())
        original_root = self.base / "prior-root"
        self.legacy.rename(original_root)
        self.legacy.symlink_to(original_root, target_is_directory=True)
        with self.assertRaises(FINISH.Retain):
            capture()

    def test_known_absent_sibling_parent_replacement_and_lock_replacement_change_identity(self):
        parent = self.root / "nested"
        parent.mkdir()
        sibling = parent / "absent"
        git(self.repo, "worktree", "add", "--lock", "--reason", "other owner", "-b", "absent", str(sibling))
        admin = Path(git(sibling, "rev-parse", "--absolute-git-dir"))
        shutil.rmtree(sibling)
        with FINISH.ledger(self.state) as db:
            snap = FINISH.revalidate(FINISH.get_row(db, self.wt))

        def capture():
            return FINISH.sibling_identity(snap, str(sibling), time.monotonic() + 30)

        initial = capture()
        parent.rename(self.base / "prior-parent")
        parent.mkdir()
        changed_parent = capture()
        self.assertNotEqual(changed_parent, initial)
        lock = admin / "locked"
        lock.rename(admin / "prior-lock")
        lock.write_bytes((admin / "prior-lock").read_bytes())
        self.assertNotEqual(capture(), changed_parent)

    def test_sibling_physical_replacement_retains_unknown_with_actual_delta(self):
        native = FINISH.safety().supervise

        def replace_sibling(command, **kwargs):
            result = native(command, **kwargs)
            if "worktree" in command and "remove" in command:
                prior = self.base / "preserved-sibling"
                self.legacy.rename(prior)
                shutil.copytree(prior, self.legacy)
            return result

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=replace_sibling):
            code, rows = self.remove()
        self.assertEqual(code, 1)
        self.assertEqual(rows[0]["checkout"], "unknown")
        with FINISH.ledger(self.state) as db:
            item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
            intent, result = json.loads(item["intent"]), json.loads(item["result"])
        self.assertFalse(result["siblings_preserved"])
        self.assertIn({"path": str(self.legacy), "kind": "identity"}, result["sibling_comparison"]["changes"])
        self.assertNotEqual(intent["sibling_identities"][str(self.legacy)]["path_id"],
                            result["sibling_identities_after"][str(self.legacy)]["path_id"])

    def test_failed_sibling_readback_keeps_successful_child_and_target_absence_facts(self):
        capture = FINISH.sibling_identities
        calls = []

        def unavailable_after(*args):
            calls.append(args)
            if len(calls) == 2:
                raise FINISH.Retain("sibling-readback-unavailable")
            return capture(*args)

        with mock.patch.object(FINISH, "sibling_identities", side_effect=unavailable_after):
            code, rows = self.remove()
        self.assertEqual(code, 1)
        self.assertEqual(rows[0]["checkout"], "unknown")
        with FINISH.ledger(self.state) as db:
            result = json.loads(FINISH.retirement(db, FINISH.get_row(db, self.wt))["result"])
        self.assertEqual(result["reason"], "sibling-readback-unavailable")
        self.assertEqual(result["child"]["returncode"], 0)
        self.assertTrue(all(result[key] for key in ("path_absent", "admin_absent", "registration_absent")))
        self.assertTrue(result["siblings_after"])
        self.assertNotIn("sibling_identities_after", result)

    def test_finalized_evidence_disposal_preserves_branch_and_skips_automatic_policy(self):
        self.artifacts()
        with mock.patch.object(FINISH, "qualified_policy") as policy, \
                mock.patch.object(FINISH, "holder_qualification") as qualification:
            code, rows = self.remove(".crabbox")
        self.assertEqual(code, 0)
        self.assertEqual(rows[0]["checkout"], "removed")
        self.assertEqual(rows[0]["reason"], "finalized-task-worktree-removed")
        self.assertFalse(self.wt.exists())
        self.assertTrue(self.legacy.exists())
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        policy.assert_not_called()
        qualification.assert_not_called()
        with FINISH.ledger(self.state) as db:
            row = FINISH.retirement(db, FINISH.get_row(db, self.wt))
            self.assertIsNone(row["intent"])
            self.assertIsNone(row["inventory"])
            self.assertEqual(row["state"], "removed")

    def test_finalized_device_renumbering_removes_artifacts_without_rebinding_ledger(self):
        self.artifacts()
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            recorded = json.loads(row["identity"])
            for key in ("path_id", "gitdir_id", "common_id", "owner_id"):
                recorded[key][0] += 1
            encoded = json.dumps(recorded, sort_keys=True)
            with db:
                db.execute("UPDATE worktrees SET identity=? WHERE id=?", (encoded, row["id"]))
            with self.assertRaisesRegex(FINISH.Retain, "worktree-identity-changed"):
                FINISH.revalidate(FINISH.get_row(db, self.wt))
        self.assertEqual(self.remove(".crabbox")[0], 0)
        self.assertFalse(self.wt.exists())
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        with FINISH.ledger(self.state) as db:
            self.assertEqual(FINISH.get_row(db, self.wt)["identity"], encoded)

    def test_device_normalization_requires_all_inodes_bindings_and_bijective_mapping(self):
        with FINISH.ledger(self.state) as db:
            row = dict(FINISH.get_row(db, self.wt))
        current = FINISH.revalidate(row)
        recorded = json.loads(row["identity"])
        identities = ("path_id", "gitdir_id", "common_id", "owner_id")
        for key in identities:
            recorded[key][0] += 1
        row["identity"] = json.dumps(recorded)
        changes = [{key: [current[key][0], current[key][1] + 1]} for key in identities]
        changes += [{key: value + "-changed"} for key, value in current.items()
                    if key not in (*identities, "head")]
        changes.append({"gitdir_id": [current["gitdir_id"][0] + 2, current["gitdir_id"][1]]})
        for change in changes:
            with self.subTest(change=change), mock.patch.object(FINISH, "snapshot", return_value={**current, **change}):
                with self.assertRaisesRegex(FINISH.Retain, "worktree-identity-changed"):
                    FINISH.revalidate(row, allow_device_renumbering=True)
        recorded["gitdir_id"][0] += 1  # Distinct old devices cannot collapse onto one current device.
        row["identity"] = json.dumps(recorded)
        with mock.patch.object(FINISH, "snapshot", return_value=current):
            with self.assertRaisesRegex(FINISH.Retain, "worktree-identity-changed"):
                FINISH.revalidate(row, allow_device_renumbering=True)
        self.assertTrue(self.wt.exists())

    def test_device_changes_during_manual_closeout_remain_strict(self):
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            current = FINISH.revalidate(row)
        changed = {**current, **{key: [current[key][0] + 1, current[key][1]]
                               for key in ("path_id", "gitdir_id", "common_id", "owner_id")}}
        with mock.patch.object(FINISH, "snapshot", side_effect=[current, changed]):
            with self.assertRaisesRegex(FINISH.Retain, "worktree-identity-changed"):
                self.remove()
        self.assertTrue(self.wt.exists())
        self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.token)
        with FINISH.ledger(self.state) as db:
            item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
            self.assertEqual(item["state"], "enrolled")
            self.assertIsNone(item["intent"])

    def test_unlisted_private_ignored_content_and_untracked_deliverables_refuse(self):
        self.artifacts()
        secret = self.wt / ".env"
        secret.write_text("PRIVATE=fixture\n")
        with self.assertRaisesRegex(FINISH.Retain, "exact-disposable-roots"):
            self.remove(".crabbox")
        self.assertTrue(secret.exists())
        secret.unlink()
        (self.wt / "deliverable.txt").write_text("keep\n")
        with self.assertRaisesRegex(FINISH.Retain, "dirty-or-untracked"):
            self.remove(".crabbox")
        self.assertTrue(self.wt.exists())

    def test_target_branch_writer_blocks_without_owning_checkout_files(self):
        lock = self.repo / ".git/refs/heads/released-feature.lock"
        lock.write_text(self.head + "\n")
        with self.assertRaisesRegex(FINISH.Retain, "preserved-branch-lock"):
            self.remove()
        self.assertTrue(lock.exists())
        self.assertTrue(self.wt.exists())

    def test_external_and_dangling_symlinks_are_not_traversed(self):
        self.artifacts()
        donor = self.base / "donor"
        donor.mkdir()
        (donor / "keep").write_text("shared installation\n")
        (self.wt / "node_modules").symlink_to(donor)
        (self.wt / ".crabbox/expired-input").symlink_to(self.base / "missing")
        self.assertEqual(self.remove(".crabbox", "node_modules")[0], 0)
        self.assertEqual((donor / "keep").read_text(), "shared installation\n")

    def test_live_holder_dirty_source_pin_and_wrong_marker_refuse(self):
        with mock.patch.object(FINISH, "manual_holders", side_effect=FINISH.Retain("pending-departure")):
            with self.assertRaisesRegex(FINISH.Retain, "pending-departure"):
                self.call("remove", "--finalized")
        (self.wt / "file").write_text("unfinished\n")
        with self.assertRaisesRegex(FINISH.Retain, "dirty-or-untracked"):
            self.remove()
        git(self.wt, "restore", "file")
        self.call("pin", "--reason", "unfinished dependency")
        with self.assertRaisesRegex(FINISH.Retain, "recovery-pin"):
            self.remove()
        self.call("unpin", "--reason", "unfinished dependency")
        git(self.repo, "worktree", "unlock", str(self.wt))
        git(self.repo, "worktree", "lock", "--reason", "other owner", str(self.wt))
        with self.assertRaisesRegex(FINISH.Retain, "registration-missing-or-locked"):
            self.remove()
        self.assertTrue(self.wt.exists())

    def test_nested_repository_and_live_artifact_types_refuse(self):
        evidence = self.artifacts()
        (evidence / ".git").mkdir()
        with self.assertRaisesRegex(FINISH.Retain, "repository-or-mount"):
            self.remove(".crabbox")
        (evidence / ".git").rmdir()
        os.mkfifo(evidence / "live-pipe")
        with self.assertRaisesRegex(FINISH.Retain, "live-or-unknown-file-type"):
            self.remove(".crabbox")

    def test_failed_native_removal_restores_ownership_and_cannot_be_replayed(self):
        real = FINISH.safety().supervise
        attempts = []

        def fail_removal(command, **kwargs):
            if "worktree" in command and "remove" in command:
                attempts.append(command)
                result = subprocess.CompletedProcess(command, 1, b"", b"fixture refusal")
                result.proof = {"returncode": 1, "reaped": True}
                result.failure = None
                return result
            return real(command, **kwargs)

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=fail_removal):
            code, rows = self.remove()
            self.assertEqual(code, 1)
            self.assertEqual(rows[0]["checkout"], "unknown")
            self.assertEqual(rows[0]["retirement_state"], "incomplete")
            self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.token)
            with self.assertRaisesRegex(FINISH.Retain, "intent"):
                self.remove()
        self.assertEqual(len(attempts), 1)

    def assert_incomplete_locked(self, code, rows):
        self.assertEqual(code, 1, rows)
        self.assertEqual(rows[0]["checkout"], "unknown")
        self.assertEqual(rows[0]["retirement_state"], "incomplete")
        self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.token)
        with self.assertRaisesRegex(FINISH.Retain, "intent"):
            self.remove()

    def test_removal_over_thirty_seconds_outlives_admission_and_preserves_branch(self):
        real, clock = FINISH.safety().supervise, time.monotonic
        offset, children = [0], []

        def delayed_removal(command, **kwargs):
            if "worktree" not in command or "remove" not in command:
                self.assertNotIn("timeout", kwargs)  # Ordinary queries retain their default.
                return real(command, **kwargs)
            self.assertGreater(kwargs["timeout"], 30)
            self.assertLessEqual(kwargs["timeout"], 180)
            delayed = [sys.executable, "-c",
                       "import os,sys,time;time.sleep(31);os.execv(sys.argv[1],sys.argv[1:])",
                       *command]
            result = real(delayed, **kwargs)
            children.append(result)
            # Expire only admission after the real slow child has joined.
            offset[0] = 121
            return result

        with mock.patch.object(time, "monotonic", side_effect=lambda: clock() + offset[0]), \
                mock.patch.object(FINISH.safety(), "supervise", side_effect=delayed_removal):
            code, rows = self.remove()
        self.assertEqual(code, 0, rows)
        self.assertEqual(rows[0]["checkout"], "removed")
        self.assertEqual(len(children), 1)
        self.assertGreater(children[0].proof["ended_monotonic"] - children[0].proof["started_monotonic"], 30)
        self.assertTrue(children[0].proof["reaped"])
        self.assertTrue(children[0].proof["group_absent"])
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertFalse(self.wt.exists())

    def test_removal_budget_clips_to_owner_reap_and_readback_reserve(self):
        admission, supervise = FINISH.finalized_admission, FINISH.safety().supervise
        remaining = []

        def limited_owner(*args):
            result = admission(*args)
            FINISH.safety().DEADLINE = time.monotonic() + 40
            return result

        def observe(command, **kwargs):
            if "worktree" in command and "remove" in command:
                remaining.append(kwargs["timeout"])
            return supervise(command, **kwargs)

        with mock.patch.object(FINISH, "finalized_admission", side_effect=limited_owner), \
                mock.patch.object(FINISH.safety(), "supervise", side_effect=observe):
            code, rows = self.remove()
        self.assertEqual(code, 0, rows)
        self.assertEqual(len(remaining), 1)
        self.assertGreater(remaining[0], 0)
        self.assertLessEqual(remaining[0], 8)

    def test_exhausted_removal_reserve_does_not_unlock_or_dispatch(self):
        admission, native = FINISH.finalized_admission, FINISH.git
        supervise = FINISH.safety().supervise

        def exhausted_owner(*args):
            result = admission(*args)
            FINISH.safety().DEADLINE = time.monotonic() + 31
            return result

        with mock.patch.object(FINISH, "finalized_admission", side_effect=exhausted_owner), \
                mock.patch.object(FINISH, "git", wraps=native) as commands, \
                mock.patch.object(FINISH.safety(), "supervise", wraps=supervise) as children:
            code, rows = self.remove()
        self.assertEqual(rows[0]["reason"], "removal-budget-exhausted")
        self.assertFalse(any(call.args[1:3] == ("worktree", "unlock") for call in commands.call_args_list))
        self.assertFalse(any("remove" in call.args[0] for call in children.call_args_list))
        self.assert_incomplete_locked(code, rows)

    def test_budget_expiring_after_unlock_restores_lock_without_dispatch(self):
        native, clock = FINISH.git, time.monotonic
        offset = [0]
        supervise = FINISH.safety().supervise

        def expire_after_unlock(path, *args, **kwargs):
            result = native(path, *args, **kwargs)
            if args[:2] == ("worktree", "unlock"):
                offset[0] = 181
            return result

        with mock.patch.object(time, "monotonic", side_effect=lambda: clock() + offset[0]), \
                mock.patch.object(FINISH, "git", side_effect=expire_after_unlock), \
                mock.patch.object(FINISH.safety(), "supervise", wraps=supervise) as children:
            code, rows = self.remove()
        self.assertEqual(rows[0]["reason"], "removal-budget-exhausted")
        self.assertFalse(any("remove" in call.args[0] for call in children.call_args_list))
        self.assert_incomplete_locked(code, rows)

    def test_removal_timeout_reaps_child_restores_lock_and_refuses_retry(self):
        supervise, attempts = FINISH.safety().supervise, []

        def timeout_removal(command, **kwargs):
            if "worktree" not in command or "remove" not in command:
                return supervise(command, **kwargs)
            result = supervise([sys.executable, "-c", "import time;time.sleep(10)"],
                               **{**kwargs, "timeout": 0.05})
            attempts.append(result)
            return result

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=timeout_removal):
            self.assert_incomplete_locked(*self.remove())
        self.assertEqual(len(attempts), 1)
        self.assertIsNotNone(attempts[0].failure)
        self.assertTrue(attempts[0].proof["reaped"])
        self.assertTrue(attempts[0].proof["group_absent"])

    def test_superseded_reflog_history_needs_no_archive(self):
        (self.wt / "file").write_text("intermediate\n")
        git(self.wt, "commit", "-am", "intermediate")
        old = git(self.wt, "rev-parse", "HEAD")
        (self.wt / "file").write_text("final\n")
        git(self.wt, "commit", "--amend", "-am", "final")
        final = git(self.wt, "rev-parse", "HEAD")
        self.assertEqual(git(self.wt, "rev-list", old, "--not", "--all"), old)
        self.assertEqual(self.remove()[0], 0)
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), final)

    @unittest.skipUnless(shutil.which("lsof"), "native lsof unavailable")
    def test_wrapper_finalized_closeout_runs_native_holder_free_removal(self):
        self.artifacts()
        env = dict(os.environ, DOTFILES_WORKTREES_ROOT=str(self.root),
                   DOTFILES_GWT_FINISH_STATE=str(self.state), TMUX="")
        script = 'source "$1"; cd "$2"; gwt rm "$3" --finalized --discard-ignored .crabbox'
        command = ["zsh", "-c", script, "fixture", str(ROOT / "functions/gwt/gwt.zsh"),
                   str(self.repo), str(self.wt)]
        rejected = subprocess.run(
            ["zsh", "-c", script + " --force", *command[3:]], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertTrue(self.wt.exists())
        result = subprocess.run(command, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)[0]["checkout"], "removed")
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertFalse(self.wt.exists())

    @unittest.skipUnless(shutil.which("lsof"), "native lsof unavailable")
    def test_native_holder_scan_detects_an_open_file(self):
        snap = {"path": str(self.wt), "gitdir": str(self.admin)}
        entry = list(FINISH.safety().file_identity((self.wt / "file").lstat()))
        with (self.wt / "file").open():
            with self.assertRaisesRegex(FINISH.Retain, "pending-departure"):
                FINISH.manual_holders(snap, {"entries": {"file": entry}}, {}, float("inf"))


class ManualHolderTests(unittest.TestCase):
    def setUp(self):
        self.snap = {"path": "/fixture/worktree", "gitdir": "/fixture/admin"}
        self.contents = {"entries": {"file": [9, 22, FINISH.stat.S_IFREG],
                                     "node_modules": [9, 24, FINISH.stat.S_IFLNK]}}
        self.admin = {"index": [[9, 23, FINISH.stat.S_IFREG], None]}

    def observe(self, output, *, errors=b"", code=0, failure=None):
        result = types.SimpleNamespace(stdout=output, stderr=errors, returncode=code, failure=failure)
        with mock.patch.object(FINISH.shutil, "which", return_value="/usr/sbin/lsof"), \
                mock.patch.object(FINISH.safety(), "supervise", return_value=result) as child:
            try:
                FINISH.manual_holders(self.snap, self.contents, self.admin, float("inf"))
            finally:
                self.assertEqual(child.call_count, 1)
                self.assertEqual(child.call_args.args[0], ["/usr/sbin/lsof", "-nP", "+w", "-F0pfnDi"])
                self.assertEqual(child.call_args.kwargs["limit"], 32 * 1024 * 1024)

    def test_no_matching_holders_accepts_optional_fields_and_preserves_symlink_targets(self):
        self.observe(b"p7\0\nf3\0D0x9\0i24\0n/shared/dependencies\0\n"
                     b"f4\0n/fixture/worktree-other/file\0\nf5\0nTCP localhost:80\0\n"
                     b"f6\0D0x8\0i22\0n/elsewhere/name\nwith-newline\0\nf7\0n\0\n")

    def test_inode_aliases_checkout_and_admin_cwd_and_fd_names_block(self):
        for record in (b"f3\0D0x9\0i22\0n/elsewhere/hardlink",
                       b"f4\0D0x9\0i23\0n/elsewhere/admin-alias",
                       b"fcwd\0n/fixture/worktree", b"fcwd\0n/fixture/admin",
                       b"f5\0n/fixture/worktree/file", b"f6\0n/fixture/admin/index",
                       b"f7\0n/fixture/worktree/nested/name\nwith-newline"):
            with self.subTest(record=record):
                with self.assertRaisesRegex(FINISH.Retain, "pending-departure"):
                    self.observe(b"p7\0\n" + record + b"\0\n")

    def test_compact_discard_inode_alias_is_still_a_holder(self):
        self.contents["discarded_inodes"] = [[9, 25]]
        with self.assertRaisesRegex(FINISH.Retain, "pending-departure"):
            self.observe(b"p7\0\nf3\0D0x9\0i25\0n/external/package-alias\0\n")

    def test_warnings_nonzero_timeout_and_output_limit_remain_unknown(self):
        output = b"p7\0\nf3\0n/elsewhere/file\0\n"
        for kwargs in ({"errors": b"lsof: permission denied\n"}, {"code": 1},
                       {"failure": TimeoutError()},
                       {"failure": FINISH.Retain("child-output-limit")}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(FINISH.Retain, "unknown|lsof-failed"):
                    self.observe(output, **kwargs)

    def test_empty_truncated_duplicate_malformed_and_unframed_records_refuse(self):
        for output in (b"", b"p7\0\n", b"f3\0n/elsewhere\0\n", b"p7\0\nf3\0n/elsewhere",
                       b"p7\0\nf3\0n/elsewhere\0n/duplicate\0\n",
                       b"p7\0\nf3\0Dno-device\0i22\0\n",
                       b"p7\0\nf3\0D0x9\0iinvalid\0\n",
                       b"p7\0\nn/elsewhere\0f3\0\n", b"p7\0\nf3\0xunexpected\0\n",
                       b"p7\0\nf3\0p8\0\n", b"pbad\0\nf3\0\n",
                       b"p7\0\nf3\0n/elsewhere\0\n\n"):
            with self.subTest(output=output):
                with self.assertRaisesRegex(FINISH.Retain, "visibility-unknown"):
                    self.observe(output)


class SiblingRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.before = [{"worktree": "/owner", "HEAD": "a", "branch": "refs/heads/main"},
                       {"worktree": "/sibling", "HEAD": "b", "branch": "refs/heads/topic",
                        "locked": "another-owner"}]

    def test_other_heads_new_registrations_and_order_are_independent(self):
        after = [dict(item, HEAD="new") for item in reversed(self.before)]
        after.append({"worktree": "/new", "HEAD": "c", "detached": ""})
        self.assertTrue(FINISH.siblings_preserved(self.before, after))

    def test_branch_and_detached_transitions_are_reported_operationally(self):
        after = [self.before[0], {"worktree": "/sibling", "HEAD": "new", "detached": "",
                                  "locked": "another-owner"}]
        report = FINISH.sibling_comparison(self.before, after)
        self.assertTrue(report["preserved"])
        self.assertEqual(report["changes"][0]["kind"], "operational")
        self.assertEqual(set(report["changes"][0]["fields"]), {"HEAD", "branch", "detached"})

    def test_loss_lock_change_prunable_and_duplicate_remain_unknown(self):
        changes = [{"locked": "changed"}, {"prunable": "missing"}]
        cases = [self.before[:1], self.before + [self.before[0]], [{"HEAD": "missing-path"}]]
        cases.extend([self.before[0], {**self.before[1], **change}] for change in changes)
        for after in cases:
            with self.subTest(after=after):
                self.assertFalse(FINISH.siblings_preserved(self.before, after))
        self.assertFalse(FINISH.siblings_preserved(self.before + [self.before[0]], self.before))

    def test_replaced_or_rebound_identity_is_not_an_operational_change(self):
        identities = {row["worktree"]: {"path_id": [1, number], "gitdir": row["worktree"] + "/.git"}
                      for number, row in enumerate(self.before)}
        for changed in ({"path_id": [1, 99]}, {"gitdir": "/different/admin"}):
            after_ids = {**identities, "/sibling": {**identities["/sibling"], **changed}}
            report = FINISH.sibling_comparison(self.before, self.before, identities, after_ids)
            self.assertFalse(report["preserved"])
            self.assertEqual(report["changes"], [{"path": "/sibling", "kind": "identity"}])


class ReconciliationTests(unittest.TestCase):
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    finish = LifecycleTests.finish
    setUp = ReleaseTests.setUp
    tearDown = ReleaseTests.tearDown
    remove = FinalizedRemovalTests.remove

    def legacy_removal(self):
        native = FINISH.safety().supervise
        comparison = FINISH.sibling_comparison

        def concurrent_checkout(command, **kwargs):
            if "worktree" in command and "remove" in command:
                git(self.legacy, "checkout", "--detach")
            return native(command, **kwargs)

        def old_comparator(*args):
            report = comparison(*args)
            if report["changes"]:
                report["preserved"] = False
            return report

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=concurrent_checkout), \
                mock.patch.object(FINISH, "sibling_comparison", side_effect=old_comparator):
            self.assertEqual(self.remove()[1][0]["checkout"], "unknown")
        # Model the exact older wire format: the native child really ran, but
        # that producer retained only a Boolean, never after rows or inode proof.
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            item = FINISH.retirement(db, row)
            intent, result = json.loads(item["intent"]), json.loads(item["result"])
            intent.pop("sibling_identities")
            result["siblings_unchanged"] = result.pop("siblings_preserved")
            for key in ("siblings_after", "sibling_identities_after", "sibling_comparison"):
                result.pop(key)
            with db:
                db.execute("UPDATE retirement SET intent=?, result=? WHERE worktree_id=?",
                           (json.dumps(intent, sort_keys=True), json.dumps(result, sort_keys=True), row["id"]))
        self.refresh_binding()

    def refresh_binding(self):
        with FINISH.ledger(self.state) as db:
            self.original = dict(FINISH.retirement(db, FINISH.get_row(db, self.wt)))
        self.arguments = ["--intent-id", json.loads(self.original["intent"])["id"],
                          "--generation", str(self.original["generation"])]
        for name in ("intent", "result"):
            self.arguments += ["--" + name + "-sha256", hashlib.sha256(self.original[name].encode()).hexdigest()]

    def reconcile(self, *extra):
        with mock.patch.object(FINISH, "manual_holders"):
            return self.call("reconcile", *self.arguments, *extra)

    def assert_receipts_unchanged(self):
        with FINISH.ledger(self.state) as db:
            current = dict(FINISH.retirement(db, FINISH.get_row(db, self.wt)))
        self.assertEqual(current, self.original)

    def test_reconciles_current_absence_without_rewriting_original_failure_or_retrying(self):
        self.call("cancel", "--reason", "incorporated preparation")
        self.legacy_removal()
        native = FINISH.safety().supervise

        def no_removal(command, **kwargs):
            self.assertFalse("worktree" in command and any(a in command for a in ("remove", "unlock", "prune")))
            return native(command, **kwargs)

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=no_removal):
            code, rows = self.reconcile()
            again = self.reconcile()[1][0]
        self.assertEqual(code, 0)
        result = rows[0]
        self.assertEqual(result, again)
        self.assertEqual(result["checkout"], "unknown")
        self.assertEqual(result["retirement_state"], "incomplete")
        self.assertEqual(result["current_disposition"], "reconciled-target-removed")
        fact = result["reconciliation"]
        self.assertEqual(fact["checkout"], "removed")
        self.assertEqual(fact["historical_sibling_preservation"], "unresolved")
        self.assertEqual(fact["historical_sibling_identities"], "unavailable")
        self.assertEqual(fact["historical_after_snapshot"], "unavailable")
        self.assertEqual(fact["sibling_comparison"]["changes"][0]["kind"], "operational")
        self.assert_receipts_unchanged()
        status = self.call("status")[1][0]
        self.assertEqual(status["reconciliation"], fact)
        self.assertEqual(status["current_disposition"], "reconciled-target-removed")
        self.assertEqual(status["reason"], "target-removal-reconciled-original-proof-retained")
        with mock.patch.object(FINISH, "remove_with_intent") as remove:
            for command, options in (("status", ("--all",)), ("check", ()),
                                     ("check", ("--all",)), ("check", ("--apply",))):
                with self.subTest(command=command, options=options):
                    records = self.call(command, *options)[1]
                    projected = next(record for record in records if record["worktree"] == str(self.wt))
                    self.assertEqual(projected["current_disposition"], "reconciled-target-removed")
                    self.assertEqual(projected["reason"], "target-removal-reconciled-original-proof-retained")
                    if "--all" in options:
                        self.assertEqual(len(records), 2)  # Historical unknown does not stop the batch.
            remove.assert_not_called()
        self.assert_receipts_unchanged()
        with self.assertRaisesRegex(FINISH.Retain, "intent"):
            self.call("resume")

    def test_wrong_exact_binding_and_options_refuse(self):
        self.legacy_removal()
        for flag, value in (("--intent-id", str(uuid.uuid4())), ("--generation", "99"),
                            ("--intent-sha256", "0" * 64), ("--result-sha256", "0" * 64),
                            ("--owner", "other-owner")):
            with self.subTest(flag=flag), self.assertRaises(FINISH.Retain):
                self.reconcile(flag, value)
        for extra in (("--all",), ("--apply",), ("--finalized",), ("--discard-ignored", "node_modules")):
            with self.subTest(extra=extra), self.assertRaises(FINISH.Retain):
                self.reconcile(*extra)
        self.assert_receipts_unchanged()

    def test_recreated_target_and_admin_refuse(self):
        self.legacy_removal()
        for path in (self.wt, self.admin):
            path.mkdir()
            try:
                with self.assertRaisesRegex(FINISH.Retain, "target-or-admin-present"):
                    self.reconcile()
            finally:
                path.rmdir()
        self.assert_receipts_unchanged()

    def test_target_visibility_errors_refuse_in_both_observations_without_recording_a_fact(self):
        self.legacy_removal()
        native_lstat, native_supervise = os.lstat, FINISH.safety().supervise

        def no_removal(command, **kwargs):
            self.assertFalse("worktree" in command and any(a in command for a in ("remove", "unlock", "prune")))
            return native_supervise(command, **kwargs)

        for target, path in (("checkout", self.wt), ("admin", self.admin)):
            for error in (errno.EACCES, errno.EIO):
                for failure_pass in (1, 2):
                    with self.subTest(target=target, error=error, observation=failure_pass):
                        observation, failed_probes = 1, []

                        def unavailable(name, *args, **kwargs):
                            if Path(name) == path and observation == failure_pass:
                                failed_probes.append(name)
                                raise OSError(error, "fixture target visibility unavailable")
                            return native_lstat(name, *args, **kwargs)

                        def next_observation(*args):
                            nonlocal observation
                            observation = 2

                        with mock.patch.object(os, "lstat", side_effect=unavailable), \
                                mock.patch.object(FINISH, "manual_holders", side_effect=next_observation), \
                                mock.patch.object(FINISH.safety(), "supervise", side_effect=no_removal):
                            with self.assertRaises(OSError) as caught:
                                self.call("reconcile", *self.arguments)
                        self.assertEqual(caught.exception.errno, error)
                        self.assertEqual(len(failed_probes), 1)
                        self.assert_receipts_unchanged()
                        with FINISH.ledger(self.state) as db:
                            self.assertIsNone(FINISH.reconciliation(db, FINISH.get_row(db, self.wt)))

    def test_failed_or_unjoined_original_child_cannot_be_reconciled(self):
        self.legacy_removal()
        original_result = self.original["result"]
        for change in ({"returncode": 1}, {"reaped": False}, {"group_absent": False}, {"pid": None}):
            with FINISH.ledger(self.state) as db:
                row = FINISH.get_row(db, self.wt)
                result = json.loads(original_result)
                result["child"].update(change)
                with db:
                    db.execute("UPDATE retirement SET result=? WHERE worktree_id=?",
                               (json.dumps(result, sort_keys=True), row["id"]))
            self.refresh_binding()
            with self.assertRaisesRegex(FINISH.Retain, "original-removal-not-qualified"):
                self.reconcile()

    def test_live_child_holder_and_target_ref_changes_refuse(self):
        self.legacy_removal()
        with mock.patch.object(FINISH.os, "kill", return_value=None):
            with self.assertRaisesRegex(FINISH.Retain, "child-or-group-present"):
                self.reconcile()
        with mock.patch.object(FINISH, "manual_holders", side_effect=FINISH.Retain("pending-departure")):
            with self.assertRaisesRegex(FINISH.Retain, "pending-departure"):
                self.call("reconcile", *self.arguments)
        lock = self.repo / ".git/refs/heads/released-feature.lock"
        lock.write_text("writer\n")
        with self.assertRaisesRegex(FINISH.Retain, "preserved-ref-changed"):
            self.reconcile()
        lock.unlink()
        git(self.repo, "update-ref", "refs/heads/released-feature", "0" * 40)
        with self.assertRaises(FINISH.Retain):
            self.reconcile()
        self.assert_receipts_unchanged()

    def assert_sibling_alert(self, kind):
        native = FINISH.safety().supervise

        def no_removal(command, **kwargs):
            self.assertFalse("worktree" in command and any(a in command for a in ("remove", "unlock", "prune")))
            return native(command, **kwargs)

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=no_removal):
            code, rows = self.reconcile()
            self.assertEqual(code, 0)
            result = rows[0]
            self.assertEqual(result["current_disposition"], "reconciled-target-removed")
            self.assertEqual(result["sibling_alert"], "current-sibling-preservation-unresolved")
            self.assertEqual(result["checkout"], "unknown")
            self.assertEqual(result["retirement_state"], "incomplete")
            report = result["reconciliation"]["sibling_comparison"]
            self.assertFalse(report["preserved"])
            self.assertTrue(any(change["path"] == str(self.legacy) and change["kind"] == kind
                                for change in report["changes"]))
            for command, options in (("status", ()), ("check", ()),
                                     ("check", ("--all",)), ("check", ("--apply",))):
                record = next(row for row in self.call(command, *options)[1]
                              if row["worktree"] == str(self.wt))
                self.assertEqual(record["sibling_alert"], result["sibling_alert"])
                self.assertEqual(record["reason"], "target-removal-reconciled-sibling-preservation-unresolved")
        self.assert_receipts_unchanged()

    def test_later_sibling_closeout_records_missing_observation_without_false_preservation(self):
        self.legacy_removal()
        git(self.repo, "worktree", "remove", str(self.legacy))
        self.assert_sibling_alert("missing")

    def test_later_sibling_lock_change_is_an_explicit_observation(self):
        self.legacy_removal()
        git(self.repo, "worktree", "lock", "--reason", "new owner", str(self.legacy))
        self.assert_sibling_alert("registration")

    def test_rebound_sibling_identity_stays_unresolved(self):
        self.legacy_removal()
        pointer = self.legacy / ".git"
        pointer.write_text("gitdir: " + str(self.repo / ".git") + "\n")
        self.assert_sibling_alert("identity-unavailable")

    def test_sibling_read_failure_is_observed_but_global_deadline_still_refuses(self):
        self.legacy_removal()
        capture = FINISH.sibling_identity

        def unreadable(snap, path, until, admin_index=None):
            if path == str(self.legacy):
                raise PermissionError(errno.EACCES, "fixture sibling unavailable")
            return capture(snap, path, until, admin_index)

        with mock.patch.object(FINISH, "sibling_identity", side_effect=FINISH.Retain("admission-deadline")):
            with self.assertRaisesRegex(FINISH.Retain, "admission-deadline"):
                self.reconcile()
        with mock.patch.object(FINISH, "sibling_identity", side_effect=unreadable):
            self.assert_sibling_alert("identity-unavailable")
        with FINISH.ledger(self.state) as db:
            fact = FINISH.reconciliation(db, FINISH.get_row(db, self.wt))
        error = next(item for item in fact["sibling_comparison"]["changes"]
                     if item["kind"] == "identity-unavailable")
        self.assertEqual(error["errno"], errno.EACCES)

    def test_cross_pass_sibling_replacement_records_alert_without_erasing_target_fact(self):
        self.legacy_removal()

        def replace_during_holder_check(*args):
            prior = self.base / "prior-sibling"
            self.legacy.rename(prior)
            shutil.copytree(prior, self.legacy)

        with mock.patch.object(FINISH, "manual_holders", side_effect=replace_during_holder_check):
            code, rows = self.call("reconcile", *self.arguments)
        self.assertEqual(code, 0)
        self.assertEqual(rows[0]["current_disposition"], "reconciled-target-removed")
        self.assertEqual(rows[0]["sibling_alert"], "current-sibling-preservation-unresolved")
        comparison = rows[0]["reconciliation"]["sibling_comparison"]
        self.assertFalse(comparison["preserved"])
        self.assertFalse(comparison["during_observation"]["preserved"])
        self.assertIn({"path": str(self.legacy), "kind": "identity"}, comparison["during_observation"]["changes"])
        self.assert_receipts_unchanged()

    def test_repeat_with_new_sibling_gap_retains_first_fact_and_reports_observation_conflict(self):
        self.legacy_removal()
        first = self.reconcile()[1][0]["reconciliation"]
        with mock.patch.object(FINISH, "sibling_identity", side_effect=OSError("fixture unavailable")):
            with self.assertRaisesRegex(FINISH.Retain, "recorded-sibling-observation-changed"):
                self.reconcile()
        git(self.repo, "worktree", "remove", str(self.legacy))
        with self.assertRaisesRegex(FINISH.Retain, "recorded-sibling-observation-changed"):
            self.reconcile()
        self.assertEqual(self.call("status")[1][0]["reconciliation"], first)
        self.assert_receipts_unchanged()


class FailureDiagnosticTests(unittest.TestCase):
    def call_cli(self, error=None, action=None):
        output, errors = io.StringIO(), io.StringIO()
        def invoke(argv):
            if error is not None:
                raise error
            return action(argv) if action else 0
        with mock.patch.object(FINISH, "main", side_effect=invoke), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = FINISH.cli(["finish"])
        return status, output.getvalue(), errors.getvalue()

    def test_os_errors_keep_category_and_errno_without_private_text(self):
        for code in (errno.ENOSPC, errno.EACCES):
            with self.subTest(code=code):
                status, output, errors = self.call_cli(OSError(code, "private-secret", "/private/secret-file"))
                self.assertEqual(status, 1)
                self.assertEqual(output, "")
                self.assertIn('"category": "os-error"', errors)
                self.assertIn('"errno": ' + str(code), errors)
                self.assertIn("(state-or-proof-unavailable)", errors)
                self.assertNotIn("private-secret", errors)
                self.assertNotIn("/private/", errors)

    def test_malformed_github_proof_keeps_owner_phase_without_body(self):
        def invoke(_):
            with mock.patch.object(FINISH, "run", return_value=types.SimpleNamespace(stdout=b"private-secret")):
                return FINISH.pr("https://github.com/example/repo/pull/1")
        status, output, errors = self.call_cli(action=invoke)
        self.assertEqual((status, output), (1, ""))
        self.assertIn('"category": "value-error"', errors)
        self.assertIn('"phase": "github-pr-proof"', errors)
        self.assertNotIn("private-secret", errors)
        self.assertNotIn("example/repo", errors)

    def test_completion_transaction_failure_reports_sqlite_code_and_rolls_back(self):
        with contextlib.closing(sqlite3.connect(":memory:")) as db:
            db.row_factory = sqlite3.Row
            db.executescript("""
                CREATE TABLE worktrees(id TEXT PRIMARY KEY, finish_head TEXT, primary_pr TEXT,
                    target TEXT, dependencies TEXT, state TEXT, reason TEXT);
                CREATE TABLE owners(worktree_id TEXT, owner TEXT, completed INTEGER);
                INSERT INTO worktrees VALUES('fixture',NULL,NULL,NULL,NULL,'active','owner-active');
                INSERT INTO owners VALUES('fixture','owner-1',0);
            """)
            row = db.execute("SELECT * FROM worktrees").fetchone()
            before = dict(row)
            caught = []
            db.set_authorizer(lambda op, table, _column, _database, _trigger:
                sqlite3.SQLITE_DENY if op == sqlite3.SQLITE_UPDATE and table == "worktrees" else sqlite3.SQLITE_OK)
            def invoke(_):
                args = types.SimpleNamespace(pr="fixture-pr", target="main", wait_for=[])
                with mock.patch.object(FINISH, "revalidate", return_value={"repo":"example/repo","head":"a"*40}), \
                        mock.patch.object(FINISH, "owner_id", return_value="owner-1"), \
                        mock.patch.object(FINISH, "pr", return_value={"repo":"example/repo","head":"a"*40,"url":"fixture-pr","target":"main"}):
                    try:
                        return FINISH.finish(db, row, args)
                    except sqlite3.Error as error:
                        caught.append(error)
                        raise
            status, output, errors = self.call_cli(action=invoke)
            db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
            self.assertEqual((status, output), (1, ""))
            self.assertIn('"category": "sqlite-error"', errors)
            self.assertEqual(len(caught), 1)
            code = getattr(caught[0], "sqlite_errorcode", None)
            if type(code) is int:
                self.assertIn('"sqlite_errorcode": ' + str(code), errors)
            else:
                self.assertNotIn('"sqlite_errorcode"', errors)
            self.assertIn('"phase": "completion"', errors)
            self.assertEqual(dict(db.execute("SELECT * FROM worktrees").fetchone()), before)
            self.assertEqual(db.execute("SELECT completed FROM owners").fetchone()[0], 0)

    def test_value_error_hides_its_message(self):
        status, output, errors = self.call_cli(ValueError("private-secret"))
        self.assertEqual((status, output), (1, ""))
        self.assertIn('"category": "value-error"', errors)
        self.assertNotIn("private-secret", errors)

    def test_custom_exception_metadata_is_not_printed(self):
        class PrivateSecretError(OSError):
            pass
        error = PrivateSecretError("private-secret")
        error.errno = "private-secret"
        error.sqlite_errorcode = True
        status, output, errors = self.call_cli(error)
        self.assertEqual((status, output), (1, ""))
        self.assertIn('"category": "os-error"', errors)
        self.assertNotIn("PrivateSecret", errors)
        self.assertNotIn("private-secret", errors)
        self.assertNotIn('"errno"', errors)
        self.assertNotIn('"sqlite_errorcode"', errors)

    def test_sqlite_error_without_extended_attributes_is_supported(self):
        status, output, errors = self.call_cli(sqlite3.OperationalError("private-secret"))
        self.assertEqual((status, output), (1, ""))
        self.assertIn('"category": "sqlite-error"', errors)
        self.assertNotIn("sqlite_errorcode", errors)
        self.assertNotIn("private-secret", errors)

    def test_explicit_inner_phase_survives_outer_phase(self):
        def invoke(_):
            with FINISH.diagnostic_phase("dispatch"), FINISH.diagnostic_phase("state-session"):
                raise OSError(errno.ENOSPC, "private-secret")
        status, output, errors = self.call_cli(action=invoke)
        self.assertEqual((status, output), (1, ""))
        self.assertIn('"phase": "state-session"', errors)

    def test_undecorated_body_failure_is_a_state_session_not_ledger_failure(self):
        original_main = FINISH.main
        db = mock.Mock()
        db.execute.return_value = [{}]
        @contextlib.contextmanager
        def session(*args, **kwargs):
            yield db
        def invoke(_):
            with mock.patch.object(FINISH, "safety", return_value=types.SimpleNamespace()), \
                    mock.patch.object(FINISH, "ledger", side_effect=session), \
                    mock.patch.object(FINISH, "summary", side_effect=ValueError("private-secret")):
                return original_main(["status", "--all"])
        status, output, errors = self.call_cli(action=invoke)
        self.assertEqual((status, output), (1, ""))
        self.assertIn('"phase": "state-session"', errors)
        self.assertNotIn("state-ledger", errors)
        self.assertNotIn("private-secret", errors)

    def test_unapproved_phase_is_not_rendered(self):
        for phase in ("private-secret", ["private-secret"]):
            with self.subTest(phase=phase):
                error = ValueError("private-secret")
                error._gwt_failure_phase = phase
                status, output, errors = self.call_cli(error)
                self.assertEqual((status, output), (1, ""))
                self.assertIn('"phase": "dispatch"', errors)
                self.assertNotIn("private-secret", errors)

    def test_retain_reason_and_exit_remain_exact(self):
        status, output, errors = self.call_cli(FINISH.Retain("lifecycle-busy"))
        self.assertEqual((status, output), (1, ""))
        self.assertEqual(errors, "gwt finish: stopped; inspect finish-status for checkout outcome (lifecycle-busy)\n")

    def test_success_output_and_exit_remain_exact(self):
        def invoke(_):
            print('[{"checkout": "retained"}]')
            return 0
        self.assertEqual(self.call_cli(action=invoke), (0, '[{"checkout": "retained"}]\n', ""))

    def test_unhandled_exception_still_propagates(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected"):
            self.call_cli(RuntimeError("unexpected"))


if __name__ == "__main__":
    unittest.main()
