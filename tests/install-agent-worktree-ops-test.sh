#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script_bash="${TEST_SCRIPT_BASH:-/bin/bash}"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

fake_bin="$temporary/bin"
launchctl_log="$temporary/launchctl.log"
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
printf '%s\n' "$*" >>"${TEST_LAUNCHCTL_LOG:?}"
exit 99
EOF
chmod 0755 "$fake_bin/uname" "$fake_bin/launchctl"

run_installer() {
  local home="$1"
  shift

  HOME="$home" \
    PATH="$fake_bin:$PATH" \
    TEST_UNAME_SYSTEM=Darwin \
    TEST_LAUNCHCTL_LOG="$launchctl_log" \
    "$script_bash" "$root/bin/agent-worktree-ops/install-agent-worktree-ops" "$@"
}

assert_runtime_only() {
  local home="$1"
  local runtime="$home/Library/Application Support/agent-worktree-ops"
  local tool

  for tool in \
    agent-worktree-clean \
    agent-worktree-maintain \
    agent-worktree-purge \
    retire-agent-worktree-scheduler \
    worktree-storage-guard; do
    [[ -x "$runtime/$tool" ]]
    cmp -s "$root/bin/agent-worktree-ops/$tool" "$runtime/$tool"
  done

  [[ ! -e "$home/Library/LaunchAgents/com.vincentkoc.agent-worktree-ops.plist" ]]
  [[ ! -e "$home/Library/LaunchAgents/com.vincentkoc.codex-worktree-maintain.plist" ]]
  [[ ! -e "$launchctl_log" ]]
}

linux_home="$temporary/linux home"
linux_output="$(
  HOME="$linux_home" \
    PATH="$fake_bin:$PATH" \
    TEST_UNAME_SYSTEM=Linux \
    TEST_LAUNCHCTL_LOG="$launchctl_log" \
    "$script_bash" "$root/bin/agent-worktree-ops/install-agent-worktree-ops" --install-only
)"
[[ "$linux_output" == "agent-worktree-ops is macOS-only; skipping" ]]
[[ ! -e "$linux_home" ]]
[[ ! -e "$launchctl_log" ]]

default_home="$temporary/default home"
default_output="$(run_installer "$default_home")"
[[ "$default_output" == "agent-worktree-ops runtime installed; scheduler state unchanged" ]]
assert_runtime_only "$default_home"

legacy_flag_home="$temporary/legacy flag home"
legacy_flag_output="$(run_installer "$legacy_flag_home" --install-only)"
[[ "$legacy_flag_output" == "agent-worktree-ops runtime installed; scheduler state unchanged" ]]
assert_runtime_only "$legacy_flag_home"

if run_installer "$temporary/bad argument home" --load-agent >"$temporary/bad.out" 2>&1; then
  printf 'installer must reject obsolete scheduler-loading arguments\n' >&2
  exit 1
fi
grep -Fq 'unknown argument: --load-agent' "$temporary/bad.out"

if grep -Eq 'launchctl|bootstrap|bootout|Library/LaunchAgents|\\.plist' \
  "$root/bin/agent-worktree-ops/install-agent-worktree-ops"; then
  printf 'runtime installer must not contain scheduler mutation logic\n' >&2
  exit 1
fi
if grep -Eq 'retire-agent-worktree-scheduler[^[:cntrl:]]*--apply' "$root/install.sh"; then
  printf 'root installer must never apply scheduler retirement\n' >&2
  exit 1
fi
grep -Fq "bash \"\$df_dir/bin/install-agent-worktree-ops\"" "$root/install.sh"

[[ ! -e "$root/Library/LaunchAgents/com.vincentkoc.agent-worktree-ops.plist" ]]
if grep -Fq 'Library/LaunchAgents/com.vincentkoc.agent-worktree-ops.plist' \
  "$root/.mackup/shellextras.cfg"; then
  printf 'Mackup must not restore the retired scheduler plist\n' >&2
  exit 1
fi

printf 'agent worktree runtime installer tests passed\n'
