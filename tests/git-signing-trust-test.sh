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
    git config --global --get user.signingkey
)" == '~/.ssh/git_signing_vincentkoc_ieee' ]]
[[ "$(
  HOME="$mode_home" GIT_CONFIG_GLOBAL="$mode_home/.gitconfig" \
    git config --global --get gpg.ssh.allowedSignersFile
)" == '~/GIT/_Perso/dotfiles/.ssh/allowed_signers' ]]
if grep -Fq "$mode_home" "$mode_home/.gitconfig"; then
  echo 'git-signing-mode wrote a machine-specific home path' >&2
  exit 1
fi

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
