#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash -n "$repo/bin/terminal-sync"
grep -q 'font-meslo-lg-nerd-font' "$repo/bin/terminal-sync"
grep -q 'com.mitchellh.ghostty/config.ghostty' "$repo/bin/terminal-sync"
grep -q 'tmux source-file' "$repo/bin/terminal-sync"
grep -q 'TERMINAL_SYNC_DOTFILES_DIR' "$repo/bin/terminal-sync"
grep -q 'set font name of default settings' "$repo/bin/terminal-sync"
grep -q 'declared_font_casks' "$repo/bin/terminal-sync"
grep -q 'terminal font pack' "$repo/bin/terminal-sync"
printf 'terminal_sync_test=passed\n'
