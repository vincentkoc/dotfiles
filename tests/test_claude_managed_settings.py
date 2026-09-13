#!/usr/bin/env python3
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "bin" / "claude-managed-settings"
LOADER = importlib.machinery.SourceFileLoader("claude_managed_settings", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
MODULE = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(MODULE)


class ValidationTests(unittest.TestCase):
    def test_regular_file_wrong_owner_is_rejected(self):
        value = {
            "kind": "file",
            "uid": MODULE.os.getuid() + 1,
            "nlink": 1,
            "mode": 0o600,
            "payload": b"{}\n",
        }
        with self.assertRaises(MODULE.ManagedSettingsError) as raised:
            MODULE.validate_json_file(value, "target_incompatible")
        self.assertEqual(raised.exception.reason, "target_incompatible")

    def test_read_only_file_is_rejected(self):
        value = {
            "kind": "file",
            "uid": MODULE.os.getuid(),
            "nlink": 1,
            "mode": 0o400,
            "payload": b"{}\n",
        }
        with self.assertRaises(MODULE.ManagedSettingsError) as raised:
            MODULE.validate_json_file(value, "target_incompatible")
        self.assertEqual(raised.exception.reason, "target_incompatible")

    def test_group_or_world_writable_file_is_rejected(self):
        for mode in (0o620, 0o602, 0o666):
            with self.subTest(mode=oct(mode)):
                value = {
                    "kind": "file",
                    "uid": MODULE.os.getuid(),
                    "nlink": 1,
                    "mode": mode,
                    "payload": b"{}\n",
                }
                with self.assertRaises(MODULE.ManagedSettingsError) as raised:
                    MODULE.validate_json_file(value, "target_incompatible")
                self.assertEqual(raised.exception.reason, "target_incompatible")

    def test_managed_symlink_wrong_owner_is_rejected(self):
        value = {
            "kind": "symlink",
            "uid": MODULE.os.getuid() + 1,
            "nlink": 1,
            "target": MODULE.MANAGED_LINK,
        }
        with self.assertRaises(MODULE.ManagedSettingsError) as raised:
            MODULE.validate_symlink(value, MODULE.MANAGED_LINK)
        self.assertEqual(raised.exception.reason, "target_incompatible")


class StatsUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.claude = self.root / ".claude"
        self.claude.mkdir(mode=0o700)
        self.dotfiles = self.root / "dotfiles"
        self.baseline = b'{"hooks":{"baseline":true}}\n'
        baseline = patch.object(
            MODULE, "validate_baseline", return_value=({"kind": "file"}, self.baseline),
        )
        baseline.start()
        self.addCleanup(baseline.stop)
        self.original = {
            "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "example hook"}]}]},
            "env": {"OTHER_SETTING": "keep", "TOKENJUICE_STATS": "on"},
            "permissions": {"defaultMode": "default"},
            "enabledPlugins": {"example": True},
        }

    def target(self, managed=False, payload=None):
        name = MODULE.MANAGED_LINK if managed else "settings.json"
        target = self.claude / name
        target.write_bytes(
            payload if payload is not None else (json.dumps(self.original) + "\n").encode(),
        )
        target.chmod(0o640)
        if managed:
            (self.claude / "settings.json").symlink_to(MODULE.MANAGED_LINK)
        return target

    def apply(self, enabled=True):
        return MODULE.apply(self.dotfiles, self.claude, enabled)

    def assert_blocked(self, reason):
        with self.assertRaises(MODULE.ManagedSettingsError) as raised:
            self.apply()
        self.assertEqual(raised.exception.reason, reason)

    def test_regular_and_managed_preserve_other_fields_and_mode(self):
        for managed in (False, True):
            with self.subTest(managed=managed):
                target = self.target(managed)
                inode = target.stat().st_ino
                expected_files = set(self.claude.iterdir())
                self.assertEqual(self.apply(), "managed" if managed else "regular")
                expected = json.loads(json.dumps(self.original))
                expected["env"]["TOKENJUICE_STATS"] = "off"
                self.assertEqual(json.loads(target.read_bytes()), expected)
                self.assertEqual(target.stat().st_mode & 0o777, 0o640)
                self.assertNotEqual(target.stat().st_ino, inode)
                self.assertEqual(set(self.claude.iterdir()), expected_files)
                if managed:
                    self.assertEqual(
                        (self.claude / "settings.json").readlink(),
                        pathlib.Path(MODULE.MANAGED_LINK),
                    )
                    (self.claude / "settings.json").unlink()
                target.unlink()

    def test_default_is_byte_and_inode_preserving(self):
        for managed in (False, True):
            with self.subTest(managed=managed):
                target = self.target(managed)
                before = target.read_bytes(), target.stat().st_ino, set(self.claude.iterdir())
                self.apply(enabled=False)
                self.assertEqual(
                    (target.read_bytes(), target.stat().st_ino, set(self.claude.iterdir())), before,
                )
                if managed:
                    (self.claude / "settings.json").unlink()
                target.unlink()

    def test_already_off_does_not_stage_or_change_inode(self):
        for managed in (False, True):
            with self.subTest(managed=managed):
                target = self.target(
                    managed, b'{ "env": { "TOKENJUICE_STATS": "off" }, "hooks": {} }\n',
                )
                before = target.read_bytes(), target.stat().st_ino, set(self.claude.iterdir())
                with patch.object(MODULE, "write_all") as write:
                    self.apply()
                write.assert_not_called()
                self.assertEqual(
                    (target.read_bytes(), target.stat().st_ino, set(self.claude.iterdir())), before,
                )
                if managed:
                    (self.claude / "settings.json").unlink()
                target.unlink()

    def test_absent_and_legacy_receive_policy_without_changing_baseline(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                settings = self.claude / "settings.json"
                if legacy:
                    settings.symlink_to(self.dotfiles / ".claude/settings.json")
                self.assertEqual(self.apply(), "migrated" if legacy else "created")
                self.assertEqual(settings.readlink(), pathlib.Path(MODULE.MANAGED_LINK))
                expected = json.loads(self.baseline)
                expected["env"] = {"TOKENJUICE_STATS": "off"}
                self.assertEqual(json.loads(settings.read_bytes()), expected)
                self.assertEqual(
                    {item.name for item in self.claude.iterdir()},
                    {"settings.json", MODULE.MANAGED_LINK},
                )
                settings.unlink()
                (self.claude / MODULE.MANAGED_LINK).unlink()

    def test_invalid_env_and_duplicate_keys_do_not_mutate(self):
        for payload, reason in (
            (b'{"env": null}', "invalid_env"),
            (b'{"env": []}', "invalid_env"),
            (b'{"env": "on"}', "invalid_env"),
            (b'{"env": false}', "invalid_env"),
            (b'{"env": {}, "env": {}}', "duplicate_json_key"),
            (b'{"env": {"TOKENJUICE_STATS": "off", "TOKENJUICE_STATS": "on"}}', "duplicate_json_key"),
            (b'{"hooks": {"command": "a", "command": "b"}}', "duplicate_json_key"),
            (b'{"example": NaN}', "invalid_json"),
        ):
            with self.subTest(payload=payload):
                target = self.target(payload=payload)
                inode = target.stat().st_ino
                self.assert_blocked(reason)
                self.assertEqual(target.read_bytes(), payload)
                self.assertEqual(target.stat().st_ino, inode)
                self.assertEqual(list(self.claude.iterdir()), [target])
                target.unlink()

    def test_any_existing_recovery_residue_is_preserved(self):
        target = self.target()
        original = target.read_bytes()
        for name in (MODULE.STATS_SIBLING, MODULE.SEED_SIBLING, MODULE.LINK_SIBLING):
            with self.subTest(name=name):
                residue = self.claude / name
                residue.write_bytes(b"operator recovery\n")
                self.assert_blocked("recovery_required")
                self.assertEqual(residue.read_bytes(), b"operator recovery\n")
                self.assertEqual(target.read_bytes(), original)
                residue.unlink()

    def test_unsafe_parent_mode_is_rejected_before_staging(self):
        target = self.target()
        self.claude.chmod(0o777)
        self.assert_blocked("parent_incompatible")
        self.assertEqual(list(self.claude.iterdir()), [target])

    def test_prepublication_in_place_edit_is_preserved(self):
        target = self.target()
        concurrent = b'{"env":{"OTHER_SETTING":"concurrent"}}\n'

        def edit(name):
            if name == "before_stats_publish":
                target.write_bytes(concurrent)

        with patch.object(MODULE, "failpoint", side_effect=edit):
            self.assert_blocked("target_changed")
        self.assertEqual(target.read_bytes(), concurrent)
        self.assertTrue((self.claude / MODULE.STATS_SIBLING).is_file())

    def test_original_changed_during_exchange_is_retained(self):
        target = self.target()
        concurrent = b'{"hooks":{"concurrent":true}}\n'
        rename = MODULE.rename_atomic

        def exchange(*args):
            target.write_bytes(concurrent)
            rename(*args)

        with patch.object(MODULE, "rename_atomic", side_effect=exchange):
            self.assert_blocked("target_changed")
        self.assertEqual((self.claude / MODULE.STATS_SIBLING).read_bytes(), concurrent)
        self.assertEqual(json.loads(target.read_bytes())["env"]["TOKENJUICE_STATS"], "off")

    def test_published_and_held_original_edits_never_trigger_rollback_or_cleanup(self):
        for changed_name in ("settings.json", MODULE.STATS_SIBLING):
            with self.subTest(changed_name=changed_name):
                target = self.target()
                original = target.read_bytes()
                concurrent = b'{"hooks":{"concurrent":true}}\n'

                def edit(name):
                    if name == "before_stats_cleanup":
                        (self.claude / changed_name).write_bytes(concurrent)

                with patch.object(MODULE, "failpoint", side_effect=edit):
                    self.assert_blocked("target_changed")
                stage = self.claude / MODULE.STATS_SIBLING
                self.assertEqual((self.claude / changed_name).read_bytes(), concurrent)
                if changed_name == "settings.json":
                    self.assertEqual(stage.read_bytes(), original)
                stage.unlink()
                target.unlink()

    def test_managed_link_retarget_during_update_is_preserved(self):
        target = self.target(managed=True)
        original = target.read_bytes()
        settings = self.claude / "settings.json"

        def retarget(name):
            if name == "before_stats_publish":
                settings.unlink()
                settings.symlink_to("operator.json")

        with patch.object(MODULE, "failpoint", side_effect=retarget):
            self.assert_blocked("target_changed")
        self.assertEqual(settings.readlink(), pathlib.Path("operator.json"))
        self.assertEqual(target.read_bytes(), original)
        self.assertTrue((self.claude / MODULE.STATS_SIBLING).is_file())

    def test_parent_retarget_during_update_keeps_both_directories(self):
        target = self.target()
        original = target.read_bytes()
        retained = self.root / "retained-claude"

        def retarget(name):
            if name == "before_stats_publish":
                self.claude.rename(retained)
                self.claude.mkdir(mode=0o700)
                (self.claude / "settings.json").write_bytes(b'{"operator":true}\n')

        with patch.object(MODULE, "failpoint", side_effect=retarget):
            self.assert_blocked("parent_retargeted")
        self.assertEqual((retained / target.name).read_bytes(), original)
        self.assertTrue((retained / MODULE.STATS_SIBLING).is_file())
        self.assertEqual((self.claude / target.name).read_bytes(), b'{"operator":true}\n')

    def test_stage_replaced_before_exchange_is_not_adopted(self):
        target = self.target()
        original = target.read_bytes()
        stage = self.claude / MODULE.STATS_SIBLING
        retained = self.claude / "retained-stage"

        def replace(name):
            if name == "before_stats_publish":
                stage.rename(retained)
                stage.write_bytes(b'{"operator":true}\n')

        with patch.object(MODULE, "failpoint", side_effect=replace):
            self.assert_blocked("target_changed")
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(stage.read_bytes(), b'{"operator":true}\n')
        self.assertTrue(retained.is_file())


if __name__ == "__main__":
    unittest.main()
