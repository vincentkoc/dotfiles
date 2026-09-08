#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export TEST_PYTHON
TEST_PYTHON="$(command -v python3)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
wrapper="$temporary/wrapper"
backend="$temporary/backend"
mkdir -p "$wrapper/gh-support" "$backend" "$temporary/home" "$temporary/alias" "$temporary/no-jq-tools"
ln -s /bin/bash "$temporary/no-jq-tools/bash"
ln -s "$(command -v dirname)" "$temporary/no-jq-tools/dirname"
ln -s "$(command -v cat)" "$temporary/no-jq-tools/cat"
cp "$root/bin/gh" "$root/bin/ghx" "$wrapper/"
cp "$root/bin/gh-support/route.sh" "$wrapper/gh-support/"
if [[ "${GH_TEST_COMPOSE_GUARD:-0}" == 1 ]]; then
  for name in gh ghx; do
    if ! grep -q 'low-data.*github-check' "$wrapper/$name"; then
      awk '{print} /^wrapper_dir=/ {print "\"$wrapper_dir/low-data\" github-check \"$@\""}' \
        "$wrapper/$name" >"$wrapper/$name.new"
      mv "$wrapper/$name.new" "$wrapper/$name"
    fi
  done
fi
cat >"$wrapper/low-data" <<'EOF'
#!/usr/bin/env bash
printf 'guard\n' >>"$TEST_GUARD"
exit "${TEST_GUARD_STATUS:-0}"
EOF
cat >"$backend/python3" <<'EOF'
#!/usr/bin/env bash
case "${TEST_BOOTSTRAP_RACE:-}" in
  directory) mkdir "$3" ;;
  symlink) ln -s "$HOME/.ghx/saved" "$3" ;;
esac
exec "$TEST_PYTHON" "$@"
EOF
cat >"$backend/gh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "${0##*/}" >"$TEST_ROUTE"
printf '%s\n' "$@" >"$TEST_ARGS"
printf '%s\n' "${GH_FORCE_TTY-unset}" "${GH_PROMPT_DISABLED-unset}" \
  "${GH_PAGER-unset}" "${NO_COLOR-unset}" "${GHX_GH_PATH-unset}" "$PWD" >"$TEST_ENV"
if [[ "${TEST_READ_STDIN:-0}" == 1 ]]; then cat >"$TEST_INPUT"; fi
[[ ! -f "$TEST_OUTPUT" ]] || cat "$TEST_OUTPUT"
printf '%s' "${TEST_ERROR:-}" >&2
exit "${TEST_STATUS:-0}"
EOF
cp "$backend/gh" "$backend/ghx"
ln -s "$wrapper/gh" "$temporary/alias/gh"
ln -s "$wrapper/ghx" "$temporary/alias/ghx"
chmod +x "$wrapper/gh" "$wrapper/ghx" "$wrapper/low-data" "$backend/gh" "$backend/ghx" "$backend/python3"
export HOME="$temporary/home" PATH="$wrapper:$temporary/alias:$backend:/usr/bin:/bin"
export TEST_ROUTE="$temporary/route" TEST_ARGS="$temporary/args" TEST_ENV="$temporary/env"
export TEST_INPUT="$temporary/input" TEST_OUTPUT="$temporary/output" TEST_GUARD="$temporary/guard"
unset GH_REPO GHX_NO_CACHE GH_FORCE_TTY GH_PROMPT_DISABLED GH_PAGER

run() {
  local expected="$1" status=0
  shift
  rm -f "$TEST_ROUTE" "$TEST_GUARD"
  "$@" >"$temporary/stdout" 2>"$temporary/stderr" || status=$?
  [[ "$status" == "$expected" ]] || {
    printf 'FAIL expected exit %s, got %s: %s\n' "$expected" "$status" "$*" >&2
    cat "$temporary/stderr" >&2
    exit 1
  }
}
route() {
  local expected="$1"
  shift
  run 0 "$@"
  [[ "$( <"$TEST_ROUTE")" == "$expected" ]] || {
    printf 'FAIL expected route %s: %s\n' "$expected" "$*" >&2
    exit 1
  }
  if grep -q 'low-data.*github-check' "$wrapper/gh"; then
    [[ "$(wc -l <"$TEST_GUARD" | tr -d ' ')" == 1 ]]
  fi
}

bash "$root/bin/ghx-bootstrap" >/dev/null
[[ "$( <"$HOME/.ghx/config.yaml")" == '{}' ]]
printf 'ttl: 42s\n' >"$HOME/.ghx/config.yaml"
bash "$root/bin/ghx-bootstrap" >/dev/null
[[ "$( <"$HOME/.ghx/config.yaml")" == 'ttl: 42s' ]]
mv "$HOME/.ghx/config.yaml" "$HOME/.ghx/saved"
ln -s "$HOME/.ghx/saved" "$HOME/.ghx/config.yaml"
run 1 bash "$root/bin/ghx-bootstrap"
rm "$HOME/.ghx/config.yaml"
mv "$HOME/.ghx/saved" "$HOME/.ghx/config.yaml"
mv "$HOME/.ghx" "$HOME/saved-ghx"
ln -s "$HOME/saved-ghx" "$HOME/.ghx"
run 1 bash "$root/bin/ghx-bootstrap"
rm "$HOME/.ghx"
mv "$HOME/saved-ghx" "$HOME/.ghx"
mv "$HOME/.ghx/config.yaml" "$HOME/.ghx/saved"
TEST_BOOTSTRAP_RACE=directory run 1 bash "$root/bin/ghx-bootstrap"
[[ -d "$HOME/.ghx/config.yaml" && -z "$(ls -A "$HOME/.ghx/config.yaml")" ]]
rmdir "$HOME/.ghx/config.yaml"
TEST_BOOTSTRAP_RACE=symlink run 1 bash "$root/bin/ghx-bootstrap"
[[ -L "$HOME/.ghx/config.yaml" && "$( <"$HOME/.ghx/saved")" == 'ttl: 42s' ]]
rm "$HOME/.ghx/config.yaml"
mv "$HOME/.ghx/saved" "$HOME/.ghx/config.yaml"

for entry in gh ghx; do
  route ghx "$entry" pr view 123 --repo example/repo --json number,title
  route ghx "$entry" --ttl 15 issue view 123 -Rexample/repo --json=number --jq=.number
  [[ "$( <"$TEST_ARGS")" == $'--ttl\n15\nissue\nview\n123\n-Rexample/repo\n--json=number\n--jq=.number' ]]
  # shellcheck disable=SC2016
  route ghx "$entry" --ttl 15 pr view 123 --repo=example/repo --json number,title \
    --jq '.number as $n | $n'
  [[ "$( <"$TEST_ARGS")" == $'--ttl\n15\npr\nview\n123\n--repo=example/repo\n--json\nnumber,title\n--jq\n.number as $n | $n' ]]
  route ghx "$entry" run view 123 -R example/repo --json status
  route ghx "$entry" pr checks 123 --repo=example/repo --json state
  route ghx "$entry" xcache stats
  route ghx "$entry" xdaemon status
  route ghx "$entry" --no-cache xcache stats
  [[ "$(head -1 "$TEST_ARGS")" == xcache ]]
  route ghx "$entry" xversion
  run 2 "$entry" --ttl 15 xcache stats
  route gh "$entry" pr view 123
  route gh "$entry" pr view --repo example/repo --json number
  route gh "$entry" pr view branch-name -R example/repo --json number
  route gh "$entry" pr view 123 -R example/repo --json number --web
  route gh "$entry" pr checks 123 -R example/repo --json state --watch
  route gh "$entry" run view 123 -R example/repo --json status --log
  route gh "$entry" --no-cache pr view 123 -R example/repo --json number
  [[ "$(head -1 "$TEST_ARGS")" == pr ]]
  route gh env GHX_NO_CACHE=1 "$entry" pr view 123 -R example/repo --json number
  route gh env GH_REPO=example/other "$entry" pr view 123 -R example/repo --json number
  for args in api 'api graphql'; do
    # Intentional word splitting expands the two fixed command fixtures.
    # shellcheck disable=SC2086
    route gh "$entry" $args -f 'query=query { viewer { login } }'
  done
  route gh "$entry" api repos/example/repo/issues -F title=fixture
  route gh "$entry" api repos/example/repo --cache 15s
  route gh "$entry" api repos/example/repo --cache=15s
  run 2 "$entry" --no-cache api repos/example/repo --cache 15s
  run 2 env GHX_NO_CACHE=1 "$entry" api repos/example/repo --cache=15s
  run 2 "$entry" --ttl
  run 2 "$entry" --ttl api repos/example/repo
  run 2 "$entry" --ttl -1 pr view 123
  run 2 "$entry" --ttl=15 pr view 123
  run 2 "$entry" --no-cache=true pr view 123
  run 2 "$entry" --ttl 15 --no-cache pr view 123
  run 2 "$entry" --ttl 15 api repos/example/repo
  run 2 "$entry" --ttl 15 auth status
  run 2 "$entry" --ttl 15 issue edit 123 --body-file - </dev/null
  route gh env GH_FORCE_TTY=0 "$entry" pr create --title fixture --body fixture
  [[ "$(sed -n '1,4p' "$TEST_ENV")" == $'unset\n1\ncat\n1' ]]
  route gh env GH_FORCE_TTY=100 GH_PROMPT_DISABLED=custom GH_PAGER=caller \
    "$entry" --no-cache auth status
  [[ "$(sed -n '1,4p' "$TEST_ENV")" == $'unset\ncustom\ncaller\n1' ]]

  printf 'payload with spaces\nsecond line\000last\n' >"$temporary/payload"
  for flag in - /dev/stdin /dev/fd/0; do
    TEST_READ_STDIN=1 route gh "$entry" issue edit 123 --body-file "$flag" <"$temporary/payload"
    cmp "$temporary/payload" "$TEST_INPUT"
  done
  TEST_READ_STDIN=1 route gh "$entry" secret set FIXTURE <"$temporary/payload"
  cmp "$temporary/payload" "$TEST_INPUT"
  TEST_READ_STDIN=1 route gh "$entry" auth login --with-token <"$temporary/payload"
  cmp "$temporary/payload" "$TEST_INPUT"
  TEST_READ_STDIN=1 route gh "$entry" api graphql --input=- <"$temporary/payload"
  cmp "$temporary/payload" "$TEST_INPUT"
  route gh "$entry" issue edit 123 --body-file "$temporary/body file.txt"
  [[ "$(tail -1 "$TEST_ARGS")" == "$temporary/body file.txt" ]]
  TEST_ERROR='expected backend error' TEST_STATUS=7 run 7 "$entry" pr create
  [[ "$( <"$temporary/stderr")" == 'expected backend error' ]]

  # The stub verifies argv and passthrough. Real native formatting is exercised
  # by ghx-native-cache-test.py, including scalars, objects, and mixed streams.
  printf '{"ok":true}\n{"ok":false}\n' >"$TEST_OUTPUT"
  for flag in --jq -q; do
    route gh "$entry" api repos/example/repo "$flag" '{ok: .ok}'
    cmp "$TEST_OUTPUT" "$temporary/stdout"
    [[ "$( <"$TEST_ARGS")" == "api"$'\n'"repos/example/repo"$'\n'"$flag"$'\n''({ok: .ok}'$'\n'') | if type == "object" then tojson else . end' ]]
  done
  for prefix in --jq= -q -q= -iq; do
    route gh "$entry" api repos/example/repo "${prefix}{ok:.ok}"
    [[ "$( <"$TEST_ARGS")" == "api"$'\n'"repos/example/repo"$'\n'"${prefix}({ok:.ok}"$'\n'') | if type == "object" then tojson else . end' ]]
  done
  route gh "$entry" api repos/example/repo -iH --jq -q .field
  [[ "$( <"$TEST_ARGS")" == $'api\nrepos/example/repo\n-iH\n--jq\n-q\n(.field\n) | if type == "object" then tojson else . end' ]]
  route gh "$entry" api repos/example/repo --input --jq --jq .field
  [[ "$( <"$TEST_ARGS")" == $'api\nrepos/example/repo\n--input\n--jq\n--jq\n(.field\n) | if type == "object" then tojson else . end' ]]
  route gh "$entry" api repos/example/repo -- --jq .field
  [[ "$( <"$TEST_ARGS")" == $'api\nrepos/example/repo\n--\n--jq\n.field' ]]
  route gh "$entry" api repos/example/repo --jq '.field # trailing comment'
  [[ "$( <"$TEST_ARGS")" == $'api\nrepos/example/repo\n--jq\n(.field # trailing comment\n) | if type == "object" then tojson else . end' ]]
  TEST_STATUS=9 run 9 "$entry" api repos/example/repo --jq '{ok:.ok}'
  printf '{\nordinary scalar output\n' >"$TEST_OUTPUT"
  route gh "$entry" api repos/example/repo --jq '"{"'
  cmp "$TEST_OUTPUT" "$temporary/stdout"
  : >"$TEST_OUTPUT"
  route gh "$entry" api repos/example/repo --jq '{ok:.ok}'
  [[ ! -s "$temporary/stdout" ]]
  if grep -q 'low-data.*github-check' "$wrapper/$entry"; then
    TEST_GUARD_STATUS=77 run 77 "$entry" repo clone example/repo
    [[ ! -e "$TEST_ROUTE" ]]
  fi
done

"$TEST_PYTHON" - "$wrapper/gh" <<'PY'
import os
import pty
import subprocess
import sys

master, slave = pty.openpty()
try:
    subprocess.run([sys.argv[1], "pr", "create"], stdin=slave,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    with open(os.environ["TEST_ENV"]) as captured:
        assert captured.read().splitlines()[1:3] == ["unset", "unset"]
finally:
    os.close(slave)
    os.close(master)
PY

mv "$backend/ghx" "$backend/proxy-disabled"
route gh ghx pr view 123 -R example/repo --json number
mv "$backend/proxy-disabled" "$backend/ghx"
route gh env PATH="$wrapper:$backend:$temporary/no-jq-tools" gh api repos/example/repo --jq '{ok:.ok}'
mv "$HOME/.ghx/config.yaml" "$HOME/.ghx/saved"
route gh ghx pr view 123 -R example/repo --json number
mv "$backend/gh" "$backend/native-disabled"
run 127 ghx pr view 123 -R example/repo --json number
[[ ! -e "$TEST_ROUTE" ]]
run 127 gh api repos/example/repo
printf 'PASS: gh/ghx routing, stdin, cache controls, bootstrap, JSONL, guards, and failures\n'
