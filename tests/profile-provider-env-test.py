#!/usr/bin/env python3
"""Exercise explicit provider loading with synthetic selectors and a stub op."""
from pathlib import Path
import subprocess
import tempfile
import unittest


PROFILE = Path(__file__).resolve().parents[1] / ".profile"
SELECTORS = {
    "OPENCLAW_1P_ACCOUNT": "fixture-account",
    "OPENCLAW_OPENAI_1P_ITEM": "fixture-openai",
    "OPENCLAW_ANTHROPIC_1P_ITEM": "fixture-anthropic",
}


class ProviderEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="provider-env-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.tools = self.root / "tools"
        self.home.mkdir()
        self.tools.mkdir()
        self.log = self.root / "op.log"
        op = self.tools / "op"
        op.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$OP_LOG"
[ "$1" = item ] && [ "$2" = get ] && [ "$4" = --account ] &&
  [ "$5" = fixture-account ] && [ "$6" = --fields ] && [ "$8" = --reveal ] || exit 2
[ "${FAIL_ITEM:-}" != "$3" ] || exit 73
case "$3:$7" in
  fixture-openai:notesPlain) printf fixture-openai-key ;;
  fixture-anthropic:password) printf fixture-anthropic-key ;;
  *) exit 2 ;;
esac
''')
        op.chmod(0o755)
        self.env = {"HOME": str(self.home), "USER": "fixture",
                    "PATH": f"{self.tools}:/usr/bin:/bin", "OP_LOG": str(self.log)}
        self.private = self.home / "GIT/_Perso/dotfiles-private"

    def selectors(self, root=None, missing=None):
        path = (root or self.private) / "openclaw/provider-env.sh"
        path.parent.mkdir(parents=True)
        path.write_text("".join(f"{key}={value}\n" for key, value in SELECTORS.items() if key != missing))
        path.chmod(0o600)
        return path

    def invoke(self, *, startup=False, env=None, profile=PROFILE):
        command = '. "$1"; result=0; '
        if not startup:
            command += 'openclaw_release_provider_env || result=$?; '
        command += 'printf "%s\\n" "$result" "${OPENAI_API_KEY-unset}" "${ANTHROPIC_API_KEY-unset}"'
        result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", command,
                                 "provider-fixture", str(profile)], env=self.env | (env or {}),
                                cwd=self.root, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout.splitlines()

    def test_startup_does_not_read_credentials(self):
        self.selectors()
        self.assertEqual(self.invoke(startup=True), ["0", "unset", "unset"])
        self.assertFalse(self.log.exists())

    def test_manual_default_and_custom_private_roots(self):
        self.selectors()
        self.assertEqual(self.invoke(), ["0", "fixture-openai-key", "fixture-anthropic-key"])
        custom = self.root / "custom private"
        self.selectors(custom)
        self.assertEqual(self.invoke(env={"DOTFILES_PRIVATE_DIR": str(custom)}),
                         ["0", "fixture-openai-key", "fixture-anthropic-key"])
        self.assertEqual(len(self.log.read_text().splitlines()), 4)

    def test_missing_file_or_selector_never_calls_op(self):
        self.assertEqual(self.invoke(), ["1", "unset", "unset"])
        for key in SELECTORS:
            with self.subTest(missing=key):
                root = self.root / key
                self.selectors(root, missing=key)
                self.assertEqual(self.invoke(env={"DOTFILES_PRIVATE_DIR": str(root)}),
                                 ["1", "unset", "unset"])
        self.assertFalse(self.log.exists())

    def test_failed_op_never_partially_exports_keys(self):
        self.selectors()
        for item in ("fixture-openai", "fixture-anthropic"):
            with self.subTest(item=item):
                self.assertEqual(self.invoke(env={"FAIL_ITEM": item}), ["1", "unset", "unset"])

    def test_old_shell_definition_uses_temporary_relative_link(self):
        self.selectors()
        legacy = self.private / "release/openclaw-provider-env.sh"
        legacy.parent.mkdir()
        legacy.symlink_to("../openclaw/provider-env.sh")
        old_profile = self.root / "old.profile"
        old_profile.write_text(PROFILE.read_text().replace(
            "/openclaw/provider-env.sh", "/release/openclaw-provider-env.sh"))
        self.assertEqual(self.invoke(profile=old_profile),
                         ["0", "fixture-openai-key", "fixture-anthropic-key"])


if __name__ == "__main__":
    unittest.main()
