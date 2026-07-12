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
WSL, then verify it with `dotfiles-audit`. Keep repositories, Git signing,
SSH, tmux, Codex CLI, and cleanup inside WSL instead of duplicating them in
native Windows.

Native Windows gets a small PowerShell bridge:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\install.ps1
```

It adds `dots`, `wgit`/`wg`, `wcx`/`cxw`, `wssh`, and `wdeepclean` commands
that execute through the configured WSL distro. Set `DOTFILES_WSL_DISTRO` if
the distro is not named `Ubuntu`.

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
