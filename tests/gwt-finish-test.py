#!/usr/bin/env python3
import contextlib
import errno
import fcntl
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
from pathlib import Path
import runpy
import sqlite3
import stat
import shutil
import struct
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


class PullRequestProofTests(unittest.TestCase):
    def read(self, head_repo, *, base="upstream/repo"):
        data = {"number": 1, "base": {"repo": {"full_name": base}, "ref": "main"},
                "head": {"sha": "a" * 40, "repo": head_repo},
                "merged": False, "merged_at": None, "state": "open"}
        with mock.patch.object(FINISH, "run", return_value=types.SimpleNamespace(
                stdout=json.dumps(data).encode())):
            return FINISH.pr("https://github.com/upstream/repo/pull/1")

    def test_fork_head_repository_is_normalized_and_base_identity_stays_bound(self):
        proof = self.read({"full_name": "Contributor/Repo"})
        self.assertEqual(proof["head_repo"], "contributor/repo")
        self.assertTrue(FINISH.primary_matches_checkout(proof, "contributor/repo", "a" * 40))
        self.assertFalse(FINISH.primary_matches_checkout(proof, "contributor/repo", "b" * 40))
        with self.assertRaisesRegex(FINISH.Retain, "github-pr-identity-mismatch"):
            self.read({"full_name": "contributor/repo"}, base="foreign/repo")

    def test_null_malformed_and_wrong_head_repositories_do_not_attest_a_fork(self):
        for value in (None, "contributor/repo", {}, {"full_name": None},
                      {"full_name": 1}, {"full_name": "contributor/repo/extra"},
                      {"full_name": " contributor/repo"}, {"full_name": "contributor/repo\n"},
                      {"full_name": "foreign/repo"}):
            with self.subTest(value=value):
                proof = self.read(value)
                self.assertFalse(FINISH.primary_matches_checkout(proof, "contributor/repo", "a" * 40))
                self.assertTrue(FINISH.primary_matches_checkout(proof, "upstream/repo", "a" * 40))


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

    def test_read_only_status_preserves_ledger_and_matches_status(self):
        expected = self.call("status", "--all")[1]
        before = {
            path.name: (path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ctime_ns)
            for path in self.state.iterdir()
        }
        self.assertEqual(self.call("status", "--all", "--read-only"), (0, expected))
        self.assertEqual(self.call("status", "--read-only"), (0, expected))
        self.assertEqual(before, {
            path.name: (path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ctime_ns)
            for path in self.state.iterdir()
        })

    def test_bulk_inventory_is_read_only_additive_and_does_not_query_git(self):
        flags = ("--all", "--read-only", "--inventory-json")
        old = self.call("status", "--all", "--read-only")
        with mock.patch.object(FINISH, "git", side_effect=AssertionError("unexpected Git invocation")):
            code, view = self.call("status", *flags)
            self.assertEqual(code, 0)
            self.assertEqual(view["schema"], "gwt-finish-inventory.v1")
            self.assertEqual(view["ledger"], "present")
            self.assertEqual(view["source"], "gwt finish-status --all --read-only --inventory-json")
            row, = view["records"]
            self.assertEqual(row["owner_signoff"], "report-only")
            self.assertEqual(row["owner_count"], 1)
            self.assertNotIn("owner-1", json.dumps(view))
            self.assertEqual(row["identity"]["checkout"], {
                "path": str(self.wt), "dev": str(self.wt.stat().st_dev), "ino": str(self.wt.stat().st_ino)})
            self.assertIsNone(row["lock_reason"])
            self.assertEqual(self.call("status", "--all", "--read-only"), old)
        for command in (("status", "--inventory-json"), ("status", "--all", "--inventory-json"),
                        ("check", *flags), ("enroll", *flags)):
            with self.subTest(command=command), self.assertRaisesRegex(FINISH.Retain, "inventory-json-requires"):
                self.call(*command)
        with (self.state / "lifecycle.lock").open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(FINISH.Retain, "lifecycle-busy"):
                self.call("status", *flags)
        absent = self.state / "no-ledger"
        self.assertEqual(self.call("status", *flags, "--state-dir", str(absent))[1],
                         {**view, "ledger": "absent", "records": []})
        self.assertFalse(absent.exists())

    def test_bulk_inventory_projects_current_generation_exact_heads_and_private_identities(self):
        flags = ("--all", "--read-only", "--inventory-json")
        token = "gwt-finish.v2:" + str(uuid.uuid4())
        with FINISH.ledger(self.state) as db:
            source = dict(FINISH.get_row(db, self.wt))
            key, snap = source["id"], json.loads(source["identity"])
            snap.update(lock_reason=token, path_id=[1 << 53, (1 << 63) + 3])
            db.execute("UPDATE worktrees SET state='finished', finish_head=?, identity=? WHERE id=?",
                       (self.head, json.dumps(snap), key))
            db.execute("UPDATE owners SET completed=1 WHERE worktree_id=?", (key,))
            db.execute("INSERT INTO retirement(worktree_id,generation,state,inventory) VALUES (?,7,'enrolled',?)",
                       (key, "PRIVATE retirement payload"))
            db.execute("INSERT INTO owner_releases VALUES (?, 'owner-1', 6)", (key,))
            db.commit()
        def projected():
            row, = self.call("status", *flags)[1]["records"]
            return row
        row = projected()
        self.assertEqual(row["identity"]["checkout"]["ino"], str((1 << 63) + 3))
        self.assertEqual(row["identity"]["root"], str(self.root))
        self.assertEqual(row["identity"]["repo"], "example/repo")
        self.assertIsNone(FINISH.inventory_identity('{"path":"/one","path_id":[true,2]}')[0]["checkout"])
        self.assertEqual((row["finish_head"], row["branch"], row["lock_reason"]),
                         (self.head, "refs/heads/feature", token))
        self.assertEqual((row["generation"], row["released_count"], row["owner_signoff"]), (7, 0, "pending"))
        self.assertNotIn("PRIVATE", json.dumps(row))
        with FINISH.ledger(self.state) as db:
            db.execute("UPDATE owner_releases SET generation=7 WHERE worktree_id=?", (key,))
            db.commit()
        self.assertEqual(projected()["owner_signoff"], "released")
        with FINISH.ledger(self.state) as db:
            db.execute("UPDATE retirement SET intent=?, result=? WHERE worktree_id=?",
                       ("PRIVATE intent", "PRIVATE result", key))
            db.commit()
        row = projected()
        self.assertTrue(row["has_intent"] and row["has_result"])
        self.assertEqual(row["checkout"], "unknown")
        self.assertNotIn("PRIVATE", json.dumps(row))
        with FINISH.ledger(self.state) as db:
            FINISH.ensure_cancellation_schema(db)
            db.execute("UPDATE owners SET completed=0 WHERE worktree_id=?", (key,))
            db.execute("INSERT INTO owner_cancellations VALUES (?, 'owner-1', ?, 'private reason', 1)", (key, self.head))
            db.commit()
        row = projected()
        self.assertEqual((row["cancelled_count"], row["cancellation_head"], row["owner_signoff"]),
                         (1, self.head, "cancelled"))
        with FINISH.ledger(self.state) as db:
            db.execute("UPDATE owner_cancellations SET head=? WHERE worktree_id=?", ("d" * 40, key))
            db.commit()
        row = projected()
        self.assertIsNone(row["cancellation_head"])
        self.assertEqual(row["owner_signoff"], "pending")
        with FINISH.ledger(self.state) as db:
            db.execute("UPDATE worktrees SET finish_head=? WHERE id=?", ("e" * 40, key))
            db.commit()
        self.assertEqual(projected()["cancelled_count"], 0)

    def test_bulk_inventory_query_count_does_not_grow_with_rows(self):
        with FINISH.ledger(self.state) as db:
            def queries():
                sql = []
                db.set_trace_callback(sql.append)
                try:
                    data = FINISH.inventory_status(db)["records"]
                finally:
                    db.set_trace_callback(None)
                return sql, len(data)
            first, n = queries()
            source = dict(FINISH.get_row(db, self.wt))
            for i in range(12):
                row = {**source, "id": str(uuid.uuid4()), "path": str(self.root / f"inventory-{i}")}
                db.execute("INSERT INTO worktrees (" + ",".join(row) + ") VALUES ("
                           + ",".join("?" for _ in row) + ")", tuple(row.values()))
            second, count = queries()
            self.assertEqual(count, n + 12)
            self.assertEqual(first, second)
            self.assertLessEqual(len(second), 12)

    def test_creation_enrollment_still_refuses_when_ledger_stays_busy(self):
        with (self.state / "lifecycle.lock").open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(FINISH.Retain, "lifecycle-busy"):
                with FINISH.ledger(self.state, create=True, wait_seconds=0.01):
                    self.fail("entered a ledger held by another operation")
            with mock.patch.object(FINISH.time, "sleep") as wait:
                with self.assertRaisesRegex(FINISH.Retain, "lifecycle-busy"):
                    self.call("enroll")
                wait.assert_not_called()

    def test_read_only_status_requires_existing_uncontended_lock_and_clean_database(self):
        lock = self.state / "lifecycle.lock"
        with lock.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(FINISH.Retain, "lifecycle-busy"):
                self.call("status", "--read-only")
        sidecar = self.state / "lifecycle.sqlite-journal"
        sidecar.write_bytes(b"pending")
        sidecar.chmod(0o600)
        with self.assertRaisesRegex(FINISH.Retain, "lifecycle-sidecar-requires-recovery"):
            self.call("status", "--read-only")
        self.assertEqual(sidecar.read_bytes(), b"pending")
        sidecar.unlink()
        lock.unlink()
        with self.assertRaisesRegex(FINISH.Retain, "lifecycle-lock-required"):
            self.call("status", "--read-only")
        self.assertFalse(lock.exists())

    def test_read_only_status_works_without_write_permission(self):
        database = self.state / "lifecycle.sqlite"
        lock = self.state / "lifecycle.lock"
        database.chmod(0o400)
        lock.chmod(0o400)
        self.state.chmod(0o500)
        try:
            result, rows = self.call("status", "--all", "--read-only")
            self.assertEqual(result, 0)
            self.assertEqual([row["worktree"] for row in rows], [str(self.wt)])
        finally:
            self.state.chmod(0o700)
            lock.chmod(0o600)
            database.chmod(0o600)

    def test_read_only_option_rejects_mutation_commands(self):
        with self.assertRaisesRegex(FINISH.Retain, "read-only-is-only-valid-for-status"):
            self.call("check", "--read-only")

    def test_finish_and_check_confirm_completion_but_retain_checkout(self):
        result = self.finish()[1][0]
        self.assertEqual(result["state"], "finished")
        self.assertEqual(result["checkout"], "retained")
        self.assertFalse(result["release_available"])
        self.assertFalse(result["removal_available"])
        self.assertEqual(self.check_reason(), "completion-confirmed-checkout-retained")
        self.assertTrue(self.wt.exists())

    def test_explicit_release_still_rejects_report_only_enrollment(self):
        with self.assertRaisesRegex(FINISH.Retain, "legacy-enrollment-report-only"):
            self.finish("--release")
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

    def test_fork_completion_uses_base_default_and_retains_open_pr(self):
        self.url = "https://github.com/upstream/repo/pull/1"
        self.proofs[self.url] = self.proof(
            self.url, repo="upstream/repo", head_repo="example/repo",
            target="trunk", merged=False, state="open", merge=None)
        with mock.patch.object(FINISH, "default_target", return_value="trunk") as target:
            result = self.finish()[1][0]
        target.assert_called_once_with("upstream/repo")
        self.assertEqual(result["state"], "finished")
        self.assertEqual(result["pr"], self.url)
        self.assertEqual(self.check_reason(), "pr-not-merged")
        self.assertTrue(self.wt.exists())

    def test_fork_primary_needs_origin_identity_and_exact_head(self):
        self.url = "https://github.com/upstream/repo/pull/1"
        valid = self.proof(self.url, repo="upstream/repo", head_repo="example/repo")
        for changes in ({"head_repo": None}, {"head_repo": "foreign/repo"}, {"head": "b" * 40}):
            with self.subTest(changes=changes):
                self.proofs[self.url] = {**valid, **changes}
                with self.assertRaisesRegex(FINISH.Retain, "primary-pr-repository-or-head-mismatch"):
                    self.finish()
                self.assertIsNone(self.call("status")[1][0]["pr"])
        self.url = "https://github.com/example/repo/pull/1"
        self.proofs[self.url] = self.proof(self.url, head_repo=None)
        self.assertEqual(self.finish()[1][0]["state"], "finished")

    def test_fork_stack_dependencies_share_base_not_contributor_repository(self):
        self.url = "https://github.com/upstream/repo/pull/1"
        self.proofs[self.url] = self.proof(self.url, repo="upstream/repo", head_repo="example/repo")
        upper = "https://github.com/upstream/repo/pull/2"
        dependency = self.proof(upper, repo="upstream/repo", head_repo="other/contributor", head="b" * 40)
        self.proofs[upper] = dependency
        self.finish("--wait-for", upper)
        self.assertEqual(self.check_reason(), "completion-confirmed-checkout-retained")
        foreign = "https://github.com/example/repo/pull/2"
        self.proofs[foreign] = self.proof(foreign, head="b" * 40)
        with self.assertRaisesRegex(FINISH.Retain, "invalid-stack-dependency"):
            self.finish("--wait-for", foreign)
        self.proofs[upper] = {**dependency, "target": "other"}
        with self.assertRaisesRegex(FINISH.Retain, "stack-pr-not-targeting-final-branch"):
            self.finish("--wait-for", upper)

    def test_fork_completion_rechecks_head_repository_head_and_target(self):
        self.url = "https://github.com/upstream/repo/pull/1"
        valid = self.proof(self.url, repo="upstream/repo", head_repo="example/repo")
        self.proofs[self.url] = valid
        self.finish()
        for changes in ({"head_repo": None}, {"head_repo": "foreign/repo"},
                        {"head": "b" * 40}, {"target": "other"}):
            with self.subTest(changes=changes):
                self.proofs[self.url] = {**valid, **changes}
                self.assertEqual(self.check_reason(), "primary-pr-proof-changed")
                self.assertTrue(self.wt.exists())

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
    def test_recover_missing_wrapper_forwards_exact_native_operands(self):
        wrapper = ROOT / "functions/gwt/gwt.zsh"
        program = 'source "$1"; _gwt_finish_tool() { printf "%s\\n" "$@"; }; gwt recover-missing --worktree /original/path --generation 0'
        result = subprocess.run(["zsh", "-f", "-c", program, "fixture", str(wrapper)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         ["recover-missing", "--worktree", "/original/path", "--generation", "0"])

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

    def test_holder_qualification_is_probe_free_and_requires_supported_platform(self):
        from contextlib import redirect_stdout

        output = io.StringIO()
        with mock.patch.object(FINISH, "ledger") as ledger, \
                mock.patch.object(FINISH, "run") as run, redirect_stdout(output):
            self.assertEqual(FINISH.main(["holder-qualification"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {
            "schema": FINISH.HOLDER_SCHEMA,
            "backend": "darwin-libproc-bounded.v1",
            "qualified": sys.platform == "darwin" and os.uname().machine == "arm64",
            "reason": ("bounded-native-observation-requires-host-qualification"
                       if sys.platform == "darwin" and os.uname().machine == "arm64"
                       else "holder-backend-unqualified-platform"),
            "mapping_coverage": ("bounded-native" if sys.platform == "darwin"
                                 and os.uname().machine == "arm64" else "unqualified"),
            "platform": FINISH.safety().platform_key(),
        })
        ledger.assert_not_called()
        run.assert_not_called()

    def test_absent_status_and_check_are_side_effect_free(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            for args in (("status", "--all"), ("status", "--all", "--read-only"),
                         ("check", "--all")):
                result = subprocess.run(
                    [str(TOOL), *args, "--state-dir", str(state)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "[]\n")
                self.assertFalse(state.exists())

    def test_read_only_single_status_preserves_absent_not_enrolled_result(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            for create_state in (False, True):
                if create_state:
                    state.mkdir(mode=0o700)
                for read_only in (False, True):
                    args = [str(TOOL), "status", "--state-dir", str(state)]
                    if read_only:
                        args.append("--read-only")
                    result = subprocess.run(args, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("(not-enrolled)", result.stderr)
                self.assertEqual(list(state.iterdir()) if state.exists() else [], [])

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
_gwt_finish_tool() { [[ "$1" != creation-check ]]; }
if gwt new feature main --full --finish-managed; then exit 88; fi
[[ ! -e "$3" ]] || exit 87
git show-ref --verify --quiet refs/heads/feature && exit 86
_gwt_finish_tool() { "$FAKE_FINISH" "$@"; }
gwt new feature main --full --finish-managed || exit
[[ "$PWD" == "$3" ]] || exit 91
gwt finish --pr https://github.com/example/repo/pull/1 || exit
[[ "$PWD" == "$2" ]] || exit 90
cd "$3"
gwt resume --acknowledge-device-renumbering --worktree "$3" || exit
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
            self.assertLess(recorded.index("creation-check --worktree"), recorded.index("creation-token"))
            self.assertIn("enroll --worktree", recorded)
            self.assertIn("finish --pr", recorded)
            self.assertIn("resume --acknowledge-device-renumbering --worktree", recorded)
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
    check_reason = LifecycleTests.check_reason

    def finish(self, *args):
        # This suite exercises the activated unattended backend explicitly.
        return LifecycleTests.finish(self, "--policy", str(self.base / "policy.json"), *args)

    def setUp(self):
        LifecycleTests.setUp(self)
        self.legacy = self.wt
        parent = self.root
        if getattr(self, "recovery_container", False):
            parent /= "example-repo"
            parent.mkdir(mode=0o700)
        self.wt = parent / "released-task"
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
        # Bounded coverage is synthetic lifecycle proof, never host qualification.
        self.observer.observe.return_value = {
            "holders": [], "processes": 1, "mapping_coverage": "bounded-native"}
        self.capability = mock.patch.object(FINISH, "holder_qualification", return_value={
            "qualified": True, "mapping_coverage": "bounded-native"})
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

    def assert_release_blocked(self, reason):
        result = self.finish("--release")[1][0]
        self.assertEqual(result["checkout"], "retained", result)
        self.assertEqual(result["released_owners"], ["owner-1"])
        self.assertEqual(result["automatic_handoff"]["baseline"], "unavailable")
        self.assertRegex(result["automatic_handoff"]["blocker"], reason)
        return result

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

    def test_plain_finish_releases_completed_owner_and_removes_eligible_checkout(self):
        result = self.finish()[1][0]
        self.assertEqual(result["released_owners"], ["owner-1"])
        self.assertEqual(result["checkout"], "removed", result)
        self.assertFalse(self.wt.exists())

    def test_old_private_v2_enrollment_cannot_adopt_bounded_automatic_contract(self):
        with FINISH.ledger(self.state) as db:
            db.execute("ALTER TABLE retirement DROP COLUMN holder_contract")
        self.assertFalse(self.call("status")[1][0]["automatic_removal_supported"])
        # Enrollment owns schema upgrade, but retrying it cannot adopt an old row.
        with self.assertRaisesRegex(FINISH.Retain, "already-enrolled"):
            self.call("enroll", "--new-token", self.token)
        self.call("resume")
        result = self.finish()[1][0]
        self.assertEqual(result["reason"], "automatic-holder-contract-not-enrolled")
        self.assertFalse(result["automatic_removal_supported"])
        self.assertEqual(result["released_owners"], ["owner-1"])
        self.assertEqual(result["automatic_handoff"]["owner_retry"],
                         "manual-finalized-closeout-from-owning-checkout")
        with FINISH.ledger(self.state) as db:
            self.assertIsNone(FINISH.retirement(db, FINISH.get_row(db, self.wt))["holder_contract"])
        # Existing explicit foreground closeout keeps its prior contract.
        with mock.patch.object(FINISH, "manual_holders"):
            self.assertEqual(LifecycleTests.finish(self)[1][0]["checkout"], "removed")

    def test_unqualified_apply_preserves_release_without_admission_work(self):
        with mock.patch.object(FINISH, "holder_qualification", return_value={
                "qualified": False, "mapping_coverage": "unqualified"}), \
                mock.patch.object(FINISH.safety(), "maintainer_lock") as lock, \
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
            "holders": [], "processes": 2, "mapping_coverage": "bounded-native"}
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
                        ({"holders": [], "mapping_coverage": "complete"}, "holder-mapping-coverage-unqualified"),
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
        self.assertEqual(second["released_owners"], ["owner-1", "owner-2"])
        self.assertEqual(second["checkout"], "removed")

    def test_unclassified_ignored_payload_is_never_deleted(self):
        (self.wt / "ignored").write_text("synthetic recovery")
        self.assert_release_blocked("ignored-roots-require-explicit-disposal")
        self.assertEqual((self.wt / "ignored").read_text(), "synthetic recovery")

    def test_released_inventory_change_requires_new_signoff(self):
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        self.finish("--release")
        (self.wt / "late").write_text("retain")
        self.proofs[self.url].update(merged=True, state="closed", merge="a" * 40)
        self.assertEqual(self.call("check", "--apply")[1][0]["checkout"], "retained")
        self.assertTrue((self.wt / "late").exists())

    def test_legacy_full_inventory_still_rejects_pure_timestamp_change(self):
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        self.finish("--release")
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            snap = FINISH.revalidate(row)
            until = time.monotonic() + 10
            legacy = {"working": FINISH.safety().inventory(snap, FINISH.git, until),
                      "admin": FINISH.safety().admin_inventory(snap, FINISH.git, until)}
            with db:
                db.execute("UPDATE retirement SET inventory=? WHERE worktree_id=?",
                           (json.dumps(legacy, sort_keys=True), row["id"]))
        os.utime(self.wt / "file", (time.time() + 5, time.time() + 5))
        self.proofs[self.url].update(merged=True, state="closed", merge="a" * 40)
        self.assertEqual(self.call("check", "--apply")[1][0]["reason"], "released-worktree-changed")
        self.assertTrue(self.wt.exists())

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

    def test_late_payload_after_first_admission_retains_enrollment_and_target(self):
        count = 0
        def observe(*args):
            nonlocal count
            count += 1
            if count == 1:
                (self.wt / "late-recovery").write_text("retain")
            return {"holders": [], "mapping_coverage": "bounded-native"}
        self.observer.observe.side_effect = observe
        result = self.finish("--release")[1][0]
        self.assertEqual(result["checkout"], "retained")
        self.assertEqual(result["retirement_state"], "enrolled")
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
        with (self.state / "lifecycle.lock").open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release(_):
                fcntl.flock(held, fcntl.LOCK_UN)

            with mock.patch.object(FINISH.time, "sleep", side_effect=release) as wait:
                self.call("enroll", "--new-token", self.token)
                wait.assert_called_once()
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
        git(self.wt, "config", "--worktree", "core.hooksPath", "git-hooks")
        git(self.wt, "config", "--worktree", "user.fixtureNote", "preserve")
        self.assert_release_blocked("worktree-config-recovery-required")
        self.assertTrue(self.wt.exists())
        git(self.wt, "config", "--worktree", "--unset", "user.fixtureNote")
        for values in (("/tmp/custom-hooks",), ("",), ("git-hooks", "git-hooks")):
            with self.subTest(hooks=values):
                git(self.wt, "config", "--worktree", "--replace-all", "core.hooksPath", values[0])
                for value in values[1:]:
                    git(self.wt, "config", "--worktree", "--add", "core.hooksPath", value)
                self.call("resume")
                self.assert_release_blocked("worktree-config-recovery-required")
                self.assertTrue(self.wt.exists())
        git(self.wt, "config", "--worktree", "--replace-all", "core.hooksPath", "git-hooks")
        self.call("resume")
        self.assertEqual(self.finish("--release")[1][0]["checkout"], "removed")

    def test_post_release_admin_config_change_requires_new_signoff(self):
        git(self.repo, "config", "extensions.worktreeConfig", "true")
        for key, value in (("dotfiles.sparseProfile", "custom"), ("core.hooksPath", None)):
            with self.subTest(key=key):
                git(self.wt, "config", "--worktree", "dotfiles.sparseProfile", "full")
                git(self.wt, "config", "--worktree", "core.hooksPath", "git-hooks")
                self.proofs[self.url].update(merged=False, state="open", merge=None)
                self.finish("--release")
                if value is None:
                    with (self.admin / "config.worktree").open("ab") as config:
                        config.write(b"# repository hooks unchanged\n")
                else:
                    git(self.wt, "config", "--worktree", key, value)
                self.proofs[self.url].update(merged=True, state="closed", merge="a" * 40)
                result = self.call("check", "--apply")[1][0]
                self.assertEqual(result["reason"], "released-admin-changed-resume-first")
                self.assertTrue(self.wt.exists())
                self.call("resume")

    def test_present_skip_worktree_file_is_not_exempt_from_bytes_proof(self):
        git(self.wt, "update-index", "--skip-worktree", "file")
        (self.wt / "file").write_text("must retain\n")
        self.assert_release_blocked("working-bytes")
        self.assertTrue(self.wt.exists())

    def test_inventory_verifies_tracked_blob_larger_than_leaf_limit(self):
        large = self.wt / "large-blob"
        large.write_bytes(b"source\n" * (9 * 1024 * 1024 // 7))
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-m", "large tracked fixture")
        snap = FINISH.revalidate(self.row())
        import time
        proof = FINISH.safety().inventory(snap, FINISH.git, time.monotonic() + 10)
        self.assertIn("large-blob", proof["entries"])
        with large.open("r+b") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(FINISH.Retain, "working-bytes"):
            FINISH.safety().inventory(snap, FINISH.git, time.monotonic() + 10)
        self.assertTrue(self.wt.exists())

    def test_inventory_supports_more_than_old_32mib_and_bounds_the_actual_next_read(self):
        # Five distinct, compressible 7MiB blobs exercise the former failure
        # without a large object store.
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
        total = sum((self.wt / name).stat().st_size
                    for name in git(self.wt, "ls-files").splitlines())
        with mock.patch.object(module, "PASS_LIMIT", 32 * 1024 * 1024), \
                mock.patch.object(module, "SOURCE_LIMIT", total):
            contents = module.inventory(snap, FINISH.git, time.monotonic() + 10)
            self.assertTrue(all(f"bulk-{number}" in contents["entries"] for number in range(5)))
        with mock.patch.object(module, "SOURCE_LIMIT", total - 1):
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
        self.assert_release_blocked("auto-merge-tree-recovery")
        self.assertTrue(self.wt.exists())

    def test_corrupt_or_linked_branch_reflog_cannot_attest_recovery(self):
        old_tip, reflog = self.rebased_task()
        before = reflog.read_bytes()
        reflog.write_bytes(old_tip.encode() + b" " + self.head.encode() + b" corrupt\n")
        self.assertEqual(self.finish("--release")[1][0]["reason"], "branch-reflog-proof-invalid")
        self.assertTrue((self.admin / "locked").exists())
        reflog.write_bytes(before)
        self.call("resume")
        real = reflog.parent
        moved = real.with_name("saved-heads")
        real.rename(moved)
        real.symlink_to(moved, target_is_directory=True)
        try:
            self.assert_release_blocked("branch-reflog-parent-unqualified")
            self.assertTrue(self.wt.exists())
        finally:
            real.unlink()
            moved.rename(real)

    def test_fetch_head_without_preserved_recovery_holds_owner_release(self):
        recovery = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", self.head, "-m", "unpreserved")
        data = (recovery + "\t\tbranch 'other' of https://example.test/repo\n").encode()
        (self.admin / "FETCH_HEAD").write_bytes(data)
        self.assert_release_blocked("fetch-head-recovery-required")
        self.assertEqual((self.admin / "FETCH_HEAD").read_bytes(), data)
        self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.token)

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

    def fast_forwarded_task(self, authored_message=None):
        (self.wt / "file").write_text("authored change\n")
        if authored_message is None:
            git(self.wt, "commit", "-am", "authored message")
        else:
            message_file = self.base / "authored-message"
            message_file.write_bytes(authored_message)
            git(self.wt, "commit", "-a", "-F", str(message_file))
        authored = git(self.wt, "rev-parse", "HEAD")
        message = (self.admin / "COMMIT_EDITMSG").read_bytes()
        git(self.repo, "merge", "--ff-only", authored)
        (self.repo / "integrated").write_text("sibling change\n")
        git(self.repo, "add", "integrated")
        git(self.repo, "commit", "-m", "integrated message")
        git(self.wt, "merge", "--ff-only", "main")
        self.head = git(self.wt, "rev-parse", "HEAD")
        self.proofs[self.url] = self.proof(self.url)
        self.assertNotEqual(authored, self.head)
        self.assertEqual((self.admin / "COMMIT_EDITMSG").read_bytes(), message)
        return authored, message

    def test_fast_forwarded_committed_message_allows_automatic_release(self):
        authored, _ = self.fast_forwarded_task()
        result = self.finish()[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertEqual(git(self.repo, "show", "-s", "--format=%B", authored), "authored message")
        self.assertTrue(self.legacy.exists())

    def test_matching_unpreserved_commit_does_not_attest_message(self):
        recovery = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", self.head, "-m", "unpreserved message")
        message = b"unpreserved message\n"
        (self.admin / "COMMIT_EDITMSG").write_bytes(message)
        self.assert_release_blocked("commit-message-recovery-required")
        self.assertEqual((self.admin / "COMMIT_EDITMSG").read_bytes(), message)
        self.assertEqual(git(self.repo, "show", "-s", "--format=%B", recovery), "unpreserved message")

    def test_message_proof_rejects_noncommit_and_missing_roots(self):
        authored, message = self.fast_forwarded_task()
        payload = self.base / "message-blob"
        payload.write_bytes(message + b"\0")
        blob = git(self.repo, "hash-object", "-w", str(payload))
        git(self.repo, "tag", "-a", "message-proof", authored, "-m", "annotated proof")
        roots = {
            "blob": blob,
            "tree": git(self.repo, "rev-parse", authored + "^{tree}"),
            "tag": git(self.repo, "rev-parse", "refs/tags/message-proof"),
            "missing": "f" * 40,
        }
        snap = FINISH.revalidate(self.row())
        for kind, oid in roots.items():
            with self.subTest(kind=kind):
                # A valid matching commit must not hide another invalid witness
                # in the same bounded read window, even when it sorts first.
                with self.assertRaisesRegex(FINISH.Retain, "commit-message-proof-invalid"):
                    FINISH.safety().admin_inventory(
                        snap, FINISH.git, time.monotonic() + 10,
                        preserved_oids={authored, oid})
                self.assertEqual((self.admin / "COMMIT_EDITMSG").read_bytes(), message)
                self.assertTrue((self.admin / "locked").exists())

    def test_message_proof_ignores_replacement_objects(self):
        authored, message = self.fast_forwarded_task()
        replacement = git(self.repo, "commit-tree", authored + "^{tree}",
                          "-m", "replacement message")
        git(self.repo, "replace", authored, replacement)
        snap = FINISH.revalidate(self.row())
        FINISH.safety().admin_inventory(
            snap, FINISH.git, time.monotonic() + 10, preserved_oids={authored})
        self.assertEqual((self.admin / "COMMIT_EDITMSG").read_bytes(), message)

        payload = self.base / "replaced-blob"
        payload.write_bytes(message + b"\0")
        blob = git(self.repo, "hash-object", "-w", str(payload))
        git(self.repo, "update-ref", "refs/replace/" + blob, authored)
        with self.assertRaisesRegex(FINISH.Retain, "commit-message-proof-invalid"):
            FINISH.safety().admin_inventory(
                snap, FINISH.git, time.monotonic() + 10, preserved_oids={blob})
        self.assertEqual((self.admin / "COMMIT_EDITMSG").read_bytes(), message)
        self.assertTrue((self.admin / "locked").exists())

    def test_message_proof_rejects_malformed_batch_output(self):
        authored, message = self.fast_forwarded_task()
        snap = FINISH.revalidate(self.row())
        valid = (authored + " commit\n").encode()
        original = FINISH.git
        for output in (b"", valid[:-1], valid + b"\n", valid * 2,
                       valid.replace(b"commit", b"blob"), b"0" * 40 + b" commit\n"):
            with self.subTest(output=output):
                def malformed(path, *args, **kwargs):
                    if "--batch-check=%(objectname) %(objecttype)" in args:
                        return output
                    return original(path, *args, **kwargs)

                with mock.patch.object(FINISH, "git", side_effect=malformed), \
                        self.assertRaisesRegex(FINISH.Retain, "commit-message-proof-invalid"):
                    FINISH.safety().admin_inventory(
                        snap, FINISH.git, time.monotonic() + 10, preserved_oids={authored})
                self.assertEqual((self.admin / "COMMIT_EDITMSG").read_bytes(), message)
                self.assertTrue((self.admin / "locked").exists())

    def test_empty_message_proof_roots_retain_without_batch_input(self):
        self.fast_forwarded_task()
        snap = FINISH.revalidate(self.row())
        with mock.patch.object(FINISH, "git", wraps=FINISH.git) as queries, \
                self.assertRaisesRegex(FINISH.Retain, "commit-message-recovery-required"):
            FINISH.safety().admin_inventory(
                snap, FINISH.git, time.monotonic() + 10, preserved_oids=set())
        self.assertFalse(any("cat-file" in call.args for call in queries.call_args_list))
        self.assertTrue((self.admin / "locked").exists())

    def message_proof_roots(self):
        roots = {git(self.repo, "commit-tree", self.head + "^{tree}", "-p", self.head,
                     "-m", "preserved message " + str(number)) for number in range(129)}
        return sorted(roots)

    def test_committed_message_proof_bounds_actual_reads(self):
        roots = self.message_proof_roots()
        with FINISH.ledger(self.state) as db:
            snap = FINISH.revalidate(FINISH.get_row(db, self.wt))
        saved = self.admin / "COMMIT_EDITMSG"
        cases = ((roots[:128], 127, None), (roots, 127, None),
                 (roots, 128, "commit-message-proof-limit"),
                 (roots, None, "commit-message-proof-limit"),
                 (roots[:128], None, "commit-message-recovery-required"))
        for candidates, match, reason in cases:
            with self.subTest(population=len(candidates), match=match, reason=reason):
                message = (git(self.repo, "show", "-s", "--format=%B", roots[match]) + "\n").encode() \
                    if match is not None else b"uncommitted draft\n"
                saved.write_bytes(message)
                with mock.patch.object(FINISH, "git", wraps=FINISH.git) as queries:
                    if reason is None:
                        FINISH.safety().admin_inventory(
                            snap, FINISH.git, time.monotonic() + 20, preserved_oids=set(candidates))
                    else:
                        with self.assertRaisesRegex(FINISH.Retain, reason):
                            FINISH.safety().admin_inventory(
                                snap, FINISH.git, time.monotonic() + 20, preserved_oids=set(candidates))
                shows = [call.args for call in queries.call_args_list if "show" in call.args]
                self.assertEqual([args[-1] for args in shows], [self.head, *candidates[:128]])
                self.assertEqual(saved.read_bytes(), message)
                self.assertTrue((self.admin / "locked").exists())

    def large_target_reflog(self):
        self.fast_forwarded_task()
        roots = self.message_proof_roots()
        for oid in roots:
            git(self.repo, "update-ref", "--create-reflog", "refs/remotes/origin/main", oid)
        saved = (git(self.repo, "show", "-s", "--format=%B", roots[0]) + "\n").encode()
        (self.admin / "COMMIT_EDITMSG").write_bytes(saved)
        with FINISH.ledger(self.state) as db:
            snap = FINISH.revalidate(FINISH.get_row(db, self.wt))
        refs, composed = FINISH.release_refs(snap, "main", time.monotonic() + 10)
        self.assertIn(snap["branch"], refs)
        self.assertIn("refs/remotes/origin/main", refs)
        self.assertGreater(len(composed - {self.head}), 128)
        self.assertLess(sorted(composed - {self.head}).index(roots[0]), 128)

    def test_large_target_reflog_allows_automatic_message_proof(self):
        self.large_target_reflog()
        result = self.finish()[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertTrue(self.legacy.exists())
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)

    def test_large_head_message_keeps_metadata_capture_limit(self):
        message = self.base / "large-message"
        message.write_bytes(b"large message\n\n" + b"body\n" * (1024 * 1024 // 5 + 1))
        git(self.wt, "commit", "--allow-empty", "-F", str(message))
        self.head = git(self.wt, "rev-parse", "HEAD")
        self.proofs[self.url] = self.proof(self.url)
        self.assertEqual(self.finish()[1][0]["checkout"], "removed")

    def test_large_preserved_message_keeps_metadata_capture_limit(self):
        self.fast_forwarded_task(b"large message\n\n" + b"body\n" * (1024 * 1024 // 5 + 1))
        self.assertEqual(self.finish()[1][0]["checkout"], "removed")

    def test_oversized_preserved_message_retains_saved_draft(self):
        message = self.base / "oversized-message"
        message.write_bytes(b"oversized message\n\n" + b"body\n" * (FINISH.safety().LEAF_LIMIT // 5 + 1))
        recovery = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", self.head, "-F", str(message))
        draft = b"uncommitted draft\n"
        (self.admin / "COMMIT_EDITMSG").write_bytes(draft)
        with FINISH.ledger(self.state) as db:
            snap = FINISH.revalidate(FINISH.get_row(db, self.wt))
        with self.assertRaisesRegex(FINISH.Retain, "git-failed"):
            FINISH.safety().admin_inventory(
                snap, FINISH.git, time.monotonic() + 10, preserved_oids={recovery})
        self.assertEqual((self.admin / "COMMIT_EDITMSG").read_bytes(), draft)

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
        self.assert_release_blocked("commit-message-recovery")
        self.assertTrue(message.read_bytes().endswith(b"substantive uncommitted draft\n"))
        message.write_bytes(original)
        git(self.repo, "config", "commit.cleanup", "scissors")
        self.call("resume")
        self.assert_release_blocked("cleanup-mode-unqualified")
        self.assertTrue(self.wt.exists())

    @unittest.skipUnless(sys.platform == "darwin", "native no-follow xattr fixture")
    def test_native_unknown_metadata_retains_checkout_and_admin_objects(self):
        for path in (self.wt, self.wt / "file", self.admin, self.admin / "index"):
            with self.subTest(kind=path.name):
                subprocess.run(["/usr/bin/xattr", "-w", "com.example.finish-fixture", "synthetic", str(path)], check=True)
                try:
                    self.call("resume")
                    self.assert_release_blocked("extended-attributes")
                    self.assertTrue(path.exists())
                    self.assertTrue((self.admin / "locked").exists())
                finally:
                    subprocess.run(["/usr/bin/xattr", "-d", "com.example.finish-fixture", str(path)], check=True)
        self.assertTrue(self.wt.exists())


class ForegroundCompletionTests(unittest.TestCase):
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    finish = LifecycleTests.finish
    def setUp(self):
        ReleaseTests.setUp(self)
        FINISH.qualified_policy.side_effect = FINISH.Retain("foreground-must-not-need-automatic-policy")

    tearDown = ReleaseTests.tearDown

    @unittest.skipUnless(shutil.which("lsof"), "native lsof unavailable")
    def test_plain_finish_removes_merged_checkout_without_activation(self):
        result = self.finish()[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.admin.exists())
        self.assertNotIn(str(self.wt), git(self.repo, "worktree", "list", "--porcelain"))
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertTrue(self.legacy.exists())
        FINISH.qualified_policy.assert_not_called()

    @unittest.skipUnless(shutil.which("lsof"), "native lsof unavailable")
    def test_fork_finish_removes_only_after_fresh_merged_proof(self):
        self.url = "https://github.com/upstream/repo/pull/1"
        self.proofs[self.url] = self.proof(self.url, repo="upstream/repo", head_repo="example/repo")
        result = self.finish()[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.admin.exists())
        self.assertTrue(self.legacy.exists())
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        FINISH.qualified_policy.assert_not_called()

    def test_fork_proof_drift_before_native_removal_retains_checkout(self):
        self.url = "https://github.com/upstream/repo/pull/1"
        self.proofs[self.url] = self.proof(self.url, repo="upstream/repo", head_repo="example/repo")
        def drift(*args, **kwargs):
            self.proofs[self.url]["head_repo"] = None
        with mock.patch.object(FINISH, "manual_holders", side_effect=drift):
            result = self.finish()[1][0]
        self.assertEqual(result["reason"], "primary-pr-proof-changed", result)
        self.assertTrue(self.wt.exists())
        self.assertTrue(self.admin.exists())
        self.assertTrue((self.admin / "locked").exists())

    def test_pending_merge_records_signoff_even_with_ignored_recovery_content(self):
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        (self.wt / "ignored").write_text("keep this recovery content\n")
        result = self.finish()[1][0]
        self.assertEqual(result["reason"], "pr-not-merged")
        self.assertEqual(result["released_owners"], ["owner-1"])
        self.assertEqual(result["automatic_handoff"]["baseline"], "unavailable")
        self.assertEqual(result["automatic_handoff"]["blocker"], "ignored-roots-require-explicit-disposal")
        FINISH.qualified_policy.side_effect = None
        self.proofs[self.url].update(merged=True, state="closed", merge="a" * 40)
        self.assertEqual(self.call("check", "--apply")[1][0]["reason"], "ignored-roots-require-explicit-disposal")
        self.assertEqual((self.wt / "ignored").read_text(), "keep this recovery content\n")

    def pending_fetch_recovery(self):
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        tree = git(self.repo, "rev-parse", "HEAD^{tree}")
        recovery = git(self.repo, "commit-tree", tree, "-p", self.head, "-m", "recovery")
        git(self.repo, "update-ref", "refs/heads/released-feature", recovery, self.head)
        git(self.repo, "update-ref", "refs/heads/released-feature", self.head, recovery)
        git(self.wt, "fetch", "--no-auto-maintenance", "--no-tags", str(self.repo), recovery)
        result = self.finish()[1][0]
        self.assertEqual(result["reason"], "pr-not-merged")
        self.assertEqual(result["released_owners"], ["owner-1"])
        return recovery

    def test_pending_finish_hands_native_index_refresh_and_fetch_recovery_to_same_consumer(self):
        recovery = self.pending_fetch_recovery()
        index = (self.admin / "index").read_bytes()
        os.utime(self.wt / "file", (time.time() + 5, time.time() + 5))
        mask = os.umask(0o077)
        try:
            git(self.wt, "status", "--porcelain")
        finally:
            os.umask(mask)
        self.assertNotEqual((self.admin / "index").read_bytes(), index)
        git(self.wt, "fetch", "--no-auto-maintenance", "--no-tags", str(self.repo), self.head)
        self.proofs[self.url].update(merged=True, state="closed", merge="a" * 40)
        FINISH.qualified_policy.side_effect = None
        result = self.call("check", "--apply")[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertEqual(git(self.repo, "cat-file", "-t", recovery), "commit")

    def test_overwritten_fetch_head_cannot_erase_release_recovery_obligation(self):
        recovery = self.pending_fetch_recovery()
        git(self.wt, "fetch", "--no-auto-maintenance", "--no-tags", str(self.repo), self.head)
        git(self.repo, "reflog", "expire", "--expire=now", "refs/heads/released-feature")
        self.assertEqual(git(self.repo, "rev-list", recovery, "--not", "--all"), recovery)
        self.proofs[self.url].update(merged=True, state="closed", merge="a" * 40)
        FINISH.qualified_policy.side_effect = None
        result = self.call("check", "--apply")[1][0]
        self.assertEqual(result["reason"], "admin-recovery-not-preserved", result)
        self.assertTrue(self.wt.exists())

    def test_ignored_disposal_is_frozen_at_release_and_worker_cannot_approve_new_content(self):
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        (self.wt / "ignored").mkdir()
        (self.wt / "ignored/proof").write_text("completed task proof\n")
        result = self.finish("--discard-ignored", "ignored")[1][0]
        self.assertEqual(result["automatic_handoff"]["baseline"], "ready")
        (self.wt / "ignored/new").write_text("new recovery data\n")
        self.proofs[self.url].update(merged=True, state="closed", merge="a" * 40)
        FINISH.qualified_policy.side_effect = None
        with self.assertRaisesRegex(FINISH.Retain, "requires-owner"):
            self.call("check", "--apply", "--discard-ignored", "ignored")
        self.assertEqual(self.call("check", "--apply")[1][0]["reason"], "released-worktree-changed")
        self.assertTrue((self.wt / "ignored/new").exists())

    def test_same_consumer_removes_unchanged_owner_discarded_content(self):
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        (self.wt / "ignored").write_text("completed proof\n")
        self.finish("--discard-ignored", "ignored")
        self.proofs[self.url].update(merged=True, state="closed", merge="a" * 40)
        FINISH.qualified_policy.side_effect = None
        result = self.call("check", "--apply")[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertTrue(self.legacy.exists())

    def pending_target_fetch(self):
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        target = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", self.head, "-m", "target advances")
        git(self.repo, "update-ref", "refs/heads/main", target, self.head)
        git(self.wt, "fetch", "--no-auto-maintenance", "--no-tags", str(self.repo),
            "refs/heads/main:refs/remotes/origin/main")
        result = self.finish()[1][0]
        self.assertEqual(result["reason"], "pr-not-merged", result)
        self.proofs[self.url].update(merged=True, state="closed", merge="a" * 40)
        FINISH.qualified_policy.side_effect = None
        return target

    def test_pending_finish_preserves_fetched_target_ahead_of_task(self):
        target = self.pending_target_fetch()
        result = self.call("check", "--apply")[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertEqual(git(self.repo, "rev-parse", "refs/remotes/origin/main"), target)

    def test_foreground_release_preserves_fetched_target_ahead_of_task(self):
        target = self.pending_target_fetch()
        with mock.patch.object(FINISH, "manual_holders"):
            result = self.call("release")[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.admin.exists())
        self.assertEqual(git(self.repo, "rev-parse", "refs/remotes/origin/main"), target)
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)

    def test_target_ref_drift_between_admissions_holds_before_native_unlock(self):
        target = self.pending_target_fetch()
        successor = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", target, "-m", "next target")
        admission, calls = FINISH.deletion_admission, []
        def advance(*args):
            calls.append(None)
            if len(calls) == 2:
                git(self.repo, "update-ref", "refs/remotes/origin/main", successor, target)
            return admission(*args)
        with mock.patch.object(FINISH, "deletion_admission", side_effect=advance), \
                mock.patch.object(FINISH.safety(), "supervise", wraps=FINISH.safety().supervise) as children:
            result = self.call("check", "--apply")[1][0]
        self.assertEqual(result["checkout"], "retained")
        self.assertEqual(result["retirement_state"], "enrolled")
        self.assertEqual(result["reason"], "removal-preimage-changed", result)
        self.assertFalse(any("worktree" in call.args[0] and ("unlock" in call.args[0] or "remove" in call.args[0])
                             for call in children.call_args_list))
        self.assertTrue(self.wt.exists())

    def test_completion_rechecks_owner_signoff_before_removal(self):
        self.call("resume", "--owner", "owner-2")
        self.assertEqual(self.finish()[1][0]["reason"], "awaiting-owner-release")
        admission = FINISH.finalized_admission
        calls = []

        def revoke(db, row, args, until, **options):
            result = admission(db, row, args, until, **options)
            calls.append(result)
            if len(calls) == 1:
                with db:
                    db.execute("DELETE FROM owner_releases WHERE worktree_id=? AND owner=?",
                               (row["id"], "owner-1"))
            return result

        with mock.patch.object(FINISH, "manual_holders"), \
                mock.patch.object(FINISH, "finalized_admission", side_effect=revoke):
            result = self.finish("--owner", "owner-2")[1][0]
        self.assertEqual(result["checkout"], "retained", result)
        self.assertEqual(result["reason"], "awaiting-owner-release")
        self.assertTrue(self.wt.exists())
        self.assertTrue((self.admin / "locked").exists())

    def test_foreground_dependency_link_preserves_external_donor(self):
        (self.repo / ".git/info/exclude").write_text("node_modules\n")
        donor = self.base / "dependency-donor"
        donor.mkdir()
        (donor / "keep").write_text("shared dependency\n")
        (self.wt / "node_modules").symlink_to(donor, target_is_directory=True)
        with mock.patch.object(FINISH, "manual_holders"):
            result = self.finish()[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertEqual((donor / "keep").read_text(), "shared dependency\n")


class MissingRecoveryTests(unittest.TestCase):
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    finish = LifecycleTests.finish
    tearDown = ReleaseTests.tearDown

    def setUp(self):
        ReleaseTests.setUp(self)
        (self.wt / "nested").mkdir()
        (self.wt / "nested/command").write_bytes(b"#!/bin/sh\nexit 0\n")
        (self.wt / "nested/command").chmod(0o755)
        self.external = self.base / "external"
        self.external.write_text("untouched\n")
        (self.wt / "external-link").symlink_to(self.external)
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-m", "recovery fixture")
        self.head = git(self.wt, "rev-parse", "HEAD")
        self.original = ReleaseTests.row(self)
        self.before_index = (self.admin / "index").read_bytes()
        self.before_lock = (self.admin / "locked").read_bytes()
        self.index_sha = hashlib.sha256(self.before_index).hexdigest()
        shutil.rmtree(self.wt)
        self.holders = mock.patch.object(FINISH, "manual_holders")
        self.holders_mock = self.holders.start()
        self.addCleanup(self.holders.stop)

    def recover(self, *args):
        return self.call("recover-missing", "--worktree-id", self.original["id"],
                         "--generation", str(getattr(self, "generation", 0)), "--expected-head", self.head,
                         "--index-sha256", self.index_sha, *args)[1][0]

    def plan(self):
        return self.recover()["recovery_sha256"]

    def disappear_again(self):
        self.head = git(self.wt, "rev-parse", "HEAD")
        self.index_sha = hashlib.sha256((self.admin / "index").read_bytes()).hexdigest()
        self.generation = self.record()[1]["generation"]
        shutil.rmtree(self.wt)

    def repeated_tree(self, directories):
        blob = git(self.repo, "rev-parse", "HEAD:file")
        def tree(records):
            result = subprocess.run(["git", "-C", str(self.repo), "mktree", "-z"],
                                    input=b"".join(records), stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, check=True)
            return result.stdout.decode().strip()
        leaf = tree(f"100644 blob {blob}\t{i:04d}-{'x' * 180}\0".encode() for i in range(1000))
        return tree(f"040000 tree {leaf}\tdirectory-{i:04d}\0".encode() for i in range(directories))

    def test_recovery_uses_tree_capture_budget_with_no_replace_prefix(self):
        tree = self.repeated_tree(8)
        self.head = git(self.repo, "commit-tree", tree, "-p", self.head, "-m", "recovery fixture")
        git(self.repo, "update-ref", "refs/heads/released-feature", self.head)
        git(self.repo, "--git-dir", str(self.admin), "read-tree", tree)
        self.index_sha = hashlib.sha256((self.admin / "index").read_bytes()).hexdigest()
        with self.assertRaisesRegex(FINISH.Retain, "git-failed"):
            FINISH.git(self.repo, "--no-replace-objects", "ls-tree", "-rz", self.head)
        plan = self.recover()
        self.assertEqual(plan["entries"], 8000)
        self.assertFalse(self.wt.exists())
        self.assertIsNone(self.record()[0])

    def test_large_index_plan_uses_index_budget_and_preserves_admin_aggregate(self):
        tree = self.repeated_tree(40)
        self.head = git(self.repo, "commit-tree", tree, "-p", self.head, "-m", "recovery fixture")
        git(self.repo, "update-ref", "refs/heads/released-feature", self.head)
        git(self.repo, "--git-dir", str(self.admin), "read-tree", tree)
        raw = (self.admin / "index").read_bytes()
        self.assertGreater(len(raw), FINISH.safety().LEAF_LIMIT)
        self.index_sha = hashlib.sha256(raw).hexdigest()
        plan = self.recover()
        self.assertEqual(plan["entries"], 40000)
        self.assertEqual((self.admin / "index").read_bytes(), raw)
        self.assertIsNone(self.record()[0])
        with mock.patch.object(FINISH.safety(), "PASS_LIMIT", len(raw) - 1):
            with self.assertRaisesRegex(FINISH.Retain, "file-type-or-size"):
                self.recover()

    def test_scoped_index_budget_covers_apply_and_inventory_only(self):
        # A small budget models the same boundary without materializing 40k files.
        module = FINISH.safety()
        tree = self.repeated_tree(1)
        self.head = git(self.repo, "commit-tree", tree, "-p", self.head, "-m", "recovery fixture")
        git(self.repo, "update-ref", "refs/heads/released-feature", self.head)
        git(self.repo, "--git-dir", str(self.admin), "read-tree", tree)
        raw = (self.admin / "index").read_bytes()
        self.index_sha = hashlib.sha256(raw).hexdigest()
        self.assertGreater(len(raw), 65536)
        with mock.patch.object(module, "LEAF_LIMIT", 65536), \
                mock.patch.object(module.read_file, "__defaults__", (65536,)):
            result = self.recover("--apply", "--recovery-sha256", self.plan())
            self.assertEqual(result["checkout"], "restored")
            snap = FINISH.snapshot(str(self.wt), str(self.root), self.before_lock.decode().strip())
            module.inventory(snap, FINISH.git, module.DEADLINE)
            (self.admin / "COMMIT_EDITMSG").write_bytes(b"x" * 65537)
            with self.assertRaisesRegex(FINISH.Retain, "file-type-or-size"):
                module.recovery_admin_inventory(snap, FINISH.git, module.DEADLINE)

    def test_tree_capture_still_stops_above_32_mib(self):
        tree = self.repeated_tree(160)
        native = FINISH.safety().supervise
        observed = []
        def bounded(*args, **kwargs):
            result = native(*args, **kwargs)
            observed.append(result)
            return result
        with mock.patch.object(FINISH.safety(), "supervise", side_effect=bounded):
            with self.assertRaisesRegex(FINISH.Retain, "git-failed"):
                FINISH.git(self.repo, "--no-replace-objects", "ls-tree", "-rz", tree,
                           capture_limit=32 * 1024 * 1024)
        self.assertGreater(len(observed[0].stdout), 32 * 1024 * 1024)
        self.assertLessEqual(len(observed[0].stdout), 32 * 1024 * 1024 + 65536)
        self.assertTrue(observed[0].proof["reaped"])
        self.assertTrue(observed[0].proof["group_absent"])

    def test_successive_recovery_preserves_exact_completed_receipts(self):
        first = self.recover("--apply", "--recovery-sha256", self.plan())
        with FINISH.ledger(self.state) as db:
            original = db.execute("SELECT fact FROM missing_recoveries WHERE worktree_id=?",
                                  (self.original["id"],)).fetchone()[0]
            # Custody covers original serialized bytes, not a canonical rewrite.
            original += "\n"
            with db:
                db.execute("UPDATE missing_recoveries SET fact=? WHERE worktree_id=?",
                           (original, self.original["id"]))
        self.call("resume")
        (self.wt / "file").write_text("next committed source\n")
        git(self.wt, "commit", "-am", "next source")
        self.disappear_again()
        second = self.recover("--apply", "--recovery-sha256", self.plan())
        self.assertNotEqual(first["intent_id"], second["intent_id"])
        self.assertEqual((self.wt / "file").read_text(), "next committed source\n")
        with FINISH.ledger(self.state) as db:
            archived = db.execute(
                "SELECT fact FROM missing_recovery_history WHERE worktree_id=? AND intent_id=?",
                (self.original["id"], first["intent_id"])).fetchone()[0]
            self.assertEqual(archived, original)
            second_fact = db.execute("SELECT fact FROM missing_recoveries WHERE worktree_id=?",
                                     (self.original["id"],)).fetchone()[0]
        self.call("resume")
        self.disappear_again()
        third_digest = self.plan()
        self.assertEqual(len(self.history()), 1)
        third = self.recover("--apply", "--recovery-sha256", third_digest)
        self.assertEqual(self.history(), {first["intent_id"]: original, second["intent_id"]: second_fact})
        self.assertNotIn(third["intent_id"], self.history())
        with FINISH.ledger(self.state) as db:
            for sql in ("UPDATE missing_recovery_history SET fact='changed'",
                        "DELETE FROM missing_recovery_history"):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                    db.execute(sql)
        with self.assertRaisesRegex(FINISH.Retain, "exact-intent-required"):
            self.recover("--apply", "--recovery-sha256", first["recovery_sha256"],
                         "--intent-id", first["intent_id"])

    def mixed_owner_missing(self, blocker=False, **proof):
        self.recover("--apply", "--recovery-sha256", self.plan())
        with mock.patch.dict(os.environ, {"GWT_OWNER_ID": "owner-2"}):
            self.call("resume")
        if blocker:
            (self.admin / "REBASE_HEAD").write_text(self.head + "\n")
        self.proofs[self.url] = self.proof(self.url, **proof)
        finished = self.finish()[1][0]
        self.assertEqual(finished["released_owners"], ["owner-1"])
        inventory = self.record()[1]["inventory"]
        self.disappear_again()
        return inventory

    def test_mixed_owner_release_is_evidence_not_recovery_authority(self):
        inventory = self.mixed_owner_missing(blocker=True)
        result = self.recover("--apply", "--recovery-sha256", self.plan())
        self.assertEqual(result["checkout"], "restored")
        record = self.record()[0]
        self.assertEqual(record["preimage"]["completion_proof"]["inventory"], inventory)
        self.assertEqual(json.loads(inventory)["blocker"], "admin-recovery-content-unknown")
        self.assertEqual(record["preimage"]["releases"],
                         [{"owner": "owner-1", "generation": self.generation}])
        with FINISH.ledger(self.state) as db:
            self.assertEqual(FINISH.owner_rows(db, self.original["id"]),
                             [{"owner": "owner-1", "completed": 1},
                              {"owner": "owner-2", "completed": 0}])
            self.assertFalse(db.execute("SELECT 1 FROM owner_releases WHERE worktree_id=?",
                                        (self.original["id"],)).fetchone())
            self.assertIsNone(FINISH.retirement(db, self.original)["inventory"])
        self.assertEqual(self.recover("--apply", "--recovery-sha256", result["recovery_sha256"],
                                      "--intent-id", result["intent_id"]), result)

    def test_same_repository_release_recovery_stays_offline(self):
        self.mixed_owner_missing()
        with mock.patch.object(FINISH, "pr", side_effect=AssertionError("same-repository recovery is offline")):
            result = self.recover("--apply", "--recovery-sha256", self.plan())
        self.assertEqual(result["checkout"], "restored")

    def test_fork_release_recovery_preserves_history_and_invalidates_releases(self):
        self.url = "https://github.com/upstream/repo/pull/1"
        inventory = self.mixed_owner_missing(
            repo="upstream/repo", head_repo="example/repo", merged=False, state="open", merge=None)
        with FINISH.ledger(self.state) as db:
            previous = db.execute("SELECT fact FROM missing_recoveries WHERE worktree_id=?",
                                  (self.original["id"],)).fetchone()[0]
        result = self.recover("--apply", "--recovery-sha256", self.plan())
        self.assertEqual(result["checkout"], "restored")
        self.assertEqual(result["generation"], self.generation + 1)
        self.assertEqual(self.history(), {json.loads(previous)["id"]: previous})
        self.assertEqual(self.record()[0]["preimage"]["completion_proof"]["inventory"], inventory)
        with FINISH.ledger(self.state) as db:
            self.assertEqual(FINISH.owner_rows(db, self.original["id"]),
                             [{"owner": "owner-1", "completed": 1}, {"owner": "owner-2", "completed": 0}])
            self.assertEqual(list(db.execute("SELECT * FROM owner_releases")), [])
            self.assertIsNone(FINISH.retirement(db, self.original)["inventory"])
        with mock.patch.object(FINISH, "pr", side_effect=AssertionError("replay grants no release authority")):
            self.assertEqual(self.recover("--apply", "--recovery-sha256", result["recovery_sha256"],
                                          "--intent-id", result["intent_id"]), result)

    def test_fork_release_recovery_refuses_changed_or_unavailable_proof(self):
        self.url = "https://github.com/upstream/repo/pull/1"
        self.mixed_owner_missing(repo="upstream/repo", head_repo="example/repo")
        digest = self.plan()
        previous = self.record()
        valid = dict(self.proofs[self.url])
        for changes in ({"head_repo": None}, {"head_repo": "foreign/repo"},
                        {"head": "b" * 40}, {"target": "other"}):
            with self.subTest(changes=changes):
                self.proofs[self.url] = {**valid, **changes}
                with self.assertRaisesRegex(FINISH.Retain, "primary-pr-proof-changed"):
                    self.recover("--apply", "--recovery-sha256", digest)
                self.assertEqual(self.record(), previous)
                self.assertFalse(self.wt.exists())
        with mock.patch.object(FINISH, "pr", side_effect=FINISH.Retain("ghx-failed")):
            with self.assertRaisesRegex(FINISH.Retain, "ghx-failed"):
                self.recover()
        self.assertEqual(self.record(), previous)

    def test_fork_release_recovery_validates_shape_before_live_proof(self):
        self.url = "https://github.com/upstream/repo/pull/1"
        inventory = self.mixed_owner_missing(repo="upstream/repo", head_repo="example/repo")
        with FINISH.ledger(self.state) as db, db:
            db.execute("UPDATE worktrees SET dependencies='[{}]' WHERE id=?", (self.original["id"],))
            self.generation = FINISH.retirement(db, self.original)["generation"]
            db.execute("INSERT INTO owner_releases VALUES (?, ?, ?)",
                       (self.original["id"], "owner-1", self.generation))
            db.execute("UPDATE retirement SET inventory=? WHERE worktree_id=?",
                       (inventory, self.original["id"]))
        with mock.patch.object(FINISH, "pr", side_effect=AssertionError("shape must be checked first")):
            with self.assertRaisesRegex(FINISH.Retain, "recovery-release-proof-invalid"):
                self.recover()
        self.assertFalse(self.wt.exists())

    def test_fork_release_proof_drift_during_materialization_retains_stage(self):
        self.url = "https://github.com/upstream/repo/pull/1"
        self.mixed_owner_missing(repo="upstream/repo", head_repo="example/repo")
        digest = self.plan()
        materialize = FINISH.recovery_tree
        def drift(*args, **kwargs):
            result = materialize(*args, **kwargs)
            if kwargs.get("write"):
                self.proofs[self.url]["head_repo"] = "foreign/repo"
            return result
        with mock.patch.object(FINISH, "recovery_tree", side_effect=drift):
            with self.assertRaisesRegex(FINISH.Retain, "primary-pr-proof-changed"):
                self.recover("--apply", "--recovery-sha256", digest)
        record, retirement = self.record()
        self.assertFalse(self.wt.exists())
        self.assertTrue(Path(record["stage"]).exists())
        self.assertIsNone(record["result"])
        self.assertEqual(retirement["state"], "recovery-pending")
        self.assertEqual(retirement["generation"], self.generation)

    def test_retained_rebase_head_outside_branch_is_preserved(self):
        oid = git(self.repo, "commit-tree", "HEAD^{tree}", "-m", "retained rebase commit")
        path = self.admin / "REBASE_HEAD"
        path.write_text(oid + "\n")
        before = FINISH.safety().read_file(path, time.monotonic() + 10)
        result = self.recover("--apply", "--recovery-sha256", self.plan())
        self.assertEqual(result["checkout"], "restored")
        self.assertEqual(FINISH.safety().read_file(path, time.monotonic() + 10), before)
        self.assertEqual(self.record()[0]["preimage"]["admin"]["REBASE_HEAD"][2], [oid])

    def history(self):
        with FINISH.ledger(self.state) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='missing_recovery_history'").fetchone():
                return {}
            return dict(db.execute("SELECT intent_id, fact FROM missing_recovery_history WHERE worktree_id=?",
                                   (self.original["id"],)))

    def second_missing(self):
        self.recover("--apply", "--recovery-sha256", self.plan())
        self.call("resume")
        self.disappear_again()

    def legacy_receipt(self):
        record, _ = self.record()
        record.pop("contract")
        for key in ("recovery_contract", "recovery_history", "releases", "completion_proof", "resolve_undo"):
            record["preimage"].pop(key)
        record["digest"] = FINISH.recovery_digest(record["preimage"])
        record["intent"]["digest"] = record["digest"]
        if record["result"]:
            record["result"]["recovery_sha256"] = record["digest"]
        with FINISH.ledger(self.state) as db, db:
            db.execute("UPDATE missing_recoveries SET fact=? WHERE worktree_id=?",
                       (json.dumps(record, sort_keys=True), self.original["id"]))
            if not record["result"]:
                db.execute("UPDATE retirement SET intent=? WHERE worktree_id=?",
                           (json.dumps(record["intent"], sort_keys=True), self.original["id"]))
        return record

    def test_legacy_completed_receipt_replays_without_digest_change(self):
        self.recover("--apply", "--recovery-sha256", self.plan())
        old = self.legacy_receipt()
        self.assertEqual(self.recover("--apply", "--intent-id", old["id"],
                                      "--recovery-sha256", old["digest"]), old["result"])
        self.assertNotIn("contract", self.record()[0])

    def test_inconsistent_completed_receipt_cannot_authorize_successor(self):
        self.second_missing()
        with FINISH.ledger(self.state) as db:
            original = FINISH.missing_recovery_fact(db, self.original)
        changes = [
            ("result", {"checkout": "removed"}),
            ("result.checkout", "unknown"),
            ("result.intent_id", str(uuid.uuid4())),
            ("result.recovery_sha256", "0" * 64),
            ("result.head", "0" * 40),
            ("result.generation", self.generation + 1),
            ("stage_id", [0, 0]),
        ]
        for key, value in changes:
            with self.subTest(key=key):
                altered = json.loads(original)
                target = altered
                parts = key.split(".")
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = value
                raw = json.dumps(altered)
                with FINISH.ledger(self.state) as db, db:
                    db.execute("UPDATE missing_recoveries SET fact=? WHERE worktree_id=?",
                               (raw, self.original["id"]))
                with self.assertRaisesRegex(FINISH.Retain, "completed-receipt-invalid"):
                    self.recover()
                with FINISH.ledger(self.state) as db:
                    self.assertEqual(FINISH.missing_recovery_fact(db, self.original), raw)
                self.assertEqual(self.history(), {})
                self.assertFalse(self.wt.exists())

    def test_legacy_pending_receipt_replays_original_contract(self):
        digest = self.plan()
        with mock.patch.object(FINISH, "recovery_publish", side_effect=RuntimeError("crash")):
            with self.assertRaisesRegex(RuntimeError, "crash"):
                self.recover("--apply", "--recovery-sha256", digest)
        old = self.legacy_receipt()
        result = self.recover("--apply", "--intent-id", old["id"], "--recovery-sha256", old["digest"])
        self.assertEqual(result["recovery_sha256"], old["digest"])
        self.assertNotIn("contract", self.record()[0])

    def test_legacy_pending_receipt_still_rejects_releases(self):
        digest = self.plan()
        with mock.patch.object(FINISH, "recovery_publish", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.recover("--apply", "--recovery-sha256", digest)
        old = self.legacy_receipt()
        with FINISH.ledger(self.state) as db, db:
            db.execute("INSERT INTO owner_releases VALUES (?, ?, ?)", (self.original["id"], "owner-1", 0))
        with self.assertRaisesRegex(FINISH.Retain, "recovery-release-present"):
            self.recover("--apply", "--intent-id", old["id"], "--recovery-sha256", old["digest"])

    def test_real_conflict_rebase_recovers_with_unchanged_resolve_undo(self):
        self.recover("--apply", "--recovery-sha256", self.plan())
        self.call("resume")
        (self.wt / "file").write_text("feature\n")
        git(self.wt, "commit", "-am", "feature")
        old_head = git(self.wt, "rev-parse", "HEAD")
        (self.repo / "file").write_text("main\n")
        git(self.repo, "commit", "-am", "main")
        with self.assertRaises(subprocess.CalledProcessError):
            git(self.wt, "rebase", "main")
        (self.wt / "file").write_text("resolved\n")
        git(self.wt, "add", "file")
        with mock.patch.dict(os.environ, {"GIT_EDITOR": "true"}):
            git(self.wt, "rebase", "--continue")
        self.assertEqual(git(self.wt, "status", "--porcelain"), "")
        self.assertEqual((self.admin / "REBASE_HEAD").read_text(), old_head + "\n")
        self.assertTrue(git(self.wt, "rev-list", old_head, "--not", "HEAD"))
        self.assertFalse(any((self.admin / name).exists() for name in ("rebase-merge", "rebase-apply", "sequencer")))
        index = (self.admin / "index").read_bytes()
        _, undo = FINISH.safety().index_entries(index, allow_resolve_undo=True)
        self.assertTrue(undo)
        before = self.record()[0]
        index_before = FINISH.safety().read_file(self.admin / "index", time.monotonic() + 10)
        admin_id = FINISH.identity(self.admin)
        lock = (self.admin / "locked").read_bytes()
        self.disappear_again()
        result = self.recover("--apply", "--recovery-sha256", self.plan())
        self.assertEqual(result["checkout"], "restored")
        self.assertEqual((self.wt / "file").read_text(), "resolved\n")
        self.assertEqual(FINISH.safety().read_file(self.admin / "index", time.monotonic() + 10), index_before)
        self.assertEqual(FINISH.identity(self.admin), admin_id)
        self.assertEqual((self.admin / "locked").read_bytes(), lock)
        self.assertEqual((self.admin / "REBASE_HEAD").read_text(), old_head + "\n")
        self.assertEqual(self.record()[0]["preimage"]["resolve_undo"], undo)
        self.assertEqual(json.loads(self.history()[before["id"]]), before)
        with FINISH.ledger(self.state) as db:
            self.assertEqual(FINISH.owner_rows(db, self.original["id"]), [{"owner": "owner-1", "completed": 0}])
        self.assertEqual(self.recover("--apply", "--intent-id", result["intent_id"],
                                      "--recovery-sha256", result["recovery_sha256"]), result)

    def undo_record(self, *, oid=None, mode=0o100644, stage=1, name=b"file"):
        oid = oid or git(self.repo, "rev-parse", "HEAD:file")
        return (name + b"\0" + b"".join((format(mode, "o").encode() if i == stage else b"0") + b"\0"
                                        for i in (1, 2, 3)) + bytes.fromhex(oid))

    def write_undo(self, *payloads, body=None):
        body = self.before_index[:-20] if body is None else body
        for payload in payloads:
            body += b"REUC" + struct.pack(">I", len(payload)) + payload
        data = body + hashlib.sha1(body).digest()
        (self.admin / "index").write_bytes(data)
        self.index_sha = hashlib.sha256(data).hexdigest()
        return data

    def test_retained_unanchored_executable_and_missing_stages_do_not_authorize_deletion(self):
        blob = FINISH.git(self.repo, "hash-object", "-w", "--stdin", input_data=b"retained undo only\n")
        payload = self.undo_record(oid=blob, mode=0o100755, stage=2, name=b"undo-only")
        self.write_undo(payload)
        before = FINISH.safety().read_file(self.admin / "index", time.monotonic() + 10)
        expected = {"undo-only": [[2, 0o100755, blob]]}
        self.recover("--apply", "--recovery-sha256", self.plan())
        self.assertEqual(self.record()[0]["preimage"]["resolve_undo"], expected)
        self.assertEqual(FINISH.safety().read_file(self.admin / "index", time.monotonic() + 10), before)
        self.assertFalse((self.wt / "undo-only").exists())
        self.assertEqual((self.wt / "file").read_bytes(), b"original\n")
        with self.assertRaisesRegex(FINISH.Retain, "resolve-undo-recovery-required"):
            FINISH.safety().preserved_resolve_undo({"path": str(self.wt)}, FINISH.git,
                                                 time.monotonic() + 10, expected, {self.head})

    def test_retained_undo_wrong_missing_and_replaced_objects_refuse(self):
        blob = git(self.repo, "rev-parse", "HEAD:file")
        for oid in (self.head, git(self.repo, "rev-parse", "HEAD^{tree}"), "f" * 40):
            with self.subTest(oid=oid):
                data = self.write_undo(self.undo_record(oid=oid))
                with self.assertRaisesRegex(FINISH.Retain, "resolve-undo-object-type-or-missing"):
                    self.recover()
                self.assertEqual((self.admin / "index").read_bytes(), data)
                self.assertIsNone(self.record()[0])
        git(self.repo, "update-ref", "refs/replace/" + self.head, blob)
        self.assertEqual(git(self.repo, "cat-file", "-t", self.head), "blob")
        self.write_undo(self.undo_record(oid=self.head))
        with self.assertRaisesRegex(FINISH.Retain, "resolve-undo-object-type-or-missing"):
            self.recover()

    def test_retained_undo_malformed_duplicate_and_symlink_stages_refuse(self):
        valid = self.undo_record()
        for payloads, reason in (
                ((valid[:-1],), "resolve-undo-invalid"),
                ((valid + valid,), "resolve-undo-invalid"),
                ((valid, valid), "resolve-undo-duplicate-extension"),
                ((self.undo_record(mode=0o120000),), "retained-mode-unsupported")):
            with self.subTest(payloads=payloads):
                data = self.write_undo(*payloads)
                with self.assertRaisesRegex(FINISH.Retain, reason):
                    self.recover()
                self.assertEqual((self.admin / "index").read_bytes(), data)
                self.assertIsNone(self.record()[0])

    def test_retained_undo_does_not_admit_unresolved_or_sparse_stage_zero(self):
        body = bytearray(self.before_index[:-20])
        flags = struct.unpack_from(">H", body, 12 + 60)[0]
        struct.pack_into(">H", body, 12 + 60, flags | 0x1000)
        self.write_undo(self.undo_record(), body=bytes(body))
        with self.assertRaisesRegex(FINISH.Retain, "raw-index-flags-or-mode-unsupported"):
            self.recover()
        (self.admin / "index").write_bytes(self.before_index)
        git(self.repo, "--git-dir", str(self.admin), "update-index", "--skip-worktree", "file")
        self.write_undo(self.undo_record(), body=(self.admin / "index").read_bytes()[:-20])
        with self.assertRaisesRegex(FINISH.Retain, "recovery-sparse-index"):
            self.recover()
        self.assertIsNone(self.record()[0])

    def test_legacy_pending_receipt_rejects_resolve_undo(self):
        digest = self.plan()
        with mock.patch.object(FINISH, "recovery_publish", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.recover("--apply", "--recovery-sha256", digest)
        old = self.legacy_receipt()
        data = self.write_undo(self.undo_record())
        with self.assertRaisesRegex(FINISH.Retain, "recovery-exact-intent-required"):
            self.recover("--apply", "--intent-id", old["id"], "--recovery-sha256", old["digest"])
        # Even with the new index operand, the original receipt's parser must
        # refuse REUC rather than silently changing its approved digest shape.
        args = types.SimpleNamespace(worktree_id=self.original["id"], generation=0,
                                     expected_head=self.head, index_sha256=self.index_sha,
                                     allow_device_renumbering=False)
        with FINISH.ledger(self.state) as db:
            with self.assertRaisesRegex(FINISH.Retain, "raw-index-extension-unsupported"):
                FINISH.missing_recovery_preimage(db, FINISH.get_row(db, self.wt), args,
                                                 time.monotonic() + 10, legacy=True)
        self.assertEqual(self.record()[0], old)
        self.assertEqual((self.admin / "index").read_bytes(), data)

    def test_retained_undo_index_byte_and_inode_drift_refuse_before_intent(self):
        self.write_undo(self.undo_record())
        path = self.admin / "index"
        digest = self.plan()
        moved = self.base / "original-index"
        path.rename(moved)
        path.write_bytes(moved.read_bytes())
        with self.assertRaisesRegex(FINISH.Retain, "reviewed-preimage-required"):
            self.recover("--apply", "--recovery-sha256", digest)
        digest = self.plan()
        self.write_undo(self.undo_record(stage=3))
        with self.assertRaisesRegex(FINISH.Retain, "reviewed-preimage-required"):
            self.recover("--apply", "--recovery-sha256", digest)
        path.unlink()
        path.symlink_to(moved)
        with self.assertRaises(FINISH.Retain):
            self.recover()
        self.assertIsNone(self.record()[0])
        self.assertFalse(self.wt.exists())

    def test_retained_undo_index_rebound_between_parse_and_inventory_refuses(self):
        self.write_undo(self.undo_record())
        validate = FINISH.safety().retained_resolve_undo
        def rebound(*args):
            validate(*args)
            path = self.admin / "index"
            path.rename(self.base / "old-index")
            path.write_bytes((self.base / "old-index").read_bytes())
        with mock.patch.object(FINISH.safety(), "retained_resolve_undo", side_effect=rebound):
            with self.assertRaisesRegex(FINISH.Retain, "recovery-index-changed"):
                self.recover()
        self.assertIsNone(self.record()[0])

    def test_retained_undo_index_drift_before_publication_refuses(self):
        self.write_undo(self.undo_record())
        digest = self.plan()
        materialize = FINISH.recovery_tree
        def drift(*args, **kwargs):
            result = materialize(*args, **kwargs)
            if kwargs.get("write"):
                self.write_undo(self.undo_record(stage=3))
            return result
        with mock.patch.object(FINISH, "recovery_tree", side_effect=drift):
            with self.assertRaisesRegex(FINISH.Retain, "recovery-index-changed"):
                self.recover("--apply", "--recovery-sha256", digest)
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.record()[1]["state"], "recovery-pending")

    def test_retained_undo_index_drift_after_publication_keeps_pending_receipt(self):
        self.write_undo(self.undo_record())
        digest = self.plan()
        publish = FINISH.recovery_publish
        def drift(*args):
            publish(*args)
            self.write_undo(self.undo_record(stage=3))
        with mock.patch.object(FINISH, "recovery_publish", side_effect=drift):
            with self.assertRaisesRegex(FINISH.Retain, "recovery-index-changed"):
                self.recover("--apply", "--recovery-sha256", digest)
        self.assertTrue(self.wt.exists())
        record, retirement = self.record()
        self.assertIsNone(record["result"])
        self.assertEqual(retirement["state"], "recovery-pending")
        self.assertEqual((self.admin / "locked").read_bytes(), self.before_lock)

    def test_resolve_undo_object_batches_are_bounded_and_strict(self):
        expected = {str(i): [[1, 0o100644, f"{i + 1:040x}"]] for i in range(2050)}
        batches = []
        def objects(path, *args, **kwargs):
            self.assertEqual(args, ("--no-replace-objects", "cat-file", "--batch-check=%(objectname) %(objecttype)"))
            self.assertTrue(kwargs["strict"])
            self.assertTrue(kwargs["raw"])
            batch = kwargs["input_data"].splitlines()
            batches.append(len(batch))
            return b"".join(oid + b" blob\n" for oid in batch)
        FINISH.safety().retained_resolve_undo(self.repo, objects, time.monotonic() + 10, expected)
        self.assertEqual(batches, [1024, 1024, 2])
        def partial(*args, **kwargs):
            return objects(*args, **kwargs)[:-1]
        with self.assertRaisesRegex(FINISH.Retain, "resolve-undo-object-type-or-missing"):
            FINISH.safety().retained_resolve_undo(self.repo, partial, time.monotonic() + 10, expected)

    def test_rebase_head_invalid_objects_and_bytes_refuse(self):
        path = self.admin / "REBASE_HEAD"
        invalid = [b"", self.head.encode(), self.head.upper().encode() + b"\n",
                   b"ref: refs/heads/main\n", b"f" * 40 + b"\n",
                   git(self.repo, "rev-parse", "HEAD^{tree}").encode() + b"\n",
                   (self.head + "\n" + self.head + "\n").encode()]
        for raw in invalid:
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                with self.assertRaisesRegex(FINISH.Retain, "rebase-head-recovery-required"):
                    self.recover()
                self.assertIsNone(self.record()[0])
        path.unlink()
        path.symlink_to(self.external)
        with self.assertRaisesRegex(FINISH.Retain, "admin-recovery-content-unknown"):
            self.recover()

    def test_rebase_head_identity_and_byte_drift_refuse(self):
        path = self.admin / "REBASE_HEAD"
        path.write_text(self.head + "\n")
        digest = self.plan()
        moved = self.base / "retained-rebase-head"
        path.rename(moved)
        path.write_bytes(moved.read_bytes())
        with self.assertRaisesRegex(FINISH.Retain, "reviewed-preimage-required"):
            self.recover("--apply", "--recovery-sha256", digest)
        digest = self.plan()
        other = git(self.repo, "rev-parse", "main")
        path.write_text(other + "\n")
        with self.assertRaisesRegex(FINISH.Retain, "reviewed-preimage-required"):
            self.recover("--apply", "--recovery-sha256", digest)

    def test_rebase_head_replacement_cannot_make_noncommit_valid(self):
        blob = git(self.repo, "rev-parse", "HEAD:file")
        git(self.repo, "update-ref", "refs/replace/" + blob, self.head)
        self.assertEqual(git(self.repo, "cat-file", "-t", blob), "commit")
        (self.admin / "REBASE_HEAD").write_text(blob + "\n")
        with self.assertRaisesRegex(FINISH.Retain, "rebase-head-recovery-required"):
            self.recover()
        self.assertFalse(self.wt.exists())

    def test_rebase_head_foreign_file_owner_refuses(self):
        path = self.admin / "REBASE_HEAD"
        path.write_text(self.head + "\n")
        native = FINISH.safety().read_file
        def foreign(candidate, *args, **kwargs):
            data, identity = native(candidate, *args, **kwargs)
            if candidate == path:
                identity = (*identity[:3], os.getuid() + 1, *identity[4:])
            return data, identity
        with mock.patch.object(FINISH.safety(), "read_file", side_effect=foreign):
            with self.assertRaisesRegex(FINISH.Retain, "rebase-head-recovery-required"):
                self.recover()
        self.assertEqual(path.read_text(), self.head + "\n")
        self.assertIsNone(self.record()[0])

    def test_rebase_head_does_not_relax_deletion_or_active_operation_guards(self):
        path = self.admin / "REBASE_HEAD"
        path.write_text(self.head + "\n")
        snap = {**json.loads(self.original["identity"]), "path": str(self.repo), "head": self.head}
        with self.assertRaisesRegex(FINISH.Retain, "admin-recovery-content-unknown"):
            FINISH.safety().admin_inventory(snap, FINISH.git, time.monotonic() + 10,
                                            preserved_oids={self.head})
        digest = self.plan()
        tree = FINISH.recovery_tree
        def started(*args, **kwargs):
            result = tree(*args, **kwargs)
            if kwargs.get("write"):
                (self.admin / "rebase-merge").mkdir()
            return result
        with mock.patch.object(FINISH, "recovery_tree", side_effect=started):
            with self.assertRaisesRegex(FINISH.Retain, "git-operation-present"):
                self.recover("--apply", "--recovery-sha256", digest)
        self.assertFalse(self.wt.exists())

    def test_recovery_requires_unfinished_owner_and_rejects_cancellation(self):
        with FINISH.ledger(self.state) as db, db:
            db.execute("UPDATE owners SET completed=1 WHERE worktree_id=?", (self.original["id"],))
        with self.assertRaisesRegex(FINISH.Retain, "needs-unfinished-owner"):
            self.recover()
        with FINISH.ledger(self.state) as db:
            FINISH.ensure_cancellation_schema(db)
            with db:
                db.execute("UPDATE owners SET completed=0 WHERE worktree_id=?", (self.original["id"],))
                db.execute("INSERT INTO owner_cancellations VALUES (?, ?, ?, ?, ?)",
                           (self.original["id"], "owner-1", self.head, "cancelled", 1))
            self.generation = FINISH.retirement(db, self.original)["generation"]
        with self.assertRaisesRegex(FINISH.Retain, "cancellation-present"):
            self.recover()
        self.assertFalse(self.wt.exists())

    def test_mixed_releases_reject_stale_unknown_and_malformed_proof(self):
        self.mixed_owner_missing()
        with FINISH.ledger(self.state) as db:
            baseline = FINISH.retirement(db, self.original)["inventory"]
        mutations = [
            ("UPDATE owner_releases SET generation=generation-1", (), "release-proof-invalid"),
            ("UPDATE owner_releases SET owner='unknown-owner'", (), "release-proof-invalid"),
            ("UPDATE owner_releases SET owner='owner-2'", (), "release-proof-invalid"),
            ("UPDATE retirement SET inventory='{}' WHERE worktree_id=?", (self.original["id"],),
             "release-inventory-invalid"),
        ]
        for sql, values, reason in mutations:
            with self.subTest(reason=reason, sql=sql):
                with FINISH.ledger(self.state) as db, db:
                    db.execute(sql, values)
                with self.assertRaisesRegex(FINISH.Retain, reason):
                    self.recover()
                with FINISH.ledger(self.state) as db, db:
                    db.execute("DELETE FROM owner_releases WHERE worktree_id=?", (self.original["id"],))
                    db.execute("INSERT INTO owner_releases VALUES (?, ?, ?)",
                               (self.original["id"], "owner-1", self.generation))
                    db.execute("UPDATE retirement SET inventory=? WHERE worktree_id=?",
                               (baseline, self.original["id"]))

    def test_release_inventory_drift_after_staging_refuses(self):
        self.mixed_owner_missing(blocker=True)
        digest = self.plan()
        tree = FINISH.recovery_tree
        def changed(*args, **kwargs):
            result = tree(*args, **kwargs)
            if kwargs.get("write"):
                # The public command already owns the native ledger lock.
                with contextlib.closing(sqlite3.connect(self.state / "lifecycle.sqlite")) as db, db:
                    raw = json.loads(db.execute("SELECT inventory FROM retirement WHERE worktree_id=?",
                                                (self.original["id"],)).fetchone()[0])
                    raw["blocker"] = "different-blocker"
                    db.execute("UPDATE retirement SET inventory=? WHERE worktree_id=?",
                               (json.dumps(raw), self.original["id"]))
            return result
        with mock.patch.object(FINISH, "recovery_tree", side_effect=changed):
            with self.assertRaisesRegex(FINISH.Retain, "pre-publication-drift"):
                self.recover("--apply", "--recovery-sha256", digest)
        self.assertFalse(self.wt.exists())
        self.assertEqual(len(self.history()), 1)

    def test_release_for_other_finish_head_refuses(self):
        inventory = self.mixed_owner_missing()
        with FINISH.ledger(self.state) as db, db:
            db.execute("UPDATE worktrees SET finish_head=? WHERE id=?",
                       (git(self.repo, "rev-parse", "main"), self.original["id"]))
            self.generation = FINISH.retirement(db, self.original)["generation"]
            db.execute("INSERT INTO owner_releases VALUES (?, ?, ?)",
                       (self.original["id"], "owner-1", self.generation))
            db.execute("UPDATE retirement SET inventory=? WHERE worktree_id=?",
                       (inventory, self.original["id"]))
        with self.assertRaisesRegex(FINISH.Retain, "release-proof-invalid"):
            self.recover()
        self.assertFalse(self.wt.exists())

    def test_release_proof_requires_producer_dependency_shape_and_repository(self):
        inventory = self.mixed_owner_missing()
        dependency = {"url": "https://github.com/example/repo/pull/2",
                      "head": self.head, "target": "main"}
        foreign = "https://github.com/foreign/repo/pull/1"
        self.proofs[foreign] = self.proof(foreign, repo="foreign/repo", head_repo="wrong/repo")
        changes = [
            ("primary_pr", "https://github.com/foreign/repo/pull/1/"),
            ("dependencies", "[{}]"),
            ("dependencies", json.dumps([{**dependency, "url": self.url}])),
            ("dependencies", json.dumps([{**dependency, "url": "https://github.com/foreign/repo/pull/2"}])),
            ("dependencies", json.dumps([{**dependency, "head": "invalid"}])),
            ("dependencies", json.dumps([{**dependency, "target": "other"}])),
            ("dependencies", json.dumps([dependency] * 17)),
        ]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                with FINISH.ledger(self.state) as db, db:
                    db.execute("UPDATE worktrees SET primary_pr=?, dependencies=? WHERE id=?",
                               (value if field == "primary_pr" else self.url,
                                value if field == "dependencies" else "[]", self.original["id"]))
                    self.generation = FINISH.retirement(db, self.original)["generation"]
                    db.execute("INSERT INTO owner_releases VALUES (?, ?, ?)",
                               (self.original["id"], "owner-1", self.generation))
                    db.execute("UPDATE retirement SET inventory=? WHERE worktree_id=?",
                               (inventory, self.original["id"]))
                with self.assertRaisesRegex(FINISH.Retain, "release-proof-invalid"):
                    self.recover()
                self.assertFalse(self.wt.exists())
        with FINISH.ledger(self.state) as db, db:
            db.execute("UPDATE worktrees SET primary_pr=?, dependencies='[]' WHERE id=?",
                       (foreign, self.original["id"]))
            self.generation = FINISH.retirement(db, self.original)["generation"]
            db.execute("INSERT INTO owner_releases VALUES (?, ?, ?)",
                       (self.original["id"], "owner-1", self.generation))
            db.execute("UPDATE retirement SET inventory=? WHERE worktree_id=?",
                       (inventory, self.original["id"]))
        with self.assertRaisesRegex(FINISH.Retain, "primary-pr-proof-changed"):
            self.recover()
        self.assertFalse(self.wt.exists())

    def test_cancellation_after_staging_is_preserved_and_refuses_publication(self):
        with FINISH.ledger(self.state) as db:
            FINISH.ensure_cancellation_schema(db)
        digest = self.plan()
        tree = FINISH.recovery_tree
        def cancelled(*args, **kwargs):
            result = tree(*args, **kwargs)
            if kwargs.get("write"):
                with contextlib.closing(sqlite3.connect(self.state / "lifecycle.sqlite")) as db, db:
                    db.execute("INSERT INTO owner_cancellations VALUES (?, ?, ?, ?, ?)",
                               (self.original["id"], "owner-1", self.head, "cancelled", 1))
            return result
        with mock.patch.object(FINISH, "recovery_tree", side_effect=cancelled):
            with self.assertRaisesRegex(FINISH.Retain, "cancellation-present"):
                self.recover("--apply", "--recovery-sha256", digest)
        self.assertFalse(self.wt.exists())
        with FINISH.ledger(self.state) as db:
            self.assertEqual(FINISH.cancellation_rows(db, self.original["id"])[0]["reason"], "cancelled")

    def test_archiving_and_replacement_roll_back_with_lifecycle_cas(self):
        self.second_missing()
        before = self.record()[0]
        digest = self.plan()
        with FINISH.ledger(self.state) as db:
            db.execute("CREATE TRIGGER fixture_cas_failure BEFORE UPDATE ON retirement "
                       "WHEN NEW.state='recovery-pending' BEGIN SELECT RAISE(ABORT, 'fixture-cas'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "fixture-cas"):
            self.recover("--apply", "--recovery-sha256", digest)
        self.assertEqual(self.record()[0], before)
        self.assertEqual(self.history(), {})
        with FINISH.ledger(self.state) as db:
            self.assertFalse(db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='missing_recovery_history'").fetchone())
        self.assertFalse(self.wt.exists())

    def test_history_collision_refuses_without_replacing_receipt(self):
        self.second_missing()
        before = self.record()[0]
        with FINISH.ledger(self.state) as db, db:
            fact = FINISH.missing_recovery_fact(db, self.original)
            db.execute("CREATE TABLE missing_recovery_history (worktree_id TEXT, intent_id TEXT, fact TEXT)")
            db.execute("INSERT INTO missing_recovery_history VALUES (?, ?, ?)",
                       (self.original["id"], before["id"], fact))
        with self.assertRaisesRegex(FINISH.Retain, "history-collision"):
            self.recover()
        self.assertEqual(self.record()[0], before)
        self.assertEqual(self.history(), {before["id"]: fact})

    def test_pending_successor_cannot_be_displaced_by_new_or_old_intent(self):
        self.second_missing()
        old, _ = self.record()
        digest = self.plan()
        with mock.patch.object(FINISH, "recovery_publish", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.recover("--apply", "--recovery-sha256", digest)
        pending, retirement = self.record()
        history = self.history()
        for args in ((), ("--intent-id", old["id"], "--recovery-sha256", old["digest"])):
            with self.assertRaisesRegex(FINISH.Retain, "exact-intent-required"):
                self.recover(*args)
        self.assertEqual(self.record(), (pending, retirement))
        self.assertEqual(self.history(), history)
        self.assertFalse(self.wt.exists())

    def test_second_recovery_crash_replays_same_intent_without_rearchiving(self):
        self.second_missing()
        digest = self.plan()
        publish = FINISH.recovery_publish
        def crashed(source, target):
            publish(source, target)
            raise RuntimeError("after publication")
        with mock.patch.object(FINISH, "recovery_publish", side_effect=crashed):
            with self.assertRaisesRegex(RuntimeError, "after publication"):
                self.recover("--apply", "--recovery-sha256", digest)
        record, _ = self.record()
        history = self.history()
        before = FINISH.identity(self.wt)
        self.assertEqual(len(history), 1)
        result = self.recover("--apply", "--recovery-sha256", digest, "--intent-id", record["id"])
        self.assertEqual(FINISH.identity(self.wt), before)
        self.assertEqual(self.history(), history)
        self.assertEqual(result["intent_id"], record["id"])

    def test_receipt_drift_after_staging_refuses(self):
        self.second_missing()
        digest = self.plan()
        tree = FINISH.recovery_tree
        def changed(*args, **kwargs):
            result = tree(*args, **kwargs)
            if kwargs.get("write"):
                with contextlib.closing(sqlite3.connect(self.state / "lifecycle.sqlite")) as db, db:
                    db.execute("UPDATE missing_recoveries SET fact=fact || ' ' WHERE worktree_id=?",
                               (self.original["id"],))
            return result
        with mock.patch.object(FINISH, "recovery_tree", side_effect=changed):
            with self.assertRaisesRegex(FINISH.Retain, "recovery-receipt-drift"):
                self.recover("--apply", "--recovery-sha256", digest)
        self.assertFalse(self.wt.exists())

    def fetched_main(self):
        target = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", self.head, "-m", "main advances")
        git(self.repo, "update-ref", "refs/heads/main", target)
        git(self.repo, "--git-dir", str(self.admin), "fetch", "--no-auto-maintenance", "--no-tags",
            str(self.repo), "refs/heads/main:refs/remotes/origin/main")
        self.assertEqual(git(self.repo, "rev-list", target, "--not", self.head), target)
        return target

    def test_fetched_main_outside_task_head_is_preserved_by_recovery(self):
        target = self.fetched_main()
        path = self.admin / "FETCH_HEAD"
        before = FINISH.safety().read_file(path, time.monotonic() + 10)
        digest = self.plan()
        self.assertFalse(self.wt.exists())
        self.assertIsNone(self.record()[0])
        with mock.patch.dict(os.environ, {"GWT_OWNER_ID": "recovery-operator"}):
            result = self.recover("--apply", "--recovery-sha256", digest)
        self.assertEqual(result["checkout"], "restored")
        self.assertEqual(FINISH.safety().read_file(path, time.monotonic() + 10), before)
        self.assertEqual((self.admin / "index").read_bytes(), self.before_index)
        self.assertEqual((self.admin / "locked").read_bytes(), self.before_lock)
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), self.head)
        self.assertEqual(git(self.repo, "rev-parse", "refs/remotes/origin/main"), target)
        record, _ = self.record()
        inventory = record["preimage"]["admin"]["FETCH_HEAD"]
        self.assertEqual(inventory[0][:-1], list(before[1]))
        self.assertEqual(inventory[1:], [hashlib.sha256(before[0]).hexdigest(), [target]])
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            self.assertEqual(FINISH.owner_rows(db, row["id"]), [{"owner": "owner-1", "completed": 0}])
            FINISH.revalidate(row)
        self.assertEqual(self.recover("--apply", "--recovery-sha256", digest,
                                      "--intent-id", result["intent_id"]), result)

    def test_retained_fetch_head_inventory_sorts_unique_validated_oids(self):
        target = self.fetched_main()
        unanchored = git(self.repo, "commit-tree", "HEAD^{tree}", "-m", "fetched-only commit")
        path = self.admin / "FETCH_HEAD"
        path.write_text("".join(oid + "\tnot-for-merge\tfetched commit\n"
                               for oid in (target, unanchored, self.head, target)))
        digest = self.plan()
        self.recover("--apply", "--recovery-sha256", digest)
        self.assertEqual(self.record()[0]["preimage"]["admin"]["FETCH_HEAD"][2],
                         sorted({target, unanchored, self.head}))

    def test_fetch_head_recovery_does_not_relax_deletion_ancestry(self):
        self.fetched_main()
        snap = {**json.loads(self.original["identity"]), "path": str(self.repo), "head": self.head}
        before = (self.admin / "FETCH_HEAD").read_bytes()
        with self.assertRaisesRegex(FINISH.Retain, "fetch-head-recovery-required"):
            FINISH.safety().admin_inventory(snap, FINISH.git, time.monotonic() + 10,
                                            preserved_oids={self.head})
        self.assertEqual((self.admin / "FETCH_HEAD").read_bytes(), before)
        self.assertFalse(self.wt.exists())
        self.assertIsNone(self.record()[0])

    def test_fetch_head_byte_or_inode_drift_invalidates_reviewed_digest(self):
        self.fetched_main()
        path = self.admin / "FETCH_HEAD"
        for change in ("bytes", "inode"):
            with self.subTest(change=change):
                digest = self.plan()
                if change == "bytes":
                    path.write_bytes(path.read_bytes().replace(b"branch ", b"updated branch "))
                else:
                    replacement = self.base / "replacement"
                    replacement.write_bytes(path.read_bytes())
                    self.assertNotEqual(path.stat().st_ino, replacement.stat().st_ino)
                    replacement.replace(path)
                changed = FINISH.safety().read_file(path, time.monotonic() + 10)
                self.assertNotEqual(self.plan(), digest)
                with self.assertRaisesRegex(FINISH.Retain, "reviewed-preimage-required"):
                    self.recover("--apply", "--recovery-sha256", digest)
                self.assertEqual(FINISH.safety().read_file(path, time.monotonic() + 10), changed)
                self.assertFalse(self.wt.exists())
                self.assertIsNone(self.record()[0])

    def test_fetch_head_malformed_missing_or_noncommit_objects_refuse(self):
        oid = self.head.encode()
        blob = git(self.repo, "rev-parse", self.head + ":file").encode()
        tree = git(self.repo, "rev-parse", self.head + "^{tree}").encode()
        git(self.repo, "tag", "-a", "fetched-tag", self.head, "-m", "tag fixture")
        tag = git(self.repo, "rev-parse", "fetched-tag").encode()
        valid = oid + b"\t\tvalid commit\n"
        path = self.admin / "FETCH_HEAD"
        for data in (
                b"0" * 40 + b"\t\tmissing object\n",
                blob + b"\t\tblob\n", tree + b"\t\ttree\n", tag + b"\t\ttag\n",
                oid.upper() + b"\t\tuppercase\n", oid[:-1] + b"\t\tshort oid\n",
                oid + b"\tmerge\tnote\n", oid + b"\t\tunterminated",
                oid + b"\t\tnote\0url\n", valid + b"\n", valid + b"partial",
                valid + blob + b"\tnot-for-merge\tmixed invalid record\n"):
            with self.subTest(data=data):
                path.write_bytes(data)
                before = FINISH.safety().read_file(path, time.monotonic() + 10)
                with self.assertRaisesRegex(FINISH.Retain, "fetch-head-recovery-required"):
                    self.recover()
                self.assertEqual(FINISH.safety().read_file(path, time.monotonic() + 10), before)
                self.assertFalse(self.wt.exists())
                self.assertIsNone(self.record()[0])
                self.assertEqual((self.admin / "index").read_bytes(), self.before_index)
                self.assertEqual((self.admin / "locked").read_bytes(), self.before_lock)

    def test_fetch_head_symlink_and_unknown_admin_metadata_refuse(self):
        path = self.admin / "FETCH_HEAD"
        path.symlink_to(self.external)
        with self.assertRaisesRegex(FINISH.Retain, "admin-recovery-content-unknown"):
            self.recover()
        self.assertTrue(path.is_symlink())
        self.assertEqual(self.external.read_text(), "untouched\n")
        path.unlink()
        self.fetched_main()
        unknown = self.admin / "unknown-recovery"
        unknown.write_text("preserve\n")
        with self.assertRaisesRegex(FINISH.Retain, "admin-recovery-content-unknown"):
            self.recover()
        self.assertEqual(unknown.read_text(), "preserve\n")
        self.assertFalse(self.wt.exists())
        self.assertIsNone(self.record()[0])

    def test_fetch_head_drift_after_staging_prevents_publication(self):
        self.fetched_main()
        digest = self.plan()
        tree = FINISH.recovery_tree
        path = self.admin / "FETCH_HEAD"
        def drift(*args, **kwargs):
            result = tree(*args, **kwargs)
            if kwargs.get("write"):
                path.write_bytes(path.read_bytes().replace(b"branch ", b"updated branch "))
            return result
        with mock.patch.object(FINISH, "recovery_tree", side_effect=drift):
            with self.assertRaisesRegex(FINISH.Retain, "pre-publication-drift"):
                self.recover("--apply", "--recovery-sha256", digest)
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.record()[1]["state"], "recovery-pending")
        self.assertEqual((self.admin / "index").read_bytes(), self.before_index)
        self.assertEqual((self.admin / "locked").read_bytes(), self.before_lock)

    def record(self):
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            return FINISH.missing_recovery_record(db, row), dict(FINISH.retirement(db, row))

    def recorded_device_change(self, transform=None):
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            snap = json.loads(row["identity"])
            for key in ("path_id", "owner_id", "common_id", "gitdir_id"):
                snap[key][0] += 100
            if transform:
                transform(snap)
            with db:
                db.execute("UPDATE worktrees SET identity=? WHERE id=?", (json.dumps(snap), row["id"]))
                db.execute("UPDATE retirement SET generation=0 WHERE worktree_id=?", (row["id"],))
        return snap

    def test_device_change_requires_opt_in_and_preserves_original_history(self):
        original = self.recorded_device_change()
        with self.assertRaisesRegex(FINISH.Retain, "admin-owner-identity-changed"):
            self.recover()
        plan = self.recover("--allow-device-renumbering")
        mapped_root = [self.wt.parent.stat().st_dev, original["path_id"][1]]
        self.assertEqual(plan["device_mapping"], [[original["path_id"][0], mapped_root[0]]])
        holder_args = self.holders_mock.call_args.args
        self.assertEqual(holder_args[0]["path_id"], mapped_root)
        self.assertEqual(holder_args[1]["discarded_inodes"], [mapped_root])
        self.assertEqual(holder_args[0]["gitdir_id"], FINISH.identity(self.admin))
        digest = plan["recovery_sha256"]
        result = self.recover("--allow-device-renumbering", "--apply", "--recovery-sha256", digest)
        record, retirement = self.record()
        self.assertEqual(record["preimage"]["identity"], original)
        self.assertEqual(retirement["generation"], 1)
        self.assertEqual((self.admin / "index").read_bytes(), self.before_index)
        self.assertEqual((self.admin / "locked").read_bytes(), self.before_lock)
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            FINISH.revalidate(row)
            self.assertEqual(FINISH.owner_rows(db, row["id"]), [{"owner": "owner-1", "completed": 0}])
        self.assertEqual(self.recover("--allow-device-renumbering", "--apply", "--recovery-sha256", digest,
                                      "--intent-id", result["intent_id"]), result)
        with self.assertRaisesRegex(FINISH.Retain, "exact-intent-required"):
            self.recover("--apply", "--recovery-sha256", digest, "--intent-id", result["intent_id"])

    def test_device_opt_in_changes_plan_digest(self):
        strict = self.recover()
        opted_in = self.recover("--allow-device-renumbering")
        self.assertNotEqual(strict["recovery_sha256"], opted_in["recovery_sha256"])

    def test_device_normalization_refuses_changed_inode(self):
        self.recorded_device_change(lambda snap: snap["gitdir_id"].__setitem__(1, snap["gitdir_id"][1] + 1))
        with self.assertRaisesRegex(FINISH.Retain, "device-or-inode-changed"):
            self.recover("--allow-device-renumbering")
        self.assertIsNone(self.record()[0])

    def test_device_normalization_refuses_nonbijective_map(self):
        self.recorded_device_change(lambda snap: snap["gitdir_id"].__setitem__(0, snap["gitdir_id"][0] + 1))
        with self.assertRaisesRegex(FINISH.Retain, "device-map-not-bijective"):
            self.recover("--allow-device-renumbering")
        self.assertIsNone(self.record()[0])

    def test_device_normalization_refuses_unmapped_root(self):
        self.recorded_device_change(lambda snap: snap["path_id"].__setitem__(0, snap["path_id"][0] + 1))
        with self.assertRaisesRegex(FINISH.Retain, "parent-volume-unmapped-or-changed"):
            self.recover("--allow-device-renumbering")
        self.assertIsNone(self.record()[0])

    def test_device_normalization_refuses_parent_volume_change(self):
        self.recorded_device_change()
        identity = FINISH.identity
        def changed_parent(path):
            dev, inode = identity(path)
            return [dev + 1, inode] if Path(path) == self.wt.parent else [dev, inode]
        with mock.patch.object(FINISH, "identity", side_effect=changed_parent):
            with self.assertRaisesRegex(FINISH.Retain, "parent-volume-unmapped-or-changed"):
                self.recover("--allow-device-renumbering")
        self.assertIsNone(self.record()[0])

    def test_plan_does_not_create_root_or_recovery_intent(self):
        result = self.recover()
        self.assertFalse(result["checkout_mutation"])
        self.assertEqual(result["untracked_unstaged"], "unavailable")
        self.assertFalse(self.wt.exists())
        record, retirement = self.record()
        self.assertIsNone(record)
        self.assertEqual(retirement["state"], "enrolled")

    def test_success_preserves_index_admin_branch_and_original_owner(self):
        digest = self.plan()
        with mock.patch.dict(os.environ, {"GWT_OWNER_ID": "recovery-operator"}):
            result = self.recover("--apply", "--recovery-sha256", digest)
        self.assertEqual(result["checkout"], "restored")
        self.assertEqual(result["generation"], 1)
        self.assertEqual(result["operator"], "recovery-operator")
        self.assertEqual((self.wt / "file").read_text(), "original\n")
        self.assertEqual(stat_mode(self.wt / "nested/command"), 0o755)
        self.assertTrue((self.wt / "external-link").is_symlink())
        self.assertEqual(os.readlink(self.wt / "external-link"), str(self.external))
        self.assertEqual(self.external.read_text(), "untouched\n")
        self.assertEqual((self.admin / "index").read_bytes(), self.before_index)
        self.assertEqual((self.admin / "locked").read_bytes(), self.before_lock)
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            self.assertEqual(FINISH.owner_rows(db, row["id"]), [{"owner": "owner-1", "completed": 0}])
            self.assertEqual(row["state"], "active")
            FINISH.revalidate(row)
        replay = self.recover("--apply", "--recovery-sha256", digest, "--intent-id", result["intent_id"])
        self.assertEqual(replay, result)

    def test_appeared_target_is_not_overwritten(self):
        digest = self.plan()
        self.wt.mkdir()
        (self.wt / "keep").write_text("other owner\n")
        with self.assertRaisesRegex(FINISH.Retain, "target-present"):
            self.recover("--apply", "--recovery-sha256", digest)
        self.assertEqual((self.wt / "keep").read_text(), "other owner\n")

    def test_native_publication_does_not_replace_even_empty_directory(self):
        source, target = self.base / "stage", self.base / "target"
        source.mkdir()
        target.mkdir()
        with self.assertRaisesRegex(FINISH.Retain, "no-replace-refused"):
            FINISH.recovery_publish(source, target)
        self.assertTrue(source.is_dir())
        self.assertTrue(target.is_dir())

    def test_index_drift_refuses_before_intent(self):
        digest = self.plan()
        (self.admin / "index").write_bytes(self.before_index + b"changed")
        with self.assertRaisesRegex(FINISH.Retain, "index-changed"):
            self.recover("--apply", "--recovery-sha256", digest)
        self.assertIsNone(self.record()[0])

    def test_completed_receipt_cannot_cover_later_source_edit(self):
        digest = self.plan()
        result = self.recover("--apply", "--recovery-sha256", digest)
        (self.wt / "file").write_text("later unfinished edit\n")
        with self.assertRaisesRegex(FINISH.Retain, "file-content-changed"):
            self.recover("--apply", "--recovery-sha256", digest, "--intent-id", result["intent_id"])
        self.assertEqual((self.wt / "file").read_text(), "later unfinished edit\n")

    def test_unknown_index_extension_refuses(self):
        raw = self.before_index[:-20] + b"TEST" + struct.pack(">I", 0)
        raw += hashlib.sha1(raw).digest()
        (self.admin / "index").write_bytes(raw)
        self.index_sha = hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(FINISH.Retain, "extension-unsupported"):
            self.recover()

    def test_index_not_head_refuses(self):
        original = bytes.fromhex(git(self.repo, "rev-parse", "released-feature:file"))
        changed = bytes.fromhex(git(self.repo, "rev-parse", "released-feature:nested/command"))
        body = self.before_index[:-20].replace(original, changed, 1)
        data = body + hashlib.sha1(body).digest()
        (self.admin / "index").write_bytes(data)
        self.index_sha = hashlib.sha256(data).hexdigest()
        with self.assertRaisesRegex(FINISH.Retain, "index-not-head"):
            self.recover()

    def test_holder_unknown_and_maintenance_lock_refuse(self):
        self.holders_mock.side_effect = FINISH.Retain("manual-holder-visibility-unknown")
        with self.assertRaisesRegex(FINISH.Retain, "visibility-unknown"):
            self.recover()
        self.holders_mock.side_effect = None
        with FINISH.safety().maintainer_lock(self.home / ".codex"):
            with self.assertRaisesRegex(FINISH.Retain, "maintainer-lock-present"):
                self.recover()
        self.assertIsNone(self.record()[0])

    def test_lifecycle_lock_contention_refuses(self):
        with FINISH.ledger(self.state):
            with self.assertRaisesRegex(FINISH.Retain, "lifecycle-busy"):
                self.recover()

    def test_partial_staging_is_retained_not_rebuilt(self):
        digest = self.plan()
        with mock.patch.object(FINISH, "recovery_tree", side_effect=RuntimeError("during materialization")):
            with self.assertRaisesRegex(RuntimeError, "during materialization"):
                self.recover("--apply", "--recovery-sha256", digest)
        record, _ = self.record()
        stage = Path(record["stage"])
        self.assertTrue(stage.is_dir())
        with self.assertRaisesRegex(FINISH.Retain, "incomplete-or-rebound"):
            self.recover("--apply", "--recovery-sha256", digest, "--intent-id", record["id"])
        self.assertTrue(stage.is_dir())
        self.assertFalse(self.wt.exists())

    def partial_stage(self):
        digest = self.plan()
        with mock.patch.object(FINISH, "recovery_tree", side_effect=RuntimeError("during materialization")):
            with self.assertRaisesRegex(RuntimeError, "during materialization"):
                self.recover("--apply", "--recovery-sha256", digest)
        record, _ = self.record()
        stage = Path(record["stage"])
        (stage / "file").write_text("original\n")
        (stage / "file").chmod(0o644)
        return stage, digest, record["id"]

    def test_explicit_partial_continuation_preserves_existing_file_and_stage(self):
        stage, digest, intent = self.partial_stage()
        before = FINISH.identity(stage)
        file_inode = (stage / "file").stat().st_ino
        plan = self.recover("--continue-partial", "--recovery-sha256", digest, "--intent-id", intent)
        self.assertTrue(plan["partial_continuation"])
        self.assertEqual(plan["recovery_sha256"], digest)
        self.assertEqual(self.holders_mock.call_args.args[0]["path"], str(stage))
        self.assertIn(before, self.holders_mock.call_args.args[1]["discarded_inodes"])
        result = self.recover("--continue-partial", "--apply", "--recovery-sha256", digest, "--intent-id", intent)
        self.assertEqual(result["generation"], 1)
        self.assertEqual(FINISH.identity(self.wt), before)
        self.assertEqual((self.wt / "file").stat().st_ino, file_inode)
        self.assertEqual(self.record()[0]["id"], intent)

    def test_partial_continuation_refuses_corrupt_existing_bytes(self):
        stage, digest, intent = self.partial_stage()
        (stage / "file").write_text("corrupt\n")
        with self.assertRaisesRegex(FINISH.Retain, "file-content-changed"):
            self.recover("--continue-partial", "--apply", "--recovery-sha256", digest, "--intent-id", intent)
        self.assertEqual((stage / "file").read_text(), "corrupt\n")
        self.assertFalse((stage / "nested").exists())
        self.assertEqual(self.record()[1]["state"], "recovery-pending")

    def test_partial_continuation_refuses_extra_entry(self):
        stage, digest, intent = self.partial_stage()
        (stage / "extra").write_text("preserve\n")
        with self.assertRaisesRegex(FINISH.Retain, "tree-unexpected-entry"):
            self.recover("--continue-partial", "--apply", "--recovery-sha256", digest, "--intent-id", intent)
        self.assertEqual((stage / "extra").read_text(), "preserve\n")
        self.assertFalse((stage / "nested").exists())

    def test_partial_continuation_does_not_overwrite_appeared_file(self):
        stage, digest, intent = self.partial_stage()
        native_git = FINISH.git
        def appeared(path, *args, **kwargs):
            result = native_git(path, *args, **kwargs)
            if args[:3] == ("--no-replace-objects", "cat-file", "--batch") and not (stage / "nested/command").exists():
                (stage / "nested/command").write_text("appeared\n")
            return result
        with mock.patch.object(FINISH, "git", side_effect=appeared):
            with self.assertRaises(FileExistsError):
                self.recover("--continue-partial", "--apply", "--recovery-sha256", digest, "--intent-id", intent)
        self.assertEqual((stage / "nested/command").read_text(), "appeared\n")
        self.assertFalse(self.wt.exists())

    def test_recovery_blob_above_eight_mib_uses_scoped_capture_and_verification(self):
        data = b"x" * (9 * 1024 * 1024)
        oid = subprocess.run(["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"],
                             input=data, stdout=subprocess.PIPE, check=True).stdout.decode().strip()
        stage = self.base / "large-stage"
        stage.mkdir(mode=0o700)
        entries = {"large": (0o100644, oid)}
        FINISH.recovery_tree(stage, entries, str(self.admin), FINISH.safety().DEADLINE,
                             write=True, owner=str(self.repo))
        FINISH.recovery_tree(stage, entries, str(self.admin), FINISH.safety().DEADLINE)
        self.assertEqual((stage / "large").read_bytes(), data)

    def test_legacy_size_failure_continues_with_large_blobs_and_preserved_inode(self):
        self.recover("--apply", "--recovery-sha256", self.plan())
        data = b"x" * 17_387_080
        for name in ("large", "large-executable"):
            (self.wt / name).write_bytes(data)
        (self.wt / "large-executable").chmod(0o755)
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-m", "large recovery fixture")
        self.disappear_again()
        digest = self.plan()
        with mock.patch.object(FINISH, "recovery_blobs", side_effect=FINISH.Retain("recovery-blob-size-limit")):
            with self.assertRaisesRegex(FINISH.Retain, "recovery-blob-size-limit"):
                self.recover("--apply", "--recovery-sha256", digest)
        old = self.legacy_receipt()
        stage = Path(old["stage"])
        (stage / "file").write_text("original\n")
        (stage / "file").chmod(0o644)
        before = (stage / "file").stat().st_ino
        plan = self.recover("--continue-partial", "--intent-id", old["id"],
                            "--recovery-sha256", old["digest"])
        self.assertEqual(plan["recovery_sha256"], old["digest"])
        result = self.recover("--continue-partial", "--apply", "--intent-id", old["id"],
                              "--recovery-sha256", old["digest"])
        self.assertEqual(result["checkout"], "restored")
        self.assertEqual((self.wt / "file").stat().st_ino, before)
        self.assertEqual((self.wt / "large").read_bytes(), data)
        self.assertEqual((self.wt / "large-executable").read_bytes(), data)
        self.assertEqual(stat_mode(self.wt / "large-executable"), 0o755)
        self.assertEqual(result["recovery_sha256"], old["digest"])
        self.assertNotIn("contract", self.record()[0])

    def test_metadata_budget_refuses_before_new_intent(self):
        digest = self.plan()
        native = FINISH.git
        regular = {git(self.repo, "rev-parse", self.head + ":" + name).encode()
                   for name in (".gitignore", "file")}
        def oversized(path, *args, **kwargs):
            raw = native(path, *args, **kwargs)
            if args[:3] == ("--no-replace-objects", "cat-file", "--batch-check"):
                lines = raw.splitlines()
                for i, line in enumerate(lines):
                    oid, kind, _ = line.split(b" ")
                    if oid in regular:
                        lines[i] = oid + b" " + kind + b" " + str(FINISH.safety().SOURCE_LIMIT // 2).encode()
                return b"\n".join(lines) + b"\n"
            return raw
        with mock.patch.object(FINISH, "git", side_effect=oversized):
            with self.assertRaisesRegex(FINISH.Retain, "recovery-source-byte-limit"):
                self.plan()
            with self.assertRaisesRegex(FINISH.Retain, "recovery-source-byte-limit"):
                self.recover("--apply", "--recovery-sha256", digest)
        record, retirement = self.record()
        self.assertIsNone(record)
        self.assertEqual(retirement["state"], "enrolled")
        self.assertFalse(list(self.wt.parent.glob(".gwt-recover-*")))
        self.assertFalse(self.wt.exists())

    def test_metadata_source_budget_is_independent_of_admin_budget(self):
        native = FINISH.git
        def sized(path, *args, **kwargs):
            raw = native(path, *args, **kwargs)
            if args[:3] == ("--no-replace-objects", "cat-file", "--batch-check"):
                first, rest = raw.split(b"\n", 1)
                oid, kind, _ = first.split(b" ")
                return oid + b" " + kind + b" " + str(FINISH.safety().PASS_LIMIT + 1).encode() + b"\n" + rest
            return raw
        self.assertGreater(FINISH.safety().SOURCE_LIMIT, FINISH.safety().PASS_LIMIT)
        with mock.patch.object(FINISH, "git", side_effect=sized):
            result = self.recover()
        self.assertFalse(result["checkout_mutation"])
        self.assertIsNone(self.record()[0])
        self.assertFalse(self.wt.exists())

    def test_pending_source_budget_refusal_preserves_exact_intent(self):
        stage, digest, intent = self.partial_stage()
        before = self.record()
        file_inode = (stage / "file").stat().st_ino
        with mock.patch.object(FINISH.safety(), "SOURCE_LIMIT", 1):
            with self.assertRaisesRegex(FINISH.Retain, "recovery-source-byte-limit"):
                self.recover("--continue-partial", "--apply", "--recovery-sha256", digest,
                             "--intent-id", intent)
        self.assertEqual(self.record(), before)
        self.assertEqual((stage / "file").stat().st_ino, file_inode)
        self.assertEqual((stage / "file").read_text(), "original\n")
        result = self.recover("--continue-partial", "--apply", "--recovery-sha256", digest,
                              "--intent-id", intent)
        self.assertEqual(result["recovery_sha256"], digest)
        self.assertEqual(result["intent_id"], intent)
        self.assertEqual(result["generation"], 1)
        self.assertEqual((self.wt / "file").stat().st_ino, file_inode)

    def test_replaced_staging_parent_symlink_is_not_followed(self):
        stage = self.base / "stage"
        stage.mkdir(mode=0o700)
        (stage / "nested").symlink_to(self.base, target_is_directory=True)
        fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaises(OSError):
                with FINISH.recovery_parent(fd, "nested/should-not-write"):
                    self.fail("followed staging symlink")
        finally:
            os.close(fd)
        self.assertFalse((self.base / "should-not-write").exists())

    @unittest.skipUnless(os.environ.get("GWT_RECOVERY_SCALE") == "1", "explicit large apply qualification")
    def test_large_batch_recovery_applies_with_original_deadline(self):
        count, source_bytes, largest = 65771, 742463141, 17387080
        target = b"000/00001"
        size, extra = divmod(source_bytes - largest - len(target), count - 2)
        expected = {}
        message = (self.admin / "COMMIT_EDITMSG").read_bytes()
        child = subprocess.Popen(["git", "-C", str(self.repo), "fast-import", "--quiet"],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for i in range(count):
                name = f"{i // 1000:03d}/{i:05d}"
                length = largest if i == 0 else size + (i <= extra)
                data = (target if i == count - 1 else
                        i.to_bytes(8, "big") + b"\0\n" + b"x" * (length - 10))
                mode = 0o120000 if i == count - 1 else 0o100755 if i == 0 else 0o100644
                oid = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                expected[name] = (mode, oid)
                child.stdin.write(f"blob\nmark :{i + 1}\ndata {len(data)}\n".encode() + data + b"\n")
            child.stdin.write(("commit refs/heads/released-feature\n"
                               "committer Fixture <fixture@example.test> 1 +0000\n"
                               f"data {len(message)}\n").encode() + message +
                              ("\nfrom " + self.head + "\ndeleteall\n").encode())
            for i, (name, (mode, _)) in enumerate(expected.items()):
                child.stdin.write(f"M {mode:o} :{i + 1} {name}\n".encode())
            child.stdin.write(b"\ndone\n")
            child.stdin.close()
            child.stdin = None
            output, errors = child.communicate()
            self.assertEqual(child.returncode, 0, errors)
            self.assertEqual(output, b"")
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()
        self.assertEqual(len({oid for _, oid in expected.values()}), count)
        self.head = git(self.repo, "rev-parse", "released-feature")
        git(self.repo, "--git-dir", str(self.admin), "read-tree", self.head)
        self.before_index = (self.admin / "index").read_bytes()
        self.index_sha = hashlib.sha256(self.before_index).hexdigest()
        digest = self.plan()
        children = {"metadata": 0, "content": 0}
        native = FINISH.safety().supervise
        def observed(command, **options):
            result = native(command, **options)
            if "cat-file" in command and command[-1] in ("--batch", "--batch-check"):
                children["content" if command[-1] == "--batch" else "metadata"] += 1
                self.assertIsNone(result.failure)
                self.assertTrue(result.proof["reaped"])
                self.assertTrue(result.proof["group_absent"])
                self.assertIn("--no-replace-objects", command)
                self.assertLessEqual(len(options["input_data"].splitlines()), 1024)
            return result
        started = time.monotonic()
        with mock.patch.object(FINISH.safety(), "supervise", side_effect=observed), \
                mock.patch.object(FINISH.os, "fsync", wraps=os.fsync) as synced:
            result = self.recover("--apply", "--recovery-sha256", digest)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 238)
        self.assertEqual(result["checkout"], "restored")
        self.assertEqual(result["generation"], 1)
        self.assertEqual(result["entries"], count)
        self.assertEqual(children, {"metadata": 130, "content": 66})
        self.assertGreaterEqual(synced.call_count, count)
        self.assertEqual((self.admin / "index").read_bytes(), self.before_index)
        self.assertEqual((self.admin / "locked").read_bytes(), self.before_lock)
        self.assertEqual((self.admin / "COMMIT_EDITMSG").read_bytes(), message)
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        for name, (mode, oid) in expected.items():
            path = self.wt / name
            self.assertEqual(path.lstat().st_uid, os.getuid())
            data = os.fsencode(os.readlink(path)) if mode == 0o120000 else path.read_bytes()
            if mode != 0o120000:
                self.assertEqual(stat_mode(path), mode & 0o777)
            self.assertEqual(hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest(), oid)
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            self.assertEqual(FINISH.owner_rows(db, row["id"]), [{"owner": "owner-1", "completed": 0}])
            FINISH.revalidate(row)
        print(json.dumps({"qualification": "large-recovery-apply", "entries": count,
                          "source_bytes": source_bytes, "largest_blob": largest,
                          "elapsed_seconds": round(elapsed, 3), "children": children,
                          "fsync_calls": synced.call_count, "generation": result["generation"]}, sort_keys=True))

    def test_crash_before_publish_retains_intent_and_requires_exact_reconciliation(self):
        digest = self.plan()
        with mock.patch.object(FINISH, "recovery_publish", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.recover("--apply", "--recovery-sha256", digest)
        record, retirement = self.record()
        self.assertEqual(retirement["state"], "recovery-pending")
        status = self.call("status")[1][0]["missing_recovery"]
        self.assertEqual(status["status"], "pending")
        self.assertEqual(status["intent_id"], record["id"])
        self.assertFalse(self.wt.exists())
        self.assertTrue(Path(record["stage"]).is_dir())
        with self.assertRaisesRegex(FINISH.Retain, "exact-intent-required"):
            self.recover("--apply", "--recovery-sha256", digest)
        with self.assertRaisesRegex(FINISH.Retain, "intent"):
            self.call("resume")
        result = self.recover("--apply", "--recovery-sha256", digest, "--intent-id", record["id"])
        self.assertEqual(result["checkout"], "restored")

    def test_crash_after_publish_reconciles_same_inode_once(self):
        digest = self.plan()
        publish = FINISH.recovery_publish
        def interrupted(source, target):
            publish(source, target)
            raise RuntimeError("interrupted after publication")
        with mock.patch.object(FINISH, "recovery_publish", side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, "interrupted after"):
                self.recover("--apply", "--recovery-sha256", digest)
        record, retirement = self.record()
        before = FINISH.identity(self.wt)
        self.assertEqual(retirement["generation"], 0)
        result = self.recover("--apply", "--recovery-sha256", digest, "--intent-id", record["id"])
        self.assertEqual(result["generation"], 1)
        self.assertEqual(FINISH.identity(self.wt), before)

    def test_post_publish_admin_drift_retains_pending_intent(self):
        digest = self.plan()
        publish = FINISH.recovery_publish
        def changed(source, target):
            publish(source, target)
            (self.admin / "locked").write_text("changed-owner\n")
        with mock.patch.object(FINISH, "recovery_publish", side_effect=changed):
            with self.assertRaisesRegex(FINISH.Retain, "registration-missing-or-locked"):
                self.recover("--apply", "--recovery-sha256", digest)
        self.assertEqual(self.record()[1]["state"], "recovery-pending")


def stat_mode(path):
    return path.stat().st_mode & 0o777


class MissingContainerRecoveryTests(unittest.TestCase):
    recovery_container = True
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    tearDown = ReleaseTests.tearDown
    recover = MissingRecoveryTests.recover
    plan = MissingRecoveryTests.plan
    record = MissingRecoveryTests.record

    def setUp(self):
        MissingRecoveryTests.setUp(self)
        self.container = self.wt.parent
        self.container.rmdir()
        self.acl = mock.patch.object(FINISH.safety(), "non_granting_acl", return_value=None)
        self.acl.start()
        self.addCleanup(self.acl.stop)

    def apply(self):
        return self.recover("--apply", "--recovery-sha256", self.plan())

    def test_plan_is_read_only_and_binds_existing_root(self):
        before = set(self.root.iterdir())
        result = self.recover()
        self.assertFalse(result["checkout_mutation"])
        self.assertEqual(result["container_layout"]["root_id"], FINISH.identity(self.root))
        self.assertEqual(result["container_layout"]["container"], "example-repo")
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertFalse(self.container.exists())
        self.assertIsNone(self.record()[0])

    def test_native_publish_preserves_source_and_generation_once(self):
        digest = self.plan()
        result = self.recover("--apply", "--recovery-sha256", digest)
        record, state = self.record()
        self.assertEqual(record["contract"], FINISH.RECOVERY_CONTAINER_CONTRACT)
        self.assertEqual(result["generation"], 1)
        self.assertEqual(state["generation"], 1)
        self.assertEqual((self.wt / "file").read_text(), "original\n")
        self.assertEqual((self.admin / "index").read_bytes(), self.before_index)
        self.assertEqual((self.admin / "locked").read_bytes(), self.before_lock)
        self.assertEqual(FINISH.identity(self.container), record["container_id"])
        self.assertFalse(Path(record["stage"]).parent.exists())
        sibling = self.container / "independent-task"
        sibling.mkdir()
        (sibling / "keep").write_text("other owner")
        replay = self.recover("--apply", "--recovery-sha256", digest, "--intent-id", result["intent_id"])
        self.assertEqual(replay, result)
        self.assertEqual(self.record()[1]["generation"], 1)
        self.assertEqual((sibling / "keep").read_text(), "other owner")

    def test_large_blob_partial_container_recovery_replays_once(self):
        self.apply()
        data = b"x" * 17_387_080
        (self.wt / "large").write_bytes(data)
        (self.wt / "large").chmod(0o644)
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-m", "large container recovery fixture")
        MissingRecoveryTests.disappear_again(self)
        self.container.rmdir()
        before_index = (self.admin / "index").read_bytes()
        digest = self.plan()
        native = FINISH.recovery_blobs

        def interrupted(*args, **kwargs):
            with contextlib.closing(native(*args, **kwargs)) as blobs:
                for name, mode, blob in blobs:
                    yield name, mode, blob
                    # Resume must verify the streamed large file and preserve
                    # its inode while publishing the same staged container.
                    if name == "large":
                        raise RuntimeError("after large blob")

        with mock.patch.object(FINISH, "recovery_blobs", side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, "after large blob"):
                self.recover("--apply", "--recovery-sha256", digest)
        record, state = self.record()
        self.assertEqual(record["contract"], FINISH.RECOVERY_CONTAINER_CONTRACT)
        self.assertEqual(state["generation"], 1)
        self.assertFalse(self.container.exists())
        stage = Path(record["stage"])
        before_stage = FINISH.identity(stage)
        before_container = FINISH.identity(stage.parent)
        before_file = (stage / "large").stat().st_ino
        self.assertEqual((stage / "large").read_bytes(), data)
        plan = self.recover("--continue-partial", "--intent-id", record["id"],
                            "--recovery-sha256", digest)
        self.assertTrue(plan["partial_continuation"])
        self.assertEqual(plan["recovery_sha256"], digest)
        result = self.recover("--continue-partial", "--apply", "--intent-id", record["id"],
                              "--recovery-sha256", digest)
        self.assertEqual(result["generation"], 2)
        self.assertEqual(FINISH.identity(self.wt), before_stage)
        self.assertEqual(FINISH.identity(self.container), before_container)
        self.assertEqual((self.wt / "large").stat().st_ino, before_file)
        self.assertEqual((self.wt / "large").read_bytes(), data)
        self.assertEqual(stat_mode(self.wt / "large"), 0o644)
        self.assertEqual((self.admin / "index").read_bytes(), before_index)
        completed = self.record()
        replay = self.recover("--apply", "--intent-id", record["id"],
                              "--recovery-sha256", digest)
        self.assertEqual(replay, result)
        self.assertEqual(self.record(), completed)
        self.assertEqual(completed[1]["generation"], 2)
        with FINISH.ledger(self.state) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM missing_recovery_history").fetchone()[0], 1)

    def test_native_fd_publication_collision_preserves_both_directories(self):
        source = self.root / "source"
        target = self.root / "target"
        source.mkdir()
        target.mkdir()
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with self.assertRaisesRegex(FINISH.Retain, "no-replace-refused"):
                FINISH.recovery_publish_at(fd, "source", "target")
        finally:
            os.close(fd)
        self.assertTrue(source.is_dir())
        self.assertTrue(target.is_dir())

    @unittest.skipUnless(sys.platform == "darwin", "Darwin access boundary fixture")
    def test_native_publish_with_real_access_boundaries(self):
        with mock.patch.object(FINISH.safety(), "access_boundary", self.boundary.temp_original), \
                mock.patch.object(FINISH.safety(), "non_granting_acl", self.acl.temp_original):
            self.assertEqual(self.apply()["checkout"], "restored")
        self.assertEqual(stat_mode(self.container), 0o700)
        self.assertEqual(stat_mode(self.wt), 0o700)

    def test_appeared_container_or_symlink_refuses_without_intent(self):
        digest = self.plan()
        for symlink in (False, True):
            if symlink:
                self.container.symlink_to(self.base, target_is_directory=True)
            else:
                self.container.mkdir()
            try:
                with self.assertRaises(FINISH.Retain):
                    self.recover("--apply", "--recovery-sha256", digest)
                self.assertIsNone(self.record()[0])
            finally:
                self.container.unlink() if symlink else self.container.rmdir()

    def test_root_replacement_invalidates_reviewed_digest(self):
        digest = self.plan()
        self.root.rename(self.base / "old-root")
        self.root.mkdir()
        with self.assertRaisesRegex(FINISH.Retain, "reviewed-preimage"):
            self.recover("--apply", "--recovery-sha256", digest)
        self.assertIsNone(self.record()[0])
        self.assertFalse(self.container.exists())

    def test_root_swap_at_mkdir_cannot_redirect_creation(self):
        mkdir = os.mkdir
        moved = self.base / "moved-root"
        def swap(name, *args, **kwargs):
            if str(name).startswith(".gwt-recover-"):
                self.root.rename(moved)
                mkdir(self.root, mode=0o700)
            return mkdir(name, *args, **kwargs)
        digest = self.plan()
        with mock.patch.object(os, "mkdir", side_effect=swap):
            with self.assertRaisesRegex(FINISH.Retain, "root-changed"):
                self.recover("--apply", "--recovery-sha256", digest)
        record, state = self.record()
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertTrue((moved / Path(record["stage"]).parent.name / self.wt.name).is_dir())
        self.assertEqual(state["generation"], 0)
        self.assertIsNone(record["result"])

    def test_root_swap_at_native_rename_cannot_redirect_publication(self):
        publish = FINISH.recovery_publish_at
        moved = self.base / "moved-root"
        def swap(fd, source, target):
            self.root.rename(moved)
            self.root.mkdir(mode=0o700)
            for name in (source, target):
                (self.root / name).mkdir(mode=0o700)
                (self.root / name / "keep").write_text(name)
            publish(fd, source, target)
        digest = self.plan()
        with mock.patch.object(FINISH, "recovery_publish_at", side_effect=swap):
            with self.assertRaisesRegex(FINISH.Retain, "root-changed"):
                self.recover("--apply", "--recovery-sha256", digest)
        record, state = self.record()
        for name in (Path(record["stage"]).parent.name, self.container.name):
            self.assertEqual((self.root / name / "keep").read_text(), name)
        self.assertEqual((moved / self.container.name / self.wt.name / "file").read_text(), "original\n")
        self.assertEqual(state["generation"], 0)

    def test_directory_receipts_follow_fsync_and_publication_syncs_root(self):
        fsync, create = os.fsync, FINISH.RecoveryContainer.create
        synced = []
        def sync(fd):
            fsync(fd)
            synced.append([os.fstat(fd).st_dev, os.fstat(fd).st_ino])
        def checked_create(instance, record, persist):
            def checked_persist():
                self.assertIn(instance.fact["root_id"], synced)
                self.assertIn(record["container_id"], synced)
                if record["stage_id"] is not None:
                    self.assertIn(record["stage_id"], synced)
                persist()
            create(instance, record, checked_persist)
        with mock.patch.object(os, "fsync", side_effect=sync), \
                mock.patch.object(FINISH.RecoveryContainer, "create", checked_create):
            self.apply()
        self.assertGreaterEqual(synced.count(FINISH.identity(self.root)), 2)

    def test_real_access_boundary_rejects_root_mode_drift(self):
        with mock.patch.object(FINISH.safety(), "access_boundary", self.boundary.temp_original):
            digest = self.plan()
            self.root.chmod(0o777)
            with self.assertRaisesRegex(FINISH.Retain, "access-boundary"):
                self.recover("--apply", "--recovery-sha256", digest)
        self.assertIsNone(self.record()[0])

    def test_missing_managed_root_never_creates_ancestors(self):
        self.legacy.rename(self.base / "legacy")
        self.root.rmdir()
        with self.assertRaises((FINISH.Retain, FileNotFoundError)):
            self.recover()
        self.assertFalse(self.root.exists())
        self.assertIsNone(self.record()[0])

    def test_deeper_missing_container_is_unsupported(self):
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            snap = json.loads(row["identity"])
        snap["path"] = str(self.container / "nested" / self.wt.name)
        with self.assertRaisesRegex(FINISH.Retain, "layout-unsupported"):
            FINISH.RecoveryContainer(snap, FINISH.safety().DEADLINE)
        self.assertFalse(self.container.exists())

    def interrupted(self):
        digest = self.plan()
        with mock.patch.object(FINISH, "recovery_tree", side_effect=RuntimeError("fixture interruption")):
            with self.assertRaises(RuntimeError):
                self.recover("--apply", "--recovery-sha256", digest)
        record, _ = self.record()
        return record, digest

    def test_partial_stage_requires_explicit_same_intent(self):
        record, digest = self.interrupted()
        self.assertFalse(self.container.exists())
        with self.assertRaisesRegex(FINISH.Retain, "incomplete-or-rebound"):
            self.recover("--apply", "--recovery-sha256", digest, "--intent-id", record["id"])
        result = self.recover("--continue-partial", "--apply", "--recovery-sha256", digest,
                              "--intent-id", record["id"])
        self.assertEqual(result["generation"], 1)

    @contextlib.contextmanager
    def native_self_holders(self):
        run = FINISH.run
        def fixture_process(command, **kwargs):
            if Path(command[0]).name == "lsof":
                # Real native cwd/FD records, restricted to this fixture process.
                command = [command[0], "-a", "-p", str(os.getpid()), *command[1:]]
            return run(command, **kwargs)
        with mock.patch.object(FINISH, "manual_holders", self.holders.temp_original), \
                mock.patch.object(FINISH, "run", side_effect=fixture_process):
            yield

    def assert_container_holder_refused(self, *, partial):
        digest = self.plan()
        with mock.patch.object(FINISH, "recovery_tree" if partial else "recovery_publish_at",
                               side_effect=RuntimeError("fixture interruption")):
            with self.assertRaises(RuntimeError):
                self.recover("--apply", "--recovery-sha256", digest)
        record, state = self.record()
        stage = Path(record["stage"]).parent
        before = sorted(str(path.relative_to(stage)) for path in stage.rglob("*"))
        for kind in ("cwd", "fd"):
            with self.subTest(kind=kind):
                cwd = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
                held = None
                try:
                    if kind == "cwd":
                        os.chdir(stage)
                    else:
                        held = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
                    with self.native_self_holders():
                        with self.assertRaisesRegex(FINISH.Retain, "pending-departure"):
                            self.recover(*(["--continue-partial"] if partial else []),
                                         "--apply", "--recovery-sha256", digest, "--intent-id", record["id"])
                finally:
                    if held is not None:
                        os.close(held)
                    os.fchdir(cwd)
                    os.close(cwd)
                self.assertEqual(self.record(), (record, state))
                self.assertEqual(sorted(str(path.relative_to(stage)) for path in stage.rglob("*")), before)
                self.assertFalse(self.container.exists())

    @unittest.skipUnless(shutil.which("lsof"), "native lsof unavailable")
    def test_container_cwd_and_fd_block_partial_write(self):
        self.assert_container_holder_refused(partial=True)

    @unittest.skipUnless(shutil.which("lsof"), "native lsof unavailable")
    def test_container_cwd_and_fd_block_same_intent_publication(self):
        self.assert_container_holder_refused(partial=False)

    @unittest.skipUnless(shutil.which("lsof"), "native lsof unavailable")
    def test_pinned_managed_root_fd_does_not_block_publication(self):
        with self.native_self_holders():
            self.assertEqual(self.apply()["checkout"], "restored")

    def test_container_inode_alias_blocks_partial_write(self):
        record, digest = self.interrupted()
        device, inode = record["container_id"]
        observed = types.SimpleNamespace(
            stdout=f"p7\0\nf3\0D0x{device:x}\0i{inode}\0n/fixture/alias\0\n".encode(), stderr=b"")
        run, which = FINISH.run, shutil.which
        def aliased(command, **kwargs):
            return observed if command[0] == "/fixture/lsof" else run(command, **kwargs)
        with mock.patch.object(FINISH, "manual_holders", self.holders.temp_original), \
                mock.patch.object(FINISH.shutil, "which", side_effect=lambda name:
                                  "/fixture/lsof" if name == "lsof" else which(name)), \
                mock.patch.object(FINISH, "run", side_effect=aliased):
            with self.assertRaisesRegex(FINISH.Retain, "pending-departure"):
                self.recover("--continue-partial", "--apply", "--recovery-sha256", digest,
                             "--intent-id", record["id"])
        self.assertEqual(self.record()[0], record)
        self.assertEqual(list(Path(record["stage"]).iterdir()), [])

    def test_extra_staged_child_is_preserved_and_refused(self):
        record, digest = self.interrupted()
        extra = Path(record["stage"]).parent / "other"
        extra.write_text("preserve")
        with self.assertRaisesRegex(FINISH.Retain, "unexpected-entry"):
            self.recover("--continue-partial", "--apply", "--recovery-sha256", digest,
                         "--intent-id", record["id"])
        self.assertEqual(extra.read_text(), "preserve")
        self.assertFalse(self.container.exists())

    def test_staged_container_symlink_is_preserved_and_refused(self):
        record, digest = self.interrupted()
        stage = Path(record["stage"]).parent
        saved = self.base / "saved-stage"
        stage.rename(saved)
        stage.symlink_to(saved, target_is_directory=True)
        with self.assertRaisesRegex(FINISH.Retain, "directory-unsafe"):
            self.recover("--continue-partial", "--apply", "--recovery-sha256", digest,
                         "--intent-id", record["id"])
        self.assertTrue(stage.is_symlink())
        self.assertTrue((saved / self.wt.name).is_dir())

    def test_staged_checkout_replacement_is_preserved_and_refused(self):
        record, digest = self.interrupted()
        stage = Path(record["stage"])
        saved = self.base / "saved-checkout"
        stage.rename(saved)
        stage.mkdir(mode=0o700)
        with self.assertRaisesRegex(FINISH.Retain, "directory-changed"):
            self.recover("--continue-partial", "--apply", "--recovery-sha256", digest,
                         "--intent-id", record["id"])
        self.assertTrue(stage.is_dir())
        self.assertTrue(saved.is_dir())
        self.assertEqual(self.record()[0], record)

    def replace_before_write(self, operation):
        materialize = FINISH.recovery_tree
        replaced = []
        saved = self.base / "recorded-stage"
        def swap(root, *args, **kwargs):
            if kwargs.get("write"):
                root = Path(root)
                root.rename(saved)
                root.mkdir(mode=0o700)
                replaced.append(root)
            return materialize(root, *args, **kwargs)
        with mock.patch.object(FINISH, "recovery_tree", side_effect=swap):
            with self.assertRaisesRegex(FINISH.Retain, "staging-rebound"):
                operation()
        self.assertEqual(len(replaced), 1)
        self.assertEqual(list(replaced[0].iterdir()), [])
        self.assertTrue(saved.is_dir())
        self.assertFalse(self.container.exists())
        self.assertEqual(self.record()[1]["generation"], 0)

    def test_replaced_stage_before_initial_write_is_untouched(self):
        digest = self.plan()
        self.replace_before_write(lambda: self.recover("--apply", "--recovery-sha256", digest))

    def test_replaced_stage_before_partial_write_is_untouched(self):
        record, digest = self.interrupted()
        self.replace_before_write(lambda: self.recover(
            "--continue-partial", "--apply", "--recovery-sha256", digest, "--intent-id", record["id"]))

    @unittest.skipUnless(sys.platform == "darwin", "Darwin ACL fixture")
    def test_real_staged_acl_grant_refuses_continuation(self):
        record, digest = self.interrupted()
        stage = Path(record["stage"]).parent
        subprocess.run(["/bin/chmod", "+a", "everyone allow readattr", str(stage)], check=True)
        try:
            with mock.patch.object(FINISH.safety(), "non_granting_acl", self.acl.temp_original):
                with self.assertRaisesRegex(FINISH.Retain, "grants"):
                    self.recover("--continue-partial", "--apply", "--recovery-sha256", digest,
                                 "--intent-id", record["id"])
        finally:
            subprocess.run(["/bin/chmod", "-N", str(stage)], check=True)
        self.assertEqual(self.record()[0], record)

    def test_mkdir_before_inode_receipt_remains_unknown(self):
        original = FINISH.RecoveryContainer.directory
        def interrupted_directory(instance, name, parent_fd, expected=None):
            if name.startswith(".gwt-recover-"):
                raise RuntimeError("before inode persistence")
            return original(instance, name, parent_fd, expected)
        digest = self.plan()
        with mock.patch.object(FINISH.RecoveryContainer, "directory", interrupted_directory):
            with self.assertRaises(RuntimeError):
                self.recover("--apply", "--recovery-sha256", digest)
        record, _ = self.record()
        stage = Path(record["stage"]).parent
        self.assertTrue(stage.is_dir())
        self.assertIsNone(record["container_id"])
        with self.assertRaisesRegex(FINISH.Retain, "inode-unknown"):
            self.recover("--continue-partial", "--apply", "--recovery-sha256", digest,
                         "--intent-id", record["id"])
        self.assertTrue(stage.is_dir())

    def test_checkout_mkdir_before_inode_receipt_remains_unknown(self):
        original = FINISH.RecoveryContainer.directory
        def interrupted_directory(instance, name, parent_fd, expected=None):
            if name == self.wt.name:
                raise RuntimeError("before checkout inode persistence")
            return original(instance, name, parent_fd, expected)
        digest = self.plan()
        with mock.patch.object(FINISH.RecoveryContainer, "directory", interrupted_directory):
            with self.assertRaises(RuntimeError):
                self.recover("--apply", "--recovery-sha256", digest)
        record, _ = self.record()
        self.assertIsNotNone(record["container_id"])
        self.assertIsNone(record["stage_id"])
        self.assertTrue(Path(record["stage"]).is_dir())
        with self.assertRaisesRegex(FINISH.Retain, "inode-unknown"):
            self.recover("--continue-partial", "--apply", "--recovery-sha256", digest,
                         "--intent-id", record["id"])

    def test_post_publish_crash_replays_same_inode_and_history(self):
        publish = FINISH.recovery_publish_at
        def interrupted(*args):
            publish(*args)
            raise RuntimeError("after publication")
        digest = self.plan()
        with mock.patch.object(FINISH, "recovery_publish_at", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.recover("--apply", "--recovery-sha256", digest)
        record, state = self.record()
        self.assertEqual(state["generation"], 0)
        before = FINISH.identity(self.wt)
        result = self.recover("--apply", "--recovery-sha256", digest, "--intent-id", record["id"])
        self.assertEqual(result["generation"], 1)
        self.assertEqual(FINISH.identity(self.wt), before)
        # A subsequent disappearance archives the exact v3 receipt and uses v2
        # when the shared container now exists.
        self.generation = 1
        shutil.rmtree(self.wt)
        second = self.apply()
        self.assertEqual(second["generation"], 2)
        self.assertEqual(self.record()[0]["contract"], FINISH.RECOVERY_CONTRACT)
        with FINISH.ledger(self.state) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM missing_recovery_history").fetchone()[0], 1)

    def test_post_publish_admin_drift_keeps_pending_intent(self):
        publish = FINISH.recovery_publish_at
        def changed(*args):
            publish(*args)
            (self.admin / "COMMIT_EDITMSG").write_text("changed after publication\n")
        digest = self.plan()
        with mock.patch.object(FINISH, "recovery_publish_at", side_effect=changed):
            with self.assertRaisesRegex(FINISH.Retain, "commit-message-recovery-required"):
                self.recover("--apply", "--recovery-sha256", digest)
        record, state = self.record()
        self.assertTrue(self.wt.is_dir())
        self.assertIsNone(record["result"])
        self.assertEqual(state["state"], "recovery-pending")
        self.assertEqual(state["generation"], 0)
        self.assertEqual((self.admin / "COMMIT_EDITMSG").read_text(), "changed after publication\n")


class RecoveryBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve()
        git(self.repo, "init", "-b", "main")
        self.stage = self.repo / "stage"
        self.stage.mkdir(mode=0o700)
        self.until = time.monotonic() + 238
        self.deadline = mock.patch.object(FINISH.safety(), "DEADLINE", self.until)
        self.deadline.start()
        self.addCleanup(self.deadline.stop)

    def blob(self, data):
        return subprocess.run(["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"],
                              input=data, stdout=subprocess.PIPE, check=True).stdout.decode().strip()

    def write(self, entries, **options):
        return FINISH.recovery_tree(self.stage, entries, str(self.repo / ".git"), self.until,
                                    write=True, owner=str(self.repo), **options)

    def test_stage_replacement_before_open_is_never_written(self):
        expected = FINISH.identity(self.stage)
        opened = os.open
        saved = self.repo / "recorded-stage"
        def swap(path, *args, **kwargs):
            if Path(path) == self.stage:
                self.stage.rename(saved)
                self.stage.mkdir(mode=0o700)
            return opened(path, *args, **kwargs)
        with mock.patch.object(os, "open", side_effect=swap):
            with self.assertRaisesRegex(FINISH.Retain, "staging-rebound"):
                self.write({}, expected_id=expected)
        self.assertEqual(list(saved.iterdir()), [])
        self.assertEqual(list(self.stage.iterdir()), [])

    def test_binary_duplicate_executable_and_symlink_blobs_are_raw(self):
        data = b"\0line\n" + b"f" * 40 + b" blob 0\n\n"
        oid, target = self.blob(data), self.blob(b"../outside\nname")
        entries = {"first": (0o100644, oid), "nested/exec": (0o100755, oid),
                   "link": (0o120000, target)}
        self.write(entries)
        self.assertEqual((self.stage / "first").read_bytes(), data)
        self.assertEqual((self.stage / "nested/exec").read_bytes(), data)
        self.assertEqual(stat_mode(self.stage / "nested/exec"), 0o755)
        self.assertEqual(os.readlink(self.stage / "link"), "../outside\nname")

    def test_large_regular_and_executable_blobs_stream_and_verify(self):
        data = b"x" * 17_387_080
        oid = self.blob(data)
        entries = {"regular": (0o100644, oid), "executable": (0o100755, oid)}
        native = FINISH.safety().supervise
        results = []
        def supervise(*args, **kwargs):
            result = native(*args, **kwargs)
            if kwargs.get("stdout_sink") is not None:
                results.append(result)
            return result
        with mock.patch.object(FINISH.safety(), "supervise", side_effect=supervise):
            self.write(entries)
            FINISH.recovery_tree(self.stage, entries, str(self.repo / ".git"), self.until)
        self.assertEqual((self.stage / "regular").read_bytes(), data)
        self.assertEqual((self.stage / "executable").read_bytes(), data)
        self.assertEqual(stat_mode(self.stage / "executable"), 0o755)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.stdout == b"" and result.stdout_bytes > len(data) for result in results))

    def test_large_symlink_still_refuses_before_content_read(self):
        oid = self.blob(b"x" * (FINISH.RECOVERY_BLOB_LIMIT + 1))
        with mock.patch.object(FINISH, "git", wraps=FINISH.git) as calls:
            with self.assertRaisesRegex(FINISH.Retain, "recovery-blob-size-limit"):
                self.write({"link": (0o120000, oid)})
        self.assertFalse(any(call.args[-1] == "--batch" for call in calls.call_args_list))
        self.assertEqual(list(self.stage.iterdir()), [])

    def test_1024_and_1025_empty_requests_remain_count_bounded(self):
        oid = self.blob(b"")
        for count in (1024, 1025):
            entries = [(str(i), (0o100644, oid)) for i in range(count)]
            with self.subTest(count=count), mock.patch.object(FINISH, "git", wraps=FINISH.git) as calls:
                actual = [(name, mode, b"".join(FINISH.recovery_blob_chunks(blob, self.until)))
                          for name, mode, blob in FINISH.recovery_blobs(self.repo, entries, self.until, 0)]
            self.assertEqual(len(actual), count)
            self.assertTrue(all(data == b"" for _, _, data in actual))
            self.assertEqual(len(calls.call_args_list), 2 if count == 1024 else 4)
            for call in calls.call_args_list:
                self.assertEqual(call.args[1:3], ("--no-replace-objects", "cat-file"))
                self.assertLessEqual(len(call.kwargs["input_data"].splitlines()), 1024)

    def test_whole_content_batch_is_validated_before_first_write(self):
        first, second = self.blob(b"first"), self.blob(b"second")
        entries = {"first": (0o100644, first), "second": (0o100644, second)}
        native = FINISH.git
        def corrupt(change):
            def invoke(path, *args, **kwargs):
                sink = kwargs.pop("stdout_sink", None)
                raw = native(path, *args, **kwargs)
                if args[-1] == "--batch":
                    sink.write(change(raw))
                    return b""
                return raw
            return invoke
        header = second.encode() + b" blob 6\n"
        for change in (lambda raw: raw.replace(header, first.encode() + b" blob 6\n"),
                       lambda raw: raw.replace(header, second.encode() + b" tree 6\n"),
                       lambda raw: raw.replace(header, second.encode() + b" blob 06\n"),
                       lambda raw: raw.replace(header, second.encode() + b" blob 5\n"),
                       lambda raw: raw.replace(b"second", b"broken"),
                       lambda raw: raw[:-1] + b"X", lambda raw: raw[:-3],
                       lambda raw: raw + b"extra\n"):
            with self.subTest(change=change), mock.patch.object(FINISH, "git", side_effect=corrupt(change)):
                with self.assertRaises(FINISH.Retain):
                    self.write(entries)
            self.assertEqual(list(self.stage.iterdir()), [])

    def test_metadata_requires_exact_order_type_size_count_and_eof(self):
        oid = self.blob(b"x")
        native = FINISH.git
        for bad in (b"", oid.encode() + b" missing\n", oid.encode() + b" tree 1\n",
                    b"f" * 40 + b" blob 1\n", oid.encode() + b" blob 01\n",
                    oid.encode() + b" blob -1\n", oid.encode() + b" blob 1",
                    oid.encode() + b" blob 1\nextra\n"):
            def invoke(path, *args, **kwargs):
                return bad if args[-1] == "--batch-check" else native(path, *args, **kwargs)
            with self.subTest(bad=bad), mock.patch.object(FINISH, "git", side_effect=invoke):
                with self.assertRaisesRegex(FINISH.Retain, "recovery-batch-metadata-invalid"):
                    self.write({"first": (0o100644, oid)})
            self.assertEqual(list(self.stage.iterdir()), [])

    def test_source_budget_counts_duplicates_partial_bytes_and_git_pointer(self):
        oid = self.blob(b"abc")
        entries = {name: (0o100644, oid) for name in ("existing", "first", "second")}
        (self.stage / "existing").write_bytes(b"abc")
        (self.stage / "existing").chmod(0o644)
        before = (self.stage / "existing").stat().st_ino
        pointer = len(os.fsencode("gitdir: " + str(self.repo / ".git") + "\n"))
        with mock.patch.object(FINISH.safety(), "SOURCE_LIMIT", pointer + 8), \
                mock.patch.object(FINISH, "git", wraps=FINISH.git) as calls:
            with self.assertRaisesRegex(FINISH.Retain, "recovery-source-byte-limit"):
                self.write(entries, allow_partial=True)
        self.assertFalse(any(call.args[-1] == "--batch" for call in calls.call_args_list))
        self.assertEqual(list(self.stage.iterdir()), [self.stage / "existing"])
        self.assertEqual((self.stage / "existing").stat().st_ino, before)
        with mock.patch.object(FINISH.safety(), "SOURCE_LIMIT", pointer + 9), \
                mock.patch.object(FINISH.safety(), "PASS_LIMIT", 1):
            self.write(entries, allow_partial=True)
        self.assertEqual((self.stage / "existing").stat().st_ino, before)
        with mock.patch.object(FINISH.safety(), "SOURCE_LIMIT", pointer + 8):
            with self.assertRaisesRegex(FINISH.Retain, "recovery-source-byte-limit"):
                FINISH.recovery_tree(self.stage, entries, str(self.repo / ".git"), self.until)
        with mock.patch.object(FINISH.safety(), "SOURCE_LIMIT", pointer + 9), \
                mock.patch.object(FINISH.safety(), "PASS_LIMIT", 1):
            self.write(entries, allow_partial=True)
        self.assertEqual((self.stage / "existing").stat().st_ino, before)

    def test_replacements_do_not_change_metadata_or_content(self):
        oid, replaced = self.blob(b"original"), self.blob(b"replacement")
        git(self.repo, "update-ref", "refs/replace/" + oid, replaced)
        self.write({"file": (0o100644, oid)})
        self.assertEqual((self.stage / "file").read_bytes(), b"original")

    def test_exit_zero_with_stderr_still_refuses_before_write(self):
        oid = self.blob(b"x")
        run = FINISH.run
        def diagnostic(_command, **options):
            return run([sys.executable, "-I", "-c",
                        "import sys; sys.stdin.buffer.read(); sys.stderr.write('private-secret')"], **options)
        with mock.patch.object(FINISH, "run", side_effect=diagnostic):
            with self.assertRaisesRegex(FINISH.Retain, "git-diagnostic"):
                self.write({"file": (0o100644, oid)})
        self.assertEqual(list(self.stage.iterdir()), [])

    def test_large_corrupt_or_truncated_spool_never_materializes(self):
        oid = self.blob(b"x" * 17_387_080)
        native = FINISH.git
        streams = []
        def invoke(path, *args, **kwargs):
            result = native(path, *args, **kwargs)
            stream = kwargs.get("stdout_sink")
            if stream is not None:
                streams.append(stream)
                if corrupt:
                    stream.seek(128)
                    stream.write(b"!")
                else:
                    stream.truncate(stream.tell() - 1)
            return result
        for corrupt in (True, False):
            with self.subTest(corrupt=corrupt), mock.patch.object(FINISH, "git", side_effect=invoke):
                with self.assertRaises(FINISH.Retain):
                    self.write({"large": (0o100644, oid)})
            self.assertEqual(list(self.stage.iterdir()), [])
        self.assertTrue(all(stream.closed for stream in streams))

    def test_copy_failure_closes_spool_without_writing_pointer(self):
        oid = self.blob(b"source")
        native, streams = FINISH.tempfile.TemporaryFile, []
        def temporary(*args, **kwargs):
            stream = native(*args, **kwargs)
            streams.append(stream)
            return stream
        with mock.patch.object(FINISH.tempfile, "TemporaryFile", side_effect=temporary), \
                mock.patch.object(FINISH, "recovery_parent", side_effect=OSError("fixture copy")):
            with self.assertRaisesRegex(OSError, "fixture copy"):
                self.write({"file": (0o100644, oid)})
        self.assertTrue(streams and all(stream.closed for stream in streams))
        self.assertEqual(list(self.stage.iterdir()), [])


class SiblingAbsenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.parent = self.base / "parent"
        self.parent.mkdir()
        self.path = self.parent / "missing" / ".git"
        FINISH.safety()

    def capture(self):
        return FINISH.sibling_absence(self.path, time.monotonic() + 10)

    def metadata(self, details, **changes):
        fields = {name: getattr(details, name, 0) for name in (
            "st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_flags")}
        return types.SimpleNamespace(**{**fields, **changes})

    def test_real_root_owned_temporary_parent_is_read_only(self):
        parent = Path("/tmp").resolve()
        before = parent.lstat()
        if os.getuid() == 0 or before.st_uid != 0 or not before.st_mode & FINISH.stat.S_ISVTX:
            self.skipTest("non-owner observation of root-owned sticky temporary directory unavailable")
        self.path = parent / ("gwt-absence-fixture-" + uuid.uuid4().hex) / ".git"
        proof = self.capture()
        self.assertEqual(proof["parent"], str(parent))
        self.assertEqual(proof["parent_id"], [before.st_dev, before.st_ino])
        self.assertEqual(proof["parent_metadata"], [before.st_uid, before.st_gid,
                                                   before.st_mode, getattr(before, "st_flags", 0)])
        self.assertFalse(os.path.lexists(self.path.parent))
        if os.getuid() != 0:
            with self.assertRaisesRegex(FINISH.Retain, "directory-identity-unsafe"):
                FINISH.identity(parent)

    def test_same_user_parent_keeps_existing_receipt_shape(self):
        details = self.parent.lstat()
        self.assertEqual(self.capture(), {"state": "absent", "errno": errno.ENOENT,
                                         "parent": str(self.parent),
                                         "parent_id": [details.st_dev, details.st_ino]})

    def test_root_modes_and_foreign_owners_have_separate_admission(self):
        native_lstat, native_fstat = os.lstat, os.fstat
        for uid, mode, accepted in ((0, 0o755, True), (0, 0o1777, True),
                                    (0, 0o777, False), (0, 0o775, False),
                                    (os.getuid() + 1000, 0o755, False)):
            with self.subTest(uid=uid, mode=mode):
                def named(path):
                    details = native_lstat(path)
                    return self.metadata(details, st_uid=uid, st_mode=FINISH.stat.S_IFDIR | mode) \
                        if Path(path) == self.parent else details
                def held(fd):
                    return self.metadata(native_fstat(fd), st_uid=uid, st_mode=FINISH.stat.S_IFDIR | mode)
                with mock.patch.object(FINISH.os, "getuid", return_value=501), \
                        mock.patch.object(FINISH.os, "lstat", side_effect=named), \
                        mock.patch.object(Path, "lstat", named), \
                        mock.patch.object(FINISH.os, "fstat", side_effect=held):
                    if accepted:
                        self.assertEqual(self.capture()["parent_metadata"][0], uid)
                    else:
                        with self.assertRaisesRegex(FINISH.Retain, "directory-identity-unsafe"):
                            self.capture()

    def test_parent_metadata_drift_is_not_preservation(self):
        native = os.fstat
        before = self.parent.lstat()
        for field, value in (("st_dev", before.st_dev + 1), ("st_ino", before.st_ino + 1),
                             ("st_uid", os.getuid() + 1000), ("st_gid", before.st_gid + 1),
                             ("st_mode", before.st_mode ^ 0o100),
                             ("st_flags", getattr(before, "st_flags", 0) ^ 1)):
            with self.subTest(field=field), mock.patch.object(
                    FINISH.os, "fstat", side_effect=lambda fd: self.metadata(native(fd), **{field: value})):
                with self.assertRaises(FINISH.Retain):
                    self.capture()

    def test_unrelated_directory_churn_does_not_change_absence(self):
        before = self.capture()
        native = os.stat
        def churn(path, *args, **kwargs):
            try:
                return native(path, *args, **kwargs)
            except FileNotFoundError:
                if "dir_fd" in kwargs and not (self.parent / "other").exists():
                    (self.parent / "other").mkdir()
                raise
        with mock.patch.object(FINISH.os, "stat", side_effect=churn):
            self.assertEqual(self.capture(), before)

    def test_symlink_parent_and_non_directory_prefix_refuse(self):
        link = self.base / "link"
        link.symlink_to(self.parent, target_is_directory=True)
        self.path = link / "missing"
        with self.assertRaisesRegex(FINISH.Retain, "sibling-path-rebound"):
            self.capture()
        file = self.base / "file"
        file.write_text("untouched\n")
        self.path = file / "missing"
        with self.assertRaises(NotADirectoryError):
            self.capture()
        self.assertEqual(file.read_text(), "untouched\n")

    def test_unknown_read_errors_are_not_enoent(self):
        for error in (PermissionError(errno.EACCES, "fixture"),
                      NotADirectoryError(errno.ENOTDIR, "fixture"), OSError(errno.EIO, "fixture")):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(FINISH.os, "lstat", side_effect=error):
                    with self.assertRaises(type(error)):
                        self.capture()
                native = os.stat
                def failed(path, *args, **kwargs):
                    if "dir_fd" in kwargs:
                        raise error
                    return native(path, *args, **kwargs)
                with mock.patch.object(FINISH.os, "stat", side_effect=failed):
                    with self.assertRaises(type(error)):
                        self.capture()

    def test_parent_replaced_during_open_refuses_and_closes_descriptor(self):
        native_open, native_fstat = os.open, os.fstat
        opened = []
        def replace(path, flags, *args, **kwargs):
            if Path(path) == self.parent:
                self.parent.rename(self.base / "old-parent")
                self.parent.mkdir()
            fd = native_open(path, flags, *args, **kwargs)
            opened.append(fd)
            return fd
        with mock.patch.object(FINISH.os, "open", side_effect=replace):
            with self.assertRaisesRegex(FINISH.Retain, "sibling-parent-rebound"):
                self.capture()
        self.assertEqual(len(opened), 1)
        with self.assertRaises(OSError) as error:
            native_fstat(opened[0])
        self.assertEqual(error.exception.errno, errno.EBADF)

    def test_missing_component_reappearance_refuses_without_following_links(self):
        native = os.stat
        for kind in ("directory", "file", "symlink"):
            with self.subTest(kind=kind):
                def appear(path, *args, **kwargs):
                    try:
                        return native(path, *args, **kwargs)
                    except FileNotFoundError:
                        if "dir_fd" in kwargs:
                            if kind == "directory":
                                self.path.parent.mkdir()
                            elif kind == "file":
                                self.path.parent.write_text("keep\n")
                            else:
                                self.path.parent.symlink_to(self.base / "unavailable")
                        raise
                with mock.patch.object(FINISH.os, "stat", side_effect=appear):
                    with self.assertRaisesRegex(FINISH.Retain, "sibling-absence-changed"):
                        self.capture()
                if kind == "directory":
                    self.path.parent.rmdir()
                else:
                    self.path.parent.unlink()


class FinalizedRemovalTests(unittest.TestCase):
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    finish = ReleaseTests.finish
    assert_release_blocked = ReleaseTests.assert_release_blocked
    setUp = ReleaseTests.setUp
    tearDown = ReleaseTests.tearDown
    fast_forwarded_task = ReleaseTests.fast_forwarded_task
    message_proof_roots = ReleaseTests.message_proof_roots
    large_target_reflog = ReleaseTests.large_target_reflog

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

    def commit_crlf_fixture(self, rule="*.cmd text eol=crlf"):
        path = self.wt / "fixture.cmd"
        (self.wt / ".gitattributes").write_text(rule + "\n")
        path.write_bytes(b"left\r\nright\r\nlast")
        git(self.wt, "add", ".gitattributes", path.name)
        git(self.wt, "commit", "-m", "synthetic Windows text")
        return path

    def test_explicit_crlf_removal_preserves_the_committed_source(self):
        path = self.commit_crlf_fixture()
        head = git(self.wt, "rev-parse", "HEAD")
        result = self.remove()[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.admin.exists())
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), head)
        self.assertEqual(git(self.repo, "show", head + ":" + path.name), "left\nright\nlast")

    def test_crlf_mixed_endings_are_preserved_even_when_git_reports_clean(self):
        path = self.commit_crlf_fixture()
        data = b"left\nright\r\nlast"
        path.write_bytes(data)
        # Git stages the same canonical LF blob and records the new stat data.
        git(self.wt, "add", path.name)
        self.assertEqual(git(self.wt, "status", "--porcelain"), "")
        with self.assertRaisesRegex(FINISH.Retain, "working-bytes-changed-or-limit"):
            self.remove()
        self.assertEqual(path.read_bytes(), data)
        self.assertTrue((self.admin / "locked").exists())

    def test_info_attributes_cannot_authorize_crlf_projection(self):
        path = self.commit_crlf_fixture("*.cmd text eol=lf")
        (self.repo / ".git/info/attributes").write_text("*.cmd text eol=crlf\n")
        self.assertEqual(git(self.wt, "status", "--porcelain"), "")
        with self.assertRaisesRegex(FINISH.Retain, "working-bytes-changed-or-limit"):
            self.remove()
        self.assertEqual(path.read_bytes(), b"left\r\nright\r\nlast")
        self.assertTrue((self.admin / "locked").exists())

    def test_info_attributes_can_veto_committed_crlf(self):
        path = self.commit_crlf_fixture()
        for rule in ("eol=lf", "filter=example", "working-tree-encoding=UTF-8"):
            with self.subTest(rule=rule):
                (self.repo / ".git/info/attributes").write_text("*.cmd " + rule + "\n")
                with self.assertRaisesRegex(FINISH.Retain, "working-bytes-changed-or-limit"):
                    self.remove()
                self.assertEqual(path.read_bytes(), b"left\r\nright\r\nlast")

    def test_nested_committed_rule_can_veto_crlf(self):
        path = self.commit_crlf_fixture()
        nested = self.wt / "nested"
        nested.mkdir()
        path = path.rename(nested / path.name)
        (nested / ".gitattributes").write_text("*.cmd eol=lf\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-m", "nested fixture policy")
        with self.assertRaisesRegex(FINISH.Retain, "working-bytes-changed-or-limit"):
            self.remove()
        self.assertEqual(path.read_bytes(), b"left\r\nright\r\nlast")

    def test_crlf_rule_change_between_admissions_preserves_checkout(self):
        path = self.commit_crlf_fixture()
        admit = FINISH.finalized_admission
        def change_rule(*args, **kwargs):
            result = admit(*args, **kwargs)
            (self.repo / ".git/info/attributes").write_text("*.cmd eol=lf\n")
            return result
        with mock.patch.object(FINISH, "finalized_admission", side_effect=change_rule):
            with self.assertRaisesRegex(FINISH.Retain, "working-bytes-changed-or-limit"):
                self.remove()
        self.assertTrue((self.admin / "locked").exists())
        self.assertEqual(path.read_bytes(), b"left\r\nright\r\nlast")

    def test_raw_lf_blob_still_uses_exact_byte_verification(self):
        path = self.commit_crlf_fixture()
        path.write_bytes(b"left\nright\nlast")
        git(self.wt, "add", path.name)
        with mock.patch.object(FINISH.safety(), "verify_committed_crlf") as projection:
            self.assertEqual(self.remove()[1][0]["checkout"], "removed")
        projection.assert_not_called()

    def test_crlf_with_other_committed_conversions_retains(self):
        for attr in ("ident", "filter=example", "working-tree-encoding=UTF-8", "crlf=input"):
            with self.subTest(attr=attr):
                path = self.commit_crlf_fixture("*.cmd text eol=crlf " + attr)
                with self.assertRaisesRegex(FINISH.Retain, "working-bytes-changed-or-limit"):
                    self.remove()
                self.assertEqual(path.read_bytes(), b"left\r\nright\r\nlast")

    def test_crlf_with_a_content_edit_retains(self):
        path = self.commit_crlf_fixture()
        path.write_bytes(b"edit\r\nright\r\nlast")
        with self.assertRaisesRegex(FINISH.Retain, "dirty-or-untracked-worktree"):
            self.remove()
        self.assertEqual(path.read_bytes(), b"edit\r\nright\r\nlast")

    def test_fast_forwarded_committed_message_allows_finalized_removal(self):
        authored, _ = self.fast_forwarded_task()
        self.artifacts()
        donor = self.base / "dependency-donor"
        donor.mkdir()
        (donor / "keep").write_text("shared dependency\n")
        (self.wt / "node_modules").symlink_to(donor, target_is_directory=True)
        result = self.remove(".crabbox", "node_modules")[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.admin.exists())
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertEqual(git(self.repo, "show", "-s", "--format=%B", authored), "authored message")
        self.assertTrue(self.legacy.exists())
        self.assertEqual((donor / "keep").read_text(), "shared dependency\n")

    def test_large_target_reflog_allows_finalized_message_proof(self):
        self.large_target_reflog()
        self.artifacts()
        self.assertEqual(self.finish()[1][0]["checkout"], "retained")
        result = self.remove(".crabbox")[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.admin.exists())
        self.assertTrue(self.legacy.exists())
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)

    def test_fast_forwarded_divergent_draft_is_preserved(self):
        _, message = self.fast_forwarded_task()
        draft = message + b"uncommitted draft\n"
        (self.admin / "COMMIT_EDITMSG").write_bytes(draft)
        with self.assertRaisesRegex(FINISH.Retain, "commit-message-recovery-required"):
            self.remove()
        self.assertEqual((self.admin / "COMMIT_EDITMSG").read_bytes(), draft)
        self.assertTrue((self.admin / "locked").exists())

    def test_reflog_blob_cannot_attest_saved_draft(self):
        draft = b"uncommitted draft\n"
        payload = self.base / "draft-blob"
        payload.write_bytes(draft + b"\0")
        blob = git(self.repo, "hash-object", "-w", str(payload))
        saved = self.admin / "COMMIT_EDITMSG"
        saved.write_bytes(draft)
        reflog = self.repo / ".git/logs/refs/heads/released-feature"
        with reflog.open("ab") as stream:
            stream.write((self.head + " " + blob
                          + " Fixture <fixture@example.test> 1 +0000\tfixture\n").encode())
        preserved = reflog.read_bytes()
        with mock.patch.object(FINISH.safety(), "supervise", wraps=FINISH.safety().supervise) as children:
            with self.assertRaisesRegex(FINISH.Retain, "commit-message-proof-invalid"):
                self.remove()
        self.assertFalse(any("worktree" in call.args[0] and ("unlock" in call.args[0] or "remove" in call.args[0])
                             for call in children.call_args_list))
        self.assertEqual(saved.read_bytes(), draft)
        self.assertEqual(reflog.read_bytes(), preserved)
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertTrue(self.wt.exists())
        self.assertTrue((self.admin / "locked").exists())

    def test_committed_message_drift_between_admissions_keeps_native_lock(self):
        _, message = self.fast_forwarded_task()
        admit = FINISH.finalized_admission
        calls = []
        draft = message + b"later draft\n"

        def change_after_admission(*args, **kwargs):
            result = admit(*args, **kwargs)
            calls.append(None)
            if len(calls) == 1:
                (self.admin / "COMMIT_EDITMSG").write_bytes(draft)
            return result

        with mock.patch.object(FINISH, "finalized_admission", side_effect=change_after_admission), \
                mock.patch.object(FINISH.safety(), "supervise", wraps=FINISH.safety().supervise) as children:
            with self.assertRaisesRegex(FINISH.Retain, "commit-message-recovery-required"):
                self.remove()
        self.assertFalse(any("worktree" in call.args[0] and ("unlock" in call.args[0] or "remove" in call.args[0])
                             for call in children.call_args_list))
        self.assertEqual((self.admin / "COMMIT_EDITMSG").read_bytes(), draft)
        self.assertTrue((self.admin / "locked").exists())

    def gitlinks(self):
        oid = "1" * 40
        names = ("vendor/empty", "vendor/missing", "absent/leaf")
        (self.wt / ".gitmodules").write_text("".join(
            f'[submodule "{name}"]\n\tpath = {name}\n\turl = https://github.com/example/submodule.git\n'
            for name in names))
        git(self.wt, "add", ".gitmodules")
        for name in names:
            git(self.wt, "update-index", "--add", "--cacheinfo", f"160000,{oid},{name}")
        git(self.wt, "commit", "-m", "unpopulated gitlinks")
        (self.wt / "vendor/empty").mkdir(parents=True)
        git(self.wt, "update-index", "--skip-worktree", "vendor/missing", "absent/leaf")
        with self.assertRaises(subprocess.CalledProcessError):
            git(self.wt, "cat-file", "-e", oid)
        return oid

    def inventory(self, *roots, compact=True, until=None, allow_empty_gitlinks=False):
        with FINISH.ledger(self.state) as db:
            snap = FINISH.revalidate(FINISH.get_row(db, self.wt))
        return FINISH.safety().inventory(
            snap, FINISH.git, time.monotonic() + 120 if until is None else until,
            discard_ignored=roots, compact_discarded=compact, allow_empty_gitlinks=allow_empty_gitlinks)

    def test_empty_gitlinks_are_manual_only_and_existing_directories_have_holder_proof(self):
        self.gitlinks()
        self.head = git(self.wt, "rev-parse", "HEAD")
        self.proofs[self.url] = self.proof(self.url)
        self.assert_release_blocked("raw-index-flags-or-mode-unsupported")
        contents = self.inventory(allow_empty_gitlinks=True)
        self.assertEqual(contents["index_sha256"], hashlib.sha256((self.admin / "index").read_bytes()).hexdigest())
        self.assertEqual({name: proof["state"] for name, proof in contents["gitlinks"].items()},
                         {"vendor/empty": "empty", "vendor/missing": "absent", "absent/leaf": "absent"})
        self.assertIn("vendor/empty", contents["entries"])
        self.assertNotIn("vendor/missing", contents["entries"])
        device, inode = contents["entries"]["vendor/empty"][:2]
        observed = types.SimpleNamespace(
            stdout=f"p7\0\nf3\0D0x{device:x}\0i{inode}\0n/other/alias\0\n".encode(), stderr=b"")
        with FINISH.ledger(self.state) as db:
            snap = FINISH.revalidate(FINISH.get_row(db, self.wt))
        with mock.patch.object(FINISH.shutil, "which", return_value="/fixture/lsof"), \
                mock.patch.object(FINISH, "run", return_value=observed):
            with self.assertRaisesRegex(FINISH.Retain, "pending-departure"):
                FINISH.manual_holders(snap, contents, {}, time.monotonic() + 10)

    def test_empty_gitlinks_reject_populated_linked_admin_and_mismatched_state(self):
        oid = self.gitlinks()
        empty = self.wt / "vendor/empty"
        donor = self.base / "donor"
        donor.mkdir()
        (donor / "keep").write_text("external content\n")
        cases = ((empty / "source", "file"), (empty / ".git", "file"),
                 (empty / ".git", "directory"), (empty, "symlink"), (empty, "file"),
                 (self.admin / "modules", "directory"), (self.admin / "modules", "symlink"))
        for path, kind in cases:
            with self.subTest(path=path.name, kind=kind):
                if path == empty:
                    empty.rmdir()
                if kind == "directory":
                    path.mkdir()
                elif kind == "symlink":
                    path.symlink_to(donor)
                else:
                    path.write_text("must retain\n")
                try:
                    with self.assertRaisesRegex(FINISH.Retain, "gitlink|submodule-admin"):
                        self.inventory(allow_empty_gitlinks=True)
                finally:
                    path.rmdir() if kind == "directory" else path.unlink()
                    if path == empty:
                        empty.mkdir()
        self.assertEqual((donor / "keep").read_text(), "external content\n")
        git(self.wt, "update-index", "--cacheinfo", f"160000,{'2' * 40},vendor/empty")
        with self.assertRaisesRegex(FINISH.Retain, "staged-or-index-only"):
            self.inventory(allow_empty_gitlinks=True)
        git(self.wt, "update-index", "--cacheinfo", f"160000,{oid},vendor/empty")
        (self.wt / "file").unlink()
        with self.assertRaisesRegex(FINISH.Retain, "working-file-missing"):
            self.inventory(allow_empty_gitlinks=True)
        (self.wt / "file").write_text("original\n")
        empty.chmod(0)
        try:
            with self.assertRaises(PermissionError):
                self.inventory(allow_empty_gitlinks=True)
        finally:
            empty.chmod(0o700)
        with mock.patch.object(FINISH.safety(), "directory_mount_id", side_effect=[1, 1, 1, 2]):
            with self.assertRaisesRegex(FINISH.Retain, "gitlink-nested-mount|gitlink-directory-changed"):
                self.inventory(allow_empty_gitlinks=True)

    def test_gitlink_descriptor_scan_rejects_population_and_replacement(self):
        self.gitlinks()
        empty = self.wt / "vendor/empty"
        moved = self.base / "original-gitlink"
        native = os.scandir
        for change in ("populate", "replace"):
            identity = (empty.stat().st_dev, empty.stat().st_ino)

            @contextlib.contextmanager
            def scan(target):
                with native(target) as children:
                    yield children
                if isinstance(target, int) and tuple(FINISH.safety().file_identity(os.fstat(target))[:2]) == identity:
                    if change == "populate":
                        (empty / "late").write_text("must retain\n")
                    else:
                        empty.rename(moved)
                        empty.mkdir()

            with self.subTest(change=change), mock.patch.object(FINISH.safety().os, "scandir", side_effect=scan):
                with self.assertRaisesRegex(FINISH.Retain, "gitlink-directory-changed"):
                    self.inventory(allow_empty_gitlinks=True)
            if change == "populate":
                (empty / "late").unlink()
            else:
                empty.rmdir()
                moved.rename(empty)
        self.assertTrue((self.admin / "locked").exists())

    def test_gitlink_open_and_absence_checks_reject_races_without_following_links(self):
        self.gitlinks()
        empty, missing = self.wt / "vendor/empty", self.wt / "vendor/missing"
        donor = self.base / "external"
        donor.mkdir()
        (donor / "keep").write_text("external content\n")
        native_open, native_stat = os.open, os.stat

        def replace(part, flags, *args, **kwargs):
            if part == "empty" and "dir_fd" in kwargs:
                empty.rmdir()
                empty.symlink_to(donor)
            return native_open(part, flags, *args, **kwargs)

        with mock.patch.object(FINISH.safety().os, "open", side_effect=replace):
            with self.assertRaises(OSError):
                self.inventory(allow_empty_gitlinks=True)
        self.assertEqual((donor / "keep").read_text(), "external content\n")
        empty.unlink()
        empty.mkdir()

        def appear(part, *args, **kwargs):
            try:
                return native_stat(part, *args, **kwargs)
            except FileNotFoundError:
                if part == "missing" and "dir_fd" in kwargs:
                    missing.mkdir()
                raise

        with mock.patch.object(FINISH.safety().os, "stat", side_effect=appear):
            with self.assertRaisesRegex(FINISH.Retain, "gitlink-absence-changed"):
                self.inventory(allow_empty_gitlinks=True)
        self.assertTrue(missing.is_dir())

    def test_new_ignored_payload_during_intent_encoding_blocks_removal(self):
        (self.repo / ".git/info/exclude").write_text("late.log\n")
        payload = self.wt / "late.log"
        encode = json.dumps
        def write_during_encoding(value, **options):
            result = encode(value, **options)
            if isinstance(value, dict) and "sibling_identities" in value and "qualification" in value:
                payload.write_text("retained")
            return result

        with mock.patch.object(json, "dumps", side_effect=write_during_encoding):
            with self.assertRaisesRegex(FINISH.Retain, "ignored"):
                self.remove()
        self.assertEqual(payload.read_text(), "retained")
        self.assert_enrolled_locked()

    def test_new_ignored_payload_after_intent_retains_receipt_and_original_lock(self):
        (self.repo / ".git/info/exclude").write_text("late.log\n")
        payload = self.wt / "late.log"
        marker = self.admin / "locked"
        inode = marker.stat().st_ino
        admission = FINISH.finalized_admission
        def publish_after_commit(db, row, *args, **kwargs):
            if FINISH.retirement(db, row)["state"] == "intent":
                self.assertFalse(db.in_transaction)
                payload.write_text("retained")
            return admission(db, row, *args, **kwargs)

        with mock.patch.object(FINISH, "finalized_admission", side_effect=publish_after_commit):
            code, rows = self.remove()
        self.assertEqual(rows[0]["reason"], "ignored-content-needs-exact-disposable-roots")
        self.assertEqual(payload.read_text(), "retained")
        self.assertEqual(marker.stat().st_ino, inode)
        self.assert_incomplete_locked(code, rows)

    def test_gitlink_replacement_between_admissions_never_dispatches_removal(self):
        self.gitlinks()
        empty = self.wt / "vendor/empty"
        admission, calls = FINISH.finalized_admission, []

        def replace(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                empty.rename(self.base / "original-gitlink")
                empty.mkdir()
            return admission(*args, **kwargs)

        with mock.patch.object(FINISH, "finalized_admission", side_effect=replace), \
                mock.patch.object(FINISH.safety(), "supervise", wraps=FINISH.safety().supervise) as children:
            with self.assertRaisesRegex(FINISH.Retain, "removal-preimage-changed"):
                self.remove()
        self.assertFalse(any("worktree" in call.args[0] and "remove" in call.args[0]
                             for call in children.call_args_list))
        self.assert_enrolled_locked()

    def test_gitlink_population_during_second_holder_scan_never_dispatches_removal(self):
        self.gitlinks()
        late = self.wt / "vendor/empty/late"
        scans = []

        def populate(*args):
            scans.append(args)
            if len(scans) == 2:
                late.write_text("must retain\n")

        with mock.patch.object(FINISH, "manual_holders", side_effect=populate), \
                mock.patch.object(FINISH.safety(), "supervise", wraps=FINISH.safety().supervise) as children:
            with self.assertRaisesRegex(FINISH.Retain, "gitlink-directory-not-empty"):
                self.call("remove", "--finalized")
        self.assertEqual(len(scans), 2)
        self.assertFalse(any("worktree" in call.args[0] and
                             ("remove" in call.args[0] or "unlock" in call.args[0])
                             for call in children.call_args_list),
                         "native removal dispatched after the second holder scan populated a gitlink")
        self.assertEqual(late.read_text(), "must retain\n")
        self.assert_enrolled_locked()

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

    def test_absent_sibling_under_system_temp_preserves_prunable_registration(self):
        parent = Path("/tmp").resolve()
        details = parent.lstat()
        if os.getuid() == 0 or details.st_uid != 0 or not details.st_mode & FINISH.stat.S_ISVTX:
            self.skipTest("non-owner observation of root-owned sticky temporary directory unavailable")
        with tempfile.TemporaryDirectory(prefix="gwt-sibling-", dir=parent) as temporary:
            sibling = Path(temporary)
            sibling.rmdir()
            git(self.repo, "worktree", "move", str(self.legacy), str(sibling))
            admin = Path(git(sibling, "rev-parse", "--absolute-git-dir"))
            admin_id = FINISH.identity(admin)
            backlinks = {name: (admin / name).read_bytes() for name in ("gitdir", "commondir")}
            shutil.rmtree(sibling)
            row = next(row for row in FINISH.records(str(self.repo)) if row["worktree"] == str(sibling))
            self.assertIn("prunable", row)
            code, rows = self.remove()
            self.assertEqual(code, 0, rows)
            self.assertEqual(rows[0]["checkout"], "removed")
            self.assertFalse(self.wt.exists())
            self.assertFalse(self.admin.exists())
            self.assertFalse(sibling.exists())
            self.assertEqual(FINISH.identity(admin), admin_id)
            self.assertEqual({name: (admin / name).read_bytes() for name in backlinks}, backlinks)
            self.assertIn(row, FINISH.records(str(self.repo)))
            with FINISH.ledger(self.state) as db:
                result = json.loads(FINISH.retirement(db, FINISH.get_row(db, self.wt))["result"])
            proof = result["sibling_identities_after"][str(sibling)]
            self.assertEqual(proof["path_absence"]["parent"], str(parent))
            self.assertEqual(proof["pointer_absence"]["parent_metadata"][0], 0)
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
        status = self.call("status")[1][0]
        self.assertEqual(status["checkout"], "unknown")
        self.assertEqual(status["retirement_state"], "incomplete")
        self.assertNotEqual(status["reason"], "finalized-task-worktree-removed")
        with FINISH.ledger(self.state) as db:
            result = json.loads(FINISH.retirement(db, FINISH.get_row(db, self.wt))["result"])
        self.assertEqual(result["reason"], "sibling-readback-unavailable")
        self.assertEqual(result["child"]["returncode"], 0)
        self.assertTrue(all(result[key] for key in ("path_absent", "admin_absent", "registration_absent")))
        self.assertTrue(result["siblings_after"])
        self.assertNotIn("sibling_identities_after", result)

    def test_finalized_evidence_disposal_preserves_branch_and_skips_automatic_policy(self):
        git(self.repo, "config", "extensions.worktreeConfig", "true")
        git(self.wt, "config", "--worktree", "core.hooksPath", "git-hooks")
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

    def test_status_keeps_removed_reason_after_prior_policy_retention(self):
        with mock.patch.object(FINISH, "qualified_policy", side_effect=FINISH.Retain(
                "explicit-release-policy-not-installed")):
            retained = self.finish("--release")[1][0]
        self.assertEqual(retained["reason"], "explicit-release-policy-not-installed")
        code, rows = self.remove()
        self.assertEqual(code, 0)
        removed = rows[0]
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            original = dict(row), dict(FINISH.retirement(db, row))
            self.assertEqual(FINISH.summary(db, row, reason="explicit-refusal")["reason"],
                             "explicit-refusal")
        with mock.patch.object(FINISH, "evaluate_release") as evaluate, \
                mock.patch.object(FINISH, "remove_with_intent") as remove:
            for options in ((), ("--all",)):
                with self.subTest(options=options):
                    code, records = self.call("status", *options)
                    self.assertEqual(code, 0)
                    status = next(row for row in records if row["worktree"] == str(self.wt))
                    self.assertEqual(status["checkout"], "removed")
                    self.assertEqual(status["reason"], removed["reason"])
                    self.assertFalse(status["removal_available"])
        evaluate.assert_not_called()
        remove.assert_not_called()
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            self.assertEqual((dict(row), dict(FINISH.retirement(db, row))), original)

    def test_removed_status_preserves_owner_cancellation(self):
        cancelled = self.call("cancel", "--reason", "superseded upstream")[1][0]
        code, rows = self.remove()
        self.assertEqual(code, 0)
        status = self.call("status")[1][0]
        self.assertEqual(status["checkout"], "removed")
        self.assertEqual(status["reason"], rows[0]["reason"])
        self.assertEqual(status["state"], "cancelled")
        self.assertEqual(status["cancelled_owners"], cancelled["cancelled_owners"])

    def test_finalized_branch_transition_preserves_both_refs_and_enrollment(self):
        # Keep the checkout available until its PR merges before manual closeout.
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        self.finish()
        self.proofs[self.url] = self.proof(self.url)
        with FINISH.ledger(self.state) as db:
            enrolled = dict(FINISH.get_row(db, self.wt))
        git(self.wt, "checkout", "-b", "reviewed-current")
        self.gitlinks()
        current_head = git(self.wt, "rev-parse", "HEAD")
        branches = {"released-feature": self.head, "reviewed-current": current_head}
        reflogs = {branch: self.repo / ".git/logs/refs/heads" / branch for branch in branches}
        before = {branch: path.read_bytes() for branch, path in reflogs.items()}
        with self.assertRaisesRegex(FINISH.Retain, "worktree-identity-changed"):
            self.call("resume")
        self.artifacts()
        with mock.patch.object(FINISH.safety(), "supervise", wraps=FINISH.safety().supervise) as children:
            code, rows = self.remove(".crabbox")
        self.assertEqual(code, 0, rows)
        self.assertEqual(rows[0]["checkout"], "removed")
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.admin.exists())
        self.assertTrue(self.legacy.exists())
        removals = [call.args[0] for call in children.call_args_list
                    if "worktree" in call.args[0] and "remove" in call.args[0]]
        self.assertEqual(len(removals), 1)
        self.assertFalse({"--force", "-f"} & set(removals[0]))
        for branch, head in branches.items():
            self.assertEqual(git(self.repo, "rev-parse", branch), head)
            self.assertEqual(reflogs[branch].read_bytes(), before[branch])
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            for key in ("identity", "finish_head", "primary_pr", "target", "dependencies"):
                self.assertEqual(row[key], enrolled[key])
            result = json.loads(FINISH.retirement(db, row)["result"])
            self.assertEqual(result["child"]["returncode"], 0)
            self.assertTrue(result["branch_preserved"])
            self.assertTrue(result["branch_reflog_preserved"])
            self.assertEqual({branch: proof["head"] for branch, proof in result["preserved_refs_after"].items()},
                             {"refs/heads/" + branch: head for branch, head in branches.items()})

    def test_fetch_head_removal_preserves_current_and_enrolled_recovery(self):
        heads, old_tips = {}, []
        for branch in ("released-feature", "reviewed-current"):
            if branch == "reviewed-current":
                git(self.wt, "checkout", "-b", branch)
            git(self.wt, "commit", "--allow-empty", "-m", "intermediate " + branch)
            old_tips.append(git(self.wt, "rev-parse", "HEAD"))
            git(self.wt, "commit", "--amend", "--allow-empty", "-m", "final " + branch)
            heads[branch] = git(self.wt, "rev-parse", "HEAD")
        reflogs = {branch: self.repo / ".git/logs/refs/heads" / branch for branch in heads}
        before = {branch: path.read_bytes() for branch, path in reflogs.items()}
        for oid in old_tips:
            self.assertEqual(git(self.repo, "rev-list", oid, "--not", "--all"), oid)
        (self.admin / "FETCH_HEAD").write_text(
            heads["released-feature"] + "\t\tbranch 'original' of https://example.test/repo\n"
            + old_tips[0] + "\tnot-for-merge\tbranch 'old-original' of https://example.test/repo\n"
            + heads["reviewed-current"] + "\tnot-for-merge\thttps://example.test/repo\\nname\n"
            + old_tips[1] + "\t\tbranch 'old-current' of https://example.test/repo\tname\rpart\n")
        with mock.patch.object(FINISH.safety(), "supervise", wraps=FINISH.safety().supervise) as children:
            code, rows = self.remove()
        self.assertEqual(code, 0, rows)
        self.assertEqual(rows[0]["checkout"], "removed")
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.admin.exists())
        self.assertTrue(self.legacy.exists())
        removals = [call.args[0] for call in children.call_args_list
                    if "worktree" in call.args[0] and "remove" in call.args[0]]
        self.assertEqual(len(removals), 1)
        self.assertFalse({"--force", "-f"} & set(removals[0]))
        for branch, head in heads.items():
            self.assertEqual(git(self.repo, "rev-parse", branch), head)
            self.assertEqual(reflogs[branch].read_bytes(), before[branch])
        for oid in old_tips:
            self.assertEqual(git(self.repo, "cat-file", "-t", oid), "commit")

    def fetched_target(self, *, recorded=True):
        if recorded:
            self.proofs[self.url].update(merged=False, state="open", merge=None)
            self.assertEqual(self.finish()[1][0]["reason"], "pr-not-merged")
        else:
            self.call("cancel", "--reason", "superseded upstream")
        target = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", self.head, "-m", "target advances")
        git(self.repo, "update-ref", "refs/heads/main", target, self.head)
        git(self.wt, "fetch", "--no-auto-maintenance", "--no-tags", str(self.repo),
            "refs/heads/main:refs/remotes/origin/main")
        self.assertEqual(git(self.repo, "rev-list", target, "--not", "released-feature"), target)
        return target

    def test_finalized_fetch_head_preserves_recorded_target_and_both_task_branches(self):
        target = self.fetched_target()
        successor = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", target, "-m", "next target")
        git(self.repo, "update-ref", "refs/remotes/origin/main", successor, target)
        git(self.wt, "checkout", "-b", "reviewed-current")
        branches = ("refs/heads/released-feature", "refs/heads/reviewed-current", "refs/remotes/origin/main")
        before = {branch: (git(self.repo, "rev-parse", branch),
                           (self.repo / ".git/logs" / branch).read_bytes()) for branch in branches}
        code, rows = self.remove()
        self.assertEqual(code, 0, rows)
        self.assertEqual(rows[0]["checkout"], "removed")
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.admin.exists())
        self.assertTrue(self.legacy.exists())
        for branch, (head, reflog) in before.items():
            self.assertEqual(git(self.repo, "rev-parse", branch), head)
            self.assertEqual((self.repo / ".git/logs" / branch).read_bytes(), reflog)
        self.assertEqual(git(self.repo, "cat-file", "-t", target), "commit")

    def test_finalized_fetch_head_target_ref_drift_retains_before_intent(self):
        target = self.fetched_target()
        successor = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", target, "-m", "next target")
        lock = (self.admin / "locked").read_bytes()
        def advance(*args):
            git(self.repo, "update-ref", "refs/remotes/origin/main", successor, target)
        with mock.patch.object(FINISH, "manual_holders", side_effect=advance):
            with self.assertRaisesRegex(FINISH.Retain, "preserved-refs-changed"):
                self.call("remove", "--finalized")
        self.assertTrue(self.wt.exists())
        self.assertEqual((self.admin / "locked").read_bytes(), lock)
        with FINISH.ledger(self.state) as db:
            item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
            self.assertEqual(item["state"], "enrolled")
            self.assertIsNone(item["intent"])

    def squash_merged_target(self):
        # A squash merge is a fresh commit on the target, not a descendant of the task head.
        (self.wt / "file").write_text("squashed change\n")
        git(self.wt, "commit", "-am", "task change")
        self.head = git(self.wt, "rev-parse", "HEAD")
        base = git(self.repo, "rev-parse", "main")
        squash = git(self.repo, "commit-tree", self.head + "^{tree}", "-p", base, "-m", "squash merge (#1)")
        git(self.repo, "update-ref", "refs/heads/main", squash, base)
        git(self.wt, "fetch", "--no-auto-maintenance", "--no-tags", str(self.repo),
            "refs/heads/main:refs/remotes/origin/main")
        self.assertIn(squash, (self.admin / "FETCH_HEAD").read_text())
        with self.assertRaises(subprocess.CalledProcessError):
            git(self.repo, "merge-base", "--is-ancestor", squash, "released-feature")
        self.proofs[self.url] = self.proof(self.url, merge=squash)
        return squash

    def test_finalized_squash_merge_in_fetch_head_is_admitted(self):
        squash = self.squash_merged_target()
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        self.assertEqual(self.finish()[1][0]["reason"], "pr-not-merged")
        result = self.remove()[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertFalse(self.wt.exists())
        self.assertEqual(git(self.repo, "rev-parse", "refs/remotes/origin/main"), squash)
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)

    def test_completed_squash_merge_in_fetch_head_is_admitted(self):
        squash = self.squash_merged_target()
        with mock.patch.object(FINISH, "manual_holders"):
            result = LifecycleTests.finish(self, "--release")[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertFalse(self.wt.exists())
        self.assertEqual(git(self.repo, "rev-parse", "refs/remotes/origin/main"), squash)

    def test_finalized_squash_fetch_head_unreachable_oid_retains(self):
        self.squash_merged_target()
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        self.assertEqual(self.finish()[1][0]["reason"], "pr-not-merged")
        stray = git(self.repo, "commit-tree", "HEAD^{tree}", "-m", "fetched elsewhere")
        path = self.admin / "FETCH_HEAD"
        path.write_text(stray + "\t\tunreachable fetch\n")
        with self.assertRaisesRegex(FINISH.Retain, "fetch-head-recovery-required"):
            self.remove()
        self.assertTrue(self.wt.exists())
        self.assertEqual(path.read_text(), stray + "\t\tunreachable fetch\n")

    def test_finalized_fetch_head_without_recorded_target_retains(self):
        self.fetched_target(recorded=False)
        before = (self.admin / "FETCH_HEAD").read_bytes()
        with self.assertRaisesRegex(FINISH.Retain, "fetch-head-recovery-required"):
            self.remove()
        self.assertTrue(self.wt.exists())
        self.assertEqual((self.admin / "FETCH_HEAD").read_bytes(), before)

    def test_finalized_fetch_head_with_missing_recorded_target_retains(self):
        self.fetched_target()
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        before = (self.admin / "FETCH_HEAD").read_bytes()
        with self.assertRaisesRegex(FINISH.Retain, "fetch-head-recovery-required"):
            self.remove()
        self.assertTrue(self.wt.exists())
        self.assertEqual((self.admin / "FETCH_HEAD").read_bytes(), before)

    def test_finalized_fetch_head_with_locked_recorded_target_retains(self):
        self.fetched_target()
        lock = self.repo / ".git/refs/remotes/origin/main.lock"
        lock.write_text("concurrent writer\n")
        with self.assertRaisesRegex(FINISH.Retain, "preserved-branch-lock-present"):
            self.remove()
        self.assertTrue(self.wt.exists())
        self.assertEqual(lock.read_text(), "concurrent writer\n")

    def test_finalized_fetch_head_does_not_accept_unrecorded_remote_branch(self):
        self.fetched_target()
        unrelated = git(self.repo, "commit-tree", "HEAD^{tree}", "-m", "unrelated history")
        git(self.repo, "update-ref", "refs/remotes/origin/other", unrelated)
        path = self.admin / "FETCH_HEAD"
        path.write_text(unrelated + "\t\tunrecorded remote branch\n")
        with self.assertRaisesRegex(FINISH.Retain, "fetch-head-recovery-required"):
            self.remove()
        self.assertTrue(self.wt.exists())
        self.assertEqual(path.read_text(), unrelated + "\t\tunrecorded remote branch\n")

    def other_recovery_branch(self):
        ancestor = git(self.repo, "commit-tree", "HEAD^{tree}", "-m", "other fetched work")
        tip = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", ancestor, "-m", "other branch tip")
        branch = "refs/heads/recovery"
        git(self.repo, "update-ref", "--create-reflog", branch, tip)
        (self.admin / "FETCH_HEAD").write_text(ancestor + "\t\tother fetched work\n")
        return branch, ancestor, tip

    def test_preserve_ref_anchors_other_branch_ancestor_and_preserves_all_refs(self):
        branch, ancestor, tip = self.other_recovery_branch()
        log = self.repo / ".git/logs" / branch
        before = log.read_bytes()
        self.assertNotIn(ancestor.encode(), before)
        with self.assertRaisesRegex(FINISH.Retain, "fetch-head-recovery-required"):
            self.remove()
        with self.assertRaisesRegex(FINISH.Retain, "fetch-head-recovery-required"):
            self.call("remove", "--finalized", "--preserve-ref", "refs/heads/main")
        with mock.patch.object(FINISH, "manual_holders"):
            code, rows = self.call("remove", "--finalized", "--preserve-ref", branch,
                                   "--preserve-ref", branch)
        self.assertEqual((code, rows[0]["checkout"]), (0, "removed"), rows)
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.admin.exists())
        self.assertTrue(self.legacy.exists())
        self.assertEqual(git(self.repo, "rev-parse", branch), tip)
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertEqual(log.read_bytes(), before)
        self.assertEqual(git(self.repo, "cat-file", "-t", ancestor), "commit")
        with FINISH.ledger(self.state) as db:
            outcome = json.loads(FINISH.retirement(db, FINISH.get_row(db, self.wt))["result"])
        self.assertEqual(set(outcome["preserved_refs_after"]), {branch, "refs/heads/released-feature"})

    def test_preserve_ref_rejects_nonliteral_missing_symbolic_and_noncommit_refs(self):
        branch, _, tip = self.other_recovery_branch()
        git(self.repo, "update-ref", "refs/remotes/origin/recovery", tip)
        git(self.repo, "tag", "recovery", tip)
        git(self.repo, "symbolic-ref", "refs/heads/alias", branch)
        (self.repo / ".git/refs/heads/noncommit").write_text(git(self.repo, "rev-parse", "HEAD:file") + "\n")
        for value in ("recovery", "HEAD", "refs/tags/recovery", "refs/remotes/origin/recovery",
                      branch + "~1", "refs/heads/*", "refs/heads/missing", "refs/heads/alias",
                      "refs/heads/noncommit", "refs/heads/../recovery", "refs/heads/"):
            with self.subTest(value=value), mock.patch.object(FINISH, "manual_holders"):
                with self.assertRaisesRegex(FINISH.Retain, "preserve-ref-needs-"):
                    self.call("remove", "--finalized", "--preserve-ref", value)
            self.assertTrue(self.wt.exists())
            self.assertTrue((self.admin / "locked").exists())
            with FINISH.ledger(self.state) as db:
                self.assertIsNone(FINISH.retirement(db, FINISH.get_row(db, self.wt))["intent"])

    def test_preserve_ref_is_only_accepted_by_explicit_finalized_remove(self):
        for command in ("finish", "release", "cancel", "check", "status", "guard", "resume",
                        "enroll", "creation-token", "holder-qualification", "remove", "orphan-locks"):
            with self.subTest(command=command), mock.patch.object(FINISH, "ledger") as ledger, \
                    mock.patch.object(FINISH.safety(), "supervise") as child:
                with self.assertRaisesRegex(FINISH.Retain, "preserve-ref-requires-manual-finalized"):
                    FINISH.main([command, "--preserve-ref", "refs/heads/recovery"])
                ledger.assert_not_called()
                child.assert_not_called()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            FINISH.main(["remove", "--finalized", "--preserve-ref"])

    def test_preserve_ref_drift_during_admission_retains_before_intent(self):
        branch, _, tip = self.other_recovery_branch()
        ref = self.repo / ".git" / branch
        log = self.repo / ".git/logs" / branch
        for change in ("lock", "move", "delete", "symbolic", "log-create", "log-delete",
                       "log-replace", "log-content"):
            git(self.repo, "update-ref", "--no-deref", branch, tip)
            ref.write_text(tip + "\n")
            if not log.exists():
                git(self.repo, "reflog", "create", branch)
            ref_bytes, log_bytes = ref.read_bytes(), log.read_bytes()
            if change == "log-create":
                log.unlink()

            def drift(*args):
                if change == "lock":
                    Path(str(ref) + ".lock").write_text("writer\n")
                elif change == "move":
                    git(self.repo, "update-ref", branch, self.head)
                elif change == "delete":
                    git(self.repo, "update-ref", "-d", branch)
                elif change == "symbolic":
                    git(self.repo, "symbolic-ref", branch, "refs/heads/released-feature")
                elif change == "log-create":
                    log.write_bytes(log_bytes)
                elif change == "log-delete":
                    log.unlink()
                elif change == "log-replace":
                    log.rename(log.with_name("old-log"))
                    log.write_bytes(log_bytes)
                else:
                    log.write_bytes(log_bytes.replace(b"\n", b"\tchanged\n", 1))

            try:
                with self.subTest(change=change), mock.patch.object(FINISH, "manual_holders", side_effect=drift):
                    with self.assertRaisesRegex(FINISH.Retain, "preserved-|preserve-ref-"):
                        self.call("remove", "--finalized", "--preserve-ref", branch)
                self.assertTrue(self.wt.exists())
                self.assertTrue((self.admin / "locked").exists())
                with FINISH.ledger(self.state) as db:
                    self.assertIsNone(FINISH.retirement(db, FINISH.get_row(db, self.wt))["intent"])
            finally:
                Path(str(ref) + ".lock").unlink(missing_ok=True)
                ref.write_bytes(ref_bytes)
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_bytes(log_bytes)
                log.with_name("old-log").unlink(missing_ok=True)

    def test_preserve_ref_drift_after_intent_never_dispatches_removal(self):
        branch, _, _ = self.other_recovery_branch()
        admission = FINISH.finalized_admission

        def drift(db, row, *args, **kwargs):
            if FINISH.retirement(db, row)["state"] == "intent":
                self.assertFalse(db.in_transaction)
                git(self.repo, "update-ref", branch, self.head)
            return admission(db, row, *args, **kwargs)

        with mock.patch.object(FINISH, "manual_holders"), \
                mock.patch.object(FINISH, "finalized_admission", side_effect=drift), \
                mock.patch.object(FINISH.safety(), "supervise", wraps=FINISH.safety().supervise) as children:
            code, rows = self.call("remove", "--finalized", "--preserve-ref", branch)
        self.assertEqual(rows[0]["reason"], "preserved-refs-changed")
        self.assertFalse(any("worktree" in call.args[0] and "remove" in call.args[0]
                             for call in children.call_args_list))
        self.assert_incomplete_locked(code, rows)

    def test_preserve_ref_drift_after_native_removal_keeps_outcome_unresolved(self):
        branch, _, _ = self.other_recovery_branch()
        native = FINISH.safety().supervise

        def drift(command, **kwargs):
            result = native(command, **kwargs)
            if "worktree" in command and "remove" in command:
                git(self.repo, "update-ref", branch, self.head)
            return result

        with mock.patch.object(FINISH, "manual_holders"), \
                mock.patch.object(FINISH.safety(), "supervise", side_effect=drift):
            code, rows = self.call("remove", "--finalized", "--preserve-ref", branch)
        self.assertEqual(rows[0]["checkout"], "unknown", rows)
        self.assertEqual(rows[0]["retirement_state"], "incomplete", rows)
        self.assertFalse(self.wt.exists())
        with FINISH.ledger(self.state) as db:
            outcome = json.loads(FINISH.retirement(db, FINISH.get_row(db, self.wt))["result"])
        self.assertTrue(outcome["registration_absent"])
        self.assertFalse(outcome["branch_preserved"])

    def test_empty_fetch_head_requires_explicit_manual_proof(self):
        (self.admin / "FETCH_HEAD").write_bytes(b"")
        with FINISH.ledger(self.state) as db:
            snap = FINISH.revalidate(FINISH.get_row(db, self.wt))
        for kwargs in ({}, {"preserved_oids": None}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(FINISH.Retain, "admin-recovery-content-unknown"):
                    FINISH.safety().admin_inventory(snap, FINISH.git, time.monotonic() + 10, **kwargs)
        result = FINISH.safety().admin_inventory(
            snap, FINISH.git, time.monotonic() + 10, preserved_oids=set())
        self.assertEqual(result["FETCH_HEAD"][1], hashlib.sha256(b"").hexdigest())
        code, rows = self.remove()
        self.assertEqual(code, 0, rows)
        self.assertEqual(rows[0]["checkout"], "removed")

    def test_fetch_head_ancestor_survives_without_direct_reflog_entry(self):
        ancestor = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", self.head, "-m", "ancestor")
        tip = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", ancestor, "-m", "descendant")
        git(self.wt, "merge", "--ff-only", tip)
        reflog = self.repo / ".git/logs/refs/heads/released-feature"
        before = reflog.read_bytes()
        self.assertNotIn(ancestor.encode(), before)
        (self.admin / "FETCH_HEAD").write_text(ancestor + "\t\tretained ancestor\n")
        code, rows = self.remove()
        self.assertEqual(code, 0, rows)
        self.assertEqual(rows[0]["checkout"], "removed")
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), tip)
        self.assertEqual(reflog.read_bytes(), before)
        self.assertEqual(git(self.repo, "cat-file", "-t", ancestor), "commit")

    def test_fetch_head_rejects_tag_object_even_when_peeled_commit_is_retained(self):
        git(self.repo, "tag", "-a", "fetched-tag", self.head, "-m", "tag fixture")
        tag = git(self.repo, "rev-parse", "fetched-tag")
        self.assertEqual(git(self.repo, "rev-parse", "fetched-tag^{}"), self.head)
        path = self.admin / "FETCH_HEAD"
        data = tag + "\t\tannotated tag\n"
        path.write_text(data)
        with self.assertRaisesRegex(FINISH.Retain, "fetch-head-recovery-required"):
            self.remove()
        self.assertTrue(self.wt.exists())
        self.assertEqual(path.read_text(), data)

    def test_fetch_head_mixed_ancestor_and_unretained_commit_keeps_checkout(self):
        ancestor = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", self.head, "-m", "ancestor")
        tip = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", ancestor, "-m", "descendant")
        unretained = git(self.repo, "commit-tree", "HEAD^{tree}", "-m", "unretained")
        git(self.wt, "merge", "--ff-only", tip)
        path = self.admin / "FETCH_HEAD"
        data = ancestor + "\t\tretained ancestor\n" + unretained + "\tnot-for-merge\tunretained\n"
        path.write_text(data)
        with self.assertRaisesRegex(FINISH.Retain, "fetch-head-recovery-required"):
            self.remove()
        self.assertTrue(self.wt.exists())
        self.assertEqual(path.read_text(), data)

    def test_malformed_or_unanchored_fetch_head_preserves_checkout_and_lock(self):
        unanchored = git(self.repo, "commit-tree", "HEAD^{tree}", "-m", "fetched-only recovery")
        self.assertEqual(git(self.repo, "cat-file", "-t", unanchored), "commit")
        oid = self.head.encode()
        valid = oid + b"\t\tbranch 'main' of https://example.test/repo\n"
        path = self.admin / "FETCH_HEAD"
        lock = (self.admin / "locked").read_bytes()
        for data in (
                unanchored.encode() + b"\t\tunretained commit\n",
                valid + unanchored.encode() + b"\tnot-for-merge\tunretained commit\n",
                b"0" * 40 + b"\t\tzero oid\n", oid.upper() + b"\t\tuppercase oid\n",
                oid[:-1] + b"\t\tshort oid\n", oid + b"\tmerge\tnote\n",
                oid + b"\tnot-for-merge \tnote\n", oid + b"\t\n",
                oid + b"  note\n", oid + b"\t\tunterminated", valid + b"\n",
                oid + b"\t\tnote\0url\n", valid + b"partial"):
            with self.subTest(data=data):
                path.write_bytes(data)
                with self.assertRaisesRegex(FINISH.Retain, "fetch-head-recovery-required"):
                    self.remove()
                self.assertEqual(path.read_bytes(), data)
                self.assertEqual((self.admin / "locked").read_bytes(), lock)
                self.assertTrue(self.wt.exists())
                self.assertTrue(self.legacy.exists())
                with FINISH.ledger(self.state) as db:
                    item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
                    self.assertEqual(item["state"], "enrolled")
                    self.assertIsNone(item["intent"])

    def test_fetch_head_drift_between_admissions_never_dispatches_removal(self):
        path = self.admin / "FETCH_HEAD"
        path.write_text(self.head + "\t\toriginal note\n")
        admission, calls = FINISH.finalized_admission, []

        def drift(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                path.write_text(self.head + "\t\tchanged note\n")
            return admission(*args, **kwargs)

        with mock.patch.object(FINISH, "finalized_admission", side_effect=drift), \
                mock.patch.object(FINISH.safety(), "supervise", wraps=FINISH.safety().supervise) as children:
            with self.assertRaisesRegex(FINISH.Retain, "removal-preimage-changed"):
                self.remove()
        self.assertFalse(any("worktree" in call.args[0] and ("remove" in call.args[0] or "unlock" in call.args[0])
                             for call in children.call_args_list))
        self.assert_enrolled_locked()
        self.assertEqual(path.read_text(), self.head + "\t\tchanged note\n")

    def resolve_undo_fixture(self):
        base = git(self.wt, "rev-parse", "HEAD:file")
        (self.wt / "file").write_text("our change\n")
        git(self.wt, "commit", "-am", "our change")
        ours = git(self.wt, "rev-parse", "HEAD:file")
        (self.repo / "file").write_text("their change\n")
        git(self.repo, "commit", "-am", "their change")
        theirs = git(self.repo, "rev-parse", "HEAD:file")
        with self.assertRaises(subprocess.CalledProcessError):
            git(self.wt, "merge", "--no-edit", "main")
        (self.wt / "file").write_text("resolved change\n")
        git(self.wt, "add", "file")
        git(self.wt, "commit", "-m", "resolve conflict")
        # Keep the native REUC index, but make its stage history reflog-only.
        squashed = git(self.wt, "commit-tree", "HEAD^{tree}", "-m", "resolve conflict")
        git(self.wt, "update-ref", "refs/heads/released-feature", squashed)
        self.head = git(self.wt, "rev-parse", "HEAD")
        self.proofs[self.url] = self.proof(self.url)
        return {"file": [[stage, 0o100644, oid] for stage, oid in enumerate((base, ours, theirs), 1)]}

    def test_resolve_undo_native_removal_records_mapping_and_preserves_blob_roots(self):
        expected = self.resolve_undo_fixture()
        current_objects = set(git(self.wt, "rev-list", "--objects", "--no-object-names", self.head).splitlines())
        self.assertTrue(all(oid not in current_objects for _, _, oid in expected["file"]))
        reflog = self.repo / ".git/logs/refs/heads/released-feature"
        before = reflog.read_bytes()
        (self.admin / "FETCH_HEAD").write_text(self.head + "\t\tretained resolution\n")
        intent = []
        native = FINISH.safety().supervise

        def observe(command, **kwargs):
            if "worktree" in command and "remove" in command:
                self.assertFalse({"--force", "-f"} & set(command))
                with contextlib.closing(sqlite3.connect(
                        (self.state / "lifecycle.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
                    intent.append(json.loads(db.execute("SELECT intent FROM retirement").fetchone()[0]))
            return native(command, **kwargs)

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=observe):
            code, rows = self.remove()
        self.assertEqual(code, 0, rows)
        self.assertEqual(rows[0]["checkout"], "removed")
        self.assertEqual(len(intent), 1)
        self.assertEqual(intent[0]["before"]["contents"]["resolve_undo"], expected)
        with FINISH.ledger(self.state) as db:
            item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
            self.assertIsNone(item["intent"])
            self.assertEqual(json.loads(item["result"])["resolve_undo"], expected)
        self.assertEqual(reflog.read_bytes(), before)
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        for _, _, oid in expected["file"]:
            self.assertEqual(git(self.repo, "cat-file", "-t", oid), "blob")
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.admin.exists())
        self.assertTrue(self.legacy.exists())

    def test_resolve_undo_release_mapping_cannot_disappear_before_automatic_retry(self):
        self.resolve_undo_fixture()
        self.proofs[self.url].update(merged=False, state="open", merge=None)
        result = self.finish("--release")[1][0]
        self.assertEqual(result["automatic_handoff"]["baseline"], "ready", result)
        git(self.wt, "update-index", "--clear-resolve-undo")
        self.proofs[self.url].update(merged=True, state="closed", merge="a" * 40)
        self.assertEqual(self.call("check", "--apply")[1][0]["reason"], "released-worktree-changed")
        self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.token)
        self.assertTrue(self.wt.exists())

    def write_resolve_undo(self, entries):
        git(self.wt, "update-index", "--clear-resolve-undo")
        path = self.admin / "index"
        payload = b""
        for name, stages in entries.items():
            by_stage = {stage: (mode, oid) for stage, mode, oid in stages}
            payload += os.fsencode(name) + b"\0"
            payload += b"".join(format(by_stage.get(stage, (0, None))[0], "o").encode() + b"\0"
                                for stage in (1, 2, 3))
            payload += b"".join(bytes.fromhex(by_stage[stage][1]) for stage in (1, 2, 3) if stage in by_stage)
        body = path.read_bytes()[:-20] + b"REUC" + struct.pack(">I", len(payload)) + payload
        path.write_bytes(body + hashlib.sha1(body).digest())

    def test_resolve_undo_requires_blobs_at_exact_literal_path_from_commit_roots(self):
        blob = git(self.wt, "rev-parse", "HEAD:file")
        tree = git(self.wt, "rev-parse", "HEAD^{tree}")
        unanchored = FINISH.git(self.wt, "hash-object", "-w", "--stdin", input_data=b"unanchored recovery\n")
        (self.wt / "nested").mkdir()
        (self.wt / "nested/file").write_text("only below directory\n")
        git(self.wt, "add", "nested/file")
        git(self.wt, "commit", "-m", "nested history")
        nested = git(self.wt, "rev-parse", "HEAD:nested/file")
        head = git(self.wt, "rev-parse", "HEAD")
        lock = (self.admin / "locked").read_bytes()
        for name, oid, reason in (
                ("file", self.head, "object-type-or-missing"),
                ("file", tree, "object-type-or-missing"),
                ("file", "a1" * 20, "object-type-or-missing"),
                ("file", unanchored, "recovery-required"),
                ("[f]ile", blob, "recovery-required"),
                ("nested", nested, "recovery-required"),
                ("other", blob, "recovery-required")):
            self.write_resolve_undo({name: [[2, 0o100644, oid]]})
            before = (self.admin / "index").read_bytes()
            with self.subTest(name=name, oid=oid), self.assertRaisesRegex(FINISH.Retain, reason):
                self.remove()
            self.assertEqual((self.admin / "index").read_bytes(), before)
            self.assertEqual((self.admin / "locked").read_bytes(), lock)
            with FINISH.ledger(self.state) as db:
                item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
                self.assertEqual(item["state"], "enrolled")
                self.assertIsNone(item["intent"])
        self.write_resolve_undo({"file": [[2, 0o100644, blob]]})
        with FINISH.ledger(self.state) as db:
            snap = FINISH.revalidate(FINISH.get_row(db, self.wt))
        for roots in ({blob}, set(), {head, tree}):
            with self.subTest(roots=roots), self.assertRaisesRegex(
                    FINISH.Retain, "resolve-undo-object-type-or-missing|resolve-undo-recovery-required"):
                FINISH.safety().inventory(snap, FINISH.git, time.monotonic() + 10, preserved_oids=roots)

    def test_resolve_undo_missing_stages_and_literal_unusual_path_survive_native_removal(self):
        name = "[literal]\nname"
        (self.wt / name).write_text("retained stage\n")
        (self.wt / "executable").write_text("retained executable\n")
        (self.wt / "executable").chmod(0o755)
        (self.wt / "link").symlink_to(name)
        git(self.wt, "add", name, "executable", "link")
        git(self.wt, "commit", "-m", "literal path and modes")
        expected = {path: [[2, mode, git(self.wt, "rev-parse", "HEAD:" + path)]]
                    for path, mode in ((name, 0o100644), ("executable", 0o100755), ("link", 0o120000))}
        self.write_resolve_undo(expected)
        code, rows = self.remove()
        self.assertEqual(code, 0, rows)
        self.assertEqual(rows[0]["checkout"], "removed")
        for stages in expected.values():
            self.assertEqual(git(self.repo, "cat-file", "-t", stages[0][2]), "blob")
        with FINISH.ledger(self.state) as db:
            item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
            self.assertEqual(json.loads(item["result"])["resolve_undo"], expected)

    def test_resolve_undo_index_drift_between_admissions_never_unlocks(self):
        expected = self.resolve_undo_fixture()
        admission, calls = FINISH.finalized_admission, []

        def drift(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                self.write_resolve_undo({"file": expected["file"][:1]})
            return admission(*args, **kwargs)

        with mock.patch.object(FINISH, "finalized_admission", side_effect=drift), \
                mock.patch.object(FINISH.safety(), "supervise", wraps=FINISH.safety().supervise) as children:
            with self.assertRaisesRegex(FINISH.Retain, "removal-preimage-changed"):
                self.remove()
        self.assertFalse(any("worktree" in call.args[0] and ("remove" in call.args[0] or "unlock" in call.args[0])
                             for call in children.call_args_list))
        self.assert_enrolled_locked()

    def test_resolve_undo_reflog_drift_during_admission_retains_before_intent(self):
        self.resolve_undo_fixture()
        index = (self.admin / "index").read_bytes()
        reflog = self.repo / ".git/logs/refs/heads/released-feature"

        def drift(*args):
            reflog.write_bytes(reflog.read_bytes() + reflog.read_bytes().splitlines(keepends=True)[-1])

        with mock.patch.object(FINISH, "manual_holders", side_effect=drift):
            with self.assertRaisesRegex(FINISH.Retain, "preserved-refs-changed"):
                self.call("remove", "--finalized")
        self.assertEqual((self.admin / "index").read_bytes(), index)
        self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.token)
        with FINISH.ledger(self.state) as db:
            item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
            self.assertEqual(item["state"], "enrolled")
            self.assertIsNone(item["intent"])

    def test_resolve_undo_git_diagnostics_failed_or_malformed_proofs_retain(self):
        self.resolve_undo_fixture()
        before = (self.admin / "index").read_bytes()
        native = FINISH.safety().supervise
        for query in ("cat-file", "rev-list"):
            for fault in ("warning", "nonzero", "partial"):
                def faulty(command, **kwargs):
                    result = native(command, **kwargs)
                    if query in command and "--no-replace-objects" in command:
                        if fault == "warning":
                            result.stderr = b"warning: incomplete object evidence\n"
                        elif fault == "nonzero":
                            result.returncode = 1
                        else:
                            result.stdout = result.stdout[:-1]
                    return result

                with self.subTest(query=query, fault=fault), \
                        mock.patch.object(FINISH.safety(), "supervise", side_effect=faulty):
                    with self.assertRaisesRegex(FINISH.Retain, "git-diagnostic|git-failed|resolve-undo"):
                        self.remove()
                self.assertEqual((self.admin / "index").read_bytes(), before)
                self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.token)
                with FINISH.ledger(self.state) as db:
                    item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
                    self.assertEqual(item["state"], "enrolled")
                    self.assertIsNone(item["intent"])

    def test_resolve_undo_git_deadline_and_output_limit_reap_owned_probe(self):
        self.resolve_undo_fixture()
        with FINISH.ledger(self.state) as db:
            snap = FINISH.revalidate(FINISH.get_row(db, self.wt))
        roots = {self.head, git(self.wt, "rev-parse", "released-feature@{1}")}
        native = FINISH.safety().supervise
        before = (self.admin / "index").read_bytes()
        for query in ("cat-file", "rev-list"):
            for fault, script in (
                    ("deadline", "import sys, time; sys.stdin.buffer.read(); time.sleep(30)"),
                    ("output", "import sys; sys.stdin.buffer.read(); sys.stdout.write('x' * (2 * 1024 * 1024))")):
                probes = []

                def oversized(command, **kwargs):
                    if query in command and "--no-replace-objects" in command:
                        result = native([sys.executable, "-I", "-c", script], **kwargs)
                        probes.append(result)
                        return result
                    return native(command, **kwargs)

                started = time.monotonic()
                with self.subTest(query=query, fault=fault), \
                        mock.patch.object(FINISH.safety(), "supervise", side_effect=oversized):
                    with self.assertRaisesRegex(FINISH.Retain, "git-failed"):
                        FINISH.safety().inventory(snap, FINISH.git, started + 0.5, preserved_oids=roots)
                self.assertLess(time.monotonic() - started, 3)
                self.assertEqual(len(probes), 1)
                self.assertIsNotNone(probes[0].failure)
                self.assertTrue(probes[0].proof["reaped"])
                self.assertTrue(probes[0].proof["group_absent"])
                self.assertEqual((self.admin / "index").read_bytes(), before)
                self.assertTrue((self.admin / "locked").exists())

    def test_branch_or_head_drift_after_manual_binding_retains_before_intent(self):
        for change in ("branch", "head"):
            git(self.wt, "checkout", "-b", "reviewed-" + change)

            def drift():
                if change == "branch":
                    git(self.wt, "checkout", "-b", "unexpected-branch")
                else:
                    git(self.wt, "commit", "--allow-empty", "-m", "unexpected work")

            with self.subTest(change=change), mock.patch.object(FINISH, "storage_guard", side_effect=drift):
                with self.assertRaisesRegex(FINISH.Retain, "worktree-identity-changed|finalized-head-changed"):
                    self.remove()
            self.assertTrue(self.wt.exists())
            self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.token)
            with FINISH.ledger(self.state) as db:
                item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
                self.assertEqual(item["state"], "enrolled")
                self.assertIsNone(item["intent"])

    def test_head_drift_between_admissions_never_dispatches_removal(self):
        git(self.wt, "checkout", "-b", "reviewed-current")
        admission, calls = FINISH.finalized_admission, []

        def drift(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                git(self.wt, "commit", "--allow-empty", "-m", "unexpected work")
            return admission(*args, **kwargs)

        with mock.patch.object(FINISH, "finalized_admission", side_effect=drift), \
                mock.patch.object(FINISH.safety(), "supervise", wraps=FINISH.safety().supervise) as children:
            with self.assertRaisesRegex(FINISH.Retain, "finalized-head-changed"):
                self.remove()
        self.assertFalse(any("worktree" in call.args[0] and "remove" in call.args[0]
                             for call in children.call_args_list))
        self.assert_enrolled_locked()
        self.assertNotEqual(git(self.wt, "rev-parse", "HEAD"), self.head)

    def test_original_ref_drift_during_admission_retains_before_intent(self):
        git(self.wt, "checkout", "-b", "reviewed-current")
        git(self.wt, "commit", "--allow-empty", "-m", "reviewed work")
        head = git(self.wt, "rev-parse", "HEAD")
        (self.admin / "FETCH_HEAD").write_text(self.head + "\t\toriginal ref\n")
        with mock.patch.object(FINISH, "manual_holders", side_effect=lambda *args:
                               git(self.repo, "update-ref", "refs/heads/released-feature", head)):
            with self.assertRaisesRegex(FINISH.Retain, "preserved-refs-changed"):
                self.call("remove", "--finalized")
        self.assertTrue(self.wt.exists())
        self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.token)
        with FINISH.ledger(self.state) as db:
            item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
            self.assertEqual(item["state"], "enrolled")
            self.assertIsNone(item["intent"])

    def original_ref_drift_after_removal(self, change):
        native = FINISH.safety().supervise
        enrolled_branch = git(self.wt, "symbolic-ref", "HEAD")
        git(self.wt, "checkout", "-b", "reviewed-current")
        git(self.wt, "commit", "--allow-empty", "-m", "reviewed work")
        head = git(self.wt, "rev-parse", "HEAD")
        reflog = self.repo / ".git/logs" / enrolled_branch

        def drift(command, **kwargs):
            result = native(command, **kwargs)
            if "worktree" in command and "remove" in command:
                if change == "ref":
                    git(self.repo, "update-ref", enrolled_branch, head)
                else:
                    with reflog.open("ab") as stream:
                        stream.write(reflog.read_bytes().splitlines(keepends=True)[-1])
            return result

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=drift):
            code, rows = self.remove()
        self.assertEqual(code, 1)
        self.assertEqual(rows[0]["checkout"], "unknown")
        self.assertEqual(rows[0]["retirement_state"], "incomplete")
        self.assertFalse(self.wt.exists())
        with FINISH.ledger(self.state) as db:
            result = json.loads(FINISH.retirement(db, FINISH.get_row(db, self.wt))["result"])
        self.assertFalse(result["branch_preserved" if change == "ref" else "branch_reflog_preserved"])

    def test_original_ref_drift_after_removal_is_unknown(self):
        self.original_ref_drift_after_removal("ref")

    def test_original_reflog_drift_after_removal_is_unknown(self):
        self.original_ref_drift_after_removal("reflog")

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
        for allow_branch_change in (False, True):
            for change in changes:
                if allow_branch_change and "branch" in change:
                    continue
                with self.subTest(change=change, branch=allow_branch_change), \
                        mock.patch.object(FINISH, "snapshot", return_value={**current, **change}):
                    with self.assertRaisesRegex(FINISH.Retain, "worktree-identity-changed"):
                        FINISH.revalidate(row, allow_device_renumbering=True, allow_branch_change=allow_branch_change)
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

    def test_recursively_empty_unreported_directories_are_removable_structure(self):
        for leaf in ("formatter-check", "syntax-check"):
            (self.wt / "target/operations" / leaf).mkdir(parents=True)
        self.call("cancel", "--reason", "superseded upstream")
        # Git reports neither untracked nor ignored roots for this empty tree,
        # so naming one cannot match; no disposal declaration is needed.
        with self.assertRaisesRegex(FINISH.Retain, "exact-disposable-roots"):
            self.remove("target")
        self.assertTrue((self.wt / "target/operations/syntax-check").is_dir())
        code, rows = self.remove()
        self.assertEqual(code, 0)
        self.assertEqual(rows[0]["checkout"], "removed", rows[0])
        self.assertFalse(self.wt.exists())
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)

    def test_empty_structure_refuses_any_nested_non_directory_entry(self):
        nested = self.wt / "target/operations/check"
        nested.mkdir(parents=True)
        snap = FINISH.revalidate(ReleaseTests.row(self))
        until = time.monotonic() + 30
        self.assertIn("target/operations/check",
                      FINISH.safety().inventory(snap, FINISH.git, until)["entries"])
        cases = (
            ("file", lambda path: path.write_text("payload\n")),
            ("symlink", lambda path: path.symlink_to(self.base / "missing")),
            ("fifo", lambda path: os.mkfifo(path)),
            (".git", lambda path: path.mkdir()),
        )
        for name, create in cases:
            with self.subTest(entry=name):
                create(nested / name)
                try:
                    with self.assertRaisesRegex(FINISH.Retain, "unclassified-ignored-directory"):
                        FINISH.safety().inventory(snap, FINISH.git, time.monotonic() + 30)
                finally:
                    target = nested / name
                    target.rmdir() if target.is_dir() and not target.is_symlink() else target.unlink()
        self.assertTrue(nested.is_dir())

    def test_tracked_or_skip_worktree_file_replaced_by_empty_directory_refuses(self):
        snap = FINISH.revalidate(ReleaseTests.row(self))
        for skip in (False, True):
            with self.subTest(skip_worktree=skip):
                if skip:
                    git(self.wt, "update-index", "--skip-worktree", "file")
                (self.wt / "file").unlink()
                (self.wt / "file/sub").mkdir(parents=True)
                try:
                    with self.assertRaisesRegex(FINISH.Retain, "working-type-changed"):
                        FINISH.safety().inventory(snap, FINISH.git, time.monotonic() + 30)
                    with mock.patch.object(FINISH, "remove_with_intent") as remove:
                        with self.assertRaises(FINISH.Retain):
                            self.remove()
                    remove.assert_not_called()
                finally:
                    shutil.rmtree(self.wt / "file")
                    git(self.wt, "update-index", "--no-skip-worktree", "file")
                    git(self.wt, "checkout", "--", "file")
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

    def test_inline_holder_errors_and_linux_records_preserve_intent_and_lock(self):
        native = FINISH.safety().supervise
        lookup = shutil.which
        lock = (self.admin / "locked").read_bytes()
        for record, sudo, reason in (
                (b"fNOFD\0n/proc/7/fd (opendir: Permission denied)", False, "visibility-unknown"),
                (b"fcwd\0n/proc/7/cwd (readlink: Permission denied)", False, "visibility-unknown"),
                (b"fmem\0n/proc/7/maps (fopen: Permission denied)", False, "visibility-unknown"),
                (b"ftxt\0n/proc/7/exe\0\n"
                 b"f0\0n/proc/7/fd/0 (readlink: No such file or directory)\0\n"
                 b"fmem\0D0x8\0i22\0n/elsewhere (stat: No such file or directory)\0\n"
                 b"p8\0\nf3\0n" + os.fsencode(self.wt / "file"), True, "pending-departure"),
                (b"ftxt\0n/proc/7/exe\0\n"
                 b"f3\0n/proc/7/task/11/fd/3 (readlink: No such file or directory)\0\n"
                 b"fmem\0D0x8\0i22\0n" + os.fsencode(self.admin)
                 + b" (stat: No such file or directory)", True, "pending-departure"),
                (b"ftxt\0n/proc/7/exe\0\n"
                 b"f3\0n/proc/7/fd/3 (readlink: No such file or directory)\0\n"
                 b"fNOFD\0n/proc/7/fd (opendir: Permission denied)", True, "visibility-unknown")):
            def observe(command, **kwargs):
                if command[0] in ("/fixture/lsof", "/fixture/sudo"):
                    return types.SimpleNamespace(
                        stdout=b"p7\0\n" + record + b"\0\n", stderr=b"",
                        returncode=0, failure=None)
                return native(command, **kwargs)

            with self.subTest(record=record), \
                    mock.patch.object(FINISH.sys, "platform", "linux"), \
                    mock.patch.object(FINISH, "trusted_holder_binary",
                                      side_effect=lambda name: "/fixture/" + name), \
                    mock.patch.object(FINISH.shutil, "which", side_effect=lambda name:
                                      "/fixture/lsof" if name == "lsof" else lookup(name)), \
                    mock.patch.object(FINISH.safety(), "supervise", side_effect=observe) as child:
                with self.assertRaisesRegex(FINISH.Retain, reason):
                    self.call("remove", "--finalized", *(["--sudo-holder-scan"] if sudo else []))
                self.assertFalse(any("worktree" in call.args[0]
                                     and ("remove" in call.args[0] or "unlock" in call.args[0])
                                     for call in child.call_args_list))
            self.assertEqual((self.admin / "locked").read_bytes(), lock)
            self.assertEqual((self.wt / "file").read_text(), "original\n")
            self.assertTrue(self.legacy.exists())
            self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
            with FINISH.ledger(self.state) as db:
                item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
                self.assertEqual(item["state"], "enrolled")
                self.assertIsNone(item["intent"])

    def assert_enrolled_locked(self):
        self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.token)
        with FINISH.read_only_ledger(self.state) as db:
            item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
            self.assertEqual(item["state"], "enrolled")
            self.assertIsNone(item["intent"])
            self.assertIsNone(item["result"])

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

        def limited_owner(*args, **kwargs):
            result = admission(*args, **kwargs)
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

        def exhausted_owner(*args, **kwargs):
            result = admission(*args, **kwargs)
            FINISH.safety().DEADLINE = time.monotonic() + 31
            return result

        with mock.patch.object(FINISH, "finalized_admission", side_effect=exhausted_owner), \
                mock.patch.object(FINISH, "git", wraps=native) as commands, \
                mock.patch.object(FINISH.safety(), "supervise", wraps=supervise) as children:
            with self.assertRaisesRegex(FINISH.Retain, "removal-budget-exhausted"):
                self.remove()
        self.assertFalse(any(call.args[1:3] == ("worktree", "unlock") for call in commands.call_args_list))
        self.assertFalse(any("remove" in call.args[0] for call in children.call_args_list))
        self.assert_enrolled_locked()

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

    def test_wrapper_forwards_sudo_only_to_finalized_helper(self):
        env = dict(os.environ, DOTFILES_WORKTREES_ROOT=str(self.root),
                   DOTFILES_GWT_FINISH_STATE=str(self.state), TMUX="")
        script = ('source "$1"; cd "$2"; '
                  '_gwt_finish_tool() { printf "%s\\n" "$@"; }; '
                  'gwt "$4" "$3" "$5" --sudo-holder-scan')
        command = ["zsh", "-f", "-c", script, "fixture", str(ROOT / "functions/gwt/gwt.zsh"),
                   str(self.repo), str(self.wt)]
        for action, option in (("rm", ""), ("rm", "--force"), ("finish", "--finalized"),
                               ("root", "--finalized")):
            result = subprocess.run([*command, action, option], env=env, capture_output=True,
                                    text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0, (action, option, result.stdout))
            self.assertNotIn("remove\n", result.stdout)
        result = subprocess.run([*command, "rm", "--finalized"], env=env, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "remove", "--worktree", str(self.wt), "--finalized", "--sudo-holder-scan"])
        self.assertTrue(self.wt.exists())
        self.assertTrue((self.admin / "locked").exists())

    def test_wrapper_forwards_preserve_ref_only_to_finalized_helper(self):
        env = dict(os.environ, DOTFILES_WORKTREES_ROOT=str(self.root),
                   DOTFILES_GWT_FINISH_STATE=str(self.state), TMUX="")
        script = ('source "$1"; cd "$2"; shift 2; '
                  '_gwt_finish_tool() { printf "%s\\n" "$@"; }; gwt "$@"')
        command = ["zsh", "-f", "-c", script, "fixture", str(ROOT / "functions/gwt/gwt.zsh"),
                   str(self.repo)]
        branch = "refs/heads/recovery"
        for args in (("rm", str(self.wt)), ("rm", str(self.wt), "--force"),
                     ("finish",), ("status",), ("root",), ("new", "branch")):
            result = subprocess.run([*command, *args, "--preserve-ref", branch],
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0, (args, result.stdout))
            self.assertNotIn("remove\n", result.stdout)
        result = subprocess.run([*command, "rm", str(self.wt), "--finalized", "--preserve-ref"],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        result = subprocess.run([*command, "rm", str(self.wt), "--finalized",
                                 "--preserve-ref", branch, "--preserve-ref", branch],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "remove", "--worktree", str(self.wt), "--finalized",
            "--preserve-ref", branch, "--preserve-ref", branch])
        self.assertTrue(self.wt.exists())

    def test_wrapper_forwards_local_checkpoint_to_finalized_helper(self):
        env = dict(os.environ, DOTFILES_WORKTREES_ROOT=str(self.root),
                   DOTFILES_GWT_FINISH_STATE=str(self.state), TMUX="")
        script = ('source "$1"; cd "$2"; shift 2; '
                  '_gwt_finish_tool() { printf "%s\\n" "$@"; }; gwt "$@"')
        command = ["zsh", "-f", "-c", script, "fixture", str(ROOT / "functions/gwt/gwt.zsh"),
                   str(self.repo)]
        branch = "refs/heads/released-feature"
        result = subprocess.run([*command, "rm", str(self.wt), "--finalized",
                                 "--keep-local-checkpoint", branch],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "remove", "--worktree", str(self.wt), "--finalized", "--keep-local-checkpoint", branch])

    def test_sudo_scan_does_not_elevate_git_or_lifecycle_and_preserves_admission_guards(self):
        native = FINISH.safety().supervise
        commands, scans = [], []
        uid = os.geteuid()

        def observe(command, **kwargs):
            self.assertEqual(os.geteuid(), uid)
            commands.append(command)
            if command[0] == "/fixture/sudo":
                scans.append((command, kwargs))
                return types.SimpleNamespace(
                    stdout=b"p7\0\nf3\0D0x8\0i22\0n/elsewhere\0\n", stderr=b"",
                    returncode=0, failure=None, proof={"pid": 123})
            return native(command, **kwargs)

        with mock.patch.object(FINISH.sys, "platform", "linux"), \
                mock.patch.object(FINISH, "trusted_holder_binary", side_effect=lambda name: "/fixture/" + name), \
                mock.patch.object(FINISH.safety(), "supervise", side_effect=observe):
            (self.wt / "file").write_text("unfinished\n")
            with self.assertRaisesRegex(FINISH.Retain, "dirty-or-untracked"):
                self.call("remove", "--finalized", "--sudo-holder-scan")
            self.assertFalse(scans)
            git(self.wt, "restore", "file")
            code, rows = self.call("remove", "--finalized", "--sudo-holder-scan")
        self.assertEqual(code, 0, rows)
        self.assertEqual(rows[0]["checkout"], "removed")
        self.assertEqual(len(scans), 3)
        for command, kwargs in scans:
            self.assertEqual(command, [
                "/fixture/sudo", "-n", "-u", "#0", "--", "/fixture/lsof", "-nP", "+w", "-F0pfnDi"])
            self.assertTrue(kwargs["sudo_monitor"])
            self.assertLessEqual(kwargs["deadline"], FINISH.safety().DEADLINE)
        self.assertTrue(any("worktree" in command and "remove" in command
                            and command[0] != "/fixture/sudo" for command in commands))
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)

    def test_sudo_failure_or_incomplete_visibility_stops_before_intent_or_unlock(self):
        native = FINISH.safety().supervise
        for code, failure, stdout, reason in (
                (1, None, b"", "sudo-holder-scan-failed"),
                (0, "TimeoutExpired", b"", "sudo-holder-scan-termination-unknown"),
                (0, None, b"p7\0\nfNOFD\0n/proc/7/fd (opendir: Permission denied)\0\n",
                 "manual-holder-visibility-unknown")):
            commands = []

            def observe(command, **kwargs):
                commands.append(command)
                if command[0] == "/fixture/sudo":
                    return types.SimpleNamespace(stdout=stdout, stderr=b"", returncode=code,
                                                 failure=failure, proof={"pid": 123})
                return native(command, **kwargs)

            with self.subTest(reason=reason), \
                    mock.patch.object(FINISH.sys, "platform", "linux"), \
                    mock.patch.object(FINISH, "trusted_holder_binary",
                                      side_effect=lambda name: "/fixture/" + name), \
                    mock.patch.object(FINISH.safety(), "supervise", side_effect=observe):
                with self.assertRaisesRegex(FINISH.Retain, reason):
                    self.call("remove", "--finalized", "--sudo-holder-scan")
            self.assertEqual(sum(command[0] == "/fixture/sudo" for command in commands), 1)
            self.assertFalse(any("worktree" in command and ("unlock" in command or "remove" in command)
                                 for command in commands))
            self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.token)
            with FINISH.ledger(self.state) as db:
                item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
                self.assertEqual(item["state"], "enrolled")
                self.assertIsNone(item["intent"])

    @unittest.skipUnless(shutil.which("lsof"), "native lsof unavailable")
    def test_native_holder_scan_detects_an_open_file(self):
        snap = {"path": str(self.wt), "gitdir": str(self.admin)}
        entry = list(FINISH.safety().file_identity((self.wt / "file").lstat()))
        with (self.wt / "file").open():
            with self.assertRaisesRegex(FINISH.Retain, "pending-departure"):
                FINISH.manual_holders(snap, {"entries": {"file": entry}}, {}, float("inf"))


class MissingFinalizedRemovalTests(unittest.TestCase):
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    finish = LifecycleTests.finish
    tearDown = ReleaseTests.tearDown
    remove = FinalizedRemovalTests.remove

    def setUp(self):
        ReleaseTests.setUp(self)
        self.proofs[self.url]["merged"] = False
        self.finish()
        self.proofs[self.url]["merged"] = True
        self.before_lock = (self.admin / "locked").read_bytes()
        self.original = ReleaseTests.row(self)

    def disappear(self):
        shutil.rmtree(self.wt)

    def assert_retained(self, reason):
        with mock.patch.object(FINISH, "remove_with_intent") as remove:
            with self.assertRaisesRegex(FINISH.Retain, reason):
                self.remove()
        remove.assert_not_called()
        self.assertTrue(self.admin.exists())
        self.assertEqual((self.admin / "locked").read_bytes(), self.before_lock)

    def test_missing_checkout_uses_one_nonforce_removal_and_preserves_survivors(self):
        donor = self.base / "donor"
        donor.mkdir()
        (donor / "keep").write_text("shared dependency")
        (self.wt / "node_modules").symlink_to(donor)
        self.disappear()
        native = FINISH.safety().supervise
        with mock.patch.object(FINISH.safety(), "supervise", wraps=native) as calls:
            result = self.remove()[1][0]
        removals = [call.args[0] for call in calls.call_args_list
                    if "worktree" in call.args[0] and "remove" in call.args[0]]
        self.assertEqual(len(removals), 1)
        self.assertNotIn("--force", removals[0])
        self.assertEqual(removals[0][-3:], ["remove", "--", str(self.wt)])
        self.assertEqual(result["checkout"], "removed", result)
        self.assertFalse(self.admin.exists())
        self.assertNotIn(str(self.wt), git(self.repo, "worktree", "list", "--porcelain"))
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertTrue(self.legacy.exists())
        self.assertEqual((donor / "keep").read_text(), "shared dependency")
        with FINISH.ledger(self.state) as db:
            outcome = json.loads(FINISH.retirement(db, FINISH.get_row(db, self.wt))["result"])
        self.assertEqual(outcome["working_tree"], "already-absent-not-audited")

    def test_missing_container_is_not_recreated(self):
        self.disappear()
        # The fixture's sibling lives under this root, so move it outside the
        # missing container through native Git before removing that container.
        git(self.repo, "worktree", "move", str(self.legacy), str(self.base / "sibling"))
        self.root.rmdir()
        # Missing the managed root itself must refuse; only its child container
        # may already be absent.
        with self.assertRaises(FileNotFoundError):
            self.remove()
        self.assertTrue(self.admin.exists())

    def test_unfinished_unreleased_foreign_and_unmerged_owners_refuse(self):
        self.disappear()
        with mock.patch.dict(os.environ, {"GWT_OWNER_ID": "other-owner"}):
            self.assert_retained("recorded-owner")
        self.proofs[self.url]["merged"] = False
        self.assert_retained("pr-closed-unmerged")
        self.proofs[self.url]["merged"] = True
        with FINISH.ledger(self.state) as db, db:
            db.execute("DELETE FROM owner_releases WHERE worktree_id=?", (self.original["id"],))
        self.assert_retained("awaiting-owner-release")
        with FINISH.ledger(self.state) as db, db:
            db.execute("UPDATE owners SET completed=0 WHERE worktree_id=?", (self.original["id"],))
        self.assert_retained("awaiting-owner-completion")

    def test_staged_only_index_bytes_refuse(self):
        (self.wt / "file").write_text("uncommitted staged work")
        git(self.wt, "add", "file")
        self.disappear()
        self.assert_retained("staged-or-index-only-changes")

    def test_unanchored_fetch_head_refuses(self):
        commit = git(self.repo, "commit-tree", "HEAD^{tree}", "-m", "unretained")
        (self.admin / "FETCH_HEAD").write_text(commit + "\t\tfixture\n")
        self.disappear()
        self.assert_retained("fetch-head-recovery-required")

    def test_reachable_ancestor_in_admin_survives_through_branch(self):
        ancestor = self.head
        (self.wt / "file").write_text("next committed revision\n")
        git(self.wt, "commit", "-am", "next revision")
        self.head = git(self.wt, "rev-parse", "HEAD")
        self.proofs[self.url] = self.proof(self.url, merged=False)
        self.finish()
        self.proofs[self.url]["merged"] = True
        (self.admin / "ORIG_HEAD").write_text(ancestor + "\n")
        self.disappear()
        self.assertEqual(self.remove()[1][0]["checkout"], "removed")
        self.assertEqual(git(self.repo, "merge-base", ancestor, "released-feature"), ancestor)

    def test_wrapper_forwards_only_exact_registered_missing_path(self):
        self.disappear()
        env = dict(os.environ, DOTFILES_WORKTREES_ROOT=str(self.root),
                   DOTFILES_GWT_FINISH_STATE=str(self.state), TMUX="")
        script = ('source "$1"; cd "$2"; '
                  '_gwt_finish_tool() { printf "%s\\n" "$@"; }; '
                  '_gwt_tmux_sync_context() { :; }; gwt rm "$3" --finalized')
        command = ["zsh", "-c", script, "fixture", str(ROOT / "functions/gwt/gwt.zsh"), str(self.repo)]
        result = subprocess.run([*command, str(self.wt)], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.splitlines(), ["remove", "--worktree", str(self.wt), "--finalized"])
        result = subprocess.run([*command, str(self.wt) + "-unknown"], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("--worktree", result.stdout)

    def test_divergent_draft_and_unknown_admin_refuse(self):
        self.disappear()
        saved = self.admin / "COMMIT_EDITMSG"
        saved.write_text("unsaved draft\n")
        self.assert_retained("commit-message-recovery-required")
        saved.unlink()
        (self.admin / "private-session").write_text("must survive")
        self.assert_retained("admin-recovery-content-unknown")

    def test_pin_refuses(self):
        self.disappear()
        with FINISH.ledger(self.state) as db, db:
            db.execute("INSERT INTO pins VALUES (?, ?, ?)", (self.original["id"], "owner-1", "needed"))
        self.assert_retained("recovery-pin-present")

    def test_pending_recovery_refuses(self):
        self.disappear()
        with FINISH.ledger(self.state) as db, db:
            db.execute("CREATE TABLE missing_recoveries (worktree_id TEXT PRIMARY KEY, fact TEXT)")
            db.execute("INSERT INTO missing_recoveries VALUES (?, ?)",
                       (self.original["id"], json.dumps({"result": None})))
        self.assert_retained("missing-recovery-intent-present")

    def test_appeared_root_or_symlink_after_admission_never_reaches_native_removal(self):
        self.disappear()
        admit = FINISH.finalized_admission
        calls = 0
        def appear(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.wt.parent.mkdir(exist_ok=True)
                self.wt.symlink_to(self.legacy)
            return admit(*args, **kwargs)
        with mock.patch.object(FINISH, "finalized_admission", side_effect=appear):
            with self.assertRaisesRegex(FINISH.Retain, "directory-identity-unsafe"):
                self.remove()
        self.assertTrue(self.wt.is_symlink())
        self.assertTrue((self.legacy / "file").exists())
        self.assertEqual((self.admin / "locked").read_bytes(), self.before_lock)

    def test_native_failure_restores_lock_and_preserves_nonretryable_intent(self):
        self.disappear()
        native = FINISH.safety().supervise
        def fail_removal(command, **kwargs):
            if "worktree" in command and "remove" in command:
                return types.SimpleNamespace(proof={"returncode": 1}, failure=None, returncode=1)
            return native(command, **kwargs)
        with mock.patch.object(FINISH.safety(), "supervise", side_effect=fail_removal):
            result = self.remove()[1][0]
        self.assertEqual(result["checkout"], "unknown")
        self.assertEqual((self.admin / "locked").read_bytes(), self.before_lock)
        with FINISH.ledger(self.state) as db:
            retirement = FINISH.retirement(db, FINISH.get_row(db, self.wt))
            self.assertTrue(json.loads(retirement["result"])["native_lock_preserved"])
            self.assertIsNotNone(retirement["intent"])
        with self.assertRaisesRegex(FINISH.Retain, "intent"):
            self.remove()


class MissingContainerFinalizedRemovalTests(MissingFinalizedRemovalTests):
    recovery_container = True

    def disappear(self):
        super().disappear()
        self.wt.parent.rmdir()


class MissingCancelledRemovalTests(unittest.TestCase):
    """An active owner's checkout vanished before any finish proof was recorded."""
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    tearDown = ReleaseTests.tearDown
    remove = FinalizedRemovalTests.remove

    def setUp(self):
        ReleaseTests.setUp(self)
        git(self.repo, "update-ref", "refs/remotes/origin/released-feature", self.head)
        self.before_lock = (self.admin / "locked").read_bytes()
        self.original = ReleaseTests.row(self)
        shutil.rmtree(self.wt)

    def cancel(self):
        return self.call("cancel", "--reason", "superseded upstream")[1][0]

    def assert_not_cancelled(self, reason):
        with self.assertRaisesRegex(FINISH.Retain, reason):
            self.cancel()
        self.assertNotIn("cancelled_owners", self.call("status")[1][0])
        self.assertEqual((self.admin / "locked").read_bytes(), self.before_lock)

    def assert_retained(self, reason):
        with mock.patch.object(FINISH, "remove_with_intent") as remove:
            with self.assertRaisesRegex(FINISH.Retain, reason):
                self.remove()
        remove.assert_not_called()
        self.assertTrue(self.admin.exists())
        self.assertEqual((self.admin / "locked").read_bytes(), self.before_lock)

    def test_recorded_owner_cancels_pushed_tip_then_finalized_closeout_removes_registration(self):
        self.assertIsNone(self.original["finish_head"])
        self.assert_retained("missing-closeout-boundary-changed")
        cancelled = self.cancel()
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(cancelled["cancelled_owners"][0]["head"], self.head)
        self.assertEqual(self.cancel(), cancelled)
        code, rows = self.remove()
        self.assertEqual(code, 0)
        self.assertEqual(rows[0]["checkout"], "removed", rows[0])
        self.assertFalse(self.admin.exists())
        self.assertNotIn(str(self.wt), git(self.repo, "worktree", "list", "--porcelain"))
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertTrue(self.legacy.exists())
        status = self.call("status")[1][0]
        self.assertEqual((status["state"], status["checkout"]), ("cancelled", "removed"))
        self.assertEqual(status["cancelled_owners"], cancelled["cancelled_owners"])
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            outcome = json.loads(FINISH.retirement(db, row)["result"])
            self.assertEqual([owner["owner"] for owner in FINISH.owner_rows(db, row["id"])], ["owner-1"])
        self.assertEqual(outcome["working_tree"], "already-absent-not-audited")

    def test_unpushed_or_unpublished_tip_refuses_cancellation(self):
        commit = git(self.repo, "commit-tree", self.head + "^{tree}", "-p", self.head, "-m", "local only")
        git(self.repo, "update-ref", "refs/heads/released-feature", commit, self.head)
        self.assert_not_cancelled("head-not-on-remote")
        git(self.repo, "update-ref", "refs/heads/released-feature", self.head, commit)
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/released-feature")
        self.assert_not_cancelled("head-not-on-remote")

    def test_local_only_checkpoint_requires_explicit_custody_and_preserves_branch(self):
        branch = "refs/heads/released-feature"
        head = git(self.repo, "commit-tree", self.head + "^{tree}", "-p", self.head,
                   "-m", "local checkpoint")
        git(self.repo, "update-ref", branch, head, self.head)
        index_sha = hashlib.sha256((self.admin / "index").read_bytes()).hexdigest()
        args = ("--keep-local-checkpoint", branch, "--expected-head", head,
                "--index-sha256", index_sha)
        self.assert_not_cancelled("head-not-on-remote")
        cancelled = self.call("cancel", "--reason", "superseded upstream", *args)[1][0]
        self.assertEqual(cancelled["local_checkpoint_custody"][0]["head"], head)
        self.assertEqual(self.call("cancel", "--reason", "superseded upstream", *args)[1][0],
                         cancelled)
        with mock.patch.object(FINISH, "remove_with_intent") as remove:
            with self.assertRaisesRegex(FINISH.Retain, "explicit-option"):
                self.remove()
        remove.assert_not_called()
        reflog = self.repo / ".git/logs/refs/heads/released-feature"
        prior_log = reflog.read_bytes()
        with mock.patch.object(FINISH, "manual_holders"):
            result = self.call("remove", "--finalized", "--keep-local-checkpoint", branch)[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertEqual(git(self.repo, "rev-parse", branch), head)
        self.assertEqual(reflog.read_bytes(), prior_log)
        self.assertEqual(result["local_checkpoint_closeout"]["working_tree"],
                         "already-absent-not-audited")

    def test_local_checkpoint_rejects_wrong_owner_and_index_drift(self):
        branch = "refs/heads/released-feature"
        head = git(self.repo, "commit-tree", self.head + "^{tree}", "-p", self.head,
                   "-m", "local checkpoint")
        git(self.repo, "update-ref", branch, head, self.head)
        index = self.admin / "index"
        index_sha = hashlib.sha256(index.read_bytes()).hexdigest()
        args = ("--keep-local-checkpoint", branch, "--expected-head", head,
                "--index-sha256", index_sha)
        with mock.patch.dict(os.environ, {"GWT_OWNER_ID": "other-owner"}):
            with self.assertRaisesRegex(FINISH.Retain, "known-owner"):
                self.call("cancel", "--reason", "superseded", *args)
        index.write_bytes(index.read_bytes() + b"changed")
        with self.assertRaisesRegex(FINISH.Retain, "index"):
            self.call("cancel", "--reason", "superseded", *args)
        self.assertNotIn("cancelled_owners", self.call("status")[1][0])

    def test_local_checkpoint_ref_drift_after_cancel_refuses_native_removal(self):
        branch = "refs/heads/released-feature"
        head = git(self.repo, "commit-tree", self.head + "^{tree}", "-p", self.head,
                   "-m", "local checkpoint")
        git(self.repo, "update-ref", branch, head, self.head)
        index_sha = hashlib.sha256((self.admin / "index").read_bytes()).hexdigest()
        self.call("cancel", "--reason", "superseded", "--keep-local-checkpoint", branch,
                  "--expected-head", head, "--index-sha256", index_sha)
        later = git(self.repo, "commit-tree", head + "^{tree}", "-p", head, "-m", "later")
        git(self.repo, "update-ref", branch, later, head)
        with mock.patch.object(FINISH, "remove_with_intent") as native:
            with self.assertRaisesRegex(FINISH.Retain, "head-changed|registration-changed"):
                self.call("remove", "--finalized", "--keep-local-checkpoint", branch)
        native.assert_not_called()
        self.assertTrue(self.admin.exists())

    def test_local_checkpoint_index_drift_during_admission_refuses_native_removal(self):
        branch = "refs/heads/released-feature"
        head = git(self.repo, "commit-tree", self.head + "^{tree}", "-p", self.head,
                   "-m", "local checkpoint")
        git(self.repo, "update-ref", branch, head, self.head)
        index = self.admin / "index"
        index_sha = hashlib.sha256(index.read_bytes()).hexdigest()
        self.call("cancel", "--reason", "superseded", "--keep-local-checkpoint", branch,
                  "--expected-head", head, "--index-sha256", index_sha)
        calls = 0

        def drift(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                index.write_bytes(index.read_bytes() + b"changed")

        supervise = FINISH.safety().supervise
        with mock.patch.object(FINISH, "manual_holders", side_effect=drift), \
                mock.patch.object(FINISH.safety(), "supervise", wraps=supervise) as native:
            result = self.call("remove", "--finalized", "--keep-local-checkpoint", branch)[1][0]
        self.assertFalse(any("worktree" in call.args[0] and "remove" in call.args[0]
                             for call in native.call_args_list))
        self.assertEqual(result["checkout"], "unknown", result)
        with FINISH.ledger(self.state) as db:
            item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
            self.assertEqual(item["state"], "incomplete")
            self.assertFalse(json.loads(item["result"])["native_attempted"])

    def test_local_checkpoint_post_native_ref_drift_stays_unknown(self):
        branch = "refs/heads/released-feature"
        head = git(self.repo, "commit-tree", self.head + "^{tree}", "-p", self.head,
                   "-m", "local checkpoint")
        git(self.repo, "update-ref", branch, head, self.head)
        index_sha = hashlib.sha256((self.admin / "index").read_bytes()).hexdigest()
        self.call("cancel", "--reason", "superseded", "--keep-local-checkpoint", branch,
                  "--expected-head", head, "--index-sha256", index_sha)
        later = git(self.repo, "commit-tree", head + "^{tree}", "-p", head, "-m", "later")
        native = FINISH.safety().supervise

        def drift(command, **kwargs):
            result = native(command, **kwargs)
            if "worktree" in command and "remove" in command:
                git(self.repo, "update-ref", branch, later, head)
            return result

        with mock.patch.object(FINISH, "manual_holders"), \
                mock.patch.object(FINISH.safety(), "supervise", side_effect=drift):
            result = self.call("remove", "--finalized", "--keep-local-checkpoint", branch)[1][0]
        self.assertEqual(result["checkout"], "unknown", result)
        with FINISH.ledger(self.state) as db:
            outcome = json.loads(FINISH.retirement(db, FINISH.get_row(db, self.wt))["result"])
        self.assertTrue(outcome["native_attempted"])
        self.assertFalse(outcome["branch_preserved"])
        self.assertEqual(outcome["local_checkpoint_custody"]["head"], head)

    def test_local_checkpoint_post_native_symbolic_branch_stays_unknown(self):
        branch = "refs/heads/released-feature"
        head = git(self.repo, "commit-tree", self.head + "^{tree}", "-p", self.head,
                   "-m", "local checkpoint")
        git(self.repo, "update-ref", branch, head, self.head)
        git(self.repo, "update-ref", "refs/heads/alias", head)
        index_sha = hashlib.sha256((self.admin / "index").read_bytes()).hexdigest()
        self.call("cancel", "--reason", "superseded", "--keep-local-checkpoint", branch,
                  "--expected-head", head, "--index-sha256", index_sha)
        native = FINISH.safety().supervise

        def replace(command, **kwargs):
            result = native(command, **kwargs)
            if "worktree" in command and "remove" in command:
                git(self.repo, "symbolic-ref", branch, "refs/heads/alias")
            return result

        with mock.patch.object(FINISH, "manual_holders"), \
                mock.patch.object(FINISH.safety(), "supervise", side_effect=replace):
            result = self.call("remove", "--finalized", "--keep-local-checkpoint", branch)[1][0]
        self.assertEqual(result["checkout"], "unknown", result)
        self.assertEqual(git(self.repo, "rev-parse", branch), head)

    def test_local_checkpoint_old_owner_update_clears_custody(self):
        branch = "refs/heads/released-feature"
        head = git(self.repo, "commit-tree", self.head + "^{tree}", "-p", self.head,
                   "-m", "local checkpoint")
        git(self.repo, "update-ref", branch, head, self.head)
        index_sha = hashlib.sha256((self.admin / "index").read_bytes()).hexdigest()
        self.call("cancel", "--reason", "superseded", "--keep-local-checkpoint", branch,
                  "--expected-head", head, "--index-sha256", index_sha)
        with FINISH.ledger(self.state) as db, db:
            db.execute("UPDATE owners SET completed=1 WHERE worktree_id=?", (self.original["id"],))
            self.assertIsNone(FINISH.checkpoint_custody(db, self.original, "owner-1"))
        with self.assertRaisesRegex(FINISH.Retain, "recorded-custody"):
            self.call("remove", "--finalized", "--keep-local-checkpoint", branch)

    def test_local_checkpoint_late_path_appearance_refuses_native_removal(self):
        branch = "refs/heads/released-feature"
        head = git(self.repo, "commit-tree", self.head + "^{tree}", "-p", self.head,
                   "-m", "local checkpoint")
        git(self.repo, "update-ref", branch, head, self.head)
        index_sha = hashlib.sha256((self.admin / "index").read_bytes()).hexdigest()
        self.call("cancel", "--reason", "superseded", "--keep-local-checkpoint", branch,
                  "--expected-head", head, "--index-sha256", index_sha)
        calls = 0

        def appear(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.wt.mkdir()

        supervise = FINISH.safety().supervise
        with mock.patch.object(FINISH, "manual_holders", side_effect=appear), \
                mock.patch.object(FINISH.safety(), "supervise", wraps=supervise) as native:
            result = self.call("remove", "--finalized", "--keep-local-checkpoint", branch)[1][0]
        self.assertEqual(result["checkout"], "unknown", result)
        self.assertTrue(self.admin.exists())
        self.assertFalse(any("worktree" in call.args[0] and "remove" in call.args[0]
                             for call in native.call_args_list))

    def test_local_checkpoint_cancel_rollback_keeps_owner_unfinished(self):
        branch = "refs/heads/released-feature"
        head = git(self.repo, "commit-tree", self.head + "^{tree}", "-p", self.head,
                   "-m", "local checkpoint")
        git(self.repo, "update-ref", branch, head, self.head)
        index_sha = hashlib.sha256((self.admin / "index").read_bytes()).hexdigest()
        with FINISH.ledger(self.state) as db:
            FINISH.ensure_cancellation_schema(db)
            db.execute("CREATE TRIGGER reject_checkpoint BEFORE INSERT ON local_checkpoint_custody "
                       "BEGIN SELECT RAISE(ABORT, 'proof-lost'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "proof-lost"):
            self.call("cancel", "--reason", "superseded", "--keep-local-checkpoint", branch,
                      "--expected-head", head, "--index-sha256", index_sha)
        status = self.call("status")[1][0]
        self.assertNotIn("cancelled_owners", status)
        self.assertEqual(status["owners"], [{"owner": "owner-1", "completed": 0}])

    def test_local_checkpoint_refuses_pending_recovery(self):
        branch = "refs/heads/released-feature"
        head = git(self.repo, "commit-tree", self.head + "^{tree}", "-p", self.head,
                   "-m", "local checkpoint")
        git(self.repo, "update-ref", branch, head, self.head)
        index_sha = hashlib.sha256((self.admin / "index").read_bytes()).hexdigest()
        args = ("--reason", "superseded", "--keep-local-checkpoint", branch,
                "--expected-head", head, "--index-sha256", index_sha)
        with FINISH.ledger(self.state) as db, db:
            db.execute("CREATE TABLE missing_recoveries (worktree_id TEXT PRIMARY KEY, fact TEXT)")
            db.execute("INSERT INTO missing_recoveries VALUES (?, ?)",
                       (self.original["id"], json.dumps({"result": None})))
        with self.assertRaisesRegex(FINISH.Retain, "missing-recovery-intent-present"):
            self.call("cancel", *args)
        with FINISH.ledger(self.state) as db, db:
            db.execute("DELETE FROM missing_recoveries")
            db.execute("CREATE TABLE failed_creations (worktree_id TEXT, creation_token TEXT, "
                       "intent TEXT, result TEXT)")
            db.execute("INSERT INTO failed_creations VALUES (?, 'token', '{}', NULL)",
                       (self.original["id"],))
        with self.assertRaisesRegex(FINISH.Retain, "failed-creation-recovery-present"):
            self.call("cancel", *args)

    def test_foreign_owner_appeared_directory_and_changed_registration_refuse(self):
        with mock.patch.dict(os.environ, {"GWT_OWNER_ID": "other-owner"}):
            self.assert_not_cancelled("known-owner-required")
        git(self.repo, "worktree", "unlock", str(self.wt))
        git(self.repo, "worktree", "lock", "--reason", "gwt-finish.v2:" + str(uuid.uuid4()), str(self.wt))
        with self.assertRaisesRegex(FINISH.Retain, "registration-changed"):
            self.cancel()
        git(self.repo, "worktree", "unlock", str(self.wt))
        (self.admin / "locked").write_bytes(self.before_lock)
        self.wt.mkdir()
        # A present directory takes the ordinary route and its identity check.
        with self.assertRaisesRegex(FINISH.Retain, "not-a-linked-worktree"):
            self.cancel()
        self.wt.rmdir()
        self.assertEqual(self.cancel()["state"], "cancelled")

    def test_closeout_rechecks_remote_owner_and_terminal_cancellation(self):
        self.cancel()
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/released-feature")
        self.assert_retained("head-not-on-remote")
        git(self.repo, "update-ref", "refs/remotes/origin/released-feature", self.head)
        with mock.patch.dict(os.environ, {"GWT_OWNER_ID": "other-owner"}):
            self.assert_retained("recorded-owner")
        with FINISH.ledger(self.state) as db, db:
            db.execute("INSERT INTO owners(worktree_id, owner, completed) VALUES (?, ?, 0)",
                       (self.original["id"], "owner-2"))
        self.assert_retained("awaiting-owner-completion")
        with FINISH.ledger(self.state) as db, db:
            db.execute("DELETE FROM owners WHERE owner='owner-2'")
            db.execute("INSERT INTO pins VALUES (?, ?, ?)", (self.original["id"], "owner-1", "needed"))
        self.assert_retained("recovery-pin-present")
        with FINISH.ledger(self.state) as db, db:
            db.execute("DELETE FROM pins")
            db.execute("UPDATE owner_cancellations SET head=?", ("b" * 40,))
        self.assert_retained("boundary-changed|registration-changed|head-changed")


class QualificationFetchLeaseTests(unittest.TestCase):
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    finish = ReleaseTests.finish
    setUp = ReleaseTests.setUp
    tearDown = ReleaseTests.tearDown
    row = ReleaseTests.row
    remove = FinalizedRemovalTests.remove

    def carrier(self):
        path = self.repo / ".git" / FINISH.QualificationFetchLease.name
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        return path

    def assert_contender(self, path, *, busy):
        # A separate process must see the same advisory exclusion, including
        # during native Git execution and the final preservation readbacks.
        code = """import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDONLY)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(11)
finally:
    os.close(fd)
"""
        result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True)
        self.assertEqual(result.returncode, 11 if busy else 0, result.stderr)

    def test_idle_carrier_is_preserved_and_held_through_native_remove_and_readback(self):
        path = self.carrier()
        before = FINISH.safety().file_identity(path.stat())
        native = FINISH.safety().supervise
        phases = []

        def observe(command, **kwargs):
            if "worktree" in command and ("unlock" in command or "remove" in command):
                self.assert_contender(path, busy=True)
                phases.append("native")
                if "remove" in command:
                    self.assertEqual(len(kwargs["pass_fds"]), 1)
                    self.assertEqual(FINISH.safety().file_identity(
                        os.fstat(kwargs["pass_fds"][0])), before)
            result = native(command, **kwargs)
            if "worktree" in command and "list" in command and not self.wt.exists():
                self.assert_contender(path, busy=True)
                phases.append("readback")
            return result

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=observe):
            result = self.remove()[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertIn("native", phases)
        self.assertIn("readback", phases)
        self.assertEqual(FINISH.safety().file_identity(path.stat()), before)
        self.assert_contender(path, busy=False)
        self.assertTrue(self.legacy.exists())

    def test_held_carrier_refuses_before_removal_intent(self):
        path = self.carrier()
        with path.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(FINISH.Retain, "qualification-fetch-lease-busy"):
                self.remove()
        with FINISH.ledger(self.state) as db:
            item = FINISH.retirement(db, FINISH.get_row(db, self.wt))
            self.assertEqual(item["state"], "enrolled")
            self.assertIsNone(item["intent"])
        self.assertTrue((self.admin / "locked").exists())
        self.assert_contender(path, busy=False)

    def test_idle_carrier_is_admitted_by_report_and_automatic_paths(self):
        path = self.carrier()
        before = FINISH.safety().file_identity(path.stat())
        row = {**self.row(), "finish_head": self.head}
        self.assertEqual(FINISH.local_checks(row)["head"], self.head)
        result = self.finish()[1][0]
        self.assertEqual(result["checkout"], "removed", result)
        self.assertEqual(FINISH.safety().file_identity(path.stat()), before)

    def test_absent_carrier_is_never_created(self):
        path = self.repo / ".git" / FINISH.QualificationFetchLease.name
        self.assertEqual(self.remove()[1][0]["checkout"], "removed")
        self.assertFalse(path.exists())

    def test_generic_and_worktree_admin_locks_remain_blocking(self):
        carrier = self.carrier()
        for path in (self.repo / ".git/index.lock", self.repo / ".git/unknown.lock",
                     self.admin / FINISH.QualificationFetchLease.name):
            with self.subTest(path=path.name):
                path.touch(mode=0o600)
                try:
                    with self.assertRaisesRegex(FINISH.Retain, "git-lock-present"):
                        self.remove()
                finally:
                    path.unlink()
        self.assert_contender(carrier, busy=False)

    def test_malformed_carriers_are_never_exempted(self):
        path = self.repo / ".git" / FINISH.QualificationFetchLease.name
        snap = FINISH.revalidate(self.row())
        for kind in ("content", "mode", "hardlink", "symlink", "directory", "fifo"):
            with self.subTest(kind=kind):
                other = self.base / "carrier-target"
                if kind in ("content", "mode", "hardlink"):
                    self.carrier()
                    if kind == "content":
                        path.write_bytes(b"active")
                    elif kind == "mode":
                        path.chmod(0o644)
                    else:
                        os.link(path, other)
                elif kind == "symlink":
                    other.touch(mode=0o600)
                    path.symlink_to(other)
                elif kind == "directory":
                    path.mkdir(mode=0o600)
                else:
                    os.mkfifo(path, 0o600)
                try:
                    with self.assertRaises((FINISH.Retain, OSError)):
                        with FINISH.QualificationFetchLease(snap):
                            self.fail("malformed carrier admitted")
                finally:
                    path.rmdir() if kind == "directory" else path.unlink()
                    if other.exists():
                        other.unlink()

    def test_wrong_common_directory_identity_is_rejected(self):
        snap = FINISH.revalidate(self.row())
        snap["common_id"] = [snap["common_id"][0], -1]
        with self.assertRaisesRegex(FINISH.Retain, "qualification-fetch-common-changed"):
            with FINISH.QualificationFetchLease(snap):
                self.fail("wrong common directory admitted")

    def test_producer_chmod_before_waiting_for_flock_is_drift(self):
        path = self.carrier()
        snap = FINISH.revalidate(self.row())
        with FINISH.QualificationFetchLease(snap) as lease:
            result = subprocess.run([sys.executable, "-c", """import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
os.fchmod(fd, 0o600)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(11)
finally:
    os.close(fd)
""", str(path)], capture_output=True)
            self.assertEqual(result.returncode, 11, result.stderr)
            with self.assertRaisesRegex(FINISH.Retain, "qualification-fetch-carrier-changed"):
                lease.validate()

    def test_native_child_retains_lease_after_parent_death(self):
        path = self.carrier()
        ready, release, done = (self.base / name for name in ("ready", "release", "done"))
        child_code = """import os, pathlib, time
ready, release, done = map(pathlib.Path, ARGS)
ready.write_text(str(os.getpid()))
until = time.monotonic() + 15
while not release.exists() and time.monotonic() < until:
    time.sleep(0.01)
done.touch()
""".replace("ARGS", repr([str(ready), str(release), str(done)]))
        parent_code = """import os, runpy, shlex, sys
tool = runpy.run_path(sys.argv[1])
snap = {"common": sys.argv[2], "common_id": tool["identity"](sys.argv[2])}
command = [tool["shutil"].which("git"), "-c",
    "alias.lease-fixture=!" + shlex.join([sys.executable, "-c", sys.argv[3]]), "lease-fixture"]
with tool["QualificationFetchLease"](snap) as lease:
    tool["safety"]().supervise(command, cwd="/",
        env=dict(os.environ), timeout=20, pass_fds=(lease.descriptor,))
"""
        parent = subprocess.Popen([sys.executable, "-c", parent_code, str(TOOL),
                                   str(path.parent), child_code],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            until = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < until and parent.poll() is None:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "fixture child failed to start")
            self.assert_contender(path, busy=True)
            parent.kill()  # Only this test's exact disposable parent process.
            parent.wait(timeout=3)
            self.assert_contender(path, busy=True)
            release.touch()
            until = time.monotonic() + 5
            while not done.exists() and time.monotonic() < until:
                time.sleep(0.01)
            self.assertTrue(done.exists(), "fixture child failed to finish")
            # The marker precedes interpreter teardown and descriptor close.
            with path.open("rb") as contender:
                while True:
                    try:
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= until:
                            self.fail("fixture child retained the lease after exit")
                        time.sleep(0.01)
        finally:
            release.touch()
            if parent.poll() is None:
                parent.kill()
            parent.wait(timeout=3)
            parent.stderr.close()

    def test_carrier_replacement_between_admissions_never_unlocks(self):
        path = self.carrier()
        admission = FINISH.finalized_admission
        calls = []

        def replace(*args, **kwargs):
            result = admission(*args, **kwargs)
            if not calls:
                path.rename(self.base / "original-carrier")
                self.carrier()
            calls.append(None)
            return result

        with mock.patch.object(FINISH, "finalized_admission", side_effect=replace):
            with self.assertRaisesRegex(FINISH.Retain, "qualification-fetch-carrier-changed"):
                self.remove()
        self.assertTrue((self.admin / "locked").exists())
        self.assertTrue(self.wt.exists())

    def test_carrier_appearance_after_final_admission_keeps_intent_absent(self):
        admission = FINISH.finalized_admission
        calls = []

        def appear(*args, **kwargs):
            result = admission(*args, **kwargs)
            if calls:
                self.carrier()
            calls.append(None)
            return result

        with mock.patch.object(FINISH, "finalized_admission", side_effect=appear):
            with self.assertRaisesRegex(FINISH.Retain, "qualification-fetch-carrier-changed"):
                self.remove()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.call("status", "--read-only")[1][0]["retirement_state"], "enrolled")
        self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.token)

    def test_carrier_drift_after_native_removal_is_incomplete(self):
        path = self.carrier()
        native = FINISH.safety().supervise

        def replace(command, **kwargs):
            result = native(command, **kwargs)
            if "worktree" in command and "remove" in command:
                path.rename(self.base / "original-carrier")
                self.carrier()
            return result

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=replace):
            result = self.remove()[1][0]
        self.assertFalse(self.wt.exists())
        self.assertEqual(result["checkout"], "unknown", result)
        self.assertEqual(result["retirement_state"], "incomplete")
        self.assertEqual(result["reason"], "qualification-fetch-carrier-changed")


class ManualHolderTests(unittest.TestCase):
    def setUp(self):
        self.snap = {"path": "/fixture/worktree", "gitdir": "/fixture/admin"}
        self.contents = {"entries": {"file": [9, 22, FINISH.stat.S_IFREG],
                                     "node_modules": [9, 24, FINISH.stat.S_IFLNK]}}
        self.admin = {"index": [[9, 23, FINISH.stat.S_IFREG], None]}

    def observe(self, output, *, errors=b"", code=0, failure=None, sudo=False, platform="linux"):
        result = types.SimpleNamespace(
            stdout=output, stderr=errors, returncode=code,
            failure=type(failure).__name__ if failure else None,
            proof={"pid": None, "reaped": False, "group_absent": None},
            failure_code="os-error" if isinstance(failure, TimeoutError) else "output-limit" if failure else None,
            failure_errno=None)
        with mock.patch.object(FINISH.sys, "platform", platform), \
                mock.patch.object(FINISH, "trusted_holder_binary",
                                  side_effect=["/usr/bin/sudo", "/usr/sbin/lsof"]), \
                mock.patch.object(FINISH.shutil, "which", return_value="/usr/sbin/lsof"), \
                mock.patch.object(FINISH.safety(), "supervise", return_value=result) as child:
            try:
                FINISH.manual_holders(self.snap, self.contents, self.admin, float("inf"),
                                      sudo_holder_scan_enabled=sudo)
            finally:
                self.assertEqual(child.call_count, 1)
                prefix = ["/usr/bin/sudo", "-n", "-u", "#0", "--"] if sudo else []
                self.assertEqual(child.call_args.args[0], prefix + ["/usr/sbin/lsof", "-nP", "+w", "-F0pfnDi"])
                self.assertEqual(child.call_args.kwargs["limit"], 32 * 1024 * 1024)

    def test_linux_root_absence_is_one_record_not_process_clearance(self):
        for absent in (b"ftxt\0n/proc/7/exe",
                       b"f0\0n/proc/7/fd/0 (readlink: No such file or directory)",
                       b"f3\0n/proc/7/fd/3 (readlink: No such file or directory)",
                       b"f12345\0n/proc/7/fd/12345 (readlink: No such file or directory)",
                       b"f27\0n/proc/7/task/11/fd/27 (readlink: No such file or directory)",
                       b"f0\0n/proc/7/task/12345/fd/0 (readlink: No such file or directory)"):
            output = b"p7\0\n" + absent + b"\0\n"
            with self.subTest(absent=absent):
                self.observe(output, sudo=True)
            for record, reason in (
                    (b"f3\0D0x9\0i22\0n/external/hardlink", "pending-departure"),
                    (b"fcwd\0D0x8\0i22\0n/fixture/admin", "pending-departure"),
                    (b"fmem\0D0x9\0i23\0n/external/admin-alias", "pending-departure"),
                    (b"p8\0\nf3\0n/fixture/worktree/file", "pending-departure"),
                    (b"fNOFD\0n/proc/7/fd (opendir: Permission denied)", "visibility-unknown"),
                    (b"f3\0n/elsewhere (stat: Permission denied)", "visibility-unknown")):
                with self.subTest(absent=absent, record=record):
                    with self.assertRaisesRegex(FINISH.Retain, reason):
                        self.observe(output + record + b"\0\n", sudo=True)

    def test_linux_root_absence_requires_exact_record_and_privilege(self):
        records = [
                b"ftxt\0n/proc/8/exe", b"ftxt\0n/proc/07/exe", b"ftxt\0n/proc/7/exe/other",
                b"ftxt\0n/proc/7/../7/exe", b"ftxt\0n/elsewhere/exe",
                b"ftxt\0D0x8\0n/proc/7/exe", b"ftxt\0i22\0n/proc/7/exe",
                b"ftxt\0n/proc/7/exe (readlink: No such file or directory)",
                b"ftxt\0n/proc/7/exe (stat: Permission denied)",
                b"ftxt\0n/proc/7/exe\0xextra", b"ftxt\0n/proc/7/exe\0n/proc/7/exe",
                b"fcwd\0n/proc/7/exe", b"fmem\0n/proc/7/exe",
                b"p8\0\nftxt\0n/proc/7/exe"]
        missing = b" (readlink: No such file or directory)"
        valid_fd = b"f3\0n/proc/7/fd/3" + missing
        records += [b"f3\0n" + path + missing for path in (
            b"/proc/8/fd/3", b"/proc/07/fd/3", b"/proc/7/fd/4", b"/proc/7/fd/03",
            b"/proc/7/fd/3/other", b"/proc/7/fd/../fd/3", b"/elsewhere/3")]
        records += [b"f" + fd + b"\0n/proc/7/fd/" + fd + missing
                    for fd in (b"00", b"03", b"+3", b"-1", b"3u", b"*003", b"fp.")]
        records += [b"f3\0" + fields + b"n/proc/7/fd/3" + missing for fields in (
            b"D0x8\0", b"i22\0", b"D0x8\0i22\0", b"Dinvalid\0", b"iinvalid\0", b"D\0")]
        records += [b"f3\0n/proc/7/fd/3" + error for error in (
            b" (readlink: Permission denied)", b" (readlink: Input/output error)",
            b" (stat: No such file or directory)", missing + b" extra", missing + missing)]
        records += [valid_fd + b"\0xextra", valid_fd + b"\0n/proc/7/fd/3" + missing,
                    b"p8\0\n" + valid_fd]
        records += [record.replace(b"/fd/", b"/task/11/fd/") for record in records if b"/fd/" in record]
        records += [b"f3\0n/proc/7/task/" + tid + b"/fd/3" + missing
                    for tid in (b"0", b"01", b"+11", b"-11", b"", b"11/extra", b"11/task/12")]
        for record in records:
            with self.subTest(record=record):
                with self.assertRaisesRegex(FINISH.Retain, "visibility-unknown"):
                    self.observe(b"p7\0\n" + record + b"\0\n", sudo=True)
        for platform, sudo in (("linux", False), ("darwin", False), ("freebsd14", False)):
            for record in (b"ftxt\0n/proc/7/exe", valid_fd, b"f0\0n/proc/7/fd/0" + missing,
                           b"f3\0n/proc/7/task/11/fd/3" + missing):
                with self.subTest(platform=platform, sudo=sudo, record=record):
                    with self.assertRaisesRegex(FINISH.Retain, "visibility-unknown"):
                        self.observe(b"p7\0\n" + record + b"\0\n", platform=platform, sudo=sudo)

    def test_linux_mem_enoent_retains_inode_and_deannotated_path_matching(self):
        self.contents["discarded_inodes"] = [[9, 25]]
        for sudo in (False, True):
            prefix = b"p7\0\n" + (b"ftxt\0n/proc/7/exe\0\n" if sudo else b"")
            with self.subTest(sudo=sudo):
                self.observe(prefix + b"fmem\0D0x8\0i22\0n/elsewhere"
                             b" (stat: No such file or directory)\0\n", sudo=sudo)
            for identity, name in (
                    (b"D0x9\0i22", b"/external/checkout-alias"),
                    (b"D0x9\0i23", b"/external/admin-alias"),
                    (b"D0x9\0i25", b"/external/discarded-alias"),
                    (b"D0x8\0i22", b"/fixture/worktree"),
                    (b"D0x8\0i22", b"/fixture/worktree/file"),
                    (b"D0x8\0i22", b"/fixture/admin"),
                    (b"D0x8\0i22", b"/fixture/admin/index")):
                with self.subTest(sudo=sudo, identity=identity, name=name):
                    with self.assertRaisesRegex(FINISH.Retain, "pending-departure"):
                        self.observe(prefix + b"fmem\0" + identity + b"\0n" + name
                                     + b" (stat: No such file or directory)\0\n", sudo=sudo)

    def test_mem_enoent_requires_linux_complete_identity_and_single_terminal_error(self):
        for descriptor, identity, name in (
                (b"mem", b"", b"/elsewhere (stat: No such file or directory)"),
                (b"mem", b"D0x8\0", b"/elsewhere (stat: No such file or directory)"),
                (b"mem", b"i22\0", b"/elsewhere (stat: No such file or directory)"),
                (b"mem", b"Dinvalid\0i22\0", b"/elsewhere (stat: No such file or directory)"),
                (b"mem", b"D0x8\0iinvalid\0", b"/elsewhere (stat: No such file or directory)"),
                (b"txt", b"D0x8\0i22\0", b"/elsewhere (stat: No such file or directory)"),
                (b"3", b"D0x8\0i22\0", b"/elsewhere (stat: No such file or directory)"),
                (b"mem", b"D0x8\0i22\0", b"/elsewhere (stat: Permission denied)"),
                (b"mem", b"D0x8\0i22\0", b"/elsewhere (stat: No such file or directory) extra"),
                (b"mem", b"D0x8\0i22\0", b"/elsewhere (stat: No such file or directory)"
                 b" (stat: No such file or directory)"),
                (b"mem", b"D0x8\0i22\0", b"/elsewhere (readlink: Permission denied)"
                 b" (stat: No such file or directory)"),
                (b"mem", b"D0x8\0i22\0", b" (stat: No such file or directory)")):
            with self.subTest(descriptor=descriptor, identity=identity, name=name):
                with self.assertRaisesRegex(FINISH.Retain, "visibility-unknown"):
                    self.observe(b"p7\0\nf" + descriptor + b"\0" + identity + b"n" + name + b"\0\n",
                                 sudo=True)
        for platform in ("darwin", "freebsd14"):
            with self.subTest(platform=platform):
                with self.assertRaisesRegex(FINISH.Retain, "visibility-unknown"):
                    self.observe(b"p7\0\nfmem\0D0x8\0i22\0n/elsewhere"
                                 b" (stat: No such file or directory)\0\n", platform=platform)

    def test_no_matching_holders_accepts_optional_fields_and_preserves_symlink_targets(self):
        self.observe(b"p7\0\nf3\0D0x9\0i24\0n/shared/dependencies\0\n"
                     b"f4\0n/fixture/worktree-other/file\0\nf5\0nTCP localhost:80\0\n"
                     b"f6\0D0x8\0i22\0n/elsewhere/name\nwith-newline\0\nf7\0n\0\n")

    def test_known_descriptors_with_file_identity_and_optional_names_are_accepted(self):
        for descriptor in (b"0", b"12345", b"*001", b"cwd", b"rtd", b"twd", b"txt", b"ltx",
                           b"mem", b"mmap", b"DEL", b"ctty", b"jld", b"pd",
                           b"m86", b"v86", b"fp.", b"L12", b"M1a", b"R12", b"tr"):
            with self.subTest(descriptor=descriptor):
                self.observe(b"p7\0\nf" + descriptor + b"\0D0x8\0i22\0\n")

    def test_numeric_and_fileport_non_files_can_omit_identity(self):
        for descriptor in (b"0", b"*001", b"fp."):
            with self.subTest(descriptor=descriptor):
                self.observe(b"p7\0\nf" + descriptor + b"\0n\0\n")

    def test_zero_exit_error_and_unrecognized_descriptors_remain_unknown(self):
        for descriptor in (b"NOFD", b"err", b"unk", b"unknown", b"arbitrary", b"-1",
                           b"3u", b"*01", b"fp.3", b"Mxx", b"Rxx"):
            with self.subTest(descriptor=descriptor):
                with self.assertRaisesRegex(FINISH.Retain, "visibility-unknown"):
                    self.observe(b"p7\0\nf3\0D0x8\0i22\0n/elsewhere\0\n"
                                 b"f" + descriptor + b"\0D0x8\0i23\0n/elsewhere\0\n")

    def test_zero_exit_inline_visibility_errors_remain_unknown(self):
        for record in (
                b"fNOFD\0n/proc/7/fd (opendir: Permission denied)",
                b"fcwd\0n/proc/7/cwd (readlink: Permission denied)",
                b"frtd\0n/proc/7/root (readlink: Permission denied)",
                b"ftxt\0n/proc/7/exe (readlink: Permission denied)",
                b"fmem\0n/proc/7/maps (fopen: Permission denied)",
                b"f3\0n/proc/7/fd/3 (readlink: Permission denied)",
                b"f3\0n/elsewhere (stat: Permission denied)",
                b"fmem\0D0x8\0i22\0n/elsewhere (stat: Permission denied)"):
            with self.subTest(record=record):
                with self.assertRaisesRegex(FINISH.Retain, "visibility-unknown"):
                    self.observe(b"p7\0\n" + record + b"\0\n")

    def test_name_only_or_partial_pseudo_descriptor_identity_remains_unknown(self):
        for descriptor in (b"cwd", b"rtd", b"txt", b"mem", b"DEL", b"M1a", b"tr"):
            for identity in (b"", b"D0x8\0", b"i22\0"):
                with self.subTest(descriptor=descriptor, identity=identity):
                    with self.assertRaisesRegex(FINISH.Retain, "visibility-unknown"):
                        self.observe(b"p7\0\nf" + descriptor + b"\0" + identity
                                     + b"n/elsewhere\0\n")

    def test_inode_aliases_checkout_and_admin_cwd_and_fd_names_block(self):
        for record in (b"f3\0D0x9\0i22\0n/elsewhere/hardlink",
                       b"f4\0D0x9\0i23\0n/elsewhere/admin-alias",
                       b"fcwd\0D0x8\0i22\0n/fixture/worktree",
                       b"fcwd\0D0x8\0i23\0n/fixture/admin",
                       b"fmem\0D0x9\0i22\0n/elsewhere/mapping-alias",
                       b"ftr\0D0x9\0i22\0n/elsewhere/trace-alias",
                       b"ftxt\0D0x8\0i22\0n/fixture/worktree/file",
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


class SudoHolderTests(unittest.TestCase):
    def setUp(self):
        platform = mock.patch.object(FINISH.sys, "platform", "linux")
        platform.start()
        self.addCleanup(platform.stop)

    def result(self, *, code=0, failure=None, pid=123, output=None, errors=b""):
        return types.SimpleNamespace(
            returncode=code, failure=failure, proof={"pid": pid},
            stdout=b"p7\0\nf3\0D0x8\0i22\0n/elsewhere\0\n" if output is None else output,
            stderr=errors)

    def test_fixed_argv_minimal_environment_and_scoped_supervision(self):
        with mock.patch.object(FINISH, "trusted_holder_binary",
                               side_effect=["/usr/bin/sudo", "/usr/bin/lsof"]) as trust, \
                mock.patch.object(FINISH.safety(), "supervise", return_value=self.result()) as child, \
                mock.patch.object(FINISH, "run") as ordinary:
            FINISH.sudo_holder_scan(float("inf"))
        self.assertEqual(trust.call_args_list, [mock.call("sudo"), mock.call("lsof")])
        child.assert_called_once_with(
            ["/usr/bin/sudo", "-n", "-u", "#0", "--", "/usr/bin/lsof", "-nP", "+w", "-F0pfnDi"],
            cwd="/", env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"},
            limit=32 * 1024 * 1024, sudo_monitor=True, deadline=float("inf"))
        ordinary.assert_not_called()

    def test_auth_failure_spawn_failure_and_uncertain_termination_never_fall_back(self):
        for result, reason in (
                (self.result(code=1, errors=b"sudo: a password is required\n"), "scan-failed"),
                (self.result(code=None, failure="OSError", pid=None), "scan-failed"),
                (self.result(code=0, failure="TimeoutExpired"), "termination-unknown"),
                (self.result(code=0, failure="Retain"), "termination-unknown")):
            with self.subTest(reason=reason, result=result), \
                    mock.patch.object(FINISH, "trusted_holder_binary", return_value="/usr/bin/tool"), \
                    mock.patch.object(FINISH.safety(), "supervise", return_value=result) as child, \
                    mock.patch.object(FINISH, "run") as ordinary:
                with self.assertRaisesRegex(FINISH.Retain, reason):
                    FINISH.sudo_holder_scan(float("inf"))
                child.assert_called_once()
                ordinary.assert_not_called()

    def test_trust_failure_precedes_any_process(self):
        with mock.patch.object(FINISH, "trusted_holder_binary",
                               side_effect=FINISH.Retain("sudo-holder-scan-untrusted-path")), \
                mock.patch.object(FINISH.safety(), "supervise") as child:
            with self.assertRaisesRegex(FINISH.Retain, "untrusted-path"):
                FINISH.sudo_holder_scan(float("inf"))
        child.assert_not_called()

    def test_non_linux_rejects_before_trust_ledger_or_process_access(self):
        for platform in ("darwin", "win32", "freebsd14"):
            with self.subTest(platform=platform), mock.patch.object(FINISH.sys, "platform", platform), \
                    mock.patch.object(FINISH, "trusted_holder_binary") as trust, \
                    mock.patch.object(FINISH, "ledger") as ledger, \
                    mock.patch.object(FINISH.safety(), "supervise") as child:
                with self.assertRaisesRegex(FINISH.Retain, "requires-linux"):
                    FINISH.sudo_holder_scan(float("inf"))
                with self.assertRaisesRegex(FINISH.Retain, "requires-linux"):
                    FINISH.main(["remove", "--finalized", "--sudo-holder-scan"])
                trust.assert_not_called()
                ledger.assert_not_called()
                child.assert_not_called()

    def test_flag_misuse_refuses_before_ledger_or_process_access(self):
        for args in (["remove"], ["finish"], ["release"], ["cancel"], ["check", "--apply"],
                     ["status"], ["guard"], ["reconcile"], ["enroll"], ["creation-token"],
                     ["holder-qualification"], ["resume"], ["remove", "--finalized", "--all"]):
            with self.subTest(args=args), mock.patch.object(FINISH, "ledger") as ledger, \
                    mock.patch.object(FINISH.safety(), "supervise") as child:
                with self.assertRaisesRegex(FINISH.Retain, "requires-manual-finalized|one-explicit"):
                    FINISH.main([*args, "--sudo-holder-scan"])
                ledger.assert_not_called()
                child.assert_not_called()

    def test_elevated_scan_keeps_all_visibility_and_holder_rejections(self):
        snap = {"path": "/fixture/worktree", "gitdir": "/fixture/admin"}
        contents = {"entries": {"file": [9, 22, FINISH.stat.S_IFREG]}}
        cases = [
            (self.result(errors=b"visibility warning\n"), "visibility-unknown"),
            (self.result(output=b"p7\0\nfNOFD\0n/proc/7/fd (opendir: Permission denied)\0\n"),
             "visibility-unknown"),
            (self.result(output=b"p7\0\nfcwd\0n/elsewhere\0\n"), "visibility-unknown"),
            (self.result(output=b"p7\0\nfmem\0D0x8\0i22\0n/elsewhere (stat: Permission denied)\0\n"),
             "visibility-unknown"),
            (self.result(output=b"p7\0\nf3\0D0x9\0i22\0n/external/hardlink\0\n"), "pending-departure"),
            (self.result(output=b"p7\0\nf3\0n/fixture/admin/index\0\n"), "pending-departure"),
            (self.result(output=b"p7\0\nf3\0D0x9\0i22"), "visibility-unknown"),
        ]
        for result, reason in cases:
            with self.subTest(reason=reason, output=result.stdout), \
                    mock.patch.object(FINISH, "sudo_holder_scan", return_value=result) as elevated, \
                    mock.patch.object(FINISH, "run") as ordinary:
                with self.assertRaisesRegex(FINISH.Retain, reason):
                    FINISH.manual_holders(snap, contents, {}, float("inf"),
                                          sudo_holder_scan_enabled=True)
                elevated.assert_called_once()
                ordinary.assert_not_called()
        with mock.patch.object(FINISH, "sudo_holder_scan", return_value=self.result()):
            FINISH.manual_holders(snap, contents, {}, float("inf"), sudo_holder_scan_enabled=True)

    def test_manual_scan_forwards_its_absolute_admission_deadline(self):
        until = time.monotonic() + 5
        with mock.patch.object(FINISH, "sudo_holder_scan", return_value=self.result()) as elevated:
            FINISH.manual_holders({"path": "/fixture/worktree", "gitdir": "/fixture/admin"},
                                  {"entries": {}}, {}, until, sudo_holder_scan_enabled=True)
        elevated.assert_called_once_with(until)

    def resolve(self, selected, *, changed=None, links=None):
        modes = {
            "/": (0, FINISH.stat.S_IFDIR | 0o755),
            "/usr": (0, FINISH.stat.S_IFDIR | 0o755),
            "/usr/bin": (0, FINISH.stat.S_IFDIR | 0o755),
            "/usr/bin/sudo": (0, FINISH.stat.S_IFREG | 0o4755),
            "/usr/bin/lsof": (0, FINISH.stat.S_IFREG | 0o755),
        }
        links = links or {}
        for path in links:
            modes[path] = (0, FINISH.stat.S_IFLNK | 0o777)
        modes.update(changed or {})

        def details(path):
            try:
                uid, mode = modes[str(path)]
            except KeyError:
                raise FileNotFoundError(str(path))
            return types.SimpleNamespace(st_uid=uid, st_mode=mode)

        with mock.patch.object(FINISH.shutil, "which", return_value=selected), \
                mock.patch.object(Path, "lstat", details), \
                mock.patch.object(FINISH.os, "readlink", side_effect=lambda path: links[str(path)]):
            return FINISH.trusted_holder_binary("sudo")

    def test_root_owned_canonical_binaries_and_verified_symlink_chains(self):
        self.assertEqual(self.resolve("/usr/bin/sudo"), "/usr/bin/sudo")
        self.assertEqual(self.resolve("/bin/sudo", links={"/bin": "usr/bin"}), "/usr/bin/sudo")
        self.assertEqual(self.resolve("/bin/sudo", links={
            "/bin": "/usr/bin", "/usr/bin/sudo": "../bin/actual",
        }, changed={"/usr/bin/actual": (0, FINISH.stat.S_IFREG | 0o755)}), "/usr/bin/actual")

    def test_untrusted_binary_parent_symlink_and_invalid_resolution_refuse(self):
        for selected, changed, links in (
                (None, {}, {}),
                ("relative/sudo", {}, {}),
                ("/missing/sudo", {}, {}),
                ("/usr/bin/sudo", {"/": (0, FINISH.stat.S_IFDIR | 0o777)}, {}),
                ("/usr/bin/sudo", {"/usr": (1000, FINISH.stat.S_IFDIR | 0o755)}, {}),
                ("/usr/bin/sudo", {"/usr/bin": (0, FINISH.stat.S_IFDIR | 0o775)}, {}),
                ("/usr/bin/sudo", {"/usr/bin/sudo": (1000, FINISH.stat.S_IFREG | 0o755)}, {}),
                ("/usr/bin/sudo", {"/usr/bin/sudo": (0, FINISH.stat.S_IFREG | 0o757)}, {}),
                ("/usr/bin/sudo", {"/usr/bin/sudo": (0, FINISH.stat.S_IFREG | 0o644)}, {}),
                ("/usr/bin/sudo", {"/usr/bin/sudo": (0, FINISH.stat.S_IFDIR | 0o755)}, {}),
                ("/bin/sudo", {"/bin": (1000, FINISH.stat.S_IFLNK | 0o777)}, {"/bin": "/usr/bin"}),
                ("/bin/sudo", {}, {"/bin": "/missing"}),
                ("/bin/sudo", {}, {"/bin": "/bin"}),
                ("/bin/sudo", {"/untrusted": (1000, FINISH.stat.S_IFDIR | 0o755)},
                 {"/bin": "/untrusted/../usr/bin"})):
            with self.subTest(selected=selected, changed=changed, links=links):
                with self.assertRaisesRegex(FINISH.Retain, "trusted-binaries|untrusted-path"):
                    self.resolve(selected, changed=changed, links=links)


class SudoMonitorTests(unittest.TestCase):
    def supervise(self, *, failure=None, sudo_monitor=True, reaps=True, signal_denied=False,
                  interrupt=None):
        module = FINISH.safety()
        child = mock.Mock(pid=123, returncode=None, stdin=None)
        child.stdout.fileno.return_value = 10
        child.stderr.fileno.return_value = 11
        selector = mock.Mock()
        selector.get_map.return_value = failure == "output-limit" or interrupt is not None
        selector.select.return_value = [(types.SimpleNamespace(fd=10, data=0), None)]
        selector.select.side_effect = interrupt
        waits = []

        def wait(timeout):
            waits.append(timeout)
            if not reaps or (failure == "timeout" and len(waits) == 1):
                raise subprocess.TimeoutExpired("fixture monitor", timeout)
            child.returncode = 0
            return 0

        child.wait.side_effect = wait
        if signal_denied:
            child.send_signal.side_effect = PermissionError()

        def signal_group(pid, signal):
            if signal == 0:
                raise ProcessLookupError()

        with mock.patch.object(module, "DEADLINE", time.monotonic() + 30), \
                mock.patch.object(module.subprocess, "Popen", return_value=child) as spawn, \
                mock.patch.object(module.selectors, "DefaultSelector", return_value=selector), \
                mock.patch.object(module.os, "set_blocking"), \
                mock.patch.object(module.os, "read", return_value=b"too much output"), \
                mock.patch.object(module.os, "killpg", side_effect=signal_group) as group:
            if interrupt is not None:
                with self.assertRaises(type(interrupt)) as raised:
                    module.supervise(["/fixture/sudo"], cwd="/", env={}, limit=1,
                                     sudo_monitor=sudo_monitor)
                self.assertIs(raised.exception, interrupt)
                result = None
            else:
                result = module.supervise(["/fixture/sudo"], cwd="/", env={}, limit=1,
                                          sudo_monitor=sudo_monitor)
        return result, child, group, spawn

    def test_normal_completion_does_not_signal_or_infer_root_group_absence(self):
        result, child, group, spawn = self.supervise()
        self.assertIsNone(result.failure)
        self.assertTrue(result.proof["reaped"])
        self.assertEqual(result.proof["privileged_termination"], "command-returned")
        self.assertIsNone(result.proof["group_absent"])
        child.send_signal.assert_not_called()
        group.assert_not_called()
        self.assertFalse(spawn.call_args.kwargs["start_new_session"])
        self.assertEqual(spawn.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_timeout_and_output_limit_remain_uncertain_after_monitor_is_reaped(self):
        for failure in ("timeout", "output-limit"):
            with self.subTest(failure=failure):
                result, child, group, _ = self.supervise(failure=failure)
                self.assertIsNotNone(result.failure)
                self.assertEqual(result.proof["privileged_termination"], "unknown")
                self.assertTrue(result.proof["reaped"])
                self.assertIsNone(result.proof["group_absent"])
                child.send_signal.assert_called_once_with(FINISH.safety().signal.SIGTERM)
                self.assertEqual(child.wait.call_args, mock.call(timeout=2))
                group.assert_not_called()

    def test_unreaped_or_unsignalable_monitor_never_escalates_to_kill(self):
        result, child, group, _ = self.supervise(failure="timeout", reaps=False, signal_denied=True)
        self.assertIsNotNone(result.failure)
        self.assertFalse(result.proof["reaped"])
        self.assertEqual(result.proof["privileged_termination"], "unknown")
        child.send_signal.assert_called_once_with(FINISH.safety().signal.SIGTERM)
        group.assert_not_called()

    def test_keyboard_interrupt_and_system_exit_propagate_after_monitor_term_and_wait(self):
        for interrupt in (KeyboardInterrupt(), SystemExit(17)):
            with self.subTest(interrupt=type(interrupt).__name__):
                _, child, group, _ = self.supervise(interrupt=interrupt)
                child.send_signal.assert_called_once_with(FINISH.safety().signal.SIGTERM)
                child.wait.assert_called_once_with(timeout=2)
                group.assert_not_called()

    def test_default_supervisor_cleanup_is_unchanged(self):
        result, child, group, spawn = self.supervise(failure="timeout", sudo_monitor=False)
        child.send_signal.assert_not_called()
        self.assertEqual(group.call_args_list, [
            mock.call(123, FINISH.safety().signal.SIGKILL), mock.call(123, 0)])
        self.assertTrue(result.proof["group_absent"])
        self.assertTrue(spawn.call_args.kwargs["start_new_session"])
        self.assertNotIn("privileged_termination", result.proof)

    def test_expired_admission_deadline_prevents_monitor_spawn(self):
        module = FINISH.safety()
        with mock.patch.object(module, "DEADLINE", time.monotonic() + 238), \
                mock.patch.object(module.subprocess, "Popen") as spawn:
            result = module.supervise(["/fixture/sudo"], cwd="/", env={},
                                      sudo_monitor=True, deadline=time.monotonic() - 1)
        spawn.assert_not_called()
        self.assertIsNotNone(result.failure)
        self.assertIsNone(result.proof["pid"])


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


class MissingDeviceRenumberingTests(unittest.TestCase):
    """A reboot renumbered the volume holding a vanished, recorded checkout."""
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    tearDown = ReleaseTests.tearDown
    setUp = MissingCancelledRemovalTests.setUp

    def remove(self, *flags):
        with mock.patch.object(FINISH, "manual_holders"):
            return self.call("remove", "--finalized", *flags)

    def renumber(self, **overrides):
        # Record an older device number with the same inodes; the live volume
        # then appears renumbered exactly as after an APFS remount.
        recorded = json.loads(self.original["identity"])
        old = recorded["path_id"][0] + 1000
        for key in ("path_id", "gitdir_id", "common_id", "owner_id"):
            recorded[key] = [old, recorded[key][1]]
        for key, value in overrides.items():
            recorded[key] = value
        with FINISH.ledger(self.state) as db, db:
            db.execute("UPDATE worktrees SET identity=? WHERE id=?",
                       (json.dumps(recorded, sort_keys=True), self.original["id"]))
        return recorded

    def stored_identity(self):
        with FINISH.ledger(self.state) as db:
            return FINISH.get_row(db, self.wt)["identity"]

    def cancel(self, *flags):
        return self.call("cancel", "--reason", "superseded upstream", *flags)[1][0]

    def test_acknowledged_renumbering_cancels_and_closes_without_rewriting_enrollment(self):
        self.renumber()
        stored = self.stored_identity()
        with self.assertRaisesRegex(FINISH.Retain, "missing-closeout-identity-changed"):
            self.cancel()
        self.assertEqual(self.cancel("--acknowledge-device-renumbering")["state"], "cancelled")
        with mock.patch.object(FINISH, "remove_with_intent") as removal:
            with self.assertRaisesRegex(FINISH.Retain, "missing-closeout-boundary-changed"):
                self.remove()
        removal.assert_not_called()
        code, rows = self.remove("--acknowledge-device-renumbering")
        self.assertEqual(code, 0)
        self.assertEqual(rows[0]["checkout"], "removed", rows[0])
        self.assertFalse(self.admin.exists())
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertEqual(self.stored_identity(), stored)

    def test_inode_mismatch_inconsistent_mapping_and_unmapped_checkout_refuse(self):
        recorded = json.loads(self.original["identity"])
        old = recorded["path_id"][0] + 1000
        cases = (
            ("worktree-identity-changed", {"common_id": [old, recorded["common_id"][1] + 1]}),
            ("worktree-identity-changed", {"owner_id": [old + 1, recorded["owner_id"][1]]}),
            ("device-renumbering-checkout-device-unmapped", {"path_id": [old + 2, recorded["path_id"][1]]}),
        )
        for reason, overrides in cases:
            with self.subTest(reason=reason, overrides=overrides):
                self.renumber(**overrides)
                with self.assertRaisesRegex(FINISH.Retain, reason):
                    self.cancel("--acknowledge-device-renumbering")
                with mock.patch.object(FINISH, "remove_with_intent") as removal:
                    with self.assertRaises(FINISH.Retain):
                        self.remove("--acknowledge-device-renumbering")
                removal.assert_not_called()
        self.assertTrue(self.admin.exists())
        self.assertEqual((self.admin / "locked").read_bytes(), self.before_lock)

    def test_root_on_other_device_and_present_checkout_refuse(self):
        recorded = self.renumber()
        real = FINISH.identity
        def identity(path):
            value = real(path)
            return [value[0] + 1, value[1]] if str(path) == recorded["root"] else value
        with mock.patch.object(FINISH, "identity", side_effect=identity):
            with self.assertRaisesRegex(FINISH.Retain, "device-renumbering-root-device-changed"):
                self.cancel("--acknowledge-device-renumbering")
        self.wt.mkdir()
        with self.assertRaisesRegex(FINISH.Retain, "requires-missing-checkout"):
            self.cancel("--acknowledge-device-renumbering")
        self.wt.rmdir()
        with self.assertRaisesRegex(FINISH.Retain, "device-acknowledgement-requires"):
            self.call("pin", "--reason", "x", "--acknowledge-device-renumbering")

    def test_wrapper_forwards_acknowledgement_only_with_finalized(self):
        env = dict(os.environ, DOTFILES_WORKTREES_ROOT=str(self.root),
                   DOTFILES_GWT_FINISH_STATE=str(self.state), TMUX="")
        script = ('source "$1"; cd "$2"; _gwt_require_worktree_storage() { :; }; '
                  '_gwt_finish_tool() { printf "%s\\n" "$@"; }; '
                  '_gwt_tmux_sync_context() { :; }; shift 2; gwt rm "$@"')
        command = ["zsh", "-c", script, "fixture", str(ROOT / "functions/gwt/gwt.zsh"), str(self.repo)]
        result = subprocess.run([*command, str(self.wt), "--finalized", "--acknowledge-device-renumbering"],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.splitlines(), ["remove", "--worktree", str(self.wt), "--finalized",
                                                      "--acknowledge-device-renumbering"])
        result = subprocess.run([*command, str(self.wt), "--acknowledge-device-renumbering"],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("remove", result.stdout)


class MissingRepositoryRenameTests(unittest.TestCase):
    """The GitHub repository was renamed after a now-missing checkout enrolled."""
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    tearDown = ReleaseTests.tearDown
    setUp = MissingCancelledRemovalTests.setUp
    remove = MissingDeviceRenumberingTests.remove
    renumber = MissingDeviceRenumberingTests.renumber
    stored_identity = MissingDeviceRenumberingTests.stored_identity
    cancel = MissingDeviceRenumberingTests.cancel
    rename = ("--acknowledge-repository-rename", "example/repo=example/repo-renamed")

    def rename_origin(self):
        git(self.repo, "remote", "set-url", "origin", "https://github.com/example/repo-renamed.git")

    def test_acknowledged_rename_cancels_and_closes_without_rewriting_enrollment(self):
        self.rename_origin()
        stored = self.stored_identity()
        with self.assertRaisesRegex(FINISH.Retain, "missing-closeout-owner-binding-changed"):
            self.cancel()
        self.assertEqual(self.cancel(*self.rename)["state"], "cancelled")
        with mock.patch.object(FINISH, "remove_with_intent") as removal:
            with self.assertRaisesRegex(FINISH.Retain, "owner-binding-changed"):
                self.remove()
        removal.assert_not_called()
        code, rows = self.remove(*self.rename)
        self.assertEqual(code, 0)
        self.assertEqual(rows[0]["checkout"], "removed", rows[0])
        self.assertFalse(self.admin.exists())
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertEqual(self.stored_identity(), stored)

    def test_rename_combines_with_device_renumbering(self):
        self.renumber()
        self.rename_origin()
        stored = self.stored_identity()
        for flags in ((), self.rename, ("--acknowledge-device-renumbering",)):
            with self.subTest(flags=flags), self.assertRaises(FINISH.Retain):
                self.cancel(*flags)
        both = (*self.rename, "--acknowledge-device-renumbering")
        self.assertEqual(self.cancel(*both)["state"], "cancelled")
        self.assertEqual(self.remove(*both)[1][0]["checkout"], "removed")
        self.assertEqual(self.stored_identity(), stored)

    def test_mismatched_or_malformed_slugs_refuse(self):
        self.rename_origin()
        for value, reason in (("example/other=example/repo-renamed", "old-slug-mismatch"),
                              ("example/repo=example/elsewhere", "new-slug-mismatch"),
                              ("example/repo=example/repo", "new-slug-mismatch"),
                              ("example/repo", "needs-old-and-new-slug"),
                              ("example/repo=renamed", "needs-old-and-new-slug")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(FINISH.Retain, reason):
                    self.cancel("--acknowledge-repository-rename", value)
                with mock.patch.object(FINISH, "remove_with_intent") as removal:
                    with self.assertRaisesRegex(FINISH.Retain, reason + "|owner-binding-changed"):
                        self.remove("--acknowledge-repository-rename", value)
                removal.assert_not_called()
        self.assertNotIn("cancelled_owners", self.call("status")[1][0])
        self.assertEqual((self.admin / "locked").read_bytes(), self.before_lock)

    def test_present_checkout_and_other_commands_refuse(self):
        self.rename_origin()
        self.wt.mkdir()
        with self.assertRaisesRegex(FINISH.Retain, "repository-rename-acknowledgement-requires-missing-checkout"):
            self.cancel(*self.rename)
        self.wt.rmdir()
        for command, extra in (("resume", ()), ("pin", ("--reason", "x")), ("status", ()),
                               ("remove", ()), ("finish", ("--pr", self.url)),
                               ("cancel", ("--reason", "x", "--pr", self.url)),
                               ("reconcile", ("--external-removal",))):
            with self.subTest(command=command), mock.patch.object(FINISH, "ledger") as ledger:
                with self.assertRaisesRegex(FINISH.Retain, "requires-missing-cancel-or-finalized-remove"):
                    self.call(command, *self.rename, *extra)
                ledger.assert_not_called()

    def test_wrapper_forwards_rename_only_with_finalized(self):
        env = dict(os.environ, DOTFILES_WORKTREES_ROOT=str(self.root),
                   DOTFILES_GWT_FINISH_STATE=str(self.state), TMUX="")
        script = ('source "$1"; cd "$2"; _gwt_require_worktree_storage() { :; }; '
                  '_gwt_finish_tool() { printf "%s\\n" "$@"; }; '
                  '_gwt_tmux_sync_context() { :; }; shift 2; gwt rm "$@"')
        command = ["zsh", "-c", script, "fixture", str(ROOT / "functions/gwt/gwt.zsh"), str(self.repo)]
        result = subprocess.run([*command, str(self.wt), "--finalized", *self.rename],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.splitlines(),
                         ["remove", "--worktree", str(self.wt), "--finalized", *self.rename])
        for arguments in ([str(self.wt), *self.rename],
                          [str(self.wt), "--finalized", "--acknowledge-repository-rename", "example/repo"],
                          [str(self.wt), "--finalized", "--acknowledge-repository-rename"]):
            result = subprocess.run([*command, *arguments], env=env, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("remove", result.stdout)


class ExactOrphanLockTests(unittest.TestCase):
    tearDown = LifecycleTests.tearDown
    proof = LifecycleTests.proof
    call = LifecycleTests.call

    def setUp(self):
        LifecycleTests.setUp(self)
        (self.home / ".codex").mkdir()
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.head)
        self.path_ids = {}
        self.orphans = {}
        self.output = b"p1\0\nf1\0n/fixture/unrelated\0\n"
        original = FINISH.run

        def run(command, **kwargs):
            if Path(command[0]).name == "lsof":
                return types.SimpleNamespace(stdout=self.output, stderr=b"")
            return original(command, **kwargs)

        self.scan = mock.patch.object(FINISH, "run", side_effect=run)
        self.scan.start()
        self.addCleanup(self.scan.stop)

    def orphan(self, name, *, prepare=None):
        def capture(path, admin):
            details = path.lstat()
            self.path_ids[str(path)] = f"{details.st_dev}:{details.st_ino}"
            if prepare is not None:
                prepare(path, admin)
        return OrphanLockTests.orphan(self, name, prepare=capture)

    def scoped_arguments(self, path, lock, *extra):
        return [
            "orphan-locks", "--worktree", str(self.repo), "--state-dir", str(self.state),
            "--managed-root", str(self.root), "--orphan-path", str(path),
            "--creation-token", lock.split(":")[1], "--expected-head", self.head,
            "--missing-path-id", self.path_ids[str(path)], *extra]

    def scoped(self, path, lock, *extra):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = FINISH.main(self.scoped_arguments(path, lock, *extra))
        return code, json.loads(output.getvalue())[0]

    def receipt(self):
        with FINISH.read_only_ledger(self.state) as db:
            return dict(db.execute("SELECT * FROM orphan_removals").fetchone())

    def prior_orphan(self, name="selected", *, same_device=False, prepare=None):
        record = {}
        def capture(path, admin):
            if prepare is not None:
                prepare(path, admin)
            # Native finish-managed creation uses umask 077.
            (admin / "locked").chmod(0o600)
            paths = {"path": path, "root": self.root, "owner": self.repo,
                     "common": self.repo / ".git", "gitdir": admin}
            record.update(schema="gwt-orphan-prior-identity.v1", uid=os.getuid(),
                          lock_reason=(admin / "locked").read_text().strip(),
                          **{key: str(value) for key, value in paths.items()})
            for key in ("path", "owner", "common", "gitdir", "lock"):
                details = (admin / "locked" if key == "lock" else paths[key]).lstat()
                record[key + "_id"] = [details.st_dev + (0 if same_device else 10000), details.st_ino]
        path, admin, lock = self.orphan(name, prepare=capture)
        self.path_ids[str(path)] = ":".join(map(str, record["path_id"]))
        source = self.base / (name + ".prior.json")
        source.write_text(json.dumps(record))
        source.chmod(0o600)
        return path, admin, lock, source, record

    def prior_options(self, source):
        return ("--allow-device-renumbering", "--prior-identity-file", str(source))

    def unstarted_orphan(self, name="selected"):
        path, admin, lock, source, record = self.prior_orphan(name, prepare=self.recovery_commit)
        sibling = self.root / (name + "-sibling")
        git(self.repo, "worktree", "add", "-b", name + "-sibling", str(sibling))
        _, plan = self.scoped(path, lock, *self.prior_options(source))
        original, scans = FINISH.manual_holders, []
        def holders(*args, **kwargs):
            original(*args, **kwargs)
            scans.append(1)
            if len(scans) == 3:
                with contextlib.closing(sqlite3.connect(
                        f"file:{self.state / 'lifecycle.sqlite'}?mode=ro", uri=True)) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM orphan_removals "
                                                "WHERE path=? AND result IS NULL", (str(path),)).fetchone()[0], 1)
                (sibling / ".git").unlink()
        with mock.patch.object(FINISH, "manual_holders", side_effect=holders):
            code, outcome = self.scoped(path, lock, *self.prior_options(source),
                                        "--apply", "--recovery-sha256", plan["recovery_sha256"])
        self.assertEqual(code, 1)
        self.assertEqual(outcome["result"]["reason"], "orphan-preimage-changed")
        self.assertIs(outcome["result"]["native_attempted"], False)
        self.assertIsNone(outcome["result"]["child"])
        self.assertIs(outcome["result"]["native_lock_preserved"], True)
        return path, admin, lock, source, record

    def orphan_receipt(self, path):
        with FINISH.read_only_ledger(self.state) as db:
            return dict(db.execute("SELECT * FROM orphan_removals WHERE path=?", (str(path),)).fetchone())

    def reconcile_orphan(self, path, lock, source, *extra):
        receipt = self.orphan_receipt(path)
        arguments = self.scoped_arguments(path, lock, *self.prior_options(source))
        arguments[0] = "reconcile-orphan"
        arguments.extend(("--intent-id", receipt["id"],
                          "--intent-sha256", hashlib.sha256(receipt["intent"].encode()).hexdigest(),
                          "--result-sha256", hashlib.sha256(receipt["result"].encode()).hexdigest(), *extra))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = FINISH.main(arguments)
        return code, json.loads(output.getvalue())[0]

    def test_orphan_reconciliation_preserves_receipt_and_requires_fresh_exact_plan(self):
        path, admin, lock, source, _ = self.unstarted_orphan()
        receipt = self.orphan_receipt(path)
        with self.assertRaisesRegex(FINISH.Retain, "lifecycle-record"):
            self.scoped(path, lock, *self.prior_options(source))
        lock_before = FINISH.safety().file_identity((admin / "locked").lstat())
        ledger = (self.state / "lifecycle.sqlite").read_bytes()
        _, plan = self.reconcile_orphan(path, lock, source)
        self.assertEqual((self.state / "lifecycle.sqlite").read_bytes(), ledger)
        self.orphan("later-sibling")
        original, mutations = FINISH.safety().supervise, []
        def supervise(command, **kwargs):
            if "worktree" in command and ("unlock" in command or "remove" in command):
                mutations.append(command)
            return original(command, **kwargs)
        with mock.patch.object(FINISH.safety(), "supervise", side_effect=supervise):
            code, result = self.reconcile_orphan(path, lock, source, "--apply",
                                                "--recovery-sha256", plan["recovery_sha256"])
        self.assertEqual(code, 0, result)
        self.assertEqual(result["disposition"], "reconciled")
        self.assertNotIn("checkout", result)
        self.assertEqual(mutations, [])
        self.assertEqual(self.orphan_receipt(path), receipt)
        self.assertEqual(FINISH.safety().file_identity((admin / "locked").lstat()), lock_before)
        with FINISH.read_only_ledger(self.state) as db:
            self.assertTrue(FINISH.orphan_known(db, str(path), str(admin), lock))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM worktrees").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM orphan_unstarted_reconciliations").fetchone()[0], 1)
        _, batch = OrphanLockTests.survey(self)
        self.assertEqual(batch[str(path)]["reason"], "orphan-has-lifecycle-record")
        with self.assertRaisesRegex(FINISH.Retain, "already-reconciled"):
            self.reconcile_orphan(path, lock, source)
        with self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
            self.scoped(path, lock, *self.prior_options(source), "--apply",
                        "--recovery-sha256", plan["recovery_sha256"])
        _, fresh = self.scoped(path, lock, *self.prior_options(source))
        code, removed = self.scoped(path, lock, *self.prior_options(source), "--apply",
                                    "--recovery-sha256", fresh["recovery_sha256"])
        self.assertEqual(code, 0, removed)
        self.assertFalse(admin.exists())
        self.assertTrue(removed["result"]["siblings_preserved"])
        self.assertTrue(removed["result"]["branch_reflog_preserved"])
        self.assertEqual(self.orphan_receipt(path), receipt)

    def test_orphan_reconciliation_rejects_unknown_producer_shapes(self):
        path, _, lock, source, _ = self.unstarted_orphan()
        original = self.orphan_receipt(path)
        cases = (("native_attempted", None), ("native_attempted", True), ("native_attempted", 0),
                 ("child", "missing"), ("child", {}), ("native_lock_preserved", False),
                 ("reason", "admission-deadline"), ("restored_lock", {}), ("path_absent", True))
        for key, value in cases:
            result = json.loads(original["result"])
            if value is None or value == "missing":
                result.pop(key)
            else:
                result[key] = value
            with FINISH.ledger(self.state) as db, db:
                db.execute("UPDATE orphan_removals SET result=? WHERE id=?", (json.dumps(result), original["id"]))
            with self.subTest(key=key, value=value), self.assertRaisesRegex(FINISH.Retain, "proof-invalid"):
                self.reconcile_orphan(path, lock, source)
        with FINISH.ledger(self.state) as db, db:
            db.execute("UPDATE orphan_removals SET result=? WHERE id=?", (original["result"], original["id"]))
        self.assertEqual(self.orphan_receipt(path), original)

    def test_orphan_reconciliation_binds_hashes_operator_and_receipt_scope(self):
        path, _, lock, source, _ = self.unstarted_orphan()
        original = self.orphan_receipt(path)
        for option, value in (("--intent-id", str(uuid.uuid4())), ("--intent-sha256", "0" * 64),
                              ("--result-sha256", "0" * 64), ("--owner", "another-operator")):
            with self.subTest(option=option), self.assertRaisesRegex(FINISH.Retain, "receipt-mismatch|proof-invalid"):
                self.reconcile_orphan(path, lock, source, option, value)
        for field, value in (("path", str(self.root / "other")), ("admin", str(self.repo / ".git/other")),
                             ("lock_reason", "gwt-finish.v2:" + str(uuid.uuid4()))):
            with FINISH.ledger(self.state) as db, db:
                db.execute(f"UPDATE orphan_removals SET {field}=? WHERE id=?", (value, original["id"]))
            with self.subTest(field=field), self.assertRaisesRegex(FINISH.Retain, "scope-mismatch"):
                # The original hash operands do not authorize another registration.
                args = self.scoped_arguments(path, lock, *self.prior_options(source))
                args[0] = "reconcile-orphan"
                FINISH.main([*args, "--intent-id", original["id"], "--intent-sha256",
                             hashlib.sha256(original["intent"].encode()).hexdigest(), "--result-sha256",
                             hashlib.sha256(original["result"].encode()).hexdigest()])
            with FINISH.ledger(self.state) as db, db:
                db.execute(f"UPDATE orphan_removals SET {field}=? WHERE id=?", (original[field], original["id"]))
        for raw in ('{"checkout":"unknown","checkout":"unknown"}', 'null', '{'):
            with FINISH.ledger(self.state) as db, db:
                db.execute("UPDATE orphan_removals SET result=? WHERE id=?", (raw, original["id"]))
            with self.subTest(raw=raw), self.assertRaisesRegex(FINISH.Retain, "proof-invalid"):
                self.reconcile_orphan(path, lock, source)
        with FINISH.ledger(self.state) as db, db:
            db.execute("UPDATE orphan_removals SET result=? WHERE id=?", (original["result"], original["id"]))
        self.assertEqual(self.orphan_receipt(path), original)

    def test_orphan_reconciliation_keeps_target_holders_and_coordination_guards(self):
        path, admin, lock, source, record = self.unstarted_orphan()
        _, plan = self.reconcile_orphan(path, lock, source)
        marker = self.repo / ".git/index.lock"
        cases = ((f"p1\0\nf3\0D0x{admin.stat().st_dev:x}\0i{record['path_id'][1]}\0n/alias\0\n".encode(),
                  "pending-departure"), (b"", "visibility-unknown"))
        for output, reason in cases:
            self.output = output
            with self.subTest(reason=reason), self.assertRaisesRegex(FINISH.Retain, reason):
                self.reconcile_orphan(path, lock, source, "--apply", "--recovery-sha256", plan["recovery_sha256"])
        self.output = b"p1\0\nf1\0n/fixture/unrelated\0\n"
        with FINISH.ledger(self.state):
            with self.assertRaisesRegex(FINISH.Retain, "lifecycle-busy"):
                self.reconcile_orphan(path, lock, source)
        with FINISH.safety().maintainer_lock(self.home / ".codex"):
            with self.assertRaisesRegex(FINISH.Retain, "maintainer-lock-present"):
                self.reconcile_orphan(path, lock, source)
        marker.touch()
        with self.assertRaisesRegex(FINISH.Retain, "git-lock-present"):
            self.reconcile_orphan(path, lock, source)
        marker.unlink()
        # Even changing only the qualified input file's metadata invalidates it.
        details = source.stat()
        os.utime(source, ns=(details.st_atime_ns, details.st_mtime_ns + 1))
        with self.assertRaisesRegex(FINISH.Retain, "target-changed"):
            self.reconcile_orphan(path, lock, source, "--apply", "--recovery-sha256", plan["recovery_sha256"])
        with FINISH.read_only_ledger(self.state) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='orphan_unstarted_reconciliations'")
                              .fetchone())
        self.assertEqual((admin / "locked").read_text().strip(), lock)

    def test_orphan_reconciliation_fact_and_new_attempt_never_bypass_history(self):
        path, admin, lock, source, _ = self.unstarted_orphan()
        original = self.orphan_receipt(path)
        _, plan = self.reconcile_orphan(path, lock, source)
        self.reconcile_orphan(path, lock, source, "--apply", "--recovery-sha256", plan["recovery_sha256"])
        with FINISH.read_only_ledger(self.state) as db:
            raw_fact = db.execute("SELECT fact FROM orphan_unstarted_reconciliations").fetchone()[0]
        for key, value in (("recovery_sha256", "0" * 64), ("operator", "another-operator"),
                           ("target_sha256", "0" * 64), ("observed_at", False)):
            fact = json.loads(raw_fact)
            fact[key] = value
            with FINISH.ledger(self.state) as db, db:
                db.execute("UPDATE orphan_unstarted_reconciliations SET fact=?", (json.dumps(fact),))
            with self.subTest(key=key), self.assertRaisesRegex(FINISH.Retain, "proof-invalid"):
                self.scoped(path, lock, *self.prior_options(source))
        with FINISH.ledger(self.state) as db, db:
            db.execute("UPDATE orphan_unstarted_reconciliations SET fact=?", (raw_fact,))
            db.execute("UPDATE orphan_removals SET result=result || ' ' WHERE id=?", (original["id"],))
        with self.assertRaisesRegex(FINISH.Retain, "proof-invalid"):
            self.scoped(path, lock, *self.prior_options(source))
        with FINISH.ledger(self.state) as db, db:
            db.execute("UPDATE orphan_removals SET result=? WHERE id=?", (original["result"], original["id"]))
        _, fresh = self.scoped(path, lock, *self.prior_options(source))
        original_supervise = FINISH.safety().supervise
        def supervise(command, **kwargs):
            if "worktree" in command and "remove" in command:
                command = [shutil.which("false")]
            return original_supervise(command, **kwargs)
        with mock.patch.object(FINISH.safety(), "supervise", side_effect=supervise):
            code, result = self.scoped(path, lock, *self.prior_options(source), "--apply",
                                       "--recovery-sha256", fresh["recovery_sha256"])
        self.assertEqual(code, 1)
        self.assertEqual(result["disposition"], "unknown")
        with FINISH.read_only_ledger(self.state) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM orphan_removals WHERE path=?", (str(path),)).fetchone()[0], 2)
            snap = json.loads(original["intent"])["before"]["snapshot"]
            args = FINISH.parser().parse_args(self.scoped_arguments(path, lock, *self.prior_options(source)))
            self.assertIsNone(FINISH.orphan_reconciliation(db, args, snap))
            self.assertTrue(FINISH.orphan_known(db, str(path), str(admin), lock, reconciled=original["id"]))
        self.assertEqual(self.orphan_receipt(path), original)

    def test_reconcile_orphan_wrapper_and_exact_only_cli(self):
        env = dict(os.environ, TMUX="")
        script = ('source "$1"; _gwt_finish_tool() { printf "%s\\n" "$@"; }; '
                  'shift; gwt reconcile-orphan "$@"')
        operands = ["--worktree", str(self.repo), "--orphan-path", str(self.root / "missing"),
                    "--intent-id", str(uuid.uuid4()), "--intent-sha256", "a" * 64, "--result-sha256", "b" * 64]
        result = subprocess.run(["zsh", "-c", script, "fixture", str(ROOT / "functions/gwt/gwt.zsh"), *operands],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["reconcile-orphan", *operands])
        for args in (["reconcile-orphan"], ["reconcile-orphan", "--intent-id", str(uuid.uuid4())],
                     ["orphan-locks", "--worktree", str(self.repo), "--intent-id", str(uuid.uuid4())]):
            with self.subTest(args=args), mock.patch.object(FINISH, "read_only_ledger") as ledger:
                with self.assertRaisesRegex(FINISH.Retain, "exact-receipt-hashes|one-owning-checkout"):
                    FINISH.main(args)
                ledger.assert_not_called()

    def test_prior_identity_maps_holders_and_preserves_original_in_intent(self):
        path, admin, lock, source, record = self.prior_orphan()
        self.orphan("sibling")
        options = self.prior_options(source)
        with self.assertRaisesRegex(FINISH.Retain, "prior-device-changed"):
            self.scoped(path, lock)
        _, planned = self.scoped(path, lock, *options)
        actual_dev = admin.stat().st_dev
        self.output = f"p1\0\nf3\0D0x{actual_dev:x}\0i{record['path_id'][1]}\0n/alias\0\n".encode()
        with self.assertRaisesRegex(FINISH.Retain, "pending-departure"):
            self.scoped(path, lock, *options)
        self.output = b""
        with self.assertRaisesRegex(FINISH.Retain, "visibility-unknown"):
            self.scoped(path, lock, *options)
        self.output = b"p1\0\nf1\0n/fixture/unrelated\0\n"
        original, removals = FINISH.safety().supervise, []
        def supervise(command, **kwargs):
            if "worktree" in command and "remove" in command:
                removals.append(command)
            return original(command, **kwargs)
        with mock.patch.object(FINISH.safety(), "supervise", side_effect=supervise):
            code, result = self.scoped(path, lock, *options, "--apply",
                                       "--recovery-sha256", planned["recovery_sha256"])
        self.assertEqual(code, 0, result)
        self.assertEqual(len(removals), 1)
        self.assertNotIn("--force", removals[0])
        self.assertNotIn("-f", removals[0])
        before = json.loads(self.receipt()["intent"])["before"]
        self.assertEqual(before["orphan_identity"]["original_identity"], record)
        self.assertEqual(before["orphan_identity"]["device_map"], [[record["path_id"][0], actual_dev]])
        self.assertEqual(before["snapshot"]["path_id"], [actual_dev, record["path_id"][1]])
        self.assertEqual(json.loads(source.read_text()), record)
        self.assertTrue(result["result"]["siblings_preserved"])
        self.assertTrue(result["result"]["branch_reflog_preserved"])
        self.assertEqual(git(self.repo, "rev-parse", "selected"), self.head)
        with FINISH.read_only_ledger(self.state) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM worktrees WHERE path=?", (str(path),)).fetchone())

    def test_identity_options_are_exact_paired_and_bound_even_on_same_device(self):
        path, _, lock, source, _ = self.prior_orphan(same_device=True)
        options = self.prior_options(source)
        for extra in ((options[0],), options[1:], (*options, *options[1:])):
            with self.subTest(extra=extra), self.assertRaises(FINISH.Retain):
                self.scoped(path, lock, *extra)
        for command in ("status", "recover-missing", "recover-failed-creation"):
            with self.subTest(command=command), self.assertRaises(FINISH.Retain):
                self.call(command, *options)
        with self.assertRaises(FINISH.Retain):
            OrphanLockTests.survey(self, *options)
        _, planned = self.scoped(path, lock, *options)
        with self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
            self.scoped(path, lock, "--apply", "--recovery-sha256", planned["recovery_sha256"])
        self.assertEqual(self.scoped(path, lock, *options, "--apply",
                                    "--recovery-sha256", planned["recovery_sha256"])[0], 0)

    def test_prior_record_schema_paths_and_surviving_identities_fail_closed(self):
        path, admin, lock, source, record = self.prior_orphan()
        cases = [dict(record, schema="unknown"), dict(record, extra=True),
                 {key: value for key, value in record.items() if key != "lock_id"},
                 dict(record, uid=True), dict(record, uid=-1), dict(record, uid=os.getuid() + 1),
                 dict(record, lock_reason="gwt-finish.v2:not-a-uuid")]
        cases += [dict(record, **{key: record[key] + "/other"})
                  for key in ("path", "root", "owner", "common", "gitdir")]
        cases += [dict(record, owner_id=value) for value in
                  ([True, 1], [1.0, 1], [-1, 1], [1, 0], [1, -1], [1], "1:2")]
        cases += [dict(record, **{key: [record[key][0], record[key][1] + 1]})
                  for key in ("owner_id", "common_id", "gitdir_id", "lock_id")]
        cases += [dict(record, owner_id=[record["owner_id"][0] + 1, record["owner_id"][1]])]
        for changed in cases:
            source.write_text(json.dumps(changed))
            with self.subTest(changed=changed), self.assertRaises(FINISH.Retain):
                self.scoped(path, lock, *self.prior_options(source))
        for raw in ('{"schema":1,"schema":2}', '[]', '{', '"record"'):
            source.write_text(raw)
            with self.subTest(raw=raw), self.assertRaises(FINISH.Retain):
                self.scoped(path, lock, *self.prior_options(source))
        source.write_text(json.dumps(record))
        self.path_ids[str(path)] = f"{record['path_id'][0] + 1}:{record['path_id'][1]}"
        record["path_id"][0] += 1
        source.write_text(json.dumps(record))
        with self.assertRaisesRegex(FINISH.Retain, "volume-unmapped-or-changed"):
            self.scoped(path, lock, *self.prior_options(source))
        self.assertEqual((admin / "locked").read_text().strip(), lock)

    def test_prior_source_is_private_and_outside_deletion_domains(self):
        path, admin, lock, source, record = self.prior_orphan()
        options = self.prior_options(source)
        with self.assertRaises((FINISH.Retain, OSError)):
            self.scoped(path, lock, *self.prior_options(self.base / "missing.json"))
        source.write_bytes(b" " * 16385)
        with self.assertRaisesRegex(FINISH.Retain, "size"):
            self.scoped(path, lock, *options)
        source.write_text(json.dumps(record))
        source.chmod(0o644)
        with self.assertRaises(FINISH.Retain):
            self.scoped(path, lock, *options)
        source.chmod(0o600)
        alias = self.base / "alias"
        alias.symlink_to(source)
        with self.assertRaises(FINISH.Retain):
            self.scoped(path, lock, *self.prior_options(alias))
        alias.unlink()
        os.link(source, alias)
        with self.assertRaises(FINISH.Retain):
            self.scoped(path, lock, *options)
        alias.unlink()
        unsafe = admin / "prior.json"
        source.rename(unsafe)
        with self.assertRaisesRegex(FINISH.Retain, "source-unsafe"):
            self.scoped(path, lock, *self.prior_options(unsafe))

    def test_identity_record_and_volume_changes_invalidate_plan(self):
        path, admin, lock, source, _ = self.prior_orphan()
        options = self.prior_options(source)
        for replace in (False, True):
            _, planned = self.scoped(path, lock, *options)
            raw = source.read_bytes()
            if replace:
                old = source.with_suffix(".old")
                source.rename(old)
                source.write_bytes(raw)
                source.chmod(0o600)
                old.unlink()
            else:
                source.write_bytes(raw + b"\n")
            with self.subTest(replace=replace), self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
                self.scoped(path, lock, *options, "--apply", "--recovery-sha256", planned["recovery_sha256"])
        original = FINISH.identity
        def wrong_volume(value):
            result = original(value)
            return [result[0] + 1, result[1]] if str(value) == str(self.root) else result
        with mock.patch.object(FINISH, "identity", side_effect=wrong_volume):
            with self.assertRaisesRegex(FINISH.Retain, "volume-unmapped-or-changed"):
                self.scoped(path, lock, *options)
        def inconsistent_mapping(value):
            result = original(value)
            return [result[0] + 1, result[1]] if str(value) == str(self.repo / ".git") else result
        with mock.patch.object(FINISH, "identity", side_effect=inconsistent_mapping):
            with self.assertRaisesRegex(FINISH.Retain, "identity-changed"):
                self.scoped(path, lock, *options)
        old_lock = admin / "old-lock"
        (admin / "locked").rename(old_lock)
        (admin / "locked").write_text(lock + "\n")
        (admin / "locked").chmod(0o600)
        old_lock.unlink()
        with self.assertRaisesRegex(FINISH.Retain, "identity-changed"):
            self.scoped(path, lock, *options)

    def test_record_mutation_is_checked_before_intent_and_before_unlock(self):
        original = FINISH.remove_with_intent
        for after_intent in (False, True):
            path, admin, lock, source, _ = self.prior_orphan("race-" + str(after_intent))
            options = self.prior_options(source)
            _, planned = self.scoped(path, lock, *options)
            def remove(*args, **kwargs):
                callback = kwargs["record_intent"]
                def record(intent):
                    callback(intent)
                    if after_intent:
                        source.write_bytes(source.read_bytes() + b"\n")
                if not after_intent:
                    source.write_bytes(source.read_bytes() + b"\n")
                kwargs["record_intent"] = record
                return original(*args, **kwargs)
            with mock.patch.object(FINISH, "remove_with_intent", side_effect=remove):
                if after_intent:
                    code, result = self.scoped(path, lock, *options, "--apply",
                                               "--recovery-sha256", planned["recovery_sha256"])
                    self.assertEqual(code, 1)
                    self.assertIsNone(result["result"]["child"])
                    with self.assertRaisesRegex(FINISH.Retain, "lifecycle-record"):
                        self.scoped(path, lock, *options)
                else:
                    with self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
                        self.scoped(path, lock, *options, "--apply",
                                    "--recovery-sha256", planned["recovery_sha256"])
                    with FINISH.read_only_ledger(self.state) as db:
                        self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='orphan_removals'").fetchone())
            self.assertEqual((admin / "locked").read_text().strip(), lock)

    def test_renumbered_failure_records_successor_lock_without_changing_admission(self):
        path, admin, lock, source, record = self.prior_orphan()
        options = self.prior_options(source)
        _, planned = self.scoped(path, lock, *options)
        original = FINISH.safety().supervise
        def supervise(command, **kwargs):
            if "worktree" in command and "remove" in command:
                command = [shutil.which("false")]
            return original(command, **kwargs)
        with mock.patch.object(FINISH.safety(), "supervise", side_effect=supervise):
            code, result = self.scoped(path, lock, *options, "--apply",
                                       "--recovery-sha256", planned["recovery_sha256"])
        self.assertEqual(code, 1)
        self.assertTrue(result["result"]["native_lock_preserved"], result)
        self.assertEqual(result["result"]["restored_lock"]["identity"][1], (admin / "locked").stat().st_ino)
        self.assertEqual(json.loads(self.receipt()["intent"])["before"]["orphan_identity"]["original_identity"], record)
        with self.assertRaisesRegex(FINISH.Retain, "identity-changed|lifecycle-record|private-file-permissions"):
            self.scoped(path, lock, *options)
        _, batch = OrphanLockTests.survey(self, "--apply")
        self.assertEqual(batch[str(path)]["reason"], "orphan-has-lifecycle-record")

    def recovery_commit(self, path, admin):
        (path / "file").write_text("earlier unpublished work\n")
        git(path, "commit", "-qam", "earlier task state")
        git(path, "reset", "--hard", self.head)

    def test_exact_custody_preserves_common_branch_reflog_but_batch_stays_strict(self):
        path, admin, lock = self.orphan("selected", prepare=self.recovery_commit)
        reflog = self.repo / ".git/logs/refs/heads/selected"
        before = reflog.read_bytes()
        _, batch = OrphanLockTests.survey(self)
        self.assertIn(batch[str(path)]["reason"], ("head-not-on-remote", "commit-message-recovery-required"))
        _, planned = self.scoped(path, lock)
        code, removed = self.scoped(path, lock, "--apply", "--recovery-sha256", planned["recovery_sha256"])
        self.assertEqual(code, 0, removed)
        self.assertEqual(reflog.read_bytes(), before)
        self.assertTrue(removed["result"]["branch_reflog_preserved"])
        self.assertFalse(admin.exists())

    def test_recovery_requires_surviving_commit_custody_and_remote_head(self):
        path, admin, lock = self.orphan("selected", prepare=self.recovery_commit)
        reflog = self.repo / ".git/logs/refs/heads/selected"
        raw = reflog.read_bytes()
        _, planned = self.scoped(path, lock)
        reflog.write_bytes(raw + raw.splitlines(keepends=True)[-1])
        with self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
            self.scoped(path, lock, "--apply", "--recovery-sha256", planned["recovery_sha256"])
        reflog.unlink()
        (admin / "COMMIT_EDITMSG").unlink()
        with self.assertRaisesRegex(FINISH.Retain, "unpreserved|not-preserved|not-on-remote"):
            self.scoped(path, lock)
        reflog.write_bytes(raw)
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        with self.assertRaisesRegex(FINISH.Retain, "head-not-on-remote"):
            self.scoped(path, lock)
        self.assertEqual((admin / "locked").read_text().strip(), lock)

    def test_remote_only_recovery_commit_remains_admitted(self):
        path, admin, lock = self.orphan("selected")
        oid = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", self.head, "-m", "remote task")
        git(self.repo, "update-ref", "refs/remotes/origin/other", oid)
        (admin / "ORIG_HEAD").write_text(oid + "\n")
        reflog = self.repo / ".git/logs/refs/heads/selected"
        self.assertNotIn(oid, reflog.read_text())
        original = FINISH.remote_preserved
        def unavailable(snap, candidate, until):
            if candidate == oid:
                raise FINISH.Retain("git-failed")
            return original(snap, candidate, until)
        with mock.patch.object(FINISH, "remote_preserved", side_effect=unavailable):
            with self.assertRaisesRegex(FINISH.Retain, "git-failed"):
                self.scoped(path, lock)
        _, planned = self.scoped(path, lock)
        self.assertEqual(self.scoped(path, lock, "--apply", "--recovery-sha256",
                                    planned["recovery_sha256"])[0], 0)
        self.assertEqual(git(self.repo, "rev-parse", "refs/remotes/origin/other"), oid)

    def test_direct_recovery_roots_skip_remote_walk_but_head_still_requires_it(self):
        path, _, lock = self.orphan("selected", prepare=self.recovery_commit)
        original, checked = FINISH.remote_preserved, []
        def remote(snap, oid, until):
            checked.append(oid)
            return original(snap, oid, until)
        with mock.patch.object(FINISH, "remote_preserved", side_effect=remote):
            _, planned = self.scoped(path, lock)
        self.assertEqual(planned["disposition"], "candidate")
        self.assertEqual(checked, [self.head])
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        with self.assertRaisesRegex(FINISH.Retain, "head-not-on-remote"):
            self.scoped(path, lock)

    def test_reflog_membership_does_not_admit_missing_or_noncommit_recovery_objects(self):
        path, admin, lock = self.orphan("selected")
        reflog = self.repo / ".git/logs/refs/heads/selected"
        raw = reflog.read_bytes()
        for oid, reason in (("a" * 40, "git-failed"),
                            ("--help", "recovery-reference-invalid"),
                            (git(self.repo, "rev-parse", "HEAD:file"), "recovery-not-a-commit"),
                            (git(self.repo, "rev-parse", "HEAD^{tree}"), "recovery-not-a-commit")):
            (admin / "ORIG_HEAD").write_text(oid + "\n")
            reflog.write_bytes(raw if oid == "--help" else
                              raw + f"{self.head} {oid} Fixture <fixture@example.test> 1 +0000\tfixture\n".encode())
            with self.subTest(oid=oid), self.assertRaisesRegex(FINISH.Retain, reason):
                self.scoped(path, lock)
        self.assertEqual((admin / "locked").read_text().strip(), lock)

    def test_plan_rejects_head_index_and_admin_reflog_drift(self):
        for surface in ("head", "branch", "index", "logs/HEAD"):
            name = "drift-" + surface.replace("/", "-")
            path, admin, lock = self.orphan(name)
            _, planned = self.scoped(path, lock)
            if surface == "head":
                commit = git(self.repo, "commit-tree", "HEAD^{tree}", "-p", self.head, "-m", "later")
                git(self.repo, "update-ref", "refs/heads/" + name, commit)
            elif surface == "branch":
                git(self.repo, "branch", name + "-moved", self.head)
                git(self.repo, "--git-dir=" + str(admin), "symbolic-ref", "HEAD", "refs/heads/" + name + "-moved")
            elif surface == "index":
                git(self.repo, "--git-dir=" + str(admin), "update-index", "--cacheinfo",
                    "100644", git(self.repo, "rev-parse", "HEAD:.gitignore"), "file")
            else:
                log = admin / surface
                log.write_bytes(log.read_bytes() * 2)
            with self.subTest(surface=surface), self.assertRaisesRegex(
                    FINISH.Retain, "selection-changed|staged-or-index-only|preimage-changed"):
                self.scoped(path, lock, "--apply", "--recovery-sha256", planned["recovery_sha256"])
            self.assertEqual((admin / "locked").read_text().strip(), lock)

    def test_exact_plan_and_apply_preserve_second_candidate_and_enrollment(self):
        path, admin, lock = self.orphan("selected")
        other, other_admin, other_lock = self.orphan("unselected")
        ledger = (self.state / "lifecycle.sqlite").read_bytes()
        code, planned = self.scoped(path, lock)
        self.assertEqual(code, 0)
        self.assertEqual(planned["disposition"], "candidate")
        self.assertEqual((self.state / "lifecycle.sqlite").read_bytes(), ledger)
        other_before = FINISH.sibling_identity(
            {"owner": str(self.repo), "common": str(self.repo / ".git"),
             "common_id": FINISH.identity(self.repo / ".git")}, str(other), float("inf"))
        code, removed = self.scoped(path, lock, "--apply", "--recovery-sha256", planned["recovery_sha256"])
        self.assertEqual(code, 0, removed)
        self.assertEqual(removed["disposition"], "pruned", removed)
        self.assertFalse(admin.exists())
        self.assertTrue(other_admin.exists())
        self.assertEqual((other_admin / "locked").read_text().strip(), other_lock)
        self.assertEqual(git(self.repo, "rev-parse", "selected"), self.head)
        outcome = removed["result"]
        self.assertTrue(outcome["siblings_preserved"])
        self.assertTrue(outcome["branch_reflog_preserved"])
        self.assertTrue(outcome["child"]["reaped"])
        self.assertTrue(outcome["child"]["group_absent"])
        self.assertEqual(outcome["sibling_identities_after"][str(other)], other_before)
        with FINISH.read_only_ledger(self.state) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM worktrees").fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT 1 FROM worktrees WHERE path=?", (str(path),)).fetchone())
        self.assertEqual(json.loads(self.receipt()["result"]), outcome)

    def test_exact_selection_rejects_ambiguous_path_and_wrong_proof(self):
        path, admin, lock = self.orphan("selected")
        for options, reason in (
                (["--orphan-path", str(path)], "one-exact-path"),
                (["--creation-token", str(uuid.uuid4())], "selection-changed"),
                (["--expected-head", "a" * 40], "selection-changed"),
                (["--missing-path-id", "not-an-inode"], "exact-path-token-head"),
                (["--apply"], "reviewed-plan"),
                (["--apply", "--recovery-sha256", "a" * 64], "preimage-changed")):
            with self.subTest(options=options), self.assertRaisesRegex(FINISH.Retain, reason):
                self.scoped(path, lock, *options)
            self.assertEqual((admin / "locked").read_text().strip(), lock)
        with FINISH.read_only_ledger(self.state) as db:
            self.assertIsNone(db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='orphan_removals'").fetchone())

    def test_holders_match_prior_root_and_admin_alias_inodes(self):
        path, admin, lock = self.orphan("selected")
        dev, ino = map(int, self.path_ids[str(path)].split(":"))
        for details in ((dev, ino), (admin.stat().st_dev, (admin / "index").stat().st_ino)):
            self.output = f"p1\0\nf3\0D0x{details[0]:x}\0i{details[1]}\0n/fixture/alias\0\n".encode()
            with self.subTest(details=details), self.assertRaisesRegex(FINISH.Retain, "pending-departure"):
                self.scoped(path, lock)
        for output in (b"", b"p1\0\nf3\0n/fixture (stat: Permission denied)\0\n"):
            self.output = output
            with self.subTest(output=output), self.assertRaisesRegex(FINISH.Retain, "visibility-unknown"):
                self.scoped(path, lock)
        self.assertEqual((admin / "locked").read_text().strip(), lock)

    def test_plan_binds_admin_and_sibling_registration(self):
        path, admin, lock = self.orphan("selected")
        _, planned = self.scoped(path, lock)
        self.orphan("new-sibling")
        with self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
            self.scoped(path, lock, "--apply", "--recovery-sha256", planned["recovery_sha256"])
        self.assertEqual((admin / "locked").read_text().strip(), lock)
        _, planned = self.scoped(path, lock)
        old = admin / "old-lock"
        (admin / "locked").rename(old)
        (admin / "locked").write_text(lock + "\n")
        old.unlink()
        with self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
            self.scoped(path, lock, "--apply", "--recovery-sha256", planned["recovery_sha256"])

    def test_plan_binds_sibling_created_before_final_observation(self):
        path, _, lock = self.orphan("selected")
        sibling = self.root / "late-sibling"
        original_holders, original_digest = FINISH.manual_holders, FINISH.recovery_digest
        proofs = []
        def holders(*args, **kwargs):
            original_holders(*args, **kwargs)
            git(self.repo, "worktree", "add", "-b", "late-sibling", str(sibling))
        def digest(proof):
            proofs.append(json.loads(json.dumps(proof)))
            return original_digest(proof)
        with mock.patch.object(FINISH, "manual_holders", side_effect=holders), \
                mock.patch.object(FINISH, "recovery_digest", side_effect=digest):
            _, first = self.scoped(path, lock)
        _, second = self.scoped(path, lock)
        self.assertEqual(first["recovery_sha256"], second["recovery_sha256"])
        self.assertIn(str(sibling), [row["worktree"] for row in proofs[0]["siblings"]])
        self.assertIn(str(sibling), proofs[0]["sibling_identities"])

    def test_separate_processes_preserve_plan_across_sibling_progress(self):
        path, admin, lock, source, _ = self.prior_orphan()
        options = self.prior_options(source)
        # Separate interpreters exercise native admission and serialization without
        # a profiling hook. Only host storage/holder observations use fixture data.
        code = '''import importlib.util, sys, types
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock
spec = importlib.util.spec_from_loader("finish", SourceFileLoader("finish", sys.argv[1]))
finish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finish)
original = finish.run
def run(command, **kwargs):
    if Path(command[0]).name == "lsof":
        return types.SimpleNamespace(stdout=b"p1\\0\\nf1\\0n/fixture/unrelated\\0\\n", stderr=b"")
    return original(command, **kwargs)
with mock.patch.object(finish, "storage_guard"), mock.patch.object(finish, "run", side_effect=run):
    sys.exit(finish.main(sys.argv[2:]))
'''
        def process(seed, *extra):
            result = subprocess.run(
                [sys.executable, "-B", "-c", code, str(TOOL),
                 *self.scoped_arguments(path, lock, *options, *extra)],
                env=dict(os.environ, PYTHONHASHSEED=str(seed)),
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            return json.loads(result.stdout)[0]

        planned = process(1)
        self.assertEqual(process(2), planned)
        git(self.wt, "commit", "--allow-empty", "-m", "sibling progress")
        progress = [process(3)]
        git(self.wt, "checkout", "--detach")
        progress.append(process(4))
        git(self.wt, "checkout", "-b", "sibling-progress")
        progress.append(process(5))
        raw = FINISH.records(str(self.repo))
        removed = process(6, "--apply", "--recovery-sha256", planned["recovery_sha256"])
        self.assertEqual(progress, [planned] * 3)
        self.assertEqual(removed["disposition"], "pruned", removed)
        self.assertFalse(admin.exists())
        self.assertTrue(removed["result"]["siblings_preserved"])
        self.assertTrue(removed["result"]["branch_reflog_preserved"])
        self.assertTrue(removed["result"]["child"]["reaped"])
        self.assertTrue(removed["result"]["child"]["group_absent"])
        intent = json.loads(self.receipt()["intent"])
        self.assertEqual(intent["siblings"], [row for row in raw if row["worktree"] != str(path)])
        self.assertTrue(all(not {"HEAD", "branch", "detached"}.intersection(row)
                            for row in intent["before"]["siblings"]))

    def test_sibling_progress_during_both_rechecks_keeps_raw_removal_receipt(self):
        path, _, lock = self.orphan("selected")
        _, planned = self.scoped(path, lock)
        original, observations = FINISH.orphan_candidate, []
        native, removals = FINISH.safety().supervise, []
        def candidate(*args, **kwargs):
            observations.append(len(observations) + 1)
            if len(observations) in (2, 3):
                git(self.wt, "commit", "--allow-empty", "-m", "concurrent sibling progress")
            return original(*args, **kwargs)
        def supervise(command, **kwargs):
            if "worktree" in command and "remove" in command:
                removals.append(command)
            return native(command, **kwargs)
        with mock.patch.object(FINISH, "orphan_candidate", side_effect=candidate), \
                mock.patch.object(FINISH.safety(), "supervise", side_effect=supervise):
            code, result = self.scoped(path, lock, "--apply", "--recovery-sha256", planned["recovery_sha256"])
        self.assertEqual(code, 0, result)
        self.assertEqual(observations, [1, 2, 3])
        self.assertEqual(len(removals), 1)
        self.assertNotIn("--force", removals[0])
        self.assertNotIn("-f", removals[0])
        report = result["result"]["sibling_comparison"]
        self.assertTrue(report["preserved"])
        self.assertEqual(report["changes"][0]["kind"], "operational")
        self.assertEqual(set(report["changes"][0]["fields"]), {"HEAD"})
        intent = json.loads(self.receipt()["intent"])
        self.assertEqual(next(row for row in intent["siblings"] if row["worktree"] == str(self.wt))["HEAD"],
                         self.head)

    def test_plan_keeps_every_nonoperational_sibling_field_and_order_bound(self):
        path, admin, lock = self.orphan("selected")
        self.orphan("other")
        _, planned = self.scoped(path, lock)
        original = FINISH.records
        raw = original(str(self.repo))
        native, mutations = FINISH.safety().supervise, []
        def supervise(command, **kwargs):
            if "worktree" in command and ("unlock" in command or "remove" in command):
                mutations.append(command)
            return native(command, **kwargs)
        for change in ("missing", "order", "locked", "prunable", "future-field"):
            def records(owner):
                rows = [dict(row) for row in original(owner)]
                if change == "missing":
                    return [row for row in rows if row["worktree"] != str(self.wt)]
                if change == "order":
                    return rows[:1] + list(reversed(rows[1:]))
                next(row for row in rows if row["worktree"] == str(self.wt))[change] = "changed"
                return rows
            with self.subTest(change=change), \
                    mock.patch.object(FINISH, "records", side_effect=records), \
                    mock.patch.object(FINISH.safety(), "supervise", side_effect=supervise), \
                    self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
                self.scoped(path, lock, "--apply", "--recovery-sha256", planned["recovery_sha256"])
            self.assertEqual(mutations, [])
            self.assertEqual((admin / "locked").read_text().strip(), lock)
            self.assertEqual(original(str(self.repo)), raw)
        with FINISH.read_only_ledger(self.state) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='orphan_removals'").fetchone())

    def test_plan_rejects_sibling_lock_and_pointer_identity_replacement(self):
        path, admin, lock = self.orphan("selected")
        sibling_admin = Path(git(self.wt, "rev-parse", "--absolute-git-dir"))
        git(self.repo, "worktree", "lock", "--reason", "sibling-owner", str(self.wt))
        native, mutations = FINISH.safety().supervise, []
        def supervise(command, **kwargs):
            if "worktree" in command and ("unlock" in command or "remove" in command):
                mutations.append(command)
            return native(command, **kwargs)
        for marker in (sibling_admin / "locked", self.wt / ".git"):
            _, planned = self.scoped(path, lock)
            previous = marker.with_name(marker.name + ".previous")
            marker.rename(previous)
            marker.write_bytes(previous.read_bytes())
            previous.unlink()
            with self.subTest(marker=marker.name), \
                    mock.patch.object(FINISH.safety(), "supervise", side_effect=supervise), \
                    self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
                self.scoped(path, lock, "--apply", "--recovery-sha256", planned["recovery_sha256"])
            self.assertEqual(mutations, [])
            self.assertEqual((admin / "locked").read_text().strip(), lock)

    def test_reappearance_and_lifecycle_or_maintenance_contention_retain_lock(self):
        path, admin, lock = self.orphan("selected")
        with FINISH.ledger(self.state):
            with self.assertRaisesRegex(FINISH.Retain, "lifecycle-busy"):
                self.scoped(path, lock)
        with FINISH.safety().maintainer_lock(self.home / ".codex"):
            with self.assertRaisesRegex(FINISH.Retain, "maintainer-lock-present"):
                self.scoped(path, lock)
        path.mkdir()
        with self.assertRaisesRegex(FINISH.Retain, "directory-present|native-admin-unavailable"):
            self.scoped(path, lock)
        self.assertEqual((admin / "locked").read_text().strip(), lock)

    def test_exact_mode_keeps_existing_content_and_enrollment_guards(self):
        def staged(path, admin):
            (path / "file").write_text("unpublished index\n")
            git(path, "add", "file")
        for name, prepare, reason in (
                ("staged", staged, "staged-or-index-only-changes"),
                ("unknown", lambda path, admin: (admin / "private-session").write_text("keep"),
                 "admin-recovery-content-unknown")):
            path, admin, lock = self.orphan(name, prepare=prepare)
            with self.subTest(name=name), self.assertRaisesRegex(FINISH.Retain, reason):
                self.scoped(path, lock)
            self.assertTrue(admin.exists())
        path, admin, lock = self.orphan("recorded")
        with FINISH.ledger(self.state) as db, db:
            db.execute("INSERT INTO worktrees(id,path,identity,created_at,state,reason) "
                       "VALUES (?,?,?,0,'active','owner-active')",
                       (str(uuid.uuid4()), str(path), json.dumps({"gitdir": str(admin)})))
        with self.assertRaisesRegex(FINISH.Retain, "lifecycle-record"):
            self.scoped(path, lock)

    def test_native_failure_is_durable_and_blocks_batch_or_exact_retry(self):
        path, admin, lock = self.orphan("selected")
        _, planned = self.scoped(path, lock)
        original = FINISH.safety().supervise
        def supervise(command, **kwargs):
            if "worktree" in command and "remove" in command:
                command = [shutil.which("false")]
            return original(command, **kwargs)
        with mock.patch.object(FINISH.safety(), "supervise", side_effect=supervise):
            code, outcome = self.scoped(path, lock, "--apply", "--recovery-sha256", planned["recovery_sha256"])
        self.assertEqual(code, 1)
        self.assertEqual(outcome["disposition"], "unknown")
        self.assertTrue(outcome["result"]["native_lock_preserved"], outcome)
        self.assertEqual((admin / "locked").read_text().strip(), lock)
        self.assertEqual(json.loads(self.receipt()["result"])["checkout"], "unknown")
        with self.assertRaisesRegex(FINISH.Retain, "lifecycle-record"):
            self.scoped(path, lock, "--apply", "--recovery-sha256", planned["recovery_sha256"])
        _, rows = OrphanLockTests.survey(self, "--apply")
        self.assertEqual(rows[str(path)]["reason"], "orphan-has-lifecycle-record")

    def test_interruption_after_intent_leaves_nonretryable_receipt(self):
        path, admin, lock = self.orphan("selected")
        _, planned = self.scoped(path, lock)
        original = FINISH.safety().supervise
        def supervise(command, **kwargs):
            if "worktree" in command and "remove" in command:
                raise KeyboardInterrupt()
            return original(command, **kwargs)
        with mock.patch.object(FINISH.safety(), "supervise", side_effect=supervise), \
                self.assertRaises(KeyboardInterrupt):
            self.scoped(path, lock, "--apply", "--recovery-sha256", planned["recovery_sha256"])
        self.assertIsNone(self.receipt()["result"])
        self.assertTrue(admin.exists())
        # Even a later restored lock must not make this uncertain attempt retryable.
        git(self.repo, "worktree", "lock", "--reason", lock, str(path))
        with self.assertRaisesRegex(FINISH.Retain, "lifecycle-record"):
            self.scoped(path, lock)

    def test_wrapper_forwards_exact_operands_and_rejects_duplicates(self):
        path, _, lock = self.orphan("selected")
        env = dict(os.environ, TMUX="")
        script = ('source "$1"; cd "$2"; _gwt_finish_tool() { printf "%s\\n" "$@"; }; '
                  '_gwt_require_worktree_storage() { :; }; _gwt_tmux_sync_context() { :; }; '
                  'shift 2; gwt prune "$@"')
        command = ["zsh", "-c", script, "fixture", str(ROOT / "functions/gwt/gwt.zsh"), str(self.repo)]
        options = ["--path", str(path), "--creation-token", lock.split(":")[1],
                   "--expected-head", self.head, "--missing-path-id", self.path_ids[str(path)],
                   *self.prior_options(self.base / "prior.json")]
        result = subprocess.run([*command, "--orphan-locks", *options], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "orphan-locks", "--worktree", str(self.repo), "--orphan-path", *options[1:]])
        for extra in (["--path", str(path)], ["--expected-head", self.head],
                      ["--allow-device-renumbering"], ["--prior-identity-file", str(self.base / "prior.json")]):
            result = subprocess.run([*command, "--orphan-locks", *options, *extra], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
        for partial in (["--allow-device-renumbering"], ["--prior-identity-file", str(self.base / "prior.json")],
                        list(self.prior_options(self.base / "prior.json"))):
            result = subprocess.run([*command, "--orphan-locks", *partial], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")


class OrphanLockTests(unittest.TestCase):
    """Missing gwt-locked registrations that never reached the lifecycle ledger."""
    tearDown = LifecycleTests.tearDown
    proof = LifecycleTests.proof
    call = LifecycleTests.call

    def setUp(self):
        LifecycleTests.setUp(self)
        (self.home / ".codex").mkdir(mode=0o700)
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.head)
        self.orphans = {}

    def orphan(self, name, *, prepare=None, reason=None):
        path = self.root / name
        lock = reason or "gwt-finish.v2:" + str(uuid.uuid4())
        git(self.repo, "worktree", "add", "--lock", "--reason", lock, "-b", name, str(path))
        admin = Path(git(path, "rev-parse", "--absolute-git-dir"))
        if prepare is not None:
            prepare(path, admin)
        shutil.rmtree(path)
        self.orphans[name] = (path, admin, lock)
        return path, admin, lock

    def survey(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = FINISH.main(["orphan-locks", "--worktree", str(self.repo), "--state-dir", str(self.state),
                                "--managed-root", str(self.root), *args])
        return code, {item["worktree"]: item for item in json.loads(output.getvalue())}

    def registered(self, path):
        return [entry for entry in FINISH.records(str(self.repo)) if entry["worktree"] == str(path)]

    def test_dry_run_lists_then_apply_unlocks_and_prunes_only_the_orphan(self):
        path, admin, lock = self.orphan("orphan-pushed")
        ledger = (self.state / "lifecycle.sqlite").read_bytes()
        code, found = self.survey()
        self.assertEqual(code, 0)
        self.assertEqual(found[str(path)]["disposition"], "candidate")
        self.assertEqual(found[str(path)]["head"], self.head)
        self.assertNotIn(str(self.wt), found)
        self.assertEqual(self.registered(path)[0]["locked"], lock)
        code, found = self.survey("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(found[str(path)]["disposition"], "pruned", found)
        self.assertEqual(self.registered(path), [])
        self.assertFalse(admin.exists())
        self.assertEqual(git(self.repo, "rev-parse", "orphan-pushed"), self.head)
        self.assertTrue(self.wt.exists())
        self.assertTrue(self.registered(self.wt))
        self.assertEqual((self.state / "lifecycle.sqlite").read_bytes(), ledger)
        self.assertEqual(self.survey()[1], {})

    def test_unpublished_staged_recorded_present_and_unknown_admin_state_are_retained(self):
        def commit(path, admin):
            (path / "file").write_text("local only\n")
            git(path, "commit", "-qam", "unpublished")
        def stage(path, admin):
            (path / "file").write_text("staged only\n")
            git(path, "add", "file")
        def unknown(path, admin):
            (admin / "private-session").write_text("keep\n")
        expected = {
            "orphan-unpushed": ("head-not-on-remote", self.orphan("orphan-unpushed", prepare=commit)),
            "orphan-staged": ("staged-or-index-only-changes", self.orphan("orphan-staged", prepare=stage)),
            "orphan-unknown": ("admin-recovery-content-unknown", self.orphan("orphan-unknown", prepare=unknown)),
            "orphan-recorded": ("orphan-has-lifecycle-record", self.orphan("orphan-recorded")),
        }
        with FINISH.ledger(self.state) as db, db:
            db.execute("INSERT INTO worktrees(id, path, identity, created_at, state, reason) "
                       "VALUES (?, ?, ?, 0, 'active', 'owner-active')",
                       (str(uuid.uuid4()), str(self.root / "elsewhere"),
                        json.dumps({"lock_reason": expected["orphan-recorded"][1][2]})))
        present = self.root / "orphan-present"
        git(self.repo, "worktree", "add", "--lock", "--reason", "gwt-finish.v2:" + str(uuid.uuid4()),
            "-b", "orphan-present", str(present))
        foreign = self.root / "foreign-lock"
        git(self.repo, "worktree", "add", "--lock", "--reason", "operator hold", "-b", "foreign-lock", str(foreign))
        shutil.rmtree(foreign)
        for options in ((), ("--apply",)):
            with self.subTest(options=options):
                code, found = self.survey(*options)
                self.assertEqual(code, 0)
                for name, (reason, (path, admin, lock)) in expected.items():
                    self.assertEqual(found[str(path)]["disposition"], "retained")
                    self.assertRegex(found[str(path)]["reason"], reason)
                    self.assertEqual(self.registered(path)[0]["locked"], lock)
                    self.assertTrue(admin.exists())
                self.assertEqual(found[str(present)]["reason"], "orphan-directory-present")
                self.assertNotIn(str(foreign), found)
                self.assertEqual(self.registered(foreign)[0]["locked"], "operator hold")

    def test_missing_path_under_another_device_is_retained(self):
        container = self.root / "volume"
        container.mkdir()
        path = container / "orphan-mounted"
        lock = "gwt-finish.v2:" + str(uuid.uuid4())
        git(self.repo, "worktree", "add", "--lock", "--reason", lock, "-b", "orphan-mounted", str(path))
        shutil.rmtree(path)
        real = os.lstat
        def lstat(target, *args, **kwargs):
            details = real(target, *args, **kwargs)
            if str(target) == str(container):
                values = list(details)
                values[stat.ST_DEV] = details.st_dev + 1
                return os.stat_result(values)
            return details
        with mock.patch.object(FINISH.os, "lstat", side_effect=lstat):
            code, found = self.survey("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(found[str(path)]["reason"], "orphan-ancestor-device-changed")
        self.assertEqual(self.registered(path)[0]["locked"], lock)

    def test_orphan_locks_needs_owning_checkout_and_lifecycle_state(self):
        with self.assertRaisesRegex(FINISH.Retain, "owning-checkout"):
            FINISH.main(["orphan-locks", "--worktree", str(self.wt), "--state-dir", str(self.state),
                         "--managed-root", str(self.root)])
        with self.assertRaisesRegex(FINISH.Retain, "one-owning-checkout"):
            FINISH.main(["orphan-locks", "--worktree", str(self.repo), "--reason", "x",
                         "--state-dir", str(self.state)])
        with self.assertRaisesRegex(FINISH.Retain, "lifecycle-state"):
            FINISH.main(["orphan-locks", "--worktree", str(self.repo),
                         "--state-dir", str(self.base / "absent-state")])

    def test_wrapper_forwards_orphan_survey_and_apply_only_with_explicit_flag(self):
        env = dict(os.environ, DOTFILES_WORKTREES_ROOT=str(self.root),
                   DOTFILES_GWT_FINISH_STATE=str(self.state), TMUX="")
        script = ('source "$1"; cd "$2"; '
                  '_gwt_finish_tool() { printf "%s\\n" "$@"; }; _gwt_require_worktree_storage() { :; }; '
                  '_gwt_tmux_sync_context() { :; }; shift 2; gwt prune "$@"')
        command = ["zsh", "-c", script, "fixture", str(ROOT / "functions/gwt/gwt.zsh"), str(self.wt)]
        for arguments, expected in ((["--orphan-locks"], ["orphan-locks", "--worktree", str(self.repo)]),
                                    (["--orphan-locks", "--apply"],
                                     ["orphan-locks", "--worktree", str(self.repo), "--apply"])):
            result = subprocess.run([*command, *arguments], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(result.stdout.splitlines(), expected)
        for arguments in (["--apply"], ["--force"]):
            result = subprocess.run([*command, *arguments], env=env, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")


class ExternalRemovalTests(unittest.TestCase):
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    finish = LifecycleTests.finish
    tearDown = ReleaseTests.tearDown

    def setUp(self):
        ReleaseTests.setUp(self)
        # Keep this finished checkout pending so the external-removal owner
        # can exercise its own closeout instead of the normal finish route.
        self.proofs[self.url].update(merged=False, state="open", merge=None)

    def arguments(self):
        status = self.call("status")[1][0]
        return ["--external-removal", "--worktree-id", status["id"],
                "--generation", str(status["generation"]),
                "--reason", "Operator removed this finished task"]

    def test_external_removal_records_truth_and_preserves_completion_history(self):
        self.finish()
        args = self.arguments()
        with FINISH.ledger(self.state) as db:
            original = dict(FINISH.get_row(db, self.wt))
        git(self.repo, "worktree", "remove", "--force", "--force", str(self.wt))
        self.assertEqual(self.call("status")[1][0]["checkout"], "retained")
        code, rows = self.call("reconcile", *args)
        result = rows[0]
        self.assertEqual(code, 0)
        self.assertEqual(result["checkout"], "removed")
        self.assertEqual(result["retirement_state"], "removed")
        self.assertFalse(result["release_available"])
        self.assertFalse(result["external_removal"]["native_attempted"])
        self.assertEqual(self.call("reconcile", *args)[1][0], result)
        self.assertEqual(self.call("status")[1][0], result)
        with FINISH.ledger(self.state) as db:
            current = dict(FINISH.get_row(db, self.wt))
            retirement = FINISH.retirement(db, current)
            self.assertIsNone(retirement["intent"])
        for key in original.keys() - {"reason", "checked_at"}:
            self.assertEqual(current[key], original[key])
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertTrue(self.legacy.exists())
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.admin.exists())

    def test_present_checkout_admin_and_registration_never_count_as_removed(self):
        self.finish()
        args = self.arguments()
        with self.assertRaisesRegex(FINISH.Retain, "target-or-admin-present"):
            self.call("reconcile", *args)
        git(self.repo, "worktree", "remove", "--force", "--force", str(self.wt))
        for path in (self.wt, self.admin):
            with self.subTest(path=path.name):
                path.symlink_to(self.base / "missing")
                try:
                    with self.assertRaisesRegex(FINISH.Retain, "target-or-admin-present"):
                        self.call("reconcile", *args)
                finally:
                    path.unlink()
        self.admin.mkdir()
        (self.admin / "gitdir").write_text(str(self.wt / ".git") + "\n")
        # Retained registration is independently disqualifying when its recorded
        # admin path is absent (Git may register the same path under another name).
        other = self.admin.with_name("other-registration")
        self.admin.rename(other)
        (other / "HEAD").write_text("ref: refs/heads/released-feature\n")
        (other / "commondir").write_text("../..\n")
        with self.assertRaisesRegex(FINISH.Retain, "target-registered"):
            self.call("reconcile", *args)
        self.assertEqual(self.call("status")[1][0]["checkout"], "retained")

    def test_exact_owner_generation_and_retained_branch_are_required(self):
        self.finish()
        args = self.arguments()
        git(self.repo, "worktree", "remove", "--force", "--force", str(self.wt))
        for extra, reason in [(["--owner", "other"], "recorded-owner"),
                              (["--generation", "999"], "exact-record-and-generation"),
                              (["--worktree-id", str(uuid.uuid4())], "exact-record-and-generation")]:
            with self.subTest(extra=extra), self.assertRaisesRegex(FINISH.Retain, reason):
                self.call("reconcile", *args, *extra)
        git(self.repo, "branch", "-D", "released-feature")
        with self.assertRaises(FINISH.Retain):
            self.call("reconcile", *args)
        self.assertEqual(self.call("status")[1][0]["checkout"], "retained")

    def test_unfinished_owners_and_recovery_pins_prevent_reconciliation(self):
        self.finish()
        self.call("pin", "--reason", "still needed")
        args = self.arguments()
        with self.assertRaisesRegex(FINISH.Retain, "finished-owners"):
            self.call("reconcile", *args)
        self.finish()
        args = self.arguments()
        with self.assertRaisesRegex(FINISH.Retain, "recovery-pin-present"):
            self.call("reconcile", *args)

    def test_native_attempt_evidence_cannot_be_overwritten(self):
        self.finish()
        args = self.arguments()
        with FINISH.ledger(self.state) as db, db:
            row = FINISH.get_row(db, self.wt)
            db.execute("UPDATE retirement SET state='incomplete', intent=? WHERE worktree_id=?",
                       ('{"original":"native-attempt"}', row["id"]))
        with self.assertRaisesRegex(FINISH.Retain, "cannot-replace-native-removal-proof"):
            self.call("reconcile", *args)
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            self.assertEqual(FINISH.retirement(db, row)["intent"], '{"original":"native-attempt"}')

    def test_observation_failure_rolls_back_the_record(self):
        self.finish()
        args = self.arguments()
        git(self.repo, "worktree", "remove", "--force", "--force", str(self.wt))
        original = FINISH.records
        checks = 0

        def denied_on_recheck(owner):
            nonlocal checks
            checks += 1
            if checks == 2:
                raise PermissionError(errno.EACCES, "fixture denied")
            return original(owner)

        with mock.patch.object(FINISH, "records", side_effect=denied_on_recheck):
            with self.assertRaises(PermissionError):
                self.call("reconcile", *args)
        result = self.call("status")[1][0]
        self.assertEqual(result["checkout"], "retained")
        self.assertEqual(result["retirement_state"], "enrolled")
        self.assertNotIn("external_removal", result)


class UnstartedRemovalTests(unittest.TestCase):
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    finish = ReleaseTests.finish
    tearDown = ReleaseTests.tearDown
    remove = FinalizedRemovalTests.remove

    def setUp(self):
        ReleaseTests.setUp(self)
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.head)
        with mock.patch.object(FINISH, "evaluate_release", return_value="fixture-retained"):
            self.finish("--release")
        with self.expire_after_intent(), mock.patch.object(FINISH, "git", wraps=FINISH.git) as native_git:
            result = self.remove()[1][0]
        self.assertFalse(any("unlock" in call.args for call in native_git.call_args_list))
        self.assertEqual((result["checkout"], result["retirement_state"]), ("unknown", "incomplete"))
        self.original = self.receipt()
        self.assertIs(json.loads(self.original["result"])["native_attempted"], False)

    def advance_ref(self, name="refs/remotes/origin/main"):
        path = self.repo / "upstream"
        path.write_text((path.read_text() if path.exists() else "") + "independent progress\n")
        git(self.repo, "add", path.name)
        git(self.repo, "commit", "-m", "upstream progress")
        head = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", name, head)
        return head

    def append_reflog(self, ref):
        path = self.repo / git(self.repo, "rev-parse", "--git-path", "logs/" + ref)
        with path.open("ab") as output:
            output.write(f"{self.head} {self.head} Fixture <fixture@example.test> "
                         "1800000000 +0000\tindependent observation\n".encode())

    @contextlib.contextmanager
    def expire_after_intent(self):
        native = FINISH.finalized_admission
        def expire(db, row, *args, **kwargs):
            if FINISH.retirement(db, row)["state"] == "intent":
                self.assertFalse(db.in_transaction)
                raise FINISH.Retain("admission-deadline")
            return native(db, row, *args, **kwargs)

        with mock.patch.object(FINISH, "finalized_admission", side_effect=expire):
            yield

    def receipt(self):
        with FINISH.ledger(self.state) as db:
            return dict(FINISH.retirement(db, FINISH.get_row(db, self.wt)))

    def arguments(self):
        item = self.receipt()
        args = ["--unstarted-removal", "--intent-id", json.loads(item["intent"])["id"],
                "--generation", str(item["generation"])]
        for key in ("intent", "result"):
            args += ["--" + key + "-sha256", hashlib.sha256(item[key].encode()).hexdigest()]
        return args

    def recover(self, *extra):
        with mock.patch.object(FINISH, "manual_holders"):
            return self.call("reconcile", *self.arguments(), *extra)

    def rewrite_result(self, result):
        with FINISH.ledger(self.state) as db, db:
            db.execute("UPDATE retirement SET result=? WHERE worktree_id=?",
                       (json.dumps(result, indent=2) + "\n", self.original["worktree_id"]))

    def test_inspector_is_read_only_and_exposes_exact_hashes_without_inventory(self):
        before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                  for p in self.state.iterdir()}
        with mock.patch.object(FINISH, "git", side_effect=AssertionError("read-only inspector ran Git")), \
                mock.patch.object(FINISH, "manual_holders", side_effect=AssertionError("holder census")):
            result = self.call("status", "--read-only", "--removal-receipt")[1][0]["removal_receipt"]
        self.assertEqual(result["intent_id"], json.loads(self.original["intent"])["id"])
        self.assertIs(result["result"]["child"], None)
        for key in ("intent", "result"):
            self.assertEqual(result[key + "_sha256"], hashlib.sha256(self.original[key].encode()).hexdigest())
        self.assertLess(len(json.dumps(result)), 2048)
        self.assertEqual(before, {p.name: (p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                                  for p in self.state.iterdir()})

    def test_recovery_archives_bytes_invalidates_releases_and_never_removes(self):
        result = json.loads(self.original["result"])
        result.pop("native_attempted")  # Existing deadline receipts predate the producer fact.
        self.rewrite_result(result)
        original, args = self.receipt(), self.arguments()
        with mock.patch.object(FINISH, "remove_with_intent", side_effect=AssertionError("removal")):
            code, rows = self.recover()
        self.assertEqual(code, 0)
        self.assertEqual(rows[0]["checkout"], "retained")
        self.assertEqual(rows[0]["generation"], original["generation"] + 1)
        self.assertEqual(rows[0]["released_owners"], [])
        self.assertEqual(rows[0]["owners"], [{"owner": "owner-1", "completed": 1}])
        self.assertTrue(self.wt.exists())
        with FINISH.ledger(self.state) as db:
            archive = db.execute("SELECT * FROM unstarted_removal_recoveries").fetchone()
            self.assertEqual((archive["intent"], archive["result"]), (original["intent"], original["result"]))
        view = self.call("status", "--read-only", "--removal-receipt")[1][0]["removal_receipt"]
        self.assertIs(view["reconciled"], True)
        with FINISH.ledger(self.state) as db, db:
            db.execute("INSERT INTO owner_releases VALUES (?, ?, ?)",
                       (original["worktree_id"], "owner-1", original["generation"] + 1))
        with self.assertRaisesRegex(FINISH.Retain, "exact-incomplete-intent"):
            self.call("reconcile", *args)
        self.assertEqual(self.receipt()["generation"], original["generation"] + 1)
        self.assertEqual(self.call("status")[1][0]["released_owners"], ["owner-1"])
        self.assertEqual(self.remove()[1][0]["checkout"], "removed")

    def test_new_explicit_unstarted_fact_qualifies(self):
        self.assertEqual(self.recover()[1][0]["retirement_state"], "enrolled")

    def test_tracking_ref_progress_is_observed_without_reusing_old_release_authority(self):
        head = self.advance_ref()
        with mock.patch.object(FINISH, "remove_with_intent", side_effect=AssertionError("removal")):
            result = self.recover()[1][0]
        self.assertEqual(result["released_owners"], [])
        self.assertEqual(result["generation"], self.original["generation"] + 1)
        self.assertTrue(self.wt.exists())
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        with FINISH.ledger(self.state) as db:
            archive = db.execute("SELECT * FROM unstarted_removal_recoveries").fetchone()
            self.assertEqual((archive["intent"], archive["result"]),
                             (self.original["intent"], self.original["result"]))
            refs = json.loads(archive["fact"])["preserved_refs"]
        self.assertEqual(refs["refs/remotes/origin/main"]["head"], head)
        receipt = self.call("status", "--read-only", "--removal-receipt")[1][0]["removal_receipt"]
        self.assertEqual(receipt["ref_observation"]["sha256"], FINISH.recovery_digest(refs))
        self.assertEqual(receipt["ref_observation"]["changed_count"], 1)
        self.assertEqual(receipt["ref_observation"]["changes"][0]["head_observed"], head)

    def test_tracking_reflog_only_progress_is_reported(self):
        self.append_reflog("refs/remotes/origin/main")
        self.recover()
        receipt = self.call("status", "--read-only", "--removal-receipt")[1][0]["removal_receipt"]
        observation = receipt["ref_observation"]
        self.assertEqual(observation["changed_count"], 1)
        self.assertEqual(observation["changes"], [{"ref": "refs/remotes/origin/main",
                         "head_before": self.head, "head_observed": self.head, "reflog_changed": True}])

    def test_archives_without_current_ref_observations_remain_readable(self):
        self.recover()
        with FINISH.ledger(self.state) as db, db:
            row = db.execute("SELECT worktree_id, fact FROM unstarted_removal_recoveries").fetchone()
            fact = json.loads(row["fact"])
            fact.pop("preserved_refs")
            db.execute("UPDATE unstarted_removal_recoveries SET fact=? WHERE worktree_id=?",
                       (json.dumps(fact), row["worktree_id"]))
        receipt = self.call("status", "--read-only", "--removal-receipt")[1][0]["removal_receipt"]
        self.assertIs(receipt["reconciled"], True)
        self.assertNotIn("ref_observation", receipt)

    def test_missing_ref_or_task_reflog_change_cannot_be_reconciled(self):
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        with self.assertRaises(FINISH.Retain):
            self.recover()
        self.assertEqual(self.receipt(), self.original)
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.head)
        self.append_reflog("refs/heads/released-feature")
        with self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
            self.recover()
        self.assertEqual(self.receipt(), self.original)

    def test_ref_progress_during_recovery_refuses_before_archiving(self):
        native = FINISH.finalized_admission

        def progress(*args, **kwargs):
            result = native(*args, **kwargs)
            self.advance_ref()
            return result

        with mock.patch.object(FINISH, "finalized_admission", side_effect=progress):
            with self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
                self.recover()
        self.assertEqual(self.receipt(), self.original)

    def test_historical_roots_cannot_attest_current_recovery_data(self):
        self.recover()
        head = self.advance_ref()
        with mock.patch.object(FINISH, "evaluate_release", return_value="fixture-retained"):
            self.finish("--release")
        (self.admin / "FETCH_HEAD").write_text(head + "\t\tbranch 'main' of https://example.test/repo\n")
        with self.expire_after_intent():
            self.remove()
        original = self.receipt()
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.head)
        git(self.repo, "reflog", "expire", "--expire=now", "--expire-unreachable=now", "refs/remotes/origin/main")
        with self.assertRaisesRegex(FINISH.Retain, "fetch-head-recovery-required"):
            self.recover()
        self.assertEqual(self.receipt(), original)

    def test_recovery_reuses_original_explicit_preserved_branches(self):
        self.recover()
        with mock.patch.object(FINISH, "evaluate_release", return_value="fixture-retained"):
            self.finish("--release")
        branch = "refs/heads/saved-recovery"
        git(self.repo, "branch", "saved-recovery", self.head)
        native = FINISH.finalized_admission
        with mock.patch.object(FINISH, "manual_holders"), self.expire_after_intent():
            self.call("remove", "--finalized", "--preserve-ref", branch)
        head = self.advance_ref(branch)
        with mock.patch.object(FINISH, "finalized_admission", wraps=native) as admission:
            self.assertEqual(self.recover()[1][0]["retirement_state"], "enrolled")
        self.assertEqual(admission.call_args.args[2].preserve_ref, [branch])
        self.assertEqual(git(self.repo, "rev-parse", branch), head)

    def test_recovery_fits_one_scan_budget_without_repeating_the_expired_admission(self):
        clock, elapsed = FINISH.time.monotonic(), 0
        native = FINISH.finalized_admission

        def slow_admission(*args, **kwargs):
            nonlocal elapsed
            result = native(*args, **kwargs)
            elapsed += 65
            FINISH.safety().tick(args[3])
            return result

        with mock.patch.object(FINISH.time, "monotonic", side_effect=lambda: clock + elapsed), \
                mock.patch.object(FINISH, "finalized_admission", side_effect=slow_admission):
            self.assertEqual(self.recover()[1][0]["retirement_state"], "enrolled")
        self.assertEqual(elapsed, 65)

    def failed_unlock(self, *, after):
        self.recover()
        native = FINISH.git

        def expire(*args, **kwargs):
            if "unlock" in args:
                if after:
                    native(*args, **kwargs)
                raise FINISH.Retain("admission-deadline")
            return native(*args, **kwargs)

        with mock.patch.object(FINISH, "git", side_effect=expire):
            result = self.remove()[1][0]
        self.assertEqual(result["checkout"], "unknown")
        outcome = json.loads(self.receipt()["result"])
        self.assertIs(outcome["native_attempted"], True)
        self.assertIs(outcome["native_lock_preserved"], True)
        self.assertIs(outcome["child"], None)
        with self.assertRaisesRegex(FINISH.Retain, "not-proven-unstarted"):
            self.recover()

    def test_unlock_failure_is_attempted_even_before_unlock_returns(self):
        self.failed_unlock(after=False)

    def test_unlock_followed_by_failure_stays_attempted_after_lock_restoration(self):
        self.failed_unlock(after=True)

    def test_completion_and_release_proof_are_still_required(self):
        self.proofs[self.url]["merged"] = False
        with self.assertRaisesRegex(FINISH.Retain, "pr-closed-unmerged"):
            self.recover()
        self.proofs[self.url]["merged"] = True
        with FINISH.ledger(self.state) as db, db:
            db.execute("DELETE FROM owner_releases WHERE worktree_id=?", (self.original["worktree_id"],))
        with self.assertRaisesRegex(FINISH.Retain, "awaiting-owner-release"):
            self.recover()
        self.assertEqual(self.receipt(), self.original)

    def test_inspector_preserves_sidecar_refusal(self):
        sidecar = self.state / "lifecycle.sqlite-journal"
        sidecar.write_bytes(b"pending")
        sidecar.chmod(0o600)
        with self.assertRaisesRegex(FINISH.Retain, "lifecycle-sidecar-requires-recovery"):
            self.call("status", "--read-only", "--removal-receipt")
        self.assertEqual(sidecar.read_bytes(), b"pending")
        sidecar.unlink()

    def test_missing_dispatched_or_contradictory_child_facts_stay_held(self):
        original = json.loads(self.original["result"])
        variants = [{**original, "native_attempted": value} for value in (True, 0, None)]
        variants += [{**original, "child": value} for value in ({}, False, {"pid": 42, "reaped": True})]
        variants += [{k: v for k, v in original.items() if k != "child"},
                     {**original, "path_absent": False}, {**original, "reason": "removal-incomplete"}]
        for result in variants:
            with self.subTest(result=result):
                self.rewrite_result(result)
                before = self.receipt()
                with self.assertRaisesRegex(FINISH.Retain, "not-proven-unstarted"):
                    self.recover()
                self.assertEqual(self.receipt(), before)

    def test_exact_owner_generation_and_receipt_are_required(self):
        for extra in (("--owner", "foreign"), ("--generation", "99"), ("--result-sha256", "0" * 64)):
            with self.subTest(extra=extra), self.assertRaises(FINISH.Retain):
                self.recover(*extra)
            self.assertEqual(self.receipt(), self.original)

    def test_source_holder_and_preimage_changes_refuse(self):
        with mock.patch.object(FINISH, "manual_holders", side_effect=FINISH.Retain("live-holder")):
            with self.assertRaisesRegex(FINISH.Retain, "live-holder"):
                self.call("reconcile", *self.arguments())
        (self.wt / "file").write_text("changed\n")
        with self.assertRaisesRegex(FINISH.Retain, "dirty-or-untracked"):
            self.recover()
        self.assertEqual(self.receipt(), self.original)

    def test_historical_sibling_departure_does_not_authorize_or_block_metadata_recovery(self):
        git(self.repo, "worktree", "remove", str(self.legacy))
        self.assertEqual(self.recover()[1][0]["retirement_state"], "enrolled")
        self.assertTrue(self.wt.exists())

    def test_archive_and_cas_failure_roll_back_together(self):
        with FINISH.ledger(self.state) as db:
            db.execute("CREATE TRIGGER fail_recovery BEFORE UPDATE ON retirement "
                       "WHEN NEW.state='enrolled' BEGIN SELECT RAISE(ABORT, 'fixture-failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.recover()
        self.assertEqual(self.receipt(), self.original)
        with FINISH.ledger(self.state) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='unstarted_removal_recoveries'").fetchone():
                self.assertEqual(db.execute("SELECT count(*) FROM unstarted_removal_recoveries").fetchone()[0], 0)

    def test_receipt_flags_do_not_broaden_other_commands(self):
        for args in (("status", "--removal-receipt"), ("status", "--read-only", "--all", "--removal-receipt"),
                     ("remove", "--finalized", "--unstarted-removal"),
                     ("reconcile", "--unstarted-removal", "--external-removal")):
            with self.subTest(args=args), self.assertRaises(FINISH.Retain):
                self.call(*args)
        self.assertEqual(self.receipt(), self.original)


class CancelledUnstartedRemovalTests(unittest.TestCase):
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    tearDown = ReleaseTests.tearDown
    remove = FinalizedRemovalTests.remove
    expire_after_intent = UnstartedRemovalTests.expire_after_intent
    receipt = UnstartedRemovalTests.receipt
    arguments = UnstartedRemovalTests.arguments
    recover = UnstartedRemovalTests.recover
    rewrite_result = UnstartedRemovalTests.rewrite_result
    advance_ref = UnstartedRemovalTests.advance_ref
    append_reflog = UnstartedRemovalTests.append_reflog
    test_missing_dispatched_or_contradictory_child_facts_stay_held = (
        UnstartedRemovalTests.test_missing_dispatched_or_contradictory_child_facts_stay_held)
    test_exact_owner_generation_and_receipt_are_required = (
        UnstartedRemovalTests.test_exact_owner_generation_and_receipt_are_required)
    test_source_holder_and_preimage_changes_refuse = (
        UnstartedRemovalTests.test_source_holder_and_preimage_changes_refuse)
    test_archive_and_cas_failure_roll_back_together = (
        UnstartedRemovalTests.test_archive_and_cas_failure_roll_back_together)

    def setUp(self):
        ReleaseTests.setUp(self)
        no_pr = mock.patch.object(FINISH, "pr", side_effect=AssertionError("cancelled task requested PR proof"))
        no_pr.start()
        self.addCleanup(no_pr.stop)
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.head)
        self.call("cancel", "--reason", "superseded without a PR")
        with self.expire_after_intent():
            result = self.remove()[1][0]
        self.assertEqual((result["checkout"], result["retirement_state"]), ("unknown", "incomplete"))
        self.original = self.receipt()

    def disposition(self):
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            return ({key: row[key] for key in ("identity", "finish_head", "primary_pr", "target", "dependencies")},
                    FINISH.owner_rows(db, row["id"]), FINISH.cancellation_rows(db, row["id"]))

    def test_cancelled_recovery_preserves_disposition_then_separate_closeout_removes(self):
        before = self.disposition()
        self.assertTrue(all(before[0][key] is None for key in ("finish_head", "primary_pr", "target", "dependencies")))
        self.assertEqual(before[1], [{"owner": "owner-1", "completed": 0}])
        self.assertEqual(len(before[2]), 1)
        with mock.patch.object(FINISH, "remove_with_intent", side_effect=AssertionError("removal")), \
                mock.patch.object(FINISH, "git", wraps=FINISH.git) as native_git, \
                mock.patch.object(FINISH, "finalized_admission", wraps=FINISH.finalized_admission) as admission:
            result = self.recover()[1][0]
        self.assertFalse(any("unlock" in call.args or "remove" in call.args
                             for call in native_git.call_args_list))
        self.assertIs(admission.call_args.kwargs["completed"], False)
        self.assertEqual(result["generation"], self.original["generation"] + 1)
        self.assertEqual((result["state"], result["retirement_state"]), ("cancelled", "enrolled"))
        self.assertEqual(result["released_owners"], [])
        self.assertEqual(self.disposition(), before)
        with FINISH.ledger(self.state) as db:
            archive = db.execute("SELECT * FROM unstarted_removal_recoveries").fetchone()
            self.assertEqual((archive["intent"], archive["result"]),
                             (self.original["intent"], self.original["result"]))
        self.assertTrue(self.wt.exists())
        self.assertTrue(self.admin.exists())
        self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.token)
        self.assertEqual(self.remove()[1][0]["checkout"], "removed")
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.admin.exists())
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertEqual(self.disposition(), before)

    def test_cancelled_recovery_requires_explicit_native_attempted_false(self):
        result = json.loads(self.original["result"])
        result.pop("native_attempted")
        self.rewrite_result(result)
        before = self.receipt()
        with self.assertRaisesRegex(FINISH.Retain, "not-proven-unstarted"):
            self.recover()
        self.assertEqual(self.receipt(), before)

    def test_cancelled_head_and_intent_headers_must_match_snapshot(self):
        original = json.loads(self.original["intent"])
        variants = [{**original, "head": "b" * 40}, {**original, "branch": "refs/heads/other"},
                    {**original, "before": {**original["before"],
                        "snapshot": {**original["before"]["snapshot"], "head": "b" * 40}}}]
        for intent in variants:
            with self.subTest(intent_head=intent["head"], branch=intent["branch"]):
                with FINISH.ledger(self.state) as db, db:
                    db.execute("UPDATE retirement SET intent=? WHERE worktree_id=?",
                               (json.dumps(intent), self.original["worktree_id"]))
                before = self.receipt()
                with self.assertRaisesRegex(FINISH.Retain, "cancellation-head-changed"):
                    self.recover()
                self.assertEqual(self.receipt(), before)
        with FINISH.ledger(self.state) as db, db:
            db.execute("UPDATE retirement SET intent=? WHERE worktree_id=?",
                       (self.original["intent"], self.original["worktree_id"]))
            db.execute("UPDATE owner_cancellations SET head=? WHERE worktree_id=?",
                       ("b" * 40, self.original["worktree_id"]))
        with self.assertRaisesRegex(FINISH.Retain, "cancellation-head-changed"):
            self.recover()
        self.assertEqual(self.receipt(), self.original)

    def test_active_owner_and_pin_remain_blocking(self):
        # Model inconsistent older/out-of-band state while preserving the exact
        # incomplete receipt. Native owner and pin commands already reject it.
        for statement, reason in (
                ("INSERT INTO owners VALUES (?, 'owner-2', 0)", "awaiting-owner-completion"),
                ("INSERT INTO pins VALUES (?, 'owner-1', 'still needed')", "recovery-pin-present")):
            with self.subTest(reason=reason):
                with FINISH.ledger(self.state) as db, db:
                    db.execute("UPDATE retirement SET state='enrolled' WHERE worktree_id=?",
                               (self.original["worktree_id"],))
                    db.execute(statement, (self.original["worktree_id"],))
                    db.execute("UPDATE retirement SET state='incomplete', generation=? WHERE worktree_id=?",
                               (self.original["generation"], self.original["worktree_id"]))
                with self.assertRaisesRegex(FINISH.Retain, reason):
                    self.recover()
                self.assertEqual(self.receipt(), self.original)
                with FINISH.ledger(self.state) as db, db:
                    db.execute("UPDATE retirement SET state='enrolled' WHERE worktree_id=?",
                               (self.original["worktree_id"],))
                    db.execute("DELETE FROM owners WHERE owner='owner-2'")
                    db.execute("DELETE FROM pins")
                    db.execute("UPDATE retirement SET state='incomplete', generation=? WHERE worktree_id=?",
                               (self.original["generation"], self.original["worktree_id"]))

    def test_admin_index_and_lock_changes_remain_blocking(self):
        for name, contents in (("index", b"invalid index"), ("locked", b"unrelated lock\n"),
                               ("MERGE_HEAD", (self.head + "\n").encode())):
            path = self.admin / name
            original = path.read_bytes() if path.exists() else None
            with self.subTest(name=name):
                path.write_bytes(contents)
                with self.assertRaises(FINISH.Retain):
                    self.recover()
                self.assertEqual(self.receipt(), self.original)
                if original is None:
                    path.unlink()
                else:
                    path.write_bytes(original)

    def test_missing_ref_or_task_reflog_change_cannot_be_reconciled(self):
        branch = "refs/heads/released-feature"
        git(self.repo, "update-ref", "-d", branch)
        with self.assertRaises(FINISH.Retain):
            self.recover()
        self.assertEqual(self.receipt(), self.original)
        git(self.repo, "update-ref", branch, self.head)
        self.append_reflog(branch)
        with self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
            self.recover()
        self.assertEqual(self.receipt(), self.original)

    def test_ref_progress_during_recovery_refuses_before_archiving(self):
        native = FINISH.finalized_admission

        def progress(*args, **kwargs):
            result = native(*args, **kwargs)
            self.append_reflog("refs/heads/released-feature")
            return result

        with mock.patch.object(FINISH, "finalized_admission", side_effect=progress):
            with self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
                self.recover()
        self.assertEqual(self.receipt(), self.original)

    def test_absent_checkout_stays_blocked(self):
        shutil.rmtree(self.wt)
        with self.assertRaises(FileNotFoundError) as failure:
            self.recover()
        self.assertEqual(failure.exception.errno, errno.ENOENT)
        self.assertEqual(self.receipt(), self.original)

    def test_current_head_and_branch_drift_stay_blocked(self):
        head = self.advance_ref()
        git(self.repo, "update-ref", "refs/heads/released-feature", head)
        with self.assertRaisesRegex(FINISH.Retain, "finalized-head-changed"):
            self.recover()
        self.assertEqual(self.receipt(), self.original)
        git(self.repo, "branch", "other", head)
        git(self.wt, "symbolic-ref", "HEAD", "refs/heads/other")
        with self.assertRaisesRegex(FINISH.Retain, "worktree-identity-changed"):
            self.recover()
        self.assertEqual(self.receipt(), self.original)

    def test_stale_recovery_cannot_change_successor_attempt(self):
        original_args = self.arguments()
        self.recover()
        with self.expire_after_intent():
            self.remove()
        successor = self.receipt()
        self.assertEqual(successor["generation"], self.original["generation"] + 1)
        with self.assertRaisesRegex(FINISH.Retain, "exact-incomplete-intent"):
            self.call("reconcile", *original_args)
        self.assertEqual(self.receipt(), successor)

    def test_another_terminal_owner_cannot_reconcile_original_operators_attempt(self):
        self.recover()
        self.call("resume", "--owner", "owner-2")
        self.call("cancel", "--owner", "owner-2", "--reason", "also superseded")
        with self.expire_after_intent():
            self.remove()
        original, before = self.receipt(), self.disposition()
        self.assertEqual({row["owner"] for row in before[2]}, {"owner-1", "owner-2"})
        with self.assertRaisesRegex(FINISH.Retain, "not-proven-unstarted"):
            self.recover("--owner", "owner-2")
        self.assertEqual(self.receipt(), original)
        self.recover()
        self.assertEqual(self.disposition(), before)


class UnstartedAdminPreimageTests(unittest.TestCase):
    def preimage(self):
        directory = [1, 2, stat.S_IFDIR | 0o700, 501, 20, 4, 256, 100, 200, 0, []]
        leaf = [1, 3, stat.S_IFREG | 0o600, 501, 20, 1, 10, 100, 200, 0, []]
        return {"admin": {"": [directory, None], "HEAD": [leaf, "hash"],
                          "index": [list(leaf), "index-hash"], "logs": [list(directory), None]},
                "snapshot": {"head": "a" * 40}, "contents": {"index": "proof", "source": "proof"}}

    def changed(self):
        before = self.preimage()
        observed = json.loads(json.dumps(before))
        observed["admin"][""][0][7:9] = [101, 202]
        return before, observed

    def test_only_directory_root_times_can_differ(self):
        before, observed = self.changed()
        expected = json.loads(json.dumps(observed))
        change = FINISH.unstarted_preimage_change(before, observed, False)
        self.assertEqual(change, {"proof": observed["admin"][""],
                                 "timestamp_delta_ns": {"mtime_ns": 1, "ctime_ns": 2}})
        self.assertEqual(observed, expected)
        self.assertIsNone(FINISH.unstarted_preimage_change(before, before, None))

    def test_other_root_fields_and_xattrs_stay_exact(self):
        for index in (*range(7), 9, 10):
            before, observed = self.changed()
            observed["admin"][""][0][index] = (["com.apple.provenance"] if index == 10
                                                else observed["admin"][""][0][index] + 1)
            with self.subTest(index=index), self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
                FINISH.unstarted_preimage_change(before, observed, False)

    def test_malformed_root_proofs_refuse(self):
        root = self.preimage()["admin"][""]
        variants = [None, [], root[:1], [root[0], "hash"], [root[0][:-1], None],
                    [root[0] + [0], None], [root[0][:7] + [False] + root[0][8:], None],
                    [root[0][:2] + [stat.S_IFREG | 0o600] + root[0][3:], None],
                    [root[0][:10] + [{}], None], [root[0][:10] + [["unknown"]], None]]
        for side in (0, 1):
            for root in variants:
                pair = self.changed()
                pair[side]["admin"][""] = root
                with self.subTest(side=side, root=root), self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
                    FINISH.unstarted_preimage_change(*pair, False)

    def test_leaf_nested_directory_source_and_index_drift_refuse(self):
        for name, index in (("HEAD", 0), ("HEAD", 1), ("index", 0), ("index", 1), ("logs", 0)):
            before, observed = self.changed()
            if index == 0:
                observed["admin"][name][0][7] += 1
            else:
                observed["admin"][name][1] += "changed"
            with self.subTest(name=name, index=index), self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
                FINISH.unstarted_preimage_change(before, observed, False)
        for mutation in (lambda value: value["admin"].pop("HEAD"),
                         lambda value: value["admin"].update(extra=value["admin"]["HEAD"]),
                         lambda value: value["contents"].update(source="changed"),
                         lambda value: value["contents"].update(index="changed"),
                         lambda value: value["snapshot"].update(head="b" * 40)):
            before, observed = self.changed()
            mutation(observed)
            with self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
                FINISH.unstarted_preimage_change(before, observed, False)

    def test_relaxation_requires_literal_native_attempted_false(self):
        for attempted in (None, True, 0, "false"):
            with self.subTest(attempted=attempted), self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
                FINISH.unstarted_preimage_change(*self.changed(), attempted)


class UnstartedAdminTimestampTests(unittest.TestCase):
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    finish = ReleaseTests.finish
    tearDown = ReleaseTests.tearDown
    remove = FinalizedRemovalTests.remove
    setUp = UnstartedRemovalTests.setUp
    expire_after_intent = UnstartedRemovalTests.expire_after_intent
    receipt = UnstartedRemovalTests.receipt
    arguments = UnstartedRemovalTests.arguments
    recover = UnstartedRemovalTests.recover
    rewrite_result = UnstartedRemovalTests.rewrite_result
    disposition = CancelledUnstartedRemovalTests.disposition

    def touch(self, path):
        before = path.stat()
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000))

    def assert_unarchived(self):
        self.assertEqual(self.receipt(), self.original)
        with FINISH.ledger(self.state) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='unstarted_removal_recoveries'").fetchone():
                self.assertEqual(db.execute("SELECT count(*) FROM unstarted_removal_recoveries").fetchone()[0], 0)

    def test_root_timestamp_recovery_preserves_receipt_and_disposition_without_removing(self):
        before = self.disposition()
        self.touch(self.admin)
        with mock.patch.object(FINISH, "remove_with_intent", side_effect=AssertionError("removal")), \
                mock.patch.object(FINISH, "git", wraps=FINISH.git) as native_git:
            result = self.recover()[1][0]
        self.assertFalse(any("unlock" in call.args or "remove" in call.args
                             for call in native_git.call_args_list))
        self.assertEqual(result["generation"], self.original["generation"] + 1)
        self.assertEqual(result["released_owners"], [])
        self.assertEqual(self.disposition(), before)
        self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.token)
        with FINISH.ledger(self.state) as db:
            archive = db.execute("SELECT * FROM unstarted_removal_recoveries").fetchone()
            self.assertEqual((archive["intent"], archive["result"]),
                             (self.original["intent"], self.original["result"]))
            observation = json.loads(archive["fact"])["admin_root_observation"]
        self.assertEqual(observation["proof"][0][:10], list(FINISH.safety().file_identity(self.admin.stat())))
        self.assertEqual(observation["timestamp_delta_ns"]["mtime_ns"], 1000000)
        self.assertTrue(self.call("status", "--read-only", "--removal-receipt")[1][0]["removal_receipt"]["reconciled"])
        self.assertTrue(self.wt.exists())

    def drift_after_admission(self, path):
        self.touch(self.admin)
        native = FINISH.finalized_admission

        def drift(*args, **kwargs):
            observed = native(*args, **kwargs)
            self.touch(path)
            return observed

        with mock.patch.object(FINISH, "finalized_admission", side_effect=drift):
            with self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
                self.recover()
        self.assert_unarchived()

    def test_root_drift_after_admission_refuses_without_archiving(self):
        self.drift_after_admission(self.admin)

    def test_leaf_drift_after_admission_refuses_without_archiving(self):
        self.drift_after_admission(self.admin / "HEAD")

    def test_root_is_rechecked_after_all_admin_leaves(self):
        self.touch(self.admin)
        native = FINISH.safety().admin_inventory
        calls = 0

        def drift(*args, **kwargs):
            nonlocal calls
            observed = native(*args, **kwargs)
            calls += 1
            if calls == 2:
                self.touch(self.admin)
            return observed

        with mock.patch.object(FINISH.safety(), "admin_inventory", side_effect=drift):
            with self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
                self.recover()
        self.assertEqual(calls, 2)
        self.assert_unarchived()

    def test_legacy_receipt_cannot_relax_root_timestamps(self):
        result = json.loads(self.original["result"])
        result.pop("native_attempted")
        self.rewrite_result(result)
        self.original = self.receipt()
        self.touch(self.admin)
        with self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
            self.recover()
        self.assert_unarchived()

    def test_actual_removal_keeps_exact_admin_root_comparison(self):
        self.recover()
        native = FINISH.finalized_admission
        calls = 0

        def drift(*args, **kwargs):
            nonlocal calls
            observed = native(*args, **kwargs)
            calls += 1
            if calls == 1:
                self.touch(self.admin)
            return observed

        with mock.patch.object(FINISH, "finalized_admission", side_effect=drift), \
                mock.patch.object(FINISH, "git", wraps=FINISH.git) as native_git:
            with self.assertRaisesRegex(FINISH.Retain, "removal-preimage-changed"):
                self.remove()
        self.assertFalse(any("unlock" in call.args for call in native_git.call_args_list))
        self.assertTrue(self.wt.exists())
        self.assertEqual(self.receipt()["state"], "enrolled")


class CancelledUnstartedAdminTimestampTests(unittest.TestCase):
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    tearDown = ReleaseTests.tearDown
    remove = FinalizedRemovalTests.remove
    setUp = CancelledUnstartedRemovalTests.setUp
    expire_after_intent = UnstartedRemovalTests.expire_after_intent
    receipt = UnstartedRemovalTests.receipt
    arguments = UnstartedRemovalTests.arguments
    recover = UnstartedRemovalTests.recover
    disposition = CancelledUnstartedRemovalTests.disposition
    touch = UnstartedAdminTimestampTests.touch
    test_root_timestamp_recovery_preserves_receipt_and_disposition_without_removing = (
        UnstartedAdminTimestampTests.test_root_timestamp_recovery_preserves_receipt_and_disposition_without_removing)


class ReconciliationTests(unittest.TestCase):
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    finish = LifecycleTests.finish
    setUp = ReleaseTests.setUp
    tearDown = ReleaseTests.tearDown
    remove = FinalizedRemovalTests.remove

    def current_removal(self):
        native = FINISH.safety().supervise

        def concurrent_closeout(command, **kwargs):
            if "worktree" in command and "remove" in command:
                git(self.repo, "worktree", "remove", str(self.legacy))
            return native(command, **kwargs)

        with mock.patch.object(FINISH.safety(), "supervise", side_effect=concurrent_closeout):
            self.assertEqual(self.remove()[1][0]["checkout"], "unknown")
        self.refresh_binding()

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

    def test_current_producer_receipt_reconciles_missing_sibling_without_retrying(self):
        self.current_removal()
        original = json.loads(self.original["result"])
        self.assertIs(original["siblings_preserved"], False)
        self.assertNotIn("siblings_unchanged", original)
        self.assert_sibling_alert("missing")
        fact = self.call("status")[1][0]["reconciliation"]
        self.assertEqual(fact["historical_sibling_identities"], "recorded")
        self.assertEqual(fact["historical_after_snapshot"], "recorded")

    def test_current_sibling_flag_cannot_fall_back_to_legacy_failure(self):
        self.legacy_removal()
        original_result = self.original["result"]
        for value in (True, None, 0, "false"):
            with self.subTest(value=value):
                with FINISH.ledger(self.state) as db:
                    row = FINISH.get_row(db, self.wt)
                    result = json.loads(original_result)
                    result["siblings_preserved"] = value
                    with db:
                        db.execute("UPDATE retirement SET result=? WHERE worktree_id=?",
                                   (json.dumps(result, sort_keys=True), row["id"]))
                self.refresh_binding()
                with self.assertRaisesRegex(FINISH.Retain, "original-removal-not-qualified"):
                    self.reconcile()
                self.assert_receipts_unchanged()

    def test_current_receipt_still_requires_successful_child_and_target_preservation(self):
        self.current_removal()
        original_result = self.original["result"]
        changes = [
            ("child", key, value) for key, value in
            (("returncode", 1), ("reaped", False), ("group_absent", False), ("pid", None))
        ]
        changes += [(None, key, False) for key in (
            "path_absent", "admin_absent", "registration_absent", "branch_preserved",
            "branch_reflog_preserved", "dependency_preserved", "discarded_link_targets_preserved")]
        for section, key, value in changes:
            with self.subTest(section=section, key=key):
                with FINISH.ledger(self.state) as db:
                    row = FINISH.get_row(db, self.wt)
                    result = json.loads(original_result)
                    (result[section] if section else result)[key] = value
                    with db:
                        db.execute("UPDATE retirement SET result=? WHERE worktree_id=?",
                                   (json.dumps(result, sort_keys=True), row["id"]))
                self.refresh_binding()
                with self.assertRaisesRegex(FINISH.Retain, "original-removal-not-qualified"):
                    self.reconcile()
                self.assert_receipts_unchanged()

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

    def test_child_failures_keep_reason_and_only_allowlisted_facts(self):
        cases = [("import sys; sys.stderr.write('private-secret'); sys.exit(7)", {}, "exit-status"),
                 ("import time; time.sleep(30)", {}, "deadline"),
                 ("print('private-secret' * 1000)", {"limit": 8}, "output-limit")]
        for code, options, cause in cases:
            with self.subTest(cause=cause):
                def invoke(_):
                    return FINISH.run([sys.executable, "-I", "-c", code],
                                      deadline=time.monotonic() + 0.1 if cause == "deadline" else None,
                                      **options)
                status, output, errors = self.call_cli(action=invoke)
                self.assertEqual((status, output), (1, ""))
                self.assertIn("(" + Path(sys.executable).name + "-failed)", errors)
                self.assertIn('"cause": "' + cause + '"', errors)
                self.assertIn('"reaped": true', errors)
                self.assertIn('"group_absent": true', errors)
                self.assertNotIn("private-secret", errors)
                self.assertNotIn(sys.executable, errors)

    def test_child_diagnostic_rejects_arbitrary_metadata(self):
        error = FINISH.Retain("git-failed")
        error._gwt_child_diagnostic = {"cause": ["private-secret"], "errno": "private-secret",
                                       "returncode": True, "argv": "private-secret"}
        status, output, errors = self.call_cli(error)
        self.assertEqual((status, output), (1, ""))
        self.assertIn('"cause": "supervisor-error"', errors)
        self.assertNotIn("private-secret", errors)
        self.assertNotIn('"returncode"', errors)

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


class ResumeAcknowledgementTests(unittest.TestCase):
    tearDown = ReleaseTests.tearDown
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    finish = ReleaseTests.finish
    row = ReleaseTests.row
    flag = "--acknowledge-device-renumbering"

    def ledger_state(self):
        with FINISH.ledger(self.state) as db:
            return tuple(db.iterdump())

    def setUp(self):
        ReleaseTests.setUp(self)
        self.current = FINISH.revalidate(self.row())
        self.changed = {**self.current, **{
            key: [self.current[key][0] + 7, self.current[key][1]]
            for key in ("path_id", "gitdir_id", "common_id", "owner_id")}}

    def reject(self, command, *args, reason="worktree-identity-changed"):
        before = self.ledger_state()
        with self.assertRaisesRegex(FINISH.Retain, reason):
            self.call(command, *args)
        self.assertEqual(self.ledger_state(), before)
        self.assertTrue(self.wt.exists())

    def test_acknowledgment_preserves_identity_dirty_source_pins_and_other_owners(self):
        self.call("resume", "--owner", "owner-2")
        self.call("pin", "--reason", "recovery")
        (self.wt / "file").write_text("unfinished source\n")
        row = self.row()
        lock = (self.admin / "locked").read_bytes()
        with mock.patch.object(FINISH, "snapshot", return_value=self.changed), \
                mock.patch.object(FINISH, "evaluate_release") as removal:
            result = self.call("resume", self.flag)[1][0]
            self.reject("resume")
            self.reject("pin", "--reason", "new pin")
            self.reject("finish", "--pr", self.url)
        removal.assert_not_called()
        self.assertEqual(result["state"], "active")
        self.assertEqual(result["reason"], "owner-resumed")
        self.assertEqual(result["pins"], [{"owner": "owner-1", "reason": "recovery"}])
        self.assertEqual([item["owner"] for item in result["owners"]], ["owner-1", "owner-2"])
        self.assertEqual(self.row()["identity"], row["identity"])
        self.assertEqual((self.admin / "locked").read_bytes(), lock)
        self.assertEqual((self.wt / "file").read_text(), "unfinished source\n")

    def test_acknowledgment_invalidates_completion_and_every_release(self):
        self.call("resume", "--owner", "owner-2")
        self.observer.observe.return_value["holders"] = [{"fixture": "present"}]
        self.finish("--release")
        completed = self.finish("--owner", "owner-2", "--release")[1][0]
        self.assertEqual(completed["released_owners"], ["owner-1", "owner-2"])
        identity = self.row()["identity"]
        with mock.patch.object(FINISH, "snapshot", return_value=self.changed):
            result = self.call("resume", self.flag)[1][0]
        self.assertEqual(result["released_owners"], [])
        self.assertGreater(result["generation"], completed["generation"])
        self.assertEqual(result["owners"], [{"owner": "owner-1", "completed": 0},
                                             {"owner": "owner-2", "completed": 1}])
        self.assertIsNone(result["pr"])
        self.assertEqual(self.row()["identity"], identity)
        for key in ("finish_head", "primary_pr", "target", "dependencies"):
            self.assertIsNone(self.row()[key])

    def test_real_snapshot_device_change_requires_explicit_acknowledgment(self):
        original = FINISH.identity
        def renumber(path):
            device, inode = original(path)
            return [device + 7, inode]
        with mock.patch.object(FINISH, "identity", side_effect=renumber):
            self.reject("resume")
            self.assertEqual(self.call("resume", self.flag)[1][0]["state"], "active")

    def test_acknowledged_completion_never_releases_or_changes_enrollment(self):
        self.call("resume", "--owner", "owner-2")
        self.call("pin", "--reason", "retain requested checkout")
        row = self.row()
        lock = (self.admin / "locked").read_bytes()
        with mock.patch.object(FINISH, "snapshot", return_value=self.changed), \
                mock.patch.object(FINISH, "release") as release, \
                mock.patch.object(FINISH, "evaluate_release") as removal:
            result = self.call("finish", self.flag, "--pr", self.url)[1][0]
        release.assert_not_called()
        removal.assert_not_called()
        with mock.patch.object(FINISH, "snapshot", return_value=self.changed):
            self.reject("finish", "--pr", self.url)
            self.reject("release")
        self.assertEqual(result["pr"], self.url)
        self.assertEqual(result["owners"], [{"owner": "owner-1", "completed": 1},
                                            {"owner": "owner-2", "completed": 0}])
        self.assertEqual(result["released_owners"], [])
        self.assertEqual(result["checkout"], "retained")
        self.assertEqual(result["pins"], [{"owner": "owner-1", "reason": "retain requested checkout"}])
        self.assertEqual(self.row()["identity"], row["identity"])
        self.assertEqual((self.admin / "locked").read_bytes(), lock)
        self.assertTrue(self.wt.exists())

    def test_acknowledged_completion_rechecks_identity_and_exact_pr(self):
        bad = {**self.changed, "path_id": [self.changed["path_id"][0], 1]}
        with mock.patch.object(FINISH, "snapshot", return_value=bad):
            self.reject("finish", self.flag, "--pr", self.url)
        late = {**self.changed, "branch": "refs/heads/replaced"}
        with mock.patch.object(FINISH, "snapshot", side_effect=[self.changed, late]):
            self.reject("finish", self.flag, "--pr", self.url)
        with mock.patch.object(FINISH, "snapshot", return_value=self.changed):
            self.reject("finish", self.flag, "--pr", self.url, "--owner", "new-owner",
                        reason="requires-recorded-owner")
            with mock.patch.object(FINISH, "pr", return_value=self.proof(self.url, head="1" * 40)):
                self.reject("finish", self.flag, "--pr", self.url,
                            reason="primary-pr-repository-or-head-mismatch")

    def test_new_owner_and_conflicting_runtime_owner_cannot_acknowledge(self):
        with mock.patch.object(FINISH, "snapshot", return_value=self.changed):
            self.reject("resume", self.flag, "--owner", "new-owner", reason="requires-recorded-owner")
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "new-thread"}):
                self.reject("resume", self.flag, reason="requires-recorded-owner")
                self.reject("resume", self.flag, "--owner", "owner-1", reason="conflicts-with-live-thread")
            with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "owner-1", "GWT_OWNER_ID": "stale"}):
                self.assertEqual(self.call("resume", self.flag)[1][0]["state"], "active")

    def test_legacy_and_unenrolled_targets_are_not_adopted(self):
        self.wt = self.legacy
        self.reject("resume", self.flag, reason="legacy-enrollment-report-only")
        self.wt = self.root / "absent"
        before = self.ledger_state()
        with self.assertRaisesRegex(FINISH.Retain, "not-enrolled"):
            self.call("resume", self.flag)
        self.assertEqual(self.ledger_state(), before)
        self.assertFalse(self.wt.exists())

    def test_incomplete_removal_intent_remains_held(self):
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            db.execute("UPDATE retirement SET state='incomplete' WHERE worktree_id=?", (row["id"],))
            db.commit()
        self.reject("resume", self.flag, reason="removal-intent")

    def test_native_lock_must_still_match(self):
        (self.admin / "locked").write_text("different lifecycle owner\n")
        self.reject("resume", self.flag, reason="registration-missing-or-locked")

    def test_busy_lifecycle_lock_refuses_without_mutation(self):
        before = self.ledger_state()
        with FINISH.ledger(self.state), self.assertRaisesRegex(FINISH.Retain, "lifecycle-busy"):
            self.call("resume", self.flag)
        self.assertEqual(self.ledger_state(), before)

    def test_storage_refusal_still_blocks_acknowledgment(self):
        self.storage.side_effect = FINISH.Retain("storage-guard-refused")
        self.reject("resume", self.flag, reason="storage-guard-refused")

    def test_inode_path_repository_and_branch_changes_refuse(self):
        identities = ("path_id", "gitdir_id", "common_id", "owner_id")
        changes = [{key: [self.changed[key][0], self.changed[key][1] + 1]} for key in identities]
        changes += [{key: value + "-changed"} for key, value in self.changed.items()
                    if key not in (*identities, "head")]
        changes.append({"gitdir_id": [self.changed["gitdir_id"][0] + 1,
                                      self.changed["gitdir_id"][1]]})
        for change in changes:
            with self.subTest(change=change), mock.patch.object(
                    FINISH, "snapshot", return_value={**self.changed, **change}):
                self.reject("resume", self.flag)

    def test_distinct_recorded_devices_cannot_collapse(self):
        with FINISH.ledger(self.state) as db:
            row = FINISH.get_row(db, self.wt)
            recorded = json.loads(row["identity"])
            recorded["gitdir_id"][0] += 1
            with db:
                db.execute("UPDATE worktrees SET identity=? WHERE id=?",
                           (json.dumps(recorded), row["id"]))
        with mock.patch.object(FINISH, "snapshot", return_value=self.changed):
            self.reject("resume", self.flag)

    def test_identity_change_between_acknowledgment_and_claim_refuses(self):
        late = {**self.changed, "path_id": [self.changed["path_id"][0] + 1,
                                            self.changed["path_id"][1]]}
        with mock.patch.object(FINISH, "snapshot", side_effect=[self.changed, late]) as probe:
            self.reject("resume", self.flag)
        self.assertEqual(probe.call_count, 2)

    def test_flag_rejects_other_actions_and_automatic_or_combined_resume(self):
        commands = ("enroll", "release", "pin", "unpin",
                    "status", "check", "holder-qualification", "creation-token", "guard",
                    "reconcile", "recover-missing")
        value_options = ("--pr", "--target", "--wait-for", "--reason", "--policy", "--new-token",
                         "--discard-ignored", "--intent-id", "--intent-sha256", "--result-sha256",
                         "--worktree-id", "--expected-head", "--index-sha256", "--recovery-sha256")
        options = (*((option,) for option in (
                       "--if-enrolled", "--all", "--apply", "--release", "--finalized",
                       "--sudo-holder-scan", "--external-removal", "--allow-device-renumbering",
                       "--continue-partial")),
                   *((option, value) for option in value_options for value in ("fixture", "")),
                   ("--generation", "0"), ("--generation", "1"))
        for command, extra in [*((command, ()) for command in commands),
                               *(("resume", option) for option in options)]:
            with self.subTest(command=command, extra=extra), mock.patch.object(FINISH, "ledger") as ledger:
                with self.assertRaisesRegex(FINISH.Retain, "explicit-resume-or-retained-finish"):
                    self.call(command, self.flag, *extra)
                ledger.assert_not_called()
        # Missing-checkout cancel and finalized removal accept the flag only
        # with their own operands; anything else refuses before the ledger.
        for command, extra, reason in (
                ("cancel", (), "bounded-cancellation-reason-required"),
                ("remove", (), "use-explicit-owner-release"),
                ("cancel", ("--reason", "x", "--pr", "fixture"), "explicit-resume-or-retained-finish"),
                ("cancel", ("--reason", "x", "--finalized"), "explicit-resume-or-retained-finish"),
                ("remove", ("--finalized", "--apply"), "explicit-resume-or-retained-finish"),
                ("remove", ("--finalized", "--reason", "x"), "explicit-resume-or-retained-finish")):
            with self.subTest(command=command, extra=extra), mock.patch.object(FINISH, "ledger") as ledger:
                with self.assertRaisesRegex(FINISH.Retain, reason):
                    self.call(command, self.flag, *extra)
                ledger.assert_not_called()

    def test_acknowledged_finish_rejects_release_and_disposal_options(self):
        for extra in (("--release",), ("--apply",), ("--policy", ""),
                      ("--discard-ignored", "ignored"), ("--all",), ("--if-enrolled",)):
            with self.subTest(extra=extra), mock.patch.object(FINISH, "ledger") as ledger:
                with self.assertRaisesRegex(FINISH.Retain, "explicit-resume-or-retained-finish"):
                    self.call("finish", self.flag, "--pr", self.url, *extra)
                ledger.assert_not_called()

    def test_failed_claim_rolls_back_owner_completion_and_release_invalidation(self):
        self.observer.observe.return_value["holders"] = [{"fixture": "present"}]
        self.finish("--release")
        with FINISH.ledger(self.state) as db:
            db.executescript("""
                CREATE TRIGGER reject_resumed_worktree BEFORE UPDATE ON worktrees
                WHEN NEW.reason = 'owner-resumed'
                BEGIN SELECT RAISE(ABORT, 'fixture claim failure'); END;
            """)
        before = self.ledger_state()
        lock = (self.admin / "locked").read_bytes()
        with mock.patch.object(FINISH, "snapshot", return_value=self.changed), \
                self.assertRaisesRegex(sqlite3.IntegrityError, "fixture claim failure"):
            self.call("resume", self.flag)
        self.assertEqual(self.ledger_state(), before)
        self.assertEqual((self.admin / "locked").read_bytes(), lock)
        self.assertTrue(self.wt.exists())

    def test_identity_error_explains_the_explicit_resume_route_without_paths(self):
        errors = io.StringIO()
        with mock.patch.object(FINISH, "snapshot", return_value=self.changed), contextlib.redirect_stderr(errors):
            code = FINISH.cli(["resume", "--worktree", str(self.wt), "--state-dir", str(self.state)])
        self.assertEqual(code, 1)
        self.assertIn("inspect recorded/current identities first", errors.getvalue())
        self.assertIn("gwt resume " + self.flag, errors.getvalue())
        self.assertNotIn(str(self.wt), errors.getvalue())


@unittest.skipUnless(sys.platform == "darwin" and os.uname().machine == "arm64", "Darwin arm64 native reader")
class NativePendingConsumerTests(unittest.TestCase):
    setUp = LifecycleTests.setUp
    tearDown = LifecycleTests.tearDown
    call = LifecycleTests.call
    proof = LifecycleTests.proof
    finish = LifecycleTests.finish

    def test_native_new_finish_pending_holders_then_consumer_removes(self):
        (self.home / ".codex").mkdir(mode=0o700)
        observed = self.base / "storage-observation.json"
        observed.write_text('{"configured": false}')
        with mock.patch.dict(os.environ, {
                "DOTFILES_GWT_FINISH_STATE": str(self.state),
                "DOTFILES_WORKTREES_ROOT": str(self.root), "TMUX": "",
                "WORKTREE_STORAGE_GUARD_TESTING": "1",
                "WORKTREE_STORAGE_CONFIG": str(self.base / "no-storage-config"),
                "WORKTREE_STORAGE_GUARD_OBSERVED": str(observed)}):
            created = subprocess.run([
                "zsh", "-f", "-c", 'source "$1"; _gwt_tmux_sync_context() { return 0; }; '
                'gwt new native-feature "$2" --full --finish-managed',
                "fixture", str(ROOT / "functions/gwt/gwt.zsh"), self.head],
                cwd=self.repo, capture_output=True, text=True, timeout=30)
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        sibling = self.wt
        self.wt = Path(next(json.loads(line)[0]["worktree"] for line in created.stdout.splitlines()
                            if line.startswith('[{')))
        admin = Path(git(self.wt, "rev-parse", "--absolute-git-dir"))
        self.assertEqual(self.wt.stat().st_mode & 0o777, 0o700)
        self.assertEqual(admin.stat().st_mode & 0o777, 0o700)
        with FINISH.ledger(self.state) as db:
            identity = json.loads(FINISH.get_row(db, self.wt)["identity"])
        policy = self.base / "policy.json"
        policy.write_text(json.dumps({
            "schema": FINISH.APPLY_POLICY_SCHEMA, "host": "fixture-host", "mode": "explicit-release",
            "repositories": [{key: identity[key] for key in ("owner", "root", "common_id")}],
            "qualification": {}}))
        policy.chmod(0o600)
        qualification = {"git": {"path": str(Path(shutil.which("git")).resolve())}, "known_processes": []}
        population = {os.getpid()}
        module = FINISH.safety()

        def consume():
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(FINISH.main([
                    "check", "--all", "--apply", "--policy", str(policy),
                    "--state-dir", str(self.state)]), 0)
            return next(row for row in json.loads(output.getvalue()) if row["worktree"] == str(self.wt))

        # This is a native selected-population canary, not whole-host qualification.
        # Only external policy/GitHub/storage facts and census scope are fixtures;
        # capability, reference reads, admission and native removal stay real.
        with mock.patch.object(FINISH, "host_key", return_value="fixture-host"), \
                mock.patch.object(FINISH, "qualified_policy", return_value=qualification), \
                mock.patch.object(module.Darwin, "population", side_effect=lambda _: set(population), autospec=True):
            self.proofs[self.url].update(merged=False, state="open", merge=None)
            pending = self.finish("--policy", str(policy))[1][0]
            self.assertEqual(pending["reason"], "pr-not-merged", pending)
            self.assertEqual(pending["released_owners"], ["owner-1"])
            self.assertEqual(pending["automatic_handoff"]["baseline"], "ready")
            self.proofs[self.url].update(merged=True, state="closed", merge=self.head)
            script = ('import mmap,os,sys; mode,path=sys.argv[1:]; '
                      'fd=os.open(path,os.O_RDONLY) if mode!="cwd" else None; '
                      'view=mmap.mmap(fd,0,access=mmap.ACCESS_READ) if mode=="mapping" else None; '
                      'os.close(fd) if mode=="mapping" else None; '
                      'os.chdir(path) if mode=="cwd" else None; '
                      'print("ready",flush=True);sys.stdin.buffer.read(1)')
            for mode, path in (("cwd", self.wt), ("fd", admin / "HEAD"), ("mapping", self.wt / "file")):
                with self.subTest(holder=mode):
                    child = subprocess.Popen([sys.executable, "-c", script, mode, str(path)],
                                             cwd=self.repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE)
                    try:
                        self.assertEqual(child.stdout.readline(), b"ready\n")
                        population.add(child.pid)
                        result = consume()
                        self.assertEqual(result["reason"], "pending-departure", result)
                        self.assertTrue(self.wt.exists())
                        self.assertTrue((admin / "locked").exists())
                    finally:
                        child.communicate(b"x", timeout=5)
                        population.discard(child.pid)
                    self.assertEqual(child.returncode, 0)
            # A surfaced native error cannot be converted into empty coverage.
            with mock.patch.object(module.Darwin, "references", side_effect=FINISH.Retain("holder-visibility-unknown")):
                self.assertEqual(consume()["reason"], "holder-visibility-unknown")
            removed = consume()
            self.assertEqual(removed["checkout"], "removed", removed)
        self.assertFalse(self.wt.exists())
        self.assertFalse(admin.exists())
        self.assertNotIn(str(self.wt), git(self.repo, "worktree", "list", "--porcelain"))
        self.assertEqual(git(self.repo, "rev-parse", "native-feature"), self.head)
        self.assertTrue(sibling.exists())
        self.assertEqual(git(sibling, "rev-parse", "HEAD"), self.head)


class FailedCreationTests(unittest.TestCase):
    proof = LifecycleTests.proof
    call = LifecycleTests.call
    tearDown = ReleaseTests.tearDown

    def setUp(self):
        ReleaseTests.setUp(self)
        self.holders = mock.patch.object(FINISH, "manual_holders")
        self.holders.start()
        self.addCleanup(self.holders.stop)
        self.assertEqual(self.call("remove", "--finalized")[1][0]["checkout"], "removed")
        with FINISH.ledger(self.state) as db:
            self.old = dict(FINISH.get_row(db, self.wt))
            self.history = FINISH.failed_creation_history(db, self.old)
        self.result_hash = hashlib.sha256(self.history["retirement"][0]["result"].encode()).hexdigest()
        self.new_token = str(uuid.uuid4())
        old_mask = os.umask(0o077)
        try:
            git(self.repo, "worktree", "add", "--lock", "--reason", "gwt-finish.v2:" + self.new_token,
                str(self.wt), "released-feature")
        finally:
            os.umask(old_mask)
        self.admin = Path(git(self.wt, "rev-parse", "--absolute-git-dir"))

    def recover(self, *args):
        return self.call("recover-failed-creation", "--worktree-id", self.old["id"],
                         "--generation", str(self.history["retirement"][0]["generation"]),
                         "--result-sha256", self.result_hash, "--creation-token", self.new_token,
                         "--expected-head", self.head, "--acknowledge-unrecorded-creation", *args)

    def assert_history_unchanged(self):
        with FINISH.ledger(self.state) as db:
            self.assertEqual(FINISH.failed_creation_history(db, FINISH.get_row(db, self.wt)), self.history)
            self.assertEqual(hashlib.sha256(FINISH.retirement(db, self.old)["result"].encode()).hexdigest(),
                             self.result_hash)

    @contextlib.contextmanager
    def recorded_identity(self, change):
        get_row = FINISH.get_row
        def altered(db, path):
            row = get_row(db, path)
            if row["id"] != self.old["id"]:
                return row
            value = dict(row)
            identity = json.loads(value["identity"])
            change(identity)
            value["identity"] = json.dumps(identity, sort_keys=True)
            return value
        with mock.patch.object(FINISH, "get_row", side_effect=altered):
            yield

    def test_explicit_device_renumbering_preserves_original_history(self):
        def renumber(identity):
            for key in ("path_id", "gitdir_id", "common_id", "owner_id"):
                identity[key][0] += 1000
        with self.recorded_identity(renumber):
            with self.assertRaisesRegex(FINISH.Retain, "incarnation-mismatch"):
                self.recover()
            plan = self.recover("--acknowledge-device-renumbering")[1][0]
            self.assertEqual(len(plan["device_renumbering"]), 1)
            result = self.recover("--acknowledge-device-renumbering", "--apply",
                                  "--recovery-sha256", plan["recovery_sha256"])
            self.assertEqual(result[1][0]["checkout"], "removed")
        self.assert_history_unchanged()

    def test_device_acknowledgment_rejects_inode_or_mapping_changes(self):
        def inode_drift(identity):
            identity["common_id"][1] += 1
        def inconsistent(identity):
            identity["owner_id"][0] += 1000
        def unchanged_checkout(identity):
            for key in ("path_id", "gitdir_id", "common_id", "owner_id"):
                identity[key][0] += 1000
            identity["path_id"][1] = self.wt.stat().st_ino
        for change in (inode_drift, inconsistent, unchanged_checkout):
            with self.subTest(change=change.__name__), self.recorded_identity(change):
                with self.assertRaises(FINISH.Retain):
                    self.recover("--acknowledge-device-renumbering")
        snapshot = FINISH.snapshot
        def separate_device(*args, **kwargs):
            result = snapshot(*args, **kwargs)
            result["path_id"][0] += 1000
            return result
        def unmapped(identity):
            identity["path_id"][0] += 1000
        with self.recorded_identity(unmapped), mock.patch.object(FINISH, "snapshot", side_effect=separate_device):
            with self.assertRaisesRegex(FINISH.Retain, "device-mapping-changed"):
                self.recover("--acknowledge-device-renumbering")
        self.assert_history_unchanged()

    def test_preflight_refuses_historical_path_without_creating_or_changing_it(self):
        git(self.repo, "worktree", "unlock", str(self.wt))
        git(self.repo, "worktree", "remove", str(self.wt))
        with self.assertRaisesRegex(FINISH.Retain, "already-enrolled-or-path-reused"):
            self.call("creation-check")
        self.assertFalse(self.wt.exists())
        self.assert_history_unchanged()

    def test_plan_and_native_removal_preserve_prior_history(self):
        before = (self.state / "lifecycle.sqlite").read_bytes()
        plan = self.recover()[1][0]
        self.assertEqual((self.state / "lifecycle.sqlite").read_bytes(), before)
        self.assertTrue(self.wt.exists())
        result = self.recover("--apply", "--recovery-sha256", plan["recovery_sha256"])
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1][0]["checkout"], "removed")
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.admin.exists())
        self.assertNotIn(str(self.wt), git(self.repo, "worktree", "list", "--porcelain"))
        self.assertEqual(git(self.repo, "rev-parse", "released-feature"), self.head)
        self.assertTrue(self.legacy.exists())
        self.assert_history_unchanged()
        status = self.call("status", "--read-only")[1][0]
        self.assertEqual(status["failed_creation_recoveries"][0]["checkout"], "removed")
        with FINISH.ledger(self.state) as db:
            record = FINISH.failed_creation_record(db, self.old, self.new_token)
            self.assertEqual(json.loads(record["result"])["checkout"], "removed")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                db.execute("DELETE FROM failed_creations")
        with self.assertRaisesRegex(FINISH.Retain, "already-recorded-no-retry"):
            self.recover("--apply", "--recovery-sha256", plan["recovery_sha256"])

    def test_final_admission_refusal_preserves_lock_without_a_failed_creation_receipt(self):
        digest = self.recover()[1][0]["recovery_sha256"]
        native = FINISH.finalized_admission
        calls = []
        def admit(*args, **kwargs):
            calls.append(None)
            if len(calls) == 2:
                raise FINISH.Retain("pending-departure")
            return native(*args, **kwargs)
        with mock.patch.object(FINISH, "finalized_admission", side_effect=admit):
            with self.assertRaisesRegex(FINISH.Retain, "pending-departure"):
                self.recover("--apply", "--recovery-sha256", digest)
        self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.new_token)
        self.assert_history_unchanged()
        with FINISH.read_only_ledger(self.state) as db:
            self.assertIsNone(FINISH.failed_creation_record(db, self.old, self.new_token))

    def test_incomplete_attempt_retains_lock_and_prior_history_without_retry(self):
        digest = self.recover()[1][0]["recovery_sha256"]
        native = FINISH.git
        def fail_unlock(path, *args, **kwargs):
            if args[:2] == ("worktree", "unlock"):
                raise FINISH.Retain("native-unlock-failed")
            return native(path, *args, **kwargs)
        with mock.patch.object(FINISH, "git", side_effect=fail_unlock):
            result = self.recover("--apply", "--recovery-sha256", digest)
        self.assertEqual(result[0], 1)
        self.assertEqual(result[1][0]["checkout"], "unknown")
        self.assertEqual((self.admin / "locked").read_text().strip(), "gwt-finish.v2:" + self.new_token)
        self.assert_history_unchanged()
        status = self.call("status", "--read-only")[1][0]
        self.assertEqual(status["failed_creation_recoveries"][0]["checkout"], "unknown")
        with self.assertRaisesRegex(FINISH.Retain, "already-recorded-no-retry"):
            self.recover("--apply", "--recovery-sha256", digest)
        with self.assertRaisesRegex(FINISH.Retain, "already-recorded-no-retry"):
            self.recover("--creation-token", str(uuid.uuid4()), "--apply", "--recovery-sha256", digest)

    def test_removal_persistence_callbacks_must_be_paired_before_any_access(self):
        for callbacks in ({"record_intent": lambda _: None}, {"record_outcome": lambda _: None}):
            with self.assertRaisesRegex(FINISH.Retain, "callbacks-must-be-paired"):
                FINISH.remove_with_intent(None, None, None, None, None, None, **callbacks)

    def test_wrong_owner_token_head_and_missing_acknowledgment_fail_closed(self):
        for args, reason in [(("--owner", "other"), "sole-recorded-owner"),
                             (("--creation-token", str(uuid.uuid4())), "registration-missing-or-locked"),
                             (("--expected-head", "1" * 40), "incarnation-mismatch")]:
            with self.subTest(args=args), self.assertRaisesRegex(FINISH.Retain, reason):
                self.recover(*args)
        with self.assertRaisesRegex(FINISH.Retain, "exact-audited"):
            self.call("recover-failed-creation")
        self.assert_history_unchanged()

    def test_old_receipt_pin_or_second_owner_cannot_be_recovered(self):
        with self.assertRaisesRegex(FINISH.Retain, "prior-removed-receipt"):
            self.recover("--result-sha256", "0" * 64)
        with FINISH.ledger(self.state) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "removal-intent-holds-worktree"):
                db.execute("INSERT INTO pins VALUES (?, ?, ?)", (self.old["id"], "owner-1", "unfinished"))
        for change in ({"pins": [{"owner": "owner-1"}]},
                       {"owners": [{"owner": "owner-1"}, {"owner": "owner-2"}]}):
            with mock.patch.object(FINISH, "failed_creation_history", return_value={**self.history, **change}):
                with self.assertRaisesRegex(FINISH.Retain, "sole-recorded-owner"):
                    self.recover()
        self.assert_history_unchanged()

    def test_dirty_or_held_checkout_and_plan_drift_fail_closed(self):
        digest = self.recover()[1][0]["recovery_sha256"]
        (self.wt / "file").write_text("changed\n")
        with self.assertRaisesRegex(FINISH.Retain, "dirty-or-untracked"):
            self.recover("--apply", "--recovery-sha256", digest)
        git(self.wt, "checkout", "--", "file")
        with mock.patch.object(FINISH, "manual_holders", side_effect=FINISH.Retain("pending-departure")):
            with self.assertRaisesRegex(FINISH.Retain, "pending-departure"):
                self.recover()
        with self.assertRaisesRegex(FINISH.Retain, "preimage-changed"):
            self.recover("--apply", "--recovery-sha256", "0" * 64)
        self.assert_history_unchanged()


if __name__ == "__main__":
    unittest.main()
