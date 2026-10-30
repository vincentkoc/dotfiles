#!/usr/bin/env python3
"""Exercise PATH dispatch and proxy routing without changing macOS settings."""

import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROXY_KEYS = (
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
)


class CurlProxyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.backend = self.root / "backend"
        self.bin.mkdir()
        self.backend.mkdir()
        wrapper = ROOT / "bin/curl"
        if wrapper.exists():
            shutil.copy2(wrapper, self.bin / "curl")
        self.write_executable(self.bin / "uname", "#!/bin/sh\nprintf '%s\\n' \"${TEST_PLATFORM:-Darwin}\"\n")
        self.write_executable(
            self.bin / "scutil",
            '#!/bin/sh\n[ "${TEST_SCUTIL_FAIL:-0}" = 0 ] || exit 1\ncat "$TEST_PROXY_FILE"\n',
        )
        self.write_executable(
            self.backend / "curl",
            f"#!{sys.executable}\nimport json, os, sys\n"
            f"print(json.dumps({{'args': sys.argv[1:], 'env': {{k: os.environ[k] for k in {PROXY_KEYS!r} if k in os.environ}}}}))\n"
            "sys.exit(int(os.environ.get('TEST_CURL_EXIT', '0')))\n",
        )
        self.config = self.root / "proxy.txt"
        self.set_proxy()
        self.env = {k: v for k, v in os.environ.items() if k not in PROXY_KEYS}
        self.env.update(
            HOME=str(self.root), CURL_HOME=str(self.root),
            PATH=f"{self.bin}:{self.backend}:/usr/bin:/bin",
            TEST_PROXY_FILE=str(self.config),
        )

    def write_executable(self, path, text):
        path.write_text(text)
        path.chmod(0o755)

    def set_proxy(self, enabled=True, host="127.0.0.1", port=1082):
        self.config.write_text(
            f"<dictionary> {{\n  HTTPEnable : {int(enabled)}\n"
            f"  HTTPProxy : {host}\n  HTTPPort : {port}\n"
            f"  HTTPSEnable : {int(enabled)}\n"
            f"  HTTPSProxy : {host}\n  HTTPSPort : {port}\n}}\n"
        )

    def run_curl(self, *args, extra=None):
        return subprocess.run(
            ["curl", *args], env=self.env | (extra or {}),
            capture_output=True, text=True, timeout=8,
        )

    def inspect(self, *args, extra=None):
        result = self.run_curl(*args, extra=extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_system_proxy_tracks_toggles_and_port_changes(self):
        result = self.inspect("--head", "https://example.invalid/a b")
        self.assertEqual(result["args"], ["--head", "https://example.invalid/a b"])
        self.assertEqual(result["env"]["http_proxy"], "http://127.0.0.1:1082")
        self.assertEqual(result["env"]["https_proxy"], "http://127.0.0.1:1082")
        self.assertEqual(result["env"]["no_proxy"], "localhost,127.0.0.1,::1")
        self.set_proxy(enabled=False)
        self.assertEqual(self.inspect()["env"], {})
        self.set_proxy(port=8080)
        self.assertEqual(self.inspect()["env"]["https_proxy"], "http://127.0.0.1:8080")

    def test_explicit_proxy_environment_is_preserved(self):
        for key in PROXY_KEYS:
            if key.lower() == "no_proxy":
                continue
            for value in ("http://explicit.invalid:8080", ""):
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.inspect(extra={key: value})["env"], {key: value})

    def test_explicit_bypass_is_preserved(self):
        for key in ("no_proxy", "NO_PROXY"):
            for value in ("example.internal", ""):
                with self.subTest(key=key, value=value):
                    result = self.inspect(extra={key: value})["env"]
                    self.assertEqual(result[key], value)
                    self.assertNotIn("NO_PROXY" if key == "no_proxy" else "no_proxy", result)

    def test_linux_and_unavailable_settings_stay_native(self):
        for extra in ({"TEST_PLATFORM": "Linux"}, {"TEST_SCUTIL_FAIL": "1"}):
            with self.subTest(extra=extra):
                self.assertEqual(self.inspect(extra=extra)["env"], {})

    def test_ipv6_proxy_address(self):
        self.set_proxy(host="::1")
        self.assertEqual(self.inspect()["env"]["https_proxy"], "http://[::1]:1082")

    def test_backend_status_and_symlink_dispatch(self):
        alias_dir = self.root / "alias"
        alias_dir.mkdir()
        (alias_dir / "curl").symlink_to(self.bin / "curl")
        result = self.run_curl("--fail", extra={
            "PATH": f"{alias_dir}:{self.env['PATH']}", "TEST_CURL_EXIT": "22",
        })
        self.assertEqual(result.returncode, 22, result.stderr)
        self.assertEqual(json.loads(result.stdout)["args"], ["--fail"])

    def test_copied_wrappers_do_not_recurse(self):
        copied_bin = self.root / "copied-bin"
        copied_bin.mkdir()
        shutil.copy2(self.bin / "curl", copied_bin / "curl")
        result = self.inspect(extra={"PATH": f"{copied_bin}:{self.env['PATH']}"})
        self.assertEqual(result["env"]["https_proxy"], "http://127.0.0.1:1082")

    def test_real_curl_routes_remote_through_proxy_and_localhost_direct(self):
        native = shutil.which("curl", path="/usr/bin:/bin")
        if not native:
            self.skipTest("native curl unavailable")
        (self.backend / "curl").unlink()
        (self.backend / "curl").symlink_to(native)
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(self.path.encode())

            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.set_proxy(port=server.server_port)
        remote = "http://example.invalid/through-proxy"
        local = f"http://127.0.0.1:{server.server_port}/direct"
        flags = ("-q", "-sS", "--connect-timeout", "1", "--max-time", "3")
        result = self.run_curl(*flags, remote)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, remote)
        result = self.run_curl(*flags, local)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "/direct")
        result = self.run_curl(*flags, "--noproxy", "*", local)
        self.assertEqual(result.stdout, "/direct")
        self.set_proxy(enabled=False)
        result = self.run_curl(*flags, local)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(requests, [remote, "/direct", "/direct", "/direct"])


if __name__ == "__main__":
    unittest.main()
