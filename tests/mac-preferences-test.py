#!/usr/bin/env python3
"""Portable preferences tests: fake storage only; never change host settings."""

import contextlib
import configparser
import copy
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/mac-preferences"
PROFILE = ROOT / "profiles/macos/personal-preferences.json"
loader = importlib.machinery.SourceFileLoader("mac_preferences", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
preferences = importlib.util.module_from_spec(spec)
loader.exec_module(preferences)


class MemoryPreferences:
    def __init__(self, values):
        self.values = copy.deepcopy(values)
        self.writes = []
        self.syncs = []
        self.reads = {}
        self.on_read = lambda pair, count: None
        self.after_write = lambda pair: None

    def read(self, pair):
        self.reads[pair] = self.reads.get(pair, 0) + 1
        self.on_read(pair, self.reads[pair])
        return copy.deepcopy(self.values[pair]) if pair in self.values else preferences.MISSING

    def write(self, pair, value):
        self.writes.append(pair)
        self.values[pair] = copy.deepcopy(value)
        self.after_write(pair)

    def sync(self, domain):
        self.syncs.append(domain)

    def refresh(self, domain):
        pass

    def close(self):
        pass


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.document = json.loads(PROFILE.read_text())
        self.profile = preferences.validate_profile(self.document)
        self.backend = MemoryPreferences(self.profile)

    def reconcile(self, apply=False, input_guard=lambda: None):
        return preferences.reconcile(self.profile, self.backend, apply, input_guard)

    def test_exact_bundled_profile_and_idempotent_apply(self):
        self.assertEqual(len(self.profile), 70)
        self.assertEqual(set(self.profile), preferences.ALLOWED_KEYS)
        result = self.reconcile(apply=True)
        self.assertTrue(result["stored_preferences_match"])
        self.assertFalse(result["loaded_ui_verified"])
        self.assertFalse(result["restarted_apps"])
        self.assertEqual(self.backend.writes, [])
        self.assertEqual(self.backend.syncs, [])

    def test_default_cli_check_reports_drift_without_writing(self):
        pair = ("NSGlobalDomain", "com.apple.swipescrolldirection")
        self.backend.values[pair] = True
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = preferences.main(["--json"], lambda: self.backend, lambda: None)
        report = json.loads(output.getvalue())
        self.assertEqual(result, 1)
        self.assertEqual(report["mode"], "check")
        self.assertEqual(report["differences"], [{"domain": pair[0], "key": pair[1]}])
        self.assertEqual(self.backend.writes, [])

    def test_typed_boolean_drift_is_not_integer_equality(self):
        pair = ("NSGlobalDomain", "com.apple.swipescrolldirection")
        self.backend.values[pair] = 0
        self.assertFalse(preferences.same(False, 0))
        self.assertFalse(self.reconcile()["stored_preferences_match"])
        self.reconcile(apply=True)
        self.assertEqual(self.backend.writes, [pair])
        self.assertIs(self.backend.values[pair], False)

    def test_missing_values_are_created_without_touching_unrelated_preferences(self):
        pair = ("com.apple.screencapture", "show-thumbnail")
        del self.backend.values[pair]
        unrelated = ("com.example.other", "unchanged")
        self.backend.values[unrelated] = {"opaque": b"public fixture"}
        self.reconcile(apply=True)
        self.assertEqual(self.backend.writes, [pair])
        self.assertEqual(self.backend.values[unrelated], {"opaque": b"public fixture"})

    def test_trackpad_types_and_hot_corner_mappings_are_preserved(self):
        boolean = (preferences.TRACKPAD_DOMAINS[0], "TrackpadRightClick")
        numeric = (preferences.TRACKPAD_DOMAINS[0], "Clicking")
        self.assertIs(self.profile[boolean], True)
        self.assertIs(type(self.profile[numeric]), int)
        self.backend.values[boolean] = 1
        self.backend.values[numeric] = False
        self.reconcile(apply=True)
        self.assertEqual(set(self.backend.writes), {boolean, numeric})
        self.assertIs(self.backend.values[boolean], True)
        self.assertIs(type(self.backend.values[numeric]), int)
        self.assertEqual([self.profile[("com.apple.dock", "wvous-" + corner + "-corner")]
                          for corner in ("tl", "tr", "bl", "br")], [12, 0, 4, 13])

    def test_device_metadata_and_unaudited_trackpad_keys_are_rejected(self):
        for key in ("version", "UserPreferences", "DeviceIdentifier", "ForceSuppressed"):
            document = copy.deepcopy(self.document)
            row = next(row for row in document["preferences"]
                       if row["domain"] == preferences.TRACKPAD_DOMAINS[1])
            row["key"] = key
            with self.subTest(key=key), self.assertRaises(preferences.PreferenceError):
                preferences.validate_profile(document)

    def test_numeric_gesture_and_corner_values_reject_wrong_types_and_scope(self):
        cases = [(preferences.TRACKPAD_DOMAINS[0], "Clicking", value)
                 for value in (False, -1, 2147483648, 1.0)]
        cases += [("com.apple.dock", "wvous-tl-corner", 99),
                  ("com.apple.dock", "wvous-tr-modifier", 131072)]
        for domain, key, value in cases:
            document = copy.deepcopy(self.document)
            row = next(row for row in document["preferences"]
                       if (row["domain"], row["key"]) == (domain, key))
            row["value"] = value
            with self.subTest(key=key, value=value), self.assertRaises(preferences.PreferenceError):
                preferences.validate_profile(document)

    def test_auth_domain_is_rejected_before_backend_creation(self):
        self.document["preferences"][0]["domain"] = "com.apple.security"
        opened = []
        with tempfile.TemporaryDirectory(prefix="mac-preferences-test-") as temporary:
            path = Path(temporary) / "profile.json"
            path.write_text(json.dumps(self.document))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = preferences.main(["--profile", str(path), "--json"],
                                          lambda: opened.append(True), lambda: None)
        self.assertEqual(result, 2)
        self.assertIn("allowlist", json.loads(output.getvalue())["error"])
        self.assertEqual(opened, [])

    def test_schema_rejects_extra_missing_duplicate_and_wrong_type_entries(self):
        variants = []
        extra = copy.deepcopy(self.document)
        extra["unknown"] = True
        variants.append(extra)
        missing = copy.deepcopy(self.document)
        missing["preferences"].pop()
        variants.append(missing)
        duplicate = copy.deepcopy(self.document)
        duplicate["preferences"][-1] = duplicate["preferences"][0]
        variants.append(duplicate)
        integer_boolean = copy.deepcopy(self.document)
        integer_boolean["preferences"][1]["value"] = 0
        variants.append(integer_boolean)
        boolean_version = copy.deepcopy(self.document)
        boolean_version["schema_version"] = True
        variants.append(boolean_version)
        null_value = copy.deepcopy(self.document)
        null_value["preferences"][9]["value"] = None
        variants.append(null_value)
        for document in variants:
            with self.subTest(document=document):
                with self.assertRaises(preferences.PreferenceError):
                    preferences.validate_profile(document)
        with self.assertRaises(preferences.PreferenceError):
            json.loads('{"schema_version":1,"schema_version":1}',
                       object_pairs_hook=preferences.unique_object)

    def test_external_input_method_is_not_an_allowed_profile_value(self):
        for row in self.document["preferences"]:
            if row["key"] == "AppleEnabledInputSources":
                row["value"].append({"Bundle ID": "com.example.external-input",
                                     "InputSourceKind": "Keyboard Input Method"})
        with self.assertRaises(preferences.PreferenceError):
            preferences.validate_profile(self.document)

    def test_builtin_input_preflight_failure_prevents_every_write(self):
        self.backend.values.clear()
        def unavailable():
            raise preferences.PreferenceError("input-method bundle missing")
        with self.assertRaisesRegex(preferences.PreferenceError, "missing"):
            self.reconcile(apply=True, input_guard=unavailable)
        self.assertEqual(self.backend.writes, [])
        self.assertEqual(self.backend.syncs, [])

    def test_hotkey_merge_preserves_unknown_ids_and_nested_fields(self):
        hotkeys = self.backend.values[preferences.HOTKEYS]
        hotkeys["60"]["enabled"] = False
        hotkeys["61"]["enabled"] = False
        hotkeys["60"]["opaque"] = {"keep": [1, 2]}
        hotkeys["60"]["value"]["future"] = "preserved"
        hotkeys["176"]["another"] = b"public fixture"
        hotkeys["999"] = {"enabled": True, "value": {"future": [3, 4]}}
        before = copy.deepcopy(hotkeys)
        self.reconcile(apply=True)
        after = self.backend.values[preferences.HOTKEYS]
        self.assertEqual(self.backend.writes, [preferences.HOTKEYS])
        self.assertTrue(after["60"]["enabled"])
        self.assertTrue(after["61"]["enabled"])
        self.assertEqual(after["60"]["opaque"], before["60"]["opaque"])
        self.assertEqual(after["60"]["value"], before["60"]["value"])
        self.assertEqual(after["176"], before["176"])
        self.assertEqual(after["999"], before["999"])

    def test_different_shortcut_binding_stops_full_preflight(self):
        self.backend.values[("NSGlobalDomain", "AppleAquaColorVariant")] = 6
        self.backend.values[preferences.HOTKEYS]["60"]["value"]["parameters"] = [1, 2, 3]
        with self.assertRaisesRegex(preferences.PreferenceError, "different binding"):
            self.reconcile(apply=True)
        self.assertEqual(self.backend.writes, [])

    def test_concurrent_change_during_all_key_preflight_stops_before_writes(self):
        first, last = next(iter(self.profile)), next(reversed(self.profile))
        self.backend.values[first] = 6
        def change(pair, count):
            if pair == last and count == 2:
                self.backend.values[last] = "com.example.concurrent-layout"
        self.backend.on_read = change
        with self.assertRaisesRegex(preferences.PreferenceError, "during preflight"):
            self.reconcile(apply=True)
        self.assertEqual(self.backend.writes, [])

    def test_per_key_cas_preserves_later_concurrent_edit_and_reports_partial_write(self):
        first, second = list(self.profile)[:2]
        self.backend.values[first] = 6
        self.backend.values[second] = True
        def change(pair):
            if pair == first:
                self.backend.values[second] = "concurrent"
        self.backend.after_write = change
        with self.assertRaises(preferences.PreferenceError) as caught:
            self.reconcile(apply=True)
        self.assertIn("before write", str(caught.exception))
        self.assertEqual(caught.exception.written, [first])
        self.assertEqual(self.backend.writes, [first])
        self.assertEqual(self.backend.values[second], "concurrent")

    def test_failed_readback_is_not_reported_as_success(self):
        pair = next(iter(self.profile))
        self.backend.values[pair] = 6
        self.backend.after_write = lambda changed: self.backend.values.update({changed: 6})
        with self.assertRaisesRegex(preferences.PreferenceError, "readback differs") as caught:
            self.reconcile(apply=True)
        self.assertEqual(caught.exception.written, [pair])

    def test_cached_native_reads_refresh_before_preflight_and_each_key(self):
        class CachedPreferences(MemoryPreferences):
            def __init__(self, values):
                super().__init__(values)
                self.disk = copy.deepcopy(values)

            def refresh(self, domain):
                for pair, value in self.disk.items():
                    if pair[0] == domain:
                        self.values[pair] = copy.deepcopy(value)

            def sync(self, domain):
                super().sync(domain)
                for pair, value in self.values.items():
                    if pair[0] == domain:
                        self.disk[pair] = copy.deepcopy(value)

        first = next(iter(self.profile))
        for when in ("during_preflight", "after_first_write", "before_final_readback"):
            with self.subTest(when=when):
                initial = copy.deepcopy(self.profile)
                initial[first] = 6
                if when != "before_final_readback":
                    initial[preferences.HOTKEYS]["60"]["enabled"] = False
                backend = CachedPreferences(initial)
                unknown = {"enabled": True, "value": {"future": [3, 4]}}
                def external_change():
                    backend.disk[preferences.HOTKEYS]["999"] = unknown
                if when == "during_preflight":
                    guard = external_change
                else:
                    guard = lambda: None
                    backend.after_write = lambda pair: external_change() if pair == first else None
                with self.assertRaises(preferences.PreferenceError) as caught:
                    preferences.reconcile(self.profile, backend, True, guard)
                self.assertEqual(backend.disk[preferences.HOTKEYS]["999"], unknown)
                self.assertNotIn(preferences.HOTKEYS, backend.writes)
                self.assertEqual(caught.exception.written,
                                 [] if when == "during_preflight" else [first])

    def test_global_domain_uses_any_application_not_a_literal_domain_string(self):
        backend = object.__new__(preferences.NativePreferences)
        backend.any_application = "native-any-application"
        backend.reference = lambda text: "string:" + text
        self.assertEqual(backend.domain("NSGlobalDomain"), "native-any-application")
        self.assertEqual(backend.domain("com.apple.finder"), "string:com.apple.finder")

    def test_help_never_initializes_preferences(self):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--help"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--apply", result.stdout)
        self.assertIn("default", result.stdout)


class SelectiveMackupTests(unittest.TestCase):
    def test_mackup_selects_only_three_literal_non_auth_files(self):
        configuration = configparser.ConfigParser(allow_no_value=True, interpolation=None)
        configuration.read(ROOT / ".mackup.cfg")
        self.assertEqual(configuration.sections(), ["storage", "applications_to_sync"])
        self.assertEqual(dict(configuration["storage"]), {"engine": "icloud", "directory": "dotfiles"})
        self.assertEqual(dict(configuration["applications_to_sync"]), {"portable-tool-preferences": None})
        application = configparser.ConfigParser(allow_no_value=True, interpolation=None)
        application.optionxform = str
        application.read(ROOT / ".mackup/portable-tool-preferences.cfg")
        self.assertEqual(application.sections(), ["application", "configuration_files"])
        self.assertEqual(dict(application["configuration_files"]), {
            ".ripgreprc": None,
            ".config/gh/config.yml": None,
            "Library/Application Support/Sublime Text/Packages/User/Preferences.sublime-settings": None,
        })


if __name__ == "__main__":
    unittest.main()
