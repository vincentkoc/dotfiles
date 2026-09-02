#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$repo_root/install.sh"

fixture_root="$(mktemp -d)"
fixture="$fixture_root/fixture with spaces"
mkdir -p "$fixture"
trap 'rm -rf "$fixture_root"' EXIT
export HOME="$fixture/home"
dotfiles="$HOME/GIT/_Perso/dotfiles"
mkdir -p "$dotfiles/.ssh" "$HOME/.ssh"
printf '[user]\n\temail = %s\n[gpg]\n\tformat = ssh\n' \
  "$SSH_SIGNING_PRINCIPAL" > "$dotfiles/.gitconfig"
printf '[gpg "ssh"]\n\tallowedSignersFile = ~/GIT/_Perso/dotfiles/.ssh/allowed_signers\n' \
  >> "$dotfiles/.gitconfig"
printf '%s test-fixture\n' "$SSH_SIGNING_PUBLIC_KEY" \
  > "$HOME/.ssh/git_signing_vincentkoc_ieee.pub"

run_check() {
  local output status
  set +e
  output="$(ensure_ssh_signing_trust_file 2>&1)"
  status=$?
  set -e
  printf '%s\n' "$status"
  printf '%s\n' "$output"
}

expected="$(canonical_ssh_allowed_signer)"
unrelated='other@example.com namespaces="git" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB'

printf '%s\n%s\n' "$unrelated" "$expected" > "$dotfiles/.ssh/allowed_signers"
result="$(run_check)"
[[ "${result%%$'\n'*}" == 0 ]]
grep -Fq 'authorizes Git and fleet cleanup bundles' <<<"$result"

git config --file "$dotfiles/.gitconfig" \
  gpg.ssh.allowedSignersFile "$HOME/custom trust/allowed_signers"
result="$(run_check)"
[[ "${result%%$'\n'*}" == 1 ]]
grep -Fq 'Git allowed signers config drift' <<<"$result"
git config --file "$dotfiles/.gitconfig" \
  gpg.ssh.allowedSignersFile '~/GIT/_Perso/dotfiles/.ssh/allowed_signers'

printf '%s\n%s namespaces="git" %s\n' \
  "$unrelated" \
  "$SSH_SIGNING_PRINCIPAL" \
  "$SSH_SIGNING_PUBLIC_KEY" \
  > "$dotfiles/.ssh/allowed_signers"
result="$(run_check)"
[[ "${result%%$'\n'*}" == 1 ]]
grep -Fq 'SSH signing trust file drift' <<<"$result"
grep -Fq "$expected" <<<"$result"
if grep -Fq '[OK]' <<<"$result"; then
  echo 'stale signer trust was incorrectly reported ready' >&2
  exit 1
fi

printf '%s\n%s\n%s\n' "$unrelated" "$expected" "$expected" \
  > "$dotfiles/.ssh/allowed_signers"
result="$(run_check)"
[[ "${result%%$'\n'*}" == 1 ]]
grep -Fq 'SSH signing trust file drift' <<<"$result"

rm "$dotfiles/.ssh/allowed_signers"
result="$(run_check)"
[[ "${result%%$'\n'*}" == 1 ]]
grep -Fq 'Missing SSH signing trust file' <<<"$result"
grep -Fq "$expected" <<<"$result"

printf '%s wrong-key\n' \
  'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB' \
  > "$HOME/.ssh/git_signing_vincentkoc_ieee.pub"
result="$(run_check)"
[[ "${result%%$'\n'*}" == 1 ]]
grep -Fq "expected $SSH_SIGNING_FINGERPRINT" <<<"$result"

signing_fixture="$fixture/signing"
fixture_key="$signing_fixture/key"
fixture_other_key="$signing_fixture/other-key"
fixture_allowed_signers="$signing_fixture/allowed_signers"
fixture_repo="$signing_fixture/repo"
fixture_payload="$signing_fixture/fleet-cleanup-bundle"
mkdir -p "$signing_fixture"
ssh-keygen -q -t ed25519 -N '' -C signing-fixture -f "$fixture_key"
ssh-keygen -q -t ed25519 -N '' -C other-signing-fixture -f "$fixture_other_key"
fixture_public_key="$(awk 'NF >= 2 { print $1 " " $2; exit }' "$fixture_key.pub")"
printf '%s namespaces="%s" %s\n' \
  "$SSH_SIGNING_PRINCIPAL" \
  "$SSH_SIGNING_NAMESPACES" \
  "$fixture_public_key" \
  > "$fixture_allowed_signers"

git -C "$signing_fixture" init -q repo
git -C "$fixture_repo" config user.name 'Signing Fixture'
git -C "$fixture_repo" config user.email "$SSH_SIGNING_PRINCIPAL"
git -C "$fixture_repo" config user.signingkey "$fixture_key"
git -C "$fixture_repo" config gpg.format ssh
git -C "$fixture_repo" config gpg.ssh.allowedSignersFile "$fixture_allowed_signers"
git -C "$fixture_repo" config commit.gpgsign true
git -C "$fixture_repo" commit --allow-empty -qm 'test: verify git namespace'
git -C "$fixture_repo" verify-commit HEAD >/dev/null

printf 'fleet-cleanup-bundle-v1\n' > "$fixture_payload"
ssh-keygen -Y sign \
  -f "$fixture_key" \
  -n fleet-cleanup-bundle-v1 \
  "$fixture_payload" >/dev/null 2>&1
ssh-keygen -Y verify \
  -f "$fixture_allowed_signers" \
  -I "$SSH_SIGNING_PRINCIPAL" \
  -n fleet-cleanup-bundle-v1 \
  -s "$fixture_payload.sig" \
  < "$fixture_payload" >/dev/null

printf '%s namespaces="git" %s\n' \
  "$SSH_SIGNING_PRINCIPAL" \
  "$fixture_public_key" \
  > "$fixture_allowed_signers"
if ssh-keygen -Y verify \
  -f "$fixture_allowed_signers" \
  -I "$SSH_SIGNING_PRINCIPAL" \
  -n fleet-cleanup-bundle-v1 \
  -s "$fixture_payload.sig" \
  < "$fixture_payload" >/dev/null 2>&1; then
  echo 'git-only namespace incorrectly verified a fleet cleanup bundle' >&2
  exit 1
fi

mode_home="$fixture/mode-home"
mode_allowed_signers="$mode_home/GIT/_Perso/dotfiles/.ssh/allowed_signers"
mkdir -p "$(dirname "$mode_allowed_signers")" "$mode_home/.ssh"
cp "$fixture_key" "$mode_home/.ssh/git_signing_vincentkoc_ieee"
cp "$fixture_key.pub" "$mode_home/.ssh/git_signing_vincentkoc_ieee.pub"
fixture_fingerprint="$(ssh-keygen -lf "$fixture_key.pub" | awk '{print $2}')"
mode_expected="$SSH_SIGNING_PRINCIPAL namespaces=\"$SSH_SIGNING_NAMESPACES\" $fixture_public_key"
printf '%s\n' "$mode_expected" > "$mode_allowed_signers"
HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
  git config --global gpg.program /stale/gpg
HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
  git config --global --add gpg.program /second-stale/gpg
HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
  git config --global gpg.ssh.program /stale/ssh-keygen
HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
  git config --global test.preserved unrelated-value
(
  export HOME="$mode_home"
  export GIT_CONFIG_GLOBAL="$mode_home/.gitconfig"
  source "$repo_root/bin/git-signing-mode"
  ssh_signing_public_key="$fixture_public_key"
  ssh_signing_fingerprint="$fixture_fingerprint"
  main ssh
) >/dev/null
[[ "$(
  HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
    git config --global --get-all user.signingkey
)" == '~/.ssh/git_signing_vincentkoc_ieee' ]]
[[ "$(
  HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
    git config --global --get-all gpg.ssh.allowedSignersFile
)" == '~/GIT/_Perso/dotfiles/.ssh/allowed_signers' ]]
[[ "$(
  HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
    git config --global --get-all gpg.format
)" == ssh ]]
[[ "$(
  HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
    git config --global --get-all gpg.ssh.program
)" == /usr/bin/ssh-keygen ]]
[[ "$(
  HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
    git config --global --get-all commit.gpgsign
)" == true ]]
[[ "$(
  HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
    git config --global --get-all tag.gpgsign
)" == true ]]
[[ "$(
  HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
    git config --global --get test.preserved
)" == unrelated-value ]]
if HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
  git config --global --get-all gpg.program >/dev/null 2>&1; then
  echo 'git-signing-mode left a stale gpg.program in SSH mode' >&2
  exit 1
fi
if grep -Fq "$mode_home" "$mode_home/.gitconfig"; then
  echo 'git-signing-mode wrote a machine-specific home path' >&2
  exit 1
fi

(
  export HOME="$mode_home"
  export GIT_CONFIG_GLOBAL="$mode_home/.gitconfig"
  source "$repo_root/bin/git-signing-mode"
  ssh_signing_public_key="$fixture_public_key"
  ssh_signing_fingerprint="$fixture_fingerprint"
  main ssh
) >"$fixture/mode-absent.out"
grep -Fq 'git signing mode: ssh' "$fixture/mode-absent.out"
if HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
  git config --global --get-all gpg.program >/dev/null 2>&1; then
  echo 'git-signing-mode recreated gpg.program from the absent path' >&2
  exit 1
fi

unset_failure_home="$fixture/unset-failure-home"
unset_failure_allowed_signers="$unset_failure_home/GIT/_Perso/dotfiles/.ssh/allowed_signers"
failing_git_dir="$fixture/failing-git"
real_git="$(command -v git)"
mkdir -p \
  "$(dirname "$unset_failure_allowed_signers")" \
  "$unset_failure_home/.ssh" \
  "$failing_git_dir"
cp "$fixture_key" "$unset_failure_home/.ssh/git_signing_vincentkoc_ieee"
cp "$fixture_key.pub" "$unset_failure_home/.ssh/git_signing_vincentkoc_ieee.pub"
printf '%s\n' "$mode_expected" > "$unset_failure_allowed_signers"
HOME="$unset_failure_home" GIT_CONFIG_GLOBAL="$unset_failure_home/.gitconfig" \
  git config --global gpg.program /stale/gpg
HOME="$unset_failure_home" GIT_CONFIG_GLOBAL="$unset_failure_home/.gitconfig" \
  git config --global test.preserved unrelated-value
cat > "$failing_git_dir/git" <<EOF
#!/usr/bin/env bash
if [[ "\${1:-}" == config &&
  "\${2:-}" == --global &&
  "\${3:-}" == --unset-all &&
  "\${4:-}" == gpg.program ]]; then
  echo 'forced gpg.program unset failure' >&2
  exit 73
fi
exec "$real_git" "\$@"
EOF
chmod +x "$failing_git_dir/git"
unset_failure_before="$(
  shasum -a 256 "$unset_failure_home/.gitconfig" | awk '{print $1}'
)"
set +e
(
  export HOME="$unset_failure_home"
  export GIT_CONFIG_GLOBAL="$unset_failure_home/.gitconfig"
  export PATH="$failing_git_dir:$PATH"
  source "$repo_root/bin/git-signing-mode"
  ssh_signing_public_key="$fixture_public_key"
  ssh_signing_fingerprint="$fixture_fingerprint"
  main ssh
) >"$fixture/mode-unset-failure.out" 2>&1
unset_failure_status=$?
set -e
[[ "$unset_failure_status" == 73 ]]
unset_failure_after="$(
  shasum -a 256 "$unset_failure_home/.gitconfig" | awk '{print $1}'
)"
[[ "$unset_failure_before" == "$unset_failure_after" ]]
[[ "$(
  HOME="$unset_failure_home" GIT_CONFIG_GLOBAL="$unset_failure_home/.gitconfig" \
    git config --global --get-all gpg.program
)" == /stale/gpg ]]
[[ "$(
  HOME="$unset_failure_home" GIT_CONFIG_GLOBAL="$unset_failure_home/.gitconfig" \
    git config --global --get test.preserved
)" == unrelated-value ]]
grep -Fq 'forced gpg.program unset failure' "$fixture/mode-unset-failure.out"
grep -Fq 'failed to remove gpg.program' "$fixture/mode-unset-failure.out"
if grep -Fq 'git signing mode: ssh' "$fixture/mode-unset-failure.out"; then
  echo 'git-signing-mode reported success after gpg.program unset failure' >&2
  exit 1
fi

mode_config_before="$(shasum -a 256 "$mode_home/.gitconfig" | awk '{print $1}')"
if (
  export HOME="$mode_home"
  export GIT_CONFIG_GLOBAL="$mode_home/.gitconfig"
  source "$repo_root/bin/git-signing-mode"
  gpg_program="$fixture/missing-gpg"
  main gpg
) >"$fixture/mode-gpg-missing.out" 2>&1; then
  echo 'git-signing-mode accepted a missing gpg program' >&2
  exit 1
fi
mode_config_after="$(shasum -a 256 "$mode_home/.gitconfig" | awk '{print $1}')"
[[ "$mode_config_before" == "$mode_config_after" ]]
grep -Fq "missing gpg program at $fixture/missing-gpg" "$fixture/mode-gpg-missing.out"
[[ "$(
  HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
    git config --global --get test.preserved
)" == unrelated-value ]]

printf '%s namespaces="git" %s\n' \
  "$SSH_SIGNING_PRINCIPAL" \
  "$fixture_public_key" \
  > "$mode_allowed_signers"
mode_config_before="$(shasum -a 256 "$mode_home/.gitconfig" | awk '{print $1}')"
if (
  export HOME="$mode_home"
  export GIT_CONFIG_GLOBAL="$mode_home/.gitconfig"
  source "$repo_root/bin/git-signing-mode"
  ssh_signing_public_key="$fixture_public_key"
  ssh_signing_fingerprint="$fixture_fingerprint"
  main ssh
) >"$fixture/mode.out" 2>&1; then
  echo 'git-signing-mode incorrectly accepted git-only trust' >&2
  exit 1
fi
mode_config_after="$(shasum -a 256 "$mode_home/.gitconfig" | awk '{print $1}')"
[[ "$mode_config_before" == "$mode_config_after" ]]
grep -Fq 'allowed signers drift' "$fixture/mode.out"
grep -Fq "$mode_expected" "$fixture/mode.out"

cp "$fixture_other_key" "$mode_home/.ssh/git_signing_vincentkoc_ieee"
printf '%s\n' "$mode_expected" > "$mode_allowed_signers"
mode_config_before="$(shasum -a 256 "$mode_home/.gitconfig" | awk '{print $1}')"
if (
  export HOME="$mode_home"
  export GIT_CONFIG_GLOBAL="$mode_home/.gitconfig"
  source "$repo_root/bin/git-signing-mode"
  ssh_signing_public_key="$fixture_public_key"
  ssh_signing_fingerprint="$fixture_fingerprint"
  main ssh
) >"$fixture/mode-private-mismatch.out" 2>&1; then
  echo 'git-signing-mode accepted a private/public key mismatch' >&2
  exit 1
fi
mode_config_after="$(shasum -a 256 "$mode_home/.gitconfig" | awk '{print $1}')"
[[ "$mode_config_before" == "$mode_config_after" ]]
grep -Fq 'signing key drift' "$fixture/mode-private-mismatch.out"
cp "$fixture_key" "$mode_home/.ssh/git_signing_vincentkoc_ieee"

mode_missing="$mode_home/configured missing/allowed_signers"
HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
  git config --global gpg.ssh.allowedSignersFile "$mode_missing"
if (
  export HOME="$mode_home"
  export GIT_CONFIG_GLOBAL="$mode_home/.gitconfig"
  source "$repo_root/bin/git-signing-mode"
  ssh_signing_public_key="$fixture_public_key"
  ssh_signing_fingerprint="$fixture_fingerprint"
  main ssh
) >"$fixture/mode-missing.out" 2>&1; then
  echo 'git-signing-mode silently accepted a noncanonical configured path' >&2
  exit 1
fi
grep -Fq 'allowed signers config drift' "$fixture/mode-missing.out"

echo git_signing_trust_test=passed
