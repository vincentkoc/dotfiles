# functions

Shell modules sourced by `.functions`.

Current modules:

- `gwt/`
  - `gwt.zsh`: git worktree helpers, sparse-checkout profiles, worktree audit/cleanup entrypoints
- `system/`
  - `update.zsh`: `up` system/package update workflow
  - `doctor.zsh`: `doctor` diagnostics workflow

Add new shell features here as focused folders instead of growing `.functions` indefinitely.

Install/runtime notes:

- installer symlinks this directory to `~/functions`
- `.functions` prefers `DOTFILES_FUNCTIONS_ROOT`, then falls back to the dotfiles repo copy
