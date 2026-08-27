#!/usr/bin/env python3
from __future__ import annotations

import importlib.machinery
import importlib.util
import pathlib
import unittest


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


if __name__ == "__main__":
    unittest.main()
