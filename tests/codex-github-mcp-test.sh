#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
unset GITHUB_PERSONAL_ACCESS_TOKEN GITHUB_PAT_TOKEN

wrapper_dir="$temporary/wrapper space"
backend_dir="$temporary/backend"
test_home="$temporary/home"
mkdir -p "$wrapper_dir" "$backend_dir" "$test_home"
ln -s "$wrapper_dir" "$test_home/bin"
cp "$repo_root/bin/codex" "$repo_root/bin/codex-github-mcp" "$wrapper_dir/"

cat >"$backend_dir/uname" <<'EOF'
#!/usr/bin/env bash
printf 'TestOS\n'
EOF

cat >"$backend_dir/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 2 && "$1" == auth && "$2" == token ]]
printf 'lookup\n' >>"$TEST_LOOKUP_LOG"
if IFS= read -r input; then
  printf 'authentication consumed stdin\n' >&2
  exit 64
fi
printf 'synthetic-stderr-secret\n' >&2
case "$TEST_AUTH_MODE" in
  success) printf 'synthetic-native-secret\n' ;;
  failed)
    printf 'synthetic-native-secret\n'
    exit 1
    ;;
  empty) ;;
  *) exit 65 ;;
esac
EOF

cat >"$wrapper_dir/gh" <<'EOF'
#!/usr/bin/env bash
printf 'forbidden wrapper invoked\n' >>"$TEST_FORBIDDEN_LOG"
exit 66
EOF
cp "$wrapper_dir/gh" "$wrapper_dir/ghx"
cp "$wrapper_dir/gh" "$backend_dir/ghx"

cat >"$backend_dir/codex" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "${GITHUB_PERSONAL_ACCESS_TOKEN:-}" == "$TEST_EXPECTED_MODERN" ]]
[[ "${GITHUB_PAT_TOKEN:-}" == "$TEST_EXPECTED_LEGACY" ]]
if [[ "$TEST_LAUNCHER" == codex-github-mcp ]]; then
  [[ "$1" == stdio ]]
  shift
fi
[[ $# -eq 2 && "$1" == run && "$2" == "two words" ]]
IFS= read -r input
[[ "$input" == "request on stdin" ]]
printf 'backend:ok\n'
EOF
cp "$backend_dir/codex" "$backend_dir/github-mcp-server"
chmod +x "$wrapper_dir/"* "$backend_dir/"*

# The helper adds system paths. Restrict these two lookups to fixtures so tests
# cannot reach an installed server, native gh credentials, or a real keychain.
command() {
  if [[ $# -eq 2 && "$1" == -v && ( "$2" == gh || "$2" == github-mcp-server ) ]]; then
    local entry
    local -a entries
    IFS=: read -r -a entries <<<"$PATH"
    for entry in "${entries[@]}"; do
      case "$entry" in
        "$TEST_ROOT"/*)
          if [[ "$2" == gh && "$TEST_NO_GH" == 1 && "$entry" -ef "$TEST_BACKEND" ]]; then
            continue
          fi
          if [[ "$2" == github-mcp-server && "$TEST_NO_SERVER" == 1 ]]; then
            continue
          fi
          if [[ -x "$entry/$2" ]]; then
            printf '%s\n' "$entry/$2"
            return 0
          fi
          ;;
      esac
    done
    return 1
  fi
  builtin command "$@"
}
export -f command

run_case() {
  local name="$1" modern="$2" legacy="$3" lookups="$4" auth_mode="$5"
  shift 5
  local expected_status=0 actual_status=0
  if [[ "$launcher" == codex-github-mcp ]]; then
    if [[ "${TEST_NO_SERVER:-0}" == 1 ]]; then
      expected_status=127
    elif [[ -z "$modern" ]]; then
      expected_status=1
    fi
  fi
  : >"$temporary/lookups"
  : >"$temporary/forbidden"
  printf 'case: %s/%s\n' "$launcher" "$name"
  printf 'request on stdin\n' |
    env -u GITHUB_PERSONAL_ACCESS_TOKEN -u GITHUB_PAT_TOKEN -u CODEX_HOME \
      -u BASH_ENV -u ENV HOME="$test_home" PATH="$test_home/bin:$backend_dir:/usr/bin:/bin" \
      TEST_ROOT="$temporary" TEST_BACKEND="$backend_dir" \
      TEST_LAUNCHER="$launcher" TEST_AUTH_MODE="$auth_mode" \
      TEST_NO_GH="${TEST_NO_GH:-0}" TEST_NO_SERVER="${TEST_NO_SERVER:-0}" \
      TEST_EXPECTED_MODERN="$modern" TEST_EXPECTED_LEGACY="$legacy" \
      TEST_LOOKUP_LOG="$temporary/lookups" TEST_FORBIDDEN_LOG="$temporary/forbidden" \
      "$@" "$test_home/bin/$launcher" run "two words" \
      >"$temporary/stdout" 2>"$temporary/stderr" || actual_status=$?
  [[ "$actual_status" -eq "$expected_status" ]]
  [[ "$(wc -l <"$temporary/lookups")" -eq "$lookups" ]]
  [[ ! -s "$temporary/forbidden" ]]
  if grep -Eq 'synthetic-(modern|legacy|native|stderr)-secret' "$temporary/stdout" "$temporary/stderr"; then
    printf 'credential leaked to launcher output\n' >&2
    exit 1
  fi
  if [[ "$expected_status" -eq 0 ]]; then
    [[ "$(<"$temporary/stdout")" == backend:ok ]]
    [[ ! -s "$temporary/stderr" ]]
  else
    [[ ! -s "$temporary/stdout" ]]
    if [[ "$expected_status" -eq 127 ]]; then
      grep -Fx 'codex-github-mcp: install github-mcp-server with Homebrew' "$temporary/stderr" >/dev/null
    else
      grep -Fx 'codex-github-mcp: set GITHUB_PERSONAL_ACCESS_TOKEN or GITHUB_PAT_TOKEN, or authenticate the native GitHub CLI' "$temporary/stderr" >/dev/null
    fi
  fi
}

for launcher in codex codex-github-mcp; do
  run_case modern synthetic-modern-secret synthetic-modern-secret 0 failed \
    GITHUB_PERSONAL_ACCESS_TOKEN=synthetic-modern-secret
  run_case legacy synthetic-legacy-secret synthetic-legacy-secret 0 failed \
    GITHUB_PAT_TOKEN=synthetic-legacy-secret
  run_case conflicting synthetic-modern-secret synthetic-legacy-secret 0 failed \
    GITHUB_PERSONAL_ACCESS_TOKEN=synthetic-modern-secret GITHUB_PAT_TOKEN=synthetic-legacy-secret
  run_case empty-modern synthetic-legacy-secret synthetic-legacy-secret 0 failed \
    GITHUB_PERSONAL_ACCESS_TOKEN= GITHUB_PAT_TOKEN=synthetic-legacy-secret
  run_case empty-legacy synthetic-modern-secret synthetic-modern-secret 0 failed \
    GITHUB_PERSONAL_ACCESS_TOKEN=synthetic-modern-secret GITHUB_PAT_TOKEN=
  run_case empty-both synthetic-native-secret synthetic-native-secret 1 success \
    GITHUB_PERSONAL_ACCESS_TOKEN= GITHUB_PAT_TOKEN=
  run_case native synthetic-native-secret synthetic-native-secret 1 success
  TEST_NO_GH=1 run_case no-gh "" "" 0 success
  run_case failed-lookup "" "" 1 failed
  run_case empty-lookup "" "" 1 empty
  TEST_NO_SERVER=1 run_case missing-mcp synthetic-modern-secret synthetic-modern-secret 0 failed \
    GITHUB_PERSONAL_ACCESS_TOKEN=synthetic-modern-secret
done

printf 'codex GitHub MCP authentication tests passed\n'
