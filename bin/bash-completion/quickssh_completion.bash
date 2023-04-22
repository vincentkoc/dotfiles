#!/bin/bash

_quickssh() {
    local cur prev opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    source $EXTRASPATH/quickssh_hosts.sh
    opts=$(echo ${!quickssh_hosts[@]})
    COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )
    return 0
}
complete -F _quickssh quickssh
