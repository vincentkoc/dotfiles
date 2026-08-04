#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tt="$repo/bin/tt"
writer="$repo/bin/tt-codex-snapshot-writer"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

fake_bin="$temporary/bin"
mkdir -p "$fake_bin"

fake_tmux="$temporary/tmux"
tmux_log="$temporary/tmux.log"
snapshot="$temporary/codex-cockpit.tsv"
state_home="$temporary/state"
session_id="11111111-1111-1111-1111-111111111111"

cat >"$fake_tmux" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' "$*" >>"$TT_TEST_TMUX_LOG"
case "${1:-}" in
  list-panes)
    if [[ "${TT_TEST_TMUX_MODE:-normal}" == "hang" ]]; then
      sleep 3 &
      child=$!
      [[ -z "${TT_TEST_CHILD_PID_FILE:-}" ]] || printf '%s\n' "$child" >"$TT_TEST_CHILD_PID_FILE"
      wait "$child"
    fi
    printf 'cockpit:1.1\t100\tcode mode\tcodex\t/tmp/work\n'
    ;;
  list-windows)
    printf '@1\n'
    ;;
esac
SH
chmod +x "$fake_tmux"

cat >"$fake_bin/ps" <<SH
#!/usr/bin/env bash
printf '  100     1 /usr/local/bin/codex --no-alt-screen %s\\n' '$session_id'
SH
chmod +x "$fake_bin/ps"

cat >"$fake_bin/lsof" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${TT_TEST_LSOF_MODE:-id}" == "hang" ]]; then
  sleep 3 &
  child=$!
  [[ -z "${TT_TEST_CHILD_PID_FILE:-}" ]] || printf '%s\n' "$child" >"$TT_TEST_CHILD_PID_FILE"
  wait "$child"
fi
printf 'n%s/.codex/sessions/22222222-2222-2222-2222-222222222222.jsonl\n' "$HOME"
SH
chmod +x "$fake_bin/lsof"

bash -n "$tt"
python3 -m py_compile "$writer"
[[ -x "$writer" ]]
if grep -Eq 'python3 - .*<<' "$tt"; then
  printf 'tt still embeds the snapshot writer in a heredoc\n' >&2
  exit 1
fi

PATH="$fake_bin:$PATH" \
  HOME="$temporary/home" \
  XDG_STATE_HOME="$state_home" \
  TT_LOGIN_SHELL=/bin/sh \
  TT_TMUX_BIN="$fake_tmux" \
  TT_TEST_TMUX_LOG="$tmux_log" \
  "$tt" codex-snapshot "$snapshot" --quiet

grep -Fq $'cockpit:1.1\tcodex\t/tmp/work\tcode mode\tcodex\t11111111-1111-1111-1111-111111111111\texact' "$snapshot"
history_dir="$state_home/tt/history/codex-cockpit"
[[ "$(find "$history_dir" -maxdepth 1 -type f -name '*.tsv' | wc -l | tr -d ' ')" == "1" ]]
[[ -f "$state_home/tt/codex-cockpit.lock" ]]

# Codex session IDs missing from the process command still come from the
# opened session file. This keeps exact recovery behavior without relying on
# an inline Python subprocess.
cat >"$fake_bin/ps" <<'SH'
#!/usr/bin/env bash
printf '  100     1 /usr/local/bin/codex --no-alt-screen\n'
SH
chmod +x "$fake_bin/ps"

PATH="$fake_bin:$PATH" \
  HOME="$temporary/home" \
  XDG_STATE_HOME="$state_home" \
  TT_LOGIN_SHELL=/bin/sh \
  TT_TMUX_BIN="$fake_tmux" \
  TT_TEST_TMUX_LOG="$tmux_log" \
  "$tt" codex-snapshot "$snapshot" --quiet

grep -Fq $'cockpit:1.1\tcodex\t/tmp/work\tcode mode\tcodex\t22222222-2222-2222-2222-222222222222\texact' "$snapshot"

# Advisory locks are released by the kernel when a previous writer dies, even
# when its old PID metadata remains in the lock file.
printf '999999\t0\n' >"$state_home/tt/codex-cockpit.lock"
PATH="$fake_bin:$PATH" \
  HOME="$temporary/home" \
  XDG_STATE_HOME="$state_home" \
  TT_LOGIN_SHELL=/bin/sh \
  TT_TMUX_BIN="$fake_tmux" \
  TT_TEST_TMUX_LOG="$tmux_log" \
  "$tt" codex-snapshot "$snapshot" --quiet
[[ -f "$state_home/tt/codex-cockpit.lock" ]]

before_snapshot="$(cksum "$snapshot")"
before_history="$(find "$history_dir" -maxdepth 1 -type f -name '*.tsv' | wc -l | tr -d ' ')"
lock_ready="$temporary/lock-ready"
python3 - "$state_home/tt/codex-cockpit.lock" "$lock_ready" <<'PY' &
import fcntl
import pathlib
import sys
import time

lock = pathlib.Path(sys.argv[1])
lock.parent.mkdir(parents=True, exist_ok=True)
with lock.open("a+") as handle:
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    pathlib.Path(sys.argv[2]).touch()
    time.sleep(2)
PY
lock_holder=$!
for _ in $(seq 1 20); do
  [[ -f "$lock_ready" ]] && break
  sleep 0.1
done
[[ -f "$lock_ready" ]]
: >"$tmux_log"
PATH="$fake_bin:$PATH" \
  HOME="$temporary/home" \
  XDG_STATE_HOME="$state_home" \
  TT_LOGIN_SHELL=/bin/sh \
  TT_TMUX_BIN="$fake_tmux" \
  TT_TEST_TMUX_LOG="$tmux_log" \
  "$tt" codex-snapshot "$snapshot" --quiet
[[ "$(cksum "$snapshot")" == "$before_snapshot" ]]
[[ "$(find "$history_dir" -maxdepth 1 -type f -name '*.tsv' | wc -l | tr -d ' ')" == "$before_history" ]]
[[ ! -s "$tmux_log" ]]
wait "$lock_holder"

# A timed-out collection does not replace the current TSV or create history.
printf 'known-good\n' >"$snapshot"
rm -rf "$history_dir"
mkdir -p "$history_dir"
printf 'known-good\n' >"$history_dir/old.tsv"
before_snapshot="$(cksum "$snapshot")"
before_history="$(find "$history_dir" -maxdepth 1 -type f -name '*.tsv' | wc -l | tr -d ' ')"
timeout_child="$temporary/tmux-timeout-child"
if PATH="$fake_bin:$PATH" \
  HOME="$temporary/home" \
  XDG_STATE_HOME="$state_home" \
  TT_LOGIN_SHELL=/bin/sh \
  TT_SNAPSHOT_COMMAND_TIMEOUT_SECONDS=1 \
  TT_SNAPSHOT_TOTAL_TIMEOUT_SECONDS=1 \
  TT_TMUX_BIN="$fake_tmux" \
  TT_TEST_TMUX_LOG="$tmux_log" \
  TT_TEST_TMUX_MODE=hang \
  TT_TEST_CHILD_PID_FILE="$timeout_child" \
  "$tt" codex-snapshot "$snapshot" --quiet; then
  printf 'timed-out snapshot unexpectedly succeeded\n' >&2
  exit 1
fi
[[ "$(cksum "$snapshot")" == "$before_snapshot" ]]
[[ "$(find "$history_dir" -maxdepth 1 -type f -name '*.tsv' | wc -l | tr -d ' ')" == "$before_history" ]]
[[ -f "$state_home/tt/codex-cockpit.lock" ]]
[[ -f "$timeout_child" ]]
if kill -0 "$(cat "$timeout_child")" 2>/dev/null; then
  printf 'tmux timeout leaked a collector child\n' >&2
  exit 1
fi

# The optional lsof lookup is also deadline-bound. A timeout is not treated as
# "unresolved": it preserves the last complete map so recovery never loses IDs.
if PATH="$fake_bin:$PATH" \
  HOME="$temporary/home" \
  XDG_STATE_HOME="$state_home" \
  TT_LOGIN_SHELL=/bin/sh \
  TT_SNAPSHOT_COMMAND_TIMEOUT_SECONDS=1 \
  TT_SNAPSHOT_TOTAL_TIMEOUT_SECONDS=3 \
  TT_TMUX_BIN="$fake_tmux" \
  TT_TEST_TMUX_LOG="$tmux_log" \
  TT_TEST_LSOF_MODE=hang \
  TT_TEST_CHILD_PID_FILE="$temporary/lsof-timeout-child" \
  "$tt" codex-snapshot "$snapshot" --quiet; then
  printf 'timed-out lsof lookup unexpectedly succeeded\n' >&2
  exit 1
fi
[[ "$(cksum "$snapshot")" == "$before_snapshot" ]]
[[ "$(find "$history_dir" -maxdepth 1 -type f -name '*.tsv' | wc -l | tr -d ' ')" == "$before_history" ]]
[[ -f "$state_home/tt/codex-cockpit.lock" ]]
if kill -0 "$(cat "$temporary/lsof-timeout-child")" 2>/dev/null; then
  printf 'lsof timeout leaked a collector child\n' >&2
  exit 1
fi

printf 'tt_codex_snapshot_test=passed\n'
