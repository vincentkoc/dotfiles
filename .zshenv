# Codex agent sandbox cannot write to ~/.cache or Homebrew cache paths.
if [[ -n "${CODEX_SANDBOX:-}" ]]; then
  export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${TMPDIR:-/tmp}/${USER}-cache}"
  export FONTCONFIG_CACHE="${FONTCONFIG_CACHE:-$XDG_CACHE_HOME/fontconfig}"
  export MPLCONFIGDIR="${MPLCONFIGDIR:-$XDG_CACHE_HOME/matplotlib}"
  mkdir -p "$FONTCONFIG_CACHE" "$MPLCONFIGDIR" 2>/dev/null || true
fi

# Load Rust environment if it has been bootstrapped
if [ -f "$HOME/.cargo/env" ]; then
  . "$HOME/.cargo/env"
fi
