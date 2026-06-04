emulate sh
source ~/.profile
emulate zsh

# Non-interactive login shells skip ~/.zshrc.
# Load shared functions so commands like `gwt` are still available.
if [[ ! -o interactive ]] && [[ -z "${DOTFILES_FUNCTIONS_LOADED:-}" ]] && [[ -r ~/.functions ]]; then
  source ~/.functions
  DOTFILES_FUNCTIONS_LOADED=1
fi

# `tmux new-window 'z foo; exec zsh'` and similar helpers run through
# non-interactive login zsh, which skips ~/.zshrc where the z plugin is loaded.
if [[ ! -o interactive ]] && [[ -z "${DOTFILES_Z_LOADED:-}" ]]; then
  _dotfiles_z_plugin="${ZSH:-$HOME/.oh-my-zsh}/plugins/z/z.plugin.zsh"
  if [[ -r "$_dotfiles_z_plugin" ]]; then
    source "$_dotfiles_z_plugin"
    DOTFILES_Z_LOADED=1
  fi
  unset _dotfiles_z_plugin
fi

# OrbStack command-line tools and integration.
source ~/.orbstack/shell/init.zsh 2>/dev/null || :
