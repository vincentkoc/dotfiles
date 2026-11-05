#!/usr/bin/env python3
"""Signal only owned fixture processes; never resolve installed credentials."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


root, home, backend = map(Path, sys.argv[1:])
fixture = root / "native-cancel-fixture.py"
fixture.write_text("""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

holder = None
if os.environ["TEST_PIPE_HOLDER"] == "1":
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
print("synthetic-native-secret", flush=True)
ready = Path(os.environ["TEST_READY"])
stage = ready.with_suffix(".staging")
stage.write_text(json.dumps({
    "supervisor": os.getppid(), "lookup": os.getpid(), "group": os.getpgrp(),
    "holder": holder.pid if holder else None,
}))
stage.replace(ready)
if holder is None:
    time.sleep(60)
""")


def group_alive(group):
    try:
        os.killpg(group, 0)
        return True
    except ProcessLookupError:
        return False


def kill_group(group):
    try:
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        pass


for signum in (signal.SIGINT, signal.SIGTERM):
    for target in ("helper", "supervisor", "group"):
        for pipe_holder in (False, True):
            name = f"{signum.name}-{target}-" + ("pipe-holder" if pipe_holder else "running")
            print(f"case: codex-github-mcp/{name}", flush=True)
            ready = root / (name + ".ready")
            launched = root / (name + ".launched")
            env = dict(os.environ)
            for key in ("GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_PAT_TOKEN", "CODEX_HOME", "BASH_ENV", "ENV"):
                env.pop(key, None)
            env.update(
                HOME=str(home), PATH=f"{home}/bin:{backend}:/usr/bin:/bin",
                TEST_ROOT=str(root), TEST_BACKEND=str(backend),
                TEST_LAUNCHER="codex-github-mcp", TEST_AUTH_MODE="cancel",
                TEST_NO_GH="0", TEST_NO_SERVER="0",
                TEST_EXPECTED_MODERN="", TEST_EXPECTED_LEGACY="",
                TEST_LOOKUP_LOG=str(root / "lookups"),
                TEST_FORBIDDEN_LOG=str(root / "forbidden"),
                TEST_PYTHON=sys.executable, TEST_NATIVE_FIXTURE=str(fixture),
                TEST_PIPE_HOLDER=str(int(pipe_holder)), TEST_READY=str(ready),
                TEST_MCP_LOG=str(launched),
            )
            process = subprocess.Popen(
                [str(home / "bin/codex-github-mcp"), "run", "two words"],
                env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True,
            )
            group = None
            try:
                deadline = time.monotonic() + 3
                while not ready.exists():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise AssertionError("fixture lookup did not become ready")
                    time.sleep(0.005)
                owned = json.loads(ready.read_text())
                group = owned["group"]
                assert owned["supervisor"] == process.pid, "helper must exec its supervisor"
                assert group == owned["lookup"] and group != process.pid
                assert group_alive(group), "lookup group must be running before cancellation"
                started = time.monotonic()
                if target == "group":
                    os.killpg(process.pid, signum)
                else:
                    os.kill(process.pid if target == "helper" else owned["supervisor"], signum)
                stdout, stderr = process.communicate(b"request on stdin\n", timeout=3)
                assert time.monotonic() - started < 3, "cancellation waited for lookup deadline"
                assert process.returncode == 128 + signum, process.returncode
                assert stdout == b"", "partial credentials reached stdout"
                assert stderr == b"codex-github-mcp: native GitHub credential lookup canceled\n", stderr
                assert not launched.exists(), "MCP launched after cancellation"
                deadline = time.monotonic() + 2
                while group_alive(group) and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert not group_alive(group), "owned lookup group survived cancellation"
            finally:
                if process.poll() is None:
                    kill_group(process.pid)
                    process.communicate(timeout=3)
                if group is not None and group_alive(group):
                    kill_group(group)
