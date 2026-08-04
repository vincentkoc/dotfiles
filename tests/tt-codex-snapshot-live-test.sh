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
normal_before="$("$tmux_bin" list-sessions 2>/dev/null || true)"

cleanup() {
  "$tmux_bin" -L "$socket" kill-server 2>/dev/null || true
  rm -rf "$temporary"
}
trap cleanup EXIT

cat >"$wrapper" <<SH
#!/usr/bin/env bash
exec $(printf '%q' "$tmux_bin") -L $(printf '%q' "$socket") "\$@"
SH
chmod +x "$wrapper"

"$tmux_bin" -L "$socket" new-session -d -s snapshot-test 'exec sleep 30'
target="$("$tmux_bin" -L "$socket" list-panes -t snapshot-test -F '#{session_name}:#{window_index}.#{pane_index}')"

HOME="$temporary/home" \
  XDG_STATE_HOME="$temporary/state" \
  TT_LOGIN_SHELL=/bin/sh \
  TT_TMUX_BIN="$wrapper" \
  "$tt" codex-snapshot "$snapshot" --quiet

"$tmux_bin" -L "$socket" has-session -t snapshot-test
grep -Fq "$target"$'\tshell' "$snapshot"

normal_after="$("$tmux_bin" list-sessions 2>/dev/null || true)"
if [[ "$normal_before" != "$normal_after" ]]; then
  printf 'snapshot changed the caller tmux server instead of the disposable socket\n' >&2
  exit 1
fi

printf 'tt_codex_snapshot_live_test=passed\n'
