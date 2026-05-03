# Load .profile for common shell setup
source ~/.profile

# Let bash bootstrap commands in tmux use the same zsh-z database before they
# hand off to zsh, e.g. `z repo; exec zsh`.
z() {
    local dest
    dest="$(zsh -lc 'zshz -e "$@"' z "$@")" || return
    [ -n "$dest" ] || return 1
    cd "$dest" || return
}

# Bash completion via Homebrew
if command -v brew >/dev/null 2>&1; then
    if [ -r "$(brew --prefix)/etc/profile.d/bash_completion.sh" ]; then
        source "$(brew --prefix)/etc/profile.d/bash_completion.sh"
    fi
fi

# LM Studio CLI
if [[ -d "$HOME/.lmstudio/bin" ]]; then
    export PATH="$PATH:$HOME/.lmstudio/bin"
fi
