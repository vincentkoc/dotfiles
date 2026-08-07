#!/usr/bin/env python3

import hashlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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


def path_metadata(path: Path) -> tuple[object, ...]:
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
        stats.st_dev,
        stats.st_ino,
        stats.st_uid,
        stats.st_gid,
        stats.st_nlink,
        stats.st_size,
        stats.st_mtime_ns,
        stats.st_ctime_ns,
        hashlib.sha256(content).hexdigest(),
    )


def snapshot_tree(root: Path) -> dict[str, tuple[object, ...]]:
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


def create_state_db(path: Path, sessions: tuple[tuple[str, int, int], ...] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            "CREATE TABLE threads (cwd TEXT, archived INTEGER, updated_at INTEGER)"
        )
        connection.executemany(
            "INSERT INTO threads (cwd, archived, updated_at) VALUES (?, ?, ?)",
            sessions,
        )
        connection.commit()
    finally:
        connection.close()


def open_active_wal_state(path: Path, cwd: str) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
    if mode != ("wal",):
        connection.close()
        raise AssertionError(f"failed to enable WAL mode: {mode}")
    connection.execute("PRAGMA wal_autocheckpoint = 0")
    connection.execute(
        "CREATE TABLE threads (cwd TEXT, archived INTEGER, updated_at INTEGER)"
    )
    connection.execute(
        "INSERT INTO threads (cwd, archived, updated_at) VALUES (?, 0, 1)",
        (cwd,),
    )
    connection.commit()
    return connection


def snapshot_state_artifacts(path: Path) -> dict[str, tuple[object, ...]]:
    return {
        candidate.name: path_metadata(candidate)
        for candidate in sorted(path.parent.glob(f"{path.name}*"))
    }


class WorktreeCleanerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.repo = self.base / "repo"
        self.codex_home = self.base / "codex"
        init_repo(self.repo)
        self.slug_root = self.codex_home / "worktrees" / "openclaw-openclaw"
        self.slug_root.mkdir(parents=True)
        self.state_db = self.codex_home / "state.sqlite"
        create_state_db(self.state_db)

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
        environment["GIT_NO_LAZY_FETCH"] = "caller-lazy-value"

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
        apply_environment.pop("GIT_NO_LAZY_FETCH")
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

    def test_audit_git_env_is_command_local(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "GIT_OPTIONAL_LOCKS": "caller-value",
                "GIT_NO_LAZY_FETCH": "caller-lazy-value",
            },
        ):
            with mock.patch.object(CLEANER.subprocess, "run") as run:
                CLEANER.run_git_audit(["git", "status"], check=False)

            self.assertEqual(os.environ["GIT_OPTIONAL_LOCKS"], "caller-value")
            self.assertEqual(os.environ["GIT_NO_LAZY_FETCH"], "caller-lazy-value")
            self.assertEqual(run.call_args.kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
            self.assertEqual(run.call_args.kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")

    def test_state_database_artifact_set_is_strict(self) -> None:
        valid_root = self.base / "valid-state"
        valid_db = valid_root / "state.sqlite"
        create_state_db(valid_db)
        discovered, artifacts = CLEANER.discover_state_artifacts(str(valid_root))
        self.assertEqual(discovered, CLEANER.normalize_path(str(valid_db)))
        self.assertEqual(artifacts, (CLEANER.normalize_path(str(valid_db)),))

        wal_root = self.base / "wal-state"
        wal_db = wal_root / "state-v1.sqlite"
        create_state_db(wal_db)
        (wal_root / "state-v1.sqlite-wal").write_bytes(b"wal")
        (wal_root / "state-v1.sqlite-shm").write_bytes(b"shm")
        _, wal_artifacts = CLEANER.discover_state_artifacts(str(wal_root))
        self.assertEqual(
            wal_artifacts,
            (
                CLEANER.normalize_path(str(wal_db)),
                CLEANER.normalize_path(str(wal_root / "state-v1.sqlite-shm")),
                CLEANER.normalize_path(str(wal_root / "state-v1.sqlite-wal")),
            ),
        )

        missing_root = self.base / "missing-state"
        missing_root.mkdir()
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            CLEANER.discover_state_artifacts(str(missing_root))

        ambiguous_root = self.base / "ambiguous-state"
        create_state_db(ambiguous_root / "state.sqlite")
        create_state_db(ambiguous_root / "state-old.sqlite")
        with self.assertRaisesRegex(RuntimeError, "found 2"):
            CLEANER.discover_state_artifacts(str(ambiguous_root))

        for suffix in ("-wal", "-shm"):
            with self.subTest(asymmetric_sidecar=suffix):
                sidecar_root = self.base / f"sidecar-{suffix[1:]}"
                sidecar_db = sidecar_root / "state.sqlite"
                create_state_db(sidecar_db)
                (sidecar_root / f"state.sqlite{suffix}").write_bytes(b"sidecar")
                with self.assertRaisesRegex(RuntimeError, "both be present"):
                    CLEANER.discover_state_artifacts(str(sidecar_root))

        journal_root = self.base / "journal-state"
        journal_db = journal_root / "state.sqlite"
        create_state_db(journal_db)
        (journal_root / "state.sqlite-journal").write_bytes(b"journal")
        with self.assertRaisesRegex(RuntimeError, "journal"):
            CLEANER.discover_state_artifacts(str(journal_root))

        extra_root = self.base / "extra-state"
        extra_db = extra_root / "state.sqlite"
        create_state_db(extra_db)
        (extra_root / "state.sqlite.backup").write_bytes(b"backup")
        with self.assertRaisesRegex(RuntimeError, "unexpected"):
            CLEANER.discover_state_artifacts(str(extra_root))

    def test_state_artifacts_require_regular_single_link_same_user_files(self) -> None:
        symlink_root = self.base / "symlink-state"
        symlink_root.mkdir()
        backing = symlink_root / "backing.db"
        create_state_db(backing)
        (symlink_root / "state.sqlite").symlink_to(backing)
        with self.assertRaisesRegex(RuntimeError, "regular non-symlink"):
            CLEANER.discover_state_artifacts(str(symlink_root))

        hardlink_root = self.base / "hardlink-state"
        hardlink_db = hardlink_root / "state.sqlite"
        create_state_db(hardlink_db)
        os.link(hardlink_db, hardlink_root / "state.sqlite-wal")
        (hardlink_root / "state.sqlite-shm").write_bytes(b"shm")
        with self.assertRaisesRegex(RuntimeError, "one hard link"):
            CLEANER.discover_state_artifacts(str(hardlink_root))

        owner_root = self.base / "owner-state"
        owner_db = owner_root / "state.sqlite"
        create_state_db(owner_db)
        with mock.patch.object(CLEANER.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaisesRegex(RuntimeError, "current user"):
                CLEANER.discover_state_artifacts(str(owner_root))

    def test_state_database_uses_relative_read_only_uri_and_required_schema(self) -> None:
        expected_uri = "file:state%20v1.sqlite?mode=ro"
        self.assertEqual(CLEANER.state_database_uri("state v1.sqlite"), expected_uri)
        self.assertNotIn("immutable", expected_uri)
        self.assertNotIn("nolock", expected_uri)
        self.assertNotIn("vfs", expected_uri)
        self.assertEqual(
            CLEANER.read_recent_session_cwds(
                state_db=str(self.state_db),
                scan_limit=10,
                keep_limit=5,
                repo_root=str(self.repo),
                worktrees=[],
            ),
            [],
        )
        connection = sqlite3.connect(str(self.state_db))
        connection.execute(
            "INSERT INTO threads (cwd, archived, updated_at) VALUES ('.', 0, 1)"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(RuntimeError, "absolute paths"):
            CLEANER.read_recent_session_cwds(
                state_db=str(self.state_db),
                scan_limit=10,
                keep_limit=5,
                repo_root=str(self.repo),
                worktrees=[],
            )

        invalid_root = self.base / "invalid-schema"
        invalid_db = invalid_root / "state.sqlite"
        invalid_root.mkdir()
        connection = sqlite3.connect(str(invalid_db))
        connection.execute("CREATE TABLE threads (cwd TEXT)")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(RuntimeError, "required columns"):
            CLEANER.read_recent_session_cwds(
                state_db=str(invalid_db),
                scan_limit=10,
                keep_limit=5,
                repo_root=str(self.repo),
                worktrees=[],
            )

    def test_state_reader_uses_anchored_directory_during_pathname_swap(self) -> None:
        original_root = self.base / "anchored-state"
        original_db = original_root / "state.sqlite"
        parked_root = self.base / "parked-state"
        forged_root = self.base / "forged-state"
        create_state_db(original_db, ((str(self.repo), 0, 2),))
        create_state_db(forged_root / "state.sqlite")

        opened = CLEANER.open_state_directory(str(original_db))
        try:
            original_root.rename(parked_root)
            forged_root.rename(original_root)
            try:
                recent = CLEANER.run_state_reader_child(
                    opened=opened,
                    scan_limit=10,
                    keep_limit=5,
                    repo_root=str(self.repo),
                    worktrees=[],
                )
            finally:
                original_root.rename(forged_root)
                parked_root.rename(original_root)
            CLEANER.revalidate_open_state(opened)
        finally:
            CLEANER.close_open_state(opened)

        self.assertEqual(recent, [CLEANER.normalize_path(str(self.repo))])

    def test_machine_reader_runs_from_anonymous_cleaner_source_fd(self) -> None:
        source_fd = os.open(CLEANER_PATH, os.O_RDONLY)
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    CLEANER.STATE_READER_BOOTSTRAP,
                    str(source_fd),
                    str(CLEANER.STATE_READER_SOURCE_MAX_BYTES),
                    "--repo",
                    str(self.repo),
                    "--codex-home",
                    str(self.codex_home),
                    "--machine",
                ],
                pass_fds=(source_fd,),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
        finally:
            os.close(source_fd)

        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout)["schema_version"], 1)

    def test_state_reader_child_ignores_repo_local_python_modules(self) -> None:
        marker = self.base / "repo-module-loaded"
        (self.repo / "tqdm.py").write_text(
            f"open({str(marker)!r}, 'w', encoding='utf-8').write('loaded')\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(CLEANER_PATH),
                "--repo",
                str(self.repo),
                "--codex-home",
                str(self.codex_home),
                "--machine",
            ],
            cwd=self.repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )

        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout)["schema_version"], 1)
        self.assertFalse(marker.exists())

    def test_missing_state_fails_before_inventory_and_does_not_create_database(self) -> None:
        self.state_db.unlink()
        before = snapshot_tree(self.codex_home)
        args = SimpleNamespace(
            repo=str(self.repo),
            codex_home=str(self.codex_home),
            quarantine_root=str(self.codex_home / "quarantine"),
            shared_node_modules_source=str(self.repo / "node_modules"),
            list_registered=False,
            machine=True,
            scan_limit=200,
            limit=30,
            min_age_days=2,
            trim_artifacts_age_days=0.25,
            include_detached=False,
            apply=False,
            delete_workers=4,
            skip_legacy_purge=True,
        )
        with mock.patch.object(CLEANER, "parse_args", return_value=args):
            with mock.patch.object(CLEANER, "build_inventory") as build_inventory:
                with self.assertRaisesRegex(RuntimeError, "exactly one"):
                    CLEANER.main()
        build_inventory.assert_not_called()
        self.assertEqual(before, snapshot_tree(self.codex_home))

        result = subprocess.run(
            [
                str(CLEANER_PATH),
                "--repo",
                str(self.repo),
                "--codex-home",
                str(self.codex_home),
                "--machine",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("expected exactly one", result.stderr)
        self.assertEqual(before, snapshot_tree(self.codex_home))

    def test_machine_output_is_exact_and_keeps_active_session(self) -> None:
        active = self.slug_root / "active-session"
        add_worktree(self.repo, active, "active-session")
        connection = sqlite3.connect(str(self.state_db))
        connection.execute(
            "INSERT INTO threads (cwd, archived, updated_at) VALUES (?, 0, 1)",
            (str(active),),
        )
        connection.commit()
        connection.close()

        fake_bin = self.base / "machine-bin"
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

        result = subprocess.run(
            [
                str(CLEANER_PATH),
                "--repo",
                str(self.repo),
                "--codex-home",
                str(self.codex_home),
                "--min-age-days",
                "0",
                "--trim-artifacts-age-days",
                "0",
                "--machine",
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        expected = {
            "artifact_trim_candidates": 0,
            "kept_by_recent_sessions": 1,
            "kept_for_safety": 0,
            "mode": "dry-run",
            "recent_sessions_scanned": 1,
            "registered_non_main_worktrees": 1,
            "removal_candidates": 0,
            "report_only_paths": 0,
            "schema_version": 1,
        }
        self.assertEqual(
            result.stdout,
            json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n",
        )
        self.assertEqual(result.stderr, "")
        parsed = json.loads(result.stdout)
        self.assertEqual(list(parsed), sorted(expected))
        self.assertNotIn(str(active), result.stdout)
        for key, value in parsed.items():
            if key == "mode":
                self.assertIsInstance(value, str)
            else:
                self.assertIsInstance(value, int)

    def test_machine_mode_rejects_mutating_or_path_output_modes(self) -> None:
        for argv, diagnostic in (
            (
                ["agent-worktree-clean", "--machine", "--apply"],
                "--machine is audit-only and cannot be combined with --apply",
            ),
            (
                ["agent-worktree-clean", "--machine", "--list-registered"],
                "--machine cannot be combined with --list-registered",
            ),
        ):
            with self.subTest(argv=argv):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with mock.patch.object(CLEANER.sys, "argv", argv):
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        with self.assertRaises(SystemExit) as raised:
                            CLEANER.parse_args()
                self.assertNotEqual(raised.exception.code, 0)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn(diagnostic, stderr.getvalue())

    @unittest.skipUnless(
        sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(),
        "requires macOS sandbox-exec",
    )
    def test_active_wal_is_zero_touch_only_under_write_denial(self) -> None:
        active = self.slug_root / "active-wal"
        add_worktree(self.repo, active, "active-wal")
        fake_bin = self.base / "wal-bin"
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

        def command(codex_home: Path) -> list[str]:
            return [
                sys.executable,
                str(CLEANER_PATH),
                "--repo",
                str(self.repo),
                "--codex-home",
                str(codex_home),
                "--min-age-days",
                "0",
                "--trim-artifacts-age-days",
                "0",
                "--machine",
            ]

        plain_home = self.base / "plain-wal-codex"
        plain_db = plain_home / "state.sqlite"
        plain_writer = open_active_wal_state(plain_db, str(active))
        try:
            plain_before = snapshot_state_artifacts(plain_db)
            subprocess.run(
                command(plain_home),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            plain_after = snapshot_state_artifacts(plain_db)
            self.assertEqual(
                set(plain_before),
                {"state.sqlite", "state.sqlite-shm", "state.sqlite-wal"},
            )
            self.assertEqual(plain_before["state.sqlite"], plain_after["state.sqlite"])
            self.assertEqual(plain_before["state.sqlite-wal"], plain_after["state.sqlite-wal"])
            self.assertNotEqual(
                plain_before["state.sqlite-shm"],
                plain_after["state.sqlite-shm"],
            )
            self.assertNotEqual(
                plain_before,
                plain_after,
                "standalone SQLite mode=ro unexpectedly proved zero-touch on active WAL",
            )
        finally:
            plain_writer.close()

        sandbox_home = self.base / "sandbox-wal-codex"
        sandbox_db = sandbox_home / "state.sqlite"
        sandbox_writer = open_active_wal_state(sandbox_db, str(active))
        try:
            sandbox_before = snapshot_state_artifacts(sandbox_db)
            physical_root = str(sandbox_home.resolve()).replace("\\", "\\\\").replace('"', '\\"')
            policy = (
                "(version 1)"
                "(allow default)"
                f'(deny file-write* (subpath "{physical_root}"))'
            )
            result = subprocess.run(
                ["/usr/bin/sandbox-exec", "-p", policy, *command(sandbox_home)],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            self.assertEqual(
                set(sandbox_before),
                {"state.sqlite", "state.sqlite-shm", "state.sqlite-wal"},
            )
            self.assertEqual(sandbox_before, snapshot_state_artifacts(sandbox_db))
            payload = json.loads(result.stdout)
            self.assertEqual(payload["kept_by_recent_sessions"], 1)
            self.assertEqual(payload["removal_candidates"], 0)
            self.assertEqual(result.stderr, "")
        finally:
            sandbox_writer.close()

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
            with mock.patch.object(
                CLEANER,
                "discover_state_artifacts",
                return_value=("/tmp/state.sqlite", ("/tmp/state.sqlite",)),
            ):
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

    def test_skip_legacy_purge_flag_prevents_spawn(self) -> None:
        with mock.patch.object(
            CLEANER.sys,
            "argv",
            ["agent-worktree-clean", "--skip-legacy-purge"],
        ):
            self.assertTrue(CLEANER.parse_args().skip_legacy_purge)

        args = SimpleNamespace(
            repo=str(self.repo),
            codex_home=str(self.codex_home),
            quarantine_root=str(self.codex_home / "quarantine"),
            shared_node_modules_source=str(self.repo / "node_modules"),
            list_registered=False,
            machine=False,
            scan_limit=200,
            limit=30,
            min_age_days=2,
            trim_artifacts_age_days=0.25,
            include_detached=False,
            apply=True,
            delete_workers=4,
            skip_legacy_purge=True,
        )
        inventory = CLEANER.Inventory(
            common_dir=str(self.repo / ".git"),
            worktrees=[],
            report_only=[],
        )
        with mock.patch.object(CLEANER, "parse_args", return_value=args):
            with mock.patch.object(CLEANER, "build_inventory", return_value=inventory):
                with mock.patch.object(
                    CLEANER,
                    "discover_state_artifacts",
                    return_value=(str(self.state_db), (str(self.state_db),)),
                ):
                    with mock.patch.object(CLEANER, "collect_owned_worktree_paths", return_value=set()):
                        with mock.patch.object(CLEANER, "print_summary"):
                            with mock.patch.object(CLEANER, "apply_trims", return_value=0):
                                with mock.patch.object(CLEANER, "apply_removals", return_value=0):
                                    with mock.patch.object(CLEANER, "spawn_async_purge") as spawn:
                                        self.assertEqual(CLEANER.main(), 0)
        spawn.assert_not_called()

    def test_delete_failure_is_reported(self) -> None:
        failed_remove = SimpleNamespace(returncode=5, stdout="", stderr="permission denied")
        with mock.patch.object(CLEANER.subprocess, "run", return_value=failed_remove):
            self.assertFalse(CLEANER.delete_paths(["/tmp/example"]))

    def test_apply_aggregates_trim_and_removal_failures(self) -> None:
        candidate = CLEANER.Worktree(
            path="/tmp/example",
            head="abc",
            branch_ref="refs/heads/example",
            detached=False,
            age_days=10,
        )
        failed_remove = SimpleNamespace(returncode=1, stdout="", stderr="refused")

        with mock.patch.object(CLEANER, "progress_bar", return_value=mock.MagicMock()):
            with mock.patch.object(CLEANER, "revalidate_candidate", return_value=None):
                with mock.patch.object(CLEANER, "trim_worktree_artifacts", return_value=False):
                    trim_failures = CLEANER.apply_trims(
                        repo_root="/tmp/repo",
                        codex_home="/tmp/codex",
                        candidates=[candidate],
                        shared_node_modules_source="/tmp/repo/node_modules",
                        session_limit=30,
                        scan_limit=200,
                    )
                with mock.patch.object(CLEANER.subprocess, "run", return_value=failed_remove):
                    removal_failures = CLEANER.apply_removals(
                        repo_root="/tmp/repo",
                        codex_home="/tmp/codex",
                        candidates=[candidate],
                        include_detached=False,
                        session_limit=30,
                        scan_limit=200,
                    )

        self.assertEqual(trim_failures, 1)
        self.assertEqual(removal_failures, 1)

    def test_main_returns_nonzero_when_selected_mutations_fail(self) -> None:
        args = SimpleNamespace(
            repo=str(self.repo),
            codex_home=str(self.codex_home),
            quarantine_root=str(self.codex_home / "quarantine"),
            shared_node_modules_source=str(self.repo / "node_modules"),
            list_registered=False,
            machine=False,
            scan_limit=200,
            limit=30,
            min_age_days=2,
            trim_artifacts_age_days=0.25,
            include_detached=False,
            apply=True,
            delete_workers=4,
            skip_legacy_purge=False,
        )
        inventory = CLEANER.Inventory(
            common_dir=str(self.repo / ".git"),
            worktrees=[],
            report_only=[],
        )
        with mock.patch.object(CLEANER, "parse_args", return_value=args):
            with mock.patch.object(CLEANER, "build_inventory", return_value=inventory):
                with mock.patch.object(
                    CLEANER,
                    "discover_state_artifacts",
                    return_value=(str(self.state_db), (str(self.state_db),)),
                ):
                    with mock.patch.object(CLEANER, "collect_owned_worktree_paths", return_value=set()):
                        with mock.patch.object(CLEANER, "print_summary"):
                            with mock.patch.object(CLEANER, "apply_trims", return_value=1):
                                with mock.patch.object(CLEANER, "apply_removals", return_value=2):
                                    with mock.patch.object(CLEANER, "spawn_async_purge") as spawn:
                                        self.assertEqual(CLEANER.main(), 1)
        spawn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
