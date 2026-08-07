# agent-worktree-ops

Agent worktree cleanup and maintenance tools.

Bins in this folder:

- `agent-worktree-clean`
  - dry-run/apply cleanup of idle worktrees and stale artifacts
  - only registered, non-main worktrees from the selected Git common dir are actionable
  - foreign, unregistered, non-Git, and stale-registration paths are report-only
  - apply mode revalidates dirtiness, reachability, sessions, tmux panes, and process CWDs
  - removals are serial, non-force `git worktree remove` operations
  - apply exits nonzero when a selected trim or removal fails
  - pass `--skip-legacy-purge` to leave legacy quarantine cleanup to a separate job
- `agent-worktree-maintain`
  - pressure-aware maintenance runner using the cleaner's registered inventory
  - pass `--state-dir` to keep its private lock and log outside `~/.codex`
  - explicit state directories must already exist, be owned by the current user,
    use private permissions, and not be symlinks
  - verified same-user lock contention is a successful skip; malformed or
    untrusted lock state exits with status 73
  - pass `--skip-legacy-purge` to forward the cleaner's purge opt-out
- `agent-worktree-purge`
  - background purge of entries left in the legacy quarantine directory
- `install-agent-worktree-ops`
  - atomically install runtime copies, render a machine-local plist, and reload launchd
  - pass `--install-only` to install and ensure the LaunchAgent is unloaded

Neither audit nor apply mode prunes Git worktree metadata. Use explicit
`git worktree prune` or `gwt prune` only after reviewing stale registrations.
