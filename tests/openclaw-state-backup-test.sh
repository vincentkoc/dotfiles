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
    case "$BACKUP_TEST_TAR_MODE" in
        fail) printf 'incomplete' >"$2"; exit 23 ;;
        corrupt) printf 'incomplete' >"$2"; exit 0 ;;
        badcrc)
            /usr/bin/tar "$@" || exit
            python3 - "$2" <<'PY'
import pathlib
import sys
path = pathlib.Path(sys.argv[1])
data = path.read_bytes()
path.write_bytes(data[:-8] + bytes(8))
PY
            exit 0 ;;
    esac
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

for i in $(seq -w 1 30); do
    printf 'retained\n' >"$backup_dir/openclaw-state-20240101_0000$i.tar.gz"
done
for mode in fail corrupt badcrc; do
    if BACKUP_TEST_TIMESTAMP=20300101_000001 BACKUP_TEST_TAR_MODE="$mode" \
        "$script" >"$temporary/$mode.out" 2>&1; then
        echo "failed tar was reported as a successful backup" >&2
        exit 1
    fi
    [[ ! -e "$backup_dir/openclaw-state-20300101_000001.tar.gz" ]]
    [[ -s "$archive" ]]
    for i in $(seq -w 1 30); do
        [[ -e "$backup_dir/openclaw-state-20240101_0000$i.tar.gz" ]]
    done
    [[ -z "$(find "$backup_dir" -maxdepth 1 -name '.openclaw-state.*' -print)" ]]
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

directory_collision="$backup_dir/openclaw-state-20300101_000005.tar.gz"
mkdir "$directory_collision"
if BACKUP_TEST_TIMESTAMP=20300101_000005 "$script" >"$temporary/directory.out" 2>&1; then
    echo "directory collision was reported as a successful backup" >&2
    exit 1
fi
[[ -d "$directory_collision" ]]
[[ -z "$(ls -A "$directory_collision")" ]]

symlink_collision="$backup_dir/openclaw-state-20300101_000006.tar.gz"
ln -s "$directory_collision" "$symlink_collision"
if BACKUP_TEST_TIMESTAMP=20300101_000006 "$script" >"$temporary/symlink.out" 2>&1; then
    echo "symlink collision was reported as a successful backup" >&2
    exit 1
fi
[[ -L "$symlink_collision" ]]
[[ -z "$(ls -A "$directory_collision")" ]]

concurrent_home="$temporary/concurrent-home"
if [[ "$(uname)" == Darwin ]]; then
    concurrent_backup="$concurrent_home/Library/Mobile Documents/com~apple~CloudDocs/dotfiles/.openclaw_backups/state"
else
    concurrent_backup="$concurrent_home/.dotfiles/.openclaw_backups/state"
fi
mkdir -p "$concurrent_backup" "$temporary/concurrent-bin" "$temporary/barrier"
for i in $(seq -w 1 30); do
    printf 'retained\n' >"$concurrent_backup/openclaw-state-20240101_0000$i.tar.gz"
done
cat >"$temporary/concurrent-bin/rm" <<'EOF'
#!/bin/sh
for target do
    case "$target" in
        */openclaw-state-20240101_000001.tar.gz)
            : >"$BACKUP_TEST_BARRIER/$$"
            attempts=0
            while [ "$(/bin/ls -1 "$BACKUP_TEST_BARRIER" | /usr/bin/wc -l)" -lt 2 ]; do
                attempts=$((attempts + 1))
                [ "$attempts" -lt 200 ] || exit 88
                /bin/sleep 0.05
            done ;;
    esac
done
exec /bin/rm "$@"
EOF
chmod +x "$temporary/concurrent-bin/rm"
BACKUP_TEST_TIMESTAMP=20300101_000010 BACKUP_TEST_BARRIER="$temporary/barrier" \
    HOME="$concurrent_home" PATH="$temporary/concurrent-bin:$PATH" \
    "$script" >"$temporary/concurrent-a.out" 2>&1 &
pid_a=$!
BACKUP_TEST_TIMESTAMP=20300101_000011 BACKUP_TEST_BARRIER="$temporary/barrier" \
    HOME="$concurrent_home" PATH="$temporary/concurrent-bin:$PATH" \
    "$script" >"$temporary/concurrent-b.out" 2>&1 &
pid_b=$!
wait "$pid_a"
wait "$pid_b"
[[ -s "$concurrent_backup/openclaw-state-20300101_000010.tar.gz" ]]
[[ -s "$concurrent_backup/openclaw-state-20300101_000011.tar.gz" ]]
[[ $(find "$concurrent_backup" -name 'openclaw-state-*.tar.gz' | wc -l | tr -d ' ') -eq 30 ]]

echo 'openclaw state backup tests passed'
