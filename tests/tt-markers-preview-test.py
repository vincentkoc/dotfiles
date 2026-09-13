#!/usr/bin/env python3
"""Public marker/preview commands with a recording tmux and disposable state."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MarkerPreviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.log = self.home / "tmux.jsonl"
        tmux = self.home / "tmux"
        tmux.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "args=sys.argv[1:]\n"
            "with pathlib.Path(os.environ['TMUX_FIXTURE_LOG']).open('a') as f: f.write(json.dumps(args)+'\\n')\n"
            "if args[0]=='display-message':\n"
            " if args[-1]=='#{window_id}': print('@1')\n"
            " else: print('fixture\\t1\\t0\\t/fixture/repo\\tfixture title\\tfixture title')\n"
            "elif args[0]=='list-panes': print('zsh')\n"
        )
        tmux.chmod(0o700)
        self.env = {
            "HOME": str(self.home), "PATH": "/usr/bin:/bin", "LC_ALL": "C",
            "TT_TMUX_BIN": str(tmux), "TMUX_FIXTURE_LOG": str(self.log),
        }

    def invoke(self, *args):
        return subprocess.run(
            [str(ROOT / "bin/tt"), *args], env=self.env,
            capture_output=True, text=True, timeout=10,
        )

    def test_new_markers_persist_sync_and_clear(self):
        for marker, color in (("violet", "#bb9af7"), ("cyan", "#7dcfff")):
            with self.subTest(marker=marker):
                result = self.invoke("marker", "set", "%7", marker)
                self.assertEqual(result.returncode, 0, result.stderr)
                files = list((self.home / ".local/state/tt/pane-markers").glob("*.md"))
                self.assertEqual(len(files), 1)
                self.assertIn('marker: "' + marker + '"', files[0].read_text())
                self.assertEqual(self.invoke("marker", "sync", "%7").returncode, 0)
                calls = [json.loads(line) for line in self.log.read_text().splitlines()]
                self.assertIn(["set-option", "-pt", "%7", "@tt_marker_color", color], calls)
                self.assertEqual(self.invoke("marker", "clear", "%7").returncode, 0)
                self.assertFalse(files[0].exists())

    def preview(self, extra_headers, extra_values):
        snapshot = self.home / "snapshot.tsv"
        headers = ["# target", "kind", "cwd", "title", "command", "session", "status", "restore", *extra_headers]
        row = ["fixture:1.0", "codex", "/fixture/repo", "fixture", "codex", "fixture-id", "dead", 'touch "' + str(self.home / "unexpected") + '"', *extra_values]
        snapshot.write_text("\t".join(headers) + "\n" + "\t".join(row) + "\n")
        result = self.invoke("restore-preview", str(snapshot))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.home / "unexpected").exists())
        self.assertFalse(self.log.exists(), "preview must not contact tmux")
        return result.stdout.splitlines()[2].split()

    def test_preview_finds_bucket_by_header_instead_of_column_number(self):
        fields = self.preview(["marker", "owner", "bucket"], ["cyan", "owner-value", "research"])
        self.assertEqual(fields[3], "research")

    def test_current_owner_and_exit_columns_are_not_a_bucket(self):
        fields = self.preview(["owner", "exit"], ["owner-value", "exited"])
        self.assertEqual(fields[3], "-")
        self.assertNotIn("owner-value", fields)


if __name__ == "__main__":
    unittest.main()
