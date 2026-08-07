#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

home="$temporary/home with spaces"
fake_bin="$temporary/bin"
launchctl_log="$temporary/launchctl.log"
launchctl_state="$temporary/launchctl.loaded"
mkdir -p "$home" "$fake_bin"

cat >"$fake_bin/launchctl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$TEST_LAUNCHCTL_LOG"
label="gui/$(id -u)/com.vincentkoc.agent-worktree-ops"
case "$1" in
  bootout)
    if [[ "${2:-}" == "$label" ]]; then
      if [[ "${TEST_FAIL_BOOTOUT:-0}" == "1" ]]; then
        exit 1
      fi
      rm -f "$TEST_LAUNCHCTL_STATE"
    fi
    ;;
  print)
    [[ "${2:-}" == "$label" && -f "$TEST_LAUNCHCTL_STATE" ]]
    ;;
  bootstrap)
    touch "$TEST_LAUNCHCTL_STATE"
    ;;
esac
EOF
chmod +x "$fake_bin/launchctl"
touch "$launchctl_state"

HOME="$home" \
PATH="$fake_bin:$PATH" \
TEST_LAUNCHCTL_LOG="$launchctl_log" \
TEST_LAUNCHCTL_STATE="$launchctl_state" \
"$root/bin/agent-worktree-ops/install-agent-worktree-ops" --install-only

plist="$home/Library/LaunchAgents/com.vincentkoc.agent-worktree-ops.plist"
runtime="$home/Library/Application Support/agent-worktree-ops"
[[ -f "$plist" && ! -L "$plist" ]]
plutil -lint "$plist" >/dev/null
grep -Fq "$runtime/agent-worktree-maintain" "$plist"
[[ -x "$runtime/agent-worktree-clean" ]]
[[ -x "$runtime/agent-worktree-maintain" ]]
[[ -x "$runtime/agent-worktree-purge" ]]
[[ ! -e "$launchctl_state" ]]
grep -Fq "bootout gui/$(id -u)/com.vincentkoc.agent-worktree-ops" "$launchctl_log"
grep -Fq "print gui/$(id -u)/com.vincentkoc.agent-worktree-ops" "$launchctl_log"
if grep -Eq '^bootstrap ' "$launchctl_log"; then
  printf 'install-only must not reload the LaunchAgent\n' >&2
  exit 1
fi

touch "$launchctl_state"
if HOME="$home" \
  PATH="$fake_bin:$PATH" \
  TEST_LAUNCHCTL_LOG="$launchctl_log" \
  TEST_LAUNCHCTL_STATE="$launchctl_state" \
  TEST_FAIL_BOOTOUT=1 \
  "$root/bin/agent-worktree-ops/install-agent-worktree-ops" --install-only \
  >"$temporary/failed.out" 2>&1; then
  printf 'install-only must fail when the LaunchAgent remains loaded\n' >&2
  exit 1
fi
[[ -e "$launchctl_state" ]]
grep -Fq 'failed to unload' "$temporary/failed.out"

printf 'agent worktree installer tests passed\n'
