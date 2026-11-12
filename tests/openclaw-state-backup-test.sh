#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="$root/bin/openclaw-state-backup.sh"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

export HOME="$temporary/home"
export OPENCLAW_LOCAL_STATE_BASE="$HOME/state"
if [[ "$(uname)" == Darwin ]]; then
    backup_dir="$HOME/Library/Mobile Documents/com~apple~CloudDocs/dotfiles/.openclaw_backups/state"
else
    backup_dir="$HOME/.dotfiles/.openclaw_backups/state"
fi
mkdir -p "$OPENCLAW_LOCAL_STATE_BASE/cron" "$OPENCLAW_LOCAL_STATE_BASE/memory" "$temporary/bin"
printf 'scheduled\n' >"$OPENCLAW_LOCAL_STATE_BASE/cron/jobs.json"
printf 'memory\n' >"$OPENCLAW_LOCAL_STATE_BASE/memory/notes.txt"
printf 'excluded\n' >"$OPENCLAW_LOCAL_STATE_BASE/cron/old.log"

cat >"$temporary/bin/date" <<'EOF'
#!/bin/sh
if [ "$1" = '+%Y%m%d_%H%M%S' ]; then
    printf '%s\n' "$BACKUP_TEST_TIMESTAMP"
else
    exec /bin/date "$@"
fi
EOF
cat >"$temporary/bin/tar" <<'EOF'
#!/bin/sh
if [ "$1" = -czf ] && [ -n "${BACKUP_TEST_TAR_MODE:-}" ]; then
    printf 'incomplete' >"$2"
    [ "$BACKUP_TEST_TAR_MODE" = corrupt ] && exit 0
    exit 23
fi
exec /usr/bin/tar "$@"
EOF
chmod +x "$temporary/bin/date" "$temporary/bin/tar"
export PATH="$temporary/bin:$PATH"

BACKUP_TEST_TIMESTAMP=20300101_000000 "$script" >"$temporary/success.out"
archive="$backup_dir/openclaw-state-20300101_000000.tar.gz"
[[ -s "$archive" ]]
tar -tzf "$archive" >"$temporary/contents"
grep -q '^cron/jobs.json$' "$temporary/contents"
grep -q '^memory/notes.txt$' "$temporary/contents"
if grep -q 'old.log' "$temporary/contents"; then
    echo "excluded log entered the backup" >&2
    exit 1
fi

for mode in fail corrupt; do
    if BACKUP_TEST_TIMESTAMP=20300101_000001 BACKUP_TEST_TAR_MODE="$mode" \
        "$script" >"$temporary/$mode.out" 2>&1; then
        echo "failed tar was reported as a successful backup" >&2
        exit 1
    fi
    [[ ! -e "$backup_dir/openclaw-state-20300101_000001.tar.gz" ]]
    [[ -s "$archive" ]]
done

for i in $(seq -w 1 30); do
    printf 'retained\n' >"$backup_dir/openclaw-state-20240101_0000$i.tar.gz"
done
BACKUP_TEST_TIMESTAMP=20300101_000002 "$script" >"$temporary/retention.out"
[[ ! -e "$backup_dir/openclaw-state-20240101_000001.tar.gz" ]]
[[ ! -e "$backup_dir/openclaw-state-20240101_000002.tar.gz" ]]
[[ -e "$backup_dir/openclaw-state-20240101_000003.tar.gz" ]]
[[ $(find "$backup_dir" -name 'openclaw-state-*.tar.gz' | wc -l | tr -d ' ') -eq 30 ]]

rm -r "$OPENCLAW_LOCAL_STATE_BASE/memory"
BACKUP_TEST_TIMESTAMP=20300101_000003 "$script" >"$temporary/cron-only.out"
tar -tzf "$backup_dir/openclaw-state-20300101_000003.tar.gz" >"$temporary/cron-only-contents"
grep -q '^cron/jobs.json$' "$temporary/cron-only-contents"
if grep -q '^memory/' "$temporary/cron-only-contents"; then
    echo "absent memory directory entered the backup" >&2
    exit 1
fi

collision="$backup_dir/openclaw-state-20300101_000004.tar.gz"
printf 'existing\n' >"$collision"
if BACKUP_TEST_TIMESTAMP=20300101_000004 "$script" >"$temporary/collision.out" 2>&1; then
    echo "backup collision replaced a prior archive" >&2
    exit 1
fi
[[ "$(cat "$collision")" == existing ]]

echo 'openclaw state backup tests passed'
