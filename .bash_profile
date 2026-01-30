# Load .profile for common shell setup
source ~/.profile

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
