#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

# shellcheck disable=SC1091
source "$root/install.sh"

test_source="$temporary/dotfiles"
mkdir -p "$test_source/.local/state/tt"
cp "$root/.local/state/tt/AGENTS.md" "$test_source/.local/state/tt/AGENTS.md"
dotfiles_dir() { printf '%s\n' "$test_source"; }

test_state="$temporary/xdg-state"
mkdir -p "$test_state/tt/history/codex-cockpit" "$test_state/tt/pane-markers"
printf 'snapshot sentinel\n' >"$test_state/tt/history/codex-cockpit/saved.tsv"
printf 'marker sentinel\n' >"$test_state/tt/pane-markers/saved.yaml"

XDG_STATE_HOME="$test_state" setup_tt_recovery_guidance >/dev/null
guide="$test_state/tt/AGENTS.md"
[[ -L "$guide" ]]
[[ "$(readlink "$guide")" == "$test_source/.local/state/tt/AGENTS.md" ]]
cmp "$guide" "$root/.local/state/tt/AGENTS.md"

# Reinstallation must leave the link and unrelated live state alone.
link_identity="$(ls -di "$guide")"
XDG_STATE_HOME="$test_state" setup_tt_recovery_guidance >/dev/null
[[ "$(ls -di "$guide")" == "$link_identity" ]]
[[ "$(readlink "$guide")" == "$test_source/.local/state/tt/AGENTS.md" ]]
[[ "$(cat "$test_state/tt/history/codex-cockpit/saved.tsv")" == 'snapshot sentinel' ]]
[[ "$(cat "$test_state/tt/pane-markers/saved.yaml")" == 'marker sentinel' ]]
[[ "$(find "$test_state/tt" -type f | wc -l | tr -d ' ')" == 2 ]]

# Updating a stale link must not replace the file it previously pointed to.
stale_target="$temporary/stale-guidance.md"
printf 'stale guidance\n' >"$stale_target"
rm "$guide"
ln -s "$stale_target" "$guide"
XDG_STATE_HOME="$test_state" setup_tt_recovery_guidance >/dev/null
[[ "$(readlink "$guide")" == "$test_source/.local/state/tt/AGENTS.md" ]]
[[ "$(cat "$stale_target")" == 'stale guidance' ]]

# Preserve a locally edited guide using the installer's normal backup contract.
rm "$guide"
printf 'local guidance\n' >"$guide"
XDG_STATE_HOME="$test_state" setup_tt_recovery_guidance >/dev/null
[[ -L "$guide" ]]
saved_guides=("$test_state/tt/AGENTS.md.pre-dotfiles."*.bak)
[[ "${#saved_guides[@]}" == 1 ]]
[[ "$(cat "${saved_guides[0]}")" == 'local guidance' ]]
link_identity="$(ls -di "$guide")"
XDG_STATE_HOME="$test_state" setup_tt_recovery_guidance >/dev/null
[[ "$(ls -di "$guide")" == "$link_identity" ]]
[[ "$(readlink "$guide")" == "$test_source/.local/state/tt/AGENTS.md" ]]
saved_guides=("$test_state/tt/AGENTS.md.pre-dotfiles."*.bak)
[[ "${#saved_guides[@]}" == 1 ]]
[[ "$(cat "${saved_guides[0]}")" == 'local guidance' ]]

# Check the default destination in an isolated child environment.
test_home="$temporary/home"
mkdir -p "$test_home/GIT/_Perso/dotfiles/.local/state/tt"
cp "$root/.local/state/tt/AGENTS.md" \
    "$test_home/GIT/_Perso/dotfiles/.local/state/tt/AGENTS.md"
# shellcheck disable=SC2016
env -u XDG_STATE_HOME HOME="$test_home" /bin/bash -c \
    'source "$1/install.sh"; setup_tt_recovery_guidance' _ "$root" >/dev/null
[[ -L "$test_home/.local/state/tt/AGENTS.md" ]]
cmp "$test_home/.local/state/tt/AGENTS.md" "$root/.local/state/tt/AGENTS.md"

# A linked tt directory must keep its identity, target and saved history.
linked_state="$temporary/linked-state"
linked_target="$temporary/linked tt target"
mkdir -p "$linked_state" "$linked_target/history/codex-cockpit"
printf 'linked history\n' >"$linked_target/history/codex-cockpit/saved.tsv"
ln -s "$linked_target" "$linked_state/tt"
state_identity="$(ls -di "$linked_state/tt")"
XDG_STATE_HOME="$linked_state" setup_tt_recovery_guidance >/dev/null
[[ -L "$linked_state/tt" ]]
[[ "$(ls -di "$linked_state/tt")" == "$state_identity" ]]
[[ "$(readlink "$linked_state/tt")" == "$linked_target" ]]
[[ "$(readlink "$linked_target/AGENTS.md")" == "$test_source/.local/state/tt/AGENTS.md" ]]
[[ "$(cat "$linked_target/history/codex-cockpit/saved.tsv")" == 'linked history' ]]
[[ "$(find "$linked_target/history" -type f | wc -l | tr -d ' ')" == 1 ]]

# Missing source must fail before creating any new state directory.
rm "$test_source/.local/state/tt/AGENTS.md"
if XDG_STATE_HOME="$temporary/missing-state" setup_tt_recovery_guidance >/dev/null; then
    echo 'missing guidance source unexpectedly succeeded' >&2
    exit 1
fi
[[ ! -e "$temporary/missing-state" ]]
# It must also leave an existing guide link and unrelated state untouched.
if XDG_STATE_HOME="$test_state" setup_tt_recovery_guidance >/dev/null; then
    echo 'missing guidance source unexpectedly changed existing state' >&2
    exit 1
fi
[[ "$(ls -di "$guide")" == "$link_identity" ]]
[[ "$(readlink "$guide")" == "$test_source/.local/state/tt/AGENTS.md" ]]
[[ "$(cat "$test_state/tt/history/codex-cockpit/saved.tsv")" == 'snapshot sentinel' ]]
[[ "$(cat "$test_state/tt/pane-markers/saved.yaml")" == 'marker sentinel' ]]

printf 'tt recovery guidance installation passed\n'
