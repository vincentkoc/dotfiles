# Source fzf if available
[ -f ~/.fzf.bash ] && source ~/.fzf.bash

# Mirror the zsh-z jump command for bash panes that later exec zsh.
z() {
    local dest
    dest="$(zsh -lc 'zshz -e "$@"' z "$@")" || return
    [ -n "$dest" ] || return 1
    cd "$dest" || return
}

# LM Studio CLI
if [[ -d "$HOME/.lmstudio/bin" ]]; then
    export PATH="$PATH:$HOME/.lmstudio/bin"
fi
