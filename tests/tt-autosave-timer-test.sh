#!/usr/bin/env bash
set -Eeuo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_tt="$repo/bin/tt"
source_writer="$repo/bin/tt-codex-snapshot-writer"
tmux_bin="${TT_TEST_REAL_TMUX_BIN:-$(command -v tmux || true)}"
temporary="$(mktemp -d)"
production_before="$temporary/production.before"
production_after="$temporary/production.after"
case_sockets=()
case_socket_paths=()
unrelated_pid=""

if [[ -z "$tmux_bin" ]]; then
  printf 'tt_autosave_timer_test=skipped (tmux unavailable)\n'
  exit 0
fi

production_snapshot() {
  {
    "$tmux_bin" list-sessions -F 's|#{session_id}|#{session_name}|#{session_windows}|#{session_attached}' 2>/dev/null || true
    "$tmux_bin" list-windows -a -F 'w|#{session_id}|#{window_id}|#{window_index}|#{window_name}|#{window_layout}' 2>/dev/null || true
    "$tmux_bin" list-panes -a -F 'p|#{session_id}|#{window_id}|#{pane_id}|#{pane_index}|#{pane_pid}|#{pane_current_command}|#{pane_current_path}' 2>/dev/null || true
  }
}

cleanup() {
  local index socket socket_path
  if [[ -n "$unrelated_pid" ]]; then
    kill "$unrelated_pid" 2>/dev/null || true
    wait "$unrelated_pid" 2>/dev/null || true
  fi
  for index in "${!case_sockets[@]}"; do
    socket="${case_sockets[$index]}"
    socket_path="${case_socket_paths[$index]:-}"
    "$tmux_bin" -L "$socket" kill-server 2>/dev/null || true
    if [[ -n "$socket_path" && "${socket_path##*/}" == "$socket" ]]; then
      rm -f -- "$socket_path"
    fi
  done
  rm -rf "$temporary"
}
trap cleanup EXIT

failure_diagnostics() {
  local status="$1"
  local line="$2"
  local command="$3"
  local file

  trap - ERR
  set +e
  printf 'tt_autosave_timer_test=failed status=%s line=%s command=%q\n' \
    "$status" "$line" "$command" >&2
  printf 'case_dir=%s socket=%s socket_path=%s\n' \
    "${CASE_DIR:-unset}" "${CASE_SOCKET:-unset}" "${CASE_SOCKET_PATH:-unset}" >&2
  for file in \
    "${CASE_EVENTS:-}" \
    "${CASE_STATE:-}/tt/codex-cockpit.timer.pid" \
    "${CASE_STATE:-}/tt/codex-cockpit.timer.log"; do
    if [[ -n "$file" && -f "$file" ]]; then
      printf '%s\n' "== $file ==" >&2
      sed -n '1,240p' "$file" >&2
    fi
  done
  if [[ -n "${CASE_SOCKET:-}" ]]; then
    printf '%s\n' "== tmux $CASE_SOCKET ==" >&2
    "$tmux_bin" -L "$CASE_SOCKET" list-sessions \
      -F 's|#{session_id}|#{session_name}|#{session_windows}|#{session_attached}' >&2
    "$tmux_bin" -L "$CASE_SOCKET" list-windows -a \
      -F 'w|#{session_id}|#{window_id}|#{window_index}|#{window_name}|#{window_layout}' >&2
    "$tmux_bin" -L "$CASE_SOCKET" list-panes -a \
      -F 'p|#{session_id}|#{window_id}|#{pane_id}|#{pane_index}|#{pane_pid}|#{pane_current_command}|#{pane_current_path}' >&2
  fi
  exit "$status"
}
trap 'failure_diagnostics "$?" "$LINENO" "$BASH_COMMAND"' ERR

wait_until() {
  local attempts="$1"
  shift
  local attempt=0
  while (( attempt < attempts )); do
    if "$@"; then
      return 0
    fi
    sleep 0.1
    attempt=$((attempt + 1))
  done
  return 1
}

portable_stat_value() {
  local bsd_format="$1"
  local gnu_format="$2"
  local path="$3"
  local value

  if value="$(stat -f "$bsd_format" -- "$path" 2>/dev/null)" &&
    [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$value"
    return 0
  fi
  if value="$(stat -c "$gnu_format" -- "$path" 2>/dev/null)" &&
    [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$value"
    return 0
  fi
  return 1
}

start_case() {
  local name="$1"
  CASE_DIR="$temporary/$name"
  CASE_SOCKET="tt-autosave-$name-$$"
  CASE_TMUX="$CASE_DIR/tmux"
  CASE_TT="$CASE_DIR/bin/tt"
  CASE_STATE="$CASE_DIR/state"
  CASE_FAIL="$CASE_DIR/fail-agent"
  CASE_FAIL_CODEX="$CASE_DIR/fail-codex"
  CASE_BUSY_CODEX="$CASE_DIR/busy-codex"
  CASE_FAIL_HOOK="$CASE_DIR/fail-hook"
  CASE_EVENTS="$CASE_DIR/events"
  mkdir -p "$CASE_DIR/bin"
  case_sockets+=("$CASE_SOCKET")

  {
    printf '#!/usr/bin/env bash\n'
    printf 'events=%q\n' "$CASE_EVENTS"
    printf 'fail_agent=%q\n' "$CASE_FAIL"
    printf 'fail_hook=%q\n' "$CASE_FAIL_HOOK"
    printf 'real_tmux=%q\n' "$tmux_bin"
    printf 'socket=%q\n' "$CASE_SOCKET"
    cat <<'SH'
printf 'tmux:%s\n' "${1:-}" >>"$events"
if [[ "${1:-}" == "list-panes" && -e "$fail_agent" ]]; then
  exit 1
fi
if [[ "${1:-}" == "set-hook" && -e "$fail_hook" ]]; then
  exit 1
fi
exec "$real_tmux" -L "$socket" "$@"
SH
  } >"$CASE_TMUX"
  chmod +x "$CASE_TMUX"
  cp "$source_tt" "$CASE_TT"
  cp "$source_writer" "$CASE_DIR/bin/tt-codex-snapshot-writer.real"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'events=%q\n' "$CASE_EVENTS"
    printf 'fail_codex=%q\n' "$CASE_FAIL_CODEX"
    printf 'busy_codex=%q\n' "$CASE_BUSY_CODEX"
    printf 'real_writer=%q\n' "$CASE_DIR/bin/tt-codex-snapshot-writer.real"
    cat <<'SH'
printf 'codex:start\n' >>"$events"
if [[ -e "$fail_codex" ]]; then
  exit 1
fi
if [[ -s "$busy_codex" ]]; then
  remaining="$(cat "$busy_codex")"
  if [[ "$remaining" =~ ^[0-9]+$ ]] && (( remaining > 0 )); then
    printf '%s\n' "$((remaining - 1))" >"$busy_codex"
    exit 75
  fi
fi
if "$real_writer" "$@"; then
  printf 'codex:success\n' >>"$events"
else
  exit $?
fi
SH
  } >"$CASE_DIR/bin/tt-codex-snapshot-writer"
  chmod +x "$CASE_TT" "$CASE_DIR/bin/tt-codex-snapshot-writer" "$CASE_DIR/bin/tt-codex-snapshot-writer.real"
  "$tmux_bin" -L "$CASE_SOCKET" new-session -d -s timer-test 'exec sleep 300'
  CASE_SOCKET_PATH="$("$tmux_bin" -L "$CASE_SOCKET" display-message -p '#{socket_path}')"
  case_socket_paths+=("$CASE_SOCKET_PATH")
  "$tmux_bin" -L "$CASE_SOCKET" set-option -t timer-test @tt_profile studio
}

case_tt() {
  HOME="$CASE_DIR/home" \
    XDG_STATE_HOME="$CASE_STATE" \
    TT_LOGIN_SHELL=/bin/sh \
    TT_TMUX_BIN="$CASE_TMUX" \
    TT_CODEX_SNAPSHOT_INTERVAL=1 \
    TT_CODEX_SNAPSHOT_INTERVAL_MIN=1 \
    TT_CODEX_SNAPSHOT_TIMER_LOG_MAX_BYTES=1024 \
    TT_SNAPSHOT_HISTORY_MAX=2 \
    TT_SNAPSHOT_TOTAL_TIMEOUT_SECONDS=5 \
    TT_CODEX_SNAPSHOT_LOCK_RETRY_ATTEMPTS=4 \
    TT_CODEX_SNAPSHOT_LOCK_RETRY_DELAY_SECONDS=0.1 \
    "$CASE_TT" "$@"
}

timer_state_file() {
  printf '%s\n' "$CASE_STATE/tt/codex-cockpit.timer.pid"
}

timer_pid() {
  awk -F '\t' '$1 == "v1" {print $2}' "$(timer_state_file)"
}

timer_token() {
  awk -F '\t' '$1 == "v1" {print $5}' "$(timer_state_file)"
}

timer_success_epoch() {
  awk -F '\t' '$1 == "v1" {print $6}' "$(timer_state_file)"
}

timer_max_age() {
  awk -F '\t' '$1 == "v1" {print $7}' "$(timer_state_file)"
}

timer_process_matches() {
  local pid="$1"
  local token="$2"
  ps -p "$pid" -o command= 2>/dev/null | grep -Fq " codex-snapshot-loop $token "
}

timer_has_server_ancestor() {
  local current="$1"
  local server="$2"
  local parent depth=0
  while [[ "$current" =~ ^[0-9]+$ ]] && (( current > 1 && depth < 64 )); do
    parent="$(ps -p "$current" -o ppid= 2>/dev/null | tr -d '[:space:]')"
    [[ "$parent" =~ ^[0-9]+$ ]] || return 1
    [[ "$parent" == "$server" ]] && return 0
    current="$parent"
    depth=$((depth + 1))
  done
  return 1
}

status_has() {
  local status
  if ! status="$(case_tt status 2>&1)"; then
    printf 'tt status failed unexpectedly:\n%s\n' "$status" >&2
    return 1
  fi
  [[ "$status" == *"$1"* ]]
}

autosave_residue_absent() {
  [[ ! -e "$(timer_state_file)" ]] &&
    [[ -z "$("$tmux_bin" -L "$CASE_SOCKET" show-options -gqv @tt_codex_snapshot_timer_token)" ]] &&
    ! "$tmux_bin" -L "$CASE_SOCKET" show-hooks -g 2>/dev/null | grep -q '\[9[01]\].*snapshot --quiet'
}

production_snapshot >"$production_before"

start_case missing-writer
chmod -x "$CASE_DIR/bin/tt-codex-snapshot-writer"
if case_tt autosave on 2>/dev/null; then
  printf 'autosave on succeeded without an executable writer\n' >&2
  exit 1
fi
autosave_residue_absent

start_case initial-agent-failure
touch "$CASE_FAIL"
if case_tt autosave on 2>/dev/null; then
  printf 'autosave on succeeded after agent collection failed\n' >&2
  exit 1
fi
autosave_residue_absent
[[ ! -e "$CASE_STATE/tt/codex-cockpit.tsv" ]]

start_case initial-codex-failure
touch "$CASE_FAIL_CODEX"
if case_tt autosave on 2>/dev/null; then
  printf 'autosave on succeeded after Codex collection failed\n' >&2
  exit 1
fi
autosave_residue_absent
[[ -f "$CASE_STATE/tt/agent-cockpit.tsv" ]]
[[ ! -e "$CASE_STATE/tt/codex-cockpit.tsv" ]]

start_case primary
printf '2\n' >"$CASE_BUSY_CODEX"

# The starter shell exits, but the tmux-server-owned timer remains live.
(case_tt autosave on)
state_file="$(timer_state_file)"
wait_until 50 test -f "$state_file"
pid="$(timer_pid)"
token="$(timer_token)"
server_pid="$("$tmux_bin" -L "$CASE_SOCKET" display-message -p '#{pid}')"
[[ "$(awk -F '\t' '{print $1, $3, $4}' "$state_file")" == "v1 $server_pid $(id -u)" ]]
[[ "$(timer_max_age)" == "7" ]]
[[ "$(portable_stat_value %Lp %a "$state_file")" == "600" ]]
kill -0 "$pid"
timer_process_matches "$pid" "$token"
timer_has_server_ancestor "$pid" "$server_pid"
status_has 'autosave: on'
status_has "timer: running pid=$pid"
status_has 'last success: '
[[ "$(grep -c '^codex:start$' "$CASE_EVENTS")" -ge 3 ]]
agent_line="$(grep -n '^tmux:list-panes$' "$CASE_EVENTS" | sed -n '1s/:.*//p')"
codex_line="$(grep -n '^codex:start$' "$CASE_EVENTS" | sed -n '1s/:.*//p')"
success_line="$(grep -n '^codex:success$' "$CASE_EVENTS" | sed -n '1s/:.*//p')"
hook_line="$(grep -n '^tmux:set-hook$' "$CASE_EVENTS" | sed -n '1s/:.*//p')"
(( agent_line < codex_line && codex_line < success_line && success_line < hook_line ))

# Concurrent enable calls converge on the same healthy generation.
case_tt autosave on &
on_one=$!
case_tt autosave on &
on_two=$!
wait "$on_one"
wait "$on_two"
[[ "$(timer_pid)" == "$pid" ]]
[[ "$("$tmux_bin" -L "$CASE_SOCKET" show-options -gqv @tt_codex_snapshot_timer_token)" == "$token" ]]
[[ "$(ps ax -o command= | grep -F " codex-snapshot-loop $token " | grep -v grep | wc -l | tr -d ' ')" == "1" ]]

# The loop survives an interrupted sleep and a failed snapshot collection.
before_failure_epoch="$(timer_success_epoch)"
touch "$CASE_FAIL"
kill -HUP "$pid"
wait_until 100 grep -Fq 'cycle-failed' "$CASE_STATE/tt/codex-cockpit.timer.log"
kill -0 "$pid"
wait_until 100 status_has 'autosave: degraded'
[[ "$(timer_pid)" == "$pid" ]]
[[ "$(timer_success_epoch)" == "$before_failure_epoch" ]]
rm -f "$CASE_FAIL"
wait_until 50 grep -Fq 'recovered' "$CASE_STATE/tt/codex-cockpit.timer.log"
wait_until 50 status_has 'autosave: on'
[[ "$(timer_pid)" == "$pid" ]]
(( $(timer_success_epoch) > before_failure_epoch ))

# Two timer intervals observe live state changes; history remains capped.
snapshot="$CASE_STATE/tt/codex-cockpit.tsv"
"$tmux_bin" -L "$CASE_SOCKET" select-pane -t timer-test:1.1 -T interval-one
wait_until 50 grep -Fq $'\tinterval-one\t' "$snapshot"
"$tmux_bin" -L "$CASE_SOCKET" select-pane -t timer-test:1.1 -T interval-two
wait_until 50 grep -Fq $'\tinterval-two\t' "$snapshot"
"$tmux_bin" -L "$CASE_SOCKET" select-pane -t timer-test:1.1 -T interval-three
wait_until 50 grep -Fq $'\tinterval-three\t' "$snapshot"
history_dir="$CASE_STATE/tt/history/codex-cockpit"
[[ "$(find "$history_dir" -maxdepth 1 -type f -name '*.tsv' | wc -l | tr -d ' ')" == "2" ]]

# Missing one hook is degraded, and enabling again repairs it transactionally.
"$tmux_bin" -L "$CASE_SOCKET" set-hook -gu 'after-new-window[91]'
status_has 'autosave: degraded'
case_tt autosave on
status_has 'autosave: on'
[[ "$(timer_pid)" == "$pid" ]]

# Racing enable/disable operations never create duplicate timers. A final
# explicit operation determines the state without signalling unrelated work.
case_tt autosave on &
race_on=$!
case_tt autosave off &
race_off=$!
wait "$race_on"
wait "$race_off"
case_tt autosave off
status_has 'autosave: off'
status_has 'timer: stopped'
[[ ! -e "$state_file" ]]

sleep 300 &
unrelated_pid=$!
printf '%s\n' "$unrelated_pid" >"$state_file"
case_tt autosave on
[[ "$(timer_pid)" != "$unrelated_pid" ]]
kill -0 "$unrelated_pid"
case_tt autosave off
[[ ! -e "$state_file" ]]

printf '%s\n' "$unrelated_pid" >"$state_file"
case_tt autosave off
kill -0 "$unrelated_pid"
[[ ! -e "$state_file" ]]

fake_token="unrelated-$RANDOM-$$"
"$tmux_bin" -L "$CASE_SOCKET" set-option -gq @tt_codex_snapshot_timer_token "$fake_token"
printf 'v1\t%s\t%s\t%s\t%s\n' "$unrelated_pid" "$server_pid" "$(id -u)" "$fake_token" >"$state_file"
case_tt autosave off
kill -0 "$unrelated_pid"
[[ ! -e "$state_file" ]]

# A hook installation failure rolls back both the desired generation and timer.
touch "$CASE_FAIL_HOOK"
if case_tt autosave on 2>/dev/null; then
  printf 'autosave on succeeded despite a hook installation failure\n' >&2
  exit 1
fi
rm -f "$CASE_FAIL_HOOK"
[[ ! -e "$state_file" ]]
[[ -z "$("$tmux_bin" -L "$CASE_SOCKET" show-options -gqv @tt_codex_snapshot_timer_token)" ]]
status_has 'autosave: off'

# A dead lock owner and stale state cannot prevent a fresh singleton.
lock_dir="$state_file.control.lock"
mkdir "$lock_dir"
printf '999999\t%s\t1\n' "$(id -u)" >"$lock_dir/owner"
case_tt autosave on
pid="$(timer_pid)"
token="$(timer_token)"
kill -0 "$pid"
status_has 'autosave: on'

# Repeated errors cap the lifecycle log instead of growing without bound.
export TT_CODEX_SNAPSHOT_TIMER_LOG_MAX_BYTES=220
touch "$CASE_FAIL"
sleep 5
rm -f "$CASE_FAIL"
log_size="$(portable_stat_value %z %s "$CASE_STATE/tt/codex-cockpit.timer.log")"
(( log_size <= 1024 ))

case_tt autosave off
wait_until 50 test ! -e "$state_file"
if kill -0 "$pid" 2>/dev/null; then
  printf 'autosave off left its validated timer alive\n' >&2
  exit 1
fi
status_has 'autosave: off'

# A disappearing isolated tmux server makes its timer self-clean.
start_case server-death
case_tt autosave on
death_state="$(timer_state_file)"
death_pid="$(timer_pid)"
kill -0 "$death_pid"
"$tmux_bin" -L "$CASE_SOCKET" kill-server
wait_until 100 test ! -e "$death_state"
wait_until 100 sh -c '! kill -0 "$1" 2>/dev/null' sh "$death_pid"
if kill -0 "$death_pid" 2>/dev/null; then
  printf 'timer survived the tmux server it belonged to\n' >&2
  exit 1
fi

production_snapshot >"$production_after"
cmp -s "$production_before" "$production_after"

printf 'tt_autosave_timer_test=passed\n'
