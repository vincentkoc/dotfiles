param(
    [string]$Repo = "$HOME\GIT\_Perso\dotfiles"
)
$ErrorActionPreference = 'Stop'
$source = Join-Path $Repo 'windows\Microsoft.PowerShell_profile.ps1'
if (!(Test-Path $source)) { throw "dotfiles PowerShell profile missing: $source" }
$profileDir = Split-Path -Parent $PROFILE
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
if ((Test-Path $PROFILE) -and !(Select-String -Quiet -Path $PROFILE -SimpleMatch '# >>> vincent-dotfiles >>>')) {
    Copy-Item $PROFILE "$PROFILE.pre-dotfiles.$(Get-Date -Format yyyyMMddHHmmss).bak"
}
$block = @"
# >>> vincent-dotfiles >>>
. '$source'
# <<< vincent-dotfiles <<<
"@
$current = if (Test-Path $PROFILE) { Get-Content -Raw $PROFILE } else { '' }
$current = [regex]::Replace($current, '(?s)# >>> vincent-dotfiles >>>.*?# <<< vincent-dotfiles <<<\r?\n?', '')
Set-Content -Path $PROFILE -Value ($current.TrimEnd() + "`r`n`r`n" + $block)
Write-Output "powershell_profile=ready"
Write-Output "wsl_distro=$((wsl.exe -l -q | Select-Object -First 1).Trim())"
