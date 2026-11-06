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
XDG_STATE_HOME="$test_state" setup_tt_recovery_guidance >/dev/null
[[ "$(cat "$test_state/tt/history/codex-cockpit/saved.tsv")" == 'snapshot sentinel' ]]
[[ "$(cat "$test_state/tt/pane-markers/saved.yaml")" == 'marker sentinel' ]]
[[ "$(find "$test_state/tt" -type f | wc -l | tr -d ' ')" == 2 ]]

# Preserve a locally edited guide using the installer's normal backup contract.
rm "$guide"
printf 'local guidance\n' >"$guide"
XDG_STATE_HOME="$test_state" setup_tt_recovery_guidance >/dev/null
[[ -L "$guide" ]]
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

# Missing source must fail before creating any new state directory.
rm "$test_source/.local/state/tt/AGENTS.md"
if XDG_STATE_HOME="$temporary/missing-state" setup_tt_recovery_guidance >/dev/null; then
    echo 'missing guidance source unexpectedly succeeded' >&2
    exit 1
fi
[[ ! -e "$temporary/missing-state" ]]

printf 'tt recovery guidance installation passed\n'
