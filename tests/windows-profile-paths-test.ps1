param([string]$Repo = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
. (Join-Path $Repo 'windows\profile-paths.ps1')
foreach ($documents in @('C:\Users\fixture\Documents', 'C:\Users\fixture\OneDrive - Example\Documents')) {
    $actual = @(Get-DotfilesPowerShellProfiles -Documents $documents)
    if ($actual.Count -ne 2 -or $actual[0] -cne "$documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1" -or $actual[1] -cne "$documents\PowerShell\Microsoft.PowerShell_profile.ps1") {
        throw 'Redirected profile paths did not follow the Windows Documents folder'
    }
}
foreach ($documents in @('', ' ')) {
    $rejected = $false
    try { Get-DotfilesPowerShellProfiles -Documents $documents | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw 'An unavailable Documents folder must not fall back to the profile home' }
}
$tokens = $null; $errors = $null
$tree = [Management.Automation.Language.Parser]::ParseFile((Join-Path $Repo 'windows\native-operator.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Native operator did not parse' }
$function = $tree.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-ProfileTargets' }, $true)
. ([scriptblock]::Create($function.Extent.Text))
if ((@(Get-ProfileTargets) -join '|') -cne (@(Get-DotfilesPowerShellProfiles) -join '|')) { throw 'Native operator uses a different profile resolver' }
Write-Output 'windows_profile_paths_test=passed'
