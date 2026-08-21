#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tt="$repo/bin/tt"
temporary="$(mktemp -d)"
live_tmux_bin=""
live_tmux_socket=""
live_tmux_tmpdir=""
live_tmux_session=""

cleanup() {
  if [[ -n "$live_tmux_bin" && -n "$live_tmux_socket" && -n "$live_tmux_session" ]]; then
    TMUX_TMPDIR="$live_tmux_tmpdir" \
      "$live_tmux_bin" -L "$live_tmux_socket" kill-session -t "$live_tmux_session" \
      >/dev/null 2>&1 || true
  fi
  [[ -z "$live_tmux_tmpdir" ]] || rm -rf "$live_tmux_tmpdir"
  rm -rf "$temporary"
}
trap cleanup EXIT

fake_tmux="$temporary/tmux"
tmux_log="$temporary/tmux.log"

cat >"$fake_tmux" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${TT_TEST_TMUX_LOG:-}" ]]; then
  for arg in "$@"; do
    printf '[%s]' "$arg" >>"$TT_TEST_TMUX_LOG"
  done
  printf '\n' >>"$TT_TEST_TMUX_LOG"
fi

if [[ "$*" == *"list-panes -a -F"* && "$*" == *"@tt_profile"* ]]; then
  printf 'factory2\tstudio\t1\t1\t6\t/tmp/work\t"%s codex --no-alt-screen; exec /bin/sh -il"\tcodex\ttest\ttest\n' "${TT_TEST_AGENT_PREFIX:?}"
elif [[ "$*" == *"list-panes -a -F"* && "$*" == *"session_name}:#{window_index}.#{pane_index}"* ]]; then
  printf 'cockpit:1.1\n'
fi
SH
chmod +x "$fake_tmux"

session_id="11111111-1111-1111-1111-111111111111"
snapshot="$temporary/codex.tsv"
printf '# target\tkind\tcwd\ttitle\tcurrent_command\tsession_id\tstatus\trestore\n' >"$snapshot"
printf 'cockpit:1.1\tcodex\t/tmp/work\ttest\tcodex\t%s\texact\tcd /tmp/work && codex resume --no-alt-screen %s\n' \
  "$session_id" "$session_id" >>"$snapshot"

HOME="$temporary/home" \
  TT_LOGIN_SHELL=/bin/sh \
  TT_TMUX_BIN="$fake_tmux" \
  TT_TEST_TMUX_LOG="$tmux_log" \
  "$tt" codex-restore pane cockpit:1.1 "$snapshot" --execute >/dev/null

grep -Fq "[respawn-pane][-k][-t][cockpit:1.1][cd /tmp/work && codex resume --no-alt-screen $session_id; exec /bin/sh -il]" "$tmux_log"

agent_state="$temporary/agent-state.tsv"
# shellcheck disable=SC2016
agent_prefix='env -u NO_COLOR -u TERMINFO CLICOLOR=1 CLICOLOR_FORCE=1 FORCE_COLOR=3 COLORTERM=truecolor TERMINFO_DIRS=$HOME/.terminfo:/usr/share/terminfo'
HOME="$temporary/home" \
  XDG_CONFIG_HOME="$temporary/config" \
  TT_LOGIN_SHELL=/bin/sh \
  TT_TMUX_BIN="$fake_tmux" \
  TT_TEST_AGENT_PREFIX="$agent_prefix" \
  "$tt" snapshot "$agent_state" --quiet

grep -Fq $'\tcodex --no-alt-screen' "$agent_state"
if grep -Fq '; exec /bin/sh -il' "$agent_state"; then
  printf 'agent snapshot retained the shell fallback suffix\n' >&2
  exit 1
fi

live_tmux_bin="$(command -v tmux)"
live_tmux_socket="s$$"
live_tmux_tmpdir="$(mktemp -d /tmp/tt-status.XXXXXX)"
live_tmux_session="tt-status-$$"
live_tmux_wrapper="$temporary/live-tmux"
status_home="$temporary/status-home"
status_state="$temporary/status-state"
mkdir -p "$live_tmux_tmpdir"
mkdir -p "$status_home"
cat >"$live_tmux_wrapper" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec "$TT_TEST_REAL_TMUX" -L "$TT_TEST_TMUX_SOCKET" "$@"
SH
chmod +x "$live_tmux_wrapper"

HOME="$status_home" \
  TMUX_TMPDIR="$live_tmux_tmpdir" \
  "$live_tmux_bin" -L "$live_tmux_socket" -f /dev/null \
  new-session -d -s "$live_tmux_session"

history_dir="$status_state/tt/history/codex-cockpit"
[[ ! -e "$history_dir" ]]
status_output="$(
  HOME="$status_home" \
    XDG_STATE_HOME="$status_state" \
    TMUX_TMPDIR="$live_tmux_tmpdir" \
    TT_TMUX_BIN="$live_tmux_wrapper" \
    TT_TEST_REAL_TMUX="$live_tmux_bin" \
    TT_TEST_TMUX_SOCKET="$live_tmux_socket" \
    "$tt" status
)"
grep -Fxq 'history: 0/864 codex-cockpit files' <<<"$status_output"
grep -Fq 'agents: ' <<<"$status_output"
[[ ! -e "$history_dir" ]]

mkdir -p "$history_dir"
: >"$history_dir/one.tsv"
: >"$history_dir/two.tsv"
: >"$history_dir/ignored.txt"
status_output="$(
  HOME="$status_home" \
    XDG_STATE_HOME="$status_state" \
    TMUX_TMPDIR="$live_tmux_tmpdir" \
    TT_TMUX_BIN="$live_tmux_wrapper" \
    TT_TEST_REAL_TMUX="$live_tmux_bin" \
    TT_TEST_TMUX_SOCKET="$live_tmux_socket" \
    "$tt" status
)"
grep -Fxq 'history: 2/864 codex-cockpit files' <<<"$status_output"

rm -rf "$history_dir"
mkdir -p "$(dirname "$history_dir")"
: >"$history_dir"
if HOME="$status_home" \
  XDG_STATE_HOME="$status_state" \
  TMUX_TMPDIR="$live_tmux_tmpdir" \
  TT_TMUX_BIN="$live_tmux_wrapper" \
  TT_TEST_REAL_TMUX="$live_tmux_bin" \
  TT_TEST_TMUX_SOCKET="$live_tmux_socket" \
  "$tt" status >"$temporary/status-invalid-history.out" 2>&1; then
  printf 'tt status accepted a non-directory history path\n' >&2
  exit 1
fi

TMUX_TMPDIR="$live_tmux_tmpdir" \
  "$live_tmux_bin" -L "$live_tmux_socket" kill-session -t "$live_tmux_session"
live_tmux_session=""

printf 'tt_lifecycle_test=passed\n'
