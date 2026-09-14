#!/usr/bin/env python3
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
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


class WrapperTests(unittest.TestCase):
    def test_unavailable_release_remove_and_apply_need_no_state(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            for args, reason in (
                (("release",), "release-unavailable-checkout-retained"),
                (("remove",), "removal-unavailable-checkout-retained"),
                (("check", "--apply"), "removal-unavailable-checkout-retained"),
            ):
                result = subprocess.run(
                    [str(TOOL), *args, "--state-dir", str(state)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn(reason, result.stderr)
                self.assertFalse(state.exists())

    def test_holder_qualification_is_explicitly_unavailable_and_probe_free(self):
        from contextlib import redirect_stdout

        output = io.StringIO()
        with mock.patch.object(FINISH, "ledger") as ledger, \
                mock.patch.object(FINISH, "run") as run, redirect_stdout(output):
            self.assertEqual(FINISH.main(["holder-qualification"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {
            "schema": FINISH.HOLDER_SCHEMA,
            "backend": "none",
            "qualified": False,
            "reason": "release-and-removal-unavailable",
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

    def test_gwt_wrapper_enrolls_tracks_and_denies_release(self):
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
if gwt release --worktree "$3"; then exit 92; fi
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
            self.assertIn("managed release/removal is unavailable", result.stdout)
            self.assertIn("checkout retained", result.stdout)
            self.assertIn("checkout retained", result.stderr)
            recorded = calls.read_text()
            self.assertIn("enroll --worktree", recorded)
            self.assertIn("finish --pr", recorded)
            self.assertGreaterEqual(recorded.count("resume --if-enrolled"), 2)
            self.assertNotIn("release ", recorded)
            self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
