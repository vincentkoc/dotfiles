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
proof of those permissions. `gwt root` is informational.

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

Report the checkout as retained and finish the task. Physical retirement is a
separate, ownership-qualified operation; cancellation never authorizes it.

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

`gwt finish` records completion. `gwt finish --release` additionally signs off
this owner's future checkout use. Release is deliberate; generic Codex Stop is
turn-scoped attention state and never creates completion or release.

```sh
# Codex supplies CODEX_THREAD_ID. Otherwise use a stable task identity.
export GWT_OWNER_ID=payment-fix
gwt new fix/payment origin/main --finish-managed

# Implementation is complete, but keep this owner's checkout claim.
gwt finish --pr https://github.com/example/project/pull/123

# Once the checkout is no longer needed, sign off explicitly.
gwt finish --pr https://github.com/example/project/pull/123 --release
```

Only newly created `--finish-managed` worktrees receive release-capable
records. Creation uses a private checkout/admin directory and a native Git lock
with a unique lifecycle marker. Existing report-only enrollments, shared and
repository-native worktrees are never adopted. Existing ancestor permissions
are not changed. The private SQLite ledger remains under
`~/.local/state/gwt-finish`.

A finished fix or feature is eligible as soon as every owner has completed and
released, its exact PR head has merged into the final target, and native
admission passes. There is no age delay. `gwt release` can sign off an already
completed owner without repeating the PR arguments.

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

The wrapper parks only its own shell before release. It cannot move the parent
Codex process. An alive parent outside the checkout/admin paths may remain
alive; any actual CWD, FD, thread CWD, fileport or mapped reference inside those
domains blocks removal. The pending consumer revisits departure and merges:

```sh
gwt finish-status --all
gwt finish-check --all                   # Refresh proof only
# Only after explicit host activation:
gwt finish-check --all --apply --policy /absolute/private/policy.json
```

The evaluator removes one exact checkout at a time with native non-force
`git worktree remove`. It preserves local branches, siblings and external
dependency targets. It never runs GC, prunes, or selects unenrolled worktrees.
Ignored content is not assumed disposable: the initial supported exception is
an exact root `node_modules` symlink to an unchanged owned external directory.
Unknown files, real ignored directories, unresolved Git recovery data, changed
bytes/index/modes, pins or locks retain the checkout.
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
reduce to the committed message; divergent drafts, unsupported cleanup modes
and verbose/scissors residue retain.
Reads are bounded to 8MiB per leaf, 512MiB of working bytes per pass and 131072
filesystem entries including directories. Apply uses one release inventory and
two admission inventories; it does not expand sparse checkouts or install tools.

Before unlocking its unique native lock, the owner durably commits a removal
intent. It then repeats admission and observes checkout, admin, registration,
branch and sibling outcomes independently. Git can delete admin metadata even
when checkout deletion fails. A crash, timeout, partial result or failed
readback therefore remains `unknown`/`incomplete`, never an automatic retry.
Updated manual removal, prune, cleaner and quarantine consumers respect the
ledger hold even after registration disappears. Inspect and reconcile that
exact intent read-only before proposing recovery.

### Activation and proof limits

Installing source does not activate deletion. Version-1 `report-only` policies
and existing enrollments retain their contract. A version-2 policy has schema
`gwt-finish-policy.v2`, mode `explicit-release`, the stable host identity and
one to eight exact owner/root/common-directory identities. Its `qualification`
binds the exact platform, native Git 2.55.0 path/hash/version, installed lifecycle
source hashes, known process identities and a private activation receipt.

The current Darwin arm64 backend reports `mapping_coverage: unqualified`.
Public libproc APIs omit submap vnode details and can hide vnode acquisition
failures. Automatic Mac deletion therefore remains blocked, even with a valid
external qualification or activation receipt. Completion and explicit release
still record their facts.
Linux, WSL and Windows have no deletion qualification in this change. The private
`scripts/install-gwt-finish.py` owner must gain a reviewed v2 activation route
before use; its existing report-only scheduler must not be relabelled as ready.
Reuse that scheduler for the bounded pending consumer, not another daemon.

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

`holder-qualification` describes this mapping-coverage hold without scanning
processes. External policy cannot override the native observation. `finish-status`
never grants fresh removal authority: `automatic_removal_supported` identifies
a new-format record, while `removal_available` remains false in the report.
Always report the recorded `retained`, verified `removed`, or `unknown` checkout
outcome and its reason.
