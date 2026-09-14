#!/usr/bin/env bash
set -euo pipefail
script="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/functions/system/deepclean.zsh"
zsh -n "$script"
zsh "$(dirname "$script")/../../tests/deepclean-test.zsh"
