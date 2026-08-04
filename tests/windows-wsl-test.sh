#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash -n "$root/bin/dotfiles-platform"
bash -n "$root/bin/dotfiles-audit"
bash -n "$root/install.sh"
grep -q 'wdeepclean' "$root/windows/Microsoft.PowerShell_profile.ps1"
grep -q 'function tt' "$root/windows/Microsoft.PowerShell_profile.ps1"
grep -q 'function gwt' "$root/windows/Microsoft.PowerShell_profile.ps1"
grep -q 'SystemRoot.*System32' "$root/windows/Microsoft.PowerShell_profile.ps1"
grep -q 'vincent-dotfiles' "$root/windows/install.ps1"
grep -q 'Documents\\PowerShell' "$root/windows/install.ps1"
grep -q 'WSL2 is the canonical Unix development environment' "$root/README.md"
"$root/tests/windows-native-operator-test.sh"
echo windows_wsl_test=passed
