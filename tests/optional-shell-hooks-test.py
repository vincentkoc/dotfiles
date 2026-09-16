#!/usr/bin/env python3
"""Exercise optional shell hooks in isolated homes without personal state."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OptionalShellHooksTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.env = {"HOME": str(self.home), "ZDOTDIR": str(self.home),
                    "PATH": "/usr/bin:/bin", "LC_ALL": "C",
                    "DOTFILES_EXPORTS_LOADED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        shutil.copyfile(ROOT / ".zshenv", self.home / ".zshenv")

    def test_codex_helper_absent_and_present_in_shell_modes(self):
        helper = self.home / ".config/codex/shell-env.sh"
        probe = 'printf "%s" "${CODEX_HOOK_FIXTURE-unset}"'
        profile = '. "$1"; ' + probe
        commands = [
            ["/bin/sh", "-c", profile, "fixture", str(ROOT / ".profile")],
            ["/bin/bash", "--noprofile", "--norc", "-c", profile, "fixture", str(ROOT / ".profile")],
            ["/bin/zsh", "-d", "-c", probe],
            ["/bin/zsh", "-d", "-i", "-c", probe],
            ["/bin/zsh", "-d", "-l", "-c", probe],
        ]
        for present in (False, True):
            if present:
                helper.parent.mkdir(parents=True)
                helper.write_text("export CODEX_HOOK_FIXTURE=loaded\n")
            for argv in commands:
                with self.subTest(present=present, argv=argv):
                    result = subprocess.run(argv, env=self.env, cwd=self.home,
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "loaded" if present else "unset")

    def test_codex_loader_source_status_and_errexit(self):
        helper = self.home / ".config/codex/shell-env.sh"
        for present in (False, True):
            if present:
                helper.parent.mkdir(parents=True)
                helper.write_text("export CODEX_HOOK_FIXTURE=loaded\n")
            for shell, filename in (("/bin/sh", ".profile"), ("/bin/bash", ".profile"),
                                    ("/bin/zsh", ".zshenv")):
                for errexit in (False, True):
                    with self.subTest(present=present, shell=shell, errexit=errexit):
                        command = ('set -e; ' if errexit else '') + (
                            '. "$1"; hook_status=$?; '
                            'printf "%s\\n" "$hook_status" "${CODEX_HOOK_FIXTURE-unset}"')
                        result = subprocess.run(
                            [shell, "-f", "-c", command, "fixture", str(ROOT / filename)],
                            env=self.env, cwd=self.home, capture_output=True, text=True, timeout=5)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout.splitlines(),
                                         ["0", "loaded" if present else "unset"])

    def test_java_helper_and_opt_in_fallback_keep_path_precedence(self):
        exports = (ROOT / ".exports").read_text()
        functions = exports.split("path_prepend() {", 1)[1].split(
            "#\n# Package Managers", 1)[0]
        functions = "path_prepend() {" + functions
        java = exports.split("# Java\n# `java_home`", 1)[1].split("# Android", 1)[0]
        java = "# `java_home`" + java
        final_paths = exports.split("# Prefer user-level CLI shims", 1)[1]
        final_paths = "# Prefer user-level CLI shims" + final_paths
        fallback = self.home / "java-home"
        fallback.write_text('#!/bin/sh\nprintf x >> "$HOME/fallback-calls"\nprintf "%s" "$HOME/fallback-jdk"\n')
        fallback.chmod(0o700)
        java = java.replace("/usr/libexec/java_home", str(fallback))
        helper = self.home / ".config/dotfiles/java-env.sh"
        helper.parent.mkdir(parents=True)
        for state, opt_in in (("absent", "0"), ("absent", "1"), ("unavailable", "0"),
                              ("unavailable", "1"), ("selected", "0"), ("selected", "1")):
            if helper.exists():
                helper.unlink()
            if state == "unavailable":
                helper.write_text("return 1\n")
            elif state == "selected":
                helper.write_text('export JAVA_HOME="$HOME/selected-jdk"\n'
                                  'path_prepend "$HOME/selected-jdk/bin"\nreturn 0\n')
            for shell in ("/bin/bash", "/bin/zsh"):
                with self.subTest(state=state, opt_in=opt_in, shell=shell):
                    log = self.home / "fallback-calls"
                    if log.exists():
                        log.unlink()
                    env = self.env | {"DOTFILES_SET_JAVA_HOME": opt_in,
                                      "DOTFILES_BIN_ROOT": str(self.home / "bin"),
                                      "JAVA_HOME": "inherited-jdk"}
                    # Repeated successful selection must not duplicate its PATH entry.
                    command = (functions + java + (java if state == "selected" else "") + final_paths +
                               '\nprintf "%s\\n" "$JAVA_HOME" "$PATH"')
                    result = subprocess.run([shell, "-f", "-c", command], env=env,
                                            cwd=self.home, capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    selected = state == "selected"
                    uses_fallback = not selected and opt_in == "1"
                    expected_java = (str(self.home / "selected-jdk") if selected else
                                     str(self.home / "fallback-jdk") if uses_fallback else "inherited-jdk")
                    expected_path = [str(self.home / "bin"), str(self.home / ".local/bin")]
                    if selected:
                        expected_path.append(str(self.home / "selected-jdk/bin"))
                    expected_path.extend(("/usr/bin", "/bin"))
                    self.assertEqual(result.stdout.splitlines(), [expected_java, ":".join(expected_path)])
                    self.assertEqual(log.read_text() if log.exists() else "", "x" if uses_fallback else "")


if __name__ == "__main__":
    unittest.main()
