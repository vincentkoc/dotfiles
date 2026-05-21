# Codex agent sandbox cannot write to ~/.cache or Homebrew cache paths.
if [[ -n "${CODEX_SANDBOX:-}" ]]; then
  export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${TMPDIR:-/tmp}/${USER}-cache}"
  export FONTCONFIG_CACHE="${FONTCONFIG_CACHE:-$XDG_CACHE_HOME/fontconfig}"
  export MPLCONFIGDIR="${MPLCONFIGDIR:-$XDG_CACHE_HOME/matplotlib}"
  mkdir -p "$FONTCONFIG_CACHE" "$MPLCONFIGDIR" 2>/dev/null || true
fi

# Agent shells can inherit both variables; Node warns when FORCE_COLOR overrides NO_COLOR.
if [[ -n "${NO_COLOR:-}" && -n "${FORCE_COLOR:-}" ]]; then
  unset FORCE_COLOR
fi

# Non-interactive shells skip ~/.zshrc; load shared exports for PATH/tooling.
if [[ ! -o interactive ]] && [[ -z "${DOTFILES_EXPORTS_LOADED:-}" ]] && [[ -r ~/.exports ]]; then
  source ~/.exports
  DOTFILES_EXPORTS_LOADED=1
fi

# Non-interactive shells used by agents/tools skip ~/.zshrc.
# Load shared functions so wrappers like `gwt` are available.
if [[ ! -o interactive ]] && [[ -z "${DOTFILES_FUNCTIONS_LOADED:-}" ]] && [[ -r ~/.functions ]]; then
  source ~/.functions
  DOTFILES_FUNCTIONS_LOADED=1
fi

# Ensure nodenv is available in non-interactive shells (Codex, scripts).
if [[ ! -o interactive ]] && [[ -d "$HOME/.nodenv" ]] && [[ "${DOTFILES_DISABLE_NODENV:-}" != "1" ]]; then
  export NODENV_ROOT="${NODENV_ROOT:-$HOME/.nodenv}"
  export PATH="$NODENV_ROOT/bin:$NODENV_ROOT/shims:$PATH"
fi

export DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1

# Load Rust environment if it has been bootstrapped
if [ -f "$HOME/.cargo/env" ]; then
  . "$HOME/.cargo/env"
fi

# Homebrew rustup is keg-only, so its cargo/rustc proxy dir must be explicit.
if [ -d "/opt/homebrew/opt/rustup/bin" ]; then
  case ":$PATH:" in
    *":/opt/homebrew/opt/rustup/bin:"*) ;;
    *) export PATH="/opt/homebrew/opt/rustup/bin:$PATH" ;;
  esac
fi
