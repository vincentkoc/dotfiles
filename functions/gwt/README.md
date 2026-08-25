# gwt

`gwt` shell module.

Exports:

- `gwt clone`
- `gwt new`
- `gwt ls`
- `gwt audit`
- `gwt clean`
- `gwt cd`
- `gwt rm`
- `gwt prune`
- `gwt sparse ...`

Key responsibilities:

- sparse-checkout profile application
- shared `node_modules` bootstrap for pnpm repos
- pretty/default worktree listing with `--raw`, `--plain`, `--color`, and `--no-color`
- unified worktree discovery across:
  - the current repository's registered Git worktrees
- agent worktree cleanup front doors
- fail-closed external worktree storage validation for mutating and audit commands

External storage behavior:

- Configured hosts mount an encrypted, case-insensitive APFS volume directly at
  canonical `~/.codex/worktrees`; a symlinked `~/.codex/worktrees` is rejected.
- The root-owned system policy binds the exact UUID and host marker, requires an
  external device with ownership enabled, disabled Spotlight, persistent Time
  Machine volume exclusion, and at least 200 GiB plus 10% free.
- `gwt new`, `add`, `rm`, `audit`, `clean`, and `prune` stop before worktree
  access when the configured volume is absent, wrong, or replaced by a writable
  internal fallback. Missing policy also fails closed when the direct mount or
  sealed backing directory proves the host is configured. `gwt root` remains
  informational.
- The guard validates both lexical and resolved containment. A worktree on the
  direct mount still resolves through Git to its canonical owning checkout;
  physical aliases beneath `/Volumes` are never independent indexing roots.
- Raw `git worktree prune` is unsafe while required storage is absent because
  Git can mistake temporarily unavailable registrations for stale worktrees.
- Global `TMPDIR`, Codex sessions/databases/logs, tmux state, and package caches
  remain internal. Use `external-tmp <command...>` for opt-in per-process scratch.

Cleanup behavior:

- `gwt audit` is metadata-immutable, rejects `--apply`, and reports foreign or stale paths.
- `gwt audit --machine` suppresses the wrapper preamble and emits the cleaner's
  single compact schema-v1 JSON count line. It never prints worktree paths.
- `gwt clean` deliberately runs pressure maintenance immediately with `--force`.
- `gwt clean --machine` is rejected because machine output is audit-only.
- Both cleanup wrappers reject repository/Codex-home scope overrides.
- The module locator chooses the local functions tree; `gwt` then resolves its
  physical source checkout and uses the adjacent cleanup helpers.
- If the source checkout is in CloudDocs or has no helper, cleanup falls back
  only to the installed Application Support runtime.
- Hardcoded Git paths, `~/.dotfiles`, CloudDocs, and `~/bin` are never direct
  cleanup-helper fallbacks.
- `gwt rm` resolves targets to an absolute path registered to the current Git common dir.
- `gwt rm` never prunes unrelated stale worktree registrations.
- `gwt prune` is the only wrapper command that prunes worktree metadata.
- `gwt prune` requires the configured external worktree volume to be present.

The cleaner retains no-follow state directory and artifact descriptors, then
opens the unique database through a relative SQLite `mode=ro` URI in an isolated
child anchored to that directory. It verifies `query_only`, schema, and the
database/WAL descriptors SQLite opened. An unsandboxed reader can still update
an active WAL shared memory file, so headless zero-touch audits also require the
private scheduler's physical-path write-denial sandbox.
