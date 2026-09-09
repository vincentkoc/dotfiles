#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tt="$repo/bin/tt"
temporary="$(mktemp -d)"
tmux_bin="${TT_TEST_REAL_TMUX_BIN:-$(command -v tmux || true)}"

if [[ -z "$tmux_bin" ]]; then
  printf 'tt_codex_snapshot_live_test=skipped (tmux unavailable)\n'
  exit 0
fi

socket="tt-codex-snapshot-test-$$"
wrapper="$temporary/tmux"
snapshot="$temporary/codex-cockpit.tsv"
unset TMUX TMUX_PANE SSH_AUTH_SOCK SSH_AGENT_PID BASH_ENV ENV CODEX_HOME
export HOME="$temporary/home" XDG_CONFIG_HOME="$temporary/config"
export XDG_STATE_HOME="$temporary/state" TMUX_TMPDIR="$temporary/sockets"
mkdir -p "$HOME" "$TMUX_TMPDIR"
stop="$temporary/stop"

cleanup() {
  touch "$stop"
  for _ in {1..100}; do
    "$tmux_bin" -L "$socket" has-session -t snapshot-test 2>/dev/null || break
    sleep 0.1
  done
  if "$tmux_bin" -L "$socket" has-session -t snapshot-test 2>/dev/null; then
    printf 'fixture did not exit after its stop file\n' >&2
    return 1
  fi
  rm -rf "$temporary"
}
trap cleanup EXIT

cat >"$wrapper" <<SH
#!/usr/bin/env bash
exec $(printf '%q' "$tmux_bin") -L $(printf '%q' "$socket") -f /dev/null "\$@"
SH
chmod +x "$wrapper"

"$tmux_bin" -L "$socket" -f /dev/null new-session -d -s snapshot-test \
  /bin/sh -c 'while [ ! -f "$1" ]; do sleep 0.1; done' sh "$stop"
target="$("$tmux_bin" -L "$socket" list-panes -t snapshot-test -F '#{session_name}:#{window_index}.#{pane_index}')"

HOME="$temporary/home" \
  XDG_STATE_HOME="$temporary/state" \
  TT_LOGIN_SHELL=/bin/sh \
  TT_TMUX_BIN="$wrapper" \
  "$tt" codex-snapshot "$snapshot" --quiet

"$tmux_bin" -L "$socket" has-session -t snapshot-test
grep -Fq "$target"$'\tshell' "$snapshot"
awk -F '\t' '!/^#/ && NF {
  if (NF != 10 || $9 != "shell" || $10 != "") exit 1
  n++
} END { if (!n) exit 1 }' "$snapshot"

printf 'tt_codex_snapshot_live_test=passed\n'
