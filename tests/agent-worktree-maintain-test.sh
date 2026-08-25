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
cp "$root/bin/agent-worktree-ops/worktree-storage-guard" "$runtime/worktree-storage-guard"

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
chmod +x "$runtime/agent-worktree-clean" "$runtime/agent-worktree-maintain" \
  "$runtime/worktree-storage-guard"

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
[[ ! -e "$codex_home/locks/agent-worktree-maintain.lock" ]]
[[ "$(mode_of "$state_dir/log")" == "700" ]]
[[ "$(mode_of "$state_dir/locks")" == "700" ]]
[[ "$(mode_of "$log_file")" == "600" ]]

cat >"$runtime/worktree-storage-guard" <<'EOF'
#!/usr/bin/env bash
printf 'storage unavailable\n' >&2
exit 78
EOF
chmod +x "$runtime/worktree-storage-guard"
set +e
run_maintainer >"$temporary/storage-failure.out" 2>&1
status=$?
set -e
[[ "$status" == "78" ]]
grep -q 'storage unavailable' "$temporary/storage-failure.out"
[[ ! -e "$state_dir/locks/agent-worktree-maintain.lock" ]]
[[ "$(grep -c 'start count=' "$log_file")" == "1" ]]
cp "$root/bin/agent-worktree-ops/worktree-storage-guard" "$runtime/worktree-storage-guard"
chmod +x "$runtime/worktree-storage-guard"

legacy_codex_home="$temporary/legacy-codex"
legacy_state_dir="$temporary/legacy-state"
legacy_lock_dir="$legacy_codex_home/locks/agent-worktree-maintain.lock"
mkdir -m 755 "$legacy_codex_home" "$legacy_codex_home/locks" "$legacy_lock_dir"
mkdir -m 700 "$legacy_state_dir"
printf '%s\n' "$$" >"$legacy_lock_dir/pid"
chmod 644 "$legacy_lock_dir/pid"
TEST_REGISTERED="$registered" \
TEST_CLEANER_ARGS="$temporary/legacy-cleaner.args" \
CODEX_HOME="$legacy_codex_home" \
"$runtime/agent-worktree-maintain" \
  --repo "$repo" \
  --codex-home "$legacy_codex_home" \
  --state-dir "$legacy_state_dir" \
  --force \
  --no-log >"$temporary/legacy-contention.out"
grep -q "already running (pid=$$)" "$temporary/legacy-contention.out"
[[ "$(mode_of "$legacy_lock_dir")" == "755" ]]
[[ "$(mode_of "$legacy_lock_dir/pid")" == "644" ]]
[[ ! -e "$legacy_state_dir/locks/agent-worktree-maintain.lock" ]]
rm "$legacy_lock_dir/pid"
rmdir "$legacy_lock_dir"

unsafe_codex_home="$temporary/unsafe-codex"
unsafe_state_dir="$temporary/unsafe-state"
unsafe_lock_dir="$unsafe_codex_home/locks/agent-worktree-maintain.lock"
mkdir -m 755 "$unsafe_codex_home" "$unsafe_codex_home/locks" "$unsafe_lock_dir"
chmod 777 "$unsafe_lock_dir"
mkdir -m 700 "$unsafe_state_dir"
printf '%s\n' "$$" >"$unsafe_lock_dir/pid"
chmod 644 "$unsafe_lock_dir/pid"
set +e
TEST_REGISTERED="$registered" \
TEST_CLEANER_ARGS="$temporary/unsafe-cleaner.args" \
CODEX_HOME="$unsafe_codex_home" \
"$runtime/agent-worktree-maintain" \
  --repo "$repo" \
  --codex-home "$unsafe_codex_home" \
  --state-dir "$unsafe_state_dir" \
  --force \
  --no-log >"$temporary/unsafe-lock.out" 2>&1
status=$?
set -e
[[ "$status" == "73" ]]
grep -q 'untrusted lock/state path' "$temporary/unsafe-lock.out"
[[ -d "$unsafe_lock_dir" ]]

probe_codex_home="$temporary/probe-codex"
probe_state_dir="$temporary/probe-state"
probe_lock_dir="$probe_codex_home/locks/agent-worktree-maintain.lock"
probe_bin="$temporary/probe-bin"
probe_cleaner_args="$temporary/probe-cleaner.args"
mkdir -m 700 "$probe_codex_home" "$probe_codex_home/locks" "$probe_lock_dir"
mkdir -m 700 "$probe_state_dir" "$probe_bin"
printf '%s\n' "$$" >"$probe_lock_dir/pid"
chmod 600 "$probe_lock_dir/pid"
cat >"$probe_bin/ps" <<'EOF'
#!/usr/bin/env bash
exit 2
EOF
chmod +x "$probe_bin/ps"
set +e
TEST_REGISTERED="$registered" \
TEST_CLEANER_ARGS="$probe_cleaner_args" \
CODEX_HOME="$probe_codex_home" \
PATH="$probe_bin:$PATH" \
"$runtime/agent-worktree-maintain" \
  --repo "$repo" \
  --codex-home "$probe_codex_home" \
  --state-dir "$probe_state_dir" \
  --force \
  --no-log >"$temporary/probe-error.out" 2>&1
status=$?
set -e
[[ "$status" == "73" ]]
grep -q 'untrusted lock/state path' "$temporary/probe-error.out"
[[ -d "$probe_lock_dir" ]]
[[ "$(cat "$probe_lock_dir/pid")" == "$$" ]]
[[ ! -e "$probe_cleaner_args" ]]

no_log_codex_home="$temporary/no-log-codex"
no_log_state_dir="$temporary/no-log-state"
fake_bin="$temporary/fake-bin"
release_log="$temporary/release-order"
mkdir -m 700 "$no_log_codex_home" "$no_log_state_dir"
mkdir "$fake_bin"
cat >"$fake_bin/rmdir" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$1" >>"$TEST_RMDIR_LOG"
exec /bin/rmdir "$@"
EOF
chmod +x "$fake_bin/rmdir"
TEST_REGISTERED="$registered" \
TEST_CLEANER_ARGS="$temporary/no-log-cleaner.args" \
TEST_RMDIR_LOG="$release_log" \
CODEX_HOME="$no_log_codex_home" \
PATH="$fake_bin:$PATH" \
"$runtime/agent-worktree-maintain" \
  --repo "$repo" \
  --codex-home "$no_log_codex_home" \
  --state-dir "$no_log_state_dir" \
  --force \
  --skip-legacy-purge \
  --no-log >"$temporary/no-log.out"
grep -q 'agent-worktree-maintain: start ' "$temporary/no-log.out"
grep -q 'agent-worktree-maintain: done' "$temporary/no-log.out"
[[ ! -e "$no_log_state_dir/log" ]]
[[ ! -e "$no_log_codex_home/log" ]]
expected_release_order="$no_log_state_dir/locks/agent-worktree-maintain.lock
$no_log_codex_home/locks/agent-worktree-maintain.lock"
[[ "$(cat "$release_log")" == "$expected_release_order" ]]
: >"$release_log"
set +e
TEST_REGISTERED="$registered" \
TEST_CLEANER_ARGS="$temporary/no-log-cleaner.args" \
TEST_CLEANER_EXIT=42 \
TEST_RMDIR_LOG="$release_log" \
CODEX_HOME="$no_log_codex_home" \
PATH="$fake_bin:$PATH" \
"$runtime/agent-worktree-maintain" \
  --repo "$repo" \
  --codex-home "$no_log_codex_home" \
  --state-dir "$no_log_state_dir" \
  --force \
  --skip-legacy-purge \
  --no-log >"$temporary/no-log-failure.out" 2>&1
status=$?
set -e
[[ "$status" == "42" ]]
grep -q 'failed status=42' "$temporary/no-log-failure.out"
[[ ! -e "$no_log_state_dir/log" ]]
[[ ! -e "$no_log_codex_home/log" ]]

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
[[ ! -e "$codex_home/locks/agent-worktree-maintain.lock" ]]
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
