# bin

Command-line tools and grouped tool families.

Top-level wrappers:

- `agent-worktree-clean`
- `agent-worktree-maintain`
- `agent-worktree-purge`
- `codex` - launch the installed CLI, preserving inherited GitHub credentials and filling missing token variables from native `gh` when available
- `codex-github-mcp` - start GitHub MCP over stdio with inherited credentials or native `gh` authentication; forwards extra server arguments
- `discrawl` - preserve explicit remote auth or load `openclaw-crawl/remote.env` from the trusted XDG config location before dispatching to a real backend
- `install-agent-worktree-ops`
- `retire-agent-worktree-scheduler`
- `mtt` - local mobile tmux helper that opens the pane picker on this machine; `mtt restore` unzooms/retiles if layout gets weird
- `mttc` - connect over `mosh`, then jump into remote `mtt` or `tt`
- `sublime-sync` - verify or restore Sublime User settings and Package Control plugins from a portable bundle
- `tt` - create or attach tmux sessions, including a `mobile` pane-picker profile; `tt restore [target]` repairs zoomed/tiled layouts

Tool folders:

- `agent-worktree-ops/`
  - cleanup, maintenance, purge, runtime installation, and explicit legacy scheduler retirement
- `bash-completion/`
  - bash completion scripts
- `zsh-completion/`
  - zsh completion scripts

## Codex MCP

Register the stable launcher path after linking `~/bin`:

```sh
codex mcp add github -- "$HOME/bin/codex-github-mcp"
```

Add `env_vars = ["GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_PAT_TOKEN"]` to the
`[mcp_servers.github]` table in `~/.codex/config.toml` so both CLI and desktop
launches can forward inherited credentials. The helper preserves each nonempty
value, fills missing values from the canonical token then the legacy token, and
only queries native `gh auth token` when both are empty. It never uses `ghx` or
starts an interactive login. Missing authentication fails GitHub MCP clearly but
does not prevent the general `codex` launcher from starting.

Let enabled plugins own their MCP registrations, including Computer Use. Avoid
duplicate `[mcp_servers.computer-use]` overrides and paths into versioned plugin
caches; plugin launchers manage their binary location and working directory.
