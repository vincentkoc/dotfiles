param(
    [ValidateSet('Check', 'Apply')]
    [string]$Mode = 'Check',
    [string]$Repo = "$HOME\GIT\_Perso\dotfiles",
    [string]$Bundle,
    [switch]$InstallApp,
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
$sourceUser = Join-Path $Repo 'Library\Application Support\Sublime Text\Packages\User'
$support = Join-Path $env:APPDATA 'Sublime Text'
$liveUser = Join-Path $support 'Packages\User'
$installedDir = Join-Path $support 'Installed Packages'
$packagesDir = Join-Path $support 'Packages'
$appCandidates = @(
    "$env:ProgramFiles\Sublime Text\sublime_text.exe",
    "$env:LOCALAPPDATA\Programs\Sublime Text\sublime_text.exe"
)

function Get-SublimeApp {
    $appCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

function Get-DesiredPackages {
    $text = Get-Content -Raw (Join-Path $sourceUser 'Package Control.sublime-settings')
    $match = [regex]::Match($text, '"installed_packages"\s*:\s*\[(.*?)\]', 'Singleline')
    [regex]::Matches($match.Groups[1].Value, '"([^"\\]*(?:\\.[^"\\]*)*)"') |
        ForEach-Object { $_.Groups[1].Value } |
        Sort-Object -Unique
}

function Get-InstalledPackages {
    $names = @()
    if (Test-Path $installedDir) {
        $names += Get-ChildItem $installedDir -Filter '*.sublime-package' -File |
            ForEach-Object { $_.BaseName }
    }
    if (Test-Path $packagesDir) {
        $names += Get-ChildItem $packagesDir -Directory |
            Where-Object Name -ne 'User' |
            ForEach-Object { if ($_.Name -eq 'zzz A File Icon') { 'A File Icon' } else { $_.Name } }
    }
    $names | Sort-Object -Unique
}

function Test-SublimeState {
    $failures = 0
    if (Get-SublimeApp) { Write-Host 'Sublime Text             ready' } else { Write-Host 'Sublime Text             missing'; $failures++ }
    if ((Test-Path $liveUser) -and (Test-Path (Join-Path $liveUser 'Preferences.sublime-settings'))) {
        $sourceHash = (Get-FileHash (Join-Path $sourceUser 'Preferences.sublime-settings') -Algorithm SHA256).Hash
        $liveHash = (Get-FileHash (Join-Path $liveUser 'Preferences.sublime-settings') -Algorithm SHA256).Hash
        if ($sourceHash -eq $liveHash) { Write-Host 'User configuration        ready' } else { Write-Host 'User configuration        drift'; $failures++ }
    } else { Write-Host 'User configuration        drift'; $failures++ }
    $desired = @(Get-DesiredPackages)
    $actual = @(Get-InstalledPackages)
    if (-not (Compare-Object $desired $actual)) { Write-Host "Package Control plugins  ready count=$($desired.Count)" }
    else { Write-Host 'Package Control plugins  drift'; $failures++ }
    return $failures
}

if ($Mode -eq 'Check') {
    exit [int](Test-SublimeState)
}

if (!(Test-Path $sourceUser)) { throw "missing tracked Sublime User source: $sourceUser" }
if (!(Test-Path $Bundle)) { throw "missing plugin bundle: $Bundle" }
$entries = tar.exe -tzf $Bundle
foreach ($entry in $entries) {
    $parts = $entry -split '/'
    if ($entry.StartsWith('/') -or $parts -contains '..' -or $parts[0] -notin @('Installed Packages', 'Packages')) {
        throw "unsafe or unexpected bundle path: $entry"
    }
}
$app = Get-SublimeApp
if (!$app -and $InstallApp) {
    winget install --id SublimeHQ.SublimeText.4 --exact --silent --accept-package-agreements --accept-source-agreements
    $app = Get-SublimeApp
}
if (!$app) { throw 'Sublime Text is missing; rerun with -InstallApp' }

$processes = @(Get-Process sublime_text -ErrorAction SilentlyContinue)
foreach ($process in $processes) { [void]$process.CloseMainWindow() }
if ($processes) {
    Wait-Process -Id $processes.Id -Timeout 10 -ErrorAction SilentlyContinue
    if (Get-Process sublime_text -ErrorAction SilentlyContinue) { throw 'Sublime Text did not quit cleanly; refusing to force-kill it' }
}

$timestamp = Get-Date -Format yyyyMMdd-HHmmss
$backup = Join-Path $support "Backups\sublime-sync-$timestamp"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
foreach ($path in @((Join-Path $support 'Local\Session.sublime_session'), (Join-Path $support 'Local\Auto Save Session.sublime_session'), $liveUser, $installedDir)) {
    if (Test-Path $path) { Copy-Item -Recurse -Force $path $backup }
}

Remove-Item -Recurse -Force $liveUser -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $liveUser | Out-Null
Copy-Item -Recurse -Force (Join-Path $sourceUser '*') $liveUser

$temporary = Join-Path $env:TEMP "sublime-sync-$timestamp"
Remove-Item -Recurse -Force $temporary -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $temporary | Out-Null
tar.exe -xzf $Bundle -C $temporary
Remove-Item -Recurse -Force $installedDir -ErrorAction SilentlyContinue
Copy-Item -Recurse -Force (Join-Path $temporary 'Installed Packages') $installedDir
Get-ChildItem $packagesDir -Directory -ErrorAction SilentlyContinue | Where-Object Name -ne 'User' | Remove-Item -Recurse -Force
Get-ChildItem (Join-Path $temporary 'Packages') -Directory -ErrorAction SilentlyContinue | Copy-Item -Destination $packagesDir -Recurse -Force
Remove-Item -Recurse -Force $temporary

if ($processes -or $Restart) {
    Start-Process $app
    Start-Sleep -Seconds 10
}

"backup=$backup"
exit [int](Test-SublimeState)
