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

Cleanup behavior:

- `gwt audit` is metadata-immutable and reports foreign or stale paths without acting on them.
- `gwt clean` deliberately runs pressure maintenance immediately with `--force`.
- `gwt rm` resolves targets to an absolute path registered to the current Git common dir.
- `gwt rm` never prunes unrelated stale worktree registrations.
- `gwt prune` is the only wrapper command that prunes worktree metadata.
