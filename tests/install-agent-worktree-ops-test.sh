#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_script="$root/tests/install-agent-worktree-ops-test.sh"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

fake_bin="$temporary/bin"
valid_plist="$temporary/valid.plist"
malformed_plist="$temporary/malformed.plist"
plutil_fallback_log="${TEST_PLUTIL_FALLBACK_LOG:-$temporary/plutil-fallback.log}"
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

cat >"$valid_plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.example.test</string>
</dict>
</plist>
EOF
printf '<plist>' >"$malformed_plist"

plutil_compatible=0
if command -v plutil >/dev/null 2>&1 \
  && plutil -lint "$valid_plist" >/dev/null 2>&1 \
  && ! plutil -lint "$malformed_plist" >/dev/null 2>&1; then
  plutil_compatible=1
fi

if (( plutil_compatible == 0 )); then
  cat >"$fake_bin/plutil" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 2 && "$1" == "-lint" ]]
printf '%s\n' "$2" >>"${TEST_PLUTIL_FALLBACK_LOG:?}"
python3 - "$2" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as plist:
    plistlib.load(plist)
PY
EOF
  chmod +x "$fake_bin/plutil"
fi

TEST_PLUTIL_FALLBACK_LOG="$plutil_fallback_log" \
  PATH="$fake_bin:$PATH" \
  plutil -lint "$valid_plist" >/dev/null
if TEST_PLUTIL_FALLBACK_LOG="$plutil_fallback_log" \
  PATH="$fake_bin:$PATH" \
  plutil -lint "$malformed_plist" >/dev/null 2>&1; then
  printf 'plist validator must reject malformed XML\n' >&2
  exit 1
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
TEST_PLUTIL_FALLBACK_LOG="$plutil_fallback_log" \
"$root/bin/agent-worktree-ops/install-agent-worktree-ops" --install-only

plist="$home/Library/LaunchAgents/com.vincentkoc.agent-worktree-ops.plist"
runtime="$home/Library/Application Support/agent-worktree-ops"
[[ -f "$plist" && ! -L "$plist" ]]
TEST_PLUTIL_FALLBACK_LOG="$plutil_fallback_log" \
  PATH="$fake_bin:$PATH" \
  plutil -lint "$plist" >/dev/null
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
  TEST_PLUTIL_FALLBACK_LOG="$plutil_fallback_log" \
  "$root/bin/agent-worktree-ops/install-agent-worktree-ops" --install-only \
  >"$temporary/failed.out" 2>&1; then
  printf 'install-only must fail when the LaunchAgent remains loaded\n' >&2
  exit 1
fi
[[ -e "$launchctl_state" ]]
grep -Fq 'failed to unload' "$temporary/failed.out"

if [[ "${TEST_SKIP_INCOMPATIBLE_PLUTIL_REGRESSION:-0}" != "1" ]]; then
  incompatible_bin="$temporary/incompatible-bin"
  incompatible_fallback_log="$temporary/incompatible-fallback.log"
  incompatible_output="$temporary/incompatible.out"
  mkdir -p "$incompatible_bin"
  cat >"$incompatible_bin/plutil" <<'EOF'
#!/usr/bin/env bash
exit 64
EOF
  chmod +x "$incompatible_bin/plutil"

  if ! PATH="$incompatible_bin:$PATH" \
    TEST_PLUTIL_FALLBACK_LOG="$incompatible_fallback_log" \
    TEST_SKIP_INCOMPATIBLE_PLUTIL_REGRESSION=1 \
    "$test_script" >"$incompatible_output" 2>&1; then
    cat "$incompatible_output" >&2
    printf 'installer tests must tolerate an incompatible plutil command\n' >&2
    exit 1
  fi
  [[ -s "$incompatible_fallback_log" ]]
  grep -Fxq 'agent worktree installer tests passed' "$incompatible_output"
fi

printf 'agent worktree installer tests passed\n'
