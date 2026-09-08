# Sparse Profiles

`gwt` reads these profiles beside its physically loaded dotfiles module, including
when the module is reached through a symlink. `DOTFILES_GIT_SPARSE_ROOT` overrides
that location when explicitly set. `.exports` no longer sets an iCloud default.
An existing shell can retain the old exported default: run
`unset DOTFILES_GIT_SPARSE_ROOT` to select the module's profiles, unless that
override is intentional.

Profiles are definitions, not live checkout state. Updating them does not reapply
sparsity to existing checkouts. Inspect the target checkout and its local changes,
then explicitly run the desired command from that checkout:

```sh
gwt sparse status
gwt sparse set core
gwt sparse add /another-directory/
gwt sparse full
```

`*.patterns` profiles use non-cone mode without a sparse index; `*.paths` profiles
use cone mode with a sparse index. `add` preserves the existing mode. From a full
checkout, `add` starts a cone checkout containing the supplied directories.
Use directory names for cone mode and Git sparse patterns for non-cone mode.

Profile application uses one native Git `set`, without an intermediate empty
`init`. Git owns worktree-config migration and checkout safety. Metadata is
written only after the native operation succeeds. A later metadata failure is
reported as such, not as a rollback: inspect `gwt sparse status` before retrying.
Native Git may remove ignored files in directories leaving a cone; this wrapper
does not promise preservation of all ignored files.

## OpenClaw Repositories

The September 8, 2026 source audit pinned these contracts:

- `openclaw/openclaw` at `d38aa7968c7c8a6b28374ad1f3985e7de0c4af33`:
  `core` includes root files, workspace packages, the complete UI, agent guides,
  configuration, QA scores, security, skills, docs, and supporting source tools.
  Native app trees remain excluded except the tool-display JSON and generated
  host-security Swift snapshots consumed by core checks.
- `openclaw/clawhub` at `d3bde70e3c9373720d4d3e335f9935399ea2c008`:
  `.agents/skills` contains contributor guidance and stays included; the
  top-level `skills` archive stays excluded.
- `openclaw/skills` at `610bae7e80761ef639acb6bca3dd8886ab92dacf`:
  the existing metadata-only profile and `blob:none` clone filter are unchanged.
  The top-level `skills` archive stays excluded.

These profiles do not narrow fetch refspecs or change other Git policy. Tests
apply them to disposable native-Git fixtures; they do not reapply active
checkouts or fetch repository history.
