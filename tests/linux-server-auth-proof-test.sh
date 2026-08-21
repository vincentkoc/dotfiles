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
# shellcheck disable=SC2154
test_auth_proof_ttl="$auth_proof_ttl"

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
    if [[ -e "$temporary/fail-mv-once" ]]; then
      rm -f "$temporary/fail-mv-once"
      return 1
    fi
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

validate_auth_proof_candidate() {
  [[ ! -e "$temporary/fail-auth-candidate" ]]
}

validate_lockdown_candidate() {
  [[ ! -e "$temporary/fail-lockdown-candidate" ]]
}

sshd_config_valid() {
  printf 'validate\n' >>"$temporary/ssh-events"
  if [[ -e "$temporary/fail-validate-once" ]]; then
    rm -f "$temporary/fail-validate-once"
    return 1
  fi
}

write_effective_config() {
  local locked="$1"
  local expose="$2"
  if [[ "$locked" == 1 ]]; then
    printf '%s\n' \
      'passwordauthentication no' \
      'kbdinteractiveauthentication no' \
      'permitrootlogin no' \
      'pubkeyauthentication yes' \
      'authenticationmethods publickey' \
      "exposeauthinfo $expose" \
      'x11forwarding no' \
      'allowagentforwarding no'
  else
    printf '%s\n' \
      'passwordauthentication yes' \
      'kbdinteractiveauthentication yes' \
      'permitrootlogin prohibit-password' \
      'pubkeyauthentication yes' \
      'authenticationmethods any' \
      "exposeauthinfo $expose" \
      'x11forwarding yes' \
      'allowagentforwarding yes'
  fi
}

sshd_effective_config() {
  local proof_dropin final_dropin
  if [[ -n "${SSHD_EFFECTIVE_OVERRIDE_FILE:-}" ]]; then
    cat "$SSHD_EFFECTIVE_OVERRIDE_FILE"
    return
  fi
  proof_dropin="$(auth_proof_dropin_path)"
  final_dropin="$(lockdown_dropin_path)"
  if [[ -e "$temporary/shadow-lockdown-once" && -f "$final_dropin" ]]; then
    rm -f "$temporary/shadow-lockdown-once"
    write_effective_config 1 yes
    return
  fi
  if [[ -e "$temporary/force-proof-effective-no" && -f "$proof_dropin" ]]; then
    write_effective_config 0 no
    return
  fi
  if [[ -f "$proof_dropin" ]] && grep -Fq 'ExposeAuthInfo yes' "$proof_dropin"; then
    write_effective_config 0 yes
  elif [[ -f "$final_dropin" ]] && grep -Fq 'ExposeAuthInfo no' "$final_dropin"; then
    write_effective_config 1 no
  else
    write_effective_config 0 no
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
  rm -f \
    "$temporary/fail-auth-candidate" \
    "$temporary/fail-lockdown-candidate" \
    "$temporary/fail-mv-once" \
    "$temporary/fail-reload-once" \
    "$temporary/fail-validate-once" \
    "$temporary/force-proof-effective-no" \
    "$temporary/shadow-lockdown-once"
  unset SSHD_EFFECTIVE_OVERRIDE_FILE
  test_boot_id=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa
  test_epoch=1700000000
}

current_boot_id() {
  printf '%s\n' "$test_boot_id"
}

current_epoch() {
  printf '%s\n' "$test_epoch"
}

run_cleanup_command() (
  # shellcheck disable=SC2329
  sudo() { :; }
  # shellcheck disable=SC2329
  validate_parameters() { :; }
  # shellcheck disable=SC2329
  require_non_root() { :; }
  # shellcheck disable=SC2329
  require_ubuntu() { :; }
  # shellcheck disable=SC2329
  ensure_sudo_access() { :; }
  cleanup_auth_proof
)

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
  render_lockdown_dropin "$temporary/sshd-lockdown.conf"
  real_admin_config="$(
    "$sshd_test_bin" -T -f "$temporary/sshd-lockdown.conf" \
      -C "user=$admin_user,host=localhost,addr=127.0.0.1"
  )"
  real_root_config="$(
    "$sshd_test_bin" -T -f "$temporary/sshd-lockdown.conf" \
      -C "user=root,host=localhost,addr=127.0.0.1"
  )"
  for real_config in "$real_admin_config" "$real_root_config"; do
    assert_effective_sshd_value "$real_config" passwordauthentication no
    assert_effective_sshd_value "$real_config" kbdinteractiveauthentication no
    assert_effective_sshd_value "$real_config" pubkeyauthentication yes
    assert_effective_sshd_value "$real_config" authenticationmethods publickey
    assert_effective_sshd_value "$real_config" exposeauthinfo no
    assert_effective_sshd_value "$real_config" x11forwarding no
    assert_effective_sshd_value "$real_config" allowagentforwarding no
  done
  assert_effective_sshd_value "$real_root_config" permitrootlogin no
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
write_effective_config 0 yes >"$temporary/effective-override"
SSHD_EFFECTIVE_OVERRIDE_FILE="$temporary/effective-override"
export SSHD_EFFECTIVE_OVERRIDE_FILE
if activate_auth_proof_transaction \
  "$admin_user" \
  "$designated_fingerprint" \
  "$authorized_keys_hash"; then
  printf 'preexisting effective ExposeAuthInfo exposure was accepted\n' >&2
  exit 1
fi
unset SSHD_EFFECTIVE_OVERRIDE_FILE
[[ ! -e "$(auth_proof_dropin_path)" ]]
[[ ! -e "$test_auth_proof_state_file" ]]

reset_activation_fixture
touch "$temporary/force-proof-effective-no"
if activate_auth_proof_transaction \
  "$admin_user" \
  "$designated_fingerprint" \
  "$authorized_keys_hash"; then
  printf 'effective ExposeAuthInfo mismatch was accepted\n' >&2
  exit 1
fi
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
validate_active_auth_proof_state
validate_active_auth_proof_dropin
[[ "$(path_mode "$test_state_root")" == 700 ]]
[[ "$(path_mode "$test_auth_proof_state_file")" == 600 ]]
chmod 644 "$test_auth_proof_state_file"
if validate_auth_proof_state; then
  printf 'non-private auth-proof state was accepted\n' >&2
  exit 1
fi
chmod 600 "$test_auth_proof_state_file"
activation_epoch="$(state_value "$test_auth_proof_state_file" enabled_epoch)"
test_epoch=$((activation_epoch + test_auth_proof_ttl + 1))
if validate_active_auth_proof_state; then
  printf 'expired auth-proof activation was accepted\n' >&2
  exit 1
fi
validate_auth_proof_state
test_epoch="$activation_epoch"
test_boot_id=bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb
if validate_active_auth_proof_state; then
  printf 'rebooted auth-proof activation was accepted\n' >&2
  exit 1
fi
test_boot_id=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa
validate_active_auth_proof_state
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

write_effective_config 0 yes >"$temporary/effective-override"
SSHD_EFFECTIVE_OVERRIDE_FILE="$temporary/effective-override"
export SSHD_EFFECTIVE_OVERRIDE_FILE
if cleanup_auth_proof_exposure "$admin_user"; then
  printf 'auth-proof cleanup effective mismatch was accepted\n' >&2
  exit 1
fi
unset SSHD_EFFECTIVE_OVERRIDE_FILE
[[ -f "$(auth_proof_dropin_path)" ]]
assert_auth_proof_effective "$admin_user" yes

cleanup_auth_proof_exposure "$admin_user"
[[ ! -e "$(auth_proof_dropin_path)" ]]
assert_auth_proof_effective "$admin_user" no
rm -f "$test_auth_proof_state_file"
run_cleanup_command
run_cleanup_command

reset_activation_fixture
activate_auth_proof_transaction \
  "$admin_user" \
  "$designated_fingerprint" \
  "$authorized_keys_hash"
activation_epoch="$(state_value "$test_auth_proof_state_file" enabled_epoch)"
test_epoch=$((activation_epoch + test_auth_proof_ttl + 1))
if validate_active_auth_proof_state; then
  printf 'expired activation unexpectedly remained active\n' >&2
  exit 1
fi
run_cleanup_command
[[ ! -e "$(auth_proof_dropin_path)" ]]
[[ ! -e "$test_auth_proof_state_file" ]]
run_cleanup_command

reset_activation_fixture
write_effective_config 1 no >"$temporary/effective-locked"
while read -r shadow_key shadow_value; do
  sed "s/^${shadow_key} .*/${shadow_key} ${shadow_value}/" \
    "$temporary/effective-locked" >"$temporary/effective-shadowed"
  SSHD_EFFECTIVE_OVERRIDE_FILE="$temporary/effective-shadowed"
  export SSHD_EFFECTIVE_OVERRIDE_FILE
  if assert_lockdown_sshd_effective "$admin_user"; then
    printf 'shadowed effective sshd value was accepted: %s\n' "$shadow_key" >&2
    exit 1
  fi
done <<'EOF'
passwordauthentication yes
kbdinteractiveauthentication yes
permitrootlogin yes
pubkeyauthentication no
authenticationmethods any
exposeauthinfo yes
x11forwarding yes
allowagentforwarding yes
EOF
unset SSHD_EFFECTIVE_OVERRIDE_FILE

printf 'operator-lockdown-content\n' >"$(lockdown_dropin_path)"
chmod 644 "$(lockdown_dropin_path)"
if install_lockdown_ssh_config "$admin_user"; then
  printf 'unknown lockdown drop-in content was overwritten\n' >&2
  exit 1
fi
grep -Fxq 'operator-lockdown-content' "$(lockdown_dropin_path)"

reset_activation_fixture
touch "$temporary/fail-lockdown-candidate"
if install_lockdown_ssh_config "$admin_user"; then
  printf 'invalid lockdown candidate was published\n' >&2
  exit 1
fi
[[ ! -e "$(lockdown_dropin_path)" ]]

reset_activation_fixture
render_legacy_lockdown_dropin "$(lockdown_dropin_path)"
chmod 644 "$(lockdown_dropin_path)"
cp "$(lockdown_dropin_path)" "$temporary/legacy-preimage"
touch "$temporary/fail-mv-once"
if install_lockdown_ssh_config "$admin_user"; then
  printf 'failed lockdown publication was accepted\n' >&2
  exit 1
fi
cmp "$temporary/legacy-preimage" "$(lockdown_dropin_path)"
[[ -z "$(find "$(dirname "$(lockdown_dropin_path)")" -name '*.preimage.*' -print -quit)" ]]
[[ -z "$(find "$(dirname "$(lockdown_dropin_path)")" -name '*.tmp.*' -print -quit)" ]]

reset_activation_fixture
render_legacy_lockdown_dropin "$(lockdown_dropin_path)"
chmod 644 "$(lockdown_dropin_path)"
cp "$(lockdown_dropin_path)" "$temporary/legacy-preimage"
touch "$temporary/shadow-lockdown-once"
if install_lockdown_ssh_config "$admin_user"; then
  printf 'shadowed lockdown policy was accepted\n' >&2
  exit 1
fi
cmp "$temporary/legacy-preimage" "$(lockdown_dropin_path)"
[[ -z "$(find "$(dirname "$(lockdown_dropin_path)")" -name '*.preimage.*' -print -quit)" ]]

reset_activation_fixture
touch "$temporary/shadow-lockdown-once"
if install_lockdown_ssh_config "$admin_user"; then
  printf 'shadowed lockdown policy without preimage was accepted\n' >&2
  exit 1
fi
[[ ! -e "$(lockdown_dropin_path)" ]]

reset_activation_fixture
render_legacy_lockdown_dropin "$(lockdown_dropin_path)"
chmod 644 "$(lockdown_dropin_path)"
install_lockdown_ssh_config "$admin_user"
grep -Fxq '# Managed by linux-server-bootstrap.' "$(lockdown_dropin_path)"
assert_lockdown_sshd_effective "$admin_user"
install_lockdown_ssh_config "$admin_user"
[[ -z "$(find "$(dirname "$(lockdown_dropin_path)")" -name '*.preimage.*' -print -quit)" ]]

printf 'linux_server_auth_proof_test=passed\n'
