#!/usr/bin/env bash
set -euo pipefail

CURRENT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

tmux set-option -gq @hn_ticker_limit "${HN_TICKER_LIMIT:-8}"
tmux set-option -gq @hn_ticker_interval "${HN_TICKER_INTERVAL:-300}"
tmux set-option -gq @hn_ticker_width "${HN_TICKER_WIDTH:-auto}"
tmux set-option -gq @hn_ticker_reserved_width "${HN_TICKER_RESERVED_WIDTH:-210}"
tmux set-option -gq @hn_ticker_min_width "${HN_TICKER_MIN_WIDTH:-18}"
tmux set-option -gq @hn_ticker_max_width "${HN_TICKER_MAX_WIDTH:-64}"
tmux set-option -gq @hn_ticker_speed "${HN_TICKER_SPEED:-0.18}"
tmux set-option -gq @hn_ticker_step "${HN_TICKER_STEP:-1}"
tmux set-option -gq @hn_ticker_separator "${HN_TICKER_SEPARATOR:- · }"
tmux set-option -gq @hn_ticker_cache "${HN_TICKER_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/tmux-hn-ticker/ticker.txt}"
tmux set-option -gq @hn_ticker_items_cache "${HN_TICKER_ITEMS_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/tmux-hn-ticker/ticker.items.jsonl}"
tmux set-option -gq @hn_ticker_url "https://news.ycombinator.com/"

current_ticker="$(tmux show-option -gqv @hn_ticker 2>/dev/null || true)"
if [ -z "$current_ticker" ] || [ "${current_ticker#\#(}" != "$current_ticker" ]; then
  current_ticker="HN: loading"
fi
tmux set-option -gq @hn_ticker "$current_ticker"
tmux set-option -gq @hn_ticker_text "$current_ticker"

shell_quote() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

tmux run-shell -b "nohup $(shell_quote "$CURRENT_DIR/scripts/hn-refresh") >/dev/null 2>&1 &"
tmux run-shell -b "nohup $(shell_quote "$CURRENT_DIR/scripts/hn-daemon") >/dev/null 2>&1 &"
tmux bind-key -n MouseDown1Status if -F '#{==:#{mouse_status_range},hn}' "run-shell -b $(shell_quote "$CURRENT_DIR/scripts/hn-open")" 'switch-client -t ='
