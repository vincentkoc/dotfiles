#!/bin/zsh

_quickssh() {
    local -a subcmds
    source $EXTRASPATH/quickssh_hosts.sh
    subcmds=(${(k)quickssh_hosts})
    _describe 'command' subcmds
}
compdef _quickssh quickssh