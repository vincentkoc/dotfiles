#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

wrapper_dir="$temporary/wrapper"
backend_dir="$temporary/backend"
symlink_dir="$temporary/symlink-bin"
mkdir -p "$wrapper_dir" "$backend_dir"
ln -s "$wrapper_dir" "$symlink_dir"
cp "$repo_root/bin/codex" "$wrapper_dir/codex"

cat >"$backend_dir/uname" <<'EOF'
#!/usr/bin/env bash
printf 'TestOS\n'
EOF

cat >"$backend_dir/gh" <<'EOF'
#!/usr/bin/env bash
if [[ "$*" == "auth token" ]]; then
  printf 'native-token\n'
  exit 0
fi
exit 64
EOF

cat >"$backend_dir/ghx" <<'EOF'
#!/usr/bin/env bash
printf 'ghx must not be called\n' >&2
exit 65
EOF

cat >"$backend_dir/codex" <<'EOF'
#!/usr/bin/env bash
printf 'version:%s\n' "$*"
if [[ "${GITHUB_PAT_TOKEN:-}" == "native-token" ]]; then
  printf 'token:injected\n'
else
  printf 'token:missing\n'
  exit 66
fi
EOF

cp "$repo_root/bin/task-runtime" "$wrapper_dir/task-runtime"
chmod +x "$wrapper_dir/codex" "$wrapper_dir/task-runtime" \
  "$backend_dir/uname" "$backend_dir/gh" "$backend_dir/ghx" "$backend_dir/codex"

long_path="$symlink_dir"
path_entry_count=1
for index in {1..41}; do
  filler="$temporary/filler-$index-with-a-deliberately-long-path-segment"
  mkdir -p "$filler"
  long_path="$long_path:$filler"
  path_entry_count=$((path_entry_count + 1))
done
long_path="$long_path:$backend_dir:/usr/bin:/bin"
path_entry_count=$((path_entry_count + 3))
[[ "$path_entry_count" -eq 45 ]]

output="$(
  env -u GITHUB_PERSONAL_ACCESS_TOKEN -u GITHUB_PAT_TOKEN -u CODEX_HOME \
    PATH="$long_path" "$symlink_dir/codex" --version
)"
[[ "$output" == *"version:--version"* ]]
[[ "$output" == *"token:injected"* ]]

darwin_home="$temporary/darwin-home"
mkdir -p "$darwin_home/.codex/packages/standalone/current/bin"
cat >"$darwin_home/.codex/packages/standalone/current/bin/codex" <<'EOF'
#!/usr/bin/env bash
printf 'standalone:%s\n' "$*"
EOF
chmod +x "$darwin_home/.codex/packages/standalone/current/bin/codex"
cat >"$backend_dir/uname" <<'EOF'
#!/usr/bin/env bash
printf 'Darwin\n'
EOF
darwin_output="$(
  env -u GITHUB_PERSONAL_ACCESS_TOKEN -u CODEX_HOME \
    GITHUB_PAT_TOKEN=test HOME="$darwin_home" PATH="$long_path" \
    "$symlink_dir/codex" run "two words"
)"
[[ "$darwin_output" == "standalone:run two words" ]]

missing_home="$temporary/darwin-missing"
mkdir -p "$missing_home"
set +e
env -u GITHUB_PERSONAL_ACCESS_TOKEN -u CODEX_HOME \
  GITHUB_PAT_TOKEN=test HOME="$missing_home" PATH="$long_path" \
  "$symlink_dir/codex" --version \
  >"$temporary/darwin-missing.stdout" 2>"$temporary/darwin-missing.stderr"
missing_status=$?
set -e
[[ $missing_status -eq 127 ]]
[[ ! -s "$temporary/darwin-missing.stdout" ]]
grep -Fx \
  "codex: official standalone CLI is missing; run codex-standalone-install" \
  "$temporary/darwin-missing.stderr" >/dev/null

linux_home="$temporary/linux-home"
mkdir -p "$linux_home/.codex/packages/standalone/current/bin"
cat >"$linux_home/.codex/packages/standalone/current/bin/codex" <<'EOF'
#!/usr/bin/env bash
printf 'standalone:%s\n' "$*"
[[ "${GITHUB_PAT_TOKEN:-}" == "native-token" ]]
EOF
chmod +x "$linux_home/.codex/packages/standalone/current/bin/codex"
cat >"$backend_dir/uname" <<'EOF'
#!/usr/bin/env bash
printf 'Linux\n'
EOF
linux_output="$(
  env -u GITHUB_PERSONAL_ACCESS_TOKEN -u GITHUB_PAT_TOKEN -u CODEX_HOME \
    HOME="$linux_home" PATH="$long_path" \
    "$symlink_dir/codex" run "two words"
)"
[[ "$linux_output" == "standalone:run two words" ]]

constrained_output="$(
  env -u GITHUB_PERSONAL_ACCESS_TOKEN -u CODEX_HOME \
    GITHUB_PAT_TOKEN=test HOME="$darwin_home" PATH="$long_path" \
    "$symlink_dir/codex" --constrained-network run "two words"
)"
[[ "$constrained_output" == "standalone:--disable unbounded_connection_retries run two words" ]]

admission_log="$temporary/admission.log"
cat >"$temporary/admission" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >"$CODEX_TEST_ADMISSION_LOG"
EOF
chmod +x "$temporary/admission"
heavy_output="$(
  env -u GITHUB_PERSONAL_ACCESS_TOKEN -u CODEX_HOME \
    GITHUB_PAT_TOKEN=test HOME="$darwin_home" PATH="$long_path" \
    CODEX_TASK_RUNTIME_BIN="$temporary/admission" \
    CODEX_TEST_ADMISSION_LOG="$admission_log" \
    "$symlink_dir/codex" --heavy-work --disk-reserve-gib 12 \
      --planned-write-bytes 4096 run
)"
[[ "$heavy_output" == "standalone:run" ]]
grep -Fx "admit --path $PWD --reserve-gib 12 --planned-bytes 4096" "$admission_log"

catalog_home="$temporary/catalog home"
catalog_binary="$catalog_home/packages/standalone/current/bin/codex"
catalog_helper="$catalog_home/model-catalogs/render-cli-catalog"
native_args="$temporary/native.args"
helper_args="$temporary/helper.args"
order_log="$temporary/order.log"
mkdir -p "$(dirname "$catalog_binary")" "$(dirname "$catalog_helper")"
cat >"$catalog_binary" <<'EOF'
#!/usr/bin/env bash
printf '%s\0' "$#" "$@" >"$CODEX_TEST_NATIVE_ARGS"
printf 'native\n' >>"$CODEX_TEST_ORDER_LOG"
[[ "$GITHUB_PERSONAL_ACCESS_TOKEN" == native-token ]]
[[ "$GITHUB_PAT_TOKEN" == native-token ]]
[[ "$CODEX_TEST_SENTINEL" == preserved ]]
printf 'native\n'
EOF
chmod +x "$catalog_binary"

catalog_run() {
  env -u GITHUB_PERSONAL_ACCESS_TOKEN -u GITHUB_PAT_TOKEN \
    HOME="$temporary/unused-home" CODEX_HOME="$catalog_home" PATH="$long_path" \
    CODEX_TEST_NATIVE_ARGS="$native_args" CODEX_TEST_HELPER_ARGS="$helper_args" \
    CODEX_TEST_ORDER_LOG="$order_log" CODEX_TEST_SENTINEL=preserved \
    CODEX_TEST_HELPER_EXIT="${CODEX_TEST_HELPER_EXIT:-0}" \
    CODEX_TASK_RUNTIME_BIN="$temporary/catalog-admission" \
    "$symlink_dir/codex" "$@"
}

assert_argv() {
  local actual="$1"
  shift
  printf '%s\0' "$#" "$@" >"$temporary/expected.args"
  cmp "$temporary/expected.args" "$actual"
}

assert_catalog_route() {
  local expected="$1" output
  shift
  : >"$native_args"
  : >"$helper_args"
  : >"$order_log"
  output="$(catalog_run "$@")"
  [[ "$output" == "$expected" ]]
  [[ "$(cat "$order_log")" == "$expected" ]]
  if [[ "$expected" == helper ]]; then
    assert_argv "$helper_args" --binary "$catalog_binary" \
      --codex-home "$catalog_home" --exec -- "$@"
    [[ ! -s "$native_args" ]]
  else
    assert_argv "$native_args" "$@"
    [[ ! -s "$helper_args" ]]
  fi
}

# No helper, or a non-executable opt-in, preserves the native launch.
assert_catalog_route native
assert_catalog_route native exec "two words"
cat >"$catalog_helper" <<'EOF'
#!/usr/bin/env bash
printf '%s\0' "$#" "$@" >"$CODEX_TEST_HELPER_ARGS"
printf 'helper\n' >>"$CODEX_TEST_ORDER_LOG"
[[ "$GITHUB_PERSONAL_ACCESS_TOKEN" == native-token ]]
[[ "$GITHUB_PAT_TOKEN" == native-token ]]
[[ "$CODEX_TEST_SENTINEL" == preserved ]]
printf 'helper\n'
exit "${CODEX_TEST_HELPER_EXIT:-0}"
EOF
chmod 644 "$catalog_helper"
assert_catalog_route native review
chmod 755 "$catalog_helper"

assert_catalog_route helper
assert_catalog_route helper "two words"
for command in exec e review resume fork app-server; do
  assert_catalog_route helper "$command"
done
assert_catalog_route helper --no-alt-screen --model test-model exec --json "two words"
assert_catalog_route helper -m help -C login -i image.png review
assert_catalog_route helper -c 'model="test-model"' --config features.example=true exec
assert_catalog_route helper --config=features.example=true -cfeatures.other=false resume --last
assert_catalog_route helper app-server --listen unix://example
assert_catalog_route helper exec "" "two words" $'line\nbreak' '*' -- \
  --profile explicit --config model_catalog_json=explicit --heavy-work
assert_catalog_route helper -- login --version --ignore-user-config
assert_catalog_route helper exec "a prompt mentioning --profile and --config"

for option in -p --profile; do
  assert_catalog_route native "$option" explicit exec
done
for option in -pexplicit -p=explicit --profile=explicit; do
  assert_catalog_route native exec "$option"
done
for key in model_catalog_json profile profiles profiles.team.model_provider \
  model_provider model_providers model_providers.custom.base_url oss_provider \
  '"model_catalog_json"' "model_providers.'custom'.base_url"; do
  for option in -c --config; do
    assert_catalog_route native "$option" "$key = \"explicit\"" exec
  done
  for option in "-c$key=explicit" "-c=$key=explicit" "--config=$key=explicit"; do
    assert_catalog_route native exec "$option"
  done
done
for option in --ignore-user-config --ignore-user-config=true -h --help -V --version \
  --bundled --oss --local-provider=custom --remote=unix://example; do
  assert_catalog_route native exec "$option"
done
assert_catalog_route native --local-provider custom exec
assert_catalog_route native --remote unix://example
for command in agents login logout mcp plugin mcp-server remote-control app completion \
  install update upgrade doctor sandbox debug apply a queue archive delete \
  migrate-rollouts unarchive cloud exec-server features help; do
  assert_catalog_route native --config features.example=true "$command"
done
assert_catalog_route native debug models --bundled
assert_catalog_route native app-server generate-ts
assert_catalog_route native app-server generate-json-schema
assert_catalog_route native --config
assert_catalog_route native -c malformed
assert_catalog_route native -m -- --profile explicit

cat >"$temporary/catalog-admission" <<'EOF'
#!/usr/bin/env bash
printf 'admission\n' >>"$CODEX_TEST_ORDER_LOG"
printf '%s\0' "$#" "$@" >"$CODEX_TEST_ORDER_LOG.args"
EOF
chmod +x "$temporary/catalog-admission"
: >"$native_args"
: >"$helper_args"
: >"$order_log"
[[ "$(catalog_run --heavy-work --disk-reserve-gib 12 --planned-write-bytes 4096 \
  --constrained-network exec "two words")" == helper ]]
[[ "$(cat "$order_log")" == $'admission\nhelper' ]]
assert_argv "$order_log.args" admit --path "$PWD" --reserve-gib 12 --planned-bytes 4096
assert_argv "$helper_args" --binary "$catalog_binary" --codex-home "$catalog_home" \
  --exec -- --disable unbounded_connection_retries exec "two words"
[[ ! -s "$native_args" ]]

: >"$native_args"
: >"$helper_args"
: >"$order_log"
if failure_output="$(CODEX_TEST_HELPER_EXIT=73 catalog_run exec "two words")"; then
  printf 'catalog helper failure must not fall back to the native CLI\n' >&2
  exit 1
else
  [[ $? -eq 73 ]]
fi
[[ "$failure_output" == helper && "$(cat "$order_log")" == helper ]]
[[ ! -s "$native_args" && -s "$helper_args" ]]

if grep -Fq 'gh auth token' "$repo_root/.zshrc"; then
  printf '.zshrc must not fetch GitHub credentials during startup\n' >&2
  exit 1
fi

printf 'codex wrapper tests passed\n'
