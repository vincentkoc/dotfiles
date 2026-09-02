#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="$root/bin/linux-server-bootstrap"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

mode() {
  stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"
}

create_test_repo() {
  local destination="$1"
  mkdir "$destination"
  (
    cd "$root"
    tar -cf - \
      .aliases \
      .functions \
      .ripgreprc \
      .tmux.conf \
      profiles/linux-server \
      bin/codex \
      bin/dotfiles-audit \
      bin/dotfiles-platform \
      bin/gh \
      bin/ghx \
      bin/linux-server-bootstrap \
      bin/tt
  ) | (
    cd "$destination"
    tar -xf -
  )
  chmod 0775 "$destination"
  chmod 0775 "$destination/bin"
  chmod 0775 \
    "$destination/bin/codex" \
    "$destination/bin/dotfiles-audit" \
    "$destination/bin/linux-server-bootstrap"
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
cat >"$fakebin/chmod" <<'EOF'
#!/usr/bin/env bash
if [[ -n "${CHMOD_CALL_LOG:-}" ]]; then
  printf '%s\n' "$*" >>"$CHMOD_CALL_LOG"
fi
for argument in "$@"; do
  if [[ -n "${FAIL_CHMOD_PATH:-}" && "$argument" == "$FAIL_CHMOD_PATH" ]]; then
    printf 'injected chmod failure: %s\n' "$argument" >>"$CHMOD_FAILURE_LOG"
    exit 96
  fi
  if [[ "$argument" == "$DEVELOPER_REPO_ROOT" ]]; then
    printf 'refusing test chmod of developer worktree\n' >>"$DEVELOPER_CHMOD_LOG"
    exit 98
  fi
done
exec /bin/chmod "$@"
EOF
chmod +x "$fakebin/chmod"

refresh_case="$temporary/refresh-case.sh"
cat >"$refresh_case" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 0002

# shellcheck disable=SC1090
DOTFILES_SERVER_SOURCE_ONLY=1 source "$BOOTSTRAP_SCRIPT"
repo_root="$TEST_REPO_ROOT"

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

if [[ "${PRESERVE_STANDALONE_CODEX:-0}" == 1 ]]; then
  standalone="$HOME/.codex/packages/standalone/current/bin/codex"
  standalone_release="$HOME/.codex/packages/standalone/releases/test-release"
  mkdir -p "$standalone_release/bin"
  chmod 0700 "$HOME/.codex"
  chmod 0755 \
    "$HOME/.codex/packages" \
    "$HOME/.codex/packages/standalone" \
    "$HOME/.codex/packages/standalone/releases" \
    "$standalone_release" \
    "$standalone_release/bin"
  ln -s "$standalone_release" "$HOME/.codex/packages/standalone/current"
  printf '#!/usr/bin/env bash\n' >"$standalone_release/bin/codex"
  chmod 0755 "$standalone_release/bin/codex"
  ln -s "$standalone" "$HOME/.local/bin/codex"
fi

refresh_user
[[ "$(owned_path_mode "$repo_root/bin")" == 755 ]]
first_links="$(
  find "$HOME" -type l -print |
    LC_ALL=C sort |
    while IFS= read -r path; do printf '%s -> %s\n' "$path" "$(readlink "$path")"; done
)"
first_backups="$(find "$state_root/backups" -mindepth 1 -print)"
refresh_user
[[ "$(owned_path_mode "$repo_root/bin")" == 755 ]]
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
test_repo="$home/.dotfiles"
create_test_repo "$test_repo"
: >"$temporary/forbidden.log"
: >"$temporary/developer-chmod.log"
HOME="$home" \
  XDG_DATA_HOME="$home/.local/share" \
  XDG_STATE_HOME="$home/.local/state" \
  PNPM_HOME="$home/.local/share/pnpm" \
  PATH="$fakebin:/usr/bin:/bin" \
  FORBIDDEN_LOG="$temporary/forbidden.log" \
  DEVELOPER_CHMOD_LOG="$temporary/developer-chmod.log" \
  DEVELOPER_REPO_ROOT="$root" \
  BOOTSTRAP_SCRIPT="$script" \
  TEST_REPO_ROOT="$test_repo" \
  "$refresh_case"
[[ ! -s "$temporary/forbidden.log" ]]
[[ ! -s "$temporary/developer-chmod.log" ]]
[[ "$(mode "$test_repo")" == 755 ]]
[[ "$(mode "$test_repo/bin")" == 755 ]]
[[ "$(mode "$test_repo/bin/codex")" == 755 ]]
[[ "$(mode "$test_repo/bin/linux-server-bootstrap")" == 755 ]]
[[ "$(mode "$test_repo/bin/dotfiles-audit")" == 755 ]]

run_refresh_component_case() {
  local name="$1"
  local case_home="$2"
  local case_pnpm_home="$3"
  local preserve_standalone="${4:-0}"
  local source_bin_mode="${5:-0775}"
  local forbidden="$temporary/forbidden-$name.log"
  local case_repo="$case_home/.dotfiles"
  create_test_repo "$case_repo"
  chmod "$source_bin_mode" "$case_repo/bin"
  : >"$forbidden"
  HOME="$case_home" \
    XDG_DATA_HOME="$case_home/.local/share" \
    XDG_STATE_HOME="$case_home/.local/state" \
    PNPM_HOME="$case_pnpm_home" \
    PATH="$fakebin:/usr/bin:/bin" \
    FORBIDDEN_LOG="$forbidden" \
    DEVELOPER_CHMOD_LOG="$temporary/developer-chmod.log" \
    DEVELOPER_REPO_ROOT="$root" \
    BOOTSTRAP_SCRIPT="$script" \
    TEST_REPO_ROOT="$case_repo" \
    PRESERVE_STANDALONE_CODEX="$preserve_standalone" \
    "$refresh_case"
  [[ ! -s "$forbidden" ]]
  [[ "$(mode "$case_repo")" == 755 ]]
  [[ "$(mode "$case_repo/bin")" == 755 ]]
  [[ "$(mode "$case_repo/bin/codex")" == 755 ]]
  [[ "$(mode "$case_repo/bin/linux-server-bootstrap")" == 755 ]]
  [[ "$(mode "$case_repo/bin/dotfiles-audit")" == 755 ]]
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

private_bin_home="$temporary/private-bin-home"
mkdir "$private_bin_home"
run_refresh_component_case \
  private-bin \
  "$private_bin_home" \
  "$private_bin_home/.local/share/pnpm" \
  0 \
  0700

standalone_home="$temporary/standalone-home"
mkdir "$standalone_home"
run_refresh_component_case \
  standalone \
  "$standalone_home" \
  "$standalone_home/.local/share/pnpm" \
  1
standalone_target="$standalone_home/.codex/packages/standalone/current/bin/codex"
standalone_release="$standalone_home/.codex/packages/standalone/releases/test-release"
[[ "$(readlink "$standalone_home/.local/bin/codex")" == "$standalone_target" ]]
[[ "$(readlink "$standalone_home/.codex/packages/standalone/current")" == \
  "$standalone_release" ]]
[[ "$(readlink -f "$standalone_target")" == \
  "$(readlink -f "$standalone_release/bin/codex")" ]]
[[ -x "$standalone_release/bin/codex" ]]

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
[[ "$(readlink "$home/.local/bin/codex")" == "$test_repo/bin/codex" ]]
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
repo_root="$TEST_REPO_ROOT"
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

create_standalone_codex_target() {
  standalone="$HOME/.codex/packages/standalone/current/bin/codex"
  standalone_current="$HOME/.codex/packages/standalone/current"
  standalone_release="$HOME/.codex/packages/standalone/releases/test-release"
  standalone_binary="$standalone_release/bin/codex"
  mkdir -p "$standalone_release/bin" "$HOME/.local/bin"
  ln -s "$standalone_release" "$standalone_current"
  printf '#!/usr/bin/env bash\n' >"$standalone_binary"
  chmod 0755 "$standalone_binary"
  ln -s "$standalone" "$HOME/.local/bin/codex"
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
  codex-wrong-target)
    mkdir -p "$HOME/.local/bin"
    ln -s "$HOME/wrong-codex" "$HOME/.local/bin/codex"
    ;;
  codex-file)
    mkdir -p "$HOME/.local/bin"
    printf 'keep-codex\n' >"$HOME/.local/bin/codex"
    ;;
  codex-foreign-target)
    create_standalone_codex_target
    simulate_foreign_owner "$(readlink -f "$standalone_binary")"
    ;;
  codex-standalone-symlink)
    create_standalone_codex_target
    rm "$standalone_binary"
    ln -s /bin/sh "$standalone_binary"
    ;;
  codex-standalone-hardlink)
    create_standalone_codex_target
    ln "$standalone_binary" "$HOME/standalone-codex-hardlink"
    ;;
  codex-standalone-unsafe-mode)
    create_standalone_codex_target
    chmod 0777 "$standalone_binary"
    ;;
  codex-standalone-noncanonical-mode)
    create_standalone_codex_target
    chmod 0775 "$standalone_binary"
    ;;
  codex-current-file)
    create_standalone_codex_target
    rm "$standalone_current"
    mkdir "$standalone_current"
    ;;
  codex-current-outside)
    standalone="$HOME/.codex/packages/standalone/current/bin/codex"
    standalone_current="$HOME/.codex/packages/standalone/current"
    standalone_release="$OUTSIDE_ROOT/release"
    standalone_binary="$standalone_release/bin/codex"
    mkdir -p "$HOME/.codex/packages/standalone/releases" "$HOME/.local/bin"
    ln -s "$standalone_release" "$standalone_current"
    ln -s "$standalone" "$HOME/.local/bin/codex"
    ;;
  codex-release-owner)
    create_standalone_codex_target
    simulate_foreign_owner "$standalone_release"
    ;;
  codex-release-world-mode)
    create_standalone_codex_target
    chmod 0777 "$standalone_release"
    ;;
  codex-release-bin-symlink)
    create_standalone_codex_target
    rm -rf "$standalone_release/bin"
    ln -s "$OUTSIDE_ROOT/release-bin" "$standalone_release/bin"
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
  repo-world-mode)
    chmod 0777 "$repo_root"
    ;;
  source-parent-symlink)
    mv "$repo_root/bin" "$OUTSIDE_ROOT/source-bin"
    ln -s "$OUTSIDE_ROOT/source-bin" "$repo_root/bin"
    ;;
  source-parent-file)
    mv "$repo_root/bin" "$OUTSIDE_ROOT/source-bin"
    printf 'keep-source-parent\n' >"$repo_root/bin"
    ;;
  source-parent-owner)
    simulate_foreign_owner "$repo_root/bin"
    ;;
  source-parent-world-mode)
    chmod 0777 "$repo_root/bin"
    ;;
  source-world-mode)
    chmod 0777 "$repo_root/bin/dotfiles-audit"
    ;;
  source-symlink)
    rm "$repo_root/bin/dotfiles-audit"
    ln -s "$OUTSIDE_ROOT/marker" "$repo_root/bin/dotfiles-audit"
    ;;
  source-hardlink)
    ln "$repo_root/bin/dotfiles-audit" "$OUTSIDE_ROOT/dotfiles-audit"
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

repo_mode_before="$(owned_path_mode "$repo_root")"
bin_mode_before="$(owned_path_mode "$repo_root/bin" 2>/dev/null || true)"
bootstrap_mode_before="$(owned_path_mode "$repo_root/bin/linux-server-bootstrap" 2>/dev/null || true)"
audit_mode_before="$(owned_path_mode "$repo_root/bin/dotfiles-audit" 2>/dev/null || true)"
: >"$CHMOD_CALL_LOG"
if refresh_user >"$CASE_OUTPUT" 2>&1; then
  printf 'drift case unexpectedly succeeded: %s\n' "$DRIFT_CASE" >&2
  exit 1
fi
if [[ "$DRIFT_CASE" == non-traversable ]]; then
  [[ "$(directory_metadata "$HOME/.local")" == "$NON_TRAVERSABLE_METADATA" ]]
fi
case "$DRIFT_CASE" in
  chmod-failure-root | chmod-failure-bin | chmod-failure-first-source) ;;
  *)
    [[ "$(owned_path_mode "$repo_root")" == "$repo_mode_before" ]]
    [[ "$(owned_path_mode "$repo_root/bin" 2>/dev/null || true)" == "$bin_mode_before" ]]
    [[ "$(owned_path_mode "$repo_root/bin/linux-server-bootstrap" 2>/dev/null || true)" == \
      "$bootstrap_mode_before" ]]
    [[ "$(owned_path_mode "$repo_root/bin/dotfiles-audit" 2>/dev/null || true)" == \
      "$audit_mode_before" ]]
    ;;
esac
if [[ "$DRIFT_CASE" == source-parent-symlink ]]; then
  [[ -L "$repo_root/bin" ]]
  [[ "$(owned_path_mode "$OUTSIDE_ROOT/source-bin/codex")" == 775 ]]
  [[ "$(owned_path_mode "$OUTSIDE_ROOT/source-bin/dotfiles-audit")" == 775 ]]
  [[ "$(owned_path_mode "$OUTSIDE_ROOT/source-bin/linux-server-bootstrap")" == 775 ]]
  rm "$repo_root/bin"
  mv "$OUTSIDE_ROOT/source-bin" "$repo_root/bin"
fi
if [[ "$DRIFT_CASE" == source-parent-file ]]; then
  [[ -f "$repo_root/bin" && "$(<"$repo_root/bin")" == keep-source-parent ]]
  rm "$repo_root/bin"
  mv "$OUTSIDE_ROOT/source-bin" "$repo_root/bin"
fi
if [[ "$DRIFT_CASE" == source-hardlink ]]; then
  [[ "$(owned_path_links "$repo_root/bin/dotfiles-audit")" == 2 ]]
  rm "$OUTSIDE_ROOT/dotfiles-audit"
fi
if [[ "$DRIFT_CASE" == codex-standalone-hardlink ]]; then
  [[ "$(owned_path_links "$standalone_binary")" == 2 ]]
  rm "$HOME/standalone-codex-hardlink"
fi
[[ ! -e "$HOME/.ssh" && ! -L "$HOME/.ssh" ]]
[[ ! -e "$HOME/.local/state" && ! -L "$HOME/.local/state" ]]
EOF
chmod +x "$drift_case"

for case_name in \
  symlink type owner codex-wrong-target codex-file codex-foreign-target \
  codex-standalone-symlink codex-standalone-hardlink codex-standalone-unsafe-mode \
  codex-standalone-noncanonical-mode codex-current-file codex-current-outside \
  codex-release-owner codex-release-world-mode codex-release-bin-symlink \
  home terminal-parent outside \
  nested-symlink nested-type nested-owner world-mode repo-world-mode non-traversable \
  source-parent-symlink source-parent-file source-parent-owner source-parent-world-mode \
  source-world-mode source-symlink source-hardlink \
  chmod-failure-root chmod-failure-bin chmod-failure-first-source \
  pnpm-file pnpx-symlink pnpm-owner; do
  case_home="$temporary/drift-$case_name"
  outside_root="$temporary/outside-$case_name"
  mkdir "$case_home"
  mkdir "$outside_root"
  case_repo="$case_home/.dotfiles"
  create_test_repo "$case_repo"
  chmod 0777 "$outside_root"
  printf 'outside-marker\n' >"$outside_root/marker"
  case "$case_name" in
    codex-current-outside)
      mkdir -p "$outside_root/release/bin"
      printf '#!/usr/bin/env bash\n' >"$outside_root/release/bin/codex"
      chmod 0755 "$outside_root/release/bin/codex"
      ;;
    codex-release-bin-symlink)
      mkdir -p "$outside_root/release-bin"
      printf '#!/usr/bin/env bash\n' >"$outside_root/release-bin/codex"
      chmod 0755 "$outside_root/release-bin/codex"
      ;;
  esac
  outside_mode_before="$(mode "$outside_root")"
  outside_listing_before="$(find "$outside_root" -mindepth 1 -print | LC_ALL=C sort)"
  home_mode_before="$(mode "$case_home")"
  case_pnpm_home="$case_home/.local/share/pnpm"
  case_state_home="$case_home/.local/state"
  fail_chmod_path=
  chmod_failure_log="$temporary/chmod-failure-$case_name.log"
  chmod_call_log="$temporary/chmod-calls-$case_name.log"
  : >"$chmod_failure_log"
  : >"$chmod_call_log"
  case "$case_name" in
    home) case_pnpm_home="$case_home" ;;
    terminal-parent) case_pnpm_home="$case_home/.." ;;
    outside) case_pnpm_home="$case_home/../$(basename "$outside_root")/pnpm" ;;
    nested-symlink) case_pnpm_home="$case_home/nested/redirect/pnpm" ;;
    nested-type | nested-owner | world-mode)
      case_pnpm_home="$case_home/nested/tools/pnpm"
      ;;
    chmod-failure-root) fail_chmod_path="$case_repo" ;;
    chmod-failure-bin) fail_chmod_path="$case_repo/bin" ;;
    chmod-failure-first-source) fail_chmod_path="$case_repo/bin/codex" ;;
  esac
  : >"$temporary/forbidden-$case_name.log"
  HOME="$case_home" \
    XDG_DATA_HOME="$case_home/.local/share" \
    XDG_STATE_HOME="$case_state_home" \
    PNPM_HOME="$case_pnpm_home" \
    PATH="$fakebin:/usr/bin:/bin" \
    FORBIDDEN_LOG="$temporary/forbidden-$case_name.log" \
    DEVELOPER_CHMOD_LOG="$temporary/developer-chmod.log" \
    DEVELOPER_REPO_ROOT="$root" \
    BOOTSTRAP_SCRIPT="$script" \
    TEST_REPO_ROOT="$case_repo" \
    DRIFT_CASE="$case_name" \
    OUTSIDE_ROOT="$outside_root" \
    CASE_OUTPUT="$temporary/drift-$case_name.out" \
    FAIL_CHMOD_PATH="$fail_chmod_path" \
    CHMOD_FAILURE_LOG="$chmod_failure_log" \
    CHMOD_CALL_LOG="$chmod_call_log" \
    "$drift_case"
  [[ ! -s "$temporary/forbidden-$case_name.log" ]]
  if [[ "$case_name" == chmod-failure-* ]]; then
    grep -Fxq "injected chmod failure: $fail_chmod_path" "$chmod_failure_log"
    [[ "$(wc -l <"$chmod_failure_log" | tr -d ' ')" == 1 ]]
    expected_chmod_calls="$temporary/chmod-calls-$case_name.expected"
    case "$case_name" in
      chmod-failure-root)
        printf '0755 %s\n' "$case_repo" >"$expected_chmod_calls"
        ;;
      chmod-failure-bin)
        printf '0755 %s\n' \
          "$case_repo" \
          "$case_repo/bin" >"$expected_chmod_calls"
        ;;
      chmod-failure-first-source)
        printf '0755 %s\n' \
          "$case_repo" \
          "$case_repo/bin" \
          "$case_repo/bin/codex" >"$expected_chmod_calls"
        ;;
    esac
    cmp "$expected_chmod_calls" "$chmod_call_log"
  else
    grep -Eq 'refusing (unsafe|non-canonical|symlinked|linked|non-directory|non-traversable|foreign-owned|world-writable|unexpected|user path outside)' \
      "$temporary/drift-$case_name.out"
    [[ ! -s "$chmod_failure_log" ]]
  fi
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
    codex-wrong-target)
      [[ "$(readlink "$case_home/.local/bin/codex")" == "$case_home/wrong-codex" ]]
      ;;
    codex-file)
      [[ "$(<"$case_home/.local/bin/codex")" == keep-codex ]]
      ;;
    codex-foreign-target)
      [[ "$(readlink "$case_home/.local/bin/codex")" == \
        "$case_home/.codex/packages/standalone/current/bin/codex" ]]
      ;;
    codex-standalone-symlink)
      [[ "$(readlink "$case_home/.codex/packages/standalone/releases/test-release/bin/codex")" == \
        /bin/sh ]]
      ;;
    codex-standalone-hardlink)
      [[ ! -e "$case_home/standalone-codex-hardlink" ]]
      ;;
    codex-standalone-unsafe-mode)
      [[ "$(mode "$case_home/.codex/packages/standalone/releases/test-release/bin/codex")" == \
        777 ]]
      ;;
    codex-standalone-noncanonical-mode)
      [[ "$(mode "$case_home/.codex/packages/standalone/releases/test-release/bin/codex")" == \
        775 ]]
      ;;
    codex-current-file)
      [[ -d "$case_home/.codex/packages/standalone/current" ]]
      ;;
    codex-current-outside)
      [[ "$(readlink "$case_home/.codex/packages/standalone/current")" == \
        "$outside_root/release" ]]
      ;;
    codex-release-owner)
      [[ -d "$case_home/.codex/packages/standalone/releases/test-release" ]]
      ;;
    codex-release-world-mode)
      [[ "$(mode "$case_home/.codex/packages/standalone/releases/test-release")" == 777 ]]
      ;;
    codex-release-bin-symlink)
      [[ "$(readlink "$case_home/.codex/packages/standalone/releases/test-release/bin")" == \
        "$outside_root/release-bin" ]]
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
    repo-world-mode)
      [[ "$(mode "$case_repo")" == 777 ]]
      ;;
    source-parent-symlink)
      [[ -d "$case_repo/bin" && ! -L "$case_repo/bin" ]]
      ;;
    source-parent-file)
      [[ -d "$case_repo/bin" && ! -L "$case_repo/bin" ]]
      ;;
    source-parent-owner)
      [[ -d "$case_repo/bin" && ! -L "$case_repo/bin" ]]
      ;;
    source-parent-world-mode)
      [[ "$(mode "$case_repo/bin")" == 777 ]]
      ;;
    source-world-mode)
      [[ "$(mode "$case_repo/bin/dotfiles-audit")" == 777 ]]
      ;;
    source-symlink)
      [[ "$(readlink "$case_repo/bin/dotfiles-audit")" == \
        "$outside_root/marker" ]]
      ;;
    source-hardlink) [[ ! -e "$outside_root/dotfiles-audit" ]] ;;
    chmod-failure-root)
      [[ "$(mode "$case_repo")" == 775 ]]
      [[ "$(mode "$case_repo/bin")" == 775 ]]
      [[ "$(mode "$case_repo/bin/codex")" == 775 ]]
      [[ "$(mode "$case_repo/bin/dotfiles-audit")" == 775 ]]
      [[ "$(mode "$case_repo/bin/linux-server-bootstrap")" == 775 ]]
      ;;
    chmod-failure-bin)
      [[ "$(mode "$case_repo")" == 755 ]]
      [[ "$(mode "$case_repo/bin")" == 775 ]]
      [[ "$(mode "$case_repo/bin/codex")" == 775 ]]
      [[ "$(mode "$case_repo/bin/dotfiles-audit")" == 775 ]]
      [[ "$(mode "$case_repo/bin/linux-server-bootstrap")" == 775 ]]
      ;;
    chmod-failure-first-source)
      [[ "$(mode "$case_repo")" == 755 ]]
      [[ "$(mode "$case_repo/bin")" == 755 ]]
      [[ "$(mode "$case_repo/bin/codex")" == 775 ]]
      [[ "$(mode "$case_repo/bin/dotfiles-audit")" == 775 ]]
      [[ "$(mode "$case_repo/bin/linux-server-bootstrap")" == 775 ]]
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
[[ ! -s "$temporary/developer-chmod.log" ]]

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
