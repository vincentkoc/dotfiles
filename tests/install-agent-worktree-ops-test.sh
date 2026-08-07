#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

home="$temporary/home with spaces"
mkdir -p "$home"

HOME="$home" "$root/bin/agent-worktree-ops/install-agent-worktree-ops" --install-only

plist="$home/Library/LaunchAgents/com.vincentkoc.agent-worktree-ops.plist"
runtime="$home/Library/Application Support/agent-worktree-ops"
[[ -f "$plist" && ! -L "$plist" ]]
plutil -lint "$plist" >/dev/null
grep -Fq "$runtime/agent-worktree-maintain" "$plist"
[[ -x "$runtime/agent-worktree-clean" ]]
[[ -x "$runtime/agent-worktree-maintain" ]]
[[ -x "$runtime/agent-worktree-purge" ]]

printf 'agent worktree installer tests passed\n'
