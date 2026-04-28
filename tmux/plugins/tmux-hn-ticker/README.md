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
set -g @hn_ticker_reserved_width 96
set -g @hn_ticker_speed 1
set -g @hn_ticker_separator ' · '
set -g @hn_ticker_cache '~/.cache/tmux-hn-ticker/ticker.txt'
```

The status renderer reads a plain tmux option (`#{@hn_ticker}`). A tiny daemon
updates that option from cache and refreshes stale cache data in the background,
so tmux does not block on network calls or render nested `#(...)` strings.
