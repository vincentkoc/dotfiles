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

## GitHub Throughput

`gh` and `ghx` use the same routing helper. Run `ghx-bootstrap` once after linking
`~/bin`. It creates a private `~/.ghx/config.yaml` only when absent, preserving
upstream defaults and existing configuration. ghx 1.5.4 otherwise ignores
`GHX_GH_PATH` when no config file exists. The helper neither installs a backend
nor restarts an existing daemon. Without native `gh`, both wrappers fail clearly.

The proxy is reserved for `pr view`, `pr checks`, `issue view`, and `run view`
with a numeric item ID, explicit repository, and `--json`. Management commands
`xdaemon` and `xcache` still reach ghx. By default, API requests, writes, auth, implicit
repository reads, `GH_REPO`-dependent calls, interactive modes, and unknown
options go directly to native `gh`. This preserves stdin and avoids relying on
the proxy's API method classification.

```sh
ghx pr view 123 -R example/repo --json number,headRefOid,state
ghx --ttl 15 pr view 123 -R example/repo --json number,headRefOid,state
ghx --no-cache pr view 123 -R example/repo --json number,headRefOid,state
ghx api repos/example/repo/pulls/123 --cache 15s
ghx --no-cache api repos/example/repo/pulls/123
```

Keep repository, item, and field spelling consistent for repeated reads.
Upstream ghx still keys entries by the current branch and exact arguments:
these wrappers do not combine caches across worktrees. Explicit endpoint GETs
with native `api --cache 15s` are an opt-in alternative; API caching is never
enabled automatically. Cache only endpoints whose stale responses are safe.

Use `--no-cache` (before the command), or `GHX_NO_CACHE=1`, for exact-head decisions
and post-write verification. Native writes do not invalidate existing ghx
entries. An uncached read bypasses rather than refreshes those entries.
Combining an uncached request with `api --cache` is rejected. `--ttl <integer
seconds>` only applies to eligible proxy reads; native routes reject it instead
of implying a TTL was honored. The wrappers do not accept `--ttl=...` or
`--no-cache=...`.

New native invocations unset `GH_FORCE_TTY` and disable color. Non-TTY stdin
also selects `GH_PROMPT_DISABLED=1` and `GH_PAGER=cat`, except auth, which retains
the caller's prompt/pager policy. Existing daemon environment is unchanged.
API jq filters compact actual object results through native `gh`'s formatter;
scalars, arrays, and unfiltered responses retain native output. No external
`jq` is required. The deployed low-data guard belongs at each entrypoint, once,
before this helper; the helper never calls one entrypoint from the other.

### Legacy Inner-Shim Auth Repair

`ghx-auth-repair` is an explicit Python 3.9+ migration for one legacy 349-byte
inner shim, SHA-256
`1c5c55becb3aaad20409892a3b3e40c18f15a6447efdffd067575346e292d420`.
It adds only a leading `auth` bypass to independently verified native `gh`.
It does not install or generate shims, change the shared router or PATH,
authenticate, enroll an Octopool client, or start a daemon. Non-auth arguments,
including `--no-cache`, keep their original behavior.

Audit the target and native binary first. Supply absolute paths and their
current lowercase SHA-256 digests; target and backup parent directories must
be canonical, user-owned, and not group/world writable. Inspection is the default:

```sh
ghx-auth-repair --target /absolute/path/to/legacy/ghx \
  --expected-sha256 TARGET_SHA256 --native-gh /absolute/path/to/native/gh \
  --native-sha256 NATIVE_SHA256
```

To apply, append `--apply --backup /absolute/private-directory/ghx.before`.
Create that private directory with mode `0700` beforehand. The backup must not
exist; it retains the original bytes with mode `0600`. The helper rechecks the
target identity and both digests before atomically replacing the target.
Do not run it alongside another editor or installer. A failure can leave the
private backup for inspection; there is no automatic rollback.

The target must be a singly linked regular executable owned by the current
user. Unknown bytes, a symlink target, native scripts/wrappers, changed pins,
ACLs, file flags, and unsupported extended attributes are refused. On macOS,
`com.apple.provenance` is preserved and verified; other attributes are refused.
On Linux, all extended attributes (including ACLs) are refused. Mode, UID,
GID, atime, and mtime are preserved; replacement necessarily changes inode and
ctime/birth time. Supported platforms are macOS and Linux.

The inserted command pins the supplied native **path**, not its digest at
runtime. A symlink is allowed for native `gh`; its resolution and digest are
verified during migration. Prefer a versioned binary path when later symlink
updates must not change auth dispatch. An exact repaired shim with the same
path is a no-op when supplied with its current digest and a verified native
binary. A different path pin is not an automatic upgrade: audit it separately.

### Optional Octopool Reads

Both entrypoints can send a narrow set of public REST reads to Octopool before
considering ghx. This is explicitly activated per host, never by finding a
binary or login alone. Install the reviewed Octopool 0.6.3 binary at a stable
versioned path, native `gh`, and `jq`. Keep existing entrypoints and their
low-data hooks; the routing change is in `bin/gh-support/route.sh`. No daemon
restart or ghx configuration change is needed.

After independently verifying the binary and native CLI paths, create the
private host-local `~/.config/gh-routing/octopool.json` with exactly these keys:

```json
{
  "enabled": true,
  "octopool_path": "/absolute/versioned/octopool",
  "gh_path": "/absolute/native/gh"
}
```

The file is parsed as JSON, not sourced. Unknown keys, relative paths, wrapper
pins, missing executables, and symlinked configuration files disable activation.
A valid configuration pins native `gh` for every route, including writes,
`--no-cache`, and ghx's backend. This avoids PATH selecting another Octopool
version or a wrapper. Existing `GHX_GH_PATH` and `OCTOPOOL_GH_PATH` cannot override
the selected backend.

Relay reads additionally require a regular, nonsymlink saved `auth.json` for
exactly `https://octopool.dev`, pool `maintainers`, and a nonempty caller token.
Octopool's native auth location is used: `~/Library/Application
Support/octopool/auth.json` on macOS, or
`${XDG_CONFIG_HOME:-$HOME/.config}/octopool/auth.json` on Linux. Keep caller
credentials host-local; never copy auth files between hosts. Missing or invalid
auth, config, `jq`, or binaries leaves the prior native/ghx route in use.

Only literal relative `api repos/openclaw/openclaw/...` and
`api repos/openclaw/octopool/...` GETs can use the relay:

- PR lists and numeric PR details, files, commits, reviews.
- Issue lists and numeric issue details or comments.
- Commit lists and single-segment commit refs, check runs/suites, status/statuses.
- Numeric check runs or check-suite check-run lists.
- Actions run lists, numeric runs, attempts and jobs; numeric jobs; workflow
  lists, numeric workflows and their runs. Logs and artifacts stay native.

Repository-root metadata stays native because its permission fields depend on
the authenticated caller.

Supported flags are `--paginate`, `--slurp` (with pagination and without jq),
`--jq <filter>`, `-q <filter>`, `--jq=<filter>`, `-X GET`, `--method GET`, and
`--method=GET`. Attached `-q...` and `-XGET` stay native. Allowed query parameters
are positive `page`, `per_page` from 1 to 100, `state=open|closed|all`, and
`filter=latest|all`. All other endpoints, options, headers, fields, input files,
hosts, URL/path escapes, and placeholders stay native. Nonempty `GH_HOST` or
`GH_REPO` disables relay routing. Top-level commands retain their prior routes.

On the relay path, inherited Octopool caller/admin tokens and destination/backend
overrides are removed. The service, pool, and native backend are pinned; native
GitHub credentials remain available only for local fallback. API jq filters use
the same object-to-JSONL transformation on both routes. No ghx cache surrounds
Octopool. Relay failures retain Octopool's exit status and native-fallback policy;
the wrapper neither retries failures nor performs login.

Use `GH_OCTOPOOL=0` to disable relay routing immediately while retaining valid
native pins. Remove the activation file (or set `enabled` to `false`) to restore
legacy PATH discovery as well. `--no-cache` and `GHX_NO_CACHE=1` always bypass
Octopool and ghx. `--ttl` never selects Octopool. `OCTOPOOL_FRESH=1` requests
relay revalidation but is not the native escape hatch for exact-head decisions.

## Repo Fetch Policy

`git-throughput` changes only the selected repository's common Git config.
It sets `fetch.prune=true`, `fetch.pruneTags=false`, and, only for explicitly
named review remotes, `skipFetchAll=true` plus `tagOpt=--no-tags`. It does not
fetch, prune, delete refs, narrow refspecs, change upstreams, change origin's tag
behavior, enable maintenance, or modify global Git settings.

Review active lanes and remotes first, then capture the current common-config
SHA-256. Provide an unused receipt directory under a private parent:

```sh
git-throughput apply --repo /path/to/repo \
  --expect-sha256 <reviewed-config-sha256> --receipt /private/path/receipt \
  --review-remote contributor
git-throughput rollback --repo /path/to/repo \
  --receipt /private/path/receipt --rollback-receipt /private/path/undo-receipt
```

The helper holds Git's real `config.lock`, checks the preimage again, preserves
file metadata, and writes atomically. Receipts contain private before/after
config files and digests. Rollback refuses a changed postimage. Do not commit or
share receipts. Config lock contention and drift are stop conditions, not retry
or lock-removal instructions.

Refresh one explicit ref when a lane needs it:

```sh
git-throughput refresh --repo /path/to/repo --remote origin \
  --source refs/heads/main --destination refs/remotes/origin/main
git-throughput refresh --repo /path/to/repo --remote origin \
  --source refs/pull/123/head --destination refs/remotes/origin/pr-123
```

Refresh disables tag following, pruning, configured wildcard mappings,
`FETCH_HEAD` writes, submodule recursion, and auto-maintenance for that command.
It updates only a ref below `refs/remotes/<remote>/`; source and destination
must be explicit full refs. It honors installed low-data protection. Maintenance
and broader refspec narrowing require a separate owner/active-upstream audit;
these helpers never schedule them.
