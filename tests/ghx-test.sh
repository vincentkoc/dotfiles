#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

wrapper_dir="$temporary/wrapper"
backend_dir="$temporary/backend"
mkdir -p "$wrapper_dir" "$backend_dir"
cp "$repo_root/bin/ghx" "$wrapper_dir/ghx"

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

chmod +x "$wrapper_dir/ghx" "$backend_dir/ghx" "$backend_dir/gh"

normal_output="$(PATH="$wrapper_dir:$backend_dir:/usr/bin:/bin" "$wrapper_dir/ghx" pr view 123)"
grep -Fq 'ghx:pr view 123' <<<"$normal_output"
grep -Fq "backend:$backend_dir/gh" <<<"$normal_output"

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
