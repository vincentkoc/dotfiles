#!/usr/bin/env python3
"""Offline migration and exec-parity fixtures; never invoke real gh or auth."""

import json
import os
from pathlib import Path
import pty
import runpy
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tty
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "ghx-auth-repair"
REPAIR = runpy.run_path(str(SCRIPT))
ORIGINAL = REPAIR["ORIGINAL"]
SHA = REPAIR["sha"]
GLOBALS = REPAIR["repair"].__globals__
ERROR = b"ghx: outer wrapper did not provide the real GitHub CLI path\n"
FAKE = """#!{python}
import json, os, sys
data = sys.stdin.buffer.read(int(os.environ.get("INPUT_SIZE", "0")))
print(json.dumps({{"argv": sys.argv[1:], "stdin": data.hex(), "pid": os.getpid(),
 "tty": [os.isatty(i) for i in range(3)],
 "env": {{key: os.environ.get(key) for key in (
  "GHX_GH_PATH", "OCTOPOOL_GH_PATH", "OCTOPOOL_FRESH", "OCTOPOOL_URL",
  "GH_PROMPT_DISABLED", "GH_PAGER", "SENTINEL")}}}}), flush=True)
sys.stderr.write("fixture stderr\\n")
sys.exit(int(os.environ.get("EXIT", "0")))
"""


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ghx-auth-repair-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.target = self.root / "ghx"
        self.target.write_bytes(ORIGINAL)
        self.target.chmod(0o751)
        self.native = self.root / "native binary"
        shutil.copyfile("/bin/echo", self.native)
        self.native.chmod(0o755)
        self.native_sha = SHA(self.native.read_bytes())
        self.backup_dir = self.root / "backup"
        self.backup_dir.mkdir(mode=0o700)
        self.backup = self.backup_dir / "ghx.before"

    def repair(self, apply=False, **changes):
        args = dict(target=self.target, expected=SHA(ORIGINAL), native=self.native,
                    native_sha=self.native_sha, backup=self.backup, apply=apply)
        args.update(changes)
        return REPAIR["repair"](**args)

    def cli(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--target", str(self.target),
             "--expected-sha256", SHA(self.target.read_bytes()),
             "--native-gh", str(self.native), "--native-sha256", self.native_sha, *extra],
            capture_output=True, timeout=10,
        )

    def test_exact_signature_and_insertion(self):
        self.assertEqual(len(ORIGINAL), 349)
        self.assertEqual(SHA(ORIGINAL), REPAIR["ORIGINAL_SHA"])
        after = REPAIR["repaired"](Path("/opt/homebrew/bin/gh"))
        self.assertEqual(SHA(after), "624c261a7bbe98556548597eed0c532543734c1c0c11e653461462c80f3bb094")
        self.assertEqual(after.replace(
            b'if [[ "${1:-}" == auth ]]; then\n  exec /opt/homebrew/bin/gh "$@"\nfi\n\n',
            b"", 1), ORIGINAL)

    def test_dry_run_apply_metadata_and_idempotency(self):
        os.utime(self.target, ns=(1600000000000000000, 1600000000000000000))
        before = self.target.stat()
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"dry run", result.stdout)
        self.assertEqual(self.target.stat().st_ino, before.st_ino)
        self.assertFalse(self.backup.exists())
        before = self.target.stat()
        attributes = REPAIR["read_file"](self.target, target=True)[3]
        result = self.cli("--apply", "--backup", str(self.backup))
        self.assertEqual(result.returncode, 0, result.stderr)
        after = self.target.stat()
        self.assertNotEqual(before.st_ino, after.st_ino)
        for field in ("st_mode", "st_uid", "st_gid", "st_mtime_ns"):
            self.assertEqual(getattr(before, field), getattr(after, field))
        self.assertEqual(REPAIR["read_file"](self.target, target=True)[3], attributes)
        self.assertEqual(self.backup.read_bytes(), ORIGINAL)
        self.assertEqual(stat.S_IMODE(self.backup.stat().st_mode), 0o600)
        self.assertEqual(self.target.read_bytes(), REPAIR["repaired"](self.native))
        result = self.cli("--apply", "--backup", str(self.backup))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"already repaired", result.stdout)
        self.assertEqual(self.target.stat().st_ino, after.st_ino)

    def test_arguments_and_private_backup_required(self):
        result = self.cli("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.backup_dir.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "0700"):
            self.repair(apply=True)
        self.backup_dir.chmod(0o700)
        self.backup.write_bytes(b"existing")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.repair(apply=True)
        self.assertEqual(self.backup.read_bytes(), b"existing")
        for path in ("relative", "/tmp/../ghx", "/tmp/line\nbreak"):
            with self.assertRaises(ValueError):
                REPAIR["absolute"](path)

    def test_atime_preserved(self):
        timestamp = 1600000000000000000
        os.utime(self.target, ns=(timestamp, timestamp))
        self.repair(apply=True)
        self.assertEqual(self.target.stat().st_atime_ns, timestamp)
        self.assertEqual(self.target.stat().st_mtime_ns, timestamp)

    def test_unknown_binary_and_digest_refused(self):
        for content in (b"#!/bin/sh\nexit 0\n", self.native.read_bytes()):
            self.target.write_bytes(content)
            with self.assertRaises(ValueError):
                self.repair(apply=True, expected=SHA(content))
        self.target.write_bytes(ORIGINAL)
        with self.assertRaisesRegex(ValueError, "target digest"):
            self.repair(expected="0" * 64)
        with self.assertRaisesRegex(ValueError, "native gh digest"):
            self.repair(native_sha="0" * 64)
        self.assertFalse(self.backup.exists())

    def test_symlink_hardlink_foreign_owner_and_mode_refused(self):
        saved = self.root / "saved"
        self.target.rename(saved)
        self.target.symlink_to(saved)
        with self.assertRaises(OSError):
            self.repair(apply=True)
        self.target.unlink()
        os.link(saved, self.target)
        with self.assertRaisesRegex(ValueError, "hard links"):
            self.repair(apply=True)
        saved.unlink()
        with mock.patch.object(os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaisesRegex(ValueError, "foreign ownership"):
                REPAIR["read_file"](self.target, target=True)
        self.target.chmod(0o4751)
        with self.assertRaisesRegex(ValueError, "permissions"):
            self.repair(apply=True)

    def test_native_script_refusal_symlink_and_pin_semantics(self):
        wrapper = self.root / "wrapper"
        wrapper.write_bytes(b"#!/bin/sh\nexit 0\n")
        wrapper.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "binary"):
            self.repair(native=wrapper, native_sha=SHA(wrapper.read_bytes()))
        link = self.root / "native-link"
        link.symlink_to(self.native)
        self.repair(apply=True, native=link)
        post_sha = SHA(self.target.read_bytes())
        self.assertIn("already repaired", self.repair(native=link, expected=post_sha))
        with self.assertRaisesRegex(ValueError, "different native path pin"):
            self.repair(native=self.native, expected=post_sha)

    def test_target_and_native_drift_preserve_backup(self):
        original_native_state = REPAIR["native_state"]
        for drift in ("target", "inode", "mode", "native"):
            with self.subTest(drift=drift):
                self.target.write_bytes(ORIGINAL)
                self.target.chmod(0o751)
                if self.backup.exists():
                    self.backup.unlink()
                calls = 0

                def changing_native(*args):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        if drift == "target":
                            self.target.write_bytes(ORIGINAL + b"# concurrent edit\n")
                        elif drift == "inode":
                            replacement = self.root / "concurrent"
                            replacement.write_bytes(ORIGINAL)
                            replacement.chmod(0o751)
                            replacement.replace(self.target)
                        elif drift == "mode":
                            self.target.chmod(0o700)
                        else:
                            self.native.write_bytes(self.native.read_bytes() + b"changed")
                    return original_native_state(*args)

                with mock.patch.dict(GLOBALS, native_state=changing_native):
                    with self.assertRaises(ValueError):
                        self.repair(apply=True)
                self.assertEqual(self.backup.read_bytes(), ORIGINAL)
                self.assertNotEqual(self.target.read_bytes(), REPAIR["repaired"](self.native))
                self.assertFalse(list(self.root.glob(".ghx-auth-repair-*")))

    def test_native_symlink_resolution_drift(self):
        link = self.root / "native-link"
        link.symlink_to(self.native)
        other = self.root / "same-bytes"
        shutil.copyfile(self.native, other)
        other.chmod(0o755)
        original_native_state = REPAIR["native_state"]
        calls = 0

        def changing_native(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                link.unlink()
                link.symlink_to(other)
            return original_native_state(*args)

        with mock.patch.dict(GLOBALS, native_state=changing_native):
            with self.assertRaisesRegex(ValueError, "native gh changed"):
                self.repair(apply=True, native=link)
        self.assertEqual(self.target.read_bytes(), ORIGINAL)
        self.assertEqual(self.backup.read_bytes(), ORIGINAL)

    @unittest.skipUnless(sys.platform == "darwin", "Darwin ACL fixture")
    def test_acl_and_private_directory_acl_refused(self):
        for path in (self.target, self.backup_dir):
            with self.subTest(path=path.name):
                subprocess.run(["/bin/chmod", "+a", "everyone allow read", str(path)],
                               check=True, capture_output=True)
                try:
                    with self.assertRaisesRegex(ValueError, "ACL"):
                        self.repair(apply=True)
                finally:
                    subprocess.run(["/bin/chmod", "-N", str(path)], check=True)

    def test_unsupported_metadata_refused(self):
        if sys.platform == "darwin":
            os.chflags(self.target, stat.UF_HIDDEN)
            try:
                with self.assertRaisesRegex(ValueError, "flags"):
                    self.repair(apply=True)
            finally:
                os.chflags(self.target, 0)
            subprocess.run(
                ["/usr/bin/xattr", "-w", "user.fixture", "fixture", str(self.target)],
                capture_output=True, check=True,
            )
        else:
            os.setxattr(self.target, "user.fixture", b"fixture")
        with self.assertRaisesRegex(ValueError, "attributes"):
            self.repair(apply=True)

    def runtime_fixture(self):
        fake = self.root / "native 'quoted' $; binary"
        fake.write_text(FAKE.format(python=sys.executable))
        fake.chmod(0o755)
        candidate = self.root / "candidate"
        candidate.write_bytes(REPAIR["repaired"](fake))
        subprocess.run(["/bin/bash", "-n", str(candidate)], check=True)
        home = self.root / "home"
        (home / ".local/bin").mkdir(parents=True)
        (home / ".local/bin/octopool").symlink_to(fake)
        env = {"PATH": os.defpath, "HOME": str(home), "SENTINEL": "untouched",
               "GH_PROMPT_DISABLED": "custom", "GH_PAGER": "custom",
               "OCTOPOOL_GH_PATH": "inherited", "OCTOPOOL_FRESH": "inherited",
               "OCTOPOOL_URL": "https://fixture.invalid"}
        return fake, candidate, env

    def test_auth_binary_io_environment_exit_and_exec_pid(self):
        fake, candidate, env = self.runtime_fixture()
        args = ["auth", "login", "--hostname", "fixture.invalid", "two words", "", "*"]
        data = b"stdin\x00bytes\xff\n"
        env.update(INPUT_SIZE=str(len(data)), EXIT="37")
        with subprocess.Popen(["/bin/bash", str(candidate), *args], env=env,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as child:
            stdout, stderr = child.communicate(data, timeout=5)
        observed = json.loads(stdout)
        self.assertEqual(child.returncode, 37)
        self.assertEqual(observed["pid"], child.pid)
        self.assertEqual(observed["argv"], args)
        self.assertEqual(observed["stdin"], data.hex())
        self.assertEqual(stderr, b"fixture stderr\n")
        self.assertEqual(observed["tty"], [False] * 3)
        self.assertEqual(observed["env"], {key: env.get(key) for key in observed["env"]})

    def test_non_auth_guard_and_octopool_parity(self):
        fake, candidate, env = self.runtime_fixture()
        env["EXIT"] = "29"
        cases = [[], ["api", "user"], ["--no-cache", "api", "user"],
                 ["--no-cache", "auth", "status"], ["xcache", "stats"],
                 ["xdaemon", "status"], ["--ttl", "15", "pr", "view", "1"]]
        for backend in (None, str(self.root / "missing"), str(fake)):
            for args in cases:
                with self.subTest(backend=backend, args=args):
                    current_env = dict(env)
                    if backend is not None:
                        current_env["GHX_GH_PATH"] = backend
                    outputs = [subprocess.run(
                        ["/bin/bash", str(path), *args], env=current_env,
                        capture_output=True, timeout=5,
                    ) for path in (self.target, candidate)]
                    if backend != str(fake):
                        self.assertEqual([(r.returncode, r.stdout, r.stderr) for r in outputs],
                                         [(127, b"", ERROR)] * 2)
                    else:
                        self.assertEqual([r.returncode for r in outputs], [29, 29])
                        self.assertEqual([r.stderr for r in outputs], [b"fixture stderr\n"] * 2)
                        values = [json.loads(r.stdout) for r in outputs]
                        for value in values:
                            value.pop("pid")
                        self.assertEqual(values[0], values[1])
                        self.assertEqual(values[0]["argv"], ["gh"] + (
                            args[1:] if args[:1] == ["--no-cache"] else args))
                        self.assertEqual(values[0]["env"]["OCTOPOOL_GH_PATH"], str(fake))
                        self.assertEqual(values[0]["env"]["OCTOPOOL_FRESH"],
                                         "1" if args[:1] == ["--no-cache"] else "inherited")

    def test_auth_pty(self):
        _, candidate, env = self.runtime_fixture()
        env.update(INPUT_SIZE="4", EXIT="23")
        master, slave = pty.openpty()
        tty.setraw(slave)
        child = subprocess.Popen(["/bin/bash", str(candidate), "auth", "status", ""],
                                 env=env, stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        try:
            os.write(master, b"pty\n")
            received = bytearray()
            deadline = time.monotonic() + 5
            while b"\n" not in received:
                self.assertLess(time.monotonic(), deadline, "PTY fixture timed out")
                if select.select([master], [], [], 0.1)[0]:
                    received.extend(os.read(master, 8192))
            observed = json.loads(received.split(b"\n", 1)[0])
            self.assertEqual(child.wait(timeout=5), 23)
            self.assertEqual(observed["argv"], ["auth", "status", ""])
            self.assertEqual(observed["stdin"], b"pty\n".hex())
            self.assertEqual(observed["tty"], [True] * 3)
            self.assertEqual(observed["pid"], child.pid)
        finally:
            os.close(master)
            if child.poll() is None:
                child.kill()
                child.wait()


if __name__ == "__main__":
    unittest.main()
