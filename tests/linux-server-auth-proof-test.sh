#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="$root/bin/linux-server-bootstrap"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

export HOME="$temporary/home"
export XDG_STATE_HOME="$temporary/state"
mkdir -p "$HOME/.ssh" "$temporary/etc/ssh/sshd_config.d"

# shellcheck disable=SC1090
DOTFILES_SERVER_SOURCE_ONLY=1 source "$script"
# shellcheck disable=SC2154
test_state_root="$state_root"
# shellcheck disable=SC2154
test_auth_proof_state_file="$auth_proof_state_file"

auth_proof_dropin_path() {
  printf '%s\n' "$temporary/etc/ssh/sshd_config.d/50-dotfiles-auth-proof.conf"
}

lockdown_dropin_path() {
  printf '%s\n' "$temporary/etc/ssh/sshd_config.d/60-dotfiles-linux-server.conf"
}

ssh_service_name() {
  printf 'ssh\n'
}

run_root() {
  if [[ "${1:-}" == mv && "${2:-}" == -T ]]; then
    shift 2
    command mv "$@"
    return
  fi
  command "$@"
}

validate_managed_sshd_dropin() {
  local path="$1"
  [[ -f "$path" && ! -L "$path" && "$(path_mode "$path")" == 644 ]]
}

sshd_config_valid() {
  printf 'validate\n' >>"$temporary/ssh-events"
  if [[ -e "$temporary/fail-validate-once" ]]; then
    rm -f "$temporary/fail-validate-once"
    return 1
  fi
}

sshd_effective_expose_auth_info() {
  local proof_dropin final_dropin
  if [[ -n "${FORCE_EFFECTIVE_EXPOSE_AUTH_INFO:-}" ]]; then
    printf '%s\n' "$FORCE_EFFECTIVE_EXPOSE_AUTH_INFO"
    return
  fi
  proof_dropin="$(auth_proof_dropin_path)"
  final_dropin="$(lockdown_dropin_path)"
  if [[ -f "$proof_dropin" ]] && grep -Fq 'ExposeAuthInfo yes' "$proof_dropin"; then
    printf 'yes\n'
  elif [[ -f "$final_dropin" ]] && grep -Fq 'ExposeAuthInfo no' "$final_dropin"; then
    printf 'no\n'
  else
    printf 'no\n'
  fi
}

reload_ssh_service() {
  printf 'reload:%s\n' "$1" >>"$temporary/ssh-events"
  if [[ -e "$temporary/fail-reload-once" ]]; then
    rm -f "$temporary/fail-reload-once"
    return 1
  fi
}

reset_activation_fixture() {
  rm -rf "${temporary:?}/etc" "$temporary/state"
  mkdir -p "$temporary/etc/ssh/sshd_config.d"
  : >"$temporary/ssh-events"
  unset FORCE_EFFECTIVE_EXPOSE_AUTH_INFO
}

ssh-keygen -q -t ed25519 -N '' -C designated-key -f "$temporary/designated-key"
ssh-keygen -q -t ed25519 -N '' -C wrong-key -f "$temporary/wrong-key"
cp "$temporary/designated-key.pub" "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
designated_fingerprint="$(
  ssh-keygen -lf "$temporary/designated-key.pub" -E sha256 |
    awk '{print $2}'
)"
authorized_keys_hash="$(sha256sum "$HOME/.ssh/authorized_keys" | awk '{print $1}')"
admin_user="$(id -un)"

sshd_test_bin="$(command -v sshd || true)"
if [[ -z "$sshd_test_bin" && -x /usr/sbin/sshd ]]; then
  sshd_test_bin=/usr/sbin/sshd
fi
if [[ -n "$sshd_test_bin" ]]; then
  render_auth_proof_dropin "$admin_user" "$temporary/sshd-effective.conf"
  printf 'ExposeAuthInfo no\n' >>"$temporary/sshd-effective.conf"
  [[ "$(
    "$sshd_test_bin" -T -f "$temporary/sshd-effective.conf" \
      -C "user=$admin_user,host=localhost,addr=127.0.0.1" |
      awk '$1=="exposeauthinfo"{print $2}'
  )" == yes ]]
  [[ "$(
    "$sshd_test_bin" -T -f "$temporary/sshd-effective.conf" \
      -C "user=other,host=localhost,addr=127.0.0.1" |
      awk '$1=="exposeauthinfo"{print $2}'
  )" == no ]]
  printf 'ExposeAuthInfo no\n' >"$temporary/sshd-final.conf"
  [[ "$(
    "$sshd_test_bin" -T -f "$temporary/sshd-final.conf" \
      -C "user=$admin_user,host=localhost,addr=127.0.0.1" |
      awk '$1=="exposeauthinfo"{print $2}'
  )" == no ]]
fi

reset_activation_fixture
printf 'operator-owned-content\n' >"$(auth_proof_dropin_path)"
if activate_auth_proof_transaction \
  "$admin_user" \
  "$designated_fingerprint" \
  "$authorized_keys_hash"; then
  printf 'unexpected preexisting auth-proof drop-in was overwritten\n' >&2
  exit 1
fi
grep -Fxq 'operator-owned-content' "$(auth_proof_dropin_path)"
[[ ! -e "$test_auth_proof_state_file" ]]

reset_activation_fixture
touch "$temporary/fail-validate-once"
if activate_auth_proof_transaction \
  "$admin_user" \
  "$designated_fingerprint" \
  "$authorized_keys_hash"; then
  printf 'invalid sshd configuration was accepted\n' >&2
  exit 1
fi
[[ ! -e "$(auth_proof_dropin_path)" ]]
[[ ! -e "$test_auth_proof_state_file" ]]
grep -Fxq 'reload:ssh' "$temporary/ssh-events"

reset_activation_fixture
FORCE_EFFECTIVE_EXPOSE_AUTH_INFO=yes
export FORCE_EFFECTIVE_EXPOSE_AUTH_INFO
if activate_auth_proof_transaction \
  "$admin_user" \
  "$designated_fingerprint" \
  "$authorized_keys_hash"; then
  printf 'preexisting effective ExposeAuthInfo exposure was accepted\n' >&2
  exit 1
fi
unset FORCE_EFFECTIVE_EXPOSE_AUTH_INFO
[[ ! -e "$(auth_proof_dropin_path)" ]]
[[ ! -e "$test_auth_proof_state_file" ]]

reset_activation_fixture
FORCE_EFFECTIVE_EXPOSE_AUTH_INFO=no
export FORCE_EFFECTIVE_EXPOSE_AUTH_INFO
if activate_auth_proof_transaction \
  "$admin_user" \
  "$designated_fingerprint" \
  "$authorized_keys_hash"; then
  printf 'effective ExposeAuthInfo mismatch was accepted\n' >&2
  exit 1
fi
unset FORCE_EFFECTIVE_EXPOSE_AUTH_INFO
[[ ! -e "$(auth_proof_dropin_path)" ]]
[[ ! -e "$test_auth_proof_state_file" ]]

reset_activation_fixture
touch "$temporary/fail-reload-once"
if activate_auth_proof_transaction \
  "$admin_user" \
  "$designated_fingerprint" \
  "$authorized_keys_hash"; then
  printf 'failed SSH service assertion was accepted\n' >&2
  exit 1
fi
[[ ! -e "$(auth_proof_dropin_path)" ]]
[[ ! -e "$test_auth_proof_state_file" ]]
[[ "$(grep -Fc 'reload:ssh' "$temporary/ssh-events")" == 2 ]]

reset_activation_fixture
activate_auth_proof_transaction \
  "$admin_user" \
  "$designated_fingerprint" \
  "$authorized_keys_hash"
validate_auth_proof_state
validate_active_auth_proof_dropin
[[ "$(path_mode "$test_state_root")" == 700 ]]
[[ "$(path_mode "$test_auth_proof_state_file")" == 600 ]]
chmod 644 "$test_auth_proof_state_file"
if validate_auth_proof_state; then
  printf 'non-private auth-proof state was accepted\n' >&2
  exit 1
fi
chmod 600 "$test_auth_proof_state_file"
grep -Fxq "Match User $admin_user" "$(auth_proof_dropin_path)"
grep -Fxq 'Match all' "$(auth_proof_dropin_path)"
independent_ssh_transport \
  '100.64.0.1 50000 100.64.0.2 22' \
  '100.64.0.1 50001 100.64.0.2 22'
if independent_ssh_transport \
  '100.64.0.1 50000 100.64.0.2 22' \
  '100.64.0.1 50000 100.64.0.2 22'; then
  printf 'multiplexed SSH transport was accepted as independent\n' >&2
  exit 1
fi

read -r wrong_key_type wrong_key_blob _ <"$temporary/wrong-key.pub"
printf 'publickey %s %s\n' "$wrong_key_type" "$wrong_key_blob" >"$temporary/auth-info"
chmod 600 "$temporary/auth-info"
SSH_USER_AUTH="$temporary/auth-info"
export SSH_USER_AUTH
enabled_epoch="$(state_value "$test_auth_proof_state_file" enabled_epoch)"
if validate_designated_auth_session \
  "$designated_fingerprint" \
  "$HOME/.ssh/authorized_keys" \
  "$authorized_keys_hash" \
  "$enabled_epoch" >/dev/null; then
  printf 'non-designated SSH key was accepted\n' >&2
  exit 1
fi

read -r designated_key_type designated_key_blob _ <"$temporary/designated-key.pub"
printf 'publickey %s %s\n' "$designated_key_type" "$designated_key_blob" >"$temporary/auth-info"
touch -t 200001010000 "$temporary/auth-info"
if validate_designated_auth_session \
  "$designated_fingerprint" \
  "$HOME/.ssh/authorized_keys" \
  "$authorized_keys_hash" \
  "$enabled_epoch" >/dev/null; then
  printf 'stale pre-activation SSH authentication was accepted\n' >&2
  exit 1
fi
touch "$temporary/auth-info"
[[ "$(
  validate_designated_auth_session \
    "$designated_fingerprint" \
    "$HOME/.ssh/authorized_keys" \
    "$authorized_keys_hash" \
    "$enabled_epoch"
)" == "$designated_fingerprint" ]]
unset SSH_USER_AUTH

stage_lockdown_ssh_config
grep -Fxq 'ExposeAuthInfo no' "$(lockdown_dropin_path)"
FORCE_EFFECTIVE_EXPOSE_AUTH_INFO=yes
export FORCE_EFFECTIVE_EXPOSE_AUTH_INFO
if finalize_auth_proof_cleanup "$admin_user"; then
  printf 'final effective ExposeAuthInfo mismatch was accepted\n' >&2
  exit 1
fi
unset FORCE_EFFECTIVE_EXPOSE_AUTH_INFO
[[ -f "$(auth_proof_dropin_path)" ]]
[[ "$(sshd_effective_expose_auth_info "$admin_user")" == yes ]]

finalize_auth_proof_cleanup "$admin_user"
[[ ! -e "$(auth_proof_dropin_path)" ]]
[[ "$(sshd_effective_expose_auth_info "$admin_user")" == no ]]
grep -Fxq 'ExposeAuthInfo no' "$(lockdown_dropin_path)"

printf 'linux_server_auth_proof_test=passed\n'
