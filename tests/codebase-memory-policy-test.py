#!/usr/bin/env python3
import json
import pathlib


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
for phrase in (
    "Before index_repository",
    "canonical owning checkout",
    "Never index a linked branch",
    "skip indexing and report it",
):
    if phrase not in message:
        raise SystemExit(f"missing hook policy: {phrase}")

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
