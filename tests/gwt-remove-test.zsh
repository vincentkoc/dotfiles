#!/usr/bin/env zsh
set -euo pipefail
umask 077

root="${0:A:h:h}"
fixture="$(mktemp -d "${TMPDIR:-/tmp}/gwt-remove.XXXXXX")"
fixture="${fixture:A}"
trap 'cd /; /bin/rm -rf -- "$fixture"' EXIT
export HOME="$fixture/home"
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export GIT_TEMPLATE_DIR="$fixture/templates"
export DOTFILES_WORKTREES_ROOT="$fixture/worktrees"
mkdir -p "$HOME" "$GIT_TEMPLATE_DIR" "$DOTFILES_WORKTREES_ROOT"
unset DOTFILES_GWT_LOADED
source "$root/functions/gwt/gwt.zsh" || exit 1
[[ "$functions_source[gwt]" == "$root/functions/gwt/gwt.zsh" ]] || exit 1
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

# Log the mutation boundary while leaving native Git behavior intact.
git() {
  if [[ "${1:-}" == worktree && "${2:-}" == remove ]]; then
    print -r -- "$*" >>"$fixture/removals"
  fi
  command git "$@"
}

repo="$fixture/repo"
git init -q -b main "$repo"
git -C "$repo" config user.name 'Removal Test'
git -C "$repo" config user.email 'removal@example.test'
printf 'node_modules\n.cache\n' >"$repo/.gitignore"
git -C "$repo" add .gitignore
git -C "$repo" commit -qm fixture
cd "$repo"

ignored="$DOTFILES_WORKTREES_ROOT/ignored"
git worktree add -qb ignored "$ignored" main
mkdir "$ignored/node_modules" "$ignored/.cache"
print -r -- dependency >"$ignored/node_modules/fixture"
print -r -- cache >"$ignored/.cache/fixture"
check 'ignored task dependencies permit native removal' gwt rm "$ignored"
check 'ignored dependency tree removed with worktree' test ! -e "$ignored"
check 'first removal was nonforced' test "$(cat "$fixture/removals")" = "worktree remove $ignored"

shared="$fixture/shared-install"
linked="$DOTFILES_WORKTREES_ROOT/linked"
mkdir "$shared"
print -r -- protected >"$shared/marker"
git worktree add -qb linked "$linked" main
ln -s "$shared" "$linked/node_modules"
check 'ignored dependency symlink permits removal' gwt rm "$linked"
check 'linked worktree removed' test ! -e "$linked"
check 'external dependency directory retained' test -d "$shared"
check 'external dependency contents retained' test "$(cat "$shared/marker")" = protected

untracked="$DOTFILES_WORKTREES_ROOT/untracked"
git worktree add -qb untracked "$untracked" main
git config status.showUntrackedFiles no
mkdir "$untracked/notes"
print -r -- protected >"$untracked/notes/untracked.txt"
check 'native configured status hides untracked file' test -z "$(git -C "$untracked" status --porcelain=v1)"
before="$(cat "$fixture/removals")"
check 'wrapper refuses suppressed untracked file' reject gwt rm "$untracked"
check 'untracked refusal happens before native removal' test "$(cat "$fixture/removals")" = "$before"
check 'untracked content retained' test "$(cat "$untracked/notes/untracked.txt")" = protected
check 'untracked refusal reports outcome' grep -Fq 'uncommitted changes' "$fixture/rejected.out"

unreadable="$DOTFILES_WORKTREES_ROOT/status-error"
git worktree add -qb status-error "$unreadable" main
(
  _gwt_git_probe() {
    if [[ "${3:-}" == status ]]; then
      printf '%s\n' "$@" >"$fixture/status-args"
      return 42
    fi
    GIT_OPTIONAL_LOCKS=0 GIT_NO_LAZY_FETCH=1 command git "$@"
  }
  reject gwt rm "$unreadable"
)
check 'status error happens before native removal' test "$(cat "$fixture/removals")" = "$before"
check 'status error preserves worktree' test -f "$unreadable/.git"
check 'status error reports refusal' grep -Fq 'cannot verify worktree status' "$fixture/rejected.out"
check 'status requests porcelain v1' grep -Fxq -- '--porcelain=v1' "$fixture/status-args"
check 'status includes all untracked files' grep -Fxq -- '--untracked-files=all' "$fixture/status-args"
check 'status includes submodule changes' grep -Fxq -- '--ignore-submodules=none' "$fixture/status-args"
git worktree list --porcelain >"$fixture/registrations"
check 'status error preserves registration' grep -Fxq "worktree $unreadable" "$fixture/registrations"
check 'untracked refusal preserves registration' grep -Fxq "worktree $untracked" "$fixture/registrations"

size_kib="$(du -sk "$fixture" | awk '{print $1}')"
check 'fixture remains within 4 MiB' test "$size_kib" -le 4096
print -r -- "gwt removal tests passed ($checks checks; $size_kib KiB fixture)"
