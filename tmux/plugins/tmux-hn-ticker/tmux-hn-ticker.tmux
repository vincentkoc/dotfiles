#!/usr/bin/env bash
set -euo pipefail

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

tmux set-option -gq @hn_ticker_limit "${HN_TICKER_LIMIT:-8}"
tmux set-option -gq @hn_ticker_interval "${HN_TICKER_INTERVAL:-300}"
tmux set-option -gq @hn_ticker_width "${HN_TICKER_WIDTH:-90}"
tmux set-option -gq @hn_ticker_speed "${HN_TICKER_SPEED:-1}"
tmux set-option -gq @hn_ticker_separator "${HN_TICKER_SEPARATOR:- · }"
tmux set-option -gq @hn_ticker_cache "${HN_TICKER_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/tmux-hn-ticker/ticker.txt}"
tmux set-option -gq @hn_ticker "#(\"$CURRENT_DIR/scripts/hn-ticker\")"

"$CURRENT_DIR/scripts/hn-refresh" >/dev/null 2>&1 &
