#!/usr/bin/env zsh
set -euo pipefail
umask 077

root="${0:A:h:h}"
fixture="$(mktemp -d "${TMPDIR:-/tmp}/gwt-bootstrap.XXXXXX")"
fixture="${fixture:A}"
trap 'cd /; /bin/rm -rf -- "$fixture"' EXIT
export HOME="$fixture/home"
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export GIT_TEMPLATE_DIR="$fixture/templates"
mkdir -p "$HOME" "$GIT_TEMPLATE_DIR"
source "$root/functions/gwt/gwt.zsh" || exit 1
_gwt_tmux_sync_context() { return 0; }
typeset -i checks=0

check() {
  local name="$1"
  shift
  if ! "$@"; then
    print -u2 -- "FAIL: $name"
    exit 1
  fi
  (( ++checks ))
}

reject() {
  if "$@" >"$fixture/rejected.out" 2>&1; then
    print -u2 -- "unexpected success: $*"
    return 1
  fi
}

budget() {
  local kib
  kib="$(du -sk "$fixture")"
  kib="${kib%%[[:space:]]*}"
  (( kib <= 4096 ))
}

source_root="$fixture/source"
export DOTFILES_GWT_LINK_DEEP_NODE_MODULES=1
export DOTFILES_WORKTREES_ROOT="$source_root/managed"
mkdir -p "$source_root/node_modules" "$source_root/packages/one/node_modules" \
  "$source_root/packages/with space/node_modules" "$source_root/nested/.git" \
  "$source_root/nested/node_modules" "$source_root/linked/node_modules" \
  "$source_root/.worktrees/hidden/node_modules" \
  "$source_root/managed/hidden/node_modules" \
  "$source_root/.codex/worktrees/hidden/node_modules" \
  "$source_root/.claude/worktrees/hidden/node_modules"
print -r -- 'gitdir: fixture' >"$source_root/linked/.git"
discovered="$(_gwt_find_workspace_node_modules "$source_root")"
expected="$(printf '%s\n' "$source_root/node_modules" \
  "$source_root/packages/one/node_modules" "$source_root/packages/with space/node_modules" | sort)"
check 'discovery excludes repository and managed roots' test \
  "$(printf '%s\n' "$discovered" | sort)" = "$expected"

target="$fixture/target"
mkdir "$target"
check 'shared links created' _gwt_link_shared_node_modules "$source_root" "$target"
check 'matching links validate' _gwt_link_shared_node_modules "$source_root" "$target" validate
check 'workspace containing spaces linked' test \
  "$(readlink "$target/packages/with space/node_modules")" = "$source_root/packages/with space/node_modules"

mkdir "$fixture/foreign" "$fixture/foreign-target" "$fixture/dangling-target"
ln -s "$fixture/foreign" "$fixture/foreign-target/node_modules"
ln -s "$fixture/missing" "$fixture/dangling-target/node_modules"
check 'foreign link rejected' reject _gwt_link_shared_node_modules "$source_root" "$fixture/foreign-target"
check 'foreign link preserved' test "$(readlink "$fixture/foreign-target/node_modules")" = "$fixture/foreign"
check 'dangling link rejected' reject _gwt_link_shared_node_modules "$source_root" "$fixture/dangling-target"
check 'dangling link preserved' test "$(readlink "$fixture/dangling-target/node_modules")" = "$fixture/missing"

mkdir "$fixture/escape-target"
ln -s "$fixture/foreign" "$fixture/escape-target/packages"
check 'escaping parent rejected' reject _gwt_link_shared_node_modules "$source_root" "$fixture/escape-target"
check 'escaping parent untouched' test ! -e "$fixture/foreign/one"

mkdir "$fixture/discovery-error"
(
  _gwt_find_workspace_node_modules() {
    print -r -- "$1/node_modules"
    return 1
  }
  reject _gwt_link_shared_node_modules "$source_root" "$fixture/discovery-error"
)
check 'discovery error creates no links' test ! -e "$fixture/discovery-error/node_modules"
check 'fixture budget after helper cases' budget

# Every Git operation below belongs to this freshly initialized, private fixture.
repo="$fixture/repo"
git init -q -b main "$repo"
git -C "$repo" config user.name 'Bootstrap Test'
git -C "$repo" config user.email 'bootstrap@example.test'
print -r -- 'lockfileVersion: fixture' >"$repo/pnpm-lock.yaml"
print -r -- 'node_modules/' >"$repo/.gitignore"
git -C "$repo" add pnpm-lock.yaml .gitignore
git -C "$repo" commit -qm fixture
mkdir "$repo/node_modules"
export DOTFILES_GWT_LINK_DEEP_NODE_MODULES=0
export DOTFILES_WORKTREES_ROOT="$fixture/worktrees"
cd "$repo"
slug="$(_gwt_repo_slug)"

gwt new shared main --full >"$fixture/shared.out" 2>&1
shared="$PWD"
check 'new worktree shares selected install' test "$(readlink "$shared/node_modules")" = "$repo/node_modules"
cd "$repo"
check 'existing shared worktree reuses valid links' gwt new shared main --full
check 'reuse enters existing worktree' test "$PWD" = "$shared"
cd "$repo"
/bin/rm "$shared/node_modules"
ln -s "$fixture/foreign" "$shared/node_modules"
check 'reuse rejects foreign root link' reject gwt new shared main --full
check 'rejected reuse leaves current directory' test "$PWD" = "$repo"
check 'rejected reuse preserves link' test "$(readlink "$shared/node_modules")" = "$fixture/foreign"

gwt new owned main --full >"$fixture/owned.out" 2>&1
owned="$PWD"
/bin/rm "$owned/node_modules"
mkdir "$owned/node_modules"
print -r -- owned >"$owned/node_modules/marker"
cd "$repo"
check 'reuse accepts owned install' gwt new owned main --full
check 'owned install remains a directory' test ! -L "$owned/node_modules"
check 'owned install content preserved' test "$( <"$owned/node_modules/marker")" = owned

cd "$repo"
gwt new code-only main --full >"$fixture/code-only.out" 2>&1
code_only="$PWD"
/bin/rm "$code_only/node_modules"
cd "$repo"
gwt new code-only main --full >"$fixture/code-only-reuse.out" 2>&1
check 'code-only reuse does not repair dependencies' test ! -e "$code_only/node_modules"
check 'code-only reuse reports outcome' grep -Fq 'reusing code-only worktree' "$fixture/code-only-reuse.out"

cd "$repo"
(
  _gwt_bootstrap_worktree() { return 1; }
  reject gwt new failed main --full
)
failed="$DOTFILES_WORKTREES_ROOT/$slug/failed"
check 'failed bootstrap preserves checkout' test -f "$failed/.git"
check 'failed bootstrap preserves branch' git show-ref --verify --quiet refs/heads/failed
git worktree list --porcelain >"$fixture/registrations.out"
check 'failed bootstrap preserves registration' grep -Fxq "worktree $failed" "$fixture/registrations.out"
check 'failed bootstrap reports preserved path' grep -Fq \
  "preserved worktree and registration at $failed" "$fixture/rejected.out"

(
  _gwt_shared_install_source() { return 1; }
  _gwt_bootstrap_worktree "$code_only" >"$fixture/missing-install.out" 2>&1
)
check 'missing install reports code-only outcome' grep -Fq \
  'created a code-only worktree' "$fixture/missing-install.out"
check 'fixture stays within 4 MiB' budget
print -r -- "gwt bootstrap tests passed ($checks checks; $(du -sk "$fixture" | awk '{print $1}') KiB fixture)"
