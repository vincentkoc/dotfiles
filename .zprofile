emulate sh
source ~/.profile
emulate zsh

# Codex runs non-interactive login shells that skip ~/.zshrc.
# Load shared functions so commands like `gwt` are still available.
if [[ -n "${CODEX_SHELL:-}" ]] && [[ -z "${DOTFILES_FUNCTIONS_LOADED:-}" ]] && [[ -r ~/.functions ]]; then
  source ~/.functions
  DOTFILES_FUNCTIONS_LOADED=1
fi
