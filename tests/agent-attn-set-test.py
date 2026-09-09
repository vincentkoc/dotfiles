#!/usr/bin/env python3
"""Exercise hooks against a recording tmux stub and fixture-owned socket."""

import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
STUB = r"""#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
assert args[:3] == ["-N","-S",os.environ["TEST_SOCKET"]], args
with open(os.environ["TEST_LOG"],"a") as log:
    log.write(json.dumps(args)+"\n")
args=args[3:]
data=json.loads(Path(os.environ["TEST_DATA"]).read_text())
if args[0] == "display-message":
    if "@tt_marker" not in args[-1]:
        print("|".join([data["server"],"%7",data["pane_pid"],"0"]))
    else:
        print("|".join([data["server"],"%7",data["pane_pid"],data["marker"],data["style"],data["active"]]))
elif args[0] == "show-option":
    print(data["state"] if args[-1] == "@agent_attention_state" else data.get("local_"+args[-1], ""))
elif args[0] != "if-shell":
    raise AssertionError(args)
"""


class AttentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.bind(str(self.root / "socket"))
        (self.root / "bin").mkdir()
        stub = self.root / "bin/tmux"
        stub.write_text(STUB)
        stub.chmod(0o700)
        self.data = {
            "server": "42", "pane_pid": str(os.getpid()), "marker": "", "state": "",
            "style": "fg=default,bg=#1a1b26", "active": "fg=default,bg=#1a1b26",
        }
        self.env = {
            "HOME": str(self.root), "PATH": f"{self.root / 'bin'}:/usr/bin:/bin",
            "TMUX": f"{self.root / 'socket'},42,0", "TMUX_PANE": "%7",
            "TEST_SOCKET": str(self.root / "socket"), "TEST_DATA": str(self.root / "data"),
            "TEST_LOG": str(self.root / "log"), "LC_ALL": "C",
        }

    def tearDown(self):
        self.socket.close()
        self.temporary.cleanup()

    def hook(self, state="waiting"):
        (self.root / "data").write_text(json.dumps(self.data))
        subprocess.run(
            ["/bin/bash", str(ROOT / "bin/agent-attn-set"), state, "codex"],
            env=self.env, check=True, timeout=10,
        )
        log = self.root / "log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def mutations(self, calls):
        return [call for call in calls if call[3] == "if-shell"]

    def test_no_tmux_context_means_no_calls(self):
        del self.env["TMUX"]
        self.assertEqual(self.hook(), [])

    def test_wrong_server_or_nonancestor_refused(self):
        for key, value in (("server", "999"), ("pane_pid", "99999999")):
            self.data.update(server="42", pane_pid=str(os.getpid()))
            self.data[key] = value
            self.assertEqual(self.mutations(self.hook()), [])

    def test_wait_is_scoped(self):
        mutations = self.mutations(self.hook())
        self.assertEqual(len(mutations), 1)
        mutation = mutations[0]
        self.assertEqual(mutation[3:7], ["if-shell", "-F", "-t", "%7"])
        self.assertIn("set-option -pt %7 window-style", mutation[-1])
        for forbidden in (" -g", "refresh-client", "select-pane", "badge", "send-keys"):
            self.assertNotIn(forbidden, mutation[-1])

    def test_unchanged_wait_has_no_mutation(self):
        self.data.update(state="waiting", style="fg=default,bg=#241d12", active="fg=default,bg=#2f2517")
        calls = self.hook()
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.mutations(calls), [])

    def test_marked_or_custom_pane_is_untouched(self):
        self.data["marker"] = "green"
        self.assertEqual(self.mutations(self.hook()), [])
        self.data["marker"] = ""
        self.data["local_window-style"] = "bg=red"
        self.assertEqual(self.mutations(self.hook()), [])

    def test_reset_restores_inherited_style_without_touching_title(self):
        self.data.update(state="waiting", style="fg=default,bg=#241d12", active="fg=default,bg=#2f2517")
        commands = self.mutations(self.hook("reset"))[0][-1]
        self.assertIn("set-option -pqu -t %7 window-style", commands)
        self.assertNotIn("title", commands)

    def test_legacy_reset_and_external_style_change(self):
        self.data.update(style="fg=default,bg=#241d12", active="fg=default,bg=#2f2517")
        self.assertEqual(self.mutations(self.hook("reset")), [])
        (self.root / "log").unlink()
        self.data.update(state="waiting", style="bg=red", active="bg=red")
        commands = self.mutations(self.hook("reset"))[0][-1]
        self.assertNotIn("window-style", commands)

    def test_explicit_palette_without_owned_state_is_untouched(self):
        self.data.update(style="fg=default,bg=#241d12", active="fg=default,bg=#2f2517")
        self.data["local_window-style"] = self.data["style"]
        self.data["local_window-active-style"] = self.data["active"]
        self.assertEqual(self.mutations(self.hook("approve")), [])


if __name__ == "__main__":
    unittest.main()
