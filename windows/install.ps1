param(
    [string]$Repo = "$HOME\GIT\_Perso\dotfiles"
)
$ErrorActionPreference = 'Stop'
$source = Join-Path $Repo 'windows\Microsoft.PowerShell_profile.ps1'
if (!(Test-Path $source)) { throw "dotfiles PowerShell profile missing: $source" }
$block = @"
# >>> vincent-dotfiles >>>
. '$source'
# <<< vincent-dotfiles <<<
"@
$profiles = @(
    (Join-Path $HOME 'Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1'),
    (Join-Path $HOME 'Documents\PowerShell\Microsoft.PowerShell_profile.ps1')
)
foreach ($profilePath in $profiles) {
    $profileDir = Split-Path -Parent $profilePath
    New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
    if ((Test-Path $profilePath) -and !(Select-String -Quiet -Path $profilePath -SimpleMatch '# >>> vincent-dotfiles >>>')) {
        Copy-Item $profilePath "$profilePath.pre-dotfiles.$(Get-Date -Format yyyyMMddHHmmss).bak"
    }
    $current = if (Test-Path $profilePath) { Get-Content -Raw $profilePath } else { '' }
    $current = [regex]::Replace($current, '(?s)# >>> vincent-dotfiles >>>.*?# <<< vincent-dotfiles <<<\r?\n?', '')
    Set-Content -Path $profilePath -Value ($current.TrimEnd() + "`r`n`r`n" + $block)
    Write-Output "powershell_profile=$profilePath"
}
$distro = if ($env:DOTFILES_WSL_DISTRO) { $env:DOTFILES_WSL_DISTRO } else { 'Ubuntu' }
Write-Output "wsl_distro=$distro"
