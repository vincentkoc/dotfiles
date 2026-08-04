# dotfiles

Personal dotfiles managed with [Mackup](https://github.com/lra/mackup).

## Install

```bash
git clone https://github.com/vincentkoc/dotfiles.git ~/.dotfiles
~/.dotfiles/install.sh
```

The installer bootstraps dependencies and links core shell dotfiles.

Git uses the dedicated SSH signing-only key at
`~/.ssh/git_signing_vincentkoc_ieee`. Keep a local `.ssh/allowed_signers` file
in the dotfiles root. It is intentionally ignored from git, and the installer
will stop if it is missing.

Create it with:

```bash
dotfiles_root="$HOME/.dotfiles"
if [[ "$(uname)" == "Darwin" ]]; then
  dotfiles_root="$HOME/Library/Mobile Documents/com~apple~CloudDocs/dotfiles"
fi
mkdir -p "$dotfiles_root/.ssh"
printf '%s namespaces="git" %s\n' \
  "$(git config --file "$dotfiles_root/.gitconfig" --get user.email)" \
  "$(cat ~/.ssh/git_signing_vincentkoc_ieee.pub)" \
  > "$dotfiles_root/.ssh/allowed_signers"
```

Optional: restore additional app configs managed via Mackup.

```bash
mackup restore
```

> For full macOS setup (apps, system prefs, Homebrew), see [natilius](https://github.com/vincentkoc/natilius).

## Windows and WSL2

WSL2 is the canonical Unix development environment. Run `./install.sh` inside
WSL, then verify it with `dotfiles-audit`. Keep tmux, Linux worktrees, SSH,
and cleanup inside WSL. Native Windows can also be an intentional ARM64
operator for Git, Node.js, PowerShell, GitHub CLI, and Codex without mounting
Windows drives inside WSL.

Plan, apply, check, or roll back the native operator setup:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\native-operator.ps1 -Mode Plan
powershell -ExecutionPolicy Bypass -File .\windows\native-operator.ps1 -Mode Apply
powershell -ExecutionPolicy Bypass -File .\windows\native-operator.ps1 -Mode Check
powershell -ExecutionPolicy Bypass -File .\windows\native-operator.ps1 -Mode Rollback
```

The operator keeps `git`, `node`, `npm`, `npx`, `pwsh`, `gh`, and `codex`
native. It adds argument-safe WSL bridges for `tt`, explicit-path
`gwt <wsl-repo-path> ...`, `dots`, `wgit`/`wg`, `wcx`/`cxw`, `wssh`, and
`wdeepclean`. Set `DOTFILES_WSL_DISTRO` if the distro is not named `Ubuntu`.
Apply receipts and profile backups live under
`%LOCALAPPDATA%\vincent-dotfiles\native-operator`.

## Structure

```
.zshrc / .exports / .aliases / .functions   # Shell entrypoints
functions/                                  # Modular sourced shell features
bin/agent-worktree-ops/                     # Agent worktree cleanup tools
bin/terminal-sync                           # Ghostty/full font pack/tmux parity audit and repair
bin/dotfiles-platform                       # macOS/Linux/WSL platform detection
bin/dotfiles-audit                          # portable shell/link/tool audit
functions/system/deepclean.zsh              # Dry-run-first Mole + worktree cleanup
windows/                                    # Native PowerShell bridge into WSL
git-sparse/                                 # Per-repo sparse-checkout profiles
.vimrc / .tmux.conf                         # Editors
.mackup/                                    # Mackup app configs
userscripts/                                # UserMonkey userscripts source
install.sh                                  # Dependency installer
```

## Credits

- tmux: [gpakosz/.tmux](https://github.com/gpakosz/.tmux)
- neovim: [NvChad](https://github.com/NvChad/NvChad) & [nyoom.nvim](https://github.com/nyoom-engineering/nyoom.nvim)
