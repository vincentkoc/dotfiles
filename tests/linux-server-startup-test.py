#!/usr/bin/env python3
"""Profile regressions using a fixture HOME, no real tmux or shell rc files."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "profiles/linux-server"


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.zsh = shutil.which("zsh")
        self.assertIsNotNone(self.zsh)
        for name in ("zshenv", "zprofile"):
            (self.home / ("." + name)).symlink_to(PROFILE / name)
        (self.home / ".profile").write_text(
            'print -r -- "$$" >> "$HOME/profile.log"\nsource '
            + shlex.quote(str(PROFILE / "profile")) + "\n"
        )
        (self.home / ".functions").symlink_to(ROOT / ".functions")
        # Keep the unchanged interactive function-loading contract without GUI/plugins.
        (self.home / ".zshrc").write_text('source "$HOME/.functions"\n')
        fake_tmux = self.home / "tmux"
        fake_tmux.write_text('#!/bin/sh\nprintf "query\\n" >> "$HOME/tmux.log"\nprintf "cockpit\\n"\n')
        fake_tmux.chmod(0o700)
        self.env = {
            "HOME": str(self.home), "ZDOTDIR": str(self.home), "PATH": "/usr/bin:/bin",
            "SHELL": self.zsh, "TERM": "xterm-256color", "LC_ALL": "C", "EDITOR": "vi",
            "TMUX": "/fixture/socket,42,0", "TMUX_PANE": "%7",
            "TT_TMUX_BIN": str(fake_tmux), "DOTFILES_DIR": str(ROOT),
            "DOTFILES_FUNCTIONS_ROOT": str(ROOT / "functions"), "TMPDIR": "/tmp",
        }

    def tearDown(self):
        self.temporary.cleanup()

    def shell(self, flags, command):
        return subprocess.run(
            [self.zsh, flags, command], env=self.env, text=True,
            capture_output=True, check=True, timeout=15,
        ).stdout

    def check_command(self):
        return (
            '[[ "$TMPDIR" == "$HOME/.cache/cockpit-tmp" ]] || exit 31\n'
            '(( $+functions[gwt] )) || exit 32\n'
            '(( $+functions[doctor] )) || exit 33\n'
            '(( $+functions[mcd] )) || exit 34\n'
            '[[ ${(j.:.)path} == "$PATH" ]] || exit 35\n'
        )

    def test_four_shell_modes(self):
        for mode in ("-c", "-lc", "-ic", "-ilc", "nested"):
            with self.subTest(mode=mode):
                for log in ("profile.log", "tmux.log"):
                    (self.home / log).unlink(missing_ok=True)
                command = self.check_command()
                flags = mode
                if mode == "nested":
                    flags = "-c"
                    command = shlex.join([self.zsh, "-c", command])
                self.shell(flags, command)
                loads = (self.home / "profile.log").read_text().splitlines()
                self.assertEqual(len(loads), 2 if mode == "nested" else 1)
                self.assertEqual((self.home / "tmux.log").read_text().splitlines(), ["query"])
                self.assertEqual((self.home / ".cache/cockpit-tmp").stat().st_mode & 0o777, 0o700)

    def test_inherited_private_tmp_skips_tmux(self):
        scratch = self.home / ".cache/cockpit-tmp"
        scratch.mkdir(parents=True, mode=0o700)
        self.env["TMPDIR"] = str(scratch)
        self.shell("-lc", self.check_command())
        self.assertFalse((self.home / "tmux.log").exists())

    def test_profile_in_sh_emulation(self):
        self.shell("-lc", 'emulate sh\nsource "$HOME/.profile"\n'
                   '[[ "$TMPDIR" == "$HOME/.cache/cockpit-tmp" ]]')
        self.assertEqual((self.home / "tmux.log").read_text().splitlines(), ["query"])
        self.assertEqual((self.home / ".cache/cockpit-tmp").stat().st_mode & 0o777, 0o700)

    def test_symlink_is_not_accepted(self):
        (self.home / ".cache").mkdir()
        (self.home / "other").mkdir()
        original_mode = (self.home / "other").stat().st_mode
        (self.home / ".cache/cockpit-tmp").symlink_to(self.home / "other")
        self.shell("-c", '[[ "$TMPDIR" == /tmp ]]')
        self.assertEqual((self.home / "other").stat().st_mode, original_mode)

    def test_wrong_mode_is_repaired(self):
        scratch = self.home / ".cache/cockpit-tmp"
        scratch.mkdir(parents=True, mode=0o755)
        scratch.chmod(0o755)
        self.env["TMPDIR"] = str(scratch)
        self.shell("-c", self.check_command())
        self.assertEqual(scratch.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
