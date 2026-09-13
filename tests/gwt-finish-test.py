#!/usr/bin/env python3
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "bin/agent-worktree-ops/agent-worktree-finish"
SPEC = importlib.util.spec_from_loader("gwt_finish", SourceFileLoader("gwt_finish", str(TOOL)))
FINISH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FINISH)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home), "CODEX_HOME": str(self.home / ".codex"), "CODEX_THREAD_ID": "", "GWT_OWNER_ID": "owner-1"})
        self.env.start()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Fixture")
        git(self.repo, "config", "user.email", "fixture@example.test")
        git(self.repo, "config", "commit.gpgsign", "false")
        git(self.repo, "remote", "add", "origin", "https://github.com/example/repo.git")
        (self.repo / ".gitignore").write_text("node_modules\nignored\n")
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
        self.proof_patch = mock.patch.object(FINISH, "pr", side_effect=lambda url: dict(self.proofs[url]))
        self.proof_patch.start()
        self.target_patch = mock.patch.object(FINISH, "default_target", return_value="main")
        self.target_patch.start()
        # Positive lifecycle fixtures exercise the remaining gates; this does
        # not qualify the shipped holder backend or provide a runtime bypass.
        self.qualification_patch = mock.patch.object(FINISH, "holder_qualification", return_value={
            "schema": FINISH.HOLDER_SCHEMA, "backend": "fixture", "qualified": True, "reason": ""})
        self.qualification_patch.start()
        self.holder_patch = mock.patch.object(FINISH, "holders", return_value=False)
        self.holders = self.holder_patch.start()
        self.enroll()

    def tearDown(self):
        self.holder_patch.stop()
        self.qualification_patch.stop()
        self.target_patch.stop()
        self.proof_patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def proof(self, url, **kwargs):
        return {"url": url, "repo": "example/repo", "head": self.head, "target": "main",
                "merged": True, "state": "closed", "merge": "a" * 40, **kwargs}

    def call(self, command, *args):
        from contextlib import redirect_stdout
        import io
        output = io.StringIO()
        with redirect_stdout(output):
            result = FINISH.main([command, "--worktree", str(self.wt), "--state-dir", str(self.state),
                                  "--managed-root", str(self.root), *args])
        return result, json.loads(output.getvalue() or "null")

    def enroll(self):
        self.call("enroll")

    def finish_release(self, *args):
        self.call("finish", "--pr", self.url, *args)
        self.call("release", "--recovery-reviewed")

    def check_reason(self, *args):
        return self.call("check", *args)[1][0]["reason"]

    def authorize(self):
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            snap = json.loads(row["identity"])
        policy = self.base / "policy.json"
        policy.write_text(json.dumps({"schema": FINISH.SCHEMA, "mode": "apply", "host": FINISH.host_key(),
                                     "repositories": [{"owner": snap["owner"], "common_id": snap["common_id"], "root": snap["root"]}]}))
        policy.chmod(0o600)
        return str(policy)

    def test_finish_is_not_release_and_resume_invalidates_finish(self):
        self.call("finish", "--pr", self.url)
        self.assertEqual(self.check_reason(), "owner-not-released")
        self.call("release", "--recovery-reviewed")
        self.assertEqual(self.check_reason(), "eligible-report-only")
        self.assertTrue(self.wt.exists())
        self.call("resume")
        self.assertEqual(self.check_reason(), "active")

    def test_all_owners_must_release_and_pins_are_owner_scoped(self):
        self.call("resume", "--owner", "owner-2")
        self.call("pin", "--reason", "saved recovery")
        self.call("finish", "--pr", self.url)
        with self.assertRaisesRegex(FINISH.Retain, "recovery-pin"):
            self.call("release", "--recovery-reviewed")
        with self.assertRaisesRegex(FINISH.Retain, "no-matching-owner-pin"):
            self.call("unpin", "--owner", "owner-2", "--reason", "saved recovery")
        self.call("unpin", "--reason", "saved recovery")
        self.call("release", "--recovery-reviewed")
        self.assertEqual(self.check_reason(), "owner-not-released")
        self.call("release", "--owner", "owner-2", "--recovery-reviewed")
        self.assertEqual(self.check_reason(), "eligible-report-only")

    def test_pending_closed_unmerged_and_auto_merge_do_not_retire(self):
        self.finish_release()
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        self.assertEqual(self.check_reason(), "pr-not-merged")
        self.proofs[self.url]["state"] = "closed"
        self.assertEqual(self.check_reason(), "pr-closed-unmerged")

    def test_stack_tracks_distinct_heads_and_requires_explicit_refresh(self):
        upper = "https://github.com/example/repo/pull/2"
        self.proofs[upper] = self.proof(upper, head="b" * 40, merged=False, state="open")
        self.finish_release("--wait-for", upper)
        self.assertEqual(self.check_reason(), "pr-not-merged")
        self.proofs[upper].update(merged=True, state="closed")
        self.assertEqual(self.check_reason(), "eligible-report-only")
        self.proofs[upper]["head"] = "c" * 40
        self.assertEqual(self.check_reason(), "stack-proof-changed-repeat-finish")

    def test_stack_merge_into_an_intermediate_branch_is_not_final_proof(self):
        upper = "https://github.com/example/repo/pull/2"
        self.proofs[upper] = self.proof(upper, head="b" * 40, target="temporary-stack")
        with self.assertRaisesRegex(FINISH.Retain, "stack-pr-not-targeting-final"):
            self.call("finish", "--pr", self.url, "--wait-for", upper)

    def test_wrong_final_target_and_primary_head_are_rejected(self):
        self.proofs[self.url]["target"] = "stack-base"
        with self.assertRaisesRegex(FINISH.Retain, "final-branch"):
            self.call("finish", "--pr", self.url)
        self.proofs[self.url].update(target="main", head="b" * 40)
        with self.assertRaisesRegex(FINISH.Retain, "head-mismatch"):
            self.call("finish", "--pr", self.url)

    def test_head_change_and_recreated_registration_retain(self):
        self.finish_release()
        (self.wt / "file").write_text("changed\n")
        git(self.wt, "commit", "-am", "change")
        self.assertEqual(self.check_reason(), "unfinished-or-head-changed")
        git(self.repo, "worktree", "remove", str(self.wt))
        git(self.repo, "worktree", "add", str(self.wt), "feature")
        self.assertEqual(self.check_reason(), "worktree-identity-changed")

    def test_live_holders_and_missing_recovery_review_block_release(self):
        self.call("finish", "--pr", self.url)
        with self.assertRaisesRegex(FINISH.Retain, "review-recovery"):
            self.call("release")
        self.holders.return_value = True
        with self.assertRaisesRegex(FINISH.Retain, "still-holds"):
            self.call("release", "--recovery-reviewed")

    def test_unqualified_backend_cannot_release_an_owner(self):
        self.call("finish", "--pr", self.url)
        self.qualification_patch.stop()
        with self.assertRaisesRegex(FINISH.Retain, "holder-backend-unqualified"):
            self.call("release", "--recovery-reviewed")
        self.holders.assert_not_called()
        row = self.call("status")[1][0]
        self.assertEqual(row["state"], "finished")
        self.assertEqual([(item["released"], item["recovery_reviewed"]) for item in row["owners"]], [(0, 0)])

    def test_unqualified_backend_blocks_removal_before_lock_or_candidate_work(self):
        self.finish_release()
        policy = self.authorize()
        self.qualification_patch.stop()
        self.holders.reset_mock()
        with mock.patch.object(FINISH, "maintenance_lock") as lock, \
                mock.patch.object(FINISH, "candidate") as candidate:
            self.assertEqual(self.check_reason("--apply", "--policy", policy), "holder-backend-unqualified")
        lock.assert_not_called()
        candidate.assert_not_called()
        self.holders.assert_not_called()
        self.assertTrue(self.wt.exists())
        self.assertEqual(self.call("status")[1][0]["state"], "finished")

    def test_report_only_check_retains_an_unqualified_released_record(self):
        self.finish_release()
        self.qualification_patch.stop()
        self.holder_patch.stop()
        with mock.patch.object(FINISH, "run", wraps=FINISH.run) as calls:
            self.assertEqual(self.check_reason(), "holder-backend-unqualified")
        self.assertFalse(any(Path(call.args[0][0]).name == "lsof" for call in calls.call_args_list))
        self.assertTrue(self.wt.exists())

    def test_ignored_untracked_and_git_locks_retain(self):
        self.finish_release()
        for name in ("ignored", "untracked\nfile"):
            (self.wt / name).write_text("must survive")
            self.assertEqual(self.check_reason(), "dirty-untracked-or-ignored-artifacts")
            (self.wt / name).unlink()
        lock = Path(git(self.wt, "rev-parse", "--absolute-git-dir")) / "index.lock"
        lock.write_text("held")
        self.assertEqual(self.check_reason(), "git-lock-present")

    def test_new_dependency_symlink_cannot_be_assumed_disposable(self):
        self.finish_release()
        (self.repo / "node_modules").mkdir()
        (self.wt / "node_modules").symlink_to(self.repo / "node_modules", target_is_directory=True)
        self.assertEqual(self.check_reason(), "worktree-identity-changed")

    def test_gated_nonforce_removal_keeps_original_branch(self):
        self.finish_release()
        self.assertEqual(self.check_reason("--apply"), "apply-requires-reviewed-policy")
        policy = self.authorize()
        lock_parent = self.home / ".codex/locks"
        lock_parent.mkdir(mode=0o755, parents=True)
        lock_parent.chmod(0o755)
        with mock.patch.object(FINISH, "run", wraps=FINISH.run) as calls:
            self.assertEqual(self.check_reason("--apply", "--policy", policy), "removed")
        remove_args = [call.args[0] for call in calls.call_args_list if "remove" in call.args[0]]
        self.assertEqual(len(remove_args), 1)
        self.assertNotIn("--force", remove_args[0])
        self.assertNotIn("prune", remove_args[0])
        self.assertFalse(self.wt.exists())
        self.assertNotIn(str(self.wt), git(self.repo, "worktree", "list", "--porcelain"))
        self.assertEqual(git(self.repo, "rev-parse", "feature"), self.head)
        self.assertEqual(lock_parent.stat().st_mode & 0o777, 0o755)
        self.assertFalse((lock_parent / "agent-worktree-maintain.lock").exists())

    def test_explicit_signoff_has_no_age_gate(self):
        self.finish_release()
        policy = self.authorize()
        with FINISH.ledger(self.state) as db:
            db.execute("UPDATE worktrees SET observed_at = ?", (int(time.time()),))
            db.commit()
        self.assertEqual(self.check_reason("--apply", "--policy", policy), "removed")

    def test_existing_maintenance_lock_is_never_cleared(self):
        self.finish_release()
        policy = self.authorize()
        lock = self.home / ".codex/locks/agent-worktree-maintain.lock"
        lock.mkdir(mode=0o700, parents=True)
        lock.parent.chmod(0o700)
        (lock / "pid").write_text("999999\n")
        self.assertEqual(self.check_reason("--apply", "--policy", policy), "maintenance-lock-present")
        self.assertEqual((lock / "pid").read_text(), "999999\n")

    def test_crash_reconciles_absence_but_never_retries_removal(self):
        self.finish_release()
        with FINISH.ledger(self.state) as db:
            db.execute("UPDATE worktrees SET state = 'retiring'")
            db.commit()
        self.assertEqual(self.check_reason(), "retirement-interrupted-review-required")
        with self.assertRaisesRegex(FINISH.Retain, "retirement-in-progress"):
            self.call("resume")
        git(self.repo, "worktree", "remove", str(self.wt))
        self.assertEqual(self.check_reason(), "removed")

    def test_symlink_replacement_and_api_failure_retain(self):
        self.finish_release()
        self.proof_patch.stop()
        with mock.patch.object(FINISH, "pr", side_effect=FINISH.Retain("ghx-failed")):
            self.assertEqual(self.check_reason(), "ghx-failed")
        self.proof_patch.start()
        moved = self.root / "moved"
        self.wt.rename(moved)
        self.wt.symlink_to(moved, target_is_directory=True)
        self.assertEqual(self.check_reason(), "registration-missing-or-locked")
        self.assertTrue((moved / "file").exists())

    def test_github_request_is_explicit_uncached_and_does_not_use_shell(self):
        self.proof_patch.stop()
        payload = {"number": 1, "head": {"sha": self.head}, "base": {"ref": "main", "repo": {"full_name": "example/repo"}},
                   "merged": True, "merged_at": "2026-01-01T00:00:00Z", "state": "closed", "merge_commit_sha": "a" * 40}
        with mock.patch.object(FINISH, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b"")) as run:
            self.assertTrue(FINISH.pr(self.url)["merged"])
            run.assert_called_once_with(["ghx", "--no-cache", "api", "repos/example/repo/pulls/1"])
        with self.assertRaises(FINISH.Retain):
            FINISH.pr(self.url + ";touch bad")
        self.proof_patch.start()

    def test_final_local_revalidation_catches_a_late_write(self):
        self.finish_release()
        policy = self.authorize()
        original = FINISH.local_checks
        calls = 0

        def write_before_final_check(row):
            nonlocal calls
            calls += 1
            if calls == 3:
                (self.wt / "new evidence").write_text("keep")
            return original(row)

        with mock.patch.object(FINISH, "local_checks", side_effect=write_before_final_check):
            self.assertEqual(self.check_reason("--apply", "--policy", policy), "dirty-untracked-or-ignored-artifacts")
        self.assertTrue((self.wt / "new evidence").exists())
        self.assertEqual(self.call("status")[1][0]["state"], "finished")
        self.call("resume")
        self.assertEqual(self.check_reason(), "active")

    def test_recorded_dependency_symlink_target_survives_removal(self):
        other = self.root / "dependencies"
        git(self.repo, "worktree", "add", "-b", "dependencies", str(other))
        target = self.repo / "node_modules"
        target.mkdir()
        (target / "shared").write_text("preserve")
        (other / "node_modules").symlink_to(target, target_is_directory=True)
        self.wt = other
        self.enroll()
        self.finish_release()
        policy = self.authorize()
        self.assertEqual(self.check_reason("--apply", "--policy", policy), "removed")
        self.assertEqual((target / "shared").read_text(), "preserve")

    def test_release_attempts_immediate_policy_scoped_cleanup(self):
        self.call("finish", "--pr", self.url)
        policy = self.authorize()
        self.assertEqual(self.call("release", "--recovery-reviewed", "--policy", policy)[1][0]["state"], "removed")

    def test_worker_stops_after_ambiguous_removal_and_releases_row_lock(self):
        self.finish_release()
        with FINISH.ledger(self.state) as db:
            db.execute("UPDATE worktrees SET state = 'retiring'")
            db.commit()
        code, output = self.call("check", "--all", "--apply", "--policy", self.authorize())
        self.assertEqual(code, 1)
        self.assertEqual(output[0]["reason"], "retirement-interrupted-review-required")
        with FINISH.ledger(self.state) as db:
            self.assertIsNotNone(FINISH.get_row(db, self.wt))

    def test_untracked_recorded_symlink_is_retained_without_unlink(self):
        other = self.root / "directory-ignore"
        git(self.repo, "worktree", "add", "-b", "directory-ignore", str(other))
        (other / ".gitignore").write_text("node_modules/\n")
        git(other, "commit", "-am", "directory ignore")
        self.head = git(other, "rev-parse", "HEAD")
        self.proofs[self.url] = self.proof(self.url)
        target = self.repo / "node_modules"
        target.mkdir()
        (other / "node_modules").symlink_to(target, target_is_directory=True)
        self.wt = other
        self.enroll()
        self.finish_release()
        self.assertEqual(self.check_reason(), "managed-dependency-link-is-untracked")
        self.assertTrue((other / "node_modules").is_symlink())

    def test_runtime_thread_identity_overrides_ambient_shell_owner(self):
        args = FINISH.parser().parse_args(["status"])
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "live-thread", "GWT_OWNER_ID": "stale-shell"}):
            self.assertEqual(FINISH.owner_id(args), "live-thread")
            args.owner = "another-thread"
            with self.assertRaisesRegex(FINISH.Retain, "conflicts-with-live-thread"):
                FINISH.owner_id(args)

    def test_darwin_host_identity_is_stable_and_unavailable_identity_retains(self):
        with mock.patch.object(FINISH.sys, "platform", "darwin"), \
                mock.patch.object(FINISH, "run", return_value=subprocess.CompletedProcess([], 0, b"Stable-Mac\n", b"")) as run:
            self.assertEqual(FINISH.host_key(), "Stable-Mac")
            run.assert_called_once_with(["scutil", "--get", "LocalHostName"])
        with mock.patch.object(FINISH.sys, "platform", "darwin"), \
                mock.patch.object(FINISH, "run", side_effect=FINISH.Retain("scutil-failed")):
            with self.assertRaisesRegex(FINISH.Retain, "scutil-failed"):
                FINISH.host_key()

    def test_holder_parser_retains_selected_inode_aliases_and_newlines(self):
        self.holder_patch.stop()
        with mock.patch.object(FINISH, "run", return_value=subprocess.CompletedProcess([], 0, b"p123\0\nfcwd\0n/alias/line\nbreak\0", b"")) as run:
            self.assertTrue(FINISH.holders(str(self.wt)))
            run.assert_called_once_with(["lsof", "-nP", "-F0pn", "+D", str(self.wt)], allowed=(0, 1))
        with mock.patch.object(FINISH, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"")):
            self.assertFalse(FINISH.holders(str(self.wt)))
        with mock.patch.object(FINISH, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"incomplete")):
            with self.assertRaisesRegex(FINISH.Retain, "scan-incomplete"):
                FINISH.holders(str(self.wt))
        self.holder_patch.start()

    def test_inherited_git_locator_and_config_cannot_redirect_proof(self):
        self.finish_release()
        env = {"GIT_DIR": str(self.repo / ".git"), "GIT_WORK_TREE": str(self.repo),
               "GIT_INDEX_FILE": str(self.base / "foreign-index"), "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "core.worktree", "GIT_CONFIG_VALUE_0": str(self.repo)}
        (self.wt / "file").write_text("dirty in actual target")
        with mock.patch.dict(os.environ, env):
            self.assertEqual(self.check_reason(), "dirty-untracked-or-ignored-artifacts")
        self.assertFalse((self.base / "foreign-index").exists())

    def test_git_proofs_deny_transport_even_when_environment_and_config_allow_it(self):
        with mock.patch.dict(os.environ, {"GIT_ALLOW_PROTOCOL": "file"}):
            result = FINISH.run(["git", "-c", "protocol.file.allow=always", "ls-remote", str(self.repo)], allowed=(128,))
        self.assertIn(b"transport 'file' not allowed", result.stderr)

    def test_policy_revocation_during_proof_prevents_removal(self):
        self.finish_release()
        policy = Path(self.authorize())
        original = FINISH.local_checks
        calls = 0

        def revoke_at_final_check(row):
            nonlocal calls
            calls += 1
            if calls == 3:
                policy.unlink()
            return original(row)

        with mock.patch.object(FINISH, "local_checks", side_effect=revoke_at_final_check):
            self.assertEqual(self.check_reason("--apply", "--policy", str(policy)), "apply-requires-reviewed-policy")
        self.assertTrue(self.wt.exists())

    def test_missing_storage_retains_before_git_or_github_probes(self):
        self.finish_release()
        with mock.patch.object(FINISH, "storage_guard", side_effect=FINISH.Retain("storage-unavailable")), \
                mock.patch.object(FINISH, "local_checks") as local:
            self.assertEqual(self.check_reason(), "storage-unavailable")
            local.assert_not_called()

    def test_wrapper_enrollment_finish_parking_release_and_existing_refusal(self):
        tools = self.base / "fake-bin"
        tools.mkdir()
        payload = {"number": 1, "head": {"sha": self.head}, "base": {"ref": "main", "repo": {"full_name": "example/repo"}},
                   "merged": True, "merged_at": "2026-01-01T00:00:00Z", "state": "closed", "merge_commit_sha": "a" * 40}
        (tools / "ghx").write_text("#!/usr/bin/env python3\nimport json,sys\nprint(" + repr(json.dumps(payload)) + " if '/pulls/' in sys.argv[-1] else '{\"full_name\":\"example/repo\",\"default_branch\":\"main\"}')\n")
        (tools / "lsof").write_text("#!/bin/sh\nexit 1\n")
        for tool in tools.iterdir():
            tool.chmod(0o755)
        wrapper_state = self.base / "wrapper-state"
        wrapper_tree = self.root / "example-repo" / "wrapper"
        env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"],
                   DOTFILES_WORKTREES_ROOT=str(self.root), DOTFILES_GWT_FINISH_STATE=str(wrapper_state), TMUX="")
        script = '''source "$1"
cd "$2"
gwt new wrapper HEAD --finish-managed || exit
[[ "$PWD" == "$3" ]] || exit 91
gwt finish --pr https://github.com/example/repo/pull/1 || exit
[[ "$PWD" == "$2" ]] || exit 92
if gwt release --worktree "$3" --recovery-reviewed; then exit 94; fi
gwt cd wrapper || exit
gwt finish-status || exit
if gwt new wrapper HEAD --finish-managed; then exit 93; fi
'''
        result = subprocess.run(["zsh", "-f", "-c", script, "zsh", str(ROOT / "functions/gwt/gwt.zsh"), str(self.repo), str(wrapper_tree)],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"reason": "owner-resumed"', result.stdout)
        self.assertIn("holder-backend-unqualified", result.stderr)
        self.assertTrue(wrapper_tree.exists())


class WrapperTests(unittest.TestCase):
    def test_qualification_has_no_state_lock_or_probe_side_effects(self):
        from contextlib import redirect_stdout
        import io
        output = io.StringIO()
        with mock.patch.object(FINISH, "ledger") as ledger, \
                mock.patch.object(FINISH, "run") as run, redirect_stdout(output):
            self.assertEqual(FINISH.main(["holder-qualification"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {
            "schema": FINISH.HOLDER_SCHEMA, "backend": "lsof-recursive",
            "qualified": False, "reason": "holder-backend-unqualified"})
        ledger.assert_not_called()
        run.assert_not_called()

    def test_unqualified_backend_never_accepts_an_empty_lsof_result(self):
        for platform in ("darwin", "linux", "win32"):
            with self.subTest(platform=platform), mock.patch.object(FINISH.sys, "platform", platform), \
                    mock.patch.object(FINISH, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"")) as run:
                with self.assertRaisesRegex(FINISH.Retain, "holder-backend-unqualified"):
                    FINISH.holders("/fixture/worktree")
                run.assert_not_called()

    def test_absent_status_is_a_noop_and_help_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = dict(os.environ, HOME=str(root), DOTFILES_GWT_FINISH_STATE=str(root / "state"))
            for args in (("status", "--all"), ("check", "--all")):
                result = subprocess.run([str(TOOL), *args], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "[]\n")
                self.assertFalse((root / "state").exists())
            result = subprocess.run(["zsh", "-f", "-c", 'source "$1"; gwt finish --help', "zsh", str(ROOT / "functions/gwt/gwt.zsh")],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("gwt finish --pr", result.stdout)
            self.assertFalse((root / "state").exists())
            result = subprocess.run(["zsh", "-f", "-c", 'source "$1"; gwt finish-check --all --apply', "zsh", str(ROOT / "functions/gwt/gwt.zsh")],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("report-only", result.stderr)
            self.assertFalse((root / "state").exists())


if __name__ == "__main__":
    unittest.main()
