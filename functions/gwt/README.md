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
- unified worktree discovery across:
  - `~/.codex/worktrees/<repo-slug>`
  - `<repo>/.claude/worktrees`
  - `<repo>/.worktrees`
- agent worktree cleanup front doors
