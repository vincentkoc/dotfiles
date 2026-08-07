#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

home="$temporary/home with spaces"
fake_bin="$temporary/bin"
launchctl_log="$temporary/launchctl.log"
mkdir -p "$home" "$fake_bin"

cat >"$fake_bin/launchctl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$TEST_LAUNCHCTL_LOG"
EOF
chmod +x "$fake_bin/launchctl"

HOME="$home" \
PATH="$fake_bin:$PATH" \
TEST_LAUNCHCTL_LOG="$launchctl_log" \
"$root/bin/agent-worktree-ops/install-agent-worktree-ops" --install-only

plist="$home/Library/LaunchAgents/com.vincentkoc.agent-worktree-ops.plist"
runtime="$home/Library/Application Support/agent-worktree-ops"
[[ -f "$plist" && ! -L "$plist" ]]
plutil -lint "$plist" >/dev/null
grep -Fq "$runtime/agent-worktree-maintain" "$plist"
[[ -x "$runtime/agent-worktree-clean" ]]
[[ -x "$runtime/agent-worktree-maintain" ]]
[[ -x "$runtime/agent-worktree-purge" ]]
grep -Fq "bootout gui/$(id -u)/com.vincentkoc.agent-worktree-ops" "$launchctl_log"
if grep -Eq '^(bootstrap|print) ' "$launchctl_log"; then
  printf 'install-only must not reload the LaunchAgent\n' >&2
  exit 1
fi

printf 'agent worktree installer tests passed\n'
