#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
temporary="$(cd "$temporary" && pwd -P)"

runtime="$temporary/runtime"
repo="$temporary/repo"
codex_home="$temporary/codex"
state_dir="$temporary/state"
registered="$temporary/registered"
mkdir -p "$runtime" "$repo" "$codex_home" "$state_dir" "$registered"
chmod 700 "$state_dir"
mkdir -m 700 "$state_dir/log"
: >"$state_dir/log/agent-worktree-maintain.log"
chmod 644 "$state_dir/log/agent-worktree-maintain.log"
cp "$root/bin/agent-worktree-ops/agent-worktree-maintain" "$runtime/agent-worktree-maintain"

cat >"$runtime/agent-worktree-clean" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" --list-registered "* ]]; then
  printf '%s\n' "$TEST_REGISTERED"
  exit 0
fi
printf '%s\n' "$*" >"$TEST_CLEANER_ARGS"
exit "${TEST_CLEANER_EXIT:-0}"
EOF
chmod +x "$runtime/agent-worktree-clean" "$runtime/agent-worktree-maintain"

mode_of() {
  stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1"
}

default_codex_home="$temporary/default-codex"
mkdir -m 700 "$default_codex_home"
TEST_REGISTERED="$registered" \
TEST_CLEANER_ARGS="$temporary/default-cleaner.args" \
CODEX_HOME="$default_codex_home" \
"$runtime/agent-worktree-maintain" \
  --repo "$repo" \
  --codex-home "$default_codex_home" \
  --force
grep -q 'start count=1 ' "$default_codex_home/log/agent-worktree-maintain.log"
if grep -q -- '--skip-legacy-purge' "$temporary/default-cleaner.args"; then
  echo 'default maintainer unexpectedly disabled legacy purge' >&2
  exit 1
fi

run_maintainer() {
  TEST_REGISTERED="$registered" \
  TEST_CLEANER_ARGS="$temporary/cleaner.args" \
  TEST_CLEANER_EXIT="${TEST_CLEANER_EXIT:-0}" \
  CODEX_HOME="$codex_home" \
  "$runtime/agent-worktree-maintain" \
    --repo "$repo" \
    --codex-home "$codex_home" \
    --state-dir "$state_dir" \
    --force \
    --skip-legacy-purge
}

run_maintainer

log_file="$state_dir/log/agent-worktree-maintain.log"
grep -q 'start count=1 ' "$log_file"
grep -q -- "--repo $repo" "$temporary/cleaner.args"
grep -q -- "--codex-home $codex_home" "$temporary/cleaner.args"
grep -q -- '--apply' "$temporary/cleaner.args"
grep -q -- '--skip-legacy-purge' "$temporary/cleaner.args"
[[ ! -e "$codex_home/log/agent-worktree-maintain.log" ]]
[[ ! -e "$state_dir/locks/agent-worktree-maintain.lock" ]]
[[ "$(mode_of "$state_dir/log")" == "700" ]]
[[ "$(mode_of "$state_dir/locks")" == "700" ]]
[[ "$(mode_of "$log_file")" == "600" ]]

hardlink_state="$temporary/hardlink-state"
mkdir -m 700 "$hardlink_state" "$hardlink_state/log"
: >"$temporary/hardlink-anchor"
chmod 600 "$temporary/hardlink-anchor"
ln "$temporary/hardlink-anchor" "$hardlink_state/log/agent-worktree-maintain.log"
set +e
TEST_REGISTERED="$registered" \
TEST_CLEANER_ARGS="$temporary/cleaner.args" \
CODEX_HOME="$codex_home" \
"$runtime/agent-worktree-maintain" \
  --repo "$repo" \
  --codex-home "$codex_home" \
  --state-dir "$hardlink_state" \
  --force >"$temporary/hardlink-state.out" 2>&1
status=$?
set -e
[[ "$status" == "73" ]]
grep -q 'untrusted lock/state path' "$temporary/hardlink-state.out"

lock_dir="$state_dir/locks/agent-worktree-maintain.lock"
mkdir -m 700 "$lock_dir"
printf '%s\n' "$$" >"$lock_dir/pid"
chmod 600 "$lock_dir/pid"
run_maintainer
grep -q "already running (pid=$$)" "$log_file"
[[ -d "$lock_dir" ]]
rm "$lock_dir/pid"
rmdir "$lock_dir"

mkdir -m 700 "$lock_dir"
printf 'not-a-pid\n' >"$lock_dir/pid"
chmod 600 "$lock_dir/pid"
set +e
run_maintainer >"$temporary/malformed.out" 2>&1
status=$?
set -e
[[ "$status" == "73" ]]
grep -q 'untrusted lock/state path' "$temporary/malformed.out"
rm "$lock_dir/pid"
rmdir "$lock_dir"

mkdir -m 700 "$lock_dir"
printf '99999999\n' >"$lock_dir/pid"
chmod 600 "$lock_dir/pid"
run_maintainer
[[ ! -e "$lock_dir" ]]

chmod 755 "$state_dir"
set +e
run_maintainer >"$temporary/public-state.out" 2>&1
status=$?
set -e
[[ "$status" == "73" ]]
chmod 700 "$state_dir"

state_link="$temporary/state-link"
ln -s "$state_dir" "$state_link"
set +e
TEST_REGISTERED="$registered" \
TEST_CLEANER_ARGS="$temporary/cleaner.args" \
CODEX_HOME="$codex_home" \
"$runtime/agent-worktree-maintain" \
  --repo "$repo" \
  --codex-home "$codex_home" \
  --state-dir "$state_link" \
  --force >"$temporary/symlink-state.out" 2>&1
status=$?
set -e
[[ "$status" == "73" ]]

set +e
TEST_CLEANER_EXIT=42 run_maintainer
status=$?
set -e
[[ "$status" == "42" ]]
grep -q 'failed status=42' "$log_file"
[[ ! -e "$lock_dir" ]]

printf 'agent worktree maintainer tests passed\n'
