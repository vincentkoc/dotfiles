#!/usr/bin/env python3
"""Focused unit tests; no tmux server is contacted."""

import curses
from dataclasses import replace
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import stat
import sys
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("mtt_unit", str(ROOT / "bin/mtt"))
spec = importlib.util.spec_from_loader(loader.name, loader)
mtt = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = mtt
loader.exec_module(mtt)
REAL_SOCKET_IDENTITY = mtt.socket_identity
REAL_PROCESS_IDENTITY = mtt.process_identity
LINE = "100\t$0\t@2\t%4\t200\t/dev/pts/3\tcockpit\t2\t1\t85\t30\t84\t29\t1\t0\t0\t0"
PANE = mtt.Pane.parse(LINE)


class Guards(unittest.TestCase):
    def setUp(self):
        self.socket = patch.object(mtt, "socket_identity", return_value=(1000, 1, 2))
        self.birth = patch.object(mtt, "process_identity", return_value=("birth", 1))
        self.socket.start()
        self.birth.start()
        self.addCleanup(self.socket.stop)
        self.addCleanup(self.birth.stop)
        self.mirror = mtt.Mirror("/tmp/fixture/socket")
        self.mirror.server, self.mirror.server_birth = 100, "birth"
        self.mirror.pane, self.mirror.root_birth = PANE, "birth"

    def test_exact_targets(self):
        for target in ("%4", "cockpit:2.1"):
            self.assertEqual(mtt.resolve([PANE], target), PANE)
        for target in ("cock", "cockpit", "2.1", "%04", "%4;send-keys Enter"):
            with self.subTest(target=target), self.assertRaises(mtt.Refused):
                mtt.resolve([PANE], target)

    def test_ambiguous_linked_pane(self):
        other = replace(PANE, session="$1", name="other")
        with self.assertRaises(mtt.Refused):
            mtt.resolve([PANE, other], "%4")
        self.assertEqual(mtt.resolve([PANE, other], "other:2.1"), other)

    def test_strict_metadata(self):
        for index, bad in ((0, "-1"), (1, "$0;"), (2, "@x"), (3, "%-1"),
                           (4, "2x"), (5, "/tmp/tty"), (9, "0"), (10, "1001"),
                           (11, "86"), (12, "30"), (13, "2"), (16, "on")):
            fields = LINE.split("\t")
            fields[index] = bad
            with self.subTest(index=index), self.assertRaises(mtt.Refused):
                mtt.Pane.parse("\t".join(fields))

    def test_wrap_pending_cursor(self):
        self.assertEqual(mtt.Pane.parse(LINE.replace("\t84\t", "\t85\t")).x, 84)

    def test_dead_mode_sync(self):
        for field in ("dead", "mode", "sync"):
            with self.subTest(field=field), self.assertRaises(mtt.Refused):
                replace(PANE, **{field: 1}).safe()

    def test_socket_replacement(self):
        with patch.object(mtt, "socket_identity", return_value=(1000, 1, 3)):
            with self.assertRaisesRegex(mtt.Refused, "socket replaced"):
                self.mirror.preflight()

    def test_disappearance(self):
        with patch.object(mtt, "process_identity", side_effect=mtt.Refused("gone")):
            with self.assertRaises(mtt.Refused):
                self.mirror.preflight()

    def test_pid_reuse(self):
        for births in ([("new", 1)], [("birth", 1), ("new", 1)]):
            with patch.object(mtt, "process_identity", side_effect=births):
                with self.assertRaisesRegex(mtt.Refused, "identity changed"):
                    self.mirror.preflight()

    def test_socket_type_and_owner(self):
        with patch.object(mtt.os, "stat") as probe:
            for mode, uid in ((stat.S_IFREG, os.getuid()), (stat.S_IFSOCK, os.getuid() + 1)):
                probe.return_value = Mock(st_mode=mode, st_uid=uid)
                with self.subTest(mode=mode), self.assertRaises(mtt.Refused):
                    # Test the real function, not setUp's patch.
                    REAL_SOCKET_IDENTITY("/tmp/fixture/socket")

    def test_self_same_server(self):
        with patch.dict(os.environ, {"TMUX": "/tmp/fixture/socket,100,0", "TMUX_PANE": "%4"}):
            with self.assertRaisesRegex(mtt.Refused, "viewer's pane"):
                mtt.refuse_self(PANE, "/tmp/fixture/socket")

    def test_other_server_same_pane_id(self):
        with patch.dict(os.environ, {"TMUX": "/tmp/other/socket,101,0", "TMUX_PANE": "%4"}):
            with patch.object(mtt.os, "isatty", return_value=False):
                mtt.refuse_self(PANE, "/tmp/fixture/socket")

    def test_self_tty(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(mtt.os, "isatty", return_value=True):
                with patch.object(mtt.os, "ttyname", return_value=PANE.tty):
                    with self.assertRaisesRegex(mtt.Refused, "TTY"):
                        mtt.refuse_self(PANE, "/tmp/fixture/socket")

    def test_self_ancestor(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(mtt.os, "isatty", return_value=False):
                with patch.object(mtt, "process_identity", return_value=("birth", PANE.root)):
                    with self.assertRaisesRegex(mtt.Refused, "ancestor"):
                        mtt.refuse_self(PANE, "/tmp/fixture/socket")

    def test_guard_fields_and_hooks(self):
        guard = mtt.condition(PANE)
        for name in ("pid", "session_id", "window_id", "pane_id", "pane_pid",
                     "pane_tty", "pane_dead", "pane_in_mode", "synchronize-panes",
                     "after-capture-pane", "after-send-keys"):
            self.assertIn("#{" + name + "}", guard)
        self.assertIn("#{==:#{after-send-keys},}", guard)

    def test_input_quoting(self):
        payload = b"'; run-shell \"bad\"; #{}\x00\x03\r\n\x1d"
        command = mtt.input_command(PANE, [payload, "Up"])
        self.assertEqual(command[:4], ["if-shell", "-F", "-t", "$0:@2.%4"])
        self.assertNotIn("run-shell", command[5])
        self.assertIn(" ".join("{:02x}".format(b) for b in payload), command[5])
        self.assertTrue(command[5].endswith("display-message -p MTT_SENT"))
        self.assertNotIn("Enter", command[5])
        self.assertNotIn("-b", command)

    def test_named_key_allowlist_and_bounds(self):
        for events in ([], ["PasteStart"], ["Enter;kill-pane"], [b"x" * 4097], ["Up"] * 65):
            with self.subTest(events=str(events)[:50]), self.assertRaises(mtt.Refused):
                mtt.input_command(PANE, events)

    def test_read_only(self):
        self.mirror.read_only = True
        with patch.object(self.mirror, "call") as call:
            with self.assertRaisesRegex(mtt.Refused, "read-only"):
                self.mirror.send([b"x"])
            call.assert_not_called()

    def test_timeout_no_replay(self):
        with patch.object(mtt, "bounded_run", side_effect=mtt.Refused("timed out")) as run:
            with self.assertRaisesRegex(mtt.Refused, "never retried"):
                self.mirror.send([b"x"])
            with self.assertRaisesRegex(mtt.Refused, "not retried"):
                self.mirror.send([b"x"])
            self.assertEqual(run.call_count, 1)

    def test_ack_required(self):
        with patch.object(mtt, "bounded_run", return_value=b"") as run:
            with self.assertRaisesRegex(mtt.Refused, "acknowledgement"):
                self.mirror.send([b"x"])
            with self.assertRaises(mtt.Refused):
                self.mirror.send([b"y"])
            self.assertEqual(run.call_count, 1)

    def test_explicit_socket_and_no_server_creation(self):
        with patch.object(mtt, "bounded_run", return_value=b"MTT_SENT\n") as run:
            self.mirror.send([b"x"])
            self.assertEqual(run.call_args.args[0][:5],
                             ["tmux", "-N", "-u", "-S", "/tmp/fixture/socket"])
            self.assertNotIn("TMUX", run.call_args.kwargs["env"])

    def test_one_inflight(self):
        self.mirror.inflight = True
        with self.assertRaises(mtt.Refused):
            self.mirror.call(["list-panes"])

    def test_metadata_has_no_capture_or_send(self):
        with patch.object(self.mirror, "call", return_value=LINE + "\n") as call:
            self.assertEqual(self.mirror.metadata(), PANE)
            body = call.call_args.args[0][5]
            self.assertNotIn("capture-pane", body)
            self.assertNotIn("send-keys", body)

    def test_guarded_snapshot_rows(self):
        rows = ["abc  "] + [""] * 29
        response = LINE + "\n" + "\n".join(rows) + "\nMTT_FRAME\n"
        with patch.object(self.mirror, "call", return_value=response) as call:
            self.assertEqual(self.mirror.snapshot(), (PANE, rows))
            body = call.call_args.args[0][5]
            self.assertIn("capture-pane -p -N -t $0:@2.%4", body)
            self.assertNotIn(" -e", body)
            self.assertNotIn(" -J", body)

    def test_snapshot_missing_or_changed(self):
        for response in ("MTT_REFUSED\n", LINE + "\nMTT_FRAME\n",
                         LINE.replace("%4", "%5") + "\n" * 31 + "MTT_FRAME\n"):
            with patch.object(self.mirror, "call", return_value=response):
                with self.assertRaises(mtt.Refused):
                    self.mirror.snapshot()

    def test_output_and_time_bounds(self):
        with self.assertRaisesRegex(mtt.Refused, "output exceeded"):
            mtt.bounded_run([sys.executable, "-c", "print('x' * 4096)"], limit=32)
        with self.assertRaisesRegex(mtt.Refused, "timed out"):
            mtt.bounded_run([sys.executable, "-c", "import time; time.sleep(2)"], timeout=0.05)

    def test_local_process_birth(self):
        born, parent = REAL_PROCESS_IDENTITY(os.getpid())
        self.assertTrue(born)
        self.assertEqual(parent, os.getppid())

    def test_macos_birth_fallback(self):
        with patch.object(mtt.sys, "platform", "darwin"):
            with patch.object(mtt, "bounded_run", return_value=b" 1 Thu Sep 10 01:02:03 2026\n"):
                self.assertEqual(REAL_PROCESS_IDENTITY(123),
                                 ("Thu Sep 10 01:02:03 2026", 1))


class ControlsAndCells(unittest.TestCase):
    def feed(self, controls, text):
        result = []
        for key in text:
            result.extend(controls.key(key, 9, 1.0))
        return result

    def test_local_controls(self):
        for prefix in ("\x02", "\x1d"):
            with self.subTest(prefix=repr(prefix)):
                controls = mtt.Controls()
                self.assertEqual(self.feed(controls, prefix + "p"), [])
                self.assertFalse(controls.interactive)
                self.assertEqual(self.feed(controls, "not sent" + prefix * 2), [])
                self.assertEqual(controls.key(curses.KEY_RIGHT, 9, 1), [])
                self.assertEqual(controls.view.left, 1)
                self.assertEqual(self.feed(controls, prefix + "f"), [])
                self.assertTrue(controls.interactive)
                self.assertEqual(self.feed(controls, prefix * 2), [prefix.encode()])

    def test_exit_aliases(self):
        for prefix in ("\x02", "\x1d"):
            for action in ("d", "q"):
                for read_only in (False, True):
                    with self.subTest(prefix=repr(prefix), action=action, read_only=read_only):
                        controls = mtt.Controls(read_only)
                        self.assertEqual(self.feed(controls, prefix + action), [])
                        self.assertTrue(controls.quit)
                        self.assertFalse(controls.prefix)

    def test_mixed_prefixes_and_unknown_actions_are_consumed(self):
        for prefix, other in (("\x02", "\x1d"), ("\x1d", "\x02")):
            for action in (other, "x", curses.KEY_UP):
                with self.subTest(prefix=repr(prefix), action=repr(action)):
                    controls = mtt.Controls()
                    self.assertEqual(self.feed(controls, prefix), [])
                    self.assertEqual(controls.prefix, prefix)
                    self.assertEqual(controls.key(action, 9, 1), [])
                    self.assertFalse(controls.prefix)
                    self.assertFalse(controls.quit)
                    self.assertTrue(controls.interactive)
                    self.assertEqual(self.feed(controls, "q"), [b"q"])

    def test_readonly_cannot_resume_input(self):
        for prefix in ("\x02", "\x1d"):
            with self.subTest(prefix=repr(prefix)):
                controls = mtt.Controls(True)
                self.feed(controls, prefix + "p" + prefix + "f")
                self.assertTrue(controls.read_only)
                self.assertFalse(controls.interactive)
                self.assertEqual(self.feed(controls, "a\x03" + prefix * 2), [])

    def test_normal_keys(self):
        controls = mtt.Controls()
        self.assertEqual(self.feed(controls, "\x03\x7f\r"), [b"\x03", b"\x7f", b"\r"])
        self.assertEqual(self.feed(controls, "dqpf"), [b"d", b"q", b"p", b"f"])
        self.assertFalse(controls.quit)
        self.assertTrue(controls.interactive)
        self.assertEqual(controls.key(curses.KEY_UP, 9, 1), ["Up"])

    def test_paste_preserves_prefix_and_newlines(self):
        controls = mtt.Controls()
        frame = mtt.PASTE_START + "a\x02d\x02p\x1dq\x1dp\nb\x1b[A" + mtt.PASTE_END
        self.assertEqual(self.feed(controls, frame), [frame.encode()])
        self.assertFalse(controls.quit)
        self.assertFalse(controls.prefix)
        self.assertTrue(controls.interactive)

    def test_paste_clears_pending_prefix(self):
        for prefix in ("\x02", "\x1d"):
            with self.subTest(prefix=repr(prefix)):
                controls = mtt.Controls()
                frame = mtt.PASTE_START + "\x02q\x1dd\n" + mtt.PASTE_END
                self.assertEqual(self.feed(controls, prefix + mtt.PASTE_START), [])
                self.assertFalse(controls.prefix)
                self.assertTrue(controls.pasting)
                self.assertEqual(self.feed(controls, "\x02q\x1dd\n" + mtt.PASTE_END),
                                 [frame.encode()])
                self.assertFalse(controls.quit)
                self.assertEqual(self.feed(controls, "d"), [b"d"])

    def test_paste_bound_and_timeout(self):
        for suffix in ("x" * 4091,):
            controls = mtt.Controls()
            with self.assertRaisesRegex(mtt.Refused, "exceeds"):
                self.feed(controls, mtt.PASTE_START + suffix)
        controls = mtt.Controls()
        self.feed(controls, mtt.PASTE_START + "unfinished")
        with self.assertRaisesRegex(mtt.Refused, "incomplete"):
            controls.tick(4)

    def test_paste_readonly_discard(self):
        for prefix in ("\x02", "\x1d"):
            for read_only in (False, True):
                with self.subTest(prefix=repr(prefix), read_only=read_only):
                    controls = mtt.Controls(read_only)
                    if not read_only:
                        self.feed(controls, prefix + "p")
                    frame = mtt.PASTE_START + "\x02q\x1dd" + mtt.PASTE_END
                    self.assertEqual(self.feed(controls, prefix + frame), [])
                    self.assertFalse(controls.quit)
                    self.assertFalse(controls.prefix)
                    self.assertFalse(controls.interactive)

    def test_escape_timeout(self):
        controls = mtt.Controls()
        self.feed(controls, "\x1b")
        self.assertEqual(controls.tick(1.1), [b"\x1b"])

    def test_partial_paste_delimiter_not_forwarded(self):
        controls = mtt.Controls()
        self.assertEqual(self.feed(controls, "\x1b[20"), [])
        with self.assertRaisesRegex(mtt.Refused, "incomplete paste delimiter"):
            controls.tick(1.1)

    def test_viewport_follow_and_bounds(self):
        view = mtt.Viewport()
        view.fit(PANE, 9, 20)
        self.assertEqual((view.top, view.left), (21, 65))
        view.fit(PANE, 50, 120)
        self.assertEqual((view.top, view.left), (0, 0))
        view.follow = False
        view.top, view.left = 999, -3
        view.fit(PANE, 9, 20)
        self.assertEqual((view.top, view.left), (21, 0))
        view.pan(curses.KEY_HOME, 9)
        self.assertEqual((view.top, view.left), (0, 0))
        view.pan(curses.KEY_NPAGE, 9)
        view.pan(curses.KEY_RIGHT, 9)
        self.assertEqual((view.top, view.left), (9, 1))

    def test_whole_rows_no_string_cell_slicing(self):
        rows = ["A\u754ce\u0301  "] + [""] * 29
        pad = Mock()
        with patch.object(mtt.curses, "newpad", return_value=pad) as new:
            self.assertIs(mtt.make_pad(PANE, rows), pad)
            new.assert_called_once_with(30, 85)
            pad.erase.assert_called_once()
            self.assertEqual(pad.addstr.call_args_list[0].args, (0, 0, rows[0]))
            self.assertEqual(pad.addstr.call_count, 30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
