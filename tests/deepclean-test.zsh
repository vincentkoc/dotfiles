#!/usr/bin/env zsh
set -euo pipefail

repo_root="${0:A:h:h}"
source "$repo_root/functions/system/deepclean.zsh"

output="$(deepclean --dry-run --repo "$repo_root" --skip-mole)"
[[ "$output" == *"deepclean mode=preview"* ]]
[[ "$output" == *"deepclean complete"* ]]

if deepclean --wat >/dev/null 2>&1; then
    print -u2 'expected unknown argument to fail'
    exit 1
fi

print 'deepclean_test=passed'
