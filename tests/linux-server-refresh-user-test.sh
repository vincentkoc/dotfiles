#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="$root/bin/linux-server-bootstrap"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

mode() {
  stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"
}

fakebin="$temporary/fakebin"
mkdir "$fakebin"
for command_name in sudo apt apt-get apt-cache dpkg-query usermod chsh sshd ufw systemctl; do
  cat >"$fakebin/$command_name" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$(basename "$0") $*" >>"$FORBIDDEN_LOG"
exit 97
EOF
  chmod +x "$fakebin/$command_name"
done

refresh_case="$temporary/refresh-case.sh"
cat >"$refresh_case" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 0002

# shellcheck disable=SC1090
DOTFILES_SERVER_SOURCE_ONLY=1 source "$BOOTSTRAP_SCRIPT"

uname() {
  [[ "${1:-}" == -s ]] && {
    printf 'Linux\n'
    return
  }
  command uname "$@"
}

install_pinned_checkout() {
  local destination="$2"
  [[ -d "$destination" && ! -L "$destination" ]]
  if [[ "$destination" == "$HOME/.oh-my-zsh/custom/themes/spaceship-prompt" ]]; then
    cat >"$destination/spaceship.zsh-theme" <<'THEME'
printf '[%s]\n' "$SPACESHIP_CHAR_SUFFIX" >"$PROMPT_CAPTURE"
THEME
    chmod 0644 "$destination/spaceship.zsh-theme"
  fi
}

install_openclaw_toolchain() {
  local install_root="$HOME/.local/opt/node-v$node_version"
  local dist="$install_root/lib/node_modules/corepack/dist"
  local shim link_target
  mkdir -p "$dist"
  for shim in pnpm pnpx; do
    cat >"$dist/$shim.js" <<'PNPM'
#!/usr/bin/env bash
printf '11.15.1\n'
PNPM
    chmod 0755 "$dist/$shim.js"
    if [[ ! -L "$PNPM_HOME/$shim" ]]; then
      link_target="$(relative_path_between "$PNPM_HOME" "$dist/$shim.js")"
      ln -s "$link_target" "$PNPM_HOME/$shim"
    fi
  done
}

mkdir -p \
  "$HOME/.ssh" \
  "$HOME/.local/bin" \
  "$HOME/.local/share/pnpm" \
  "$HOME/.local/state/dotfiles/linux-server/backups" \
  "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions" \
  "$HOME/.oh-my-zsh/custom/themes/spaceship-prompt"
chmod 0775 \
  "$HOME/.ssh" \
  "$HOME/.local" \
  "$HOME/.local/bin" \
  "$HOME/.local/share" \
  "$HOME/.local/share/pnpm" \
  "$HOME/.local/state" \
  "$HOME/.local/state/dotfiles" \
  "$HOME/.local/state/dotfiles/linux-server" \
  "$HOME/.local/state/dotfiles/linux-server/backups" \
  "$HOME/.oh-my-zsh" \
  "$HOME/.oh-my-zsh/custom" \
  "$HOME/.oh-my-zsh/custom/plugins" \
  "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions" \
  "$HOME/.oh-my-zsh/custom/themes" \
  "$HOME/.oh-my-zsh/custom/themes/spaceship-prompt"

refresh_user
first_links="$(
  find "$HOME" -type l -print |
    LC_ALL=C sort |
    while IFS= read -r path; do printf '%s -> %s\n' "$path" "$(readlink "$path")"; done
)"
first_backups="$(find "$state_root/backups" -mindepth 1 -print)"
refresh_user
second_links="$(
  find "$HOME" -type l -print |
    LC_ALL=C sort |
    while IFS= read -r path; do printf '%s -> %s\n' "$path" "$(readlink "$path")"; done
)"
[[ "$first_links" == "$second_links" ]]
[[ -z "$first_backups" ]]
[[ -z "$(find "$state_root/backups" -mindepth 1 -print)" ]]
EOF
chmod +x "$refresh_case"

home="$temporary/home"
mkdir "$home"
: >"$temporary/forbidden.log"
HOME="$home" \
  XDG_DATA_HOME="$home/.local/share" \
  XDG_STATE_HOME="$home/.local/state" \
  PNPM_HOME="$home/.local/share/pnpm" \
  PATH="$fakebin:/usr/bin:/bin" \
  FORBIDDEN_LOG="$temporary/forbidden.log" \
  BOOTSTRAP_SCRIPT="$script" \
  "$refresh_case"
[[ ! -s "$temporary/forbidden.log" ]]

run_refresh_component_case() {
  local name="$1"
  local case_home="$2"
  local case_pnpm_home="$3"
  local forbidden="$temporary/forbidden-$name.log"
  : >"$forbidden"
  HOME="$case_home" \
    XDG_DATA_HOME="$case_home/.local/share" \
    XDG_STATE_HOME="$case_home/.local/state" \
    PNPM_HOME="$case_pnpm_home" \
    PATH="$fakebin:/usr/bin:/bin" \
    FORBIDDEN_LOG="$forbidden" \
    BOOTSTRAP_SCRIPT="$script" \
    "$refresh_case"
  [[ ! -s "$forbidden" ]]
}

direct_home="$temporary/direct-home"
mkdir "$direct_home"
run_refresh_component_case direct "$direct_home" "$direct_home/pnpm"
[[ "$(mode "$direct_home/pnpm")" == 755 ]]

deep_home="$temporary/deep-home"
mkdir "$deep_home"
deep_pnpm="$deep_home/tools/node/package-managers/pnpm"
run_refresh_component_case deep "$deep_home" "$deep_pnpm"
for path in \
  "$deep_home/tools" \
  "$deep_home/tools/node" \
  "$deep_home/tools/node/package-managers" \
  "$deep_pnpm"; do
  [[ "$(mode "$path")" == 755 ]]
done

normalize_home="$temporary/normalize-home"
normalize_pnpm="$normalize_home/existing/deep/pnpm"
mkdir -p "$normalize_pnpm"
chmod 0775 \
  "$normalize_home/existing" \
  "$normalize_home/existing/deep" \
  "$normalize_pnpm"
run_refresh_component_case normalize "$normalize_home" "$normalize_pnpm"
for path in \
  "$normalize_home/existing" \
  "$normalize_home/existing/deep" \
  "$normalize_pnpm"; do
  [[ "$(mode "$path")" == 755 ]]
done

for secure_path in \
  "$home/.ssh" \
  "$home/.codex" \
  "$home/.local/state" \
  "$home/.local/state/dotfiles" \
  "$home/.local/state/dotfiles/linux-server" \
  "$home/.local/state/dotfiles/linux-server/backups"; do
  [[ "$(mode "$secure_path")" == 700 ]]
done
for public_path in \
  "$home/.local" \
  "$home/.local/bin" \
  "$home/.local/share" \
  "$home/.local/share/pnpm" \
  "$home/.local/opt" \
  "$home/.oh-my-zsh" \
  "$home/.oh-my-zsh/custom" \
  "$home/.oh-my-zsh/custom/plugins" \
  "$home/.oh-my-zsh/custom/plugins/zsh-autosuggestions" \
  "$home/.oh-my-zsh/custom/themes" \
  "$home/.oh-my-zsh/custom/themes/spaceship-prompt"; do
  [[ "$(mode "$public_path")" == 755 ]]
done
[[ -x "$home/.local/share/pnpm/pnpm" ]]
[[ -x "$home/.local/share/pnpm/pnpx" ]]
[[ ! -e "$home/.local/share/pnpm/bin" ]]
[[ "$(readlink "$home/.local/bin/codex")" == "$root/bin/codex" ]]
for absent in \
  "$home/.local/bin/quickssh" \
  "$home/.local/bin/op" \
  "$home/.local/bin/openclaw" \
  "$home/.local/bin/mttc" \
  "$home/.local/share/pnpm/yarn" \
  "$home/.local/share/pnpm/yarnpkg" \
  "$home/.codex/hooks.json" \
  "$home/.codex/AGENTS.md" \
  "$home/.codex/config.toml" \
  "$home/.codex/models.json" \
  "$home/.codex/auth.json"; do
  [[ ! -e "$absent" && ! -L "$absent" ]]
done
[[ -z "$(find "$home/.codex" -mindepth 1 -print)" ]]

profile_output="$(
  HOME="$home" \
    XDG_DATA_HOME="$home/.local/share" \
    PNPM_HOME="$home/.local/share/pnpm" \
    PATH="$home/.local/share/pnpm:/usr/bin:$home/.local/share/pnpm:/bin" \
    zsh -dfc '
      source "$HOME/.profile"
      print -r -- "$PATH"
    '
)"
[[ "$(tr ':' '\n' <<<"$profile_output" | grep -Fxc "$home/.local/share/pnpm")" == 1 ]]
if grep -Fq "$home/.local/share/pnpm/bin" <<<"$profile_output"; then
  printf 'server profile added PNPM_HOME/bin\n' >&2
  exit 1
fi

prompt_capture="$temporary/prompt-suffix"
cx_log="$temporary/cx-args"
HOME="$home" \
  PROMPT_CAPTURE="$prompt_capture" \
  CX_LOG="$cx_log" \
  zsh -dfc '
    codex() {
      print -r -- "$#" >"$CX_LOG"
      local argument
      for argument in "$@"; do
        print -r -- "$argument" >>"$CX_LOG"
      done
    }
    source "$HOME/.zshrc"
    cx "two words" "*"
  '
[[ "$(<"$prompt_capture")" == "[ ]" ]]
printf '%s\n' 3 --no-alt-screen "two words" '*' >"$temporary/cx-expected"
cmp "$temporary/cx-expected" "$cx_log"

drift_case="$temporary/drift-case.sh"
cat >"$drift_case" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1090
DOTFILES_SERVER_SOURCE_ONLY=1 source "$BOOTSTRAP_SCRIPT"
uname() {
  [[ "${1:-}" == -s ]] && {
    printf 'Linux\n'
    return
  }
  command uname "$@"
}
install_pinned_checkout() { :; }
install_openclaw_toolchain() { :; }

simulate_foreign_owner() {
  foreign="$1"
  original_owned_path_uid="$(declare -f owned_path_uid)"
  eval "${original_owned_path_uid/owned_path_uid/original_owned_path_uid}"
  owned_path_uid() {
    if [[ "$1" == "$foreign" ]]; then
      printf '424242\n'
    else
      original_owned_path_uid "$1"
    fi
  }
}

create_corepack_targets() {
  local install_root="$HOME/.local/opt/node-v$node_version"
  local dist="$install_root/lib/node_modules/corepack/dist"
  local shim
  mkdir -p "$dist"
  for shim in pnpm pnpx; do
    printf '#!/usr/bin/env bash\n' >"$dist/$shim.js"
    chmod 0755 "$dist/$shim.js"
  done
}

directory_metadata() {
  stat -c '%f:%s:%Y:%Z' "$1" 2>/dev/null ||
    stat -f '%p:%z:%m:%c' "$1"
}

case "$DRIFT_CASE" in
  symlink)
    mkdir "$HOME/redirect"
    ln -s "$HOME/redirect" "$HOME/.local"
    ;;
  type)
    : >"$HOME/.codex"
    ;;
  owner)
    mkdir -p "$HOME/.local/share/pnpm"
    simulate_foreign_owner "$HOME/.local/share/pnpm"
    ;;
  link)
    mkdir -p "$HOME/.local/bin"
    ln -s "$HOME/wrong-codex" "$HOME/.local/bin/codex"
    ;;
  home | terminal-parent | outside)
    ;;
  nested-symlink)
    mkdir "$HOME/nested"
    ln -s "$OUTSIDE_ROOT" "$HOME/nested/redirect"
    ;;
  nested-type)
    : >"$HOME/nested"
    ;;
  nested-owner)
    mkdir "$HOME/nested"
    simulate_foreign_owner "$HOME/nested"
    ;;
  world-mode)
    mkdir "$HOME/nested"
    chmod 0777 "$HOME/nested"
    ;;
  non-traversable)
    mkdir "$HOME/.local"
    ln -s "$OUTSIDE_ROOT" "$HOME/.local/bin"
    chmod 0600 "$HOME/.local"
    NON_TRAVERSABLE_METADATA="$(directory_metadata "$HOME/.local")"
    ;;
  pnpm-file)
    mkdir -p "$PNPM_HOME"
    printf 'keep-pnpm\n' >"$PNPM_HOME/pnpm"
    ;;
  pnpx-symlink)
    mkdir -p "$PNPM_HOME"
    ln -s "$OUTSIDE_ROOT/wrong-pnpx" "$PNPM_HOME/pnpx"
    ;;
  pnpm-owner)
    create_corepack_targets
    mkdir -p "$PNPM_HOME"
    ln -s "../../opt/node-v$node_version/lib/node_modules/corepack/dist/pnpm.js" \
      "$PNPM_HOME/pnpm"
    simulate_foreign_owner "$PNPM_HOME/pnpm"
    ;;
esac

if refresh_user >"$CASE_OUTPUT" 2>&1; then
  printf 'drift case unexpectedly succeeded: %s\n' "$DRIFT_CASE" >&2
  exit 1
fi
if [[ "$DRIFT_CASE" == non-traversable ]]; then
  [[ "$(directory_metadata "$HOME/.local")" == "$NON_TRAVERSABLE_METADATA" ]]
fi
[[ ! -e "$HOME/.ssh" && ! -L "$HOME/.ssh" ]]
[[ ! -e "$HOME/.local/state" && ! -L "$HOME/.local/state" ]]
EOF
chmod +x "$drift_case"

for case_name in \
  symlink type owner link home terminal-parent outside \
  nested-symlink nested-type nested-owner world-mode non-traversable \
  pnpm-file pnpx-symlink pnpm-owner; do
  case_home="$temporary/drift-$case_name"
  outside_root="$temporary/outside-$case_name"
  mkdir "$case_home"
  mkdir "$outside_root"
  chmod 0777 "$outside_root"
  printf 'outside-marker\n' >"$outside_root/marker"
  outside_mode_before="$(mode "$outside_root")"
  outside_listing_before="$(find "$outside_root" -mindepth 1 -print | LC_ALL=C sort)"
  home_mode_before="$(mode "$case_home")"
  case_pnpm_home="$case_home/.local/share/pnpm"
  case_state_home="$case_home/.local/state"
  case "$case_name" in
    home) case_pnpm_home="$case_home" ;;
    terminal-parent) case_pnpm_home="$case_home/.." ;;
    outside) case_pnpm_home="$case_home/../$(basename "$outside_root")/pnpm" ;;
    nested-symlink) case_pnpm_home="$case_home/nested/redirect/pnpm" ;;
    nested-type | nested-owner | world-mode)
      case_pnpm_home="$case_home/nested/tools/pnpm"
      ;;
  esac
  : >"$temporary/forbidden-$case_name.log"
  HOME="$case_home" \
    XDG_DATA_HOME="$case_home/.local/share" \
    XDG_STATE_HOME="$case_state_home" \
    PNPM_HOME="$case_pnpm_home" \
    PATH="$fakebin:/usr/bin:/bin" \
    FORBIDDEN_LOG="$temporary/forbidden-$case_name.log" \
    BOOTSTRAP_SCRIPT="$script" \
    DRIFT_CASE="$case_name" \
    OUTSIDE_ROOT="$outside_root" \
    CASE_OUTPUT="$temporary/drift-$case_name.out" \
    "$drift_case"
  [[ ! -s "$temporary/forbidden-$case_name.log" ]]
  grep -Eq 'refusing (unsafe|non-canonical|symlinked|non-directory|non-traversable|foreign-owned|world-writable|unexpected|user path outside)' \
    "$temporary/drift-$case_name.out"
  [[ "$(mode "$case_home")" == "$home_mode_before" ]]
  [[ "$(mode "$outside_root")" == "$outside_mode_before" ]]
  [[ "$(<"$outside_root/marker")" == outside-marker ]]
  [[ "$(find "$outside_root" -mindepth 1 -print | LC_ALL=C sort)" == "$outside_listing_before" ]]
  case "$case_name" in
    symlink)
      [[ "$(readlink "$case_home/.local")" == "$case_home/redirect" ]]
      ;;
    type)
      [[ -f "$case_home/.codex" && ! -L "$case_home/.codex" ]]
      ;;
    link)
      [[ "$(readlink "$case_home/.local/bin/codex")" == "$case_home/wrong-codex" ]]
      ;;
    nested-symlink)
      [[ "$(readlink "$case_home/nested/redirect")" == "$outside_root" ]]
      ;;
    nested-type)
      [[ -f "$case_home/nested" && ! -L "$case_home/nested" ]]
      ;;
    world-mode)
      [[ "$(mode "$case_home/nested")" == 777 ]]
      ;;
    non-traversable)
      [[ "$(mode "$case_home/.local")" == 600 ]]
      chmod 0700 "$case_home/.local"
      ;;
    pnpm-file)
      [[ "$(<"$case_pnpm_home/pnpm")" == keep-pnpm ]]
      ;;
    pnpx-symlink)
      [[ "$(readlink "$case_pnpm_home/pnpx")" == "$outside_root/wrong-pnpx" ]]
      ;;
    pnpm-owner)
      [[ "$(readlink "$case_pnpm_home/pnpm")" == \
        "../../opt/node-v24.19.0/lib/node_modules/corepack/dist/pnpm.js" ]]
      ;;
  esac
done

refresh_definitions="$(
  # shellcheck disable=SC1090
  DOTFILES_SERVER_SOURCE_ONLY=1 source "$script"
  declare -f refresh_user refresh_user_content preflight_refresh_user
)"
if grep -Eq '(^|[[:space:]])(sudo|apt|apt-get|usermod|sshd|ufw|run_root)([[:space:]]|$)' \
  <<<"$refresh_definitions"; then
  printf 'refresh-user contains a privileged mutation route\n' >&2
  exit 1
fi
ensure_directory_definition="$(
  # shellcheck disable=SC1090
  DOTFILES_SERVER_SOURCE_ONLY=1 source "$script"
  declare -f ensure_owned_directory
)"
if grep -Eq 'mkdir[[:space:]]+-p' <<<"$ensure_directory_definition"; then
  printf 'refresh-user uses mkdir -p for managed directory creation\n' >&2
  exit 1
fi

printf 'linux_server_refresh_user_test=passed\n'
