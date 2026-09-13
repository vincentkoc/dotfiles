#!/usr/bin/env python3
"""Real Git boundaries for owner routing, snapshot storage and APFS sharing."""
import json
import os
import runpy
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "bin/gwt-storage"


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {**os.environ, "HOME": str(self.home), "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
                    "XDG_CONFIG_HOME": str(self.home / ".config"), "XDG_STATE_HOME": str(self.home / ".local/state"), "GWT_OWNER_ID": "fixture",
                    "DOTFILES_WORKTREES_ROOT": str(self.home / ".codex/worktrees"), "TMUX": ""}
        self.env.pop("DOTFILES_GWT_LOADED", None)
        self.env.pop("DOTFILES_GIT_SPARSE_ROOT", None)
        self.repo = self.root / "source"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q", "-b", "main")
        self.git(self.repo, "config", "user.name", "Fixture")
        self.git(self.repo, "config", "user.email", "fixture@example.invalid")
        self.git(self.repo, "remote", "add", "origin", "https://github.com/example/project.git")
        (self.repo / "file").write_text("old\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "old")
        (self.repo / "file").write_text("current\n")
        (self.repo / "executable").write_text("#!/bin/sh\nexit 0\n")
        (self.repo / "executable").chmod(0o755)
        (self.repo / "link").symlink_to("file")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "current")

    def run_command(self, args, cwd=None, ok=True):
        result = subprocess.run(args, cwd=cwd or (self.repo if self.repo.exists() else self.root), env=self.env, text=True, capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def git(self, repo, *args, ok=True):
        return self.run_command(["git", "-C", str(repo), *args], ok=ok).stdout.strip()

    def helper(self, *args, ok=True):
        return self.run_command([sys.executable, str(HELPER), *map(str, args)], ok=ok)

    def gwt(self, *args, cwd=None, ok=True):
        return self.run_command(["zsh", "-f", "-c", 'source "$1"; shift; gwt "$@"', "fixture",
                                 str(ROOT / "functions/gwt/gwt.zsh"), *map(str, args)], cwd=cwd, ok=ok)

    def configure(self, owners=None, protected=None):
        path = self.home / ".config/gwt/storage.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "owners": owners or {}, "protected": protected or []}))

    def other_owner(self):
        target = self.root / "preferred"
        self.git(self.repo, "clone", "-q", "--no-hardlinks", str(self.repo), str(target))
        self.git(target, "remote", "set-url", "origin", "git@github.com:example/project.git")
        self.configure({"github.com/example/project": str(target)})
        return target

    def test_routes_only_new_work(self):
        self.gwt("new", "existing", "HEAD", "--full")
        old = self.home / ".codex/worktrees/example-project/existing"
        target = self.other_owner()
        self.gwt("new", "existing", "HEAD", "--full")
        self.assertEqual(self.git(old, "rev-parse", "--git-common-dir"), str(self.repo / ".git"))
        self.gwt("new", "fresh", "HEAD", "--full")
        fresh = old.with_name("fresh")
        self.assertEqual(self.git(fresh, "rev-parse", "--git-common-dir"), str(target / ".git"))

    def test_local_only_branch_does_not_move(self):
        self.other_owner()
        self.git(self.repo, "branch", "local-only")
        result = self.gwt("new", "local-only", "HEAD", "--full", ok=False)
        self.assertIn("absent in preferred owner", result.stderr)

    def test_local_only_start_does_not_fetch(self):
        self.other_owner()
        self.git(self.repo, "commit", "--allow-empty", "-qm", "local")
        self.gwt("new", "fresh", "HEAD", "--full", ok=False)

    def prepare_dependencies(self):
        (self.repo / "package.json").write_text('{"packageManager":"pnpm@10.12.1"}\n')
        (self.repo / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
        (self.repo / ".gitignore").write_text("node_modules/\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "dependency fixture")
        modules = self.repo / "node_modules"
        (modules / ".pnpm").mkdir(parents=True)
        (modules / ".modules.yaml").write_text("packageManager: pnpm@10.12.1\nnodeLinker: isolated\nvirtualStoreDir: .pnpm\n")
        (modules / ".pnpm/lock.yaml").write_bytes((self.repo / "pnpm-lock.yaml").read_bytes())
        return modules

    def test_dependency_receipt_gates_new_sharing_and_input_drift(self):
        modules = self.prepare_dependencies()
        self.gwt("new", "unknown-deps", "HEAD", "--full")
        worktrees = self.home / ".codex/worktrees/example-project"
        self.assertFalse((worktrees / "unknown-deps/node_modules").exists())
        self.helper("dependencies", "record", "--source", self.repo, ok=False)
        self.helper("dependencies", "record", "--source", self.repo, "--after-frozen-install")
        self.gwt("new", "compatible", "HEAD", "--full")
        target = worktrees / "compatible"
        self.assertEqual((target / "node_modules").resolve(), modules)
        (target / "package.json").write_text('{"packageManager":"pnpm@10.12.1","engines":{"node":">=24"}}')
        self.gwt("new", "compatible", "HEAD", "--full", ok=False)
        self.assertEqual((target / "node_modules").resolve(), modules)
        (modules / ".modules.yaml").write_text("packageManager: pnpm@9.0.0\n")
        self.helper("dependencies", "check", "--source", self.repo, "--target", target, ok=False)

    def test_dependency_selector_survives_owner_routing(self):
        self.prepare_dependencies()
        self.helper("dependencies", "record", "--source", self.repo, "--after-frozen-install")
        owner = self.other_owner()
        self.gwt("new", "routed-deps", "HEAD", "--full", "--dependency-source", self.repo)
        target = self.home / ".codex/worktrees/example-project/routed-deps"
        self.assertEqual(self.git(target, "rev-parse", "--git-common-dir"), str(owner / ".git"))
        self.assertEqual((target / "node_modules").resolve(), self.repo / "node_modules")
        self.gwt("new", "routed-deps", "HEAD", "--full", "--dependency-source", self.repo, cwd=owner)

    def test_dependency_receipt_rejects_external_links_and_protected_donors(self):
        modules = self.prepare_dependencies()
        (modules / "workspace").symlink_to(self.repo, target_is_directory=True)
        result = self.helper("dependencies", "record", "--source", self.repo, "--after-frozen-install", ok=False)
        self.assertIn("symlink escapes", result.stderr)
        (modules / "workspace").unlink()
        self.configure(protected=[str(self.repo)])
        result = self.helper("dependencies", "record", "--source", self.repo, "--after-frozen-install", ok=False)
        self.assertIn("protected", result.stderr)
        self.assertFalse((self.home / ".local/state/gwt/dependencies").exists())

    def test_dependency_scan_failure_is_not_compatibility_proof(self):
        modules = self.prepare_dependencies()
        original = os.scandir
        def unreadable(path):
            if Path(path) == modules / ".pnpm":
                raise PermissionError("fixture unreadable directory")
            return original(path)
        with mock.patch.dict(os.environ, self.env, clear=True):
            helper = runpy.run_path(str(HELPER))
            with mock.patch("os.scandir", side_effect=unreadable):
                with self.assertRaisesRegex(PermissionError, "unreadable"):
                    helper["dependency_install"](self.repo)

    def test_explicit_donor_cannot_enroll_unsupported_finish_contract(self):
        self.prepare_dependencies()
        result = self.gwt("new", "unsupported", "HEAD", "--full", "--finish-managed", "--dependency-source", self.repo, ok=False)
        self.assertIn("not supported by managed finish", result.stderr)
        self.assertFalse((self.home / ".codex/worktrees/example-project/unsupported").exists())
        self.git(self.repo, "show-ref", "--verify", "refs/heads/unsupported", ok=False)

    def test_scheduled_profile_runs_only_commit_graph(self):
        self.git(self.repo, "remote", "set-url", "origin", str(self.repo))
        self.git(self.repo, "config", "--local", "include.path", str(ROOT / "functions/gwt/maintenance.config"))
        trace = self.root / "maintenance-trace.jsonl"
        self.env["GIT_TRACE2_EVENT"] = str(trace)
        self.git(self.repo, "maintenance", "run", "--schedule=daily", "--no-detach", "--no-quiet")
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        tasks = [event["label"] for event in events if event.get("event") == "region_enter" and event.get("category") == "maintenance"]
        self.assertEqual(tasks, ["commit-graph"])
        self.assertTrue((self.repo / ".git/objects/info/commit-graphs/commit-graph-chain").exists())
        self.assertEqual(self.git(self.repo, "config", "--get", "maintenance.auto"), "false")

    def test_git_environment_cannot_redirect_snapshot_writes(self):
        before = (self.repo / ".git/index").read_bytes()
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_CONFIG_COUNT"):
            with self.subTest(name=name):
                self.env[name] = str(self.repo / ".git/index")
                result = self.helper("snapshot", "--task", "proof", "--purpose", "fixture", ok=False)
                self.assertIn("repository-routing", result.stderr)
                del self.env[name]
        self.assertEqual((self.repo / ".git/index").read_bytes(), before)
        self.assertFalse((self.home / "GIT/_Synthetic").exists())

    def test_case_equivalent_github_owner_uses_actual_destination(self):
        target = self.other_owner()
        self.git(target, "remote", "set-url", "origin", "git@github.com:Example/Project.git")
        self.gwt("new", "fresh", "HEAD", "--full")
        self.assertTrue((self.home / ".codex/worktrees/Example-Project/fresh/.git").exists())

    def test_non_github_port_and_path_case_are_distinct(self):
        target = self.other_owner()
        self.git(self.repo, "remote", "set-url", "origin", "ssh://git@example.test:2222/Team/repo.git")
        self.configure({"example.test:2222/Team/repo": str(target)})
        for url in ("ssh://git@example.test:2200/Team/repo.git", "ssh://git@example.test:2222/team/repo.git"):
            self.git(target, "remote", "set-url", "origin", url)
            result = self.helper("owner", ok=False)
            self.assertIn("does not match", result.stderr)

    def test_wrong_partial_alternate_and_protected_owners_fail(self):
        target = self.other_owner()
        self.git(target, "remote", "set-url", "origin", "https://github.com/wrong/repo.git")
        self.helper("owner", ok=False)
        self.git(target, "remote", "set-url", "origin", "https://github.com/example/project.git")
        self.git(target, "config", "remote.extra.promisor", "true")
        self.helper("owner", ok=False)
        self.git(target, "config", "--unset", "remote.extra.promisor")
        alternates = target / ".git/objects/info/alternates"
        alternates.write_text(str(self.repo / ".git/objects") + "\n")
        self.helper("owner", ok=False)
        alternates.unlink()
        self.configure({"github.com/example/project": str(target)}, [str(target)])
        self.helper("owner", ok=False)

    def test_snapshot_is_independent_exact_tree_and_push_guarded(self):
        result = self.helper("snapshot", "--task", "proof", "--purpose", "fixture")
        snapshot = Path(result.stdout.strip())
        self.assertEqual(self.git(snapshot, "rev-list", "--count", "HEAD"), "1")
        self.assertEqual(self.git(snapshot, "rev-parse", "HEAD^{tree}"), self.git(self.repo, "rev-parse", "HEAD^{tree}"))
        self.assertEqual((snapshot / "link").readlink(), Path("file"))
        self.assertTrue((snapshot / "executable").stat().st_mode & stat.S_IXUSR)
        record = json.loads((snapshot / ".git/gwt-synthetic.json").read_text())
        self.assertEqual(record["state"], "ready")
        self.assertEqual(record["borrowed_object_stores"], [])
        self.repo.rename(self.root / "source-hidden")
        self.git(snapshot, "fsck", "--full")
        destination = self.root / "remote.git"
        self.git(snapshot, "init", "--bare", str(destination))
        result = self.run_command(["git", "-C", str(snapshot), "push", str(destination), "HEAD:main"], cwd=snapshot, ok=False)
        self.assertIn("synthetic history must not be published", result.stderr)
        self.helper("owner", "--repo", snapshot, ok=False)

    def test_snapshot_collision_and_protected_source(self):
        self.helper("snapshot", "--task", "proof", "--purpose", "fixture")
        self.helper("snapshot", "--task", "proof", "--purpose", "fixture", ok=False)
        self.configure(protected=[str(self.repo)])
        self.helper("snapshot", "--task", "other", "--purpose", "fixture", ok=False)

    def test_failed_ref_refresh_does_not_use_cached_head(self):
        self.git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.git(self.repo, "remote", "set-url", "origin", str(self.root / "missing"))
        result = self.gwt("new", "fresh", "origin/main", "--full", ok=False)
        self.assertIn("ref refresh failed", result.stderr)
        self.git(self.repo, "show-ref", "--verify", "refs/heads/fresh", ok=False)

    def test_ref_refresh_updates_only_the_named_branch(self):
        old = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "commit", "--allow-empty", "-qm", "upstream")
        current = self.git(self.repo, "rev-parse", "HEAD")
        remote = self.root / "remote.git"
        self.git(self.repo, "clone", "--bare", "-q", str(self.repo), str(remote))
        self.git(remote, "update-ref", "refs/heads/unrelated", current)
        self.git(remote, "update-ref", "refs/tags/not-requested", current)
        self.git(self.repo, "remote", "set-url", "origin", str(remote))
        self.git(self.repo, "update-ref", "refs/remotes/origin/main", old)
        fetch_head = self.repo / ".git/FETCH_HEAD"
        fetch_head.write_text("preserved fixture\n")
        self.gwt("new", "fresh", "origin/main", "--full")
        self.assertEqual(self.git(self.repo, "rev-parse", "refs/heads/fresh"), current)
        self.assertEqual(fetch_head.read_text(), "preserved fixture\n")
        self.git(self.repo, "show-ref", "--verify", "refs/remotes/origin/unrelated", ok=False)
        self.git(self.repo, "show-ref", "--verify", "refs/tags/not-requested", ok=False)

    def test_clone_flags_are_separate_and_conflicts_fail(self):
        destination = self.root / "clone"
        self.gwt("clone", self.repo, destination, "--checkout", "full", "--history", "full")
        self.assertEqual(self.git(destination, "rev-parse", "--is-shallow-repository"), "false")
        self.gwt("clone", self.repo, self.root / "conflict", "--history", "full", "--history", "blobless", ok=False)
        self.gwt("clone", self.repo, self.root / "conflict", "--profile", "core", "--full", ok=False)
        self.assertFalse((self.root / "conflict").exists())
        # Keep all local fixture objects present: the download guard can disable
        # lazy hydration. This checks option/config forwarding, not server filtering.
        filtered = self.root / "blobless"
        self.gwt("clone", self.repo.as_uri(), filtered, "--full", "--history", "blobless")
        self.assertEqual(self.git(filtered, "config", "remote.origin.partialclonefilter"), "blob:none")
        self.assertEqual((filtered / "file").read_text(), "current\n")
        self.helper("owner", "--repo", filtered, ok=False)

    @unittest.skipUnless(sys.platform == "darwin", "APFS prototype")
    def test_cow_immutable_seed_isolation_and_modes(self):
        for name in ("file", "executable"):
            os.chflags(self.repo / name, stat.UF_IMMUTABLE)
        self.addCleanup(lambda: [os.chflags(self.repo / name, 0) for name in ("file", "executable")])
        result = self.gwt("new", "cow", "HEAD", "--full", "--cow-from", self.repo)
        report = next(json.loads(line) for line in result.stdout.splitlines() if line.startswith('{"cloned_files"'))
        self.assertEqual(report["cloned_files"], 2)
        target = self.home / ".codex/worktrees/example-project/cow"
        self.assertNotEqual((target / "file").stat().st_ino, (self.repo / "file").stat().st_ino)
        (target / "file").write_text("changed\n")
        self.assertEqual((self.repo / "file").read_text(), "current\n")
        self.assertTrue((target / "executable").stat().st_mode & stat.S_IXUSR)
        self.assertTrue((target / "link").is_symlink())
        print("cow_fixture=" + json.dumps(report))

    @unittest.skipUnless(sys.platform == "darwin", "APFS metadata")
    def test_cow_preserves_destination_xattrs(self):
        self.gwt("new", "cow", "HEAD", "--full")
        target = self.home / ".codex/worktrees/example-project/cow"
        self.run_command(["xattr", "-w", "com.example.seed", "seed", str(self.repo / "file")])
        self.run_command(["xattr", "-w", "com.example.target", "target", str(target / "file")])
        os.chflags(self.repo / "file", stat.UF_IMMUTABLE)
        self.addCleanup(lambda: os.chflags(self.repo / "file", 0))
        self.helper("cow", "--source", self.repo, "--target", target, ok=False)
        self.env["GWT_NEW_WORKTREE"] = str(target)
        self.helper("cow", "--source", self.repo, "--target", target)
        self.assertEqual(self.run_command(["xattr", "-p", "com.example.target", str(target / "file")]).stdout.strip(), "target")
        self.assertNotIn("com.example.seed", self.run_command(["xattr", str(target / "file")]).stdout)


if __name__ == "__main__":
    unittest.main()
