#!/bin/bash

_quickssh() {
    local cur opts hosts_file ssh_files
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"

    hosts_file="${EXTRASPATH:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/dotfiles/.extra}/quickssh_hosts.sh"
    if [[ -r "$hosts_file" ]]; then
        # shellcheck source=/dev/null
        source "$hosts_file"
        opts="${quickssh_hosts[*]}"
    fi

    ssh_files=("$HOME/.ssh/config" "$HOME/.ssh/config.local")
    if [[ -d "$HOME/.ssh/config.d" ]]; then
        ssh_files+=("$HOME"/.ssh/config.d/*)
    fi

    opts+=" $(awk '
        tolower($1) == "host" {
            for (i = 2; i <= NF; i++) {
                if ($i !~ /[*?]/) print $i
            }
        }
    ' "${ssh_files[@]}" 2>/dev/null)"

    COMPREPLY=( $(compgen -W "${opts}" -- "${cur}") )
    return 0
}
complete -F _quickssh quickssh
