# agent-worktree-ops

Agent worktree cleanup and maintenance tools.

Bins in this folder:

- `agent-worktree-clean`
  - dry-run/apply cleanup of idle worktrees and stale artifacts
  - only registered, non-main worktrees from the selected Git common dir are actionable
  - foreign, unregistered, non-Git, and stale-registration paths are report-only
  - apply mode revalidates dirtiness, reachability, sessions, tmux panes, and process CWDs
  - removals are serial, non-force `git worktree remove` operations
- `agent-worktree-maintain`
  - pressure-aware maintenance runner using the cleaner's registered inventory
- `agent-worktree-purge`
  - background purge of entries left in the legacy quarantine directory
- `install-agent-worktree-ops`
  - atomically install runtime copies, render a machine-local plist, and reload launchd
  - pass `--install-only` to leave the LaunchAgent unloaded

Neither audit nor apply mode prunes Git worktree metadata. Use explicit
`git worktree prune` or `gwt prune` only after reviewing stale registrations.
