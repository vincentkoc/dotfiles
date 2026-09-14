#!/usr/bin/env python3
"""Secretless wrapper fixtures; no installed backend or user config is read."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "bin/gitcrawl"


class GitcrawlWrapperTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gitcrawl-wrapper-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.tools = self.root / "tools"
        self.home.mkdir()
        self.tools.mkdir()
        (self.tools / "bash").symlink_to("/bin/bash")
        self.write(self.tools / "uname", "#!/bin/bash\nprintf '%s\\n' Linux\n")
        self.env = {"HOME": str(self.home), "PATH": str(self.tools)}
        self.wrapper = self.root / "wrapper"
        self.wrapper.write_bytes(SOURCE.read_bytes())
        self.wrapper.chmod(0o755)

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o755)
        return path

    def backend(self, path=None, label="path"):
        path = path or self.tools / "gitcrawl"
        return self.write(path, f"#!{sys.executable}\n" +
            "import json, os, sys\n" +
            f"print(json.dumps({{'backend': {label!r}, 'args': sys.argv[1:], " +
            "'cwd': os.getcwd(), 'stdin': sys.stdin.read(), " +
            "'env': {k: v for k, v in os.environ.items() if k.startswith('GITCRAWL_') " +
            "or k in ('CRAWL_REMOTE_TOKEN', 'CUSTOM_TOKEN', 'UNEXPORTED')}}))\n" +
            "sys.exit(int(os.environ.get('TEST_EXIT', '0')))\n")

    def run_wrapper(self, *args, env=None, wrapper=None):
        return subprocess.run([str(wrapper or self.wrapper), *args], cwd=self.root,
            env=self.env | (env or {}), input="fixture stdin\n", text=True,
            capture_output=True, timeout=5)

    def result(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def test_missing_file_preserves_args_stdin_cwd_exit(self):
        self.backend()
        result = self.run_wrapper("", "two words", "x*y", env={"TEST_EXIT": "7"})
        self.assertEqual(result.returncode, 7)
        data = json.loads(result.stdout)
        self.assertEqual(data["args"], ["", "two words", "x*y"])
        self.assertEqual(data["stdin"], "fixture stdin\n")
        self.assertEqual(data["cwd"], str(self.root))
        self.assertEqual(data["env"], {})

    def test_default_xdg_and_explicit_locations(self):
        self.backend()
        for location, env in [
            (self.home / ".config/openclaw-crawl/remote.env", {}),
            (self.root / "xdg/openclaw-crawl/remote.env", {"XDG_CONFIG_HOME": str(self.root / "xdg")}),
            (self.root / "explicit.env", {"OPENCLAW_CRAWL_ENV": str(self.root / "explicit.env")}),
        ]:
            with self.subTest(location=location.name):
                self.write(location, "export CRAWL_REMOTE_TOKEN=fixture-default\nUNEXPORTED=private-shell-value\n")
                data = self.result(self.run_wrapper(env=env))
                self.assertEqual(data["env"], {"CRAWL_REMOTE_TOKEN": "fixture-default"})
                location.unlink()

    def test_inherited_exports_including_empty_override_defaults(self):
        self.backend()
        values = {"GITCRAWL_CONFIG": "caller.toml", "GITCRAWL_REMOTE_ENDPOINT": "https://example.invalid",
            "GITCRAWL_REMOTE_TOKEN_ENV": "CUSTOM_TOKEN", "CUSTOM_TOKEN": "", "CRAWL_REMOTE_TOKEN": "caller-token",
            "GITCRAWL_REMOTE_ARCHIVE": "", "GITCRAWL_DB_PATH": "caller.db"}
        location = self.root / "defaults.env"
        self.write(location, "\n".join(f"export {name}=file-default" for name in values) + "\n")
        self.assertEqual(self.result(self.run_wrapper(env=values | {"OPENCLAW_CRAWL_ENV": str(location)}))["env"], values)

    def test_unset_or_unexported_inherited_value_is_restored(self):
        self.backend()
        location = self.write(self.root / "defaults.env", "unset CUSTOM_TOKEN\nexport -n GITCRAWL_CONFIG\n")
        values = {"CUSTOM_TOKEN": "caller-token", "GITCRAWL_CONFIG": "caller.toml"}
        self.assertEqual(self.result(self.run_wrapper(env=values | {"OPENCLAW_CRAWL_ENV": str(location)}))["env"], values)

    def test_source_output_is_suppressed_and_failure_is_redacted(self):
        self.backend()
        location = self.root / "defaults.env"
        for suffix, expected in [("export CRAWL_REMOTE_TOKEN=fixture-default\n", 0), ("return 1\n", 1), ("export CRAWL_REMOTE_TOKEN=(\n", 1)]:
            with self.subTest(expected=expected, suffix=suffix):
                self.write(location, "printf 'sensitive-fixture-value'\nprintf 'sensitive-fixture-value' >&2\n" + suffix)
                result = self.run_wrapper(env={"OPENCLAW_CRAWL_ENV": str(location)})
                self.assertEqual(result.returncode, expected)
                self.assertNotIn("sensitive-fixture-value", result.stdout + result.stderr)
                if expected:
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr, "gitcrawl: failed to load remote environment defaults\n")

    def test_local_backend_precedes_darwin_homebrew(self):
        self.backend(self.home / ".local/bin/gitcrawl", "local")
        self.darwin_wrapper()
        self.backend(self.root / "arm/gitcrawl", "arm")
        self.backend(label="path")
        self.assertEqual(self.result(self.run_wrapper())["backend"], "local")

    def darwin_wrapper(self):
        self.write(self.tools / "uname", "#!/bin/bash\nprintf '%s\\n' Darwin\n")
        self.wrapper.write_text(SOURCE.read_text().replace("/opt/homebrew/bin/gitcrawl", str(self.root / "arm/gitcrawl"))
            .replace("/usr/local/bin/gitcrawl", str(self.root / "intel/gitcrawl")))

    def test_darwin_arm_intel_and_path_fallback(self):
        self.darwin_wrapper()
        arm = self.backend(self.root / "arm/gitcrawl", "arm")
        intel = self.backend(self.root / "intel/gitcrawl", "intel")
        self.backend(label="path")
        self.assertEqual(self.result(self.run_wrapper())["backend"], "arm")
        arm.unlink()
        self.assertEqual(self.result(self.run_wrapper())["backend"], "intel")
        intel.unlink()
        self.assertEqual(self.result(self.run_wrapper())["backend"], "path")

    def test_self_alias_copy_and_symlink_loop_are_skipped(self):
        alias = self.root / "alias"
        alias.mkdir()
        (alias / "gitcrawl").symlink_to(self.wrapper)
        local = self.home / ".local/bin/gitcrawl"
        local.parent.mkdir(parents=True)
        local.symlink_to(self.wrapper)
        copy = self.root / "copy/gitcrawl"
        self.write(copy, SOURCE.read_text())
        loop = self.root / "loop"
        loop.mkdir()
        (loop / "gitcrawl").symlink_to("gitcrawl")
        self.backend()
        env = {"PATH": f"{alias}:{copy.parent}:{loop}:{self.tools}"}
        self.assertEqual(self.result(self.run_wrapper(env=env, wrapper=alias / "gitcrawl"))["backend"], "path")

    def test_no_backend_has_fixed_exit_and_diagnostic(self):
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 127)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "gitcrawl: no usable backend found\n")


if __name__ == "__main__":
    unittest.main()
