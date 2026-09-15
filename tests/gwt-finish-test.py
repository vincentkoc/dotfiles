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
        self.boundary = mock.patch.object(FINISH.safety(), "access_boundary", return_value=[])
        self.boundary.start()
        self.policy = mock.patch.object(FINISH, "qualified_policy", return_value={
            "git": {"path": str(Path(shutil.which("git")).resolve())}, "known_processes": []})
        self.policy.start()

    def tearDown(self):
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
        with mock.patch.object(FINISH.safety(), "PASS_LIMIT", 32 * 1024 * 1024):
            with self.assertRaisesRegex(FINISH.Retain, "size"):
                FINISH.safety().inventory(snap, FINISH.git, time.monotonic() + 10)
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
