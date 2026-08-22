#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / ".codex" / "AGENTS.md"


policy = AGENTS.read_text(encoding="utf-8")

for obsolete in (
    "If disk is low, worktree count is high, or local state looks stale, run "
    "`agent-worktree-maintain --force` before continuing.",
):
    if obsolete in policy:
        raise SystemExit(f"obsolete automatic maintenance policy remains: {obsolete}")

for required in (
    "never an automatic recovery step",
    "another cleanup command such as `gwt rm` just ran",
    "canonical repository set",
    "owner session or PIDs",
    "lock owner",
    "A live maintainer/cleaner process or lock is contention",
    "do not start a second process, kill it, or clear its lock",
    "current-turn permission naming that exact process or lock",
    "current-turn user authorization naming the exact host",
    "repository/worktree scope",
    "paths owned by the current task or exact paths explicitly authorized",
    "missing panes, and low disk never prove ownership",
):
    if required not in policy:
        raise SystemExit(f"missing worktree maintenance policy: {required}")

print("worktree_maintenance_policy_test=passed")
