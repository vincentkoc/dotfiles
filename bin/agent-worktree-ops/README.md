# agent-worktree-ops

Agent worktree cleanup and maintenance tools.

Bins in this folder:

- `worktree-storage-guard`
  - reads the private system policy
    `external-worktree-storage.v2`; schema v1 is rejected
  - reads the root-owned, non-writable policy at
    `/Library/Application Support/agent-worktree-ops/external-worktree-storage.json`
  - treats a missing contract as unconfigured only when the canonical path has
    neither a direct mount nor the sealed `root:wheel` mode-0000 backing directory
  - requires the exact UUID to be mounted directly at canonical
    `~/.codex/worktrees` as case-insensitive APFS with policy-matching
    encryption, ownership enabled, and a user-owned mode-0700 mounted root
  - requires policy `encrypted` to be an exact JSON boolean and requires the
    observed encryption boolean to match it exactly; unknown or mismatched
    encryption fails without disclosing either value
  - treats modern `diskutil` `Encryption` as authoritative whenever present;
    malformed modern values are unknown, and legacy `Encrypted` is consulted
    only when the modern field is absent
  - exposes static integration discovery only through
    `worktree-storage-guard --capabilities --json`; this returns the v1 exact-
    boolean capability contract before reading environment configuration,
    filesystem state, or `diskutil`, and rejects runtime options
  - requires at least 200 GiB and 10% free, an exact host marker, external-device
    location, disabled Spotlight indexing, and a UUID-tracked Time Machine volume
    exclusion verified with `tmutil isexcluded`
  - requires the exact `openclaw-managed` and `.pnpm-store/openclaw` child
    contracts; every path component must remain a real, user-owned mode-0700
    directory on the mounted volume's filesystem
  - reserves `openclaw-managed` for OpenClaw's registered-worktree lifecycle and
    `.pnpm-store/openclaw` as a disposable store owned by `dotfiles-private`
  - rejects symlinked mountpoints or parent components, wrong UUID/filesystem,
    missing, overlapping, symlinked, cross-filesystem, wrongly owned, or
    wrongly permissioned children, writable internal fallback, and hot
    disappearance
  - when absent, the underlying mountpoint must remain `root:wheel` mode `0000`
- `agent-worktree-clean`
  - dry-run/apply cleanup of idle worktrees and stale artifacts
  - only registered, non-main worktrees from the selected Git common dir are actionable
  - foreign, unregistered, non-Git, and stale-registration paths are report-only
  - requires exactly one same-user, single-link `state*.sqlite` database; accepts
    either the database alone or the database with both `-wal` and `-shm`
    sidecars, and refuses ambiguous, asymmetric, journaled, or unexpected state
    artifacts before classifying worktrees
  - retains no-follow descriptors for the state directory and every accepted
    artifact, then reads a relative SQLite `mode=ro` URI from an isolated child
    anchored to that directory
  - enables and verifies `query_only`, verifies the `threads` schema and the
    database/WAL descriptors SQLite opened, then rechecks the anchored directory
    and artifact identities after the child exits
  - SQLite `mode=ro` alone may update an active WAL `-shm` file. Scheduled
    zero-touch audits must additionally run inside the private scheduler's
    physical-path write-denial sandbox; there is no writable fallback.
  - pass `--machine` for one compact, sorted schema-v1 JSON audit line with
    counts only; it is incompatible with `--apply` and `--list-registered`
  - machine mode runs every Git, `lsof`, and SQLite-reader child through one
    bounded supervisor; Git hooks, fsmonitor, pagers, lazy fetches, credential
    prompts, and tmux probing are disabled for that mode
  - each machine child is tracked by its positive PID, per-run birth token, and
    owning `Popen`; timeout recovery signals only that unreaped direct child,
    never a process group or descendant
  - a child-only liveness descriptor must reach EOF after reap, so a forked
    survivor that inherited it fails the audit before JSON is emitted; the
    in-memory child registry must also be balanced
  - machine subprocesses have a 30-second deadline and an 8 MiB combined output
    limit; child descriptors are closed by default except for the exact
    SQLite protocol descriptors and liveness descriptor passed by the wrapper
  - apply mode revalidates dirtiness, reachability, sessions, tmux panes, and process CWDs
  - removals are serial, non-force `git worktree remove` operations
  - apply exits nonzero when a selected trim or removal fails
  - validates required external storage before inventory and again before every
    trim/removal mutation
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
  - validates required external storage before creating locks, logs, or state
- `agent-worktree-purge`
  - background purge of entries left in the legacy quarantine directory
- `install-agent-worktree-ops`
  - atomically installs runtime copies, including the storage guard, only
  - never creates, loads, unloads, enables, disables, or removes a LaunchAgent
  - accepts legacy `--install-only` as an alias for the runtime-only default
- `retire-agent-worktree-scheduler`
  - checks legacy launchd state without mutation by default
  - pass `--apply` to persistently disable both known user labels after proving
    no matching job is loaded, back up the recognized current plist, and
    remove it through a validated same-directory quarantine
  - refuses an active maintainer lock, any system-scoped legacy job or plist,
    any loaded matching job, symlinks, hard links, unexpected ownership or
    permissions, unrecognized plist bytes, and the older untracked plist
  - holds the canonical maintainer lock through apply and rollback mutations
  - re-discovers GUI availability after quarantine and before completion
  - writes a private receipt and exact backup before mutation
  - pass `--rollback <receipt-directory>` to atomically restore the exact backup
    as mode `0644`; rollback refuses unless both user and GUI domains prove the
    labels disabled with no matching jobs loaded
  - never removes runtime directories, logs, history, or iCloud/Mackup residue

Neither audit nor apply mode prunes Git worktree metadata. Use guarded
`gwt prune` only after reviewing stale registrations. Raw `git worktree prune`
is unsafe while required storage is absent.
