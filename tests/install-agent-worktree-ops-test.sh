#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

fake_bin="$temporary/bin"
linux_home="$temporary/linux home"
linux_plist="$linux_home/Library/LaunchAgents/com.vincentkoc.agent-worktree-ops.plist"
linux_runtime="$linux_home/Library/Application Support/agent-worktree-ops"
linux_launchctl_log="$temporary/linux-launchctl.log"
home="$temporary/home with spaces"
launchctl_log="$temporary/launchctl.log"
launchctl_state="$temporary/launchctl.loaded"
mkdir -p "$fake_bin"

cat >"$fake_bin/uname" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 1 && "$1" == "-s" ]]
printf '%s\n' "${TEST_UNAME_SYSTEM:?}"
EOF

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

if ! command -v plutil >/dev/null 2>&1; then
  cat >"$fake_bin/plutil" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 2 && "$1" == "-lint" ]]
python3 - "$2" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as plist:
    plistlib.load(plist)
PY
EOF
  chmod +x "$fake_bin/plutil"

  printf '<plist>' >"$temporary/malformed.plist"
  if PATH="$fake_bin:$PATH" plutil -lint "$temporary/malformed.plist" >/dev/null 2>&1; then
    printf 'fallback plutil must reject malformed XML\n' >&2
    exit 1
  fi
fi

chmod +x "$fake_bin/uname" "$fake_bin/launchctl"

linux_output="$(
  HOME="$linux_home" \
  PATH="$fake_bin:$PATH" \
  TEST_UNAME_SYSTEM=Linux \
  TEST_LAUNCHCTL_LOG="$linux_launchctl_log" \
  TEST_LAUNCHCTL_STATE="$temporary/linux-launchctl.loaded" \
  "$root/bin/agent-worktree-ops/install-agent-worktree-ops" --install-only
)"
[[ "$linux_output" == "agent-worktree-ops is macOS-only; skipping" ]]
[[ ! -e "$linux_home" ]]
[[ ! -e "$linux_plist" ]]
[[ ! -e "$linux_runtime" ]]
[[ ! -e "$linux_launchctl_log" ]]
[[ ! -e "$temporary/linux-launchctl.loaded" ]]

mkdir -p "$home"
touch "$launchctl_state"

HOME="$home" \
PATH="$fake_bin:$PATH" \
TEST_UNAME_SYSTEM=Darwin \
TEST_LAUNCHCTL_LOG="$launchctl_log" \
TEST_LAUNCHCTL_STATE="$launchctl_state" \
"$root/bin/agent-worktree-ops/install-agent-worktree-ops" --install-only

plist="$home/Library/LaunchAgents/com.vincentkoc.agent-worktree-ops.plist"
runtime="$home/Library/Application Support/agent-worktree-ops"
[[ -f "$plist" && ! -L "$plist" ]]
PATH="$fake_bin:$PATH" plutil -lint "$plist" >/dev/null
grep -Fq "$runtime/agent-worktree-maintain" "$plist"
[[ -x "$runtime/agent-worktree-clean" ]]
[[ -x "$runtime/agent-worktree-maintain" ]]
[[ -x "$runtime/agent-worktree-purge" ]]
[[ ! -e "$launchctl_state" ]]
grep -Fxq "bootout gui/$(id -u)/com.vincentkoc.codex-worktree-maintain" "$launchctl_log"
grep -Fxq "bootout gui/$(id -u)/com.vincentkoc.agent-worktree-ops" "$launchctl_log"
grep -Fxq "print gui/$(id -u)/com.vincentkoc.agent-worktree-ops" "$launchctl_log"
if grep -Eq '^bootstrap ' "$launchctl_log"; then
  printf 'install-only must not reload the LaunchAgent\n' >&2
  exit 1
fi

touch "$launchctl_state"
if HOME="$home" \
  PATH="$fake_bin:$PATH" \
  TEST_UNAME_SYSTEM=Darwin \
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
