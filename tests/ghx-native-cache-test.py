#!/usr/bin/env python3
"""Opt-in real-gh proof using only a task-owned loopback HTTP fixture."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading


native = Path(os.environ["GH_TEST_NATIVE_GH"]).resolve(strict=True)
wrapper = Path(__file__).resolve().parents[1] / "bin/ghx"
requests = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        requests.append(self.path)
        body = json.dumps({"count": len(requests)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    backend = root / "backend"
    backend.mkdir()
    (backend / "gh").symlink_to(native)
    for directory in ("a", "b", "home", "config", "cache"):
        (root / directory).mkdir()
    environment = {
        "PATH": f"{backend}:/usr/bin:/bin",
        "HOME": str(root / "home"),
        "GH_CONFIG_DIR": str(root / "config"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "GH_TOKEN": "loopback-fixture-not-a-credential",
        "GH_PROMPT_DISABLED": "1",
        "GH_NO_UPDATE_NOTIFIER": "1",
        "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1",
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/fixture"

    def call(cwd, *args):
        result = subprocess.run(
            [str(wrapper), *args], cwd=root / cwd, env=environment,
            stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=True
        )
        assert not result.stderr, result.stderr.decode()
        assert b"\x1b" not in result.stdout
        return json.loads(result.stdout)

    try:
        assert call("a", "api", endpoint, "--cache", "15s") == {"count": 1}
        assert call("b", "api", endpoint, "--cache=15s") == {"count": 1}
        assert call("b", "api", endpoint) == {"count": 2}
        assert call("a", "--no-cache", "api", endpoint) == {"count": 3}
        assert call("a", "api", endpoint, "--cache", "15s") == {"count": 1}
        assert len(requests) == 3
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
print("PASS: real gh cache reuse across cwd, default fresh reads, uncached bypass, and no ANSI")
