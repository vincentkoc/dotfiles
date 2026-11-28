# system

System maintenance shell functions.

Exports:

- `up`
- `doctor`

Use `up --system-only` for an interactive operating system update without tool
updaters, cleanup, project changes, or a sudo keepalive. On macOS it runs
`softwareupdate`; on Debian/Ubuntu it refreshes APT with `--error-on=any`, then
runs `apt-get upgrade --with-new-pkgs --no-remove`. It returns the OS updater's
status and stops before upgrading packages if the APT refresh fails.

The Linux upgrade sets `NEEDRESTART_MODE=l` for that command only, so needrestart
lists pending service restarts. Package scripts can still restart their own
services. A pending reboot is reported and must be scheduled separately.

`up --updates-only` updates the OS and global tools while skipping cleanup, DNS
signals, project pip/Go/Flutter steps, locate rebuilding, and the sudo keepalive.
The two flags are mutually exclusive. Bare `up` retains the legacy tool and
cleanup workflow.

Use `up --skip-macos` to skip macOS software updates. Combine it with
`--updates-only` to update tools without cleanup or project dependency changes:

```sh
up --updates-only --skip-macos
```

Run `up --help` to see all options.
`--skip-macos` does not skip Linux OS updates and cannot be combined with
`--system-only`.
