# Userscripts

Source of truth for Firefox UserMonkey scripts.

## Layout

- `userscripts/*.user.js`: installable userscripts
- `userscripts/templates/base.user.js`: starter template for new scripts

## Current scripts

- `github-pr-copy-open-review-thread.user.js`

## Install in UserMonkey

1. Open the script file in this repo.
2. Copy full contents.
3. In Firefox UserMonkey, create/import script and paste.
4. Save and verify it runs on the configured `@match` URL.

## Sync workflow

- Edit scripts in git first (this repo).
- Re-paste into UserMonkey after edits.
- Keep extension export backups as a secondary safety copy.

## Versioning convention

Use semver in `@version`:

- Patch (`1.0.1`): bug fixes and non-behavioral cleanup.
- Minor (`1.1.0`): new behavior that is backward compatible.
- Major (`2.0.0`): breaking behavior changes.

Every functional change should bump `@version` and include a short commit note.

## Optional auto-update metadata

If you publish scripts to a raw URL (GitHub raw/Gist raw), add:

- `@downloadURL`
- `@updateURL`

Then UserMonkey can check for updates from that URL.
