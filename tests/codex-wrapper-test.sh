#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

wrapper_dir="$temporary/wrapper"
backend_dir="$temporary/backend"
mkdir -p "$wrapper_dir" "$backend_dir"
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

output="$(env -u GITHUB_PAT_TOKEN PATH="$wrapper_dir:$backend_dir:/usr/bin:/bin" "$wrapper_dir/codex" --version)"
grep -Fq 'version:--version' <<<"$output"
grep -Fq 'token:injected' <<<"$output"

if grep -Fq 'gh auth token' "$repo_root/.zshrc"; then
  printf '.zshrc must not fetch GitHub credentials during startup\n' >&2
  exit 1
fi

printf 'codex wrapper tests passed\n'
