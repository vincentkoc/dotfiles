$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'windows\native-operator.ps1')

$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('native-operator-rollback-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null

function Write-TestFile([string]$Path, [string]$Content) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [IO.File]::WriteAllText($Path, $Content, [Text.UTF8Encoding]::new($false))
}

function Assert-Equal($Expected, $Actual, [string]$Message) {
    if ($Expected -ne $Actual) {
        throw "$Message (expected '$Expected', got '$Actual')"
    }
}

function Assert-True([bool]$Condition, [string]$Message) {
    if (!$Condition) {
        throw $Message
    }
}

function New-ProfileFixture(
    [string]$CaseRoot,
    [string]$Name,
    [bool]$Existed,
    [string]$Original,
    [string]$Applied
) {
    $profilePath = Join-Path $CaseRoot "profiles\$Name.ps1"
    $backupPath = Join-Path $CaseRoot "backups\$Name.ps1"
    $originalSha256 = $null
    if ($Existed) {
        Write-TestFile $backupPath $Original
        # A distinct protected ACL proves rollback restores metadata, not just bytes.
        $backupAcl = Get-Acl -LiteralPath $backupPath
        $backupAcl.SetAccessRuleProtection($true, $true)
        Set-Acl -LiteralPath $backupPath -AclObject $backupAcl
        $originalSha256 = Get-Sha256 $backupPath
    }
    Write-TestFile $profilePath $Applied
    [pscustomobject]@{
        Path = $profilePath
        Existed = $Existed
        BackupPath = if ($Existed) { $backupPath } else { $null }
        Sha256 = $originalSha256
        Sddl = if ($Existed) { (Get-Acl -LiteralPath $backupPath).Sddl } else { $null }
        AppliedSha256 = Get-Sha256 $profilePath
    }
}

function New-TestReceipt([string]$CaseRoot, [object[]]$Profiles, [object[]]$Packages = @()) {
    $receiptPath = Join-Path $CaseRoot 'apply.json'
    [ordered]@{
        Schema = 1
        Status = 'applied'
        RunId = 'fixture'
        Packages = $Packages
        Profiles = $Profiles
    } | ConvertTo-Json -Depth 10 | ForEach-Object { [IO.File]::WriteAllText($receiptPath, $_, $utf8) }
    $receiptPath
}

function Invoke-ExpectedRollbackFailure([string]$Pattern) {
    try {
        Invoke-Rollback | Out-Null
        throw 'Rollback unexpectedly succeeded'
    } catch {
        if ($_.Exception.Message -notlike $Pattern) {
            throw
        }
    }
}

function Test-IsAdministrator { $true }
function Resolve-ReceiptPath { $script:testReceiptPath }
$script:realPackageVersion = ${function:Get-WingetPackageVersion}
$script:useRealPackageVersion = $false
$script:wingetQueryResult = $null
function Get-WingetPackageVersion([string]$Id) {
    if ($script:useRealPackageVersion) {
        return & $script:realPackageVersion $Id
    }
    $script:packageQueries.Add($Id) | Out-Null
    if ($script:packageVersions.ContainsKey($Id)) {
        return $script:packageVersions[$Id]
    }
    $null
}
# The real copy runs only inside this fixture; inject edits between target restores.
$script:afterCopyAction = $null
function Copy-Item([string]$LiteralPath, [string]$Destination, [switch]$Force) {
    Microsoft.PowerShell.Management\Copy-Item -LiteralPath $LiteralPath -Destination $Destination -Force:$Force
    if ($script:afterCopyAction) {
        & $script:afterCopyAction
        $script:afterCopyAction = $null
    }
}
function Invoke-Winget([string[]]$Arguments) {
    if ($Arguments[0] -eq 'list') {
        return $script:wingetQueryResult
    }
    $script:wingetMutations.Add(($Arguments -join ' ')) | Out-Null
    if ($Arguments[0] -eq 'uninstall') {
        $id = $Arguments[[Array]::IndexOf($Arguments, '--id') + 1]
        $script:packageVersions.Remove($id) | Out-Null
    }
    [pscustomobject]@{ ExitCode = 0; Output = @() }
}

try {
    $caseRoot = Join-Path $testRoot 'existing-drift'
    $existing = New-ProfileFixture $caseRoot 'existing' $true 'original' 'applied'
    $created = New-ProfileFixture $caseRoot 'created' $false '' 'applied-created'
    $script:testReceiptPath = New-TestReceipt $caseRoot @($existing, $created) @(
        [pscustomobject]@{ Id = 'Fixture.Package'; BeforeVersion = $null; TargetVersion = '1.0' }
    )
    Write-TestFile $existing.Path 'user edit'
    $script:packageQueries = [Collections.Generic.List[string]]::new()
    $script:wingetMutations = [Collections.Generic.List[string]]::new()
    $script:packageVersions = @{ 'Fixture.Package' = '1.0' }

    Invoke-ExpectedRollbackFailure '*Refusing to rollback changed profile*'
    Assert-Equal 'user edit' ([IO.File]::ReadAllText($existing.Path)) 'Changed existing profile was overwritten'
    Assert-Equal 'applied-created' ([IO.File]::ReadAllText($created.Path)) 'Clean created profile was removed'
    Assert-Equal 0 $script:packageQueries.Count 'Package state was queried after profile preflight failed'
    Assert-Equal 0 $script:wingetMutations.Count 'Winget was invoked after profile preflight failed'

    $caseRoot = Join-Path $testRoot 'later-created-drift'
    $existing = New-ProfileFixture $caseRoot 'existing' $true 'original' 'applied'
    $created = New-ProfileFixture $caseRoot 'created' $false '' 'applied-created'
    $script:testReceiptPath = New-TestReceipt $caseRoot @($existing, $created) @(
        [pscustomobject]@{ Id = 'Fixture.Package'; BeforeVersion = $null; TargetVersion = '1.0' }
    )
    Write-TestFile $created.Path 'user edit'
    $script:packageQueries.Clear()
    $script:wingetMutations.Clear()

    Invoke-ExpectedRollbackFailure '*Refusing to rollback changed profile*'
    Assert-Equal 'applied' ([IO.File]::ReadAllText($existing.Path)) 'Earlier existing profile was restored before later drift was found'
    Assert-Equal 'user edit' ([IO.File]::ReadAllText($created.Path)) 'Changed created profile was removed'
    Assert-Equal 0 $script:packageQueries.Count 'Package state was queried after later profile drift'
    Assert-Equal 0 $script:wingetMutations.Count 'Winget was invoked after later profile drift'

    $caseRoot = Join-Path $testRoot 'backup-drift'
    $existing = New-ProfileFixture $caseRoot 'existing' $true 'original' 'applied'
    $script:testReceiptPath = New-TestReceipt $caseRoot @($existing) @(
        [pscustomobject]@{ Id = 'Fixture.Package'; BeforeVersion = $null; TargetVersion = '1.0' }
    )
    Write-TestFile $existing.BackupPath 'corrupt backup'
    $script:packageQueries.Clear()
    $script:wingetMutations.Clear()

    Invoke-ExpectedRollbackFailure '*Profile backup changed*'
    Assert-Equal 'applied' ([IO.File]::ReadAllText($existing.Path)) 'Profile changed before backup validation completed'
    Assert-Equal 0 $script:packageQueries.Count 'Package state was queried after backup validation failed'
    Assert-Equal 0 $script:wingetMutations.Count 'Winget was invoked after backup validation failed'

    foreach ($missing in @('profile', 'backup')) {
        $caseRoot = Join-Path $testRoot "missing-$missing"
        $existing = New-ProfileFixture $caseRoot 'existing' $true 'original' 'applied'
        $script:testReceiptPath = New-TestReceipt $caseRoot @($existing)
        $path = if ($missing -eq 'profile') { $existing.Path } else { $existing.BackupPath }
        Remove-Item -LiteralPath $path
        $script:wingetMutations.Clear()
        Invoke-ExpectedRollbackFailure '*missing*'
        Assert-Equal 0 $script:wingetMutations.Count 'Missing rollback input reached Winget'
        Assert-True (!(Test-Path -LiteralPath (Join-Path $caseRoot 'rollback.json'))) 'Failed rollback wrote a success receipt'
    }

    foreach ($drift in @('profile', 'backup')) {
        $caseRoot = Join-Path $testRoot "between-targets-$drift"
        $existing = New-ProfileFixture $caseRoot 'existing' $true 'original' 'applied'
        $later = New-ProfileFixture $caseRoot 'later' $true 'later-original' 'later-applied'
        $script:testReceiptPath = New-TestReceipt $caseRoot @($existing, $later)
        $script:driftPath = if ($drift -eq 'profile') { $later.Path } else { $later.BackupPath }
        $script:afterCopyAction = { Write-TestFile $script:driftPath 'concurrent edit' }
        $script:wingetMutations.Clear()
        Invoke-ExpectedRollbackFailure '*changed*'
        Assert-Equal 'original' ([IO.File]::ReadAllText($existing.Path)) 'First target was not restored'
        Assert-Equal 'concurrent edit' ([IO.File]::ReadAllText($script:driftPath)) 'Concurrent edit was overwritten'
        if ($drift -eq 'backup') {
            Assert-Equal 'later-applied' ([IO.File]::ReadAllText($later.Path)) 'Changed backup was restored'
        }
        Assert-True (Test-Path -LiteralPath $existing.BackupPath) 'Recovery backup disappeared'
        Assert-Equal 0 $script:wingetMutations.Count 'Profile drift reached Winget'
        Assert-True (!(Test-Path -LiteralPath (Join-Path $caseRoot 'rollback.json'))) 'Partial rollback wrote a success receipt'
    }

    foreach ($before in @($null, '0.9')) {
        $caseRoot = Join-Path $testRoot ("package-drift-" + [guid]::NewGuid().ToString('N'))
        $existing = New-ProfileFixture $caseRoot 'existing' $true 'original' 'applied'
        $script:testReceiptPath = New-TestReceipt $caseRoot @($existing) @(
            [pscustomobject]@{ Id = 'Fixture.Package'; BeforeVersion = $before; TargetVersion = '1.0' }
        )
        $script:packageVersions = @{ 'Fixture.Package' = '2.0' }
        $script:wingetMutations.Clear()
        Invoke-ExpectedRollbackFailure '*Refusing to rollback changed package*'
        Assert-Equal 'applied' ([IO.File]::ReadAllText($existing.Path)) 'Package preflight ran after profile mutation'
        Assert-Equal 0 $script:wingetMutations.Count 'Concurrent package upgrade was replaced'
    }

    $caseRoot = Join-Path $testRoot 'package-drift-after-preflight'
    $existing = New-ProfileFixture $caseRoot 'existing' $true 'original' 'applied'
    $script:testReceiptPath = New-TestReceipt $caseRoot @($existing) @(
        [pscustomobject]@{ Id = 'Fixture.Package'; BeforeVersion = '0.9'; TargetVersion = '1.0' }
    )
    $script:packageVersions = @{ 'Fixture.Package' = '1.0' }
    $script:afterCopyAction = { $script:packageVersions['Fixture.Package'] = '2.0' }
    $script:wingetMutations.Clear()
    Invoke-ExpectedRollbackFailure '*Refusing to rollback changed package*'
    Assert-Equal '2.0' $script:packageVersions['Fixture.Package'] 'Concurrent package upgrade was not retained'
    Assert-Equal 0 $script:wingetMutations.Count 'Package changed after preflight reached Winget'
    Assert-True (!(Test-Path -LiteralPath (Join-Path $caseRoot 'rollback.json'))) 'Partial rollback wrote a success receipt'

    foreach ($linked in @('profile', 'backup')) {
        $caseRoot = Join-Path $testRoot "linked-$linked"
        $existing = New-ProfileFixture $caseRoot 'existing' $true 'original' 'applied'
        $script:testReceiptPath = New-TestReceipt $caseRoot @($existing)
        $path = if ($linked -eq 'profile') { $existing.Path } else { $existing.BackupPath }
        $foreignPath = Join-Path $caseRoot 'foreign.ps1'
        Move-Item -LiteralPath $path -Destination $foreignPath
        New-Item -ItemType SymbolicLink -Path $path -Target $foreignPath | Out-Null
        $foreignHash = Get-Sha256 $foreignPath
        Invoke-ExpectedRollbackFailure '*Refusing linked or non-file profile input*'
        Assert-Equal $foreignHash (Get-Sha256 $foreignPath) 'Linked rollback target was overwritten'
        Assert-True ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) 'Rollback removed a link'
        if ($linked -eq 'profile') {
            try {
                Install-ManagedProfile $path 'fixture-source'
                throw 'Apply unexpectedly accepted linked profile'
            } catch {
                if ($_.Exception.Message -notlike '*Refusing linked or non-file profile input*') { throw }
            }
        }
    }

    $script:useRealPackageVersion = $true
    foreach ($query in @(
        [pscustomobject]@{ ExitCode = 1; Output = @('source unavailable'); Pattern = '*Winget list failed*' },
        [pscustomobject]@{ ExitCode = 0; Output = @('unrecognized table'); Pattern = '*did not report one installed version*' },
        [pscustomobject]@{ ExitCode = 0; Output = @('Fixture Fixture.Package 1.0', 'Fixture Fixture.Package 2.0'); Pattern = '*did not report one installed version*' }
    )) {
        $caseRoot = Join-Path $testRoot ('query-failure-' + [guid]::NewGuid().ToString('N'))
        $existing = New-ProfileFixture $caseRoot 'existing' $true 'original' 'applied'
        $script:testReceiptPath = New-TestReceipt $caseRoot @($existing) @(
            [pscustomobject]@{ Id = 'Fixture.Package'; BeforeVersion = $null; TargetVersion = '1.0' }
        )
        $script:wingetQueryResult = $query
        $script:wingetMutations.Clear()
        Invoke-ExpectedRollbackFailure $query.Pattern
        Assert-Equal 'applied' ([IO.File]::ReadAllText($existing.Path)) 'Unknown package state mutated a profile'
        Assert-Equal 0 $script:wingetMutations.Count 'Unknown package state reached mutation'
    }
    $script:wingetQueryResult = [pscustomobject]@{ ExitCode = -1978335212; Output = @('No installed package found') }
    Assert-Equal $null (Get-WingetPackageVersion 'Fixture.Package') 'Explicit absence was not recognized'
    $result = Invoke-Rollback
    Assert-Equal 0 $script:wingetMutations.Count 'An already absent package was changed'
    Assert-True (Test-Path -LiteralPath $result.Receipt) 'Confirmed package absence did not finish rollback'
    $script:wingetQueryResult = [pscustomobject]@{ ExitCode = 0; Output = @('Name Id Version', 'Fixture Fixture.Package 1.0 winget') }
    Assert-Equal '1.0' (Get-WingetPackageVersion 'Fixture.Package') 'Installed package table did not parse'
    $script:useRealPackageVersion = $false

    $caseRoot = Join-Path $testRoot 'already-restored-package'
    $existing = New-ProfileFixture $caseRoot 'existing' $true 'original' 'applied'
    $script:testReceiptPath = New-TestReceipt $caseRoot @($existing) @(
        [pscustomobject]@{ Id = 'Fixture.Package'; BeforeVersion = '0.9'; TargetVersion = '1.0' }
    )
    $script:packageVersions = @{ 'Fixture.Package' = '0.9' }
    $script:wingetMutations.Clear()
    Invoke-Rollback | Out-Null
    Assert-Equal 0 $script:wingetMutations.Count 'Original package version was unnecessarily changed'

    $caseRoot = Join-Path $testRoot 'restore-package'
    $existing = New-ProfileFixture $caseRoot 'existing' $true 'original' 'applied'
    $script:testReceiptPath = New-TestReceipt $caseRoot @($existing) @(
        [pscustomobject]@{ Id = 'Fixture.Package'; BeforeVersion = '0.9'; TargetVersion = '1.0' }
    )
    $script:packageVersions = @{ 'Fixture.Package' = '1.0' }
    $script:wingetMutations.Clear()
    Invoke-Rollback | Out-Null
    Assert-Equal 1 $script:wingetMutations.Count 'Applied package version was not restored'
    Assert-True ($script:wingetMutations[0] -like 'install --id Fixture.Package --exact --version 0.9*') 'Unexpected package restore command'

    $caseRoot = Join-Path $testRoot 'success'
    $existing = New-ProfileFixture $caseRoot 'existing' $true 'original' 'applied'
    $created = New-ProfileFixture $caseRoot 'created' $false '' 'applied-created'
    $script:testReceiptPath = New-TestReceipt $caseRoot @($existing, $created) @(
        [pscustomobject]@{ Id = 'Fixture.Package'; BeforeVersion = $null; TargetVersion = '1.0' }
    )
    $script:packageQueries.Clear()
    $script:wingetMutations.Clear()
    $script:packageVersions = @{ 'Fixture.Package' = '1.0' }

    $result = Invoke-Rollback
    Assert-Equal 'Rollback' $result.Mode 'Rollback result mode was wrong'
    Assert-Equal 'original' ([IO.File]::ReadAllText($existing.Path)) 'Existing profile was not restored'
    Assert-Equal $existing.Sddl (Get-Acl -LiteralPath $existing.Path).Sddl 'Existing profile ACL was not restored'
    Assert-True (!(Test-Path -LiteralPath $created.Path)) 'Created profile was not removed'
    Assert-Equal 1 $script:wingetMutations.Count 'Clean rollback did not preserve package rollback behavior'
    Assert-True ($script:wingetMutations[0] -like 'uninstall --id Fixture.Package*') 'Unexpected Winget rollback command'
    Assert-True (Test-Path -LiteralPath $result.Receipt -PathType Leaf) 'Rollback receipt was not written'

    Write-Output 'windows_native_operator_rollback_test=passed'
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
