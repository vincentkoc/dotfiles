#!/usr/bin/env bash
set -Eeuo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_tt="$repo/bin/tt"
source_writer="$repo/bin/tt-codex-snapshot-writer"
tmux_bin="${TT_TEST_REAL_TMUX_BIN:-$(command -v tmux || true)}"
temporary="$(mktemp -d)"
case_sockets=()
case_socket_paths=()
case_process_identities=()
case_stops=()
unrelated_pid=""
unset TMUX TMUX_PANE SSH_AUTH_SOCK SSH_AGENT_PID BASH_ENV ENV CODEX_HOME
export HOME="$temporary/home" XDG_CONFIG_HOME="$temporary/config"
export XDG_STATE_HOME="$temporary/state" TMUX_TMPDIR="$temporary/sockets"
mkdir -p "$HOME" "$TMUX_TMPDIR"

if [[ -z "$tmux_bin" ]]; then
  printf 'tt_autosave_timer_test=skipped (tmux unavailable)\n'
  exit 0
fi

fixture_process_identity() {
  python3 - "$@" <<'PY'
import errno
import json
from pathlib import Path
import socket
import subprocess
import sys

def process(pid):
    if Path("/proc").is_dir():
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            return None
        return {"pid": pid, "start": fields[19], "state": fields[0]}
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat=,lstart="],
        capture_output=True, text=True, timeout=2,
    )
    if result.returncode:
        return None
    state, start = result.stdout.strip().split(maxsplit=1)
    return {"pid": pid, "start": start, "state": state}

if sys.argv[1] == "capture":
    identities = [process(int(pid)) for pid in sys.argv[2:]]
    if any(identity is None for identity in identities):
        sys.exit("fixture process exited before identity capture")
    print(json.dumps(identities))
else:
    for identity in json.loads(sys.argv[2]):
        current = process(identity["pid"])
        if (current is not None and current["start"] == identity["start"]
                and not current["state"].startswith("Z")):
            sys.exit(1)
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(0.2)
        try:
            connection.connect(sys.argv[3])
        except OSError as error:
            if error.errno in (errno.ENOENT, errno.ECONNREFUSED):
                sys.exit(0)
            raise
    sys.exit(1)
PY
}

cleanup() {
  local index socket_path
  if [[ -n "$unrelated_pid" ]]; then
    kill "$unrelated_pid" 2>/dev/null || true
    wait "$unrelated_pid" 2>/dev/null || true
  fi
  for stop in "${case_stops[@]}"; do
    touch "$stop"
  done
  for index in "${!case_sockets[@]}"; do
    socket_path="${case_socket_paths[$index]:-}"
    [[ -n "$socket_path" ]] || continue
    for _ in {1..100}; do
      fixture_process_identity stopped "${case_process_identities[$index]}" "$socket_path" && break
      sleep 0.1
    done
    fixture_process_identity stopped "${case_process_identities[$index]}" "$socket_path" || {
      printf 'fixture server did not exit: %s\n' "$socket_path" >&2
      return 1
    }
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
  printf 'reverse_status=%s\n' "${reverse_status:-unset}" >&2
  for file in reverse-autosave.out reverse.out; do
    printf '== %s ==\n' "$file" >&2
    if [[ -n "${CASE_DIR:-}" && -f "${CASE_DIR:-}/$file" ]]; then
      head -c 16384 "${CASE_DIR:-}/$file" 2>&1 | sed -n '1,240p' >&2
    else
      printf 'missing\n' >&2
    fi
  done
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
  local identity_record fixture_server_pid fixture_pane_pid
  CASE_RETRY_ATTEMPTS="${2:-4}"
  CASE_RETRY_DELAY="${3:-0.1}"
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
  CASE_STOP="$CASE_DIR/stop"
  mkdir -p "$CASE_DIR/bin"
  case_sockets+=("$CASE_SOCKET")
  case_stops+=("$CASE_STOP")

  {
    printf '#!/usr/bin/env bash\n'
    printf 'events=%q\n' "$CASE_EVENTS"
    printf 'fail_agent=%q\n' "$CASE_FAIL"
    printf 'fail_codex=%q\n' "$CASE_FAIL_CODEX"
    printf 'fail_hook=%q\n' "$CASE_FAIL_HOOK"
    printf 'real_tmux=%q\n' "$tmux_bin"
    printf 'socket=%q\n' "$CASE_SOCKET"
    cat <<'SH'
printf 'tmux:%s\n' "${1:-}" >>"$events"
if [[ "${1:-}" == "list-panes" && -e "$fail_agent" ]]; then
  exit 1
fi
if [[ "${1:-}" == "list-panes" && "$*" == *'#{pane_dead}'* && -e "$fail_codex" ]]; then
  exit 1
fi
if [[ "${1:-}" == "set-hook" && -e "$fail_hook" ]]; then
  exit 1
fi
exec "$real_tmux" -L "$socket" -f /dev/null "$@"
SH
  } >"$CASE_TMUX"
  chmod +x "$CASE_TMUX"
  cp "$source_tt" "$CASE_TT"
  cp "$source_writer" "$CASE_DIR/bin/tt-codex-snapshot-writer.real"
  chmod u+w "$CASE_TT" "$CASE_DIR/bin/tt-codex-snapshot-writer.real"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'events=%q\n' "$CASE_EVENTS"
    printf 'fail_codex=%q\n' "$CASE_FAIL_CODEX"
    printf 'busy_codex=%q\n' "$CASE_BUSY_CODEX"
    printf 'real_writer=%q\n' "$CASE_DIR/bin/tt-codex-snapshot-writer.real"
    cat <<'SH'
if [[ "${1:-}" == "--control-query" ]]; then
  exec "$real_writer" "$@"
fi
printf 'codex:start\n' >>"$events"
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
  "$tmux_bin" -L "$CASE_SOCKET" -f /dev/null new-session -d -s timer-test \
    /bin/sh -c 'while [ ! -f "$1" ]; do sleep 0.1; done' sh "$CASE_STOP"
  identity_record="$("$tmux_bin" -L "$CASE_SOCKET" display-message -p '#{socket_path}|#{pid}|#{pane_pid}')"
  IFS='|' read -r CASE_SOCKET_PATH fixture_server_pid fixture_pane_pid <<<"$identity_record"
  case_socket_paths+=("$CASE_SOCKET_PATH")
  case_process_identities+=("$(fixture_process_identity capture "$fixture_server_pid" "$fixture_pane_pid")")
  "$tmux_bin" -L "$CASE_SOCKET" set-option -g base-index 1
  "$tmux_bin" -L "$CASE_SOCKET" set-window-option -g pane-base-index 1
  "$tmux_bin" -L "$CASE_SOCKET" move-window -s timer-test:0 -t timer-test:1
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
    TT_CODEX_SNAPSHOT_LOCK_RETRY_ATTEMPTS="$CASE_RETRY_ATTEMPTS" \
    TT_CODEX_SNAPSHOT_LOCK_RETRY_DELAY_SECONDS="$CASE_RETRY_DELAY" \
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

timer_uniqueness_observation() {
  python3 - "$@" <<'PY'
import json
import os
from pathlib import Path
import subprocess
import sys

owner, token, server, state_path = sys.argv[1:]
owner, server = int(owner), int(server)
report = {"expected_pid": owner, "expected_token": token, "fixture_server_pid": server,
          "saved_state": None, "count": None, "expected_owner": None, "ancestry": {},
          "errors": []}
try:
    report["saved_state"] = Path(state_path).read_text()
    saved = report["saved_state"].rstrip("\n").split("\t")
    if (len(saved) != 7 or saved[:5] != ["v1", str(owner), str(server), str(os.getuid()), token]
            or any(not value.isascii() or not value.isdigit() for value in saved[5:])):
        report["errors"].append("saved-state missing-or-ambiguous")
except (OSError, UnicodeError) as error:
    report["errors"].append(f"saved-state read-failed: {type(error).__name__}: {error}")

try:
    observation = subprocess.run(
        ["ps", "axww", "-o", "pid=,ppid=,uid=,pgid=,stat=,lstart=,command="],
        capture_output=True, text=True, timeout=5, env={**os.environ, "LC_ALL": "C"},
    )
    report["ps_status"], report["ps_stderr"] = observation.returncode, observation.stderr
    if observation.returncode or observation.stderr:
        raise RuntimeError("process-table read-failed")
    needle = f" codex-snapshot-loop {token} "
    # Preserve both filters from the original grep pipeline, including descendants.
    report["matching_rows"] = [
        line for line in observation.stdout.splitlines() if needle in line and "grep" not in line
    ]
    report["count"] = len(report["matching_rows"])
    rows, matches = {}, []
    for line in observation.stdout.splitlines():
        fields = line.split(None, 10)
        if len(fields) != 11:
            report["errors"].append(f"process-table malformed-row: {line!r}")
            continue
        pid, ppid, uid, pgid = map(int, fields[:4])
        row = {"pid": pid, "ppid": ppid, "uid": uid, "pgid": pgid,
               "state": fields[4], "start_lstart": " ".join(fields[5:10]), "argv": fields[10]}
        if pid in rows:
            report["errors"].append(f"process-table ambiguous duplicate pid: {pid}")
        rows[pid] = row
        if needle in line and "grep" not in line:
            matches.append(pid)
    report["expected_owner"] = rows.get(owner)
    for target in sorted({owner, server, *matches}):
        chain, seen = [], set()
        ancestry = {"rows": chain, "reaches_fixture_server": False}
        report["ancestry"][str(target)] = ancestry
        current = target
        for _ in range(64):
            if current in seen or current not in rows:
                reason = "ambiguous-cycle" if current in seen else "vanished-or-not-observed"
                report["errors"].append(f"ancestry pid={current}: {reason}")
                break
            seen.add(current)
            row = rows[current]
            if "sid" not in row:
                try:
                    row["sid"] = os.getsid(current)
                except OSError as error:
                    row["sid"] = "vanished" if isinstance(error, ProcessLookupError) else "read-failed"
                    report["errors"].append(f"sid pid={current}: {row['sid']}: {error}")
            chain.append(row)
            if current == server:
                ancestry["reaches_fixture_server"] = True
                break
            if row["ppid"] == 0:
                break
            current = row["ppid"]
        else:
            report["errors"].append(f"ancestry pid={target}: depth-limit")
except (OSError, UnicodeError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
    report["errors"].append(f"observation read-failed: {type(error).__name__}: {error}")

print("timer_uniqueness_observation=" + json.dumps(report, sort_keys=True), file=sys.stderr, flush=True)
sys.exit(0 if report["count"] == 1 and not report["errors"] else 1)
PY
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

timer_reuse_race_checks() (
  # Load functions without contacting tmux. Only external collection/identity
  # probes are stubbed; activation, TSV IO, comparison and control locks are real.
  export TT_TMUX_BIN=/bin/false
  source "$source_tt" ls
  autosave_snapshot_cycle() { return 0; }
  codex_snapshot_timer_state_available() { return 0; }
  agent_autosave_hooks_healthy() { return 0; }
  set_agent_autosave_hooks() { hook_calls=$((hook_calls + 1)); }
  start_codex_snapshot_timer_locked() { start_calls=$((start_calls + 1)); return 1; }
  codex_snapshot_timer_owner_alive() { codex_snapshot_timer_same_owner "$1" "$expected"; }
  codex_snapshot_timer_controls_match() {
    control_checks=$((control_checks + 1))
    if (( control_checks == 1 )); then
      # This runs after activation's first record read, before its reread.
      case "$race" in
        heartbeat|controls)
          codex_snapshot_timer_write_state 101 202 "$fixture_uid" fixture-token 12 19
          ;;
        stale)
          codex_snapshot_timer_write_state 101 202 "$fixture_uid" fixture-token 1 7
          ;;
        pid)
          codex_snapshot_timer_write_state 303 202 "$fixture_uid" fixture-token 12 19
          ;;
        server)
          codex_snapshot_timer_write_state 101 303 "$fixture_uid" fixture-token 12 19
          ;;
        uid)
          codex_snapshot_timer_write_state 101 202 "$((fixture_uid + 1))" fixture-token 12 19
          ;;
        token)
          codex_snapshot_timer_write_state 101 202 "$fixture_uid" other-token 12 19
          ;;
        malformed) printf 'not-a-timer-record\n' >"$race_state" ;;
        missing) rm "$race_state" ;;
      esac
      injected="$(cat "$race_state" 2>/dev/null || true)"
      return 0
    fi
    [[ "$race" != controls ]]
  }
  fixture_uid="$(id -u)"
  for race in heartbeat stale pid server uid token malformed missing controls; do
    export XDG_STATE_HOME="$temporary/reuse-race/$race"
    control_checks=0 hook_calls=0 start_calls=0
    codex_snapshot_timer_write_state 101 202 "$fixture_uid" fixture-token 11 7
    race_state="$(codex_cockpit_timer_pid_file)"
    expected="$(codex_snapshot_timer_read_state)"
    race_status=0
    activate_agent_autosave --explicit 2>/dev/null || race_status=$?
    [[ "$start_calls" == 0 ]]
    [[ ! -e "$(codex_cockpit_timer_lock_dir)" ]]
    [[ "$(cat "$race_state" 2>/dev/null || true)" == "$injected" ]]
    case "$race" in
      heartbeat|stale)
        [[ "$race_status" == 0 && "$hook_calls" == 1 && "$control_checks" == 2 ]]
        current="$(codex_snapshot_timer_read_state)"
        codex_snapshot_timer_same_owner "$expected" "$current"
        if [[ "$race" == heartbeat ]]; then
          [[ "$current" == *$'\t12\t19' ]]
          codex_snapshot_timer_write_state 101 202 "$fixture_uid" fixture-token 13 19
          [[ "$(codex_snapshot_timer_read_state)" == *$'\t13\t19' ]]
        else
          ! codex_snapshot_timer_success_fresh "$current"
        fi
        ;;
      *)
        [[ "$race_status" != 0 && "$hook_calls" == 0 ]]
        ;;
    esac
  done
)

mixed_version_checks() {
  local previous_tt="${TT_TEST_PREVIOUS_TT:?supply a frozen previous tt}"
  local previous_writer="${TT_TEST_PREVIOUS_WRITER:?supply a frozen previous writer}"
  local previous_protocol="${TT_TEST_PREVIOUS_PROTOCOL:?use records or capture}"
  local old_pid old_token epoch snapshot inode lock_pid before
  local lock_ready lock_release failures_before reverse_status

  case "$previous_protocol" in
    records|capture) ;;
    *) printf 'unknown previous snapshot protocol: %s\n' "$previous_protocol" >&2; return 1 ;;
  esac

  start_case mixed-version 5 1
  cp "$previous_tt" "$CASE_TT"
  cp "$previous_writer" "$CASE_DIR/bin/tt-codex-snapshot-writer.real"
  chmod +x "$CASE_TT" "$CASE_DIR/bin/tt-codex-snapshot-writer.real"
  (case_tt autosave on)
  wait_until 50 test -s "$(timer_state_file)"
  old_pid="$(timer_pid)"
  old_token="$(timer_token)"
  snapshot="$CASE_STATE/tt/codex-cockpit.tsv"
  inode="$(portable_stat_value %i %i "$CASE_STATE/tt/codex-cockpit.lock")"
  timer_process_matches "$old_pid" "$old_token"
  awk -F '\t' '!/^#/ && NF { if (NF != 8) exit 1; n++ } END { if (!n) exit 1 }' "$snapshot"

  # Certify the installed forward pair with its own unlocked publication state,
  # before changing either sibling used by the running timer.
  if [[ "$previous_protocol" == "records" ]]; then
    mkdir "$CASE_DIR/forward"
    cp "$source_tt" "$CASE_DIR/forward/tt"
    cp "$previous_writer" "$CASE_DIR/forward/tt-codex-snapshot-writer"
    chmod +x "$CASE_DIR/forward/tt" "$CASE_DIR/forward/tt-codex-snapshot-writer"
    HOME="$CASE_DIR/home" XDG_STATE_HOME="$CASE_DIR/forward/state" \
      TT_TMUX_BIN="$CASE_TMUX" TT_LOGIN_SHELL=/bin/sh \
      TT_SNAPSHOT_TOTAL_TIMEOUT_SECONDS=10 TT_SNAPSHOT_HISTORY_MAX=2 \
      "$CASE_DIR/forward/tt" autosave-snapshot >"$CASE_DIR/forward/autosave.out" 2>&1
    awk -F '\t' '!/^#/ && NF { if (NF != 8) exit 1; n++ } END { if (!n) exit 1 }' \
      "$CASE_DIR/forward/state/tt/codex-cockpit.tsv"
    [[ ! -e "$CASE_DIR/forward/state/tt/codex-cockpit.timer.pid" ]]
    [[ "$(timer_pid)" == "$old_pid" && "$(timer_token)" == "$old_token" ]]
    printf 'tt_forward_pair_autosave=passed protocol=records\n'
  else
    printf 'tt_forward_pair_autosave=incompatible protocol=capture\n'
  fi

  # The historical loop calls the writer directly; the installed loop retries
  # 75. Neither may publish or advance its heartbeat under this real flock.
  lock_ready="$CASE_DIR/lock-ready"
  lock_release="$CASE_DIR/lock-release"
  case_stops+=("$lock_release")
  failures_before="$(grep -c 'cycle-failed' "$CASE_STATE/tt/codex-cockpit.timer.log" || true)"
  python3 - "$CASE_STATE/tt/codex-cockpit.lock" "$lock_ready" "$lock_release" <<'PY' &
import fcntl
from pathlib import Path
import sys
import time
with open(sys.argv[1], "r+") as handle:
    fcntl.flock(handle, fcntl.LOCK_EX)
    Path(sys.argv[2]).touch()
    deadline = time.monotonic() + 30
    while not Path(sys.argv[3]).exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not Path(sys.argv[3]).exists():
        sys.exit("fixture publication lock expired before release")
PY
  lock_pid=$!
  wait_until 50 test -f "$lock_ready"
  mixed_cycle_failed() {
    local failures
    failures="$(grep -c 'cycle-failed' "$CASE_STATE/tt/codex-cockpit.timer.log" || true)"
    (( ${failures:-0} > ${failures_before:-0} ))
  }
  wait_until 100 mixed_cycle_failed
  epoch="$(timer_success_epoch)"
  before="$(cksum "$snapshot")"
  # Replace both siblings under one lock, caller first. This also protects the
  # historical writer's incompatible recursive snapshot-capture interval.
  cp "$source_tt" "$CASE_TT.next"
  chmod u+w "$CASE_TT.next"
  mv "$CASE_TT.next" "$CASE_TT"
  if CASE_RETRY_ATTEMPTS=1 case_tt codex-snapshot "$snapshot" --quiet; then
    printf 'new caller and old writer bypassed publication lock\n' >&2
    return 1
  else
    [[ "$?" == "75" ]]
  fi
  printf 'mixed:1.1\tcodex\t%s\tworker\tcodex\t11111111-1111-1111-1111-111111111111\texact\tignored\n' \
    "$CASE_DIR" >"$CASE_DIR/eight-agent.tsv"
  case_tt codex-restore all "$CASE_DIR/eight-agent.tsv" >"$CASE_DIR/eight-preview"
  grep -Fq $'mixed:1.1\tcodex\texact\tignored' "$CASE_DIR/eight-preview"
  cp "$source_writer" "$CASE_DIR/bin/tt-codex-snapshot-writer.real.next"
  chmod u+w "$CASE_DIR/bin/tt-codex-snapshot-writer.real.next"
  mv "$CASE_DIR/bin/tt-codex-snapshot-writer.real.next" "$CASE_DIR/bin/tt-codex-snapshot-writer.real"
  if HOME="$CASE_DIR/home" XDG_STATE_HOME="$CASE_STATE" TT_TMUX_BIN="$CASE_TMUX" \
    "$CASE_DIR/bin/tt-codex-snapshot-writer" --autosave "$snapshot"; then
    printf 'contended writer unexpectedly published\n' >&2
    return 1
  else
    [[ "$?" == "75" ]]
  fi
  wait_until 50 grep -Fq 'cycle-failed' "$CASE_STATE/tt/codex-cockpit.timer.log"
  [[ "$(cksum "$snapshot")" == "$before" ]]
  [[ "$(timer_success_epoch)" == "$epoch" ]]
  [[ "$(portable_stat_value %i %i "$CASE_STATE/tt/codex-cockpit.lock")" == "$inode" ]]
  touch "$lock_release"
  wait "$lock_pid"
  mixed_progressed() { (( $(timer_success_epoch) > epoch )); }
  wait_until 100 mixed_progressed
  [[ "$(timer_pid)" == "$old_pid" && "$(timer_token)" == "$old_token" ]]
  timer_process_matches "$old_pid" "$old_token"
  awk -F '\t' '!/^#/ && NF { if (NF != 10) exit 1; n++ } END { if (!n) exit 1 }' "$snapshot"
  printf 'mixed:1.1\tcodex\t%s\tworker\tcodex\t11111111-1111-1111-1111-111111111111\texact\tignored\texited\t137\n' \
    "$CASE_DIR" >"$CASE_DIR/ten-agent.tsv"
  case_tt codex-restore all "$CASE_DIR/ten-agent.tsv" >"$CASE_DIR/ten-preview"
  grep -Fq $'mixed:1.1\tcodex\texact\tignored' "$CASE_DIR/ten-preview"
  [[ ! -e "$CASE_DIR/bin/task-runtime" && ! -e "$CASE_DIR/bin/codex-hooks" ]]

  # Independently exercise the reverse pair without touching the timer's
  # siblings. An older tt lacks topology-authorize, so direct recovery refuses.
  mkdir "$CASE_DIR/reverse"
  cp "$previous_tt" "$CASE_DIR/reverse/tt"
  cp "$source_writer" "$CASE_DIR/reverse/tt-codex-snapshot-writer"
  chmod u+x "$CASE_DIR/reverse/tt" "$CASE_DIR/reverse/tt-codex-snapshot-writer"
  [[ -x "$CASE_DIR/reverse/tt" && -x "$CASE_DIR/reverse/tt-codex-snapshot-writer" ]]
  printf 'preserve prior snapshot\n' >"$CASE_DIR/reverse/evidence.tsv"
  reverse_status=0
  HOME="$CASE_DIR/home" XDG_STATE_HOME="$CASE_DIR/reverse/state" TT_TMUX_BIN="$CASE_TMUX" \
    python3 "$CASE_DIR/reverse/tt-codex-snapshot-writer" --autosave "$CASE_DIR/reverse/evidence.tsv" \
    >"$CASE_DIR/reverse-autosave.out" 2>&1 || reverse_status=$?
  case "$previous_protocol" in
    records)
      [[ "$reverse_status" == "0" ]]
      awk -F '\t' '!/^#/ && NF { if (NF != 10) exit 1; n++ } END { if (!n) exit 1 }' \
        "$CASE_DIR/reverse/evidence.tsv"
      ;;
    capture)
      [[ "$reverse_status" == "1" ]]
      grep -Fxq 'preserve prior snapshot' "$CASE_DIR/reverse/evidence.tsv"
      ;;
  esac
  if HOME="$CASE_DIR/home" XDG_STATE_HOME="$CASE_STATE" TT_TMUX_BIN="$CASE_TMUX" \
    CODEX_THREAD_ID=fixture python3 "$CASE_DIR/reverse/tt-codex-snapshot-writer" \
    --recover-cold codex "$CASE_DIR/ten-agent.tsv" mixed 1 \
    >"$CASE_DIR/reverse.out" 2>&1; then
    printf 'older caller authorized the new recovery helper\n' >&2
    return 1
  fi
  grep -Fq 'tt recovery refused:' "$CASE_DIR/reverse.out"
  if grep -Eq '^tmux:(new-session|new-window|split-window|respawn-pane|kill-server|kill-session)$' "$CASE_EVENTS"; then
    printf 'mixed-version recovery mutated topology\n' >&2
    return 1
  fi
  [[ "$(timer_pid)" == "$old_pid" && "$(timer_token)" == "$old_token" ]]
  case_tt autosave off
  wait_until 50 autosave_residue_absent
  mixed_timer_stopped() { ! timer_process_matches "$old_pid" "$old_token"; }
  wait_until 50 mixed_timer_stopped
  printf 'tt_mixed_version_timer_test=passed protocol=%s\n' "$previous_protocol"
}

if [[ "${1:-}" == "--mixed-version-only" ]]; then
  mixed_version_checks
  exit
fi

timer_reuse_race_checks

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
[[ "$(grep -c '^codex:start$' "$CASE_EVENTS")" == "1" ]]

start_case initial-codex-failure
touch "$CASE_FAIL_CODEX"
if case_tt autosave on 2>/dev/null; then
  printf 'autosave on succeeded after Codex collection failed\n' >&2
  exit 1
fi
autosave_residue_absent
[[ ! -e "$CASE_STATE/tt/agent-cockpit.tsv" ]]
[[ ! -e "$CASE_STATE/tt/codex-cockpit.tsv" ]]
[[ "$(grep -c '^codex:start$' "$CASE_EVENTS")" == "1" ]]

# Persistent contention exhausts the configured budget without enabling autosave.
start_case initial-busy
printf '9\n' >"$CASE_BUSY_CODEX"
busy_status=0
case_tt autosave on 2>/dev/null || busy_status=$?
[[ "$busy_status" == "75" ]]
[[ "$(grep -c '^codex:start$' "$CASE_EVENTS")" == "4" ]]
[[ "$(cat "$CASE_BUSY_CODEX")" == "5" ]]
autosave_residue_absent
[[ ! -e "$CASE_STATE/tt/agent-cockpit.tsv" ]]
[[ ! -e "$CASE_STATE/tt/codex-cockpit.tsv" ]]

# Real concurrent collectors use the production retry budget, including the timer.
start_case primary 5 1
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
(( codex_line < agent_line && agent_line < success_line && success_line < hook_line ))

# Concurrent enable calls converge on the same healthy generation.
before_concurrent_epoch="$(timer_success_epoch)"
case_tt autosave on &
on_one=$!
case_tt autosave on &
on_two=$!
wait "$on_one"
wait "$on_two"
[[ "$(timer_pid)" == "$pid" ]]
[[ "$("$tmux_bin" -L "$CASE_SOCKET" show-options -gqv @tt_codex_snapshot_timer_token)" == "$token" ]]
timer_uniqueness_observation "$pid" "$token" "$server_pid" "$state_file"
(( $(timer_success_epoch) >= before_concurrent_epoch ))
timer_progressed() { (( $(timer_success_epoch) > before_concurrent_epoch )); }
wait_until 50 timer_progressed

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
"$tmux_bin" -L "$CASE_SOCKET" set-hook -gu 'after-new-window[90]'
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

# Stale locks are retained: recovery cannot race another contender's owner file.
lock_dir="$state_file.control.lock"
mkdir "$lock_dir"
printf '999999\t%s\t1\n' "$(id -u)" >"$lock_dir/owner"
if case_tt autosave on; then
  printf 'autosave stole a stale control lock\n' >&2
  exit 1
fi
grep -q '^999999' "$lock_dir/owner"
rm "$lock_dir/owner"
rmdir "$lock_dir"
case_tt autosave on
pid="$(timer_pid)"
token="$(timer_token)"
kill -0 "$pid"
status_has 'autosave: on'

# Repeated errors cap the lifecycle log instead of growing without bound.
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
touch "$CASE_STOP"
wait_until 100 test ! -e "$death_state"
wait_until 100 sh -c '! kill -0 "$1" 2>/dev/null' sh "$death_pid"
if kill -0 "$death_pid" 2>/dev/null; then
  printf 'timer survived the tmux server it belonged to\n' >&2
  exit 1
fi

printf 'tt_autosave_timer_test=passed\n'
