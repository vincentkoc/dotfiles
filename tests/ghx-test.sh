#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

wrapper_dir="$temporary/wrapper"
backend_dir="$temporary/backend"
mkdir -p "$wrapper_dir" "$backend_dir"
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

normal_output="$(PATH="$wrapper_dir:$backend_dir:/usr/bin:/bin" "$wrapper_dir/ghx" pr view 123)"
grep -Fq 'ghx:pr view 123' <<<"$normal_output"
grep -Fq "backend:$backend_dir/gh" <<<"$normal_output"

gh_normal_output="$(PATH="$wrapper_dir:$backend_dir:/usr/bin:/bin" "$wrapper_dir/gh" pr view 456)"
grep -Fq 'ghx:pr view 456' <<<"$gh_normal_output"
grep -Fq "backend:$backend_dir/gh" <<<"$gh_normal_output"

auth_output="$(PATH="$wrapper_dir:$backend_dir:/usr/bin:/bin" "$wrapper_dir/ghx" auth token)"
grep -Fq 'gh:auth token' <<<"$auth_output"
if grep -Fq 'ghx:' <<<"$auth_output"; then
  printf 'ghx auth command unexpectedly used the ghx backend\n' >&2
  exit 1
fi

gh_auth_output="$(PATH="$wrapper_dir:$backend_dir:/usr/bin:/bin" "$wrapper_dir/gh" auth status)"
grep -Fq 'gh:auth status' <<<"$gh_auth_output"
if grep -Fq 'ghx:' <<<"$gh_auth_output"; then
  printf 'gh auth command unexpectedly used the ghx backend\n' >&2
  exit 1
fi

stdin_output="$(printf 'preserved body' | PATH="$wrapper_dir:$backend_dir:/usr/bin:/bin" "$wrapper_dir/ghx" pr edit 123 --body-file -)"
grep -Fq 'gh:pr edit 123 --body-file -' <<<"$stdin_output"
grep -Fq 'preserved body' <<<"$stdin_output"
if grep -Fq 'ghx:' <<<"$stdin_output"; then
  printf 'stdin-backed command unexpectedly used ghx\n' >&2
  exit 1
fi

token_output="$(printf 'token-value' | PATH="$wrapper_dir:$backend_dir:/usr/bin:/bin" "$wrapper_dir/ghx" auth login --with-token)"
grep -Fq 'gh:auth login --with-token' <<<"$token_output"
grep -Fq 'token-value' <<<"$token_output"

printf 'ghx wrapper tests passed\n'
