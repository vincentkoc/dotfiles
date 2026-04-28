# tmux-hn-ticker

Tiny Hacker News ticker for tmux status lines.

## Install

TPM:

```tmux
set -g @plugin 'vincentkoc/tmux-hn-ticker'
```

Manual:

```tmux
run-shell '/path/to/tmux-hn-ticker/tmux-hn-ticker.tmux'
```

Then add the segment to your status line:

```tmux
set -g status-right '#{@hn_ticker} | %H:%M'
```

## Options

```tmux
set -g @hn_ticker_limit 8
set -g @hn_ticker_interval 300
set -g @hn_ticker_width auto
set -g @hn_ticker_reserved_width 210
set -g @hn_ticker_min_width 18
set -g @hn_ticker_max_width 64
set -g @hn_ticker_speed 0.18
set -g @hn_ticker_step 1
set -g @hn_ticker_separator ' · '
set -g @hn_ticker_cache '~/.cache/tmux-hn-ticker/ticker.txt'
set -g @hn_ticker_items_cache '~/.cache/tmux-hn-ticker/ticker.items.jsonl'
```

The status renderer reads a plain tmux option (`#{@hn_ticker}`). A tiny daemon
updates that option from cache and refreshes stale cache data in the background,
so tmux does not block on network calls or render nested `#(...)` strings.

Wrap the ticker segment in `#[range=user|hn]...#[norange]` and load the plugin to
make mouse clicks on the ticker open the currently visible story URL.
