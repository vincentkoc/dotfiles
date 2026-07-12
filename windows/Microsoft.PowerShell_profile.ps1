$DotfilesWslDistro = if ($env:DOTFILES_WSL_DISTRO) { $env:DOTFILES_WSL_DISTRO } else { 'Ubuntu' }

function Invoke-DotfilesWsl {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Command)
    $line = $Command -join ' '
    wsl.exe -d $DotfilesWslDistro -- zsh -lic $line
}

function dots { Invoke-DotfilesWsl 'cd ~/GIT/_Perso/dotfiles && exec zsh' }
function wgit { Invoke-DotfilesWsl ('git ' + (($args | ForEach-Object { "'$_'" }) -join ' ')) }
function wcx { Invoke-DotfilesWsl ('codex ' + (($args | ForEach-Object { "'$_'" }) -join ' ')) }
function wdeepclean { Invoke-DotfilesWsl ('deepclean ' + (($args | ForEach-Object { "'$_'" }) -join ' ')) }
function wssh { Invoke-DotfilesWsl ('ssh ' + (($args | ForEach-Object { "'$_'" }) -join ' ')) }

Set-Alias wg wgit
Set-Alias cxw wcx
