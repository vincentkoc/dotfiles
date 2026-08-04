[CmdletBinding()]
param(
    [ValidateSet('Plan', 'Apply', 'Check', 'Rollback')]
    [string]$Mode = 'Plan',
    [string]$Repo = "$HOME\GIT\_Perso\dotfiles",
    [string]$StateRoot = "$env:LOCALAPPDATA\vincent-dotfiles\native-operator",
    [string]$Receipt
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$markerStart = '# >>> vincent-dotfiles >>>'
$markerEnd = '# <<< vincent-dotfiles <<<'
$packages = @(
    [pscustomobject]@{ Id = 'Git.Git'; Version = '2.55.0.3'; ExpectedVersion = '2.55.0.3'; Command = 'git' },
    [pscustomobject]@{ Id = 'OpenJS.NodeJS.LTS'; Version = '24.19.0'; ExpectedVersion = '24.19.0'; Command = 'node' },
    [pscustomobject]@{ Id = 'Microsoft.PowerShell'; Version = '7.6.4'; ExpectedVersion = '7.6.4.0'; Command = 'pwsh' },
    [pscustomobject]@{ Id = 'GitHub.cli'; Version = '2.97.0'; ExpectedVersion = '2.97.0'; Command = 'gh' }
)

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Sha256([string]$Path) {
    (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Get-WingetPath {
    $command = Get-Command winget.exe -ErrorAction Stop
    $command.Source
}

function Invoke-Winget([string[]]$Arguments) {
    for ($attempt = 1; $attempt -le 4; $attempt++) {
        try {
            $winget = Get-WingetPath
            $output = @(& $winget @Arguments 2>&1 | ForEach-Object { "$_" })
            return [pscustomobject]@{
                ExitCode = $LASTEXITCODE
                Output = $output
            }
        } catch {
            $transientAliasLock = $_.Exception.Message -match 'Access is denied'
            if (!$transientAliasLock -or $attempt -eq 4) {
                throw
            }
            Start-Sleep -Seconds (2 * $attempt)
        }
    }
}

function Get-WingetPackageVersion([string]$Id) {
    $result = Invoke-Winget @(
        'list', '--id', $Id, '--exact', '--source', 'winget',
        '--accept-source-agreements', '--disable-interactivity'
    )
    if ($result.ExitCode -ne 0) {
        return $null
    }
    foreach ($line in $result.Output) {
        if ($line -match ('\s' + [regex]::Escape($Id) + '\s+(\S+)')) {
            return $Matches[1]
        }
    }
    $null
}

function Test-WingetArm64Manifest($Package) {
    $result = Invoke-Winget @(
        'show', '--id', $Package.Id, '--exact', '--version', $Package.Version,
        '--source', 'winget', '--architecture', 'arm64',
        '--accept-source-agreements', '--disable-interactivity'
    )
    [pscustomobject]@{
        Id = $Package.Id
        Version = $Package.Version
        Available = ($result.ExitCode -eq 0)
        Installer = @($result.Output | Where-Object { $_ -match '^\s*Installer Url:' } | Select-Object -First 1)
        Sha256 = @($result.Output | Where-Object { $_ -match '^\s*Installer SHA256:' } | Select-Object -First 1)
    }
}

function Update-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:PATH = "$machinePath;$userPath"
}

function Get-ProfileTargets {
    @(
        (Join-Path $HOME 'Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1'),
        (Join-Path $HOME 'Documents\PowerShell\Microsoft.PowerShell_profile.ps1')
    )
}

function Get-ManagedProfileText([string]$Current, [string]$Source) {
    $escapedSource = $Source.Replace("'", "''")
    $block = @"
$markerStart
. '$escapedSource'
$markerEnd
"@
    $withoutBlock = [regex]::Replace(
        $Current,
        "(?s)$([regex]::Escape($markerStart)).*?$([regex]::Escape($markerEnd))\r?\n?",
        ''
    )
    $prefix = $withoutBlock.TrimEnd()
    if ($prefix.Length -gt 0) {
        return $prefix + "`r`n`r`n" + $block + "`r`n"
    }
    $block + "`r`n"
}

function Save-ProfileState([string]$Path, [string]$BackupRoot) {
    $exists = Test-Path -LiteralPath $Path -PathType Leaf
    $safeName = ($Path -replace '[:\\]', '_').TrimStart('_')
    $backupPath = Join-Path $BackupRoot $safeName
    $state = [ordered]@{
        Path = $Path
        Existed = $exists
        BackupPath = $null
        Sha256 = $null
        Sddl = $null
        AppliedSha256 = $null
    }
    if ($exists) {
        Copy-Item -LiteralPath $Path -Destination $backupPath -Force
        $state.BackupPath = $backupPath
        $state.Sha256 = Get-Sha256 $Path
        $state.Sddl = (Get-Acl -LiteralPath $Path).Sddl
    }
    [pscustomobject]$state
}

function Install-ManagedProfile([string]$Path, [string]$Source) {
    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $current = if (Test-Path -LiteralPath $Path -PathType Leaf) {
        [IO.File]::ReadAllText($Path)
    } else {
        ''
    }
    $updated = Get-ManagedProfileText $current $Source
    $temp = Join-Path $directory ('.dotfiles-profile-' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($temp, $updated, $utf8)
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        $acl = Get-Acl -LiteralPath $Path
        Set-Acl -LiteralPath $temp -AclObject $acl
        [IO.File]::Replace($temp, $Path, $null, $true)
        Set-Acl -LiteralPath $Path -AclObject $acl
    } else {
        Move-Item -LiteralPath $temp -Destination $Path
    }
}

function Resolve-NativeCommand([string]$Name) {
    if ($Name -eq 'pwsh') {
        $package = Get-AppxPackage -Name 'Microsoft.PowerShell' -ErrorAction SilentlyContinue |
            Sort-Object Version -Descending |
            Select-Object -First 1
        if ($package) {
            $appxPath = Join-Path $package.InstallLocation 'pwsh.exe'
            if (Test-Path -LiteralPath $appxPath -PathType Leaf) {
                return $appxPath
            }
        }
    }
    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) {
        return $command.Source
    }
    $fallbacks = @{
        git = @("$env:ProgramFiles\Git\cmd\git.exe")
        node = @("$env:ProgramFiles\nodejs\node.exe")
        npm = @("$env:ProgramFiles\nodejs\npm.cmd")
        npx = @("$env:ProgramFiles\nodejs\npx.cmd")
        pwsh = @(
            "$env:ProgramFiles\PowerShell\7\pwsh.exe",
            "$env:LOCALAPPDATA\Microsoft\WindowsApps\pwsh.exe"
        )
        gh = @("$env:ProgramFiles\GitHub CLI\gh.exe")
    }
    foreach ($path in @($fallbacks[$Name])) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            return $path
        }
    }
    $null
}

function Get-PeMachine([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $reader = New-Object IO.BinaryReader($stream)
    try {
        $stream.Position = 0x3c
        $peOffset = $reader.ReadInt32()
        $stream.Position = $peOffset
        if ($reader.ReadUInt32() -ne 0x00004550) {
            throw "Not a PE executable: $Path"
        }
        ('0x{0:X4}' -f $reader.ReadUInt16())
    } finally {
        $reader.Dispose()
        $stream.Dispose()
    }
}

function Get-CheckResult {
    Update-ProcessPath
    $commands = [ordered]@{}
    foreach ($name in @('git', 'node', 'npm', 'npx', 'pwsh', 'gh')) {
        $path = Resolve-NativeCommand $name
        $commands[$name] = [ordered]@{
            Path = $path
            Native = ($path -and $path -notmatch '(?i)wsl\.exe|\\WindowsApps\\(git|node|npm|gh|codex)')
        }
    }

    $pe = [ordered]@{}
    foreach ($name in @('git', 'node', 'pwsh', 'gh')) {
        $path = $commands[$name].Path
        $pe[$name] = if ($path) { Get-PeMachine $path } else { $null }
    }

    $packageVersions = [ordered]@{}
    foreach ($package in $packages) {
        $packageVersions[$package.Id] = Get-WingetPackageVersion $package.Id
    }

    $profiles = @()
    foreach ($path in Get-ProfileTargets) {
        $profiles += [pscustomobject]@{
            Path = $path
            Exists = Test-Path -LiteralPath $path -PathType Leaf
            Managed = (
                (Test-Path -LiteralPath $path -PathType Leaf) -and
                (Select-String -LiteralPath $path -SimpleMatch $markerStart -Quiet)
            )
        }
    }

    $allPackagesExact = $true
    foreach ($package in $packages) {
        if ($packageVersions[$package.Id] -ne $package.ExpectedVersion) {
            $allPackagesExact = $false
        }
    }
    $allPeArm64 = @($pe.Values | Where-Object { $_ -ne '0xAA64' }).Count -eq 0
    $allNative = @($commands.Values | Where-Object { -not $_.Native }).Count -eq 0
    $allProfilesManaged = @($profiles | Where-Object { -not $_.Managed }).Count -eq 0

    [pscustomobject]@{
        Architecture = $env:PROCESSOR_ARCHITECTURE
        Packages = $packageVersions
        Commands = $commands
        PeMachine = $pe
        Profiles = $profiles
        Passed = (
            $env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -and
            $allPackagesExact -and $allPeArm64 -and $allNative -and $allProfilesManaged
        )
    }
}

function Write-Json([string]$Path, $Value) {
    $json = $Value | ConvertTo-Json -Depth 10
    [IO.File]::WriteAllText($Path, $json, $utf8)
}

function Resolve-ReceiptPath {
    if ($Receipt) {
        return $Receipt
    }
    $latest = Join-Path $StateRoot 'latest.json'
    if (!(Test-Path -LiteralPath $latest -PathType Leaf)) {
        throw "No native operator receipt found under $StateRoot"
    }
    (Get-Content -LiteralPath $latest -Raw | ConvertFrom-Json).Receipt
}

function Invoke-Plan {
    $source = Join-Path $Repo 'windows\Microsoft.PowerShell_profile.ps1'
    if (!(Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Dotfiles PowerShell profile missing: $source"
    }
    $manifests = @()
    foreach ($package in $packages) {
        $manifests += Test-WingetArm64Manifest $package
    }
    [pscustomobject]@{
        Mode = 'Plan'
        Architecture = $env:PROCESSOR_ARCHITECTURE
        Administrator = Test-IsAdministrator
        Repo = $Repo
        Profiles = Get-ProfileTargets
        Manifests = $manifests
        Ready = (
            $env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -and
            @($manifests | Where-Object { -not $_.Available }).Count -eq 0
        )
    }
}

function Invoke-Apply {
    if ($env:PROCESSOR_ARCHITECTURE -ne 'ARM64') {
        throw "Native operator requires ARM64; found $env:PROCESSOR_ARCHITECTURE"
    }
    if (!(Test-IsAdministrator)) {
        throw 'Native operator Apply requires an elevated PowerShell session'
    }
    $source = Join-Path $Repo 'windows\Microsoft.PowerShell_profile.ps1'
    if (!(Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Dotfiles PowerShell profile missing: $source"
    }

    $plan = Invoke-Plan
    if (!$plan.Ready) {
        throw 'One or more exact ARM64 Winget manifests are unavailable'
    }

    if ($Receipt) {
        $receiptPath = $Receipt
        $state = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
        if ($state.Status -ne 'applying') {
            throw "Apply receipt cannot be resumed from status: $($state.Status)"
        }
        $runId = $state.RunId
        $runRoot = Split-Path -Parent $receiptPath
        $profileStates = @($state.Profiles)
        $packageStates = @($state.Packages)
        foreach ($packageState in $packageStates) {
            $definition = @($packages | Where-Object { $_.Id -eq $packageState.Id })[0]
            $packageState.TargetVersion = $definition.ExpectedVersion
            if ($packageState.PSObject.Properties.Name -contains 'InstallVersion') {
                $packageState.InstallVersion = $definition.Version
            } else {
                $packageState | Add-Member -NotePropertyName InstallVersion -NotePropertyValue $definition.Version
            }
        }
    } else {
        $runId = Get-Date -Format 'yyyyMMdd-HHmmss'
        $runRoot = Join-Path (Join-Path $StateRoot 'runs') $runId
        $backupRoot = Join-Path $runRoot 'profile-backups'
        New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null

        $profileStates = @()
        foreach ($path in Get-ProfileTargets) {
            $profileStates += Save-ProfileState $path $backupRoot
        }
        $packageStates = @()
        foreach ($package in $packages) {
            $packageStates += [pscustomobject]@{
                Id = $package.Id
                BeforeVersion = Get-WingetPackageVersion $package.Id
                TargetVersion = $package.ExpectedVersion
                InstallVersion = $package.Version
                AfterVersion = $null
            }
        }

        $receiptPath = Join-Path $runRoot 'apply.json'
        $state = [ordered]@{
            Schema = 1
            Status = 'applying'
            RunId = $runId
            Repo = $Repo
            CreatedAt = (Get-Date).ToString('o')
            CompletedAt = $null
            Packages = $packageStates
            Profiles = $profileStates
        }
        Write-Json $receiptPath $state
    }

    foreach ($package in $packages) {
        $current = Get-WingetPackageVersion $package.Id
        if ($current -ne $package.ExpectedVersion) {
            $result = Invoke-Winget @(
                'install', '--id', $package.Id, '--exact', '--version', $package.Version,
                '--source', 'winget', '--architecture', 'arm64', '--silent',
                '--accept-package-agreements', '--accept-source-agreements',
                '--disable-interactivity'
            )
            if ($result.ExitCode -ne 0) {
                throw "Winget install failed for $($package.Id): $($result.Output -join [Environment]::NewLine)"
            }
        }
    }
    Update-ProcessPath

    foreach ($profile in $profileStates) {
        Install-ManagedProfile $profile.Path $source
        $profile.AppliedSha256 = Get-Sha256 $profile.Path
    }
    foreach ($packageState in $packageStates) {
        $packageState.AfterVersion = Get-WingetPackageVersion $packageState.Id
        if ($packageState.AfterVersion -ne $packageState.TargetVersion) {
            throw "Package version mismatch for $($packageState.Id): $($packageState.AfterVersion)"
        }
    }

    $state.Status = 'applied'
    if ($state.PSObject.Properties.Name -contains 'CompletedAt') {
        $state.CompletedAt = (Get-Date).ToString('o')
    } else {
        $state | Add-Member -NotePropertyName CompletedAt -NotePropertyValue (Get-Date).ToString('o')
    }
    Write-Json $receiptPath $state
    New-Item -ItemType Directory -Path $StateRoot -Force | Out-Null
    Write-Json (Join-Path $StateRoot 'latest.json') ([ordered]@{
        RunId = $runId
        Receipt = $receiptPath
    })

    $check = Get-CheckResult
    if (!$check.Passed) {
        throw 'Native operator post-apply check failed'
    }
    [pscustomobject]@{
        Mode = 'Apply'
        Receipt = $receiptPath
        Check = $check
    }
}

function Invoke-Rollback {
    if (!(Test-IsAdministrator)) {
        throw 'Native operator Rollback requires an elevated PowerShell session'
    }
    $receiptPath = Resolve-ReceiptPath
    $state = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    if ($state.Status -ne 'applied') {
        throw "Receipt is not in applied state: $($state.Status)"
    }

    foreach ($profile in $state.Profiles) {
        if ($profile.Existed) {
            Copy-Item -LiteralPath $profile.BackupPath -Destination $profile.Path -Force
            if ($profile.Sddl) {
                $acl = New-Object Security.AccessControl.FileSecurity
                $acl.SetSecurityDescriptorSddlForm($profile.Sddl)
                Set-Acl -LiteralPath $profile.Path -AclObject $acl
            }
        } elseif (Test-Path -LiteralPath $profile.Path -PathType Leaf) {
            if ((Get-Sha256 $profile.Path) -ne $profile.AppliedSha256) {
                throw "Refusing to remove changed profile: $($profile.Path)"
            }
            Remove-Item -LiteralPath $profile.Path -Force
        }
    }

    foreach ($package in @($state.Packages | Sort-Object Id -Descending)) {
        $current = Get-WingetPackageVersion $package.Id
        if (!$package.BeforeVersion) {
            if ($current -eq $package.TargetVersion) {
                $result = Invoke-Winget @(
                    'uninstall', '--id', $package.Id, '--exact', '--silent',
                    '--disable-interactivity'
                )
                if ($result.ExitCode -ne 0) {
                    throw "Winget uninstall failed for $($package.Id)"
                }
            }
        } elseif ($current -ne $package.BeforeVersion) {
            $result = Invoke-Winget @(
                'install', '--id', $package.Id, '--exact', '--version', $package.BeforeVersion,
                '--source', 'winget', '--architecture', 'arm64', '--silent',
                '--accept-package-agreements', '--accept-source-agreements',
                '--disable-interactivity', '--force'
            )
            if ($result.ExitCode -ne 0) {
                throw "Winget restore failed for $($package.Id)"
            }
        }
    }

    $rollback = [ordered]@{
        Schema = 1
        Status = 'rolled-back'
        ApplyReceipt = $receiptPath
        CompletedAt = (Get-Date).ToString('o')
    }
    $rollbackPath = Join-Path (Split-Path -Parent $receiptPath) 'rollback.json'
    Write-Json $rollbackPath $rollback
    [pscustomobject]@{
        Mode = 'Rollback'
        Receipt = $rollbackPath
    }
}

switch ($Mode) {
    'Plan' { Invoke-Plan | ConvertTo-Json -Depth 10 }
    'Apply' { Invoke-Apply | ConvertTo-Json -Depth 10 }
    'Check' {
        $check = Get-CheckResult
        $check | ConvertTo-Json -Depth 10
        if (!$check.Passed) { exit 1 }
    }
    'Rollback' { Invoke-Rollback | ConvertTo-Json -Depth 10 }
}
