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
  - always coordinates through the canonical Codex lock first; a distinct
    state directory adds a second private lock acquired after it
  - explicit state directories must already exist, be owned by the current user,
    use private permissions, and not be symlinks
  - verified same-user lock contention is a successful skip; malformed or
    untrusted lock state exits with status 73
  - pass `--skip-legacy-purge` to forward the cleaner's purge opt-out
  - pass `--no-log` to emit output for ephemeral capture without creating a log
- `agent-worktree-purge`
  - background purge of entries left in the legacy quarantine directory
- `install-agent-worktree-ops`
  - atomically installs runtime copies only
  - never creates, loads, unloads, enables, disables, or removes a LaunchAgent
  - accepts legacy `--install-only` as an alias for the runtime-only default
- `retire-agent-worktree-scheduler`
  - checks legacy launchd state without mutation by default
  - pass `--apply` to persistently disable both known user labels, unload only
    idle jobs under those exact labels, back up the recognized current plist,
    and unlink it
  - refuses an active maintainer lock, any system-scoped legacy job or plist,
    running jobs, symlinks, hard links, unexpected ownership or permissions,
    unrecognized plist bytes, and the older untracked plist
  - holds the canonical maintainer lock through apply and rollback mutations
  - writes a private receipt and exact backup before mutation
  - pass `--rollback <receipt-directory>` to atomically restore the exact backup
    as mode `0644`; rollback leaves both labels disabled and unloaded
  - never removes runtime directories, logs, history, or iCloud/Mackup residue

Neither audit nor apply mode prunes Git worktree metadata. Use explicit
`git worktree prune` or `gwt prune` only after reviewing stale registrations.
