# gwt

`gwt` shell module.

## Git storage

Keep durable clones in their normal `~/GIT` categories. Linked worktrees share
their owner's Git objects and history. Their checked-out source files use
separate storage unless the filesystem shares those bytes.

Use full, non-promisor owners for development and maintainer work. This includes
OpenClaw review, release, merge-base, and ancestry operations. Sparse checkout limits
working files without removing history. A shallow or blobless clone can serve
an explicit browsing or proof task, but cannot own new GWT worktrees.

```sh
gwt clone https://github.com/example/project.git ~/GIT/_Perso/project --history full
gwt clone https://github.com/example/archive.git ~/GIT/_Perso/archive --history blobless
gwt new fix/example <verified-commit> --checkout full
gwt owner
```

`--checkout <profile>` and `--profile <profile>` are aliases. `--full` remains
the full-checkout alias. `--history full` disables the configured clone filter.
`--history blobless` explicitly requests `blob:none`. Without `--history`, only
an existing repository-specific `clone.filter` can enable filtering. OpenClaw
has no clone filter. No option automatically unshallows an existing owner.

Remote branch bases refresh one exact branch into its remote-tracking ref.
Refreshes disable automatic maintenance, tags, pruning, and shared `FETCH_HEAD`
writes. A failed refresh stops creation. Pass a verified local commit explicitly
when working offline. Cached refs do not prove freshness.

## Account-specific worktree roots

`DOTFILES_WORKTREES_ROOT` selects the parent for new shell-GWT worktrees;
`gwt root` reports its effective value. The default remains `~/.codex/worktrees`.
Keep server-specific paths in the host's private configuration and set the
variable in the invoking account's environment, not in shared workstation defaults.

When a server profile requires workspaces outside home directories, provision
an absolute, account-owned root outside the affected accounts' actual homes.
Verify its physical path, mount, parent permissions and trust-domain separation
before use. Root selection is placement policy, not a security boundary or
proof of those permissions. `gwt root` validates any configured external storage before reporting placement.

This setting does not relocate existing worktrees or change their Git owners,
registrations, dependency links or completion state. Before a migration, identify
each tree's producer, native owner and active users. Preserve dirty and untracked
work, leases and recovery state; changing the destination does not authorize
cleanup or maintenance activation.

Application-managed sessions and sandbox projections retain their application's
allocation, relocation and retention lifecycle. A `.git` entry does not make them
shell-GWT tasks. For example, OpenClaw's native `worktreeRoot` setting is separate
from this variable; its workspace owner must update persisted references during
relocation. Do not make an application invoke shell GWT or enroll its existing
workspaces in personal managed finish to obtain cleanup authority.

### Allocator ownership and retention

A shared parent directory does not give different allocators the same lifecycle.

| Producer | Placement and retention owner |
| --- | --- |
| Shell GWT | `DOTFILES_WORKTREES_ROOT` controls new GWT paths. Use GWT's task closeout and recorded ownership. |
| Codex CLI | In [0.166.0](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/worktree/src/settings.rs), `--worktree` shares the desktop root setting or defaults to `$CODEX_HOME/worktrees`, but CLI automatic cleanup is disabled. CLI exit does not imply removal. |
| Codex desktop app | [Codex-managed chats](https://learn.chatgpt.com/docs/environments/git-worktrees#worktree-cleanup) have app-owned cleanup. The default keeps the latest 15 trees; pinned and active chats are protected. Archiving a chat can trigger cleanup. |
| Claude Code | Native `--worktree` uses `<project>/.claude/worktrees/`. [Interactive exit](https://code.claude.com/docs/en/worktrees#clean-up-worktrees) removes a clean unnamed tree; named or work-bearing trees prompt. Noninteractive `-p` runs leave the tree and creation lock for later owner closeout. |

Claude's [`worktree.location`](https://code.claude.com/docs/en/settings-reference#worktreelocation)
setting applies only to Desktop SSH sessions. CLI worktrees, `EnterWorktree`,
isolated subagents and background sessions ignore it. It does not configure GWT.

Identify the producer and its native owner before closing a finished checkout.
Do not enroll existing app or CLI trees in GWT to obtain deletion authority.
Changing a shared root is not a retention fix and does not migrate existing trees.

## Preferred owners

Keep host-specific policy in private dotfiles. Install a regular, current-user
file at `${XDG_CONFIG_HOME:-~/.config}/gwt/storage.json`:

```json
{
  "version": 1,
  "owners": {
    "github.com/example/project": "~/GIT/_Perso/project"
  },
  "protected": ["~/GIT/_Perso/project-active-base"]
}
```

The policy selects an owner only when `gwt new` creates a new path. Existing
registered worktrees retain their owners. Selection requires matching origin
identity, a direct physical owner path, full history, and no object alternates.
Protected paths also cover linked worktrees through their common Git directory.
Owner selection, snapshots, and CoW enforce these exclusions. Native Git and
existing maintenance helpers do not read this file.
An existing branch must have the same tip in both owners. Local-only base
commits must already exist in the selected owner. Selection never migrates them.

Owner selection establishes eligibility, not full object integrity. It does
not change the behavior of repository-native worktree commands. Run native
OpenClaw PR commands from the selected owner. Do not relocate active bases.

## Cancelled or superseded work

Use `gwt cancel --reason 'superseded by ...' [--worktree <path>]` when the
current owner's work has ended without a matching PR. It records a terminal
owner disposition, preserves the branch, files, pins and other owners, and
invalidates prior release sign-offs. It does not fetch GitHub proof, scan
holders, remove the checkout or permit ordinary `gwt rm` to bypass enrollment.
Existing report-only enrollments remain report-only.

`finish-status` reports the recorded cancellation and HEAD. `finish-check`
invalidates it if HEAD changed. Resume or changed completion/proof clears it,
including updates from an older client. An unfinished removal intent refuses
cancellation. A repeated identical cancellation is a no-op.

Once the task is finalized, use the manual closeout route below to retire the
checkout. Cancellation alone does not run deletion or clear another owner's pin.

If a natively enrolled checkout directory is already absent (`ENOENT` only),
a recorded owner can still cancel it from the owning repository with
`gwt cancel --reason <text> --worktree /absolute/task/path`. The surviving
registration, native lock, admin directory, branch and owner identities must
match the enrollment, and the branch tip must be reachable from a
remote-tracking ref. That tip becomes the recorded cancellation HEAD. When
every owner is completed or cancelled at that HEAD, the literal
`gwt rm /absolute/task/path --finalized` closeout below retires the surviving
registration without PR or release proof; it rechecks the remote, owners and
pins. Unpushed commits, an appeared directory or a changed registration refuse.

After a reboot can renumber an APFS device, the recorded owner can add
`--acknowledge-device-renumbering` to that missing-checkout `gwt cancel` and
`gwt rm --finalized`. The surviving owner, common and admin directories must
keep their exact inodes under one consistent, one-to-one device mapping; the
absent checkout's recorded device must map with them and the managed root must
be on that new device. The durable enrollment is not rewritten. Without the flag,
or for a present checkout, behavior is unchanged.

If the GitHub repository was renamed after enrollment, the recorded owner can
add `--acknowledge-repository-rename <old-owner/old-name>=<new-owner/new-name>`
to the same missing-checkout `gwt cancel` and `gwt rm --finalized` (with or
without the device acknowledgment). The old slug must equal the enrolled
repository and the new slug must be what the unchanged owner's `origin`
resolves to now; the common Git directory must still match. Slugs compare
case-insensitively, as enrollment records them. The enrollment is not
rewritten, and remote reachability is checked against the current refs.

## Worktrees removed outside GWT

When an explicitly authorized removal already deleted a finished checkout, use
`gwt finish-status` to obtain its record ID and generation, then record the
observed outcome:

```sh
gwt finish-reconcile --external-removal --worktree /absolute/task/path \
  --worktree-id <id> --generation <n> --reason 'Removed with explicit operator approval'
```

This command only updates the lifecycle record. It requires a recorded owner,
completed owners, no recovery pins or native removal attempt, an absent checkout,
admin directory and registration, and the unchanged source branch in its original
repository. A repeated identical request leaves the recorded outcome unchanged.
Existing native removal attempts keep their separate receipt-based reconciliation.
The record reports `checkout: removed` and `native_attempted: false`; it does not
claim historical preservation checks that GWT did not perform. History, branches
and the path reservation remain intact. This command never removes files.

## Orphan creation locks

`gwt new --finish-managed` locks a new registration with
`gwt-finish.v2:<token>` before enrollment. If the checkout later disappears and
no lifecycle record ever matched it, ordinary prune skips the locked entry.
Run `gwt prune --orphan-locks` from the owning repository to list such
registrations; it changes nothing. `gwt prune --orphan-locks --apply` then
unlocks and runs one non-force native removal for each listed candidate only.

A candidate needs an absent directory (`ENOENT`) under the managed root, a
gwt-finish lock token, no lifecycle row, identity, lock reason or failed
creation that mentions it, and a named branch at its recorded HEAD. Its admin
directory must pass the closeout admin classification, its index must match
HEAD, and HEAD plus every admin recovery commit must be reachable from a
remote-tracking ref. Anything else is reported as retained with its reason.
Apply rechecks each candidate under the maintainer lock, never deletes
branches and restores the lock if native removal fails.

For one audited registration, use exact selection instead of the repository-wide
candidate set:

```sh
gwt prune --orphan-locks --path /absolute/missing/checkout \
  --creation-token <lock-uuid> --expected-head <full-sha> \
  --missing-path-id <prior-device:prior-inode>
```

The device/inode must come from an audit while that checkout existed; do not
guess it from the retained admin directory. This identity keeps references to
the deleted checkout root in the holder scan. The command checks only the
selected candidate, binds surviving admin contents and sibling registrations,
and returns a `recovery_sha256`. Apply the same operands with
`--apply --recovery-sha256 <reviewed-hash>`. Changed identity, lock, target HEAD,
contents, holders, lifecycle state or sibling proof retains the registration.
Sibling HEAD advances, branch switches and detached checkout transitions do not
change this proof. Sibling registration paths, order, other fields and physical
identities remain bound to the final registration observation during admission;
later additions or removals require a fresh plan. Native removal separately
records full current sibling rows and verifies preservation.

Exact selection still requires HEAD on a remote-tracking ref. Earlier admin
recovery commits may instead use the selected task branch's surviving common
reflog, as finalized closeout does. Each recovery OID must name an existing
commit. Validated commits named directly in these qualified roots need no extra
remote reachability walk. The plan binds the common reflog's identity, bytes and
OIDs, rechecks them before intent and unlock, and verifies their preservation
after removal.
Batch selection keeps its remote-only requirement.

If the filesystem device number changed, exact selection additionally accepts
`--allow-device-renumbering --prior-identity-file /absolute/private-record.json`.
Both options are required together, even for a same-device record. The default
device check remains strict. Supply an authentic historical operator record,
captured while the checkout existed, with exactly these JSON fields:

```json
{
  "schema": "gwt-orphan-prior-identity.v1",
  "path": "/managed/repo/task",
  "root": "/managed/repo",
  "owner": "/repos/repo",
  "common": "/repos/repo/.git",
  "gitdir": "/repos/repo/.git/worktrees/task",
  "path_id": [100, 101],
  "owner_id": [100, 102],
  "common_id": [100, 103],
  "gitdir_id": [100, 104],
  "lock_id": [100, 105],
  "uid": 1000,
  "lock_reason": "gwt-finish.v2:00000000-0000-4000-8000-000000000001"
}
```

Use original canonical paths and `[device, inode]` pairs, including the original
missing checkout identity; never substitute current values to manufacture a
record. The file must be caller-owned, private, regular, singly linked, at most
16 KiB and outside the checkout/admin deletion domains. Surviving owner,
common, admin and lock inodes and ownership must match. Their device mapping
must be consistent and one-to-one; the missing checkout's mapped device must
match both the current managed root and nearest surviving parent. The plan and
intent bind the original and observed identities, mapping, opt-in, and input
file identity and bytes. The holder scan uses the mapped checkout identity.
Apply must repeat the same options and unchanged record.

Exact apply records its removal intent before unlocking, uses one non-force
native removal, and verifies registration/admin absence and branch/sibling
preservation. It creates no enrollment. An interrupted or uncertain attempt
remains blocked by its receipt; do not retry it or use the batch route to
bypass that hold. A lock recreated after native failure is recorded only as
rollback evidence; it does not replace the historical admission identity.
This retires surviving administration only: already missing
unstaged and untracked files remain unaudited.

A narrowly proven unstarted exact-orphan attempt can be reconciled separately:

```sh
gwt reconcile-orphan --worktree /absolute/owning/checkout \
  --orphan-path /absolute/missing/checkout \
  --creation-token <lock-uuid> --expected-head <full-sha> \
  --missing-path-id <prior-device:prior-inode> \
  --intent-id <receipt-uuid> --intent-sha256 <raw-intent-hash> \
  --result-sha256 <raw-result-hash>
```

Repeat any original device-renumbering options and unchanged private identity
record. The selected receipt must explicitly record `native_attempted: false`,
`child: null`, a preserved native lock, and `orphan-preimage-changed` for the
missing exact registration. The operator, UUID, raw receipt hashes and current
target proof must match. Fresh admission still checks holders, locks, index,
admin contents and recovery custody. Only historical sibling observations are
excluded from this reconciliation proof; unrelated sibling progress does not
change the target's no-attempt evidence.

Review the returned digest, then repeat with
`--apply --recovery-sha256 <reviewed-hash>`. This appends one reconciliation fact;
it never unlocks, removes, enrolls, or changes the original UNKNOWN receipt.
Batch and older clients remain blocked. A subsequent exact-orphan plan can
honor only this verified receipt and fact, with fresh complete admission and
its own removal digest. Any other attempt, receipt corruption or target drift
still blocks it. Reconciliation never automatically starts removal.

## Missing enrolled checkout recovery

`gwt recover-missing` restores a missing directory only when its original Git
registration, native lock, enrollment, owners, branch and index still exist.
Obtain explicit permission to recover the named checkout first. This operation
does not recover lost unstaged or untracked files; their disposition stays
unknown. It is not `gwt new`, adoption, re-enrollment, unlock, reset or pruning.

Plan against exact retained identities, then review the returned preimage hash:

```sh
gwt recover-missing --worktree /absolute/original/checkout \
  --worktree-id <enrollment-uuid> --generation <n> \
  --expected-head <full-commit-sha> --index-sha256 <sha256>
```

The plan takes native lifecycle and maintenance coordination locks but does not
create the checkout or a recovery intent. Apply the same operands with
`--apply --recovery-sha256 <reviewed-plan-hash>`. Both phases require complete
holder visibility and unchanged admin, branch, lock, index and owner facts.
The retained Git index has a separate 32 MiB read limit and the existing
131,072-entry ceiling. Ordinary admin leaves keep their 8 MiB limit, and all
admin reads still share the aggregate budget. Larger or unsupported indexes
refuse before recovery; this does not change the accepted index formats.
Conflicts, sparse/unknown index formats, gitlinks, pending
Git or lifecycle operations, held paths and appeared destinations refuse.

When only the managed repository container and its checkout are missing, the
existing managed root must still resolve to a verified physical directory.
The plan binds that root's inode, access boundary and the two missing names.
Apply holds the root descriptor while creating a private container stage and
publishing it with a relative native no-replace rename. It never creates a
missing managed root or a deeper ancestor chain, and never adopts an appeared
container. The new container and checkout use mode `0700`.

This layout uses a `gwt-missing-recovery.v3` receipt, recording each created
directory inode separately after syncing it and its parent. A crash between
creation and the corresponding durable inode record remains unknown; even an
empty appeared directory cannot be adopted. A private stage must contain only
the recorded checkout. Initial and partial writes verify the recorded checkout
inode again when opening the staging directory. Existing-parent recovery retains
its v2 receipt shape, and pending legacy/v2 receipts keep their original
reconciliation rules. Before partial writes and publication, holder checks cover
the unpublished container itself and its checkout. Published sibling checkouts
remain outside that private staging scope.

Recovery preserves `FETCH_HEAD`, including fetched commits outside the task's
ancestry. Its records must parse strictly and name existing commit objects; the
plan hash binds their sorted OIDs, file identity and exact bytes. Apply refuses
drift. Removal still requires proof that fetched commits survive outside the
admin directory being deleted.

Successful conflict rebases can also leave `REBASE_HEAD` outside the current
branch's ancestry. Recovery retains this file only when it contains one full
lowercase commit OID followed by a newline, with no active Git operation. The
plan binds its bytes, identity, metadata and validated object without following
Git replacement objects. Removal continues to refuse this recovery metadata.
Recovery also retains strictly parsed `REUC` resolve-undo entries with regular
`100644` or executable `100755` stages. Every recorded stage must name an
existing blob without Git replacements; absent stages are allowed. The plan
binds the parsed stages and exact index bytes, inode and metadata, and rechecks
them before and after publication. Only stage-zero files matching `HEAD` are
restored. Undo stages need not be reachable from a retained commit because
recovery preserves the index and object store; deletion still requires its
separate literal-path reachability proof. Symlink undo stages, unresolved or
sparse entries refuse. Legacy recovery receipts retain their original index
rules and digest shape.

At least one recorded owner must have unfinished work. Completed owners may
have current-generation releases only with matching completion proof. Recovery
binds those releases and their exact inventory, including a recorded blocker,
into the receipt. Publishing the recovered identity invalidates deletion
authority through the normal generation transition; owner completion stays
intact. Any cancellation refuses recovery.

Device identities are strict by default. After independently establishing a
device-number change, `--allow-device-renumbering` permits only a bijective map
with unchanged physical paths, directory ownership and inodes. The missing
root's recorded device must map to its current parent volume. The plan hash
binds this map and opt-in; apply and reconciliation require the same option.
Holder checks include the mapped original root inode. The original identity
remains in recovery history; only the verified successor identity is normalized.

Only committed blobs whose tree exactly matches the saved index are written.
The operation runs no checkout filters, hooks, dependency installation or fetch.
Recovery streams regular blobs in 64 KiB chunks within the 1 GiB source budget
per staging pass. Admin and subprocess inventory retain their separate 512 MiB
budget. Metadata admission counts every restored path, including duplicate blobs
and the Git pointer, before recording a new recovery intent.
Symlink contents retain their 16 MiB limit. Tree enumeration uses the native
32 MiB capture budget,
including when replacement objects are disabled; other read limits are unchanged.
Missing blobs are read in finite batches of at most 1024 requests and 16 MiB of
content, or one larger regular blob, with separately bounded protocol framing.
An anonymous temporary spool holds each batch; its complete framing and hashes
are verified before any member is written. The spool is closed on success or
failure. Duplicate blobs count once per restored path. Supervised
child failures retain fixed cause codes, numeric exit/byte counts and cleanup
facts without printing command arguments, paths or child diagnostics.
The existing 238-second owner deadline is unchanged. A private sibling directory
is verified before native no-replace publication at the original path. The
original Git admin files, index, ref, native lock and owner rows remain intact;
the lifecycle records the actual recovery operator separately and advances the
root identity/generation once. Extra lifecycle tables are preserved.

An interruption retains a durable pending intent and any staging directory.
Do not delete that staging directory, clear the intent or rerun blind. Inspect
the exact checkout with `gwt finish-status`; its `missing_recovery` field provides
the intent ID and preimage hash. Only the same operands plus `--intent-id <id>`
can reconcile a complete, verified staging tree or the already-published same
inode. Partial staging remains held unless a separately reviewed continuation
adds `--continue-partial` to the exact intent and preimage operands. This mode
verifies every existing byte, mode and link, refuses extras or corrupt files,
and admits native holders for the staging inode before writing. It creates only
missing committed entries with no-follow, exclusive creation; existing files
are never rewritten. Plan this continuation without `--apply` first. Changed
identities or later source edits remain held.
After a completed recovery, a later disappearance can be planned against the
current generation and committed index. Applying that new plan atomically
archives the exact completed receipt, replaces the current receipt and records
the new lifecycle intent. History is immutable and bound into subsequent plan
hashes. Pending intents are never displaced, and an old intent cannot authorize
a later generation. Legacy receipts keep their original exact-replay digest
and no-release requirement.
No recovery failure authorizes an alternate checkout or broad maintenance.

## Scheduled Git maintenance

Use daily commit-graph maintenance for explicitly enrolled, healthy owning
clones. Worktrees share that owner's graph. `maintenance.config` is a reviewed
Git 2.55 profile. Storing it here does not install or activate it.

The profile disables foreground automatic maintenance, background prefetch,
object packing, ref packing, reflog expiry, worktree pruning and rerere cleanup.
It improves history queries without claiming to reclaim pack storage. Keep
protected, shallow, partial, borrowed and unresolved-lock stores unenrolled.

Before activation, review the exact owners, effective config, hooks, Git version,
locks and existing registrations (`git config --global --get-all maintenance.repo`).
Apply the profile to each approved owner's local config before registration.
Inspect the effective settings, including `core.commitGraph`, after all includes.
Do not set this profile globally or enroll each linked worktree.

`git maintenance register` changes local config and enrolls the current repository.
An existing scheduler can process it immediately. On macOS,
`git maintenance start --scheduler=launchctl` creates the native user jobs and
registers the current repository. Activation affects every registered owner.
Inspect the entire list first. Report profile settings, enrollment, loaded jobs
and observed execution separately. A successful command or lock collision alone
is not proof that a graph was written.

Keep object repacking a separate operation with measured temporary space and
recovery review. Do not enable it as an automatic response to low disk space.
Never enable prefetch as a side effect of registration: the incremental default
includes it unless explicitly disabled. Direct `--task` invocations override
task selection settings, so the profile does not constrain arbitrary Git commands.
Recheck task defaults when upgrading Git.

Contract: [Git maintenance](https://git-scm.com/docs/git-maintenance) and
[Git 2.55 task selection](https://github.com/git/git/blob/v2.55.0/builtin/gc.c).

## Synthetic snapshots

```sh
gwt snapshot --task source-proof --purpose 'inspect the selected source tree' --ref HEAD
```

The command creates `~/GIT/_Synthetic/<host>/<org>/<repo>/<task-id>`. It transfers
only the selected tree's objects and creates one parentless commit. The result
has independent Git metadata, no remote, and no borrowed object store. Source
history stays in the owner. Submodules require a normal checkout.

`.git/gwt-synthetic.json` records provenance, purpose, task ownership, state, and
object dependencies. Outside Codex, set a task-specific `GWT_OWNER_ID`. Existing
destinations fail closed. A failed creation remains available for inspection.
The command does not install dependencies or enroll the snapshot for cleanup.

The local pre-push hook and `push.default=nothing` prevent accidental publishing.
They are not a security boundary against explicit hook or config overrides.
Never independently index a synthetic root or a marked synthetic repository.
Do not reuse synthetic history for contributor PRs or maintainer operations.

Repository-native bounded source capsules and Testbox clones retain their own
contracts. Temporary test fixtures may retain tool-required locations. Use this
directory for durable agent-created proof repositories, not every temporary file.

## Experimental APFS source sharing

`gwt new <branch> <commit> --cow-from <immutable-seed>` first creates a normal
Git worktree. It then replaces identical regular files with `fclonefileat`
copies. Git administration, indexes, symlinks, and dependencies remain separate.

The seed must have a matching committed tree, clean tracked files, and immutable
file flags (`uchg`). The prototype never sets those flags on an existing owner.
It skips mutable seeds, sparse paths, different checkout bytes, and unsupported
clones. Normal files remain usable when sharing is unavailable. Seed and target
must reside on the same filesystem. Executable modes follow the target checkout.

Sharing reports include elapsed time and cloned logical bytes. They do not
measure exclusive physical extents or promise faster checkout. Initial native
checkout still writes files. Errors retain the newly created worktree. Keep this
opt-in until representative same-tree benchmarks justify wider use.

A local 8 MiB random-file fixture took 1.655 seconds for native creation and
3.434 seconds with the CoW pass. The pass cloned 8 MiB in 0.756 seconds. These
small-fixture timings prove neither production speed nor physical space savings.

Dependency qualification and managed-finish deployment are separate contracts.
Storage selection does not authorize
cleanup. Preserve pending owner changes when integrating this module.

Exports:

- `gwt clone`
- `gwt new`
- `gwt ls`
- `gwt audit`
- `gwt clean`
- `gwt cd`
- `gwt rm`
- `gwt prune`
- `gwt sparse ...`
- `gwt owner`
- `gwt snapshot`

Key responsibilities:

- sparse-checkout profile application
- shared `node_modules` bootstrap for pnpm repos
- pretty/default worktree listing with `--raw`, `--plain`, `--color`, and `--no-color`
- metadata-immutable `-h`/`--help` handling before repository probes or command dispatch
- unified worktree discovery across:
  - the current repository's registered Git worktrees
- agent worktree cleanup front doors
- fail-closed external worktree storage validation for mutating and audit commands

Sparse profile authority and explicit application behavior are documented in
[`git-sparse/README.md`](../../git-sparse/README.md). Profiles default to the
physically loaded module's checkout; an explicit `DOTFILES_GIT_SPARSE_ROOT`
override is preserved. Updates do not reapply existing checkouts.

Legacy v2 external storage behavior:

- Configured hosts mount a case-insensitive APFS volume in the policy-selected
  encrypted or unencrypted state directly at canonical `~/.codex/worktrees`;
  a symlinked `~/.codex/worktrees` is rejected.
- The root-owned system policy binds the exact UUID and host marker, requires an
  external device with ownership enabled, disabled Spotlight, persistent Time
  Machine volume exclusion, and at least 200 GiB plus 10% free.
- Encryption policy and observation are exact JSON booleans. The observed value
  must match policy; unknown or mismatched state fails without disclosing the
  values. Integrations can discover this contract statically with
  `worktree-storage-guard --capabilities --json` before any storage probe.
- `gwt new`, `add`, `rm`, `audit`, `clean`, and `prune` stop before worktree
  access when the configured volume is absent, wrong, or replaced by a writable
  internal fallback. Missing policy also fails closed when the direct mount or
  sealed backing directory proves the host is configured. Root resolution
  validates configured storage before returning its path.
- The guard validates both lexical and resolved containment. A worktree on the
  direct mount still resolves through Git to its canonical owning checkout;
  physical aliases beneath `/Volumes` are never independent indexing roots.
- Raw `git worktree prune` is unsafe while required storage is absent because
  Git can mistake temporarily unavailable registrations for stale worktrees.
- Global `TMPDIR`, Codex sessions/databases/logs, tmux state, and package caches
  remain internal. Use `external-tmp <command...>` for opt-in per-process
  scratch. The wrapper creates one mode-`0700`, current-user directory beneath
  `.scratch/tmp`, revalidates the storage guard and mount identity immediately
  before child execution, and removes only that exact inode while the same
  mount remains valid. `external-tmp --check --json` performs a non-mutating
  readiness check.

### Shared external-volume folders

Folder placement uses `external-worktree-storage.v3`. The guard reads a private
user policy at `~/Library/Application Support/agent-worktree-ops/storage/external-worktree-storage.json`.
Its `mount_point` identifies the actual external volume; `worktree_root` is a
physical child on that volume. The guard verifies the UUID, APFS semantics,
ownership, encryption policy and capacity before callers create anything.
Only the managed folder and its children require private ownership and mode
`0700`. The shared volume root and existing sibling data remain untouched.
Missing disks, symlinks, nested mounts and invalid policies refuse allocation;
there is no internal fallback. Simultaneous user and legacy system policies
are an error. The root-owned v2 direct-mount contract remains supported.

GWT resolves this guarded root before making directories. A custom
`DOTFILES_WORKTREES_ROOT` still takes precedence. Its literal
`$HOME/.codex/worktrees` value is treated as the inherited legacy default and
does not mask configured folder placement.
`external-tmp` creates invocation-scoped scratch below `worktree_root`, never
the shared volume root. No global temporary-directory setting changes.

Codex's supported `desktop.git-worktree-root` setting controls future native
allocation. Native Codex does not call this guard. Existing Codex, Claude and
GWT trees retain their original paths and lifecycle owners; this change does
not migrate them, configure Claude's allocator, or activate cleanup.

Dependency bootstrap:

- `gwt new` links a canonical pnpm install only after the compatibility check.
  Otherwise the worktree remains usable for code-only work or remote validation.
- Deep sharing is disabled pending qualification of each nested installation.
  The discovery helper excludes nested Git repositories and managed worktree roots.
- Existing dependency symlinks must resolve to the exact selected source.
  Broken or foreign links fail without replacement.
- Reusing a worktree validates shared links when root `node_modules` is a symlink.
  It does not create missing links or change owned dependency directories.
- A setup failure preserves the new worktree, its branch, and its registration.
  Inspect the reported path before retrying setup or explicitly removing it.

Explicit dependency source:

```sh
gwt new <branch> [start-point] --full --dependency-source /absolute/install-root
gwt add <branch> [start-point] --full --dependency-source /absolute/install-root
```

- The value is the install root containing `node_modules`, not that directory
  itself. Compatibility checks require a Git checkout root. Only one absolute
  value, separated from the option by a space, is accepted. Python 3 performs the identity checks and exclusive link.
- Creation selects this install before the first root link. Source, owner,
  consumer, registration and HEAD checks protect the binding boundary. A
  concurrent entry is preserved. A late failure retains the worktree and branch.
- Repeat the selector on `new` or `add` reuse. Reuse requires the exact existing
  symlink. It never creates, replaces or adopts an entry. Missing, real-directory,
  foreign or broken entries fail. Non-pnpm worktrees also fail explicitly.
- Explicit donors cannot combine with `--finish-managed`. Use a code-only
  managed checkout when using completion tracking.
- This option links only root `node_modules`. It refuses
  `DOTFILES_GWT_LINK_DEEP_NODE_MODULES=1`, source/target overlap and escaping
  target parents. It does not change the environment.
- Omitting the selector preserves the canonical install and code-only behavior
  above. No donor choice is remembered. Omission does not adopt a foreign link.
- The caller must have the install owner's permission and preserve its stability
  during use. The checks compare recorded install inputs and runtime facts. They
  do not prove native build correctness. No install, copy or donor mutation occurs.
- Existing storage/source/profile guards, cwd changes and tmux context sync still
  apply. `--help` takes precedence even with malformed selector arguments.

Dependency compatibility:

- New links require an explicit frozen-install receipt. The install owner creates
  it after a successful frozen install with the intended Node runtime:

  ```sh
  gwt dependencies record --source /absolute/install-root --after-frozen-install
  gwt dependencies check --source /absolute/install-root --target /absolute/worktree
  ```

- Recording is an operator attestation. It does not run or prove the install.
  The receipt lives under `${XDG_STATE_HOME:-~/.local/state}/gwt/dependencies`.
  It binds the physical install directory, input hashes, exact pnpm version,
  installed lockfile, pnpm layout, Node version/ABI, OS and architecture.
- For pnpm 12's two-document wanted lock, qualification compares the installed
  lock byte-for-byte with the normalized dependency document. The complete
  wanted lock, including its environment document, remains hashed into the
  receipt and must also match the consumer checkout.
  Document markers must match pnpm's exact LF-delimited format after
  normalization; malformed or extra documents are refused.
- Consumer manifests, workspace configuration, patches and lockfile must match.
  Missing sparse inputs, absent receipts, changed metadata or unknown layouts
  leave new worktrees code-only. An explicit source fails and retains the new
  checkout. Existing links are validated without repair or replacement.
- Links inside the install must resolve within its physical `node_modules`.
  Workspace links into donor source are refused. Deep workspace sharing remains
  unavailable until each installation has its own qualification contract.
- The receipt does not hash every installed package or prove native artifacts.
  Keep the donor stable while consumers use it. Package installs, native rebuilds,
  platform changes and uncertain state require a separate qualified install.
  Do not run an install through a shared symlink.

Cleanup behavior:

- `gwt audit` is metadata-immutable, rejects `--apply`, and reports foreign or stale paths.
- `gwt audit --machine` suppresses the wrapper preamble and emits the cleaner's
  single compact schema-v1 JSON count line. It never prints worktree paths.
- `gwt clean` deliberately runs pressure maintenance immediately with `--force`.
- `gwt clean --machine` is rejected because machine output is audit-only.
- Both cleanup wrappers reject repository/Codex-home scope overrides.
- The module locator chooses the local functions tree; `gwt` then resolves its
  physical source checkout and uses the adjacent cleanup helpers.
- If the source checkout is in CloudDocs or has no helper, cleanup falls back
  only to the installed Application Support runtime.
- Hardcoded Git paths, `~/.dotfiles`, CloudDocs, and `~/bin` are never direct
  cleanup-helper fallbacks.
- `gwt rm` resolves targets to an absolute path registered to the current Git common dir.
- `gwt rm` never prunes unrelated stale worktree registrations.
- `gwt prune` is the only wrapper command that prunes worktree metadata.
- `gwt prune` requires the configured external worktree volume to be present.

The cleaner retains no-follow state directory and artifact descriptors, then
opens the unique database through a relative SQLite `mode=ro` URI in an isolated
child anchored to that directory. It verifies `query_only`, schema, and the
database/WAL descriptors SQLite opened. An unsandboxed reader can still update
an active WAL shared memory file, so headless zero-touch audits also require the
private scheduler's physical-path write-denial sandbox.

## Managed worktree completion

Finalized tasks do not retain local validation evidence by default. Their logs,
test proof, receipts, archives, build outputs and scratch are disposable; no
archive or extra retention approval is required. Preserve user deliverables,
unfinished source, live owners, credentials and shared dependency targets.
Recovery checkpoints remain necessary only for unfinished work or an explicit
retention request. Historical evidence alone must not keep a finalized task's
checkout alive.

Agent session history is retained separately from validation evidence. Preserve
completed and archived conversations, transcripts, history indexes/databases,
and tmux recovery history. Worktree retirement never authorizes their deletion
or truncation. If protected history lives inside the checkout, retain it until
the history is preserved through a separately authorized operation by its
native owner; never include it in `--discard-ignored`.

### Manual finalized-task closeout

For a finalized managed task, run the following from its owning checkout:

```sh
gwt rm /absolute/path/to/task --finalized \
  --discard-ignored .crabbox --discard-ignored node_modules
```

Use the task's actual ignored roots; these names are examples. The arguments
declare those exact ignored roots to be disposable task artifacts. Every other
ignored root blocks removal, as do dirty or untracked source, populated submodules, pins,
in-progress Git operations, unrelated locks and live file/CWD holders. Symlink
targets are never traversed or removed. This is routine task closeout, with no
evidence archive or additional permission step.

For a tracked regular file with committed `text eol=crlf`, foreground closeout
can verify the exact CRLF checkout bytes against the preserved LF blob. Mixed
line endings, other changes, and unsupported conversions retain the checkout.
The committed and effective checkout attributes must agree on this conversion.

Git never reports an empty directory as untracked or ignored, so such a tree
needs no `--discard-ignored` root. Every checkout inventory (manual closeout,
completion checks and release baselines) admits a directory that no tracked
path needs only while it holds nothing but further directories at every depth.
A tracked or skip-worktree path replaced by a directory is a deletion and
refuses. Any file, symlink, special file, `.git` entry or mount inside it still
blocks removal unless it falls under a declared ignored root.

The common Git directory's `openclaw-main-qualification-fetch.lock` is a
persistent advisory-lock carrier. GWT admits only its existing empty, regular,
single-link, current-user file with mode `0600`, opened without following
symlinks. It pins the common directory and holds a nonblocking exclusive flock
through all admission passes, native removal and outcome verification. A busy,
malformed, replaced or newly appearing carrier retains the checkout; drift after
removal starts leaves an incomplete outcome. GWT never creates, writes, changes
permissions on or unlinks this carrier. Every other Git lock remains blocking.
Native removal inherits only the carrier descriptor, so parent death cannot
release its flock while the removal child still runs.

This route consumes only a matching native managed enrollment. It rechecks the
tree before committing a removal intent and again before unlocking its own
enrollment marker for one non-force Git removal. It preserves the branch, shared owner and
external dependencies. A partial failure holds the intent and restores the
matching native lock when its original registration still exists; it never
replays deletion. Unenrolled task trees continue to use plain `gwt rm`.

If the checkout directory is already absent, the same literal `--finalized`
command can retire its surviving registration. This requires all recorded
owners completed and released, exact merged PR proof, no pending recovery,
and unchanged owner, admin, branch, index and native lock identities. The index
and admin recovery data must remain recoverable through surviving refs; holders
and sibling preservation are checked as usual. The result records that working
files were already absent and could not be audited. No checkout is recreated.
An appeared directory or symlink refuses closeout.

An explicit finalized closeout can admit a different named local branch from
the one recorded at enrollment. Checkout, owner, Git administration and native
enrollment-marker identities must still match. Admission pins the current
branch and HEAD for that attempt; later drift blocks removal. Both the enrolled
and current refs must remain present and unchanged, with unchanged reflog
existence and contents. The durable enrollment and completion proof are not
rewritten.
Automatic lifecycle checks do not accept this branch transition.

Worktree configuration may contain one `core.hooksPath=git-hooks` entry alongside
the supported sparse-checkout settings. Custom, empty or duplicate hook paths and
unknown settings retain the checkout. Closeout does not execute hooks, and the
configuration's exact bytes and identity remain bound through removal; changing
them after release requires a new signoff.

Manual closeout accepts `FETCH_HEAD` only when every record names a literal
commit preserved by the captured current or enrolled branch heads, the recorded
target's existing `origin` remote-tracking ref, or their common reflogs, including
their reachable ancestors. Tasks without a recorded target do not infer one.
Annotated tag objects do not qualify merely because their peeled commit is
retained. Bounded Git queries disable replacement objects; unavailable or
ambiguous proof blocks closeout.
Records must have Git's complete OID, merge-marker, and note/URL fields with LF
termination. An empty file is also accepted under this explicit manual proof.
The file's identity, bytes, metadata, and retained refs remain bound through
admission and removal. Malformed or unanchored records block closeout.
New automatic release baselines use the same recovery proof. Release-time OIDs
remain required even if a later fetch overwrites or removes `FETCH_HEAD`. Legacy
full inventories continue to reject it.

If recovery data belongs to another existing local branch, explicitly bind it
with `gwt rm /absolute/task/path --finalized --preserve-ref refs/heads/branch`.
Repeat `--preserve-ref` for additional branches. Only literal, non-symbolic local
branches pointing directly to commits are accepted; tags, remote refs and revision
expressions are not. Their tips and reflog identities and contents must remain
unchanged through admission and removal. This preserves source branches and adds
recovery roots for this manual attempt only; default and automatic closeout do not
search other branches.

Manual closeout also accepts a strictly parsed `REUC` resolve-undo index
extension. Each recorded stage must be a regular-file or symlink blob reachable
at its literal path from those same retained commit roots. Bounded Git queries
must complete without diagnostics or fetching objects. Duplicate, malformed,
unsupported, missing, or unanchored records block closeout. Closeout stores the
decoded path/stage/mode/OID mapping in the removal intent and terminal result.
The full index and retained refs must match at each admission.
New automatic release baselines preserve this decoded mapping and revalidate its
roots. Legacy full inventories continue to reject `REUC`.

Manual admission also accepts unpopulated gitlinks when index and HEAD paths,
modes and commit IDs match exactly. It does not resolve or fetch those commits.
Each gitlink must be absent or a real empty directory. No-follow descriptor
checks bind directory identities, emptiness and absence at each admission;
existing directories remain part of holder checks. Populated or linked paths,
mounts, unreadable state and a candidate worktree's `modules` admin entry block
removal. The normal dirty-tree check still applies: missing non-sparse gitlinks
reported as deletions by Git are not eligible. Automatic inventories continue
to reject gitlinks.

Manual closeout filters one bounded native `lsof` scan to checkout/admin paths
and file identities, with the operator's explicit finalized-task declaration.
An exit-zero scan is not sufficient: error or unrecognized descriptors, missing
device/inode identity for CWD, text or mapping records, and inline access errors
retain the checkout as `manual-holder-visibility-unknown`, even with empty
stderr. Numeric descriptors and fileports can omit identities for non-files
such as sockets. Unknown visibility never means holder-free; do not exclude an
unreadable process or suppress warnings to make closeout pass.

On Linux, if the invoking account cannot inspect all holders, manual `--finalized`
closeout can opt into `--sudo-holder-scan`. Only the read-only holder scan uses
`sudo -n -u '#0' -- <resolved-lsof> -nP +w -F0pfnDi`. The resolved `sudo` and
`lsof` executables and their parent directories must be root-owned and not
group- or world-writable. Root-owned symlinks are checked through their target
paths. Existing non-interactive sudo authorization for this exact command is
required; the helper never prompts, refreshes authorization, accepts extra
arguments or environment settings, or falls back to an unprivileged scan.
The sudo monitor retains the caller's existing controlling terminal, with
standard input still connected to `/dev/null`. It creates no terminal and
does not guarantee that credentials from another terminal or a headless
context are usable.
Git, lifecycle state and deletion remain under the original invoking account.
The flag is rejected outside manual finalized closeout and on non-Linux
platforms before launch. Without it, the existing unprivileged scan is unchanged.

The Linux privileged scan accepts one missing-executable shape: a `txt` record
containing only `f` and `n`, with `n` exactly `/proc/<enclosing-pid>/exe`.
Linux `lsof` suppresses the `ENOENT` annotation for this root-owned absent
executable. This accepts only that record, not the process's other references.
The same privileged route accepts a numeric FD with only `f` and `n` when its
name is exactly `/proc/<enclosing-pid>/fd/<same-fd> (readlink: No such file or directory)`.
FD numbers must be canonical decimal (`0` or a positive integer without leading
zeros). This procfs lookup failure exempts only that record, not its siblings.
The path can include `/task/<tid>` immediately before `/fd/`. The TID must be
positive decimal without leading zeros.
Linux `mem` records with valid device/inode fields can carry one terminal
` (stat: No such file or directory)` annotation. The parser removes that
annotation for pathname matching and retains the mapping's device/inode.
Other errors, partial identities, and mismatched executable paths still block
closeout. These cases do not change macOS or automatic cleanup.

Warnings, incomplete visibility and actual holders still block removal.
On interruption, scan timeout or output-limit failure, the helper sends
`SIGTERM` to its task-owned sudo monitor and waits at most two seconds.
It does not kill the monitor's group or claim that monitor exit proves
privileged-child cleanup.
Termination remains unknown and closeout stops, even if the monitor exits.
An interruption still propagates after the bounded monitor cleanup.
Do not retry until the privileged scan's termination has been independently
resolved. This option does not activate unattended removal.
The scan also respects the current admission deadline.

Explicitly discarded trees are fully scanned under a separate 262144-entry
limit and the same 120-second admission deadline. Compact metadata digests and
inode sets bind their preimage and detect holders through external hardlinks;
symlink targets, nested repositories, mounts and live special files remain
protected. The 131072-entry source/index limit is unchanged.
It does not claim complete macOS mapping
coverage or qualify unattended deletion. Unattended checks and host
activation retain their stronger admission requirements below.

### Completion and unattended removal

`gwt finish` records completion and signs off this owner's future checkout use
for native managed enrollments, then attempts native foreground closeout. This
operator-invoked route does not require an unattended host policy. `--release`
remains accepted for existing callers.
Use a recovery pin while the checkout is still needed. Generic Codex Stop is
turn-scoped attention state and never creates completion or release.

```sh
# Codex supplies CODEX_THREAD_ID. Otherwise use a stable task identity.
export GWT_OWNER_ID=payment-fix
gwt new fix/payment origin/main --finish-managed

# Finish this owner's work and sign off checkout use in one operation.
gwt finish --pr https://github.com/example/project/pull/123
```

Only newly created `--finish-managed` worktrees receive release-capable
records. Creation uses a private checkout/admin directory and a native Git lock
with a unique lifecycle marker. Existing report-only enrollments, shared and
repository-native worktrees are never adopted. Existing ancestor permissions
are not changed. The private SQLite ledger remains under
`~/.local/state/gwt-finish`.

After creating the checkout, enrollment waits up to 10 seconds for another
lifecycle operation to release the ledger lock. If it stays busy, GWT reports
`lifecycle-busy` and retains the locked checkout.

Creation rejects a previously enrolled path before creating its Git checkout.
Older versions could create a new locked checkout there, then fail enrollment.
For that specific failure, the original sole owner may explicitly attest the
audited creation token and plan a one-shot closeout:

```sh
gwt recover-failed-creation --worktree /absolute/task/checkout \
  --worktree-id <prior-id> --generation <prior-generation> \
  --result-sha256 <prior-result-hash> --creation-token <new-lock-token> \
  --expected-head <sha> --acknowledge-unrecorded-creation
```

The acknowledgment requires independent evidence that this owner created the
replacement; automation must not infer it from a path or lock. The prior record
must prove successful removal. The new checkout must have the same branch and
repository, distinct checkout/admin identities, no pins, clean source and no
holders. Apply the reviewed plan by repeating it with
`--apply --recovery-sha256 <plan-hash>`. Native closeout rechecks the exact
preimage, preserves prior records and records its own intent before removal.
If independently verified device renumbering changed only the stable owner and
common-directory device IDs, add `--acknowledge-device-renumbering` to both
commands. Their inode identities must match, the device mapping must be
one-to-one, and the new checkout/admin inodes must still differ. The plan binds
and reports this explicit acknowledgment and mapping; it never rewrites history.
It never enrolls the replacement. Any recorded attempt, including an incomplete
one, refuses replay. Inspect the eight most recent recovery outcomes with
`gwt finish-status --worktree /absolute/task/checkout --read-only`; they appear
under `failed_creation_recoveries`, separately from the original retirement.

A finished fix or feature is eligible as soon as every owner has completed and
released, its exact PR head has merged into the final target, and native
admission passes. There is no age delay. `gwt release` can sign off an already
completed owner without repeating the PR arguments.

The primary PR may target another repository when GitHub identifies the checkout's
origin as its head repository and its head commit exactly matches the checkout.
The final branch defaults to the primary PR's base repository default branch;
all stack dependencies must target that same repository and final branch.
Same-repository completion does not require an available head repository.
If a cross-repository PR's head repository is deleted or no longer matches the
origin, completion and removal retain the checkout.

Missing-checkout recovery with recorded cross-repository owner releases requires
fresh GitHub identity, head and target proof. Unavailable or changed proof refuses
recovery completion. If an intent already exists, inspect its recorded state before
continuation; publication may already have occurred. Do not automatically retry.
Same-repository recovery keeps its offline proof path. Completion alone never
substitutes for the fresh merged proof required before eligible checkout removal.

For stacked work, retain a pin while another task still needs this checkout.
Repeat `--wait-for` for every dependent PR and name the final integration branch:

```sh
gwt finish-pin --reason 'upper PR still uses this checkout'
# Later, after that dependency no longer needs the checkout:
gwt finish-unpin --reason 'upper PR still uses this checkout'
gwt finish --pr https://github.com/example/project/pull/123 --target main \
  --wait-for https://github.com/example/project/pull/124 --release
```

Every listed PR must target that final branch. Open or closed-unmerged PRs
retain the checkout; a head or target change invalidates the recorded proof.
Pushed commits, green CI and auto-merge are
not merged proof. Every enrolled owner, including the creator, must sign off
against the same head and dependency set. A proof change resets completion;
`gwt resume`, `gwt cd`, reuse through `gwt new`, sparse-profile mutations or pin changes invalidate all
releases. Storage guards also invalidate release when an older client resumes
and restores an identical proof. Claims never expire.

If resume reports `worktree-identity-changed`, inspect the recorded and current
checkout, admin, common-directory and owner identities before continuing. Device
numbers can change after a remount. An existing recorded owner may explicitly
acknowledge only that difference for one non-destructive resume:

```sh
gwt resume --acknowledge-device-renumbering --worktree /absolute/path/to/task
```

This requires an open native v2 enrollment, its matching lock, unchanged inodes
and path/repository/branch bindings, and a one-to-one device-number mapping.
It never adds an owner or adopts an existing checkout. It invalidates prior
completion and release through normal resume, preserving pins and other owners.
The identity is rechecked under the lifecycle lock before that mutation.
The acknowledgment does not prove historical volume continuity or rewrite the
recorded enrollment identity. Default resume, finish, release, pin and removal
admission keep their existing checks; a later strict identity check can still
refuse. Automatic `gwt cd`/`gwt new` reuse never acknowledges this difference.

To record completed work while retaining a checkout whose device number changed:

```sh
gwt finish --pr https://github.com/example/project/pull/123 \
  --acknowledge-device-renumbering --worktree /absolute/path/to/task
```

This verifies the same owner and identity constraints and the exact PR head,
then records completion without signing off or attempting removal. It preserves
the original enrollment identity, pins and other owners. It is not a release
or removal qualification. Ordinary later release still requires strict identity
validation.

For resume, the flag accepts only checkout, state-directory and owner selection.
For completion, it also accepts the PR, target and dependencies. It rejects
other actions, batch mode, `--release`, `--if-enrolled`, and all recovery,
reconciliation, disposal and holder-scan options, including explicitly supplied
empty values.

The wrapper parks only its own shell before release. It cannot move the parent
Codex process. An alive parent outside the checkout/admin paths may remain
alive; any actual CWD, FD, thread CWD, fileport or mapped reference inside those
domains blocks removal. If a merge or departure is pending, retry `gwt finish`
from outside the checkout after that blocker clears. Retained owner releases
capture a versioned semantic baseline for the existing bounded pending consumer.
Only explicit installation and qualified activation let that consumer remove trees:

```sh
gwt finish-status --all
gwt finish-status --all --read-only      # Audit without opening the ledger for writes
gwt finish-status --all --read-only --inventory-json  # Compact bulk inventory
gwt finish-check --all                   # Refresh proof only
# Only after explicit host activation:
gwt finish-check --all --apply --policy /absolute/private/policy.json
```

`--inventory-json` emits a `gwt-finish-inventory.v1` object with a source,
ledger presence, and records ordered by checkout path. The bulk reader uses the
same guarded snapshot as ordinary read-only status. Each row contains recorded
checkout, Git administrative directory, common directory and repository-owner
paths with device and inode as decimal strings. It includes the recorded branch,
enrollment lock reason, finish HEAD, agreed cancellation HEAD, generation, pin
count, intent/result flags and owner signoff counts. Release counts use the
current retirement generation. Cancellations use the native proof-invalidated
owner records and their agreed HEAD. Owner identifiers, pin reasons and
retirement payloads stay private.

The projection describes recorded state. Inventory consumers must match the
current Git registration, physical directory identities, lock and HEAD before
classifying a checkout for native closeout review. Missing identity evidence
leaves that classification unknown. The ordinary status JSON array remains
available for existing callers.

`finish-status --read-only` uses the existing shared lifecycle lock and opens
SQLite without creating files or performing recovery. It refuses a busy or
missing lock and any journal/WAL sidecar; use the normal owner workflow to
resolve those states before retrying the audit. The flag also works for one
enrolled checkout and does not change normal `finish-status` behavior.
For one enrolled checkout, add `--removal-receipt` to this read-only command
to inspect bounded producer facts and SHA-256 hashes of the exact stored
intent/result text. Large inventory bodies remain in the ledger. After an
unstarted-attempt reconciliation, the inspector labels the archived receipt
`reconciled=true`; its generation is the original attempt's generation.

The evaluator removes one exact checkout at a time with native non-force
`git worktree remove`. It preserves local branches, siblings and external
dependency targets. It never runs GC, prunes, or selects unenrolled worktrees.
Ignored content is not assumed disposable. An exact root `node_modules` symlink
to an unchanged owned external directory is supported. Owners may also name
literal disposable roots with `gwt finish --discard-ignored <root>` or
`gwt release --discard-ignored <root>`. That decision and the discarded contents'
exact inventory are frozen at release; a worker cannot approve later contents.
Unknown files, unresolved Git recovery data, changed source/index semantics,
modes, pins or locks retain the checkout.

The release baseline preserves tracked bytes at the recorded HEAD, logical index
entries, sparse configuration, recovery obligations and ownership. A later Git
index stat-cache refresh or pure timestamp change does not invalidate it.
Physical snapshots still match exactly immediately before native removal.
Legacy full-inventory receipts keep their original exact comparison. Failed
capture preserves completion and owner release, but cannot authorize automatic
retry: status reports the blocker and requires that owner to resume then finish
again. `automatic_handoff` reports baseline readiness and native qualification
separately; it does not attest consumer installation or a scheduled retry.
Checkout and admin files, directories and links receive no-follow xattr-name
checks, including Darwin compression names. Only the OS-created
`com.apple.provenance` tracking attribute is disposable; resource forks,
quarantine and user attributes retain. Attribute values are never read.

Full checkouts and the default OpenClaw non-cone sparse profile are supported.
The full v2/v3 index supplies omitted-file facts; present skipped files still
receive byte and mode checks. Generated sparse/full admin configuration is bound
at release. Unknown admin keys, conflict/intent-to-add/assume-valid entries,
split or compressed sparse indexes and Git conversion filters retain.
Normal rebases preserve old tips through the existing common branch reflog,
whose bytes and identity must survive removal. Missing or expired recovery
coverage retains. `AUTO_MERGE` is disposable only when it names the final HEAD
tree. Editor-generated `COMMIT_EDITMSG` comments use Git's own cleanup and must
reduce to HEAD's message or a commit message retained by the bound surviving
refs and reflogs (at most 128 additional commit roots). This includes authored
messages left behind by fast-forwards. Divergent drafts, unsupported cleanup
modes and verbose/scissors residue retain.
Tracked regular source files are hashed in chunks of at most 64KiB, bounded by
1GiB of verified working bytes per pass and 131072 source entries including
directories. Other leaf reads retain the 8MiB limit. Subprocess and admin reads keep
the separate 512MiB bound. Tracked reads bind admitted metadata to the opened
descriptor; independent EOF, final identity and deadline checks still apply.
A retained owner release captures its baseline once;
apply repeats its inventory before and after committing the removal intent. Successful foreground closeout does
not capture an automatic baseline. Neither route expands sparse checkouts or
installs tools.

The owner repeats admission before committing the removal intent. A refusal
at that point leaves the enrollment and lock intact. After committing, the
owner repeats admission before unlocking and observes checkout, admin,
registration, branch and sibling outcomes independently. Existing sibling registration paths,
root/admin inode identities, common-directory backlinks and lock fields must
survive. Another owner's HEAD advance, branch switch or detached checkout is an
operational change, recorded separately; it does not invalidate preservation.
Additional registrations and listing order are also independent. The intent
stores initial sibling identities; the result retains actual after rows,
identities and classified deltas, including failed readbacks.
An already absent sibling root or `.git` pointer is captured as an explicit
ENOENT state, bound to its canonical existing parent and unique native admin
backlink. The admin/common identities and lock still have to survive. This
does not depend on Git marking the registration prunable, and never prunes it.
Absence may also be observed beneath a root-owned parent that is not group- or
other-writable, or has the sticky bit. A no-follow directory descriptor binds
its identity, ownership, mode and flags; unrelated directory timestamp and
link-count changes are ignored. The sticky bit does not prevent new entries,
so reappearance still fails preservation. Target and admin ownership rules
remain unchanged.
New absence, reappearance, replacement, ambiguous backlinks and unknown read
errors fail preservation; they are not treated as preexisting absence.
Admission keeps its 120s limit. Recursive deletion has a separate maximum 180s
phase within the unchanged 238s owner deadline, reserving 2s to reap and 30s for
postchecks. Ordinary subprocess queries retain their 30s limit.
Git can delete admin metadata even
when checkout deletion fails. A crash, timeout, partial result or failed
readback therefore remains `unknown`/`incomplete`, never an automatic retry.
Updated manual removal, prune, cleaner and quarantine consumers respect the
ledger hold even after registration disappears. Never repeat removal to resolve
an incomplete result.

A foreground finalized attempt can expire during its repeated admission,
before native unlock or removal begins. To reconcile this specific case,
inspect the receipt, then bind the explicit recovery to those exact hashes:

```sh
gwt finish-status --worktree /absolute/task/checkout --read-only --removal-receipt
gwt finish-reconcile --unstarted-removal --worktree /absolute/task/checkout \
  --intent-id <uuid> --generation <number> \
  --intent-sha256 <original-intent-hash> --result-sha256 <original-result-hash>
```

This mode requires the original recorded operator, completed and released
owners, merged PR proof, no pins, the unchanged native lock, and fresh admission
matching the original source/admin/index, preserved refs and disposal scope.
The original ref names must still exist. Recovery qualifies their current tips
and reflogs as custody roots; it does not require independent refs to retain
their historical values. The checkout's HEAD, own branch reflog and complete
source/admin proof remain exact. Historical OIDs are never added to current
custody roots, so ref progress that loses required recovery data still refuses.
It accepts only a pre-unlock `admission-deadline` result with explicit
`child: null`. New receipts must record literal `native_attempted=false`;
legacy receipts must match the known pre-dispatch shape with no postcheck
fields. Missing child proof, attempted unlock, cancellation and partial removal
remain blocked.

Recovery archives the original text unchanged, advances the generation and
clears releases atomically. It never removes files or unlocks the checkout.
The archive records the current ref observation beside the original receipt.
Read-only inspection includes its full-proof digest and changed-ref count,
plus up to eight head/reflog change summaries with ref names capped at 160
characters. Reflog-only changes count even when the tip is unchanged.
Historical sibling evidence remains archived; it cannot authorize deletion.
After successful reconciliation, an explicitly invoked
`gwt rm <literal-path> --finalized` performs new admission and sibling checks.
Old releases cannot authorize automatic removal, and repeating recovery cannot
change a successor attempt. Admission and owner deadlines remain unchanged.

Current removal receipts report sibling preservation failure as
`siblings_preserved=false`; older receipts used `siblings_unchanged=false`.
Reconciliation accepts either format. If the child exited successfully and
every other preservation predicate passed, the recorded owner can reconcile
that exact intent after reviewing its original bytes:

```sh
gwt finish-reconcile --worktree /absolute/removed/checkout \
  --intent-id <uuid> --generation <number> \
  --intent-sha256 <original-intent-hash> --result-sha256 <original-result-hash>
```

Hashes bind the exact stored UTF-8 intent/result text, without reserialization.
This command never removes files, unlocks a worktree or retries a child. Under
the lifecycle lock it verifies the recorded child/group is absent, current
target/admin/registration absence, preserved refs and external targets, and no
remaining target holder. Wrong target bindings, recreated targets, changed
target refs, live children or unknown target visibility refuse. It separately
observes prior siblings: missing or changed registrations, rebound identities
and identity-read failures remain explicit unresolved observations. These later
observations do not erase an independently proven target-removal fact and never
qualify historical sibling preservation. Prospective removal still refuses
sibling loss, rebinding or changed lock fields.

Reconciliation appends a separate current `removed` observation. Status reports
`current_disposition=reconciled-target-removed` and a reconciliation reason while
retaining original `checkout=unknown` and `retirement_state=incomplete` beside
the reconciliation result; old consumers continue to hold the record. The original
intent and failed result remain byte-identical. Legacy records lack historical
sibling inode identities and an after snapshot: those facts remain explicitly
unavailable, and historical sibling preservation stays unresolved. A repeated
exact reconciliation revalidates current state and preserves the first fact.
A changed sibling observation on repeat is an explicit record conflict; it
neither overwrites the first observation nor reports that stale observation as
a fresh success.
When the sibling observation is incomplete, status and check also expose
`sibling_alert=current-sibling-preservation-unresolved` with the named deltas in
the reconciliation fact. This is an operator alert, not a clean preservation
verdict. GWT does not infer a sibling's deletion actor or time from its absence
or interpret another repository's closeout receipts.

### Activation and proof limits

Installing source does not activate unattended deletion. Native foreground
`finish` and `release` use the same admission and non-force removal engine as
explicit finalized closeout, with exact merged proof and every owner's signoff
rechecked before removal. Unknown ignored content retains the checkout without
preventing completion or signoff from being recorded. Explicit `--policy` selects
the unattended evaluator and its frozen release inventory contract; foreground
signoff alone does not qualify a later unattended removal.

Version-1 `report-only` policies
and existing enrollments retain their contract. A version-2 policy has schema
`gwt-finish-policy.v2`, mode `explicit-release`, the stable host identity and
one to eight exact owner/root/common-directory identities. Its `qualification`
binds the exact platform, native Git 2.55.0 path/hash/version, installed lifecycle
source hashes, known process identities and a private activation receipt.

The Darwin arm64 backend uses `PROC_PIDREGIONPATHINFO2` (22), its 1272-byte
ABI, and reports `mapping_coverage: bounded-native`. It observes native CWD,
FD, thread CWD, fileport and mapped-vnode references, with process identities
and population checked again. Surfaced errors, short or malformed results,
unknown descriptor kinds, limits and identity changes retain the checkout.

This is an explicitly bounded cooperative observation, not complete or frozen
VM coverage. Apple's [native implementation](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/proc_info.c)
returns `EINVAL` for both enumeration exhaustion and a lost vnode, and can hide
vnode-info errors. The accepted blind spot is a mapping missed through those
native omissions, including a still-live mapping after its FD closes. Existing
repeated admission observations reduce exposure but do not eliminate that gap or
prevent a noncooperative later opener. No external receipt can improve the
reader's actual coverage.

Only newly created `--finish-managed` checkouts record this bounded holder
contract at enrollment. Existing private v2 rows, older report-only rows,
shared and application-owned checkouts are not adopted. Schema upgrade leaves
old contract stamps NULL; resume, finish and release cannot set them. Those
checkouts keep their existing explicit foreground/manual closeout route.
Eligible new records still need every owner released, exact merged proof,
unchanged recoverable source, private access boundaries and qualified host
activation before unattended removal. Installing this backend enables none
of those host policies or scheduled jobs.
Linux, WSL and Windows have no deletion qualification in this change. The private
`scripts/install-gwt-finish.py` owner already provides the reviewed v2 activation
route for the existing bounded scheduler. Installed report-only scheduling is
separate from qualified apply activation; no second daemon is needed.

Activation must qualify every configured automatic cleanup route and actually
running cleanup job, including loaded callbacks that can run without a new
manual command. Updated consumers must protect pending intents. Unknown or old
configured/running consumers block activation. Arbitrary historical scripts,
raw Git/rm, privileged actors and noncooperative later writers remain outside
this cooperative contract; unrelated idle interactive shells are not automatic
cleanup consumers.

The native observer covers every process whose real or effective UID matches
the owner, plus explicitly known controllers/borrowers. Both checkout and admin
need verified private access boundaries before unrelated foreign-UID processes
can be excluded. Selected-process errors, churn, truncation or unknown coverage
retain. Neither stock lsof output nor a successful own-PID fixture establishes
global holder absence.
Empty or deny-only ancestry ACLs preserve that boundary; allow/unknown ACLs
retain. Normal macOS deny-delete home ACLs do not require a permission change.

`holder-qualification` describes backend platform/ABI support and the bounded
contract without scanning processes; it does not qualify or activate a host.
External policy cannot override the native observation. `finish-status`
never grants fresh removal authority: `automatic_removal_supported` identifies
a record enrolled under this holder contract, while `removal_available` remains
false in the report.
Always report the recorded `retained`, verified `removed`, or `unknown` checkout
outcome and its reason.
