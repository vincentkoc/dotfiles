emulate sh
source ~/.profile
emulate zsh

# Non-interactive login shells skip ~/.zshrc.
# Load shared functions so commands like `gwt` are still available.
if [[ ! -o interactive ]] && [[ -z "${DOTFILES_FUNCTIONS_LOADED:-}" ]] && [[ -r ~/.functions ]]; then
  source ~/.functions
  DOTFILES_FUNCTIONS_LOADED=1
fi
