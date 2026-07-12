#!/usr/bin/env bash
set -euo pipefail
script="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/functions/system/deepclean.zsh"
zsh -n "$script"
grep -q 'repository disappeared during cleanup; skipping' "$script"
grep -q 'purge_status != 0 && purge_status != 2' "$script"
grep -q 'The default is --dry-run' "$script"
grep -q 'does not kill Codex' "$script"
echo deepclean_test=passed
