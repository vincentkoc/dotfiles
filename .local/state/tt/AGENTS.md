# tt snapshot and recovery instructions

This directory contains live recovery state. Read these instructions before
restoring a cockpit from this directory or its `history/` snapshots. Use the
installed `codex-session-recovery` skill and its tt recovery reference.

## Source and installation

- The maintained source is `.local/state/tt/AGENTS.md` in the owning dotfiles
  checkout. `setup_tt_recovery_guidance` in `install.sh` installs this file at
  `${XDG_STATE_HOME:-$HOME/.local/state}/tt/AGENTS.md`.
- Mackup's tmux profile backs up this instruction file only. Never add snapshot
  TSVs, session IDs, pane markers, logs, locks or timer state to public dotfiles.
  Its mapping covers the default state root; custom `XDG_STATE_HOME` locations
  require a matching private backup configuration.
- When the installed guide is missing, install this file alone and verify it
  matches the source. Do not run the full installer or replace the state tree.

## Preserve exact owners and restore inputs

- Inspect live panes, `tt status`, snapshot history and an explicit restore
  preview before mutation. Preserve the selected snapshot and its hash; do not
  save degraded live state over the recovery source.
- Bind each saved target to its exact UUID, CWD and title. Preserve shells,
  partial windows, outside controllers and unrelated sessions. Read persisted
  pane-marker records even when the snapshot has no marker columns.
- A user-authorized full close and re-restore means recreate the named cockpit,
  including its Codex processes. Do not replace that request with another
  one-pane repair. Keep any attached client through a task-owned holding session
  if needed, close only the audited cockpit, and verify its old exact UUID
  processes exited before resuming them. Never use broad process-name kills.

## Native appearance must precede every Codex launch

1. With an existing server, disable native autosave before reconstruction. For
   a cold start, bootstrap only a shell with native hooks/autosave absent, then
   disable native autosave before building the scaffold or running setup helpers.
   Do not require the off command to succeed before a server exists. Keep it off
   until verification; do not publish scaffold or holding-session state.
2. Build the exact shell scaffold. For six-pane studio windows use **three
   columns and two rows**, ordered 1/2 left, 3/4 center, 5/6 right. Preserve
   partial-window geometry; do not pad windows or use generic tiled layouts.
   When historical geometry is absent, retain exact counts and use a 2×2 grid
   for four panes or a vertical pair for two; describe that as a fallback.
3. Apply native terminal settings, explicit RGB foreground/background defaults,
   saved titles and persisted pane-marker styles before launching any Codex
   process. Include both active and inactive styles and default/unmarked panes.
4. Use the installed `tt` native agent launch contract, including its color
   prefix and login-shell fallback. Retain exact UUID/original options and use
   `--no-alt-screen`. Do not add a goals-disabling override during restoration.
   The current prefix clears `NO_COLOR`/`TERMINFO` and supplies `CLICOLOR=1`,
   `CLICOLOR_FORCE=1`, `FORCE_COLOR=3`, `COLORTERM=truecolor` and native
   `TERMINFO_DIRS`. Sanitizing global tmux variables alone is insufficient.
5. Verify settings before the first launch. With an existing server,
   `tt recovery-config` can feed `tmux source-file -`. Do not rely on
   `tmux -f /dev/stdin new-session` to load startup configuration. Use a real
   temporary config if a fresh server requires it; do not launch on defaults.

Codex caches its startup palette probe. Applying `tt colorfix` or attaching a
terminal afterward cannot repair a process that started without valid RGB
defaults. Bare `codex resume` also bypasses the native color environment.
Use native helpers where they preserve the audited map; if a custom exact
restore is necessary, preserve these native contracts in full.

## Preserve goals and their native continuation

- Preserve the original goals feature configuration. Never add `--disable goals`
  or `features.goals=false` as a recovery precaution. Remove recovery-added
  overrides from corrected launch plans; retain intentional user preferences.
- Inspect native thread goal state read-only before owner replacement when
  available. Preserve ID, objective, lifecycle, token/time budgets and consumed
  usage. Report missing state as unknown; do not recreate goals or reset budgets.
- Active goals retain normal native continuation. Paused, blocked, complete,
  cancelled and budget-limited goals retain their saved states. Do not issue
  blanket resume/pause/clear commands or edit goal databases. An explicit user
  request to change lifecycle is separate from cockpit restoration.
- Interrupting a turn can pause its goal. Preserve busy owners. If correcting a
  launch flag requires a restart, obtain current authority naming the exact
  eligible owners; completed earlier restore permission does not cover another
  restart cycle. Preserve safety notices; do not automatically retry affected
  requests through recovery.

## Completion proof

- Verify every target/UUID/CWD/title, actual pane geometry and saved marker
  style. Process counts alone are not restoration proof.
- Restore native automatic window sizing after temporary scaffold dimensions.
  `resize-window` creates local `window-size manual` overrides that defeat a
  global `latest` policy. Remove only task-created overrides with
  `set-window-option -u -t <window-id> window-size`, preserving intentional prior
  settings. Check each window's effective policy and client dimensions; do not
  leave the restored cockpit locked to scaffold sizes.
- Verify actual colored text and input-background shading in every restored
  Codex UI. Bold/dim output, `TERM` and an idle-looking glyph are insufficient.
  Inspect selected color variables only; never dump full environments.
- Resolve startup directory-choice dialogs according to the audited CWD map;
  a dialog is not a loaded conversation. Do not send new task text or change
  goal lifecycle; allow the restored thread's normal native continuation.
- Verify goal capability, unchanged budgets and no reset or recovery-induced
  lifecycle change after launch. Normal progress may increase usage, complete
  the goal or reach its budget. Check for recovery-added disabling flags; a
  matching UUID alone does not prove that the process loaded its persisted goal.
- Return the attached client to its mapped pane, remove only the task-owned
  holding session, then enable native menus/autosave. Preserve the source hash
  and outside processes. For a full rebuild, verify all old owners were replaced.
- Stop after verification or the user's confirmation that it works. Newly
  active panes do not authorize another audit, restart or interruption.
