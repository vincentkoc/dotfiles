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
will stop if it is missing or does not contain exactly one canonical entry for
the signing identity. The entry authorizes Git signatures and signed fleet
cleanup bundles:

```text
vincentkoc@ieee.org namespaces="fleet-cleanup-bundle-v1,git" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMYJHwFnesHbwtPLErQBRMffbET0Wrbzh+PjkndZRNyy
```

The expected fingerprint remains
`SHA256:OIEOnMWCJeKhWpBNlB42wwPuUG5gsC8Crq1ibnt7ylQ`. Verify the public key,
then create a new trust file. Fleet Git config must use the stable literal
`~/GIT/_Perso/dotfiles/.ssh/allowed_signers`; resolve it only when accessing
the filesystem:

```bash
allowed_signers="$HOME/GIT/_Perso/dotfiles/.ssh/allowed_signers"
ssh-keygen -lf ~/.ssh/git_signing_vincentkoc_ieee.pub
test ! -e "$allowed_signers" || {
  printf 'refusing to overwrite existing signer entries: %s\n' "$allowed_signers" >&2
  exit 1
}
mkdir -p "$(dirname "$allowed_signers")"
umask 077
printf '%s\n' \
  'vincentkoc@ieee.org namespaces="fleet-cleanup-bundle-v1,git" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMYJHwFnesHbwtPLErQBRMffbET0Wrbzh+PjkndZRNyy' \
  > "$allowed_signers"
```

If the file already contains unrelated signer entries, preserve them and
replace only the entry for `vincentkoc@ieee.org` and the key above. The private
fleet bootstrap performs that normalization atomically.

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

## Headless Linux servers

Use the guarded server profile instead of the full desktop installer:

```bash
git clone https://github.com/vincentkoc/dotfiles.git ~/.dotfiles
~/.dotfiles/bin/linux-server-bootstrap plan
~/.dotfiles/bin/linux-server-bootstrap audit
~/.dotfiles/bin/linux-server-bootstrap prepare
```

`prepare` installs the Ubuntu shell baseline, checksum-pinned Node 24.19.0,
pnpm 11.15.1, pinned zsh components, tmux, Git/GitHub tooling, and the portable
aliases/functions. Node and pnpm stay under the current user's home directory;
no downloaded script runs as root. The profile deliberately excludes macOS
paths, private dotfiles, Git credentials/signing, GUI apps, OpenClaw global npm
installs, and firewall activation.

After Tailscale and a public key are proven, open two regular OpenSSH sessions
over Tailscale. Run `prove-second-session` in the second and `lockdown` in the
first. The proof is boot-bound, expires after 15 minutes, and must come from
another TTY. Lockdown permits SSH and Gateway HTTPS only on `tailscale0`,
disables password and root SSH, validates `sshd` before reload, and leaves
automatic reboots off. Override the interface or ports with the documented
`DOTFILES_SERVER_*` environment variables.

## Structure

```
.zshrc / .exports / .aliases / .functions   # Shell entrypoints
functions/                                  # Modular sourced shell features
bin/agent-worktree-ops/                     # Worktree cleanup and legacy scheduler retirement
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
