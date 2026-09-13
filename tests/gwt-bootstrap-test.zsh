#!/usr/bin/env zsh
set -euo pipefail
umask 077

root="${0:A:h:h}"
fixture="$(mktemp -d "${TMPDIR:-/tmp}/gwt-bootstrap.XXXXXX")"
fixture="${fixture:A}"
exec {report_fd}>&1
trap 'result=$?; if (( result )); then print -r -- "fixture failed (exit $result): $fixture" >&$report_fd; for output in "$fixture"/*.out(N); do print -r -- "${output:t}:" >&$report_fd; cat "$output" >&$report_fd; done; fi; cd /; /bin/rm -rf -- "$fixture"; print -r -- "fixture removed: $fixture" >&$report_fd' EXIT
export HOME="$fixture/home"
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export GIT_TEMPLATE_DIR="$fixture/templates"
mkdir -p "$HOME" "$GIT_TEMPLATE_DIR"
# The fixture must load this candidate even if startup selected an installed module.
unset DOTFILES_GWT_LOADED
source "$root/functions/gwt/gwt.zsh" || exit 1
[[ "$functions_source[gwt]" == "$root/functions/gwt/gwt.zsh" ]] || exit 1
print -r -- "fixture candidate: $functions_source[gwt]; fixture: $fixture" >&$report_fd
_gwt_tmux_sync_context() { return 0; }
_gwt_refresh_remote_ref() { print -u2 'unexpected fetch'; return 1; }
# Binding tests isolate identity checks; storage tests exercise the compatibility gate.
_gwt_dependency_compatible() { return 0; }
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
check 'owned install content preserved' test "$(cat "$owned/node_modules/marker")" = owned

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

# Explicit selectors never adopt or repair existing dependency entries.
donor="$fixture/approved donor"
mkdir -p "$donor/node_modules" "$donor/packages/deep/node_modules"
print -r -- donor >"$donor/node_modules/marker"
for verb in new add; do
  cd "$repo"
  selected_branch="selected-$verb"
  gwt "$verb" "$selected_branch" main --full --dependency-source "$donor" >"$fixture/selected-$verb.out" 2>&1
  selected="$PWD"
  check "$verb reports consumer identity before binding" python3 - "$selected" "$fixture/selected-$verb.out" <<'PY'
import json, os, sys
consumer, report = sys.argv[1:]
details = os.lstat(consumer)
identity = json.dumps([consumer, details.st_dev, details.st_ino], separators=(",", ":"))
with open(report) as stream:
    output = stream.read()
assert output.index("(identity " + identity + "; HEAD ") < output.index("gwt: created explicit node_modules link")
PY
  check "$verb selects donor before canonical bootstrap" test "$(readlink "$selected/node_modules")" = "$donor/node_modules"
  check "$verb does not deep link" test ! -e "$selected/packages"
  before_link="$(ls -di "$selected/node_modules")"
  cd "$repo"
  check "$verb explicit matching reuse" gwt "$verb" "$selected_branch" main --full --dependency-source "$donor"
  check "$verb matching reuse enters worktree" test "$PWD" = "$selected"
  check "$verb matching reuse preserves link inode" test "$(ls -di "$selected/node_modules")" = "$before_link"
  cd "$repo"
  check "$verb omission refuses donor link" reject gwt "$verb" "$selected_branch" main --full
  check "$verb omission preserves cwd" test "$PWD" = "$repo"
  for kind in missing owned dangling foreign; do
    /bin/rm -- "$selected/node_modules"
    case "$kind" in
      missing) ;;
      owned) mkdir "$selected/node_modules"; print marker >"$selected/node_modules/marker" ;;
      dangling) ln -s "$fixture/absent" "$selected/node_modules" ;;
      foreign) ln -s "$fixture/foreign" "$selected/node_modules" ;;
    esac
    before="$(ls -ldi "$selected/node_modules" 2>/dev/null || print absent)"
    check "$verb refuses explicit $kind reuse" reject gwt "$verb" "$selected_branch" main --full --dependency-source "$donor"
    check "$verb $kind refusal preserves cwd" test "$PWD" = "$repo"
    check "$verb $kind refusal preserves entry" test "$(ls -ldi "$selected/node_modules" 2>/dev/null || print absent)" = "$before"
    if [[ "$kind" == owned ]]; then
      check "$verb preserves owned content" test "$(cat "$selected/node_modules/marker")" = marker
      /bin/rm "$selected/node_modules/marker"
      rmdir "$selected/node_modules"
    elif [[ "$kind" != missing ]]; then
      /bin/rm "$selected/node_modules"
    fi
    ln -s "$donor/node_modules" "$selected/node_modules"
  done
  check "$verb rejects a different donor" reject gwt "$verb" "$selected_branch" main --full --dependency-source "$source_root"
  for invalid in missing empty relative duplicate equals unusable deep; do
    bad_branch="invalid-$verb-$invalid"
    case "$invalid" in
      missing) args=(--dependency-source) ;;
      empty) args=(--dependency-source '') ;;
      relative) args=(--dependency-source relative) ;;
      duplicate) args=(--dependency-source "$donor" --dependency-source "$donor") ;;
      equals) args=("--dependency-source=$donor") ;;
      unusable) args=(--dependency-source "$fixture/no-install") ;;
      deep) args=(--dependency-source "$donor") ;;
    esac
    if [[ "$invalid" == deep ]]; then
      ( export DOTFILES_GWT_LINK_DEEP_NODE_MODULES=1; reject gwt "$verb" "$bad_branch" main --full "${args[@]}" )
      (( ++checks ))
    else
      check "$verb rejects $invalid selector" reject gwt "$verb" "$bad_branch" main --full "${args[@]}"
    fi
    check "$verb $invalid creates no branch" reject git show-ref --verify --quiet "refs/heads/$bad_branch"
    check "$verb $invalid creates no path" test ! -e "$DOTFILES_WORKTREES_ROOT/$slug/$bad_branch"
    check "$verb $invalid preserves cwd" test "$PWD" = "$repo"
  done
done
cd "$repo"
check 'add preserves default owned directory' gwt add owned main --full
check 'default owned marker survives' test "$(cat "$owned/node_modules/marker")" = owned
cd "$repo"
check 'add preserves code-only reuse' gwt add code-only main --full
check 'add does not create missing dependencies' test ! -e "$code_only/node_modules"
cd "$repo"

# Reject a target-parent escape before any branch or child is created.
(
  export DOTFILES_WORKTREES_ROOT="$fixture/escaped-worktrees"
  mkdir "$DOTFILES_WORKTREES_ROOT"
  ln -s "$fixture/foreign" "$DOTFILES_WORKTREES_ROOT/$slug"
  reject gwt new escaped main --full --dependency-source "$donor"
)
check 'escaped target creates no branch' reject git show-ref --verify --quiet refs/heads/escaped
check 'escaped target creates no child' test ! -e "$fixture/foreign/escaped"
(
  export DOTFILES_WORKTREES_ROOT="$donor"
  reject gwt add overlap main --full --dependency-source "$donor"
)
check 'overlap creates no branch' reject git show-ref --verify --quiet refs/heads/overlap

# Change the donor identity after native checkout/profile, before binding.
(
  functions[_gwt_original_profile]="$functions[_gwt_sparse_apply_default_profile]"
  _gwt_sparse_apply_default_profile() {
    _gwt_original_profile "$@" || return
    mv "$donor/node_modules" "$donor/saved-node_modules"
    mkdir "$donor/node_modules"
  }
  reject gwt new drift main --full --dependency-source "$donor"
)
check 'changed donor refuses link' test ! -e "$DOTFILES_WORKTREES_ROOT/$slug/drift/node_modules"
check 'identity failure retains checkout' test -f "$DOTFILES_WORKTREES_ROOT/$slug/drift/.git"
rmdir "$donor/node_modules"
mv "$donor/saved-node_modules" "$donor/node_modules"

# Owner metadata is also pinned while native checkout/profile work runs.
for owner_entry in node_modules .git; do
  drift_branch="owner-${owner_entry#.}"
  (
    functions[_gwt_original_profile]="$functions[_gwt_sparse_apply_default_profile]"
    _gwt_sparse_apply_default_profile() {
      _gwt_original_profile "$@" || return
      mv "$repo/$owner_entry" "$repo/saved-owner-entry"
      cp -R "$repo/saved-owner-entry" "$repo/$owner_entry"
    }
    reject gwt new "$drift_branch" main --full --dependency-source "$donor"
  )
  /bin/rm -rf -- "$repo/$owner_entry"
  mv "$repo/saved-owner-entry" "$repo/$owner_entry"
  drift_target="$DOTFILES_WORKTREES_ROOT/$slug/$drift_branch"
  check "changed owner $owner_entry refuses link" test ! -e "$drift_target/node_modules"
  check "changed owner $owner_entry retains checkout" test -f "$drift_target/.git"
  check "changed owner $owner_entry retains branch" git show-ref --verify --quiet "refs/heads/$drift_branch"
  check "changed owner $owner_entry reports identity drift" grep -Fq 'owner common directory or node_modules entry changed' "$fixture/rejected.out"
done

# Race a real directory at symlinkat itself. It must not receive a nested link.
python_bin="$(command -v python3)"
for dependency_mode in explicit automatic; do
(
  python3() {
    if [[ "${1:-}" == - && "${2:-}" == link ]]; then
      local program="$(cat)"
      "$python_bin" -c '
import os,sys
original=os.symlink
def race(src,dst,**kwargs):
    os.mkdir(dst,dir_fd=kwargs["dir_fd"])
    return original(src,dst,**kwargs)
os.symlink=race
exec(sys.stdin.read())
' "${@:2}" <<<"$program"
    else
      "$python_bin" "$@"
    fi
  }
  if [[ "$dependency_mode" == explicit ]]; then
    reject gwt add raced-explicit main --full --dependency-source "$donor"
  else
    reject gwt add raced-automatic main --full
  fi
)
raced="$DOTFILES_WORKTREES_ROOT/$slug/raced-$dependency_mode"
check 'concurrent directory is preserved' test -d "$raced/node_modules"
check 'concurrent directory is not a link' test ! -L "$raced/node_modules"
check 'concurrent directory receives no nested link' test ! -e "$raced/node_modules/node_modules"
check 'concurrent failure retains branch' git show-ref --verify --quiet "refs/heads/raced-$dependency_mode"
check 'concurrent failure retains registration' _gwt_registered_worktree_path "$repo" "$raced"
check 'concurrent failure reports retention' grep -Fq 'preserved worktree and registration' "$fixture/rejected.out"

done

plain="$fixture/plain"
git init -q -b main "$plain"
git -C "$plain" config user.name 'Bootstrap Test'
git -C "$plain" config user.email 'bootstrap@example.test'
print plain >"$plain/README"
git -C "$plain" add README
git -C "$plain" commit -qm fixture
(
  cd "$plain"
  reject gwt new plain main --full --dependency-source "$donor"
)
check 'non-pnpm refusal is visible' grep -Fq 'requires a pnpm worktree' "$fixture/rejected.out"
check 'donor content unchanged' test "$(cat "$donor/node_modules/marker")" = donor

check 'fixture stays within 4 MiB' budget
print -r -- "gwt bootstrap tests passed ($checks checks; $(du -sk "$fixture" | awk '{print $1}') KiB fixture)"
