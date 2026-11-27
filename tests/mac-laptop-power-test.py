#!/usr/bin/env python3
"""Exercise laptop power policy with fake native commands only."""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/mac-laptop-power"
loader = importlib.machinery.SourceFileLoader("mac_laptop_power", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
power = importlib.util.module_from_spec(spec)
loader.exec_module(power)

LAPTOP = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged\n"
CONFIG = """Battery Power:
 sleep                1
 displaysleep         2
 womp                 0
 hibernatemode        3
AC Power:
 Sleep On Power Button 1
 sleep                1
 displaysleep         10
 womp                 1
 hibernatemode        3
 hibernatefile        /var/vm/sleepimage
"""
APPLIED = CONFIG.replace(" sleep                1\n displaysleep         10",
                         " sleep                0\n displaysleep         10")


class PowerTests(unittest.TestCase):
    def invoke(self, args=(), platform="darwin", battery=LAPTOP,
               before=CONFIG, after=APPLIED, failure=None):
        commands = []
        configs = iter((before, after))

        def run(command, **kwargs):
            commands.append(command)
            if command == [power.PMSET, "-g", "batt"]:
                return subprocess.CompletedProcess(command, 0, stdout=battery)
            if command == [power.PMSET, "-g", "custom"]:
                return subprocess.CompletedProcess(command, 0, stdout=next(configs))
            if command == ["/usr/bin/sudo", power.PMSET, "-c", "sleep", "0"]:
                if failure:
                    raise failure
                return subprocess.CompletedProcess(command, 0)
            self.fail("unexpected native command: " + repr(command))

        output = io.StringIO()
        with patch.object(power.sys, "platform", platform), \
                patch.object(power.subprocess, "run", side_effect=run), \
                contextlib.redirect_stdout(output):
            code = power.main([*args, "--json"])
        return code, json.loads(output.getvalue()), commands

    def test_default_check_reports_drift_without_write(self):
        code, report, commands = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["ac_sleep"], 1)
        self.assertFalse(report["change_attempted"])
        self.assertEqual(commands, [[power.PMSET, "-g", "batt"],
                                    [power.PMSET, "-g", "custom"]])

    def test_apply_changes_only_ac_sleep_and_verifies_it(self):
        code, report, commands = self.invoke(["--apply"])
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "applied")
        self.assertTrue(report["changed"])
        self.assertEqual(commands[2], ["/usr/bin/sudo", power.PMSET, "-c", "sleep", "0"])
        self.assertEqual(commands[3], [power.PMSET, "-g", "custom"])
        self.assertEqual(power.settings(APPLIED)["Battery Power"],
                         power.settings(CONFIG)["Battery Power"])

    def test_already_matching_apply_does_not_elevate(self):
        code, report, commands = self.invoke(["--apply"], before=APPLIED)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "match")
        self.assertFalse(report["changed"])
        self.assertEqual(len(commands), 2)

    def test_other_platform_is_noop_before_native_access(self):
        code, report, commands = self.invoke(["--apply"], platform="linux")
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "not-applicable")
        self.assertEqual(commands, [])

    def test_desktop_and_external_ups_do_not_change_power(self):
        for battery in ("Now drawing from 'AC Power'\n",
                        "Now drawing from 'UPS Power'\n -UPS-0\t100%\n"):
            with self.subTest(battery=battery):
                code, report, commands = self.invoke(["--apply"], battery=battery)
                self.assertEqual(code, 0)
                self.assertEqual(report["status"], "not-applicable")
                self.assertEqual(len(commands), 1)

    def test_laptop_on_battery_still_updates_only_ac_policy(self):
        code, report, commands = self.invoke(
            ["--apply"], battery=LAPTOP.replace("'AC Power'", "'Battery Power'"))
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "applied")
        self.assertEqual(commands[2][-4:], [power.PMSET, "-c", "sleep", "0"])

    def test_malformed_battery_output_fails_without_write(self):
        for battery in ("", "unknown", "Now drawing from 'Battery Power'\n"):
            with self.subTest(battery=battery):
                code, report, commands = self.invoke(["--apply"], battery=battery)
                self.assertEqual(code, 2)
                self.assertEqual(report["status"], "error")
                self.assertFalse(report["change_attempted"])
                self.assertEqual(len(commands), 1)

    def test_malformed_settings_fail_before_write(self):
        for before in ("", "AC Power:\n sleep invalid\n", CONFIG + "AC Power:\n"):
            with self.subTest(before=before):
                code, report, commands = self.invoke(["--apply"], before=before)
                self.assertEqual(code, 2)
                self.assertFalse(report["change_attempted"])
                self.assertEqual(len(commands), 2)

    def test_failed_sudo_is_error_with_unknown_result(self):
        code, report, commands = self.invoke(
            ["--apply"], failure=subprocess.CalledProcessError(1, "sudo"))
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "error")
        self.assertTrue(report["change_attempted"])
        self.assertIsNone(report["changed"])
        self.assertEqual(len(commands), 3)

    def test_unchanged_readback_does_not_claim_applied(self):
        code, report, commands = self.invoke(["--apply"], after=CONFIG)
        self.assertEqual(code, 2)
        self.assertFalse(report["changed"])
        self.assertEqual(len(commands), 4)

    def test_changed_battery_or_display_policy_is_error_without_rollback(self):
        for after in (APPLIED.replace("displaysleep         10", "displaysleep         0"),
                      APPLIED.replace("sleep                1", "sleep                0")):
            with self.subTest(after=after):
                code, report, commands = self.invoke(["--apply"], after=after)
                self.assertEqual(code, 2)
                self.assertTrue(report["changed"])
                self.assertIn("AC-only", report["error"])
                self.assertEqual(len(commands), 4)

    def test_parser_preserves_multiword_keys_and_paths(self):
        values = power.settings(CONFIG)
        self.assertEqual(values["AC Power"]["Sleep On Power Button"], "1")
        self.assertEqual(values["AC Power"]["hibernatefile"], "/var/vm/sleepimage")

    def test_help_does_not_access_native_power(self):
        with patch.object(power.subprocess, "run") as run, \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as caught:
            power.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        run.assert_not_called()

    def test_installer_calls_policy_without_elevating_script(self):
        with tempfile.TemporaryDirectory(prefix="laptop-power-test-") as directory:
            fixture = Path(directory)
            (fixture / "bin").mkdir()
            (fixture / "install.sh").write_text((ROOT / "install.sh").read_text())
            helper = fixture / "bin/mac-laptop-power"
            helper.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\nexit 37\n')
            helper.chmod(0o755)
            for platform, expected in (("Darwin", 37), ("Linux", 0)):
                result = subprocess.run(
                    ["/bin/bash", "-c", 'source "$1/install.sh"; uname() { echo "$TEST_PLATFORM"; }; '
                     'info() { :; }; setup_mac_laptop_power', "_", str(fixture)],
                    capture_output=True, text=True, timeout=10,
                    env={"PATH": "/usr/bin:/bin", "HOME": directory, "TEST_PLATFORM": platform})
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(result.stdout, "--apply\n" if platform == "Darwin" else "")


if __name__ == "__main__":
    unittest.main()
