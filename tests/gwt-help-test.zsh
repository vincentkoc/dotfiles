#!/usr/bin/env zsh
set -euo pipefail

root="${0:A:h:h}"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

home="$temporary/home"
repo="$temporary/repo"
fixture="$temporary/gwt-fixture"
gwt_source="$fixture/functions/gwt/gwt.zsh"
maintainer="$fixture/bin/agent-worktree-ops/agent-worktree-maintain"
fake_bin="$temporary/bin"
mkdir -p "$home" "$repo" "${gwt_source:h}" "${maintainer:h}" "$fake_bin"
cp "$root/functions/gwt/gwt.zsh" "$gwt_source"

cat >"$maintainer" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"$TEST_MAINTAIN_LOG"
EOF
chmod +x "$maintainer"

real_git="$(command -v git)"
export TEST_REAL_GIT="$real_git"
export TEST_GIT_LOG="$temporary/git.log"
export TEST_MAINTAIN_LOG="$temporary/maintain.log"
export TEST_TT_LOG="$temporary/tt.log"
cat >"$fake_bin/git" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"$TEST_GIT_LOG"
exec "$TEST_REAL_GIT" "$@"
EOF
cat >"$fake_bin/tt" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"$TEST_TT_LOG"
EOF
chmod +x "$fake_bin/git" "$fake_bin/tt"

git init -b main "$repo" >/dev/null
git -C "$repo" config user.name "GWT Help Test"
git -C "$repo" config user.email "gwt-help@example.test"
print fixture >"$repo/README.md"
git -C "$repo" add README.md
git -C "$repo" commit -m fixture >/dev/null

tree_snapshot() {
  python3 - "$1" <<'PY'
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
snapshot = {}
for path in [root, *sorted(root.rglob("*"))]:
    details = path.lstat()
    if path.is_symlink():
        kind = "symlink"
        content = os.readlink(path).encode()
    elif path.is_dir():
        kind = "directory"
        content = b""
    else:
        kind = "file"
        content = path.read_bytes()
    relative = "." if path == root else str(path.relative_to(root))
    snapshot[relative] = {
        "type": kind,
        "mode": stat.S_IMODE(details.st_mode),
        "size": details.st_size,
        "mtime_ns": details.st_mtime_ns,
        "sha256": hashlib.sha256(content).hexdigest(),
    }
print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
PY
}

typeset -a cases
cases=(
  'empty|'
  'help|help'
  'top-short|-h'
  'top-long|--help'
  'help-short|help -h'
  'help-long|help --help'
  'help-help|help help'
  'root|root --help'
  'clone|clone https://example.invalid/repo.git clone-dest --help'
  'ls|ls --help'
  'list-alias|list -h'
  'audit|audit --help'
  'clean|clean -h'
  'new|new help-branch --help'
  'add-alias|add help-branch -h'
  'cd|cd remove-me --help'
  'rm|rm remove-me -h'
  'remove-alias|remove remove-me --help'
  'prune|prune -h'
  'sparse|sparse --help'
  'sparse-status|sparse status --help'
  'sparse-show-alias|sparse show -h'
  'sparse-list|sparse list --help'
  'sparse-profiles-alias|sparse profiles -h'
  'sparse-set|sparse set profile --help'
  'sparse-add|sparse add path -h'
  'sparse-expand-alias|sparse expand path --help'
  'sparse-full|sparse full --help'
  'sparse-disable-alias|sparse disable -h'
)

repo_before="$(tree_snapshot "$repo")"
home_before="$(tree_snapshot "$home")"
reference_output="$temporary/reference.out"
for test_case in "${cases[@]}"; do
  label="${test_case%%|*}"
  invocation="${test_case#*|}"
  output="$temporary/$label.out"

  PATH="$fake_bin:$PATH" \
  HOME="$home" \
  TMUX=test \
  zsh -f -c '
    source "$1"
    cd "$2"
    before="$PWD"
    gwt ${(z)3}
    [[ "$PWD" == "$before" ]]
  ' zsh "$gwt_source" "$repo" "$invocation" >"$output"

  grep -Fq 'Usage: gwt <command> [args]' "$output"
  if [[ -e "$reference_output" ]]; then
    cmp -s "$reference_output" "$output"
  else
    cp "$output" "$reference_output"
  fi
  [[ "$repo_before" == "$(tree_snapshot "$repo")" ]]
  [[ "$home_before" == "$(tree_snapshot "$home")" ]]
  [[ ! -e "$TEST_GIT_LOG" ]]
  [[ ! -e "$TEST_MAINTAIN_LOG" ]]
  [[ ! -e "$TEST_TT_LOG" ]]
done

if PATH="$fake_bin:$PATH" \
  HOME="$home" \
  TMUX=test \
  zsh -f -c '
    source "$1"
    cd "$2"
    before="$PWD"
    if gwt unknown-command; then
      exit 1
    fi
    [[ "$PWD" == "$before" ]]
  ' zsh "$gwt_source" "$repo" >"$temporary/unknown.out" 2>&1; then
  :
else
  print -u2 'unknown command compatibility check failed'
  exit 1
fi
grep -Fxq "gwt: unknown command 'unknown-command' (run 'gwt help')" \
  "$temporary/unknown.out"
grep -Fxq 'rev-parse --is-inside-work-tree' "$TEST_GIT_LOG"
[[ "$repo_before" == "$(tree_snapshot "$repo")" ]]
[[ "$home_before" == "$(tree_snapshot "$home")" ]]
[[ ! -e "$TEST_MAINTAIN_LOG" ]]
[[ ! -e "$TEST_TT_LOG" ]]

print 'gwt help tests passed'
