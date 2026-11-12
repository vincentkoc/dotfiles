function Get-DotfilesPowerShellProfiles {
    param([string]$Documents = [Environment]::GetFolderPath('MyDocuments'))
    if ([string]::IsNullOrWhiteSpace($Documents)) { throw 'Windows Documents folder is unavailable' }
    @(
        (Join-Path $Documents 'WindowsPowerShell\Microsoft.PowerShell_profile.ps1'),
        (Join-Path $Documents 'PowerShell\Microsoft.PowerShell_profile.ps1')
    )
}
