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

chmod +x "$wrapper_dir/codex" "$backend_dir/uname" "$backend_dir/gh" "$backend_dir/ghx" "$backend_dir/codex"

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

output="$(env -u GITHUB_PAT_TOKEN PATH="$long_path" "$symlink_dir/codex" --version)"
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
  GITHUB_PAT_TOKEN=test HOME="$darwin_home" PATH="$long_path" \
    "$symlink_dir/codex" run "two words"
)"
[[ "$darwin_output" == "standalone:run two words" ]]

missing_home="$temporary/darwin-missing"
mkdir -p "$missing_home"
set +e
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
  env -u GITHUB_PAT_TOKEN HOME="$linux_home" PATH="$long_path" \
    "$symlink_dir/codex" run "two words"
)"
[[ "$linux_output" == "standalone:run two words" ]]

if grep -Fq 'gh auth token' "$repo_root/.zshrc"; then
  printf '.zshrc must not fetch GitHub credentials during startup\n' >&2
  exit 1
fi

printf 'codex wrapper tests passed\n'
