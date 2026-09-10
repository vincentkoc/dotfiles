#!/usr/bin/env python3
"""Opt-in real-gh proof using only a task-owned loopback HTTP fixture."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading


native = Path(os.environ["GH_TEST_NATIVE_GH"]).resolve(strict=True)
wrappers = [Path(__file__).resolve().parents[1] / "bin" / name for name in ("gh", "ghx")]
requests = []
payload = {
    "field": {"alpha": 1, "beta": "two"},
    "text": "ordinary string with spaces",
    "multiline": "first line\nsecond line\n",
    "array": [{"alpha": 1}, "{", True, None, 3],
    "values": [{"alpha": 1}, "{", 3, True, None, ["x"], {"beta": 2}],
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        requests.append(self.path)
        if self.path == "/invalid":
            body = b"not-json"
        elif self.path == "/error":
            body = b'{"message":"fixture failure"}'
        elif self.path == "/data":
            body = json.dumps(payload, indent=2).encode()
        else:
            body = json.dumps({"count": len(requests)}).encode()
        self.send_response(422 if self.path == "/error" else 200)
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
    # Deliberately omit jq, including the system jq present on recent macOS.
    (backend / "bash").symlink_to("/bin/bash")
    (backend / "dirname").symlink_to(shutil.which("dirname"))
    (backend / "readlink").symlink_to(shutil.which("readlink"))
    for directory in ("a", "b", "home", "config", "cache"):
        (root / directory).mkdir()
    for name in ("--cache", "--cache=15s"):
        (root / "a" / name).write_text('{"fixture":"input body"}')
    environment = {
        "PATH": str(backend),
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
    data_endpoint = f"http://127.0.0.1:{server.server_port}/data"

    def invoke(binary, *args, cwd="a", success=True):
        result = subprocess.run(
            [str(binary), *args], cwd=root / cwd, env=environment,
            stdin=subprocess.DEVNULL, capture_output=True, timeout=15
        )
        assert (result.returncode == 0) == success, (args, result.stderr.decode())
        if success:
            assert not result.stderr, result.stderr.decode()
        assert b"\x1b" not in result.stdout
        return result

    def call(cwd, *args):
        return json.loads(invoke(wrappers[1], *args, cwd=cwd).stdout)

    def query_args(query, form):
        if form in ("--jq", "-q", "-iq"):
            return [form, query]
        return [form + query]

    try:
        assert call("a", "api", endpoint, "--cache", "15s") == {"count": 1}
        assert call("b", "api", endpoint, "--cache=15s") == {"count": 1}
        assert call("b", "api", endpoint) == {"count": 2}
        assert call("a", "--no-cache", "api", endpoint) == {"count": 3}
        assert call("a", "api", endpoint, "--cache", "15s") == {"count": 1}
        assert len(requests) == 3
        scalar_queries = [
            '"{"', ".text", ".multiline", "42", "0.125", "true", "false", "null",
            ".array", '""', "empty", '"first", "second"', ".text # trailing comment",
        ]
        baseline = {
            query: invoke(native, "api", data_endpoint, "--jq", query).stdout
            for query in scalar_queries
        }
        forms = ("--jq", "--jq=", "-q", "-q=", "attached-q")
        for wrapper in wrappers:
            for value in ("--cache", "--cache=15s"):
                for options in (["--template", value], ["--input", value, "--method", "GET"]):
                    expected = invoke(native, "api", data_endpoint, *options).stdout
                    observed = invoke(wrapper, "--no-cache", "api", data_endpoint, *options).stdout
                    assert observed == expected, (wrapper.name, options, observed, expected)
            sentinel_args = ["api", "--help", "--", "--cache"]
            expected = invoke(native, *sentinel_args).stdout
            assert invoke(wrapper, "--no-cache", *sentinel_args).stdout == expected
            request_count = len(requests)
            for cache_flag in (["--cache", "15s"], ["--cache=15s"]):
                result = invoke(wrapper, "--no-cache", "api", data_endpoint, *cache_flag, success=False)
                assert result.returncode == 2 and b"conflicts with api --cache" in result.stderr
            assert len(requests) == request_count
            for form in forms:
                def flags(query):
                    return ["-q" + query] if form == "attached-q" else query_args(query, form)

                for query in scalar_queries:
                    observed = invoke(wrapper, "api", data_endpoint, *flags(query)).stdout
                    assert observed == baseline[query], (wrapper.name, form, query, observed, baseline[query])
                for query in (".field", "{beta: .field.beta, alpha: .field.alpha}",
                              ".field # trailing comment"):
                    observed = invoke(wrapper, "api", data_endpoint, *flags(query)).stdout
                    assert observed == b'{"alpha":1,"beta":"two"}\n', (form, query, observed)
            unfiltered = invoke(native, "api", data_endpoint).stdout
            assert invoke(wrapper, "api", data_endpoint).stdout == unfiltered
            for empty_flag in (["--jq", ""], ["--jq="], ["-q", ""]):
                expected = invoke(native, "api", data_endpoint, *empty_flag).stdout
                assert invoke(wrapper, "api", data_endpoint, *empty_flag).stdout == expected
            mixed = []
            for index, value in enumerate(payload["values"]):
                if isinstance(value, dict):
                    mixed.append(json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n")
                else:
                    mixed.append(invoke(native, "api", data_endpoint, "--jq", f".values[{index}]").stdout)
            assert invoke(wrapper, "api", data_endpoint, "--jq", ".values[]").stdout == b"".join(mixed)
            assert invoke(wrapper, "api", data_endpoint, "--jq", ".field", "-q", '"{"').stdout == b"{\n"
            assert invoke(wrapper, "api", data_endpoint, "-iq", ".text").stdout.endswith(baseline[".text"])
            assert invoke(wrapper, "api", data_endpoint, "-iq.field").stdout.endswith(b'{"alpha":1,"beta":"two"}\n')
            for query in ("{broken", ".missing | error(\"fixture failure\")"):
                for binary in (native, wrapper):
                    invoke(binary, "api", data_endpoint, "--jq", query, success=False)
            for path in ("invalid", "error"):
                failure_endpoint = f"http://127.0.0.1:{server.server_port}/{path}"
                for binary in (native, wrapper):
                    invoke(binary, "api", failure_endpoint, "--jq", ".", success=False)
            for binary in (native, wrapper):
                invoke(binary, "api", data_endpoint, "--jq", success=False)
                invoke(binary, "api", data_endpoint, "-q=", success=False)
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
print("PASS: real gh cache/freshness, byte-identical scalar/array output, object JSONL, mixed streams, jq flag forms/errors, and no external jq")
