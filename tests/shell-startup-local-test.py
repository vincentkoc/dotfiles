#!/usr/bin/env python3
"""Exercise startup fragments without loading personal plugins or credentials."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "LC_ALL": "C"}

    def run_shell(self, shell, source, **extra):
        if "OSTYPE" in extra:
            extra["FIXTURE_OSTYPE"] = extra.pop("OSTYPE")
            source = 'OSTYPE="$FIXTURE_OSTYPE"\n' + source
        return subprocess.run(
            [shell, "-f", "-c", source], env={**self.env, **extra},
            capture_output=True, text=True, timeout=10,
        )

    def test_lmstudio_is_appended_only_when_installed(self):
        for name, shell in ((".profile", "/bin/sh"), (".zshrc", "/bin/zsh")):
            # Test the authored PATH fragment without unrelated shell plugins.
            fragment = (ROOT / name).read_text()
            fragment = fragment[fragment.rindex("\nif "):]
            for installed in (False, True):
                with self.subTest(name=name, installed=installed):
                    directory = self.home / ".lmstudio/bin"
                    if installed:
                        directory.mkdir(parents=True, exist_ok=True)
                    elif directory.exists():
                        directory.rmdir()
                    result = self.run_shell(shell, fragment + '\nprintf "%s" "$PATH"')
                    self.assertEqual(result.returncode, 0, result.stderr)
                    expected = self.env["PATH"] + (":" + str(directory) if installed else "")
                    self.assertEqual(result.stdout, expected)

    def env_fragment(self):
        source = (ROOT / ".zshrc").read_text()
        return source.split("# Load dotfiles .env early", 1)[1].split(
            "# Interactive shells", 1
        )[0].split("\n", 1)[1]

    @unittest.skipUnless(sys.platform == "darwin", "native BSD stat contract")
    def test_local_darwin_env_is_sourced_and_exported(self):
        path = self.home / "Library/Mobile Documents/com~apple~CloudDocs/dotfiles/.env"
        path.parent.mkdir(parents=True)
        path.write_text("STARTUP_FIXTURE_VALUE=local\n")
        result = self.run_shell(
            "/bin/zsh", self.env_fragment() +
            '\n/bin/sh -c \'printf "%s" "$STARTUP_FIXTURE_VALUE"\'', OSTYPE="darwin",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "local")

    def test_dataless_unknown_and_invalid_metadata_do_not_source(self):
        path = self.home / "Library/Mobile Documents/com~apple~CloudDocs/dotfiles/.env"
        path.parent.mkdir(parents=True)
        sentinel = self.home / "unexpected-source"
        path.write_text('touch "$HOME/unexpected-source"\n')
        probe = self.home / "stat-fixture"
        probe.write_text('#!/bin/sh\nprintf "%s\\n" "$FIXTURE_FLAGS"\nexit "$FIXTURE_EXIT"\n')
        probe.chmod(0o700)
        fragment = self.env_fragment().replace("/usr/bin/stat", '"' + str(probe) + '"')
        # Darwin SDK sys/stat.h defines SF_DATALESS as 0x40000000.
        for flags, status in (("1073741824", "0"), ("0", "1"), ("unknown", "0")):
            with self.subTest(flags=flags, status=status):
                result = self.run_shell(
                    "/bin/zsh", fragment, OSTYPE="darwin",
                    FIXTURE_FLAGS=flags, FIXTURE_EXIT=status,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("skipped .env", result.stderr)
                self.assertFalse(sentinel.exists())

    def test_non_darwin_env_uses_local_dotfiles_without_darwin_stat(self):
        directory = self.home / ".dotfiles"
        directory.mkdir()
        (directory / ".env").write_text("STARTUP_FIXTURE_VALUE=portable\n")
        fragment = self.env_fragment().replace("/usr/bin/stat", "/not-an-installed-stat")
        result = self.run_shell(
            "/bin/zsh", fragment + '\nprintf "%s" "$STARTUP_FIXTURE_VALUE"', OSTYPE="linux-gnu",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "portable")


if __name__ == "__main__":
    unittest.main()
