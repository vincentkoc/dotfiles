#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash -n "$root/bin/dotfiles-platform"
bash -n "$root/bin/dotfiles-audit"
bash -n "$root/install.sh"
grep -q 'wdeepclean' "$root/windows/Microsoft.PowerShell_profile.ps1"
grep -q 'vincent-dotfiles' "$root/windows/install.ps1"
grep -q 'WSL2 is the canonical Unix development environment' "$root/README.md"
echo windows_wsl_test=passed
