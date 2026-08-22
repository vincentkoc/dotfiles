#!/usr/bin/env python3
import json
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".codex" / "hooks.json"
AGENTS = ROOT / ".codex" / "AGENTS.md"


payload = json.loads(HOOKS.read_text())
commands = [
    hook["command"]
    for group in payload["hooks"]["SessionStart"]
    for hook in group["hooks"]
    if hook.get("type") == "command"
]
message = next(command for command in commands if "codebase-memory-mcp" in command)
rendered = subprocess.run(
    ["/bin/sh", "-c", message],
    check=True,
    capture_output=True,
    text=True,
).stdout
for phrase in (
    "search/query tools",
    "Direct index_repository use is policy-forbidden",
    "codebase-memory-graph.sh index|init canonical helper",
    "direct codebase-memory-mcp cli index_repository is forbidden",
    "Linked worktrees rewrite to their owning checkout",
    (
        "Independent indexing is denied for ~/.codex/worktrees, "
        "~/GIT/_Worktrees, any repo-local .worktrees path, /tmp, "
        "and /private/tmp."
    ),
):
    if phrase not in rendered:
        raise SystemExit(f"missing hook policy: {phrase}")
if "intentionally disabled" in rendered:
    raise SystemExit("hook must describe policy rather than tool capability")

agents = AGENTS.read_text()
for phrase in (
    "canonical owning checkout",
    "Linked branch worktrees must reuse the owner graph",
    "~/.codex/worktrees",
    "~/GIT/_Worktrees",
    "repo-local `.worktrees`",
    "`/tmp`",
    "`/private/tmp`",
    "skip indexing and report the missing canonical checkout",
):
    if phrase not in agents:
        raise SystemExit(f"missing agent policy: {phrase}")

print("codebase_memory_policy_test=passed")
