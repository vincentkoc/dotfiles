#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
retire="$root/bin/agent-worktree-ops/retire-agent-worktree-scheduler"
script_bash="${TEST_SCRIPT_BASH:-/bin/bash}"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

real_uid="$(id -u)"
current_label="com.vincentkoc.agent-worktree-ops"
old_label="com.vincentkoc.codex-worktree-maintain"
fake_bin="$temporary/bin"
mkdir -p "$fake_bin"

cat >"$fake_bin/uname" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 1 && "$1" == "-s" ]]
printf 'Darwin\n'
EOF

cat >"$fake_bin/id" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 1 && "$1" == "-u" ]]
printf '%s\n' "${TEST_UID:?}"
EOF

cat >"$fake_bin/launchctl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

state="${TEST_LAUNCHCTL_STATE:?}"
log="${TEST_LAUNCHCTL_LOG:?}"
uid_value="${TEST_UID:?}"
current_label="com.vincentkoc.agent-worktree-ops"
old_label="com.vincentkoc.codex-worktree-maintain"
canonical_lock="${TEST_CANONICAL_LOCK:?}"

require_canonical_lock() {
  local entries

  [[ ! -L "$canonical_lock" && -d "$canonical_lock" ]]
  [[ "$(stat -f '%Lp' "$canonical_lock")" == "700" ]]
  [[ ! -L "$canonical_lock/pid" && -f "$canonical_lock/pid" ]]
  [[ "$(stat -f '%Lp' "$canonical_lock/pid")" == "600" ]]
  [[ "$(cat "$canonical_lock/pid")" =~ ^[0-9]+$ ]]
  entries="$(find "$canonical_lock" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')"
  [[ "$entries" == "1" ]]
}

key_for() {
  printf '%s' "$1" | tr '/.' '__'
}

loaded_path() {
  printf '%s/loaded.%s\n' "$state" "$(key_for "$1")"
}

disabled_path() {
  printf '%s/disabled.%s\n' "$state" "$(key_for "$1")"
}

printf '%s\n' "$*" >>"$log"
command="$1"
shift

case "$command" in
  print)
    target="$1"
    if [[ "$target" == "user/$uid_value" ]]; then
      printf 'domain = %s\n' "$target"
      exit 0
    fi
    if [[ "$target" == "gui/$uid_value" ]]; then
      if [[ -f "$state/gui-unavailable" ]]; then
        printf 'Domain does not support specified action.\n' >&2
        exit 125
      fi
      if [[ -f "$state/gui-missing" ]]; then
        printf 'Bad request.\nCould not find domain for user gui: %s\n' "$uid_value" >&2
        exit 112
      fi
      printf 'domain = %s\n' "$target"
      exit 0
    fi
    if [[ "$target" == "gui/$uid_value/"* ]]; then
      if [[ -f "$state/gui-unavailable" ]]; then
        printf 'Domain does not support specified action.\n' >&2
        exit 125
      fi
      if [[ -f "$state/gui-missing" ]]; then
        printf 'Bad request.\nCould not find domain for user gui: %s\n' "$uid_value" >&2
        exit 112
      fi
    fi

    loaded="$(loaded_path "$target")"
    if [[ ! -f "$loaded" ]]; then
      exit 113
    fi
    case "$(cat "$loaded")" in
      idle)
        printf 'service = {\n\tstate = not running\n}\n'
        ;;
      running)
        printf 'service = {\n\tpid = 4242\n\tstate = running\n}\n'
        ;;
      *)
        printf 'service = {\n\tstate = unknown\n}\n'
        ;;
    esac
    ;;
  print-disabled)
    domain="$1"
    if [[ "$domain" == "system" \
      && -f "$state/create-canonical-lock-on-system-print" \
      && ! -f "$state/canonical-lock-created" ]]; then
      mkdir -p "$(dirname "$canonical_lock")"
      mkdir -m 0700 "$canonical_lock"
      (umask 077; printf '%s\n' "$$" >"$canonical_lock/pid")
      touch "$state/canonical-lock-created"
    fi
    printf 'disabled services = {\n'
    for label in "$current_label" "$old_label"; do
      if [[ -f "$(disabled_path "$domain/$label")" ]]; then
        printf '\t"%s" => disabled\n' "$label"
      fi
    done
    printf '}\n'
    ;;
  disable)
    require_canonical_lock
    [[ ! -f "$state/fail-disable" ]] || exit 98
    touch "$(disabled_path "$1")"
    ;;
  bootout)
    require_canonical_lock
    loaded="$(loaded_path "$1")"
    [[ -f "$loaded" ]] || exit 113
    unlink "$loaded"
    ;;
  bootstrap|enable)
    exit 99
    ;;
  *)
    exit 98
    ;;
esac
EOF
chmod 0755 "$fake_bin/uname" "$fake_bin/id" "$fake_bin/launchctl"

write_current_plist() {
  local home="$1"
  local target="$home/Library/LaunchAgents/$current_label.plist"

  mkdir -p "$(dirname "$target")"
  cat >"$target" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$current_label</string>

  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/nice</string>
    <string>-n</string>
    <string>20</string>
    <string>$home/Library/Application Support/agent-worktree-ops/agent-worktree-maintain</string>
  </array>

  <key>StartInterval</key>
  <integer>900</integer>

  <key>RunAtLoad</key>
  <true/>

  <key>StandardOutPath</key>
  <string>/tmp/$current_label.log</string>

  <key>StandardErrorPath</key>
  <string>/tmp/$current_label.log</string>
</dict>
</plist>
EOF
  chmod 0644 "$target"
}

new_case() {
  local name="$1"

  case_root="$temporary/$name"
  case_home="$case_root/home"
  case_state="$case_root/launchctl-state"
  case_log="$case_root/launchctl.log"
  case_receipts="$case_root/receipts"
  case_system="$case_root/system"
  case_plist="$case_home/Library/LaunchAgents/$current_label.plist"
  case_old_plist="$case_home/Library/LaunchAgents/$old_label.plist"
  case_lock="$case_home/.codex/locks/agent-worktree-maintain.lock"
  mkdir -p \
    "$case_home/Library/LaunchAgents" \
    "$case_home/.codex" \
    "$case_state" \
    "$case_system/Library/LaunchAgents" \
    "$case_system/Library/LaunchDaemons"
  write_current_plist "$case_home"
  case_expected_hash="$(shasum -a 256 "$case_plist" | awk '{print $1}')"
}

invoke_with_uid() {
  local uid_value="$1"
  shift

  HOME="$case_home" \
    PATH="$fake_bin:$PATH" \
    TEST_LAUNCHCTL_STATE="$case_state" \
    TEST_LAUNCHCTL_LOG="$case_log" \
    TEST_CANONICAL_LOCK="$case_lock" \
    TEST_UID="$uid_value" \
    AGENT_WORKTREE_RETIRE_STATE_DIR="$case_receipts" \
    AGENT_WORKTREE_RETIRE_TESTING=1 \
    AGENT_WORKTREE_RETIRE_TEST_EXPECTED_SHA256="$case_expected_hash" \
    AGENT_WORKTREE_RETIRE_TEST_SYSTEM_ROOT="$case_system" \
    "$script_bash" "$retire" "$@"
}

invoke() {
  invoke_with_uid "$real_uid" "$@"
}

mark_loaded() {
  local target="$1"
  local state="${2:-idle}"
  local key
  key="$(printf '%s' "$target" | tr '/.' '__')"
  printf '%s\n' "$state" >"$case_state/loaded.$key"
}

mark_disabled() {
  local target="$1"
  local key
  key="$(printf '%s' "$target" | tr '/.' '__')"
  touch "$case_state/disabled.$key"
}

assert_fails_with() {
  local expected="$1"
  shift
  local output="$case_root/failure.out"

  if "$@" >"$output" 2>&1; then
    printf 'expected failure containing: %s\n' "$expected" >&2
    exit 1
  fi
  grep -Fq "$expected" "$output"
}

new_case dry-run
dry_output="$(invoke)"
grep -Fq 'scheduler retirement check: safe to apply' <<<"$dry_output"
[[ -f "$case_plist" ]]
[[ ! -e "$case_receipts" ]]
[[ ! -e "$case_home/.codex/locks" ]]
if grep -Eq '^(disable|bootout|bootstrap|enable) ' "$case_log"; then
  printf 'default check mutated launchd state\n' >&2
  exit 1
fi
assert_fails_with 'choose exactly one operation' invoke --check --apply
[[ -f "$case_plist" && ! -e "$case_receipts" ]]

new_case apply-and-rollback
mkdir -p \
  "$case_home/Library/Application Support/agent-worktree-ops" \
  "$case_home/Library/Application Support/codex-worktree-maintain" \
  "$case_home/.codex/log" \
  "$case_home/Library/Mobile Documents/example"
touch \
  "$case_home/Library/Application Support/agent-worktree-ops/runtime.keep" \
  "$case_home/Library/Application Support/codex-worktree-maintain/runtime.keep" \
  "$case_home/.codex/log/history.keep" \
  "$case_home/Library/Mobile Documents/example/residue.keep"
cp "$case_plist" "$case_root/original.plist"
mark_loaded "user/$real_uid/$current_label" idle
mark_loaded "gui/$real_uid/$old_label" idle

apply_output="$(invoke --apply)"
receipt="$(sed -n 's/^receipt=//p' <<<"$apply_output")"
[[ -n "$receipt" && -d "$receipt" ]]
[[ "$(stat -f '%Lp' "$receipt")" == "700" ]]
backup="$receipt/$current_label.plist.backup"
[[ -f "$backup" && "$(stat -f '%Lp' "$backup")" == "600" ]]
cmp -s "$backup" "$case_root/original.plist"
[[ ! -e "$case_plist" ]]
[[ ! -e "$case_lock" ]]
grep -Fxq "bootout user/$real_uid/$current_label" "$case_log"
grep -Fxq "bootout gui/$real_uid/$old_label" "$case_log"
for domain in "user/$real_uid" "gui/$real_uid"; do
  for label in "$current_label" "$old_label"; do
    grep -Fxq "disable $domain/$label" "$case_log"
    [[ -f "$case_state/disabled.$(printf '%s' "$domain/$label" | tr '/.' '__')" ]]
  done
done
[[ -e "$case_home/Library/Application Support/agent-worktree-ops/runtime.keep" ]]
[[ -e "$case_home/Library/Application Support/codex-worktree-maintain/runtime.keep" ]]
[[ -e "$case_home/.codex/log/history.keep" ]]
[[ -e "$case_home/Library/Mobile Documents/example/residue.keep" ]]

receipt_count_before="$(find "$case_receipts" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
second_apply_output="$(invoke --apply)"
grep -Fq 'scheduler retirement: already retired' <<<"$second_apply_output"
receipt_count_after="$(find "$case_receipts" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
[[ "$receipt_count_before" == "$receipt_count_after" ]]

rollback_output="$(invoke --rollback "$receipt")"
grep -Fq 'labels remain disabled and unloaded' <<<"$rollback_output"
[[ -f "$case_plist" && "$(stat -f '%Lp' "$case_plist")" == "644" ]]
[[ "$(stat -f '%l' "$case_plist")" == "1" ]]
cmp -s "$case_plist" "$backup"
[[ ! -e "$case_lock" ]]
[[ -f "$backup" && "$(stat -f '%Lp' "$backup")" == "600" ]]
[[ -f "$receipt/rollback.txt" && "$(stat -f '%Lp' "$receipt/rollback.txt")" == "600" ]]
second_rollback_output="$(invoke --rollback "$receipt")"
grep -Fq 'scheduler rollback complete' <<<"$second_rollback_output"
[[ ! -e "$case_lock" ]]
if grep -Eq '^(bootstrap|enable) ' "$case_log"; then
  printf 'rollback must not re-enable or reload a legacy job\n' >&2
  exit 1
fi

new_case wrong-mode
chmod 0600 "$case_plist"
assert_fails_with 'unexpected mode: 600' invoke --apply
[[ -f "$case_plist" && ! -e "$case_receipts" ]]

new_case wrong-hash
printf '\n<!-- changed -->\n' >>"$case_plist"
assert_fails_with 'hash is not the audited deployment' invoke --apply
[[ -f "$case_plist" && ! -e "$case_receipts" ]]

new_case hard-link
ln "$case_plist" "$case_root/second-link.plist"
assert_fails_with 'unexpected link count: 2' invoke --apply
[[ -f "$case_plist" && ! -e "$case_receipts" ]]

new_case symlink
mv "$case_plist" "$case_root/real.plist"
ln -s "$case_root/real.plist" "$case_plist"
assert_fails_with 'is a symlink' invoke --apply
[[ -L "$case_plist" && ! -e "$case_receipts" ]]

new_case wrong-type
unlink "$case_plist"
mkdir "$case_plist"
assert_fails_with 'is not a regular file' invoke --apply
[[ -d "$case_plist" && ! -e "$case_receipts" ]]

new_case wrong-owner
wrong_uid=$((real_uid + 1))
assert_fails_with "unexpected owner uid: $real_uid" invoke_with_uid "$wrong_uid" --apply
[[ -f "$case_plist" && ! -e "$case_receipts" ]]

new_case old-plist-refusal
printf 'unreviewed\n' >"$case_old_plist"
assert_fails_with 'has no reviewed byte identity' invoke --apply
[[ -f "$case_plist" && -f "$case_old_plist" && ! -e "$case_receipts" ]]

new_case running-job
mark_loaded "user/$real_uid/$current_label" running
assert_fails_with 'legacy job is running and will not be interrupted' invoke --apply
[[ -f "$case_plist" && ! -e "$case_receipts" ]]
if grep -Eq '^(disable|bootout) ' "$case_log"; then
  printf 'running-job refusal must happen before mutation\n' >&2
  exit 1
fi

new_case unknown-job-state
mark_loaded "user/$real_uid/$current_label" unknown
assert_fails_with 'idle state is not proven' invoke --apply
[[ -f "$case_plist" && ! -e "$case_receipts" ]]

new_case system-job
mark_loaded "system/$old_label" idle
assert_fails_with 'protected legacy label is loaded in system scope' invoke --apply
[[ -f "$case_plist" && ! -e "$case_receipts" ]]

new_case system-disabled-state
mark_disabled "system/$current_label"
assert_fails_with 'protected legacy label exists in system disabled state' invoke --apply
[[ -f "$case_plist" && ! -e "$case_receipts" ]]

new_case system-plist
touch "$case_system/Library/LaunchDaemons/$current_label.plist"
assert_fails_with 'protected legacy plist exists in system scope' invoke --apply
[[ -f "$case_plist" && ! -e "$case_receipts" ]]

new_case maintainer-lock
mkdir -p "$case_lock"
lock_output="$case_root/lock.out"
set +e
invoke --apply >"$lock_output" 2>&1
lock_rc=$?
set -e
[[ "$lock_rc" == "73" ]]
grep -Fq 'maintainer lock exists' "$lock_output"
[[ -f "$case_plist" && ! -e "$case_receipts" ]]

new_case maintainer-lock-race
touch "$case_state/create-canonical-lock-on-system-print"
lock_race_output="$case_root/lock-race.out"
set +e
invoke --apply >"$lock_race_output" 2>&1
lock_race_rc=$?
set -e
[[ "$lock_race_rc" == "73" ]]
grep -Fq 'maintainer lock exists' "$lock_race_output"
[[ -f "$case_plist" && ! -e "$case_receipts" && -d "$case_lock" ]]

new_case release-lock-after-failure
touch "$case_state/fail-disable"
assert_fails_with 'failed to persistently disable' invoke --apply
[[ -f "$case_plist" && -d "$case_receipts" ]]
[[ ! -e "$case_lock" ]]

new_case gui-unavailable
touch "$case_state/gui-unavailable"
gui_output="$(invoke --apply)"
grep -Fq 'scheduler retirement complete' <<<"$gui_output"
[[ ! -e "$case_plist" ]]
grep -Fxq "disable user/$real_uid/$current_label" "$case_log"
grep -Fxq "disable user/$real_uid/$old_label" "$case_log"
if grep -Eq "^(disable|bootout|print-disabled) gui/$real_uid" "$case_log"; then
  printf 'GUI rc 125 must prevent GUI-domain mutation\n' >&2
  exit 1
fi

new_case gui-missing
touch "$case_state/gui-missing"
gui_missing_output="$(invoke --apply)"
grep -Fq 'scheduler retirement complete' <<<"$gui_missing_output"
[[ ! -e "$case_plist" ]]
grep -Fxq "disable user/$real_uid/$current_label" "$case_log"
grep -Fxq "disable user/$real_uid/$old_label" "$case_log"
if grep -Eq "^(disable|bootout|print-disabled) gui/$real_uid" "$case_log"; then
  printf 'GUI rc 112 must prevent GUI-domain mutation\n' >&2
  exit 1
fi

printf 'agent worktree scheduler retirement tests passed\n'
