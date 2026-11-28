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
from types import SimpleNamespace
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
        self.env["DOTFILES_GWT_AGENT_COW"] = "1"
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

    def prepare_pnpm12_dependencies(self, wanted, installed=None):
        modules = self.prepare_dependencies()
        (self.repo / "package.json").write_text('{"packageManager":"pnpm@12.3.4"}\n')
        (self.repo / "pnpm-lock.yaml").write_bytes(wanted)
        (modules / ".modules.yaml").write_text(
            "packageManager: pnpm@12.3.4\nnodeLinker: isolated\nvirtualStoreDir: .pnpm\n"
        )
        (modules / ".pnpm/lock.yaml").write_bytes(installed if installed is not None else wanted)
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

    def test_dependency_receipt_accepts_pnpm12_environment_document(self):
        dependency_lock = b"lockfileVersion: '9.0'\nsettings:\n  autoInstallPeers: true\n"
        self.prepare_pnpm12_dependencies(
            b"---\npackageManager: pnpm@12.3.4\n---\n" + dependency_lock,
            dependency_lock,
        )

        self.helper("dependencies", "record", "--source", self.repo, "--after-frozen-install")

    def test_dependency_lock_normalizes_bom_crlf_and_ignores_indented_markers(self):
        dependency_lock = (
            b"lockfileVersion: '9.0'\r\n"
            b"notes: |\r\n"
            b"  ---\r\n"
            b"  ...\r\n"
        )
        self.prepare_pnpm12_dependencies(
            b"\xef\xbb\xbf---\r\npackageManager: pnpm@12.3.4\r\n---\r\n" + dependency_lock,
            b"\xef\xbb\xbf" + dependency_lock,
        )

        self.helper("dependencies", "record", "--source", self.repo, "--after-frozen-install")

    def test_dependency_lock_rejects_dependency_and_environment_drift(self):
        dependency_lock = b"lockfileVersion: '9.0'\nsettings:\n  autoInstallPeers: true\n"
        modules = self.prepare_pnpm12_dependencies(
            b"---\npackageManager: pnpm@12.3.4\nchannel: stable\n---\n" + dependency_lock,
            dependency_lock,
        )
        self.helper("dependencies", "record", "--source", self.repo, "--after-frozen-install")

        (modules / ".pnpm/lock.yaml").write_bytes(
            dependency_lock.replace(b"autoInstallPeers: true", b"autoInstallPeers: false")
        )
        result = self.helper(
            "dependencies", "record", "--source", self.repo, "--after-frozen-install", ok=False
        )
        self.assertIn("installed lockfile differs", result.stderr)

        (modules / ".pnpm/lock.yaml").write_bytes(dependency_lock.rstrip(b"\n"))
        result = self.helper(
            "dependencies", "record", "--source", self.repo, "--after-frozen-install", ok=False
        )
        self.assertIn("installed lockfile differs", result.stderr)

        (modules / ".pnpm/lock.yaml").write_bytes(dependency_lock)
        (self.repo / "pnpm-lock.yaml").write_bytes(
            b"---\npackageManager: pnpm@12.3.4\nchannel: next\n---\n" + dependency_lock
        )
        result = self.helper(
            "dependencies", "record", "--source", self.repo, "--after-frozen-install", ok=False
        )
        self.assertIn("existing receipt differs", result.stderr)

    def test_dependency_lock_strips_exactly_one_bom(self):
        dependency_lock = b"lockfileVersion: '9.0'\n"
        self.prepare_pnpm12_dependencies(
            b"\xef\xbb\xbf\xef\xbb\xbf---\npackageManager: pnpm@12.3.4\n---\n"
            + dependency_lock,
            dependency_lock,
        )

        result = self.helper(
            "dependencies", "record", "--source", self.repo, "--after-frozen-install", ok=False
        )
        self.assertIn("checkout lockfile has an invalid document envelope", result.stderr)

    def test_dependency_lock_rejects_invalid_wanted_document_envelopes(self):
        dependency_lock = b"lockfileVersion: '9.0'\n"
        modules = self.prepare_pnpm12_dependencies(dependency_lock, dependency_lock)
        invalid = {
            "missing separator": b"---\npackageManager: pnpm@12.3.4\n" + dependency_lock,
            "empty environment": b"---\n# comment only\n---\n" + dependency_lock,
            "empty dependency": b"---\npackageManager: pnpm@12.3.4\n---\n# comment only\n",
            "stray separator": dependency_lock + b"---\n",
            "decorated start": b"--- # environment\npackageManager: pnpm@12.3.4\n---\n" + dependency_lock,
            "decorated separator": b"---\npackageManager: pnpm@12.3.4\n--- # dependency\n" + dependency_lock,
            "end marker": dependency_lock + b"...\n",
            "bare carriage return": b"---\rpackageManager: pnpm@12.3.4\r---\r" + dependency_lock,
            "bare carriage return before separator": (
                b"---\npackageManager: pnpm@12.3.4\r---\n" + dependency_lock
            ),
            "third document": (
                b"---\npackageManager: pnpm@12.3.4\n---\n"
                + dependency_lock
                + b"---\nextra: document\n"
            ),
        }
        for name, wanted in invalid.items():
            with self.subTest(name=name):
                (self.repo / "pnpm-lock.yaml").write_bytes(wanted)
                (modules / ".pnpm/lock.yaml").write_bytes(dependency_lock)
                result = self.helper(
                    "dependencies", "record", "--source", self.repo,
                    "--after-frozen-install", ok=False,
                )
                self.assertIn("checkout lockfile", result.stderr)

    def test_dependency_lock_rejects_marked_installed_documents(self):
        dependency_lock = b"lockfileVersion: '9.0'\n"
        wanted = b"---\npackageManager: pnpm@12.3.4\n---\n" + dependency_lock
        modules = self.prepare_pnpm12_dependencies(wanted, dependency_lock)
        invalid = {
            "multiple documents": b"---\nfirst: document\n---\n" + dependency_lock,
            "decorated marker": b"--- # dependency\n" + dependency_lock,
            "end marker": dependency_lock + b"...\n",
            "bare carriage return": b"---\r" + dependency_lock,
        }
        for name, installed in invalid.items():
            with self.subTest(name=name):
                (modules / ".pnpm/lock.yaml").write_bytes(installed)
                result = self.helper(
                    "dependencies", "record", "--source", self.repo,
                    "--after-frozen-install", ok=False,
                )
                self.assertIn("installed lockfile must contain one unmarked document", result.stderr)

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

    def prepare_agent_files(self):
        agents = self.repo / ".agents/skills/example"
        agents.mkdir(parents=True)
        (agents / "SKILL.md").write_text("agent guidance\n")
        (agents / "tool").write_text("#!/bin/sh\nexit 0\n")
        (agents / "tool").chmod(0o755)
        (agents / "link").symlink_to("SKILL.md")
        (self.repo / ".gitattributes").write_text("* text=auto eol=lf\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "agent guidance")
        return agents

    @unittest.skipUnless(sys.platform == "darwin", "APFS native sharing")
    def test_agent_checkout_shares_before_native_checkout_and_isolates_edits(self):
        agents = self.prepare_agent_files()
        result = self.gwt("new", "agent-shared", "HEAD", "--full")
        report = next(json.loads(line) for line in result.stdout.splitlines() if line.startswith('{"checkout_sharing"'))
        self.assertEqual(report["cloned_files"], 2)
        target = self.home / ".codex/worktrees/example-project/agent-shared"
        self.assertEqual(self.git(target, "status", "--porcelain"), "")
        self.assertTrue((target / ".agents/skills/example/link").is_symlink())
        self.assertTrue((target / ".agents/skills/example/tool").stat().st_mode & stat.S_IXUSR)
        file = target / ".agents/skills/example/SKILL.md"
        before = file.stat()
        self.git(target, "checkout", "-f")
        self.assertEqual((file.stat().st_ino, file.stat().st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        file.write_text("worktree edit\n")
        self.assertEqual((agents / "SKILL.md").read_text(), "agent guidance\n")
        (agents / "tool").write_text("source edit\n")
        self.assertEqual((target / ".agents/skills/example/tool").read_text(), "#!/bin/sh\nexit 0\n")

    @unittest.skipUnless(sys.platform == "darwin", "APFS native sharing")
    def test_agent_checkout_uses_requested_revision_and_skips_dirty_donor(self):
        agents = self.prepare_agent_files()
        (agents / "SKILL.md").write_text("new committed guidance\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "new guidance")
        (agents / "tool").write_text("uncommitted source\n")
        result = self.gwt("new", "agent-old", "HEAD~1", "--full")
        report = next(json.loads(line) for line in result.stdout.splitlines() if line.startswith('{"checkout_sharing"'))
        self.assertEqual(report["cloned_files"], 0)
        target = self.home / ".codex/worktrees/example-project/agent-old"
        self.assertEqual((target / ".agents/skills/example/SKILL.md").read_text(), "agent guidance\n")
        self.assertEqual((target / ".agents/skills/example/tool").read_text(), "#!/bin/sh\nexit 0\n")
        self.assertEqual(self.git(target, "status", "--porcelain"), "")

    def test_agent_checkout_preserves_full_post_checkout_hook(self):
        self.prepare_agent_files()
        hook = self.repo / ".git/hooks/post-checkout"
        hook.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > hook-args\n')
        hook.chmod(0o755)
        self.gwt("new", "agent-hook", "HEAD", "--full")
        target = self.home / ".codex/worktrees/example-project/agent-hook"
        self.assertEqual((target / "hook-args").read_text().splitlines(),
                         ["0" * 40, self.git(target, "rev-parse", "HEAD"), "1"])

    def test_agent_checkout_large_attribute_batch_and_native_fallback(self):
        agents = self.prepare_agent_files()
        for index in range(400):
            (agents / (f"guidance-{index:04}-" + "long-name-" * 5 + ".md")).write_text("guidance\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "large attribute batch")
        result = self.gwt("new", "agent-batch", "HEAD", "--full")
        target = self.home / ".codex/worktrees/example-project/agent-batch"
        self.assertEqual(self.git(target, "status", "--porcelain"), "")
        self.assertEqual(len(list((target / ".agents/skills/example").iterdir())), 403)
        self.assertIn('"checkout_sharing"', result.stdout)

    def test_agent_checkout_conversion_and_disable_fallback(self):
        self.prepare_agent_files()
        self.git(self.repo, "config", "core.autocrlf", "yes")
        result = self.gwt("new", "agent-crlf", "HEAD", "--full")
        self.assertIn('"fallback": "checkout-conversion"', result.stdout)
        target = self.home / ".codex/worktrees/example-project/agent-crlf"
        self.assertEqual(self.git(target, "status", "--porcelain"), "")
        self.env["DOTFILES_GWT_AGENT_COW"] = "0"
        result = self.gwt("new", "agent-disabled", "HEAD", "--full")
        self.assertNotIn('"checkout_sharing"', result.stdout)

    def test_agent_checkout_preserves_relative_target_hook(self):
        self.prepare_agent_files()
        hooks = self.repo / ".githooks"
        hooks.mkdir()
        hook = hooks / "post-checkout"
        hook.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > hook-args\n')
        hook.chmod(0o755)
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "target hook")
        self.git(self.repo, "branch", "target-with-hook")
        self.git(self.repo, "checkout", "HEAD~1")
        self.git(self.repo, "config", "core.hooksPath", ".githooks")
        baseline = self.root / "native-hook"
        self.git(self.repo, "worktree", "add", "--detach", str(baseline), "target-with-hook")
        result = self.gwt("new", "agent-target-hook", "target-with-hook", "--full")
        target = self.home / ".codex/worktrees/example-project/agent-target-hook"
        self.assertNotIn('"checkout_sharing"', result.stdout)
        self.assertEqual((target / "hook-args").exists(), (baseline / "hook-args").exists())
        if (baseline / "hook-args").exists():
            self.assertEqual((target / "hook-args").read_bytes(), (baseline / "hook-args").read_bytes())

    @unittest.skipUnless(sys.platform == "darwin", "APFS native sharing")
    def test_agent_checkout_sparse_membership(self):
        self.prepare_agent_files()
        profiles = self.root / "profiles/example-project"
        profiles.mkdir(parents=True)
        (profiles / "agents.patterns").write_text("/*\n!/*/\n/.agents/\n")
        (profiles / "no-agents.patterns").write_text("/*\n!/*/\n")
        self.env["DOTFILES_GIT_SPARSE_ROOT"] = str(profiles.parent)
        for profile, present in (("agents", True), ("no-agents", False)):
            with self.subTest(profile=profile):
                result = self.gwt("new", profile, "HEAD", "--profile", profile)
                report = next(json.loads(line) for line in result.stdout.splitlines() if line.startswith('{"checkout_sharing"'))
                self.assertEqual(report["cloned_files"], 2 if present else 0)
                target = self.home / ".codex/worktrees/example-project" / profile
                self.assertEqual((target / ".agents/skills/example/SKILL.md").exists(), present)
                self.assertEqual(self.git(target, "status", "--porcelain"), "")

    @unittest.skipUnless(sys.platform == "darwin", "APFS native sharing")
    def test_all_tracked_files_share_by_default_and_preserve_git(self):
        self.prepare_agent_files()
        self.env.pop("DOTFILES_GWT_AGENT_COW", None)
        self.env.pop("DOTFILES_GWT_COW", None)
        result = self.gwt("new", "all-files", "HEAD", "--full")
        report = next(json.loads(line) for line in result.stdout.splitlines() if line.startswith('{"checkout_sharing"'))
        self.assertEqual(report["scope"], "all")
        self.assertEqual(report["cloned_files"], 5)
        target = self.home / ".codex/worktrees/example-project/all-files"
        self.assertEqual(self.git(target, "status", "--porcelain"), "")
        self.assertNotEqual((target / "file").stat().st_ino, (self.repo / "file").stat().st_ino)
        self.assertEqual((target / "file").stat().st_nlink, 1)
        self.assertFalse(list(target.glob(".gwt-checkout-*")))
        self.assertTrue((target / "link").is_symlink())
        (target / "file").write_text("independent worktree edit\n")
        self.assertEqual((self.repo / "file").read_text(), "current\n")
        self.git(target, "add", "file")
        self.git(target, "commit", "-qm", "edit shared clone")
        self.assertEqual(self.git(target, "status", "--porcelain"), "")
        self.git(target, "checkout", "HEAD~1")
        self.assertEqual((target / "file").read_text(), "current\n")

    @unittest.skipUnless(sys.platform == "darwin", "APFS native sharing")
    def test_all_tracked_sharing_obeys_sparse_patterns(self):
        self.prepare_agent_files()
        self.env["DOTFILES_GWT_COW"] = "all"
        profiles = self.root / "profiles/example-project"
        profiles.mkdir(parents=True)
        (profiles / "selected.patterns").write_text("/*\n!/*/\n/.agents/\n!/.agents/skills/example/tool\n")
        self.env["DOTFILES_GIT_SPARSE_ROOT"] = str(profiles.parent)
        result = self.gwt("new", "selected", "HEAD", "--profile", "selected")
        report = next(json.loads(line) for line in result.stdout.splitlines() if line.startswith('{"checkout_sharing"'))
        self.assertEqual(report["cloned_files"], 4)
        target = self.home / ".codex/worktrees/example-project/selected"
        self.assertFalse((target / ".agents/skills/example/tool").exists())
        self.assertEqual(self.git(target, "status", "--porcelain"), "")

    @unittest.skipUnless(sys.platform == "darwin", "APFS fallback")
    def test_all_tracked_sharing_falls_back_when_clone_is_unsupported(self):
        self.prepare_agent_files()
        target = self.root / "unsupported-clone"
        self.git(self.repo, "worktree", "add", "--no-checkout", "-b", "unsupported", str(target))
        library = mock.Mock()

        def unavailable(*args):
            import ctypes
            import errno
            ctypes.set_errno(errno.EXDEV)
            return -1

        library.fclonefileat.side_effect = unavailable
        with mock.patch.dict(os.environ, {**self.env, "GWT_NEW_WORKTREE": str(target)}, clear=True):
            namespace = runpy.run_path(str(HELPER))
            with mock.patch("ctypes.CDLL", return_value=library), mock.patch.dict(
                namespace["checkout_files"].__globals__, checkout_directory_fallback=lambda *args: None
            ):
                namespace["checkout_files"](SimpleNamespace(source=self.repo, target=target, scope="all"))
        self.assertFalse(list(target.glob(".gwt-checkout-*")))
        self.git(target, "checkout", "-f")
        self.assertEqual((target / "file").read_text(), "current\n")
        self.assertEqual(self.git(target, "status", "--porcelain"), "")

    @unittest.skipUnless(sys.platform == "darwin", "APFS private publication")
    def test_checkout_keeps_dirty_snapshot_private_until_verified(self):
        agents = self.prepare_agent_files()
        (agents / "SKILL.md").write_text("private dirty donor\n")
        target = self.root / "private-publication"
        self.git(self.repo, "worktree", "add", "--no-checkout", "--detach", str(target))
        target.chmod(0o755)
        namespace = runpy.run_path(str(HELPER))
        original_hash = namespace["hashlib"].new
        observed = []

        def inspect_hash(*args, **kwargs):
            stages = list(target.glob(".gwt-checkout-*"))
            self.assertEqual(len(stages), 1)
            self.assertEqual(stat.S_IMODE(stages[0].stat().st_mode), 0o700)
            if (stages[0] / "file").read_text() == "private dirty donor\n":
                self.assertFalse((target / ".agents/skills/example/SKILL.md").exists())
                observed.append(True)
            return original_hash(*args, **kwargs)

        previous_mask = os.umask(0)
        try:
            with mock.patch.dict(os.environ, {**self.env, "GWT_NEW_WORKTREE": str(target)}, clear=True):
                with mock.patch("hashlib.new", side_effect=inspect_hash):
                    namespace["checkout_files"](SimpleNamespace(source=self.repo, target=target, scope="agents"))
        finally:
            os.umask(previous_mask)
        self.assertEqual(observed, [True])
        self.assertFalse(list(target.glob(".gwt-checkout-*")))
        self.assertFalse((target / ".agents/skills/example/SKILL.md").exists())
        self.git(target, "checkout", "-f")
        self.assertEqual((target / ".agents/skills/example/SKILL.md").read_text(), "agent guidance\n")

    @unittest.skipUnless(sys.platform == "darwin", "APFS private publication")
    def test_checkout_does_not_replace_concurrently_created_file(self):
        target = self.root / "publication-race"
        self.git(self.repo, "worktree", "add", "--no-checkout", "--detach", str(target))
        namespace = runpy.run_path(str(HELPER))
        original_link = os.link

        def raced_link(source, destination, **kwargs):
            Path(destination).write_text("concurrent writer\n")
            return original_link(source, destination, **kwargs)

        with mock.patch.dict(os.environ, {**self.env, "GWT_NEW_WORKTREE": str(target)}, clear=True):
            with mock.patch("os.link", side_effect=raced_link):
                with self.assertRaises(FileExistsError):
                    namespace["checkout_files"](SimpleNamespace(source=self.repo, target=target, scope="all"))
        self.assertEqual((target / "executable").read_text(), "concurrent writer\n")
        self.assertFalse(list(target.glob(".gwt-checkout-*")))

    @unittest.skipUnless(sys.platform == "darwin", "APFS private publication")
    def test_checkout_cleans_staging_after_hash_failure(self):
        target = self.root / "hash-failure"
        self.git(self.repo, "worktree", "add", "--no-checkout", "--detach", str(target))
        namespace = runpy.run_path(str(HELPER))
        with mock.patch.dict(os.environ, {**self.env, "GWT_NEW_WORKTREE": str(target)}, clear=True):
            with mock.patch("hashlib.new", side_effect=RuntimeError("hash failed")):
                with self.assertRaisesRegex(RuntimeError, "hash failed"):
                    namespace["checkout_files"](SimpleNamespace(source=self.repo, target=target, scope="all"))
        self.assertEqual([entry.name for entry in target.iterdir()], [".git"])

    @unittest.skipUnless(sys.platform == "darwin", "native macOS ACL inheritance")
    def test_checkout_acl_fallback_preserves_native_inheritance(self):
        target, baseline = self.root / "acl-target", self.root / "acl-native"
        acl = "everyone allow read,readattr,readextattr,readsecurity,file_inherit,directory_inherit"
        for directory in (target, baseline):
            self.git(self.repo, "worktree", "add", "--no-checkout", "--detach", str(directory))
            self.run_command(["chmod", "+a", acl, str(directory)])
        self.env["GWT_NEW_WORKTREE"] = str(target)
        result = self.helper("checkout", "--source", self.repo, "--target", target, "--scope", "all")
        self.assertEqual(json.loads(result.stdout)["fallback"], "destination-acl")
        self.assertEqual(self.git(target, "ls-files"), "")
        self.assertFalse(list(target.glob(".gwt-checkout-*")))
        for directory in (target, baseline):
            self.git(directory, "checkout", "-f")
        for name in ("file", "executable"):
            actual = self.run_command(["ls", "-le", str(target / name)]).stdout.splitlines()[1:]
            expected = self.run_command(["ls", "-le", str(baseline / name)]).stdout.splitlines()[1:]
            self.assertTrue(actual)
            self.assertEqual(actual, expected)
            self.assertEqual(stat.S_IMODE((target / name).stat().st_mode), stat.S_IMODE((baseline / name).stat().st_mode))

    def test_checkout_linux_acl_and_setgid_fallback(self):
        namespace = runpy.run_path(str(HELPER))
        import errno

        def no_acl(descriptor, attribute):
            raise OSError(errno.ENODATA, "no ACL")

        for attribute in ("system.posix_acl_access", "system.posix_acl_default", None):
            with self.subTest(attribute=attribute):
                def get_acl(descriptor, requested):
                    return b"acl" if requested == attribute else no_acl(descriptor, requested)

                with mock.patch("os.getxattr", side_effect=get_acl, create=True):
                    self.assertEqual(namespace["checkout_directory_fallback"](self.root, None),
                                     "destination-acl" if attribute else None)
        for error in (errno.EACCES, errno.ENOTSUP, errno.EOPNOTSUPP):
            with self.subTest(error=error):
                with mock.patch("os.getxattr", side_effect=OSError(error, "unavailable"), create=True):
                    self.assertEqual(namespace["checkout_directory_fallback"](self.root, None), "destination-acl-unavailable")
        self.root.chmod(0o2700)
        self.assertEqual(namespace["checkout_directory_fallback"](self.root, None), "destination-setgid")

    def test_all_tracked_sharing_does_not_follow_source_directory_links(self):
        agents = self.prepare_agent_files()
        outside = self.root / "outside"
        (self.repo / ".agents/skills").rename(outside)
        (self.repo / ".agents/skills").symlink_to(outside, target_is_directory=True)
        self.env["DOTFILES_GWT_COW"] = "all"
        self.gwt("new", "no-follow", "HEAD", "--full")
        target = self.home / ".codex/worktrees/example-project/no-follow"
        self.assertEqual((target / ".agents/skills/example/SKILL.md").read_text(), "agent guidance\n")
        self.assertFalse((target / ".agents/skills").is_symlink())
        self.assertTrue((self.repo / ".agents/skills").is_symlink())
        self.assertEqual(self.git(target, "status", "--porcelain"), "")

    def test_all_tracked_sharing_supports_disabled_hooks(self):
        self.prepare_agent_files()
        self.git(self.repo, "config", "core.hooksPath", "/dev/null")
        self.env["DOTFILES_GWT_COW"] = "all"
        self.gwt("new", "disabled-hooks", "HEAD", "--full")
        target = self.home / ".codex/worktrees/example-project/disabled-hooks"
        self.assertEqual(self.git(target, "status", "--porcelain"), "")

    def test_cross_volume_source_uses_registered_same_volume_donor(self):
        candidate = self.root / "same-volume-donor"
        target = self.root / "new-target"
        self.git(self.repo, "worktree", "add", "--detach", str(candidate), "HEAD")
        self.git(self.repo, "worktree", "add", "--no-checkout", "--detach", str(target), "HEAD")
        namespace = runpy.run_path(str(HELPER))
        original_stat = Path.stat

        def different_source_device(path, *args, **kwargs):
            info = original_stat(path, *args, **kwargs)
            if path == self.repo:
                fields = list(info)
                fields[2] += 1
                return os.stat_result(fields)
            return info

        with mock.patch.object(Path, "stat", different_source_device):
            self.assertEqual(namespace["same_filesystem_donor"](self.repo, target), candidate)

    @unittest.skipUnless(sys.platform == "darwin", "APFS managed permissions")
    def test_all_tracked_sharing_keeps_managed_full_checkout_private_modes(self):
        self.prepare_agent_files()
        (self.repo / ".gitattributes").write_text("* text=auto eol=lf\nfile ident\n")
        (self.repo / "file").write_text("$Id$\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "native fallback mode")
        self.env["DOTFILES_GWT_COW"] = "all"
        self.gwt("new", "private-full", "HEAD", "--full", "--finish-managed")
        target = self.home / ".codex/worktrees/example-project/private-full"
        for name in ("file", ".agents/skills/example/SKILL.md"):
            self.assertEqual(stat.S_IMODE((target / name).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((target / "executable").stat().st_mode), 0o700)

    @unittest.skipUnless(sys.platform == "darwin", "APFS native sharing")
    def test_agent_checkout_uses_target_attributes_and_native_metadata(self):
        agents = self.prepare_agent_files()
        (self.repo / ".gitattributes").write_text("* text=auto eol=lf\n.agents/skills/example/SKILL.md ident\n")
        (agents / "SKILL.md").write_text("$Id$\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "expanded guidance")
        self.run_command(["xattr", "-w", "com.example.donor", "private", str(agents / "tool")])
        result = self.gwt("new", "agent-attrs", "HEAD", "--full")
        report = next(json.loads(line) for line in result.stdout.splitlines() if line.startswith('{"checkout_sharing"'))
        self.assertEqual(report["cloned_files"], 1)
        target = self.home / ".codex/worktrees/example-project/agent-attrs"
        self.assertIn("$Id: ", (target / ".agents/skills/example/SKILL.md").read_text())
        self.assertNotIn("com.example.donor", self.run_command(["xattr", str(target / ".agents/skills/example/tool")]).stdout)
        self.assertEqual(self.git(target, "status", "--porcelain"), "")

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
