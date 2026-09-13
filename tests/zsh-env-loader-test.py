#!/usr/bin/env python3
"""Shell policy checks with isolated startup files and no agent processes."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        for name in (".zshenv", ".exports", ".bashrc"):
            (self.home / name).symlink_to(ROOT / name)
        self.env = {
            "HOME": str(self.home),
            "ZDOTDIR": str(self.home),
            "PATH": "/usr/bin:/bin",
            "TERM": "xterm",
            "LC_ALL": "C",
            "DOTFILES_EXPORTS_LOADED": "1",
        }
        self.probe = (
            '[[ "$TOKENJUICE_STATS" == off ]] || exit 31\n'
            '/bin/bash --noprofile --norc -c \'[[ "$TOKENJUICE_STATS" == off ]]\''
        )

    def check_shell(self, argv):
        for inherited in (None, "on"):
            with self.subTest(inherited=inherited, argv=argv):
                env = dict(self.env)
                if inherited is not None:
                    env["TOKENJUICE_STATS"] = inherited
                result = subprocess.run(
                    argv, env=env, capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_nonlogin_zsh_with_exports_already_loaded(self):
        zsh = shutil.which("zsh")
        self.assertIsNotNone(zsh)
        self.check_shell([zsh, "-c", self.probe])

    def test_nonlogin_interactive_bash(self):
        self.check_shell(["/bin/bash", "--noprofile", "-ic", self.probe])

    def test_shared_exports(self):
        self.check_shell([
            "/bin/bash", "--noprofile", "--norc", "-c",
            'source "$HOME/.exports"\n' + self.probe,
        ])


if __name__ == "__main__":
    unittest.main()
