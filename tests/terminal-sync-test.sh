#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash -n "$repo/bin/terminal-sync"
grep -q 'font-meslo-lg-nerd-font' "$repo/bin/terminal-sync"
grep -q 'com.mitchellh.ghostty/config.ghostty' "$repo/bin/terminal-sync"
grep -q 'check_absent.*HOME/.config/ghostty' "$repo/bin/terminal-sync"
grep -q 'tmux source-file' "$repo/bin/terminal-sync"
grep -q 'TERMINAL_SYNC_DOTFILES_DIR' "$repo/bin/terminal-sync"
grep -q 'set font name of default settings' "$repo/bin/terminal-sync"
grep -q 'declared_font_casks' "$repo/bin/terminal-sync"
grep -q 'terminal font pack' "$repo/bin/terminal-sync"
[[ "$(grep -c '^font-family = ' "$repo/.config/ghostty/config.ghostty")" -eq 2 ]]
grep -qx 'font-family = Monaco' "$repo/.config/ghostty/config.ghostty"
grep -qx 'font-family = MesloLGL Nerd Font Mono' "$repo/.config/ghostty/config.ghostty"
if grep -q '^font-family-' "$repo/.config/ghostty/config.ghostty"; then
    printf 'legacy Ghostty style-specific font override remains\n' >&2
    exit 1
fi
printf 'terminal_sync_test=passed\n'
