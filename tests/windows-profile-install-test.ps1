$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('profile-install-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null

function Write-TestFile([string]$Path, [string]$Content) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [IO.File]::WriteAllText($Path, $Content, [Text.UTF8Encoding]::new($false))
}

function Assert-True([bool]$Condition, [string]$Message) {
    if (!$Condition) { throw $Message }
}

function Read-TestBytes([string]$Path) {
    [Convert]::ToBase64String([IO.File]::ReadAllBytes($Path))
}

try {
    $fixtureRepo = Join-Path $testRoot "owner's dotfiles"
    $installerRoot = Join-Path $testRoot 'installer'
    $installer = Join-Path $installerRoot 'install.ps1'
    $profiles = @(
        (Join-Path $installerRoot 'profiles\WindowsPowerShell\Microsoft.PowerShell_profile.ps1'),
        (Join-Path $installerRoot 'profiles\PowerShell\Microsoft.PowerShell_profile.ps1')
    )
    New-Item -ItemType Directory -Path $installerRoot | Out-Null
    Copy-Item -LiteralPath (Join-Path $root 'windows\install.ps1') -Destination $installer
    # Only the resolver is replaced: the real installer can write only these profiles.
    $stub = "function Get-DotfilesPowerShellProfiles {`r`n"
    foreach ($path in $profiles) { $stub += "    '$($path.Replace("'", "''"))'`r`n" }
    $stub += "}`r`n"
    Write-TestFile (Join-Path $installerRoot 'profile-paths.ps1') $stub

    $sentinel = Join-Path $testRoot 'source-loaded.txt'
    $source = Join-Path $fixtureRepo 'windows\Microsoft.PowerShell_profile.ps1'
    $sourceText = "[IO.File]::AppendAllText('$($sentinel.Replace("'", "''"))', 'loaded' + [Environment]::NewLine)"
    Write-TestFile $source $sourceText
    $unmanaged = '$FixtureUser = ''keep unmanaged user text''' + "`r`n"
    $managed = @'
$FixtureBefore = 'keep before managed block'
# >>> vincent-dotfiles >>>
throw 'obsolete managed block executed'
# <<< vincent-dotfiles <<<
$FixtureAfter = 'keep after managed block'
'@
    Write-TestFile $profiles[0] $unmanaged
    Write-TestFile $profiles[1] $managed
    $original = Read-TestBytes $profiles[0]

    & $installer -Repo $fixtureRepo | Out-Null
    $backups = @(Get-ChildItem -LiteralPath (Split-Path -Parent $profiles[0]) -Filter '*.pre-dotfiles.*.bak')
    Assert-True ($backups.Count -eq 1) 'Unmanaged profile did not receive exactly one backup'
    Assert-True ((Read-TestBytes $backups[0].FullName) -ceq $original) 'Backup bytes differ from the unmanaged profile'
    Assert-True (@(Get-ChildItem -LiteralPath (Split-Path -Parent $profiles[1]) -Filter '*.pre-dotfiles.*.bak').Count -eq 0) 'Managed profile received an unnecessary backup'

    $firstBytes = @{}
    foreach ($profilePath in $profiles) {
        $content = [IO.File]::ReadAllText($profilePath)
        foreach ($marker in @('# >>> vincent-dotfiles >>>', '# <<< vincent-dotfiles <<<')) {
            Assert-True ([regex]::Matches($content, [regex]::Escape($marker)).Count -eq 1) 'Managed markers were duplicated or lost'
        }
        Assert-True ($content.Contains(". '$($source.Replace("'", "''"))'")) 'Apostrophe in the source path was not escaped'
        Assert-True (!$content.Contains('obsolete managed block')) 'Obsolete managed content survived'
        $tokens = $null; $errors = $null
        [Management.Automation.Language.Parser]::ParseFile($profilePath, [ref]$tokens, [ref]$errors) | Out-Null
        Assert-True ($errors.Count -eq 0) 'Generated fixture profile did not parse'
        # These are the stub resolver's temporary profiles, never host profile paths.
        & $profilePath
        $firstBytes[$profilePath] = Read-TestBytes $profilePath
    }
    Assert-True ([IO.File]::ReadAllText($profiles[0]).Contains('keep unmanaged user text')) 'Unmanaged user text was lost'
    $managedContent = [IO.File]::ReadAllText($profiles[1])
    Assert-True ($managedContent.Contains('keep before managed block') -and $managedContent.Contains('keep after managed block')) 'User text around the managed block was lost'
    Assert-True (([IO.File]::ReadAllLines($sentinel) -join ',') -ceq 'loaded,loaded') 'Fixture profiles did not load the intended source exactly once each'
    Assert-True ([IO.File]::ReadAllText($source) -ceq $sourceText) 'Installer changed the source profile'

    & $installer -Repo $fixtureRepo | Out-Null
    foreach ($profilePath in $profiles) {
        Assert-True ((Read-TestBytes $profilePath) -ceq $firstBytes[$profilePath]) 'Second install changed profile bytes'
    }
    $afterBackups = @(Get-ChildItem -LiteralPath (Split-Path -Parent $profiles[0]) -Filter '*.pre-dotfiles.*.bak')
    Assert-True ($afterBackups.Count -eq 1 -and $afterBackups[0].FullName -ceq $backups[0].FullName) 'Second install changed backup identity'
    Assert-True ((Read-TestBytes $afterBackups[0].FullName) -ceq $original) 'Second install changed backup bytes'
    Assert-True (@(Get-ChildItem -LiteralPath (Split-Path -Parent $profiles[1]) -Filter '*.pre-dotfiles.*.bak').Count -eq 0) 'Second install backed up an already managed profile'
    Write-Output 'windows_profile_install_test=passed'
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
