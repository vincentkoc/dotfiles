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
- a custom `DOTFILES_FUNCTIONS_ROOT` wins when its physical path is local
- otherwise `.functions` prefers `~/GIT/_Perso/dotfiles/functions`, then
  `~/.dotfiles/functions`, before a local physical `~/functions`
- the generated `DOTFILES_FUNCTIONS_ROOT=~/functions` default does not override
  either canonical checkout
- if none of those roots exists, modular functions stay unloaded
- CloudDocs paths and symlinks resolving into CloudDocs are never module roots
