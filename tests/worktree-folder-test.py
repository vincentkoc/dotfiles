#!/usr/bin/env python3
"""Physical folder placement through the public guard and its callers."""
import contextlib
import io
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
GUARD = ROOT / "bin/agent-worktree-ops/worktree-storage-guard"
loader = importlib.machinery.SourceFileLoader("folder_guard", str(GUARD))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class FolderStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = pathlib.Path(self.temp.name).resolve()
        self.home = self.base / "home"
        self.volume = self.base / "volume"
        self.root = self.volume / "agent-worktrees"
        for p in (self.home, self.volume, self.root):
            p.mkdir(mode=0o700)
        self.volume.chmod(0o755)
        self.uuid = "11111111-2222-3333-4444-555555555555"
        self.config = dict(schema_version="external-worktree-storage.v3", required=True,
            mount_point=str(self.volume), worktree_root=str(self.root),
            volume_uuid=self.uuid, filesystem="apfs", encrypted=False,
            case_sensitive=False, owners=True, device_location="External",
            minimum_free_gib=200, minimum_free_percent=10, marker_id="fixture.folder.v3",
            children=module.EXPECTED_CHILDREN)
        (self.root / module.MARKER_NAME).write_text("fixture.folder.v3\n")
        for child in ("openclaw-managed", ".pnpm-store", ".pnpm-store/openclaw"):
            (self.root / child).mkdir(mode=0o700)
        self.observation = dict(mounted=True, mount_point=str(self.volume), volume_uuid=self.uuid,
            filesystem="apfs", encrypted=False, case_sensitive=False, ownership_enabled=True,
            device_location="External", free_gib=1000, free_percent=50,
            device=self.volume.stat().st_dev, parent_device=self.volume.stat().st_dev + 1)
        self.config_file = self.base / "config.json"
        self.observed_file = self.base / "observed.json"
        self.environment = dict(os.environ, HOME=str(self.home),
            WORKTREE_STORAGE_GUARD_TESTING="1", WORKTREE_STORAGE_CONFIG=str(self.config_file),
            WORKTREE_STORAGE_GUARD_OBSERVED=str(self.observed_file))
        self.environment.pop("DOTFILES_WORKTREES_ROOT", None)

    def guard(self, *args):
        self.config_file.write_text(json.dumps(self.config))
        self.config_file.chmod(0o600)
        self.observed_file.write_text(json.dumps(self.observation))
        return subprocess.run([str(GUARD), *args], env=self.environment, capture_output=True, text=True)

    def test_child_attestation_preserves_shared_volume(self):
        sibling = self.volume / "existing.txt"
        sibling.write_bytes(b"keep")
        before = self.volume.stat()
        result = self.guard("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["mount_point"], str(self.volume))
        self.assertEqual(value["worktree_root"], str(self.root))
        self.assertEqual(sibling.read_bytes(), b"keep")
        after = self.volume.stat()
        self.assertEqual((before.st_dev, before.st_ino, before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)),
                         (after.st_dev, after.st_ino, after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)))

    def test_login_allocator_tmp_and_native_closeout(self):
        self.assertEqual(self.guard().returncode, 0)
        owner = self.base / "owner"
        owner.mkdir()
        env = dict(self.environment, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        def git(*args):
            subprocess.run(["/usr/bin/git", "-C", str(owner), *args], env=env, check=True, capture_output=True)
        git("init", "-q")
        git("remote", "add", "origin", "https://github.com/example/folder-fixture.git")
        (owner / "runner").write_text("#!/bin/sh\nexit 0\n")
        (owner / "runner").chmod(0o755)
        (owner / "runner-link").symlink_to("runner")
        git("add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        shell = 'source "$1/.exports"; source "$1/functions/gwt/gwt.zsh"; gwt root; gwt new fixture-folder HEAD'
        # A fresh login still inherits the old .exports default from a parent shell.
        env["DOTFILES_WORKTREES_ROOT"] = str(self.home / ".codex/worktrees")
        q = subprocess.run(["/bin/zsh", "-lc", shell, "fixture", str(ROOT)], env=env, cwd=owner, capture_output=True, text=True)
        self.assertEqual(q.returncode, 0, q.stderr)
        self.assertEqual(q.stdout.splitlines()[0], str(self.root))
        checkout = self.root / "example-folder-fixture/fixture-folder"
        self.assertTrue((checkout / "runner-link").is_symlink())
        self.assertTrue(os.access(checkout / "runner", os.X_OK))
        subprocess.run([str(checkout / "runner-link")], check=True)
        tmp = subprocess.run([str(ROOT / "bin/external-tmp"), "/bin/sh", "-c", 'printf "%s" "$TMPDIR"'], env=env, capture_output=True, text=True)
        self.assertEqual(tmp.returncode, 0, tmp.stderr)
        self.assertTrue(tmp.stdout.startswith(str(self.root / ".scratch/tmp") + "/"))
        self.assertEqual(list((self.root / ".scratch/tmp").iterdir()), [])
        q = subprocess.run(["/bin/zsh", "-lc", 'source "$1/.exports"; source "$1/functions/gwt/gwt.zsh"; gwt rm "$2"', "fixture", str(ROOT), str(checkout)], env=env, cwd=owner, capture_output=True, text=True)
        self.assertEqual(q.returncode, 0, q.stderr)
        self.assertFalse(checkout.exists())
        git("show-ref", "--verify", "refs/heads/fixture-folder")

    def test_user_policy_conflict_and_invalid_policy_never_fall_back(self):
        user = self.home / module.USER_CONFIG_RELATIVE
        user.parent.mkdir(parents=True, mode=0o700)
        user.write_text("{")
        user.chmod(0o600)
        system = self.base / "system-policy.json"
        self.guard()
        environment = {k:v for k,v in self.environment.items() if k != "WORKTREE_STORAGE_CONFIG"}
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(module, "DEFAULT_CONFIG", system), mock.patch("sys.argv", [str(GUARD)]):
            self.assertEqual(module.main(), 78)
            user.write_text(json.dumps(self.config))
            system.write_text("{}")
            self.assertEqual(module.main(), 78)
        self.assertFalse((self.home / ".codex/worktrees").exists())

    def test_user_policy_cannot_supply_root_owned_legacy_authority(self):
        user = self.home / module.USER_CONFIG_RELATIVE
        user.parent.mkdir(parents=True, mode=0o700)
        legacy = {k:v for k,v in self.config.items() if k != "worktree_root"}
        legacy.update(schema_version=module.SCHEMA, mount_point=str(self.home / ".codex/worktrees"),
                      backing_directory_mode="0000", spotlight="disabled", time_machine_excluded=True)
        user.write_text(json.dumps(legacy))
        user.chmod(0o600)
        environment = {k:v for k,v in self.environment.items() if not k.startswith("WORKTREE_STORAGE_")}
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(module, "DEFAULT_CONFIG", self.base / "absent-system"), mock.patch("sys.argv", [str(GUARD)]), mock.patch.object(module, "live_observation", side_effect=AssertionError("must reject before observation")):
            self.assertEqual(module.main(), 78)

    def test_inaccessible_user_policy_never_falls_back(self):
        user = self.home / module.USER_CONFIG_RELATIVE
        system = self.base / "absent-system"
        original = os.lstat
        def lstat(path, *args, **kwargs):
            if pathlib.Path(path) == user:
                raise PermissionError("fixture denied existing user policy")
            return original(path, *args, **kwargs)
        environment = {k:v for k,v in self.environment.items() if not k.startswith("WORKTREE_STORAGE_")}
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(module, "DEFAULT_CONFIG", system), mock.patch("sys.argv", [str(GUARD), "--json"]), mock.patch.object(module.os, "lstat", side_effect=lstat), mock.patch.object(module, "live_observation", return_value={}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(module.main(), 78)
            self.assertEqual(output.getvalue(), "")
            with self.assertRaises(PermissionError):
                module.load_config(user, production_default=False)

    def test_wrong_or_missing_volume_and_symlink_refuse(self):
        for field, value in (("mounted", False), ("volume_uuid", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
                             ("filesystem", "exfat")):
            with self.subTest(field=field):
                original = self.observation[field]
                self.observation[field] = value
                self.assertNotEqual(self.guard().returncode, 0)
                self.observation[field] = original
        moved = self.volume / "moved"
        self.root.rename(moved)
        self.root.symlink_to(moved, target_is_directory=True)
        self.assertNotEqual(self.guard().returncode, 0)


if __name__ == "__main__":
    unittest.main()
