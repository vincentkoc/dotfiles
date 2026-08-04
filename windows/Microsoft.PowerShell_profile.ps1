$DotfilesWslDistro = if ($env:DOTFILES_WSL_DISTRO) { $env:DOTFILES_WSL_DISTRO } else { 'Ubuntu' }

function Invoke-DotfilesWslCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$ArgumentList = @(),
        [string]$WorkingDirectory = '~'
    )

    $windowsPath = $env:PATH
    $windowsWslEnv = $env:WSLENV
    $windowsArgumentPayload = $env:DOTFILES_WSL_ARGV_B64
    try {
        $payload = [System.IO.MemoryStream]::new()
        foreach ($argument in @($Command) + @($ArgumentList)) {
            $bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$argument)
            $payload.Write($bytes, 0, $bytes.Length)
            $payload.WriteByte(0)
        }

        $wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
        $env:PATH = ''
        $env:DOTFILES_WSL_ARGV_B64 = [System.Convert]::ToBase64String($payload.ToArray())
        $wslEnvEntries = @($windowsWslEnv -split ':' | Where-Object {
            $_ -and !($_ -match '^DOTFILES_WSL_ARGV_B64(?:/.*)?$')
        })
        $env:WSLENV = (@($wslEnvEntries) + 'DOTFILES_WSL_ARGV_B64/u') -join ':'
        $invoke = @'
source ~/.zshrc
typeset -a dotfiles_argv
while IFS= read -r -d $'\0' dotfiles_argument; do
    dotfiles_argv+=("$dotfiles_argument")
done < <(printf '%s' "$DOTFILES_WSL_ARGV_B64" | base64 -d)
dotfiles_command="$dotfiles_argv[1]"
dotfiles_argv=("${dotfiles_argv[@]:1}")
"$dotfiles_command" "${dotfiles_argv[@]}"
# Keep PowerShell's trailing CR on a comment instead of the final argument.
'@
        $invoke | & $wsl -d $DotfilesWslDistro --cd $WorkingDirectory -- zsh -ls
    } finally {
        $env:PATH = $windowsPath
        if ($null -eq $windowsWslEnv) {
            Remove-Item Env:WSLENV -ErrorAction SilentlyContinue
        } else {
            $env:WSLENV = $windowsWslEnv
        }
        if ($null -eq $windowsArgumentPayload) {
            Remove-Item Env:DOTFILES_WSL_ARGV_B64 -ErrorAction SilentlyContinue
        } else {
            $env:DOTFILES_WSL_ARGV_B64 = $windowsArgumentPayload
        }
    }
}

function dots {
    $wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
    & $wsl -d $DotfilesWslDistro --cd '~/GIT/_Perso/dotfiles' -- zsh -l
}

function tt {
    Invoke-DotfilesWslCommand -Command 'tt' -ArgumentList @($args)
}

function gwt {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true, Position = 0)][string]$WslRepoPath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$ArgumentList
    )

    if (!$WslRepoPath.StartsWith('/') -or $WslRepoPath.Contains('\') -or $WslRepoPath.Contains(':')) {
        throw 'gwt requires an explicit absolute WSL repository path such as /home/user/GIT/repo'
    }
    Invoke-DotfilesWslCommand -WorkingDirectory $WslRepoPath -Command 'gwt' -ArgumentList $ArgumentList
}

function wgit { Invoke-DotfilesWslCommand -Command 'git' -ArgumentList @($args) }
function wcx { Invoke-DotfilesWslCommand -Command 'codex' -ArgumentList @($args) }
function wdeepclean { Invoke-DotfilesWslCommand -Command 'deepclean' -ArgumentList @($args) }
function wssh { Invoke-DotfilesWslCommand -Command 'ssh' -ArgumentList @($args) }

Set-Alias wg wgit
Set-Alias cxw wcx
