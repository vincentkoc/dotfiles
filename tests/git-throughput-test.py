#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest


HELPER = Path(__file__).resolve().parents[1] / "bin/git-throughput"


class ThroughputTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo with spaces"
        self.remote = self.root / "remote"
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
        self.git("init", "-q", str(self.repo), cwd=self.root)
        self.git("init", "-q", "--bare", str(self.remote), cwd=self.root)
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("commit", "--allow-empty", "-qm", "fixture")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("remote", "add", "review", str(self.remote))
        self.git("branch", "active")
        self.git("config", "branch.active.remote", "review")
        self.git("config", "branch.active.merge", "refs/heads/topic")
        self.git("config", "gc.auto", "0")
        self.git("config", "maintenance.auto", "false")
        self.git("tag", "fixture")
        self.git("push", "-q", "origin", "HEAD:refs/heads/main", "HEAD:refs/heads/topic", "--tags")
        self.config = self.repo / ".git/config"
        self.config.chmod(0o640)
        self.before = self.config.read_bytes()
        self.pre_sha = self.sha()
        self.receipt = self.root / "receipt"

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env,
                              check=True, capture_output=True).stdout

    def sha(self):
        return hashlib.sha256(self.config.read_bytes()).hexdigest()

    def run_helper(self, *args, ok=True):
        result = subprocess.run(["python3", str(HELPER), *map(str, args)], env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        return result

    def apply(self, receipt=None, expected=None, ok=True):
        return self.run_helper("apply", "--repo", self.repo, "--expect-sha256",
                               expected or self.pre_sha, "--receipt", receipt or self.receipt,
                               "--review-remote", "review", ok=ok)

    def test_apply_idempotent_preserves_refs_and_settings(self):
        refs = self.git("show-ref")
        for key in ("remote.origin.fetch", "remote.review.fetch", "remote.origin.url",
                    "remote.review.url", "branch.active.remote", "branch.active.merge",
                    "maintenance.auto", "gc.auto"):
            expected = self.git("config", "--get-all", key)
            setattr(self, key, expected)
        self.apply()
        self.assertEqual(self.git("show-ref"), refs)
        for key in ("remote.origin.fetch", "remote.review.fetch", "remote.origin.url",
                    "remote.review.url", "branch.active.remote", "branch.active.merge",
                    "maintenance.auto", "gc.auto"):
            self.assertEqual(self.git("config", "--get-all", key), getattr(self, key))
        self.assertEqual(self.git("config", "fetch.prune"), b"true\n")
        self.assertEqual(self.git("config", "fetch.pruneTags"), b"false\n")
        self.assertEqual(self.git("config", "remote.review.skipFetchAll"), b"true\n")
        self.assertEqual(self.git("config", "remote.review.tagOpt"), b"--no-tags\n")
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        after = self.config.read_bytes()
        self.apply(self.root / "second receipt", self.sha())
        self.assertEqual(self.config.read_bytes(), after)
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o700)
        record = json.loads((self.receipt / "receipt.json").read_text())
        self.assertEqual(record["before"], self.pre_sha)
        self.assertEqual(record["after"], self.sha())

    @unittest.skipUnless(sys.platform == "darwin", "native macOS metadata contract")
    def test_preserves_macos_acl_and_xattr(self):
        subprocess.run(["xattr", "-w", "com.example.throughput-fixture", "fixture",
                        str(self.config)], check=True)
        subprocess.run(["chmod", "+a", "everyone allow read", str(self.config)], check=True)
        before_acl = subprocess.check_output(["ls", "-le", str(self.config)]).splitlines()[1:]
        self.apply()
        after_acl = subprocess.check_output(["ls", "-le", str(self.config)]).splitlines()[1:]
        self.assertEqual(after_acl, before_acl)
        self.assertEqual(subprocess.check_output([
            "xattr", "-p", "com.example.throughput-fixture", str(self.config)
        ]), b"fixture\n")

    def test_lock_and_drift_stop_without_mutation(self):
        lock = self.config.with_name("config.lock")
        lock.write_text("other owner")
        self.apply(ok=False)
        self.assertEqual(lock.read_text(), "other owner")
        lock.unlink()
        self.git("config", "user.name", "Changed")
        current = self.config.read_bytes()
        self.apply(ok=False)
        self.assertEqual(self.config.read_bytes(), current)
        self.assertFalse(lock.exists())
        self.assertFalse(self.receipt.exists())

    def test_drift_after_lock_is_not_overwritten(self):
        replace = runpy.run_path(str(HELPER))["locked_replace"]

        def prepare(stage):
            stage.write_bytes(stage.read_bytes() + b"\n[fetch]\nprune = true\n")
            self.config.write_bytes(self.before + b"\n# concurrent edit\n")

        with self.assertRaisesRegex(ValueError, "changed while locked"):
            replace(self.config, self.pre_sha, self.receipt, prepare)
        self.assertEqual(self.config.read_bytes(), self.before + b"\n# concurrent edit\n")
        self.assertFalse(self.config.with_name("config.lock").exists())

    def test_slash_remote(self):
        self.git("remote", "add", "team/review", str(self.remote))
        self.run_helper("apply", "--repo", self.repo, "--expect-sha256", self.sha(),
                        "--receipt", self.receipt, "--review-remote", "team/review")
        self.assertEqual(self.git("config", "remote.team/review.skipFetchAll"), b"true\n")

    def test_option_like_remote_stays_an_opaque_argument(self):
        self.git("config", "remote.--fixture.url", str(self.remote))
        self.run_helper("apply", "--repo", self.repo, "--expect-sha256", self.sha(),
                        "--receipt", self.receipt, "--review-remote=--fixture")
        self.run_helper("refresh", "--repo", self.repo, "--remote=--fixture",
                        "--source", "refs/heads/main",
                        "--destination", "refs/remotes/--fixture/main")
        self.assertEqual(self.git("rev-parse", "refs/remotes/--fixture/main"),
                         self.git("rev-parse", "HEAD"))

    def test_replaced_lock_is_not_overwritten(self):
        replace = runpy.run_path(str(HELPER))["locked_replace"]
        lock = self.config.with_name("config.lock")

        def prepare(stage):
            stage.write_bytes(stage.read_bytes() + b"\n[fetch]\nprune = true\n")
            other = self.root / "other-lock"
            other.write_text("other owner's lock")
            other.replace(lock)

        with self.assertRaisesRegex(ValueError, "lock ownership changed"):
            replace(self.config, self.pre_sha, self.receipt, prepare)
        self.assertEqual(lock.read_text(), "other owner's lock")
        self.assertEqual(self.config.read_bytes(), self.before)

    def test_lock_replaced_at_write_boundary_is_not_overwritten(self):
        replace = runpy.run_path(str(HELPER))["locked_replace"]
        copy_metadata = replace.__globals__["copy_metadata"]
        lock = self.config.with_name("config.lock")

        def interleave(source_fd, target_fd):
            other = self.root / "other-lock"
            other.write_text("late replacement lock")
            other.replace(lock)
            copy_metadata(source_fd, target_fd)

        replace.__globals__["copy_metadata"] = interleave

        def prepare(stage):
            stage.write_bytes(stage.read_bytes() + b"\n[fetch]\nprune = true\n")

        with self.assertRaisesRegex(ValueError, "lock or config changed"):
            replace(self.config, self.pre_sha, self.receipt, prepare)
        self.assertEqual(lock.read_text(), "late replacement lock")
        self.assertEqual(self.config.read_bytes(), self.before)

    def test_same_bytes_replacement_config_is_not_overwritten(self):
        replace = runpy.run_path(str(HELPER))["locked_replace"]

        def prepare(stage):
            stage.write_bytes(stage.read_bytes() + b"\n[fetch]\nprune = true\n")
            alternate = self.root / "replacement-config"
            alternate.write_bytes(self.before)
            self.config.unlink()
            self.config.symlink_to(alternate)

        with self.assertRaisesRegex(ValueError, "config changed while locked"):
            replace(self.config, self.pre_sha, self.receipt, prepare)
        self.assertTrue(self.config.is_symlink())
        self.assertEqual(self.config.read_bytes(), self.before)

    def test_conditional_rollback(self):
        self.apply()
        self.run_helper("rollback", "--repo", self.repo, "--receipt", self.receipt,
                        "--rollback-receipt", self.root / "undo")
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)

    def test_rollback_refuses_drift_or_wrong_owner(self):
        self.apply()
        self.git("config", "user.name", "Later change")
        changed = self.config.read_bytes()
        self.run_helper("rollback", "--repo", self.repo, "--receipt", self.receipt,
                        "--rollback-receipt", self.root / "undo", ok=False)
        self.assertEqual(self.config.read_bytes(), changed)
        record = json.loads((self.receipt / "receipt.json").read_text())
        record["config"] = str(self.remote / "config")
        (self.receipt / "receipt.json").write_text(json.dumps(record))
        self.run_helper("rollback", "--repo", self.repo, "--receipt", self.receipt,
                        "--rollback-receipt", self.root / "undo", ok=False)

    def test_origin_and_missing_remote_rejected(self):
        for remote in ("origin", "missing"):
            self.run_helper("apply", "--repo", self.repo, "--expect-sha256", self.pre_sha,
                            "--receipt", self.receipt, "--review-remote", remote, ok=False)
        self.assertEqual(self.config.read_bytes(), self.before)

    def test_refresh_one_ref_only(self):
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        self.git("update-ref", "-d", "refs/remotes/origin/topic")
        self.git("update-ref", "refs/remotes/origin/stale", "HEAD")
        self.git("config", "fetch.prune", "true")
        self.git("config", "fetch.pruneTags", "true")
        config_before = self.config.read_bytes()
        args = ("refresh", "--repo", self.repo, "--remote", "origin",
                "--source", "refs/heads/main", "--destination", "refs/remotes/origin/main")
        self.run_helper(*args)
        refs = self.git("for-each-ref", "--format=%(refname)").decode().splitlines()
        self.assertIn("refs/remotes/origin/main", refs)
        self.assertNotIn("refs/remotes/origin/topic", refs)
        self.assertIn("refs/remotes/origin/stale", refs)
        self.assertIn("refs/tags/fixture", refs)
        self.assertFalse((self.repo / ".git/FETCH_HEAD").exists())
        self.assertEqual(self.config.read_bytes(), config_before)
        for destination in ("refs/heads/main", "refs/tags/new", "refs/remotes/other/main"):
            self.run_helper(*args[:-1], destination, ok=False)
        self.run_helper("refresh", "--repo", self.repo, "--remote", "origin",
                        "--source", "refs/heads/*", "--destination", "refs/remotes/origin/all",
                        ok=False)

    def test_symlink_config_rejected(self):
        saved = self.root / "saved"
        shutil.move(self.config, saved)
        self.config.symlink_to(saved)
        self.apply(ok=False)
        self.assertTrue(self.config.is_symlink())
        self.assertEqual(saved.read_bytes(), self.before)


if __name__ == "__main__":
    unittest.main()
