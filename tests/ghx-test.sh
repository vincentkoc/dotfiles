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
cp "$repo_root/bin/ghx" "$wrapper_dir/ghx"
cp "$repo_root/bin/gh" "$wrapper_dir/gh"

cat >"$backend_dir/ghx" <<'EOF'
#!/usr/bin/env bash
printf 'ghx:%s\n' "$*"
printf 'backend:%s\n' "${GHX_GH_PATH:-}"
EOF

cat >"$backend_dir/gh" <<'EOF'
#!/usr/bin/env bash
printf 'gh:%s\n' "$*"
cat
EOF

chmod +x "$wrapper_dir/ghx" "$wrapper_dir/gh" "$backend_dir/ghx" "$backend_dir/gh"

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

normal_output="$(PATH="$long_path" "$symlink_dir/ghx" pr view 123)"
[[ "$normal_output" == *"ghx:pr view 123"* ]]
[[ "$normal_output" == *"backend:$backend_dir/gh"* ]]

gh_normal_output="$(PATH="$long_path" "$symlink_dir/gh" pr view 456)"
[[ "$gh_normal_output" == *"ghx:pr view 456"* ]]
[[ "$gh_normal_output" == *"backend:$backend_dir/gh"* ]]

auth_output="$(PATH="$long_path" "$symlink_dir/ghx" auth token)"
[[ "$auth_output" == *"gh:auth token"* ]]
if [[ "$auth_output" == *"ghx:"* ]]; then
  printf 'ghx auth command unexpectedly used the ghx backend\n' >&2
  exit 1
fi

gh_auth_output="$(PATH="$long_path" "$symlink_dir/gh" auth status)"
[[ "$gh_auth_output" == *"gh:auth status"* ]]
if [[ "$gh_auth_output" == *"ghx:"* ]]; then
  printf 'gh auth command unexpectedly used the ghx backend\n' >&2
  exit 1
fi

stdin_output="$(printf 'preserved body' | PATH="$long_path" "$symlink_dir/ghx" pr edit 123 --body-file -)"
[[ "$stdin_output" == *"gh:pr edit 123 --body-file -"* ]]
[[ "$stdin_output" == *"preserved body"* ]]
if [[ "$stdin_output" == *"ghx:"* ]]; then
  printf 'stdin-backed command unexpectedly used ghx\n' >&2
  exit 1
fi

token_output="$(printf 'token-value' | PATH="$long_path" "$symlink_dir/ghx" auth login --with-token)"
[[ "$token_output" == *"gh:auth login --with-token"* ]]
[[ "$token_output" == *"token-value"* ]]

printf 'ghx wrapper tests passed\n'
