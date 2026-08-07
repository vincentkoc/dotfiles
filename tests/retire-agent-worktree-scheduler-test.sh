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

cat >"$fake_bin/mv" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

state="${TEST_LAUNCHCTL_STATE:?}"
current_plist="${TEST_CURRENT_PLIST:?}"
log="${TEST_MV_LOG:?}"
printf '%s\n' "$*" >>"$log"

if [[ $# -eq 2 && "$1" == "$current_plist" && ! -f "$state/mv-race-fired" ]]; then
  if [[ -f "$state/replace-before-quarantine" ]]; then
    /bin/mv "$current_plist" "$state/raced-audited.plist"
    printf 'replacement-before-quarantine\n' >"$current_plist"
    chmod 0644 "$current_plist"
    touch "$state/mv-race-fired"
  elif [[ -f "$state/replace-after-quarantine" ]]; then
    /bin/mv "$@"
    printf 'replacement-after-quarantine\n' >"$current_plist"
    chmod 0644 "$current_plist"
    touch "$state/mv-race-fired"
    exit 0
  elif [[ -f "$state/invalid-quarantine-and-occupied-target" ]]; then
    /bin/mv "$current_plist" "$state/raced-audited.plist"
    printf 'invalid-quarantined-replacement\n' >"$current_plist"
    chmod 0644 "$current_plist"
    /bin/mv "$@"
    printf 'occupied-current-replacement\n' >"$current_plist"
    chmod 0644 "$current_plist"
    touch "$state/mv-race-fired"
    exit 0
  fi
fi

exec /bin/mv "$@"
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
      if [[ -f "$state/gui-unavailable-live" ]]; then
        printf 'Could not print domain: 125: Domain does not support specified action\n' >&2
        exit 125
      fi
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
      if [[ -f "$state/gui-unavailable-live" ]]; then
        printf 'Could not print domain: 125: Domain does not support specified action\n' >&2
        exit 125
      fi
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
    if [[ -f "$state/drop-gui-disable" && "$1" == "gui/$uid_value/"* ]]; then
      exit 0
    fi
    touch "$(disabled_path "$1")"
    if [[ -f "$state/load-after-disable" && ! -f "$state/load-after-disable-fired" ]]; then
      printf 'idle\n' >"$(loaded_path "user/$uid_value/$current_label")"
      touch "$state/load-after-disable-fired"
    fi
    ;;
  bootout)
    exit 99
    ;;
  bootstrap|enable)
    exit 99
    ;;
  *)
    exit 98
    ;;
esac
EOF
chmod 0755 "$fake_bin/uname" "$fake_bin/id" "$fake_bin/mv" "$fake_bin/launchctl"

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
  case_mv_log="$case_root/mv.log"
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
    TEST_CURRENT_PLIST="$case_plist" \
    TEST_MV_LOG="$case_mv_log" \
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

clear_loaded() {
  local target="$1"
  local key
  key="$(printf '%s' "$target" | tr '/.' '__')"
  if [[ -f "$case_state/loaded.$key" ]]; then
    unlink "$case_state/loaded.$key"
  fi
}

mark_disabled() {
  local target="$1"
  local key
  key="$(printf '%s' "$target" | tr '/.' '__')"
  touch "$case_state/disabled.$key"
}

clear_disabled() {
  local target="$1"
  local key
  key="$(printf '%s' "$target" | tr '/.' '__')"
  if [[ -f "$case_state/disabled.$key" ]]; then
    unlink "$case_state/disabled.$key"
  fi
}

launchctl_mutation_count() {
  if [[ ! -f "$case_log" ]]; then
    printf '0\n'
    return
  fi
  grep -Ec '^(disable|bootout|bootstrap|enable) ' "$case_log" || true
}

assert_no_success_claim() {
  local output="$1"

  if grep -Eq 'scheduler rollback complete|labels remain disabled' "$output"; then
    printf 'failed rollback must not claim completion or durable disable state\n' >&2
    exit 1
  fi
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

assert_headless_apply_and_rollback_refusal() {
  local marker="$1"
  local output receipt mutations_before mutations_after

  touch "$case_state/$marker"
  output="$(invoke --apply)"
  grep -Fq 'scheduler retirement complete' <<<"$output"
  receipt="$(sed -n 's/^receipt=//p' <<<"$output")"
  [[ -n "$receipt" && -d "$receipt" && ! -e "$case_plist" ]]
  grep -Fxq "disable user/$real_uid/$current_label" "$case_log"
  grep -Fxq "disable user/$real_uid/$old_label" "$case_log"
  mutations_before="$(launchctl_mutation_count)"

  assert_fails_with 'rollback requires an available GUI launchd domain' \
    invoke --rollback "$receipt"
  mutations_after="$(launchctl_mutation_count)"
  [[ "$mutations_before" == "$mutations_after" ]]
  [[ ! -e "$case_plist" && ! -e "$receipt/rollback.txt" && ! -e "$case_lock" ]]
  assert_no_success_claim "$case_root/failure.out"
  if grep -Eq "^(disable|bootout|print-disabled) gui/$real_uid" "$case_log"; then
    printf 'unavailable GUI domain must never receive launchctl mutation\n' >&2
    exit 1
  fi
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

apply_output="$(invoke --apply)"
receipt="$(sed -n 's/^receipt=//p' <<<"$apply_output")"
[[ -n "$receipt" && -d "$receipt" ]]
[[ "$(stat -f '%Lp' "$receipt")" == "700" ]]
backup="$receipt/$current_label.plist.backup"
[[ -f "$backup" && "$(stat -f '%Lp' "$backup")" == "600" ]]
cmp -s "$backup" "$case_root/original.plist"
[[ ! -e "$case_plist" ]]
[[ ! -e "$case_lock" ]]
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

clear_disabled "user/$real_uid/$current_label"
touch "$case_state/load-after-disable"
assert_fails_with 'legacy job is loaded and will not be interrupted' \
  invoke --rollback "$receipt"
[[ ! -e "$case_plist" && ! -e "$case_lock" ]]
assert_no_success_claim "$case_root/failure.out"
unlink "$case_state/load-after-disable"
unlink "$case_state/load-after-disable-fired"
clear_loaded "user/$real_uid/$current_label"

clear_disabled "gui/$real_uid/$current_label"
touch "$case_state/drop-gui-disable"
assert_fails_with "legacy label is not persistently disabled: gui/$real_uid/$current_label" \
  invoke --rollback "$receipt"
[[ ! -e "$case_plist" && ! -e "$case_lock" ]]
[[ -f "$receipt/rollback.txt" && "$(stat -f '%Lp' "$receipt/rollback.txt")" == "600" ]]
grep -Fxq 'status=prepared' "$receipt/rollback.txt"
if grep -Fxq 'status=complete' "$receipt/rollback.txt"; then
  printf 'rollback must not complete before GUI disable persistence is proven\n' >&2
  exit 1
fi
assert_no_success_claim "$case_root/failure.out"
unlink "$case_state/drop-gui-disable"

rollback_output="$(invoke --rollback "$receipt")"
grep -Fq 'labels remain disabled and no matching jobs are loaded' <<<"$rollback_output"
[[ -f "$case_plist" && "$(stat -f '%Lp' "$case_plist")" == "644" ]]
[[ "$(stat -f '%l' "$case_plist")" == "1" ]]
cmp -s "$case_plist" "$backup"
[[ ! -e "$case_lock" ]]
[[ -f "$backup" && "$(stat -f '%Lp' "$backup")" == "600" ]]
[[ -f "$receipt/rollback.txt" && "$(stat -f '%Lp' "$receipt/rollback.txt")" == "600" ]]
second_rollback_output="$(invoke --rollback "$receipt")"
grep -Fq 'scheduler rollback complete' <<<"$second_rollback_output"
[[ ! -e "$case_lock" ]]
if grep -Eq '^(bootout|bootstrap|enable) ' "$case_log"; then
  printf 'retirement and rollback must not interrupt or load a legacy job\n' >&2
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
[[ ! -e "$case_mv_log" ]]
[[ -z "$(find "$case_home/Library/LaunchAgents" -maxdepth 1 -name '.agent-worktree-retire.*' -print -quit)" ]]

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

new_case idle-user-job
mark_loaded "user/$real_uid/$current_label" idle
assert_fails_with 'legacy job is loaded and will not be interrupted' invoke --apply
[[ -f "$case_plist" && ! -e "$case_receipts" ]]
if grep -Eq '^(disable|bootout) ' "$case_log"; then
  printf 'loaded-job refusal must happen before mutation\n' >&2
  exit 1
fi

new_case idle-gui-job
mark_loaded "gui/$real_uid/$old_label" idle
assert_fails_with 'legacy job is loaded and will not be interrupted' invoke --apply
[[ -f "$case_plist" && ! -e "$case_receipts" ]]

new_case running-job
mark_loaded "user/$real_uid/$current_label" running
assert_fails_with 'legacy job is loaded and will not be interrupted' invoke --apply
[[ -f "$case_plist" && ! -e "$case_receipts" ]]
if grep -Eq '^(disable|bootout) ' "$case_log"; then
  printf 'running-job refusal must happen before mutation\n' >&2
  exit 1
fi

new_case unknown-job-state
mark_loaded "user/$real_uid/$current_label" unknown
assert_fails_with 'legacy job is loaded and will not be interrupted' invoke --apply
[[ -f "$case_plist" && ! -e "$case_receipts" ]]

new_case job-load-race
touch "$case_state/load-after-disable"
assert_fails_with 'legacy job is loaded and will not be interrupted' invoke --apply
[[ -f "$case_plist" && -d "$case_receipts" && ! -e "$case_lock" ]]
[[ ! -e "$case_mv_log" ]]
if grep -Eq '^bootout ' "$case_log"; then
  printf 'job-load race must never trigger bootout\n' >&2
  exit 1
fi

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

new_case quarantine-pre-rename-replacement
cp "$case_plist" "$case_root/original.plist"
touch "$case_state/replace-before-quarantine"
assert_fails_with 'changed during atomic quarantine; moved file was restored without deletion' \
  invoke --apply
grep -Fxq 'replacement-before-quarantine' "$case_plist"
cmp -s "$case_state/raced-audited.plist" "$case_root/original.plist"
[[ ! -e "$case_lock" ]]
[[ -z "$(find "$case_home/Library/LaunchAgents" -maxdepth 1 -name '.agent-worktree-retire.*' -print -quit)" ]]
pre_race_receipt="$(find "$case_receipts" -mindepth 1 -maxdepth 1 -type d -print -quit)"
grep -Fxq 'status=prepared' "$pre_race_receipt/receipt.txt"
if grep -Fxq 'status=complete' "$pre_race_receipt/receipt.txt"; then
  printf 'pre-rename race must not complete retirement\n' >&2
  exit 1
fi

new_case quarantine-post-rename-replacement
touch "$case_state/replace-after-quarantine"
assert_fails_with 'replacement appeared at the current plist path and was preserved' invoke --apply
grep -Fxq 'replacement-after-quarantine' "$case_plist"
[[ ! -e "$case_lock" ]]
[[ -z "$(find "$case_home/Library/LaunchAgents" -maxdepth 1 -name '.agent-worktree-retire.*' -print -quit)" ]]
post_race_receipt="$(find "$case_receipts" -mindepth 1 -maxdepth 1 -type d -print -quit)"
grep -Fxq 'status=prepared' "$post_race_receipt/receipt.txt"
if grep -Fxq 'status=complete' "$post_race_receipt/receipt.txt"; then
  printf 'post-rename race must not complete retirement\n' >&2
  exit 1
fi

new_case quarantine-no-clobber
cp "$case_plist" "$case_root/original.plist"
touch "$case_state/invalid-quarantine-and-occupied-target"
assert_fails_with 'current path is occupied and moved entry was preserved' invoke --apply
grep -Fxq 'occupied-current-replacement' "$case_plist"
cmp -s "$case_state/raced-audited.plist" "$case_root/original.plist"
preserved_quarantine="$(
  find "$case_home/Library/LaunchAgents" -path '*/.agent-worktree-retire.*/*.plist' -type f -print -quit
)"
[[ -n "$preserved_quarantine" ]]
grep -Fxq 'invalid-quarantined-replacement' "$preserved_quarantine"
[[ ! -e "$case_lock" ]]

new_case gui-unavailable
assert_headless_apply_and_rollback_refusal gui-unavailable

new_case gui-missing
assert_headless_apply_and_rollback_refusal gui-missing

new_case gui-unavailable-live-check
touch "$case_state/gui-unavailable-live"
live_check_output="$(invoke --check)"
grep -Fq 'scheduler retirement check: safe to apply' <<<"$live_check_output"
[[ -f "$case_plist" && ! -e "$case_receipts" && ! -e "$case_mv_log" ]]
if grep -Eq '^(disable|bootout|bootstrap|enable) ' "$case_log"; then
  printf 'exact live rc 125 check must remain read-only\n' >&2
  exit 1
fi

new_case gui-unavailable-live
assert_headless_apply_and_rollback_refusal gui-unavailable-live

if grep -Fq 'bootout' "$retire"; then
  printf 'retirement command must never contain a bootout path\n' >&2
  exit 1
fi

printf 'agent worktree scheduler retirement tests passed\n'
