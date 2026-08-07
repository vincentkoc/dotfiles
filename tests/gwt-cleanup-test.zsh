#!/usr/bin/env zsh
set -euo pipefail

root="${0:A:h:h}"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

home="$temporary/home"
repo="$temporary/repo"
foreign="$temporary/foreign"
worktrees_root="$home/worktrees"
runtime="$home/Library/Application Support/agent-worktree-ops"
mkdir -p "$home" "$runtime"

git init -b main "$repo" >/dev/null
git -C "$repo" config user.name "Worktree Test"
git -C "$repo" config user.email "worktree@example.test"
git -C "$repo" config remote.origin.url "git@example.test:owner/repo.git"
print fixture >"$repo/README.md"
git -C "$repo" add README.md
git -C "$repo" commit -m fixture >/dev/null

remove_path="$temporary/remove-me"
stale_path="$temporary/stale"
git -C "$repo" worktree add -b remove-me "$remove_path" main >/dev/null
git -C "$repo" worktree add -b stale "$stale_path" main >/dev/null
rm -rf "$stale_path"

HOME="$home" DOTFILES_WORKTREES_ROOT="$worktrees_root" zsh -f -c '
  source "$1"
  cd "$2"
  gwt rm remove-me
' zsh "$root/functions/gwt/gwt.zsh" "$repo"

[[ ! -d "$remove_path" ]]
git -C "$repo" worktree list --porcelain | grep -Fq "worktree ${stale_path:A}"

git init -b main "$foreign" >/dev/null
git -C "$foreign" config user.name "Worktree Test"
git -C "$foreign" config user.email "worktree@example.test"
print foreign >"$foreign/README.md"
git -C "$foreign" add README.md
git -C "$foreign" commit -m foreign >/dev/null

if HOME="$home" DOTFILES_WORKTREES_ROOT="$worktrees_root" zsh -f -c '
  source "$1"
  cd "$2"
  gwt rm "$3"
' zsh "$root/functions/gwt/gwt.zsh" "$repo" "$foreign" >"$temporary/rm.out" 2>&1; then
  print -u2 'expected foreign absolute path removal to fail'
  exit 1
fi
grep -Fq 'refusing path not registered to this repository' "$temporary/rm.out"

existing_path="$worktrees_root/owner-repo/existing-branch"
mkdir -p "${existing_path:h}"
git clone -q "$foreign" "$existing_path"
if HOME="$home" DOTFILES_WORKTREES_ROOT="$worktrees_root" zsh -f -c '
  source "$1"
  cd "$2"
  gwt new existing/branch main
' zsh "$root/functions/gwt/gwt.zsh" "$repo" >"$temporary/new.out" 2>&1; then
  print -u2 'expected foreign existing path reuse to fail'
  exit 1
fi
grep -Fq 'refusing existing path not registered to this repository' "$temporary/new.out"

cat >"$runtime/agent-worktree-maintain" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >"$TEST_MAINTAIN_ARGS"
EOF
chmod +x "$runtime/agent-worktree-maintain"

HOME="$home" \
DOTFILES_WORKTREES_ROOT="$worktrees_root" \
TEST_MAINTAIN_ARGS="$temporary/maintain.args" \
zsh -f -c '
  source "$1"
  cd "$2"
  gwt clean --min-age-days 7
' zsh "$root/functions/gwt/gwt.zsh" "$repo" >"$temporary/clean.out"

grep -Fq 'running immediate forced maintenance' "$temporary/clean.out"
grep -Fq -- '--force --min-age-days 7' "$temporary/maintain.args"
grep -Fq -- "--repo ${repo:A}" "$temporary/maintain.args"

print 'gwt cleanup tests passed'
