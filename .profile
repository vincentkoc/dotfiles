# Load Rust environment when available
if [ -f "$HOME/.cargo/env" ]; then
  . "$HOME/.cargo/env"
fi

# Ensure cache directories are writable in sandboxed/non-interactive environments
if [ -z "${XDG_CACHE_HOME:-}" ]; then
  if [ -w "$HOME/.cache" ] || mkdir -p "$HOME/.cache" 2>/dev/null; then
    export XDG_CACHE_HOME="$HOME/.cache"
  else
    export XDG_CACHE_HOME="${TMPDIR:-/tmp}/${USER}-cache"
  fi
fi

export FONTCONFIG_CACHE="${XDG_CACHE_HOME}/fontconfig"
if [ -z "${MPLCONFIGDIR:-}" ]; then
  export MPLCONFIGDIR="${XDG_CACHE_HOME}/matplotlib"
fi
mkdir -p "$FONTCONFIG_CACHE" "$MPLCONFIGDIR" 2>/dev/null || true

# Homebrew OpenBLAS (keg-only) environment
if [ -d "/opt/homebrew/opt/openblas" ]; then
  export LDFLAGS="-L/opt/homebrew/opt/openblas/lib${LDFLAGS:+ $LDFLAGS}"
  export CPPFLAGS="-I/opt/homebrew/opt/openblas/include${CPPFLAGS:+ $CPPFLAGS}"
  export PKG_CONFIG_PATH="/opt/homebrew/opt/openblas/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
  export CMAKE_PREFIX_PATH="/opt/homebrew/opt/openblas${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
fi
