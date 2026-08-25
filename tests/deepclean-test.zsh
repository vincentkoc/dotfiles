#!/usr/bin/env zsh
set -euo pipefail

repo_root="${0:A:h:h}"
source "$repo_root/functions/system/deepclean.zsh"

output="$(deepclean --dry-run --repo "$repo_root" --skip-mole)"
[[ "$output" == *"deepclean mode=preview"* ]]
[[ "$output" == *"deepclean complete"* ]]

temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
mkdir -p "$temporary/bin"
cat >"$temporary/bin/worktree-storage-guard" <<'EOF'
#!/bin/sh
echo storage-unavailable >&2
exit 78
EOF
chmod +x "$temporary/bin/worktree-storage-guard"
function mole() {
    print -r -- called >"$temporary/mole-called"
}
if WORKTREE_STORAGE_GUARD="$temporary/bin/worktree-storage-guard" \
    deepclean --dry-run --repo "$repo_root" \
        >"$temporary/storage.out" 2>&1; then
    print -u2 'expected unavailable storage to fail deepclean'
    exit 1
fi
grep -Fq storage-unavailable "$temporary/storage.out"
[[ ! -e "$temporary/mole-called" ]]
if grep -Fq 'deepclean complete' "$temporary/storage.out"; then
    print -u2 'storage failure must not print completion'
    exit 1
fi

if deepclean --wat >/dev/null 2>&1; then
    print -u2 'expected unknown argument to fail'
    exit 1
fi

print 'deepclean_test=passed'
