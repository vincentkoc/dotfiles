# bin

Command-line tools and grouped tool families.

Top-level wrappers:

- `agent-worktree-clean`
- `agent-worktree-maintain`
- `agent-worktree-purge`
- `install-agent-worktree-ops`
- `mtt` - local mobile tmux helper that opens the pane picker on this machine; `mtt restore` unzooms/retiles if layout gets weird
- `mttc` - connect over `mosh`, then jump into remote `mtt` or `tt`
- `tt` - create or attach tmux sessions, including a `mobile` pane-picker profile; `tt restore [target]` repairs zoomed/tiled layouts

Tool folders:

- `agent-worktree-ops/`
  - cleanup, maintenance, purge, and install scripts for agent-managed worktrees
- `bash-completion/`
  - bash completion scripts
- `zsh-completion/`
  - zsh completion scripts
