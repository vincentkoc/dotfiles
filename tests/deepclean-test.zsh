#!/usr/bin/env zsh
set -euo pipefail

repo_root="${0:A:h:h}"
source "$repo_root/functions/system/deepclean.zsh"

temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
mkdir "$temporary/repo"
calls="$temporary/calls"
output="$temporary/output"
storage_result=0
maintenance_result=0
audit_result=0
clean_result=0
purge_result=0

# Every cleanup boundary is a stub, including the successful paths.
deepclean_test_storage() { return "$storage_result"; }
WORKTREE_STORAGE_GUARD=deepclean_test_storage
agent-worktree-maintain() {
    print -r -- "maintain $*" >>"$calls"
    return "$maintenance_result"
}
agent-worktree-clean() {
    print -r -- "audit $*" >>"$calls"
    return "$audit_result"
}
mole() {
    print -r -- "mole worktrees=${MOLE_AGENT_WORKTREES:-unset} $*" >>"$calls"
    if [[ "$1" == clean ]]; then
        return "$clean_result"
    fi
    return "$purge_result"
}

run_case() {
    local expected="$1" actual=0
    shift
    : >"$calls"
    deepclean "$@" >"$output" 2>&1 || actual=$?
    if (( actual != expected )); then
        cat "$output" >&2
        print -u2 -- "expected exit $expected, got $actual: $*"
        exit 1
    fi
}

assert_no_mole() { ! grep -q '^mole ' "$calls"; }
assert_incomplete() { ! grep -q 'deepclean complete' "$output"; }

if [[ "${1:-}" == --errexit-purge ]]; then
    purge_result=2
    deepclean --apply --repo "$temporary/repo" >"$output" 2>&1
    grep -q 'deepclean complete' "$output"
    exit 0
fi

run_case 0 --repo "$temporary/repo"
grep -q '^audit ' "$calls"
! grep -q '^maintain ' "$calls"
grep -q 'deepclean mode=preview' "$output"
grep -q 'deepclean complete' "$output"
if [[ "$OSTYPE" == darwin* ]]; then
    grep -qx 'mole worktrees=0 clean --dry-run' "$calls"
    grep -qx 'mole worktrees=0 purge --dry-run' "$calls"
fi

storage_result=78 run_case 78 --apply --repo "$temporary/repo"
[[ ! -s "$calls" ]]
assert_incomplete

maintenance_result=17 run_case 17 --apply --repo "$temporary/repo"
grep -q '^maintain ' "$calls"
assert_no_mole
assert_incomplete

audit_result=23 run_case 1 --dry-run --repo "$temporary/repo"
grep -q '^audit ' "$calls"
assert_no_mole
assert_incomplete

run_case 1 --apply --repo "$temporary/missing"
[[ ! -s "$calls" ]]
assert_incomplete
run_case 1 --dry-run --repo "$temporary/missing"
assert_no_mole
assert_incomplete

export MOLE_AGENT_WORKTREES=1
run_case 0 --apply --repo "$temporary/repo"
[[ "$MOLE_AGENT_WORKTREES" == 1 ]]
grep -q '^maintain ' "$calls"
if [[ "$OSTYPE" == darwin* ]]; then
    grep -qx 'mole worktrees=0 clean' "$calls"
    grep -qx 'mole worktrees=0 purge' "$calls"
    # A fresh shell keeps ERR_EXIT active across the actual deepclean call.
    zsh "$0" --errexit-purge
    purge_result=2 run_case 0 --apply --repo "$temporary/repo"
    clean_result=31 run_case 31 --apply --repo "$temporary/repo"
    ! grep -q ' purge' "$calls"
    assert_incomplete
    purge_result=32 run_case 32 --apply --repo "$temporary/repo"
    assert_incomplete
fi

run_case 0 --apply --repo "$temporary/repo" --skip-mole
assert_no_mole
run_case 2 --wat
[[ ! -s "$calls" ]]

print 'deepclean_test=passed'
