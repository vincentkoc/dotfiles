#!/usr/bin/env python3
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.native = self.directory / "native"
        self.write_executable(self.native, '''
import json, os, sys
if sys.argv[1:] == ["config", "show", "--json"]:
    sys.exit(4) if os.environ.get("FAIL_CONFIG") else None
    provider = os.environ.get("TEST_PROVIDER", "ascii-box")
    print(json.dumps({"provider": provider, "jobs": {"example": {"provider": os.environ.get("TEST_JOB_PROVIDER")}}}))
else:
    print(json.dumps({"args": sys.argv[1:], "injected": os.environ.get("TEST_INJECTED") == "yes"}))
''')
        self.write_executable(self.directory / "fleet-credentials", '''
import os, sys
assert sys.argv[1:6] == ["run", "test-profile", "--key", "CRABBOX_ASCII_BOX_API_KEY", "--"]
os.environ["TEST_INJECTED"] = "yes"
os.execv(sys.argv[6], sys.argv[6:])
''')
        self.env = {"PATH": f"{self.directory}:{os.path.dirname(sys.executable)}:/usr/bin:/bin",
                    "HOME": str(self.directory), "CRABBOX_BIN": str(self.native),
                    "CRABBOX_BOAT_CREDENTIAL_PROFILE": "test-profile"}

    def write_executable(self, path, body):
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o755)

    def run_launcher(self, args, **env):
        return subprocess.run([sys.executable, str(ROOT / "bin/crabbox"), *args],
                              env=self.env | env, capture_output=True, text=True)

    def test_merged_boat_config_injects_and_preserves_command(self):
        result = self.run_launcher(["run", "--", "echo", "hello world"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         {"args": ["run", "--", "echo", "hello world"], "injected": True})

    def test_explicit_provider_overrides_config_and_remote_arguments(self):
        for args in (["run", "--provider", "blacksmith-testbox", "--", "--provider=boat"],
                     ["run", "-provider=blacksmith-testbox"]):
            with self.subTest(args=args):
                result = self.run_launcher(args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(json.loads(result.stdout)["injected"])
        result = self.run_launcher(["run", "--provider=boat"], TEST_PROVIDER="blacksmith-testbox")
        self.assertTrue(json.loads(result.stdout)["injected"])

    def test_remote_provider_flag_does_not_select_local_provider(self):
        result = self.run_launcher(["run", "--", "tool", "--provider=boat"], TEST_PROVIDER="aws")
        self.assertFalse(json.loads(result.stdout)["injected"])

    def test_job_provider_overrides_default_and_profile_stays_on_original_command(self):
        for default, selected, injected in (("ascii-box", "aws", False), ("aws", "boat", True)):
            result = self.run_launcher(["job", "run", "example"],
                                       TEST_PROVIDER=default, TEST_JOB_PROVIDER=selected)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["injected"], injected)
        result = self.run_launcher(["run", "--profile", "example"])
        self.assertEqual(json.loads(result.stdout),
                         {"args": ["run", "--profile", "example"], "injected": True})

    def test_explicit_credentials_and_help_do_not_probe_or_inject(self):
        for name in ("ASCII_BOX_API_KEY", "CRABBOX_ASCII_BOX_API_KEY"):
            result = self.run_launcher(["list"], FAIL_CONFIG="1", **{name: "synthetic"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)["injected"])
        for args in (["--version"], ["run", "--help"], ["help"]):
            result = self.run_launcher(args, FAIL_CONFIG="1")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)["injected"])

    def test_opt_out_and_failed_probe_do_not_inject(self):
        result = self.run_launcher(["list"], CRABBOX_BOAT_CREDENTIAL_PROFILE="", FAIL_CONFIG="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["injected"])
        result = self.run_launcher(["list"], FAIL_CONFIG="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_path_discovery_skips_launcher_symlink(self):
        (self.directory / "crabbox").symlink_to(ROOT / "bin/crabbox")
        native_directory = self.directory / "backend"
        native_directory.mkdir()
        (native_directory / "crabbox").symlink_to(self.native)
        result = self.run_launcher(["--version"], CRABBOX_BIN="",
                                   PATH=f"{self.directory}:{native_directory}")
        self.assertEqual(result.returncode, 0, result.stderr)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        loader = importlib.machinery.SourceFileLoader("boat_install", str(ROOT / "bin/boat-install"))
        self.module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(self.module)

    def install(self, directory, binary, checksum):
        responses = [io.BytesIO(f"{checksum}  boat-darwin-arm64\n".encode()), io.BytesIO(binary)]
        with patch.object(self.module.platform, "system", return_value="Darwin"), \
                patch.object(self.module.platform, "machine", return_value="arm64"), \
                patch.object(self.module.Path, "home", return_value=Path(directory)), \
                patch.object(self.module.urllib.request, "urlopen", side_effect=responses):
            self.module.install()

    def test_installs_verified_executable_without_shell_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = b"synthetic executable"
            self.install(directory, binary, hashlib.sha256(binary).hexdigest())
            target = Path(directory) / ".local/bin/boat"
            self.assertEqual(target.read_bytes(), binary)
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)
            self.assertFalse((Path(directory) / ".zshrc").exists())

    def test_bad_checksum_preserves_existing_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / ".local/bin/boat"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"existing executable")
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                self.install(directory, b"corrupted", "0" * 64)
            self.assertEqual(target.read_bytes(), b"existing executable")
            self.assertEqual(list(target.parent.iterdir()), [target])


if __name__ == "__main__":
    unittest.main()
