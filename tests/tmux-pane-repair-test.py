#!/usr/bin/env python3
"""Pure mocked tests: no tmux process or terminal is touched."""

import base64
import copy
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import stat
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("repair", str(ROOT / "bin/tmux-pane-repair"))
spec = importlib.util.spec_from_loader(loader.name, loader)
repair = importlib.util.module_from_spec(spec)
loader.exec_module(repair)


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmux = ["/fixture/tmux", "-N", "-S", "/fixture/socket"]
        self.target = {
            "pane": "%7", "process": {"pid": 123, "start": "original", "command": "bash"},
            "server": {"pid": 42}, "socket_id": [1, 2], "session": "$1", "window": "@3",
        }

    def token(self, target=None, expires=None):
        return base64.urlsafe_b64encode(json.dumps({
            "target": target or self.target,
            "expires": expires if expires is not None else time.time() + 100,
        }).encode()).decode()

    def test_outside_tmux_refused(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            repair.context()

    def test_request_only_confirms(self):
        with patch.object(repair, "inspect_pane", return_value=self.target), \
             patch.object(repair, "run") as run:
            repair.request(self.tmux, 42, "%7", "/dev/fixture")
        args = run.call_args.args[0]
        self.assertEqual(args[4], "confirm-before")
        self.assertEqual(args[5], "-b")
        self.assertNotIn("send-keys", " ".join(args))
        self.assertIn("confirm ", args[-1])

    def test_confirm_sends_no_input(self):
        with patch.object(repair, "inspect_pane", return_value=self.target), \
             patch.object(repair, "run") as run:
            repair.confirm(self.tmux, 42, self.token())
        args = run.call_args.args[0]
        self.assertEqual(args[:8], self.tmux + ["if-shell", "-F", "-t", "%7"])
        self.assertEqual(args[-2], "send-keys -R -t %7 ''")
        self.assertIn("#{==:#{pid},42}", args[-3])
        self.assertIn("#{==:#{pane_pid},123}", args[-3])
        self.assertIn("#{==:#{pane_dead},0}", args[-3])
        self.assertIn("#{==:#{pane_current_command},bash}", args[-3])
        self.assertIn("#{==:#{session_id},$1}", args[-3])
        self.assertIn("#{==:#{window_id},@3}", args[-3])

    def test_changed_identities_refuse_without_mutation(self):
        for field in ("process", "server", "socket_id", "session", "window"):
            changed = copy.deepcopy(self.target)
            changed[field] = "changed"
            with self.subTest(field=field), \
                 patch.object(repair, "inspect_pane", return_value=changed), \
                 patch.object(repair, "run") as run, self.assertRaises(ValueError):
                repair.confirm(self.tmux, 42, self.token())
            run.assert_not_called()

    def test_expired_confirmation(self):
        with patch.object(repair, "inspect_pane") as inspect, self.assertRaises(ValueError):
            repair.confirm(self.tmux, 42, self.token(expires=time.time() - 1))
        inspect.assert_not_called()

    def test_unknown_or_scripted_shell(self):
        for command in ("codex", "claude", "ssh", "mosh-client", "vim"):
            with self.subTest(command=command), self.assertRaises(ValueError):
                repair.interactive_shell(999999, command)
        with patch.object(repair.Path, "exists", return_value=False), \
             patch.object(repair, "run", return_value="/bin/bash -c do-work"), \
             self.assertRaises(ValueError):
            repair.interactive_shell(999999, "bash")

    def test_foreground_and_ownership_checks(self):
        socket_stat = SimpleNamespace(st_mode=stat.S_IFSOCK, st_uid=os.getuid(), st_dev=1, st_ino=2)
        tty_stat = SimpleNamespace(st_mode=stat.S_IFCHR, st_uid=os.getuid(), st_dev=3, st_ino=4)
        output = "42|%7|123|/dev/fixture|bash|0|$1|@3"
        with patch.object(repair.os, "stat", return_value=socket_stat), \
             patch.object(repair, "run", return_value=output), \
             patch.object(repair, "process_identity", return_value={"pid": 123, "command": "bash", "pgid": 123, "foreground": 456}), \
             patch.object(repair, "interactive_shell"), \
             patch.object(repair.os, "open", return_value=8), \
             patch.object(repair.os, "close"), \
             patch.object(repair.os, "fstat", return_value=tty_stat), \
             self.assertRaises(ValueError):
            repair.inspect_pane(self.tmux, 42, "%7")


if __name__ == "__main__":
    unittest.main()
