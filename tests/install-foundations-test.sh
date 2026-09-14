#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

# shellcheck disable=SC1091
source "$root/install.sh"

fake_bin="$temporary/bin"
mkdir -p "$fake_bin"
cat >"$fake_bin/uname" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "${TEST_UNAME:-Darwin}"
EOF
chmod +x "$fake_bin/uname"

canonical_home="$temporary/canonical-home"
mkdir -p "$canonical_home/GIT/_Perso/dotfiles" "$canonical_home/.dotfiles"
canonical_result="$(
    HOME="$canonical_home" TEST_UNAME=Darwin PATH="$fake_bin:/usr/bin:/bin" dotfiles_dir
)"
[[ "$canonical_result" == "$canonical_home/GIT/_Perso/dotfiles" ]]

explicit_home="$temporary/explicit-home"
mkdir -p "$explicit_home/.dotfiles"
explicit_result="$(
    HOME="$explicit_home" TEST_UNAME=Darwin PATH="$fake_bin:/usr/bin:/bin" dotfiles_dir
)"
[[ "$explicit_result" == "$explicit_home/.dotfiles" ]]

darwin_home="$temporary/darwin-home"
mkdir -p "$darwin_home"
darwin_result="$(
    HOME="$darwin_home" TEST_UNAME=Darwin PATH="$fake_bin:/usr/bin:/bin" dotfiles_dir
)"
[[ "$darwin_result" == \
    "$darwin_home/Library/Mobile Documents/com~apple~CloudDocs/dotfiles" ]]

linux_home="$temporary/linux-home"
mkdir -p "$linux_home"
linux_result="$(
    HOME="$linux_home" TEST_UNAME=Linux PATH="$fake_bin:/usr/bin:/bin" dotfiles_dir
)"
[[ "$linux_result" == "$linux_home/.dotfiles" ]]

cat >"$fake_bin/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$FZF_TEST_GIT_LOG"
destination="${@: -1}"
mkdir -p "$destination/.git"
cat >"$destination/install" <<'INSTALL'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$FZF_TEST_INSTALL_LOG"
if [[ "${FZF_TEST_SKIP_BINARY:-0}" != 1 ]]; then
    mkdir -p "$HOME/.fzf/bin"
    printf '#!/usr/bin/env bash\nexit 0\n' >"$HOME/.fzf/bin/fzf"
    chmod +x "$HOME/.fzf/bin/fzf"
fi
INSTALL
chmod +x "$destination/install"
EOF
chmod +x "$fake_bin/git"

new_home="$temporary/new-fzf-home"
mkdir -p "$new_home"
: >"$temporary/new-git.log"
: >"$temporary/new-install.log"
new_output="$(
    HOME="$new_home" \
    FZF_TEST_GIT_LOG="$temporary/new-git.log" \
    FZF_TEST_INSTALL_LOG="$temporary/new-install.log" \
    PATH="$fake_bin:/usr/bin:/bin" \
    install_fzf
)"
grep -Fxq -- 'clone --depth 1 https://github.com/junegunn/fzf.git '"$new_home"'/.fzf' \
    "$temporary/new-git.log"
grep -Fxq -- '--bin' "$temporary/new-install.log"
[[ -x "$new_home/.fzf/bin/fzf" ]]
grep -Fq '[OK]' <<<"$new_output"

existing_home="$temporary/existing-fzf-home"
mkdir -p "$existing_home/.fzf/.git"
cp "$new_home/.fzf/install" "$existing_home/.fzf/install"
: >"$temporary/existing-git.log"
: >"$temporary/existing-install.log"
HOME="$existing_home" \
FZF_TEST_GIT_LOG="$temporary/existing-git.log" \
FZF_TEST_INSTALL_LOG="$temporary/existing-install.log" \
PATH="$fake_bin:/usr/bin:/bin" \
install_fzf >/dev/null
[[ ! -s "$temporary/existing-git.log" ]]
grep -Fxq -- '--bin' "$temporary/existing-install.log"
[[ -x "$existing_home/.fzf/bin/fzf" ]]

failed_home="$temporary/failed-fzf-home"
mkdir -p "$failed_home"
: >"$temporary/failed-git.log"
: >"$temporary/failed-install.log"
set +e
failed_output="$(
    HOME="$failed_home" \
    FZF_TEST_GIT_LOG="$temporary/failed-git.log" \
    FZF_TEST_INSTALL_LOG="$temporary/failed-install.log" \
    FZF_TEST_SKIP_BINARY=1 \
    PATH="$fake_bin:/usr/bin:/bin" \
    install_fzf 2>&1
)"
failed_status=$?
set -e
[[ "$failed_status" -ne 0 ]]
grep -Fq 'fzf installer did not create ~/.fzf/bin/fzf' <<<"$failed_output"
if grep -Fq '[OK] fzf installed' <<<"$failed_output"; then
    printf 'fzf install failure was reported as success\n' >&2
    exit 1
fi

unusable_home="$temporary/unusable-fzf-home"
mkdir -p "$unusable_home/.fzf"
set +e
unusable_output="$(
    HOME="$unusable_home" \
    PATH="$fake_bin:/usr/bin:/bin" \
    install_fzf 2>&1
)"
unusable_status=$?
set -e
[[ "$unusable_status" -ne 0 ]]
grep -Fq '~/.fzf already exists but is not a usable fzf installation' \
    <<<"$unusable_output"

# A binary installed with --bin must be reachable after normal shell setup.
HOME="$new_home" PATH="$fake_bin:/usr/bin:/bin" /bin/bash --noprofile --norc -c '
    OSTYPE=linux-gnu
    source "$1/.exports"
    case ":$PATH:" in *":$HOME/.fzf/bin:"*) ;; *) exit 1 ;; esac
    source "$1/.exports"
    count=$(printf "%s" "$PATH" | tr ":" "\n" | grep -Fxc "$HOME/.fzf/bin")
    [[ "$count" == 1 ]]
' _ "$root"

# Existing binaries need no installer, and failures must retain their status.
HOME="$existing_home" PATH="$fake_bin:/usr/bin:/bin" install_fzf >/dev/null
[[ "$(wc -l <"$temporary/existing-install.log" | tr -d ' ')" == 1 ]]
failed_installer_home="$temporary/failed-installer-home"
mkdir -p "$failed_installer_home/.fzf/.git"
printf '#!/usr/bin/env bash\nexit 19\n' >"$failed_installer_home/.fzf/install"
chmod +x "$failed_installer_home/.fzf/install"
failed_status=0
HOME="$failed_installer_home" PATH="$fake_bin:/usr/bin:/bin" install_fzf >/dev/null || failed_status=$?
[[ "$failed_status" == 19 ]]

printf 'install_foundations_test=passed\n'
