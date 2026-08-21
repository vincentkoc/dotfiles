#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="$root/bin/linux-server-bootstrap"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

bash -n "$script"
shellcheck "$script"
zsh -n \
  "$root/profiles/linux-server/zprofile" \
  "$root/profiles/linux-server/zshenv" \
  "$root/profiles/linux-server/zshrc"

mkdir -p "$temporary/home"
ln -s "$root" "$temporary/home/.dotfiles"
ln -s "$root/.aliases" "$temporary/home/.aliases"
ln -s "$root/.functions" "$temporary/home/.functions"
HOME="$temporary/home" zsh -dfc '
  source "$HOME/.dotfiles/profiles/linux-server/profile"
  source "$HOME/.dotfiles/profiles/linux-server/zshrc"
  alias gs >/dev/null
  whence -w gwt | grep -Fq "gwt: function"
  [[ "$TT_TMUX_BIN" == /usr/bin/tmux ]]
'

plan_one="$temporary/plan-one"
plan_two="$temporary/plan-two"
"$script" plan >"$plan_one"
"$script" plan >"$plan_two"
cmp "$plan_one" "$plan_two"
grep -Fxq 'mutates=no' "$plan_one"
grep -Fxq 'prepare.node=v24.19.0' "$plan_one"
grep -Fxq 'prepare.node_source=nodejs.org-checksum-pinned-user-install' "$plan_one"
grep -Fxq 'prepare.pnpm=pnpm@11.15.1' "$plan_one"
grep -Fxq 'prepare.excludes=credentials-signing-gui-apps-global-openclaw' "$plan_one"
grep -Fxq 'lockdown.interface=tailscale0' "$plan_one"

DOTFILES_SERVER_DRY_RUN=1 "$script" prepare >"$temporary/dry-run"
cmp "$plan_one" "$temporary/dry-run"

DOTFILES_SERVER_TAILSCALE_INTERFACE=ts0 \
  DOTFILES_SERVER_SSH_PORT=2222 \
  DOTFILES_SERVER_GATEWAY_PORT=8443 \
  "$script" plan >"$temporary/custom-plan"
grep -Fxq 'lockdown.interface=ts0' "$temporary/custom-plan"
grep -Fxq 'lockdown.ssh_port=2222' "$temporary/custom-plan"
grep -Fxq 'lockdown.gateway_port=8443' "$temporary/custom-plan"

set +e
DOTFILES_SERVER_SSH_PORT=invalid "$script" audit >"$temporary/invalid.out" 2>&1
invalid_status=$?
HOME="$temporary/home" "$script" lockdown >"$temporary/lockdown.out" 2>&1
lockdown_status=$?
set -e
[[ "$invalid_status" == 2 ]]
grep -Fq 'must be a TCP port' "$temporary/invalid.out"
[[ "$lockdown_status" != 0 ]]
grep -Eq 'requires a live Tailscale SSH connection|requires Linux' "$temporary/lockdown.out"

grep -Fq 'AuthenticationMethods publickey' "$script"
# shellcheck disable=SC2016
grep -Fq 'ufw allow in on "$tailscale_interface"' "$script"
grep -Fq 'lockdown must run from a different SSH session' "$script"
grep -Fq 'authorized_keys is empty; refusing SSH lockdown' "$script"
grep -Fq '14b342e71204f811bde6153be8e04b62aef63c236fef92b55f9c83154b409647' "$script"

if grep -Eq '(^|[[:space:]])(npm install -g openclaw|tailscale up --ssh|PermitRootLogin yes)' "$script"; then
  printf 'linux server bootstrap contains a prohibited default\n' >&2
  exit 1
fi
if grep -Eq '(curl[^|]*\|[^|]*(sudo|sh|bash)|osxkeychain|/opt/homebrew|IdentityAgent|git_signing)' \
  "$script" "$root/profiles/linux-server/"*; then
  printf 'linux server profile contains an unsafe installer or machine-specific path\n' >&2
  exit 1
fi
if grep -Eq '([0-9]{1,3}\.){3}[0-9]{1,3}|\.ts\.net|tailnet' \
  "$root/profiles/linux-server/"*; then
  printf 'linux server profile contains a private network identity\n' >&2
  exit 1
fi

printf 'linux_server_bootstrap_test=passed\n'
