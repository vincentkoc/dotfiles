#!/bin/bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
export HOME="$temporary/home"
export XDG_CONFIG_HOME="$HOME/.config"
export PATH="$root/bin:$temporary/bin:/opt/homebrew/bin:/usr/bin:/bin"
export TEST_CALL="$temporary/call"
export TEST_STDIN="$temporary/stdin"
export TEST_GH_PATH="$temporary/gh-path"
unset LOW_DATA_ALLOW_NETWORK
mkdir -p "$HOME" "$temporary/bin"
ghx-bootstrap >/dev/null
cat >"$temporary/bin/gh" <<'STUB'
#!/bin/bash
printf '%s\n' "${0##*/}" >"$TEST_CALL"
printf '%s\n' "${GHX_GH_PATH:-}" >"$TEST_GH_PATH"
if [[ "${TEST_READ_STDIN:-}" == 1 ]]; then
  cat >"$TEST_STDIN"
fi
printf '{"ok":true}\n'
STUB
cp "$temporary/bin/gh" "$temporary/bin/ghx"
chmod +x "$temporary/bin/gh" "$temporary/bin/ghx"
low-data on >/dev/null

expect_route() {
  local route="$1"
  shift
  rm -f "$TEST_CALL"
  "$@" >"$temporary/stdout" 2>"$temporary/stderr"
  [[ "$( < "$TEST_CALL")" == "$route" ]]
  [[ ! -s "$temporary/stderr" ]]
}

expect_blocked() {
  local actual=0
  rm -f "$TEST_CALL"
  "$@" >"$temporary/stdout" 2>"$temporary/stderr" || actual=$?
  if [[ "$actual" != 77 || -e "$TEST_CALL" || -s "$temporary/stdout" ]]; then
    printf 'FAIL: expected pre-dispatch download rejection: %s\n' "$*" >&2
    exit 1
  fi
}

for wrapper in gh ghx; do
  expect_blocked "$wrapper" repo clone example/repo
  expect_blocked "$wrapper" --no-cache repo clone example/repo
  expect_blocked "$wrapper" --cache-only --hostname api.example.test repo clone example/repo
  expect_blocked "$wrapper" --hostname=api.example.test release download v1
  expect_blocked "$wrapper" -- run download 123
  expect_blocked "$wrapper" api repos/example/repo/tarball
  expect_blocked "$wrapper" api --hostname api.example.test repos/example/repo/zipball/main
  expect_blocked "$wrapper" api /repos/example/repo/tarball/main?archive=1
  expect_blocked "$wrapper" api --header 'Accept: application/json' \
    https://api.example.test/repos/example/repo/zipball/main
  expect_blocked "$wrapper" api -- \
    https://api.example.test/api/v3/repos/example/repo/actions/artifacts/123/zip
  expect_blocked "$wrapper" api repos/example/repo/releases/assets/123 \
    -H 'Accept: application/octet-stream'
  expect_blocked "$wrapper" api --header='ACCEPT: APPLICATION/OCTET-STREAM' \
    repos/example/repo/releases/assets/123
  expect_blocked "$wrapper" api -HAccept:application/octet-stream \
    repos/example/repo/releases/assets/123
  expect_blocked "$wrapper" api -H=Accept:application/octet-stream \
    repos/example/repo/releases/assets/123
  expect_blocked "$wrapper" api --input - repos/example/repo/zipball/main </dev/null

  route=gh
  expect_route "$route" "$wrapper" api repos/example/repo
  expect_route "$route" "$wrapper" api repos/example/repo/releases/assets/123
  expect_route "$route" "$wrapper" api -H 'Accept: application/vnd.github+json' \
    repos/example/repo/releases/assets/123
  expect_route "$route" "$wrapper" api repos/example/repo/actions/runs/123/artifacts
  expect_route "$route" "$wrapper" api repos/example/repo/issues \
    -f title=tarball -f body=application/octet-stream
  expect_route gh "$wrapper" pr view 123
  expect_route ghx "$wrapper" pr view 123 -R example/repo --json number
  expect_route gh "$wrapper" issue create --title fixture --body fixture
  expect_route gh "$wrapper" auth status
  printf 'fixture payload\nsecond line\n' |
    TEST_READ_STDIN=1 expect_route gh "$wrapper" api repos/example/repo/issues --input -
  [[ "$( < "$TEST_STDIN")" == $'fixture payload\nsecond line' ]]
  printf 'fixture body\n' |
    TEST_READ_STDIN=1 expect_route gh "$wrapper" issue create --body-file -
  [[ "$( < "$TEST_STDIN")" == 'fixture body' ]]
  expect_route gh env LOW_DATA_ALLOW_NETWORK=1 "$wrapper" repo clone example/repo
done

expect_route gh ghx --no-cache api repos/example/repo
expect_route ghx ghx pr view 123 -R example/repo --json number
[[ "$( < "$TEST_GH_PATH")" -ef "$temporary/bin/gh" ]]
expect_route gh gh api repos/example/repo --jq '{ok: .ok}'
[[ "$( < "$temporary/stdout")" == '{"ok":true}' ]]
low-data off >/dev/null
expect_route gh ghx run download 123
low-data on >/dev/null
mv "$temporary/bin/ghx" "$temporary/bin/proxy-disabled"
export PATH="$root/bin:$temporary/bin:/usr/bin:/bin"
expect_route gh ghx pr view 123
expect_blocked ghx repo clone example/repo
# Individual symlink installs must resolve both the router and optional guard.
mkdir "$temporary/entry"
ln -s "$root/bin/gh" "$temporary/entry/gh"
ln -s "$root/bin/ghx" "$temporary/entry/ghx"
ln -s "$root/bin/low-data" "$temporary/entry/low-data"
expect_blocked "$temporary/entry/gh" repo clone example/repo
expect_route gh "$temporary/entry/ghx" --no-cache api repos/example/repo
expect_blocked "$temporary/entry/low-data" github-check repo clone example/repo

# Copy installs without the optional runtime are valid only before enrollment.
mkdir -p "$temporary/incomplete/gh-support"
cp "$root/bin/gh" "$root/bin/ghx" "$temporary/incomplete/"
cp "$root/bin/gh-support/route.sh" "$temporary/incomplete/gh-support/"
for wrapper in gh ghx; do
  actual=0
  rm -f "$TEST_CALL"
  "$temporary/incomplete/$wrapper" api repos/example/repo >"$temporary/stdout" 2>"$temporary/stderr" || actual=$?
  [[ "$actual" == 126 && ! -e "$TEST_CALL" ]]
  grep -Fq 'enrolled low-data runtime is unavailable' "$temporary/stderr"
done
rm "$XDG_CONFIG_HOME/low-data/mode"
expect_route gh "$temporary/incomplete/gh" api repos/example/repo
expect_route gh "$temporary/incomplete/ghx" api repos/example/repo
low-data on >/dev/null
cp "$root/bin/low-data" "$temporary/incomplete/low-data"
actual=0
rm -f "$TEST_CALL"
"$temporary/incomplete/gh" repo clone example/repo >"$temporary/stdout" 2>"$temporary/stderr" || actual=$?
[[ "$actual" == 126 && ! -e "$TEST_CALL" ]]
grep -Fq 'enrolled guard support is unavailable' "$temporary/stderr"
printf 'PASS: gh/ghx bulk guards, metadata, stdin, enrollment and symlink routing\n'
