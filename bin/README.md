# bin

Command-line tools and grouped tool families.

Top-level wrappers:

- `agent-worktree-clean`
- `agent-worktree-maintain`
- `agent-worktree-purge`
- `discrawl` - preserve explicit remote auth or load `openclaw-crawl/remote.env` from the trusted XDG config location before dispatching to a real backend
- `install-agent-worktree-ops`
- `retire-agent-worktree-scheduler`
- `mtt` - local mobile tmux helper that opens the pane picker on this machine; `mtt restore` unzooms/retiles if layout gets weird
- `mttc` - connect over `mosh`, then jump into remote `mtt` or `tt`
- `sublime-sync` - verify or restore Sublime User settings and Package Control plugins from a portable bundle
- `tt` - create or attach tmux sessions, including a `mobile` pane-picker profile; `tt restore [target]` repairs zoomed/tiled layouts

Tool folders:

- `agent-worktree-ops/`
  - cleanup, maintenance, purge, runtime installation, and explicit legacy scheduler retirement
- `bash-completion/`
  - bash completion scripts
- `zsh-completion/`
  - zsh completion scripts
