#!/usr/bin/env bash
set -euo pipefail
umask 077

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export HOME="$tmp/home" CODEX_HOME="$tmp/home/.codex"
export XDG_STATE_HOME="$tmp/state" XDG_CONFIG_HOME="$tmp/config"
export TT_TMUX_BIN="$tmp/tmux" TT_TEST_TMUX_LOG="$tmp/tmux.log"
unset TMUX TMUX_PANE
mkdir -p "$HOME" "$CODEX_HOME" "$tmp/work"

cat >"$TT_TMUX_BIN" <<'SH'
#!/bin/sh
printf '%s\n' "$*" >>"$TT_TEST_TMUX_LOG"
exit 99
SH
chmod +x "$TT_TMUX_BIN"

# Palette helpers are pure; do not source tt's command dispatcher.
eval "$(sed -n '/^pane_marker_color() {/,/^}/p' "$repo/bin/tt")"
eval "$(sed -n '/^pane_marker_window_style() {/,/^}/p' "$repo/bin/tt")"
eval "$(sed -n '/^pane_marker_active_style() {/,/^}/p' "$repo/bin/tt")"
[[ "$(pane_marker_color violet)" == '#bb9af7' ]]
[[ "$(pane_marker_color cyan)" == '#7dcfff' ]]
[[ "$(pane_marker_window_style violet)" == 'fg=default,bg=#211a2c' ]]
[[ "$(pane_marker_window_style cyan)" == 'fg=default,bg=#17242a' ]]
[[ "$(pane_marker_active_style violet)" == 'fg=default,bg=#2a2139' ]]
[[ "$(pane_marker_active_style cyan)" == 'fg=default,bg=#1c2e35' ]]

session_id=33333333-3333-3333-3333-333333333333
snapshot="$tmp/legacy-snapshot.tsv"
printf '# target\tkind\tcwd\ttitle\tcurrent_command\tsession_id\tstatus\trestore\n' >"$snapshot"
printf 'cockpit:1.1\tcodex\t%s\ttest\tcodex\t%s\texact\tcodex resume --no-alt-screen %s\n' \
  "$tmp/work" "$session_id" "$session_id" >>"$snapshot"
before="$(shasum -a 256 "$snapshot")"

"$repo/bin/tt" codex-restore pane cockpit:1.1 "$snapshot" >"$tmp/preview" 2>"$tmp/preview.err"
grep -Fq "$session_id" "$tmp/preview"
"$repo/bin/tt" restore-preview "$snapshot" >"$tmp/table"
grep -Fq "$session_id" "$tmp/table"
[[ "$(awk '$1 == "cockpit:1.1" {print $4}' "$tmp/table")" == "-" ]]
[[ "$before" == "$(shasum -a 256 "$snapshot")" ]]
[[ ! -e "$TT_TEST_TMUX_LOG" ]]

# Display-only legacy metadata must not be confused with current exit status.
for schema in legacy current headerless; do
  case "$schema" in
    legacy) columns=$'marker\tbucket'; values=$'violet\tpersonal'; expected=personal ;;
    current) columns=$'owner_state\texit_status'; values=$'exited\t137'; expected=- ;;
    headerless) columns=""; values=$'violet\tpersonal'; expected=- ;;
  esac
  display_snapshot="$tmp/$schema.tsv"
  if [[ -n "$columns" ]]; then
    printf '# target\tkind\tcwd\ttitle\tcurrent_command\tsession_id\tstatus\trestore\t%s\n' \
      "$columns" >"$display_snapshot"
  else
    : >"$display_snapshot"
  fi
  printf 'cockpit:1.1\tcodex\t%s\ttest\tcodex\t%s\texact\tcodex resume --no-alt-screen %s\t%s\n' \
    "$tmp/work" "$session_id" "$session_id" "$values" >>"$display_snapshot"
  display_before="$(shasum -a 256 "$display_snapshot")"
  "$repo/bin/tt" restore-preview "$display_snapshot" >"$tmp/table"
  [[ "$(awk '$1 == "cockpit:1.1" {print $4}' "$tmp/table")" == "$expected" ]]
  grep -Fq "$session_id" "$tmp/table"
  [[ "$display_before" == "$(shasum -a 256 "$display_snapshot")" ]]
  [[ ! -e "$TT_TEST_TMUX_LOG" ]]
done

# A legacy UUID is preview evidence, not permission to infer a parent or replace a pane.
if "$repo/bin/tt" codex-restore pane cockpit:1.1 "$snapshot" --execute \
  >"$tmp/execute.out" 2>"$tmp/execute.err"; then
  printf 'legacy snapshot unexpectedly became executable\n' >&2
  exit 1
fi
grep -Fq 'legacy snapshot has no unique server/pane identity; preview only' "$tmp/execute.err"
[[ "$before" == "$(shasum -a 256 "$snapshot")" ]]
[[ ! -e "$TT_TEST_TMUX_LOG" ]]

printf 'tt_test=passed\n'
