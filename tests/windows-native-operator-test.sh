#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
operator="$root/windows/native-operator.ps1"
profile="$root/windows/Microsoft.PowerShell_profile.ps1"

test -f "$operator"
grep -q "ValidateSet('Plan', 'Apply', 'Check', 'Rollback')" "$operator"
grep -q "Git.Git'; Version = '2.55.0.3'" "$operator"
grep -q "OpenJS.NodeJS.LTS'; Version = '24.19.0'" "$operator"
grep -q "Microsoft.PowerShell'; Version = '7.6.4'" "$operator"
grep -q "GitHub.cli'; Version = '2.97.0'" "$operator"
grep -q "'--architecture', 'arm64'" "$operator"
grep -q "profile-backups" "$operator"
grep -q "rollback.json" "$operator"
grep -q "Apply receipt cannot be resumed" "$operator"
grep -q "Get-AppxPackage -Name 'Microsoft.PowerShell'" "$operator"
grep -q "0xAA64" "$operator"
grep -q "transientAliasLock" "$operator"
! grep -q -- "--upgrade-all" "$operator"

grep -q "function tt" "$profile"
grep -q "function gwt" "$profile"
grep -q "explicit absolute WSL repository path" "$profile"
grep -q "DOTFILES_WSL_ARGV_B64/u" "$profile"
grep -q "DOTFILES_WSL_PROGRAM_B64/u" "$profile"
grep -q "ToBase64String" "$profile"
grep -q "WriteByte(0)" "$profile"
grep -q "base64 -d" "$profile"
grep -q 'dotfiles_argv=("\${dotfiles_argv\[@\]:1}")' "$profile"
grep -q 'program -replace.*`r' "$profile"
grep -q 'source <(printf %s "\$DOTFILES_WSL_PROGRAM_B64" | base64 -d)' "$profile"
grep -q 'zsh -lc' "$profile"
grep -q 'maxPayloadBytes = 24576' "$profile"
grep -q 'payload.Dispose()' "$profile"
grep -q 'global:LASTEXITCODE = \$exitCode' "$profile"
grep -q 'windowsProgramPayload' "$profile"
! grep -q 'Start-Process' "$profile"
! grep -q 'RedirectStandard' "$profile"
! grep -q 'eval ' "$profile"
! grep -Eq '\$[A-Za-z]+[[:space:]]*\|[[:space:]]*&[[:space:]]*\$wsl' "$profile"
grep -q "function wgit" "$profile"
grep -q "function wcx" "$profile"
! grep -Eq '^function (git|node|npm|npx|pwsh|gh|codex) ' "$profile"

temp="$(mktemp -d)"
trap 'rm -rf "$temp"' EXIT
cp "$profile" "$temp/profile-lf.ps1"
sed 's/$/\r/' "$profile" >"$temp/profile-crlf.ps1"
for variant in "$temp/profile-lf.ps1" "$temp/profile-crlf.ps1"; do
    grep -q 'DOTFILES_WSL_ARGV_B64/u' "$variant"
    grep -q 'DOTFILES_WSL_PROGRAM_B64/u' "$variant"
    grep -q 'program -replace.*`r' "$variant"
    grep -q 'zsh -lc' "$variant"
done

echo windows_native_operator_test=passed
