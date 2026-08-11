#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tt="$repo/bin/tt"
tmux_bin="${TT_TEST_REAL_TMUX_BIN:-$(command -v tmux || true)}"
temporary="$(mktemp -d)"
production_before="$temporary/production.before"
production_after="$temporary/production.after"
case_sockets=()
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
  local socket
  if [[ -n "$unrelated_pid" ]]; then
    kill "$unrelated_pid" 2>/dev/null || true
    wait "$unrelated_pid" 2>/dev/null || true
  fi
  for socket in "${case_sockets[@]}"; do
    "$tmux_bin" -L "$socket" kill-server 2>/dev/null || true
  done
  rm -rf "$temporary"
}
trap cleanup EXIT

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

start_case() {
  local name="$1"
  CASE_DIR="$temporary/$name"
  CASE_SOCKET="tt-autosave-$name-$$"
  CASE_TMUX="$CASE_DIR/tmux"
  CASE_STATE="$CASE_DIR/state"
  CASE_FAIL="$CASE_DIR/fail-snapshot"
  CASE_FAIL_HOOK="$CASE_DIR/fail-hook"
  mkdir -p "$CASE_DIR"
  case_sockets+=("$CASE_SOCKET")

  cat >"$CASE_TMUX" <<SH
#!/usr/bin/env bash
if [[ "\${1:-}" == "list-panes" && -e $(printf '%q' "$CASE_FAIL") ]]; then
  exit 1
fi
if [[ "\${1:-}" == "set-hook" && -e $(printf '%q' "$CASE_FAIL_HOOK") ]]; then
  exit 1
fi
exec $(printf '%q' "$tmux_bin") -L $(printf '%q' "$CASE_SOCKET") "\$@"
SH
  chmod +x "$CASE_TMUX"
  "$tmux_bin" -L "$CASE_SOCKET" new-session -d -s timer-test 'exec sleep 300'
}

case_tt() {
  HOME="$CASE_DIR/home" \
    XDG_STATE_HOME="$CASE_STATE" \
    TT_LOGIN_SHELL=/bin/sh \
    TT_TMUX_BIN="$CASE_TMUX" \
    TT_CODEX_SNAPSHOT_INTERVAL=1 \
    TT_CODEX_SNAPSHOT_INTERVAL_MIN=1 \
    TT_CODEX_SNAPSHOT_TIMER_LOG_MAX_BYTES=220 \
    TT_SNAPSHOT_HISTORY_MAX=2 \
    "$tt" "$@"
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
  status="$(case_tt status)"
  grep -Fq "$1" <<<"$status"
}

production_snapshot >"$production_before"

start_case primary

# The starter shell exits, but the tmux-server-owned timer remains live.
(case_tt autosave on)
state_file="$(timer_state_file)"
wait_until 50 test -f "$state_file"
pid="$(timer_pid)"
token="$(timer_token)"
server_pid="$("$tmux_bin" -L "$CASE_SOCKET" display-message -p '#{pid}')"
[[ "$(awk -F '\t' '{print $1, $3, $4}' "$state_file")" == "v1 $server_pid $(id -u)" ]]
kill -0 "$pid"
timer_process_matches "$pid" "$token"
timer_has_server_ancestor "$pid" "$server_pid"
status_has 'autosave: on'
status_has "timer: running pid=$pid"

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
kill -HUP "$pid"
sleep 0.2
kill -0 "$pid"
touch "$CASE_FAIL"
wait_until 50 grep -Fq 'snapshot-failed' "$CASE_STATE/tt/codex-cockpit.timer.log"
kill -0 "$pid"
rm -f "$CASE_FAIL"

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
touch "$CASE_FAIL"
sleep 5
rm -f "$CASE_FAIL"
log_size="$(stat -f %z "$CASE_STATE/tt/codex-cockpit.timer.log" 2>/dev/null ||
  stat -c %s "$CASE_STATE/tt/codex-cockpit.timer.log")"
(( log_size <= 220 ))

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
wait_until 50 test ! -e "$death_state"
if kill -0 "$death_pid" 2>/dev/null; then
  printf 'timer survived the tmux server it belonged to\n' >&2
  exit 1
fi

production_snapshot >"$production_after"
cmp -s "$production_before" "$production_after"

printf 'tt_autosave_timer_test=passed\n'
