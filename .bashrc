# Source fzf if available
[ -f ~/.fzf.bash ] && source ~/.fzf.bash

# LM Studio CLI
if [[ -d "$HOME/.lmstudio/bin" ]]; then
    export PATH="$PATH:$HOME/.lmstudio/bin"
fi
