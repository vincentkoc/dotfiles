#!/usr/bin/env python3
"""Source only the Linux local config on fixture-owned private tmux servers."""

import importlib.machinery
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader(
    "tmux_fixture", str(ROOT / "tests/agent-attn-tmux-test.py")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
fixture = importlib.util.module_from_spec(spec)
loader.exec_module(fixture)
DEFAULTS = [
    "DISPLAY", "KRB5CCNAME", "SSH_ASKPASS", "SSH_AUTH_SOCK", "SSH_AGENT_PID",
    "SSH_CONNECTION", "WINDOWID", "XAUTHORITY",
]


class LinuxConfigTests(fixture.TmuxFixture):
    def test_global_environment_union_and_local_only_bindings(self):
        local = self.root / ".tmux.conf.local"
        local.symlink_to(ROOT / "profiles/linux-server/tmux.conf.local")
        session_override = ["SESSION_ONLY", "SSH_CONNECTION"]
        self.tmux("set-option", "-t", "fixture", "update-environment", " ".join(session_override))
        identity_format = "#{pane_id}|#{pane_pid}|#{pane_active}|#{window_id}"
        identities = self.tmux("list-panes", "-a", "-F", identity_format)
        cases = (
            ["ITERM2_DISABLE_SHELL_INTEGRATION", "ITERM_DISABLE_PROMPT_MARK"],
            DEFAULTS,
            ["MY_CUSTOM_ENV", "SSH_AUTH_SOCK", "DISPLAY"],
        )
        for initial in cases:
            with self.subTest(initial=initial):
                self.tmux("set-option", "-g", "update-environment", " ".join(initial))
                expected = initial + [entry for entry in DEFAULTS if entry not in initial]
                for _ in range(2):
                    self.tmux("source-file", str(local))
                    actual = self.tmux("show-options", "-gqv", "update-environment").splitlines()
                    self.assertEqual(actual, expected)
                    self.assertEqual(
                        self.tmux("show-options", "-t", "fixture", "-qv", "update-environment").splitlines(),
                        session_override,
                    )
                self.assertEqual(self.tmux("list-panes", "-a", "-F", identity_format), identities)

        reload_binding = self.tmux("list-keys", "-T", "prefix", "r")
        edit_binding = self.tmux("list-keys", "-T", "prefix", "e")
        repair_binding = self.tmux("list-keys", "-T", "prefix", "R")
        self.assertIn("source-file", reload_binding)
        self.assertIn(".tmux.conf.local", reload_binding)
        self.assertIn("source-file", edit_binding)
        self.assertIn(".tmux.conf.local", edit_binding)
        self.assertNotRegex(reload_binding + edit_binding, r"\.tmux\.conf[\"' ;]")
        self.assertIn("run-shell", repair_binding)
        self.assertIn("tmux-pane-repair", repair_binding)
        self.assertIn("request", repair_binding)
        self.assertNotIn("send-keys", repair_binding)


if __name__ == "__main__":
    unittest.main()
