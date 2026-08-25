#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
helper="$repo_root/bin/codex-standalone-install"
update_functions="$repo_root/functions/system/update.zsh"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

make_fake_tools() {
  local directory="$1"
  mkdir -p "$directory"

  cat >"$directory/uname" <<'EOF'
#!/usr/bin/env bash
printf 'Darwin\n'
EOF

  cat >"$directory/lockf" <<'EOF'
#!/usr/bin/env bash
exit "${TEST_LOCK_STATUS:-0}"
EOF

  cat >"$directory/curl" <<'EOF'
#!/usr/bin/env bash
output=""
url=""
while (($#)); do
  case "$1" in
    -o)
      output="$2"
      shift 2
      ;;
    -*)
      shift
      ;;
    *)
      url="$1"
      shift
      ;;
  esac
done
printf '%s\n' "$url" >"$TEST_CASE_DIR/url"
touch "$TEST_CASE_DIR/curl-ran"
cat >"$output" <<'INSTALLER'
#!/bin/sh
RELEASES_BASE_URL="https://releases.openai.com/codex"
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
STANDALONE_ROOT="$CODEX_HOME_DIR/packages/standalone"
[ "${CODEX_NON_INTERACTIVE:-}" = "1" ] || exit 81
printf '%s\n' "$*" >"$TEST_CASE_DIR/installer-args"
touch "$TEST_CASE_DIR/installer-ran"
INSTALLER
EOF

  chmod +x "$directory/uname" "$directory/lockf" "$directory/curl"
}

case_dir="$temporary/success"
tools="$case_dir/tools"
mkdir -p "$case_dir/home/.codex/packages/standalone"
touch "$case_dir/home/.codex/packages/standalone/install.lock"
make_fake_tools "$tools"
TEST_CASE_DIR="$case_dir" \
  HOME="$case_dir/home" \
  PATH="$tools:/usr/bin:/bin" \
  "$helper" --release 1.2.3
[[ -e "$case_dir/installer-ran" ]]
[[ "$(<"$case_dir/url")" == "https://chatgpt.com/codex/install.sh" ]]
[[ "$(<"$case_dir/installer-args")" == "--release 1.2.3" ]]

case_dir="$temporary/locked-file"
tools="$case_dir/tools"
mkdir -p "$case_dir/home/.codex/packages/standalone"
touch "$case_dir/home/.codex/packages/standalone/install.lock"
make_fake_tools "$tools"
set +e
TEST_CASE_DIR="$case_dir" \
  TEST_LOCK_STATUS=75 \
  HOME="$case_dir/home" \
  PATH="$tools:/usr/bin:/bin" \
  "$helper" >"$case_dir/stdout" 2>"$case_dir/stderr"
status=$?
set -e
[[ $status -eq 75 ]]
[[ ! -e "$case_dir/curl-ran" ]]
grep -F "installer lock is active" "$case_dir/stderr" >/dev/null

case_dir="$temporary/locked-directory"
tools="$case_dir/tools"
mkdir -p "$case_dir/home/.codex/packages/standalone/install.lock.d"
make_fake_tools "$tools"
set +e
TEST_CASE_DIR="$case_dir" \
  HOME="$case_dir/home" \
  PATH="$tools:/usr/bin:/bin" \
  "$helper" >"$case_dir/stdout" 2>"$case_dir/stderr"
status=$?
set -e
[[ $status -eq 75 ]]
[[ ! -e "$case_dir/curl-ran" ]]
grep -F "installer lock is active" "$case_dir/stderr" >/dev/null

grep -F "codex-standalone-install" "$update_functions" >/dev/null
if grep -E 'brew upgrade --cask codex|npm-global-update-all-nodenv|@openai/codex' \
  "$update_functions" >/dev/null; then
  printf 'legacy Codex updater remains in update.zsh\n' >&2
  exit 1
fi

printf 'codex standalone installer tests passed\n'
