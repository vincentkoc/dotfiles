#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="$root/bin/linux-server-bootstrap"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

bash -n "$script"
bash -n "$root/bin/tt"
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
  ! whence -w mymosh >/dev/null
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
grep -Fxq 'prepare.remote_shell=ssh' "$plan_one"
grep -Fxq 'prepare.excludes=credentials-signing-gui-apps-global-openclaw-mosh' "$plan_one"
grep -Fxq 'lockdown.requires=unchanged-authorized_keys,publickey-only-proof,tailscale-whois,passwordless-sudo,ufw-ipv6' "$plan_one"
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

mkdir -p "$temporary/installed-bin"
ln -s "$script" "$temporary/installed-bin/linux-server-bootstrap"
"$temporary/installed-bin/linux-server-bootstrap" plan >"$temporary/symlink-plan"
cmp "$plan_one" "$temporary/symlink-plan"

cat >"$temporary/fake-tmux" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" == has-session ]] && exit 1
exit 0
EOF
chmod +x "$temporary/fake-tmux"
ln -s "$root/bin/tt" "$temporary/installed-bin/tt"
set +e
TT_TMUX_BIN="$temporary/fake-tmux" \
  bash -x "$temporary/installed-bin/tt" not-a-session \
  >"$temporary/tt-symlink.out" 2>&1
tt_symlink_status=$?
set -e
[[ "$tt_symlink_status" == 2 ]]
grep -Fq "SCRIPT_DIR=$root/bin" "$temporary/tt-symlink.out"
grep -Fq "DOTFILES_DIR=$root" "$temporary/tt-symlink.out"

# shellcheck disable=SC1090
DOTFILES_SERVER_SOURCE_ONLY=1 source "$script"

is_tailscale_ip 100.64.0.1
is_tailscale_ip 100.127.255.254
is_tailscale_ip fd7a:115c:a1e0::1
if is_tailscale_ip 100.63.255.255 ||
  is_tailscale_ip 100.128.0.1 ||
  is_tailscale_ip fd7a:115c:a1df::1 ||
  is_tailscale_ip fd7a::1; then
  printf 'out-of-range address accepted as Tailscale\n' >&2
  exit 1
fi

mkdir -p "$temporary/fake-bin"
cat >"$temporary/fake-bin/tailscale" <<'EOF'
#!/usr/bin/env bash
if [[ "$*" == "whois --json 100.64.0.1" ]]; then
  printf '{"Node":{"ID":"node"},"UserProfile":{"ID":1}}\n'
  exit 0
fi
exit 1
EOF
chmod +x "$temporary/fake-bin/tailscale"
PATH="$temporary/fake-bin:$PATH" tailscale_whois_valid 100.64.0.1
if PATH="$temporary/fake-bin:$PATH" tailscale_whois_valid 100.64.0.2; then
  printf 'unknown Tailscale identity passed WhoIs\n' >&2
  exit 1
fi
SSH_CONNECTION='100.64.0.1 50000 100.100.100.100 22' \
  PATH="$temporary/fake-bin:$PATH" tailnet_source
if SSH_CONNECTION='100.0.0.1 50000 100.100.100.100 22' \
  PATH="$temporary/fake-bin:$PATH" tailnet_source; then
  printf 'non-Tailscale SSH source passed validation\n' >&2
  exit 1
fi

cat >"$temporary/fake-bin/sudo" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$SUDO_TEST_LOG"
if [[ "$*" == "-n true" && "${SUDO_TEST_NONINTERACTIVE:-0}" == 1 ]]; then
  exit 0
fi
[[ "$*" == "-v" ]]
EOF
chmod +x "$temporary/fake-bin/sudo"
: >"$temporary/sudo.log"
PATH="$temporary/fake-bin:$PATH" \
  SUDO_TEST_LOG="$temporary/sudo.log" \
  SUDO_TEST_NONINTERACTIVE=1 \
  ensure_sudo_access
grep -Fxq -- '-n true' "$temporary/sudo.log"
if grep -Fxq -- '-v' "$temporary/sudo.log"; then
  printf 'interactive sudo validation ran despite working noninteractive sudo\n' >&2
  exit 1
fi
: >"$temporary/sudo.log"
PATH="$temporary/fake-bin:$PATH" \
  SUDO_TEST_LOG="$temporary/sudo.log" \
  SUDO_TEST_NONINTERACTIVE=0 \
  ensure_sudo_access
printf '%s\n' '-n true' '-v' >"$temporary/sudo.expected"
cmp "$temporary/sudo.expected" "$temporary/sudo.log"

ssh-keygen -q -t ed25519 -N '' -C proof-key -f "$temporary/proof-key"
ssh-keygen -q -t ed25519 -N '' -C wrong-key -f "$temporary/wrong-key"
mkdir -p "$temporary/auth-home/.ssh"
cp "$temporary/proof-key.pub" "$temporary/auth-home/.ssh/authorized_keys"
chmod 600 "$temporary/auth-home/.ssh/authorized_keys"
proof_fingerprint="$(ssh-keygen -lf "$temporary/proof-key.pub" -E sha256 | awk '{print $2}')"
wrong_fingerprint="$(ssh-keygen -lf "$temporary/wrong-key.pub" -E sha256 | awk '{print $2}')"
read -r proof_key_type proof_key_blob _ <"$temporary/proof-key.pub"
printf 'publickey %s %s\n' "$proof_key_type" "$proof_key_blob" >"$temporary/auth-info"
chmod 600 "$temporary/auth-info"
SSH_USER_AUTH="$temporary/auth-info"
export SSH_USER_AUTH
[[ "$(auth_info_publickey_fingerprint)" == "$proof_fingerprint" ]]
printf 'publickey %s %s\n' "$proof_key_type" "$proof_fingerprint" >"$temporary/auth-info"
if auth_info_publickey_fingerprint >/dev/null; then
  printf 'fabricated SSH_USER_AUTH fingerprint format accepted\n' >&2
  exit 1
fi
printf 'publickey %s %s\n' "$proof_key_type" "$proof_key_blob" >"$temporary/auth-info"
authorized_keys_has_fingerprint "$temporary/auth-home/.ssh/authorized_keys" "$proof_fingerprint"
if authorized_keys_has_fingerprint "$temporary/auth-home/.ssh/authorized_keys" "$wrong_fingerprint"; then
  printf 'wrong authorized key fingerprint accepted\n' >&2
  exit 1
fi
authorized_keys_hash="$(sha256sum "$temporary/auth-home/.ssh/authorized_keys" | awk '{print $1}')"
validate_proven_authorized_keys \
  "$temporary/auth-home/.ssh/authorized_keys" \
  "$proof_fingerprint" \
  "$authorized_keys_hash"
printf '%s\n' "$(cat "$temporary/wrong-key.pub")" >"$temporary/auth-home/.ssh/authorized_keys"
if validate_proven_authorized_keys \
  "$temporary/auth-home/.ssh/authorized_keys" \
  "$proof_fingerprint" \
  "$authorized_keys_hash"; then
  printf 'changed authorized_keys accepted after proof\n' >&2
  exit 1
fi
printf 'keyboard-interactive\n' >>"$temporary/auth-info"
if auth_info_publickey_fingerprint >/dev/null; then
  printf 'multi-method SSH authentication accepted as publickey-only\n' >&2
  exit 1
fi
unset SSH_USER_AUTH

printf 'IPV6=yes\n' >"$temporary/ufw-good"
printf 'IPV6=no\n' >"$temporary/ufw-bad"
ufw_ipv6_enabled "$temporary/ufw-good"
if ufw_ipv6_enabled "$temporary/ufw-bad"; then
  printf 'UFW IPV6=no accepted\n' >&2
  exit 1
fi
ufw_rules='Status: active
22/tcp on tailscale0        ALLOW IN    Anywhere
22/tcp (v6) on tailscale0   ALLOW IN    Anywhere (v6)
443/tcp on tailscale0       ALLOW IN    Anywhere
443/tcp (v6) on tailscale0  ALLOW IN    Anywhere (v6)'
verify_ufw_coverage "$ufw_rules"
if verify_ufw_coverage "${ufw_rules//$'443/tcp (v6) on tailscale0  ALLOW IN    Anywhere (v6)'/}"; then
  printf 'UFW coverage accepted without Gateway IPv6 rule\n' >&2
  exit 1
fi

set +e
DOTFILES_SERVER_SSH_PORT=invalid "$script" audit >"$temporary/invalid.out" 2>&1
invalid_status=$?
HOME="$temporary/home" "$script" lockdown >"$temporary/lockdown.out" 2>&1
lockdown_status=$?
set -e
[[ "$invalid_status" == 2 ]]
grep -Fq 'must be a TCP port' "$temporary/invalid.out"
[[ "$lockdown_status" != 0 ]]
grep -Eq 'lockdown requires an exact Tailscale source verified by tailscale whois|requires Linux' \
  "$temporary/lockdown.out"

grep -Fq 'AuthenticationMethods publickey' "$script"
grep -Fq 'ExposeAuthInfo yes' "$script"
# shellcheck disable=SC2016
grep -Fq 'ufw allow in on "$tailscale_interface"' "$script"
grep -Fq 'lockdown must run from a different SSH session' "$script"
grep -Fq 'authorized_keys changed or no longer contains the proven SSH key' "$script"
grep -Fq '14b342e71204f811bde6153be8e04b62aef63c236fef92b55f9c83154b409647' "$script"
if grep -Fq 'tmux mosh gh' "$script"; then
  printf 'Mosh is still installed by the server profile\n' >&2
  exit 1
fi

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
