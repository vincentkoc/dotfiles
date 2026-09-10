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

### Single-pane mobile mirror

`mtt cockpit:2.1` (or `mtt %12`) mirrors exactly one existing pane at its
source dimensions. `mtt` opens a metadata-only picker; `mtt --check %12`
prints the resolved ID and dimensions without capturing or sending input.
Use `--socket /path/to/socket` for an explicit existing server.
Python 3.9+ with curses and a UTF-8 terminal are required.

For a full-phone single-pane view, run from plain SSH or a local terminal outside
tmux. Detach the old mobile tmux client first, without closing any panes. Inside
an already attached multi-pane cockpit, the outer grid still confines `mtt` to
the calling pane; `mtt` never automatically detaches clients or zooms panes.
An outer tmux also intercepts `Ctrl-b`, so use the shortcuts below from plain SSH.

The local viewport crops/pans the fixed-size screen and follows the source
cursor. Resizing the phone never resizes the source; only source-side changes
update the pad dimensions. No sessions are created or attached, and desktop
focus, zoom, layouts and sibling panes are left alone.

Input starts after the first validated frame. `Ctrl-b p` disarms input and
enables local arrows, PageUp/PageDown and Home; `Ctrl-b f` resumes follow/input.
`--read-only` never forwards input. `Ctrl-b d` or `Ctrl-b q` exits only the viewer,
leaving the source running. `Ctrl-b Ctrl-b` sends a literal Ctrl-b when interactive.
The legacy `Ctrl-]` prefix supports the same `d`, `q`, `p` and `f` actions;
`Ctrl-] Ctrl-]` sends a literal Ctrl-]. Mixed prefixes and unknown prefixed
actions are consumed locally. Ctrl-C, arrows,
backspace and ordinary keys go to the source application. Complete bracketed
paste frames are forwarded literally, up to 4096 bytes including delimiters;
incomplete or oversized frames close the viewer without sending that paste.
Typed/pasted newlines retain their normal application behavior; no Enter or
shell command is generated.

This is a monochrome, current-screen mirror, not a full terminal replica:
no tmux history, colors/attributes, mouse forwarding or copy-mode navigation.
Curses handles wide/combining glyphs; terminal/font width differences can
still affect rendering. The viewer closes on copy/other pane modes, synchronized
input, capture/send hooks, identity changes or uncertain delivery. Socket and
process births are checked locally, not atomically inside tmux; each send has a
separate non-yielding server-side identity/mode guard and acknowledgement.
Timed-out input is never retried.

### Server Profile

Use the guarded server profile instead of the full desktop installer:

```bash
git clone https://github.com/vincentkoc/dotfiles.git ~/.dotfiles
~/.dotfiles/bin/linux-server-bootstrap plan
~/.dotfiles/bin/linux-server-bootstrap audit
~/.dotfiles/bin/linux-server-bootstrap prepare
~/.dotfiles/bin/linux-server-bootstrap refresh-user
```

`prepare` installs the Ubuntu shell baseline, checksum-pinned Node 24.19.0,
pnpm 11.15.1, pinned zsh components, tmux, Git/GitHub tooling, and the portable
aliases/functions. Node and pnpm stay under the current user's home directory;
no downloaded script runs as root. The profile deliberately excludes macOS
paths, private dotfiles, Git credentials/signing, GUI apps, OpenClaw global npm
installs, Mosh, and firewall activation. Regular SSH is the safe default; add a
separately reviewed tailnet UDP policy before opting into Mosh. `prepare` does
not install, write, validate, or reload the SSH server; the Ubuntu host must
already have a working `sshd`.

The Linux tmux profile keeps the desktop status/tab colors, powerline separators,
indicators, and `tt` note/pane menus without macOS clipboard helpers or GUI setup.
On an existing server, parse it with `tmux source-file -n ~/.tmux.conf.local`,
then reload only that file with `tmux source-file ~/.tmux.conf.local`; do not
reload the full desktop configuration to apply the portable appearance.
The Linux `prefix r` and edit-config bindings reload only the local profile.
Plain Ctrl-L belongs to the foreground application and does not erase scrollback;
`prefix +` uses tmux's native zoom. `prefix R` requests a confirmed terminal-state
reset only for the same local foreground shell. It sends no shell commands and
does not run `stty` or reset an agent/SSH pane. If foreground identity cannot be
verified, it refuses the operation.

Agent attention hooks only style their own unmarked pane. They leave custom pane
styles, titles, other sessions, and terminal backgrounds alone. Repeated unchanged
states do not rewrite options or refresh clients. Existing attention-colored
styles without pane-local ownership state are left untouched. Reconnect-variable
checks read the global list, preserving separate session overrides and custom
entries. Shell startup retains all
noninteractive helper functions; login shells avoid loading the profile twice,
and inherited private cockpit scratch paths avoid another tmux query.

Snapshots include owner state and exit status; recovery still reads legacy
eight-column files. Exited session IDs are retained only with matching saved
server, pane and process-start evidence. Reaped owners without that evidence
remain unresolved in the new snapshot; prior recovery history is preserved.
Cold recovery checks every selected session's topology authorization before
creating the first session, including direct snapshot-helper invocation.

`refresh-user` reruns only the user-owned portion: safe directory modes,
public shell links, pinned prompt tooling, Node/pnpm, and the public Codex
launcher. It never invokes sudo, package management, firewall, account, or SSH
server mutations. PNPM globals live directly in `PNPM_HOME`, and the server
profile adds `cx` as an argument-preserving `codex --no-alt-screen` shortcut.
After the complete safety preflight, the owned dotfiles checkout and its
managed `codex`, `dotfiles-audit`, and `linux-server-bootstrap` source launchers
are normalized to mode `0755`. A fresh host gets the public Codex wrapper; an
existing, user-owned executable link from `~/.local/bin/codex` to the official
standalone target under `~/.codex/packages/standalone/current/bin/codex` is
preserved only when `current` resolves through owned, non-world-writable
directories to an unlinked mode-`0755` binary beneath the standalone
`releases` directory. Other Codex launcher files or links are rejected. The
private `~/.codex` surface remains host-local; no hooks, global instructions,
model catalog, credentials, QuickSSH, Mosh, or OpenClaw launcher configuration
is linked.

Obtain the SHA256 fingerprint of the designated recovery public key through an
independent trusted path, then explicitly enable the temporary proof phase:

```bash
DOTFILES_SERVER_ADMIN_USER="$(id -un)" \
DOTFILES_SERVER_EXPECTED_KEY_SHA256='SHA256:replace-with-designated-key-fingerprint' \
  ~/.dotfiles/bin/linux-server-bootstrap enable-auth-proof
```

The activation is bound to the current boot and expires after 15 minutes. If
the proof session is abandoned, remove the temporary exposure explicitly:

```bash
~/.dotfiles/bin/linux-server-bootstrap cleanup-auth-proof
```

Open the proof connection with a new transport, not an existing SSH control
socket:

```bash
ssh -o ControlMaster=no -o ControlPath=none <tailnet-host>
~/.dotfiles/bin/linux-server-bootstrap prove-second-session
```

Run `lockdown` from the independent original Tailscale SSH connection. The
proof requires the freshly authenticated public key to match the externally
supplied fingerprint and unchanged `authorized_keys`; it is boot-bound, expires
after 15 minutes, and records a distinct source connection. Successful proof
immediately removes the temporary exposure while retaining the proof evidence
needed by `lockdown`. This identifies the designated public key, not the agent
or service that supplied it. Lockdown verifies the peer with Tailscale WhoIs,
requires UFW IPv6 support, permits SSH and Gateway HTTPS only on `tailscale0`,
and transactionally publishes the owned SSH policy without clobbering unknown
content. It asserts effective password, keyboard-interactive, root, public-key,
authentication-method, auth-info, X11, and agent-forwarding values before and
after reload. Use another nonmultiplexed login for the final `audit`; it must
report the locked values, including `ssh:expose_auth_info=no` and
`ssh:user_auth_file=absent`. Override the interface or ports with the documented
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
