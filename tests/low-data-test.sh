#!/bin/bash
# shellcheck disable=SC2016
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
temporary="$(cd "$temporary" && pwd -P)"
trap 'rm -rf "$temporary"' EXIT
export HOME="$temporary/home"
export XDG_CONFIG_HOME="$HOME/.config"
export PATH="$root/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_TEMPLATE_DIR="$temporary/template"
unset LOW_DATA_ALLOW_NETWORK
unset GIT_ALLOW_PROTOCOL GIT_NO_LAZY_FETCH
mkdir -p "$HOME" "$GIT_TEMPLATE_DIR"

expect_status() {
  local expected="$1" actual=0
  shift
  "$@" >"$temporary/stdout" 2>"$temporary/stderr" || actual=$?
  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL: expected %s, got %s: %s\n' "$expected" "$actual" "$*" >&2
    cat "$temporary/stderr" >&2
    exit 1
  fi
}

expect_status 1 low-data active
expect_status 0 low-data status
grep -Fq "not enrolled" "$temporary/stdout"
[[ ! -e "$XDG_CONFIG_HOME/low-data" ]]
expect_status 0 low-data on
expect_status 0 low-data active
expect_status 1 env LOW_DATA_ALLOW_NETWORK=1 low-data active
expect_status 0 low-data off
expect_status 1 low-data active
expect_status 0 curl --version
expect_status 0 low-data auto
expect_status 0 low-data active

mkdir -p "$HOME/.local/libexec/low-data"
printf '#!/bin/sh\nprintf "%%s\\n" "${TEST_COST:-unavailable}"\nexit "${TEST_COST_EXIT:-0}"\n' \
  >"$HOME/.local/libexec/low-data/network-cost"
chmod +x "$HOME/.local/libexec/low-data/network-cost"
for cost in constrained expensive unavailable unexpected ""; do
  expect_status 0 env TEST_COST="$cost" low-data active
done
expect_status 1 env TEST_COST=unmetered low-data active
expect_status 0 env TEST_COST=unmetered TEST_COST_EXIT=1 low-data active
expect_status 0 env TEST_COST=constrained low-data status
grep -Fq constrained "$temporary/stdout"
expect_status 0 low-data on

[[ ! -e "$root/bin/curl" ]]
expect_status 0 curl -q file:///dev/null

repo="$temporary/repo"
expect_status 0 git init -b main "$repo"
expect_status 0 git -C "$repo" -c user.name=Test -c user.email=test@example.test \
  commit --allow-empty -m fixture
expect_status 0 git -C "$repo" status --porcelain
expect_status 0 git clone "$repo" "$temporary/clone"
expect_status 0 git -C "$repo" fetch "$temporary/clone"
expect_status 0 git -C "$repo" -c 'alias.guard=!test -z "${GIT_ALLOW_PROTOCOL:-}" && test "$GIT_NO_LAZY_FETCH" = 1' guard
expect_status 0 git -C "$repo" -c 'alias.nooverride=!test -z "${LOW_DATA_ALLOW_NETWORK:-}"' nooverride
for url in https://127.0.0.1:1/repo http://127.0.0.1:1/repo ssh://127.0.0.1/repo git://127.0.0.1/repo 'ext::false'; do
  expect_status 128 git -C "$repo" -c protocol.allow=always fetch "$url"
  grep -Eq "transport '.*' not allowed" "$temporary/stderr"
done
expect_status 128 git clone https://127.0.0.1:1/repo "$temporary/blocked-clone"
grep -Fq "transport 'https' not allowed" "$temporary/stderr"
expect_status 0 git -C "$repo" remote add local "$temporary/clone"
expect_status 0 git -C "$repo" fetch local
expect_status 0 git -c "url.$repo.insteadOf=https://fixture.invalid/repo" \
  clone https://fixture.invalid/repo "$temporary/rewritten-clone"
expect_status 0 git init --bare "$temporary/push-target"
mkdir -p "$repo/.git/hooks"
printf '#!/bin/sh\ntest -z "${GIT_ALLOW_PROTOCOL:-}" && test -z "${GIT_NO_LAZY_FETCH:-}"\n' \
  >"$repo/.git/hooks/pre-push"
chmod +x "$repo/.git/hooks/pre-push"
expect_status 0 git -C "$repo" push "$temporary/push-target" main
expect_status 0 git ls-remote "$temporary/push-target"
expect_status 0 env LOW_DATA_ALLOW_NETWORK=1 git -C "$repo" \
  -c 'alias.override=!test -z "${GIT_ALLOW_PROTOCOL:-}" && test -z "${GIT_NO_LAZY_FETCH:-}"' override
# A detector alone is still enrollment in automatic mode.
rm "$XDG_CONFIG_HOME/low-data/mode"
expect_status 0 env TEST_COST=constrained low-data active
expect_status 1 env TEST_COST=unmetered low-data active
rm "$HOME/.local/libexec/low-data/network-cost"
expect_status 1 low-data active
ln -s "$temporary/absent-detector" "$HOME/.local/libexec/low-data/network-cost"
expect_status 0 low-data active
rm "$HOME/.local/libexec/low-data/network-cost"
ln -s "$temporary/absent-mode" "$XDG_CONFIG_HOME/low-data/mode"
expect_status 0 low-data active
rm "$XDG_CONFIG_HOME/low-data/mode"
printf 'invalid\n' > "$XDG_CONFIG_HOME/low-data/mode"
expect_status 0 low-data active
printf 'off\nextra\n' > "$XDG_CONFIG_HOME/low-data/mode"
expect_status 0 low-data active
printf 'off\n' > "$XDG_CONFIG_HOME/low-data/mode"
chmod 000 "$XDG_CONFIG_HOME/low-data/mode"
expect_status 0 low-data active
chmod 600 "$XDG_CONFIG_HOME/low-data/mode"
rm "$XDG_CONFIG_HOME/low-data/mode"
mkdir "$XDG_CONFIG_HOME/low-data/mode"
expect_status 0 low-data active
rmdir "$XDG_CONFIG_HOME/low-data/mode"

# A source symlink and marked copied wrappers must never become the backend.
mkdir -p "$temporary/entry" "$temporary/copied" "$temporary/backend"
ln -s "$root/bin/git" "$temporary/entry/git"
cp "$root/bin/git" "$temporary/copied/git"
cat > "$temporary/backend/git" <<'STUB'
#!/bin/bash
printf '%s\n' "$0" "$@"
STUB
chmod +x "$temporary/backend/git"
expect_status 0 env PATH="$temporary/entry:$temporary/copied:$temporary/backend:/usr/bin:/bin" "$temporary/entry/git" --version
grep -Fxq "$temporary/backend/git" "$temporary/stdout"
expect_status 0 env PATH="$temporary/entry:$temporary/copied:$temporary/backend:/usr/bin:/bin" "$root/bin/git" -C 'directory with spaces' status
grep -Fxq 'directory with spaces' "$temporary/stdout"
mkdir "$temporary/broken"
ln -s git "$temporary/broken/git"
expect_status 0 env PATH="$temporary/broken:$temporary/backend:/usr/bin:/bin" "$root/bin/git" --version
grep -Fxq "$temporary/backend/git" "$temporary/stdout"
mkdir "$temporary/tools"
ln -s /usr/bin/dirname "$temporary/tools/dirname"
ln -s /usr/bin/readlink "$temporary/tools/readlink"
expect_status 126 env PATH="$temporary/entry:$temporary/copied:$temporary/tools" "$root/bin/git" --version
grep -Fq "no nonrecursive native backend" "$temporary/stderr"

# A copied entrypoint without its optional runtime passes only when unenrolled.
mkdir "$temporary/incomplete"
cp "$root/bin/git" "$temporary/incomplete/git"
expect_status 0 env PATH="$temporary/backend:/usr/bin:/bin" "$temporary/incomplete/git" --version
low-data on >/dev/null
expect_status 126 env PATH="$temporary/backend:/usr/bin:/bin" "$temporary/incomplete/git" --version
grep -Fq 'enrolled low-data runtime is unavailable' "$temporary/stderr"
printf 'PASS: enrollment, native policy, portable backend, guarded downloads and normal local Git operations\n'
