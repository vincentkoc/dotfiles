# bin

Command-line tools and grouped tool families.

Top-level wrappers:

- `agent-worktree-clean`
- `agent-worktree-maintain`
- `agent-worktree-purge`
- `codex` - launch the installed CLI, preserving inherited GitHub credentials and filling missing token variables from native `gh` when available; supports opt-in constrained-network and heavy-work admission controls
- `codex-github-mcp` - start GitHub MCP over stdio with inherited credentials or native `gh` authentication; forwards extra server arguments
- `codex-hooks` - render the effective hooks file from stable integration fragments while preserving unknown user hooks and concurrent-edit safety
- `discrawl` - preserve explicit remote auth or load `openclaw-crawl/remote.env` from the trusted XDG config location before dispatching to a real backend
- `install-agent-worktree-ops`
- `retire-agent-worktree-scheduler`
- `mtt` - local mobile tmux helper that opens the pane picker on this machine; `mtt restore` unzooms/retiles if layout gets weird
- `mttc` - connect over `mosh`, then jump into remote `mtt` or `tt`
- `sublime-sync` - verify or restore Sublime User settings and Package Control plugins from a portable bundle
- `task-runtime` - check configurable disk reserve before new heavy work and atomically update one bounded task phase receipt
- `tt` - create or attach tmux sessions, including a `mobile` pane-picker profile; agent-driven topology changes require an exact `TT_OPERATOR_TMUX_SCOPE=<action>:<target>`, while interactive use and isolated fixtures remain available

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

`codex --constrained-network` forwards the natively supported
`--disable unbounded_connection_retries` flag. It is opt-in and does not promise
an exact failure timeout. For new work expected to write substantial data, use
`codex --heavy-work`; tune `CODEX_DISK_RESERVE_GIB`,
`CODEX_PLANNED_WRITE_BYTES`, or their matching command options.

Scripts that need durable phase state can call `task-runtime phase` with an
explicit artifact root. It retains one locked JSON receipt; bounds files, bytes,
entries, directories, state reads, phases, and lock wait; and returns a small
JSON error on stdout when a budget is exhausted. Omit the command entirely when
no retained output is required. Phase receipts can include owner, source/input
identity, acceptance, blocker/fingerprint/retry event, and last-artifact
kind/pin/retention/closeout fields. `task-runtime retry` is a read-only gate: it
permits no process action and blocks an unchanged failure unless its recorded
allowance or exact permitting event is present.

`codex-hooks register --integration-id <id> --fragment <file> --target <file>`
is the only supported writer for effective Codex hooks. Integration fragments
contain one `hooks` object and use stable IDs such as `dotfiles.core` or
`tokenjuice.post-tool-use`. The renderer locks, rechecks, and atomically replaces
the target; unknown hooks survive, and fragment order does not depend on install
order.

Codex cockpit snapshots retain an exited owner's exact recovery identity and
record its exit status separately. `tt status` reports exited and failed owners;
it does not rename, close, or relaunch their panes.

Let enabled plugins own their MCP registrations, including Computer Use. Avoid
duplicate `[mcp_servers.computer-use]` overrides and paths into versioned plugin
caches; plugin launchers manage their binary location and working directory.
