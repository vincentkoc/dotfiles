#!/usr/bin/env python3

import json
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMAND = ROOT / "bin" / "codex-hooks"
DOTFILES = ROOT / ".codex" / "hooks.json"


class CodexHooksTest(unittest.TestCase):
    def register(self, target, integration_id, fragment, *extra):
        result = subprocess.run(
            [
                str(COMMAND),
                "register",
                "--target",
                str(target),
                "--integration-id",
                integration_id,
                "--fragment",
                str(fragment),
                *(str(value) for value in extra),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def unregister(self, target, integration_id):
        result = subprocess.run(
            [
                str(COMMAND),
                "unregister",
                "--target",
                str(target),
                "--integration-id",
                integration_id,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_install_order_is_stable_and_unknown_hooks_survive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            custom = {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {"type": "command", "command": "custom-stop"}
                            ]
                        }
                    ]
                },
                "unknownTopLevel": {"preserved": True},
            }
            tokenjuice = root / "tokenjuice.json"
            tokenjuice.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PostToolUse": [
                                {
                                    "matcher": ".*",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "tokenjuice hook",
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                )
            )

            outputs = []
            for order in ("dotfiles-first", "tokenjuice-first"):
                home = root / order
                home.mkdir()
                target = home / "hooks.json"
                target.write_text(json.dumps(custom))
                registrations = [
                    ("dotfiles.core", DOTFILES),
                    ("tokenjuice.post-tool-use", tokenjuice),
                ]
                if order == "tokenjuice-first":
                    registrations.reverse()
                for integration_id, fragment in registrations:
                    self.register(target, integration_id, fragment)
                outputs.append(target.read_bytes())
                payload = json.loads(target.read_text())
                self.assertEqual(payload["unknownTopLevel"], {"preserved": True})
                commands = json.dumps(payload)
                self.assertIn("custom-stop", commands)
                self.assertIn("tokenjuice hook", commands)
                self.assertIn("agent-attn-set", commands)

            self.assertEqual(outputs[0], outputs[1])

    def test_repeat_register_and_external_unknown_edit_do_not_duplicate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            target = root / "hooks.json"
            target.write_text('{"hooks":{}}\n')
            self.register(target, "dotfiles.core", DOTFILES)
            first = json.loads(target.read_text())
            first["hooks"]["Stop"].append(
                {"hooks": [{"type": "command", "command": "later custom hook"}]}
            )
            target.write_text(json.dumps(first))
            self.register(target, "dotfiles.core", DOTFILES)
            rendered = target.read_text()
            self.assertEqual(rendered.count("later custom hook"), 1)
            self.assertEqual(rendered.count("$HOME/bin/agent-attn-set waiting codex"), 1)

    def test_owned_symlink_is_adopted_without_duplicate_groups(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            target = root / "hooks.json"
            target.symlink_to(DOTFILES)
            self.register(
                target,
                "dotfiles.core",
                DOTFILES,
                "--owned-source",
                DOTFILES,
            )
            self.assertFalse(target.is_symlink())
            rendered = target.read_text()
            self.assertEqual(rendered.count("$HOME/bin/agent-attn-set waiting codex"), 1)

    def test_identical_user_group_remains_user_owned_after_unregister(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            target = root / "hooks.json"
            fragment = root / "shared.json"
            shared = {
                "hooks": [
                    {"type": "command", "command": "shared user command"}
                ]
            }
            target.write_text(json.dumps({"hooks": {"Stop": [shared]}}))
            fragment.write_text(json.dumps({"hooks": {"Stop": [shared]}}))
            self.register(target, "shared.integration", fragment)

            payload = json.loads(target.read_text())
            payload["hooks"]["Stop"].append(
                {"hooks": [{"type": "command", "command": "later user command"}]}
            )
            target.write_text(json.dumps(payload))
            self.unregister(target, "shared.integration")
            rendered = target.read_text()
            self.assertEqual(rendered.count("shared user command"), 1)
            self.assertEqual(rendered.count("later user command"), 1)

    def test_owned_source_migrates_both_install_orders_and_unregisters(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            tokenjuice = root / "tokenjuice.json"
            token_group = {
                "matcher": ".*",
                "hooks": [{"type": "command", "command": "tokenjuice hook"}],
            }
            tokenjuice.write_text(
                json.dumps({"hooks": {"PostToolUse": [token_group]}})
            )

            for order in ("tokenjuice-first", "dotfiles-first"):
                home = root / order
                home.mkdir()
                target = home / "hooks.json"
                target.write_text(
                    json.dumps(
                        {
                            "hooks": {
                                "Stop": [
                                    {
                                        "hooks": [
                                            {
                                                "type": "command",
                                                "command": "custom user hook",
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    )
                )
                if order == "tokenjuice-first":
                    payload = json.loads(target.read_text())
                    payload["hooks"]["PostToolUse"] = [token_group]
                    target.write_text(json.dumps(payload))

                self.register(
                    target,
                    "dotfiles.core",
                    DOTFILES,
                    "--owned-source",
                    DOTFILES,
                )
                if order == "dotfiles-first":
                    payload = json.loads(target.read_text())
                    payload["hooks"]["PostToolUse"] = [token_group]
                    target.write_text(json.dumps(payload))

                self.register(
                    target,
                    "tokenjuice.post-tool-use",
                    tokenjuice,
                    "--owned-source",
                    tokenjuice,
                )
                self.register(
                    target,
                    "dotfiles.core",
                    DOTFILES,
                    "--owned-source",
                    DOTFILES,
                )
                self.register(
                    target,
                    "tokenjuice.post-tool-use",
                    tokenjuice,
                    "--owned-source",
                    tokenjuice,
                )
                self.unregister(target, "tokenjuice.post-tool-use")

                rendered = target.read_text()
                self.assertNotIn("tokenjuice hook", rendered)
                self.assertIn("custom user hook", rendered)
                self.assertIn("agent-attn-set", rendered)


if __name__ == "__main__":
    unittest.main()
