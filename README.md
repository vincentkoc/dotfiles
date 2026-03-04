# dotfiles

Personal dotfiles managed with [Mackup](https://github.com/lra/mackup).

## Install

```bash
git clone https://github.com/vincentkoc/dotfiles.git ~/.dotfiles
~/.dotfiles/install.sh
```

The installer bootstraps dependencies and links core shell dotfiles.

Optional: restore additional app configs managed via Mackup.

```bash
mackup restore
```

> For full macOS setup (apps, system prefs, Homebrew), see [natilius](https://github.com/vincentkoc/natilius).

## Structure

```
.zshrc / .exports / .aliases / .functions   # Shell
.vimrc / .tmux.conf                         # Editors
.mackup/                                    # Mackup app configs
userscripts/                                # UserMonkey userscripts source
install.sh                                  # Dependency installer
```

## Credits

- tmux: [gpakosz/.tmux](https://github.com/gpakosz/.tmux)
- neovim: [NvChad](https://github.com/NvChad/NvChad) & [nyoom.nvim](https://github.com/nyoom-engineering/nyoom.nvim)
