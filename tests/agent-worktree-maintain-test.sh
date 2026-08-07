#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

runtime="$temporary/runtime"
repo="$temporary/repo"
codex_home="$temporary/codex"
registered="$temporary/registered"
mkdir -p "$runtime" "$repo" "$registered"
cp "$root/bin/agent-worktree-ops/agent-worktree-maintain" "$runtime/agent-worktree-maintain"

cat >"$runtime/agent-worktree-clean" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" --list-registered "* ]]; then
  printf '%s\n' "$TEST_REGISTERED"
  exit 0
fi
printf '%s\n' "$*" >"$TEST_CLEANER_ARGS"
EOF
chmod +x "$runtime/agent-worktree-clean" "$runtime/agent-worktree-maintain"

TEST_REGISTERED="$registered" \
TEST_CLEANER_ARGS="$temporary/cleaner.args" \
CODEX_HOME="$codex_home" \
"$runtime/agent-worktree-maintain" \
  --repo "$repo" \
  --codex-home "$codex_home" \
  --force

grep -q 'start count=1 ' "$codex_home/log/agent-worktree-maintain.log"
grep -q -- "--repo $repo" "$temporary/cleaner.args"
grep -q -- "--codex-home $codex_home" "$temporary/cleaner.args"
grep -q -- '--apply' "$temporary/cleaner.args"

printf 'agent worktree maintainer tests passed\n'
