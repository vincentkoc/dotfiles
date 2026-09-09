#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="$repo/profiles/linux-server/tmux.conf.local"
tt="$repo/bin/tt"

fail() {
  printf 'linux-server-tmux: %s\n' "$*" >&2
  exit 1
}

has_line() {
  grep -Fxq -- "$1" "$config" || fail "missing config line: $1"
}

# Only options, deferred bindings, and exact global-environment probes may run.
LC_ALL=C grep -q '[^ -~]' "$config" && fail 'config must remain ASCII'
awk '
  /^[[:space:]]*($|#)/ { next }
  continuation { continuation = /\\$/; next }
  /^tmux_conf_[a-z_0-9]+=([a-z]+)$/ { next }
  /^%if / { if (guard) invalid = 1; guard = $0; appends = 0; next }
  /^%endif$/ { if (!guard || appends != 1) invalid = 1; guard = ""; next }
  /^if-shell / {
    entry = $0
    sub(/^.*grep -Fxq /, "", entry)
    sub(/\047.*$/, "", entry)
    expected = "if-shell \047! tmux -N -S #{q:socket_path} show-options -gqv update-environment | grep -Fxq " entry "\047 \047set -ga update-environment " entry "\047"
    if (entry !~ /^[A-Z_][A-Z0-9_]*$/ || $0 != expected || seen["environment " entry]++ || guard) {
      print "invalid environment probe: " $0
      invalid = 1
    }
    next
  }
  /^set -ga terminal-(features|overrides) / {
    entry = $4
    sub(/^",/, "", entry)
    sub(/"$/, "", entry)
    gsub(/\*/, "[*]", entry)
    expected = "%if \"#{==:#{m/r:(^| )" entry "($| ),#{" $3 "}},0}\""
    if (NF != 4 || $4 !~ /^",[A-Za-z0-9*:@-]+"$/ || guard != expected ||
        appends++ || seen[$3 " " $4]++) {
      print "invalid capability guard: " $0; invalid = 1
    }
    next
  }
  guard { print "unexpected guarded command: " $0; invalid = 1; next }
  /^(set|setw) -g(a)? / && !/;/ { next }
  /^unbind -n C-l$/ { next }
  /^bind / { continuation = /\\$/; next }
  { print "unexpected top-level command: " $0; invalid = 1 }
  END { exit invalid || continuation || guard }
' "$config" || fail 'unsafe top-level config command'
if grep -Eq '/Users/|/opt/homebrew|pbcopy|reattach-to-user-namespace|set-environment|set-hook|pane-border-(status|format)|#\(' "$config"; then
  fail 'desktop paths, shell segments, or out-of-scope state changes'
fi

while IFS= read -r line; do
  has_line "$line"
done <<'OPTIONS'
set -g default-terminal "tmux-256color"
set -g allow-passthrough on
set -g extended-keys on
set -g extended-keys-format csi-u
set -g history-limit 100000
set -g status-position top
set -g status-left-length 32
set -g status-right-length 120
set -g status-style "fg=#565f89,bg=#1a1b26,none"
setw -g window-style "fg=default,bg=#1a1b26"
setw -g window-active-style "fg=default,bg=#1a1b26"
setw -g window-status-separator ""
setw -g window-status-style "fg=#565f89,bg=#1a1b26,none"
setw -g window-status-current-style "fg=#1a1b26,bg=#7aa2f7,bold"
setw -g window-status-activity-style "fg=default,bg=default,underscore"
setw -g window-status-bell-style "fg=#ff9e64,bg=default,blink,bold"
setw -g window-status-last-style "fg=#7aa2f7,bg=#16161e,none"
set -g message-style "fg=#1a1b26,bg=#7aa2f7,bold"
set -g message-command-style "fg=#7aa2f7,bg=#1a1b26,bold"
setw -g mode-style "fg=#1a1b26,bg=#7aa2f7,bold"
set -g display-panes-colour "#7aa2f7"
set -g display-panes-active-colour "#7aa2f7"
setw -g clock-mode-colour "#7aa2f7"
setw -g clock-mode-style 24
OPTIONS

capabilities=(
  'terminal-features|xterm*:RGB:clipboard:ccolour:cstyle:extkeys:focus:title'
  'terminal-features|screen*:title'
  'terminal-features|rxvt*:ignorefkeys'
  'terminal-features|xterm-256color:RGB:hyperlinks'
  'terminal-features|tmux-256color:RGB:hyperlinks'
  'terminal-overrides|*:Tc'
  'terminal-overrides|linux*:AX@'
)
for capability in "${capabilities[@]}"; do
  IFS='|' read -r option entry <<<"$capability"
  pattern="${entry//\*/[*]}"
  has_line "%if \"#{==:#{m/r:(^| )$pattern($| ),#{$option}},0}\""
  has_line "set -ga $option \",$entry\""
done

# These direct formats must match the canonical desktop values after decoding.
for option in status-left status-right window-status-format window-status-current-format; do
  actual="$(grep -E "^(set|setw) -g $option " "$config")"
  expected="$(grep -E "^(set|setw) -g $option " "$repo/.tmux.conf.local")"
  expected="${expected% #!important}"
  decoded="$(python3 -c 'import re,sys; text=re.sub(r"\\u([0-9A-Fa-f]{4})", lambda m: chr(int(m[1],16)), sys.argv[1]); sys.stdout.buffer.write(text.encode("utf-8"))' "$actual")"
  [[ "$decoded" == "$expected" ]] || fail "$option differs from desktop"
done
has_line 'unbind -n C-l'
has_line 'bind + resize-pane -Z'
has_line "bind r source-file ~/.tmux.conf.local \\; display '~/.tmux.conf.local sourced'"
edit_binding="$(grep '^bind e ' "$config")"
# shellcheck disable=SC2016
[[ "$edit_binding" == *'tmux source-file "$HOME/.tmux.conf.local"'* &&
   "$edit_binding" != *'source ~/.tmux.conf '* ]] || fail 'edit binding reloads the base config'
grep -Fq 'bind R run-shell -b' "$config"
grep -Fq 'tmux-pane-repair" request #{pane_id} #{q:client_name}' "$config"
if grep -Eq 'clear-history|bind R send-keys|stty sane' "$repo/.tmux.conf" "$config" "$repo/.tmux.conf.local"; then
  fail 'destructive screen-clear or unguarded repair binding'
fi
for entry in DISPLAY KRB5CCNAME SSH_ASKPASS SSH_AUTH_SOCK SSH_AGENT_PID SSH_CONNECTION WINDOWID XAUTHORITY; do
  has_line "if-shell '! tmux -N -S #{q:socket_path} show-options -gqv update-environment | grep -Fxq $entry' 'set -ga update-environment $entry'"
done
for escape in E0B0 E0B2 2750 2328 2197 2687 268F; do
  grep -Fq "\\u$escape" "$config" || fail "missing escaped glyph: $escape"
done
for binding in 'bind N display-popup' 'bind -T root MouseDown3Pane if-shell' \
  'bind -T root MouseDown1StatusRight run-shell' 'bind -T root MouseDown3StatusRight display-menu'; do
  line="$(grep -F "$binding" "$config")"
  [[ -n "$line" ]] || fail "missing binding: $binding"
done
grep -Fq 'pane-menu #{pane_id} #{mouse_x} #{mouse_y} #{client_name}' "$config"
grep -Fq 'note edit #{pane_id}' "$config"
grep -Fq -- '-t "#{pane_id}" -T "note: #{session_name}:#{window_index}.#{pane_index}" -x "#{pane_left}" -y "#{pane_top}"' "$config"
grep -Fq '#{||:#{==:#{pane_current_command},mosh-client},#{==:#{pane_current_command},ssh}}' "$config"
grep -Fq '"select-pane -t = \\; send-keys -M"' "$config"
grep -Fq 'display-menu -T "CX SAVE" -x M -y S' "$config"
for action in 'codex-snapshot --quiet' 'popup snapshot' 'popup restore-preview' \
  'popup snapshot-history' 'popup status' 'popup doctor' mobile 'autosave on' 'autosave off' \
  'note edit' pane-menu; do
  grep -F "$action" "$config" | grep -Fq "\\\$HOME/.local/bin/tt" || fail "wrong entrypoint: $action"
done

# Decode only the two bindings into recording functions, never tmux commands.
capture_binding() { binding_args=("$@"); }
line="$(grep '^bind N display-popup ' "$config")"
eval "capture_binding ${line#bind }"
note_command="${binding_args[${#binding_args[@]}-1]}"
[[ "$note_command" == "\"\$HOME/.local/bin/tt\" note edit #{pane_id}" ]] || fail 'unexpected note command'
line="$(grep '^bind -T root MouseDown3Pane ' "$config")"
eval "capture_binding ${line#bind }"
[[ "${#binding_args[@]}" == 8 && "${binding_args[6]}" == 'select-pane -t = \; send-keys -M' ]] ||
  fail 'unexpected mouse passthrough'
passthrough="${binding_args[5]}"
menu_action="${binding_args[7]}"
[[ "$menu_action" == 'run-shell -b '* ]] || fail 'unexpected menu action'
eval "capture_binding ${menu_action#run-shell }"
[[ "${#binding_args[@]}" == 2 && "${binding_args[0]}" == -b ]] || fail 'unexpected menu arguments'
pane_command="${binding_args[1]}"
[[ "$pane_command" == "\"\$HOME/.local/bin/tt\" pane-menu #{pane_id} #{mouse_x} #{mouse_y} #{client_name}" ]] ||
  fail 'unexpected pane command'

temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
stub_home="$temporary/home with spaces"
mkdir -p "$stub_home/.local/bin"
cat >"$stub_home/.local/bin/tt" <<'STUB'
#!/usr/bin/env bash
printf '%s\0' "$0" "$@" >"$TT_BINDING_LOG"
STUB
chmod +x "$stub_home/.local/bin/tt"
for action in note pane; do
  command="$note_command"
  expected_args=("$stub_home/.local/bin/tt" note edit %42)
  if [[ "$action" == pane ]]; then
    command="$pane_command"
    expected_args=("$stub_home/.local/bin/tt" pane-menu %42 17 23 /dev/pts/99)
  fi
  command="${command//'#{pane_id}'/%42}"
  command="${command//'#{mouse_x}'/17}"
  command="${command//'#{mouse_y}'/23}"
  command="${command//'#{client_name}'//dev/pts/99}"
  HOME="$stub_home" TT_BINDING_LOG="$temporary/args" bash --noprofile --norc -c "$command"
  recorded=()
  while IFS= read -r -d '' argument; do recorded+=("$argument"); done <"$temporary/args"
  [[ "${#recorded[@]}" == "${#expected_args[@]}" ]] || fail "$action argument count"
  for index in "${!expected_args[@]}"; do
    [[ "${recorded[index]}" == "${expected_args[index]}" ]] || fail "$action argument $index"
  done
done

# Extract only the reviewed functions; never source tt's CLI dispatcher.
load_function() {
  local definition
  definition="$(awk -v name="$1" '
    $0 == name "() {" { copying = 1 }
    copying { print }
    copying && $0 == "}" { exit }
  ' "$tt")"
  [[ -n "$definition" ]] || fail "missing function: $1"
  eval "$definition"
}
load_function agent_effective_kind_format
load_function agent_label_format
load_function apply_studio_pane_titles

border=
fake_tmux() {
  case "$1" in
    list-panes)
      [[ "$*" == 'list-panes -t @fixture -F #{pane_current_command}' ]] || fail 'unexpected pane lookup'
      printf 'codex\nzsh\n'
      ;;
    set-window-option)
      [[ "$2" == -t && "$3" == @fixture && "$4" == -q ]] || fail 'unexpected target'
      case "$5" in
        pane-border-format) border="$6" ;;
        pane-border-status|pane-border-style|pane-active-border-style) ;;
        *) fail "unexpected option: $5" ;;
      esac
      ;;
    *) fail "unexpected tmux command: $*" ;;
  esac
}
TMUX_BIN=fake_tmux apply_studio_pane_titles @fixture
kind="$(agent_effective_kind_format)"
label="$(agent_label_format)"
expected_kind='#{?#{!=:#{@tt_agent_kind},},#{@tt_agent_kind},#{?#{==:#{pane_current_command},codex},CODEX,#{?#{==:#{pane_current_command},claude},CLAUDE,SHELL}}}'
[[ "$kind" == "$expected_kind" ]] || fail 'kind must preserve all nonempty metadata'
[[ "$label" == "#{?#{==:$kind,CLAUDE},CC,#{?#{==:$kind,CODEX},CX,TM}}" ]] || fail 'label fallback differs'
color="${border##*'#[fg='}"
color="${color%%',bold]'*}"
[[ "$color" == "#{?#{==:$kind,CLAUDE},#bb9af7,#{?#{==:$kind,CODEX},#7dcfff,#9ece6a}}" ]] || fail 'color fallback differs'
[[ "$border" == *"$label"*'#{?@tt_base_title,#{@tt_base_title},#{pane_title}} '* ]] || fail 'title template changed'

# Optional real format evaluation is read-only and requires an explicit server/pane.
if [[ -n "${TT_TEST_TMUX_SOCKET:-}" && -n "${TT_TEST_TMUX_PANE:-}" ]]; then
  real_tmux="${TT_TEST_TMUX_BIN:-/usr/bin/tmux}"
  for command in ssh mosh-client codex zsh ssh-helper mosh; do
    condition="${passthrough//'#{pane_current_command}'/$command}"
    expected=0
    [[ "$command" != ssh && "$command" != mosh-client ]] || expected=1
    actual="$("$real_tmux" -S "$TT_TEST_TMUX_SOCKET" display-message -p -t "$TT_TEST_TMUX_PANE" "$condition")"
    [[ "$actual" == "$expected" ]] || fail "passthrough mismatch: $command"
  done

  # Model appends in memory; evaluate the actual guards with tmux, read-only.
  load_capabilities() {
    local line condition enabled=0 option entry
    while IFS= read -r line; do
      case "$line" in
        '%if '*)
          condition="${line#'%if "'}"
          condition="${condition%\"}"
          condition="${condition//'#{terminal-features}'/$features}"
          condition="${condition//'#{terminal-overrides}'/$overrides}"
          enabled="$("$real_tmux" -S "$TT_TEST_TMUX_SOCKET" display-message -p -t "$TT_TEST_TMUX_PANE" "$condition")"
          [[ "$enabled" == 0 || "$enabled" == 1 ]] || fail "invalid guard: $condition"
          ;;
        'set -ga terminal-features '*|'set -ga terminal-overrides '*)
          [[ "$enabled" == 1 ]] || continue
          option="${line#'set -ga '}"
          entry="${option#* }"
          option="${option%% *}"
          entry="${entry:2}"
          entry="${entry%\"}"
          if [[ "$option" == terminal-features ]]; then
            features="${features:+$features }$entry"
          else
            overrides="${overrides:+$overrides }$entry"
          fi
          ;;
        '%endif') enabled=0 ;;
      esac
    done <"$config"
  }
  reload_cases=0
  while IFS='|' read -r initial_features initial_overrides; do
    features="$initial_features"
    overrides="$initial_overrides"
    load_capabilities
    first_features="$features"
    first_overrides="$overrides"
    load_capabilities
    [[ "$features" == "$first_features" && "$overrides" == "$first_overrides" ]] ||
      fail 'repeated load appended duplicate capabilities'
    [[ "$features" == "$initial_features"* && "$overrides" == "$initial_overrides"* ]] ||
      fail 'existing capabilities changed'
    expected_features="$initial_features"
    expected_overrides="$initial_overrides"
    for capability in "${capabilities[@]}"; do
      IFS='|' read -r option entry <<<"$capability"
      if [[ "$option" == terminal-features ]]; then
        [[ " $expected_features " == *" $entry "* ]] ||
          expected_features="${expected_features:+$expected_features }$entry"
      else
        [[ " $expected_overrides " == *" $entry "* ]] ||
          expected_overrides="${expected_overrides:+$expected_overrides }$entry"
      fi
    done
    [[ "$first_features" == "$expected_features" && "$first_overrides" == "$expected_overrides" ]] ||
      fail 'first load must append each missing entry exactly once'
    reload_cases=$((reload_cases + 1))
  done <<'RELOAD_CASES'
|
custom-term:RGB|custom-term:colors=256
screen*:title rxvt*:ignorefkeys|linux*:AX@
myscreen*:title screen*:title:extra|some-linux*:AX@ linux*:AX@extra
screen*:title screen*:title|linux*:AX@ linux*:AX@
RELOAD_CASES
  printf 'read_only_repeat_load_cases=%s\n' "$reload_cases"

  cases=0
  while IFS='|' read -r metadata command expected; do
    expression="$label|$color"
    expression="${expression//'#{@tt_agent_kind}'/$metadata}"
    expression="${expression//'#{pane_current_command}'/$command}"
    actual="$("$real_tmux" -S "$TT_TEST_TMUX_SOCKET" display-message -p -t "$TT_TEST_TMUX_PANE" "$expression")"
    [[ "$actual" == "$expected" ]] || fail "kind=$metadata command=$command: $actual != $expected"
    cases=$((cases + 1))
  done <<'CASES'
|codex|CX|#7dcfff
|claude|CC|#bb9af7
|zsh|TM|#9ece6a
|ssh|TM|#9ece6a
|mosh-client|TM|#9ece6a
CLAUDE|codex|CC|#bb9af7
CODEX|claude|CX|#7dcfff
SHELL|codex|TM|#9ece6a
unknown|codex|TM|#9ece6a
0|codex|TM|#9ece6a
 |codex|TM|#9ece6a
|Codex|TM|#9ece6a
|codex-helper|TM|#9ece6a
|mycodex|TM|#9ece6a
|claude-helper|TM|#9ece6a
||TM|#9ece6a
CASES
  printf 'read_only_format_cases=%s\n' "$cases"
fi
printf 'linux_server_tmux_test=passed\n'
