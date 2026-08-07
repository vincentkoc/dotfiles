#!/usr/bin/env zsh
set -euo pipefail

root="${0:A:h:h}"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

write_module() {
  local module_root="$1"
  local marker="$2"
  mkdir -p "$module_root/gwt"
  cat >"$module_root/gwt/gwt.zsh" <<EOF
typeset -g TEST_FUNCTIONS_AUTHORITY='$marker'
gwt() {
  print -r -- '$marker'
}
EOF
}

write_cloud_decoy() {
  local module_root="$1"
  mkdir -p "$module_root/decoy"
  cat >"$module_root/decoy/cloud.zsh" <<'EOF'
typeset -g TEST_CLOUDDOCS_LOADED=1
print -r -- touched >"$TEST_CLOUDDOCS_TOUCH"
EOF
}

assert_authority() {
  local home="$1"
  local functions_root="$2"
  local expected="$3"
  local touch_path="$4"

  HOME="$home" \
  DOTFILES_FUNCTIONS_ROOT="$functions_root" \
  TEST_CLOUDDOCS_TOUCH="$touch_path" \
  zsh -f -c '
    source "$1"
    [[ "$TEST_FUNCTIONS_AUTHORITY" == "$2" ]]
    [[ "$(gwt)" == "$2" ]]
    [[ -z "${TEST_CLOUDDOCS_LOADED:-}" ]]
  ' zsh "$root/.functions" "$expected"
  [[ ! -e "$touch_path" ]]
}

generated_home="$temporary/generated-home"
generated_cloud="$generated_home/Library/Mobile Documents/com~apple~CloudDocs/dotfiles/functions"
mkdir -p "$generated_home"
write_module "$generated_home/GIT/_Perso/dotfiles/functions" git
write_cloud_decoy "$generated_cloud"
ln -s "$generated_cloud" "$generated_home/functions"
assert_authority "$generated_home" "" git "$temporary/generated-empty.touch"
assert_authority "$generated_home" "$generated_home/functions" git "$temporary/generated-lexical.touch"

wsl_home="$temporary/wsl-home"
mkdir -p "$wsl_home"
write_module "$wsl_home/.dotfiles/functions" wsl
assert_authority "$wsl_home" "" wsl "$temporary/wsl.touch"

both_home="$temporary/both-home"
mkdir -p "$both_home"
write_module "$both_home/GIT/_Perso/dotfiles/functions" git
write_module "$both_home/.dotfiles/functions" wsl
assert_authority "$both_home" "" git "$temporary/both.touch"

explicit_home="$temporary/explicit-home"
explicit_root="$temporary/explicit-functions"
mkdir -p "$explicit_home"
write_module "$explicit_root" explicit
write_module "$explicit_home/GIT/_Perso/dotfiles/functions" git
assert_authority "$explicit_home" "$explicit_root" explicit "$temporary/explicit.touch"

cloud_explicit_home="$temporary/cloud-explicit-home"
cloud_explicit_root="$cloud_explicit_home/Library/Mobile Documents/com~apple~CloudDocs/custom/functions"
mkdir -p "$cloud_explicit_home"
write_cloud_decoy "$cloud_explicit_root"
write_module "$cloud_explicit_home/GIT/_Perso/dotfiles/functions" git
assert_authority "$cloud_explicit_home" "$cloud_explicit_root" git "$temporary/cloud-explicit.touch"

local_home="$temporary/local-home"
local_functions="$temporary/local-functions"
mkdir -p "$local_home"
write_module "$local_functions" home
ln -s "$local_functions" "$local_home/functions"
assert_authority "$local_home" "" home "$temporary/local.touch"

closed_home="$temporary/closed-home"
closed_cloud="$closed_home/Library/Mobile Documents/com~apple~CloudDocs/dotfiles/functions"
mkdir -p "$closed_home"
write_cloud_decoy "$closed_cloud"
ln -s "$closed_cloud" "$closed_home/functions"

HOME="$closed_home" \
DOTFILES_FUNCTIONS_ROOT="$closed_home/functions" \
DOTFILES_DIR="${closed_cloud:h}" \
TEST_CLOUDDOCS_TOUCH="$temporary/closed.touch" \
zsh -f -c '
  source "$1"
  [[ -z "${TEST_FUNCTIONS_AUTHORITY:-}" ]]
  [[ -z "${TEST_CLOUDDOCS_LOADED:-}" ]]
  ! whence -w gwt >/dev/null 2>&1
' zsh "$root/.functions"
[[ ! -e "$temporary/closed.touch" ]]

print 'functions authority tests passed'
