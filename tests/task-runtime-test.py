#!/usr/bin/env python3

import fcntl
import importlib.machinery
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMAND = ROOT / "bin" / "task-runtime"


def load_runtime_module():
    loader = importlib.machinery.SourceFileLoader("task_runtime", str(COMMAND))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class TaskRuntimeTest(unittest.TestCase):
    def run_command(self, *args: object, expected: int = 0):
        result = subprocess.run(
            [str(COMMAND), *(str(arg) for arg in args)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
        return json.loads(result.stdout)

    def test_admission_reports_capacity_without_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            payload = self.run_command(
                "admit", "--path", root, "--reserve-gib", "0", "--planned-bytes", "0"
            )
            self.assertEqual(payload["status"], "admitted")
            self.assertEqual(list(root.iterdir()), [])

            payload = self.run_command(
                "admit",
                "--path",
                root,
                "--reserve-gib",
                "999999999",
                expected=75,
            )
            self.assertEqual(payload["status"], "persistence-degraded")
            self.assertEqual(list(root.iterdir()), [])

    def test_non_finite_reserve_and_lock_timeout_are_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary).resolve()
            for value in ("nan", "inf"):
                admission = subprocess.run(
                    [
                        str(COMMAND),
                        "admit",
                        "--path",
                        str(base),
                        "--reserve-gib",
                        value,
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(admission.returncode, 2)
                self.assertIn("must be finite and non-negative", admission.stderr)

                receipt = base / f"receipt-{value}"
                phase = subprocess.run(
                    [
                        str(COMMAND),
                        "phase",
                        "--root",
                        str(receipt),
                        "--task-id",
                        "task-1",
                        "--phase",
                        "audit",
                        "--status",
                        "complete",
                        "--lock-timeout-seconds",
                        value,
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(phase.returncode, 2)
                self.assertIn("must be finite and non-negative", phase.stderr)
                self.assertFalse(receipt.exists())

    def test_phase_receipt_is_single_bounded_state_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt = pathlib.Path(temporary).resolve() / "artifact"
            first = self.run_command(
                "phase",
                "--root",
                receipt,
                "--task-id",
                "task-1",
                "--owner-id",
                "owner-1",
                "--phase",
                "audit",
                "--status",
                "complete",
                "--source-id",
                "sha256:one",
                "--input-id",
                "sha256:input",
                "--acceptance",
                "tests pass",
                "--command-exit",
                "0",
                "--last-artifact",
                "proof.json",
                "--artifact-kind",
                "proof",
                "--artifact-pin",
                "--retention-intent",
                "retained",
                "--artifact-closeout",
                "retained",
            )
            self.assertEqual(first["status"], "complete")
            self.run_command(
                "phase",
                "--root",
                receipt,
                "--task-id",
                "task-1",
                "--phase",
                "review",
                "--status",
                "blocked",
                "--command-exit",
                "17",
            )
            state = json.loads((receipt / "task-state.json").read_text())
            self.assertEqual(state["latest_phase"], "review")
            self.assertEqual(state["phases"]["review"]["command_exit"], 17)
            self.assertEqual(state["completed_phases"], ["audit"])
            self.assertEqual(state["owner"], "owner-1")
            self.assertEqual(
                state["identity"],
                {"input": "sha256:input", "source": "sha256:one"},
            )
            self.assertEqual(state["acceptance"], ["tests pass"])
            self.assertEqual(
                state["last_artifact"],
                {
                    "closeout": "retained",
                    "kind": "proof",
                    "pinned": True,
                    "ref": "proof.json",
                    "retention": "retained",
                },
            )
            self.assertEqual(
                sorted(path.name for path in receipt.iterdir()),
                [".task-state.lock", "task-state.json"],
            )
            self.assertEqual(receipt.stat().st_mode & 0o777, 0o700)

    def test_unchanged_blocker_denies_retry_and_preserves_completed_phases(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt = pathlib.Path(temporary).resolve() / "artifact"
            self.run_command(
                "phase",
                "--root",
                receipt,
                "--task-id",
                "task-1",
                "--phase",
                "audit",
                "--status",
                "complete",
            )
            self.run_command(
                "phase",
                "--root",
                receipt,
                "--task-id",
                "task-1",
                "--phase",
                "download",
                "--status",
                "blocked",
                "--failure-fingerprint",
                "sha256:failure",
                "--blocker",
                "network unavailable",
                "--no-retry-allowed",
                "--retry-permitting-event",
                "network-restored",
            )
            before = (receipt / "task-state.json").read_bytes()
            blocked = self.run_command(
                "retry",
                "--root",
                receipt,
                "--task-id",
                "task-1",
                expected=75,
            )
            self.assertEqual(blocked["status"], "retry-blocked")
            self.assertEqual(blocked["completed_phases"], ["audit"])
            self.assertEqual(blocked["failure_fingerprint"], "sha256:failure")
            self.assertEqual((receipt / "task-state.json").read_bytes(), before)

            changed = self.run_command(
                "retry",
                "--root",
                receipt,
                "--task-id",
                "task-1",
                "--observed-event",
                "network-restored",
            )
            self.assertEqual(changed["status"], "retry-permitted")
            self.assertEqual((receipt / "task-state.json").read_bytes(), before)

    def test_phase_and_artifact_budgets_fail_with_stdout_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary).resolve()
            receipt = base / "artifact"
            self.run_command(
                "phase",
                "--root",
                receipt,
                "--task-id",
                "task-1",
                "--phase",
                "audit",
                "--status",
                "complete",
                "--max-phases",
                "1",
            )
            before = (receipt / "task-state.json").read_bytes()
            payload = self.run_command(
                "phase",
                "--root",
                receipt,
                "--task-id",
                "task-1",
                "--phase",
                "review",
                "--status",
                "complete",
                "--max-phases",
                "1",
                expected=75,
            )
            self.assertEqual(payload["status"], "phase-budget-exceeded")
            self.assertEqual((receipt / "task-state.json").read_bytes(), before)

            payload = self.run_command(
                "phase",
                "--root",
                base / "tiny",
                "--task-id",
                "task-2",
                "--phase",
                "audit",
                "--status",
                "complete",
                "--max-bytes",
                "1",
                expected=75,
            )
            self.assertEqual(payload["status"], "artifact-budget-exceeded")
            self.assertFalse((base / "tiny" / "task-state.json").exists())

    def test_symlinked_artifact_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary).resolve()
            real = base / "real"
            real.mkdir()
            link = base / "link"
            link.symlink_to(real, target_is_directory=True)
            payload = self.run_command(
                "phase",
                "--root",
                link,
                "--task-id",
                "task-1",
                "--phase",
                "audit",
                "--status",
                "complete",
                expected=2,
            )
            self.assertEqual(payload["status"], "invalid")
            self.assertEqual(list(real.iterdir()), [])

    def test_symlinked_parent_and_foreign_receipt_are_not_mutated(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary).resolve()
            real = base / "real"
            real.mkdir()
            parent_link = base / "parent-link"
            parent_link.symlink_to(real, target_is_directory=True)
            payload = self.run_command(
                "phase",
                "--root",
                parent_link / "task",
                "--task-id",
                "task-1",
                "--phase",
                "audit",
                "--status",
                "complete",
                expected=2,
            )
            self.assertEqual(payload["status"], "invalid")
            self.assertEqual(list(real.iterdir()), [])

            foreign = base / "foreign"
            foreign.mkdir(mode=0o755)
            state = foreign / "task-state.json"
            state.write_text('{"schema":1,"task_id":"other","phases":{}}\n')
            before_mode = foreign.stat().st_mode & 0o777
            before_state = state.read_bytes()
            payload = self.run_command(
                "phase",
                "--root",
                foreign,
                "--task-id",
                "task-1",
                "--phase",
                "audit",
                "--status",
                "complete",
                expected=75,
            )
            self.assertEqual(payload["status"], "receipt-owner-mismatch")
            self.assertEqual(foreign.stat().st_mode & 0o777, before_mode)
            self.assertEqual(state.read_bytes(), before_state)
            self.assertFalse((foreign / ".task-state.lock").exists())

    def test_corrupt_receipt_and_lock_wait_are_bounded_blockers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve() / "artifact"
            root.mkdir()
            (root / "task-state.json").write_text("[]\n")
            payload = self.run_command(
                "phase",
                "--root",
                root,
                "--task-id",
                "task-1",
                "--phase",
                "audit",
                "--status",
                "complete",
                expected=75,
            )
            self.assertEqual(payload["status"], "receipt-corrupt")
            self.assertFalse((root / ".task-state.lock").exists())

            (root / "task-state.json").write_text(
                '{"schema":1,"task_id":"task-1","phases":{}}\n'
            )
            (root / ".task-state.lock").symlink_to(root / "task-state.json")
            payload = self.run_command(
                "phase",
                "--root",
                root,
                "--task-id",
                "task-1",
                "--phase",
                "audit",
                "--status",
                "complete",
                "--lock-timeout-seconds",
                "0",
                expected=75,
            )
            self.assertEqual(payload["status"], "receipt-corrupt")

            (root / ".task-state.lock").unlink()
            (root / "task-state.json").write_text(
                '{"schema":1,"task_id":"task-1","phases":{}}\n'
            )
            with (root / ".task-state.lock").open("w+") as held_lock:
                fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX)
                payload = self.run_command(
                    "phase",
                    "--root",
                    root,
                    "--task-id",
                    "task-1",
                    "--phase",
                    "audit",
                    "--status",
                    "complete",
                    "--lock-timeout-seconds",
                    "0",
                    expected=75,
                )
            self.assertEqual(payload["status"], "lock-timeout")

            (root / "task-state.json").write_text(" " * 64)
            payload = self.run_command(
                "phase",
                "--root",
                root,
                "--task-id",
                "task-1",
                "--phase",
                "audit",
                "--status",
                "complete",
                "--max-state-bytes",
                "8",
                expected=75,
            )
            self.assertEqual(payload["status"], "receipt-corrupt")

    def test_fake_scandir_stops_before_unbounded_directory_enqueue(self):
        runtime = load_runtime_module()

        class FakeEntry:
            def __init__(self, index):
                self.path = f"/fixture/{index}"

            def is_symlink(self):
                return False

            def is_dir(self, *, follow_symlinks):
                return True

        class FakeScan:
            def __enter__(self):
                return iter(FakeEntry(index) for index in range(1000))

            def __exit__(self, *_args):
                return False

        with mock.patch.object(runtime.os, "scandir", return_value=FakeScan()):
            with self.assertRaises(runtime.ScanBudgetExceeded) as raised:
                runtime.bounded_usage(
                    pathlib.Path("/fixture"),
                    max_files=100,
                    max_bytes=100,
                    max_entries=3,
                    max_directories=3,
                )
        self.assertIn(raised.exception.budget, {"entry", "directory"})
        self.assertLessEqual(raised.exception.directories, 4)

    def test_concurrent_phase_updates_share_one_locked_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt = pathlib.Path(temporary).resolve() / "artifact"
            processes = [
                subprocess.Popen(
                    [
                        str(COMMAND),
                        "phase",
                        "--root",
                        str(receipt),
                        "--task-id",
                        "task-1",
                        "--phase",
                        f"phase-{index}",
                        "--status",
                        "complete",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for index in range(8)
            ]
            results = []
            for process in processes:
                stdout, stderr = process.communicate()
                results.append((process.returncode, stderr + stdout))
            for returncode, output in results:
                self.assertEqual(returncode, 0, output)
            state = json.loads((receipt / "task-state.json").read_text())
            self.assertEqual(len(state["phases"]), 8)


if __name__ == "__main__":
    unittest.main()
