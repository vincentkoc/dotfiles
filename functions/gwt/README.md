# gwt

`gwt` shell module.

## Git storage

Keep durable clones in their normal `~/GIT` categories. Linked worktrees share
their owner's Git objects and history. Their checked-out source files use
separate storage unless the filesystem shares those bytes.

Use full, non-promisor owners for development and maintainer work. This includes
OpenClaw review, release, merge-base, and ancestry checks. Sparse checkout limits
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

Remote start points refresh one exact branch into its remote-tracking ref.
Refreshes disable automatic maintenance, tags, pruning, and shared `FETCH_HEAD`
writes. A failed refresh stops creation. Pass a verified local commit explicitly
when working offline. Cached refs do not prove freshness.

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
An existing branch must have the same tip in both owners. Local-only start
commits must already exist in the selected owner. Selection never migrates them.

These checks establish eligibility, not a full object-integrity audit. They do
not change the behavior of repository-native worktree commands. Run native
OpenClaw PR commands from the selected owner. Do not relocate active bases.

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

Dependency compatibility and managed-finish deployment are separate contracts.
Storage selection does not establish a compatible pnpm installation or authorize
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

External storage behavior:

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
  sealed backing directory proves the host is configured. `gwt root` remains
  informational.
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

Dependency bootstrap:

- `gwt new` links the selected canonical pnpm install when one is available.
  Without it, the worktree remains usable for code-only work or remote validation.
- Deep linking skips nested Git repositories, `.worktrees`, and configured or
  conventional agent worktree roots.
- Existing dependency symlinks must resolve to the exact selected source.
  Broken or foreign links fail without replacement.
- Reusing a worktree validates shared links when root `node_modules` is a symlink.
  It does not create missing links or change owned dependency directories.
- A setup failure preserves the new worktree, its branch, and its registration.
  Inspect the reported path before retrying setup or explicitly removing it.

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

`gwt finish` records the owner's sign-off that the job is complete. It does not
infer completion from branch names, commit prefixes, agent turns, or age.
Only a worktree created with `--finish-managed` participates. Existing worktrees
and repository-native PR worktrees keep their existing lifecycle.

Owner release and worker removal currently stop with
`holder-backend-unqualified` on every platform. Recursive `lsof +D` can silently
omit processes, so an empty result cannot prove that a tree has no holders.
The helper does not run that scan while the backend is unqualified. Enrollment,
resume, finish, pins, status and report-only checks remain available; retain
the checkout until a qualified holder backend is implemented.

The following command reports the qualification as JSON without opening the
ledger, taking locks or starting probes:

```sh
agent-worktree-finish holder-qualification
```

```sh
# Codex supplies CODEX_THREAD_ID. Outside Codex, set a stable task identity.
export GWT_OWNER_ID=payment-fix
gwt new fix/payment origin/main --finish-managed

# Finish implementation, review, merge and post-merge proof first.
gwt finish --pr https://github.com/example/project/pull/123
# The calling shell is now at the canonical checkout. The tree is retained.

# After every long-lived agent, terminal, editor and test process has left,
# review saved recovery references and release the same owner from outside it.
# This currently returns holder-backend-unqualified and retains the owner.
gwt release --worktree /path/to/managed/payment-fix --recovery-reviewed
```

In Codex, do not override `GWT_OWNER_ID` with a shared generic name. The runtime
injects `CODEX_THREAD_ID` into each tool process, including restored shell
snapshots; that identifies the owner across turns. A child shell's `cd` cannot
move its parent agent. If release says a process still holds the tree, keep it,
exit or deliberately park that agent, then release under the same owner ID.
Never kill another session to satisfy cleanup.

The lifecycle supports immediate removal after sign-off and release, without
a minimum age. The shipped holder backend keeps that path disabled, even when
an apply policy is present. A future qualified backend must still check fresh
GitHub merge evidence and every local safety condition before removal.

## What finished means

| Situation | Action |
| --- | --- |
| A fix or feature is merged and post-merge proof is complete | `finish`; retain while holder qualification is unavailable |
| Work is complete but the PR is waiting to merge | `finish` may record the intent; the worker retains it until GitHub confirms the merge |
| Auto-merge is enabled, CI is green, a review is done, or commits are pushed | These alone do not establish completion or merge |
| A PR is closed without merging | Retain; never treat closure as merge |
| A lower stacked PR merged but the feature/rebase/proof is still in progress | Keep working; do not sign off the feature's tree |
| The lower tree is complete but upper PRs still depend on it | Pin the dependency or include each upper PR with `--wait-for`; retain until resolved |
| One worktree per stack layer | Each owner signs off separately after its own work and dependent use are finished |
| Handoff, abandoned session, or required recovery evidence remains | Keep an owner claim or explicit pin; no timeout releases it |

The primary `--pr` must match the worktree's full local HEAD and repository.
It must target the repository's current default branch, or the deliberately
chosen final branch supplied with `--target`. Do not name a temporary stack
branch as the final target to make cleanup pass. Squash/rebase merges are
supported through the recorded PR head; Git ancestry alone is insufficient.
The local branch and original commits remain after removal.

```sh
gwt finish --pr https://github.com/example/project/pull/123 \
  --wait-for https://github.com/example/project/pull/124 \
  --wait-for https://github.com/example/project/pull/125
```

Dependencies pin their own head SHAs, which naturally differ from this tree's
HEAD. All must target and merge into the declared final branch. A merge into
another temporary stack branch does not qualify. Retarget the upper PRs before
sign-off. A dependent rebase invalidates that proof: explicitly
resume and rerun `finish` with the complete dependency list when the work is
finished again. Names such as `fix/` and `feat/` have no cleanup meaning.

## Ownership and recovery

`gwt cd` and reuse through `gwt new` claim an enrolled tree before entering it
and cancel previous completion. For direct-path agent launches or a resumed
saved session, run `gwt resume --worktree <path>` before entering or editing.
Claims do not expire. Every recorded owner must release its own claim.

```sh
gwt finish-pin --reason 'upper PR still needs this checkout'
gwt finish-unpin --reason 'upper PR still needs this checkout'
```

Pins are owner-scoped and persist until the same owner explicitly clears the
exact reason. Pin any needed snapshots, handovers, artifacts, or dependent work
before leaving. `--recovery-reviewed` attests that the owner inspected its
known active/saved recovery references and no longer needs this checkout;
it is not a request to delete those references. Unknown ownership, recovery
needs, or required evidence means retain. The worker never reads transcripts,
expires claims, mines history, or deletes saved sessions.

`finish` can also run from the canonical checkout using an exact
`--worktree <path>`. `finish-status` shows local claims, pins, state and the last
retention reason. `finish-check` refreshes evidence without removing anything:

```sh
gwt finish-status --all
gwt finish-check --worktree /path/to/managed/payment-fix
```

## Removal contract

The stdlib helper owns a separate private SQLite ledger under
`~/.local/state/gwt-finish`. It records identities at creation: canonical owner,
Git common/admin directories, filesystem device/inodes, local branch and
known shared root `node_modules` symlink. It does not use the Codex state DB.
Changing or recreating any recorded identity invalidates enrollment; no path
or branch-name reuse grants old deletion authority.

Removal first requires a qualified holder backend, before maintenance-lock or
candidate work. Release checks qualification before changing an owner's
release state. Neither path has an environment or CLI override.

Removal also requires explicit finish, all owners released with recovery reviewed,
no pins, fresh merged primary and dependency PRs, unchanged HEAD/identity, no
Git operation/locks, and clean tracked, untracked and ignored state. Unknown
or failed Git, GitHub, storage, or holder probes retain the tree. Ignored files
are protected because even non-force Git removal deletes them. Only the exact
root dependency symlink recorded at creation is disposable; its shared target
is preserved. Nested dependency links and other ignored outputs require
explicit separate handling; no broad generated-file exemption exists.
If an ignore rule such as `node_modules/` leaves the symlink untracked, the
helper reports `managed-dependency-link-is-untracked` and preserves it; it
does not unlink the dependency or force Git removal.

The helper holds its lifecycle lock and the compatible existing maintenance
lock during the final checks and serial non-force Git removal. It never
forces, prunes, deletes branches, reaps locks, kills holders, or enables broad
maintenance. Direct filesystem/Git access does not participate in that lock;
enrolled work must use the claim/resume contract before entry.

An interrupted removal remains `retiring`. A later check can verify complete
absence and the retained branch, but never blindly retries deletion. If the
original tree remains, both removal and resume stop for an explicit recovery
review; ordinary entry cannot clear ambiguous deletion state.

Local Git proof commands disable all transports, including lazy fetch and
protocol-specific configuration overrides. Missing local objects retain the
tree; fresh GitHub metadata continues through the explicit `ghx` API calls.

The local scheduler and exact host/repository policy belong in private
dotfiles. Installing the public runtime alone does not install a scheduler.
Report-only checks remain available regardless of policy. Apply mode consumes
only already enrolled, explicitly signed-off records; it never discovers
new candidates by scanning old worktrees.
Worker batches cover at most 16 signed-off/interrupted records, oldest check
first, releasing and reacquiring the lifecycle lock between exact rows.
Lock contention is a visible `lifecycle-busy` retention, not permission to enter.

## Design references

- [GTR's exact PR-head check](https://github.com/coderabbitai/git-worktree-runner/blob/cd723018caf03b5ca3873583b7043eee6f3306d3/lib/provider.sh#L169-L205)
  informs squash-merge proof.
- [Worktrunk's removal checks](https://github.com/max-sixty/worktrunk/blob/28399c2ae51862fb24b8a4ddf57d8e5b2b13f72e/src/git/remove.rs)
  inform fresh topology/identity checks and retention after races.
- [Git's post-merge contract](https://git-scm.com/docs/githooks#_post_merge)
  is local merge/pull activity, not a GitHub PR completion notification.

This lifecycle borrows those patterns without bulk prune, forced removal, or
process-reaping behavior. Agent Stop/SessionEnd hooks are not job sign-off.
