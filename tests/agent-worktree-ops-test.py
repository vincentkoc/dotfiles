#!/usr/bin/env python3

import hashlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLEANER_PATH = ROOT / "bin" / "agent-worktree-ops" / "agent-worktree-clean"
SPEC = importlib.util.spec_from_loader(
    "agent_worktree_clean",
    SourceFileLoader("agent_worktree_clean", str(CLEANER_PATH)),
)
assert SPEC and SPEC.loader
CLEANER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CLEANER
SPEC.loader.exec_module(CLEANER)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=check,
    )


def init_repo(path: Path, *, origin: str = "git@example.test:openclaw/openclaw.git") -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, stdout=subprocess.DEVNULL)
    git(path, "config", "user.name", "Worktree Test")
    git(path, "config", "user.email", "worktree@example.test")
    git(path, "config", "remote.origin.url", origin)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "fixture")


def add_worktree(repo: Path, path: Path, branch: str, start: str = "main") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-b", branch, str(path), start)


def path_metadata(path: Path) -> tuple[str, int, int, int, str]:
    stats = path.lstat()
    if path.is_symlink():
        kind = "symlink"
        content = os.readlink(path).encode()
    elif path.is_dir():
        kind = "directory"
        content = b""
    else:
        kind = "file"
        content = path.read_bytes()
    return (
        kind,
        stats.st_mode,
        stats.st_size,
        stats.st_mtime_ns,
        hashlib.sha256(content).hexdigest(),
    )


def snapshot_tree(root: Path) -> dict[str, tuple[str, int, int, int, str]]:
    if not root.exists():
        return {}
    paths = [root, *sorted(root.rglob("*"))]
    return {
        "." if path == root else str(path.relative_to(root)): path_metadata(path)
        for path in paths
    }


def snapshot_worktree_metadata(repo: Path) -> dict[str, object]:
    common_dir = Path(
        git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    )
    return {
        "common_dir": path_metadata(common_dir),
        "worktrees": snapshot_tree(common_dir / "worktrees"),
    }


def worktree_index(worktree: Path) -> Path:
    return Path(
        git(
            worktree,
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "index",
        ).stdout.strip()
    )


def invalidate_index_stat_cache(worktree: Path) -> Path:
    tracked_file = worktree / "README.md"
    original = tracked_file.read_bytes()
    original_stats = tracked_file.stat()
    tracked_file.write_bytes(original)
    os.utime(
        tracked_file,
        ns=(original_stats.st_atime_ns, original_stats.st_mtime_ns + 5_000_000_000),
    )
    index = worktree_index(worktree)
    os.chmod(index, 0o644)
    return index


class WorktreeCleanerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.repo = self.base / "repo"
        self.codex_home = self.base / "codex"
        init_repo(self.repo)
        self.slug_root = self.codex_home / "worktrees" / "openclaw-openclaw"
        self.slug_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_inventory_is_registry_and_common_dir_authoritative(self) -> None:
        registered = self.slug_root / "registered"
        add_worktree(self.repo, registered, "registered")

        stale_path = self.slug_root / "stale-registration"
        add_worktree(self.repo, stale_path, "stale-registration")
        shutil.rmtree(stale_path)

        moved_source = self.slug_root / "moved-source"
        unregistered = self.slug_root / "unregistered"
        add_worktree(self.repo, moved_source, "moved-source")
        moved_source.rename(unregistered)

        foreign = self.slug_root / "foreign"
        init_repo(foreign)
        non_git = self.slug_root / "notes"
        non_git.mkdir()

        inventory = CLEANER.build_inventory(str(self.repo), str(self.codex_home))

        self.assertEqual(
            [worktree.path for worktree in inventory.worktrees],
            [CLEANER.normalize_path(str(registered))],
        )
        reasons = {item.path: item.reason for item in inventory.report_only}
        self.assertEqual(reasons[CLEANER.normalize_path(str(stale_path))], "stale-registration")
        self.assertEqual(reasons[CLEANER.normalize_path(str(moved_source))], "stale-registration")
        self.assertEqual(reasons[CLEANER.normalize_path(str(unregistered))], "unregistered")
        self.assertEqual(reasons[CLEANER.normalize_path(str(foreign))], "foreign-common-dir")
        self.assertEqual(reasons[CLEANER.normalize_path(str(non_git))], "non-git")

    def test_audit_does_not_mutate_metadata_and_apply_preserves_unrelated_stale_registration(self) -> None:
        removable = self.slug_root / "removable"
        stale = self.slug_root / "stale"
        add_worktree(self.repo, removable, "removable")
        add_worktree(self.repo, stale, "stale")
        stale_admin = Path(
            git(
                stale,
                "rev-parse",
                "--path-format=absolute",
                "--git-dir",
            ).stdout.strip()
        )
        shutil.rmtree(stale)
        removable_index = invalidate_index_stat_cache(removable)

        fake_bin = self.base / "bin"
        fake_bin.mkdir()
        (fake_bin / "lsof").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        (fake_bin / "tmux").write_text(
            "#!/bin/sh\necho 'no server running' >&2\nexit 1\n",
            encoding="utf-8",
        )
        os.chmod(fake_bin / "lsof", 0o755)
        os.chmod(fake_bin / "tmux", 0o755)
        environment = os.environ.copy()
        environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
        environment["GIT_OPTIONAL_LOCKS"] = "caller-value"

        before = snapshot_worktree_metadata(self.repo)
        index_before = path_metadata(removable_index)
        audit = subprocess.run(
            [
                str(CLEANER_PATH),
                "--repo",
                str(self.repo),
                "--codex-home",
                str(self.codex_home),
                "--min-age-days",
                "0",
                "--trim-artifacts-age-days",
                "999",
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        self.assertIn("mode: dry-run", audit.stdout)
        self.assertEqual(before, snapshot_worktree_metadata(self.repo))
        self.assertEqual(index_before, path_metadata(removable_index))
        self.assertEqual(removable_index.stat().st_mode & 0o777, 0o644)

        stale_before = snapshot_tree(stale_admin)
        apply_environment = environment.copy()
        apply_environment.pop("GIT_OPTIONAL_LOCKS")
        apply = subprocess.run(
            [
                str(CLEANER_PATH),
                "--repo",
                str(self.repo),
                "--codex-home",
                str(self.codex_home),
                "--min-age-days",
                "0",
                "--trim-artifacts-age-days",
                "999",
                "--apply",
            ],
            env=apply_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        after = git(self.repo, "worktree", "list", "--porcelain").stdout
        self.assertFalse(removable.exists(), apply.stdout)
        self.assertIn(f"worktree {CLEANER.normalize_path(str(stale))}", after)
        self.assertEqual(stale_before, snapshot_tree(stale_admin))

    def test_reachability_matrix(self) -> None:
        safe_upstream = self.slug_root / "safe-upstream"
        ahead_upstream = self.slug_root / "ahead-upstream"
        safe_no_upstream = self.slug_root / "safe-no-upstream"
        ahead_no_upstream = self.slug_root / "ahead-no-upstream"
        detached_reachable = self.slug_root / "detached-reachable"
        detached_unreachable = self.slug_root / "detached-unreachable"

        add_worktree(self.repo, safe_upstream, "safe-upstream")
        git(safe_upstream, "config", "branch.safe-upstream.remote", ".")
        git(safe_upstream, "config", "branch.safe-upstream.merge", "refs/heads/main")

        add_worktree(self.repo, ahead_upstream, "ahead-upstream")
        git(ahead_upstream, "config", "branch.ahead-upstream.remote", ".")
        git(ahead_upstream, "config", "branch.ahead-upstream.merge", "refs/heads/main")
        (ahead_upstream / "ahead.txt").write_text("ahead\n", encoding="utf-8")
        git(ahead_upstream, "add", "ahead.txt")
        git(ahead_upstream, "commit", "-m", "ahead")

        add_worktree(self.repo, safe_no_upstream, "safe-no-upstream")
        add_worktree(self.repo, ahead_no_upstream, "ahead-no-upstream")
        (ahead_no_upstream / "unique.txt").write_text("unique\n", encoding="utf-8")
        git(ahead_no_upstream, "add", "unique.txt")
        git(ahead_no_upstream, "commit", "-m", "unique")

        git(self.repo, "worktree", "add", "--detach", str(detached_reachable), "main")
        git(self.repo, "worktree", "add", "--detach", str(detached_unreachable), "main")
        git(detached_unreachable, "config", "user.name", "Worktree Test")
        git(detached_unreachable, "config", "user.email", "worktree@example.test")
        (detached_unreachable / "detached.txt").write_text("detached\n", encoding="utf-8")
        git(detached_unreachable, "add", "detached.txt")
        git(detached_unreachable, "commit", "-m", "detached")

        inventory = CLEANER.build_inventory(str(self.repo), str(self.codex_home))
        worktrees = {Path(item.path).name: item for item in inventory.worktrees}
        self.assertIsNone(CLEANER.unsafe_reachability_reason(worktrees["safe-upstream"]))
        self.assertEqual(
            CLEANER.unsafe_reachability_reason(worktrees["ahead-upstream"]),
            "unmerged-upstream",
        )
        self.assertIsNone(CLEANER.unsafe_reachability_reason(worktrees["safe-no-upstream"]))
        self.assertEqual(
            CLEANER.unsafe_reachability_reason(worktrees["ahead-no-upstream"]),
            "unreachable-no-upstream",
        )
        self.assertIsNone(CLEANER.unsafe_reachability_reason(worktrees["detached-reachable"]))
        self.assertEqual(
            CLEANER.unsafe_reachability_reason(worktrees["detached-unreachable"]),
            "detached-unreachable",
        )

    def test_dirty_submodule_is_dirty(self) -> None:
        child = self.base / "child"
        parent = self.base / "parent"
        init_repo(child, origin="git@example.test:fixtures/child.git")
        init_repo(parent, origin="git@example.test:fixtures/parent.git")
        git(
            parent,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(child),
            "vendor/child",
        )
        git(parent, "commit", "-am", "add submodule")
        (parent / "vendor" / "child" / "README.md").write_text("dirty\n", encoding="utf-8")
        self.assertTrue(CLEANER.is_worktree_dirty(str(parent)))

    def test_dirty_probe_uses_status_without_git_diff(self) -> None:
        with mock.patch.object(CLEANER, "run_text", return_value="") as run_text:
            self.assertFalse(CLEANER.is_worktree_dirty("/tmp/example"))

        command = run_text.call_args.args[0]
        self.assertEqual(command[:4], ["git", "-C", "/tmp/example", "status"])
        self.assertNotIn("diff", command)

    def test_readonly_git_env_is_command_local(self) -> None:
        with mock.patch.dict(os.environ, {"GIT_OPTIONAL_LOCKS": "caller-value"}):
            with mock.patch.object(CLEANER.subprocess, "run") as run:
                CLEANER.run_git_readonly(["git", "status"], check=False)

            self.assertEqual(os.environ["GIT_OPTIONAL_LOCKS"], "caller-value")
            self.assertEqual(run.call_args.kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")

    def test_live_ownership_probe_failures_are_fatal(self) -> None:
        worktree = CLEANER.Worktree("/tmp/example", "abc", "refs/heads/test", False, 10)
        failed = SimpleNamespace(returncode=2, stdout="", stderr="permission denied")
        with mock.patch.object(CLEANER.shutil, "which", return_value="/usr/bin/tool"):
            with mock.patch.object(CLEANER.subprocess, "run", return_value=failed):
                with self.assertRaisesRegex(RuntimeError, "lsof"):
                    CLEANER.collect_lsof_worktree_paths([worktree])
                with self.assertRaisesRegex(RuntimeError, "permission denied"):
                    CLEANER.collect_tmux_worktree_paths([worktree])

    def test_apply_revalidation_detects_new_dirty_state(self) -> None:
        target = self.slug_root / "race"
        add_worktree(self.repo, target, "race")
        expected = CLEANER.build_inventory(str(self.repo), str(self.codex_home)).worktrees[0]
        (target / "appeared-after-audit.txt").write_text("race\n", encoding="utf-8")

        with mock.patch.object(CLEANER, "collect_owned_worktree_paths", return_value=set()):
            reason = CLEANER.revalidate_candidate(
                repo_root=str(self.repo),
                codex_home=str(self.codex_home),
                expected=expected,
                require_reachable=True,
                include_detached=False,
                session_limit=30,
                scan_limit=200,
            )
        self.assertEqual(reason, "dirty")

    def test_apply_revalidation_preserves_session_limits(self) -> None:
        target = self.slug_root / "limits"
        add_worktree(self.repo, target, "limits")
        expected = CLEANER.build_inventory(str(self.repo), str(self.codex_home)).worktrees[0]

        with mock.patch.object(CLEANER, "collect_owned_worktree_paths", return_value=set()):
            with mock.patch.object(CLEANER, "find_latest_state_db", return_value="/tmp/state.sqlite"):
                with mock.patch.object(
                    CLEANER,
                    "read_recent_session_cwds",
                    return_value=[],
                ) as read_sessions:
                    reason = CLEANER.revalidate_candidate(
                        repo_root=str(self.repo),
                        codex_home=str(self.codex_home),
                        expected=expected,
                        require_reachable=False,
                        include_detached=False,
                        session_limit=7,
                        scan_limit=13,
                    )

        self.assertIsNone(reason)
        self.assertEqual(read_sessions.call_args.kwargs["keep_limit"], 7)
        self.assertEqual(read_sessions.call_args.kwargs["scan_limit"], 13)

    def test_argparse_rejects_abbreviated_apply_and_repo_flags(self) -> None:
        for abbreviated in ("--app", "--rep"):
            with self.subTest(abbreviated=abbreviated):
                with mock.patch.object(
                    CLEANER.sys,
                    "argv",
                    ["agent-worktree-clean", abbreviated, "/tmp/other"],
                ):
                    with redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as raised:
                            CLEANER.parse_args()
                self.assertNotEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
