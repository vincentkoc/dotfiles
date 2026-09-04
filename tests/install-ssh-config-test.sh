#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

# shellcheck disable=SC1091
source "$repo_root/install.sh"

fixture_home() {
    local name="$1"
    local home="$temporary/$name"
    local fixture_repo="$home/GIT/_Perso/dotfiles"

    mkdir -p "$fixture_repo/.ssh"
    printf 'Host *\n  StrictHostKeyChecking yes\n' >"$fixture_repo/.ssh/config"
    printf '%s\n' "$home"
}

assert_config_link() {
    local home="$1"
    local expected="$home/GIT/_Perso/dotfiles/.ssh/config"

    [[ -d "$home/.ssh" ]]
    [[ "$(stat -f '%Lp' "$home/.ssh" 2>/dev/null || stat -c '%a' "$home/.ssh")" == 700 ]]
    [[ -L "$home/.ssh/config" ]]
    [[ "$(readlink "$home/.ssh/config")" == "$expected" ]]
}

absent_home="$(fixture_home absent-parent)"
HOME="$absent_home" setup_config_symlinks >/dev/null
assert_config_link "$absent_home"

HOME="$absent_home" setup_config_symlinks >/dev/null
assert_config_link "$absent_home"
[[ -z "$(find "$absent_home/.ssh" -maxdepth 1 -name 'config.pre-dotfiles.*.bak' -print -quit)" ]]

wrong_link_home="$(fixture_home wrong-link)"
mkdir -p "$wrong_link_home/.ssh"
ln -s "$wrong_link_home/obsolete-config" "$wrong_link_home/.ssh/config"
HOME="$wrong_link_home" setup_config_symlinks >/dev/null
assert_config_link "$wrong_link_home"

different_file_home="$(fixture_home differing-file)"
mkdir -p "$different_file_home/.ssh"
printf 'Host legacy\n' >"$different_file_home/.ssh/config"
HOME="$different_file_home" setup_config_symlinks >/dev/null
assert_config_link "$different_file_home"
backup_count="$(
    find "$different_file_home/.ssh" -maxdepth 1 -name 'config.pre-dotfiles.*.bak' -print |
        wc -l | tr -d ' '
)"
backup_path="$(
    find "$different_file_home/.ssh" -maxdepth 1 -name 'config.pre-dotfiles.*.bak' -print -quit
)"
[[ "$backup_count" -eq 1 ]]
[[ "$(cat "$backup_path")" == "Host legacy" ]]

exact_target_home="$(fixture_home 'exact target')"
HOME="$exact_target_home" setup_config_symlinks >/dev/null
assert_config_link "$exact_target_home"
[[ "$(readlink "$exact_target_home/.ssh/config")" == \
    "$exact_target_home/GIT/_Perso/dotfiles/.ssh/config" ]]

printf 'install SSH config tests passed\n'
