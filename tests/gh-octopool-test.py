#!/usr/bin/env python3
"""Hermetic gh/ghx routing proof: fake backends, real jq, no network or login."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
JQ = shutil.which("jq")
assert JQ, "jq is required for the opt-in Octopool routing tests"
FIXTURE_AUTH = {
    "url": "https://octopool.dev",
    "pool": "maintainers",
    "token": "fixture-caller-not-a-credential",
}
ENV_KEYS = (
    "OCTOPOOL_TOKEN", "OCTOPOOL_ADMIN_TOKEN", "OCTOPOOL_URL", "OCTOPOOL_POOL",
    "OCTOPOOL_GH_PATH", "GHX_GH_PATH", "GH_TOKEN", "GITHUB_TOKEN",
    "OCTOPOOL_FRESH", "OCTOPOOL_NO_FALLBACK", "NO_COLOR", "GH_FORCE_TTY",
)
BACKEND = f"""#!{sys.executable}
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
record = {{
    "route": pathlib.Path(sys.argv[0]).name,
    "args": args,
    "env": {{key: os.environ.get(key) for key in {ENV_KEYS!r}}},
    "stdin": sys.stdin.buffer.read().hex(),
}}
pathlib.Path(os.environ["TEST_RECORD"]).write_text(json.dumps(record))
if os.environ.get("TEST_JQ_OUTPUT") == "1":
    query = None
    for index, arg in enumerate(args):
        if arg in ("--jq", "-q"):
            query = args[index + 1]
        elif arg.startswith("--jq="):
            query = arg[len("--jq="):]
    payload = '{{"values":[{{"ok":true}},"{{",false,null,[1,2]]}}'
    result = subprocess.run([{JQ!r}, "-r", "--", query],
                            input=payload, text=True, capture_output=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    sys.exit(result.returncode)
sys.stdout.buffer.write(b'fixture output\\n')
sys.stderr.write(os.environ.get("TEST_ERROR", ""))
sys.exit(int(os.environ.get("TEST_STATUS", "0")))
"""


with tempfile.TemporaryDirectory(prefix="gh-octopool-test-") as temporary:
    root = Path(temporary)
    home, wrapper, backend, tools, shims = (root / name for name in (
        "home", "wrapper", "backend", "tools", "shims",
    ))
    for directory in (home, wrapper, backend, tools, shims):
        directory.mkdir()
    shutil.copytree(ROOT / "bin" / "gh-support", wrapper / "gh-support")
    for name in ("gh", "ghx"):
        shutil.copy2(ROOT / "bin" / name, wrapper / name)
    for name in ("gh", "ghx", "octopool"):
        (backend / name).write_text(BACKEND)
        (backend / name).chmod(0o755)
    for name in ("bash", "dirname", "uname", "readlink", "jq", "env"):
        (tools / name).symlink_to("/bin/bash" if name == "bash" else shutil.which(name))
    (home / ".ghx").mkdir()
    (home / ".ghx" / "config.yaml").write_text("{}\n")
    activation = home / ".config" / "gh-routing" / "octopool.json"
    activation.parent.mkdir(parents=True)
    config = root / "config"
    auth = (home / "Library" / "Application Support" if sys.platform == "darwin"
            else config) / "octopool" / "auth.json"
    auth.parent.mkdir(parents=True)
    environment = {
        "PATH": os.pathsep.join(map(str, (wrapper, shims, backend, tools))),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config),
        "TEST_RECORD": str(root / "record"),
    }
    calls = 0
    configuration = {
        "enabled": True, "octopool_path": str(backend / "octopool"),
        "gh_path": str(backend / "gh"),
    }
    linked_entrypoints = root / "linked-entrypoints"
    linked_entrypoints.mkdir()
    for name in ("gh", "ghx", "gh-support"):
        (linked_entrypoints / name).symlink_to(wrapper / name)

    def activate():
        activation.write_text(json.dumps(configuration))
        auth.write_text(json.dumps(FIXTURE_AUTH))
        auth.chmod(0o600)

    def invoke(entry, *args, route="gh", status=0, env=None, payload=b""):
        global calls
        calls += 1
        record_path = root / "record"
        record_path.unlink(missing_ok=True)
        result = subprocess.run(
            [str(wrapper / entry), *args], env={**environment, **(env or {})},
            input=payload, capture_output=True, timeout=10,
        )
        assert result.returncode == status, (entry, args, result.stderr, result.returncode)
        if route is None:
            assert not record_path.exists(), (args, record_path.read_text())
            return result, None
        record = json.loads(record_path.read_text())
        assert record["route"] == route, (entry, args, record)
        assert record["stdin"] == payload.hex(), (entry, args, record)
        return result, record

    accepted = (
        "", "/pulls", "/pulls/123", "/pulls/123/files", "/pulls/123/commits",
        "/pulls/123/reviews", "/issues", "/issues/123", "/issues/123/comments",
        "/commits", "/commits/main", "/commits/abc123/check-runs",
        "/commits/abc123/check-suites", "/commits/abc123/status",
        "/commits/abc123/statuses", "/check-runs/123",
        "/check-suites/123/check-runs", "/actions/runs", "/actions/runs/123",
        "/actions/runs/123/jobs", "/actions/runs/123/attempts/2",
        "/actions/runs/123/attempts/2/jobs", "/actions/jobs/123",
        "/actions/workflows", "/actions/workflows/123",
        "/actions/workflows/123/runs", "/pulls?state=open&per_page=100&page=2",
        "/actions/runs/123/jobs?filter=all",
    )
    endpoint = "repos/openclaw/openclaw/pulls/123"
    rejected_paths = (
        "user", "rate_limit", "graphql", "/repos/openclaw/openclaw/pulls",
        "https://api.github.com/" + endpoint,
        "repos/openclaw/private/pulls", "repos/example/repo/pulls",
        "repos/openclaw/openclaw-other/pulls", "repos/OPENCLAW/openclaw/pulls",
        "repos/{owner}/{repo}/pulls", "repos/:owner/:repo/pulls",
        "repos/openclaw/openclaw/../private/pulls",
        "repos/openclaw/openclaw/./pulls", endpoint + "/.",
        endpoint + "//", endpoint + "%2fcomments", endpoint + "%2E%2E",
        endpoint + "%5C", endpoint + "\\comments", endpoint + "#fragment",
        endpoint + "\n", endpoint + "\t", endpoint + "\x01",
        endpoint + "?access_token=fixture", endpoint + "?token=fixture",
        endpoint + "?per_page=10&secret=fixture", endpoint + "?",
        endpoint + "?page=1&", endpoint + "?page=1&&state=open",
        endpoint + "?page=0", endpoint + "?per_page=101",
        endpoint + "?state=merged", endpoint + "?filter=unknown",
        endpoint + "?page=%31", endpoint + "?page=1#fragment",
        endpoint + "?page=1;access_token=fixture",
        endpoint + "?page=1?token=fixture", endpoint + "?page=1=2",
        "repos/openclaw/openclaw/actions/secrets",
        "repos/openclaw/openclaw/collaborators",
        "repos/openclaw/openclaw/rulesets",
        "repos/openclaw/openclaw/branches/main/protection",
        "repos/openclaw/openclaw/actions/runs/123/logs",
        "repos/openclaw/openclaw/actions/runs/123/artifacts",
    )
    for entry in ("gh", "ghx"):
        # An installed binary and saved login are not activation.
        auth.write_text(json.dumps(FIXTURE_AUTH))
        invoke(entry, "api", endpoint)
        activate()
        for repo in ("openclaw", "octopool"):
            for suffix in accepted:
                invoke(entry, "api", "repos/openclaw/" + repo + suffix, route="octopool")
        for flags in (
            [], ["--paginate"], ["--paginate", "--slurp"], ["-X", "GET"],
            ["--method", "GET"], ["--method=GET"], ["--jq", ".number"],
            ["-q", ".number"], ["--jq=.number"],
        ):
            _, record = invoke(entry, "api", endpoint, *flags, route="octopool")
            assert record["args"][:3] == ["gh", "api", endpoint]
        invoke(entry, "api", "--paginate", endpoint, route="octopool")
        invoke(str(linked_entrypoints / entry), "api", endpoint, route="octopool")
        invoke(str(linked_entrypoints / entry), "--no-cache", "api", endpoint)
        for path in rejected_paths:
            invoke(entry, "api", path)
        for flags in (
            ["-X", "POST"], ["--method=DELETE"], ["-X", "GET", "-f", "x=y"],
            ["-f", "x=y"], ["-F", "x=y"], ["--field=x=y"], ["--raw-field", "x=y"],
            ["--input", "-"], ["--input=-"], ["--input", "/dev/stdin"],
            ["-H", "Authorization: token fixture"], ["--header=Accept: application/json"],
            ["--hostname", "example.invalid"], ["--hostname=github.com"],
            ["--include"], ["-i"], ["--cache", "15s"], ["--template", "{{.number}}"],
            ["--unknown"], ["--"], ["--jq"], ["--jq", ""], ["--jq="],
            ["-q.number"], ["-q=.number"], ["-XGET"],
            ["--slurp"], ["--paginate", "--slurp", "--jq", "."],
            ["--method", "get"], ["--method"], ["--paginate=true"],
        ):
            invoke(entry, "api", endpoint, *flags)
        invoke(entry, "api", endpoint, endpoint)
        for args in (
            ["auth", "status"], ["api", "graphql", "-f", "query=fixture"],
            ["issue", "edit", "123", "--body-file", "-"],
            ["secret", "set", "FIXTURE"], ["pr", "create"],
            ["--hostname", "example.invalid", "api", endpoint],
        ):
            invoke(entry, *args, payload=b"fixture\x00stdin\n")
        invoke(entry, "api", endpoint, route="octopool", payload=b"unconsumed input\n")
        for env in ({"GH_OCTOPOOL": "0"}, {"GHX_NO_CACHE": "1"},
                    {"GH_HOST": "github.com"}, {"GH_HOST": "example.invalid"},
                    {"GH_REPO": "openclaw/openclaw"}):
            invoke(entry, "api", endpoint, env=env)
        invoke(entry, "--no-cache", "api", endpoint)
        invoke(entry, "--ttl", "15", "api", endpoint, status=2, route=None)
        invoke(entry, "--ttl", "15", "--no-cache", "api", endpoint, status=2, route=None)
        invoke(entry, "--no-cache", "api", endpoint, "--cache", "15s", status=2, route=None)
        invoke(entry, "pr", "view", "123", "-R", "openclaw/openclaw",
               "--json", "number", route="ghx")
        invoke(entry, "--ttl", "15", "pr", "view", "123", "-R", "openclaw/openclaw",
               "--json", "number", route="ghx")
        invoke(entry, "xdaemon", "status", route="ghx")
        overrides = {
            "OCTOPOOL_TOKEN": "untrusted-token", "OCTOPOOL_ADMIN_TOKEN": "untrusted-admin",
            "OCTOPOOL_URL": "https://example.invalid", "OCTOPOOL_POOL": "untrusted",
            "OCTOPOOL_GH_PATH": str(wrapper / "gh"), "GHX_GH_PATH": str(wrapper / "ghx"),
            "GH_TOKEN": "native-fixture", "GITHUB_TOKEN": "native-fixture-2",
            "OCTOPOOL_FRESH": "1", "OCTOPOOL_NO_FALLBACK": "1", "GH_FORCE_TTY": "100",
        }
        result, record = invoke(entry, "api", endpoint, route="octopool", env=overrides)
        assert result.stdout == b"fixture output\n" and not result.stderr
        assert record["env"] == {
            **{key: None for key in ENV_KEYS},
            "OCTOPOOL_URL": "https://octopool.dev", "OCTOPOOL_POOL": "maintainers",
            "OCTOPOOL_GH_PATH": str(backend / "gh"), "GH_TOKEN": "native-fixture",
            "GITHUB_TOKEN": "native-fixture-2", "OCTOPOOL_FRESH": "1",
            "OCTOPOOL_NO_FALLBACK": "1", "NO_COLOR": "1",
        }
        _, record = invoke(entry, "--no-cache", "api", endpoint, env=overrides)
        assert record["env"]["OCTOPOOL_TOKEN"] == overrides["OCTOPOOL_TOKEN"]
        result, _ = invoke(entry, "api", endpoint, route="octopool", status=7,
                           env={"TEST_STATUS": "7", "TEST_ERROR": "fixture error"})
        assert result.stdout == b"fixture output\n" and result.stderr == b"fixture error"
        for flags in (["--jq", ".values[]"], ["-q", ".values[]"],
                      ["--jq=.values[]"], ["--jq", ".values[] # trailing comment"]):
            result, record = invoke(entry, "api", endpoint, *flags, route="octopool",
                                    env={"TEST_JQ_OUTPUT": "1"})
            assert result.stdout == b'{"ok":true}\n{\nfalse\nnull\n[\n  1,\n  2\n]\n'
            native_result, native_record = invoke(
                entry, "--no-cache", "api", endpoint, *flags, env={"TEST_JQ_OUTPUT": "1"})
            assert native_record["args"] == record["args"][1:]
            assert native_result.stdout == result.stdout
        for data in (
            {}, {**FIXTURE_AUTH, "url": "https://example.invalid"},
            {**FIXTURE_AUTH, "pool": "other"}, {**FIXTURE_AUTH, "token": ""},
            {**FIXTURE_AUTH, "token": None}, {**FIXTURE_AUTH, "token": " \n"},
            {**FIXTURE_AUTH, "token": "fixture\n"}, ["not", "an", "object"],
        ):
            auth.write_text(json.dumps(data))
            invoke(entry, "api", endpoint)
        auth.write_text("invalid json")
        invoke(entry, "api", endpoint)
        auth.write_text(json.dumps(FIXTURE_AUTH) + "\n" + json.dumps(FIXTURE_AUTH))
        invoke(entry, "api", endpoint)
        auth.unlink()
        invoke(entry, "api", endpoint)
        activate()
        saved_auth = root / "saved-auth"
        auth.rename(saved_auth)
        auth.symlink_to(saved_auth)
        invoke(entry, "api", endpoint)
        auth.unlink()
        saved_auth.rename(auth)
        for data in (
            {}, {**configuration, "enabled": False},
            {**configuration, "url": "https://example.invalid"},
            {**configuration, "gh_path": str(wrapper / "gh")},
            {**configuration, "gh_path": str(backend / "octopool")},
            {**configuration, "gh_path": "gh"},
            {**configuration, "octopool_path": "octopool"},
            {**configuration, "octopool_path": str(wrapper / "gh")},
            {**configuration, "gh_path": str(backend / "gh") + "\n"},
            {**configuration, "octopool_path": str(root / "absent")},
        ):
            activation.write_text(json.dumps(data))
            invoke(entry, "api", endpoint)
        for contents in ("", "1", "https://octopool.dev other",
                         json.dumps(configuration) + "\n{}",
                         "$(touch " + str(root / "must-not-exist") + ")"):
            activation.write_text(contents)
            invoke(entry, "api", endpoint)
        assert not (root / "must-not-exist").exists()
        activation.unlink()
        activation.symlink_to(auth)
        invoke(entry, "api", endpoint)
        activation.unlink()
        activate()
        for path in (backend / "octopool", tools / "jq"):
            disabled = root / "disabled"
            path.rename(disabled)
            invoke(entry, "api", endpoint)
            disabled.rename(path)
        # The activation pin takes priority over a different PATH Octopool/native.
        pinned = root / "pinned"
        pinned.mkdir(exist_ok=True)
        for name in ("gh", "octopool"):
            shutil.copy2(backend / name, pinned / name)
        activation.write_text(json.dumps({
            "enabled": True, "octopool_path": str(pinned / "octopool"),
            "gh_path": str(pinned / "gh"),
        }))
        _, record = invoke(entry, "api", endpoint, route="octopool")
        assert record["env"]["OCTOPOOL_GH_PATH"] == str(pinned / "gh")
        (backend / "gh").rename(root / "native-disabled")
        (backend / "octopool").rename(root / "octopool-disabled")
        invoke(entry, "api", endpoint, route="octopool")
        invoke(entry, "--no-cache", "api", endpoint)
        invoke(entry, "api", endpoint, env={"GH_OCTOPOOL": "0"})
        invoke(entry, "api", endpoint, env={"GHX_NO_CACHE": "1"})
        invoke(entry, "api", endpoint, env={"GH_HOST": "example.invalid"})
        (root / "native-disabled").rename(backend / "gh")
        (root / "octopool-disabled").rename(backend / "octopool")
        activate()
        # Skip Octopool symlinks and copied ghx wrappers during native discovery.
        (shims / "gh").symlink_to(backend / "octopool")
        invoke(entry, "api", endpoint, route="octopool")
        invoke(entry, "--no-cache", "api", endpoint)
        (shims / "gh").unlink()
        shutil.copy2(wrapper / "gh", shims / "gh")
        invoke(entry, "--no-cache", "api", endpoint)
        (shims / "gh").unlink()
        # A shim remains recognizable even when octopool is absent from PATH.
        hidden = root / "hidden"
        hidden.mkdir(exist_ok=True)
        (backend / "octopool").rename(hidden / "octopool")
        (shims / "gh").symlink_to(hidden / "octopool")
        invoke(entry, "api", endpoint)
        (hidden / "octopool").rename(backend / "octopool")
        (shims / "gh").unlink()
        (backend / "gh").rename(root / "native-disabled")
        invoke(entry, "api", endpoint, status=127, route=None)
        (root / "native-disabled").rename(backend / "gh")
        activation.unlink()

print(f"PASS: {calls} hermetic gh/ghx Octopool routing, auth, isolation, and parity cases")
