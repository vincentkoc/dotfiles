#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tt="$repo/bin/tt"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

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

printf 'tt_lifecycle_test=passed\n'
