#!/bin/zsh

_quickssh() {
    local hosts_file
    local -a hosts ssh_files

    hosts_file="${EXTRASPATH:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/dotfiles/.extra}/quickssh_hosts.sh"
    if [[ -r "$hosts_file" ]]; then
        source "$hosts_file"
        hosts+=("${quickssh_hosts[@]}")
    fi

    ssh_files=("$HOME/.ssh/config" "$HOME/.ssh/config.local" "$HOME/.ssh/config.d/"*(N))
    hosts+=("${(@f)$(awk '
        tolower($1) == "host" {
            for (i = 2; i <= NF; i++) {
                if ($i !~ /[*?]/) print $i
            }
        }
    ' "${ssh_files[@]}" 2>/dev/null)}")

    _describe 'host' hosts
}
compdef _quickssh quickssh
