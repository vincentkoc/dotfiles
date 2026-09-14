#!/usr/bin/env python3
"""Disposable reader/supervisor fixtures; no live process census or activation."""

import ctypes
import contextlib
import errno
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock


MODULE = Path(__file__).resolve().parents[1] / "bin/agent-worktree-ops/gwt_finish_safety.py"
SPEC = importlib.util.spec_from_file_location("finish_native", MODULE)
NATIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NATIVE)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.file = self.root / "source"
        self.file.write_bytes(b"source")
        NATIVE.DEADLINE = float("inf")

    def tearDown(self):
        NATIVE.DEADLINE = float("inf")
        self.temp.cleanup()

    def test_oversized_leaf_is_rejected_before_open(self):
        with mock.patch.object(NATIVE.os, "open") as opened:
            with self.assertRaisesRegex(NATIVE.Retain, "size"):
                NATIVE.read_file(self.file, time.monotonic() + 1, 5)
            opened.assert_not_called()

    def test_growth_cannot_exceed_bound_by_more_than_one_byte(self):
        with mock.patch.object(NATIVE.os, "read", return_value=b"1234567") as read:
            with self.assertRaisesRegex(NATIVE.Retain, "size-limit"):
                NATIVE.read_file(self.file, time.monotonic() + 1, 6)
            self.assertEqual(read.call_args.args[1], 7)

    def test_symlink_is_never_opened(self):
        link = self.root / "link"
        link.symlink_to(self.file)
        with self.assertRaisesRegex(NATIVE.Retain, "file-type"):
            NATIVE.read_file(link, time.monotonic() + 1)

    def test_operation_deadline_is_earlier_than_local_read_deadline(self):
        NATIVE.DEADLINE = time.monotonic() - 1
        with mock.patch.object(NATIVE.os, "open") as opened:
            with self.assertRaisesRegex(NATIVE.Retain, "deadline"):
                NATIVE.read_file(self.file, time.monotonic() + 10)
            opened.assert_not_called()

    def test_split_and_unknown_index_extensions_refuse_without_git(self):
        import struct
        base = b"DIRC" + struct.pack(">II", 2, 0)
        for extension in (b"link", b"sdir", b"FSMN", b"nope"):
            body = base + extension + struct.pack(">I", 0)
            with self.subTest(extension=extension), self.assertRaisesRegex(NATIVE.Retain, "extension"):
                NATIVE.index_entries(body + hashlib.sha1(body).digest())

    def test_invalid_index_checksum_refuses(self):
        with self.assertRaisesRegex(NATIVE.Retain, "raw-index-invalid"):
            NATIVE.index_entries(b"DIRC" + b"\0" * 40)

    def test_xattr_names_only_allow_bound_provenance(self):
        def listing(data, error=0):
            def call(path, buffer, size, *options):
                ctypes.set_errno(error)
                ctypes.memmove(buffer, data, len(data))
                return len(data) if not error else -1
            return call
        for data in (b"com.apple.ResourceFork\0", b"com.apple.quarantine\0",
                     b"com.apple.decmpfs\0", b"user.recovery\0",
                     b"com.apple.provenance\0com.apple.provenance\0", b"unterminated"):
            with self.subTest(names=data), mock.patch.object(NATIVE, "xattr_reader", return_value=(listing(data), ())):
                with self.assertRaises(NATIVE.Retain):
                    NATIVE.disposable_metadata(self.file, time.monotonic() + 1)
        with mock.patch.object(NATIVE, "xattr_reader", return_value=(listing(b"com.apple.provenance\0"), ())):
            self.assertEqual(NATIVE.disposable_metadata(self.file, time.monotonic() + 1),
                             ["com.apple.provenance"])
        with mock.patch.object(NATIVE, "xattr_reader", return_value=(listing(b"", errno.EPERM), ())):
            with self.assertRaisesRegex(NATIVE.Retain, "unavailable"):
                NATIVE.disposable_metadata(self.file, time.monotonic() + 1)

    @unittest.skipUnless(sys.platform == "darwin", "Darwin listxattr contract")
    def test_native_xattr_reader_requests_nofollow_and_compression_names(self):
        self.assertEqual(NATIVE.xattr_reader()[1], (0x21,))

    def test_working_and_admin_enumeration_stop_at_first_over_cap_entry(self):
        snap = {"path": str(self.root), "gitdir": str(self.root),
                "path_id": [self.root.stat().st_dev, self.root.stat().st_ino]}
        for admin, cap in ((False, 4), (True, 1024)):
            with self.subTest(admin=admin):
                consumed = []
                def population():
                    for n in range(cap + 1):
                        consumed.append(n)
                        self.assertLessEqual(len(consumed), cap)
                        yield types.SimpleNamespace(name=str(n), path=str(self.root / str(n)))
                with mock.patch.object(NATIVE.os, "scandir", return_value=contextlib.nullcontext(population())), \
                        mock.patch.object(NATIVE, "read_file", return_value=(b"", (1,))), \
                        mock.patch.object(NATIVE, "index_entries", return_value={}), \
                        mock.patch.object(NATIVE, "disposable_metadata", return_value=[]), \
                        mock.patch.object(NATIVE, "ENTRY_LIMIT", 4):
                    call = NATIVE.admin_inventory if admin else NATIVE.inventory
                    with self.assertRaisesRegex(NATIVE.Retain, "entry-limit|population"):
                        call(snap, mock.Mock(return_value=""), time.monotonic() + 2)
                self.assertEqual(len(consumed), cap)

    def test_real_working_directory_cap_is_checked_before_leaf_classification(self):
        for name in ("one", "two", "three"):
            (self.root / name).touch()
        snap = {"path": str(self.root), "gitdir": str(self.root),
                "path_id": [self.root.stat().st_dev, self.root.stat().st_ino]}
        with mock.patch.object(NATIVE, "read_file", return_value=(b"", (1,))), \
                mock.patch.object(NATIVE, "index_entries", return_value={}), \
                mock.patch.object(NATIVE, "ENTRY_LIMIT", 3):
            with self.assertRaisesRegex(NATIVE.Retain, "inventory-entry-limit"):
                NATIVE.inventory(snap, mock.Mock(return_value=""), time.monotonic() + 2)


class SupervisorTests(unittest.TestCase):
    def invoke(self, code, **kwargs):
        return NATIVE.supervise([sys.executable, "-I", "-c", code],
                                cwd="/", env=dict(os.environ), **kwargs)

    def test_actual_owned_child_exits_reaped_with_output_hash(self):
        result = self.invoke("print('fixture')")
        self.assertIsNone(result.failure)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.proof["reaped"])
        self.assertTrue(result.proof["group_absent"])
        self.assertGreater(result.proof["pid"], 0)
        self.assertEqual(result.proof["stdout_sha256"], hashlib.sha256(b"fixture\n").hexdigest())

    def test_timeout_kills_only_owned_group_and_reaps(self):
        result = self.invoke("import time; time.sleep(30)", timeout=0.05)
        self.assertIsNotNone(result.failure)
        self.assertTrue(result.proof["reaped"])
        self.assertTrue(result.proof["group_absent"])
        self.assertLess(result.proof["ended_monotonic"] - result.proof["started_monotonic"], 3)

    def test_bounded_input_larger_than_pipe_is_drained_with_output(self):
        data = b"fixture\n" * 32768
        result = self.invoke("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())", input_data=data)
        self.assertIsNone(result.failure)
        self.assertEqual(result.stdout, data)
        self.assertTrue(result.proof["reaped"])

    def test_oversized_input_never_spawns_and_early_stdin_close_is_failure(self):
        with mock.patch.object(NATIVE.subprocess, "Popen") as spawn:
            result = self.invoke("pass", input_data=b"x" * (NATIVE.LEAF_LIMIT + 1))
        spawn.assert_not_called()
        self.assertIsNotNone(result.failure)
        result = self.invoke("import os; os.close(0)", input_data=b"x" * NATIVE.LEAF_LIMIT)
        self.assertIsNotNone(result.failure)
        self.assertTrue(result.proof["reaped"])

    def test_post_spawn_selector_failure_reaps_owned_child(self):
        with mock.patch.object(NATIVE.selectors, "DefaultSelector", side_effect=OSError("fixture")):
            result = self.invoke("import time; time.sleep(30)")
        self.assertIsNotNone(result.failure)
        self.assertTrue(result.proof["reaped"])
        self.assertTrue(result.proof["group_absent"])

    def test_spawn_failure_has_no_invented_pid_or_reap(self):
        with mock.patch.object(NATIVE.subprocess, "Popen", side_effect=OSError("fixture")):
            result = self.invoke("pass")
        self.assertIsNone(result.proof["pid"])
        self.assertFalse(result.proof["reaped"])
        self.assertIsNotNone(result.failure)

    def test_expired_deadline_never_spawns(self):
        with mock.patch.object(NATIVE, "DEADLINE", time.monotonic() - 1), \
                mock.patch.object(NATIVE.subprocess, "Popen") as spawn:
            result = self.invoke("pass")
        spawn.assert_not_called()
        self.assertIsNone(result.proof["pid"])
        self.assertIsNotNone(result.failure)

    def test_selector_close_failure_still_reaps_owned_child(self):
        original = NATIVE.selectors.DefaultSelector
        selector = original()
        close = selector.close
        def failed_close():
            close()
            raise OSError("fixture close")
        with mock.patch.object(selector, "close", side_effect=failed_close), \
                mock.patch.object(NATIVE.selectors, "DefaultSelector", return_value=selector):
            result = self.invoke("print('fixture')")
        self.assertIsNotNone(result.failure)
        self.assertTrue(result.proof["reaped"])
        self.assertTrue(result.proof["group_absent"])


class ObserverTests(unittest.TestCase):
    def observer(self):
        result = object.__new__(NATIVE.Darwin)
        result.until = time.monotonic() + 1
        result.calls = 0
        result.population = mock.Mock(return_value={123})
        bsd = NATIVE.BSD()
        bsd.pid, bsd.uid, bsd.ruid = 123, os.getuid(), os.getuid()
        bsd.start_sec = 100
        result.info = mock.Mock(return_value=bsd)
        result.references = mock.Mock(return_value=[])
        return result

    def test_outside_alive_process_can_clear_without_exit(self):
        observer = self.observer()
        proof = observer.observe(["/fixture/task", "/fixture/admin"], {(1, 2)})
        self.assertEqual(proof["holders"], [])
        self.assertEqual(proof["processes"], 1)

    def test_checkout_and_admin_references_block_by_path_or_inode(self):
        for kind, path, inode in (("cwd", b"/fixture/task", (3, 4)),
                                  ("fd", b"/fixture/admin/index", (3, 4)),
                                  ("mapping", b"", (1, 2)),
                                  ("fileport", b"/fixture/task/file", (3, 4))):
            with self.subTest(kind=kind):
                observer = self.observer()
                vnode = NATIVE.VPath()
                vnode.path = path
                vnode.stat.dev, vnode.stat.ino = inode
                observer.references.return_value = [(kind, vnode)]
                self.assertEqual(observer.observe(["/fixture/task", "/fixture/admin"], {(1, 2)})[
                    "holders"][0]["kind"], kind)

    def test_selected_visibility_error_never_becomes_absence(self):
        observer = self.observer()
        observer.info.side_effect = NATIVE.Retain("holder-visibility-unknown")
        with self.assertRaisesRegex(NATIVE.Retain, "visibility"):
            observer.observe(["/fixture/task"], set())

    def test_births_and_pid_reuse_refuse(self):
        observer = self.observer()
        observer.population.side_effect = [{123}, {123, 124}]
        with self.assertRaisesRegex(NATIVE.Retain, "population-changed"):
            observer.observe(["/fixture/task"], set())
        observer = self.observer()
        with self.assertRaisesRegex(NATIVE.Retain, "known-holder-identity-changed"):
            observer.observe(["/fixture/task"], set(), [[123, 99, 0, os.getuid(), os.getuid()]])

    def test_short_struct_errno_and_full_array_are_unknown(self):
        observer = object.__new__(NATIVE.Darwin)
        observer.lib = mock.Mock()
        observer.call = mock.Mock(return_value=(0, errno.EPERM))
        with self.assertRaisesRegex(NATIVE.Retain, "visibility"):
            observer.info(123, 3, NATIVE.BSD)
        observer.call.return_value = (ctypes.sizeof(NATIVE.BSD) - 1, 0)
        with self.assertRaisesRegex(NATIVE.Retain, "visibility"):
            observer.info(123, 3, NATIVE.BSD)
        observer.call.return_value = (3 * ctypes.sizeof(NATIVE.FD), 0)
        with self.assertRaisesRegex(NATIVE.Retain, "list-unknown"):
            observer.array(123, 1, NATIVE.FD, 2)


@unittest.skipUnless(sys.platform == "darwin", "Darwin ACL fixture")
class NativeACLTests(unittest.TestCase):
    def test_actual_owned_empty_acl_is_admitted(self):
        with tempfile.TemporaryDirectory() as temp:
            NATIVE.non_granting_acl(Path(temp).resolve(), time.monotonic() + 1)

    def test_actual_deny_only_ancestor_preserves_private_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            private = root / "private"
            private.mkdir(mode=0o700)
            subprocess.run(["/bin/chmod", "+a", "everyone deny delete", str(root)], check=True)
            try:
                before = NATIVE.access_boundary(private, time.monotonic() + 3)
                self.assertTrue(before)
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)
                subprocess.run(["/bin/chmod", "+a", "everyone allow readattr", str(root)], check=True)
                with self.assertRaisesRegex(NATIVE.Retain, "grants"):
                    NATIVE.access_boundary(private, time.monotonic() + 3)
            finally:
                subprocess.run(["/bin/chmod", "-N", str(root)], check=True)


if __name__ == "__main__":
    unittest.main()
