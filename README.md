# dotfiles

Personal dotfiles managed with [Mackup](https://github.com/lra/mackup).

## Install

```bash
git clone https://github.com/vincentkoc/dotfiles.git ~/.dotfiles
~/.dotfiles/install.sh
```

The installer bootstraps dependencies and links core shell dotfiles.

If SSH commit signing is your default Git mode, keep a local
`.ssh/allowed_signers` file in the dotfiles root. It is intentionally ignored
from git, and the installer will stop if it is missing.

Create it with:

```bash
dotfiles_root="$HOME/.dotfiles"
if [[ "$(uname)" == "Darwin" ]]; then
  dotfiles_root="$HOME/Library/Mobile Documents/com~apple~CloudDocs/dotfiles"
fi
mkdir -p "$dotfiles_root/.ssh"
printf '%s namespaces="git" %s\n' \
  "$(git config --file "$dotfiles_root/.gitconfig" --get user.email)" \
  "$(cat ~/.ssh/id_ed25519.pub)" \
  > "$dotfiles_root/.ssh/allowed_signers"
```

Optional: restore additional app configs managed via Mackup.

```bash
mackup restore
```

> For full macOS setup (apps, system prefs, Homebrew), see [natilius](https://github.com/vincentkoc/natilius).

## Structure

```
.zshrc / .exports / .aliases / .functions   # Shell entrypoints
functions/                                  # Modular sourced shell features
bin/agent-worktree-ops/                     # Agent worktree cleanup tools
git-sparse/                                 # Per-repo sparse-checkout profiles
.vimrc / .tmux.conf                         # Editors
.mackup/                                    # Mackup app configs
userscripts/                                # UserMonkey userscripts source
install.sh                                  # Dependency installer
```

## Credits

- tmux: [gpakosz/.tmux](https://github.com/gpakosz/.tmux)
- neovim: [NvChad](https://github.com/NvChad/NvChad) & [nyoom.nvim](https://github.com/nyoom-engineering/nyoom.nvim)
