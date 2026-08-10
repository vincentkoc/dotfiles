#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tt="$repo/bin/tt"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
recovery_section="$temporary/recovery-section.sh"
awk '
  /^tmux_server_running\(\)/ { in_recovery = 1 }
  /^reset_to_profile\(\)/ { in_recovery = 0 }
  in_recovery { print }
' "$tt" >"$recovery_section"

bash -n "$tt"

if grep -Eq '(^|[^[:alnum:]_])(kill-server|kill_server|kill-session|kill_session)([^[:alnum:]_]|$)' "$recovery_section"; then
  printf 'recovery code must not delete the tmux server or a session\n' >&2
  exit 1
fi

grep -Fq 'require_recovery_session_absent "$session" || exit 2' "$recovery_section"
grep -Fq 'require_recovery_session_absent "ops" || exit 2' "$recovery_section"
grep -Fq 'require_recovery_manifest_sessions_absent "$manifest" || exit 2' "$recovery_section"
grep -Fq 'restore_cockpit_snapshot "$snapshot" "$session" --preserve-server' "$recovery_section"
grep -Fq 'restore_agent_cockpit_layouts "$manifest"' "$recovery_section"
grep -Fq 'create_ops_detached "$session" "$HOME" --no-shell-upgrade "$preserve_server" --recovery-session' "$recovery_section"
grep -Fq 'create_agent_cockpit_sessions "$manifest" "$preserve_server" --recovery-session' "$recovery_section"
grep -Fq 'set_recovery_session_indices "$session" "$window_id"' "$tt"
grep -Fq 'recovery_pane_target "$session" 1 "$pane"' "$tt"

fake_tmux="$temporary/tmux"
tmux_log="$temporary/tmux.log"
snapshot="$temporary/cockpit.tsv"

# Model a live server whose first pane was created at base index 0. Recovery
# must move its first window to :1 and map logical snapshot pane 1 to %1.
cp "$repo/tests/fixtures/tt-recovery/tmux" "$fake_tmux"
chmod +x "$fake_tmux"

printf 'cockpit:1.1\tcodex\t%s\tops.1\tcodex\tfake-session-1\texact\tfake-session-1\n' "$temporary" >"$snapshot"
printf 'cockpit:5.1\tcodex\t%s\tL4.1\tcodex\tfake-session-5\texact\tfake-session-5\n' "$temporary" >>"$snapshot"
env -u TMUX \
  HOME="$temporary/home" \
  XDG_CONFIG_HOME="$temporary/config" \
  TT_TMUX_BIN="$fake_tmux" \
  TT_LOGIN_SHELL=/bin/sh \
  TT_TEST_TMUX_LOG="$tmux_log" \
  TT_TEST_TMUX_SERVER_RUNNING=1 \
  "$tt" recover cockpit "$snapshot"

if grep -Eq '(^| )(kill-server|kill-session)( |$)' "$tmux_log"; then
  printf 'recovery invoked a destructive tmux command\n' >&2
  exit 1
fi
grep -Eq '^new-session .* -s cockpit ' "$tmux_log"
grep -Eq '^set-option -t cockpit base-index 1$' "$tmux_log"
grep -Eq '^set-option -t cockpit pane-base-index 1$' "$tmux_log"
grep -Eq '^move-window -s @1 -t cockpit:1$' "$tmux_log"
grep -Eq '^new-window .* -n L4 ' "$tmux_log"
grep -Eq '^respawn-pane -k -t %1 -c .*codex resume --no-alt-screen fake-session-1; exec /bin/sh -il$' "$tmux_log"
grep -Eq '^respawn-pane -k -t %25 -c .*codex resume --no-alt-screen fake-session-5; exec /bin/sh -il$' "$tmux_log"
if grep -Eq '^(set|set-option|set-hook) -g' "$tmux_log"; then
  printf 'recovery rewrote global server configuration\n' >&2
  exit 1
fi

: >"$tmux_log"
env -u TMUX \
  HOME="$temporary/home" \
  XDG_CONFIG_HOME="$temporary/config" \
  TT_TMUX_BIN="$fake_tmux" \
  TT_LOGIN_SHELL=/bin/sh \
  TT_TEST_TMUX_LOG="$tmux_log" \
  TT_TEST_TMUX_FIRST_WINDOW_INDEX=1 \
  "$tt" recover cockpit "$snapshot"

if grep -Eq '(^| )(kill-server|kill-session)( |$)' "$tmux_log"; then
  printf 'fresh cockpit recovery invoked a destructive tmux command\n' >&2
  exit 1
fi
grep -Eq '^set-option -t cockpit base-index 1$' "$tmux_log"
grep -Eq '^set-option -t cockpit pane-base-index 1$' "$tmux_log"
if grep -Eq '^move-window -s @1 -t cockpit:1$' "$tmux_log"; then
  printf 'recovery moved a first window already at index 1\n' >&2
  exit 1
fi
grep -Eq '^new-window .* -n L4 ' "$tmux_log"
grep -Eq '^respawn-pane -k -t %1 -c .*codex resume --no-alt-screen fake-session-1; exec /bin/sh -il$' "$tmux_log"
grep -Eq '^respawn-pane -k -t %25 -c .*codex resume --no-alt-screen fake-session-5; exec /bin/sh -il$' "$tmux_log"

: >"$tmux_log"
agent_manifest="$temporary/agents.tsv"
printf 'factory2\t1\t%s\tworker\tprintf ready\n' "$temporary" >"$agent_manifest"
env -u TMUX \
  HOME="$temporary/home" \
  XDG_CONFIG_HOME="$temporary/config" \
  TT_TMUX_BIN="$fake_tmux" \
  TT_LOGIN_SHELL=/bin/sh \
  TT_TEST_TMUX_LOG="$tmux_log" \
  TT_TEST_TMUX_SERVER_RUNNING=1 \
  "$tt" recover agents "$agent_manifest" factory2

if grep -Eq '(^| )(kill-server|kill-session)( |$)' "$tmux_log"; then
  printf 'agent recovery invoked a destructive tmux command\n' >&2
  exit 1
fi
grep -Eq '^set-option -t factory2 base-index 1$' "$tmux_log"
grep -Eq '^set-option -t factory2 pane-base-index 1$' "$tmux_log"
grep -Eq '^move-window -s @1 -t factory2:1$' "$tmux_log"
grep -Eq '^respawn-pane -k -t %1 -c .*printf ready; exec /bin/sh -il$' "$tmux_log"
if grep -Eq '^(set|set-option|set-hook) -g' "$tmux_log"; then
  printf 'agent recovery rewrote global server configuration\n' >&2
  exit 1
fi

: >"$tmux_log"
env -u TMUX \
  HOME="$temporary/home" \
  XDG_CONFIG_HOME="$temporary/config" \
  TT_TMUX_BIN="$fake_tmux" \
  TT_LOGIN_SHELL=/bin/sh \
  TT_TEST_TMUX_LOG="$tmux_log" \
  TT_TEST_TMUX_FIRST_WINDOW_INDEX=1 \
  "$tt" recover agents "$agent_manifest" factory2

if grep -Eq '(^| )(kill-server|kill-session)( |$)' "$tmux_log"; then
  printf 'fresh agent recovery invoked a destructive tmux command\n' >&2
  exit 1
fi
grep -Eq '^set-option -t factory2 base-index 1$' "$tmux_log"
grep -Eq '^set-option -t factory2 pane-base-index 1$' "$tmux_log"
if grep -Eq '^move-window -s @1 -t factory2:1$' "$tmux_log"; then
  printf 'agent recovery moved a first window already at index 1\n' >&2
  exit 1
fi
grep -Eq '^respawn-pane -k -t %1 -c .*printf ready; exec /bin/sh -il$' "$tmux_log"

: >"$tmux_log"
if env -u TMUX \
  HOME="$temporary/home" \
  XDG_CONFIG_HOME="$temporary/config" \
  TT_TMUX_BIN="$fake_tmux" \
  TT_TEST_TMUX_LOG="$tmux_log" \
  TT_TEST_TMUX_EXISTING_SESSION="cockpit" \
  "$tt" recover cockpit "$snapshot" >/dev/null 2>&1; then
  printf 'recovery accepted an existing cockpit session\n' >&2
  exit 1
fi
if grep -Eq '(^| )(kill-server|kill-session|new-session)( |$)' "$tmux_log"; then
  printf 'conflicting recovery mutated tmux state\n' >&2
  exit 1
fi

printf 'tt_recovery_test=passed\n'
