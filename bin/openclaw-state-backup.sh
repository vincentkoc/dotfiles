#!/usr/bin/env bash
#
# OpenClaw state backup script
# Creates timestamped tar.gz snapshots of cron + memory state
# Backups go to iCloud dotfiles for sync/restore capability
#

set -e

# Detect dotfiles location
if [[ "$(uname)" == "Darwin" ]]; then
    DOTFILES_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/dotfiles"
else
    DOTFILES_DIR="$HOME/.dotfiles"
fi

# Paths
OPENCLAW_LOCAL_STATE="${OPENCLAW_LOCAL_STATE_BASE:-$HOME/.openclaw_state}"
BACKUP_DIR="$DOTFILES_DIR/.openclaw_backups/state"
MAX_BACKUPS=30  # Keep last 30 backups (~10 days at 3x/day)

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# Timestamp for backup filename
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/openclaw-state-$TIMESTAMP.tar.gz"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting OpenClaw state backup..."

# Check if local state exists
if [[ ! -d "$OPENCLAW_LOCAL_STATE" ]]; then
    echo "[WARN] Local state directory not found: $OPENCLAW_LOCAL_STATE"
    echo "[WARN] Run: ./claw.sh move-state-off-icloud first"
    exit 0
fi

sources=()
[[ -d "$OPENCLAW_LOCAL_STATE/cron" ]] && sources+=(cron)
[[ -d "$OPENCLAW_LOCAL_STATE/memory" ]] && sources+=(memory)
if [[ ${#sources[@]} -eq 0 ]]; then
    echo "[WARN] No cron or memory directories found in $OPENCLAW_LOCAL_STATE"
    exit 0
fi

# Build and check the archive before it can replace a recovery set.
cd "$OPENCLAW_LOCAL_STATE"
TEMP_FILE="$(mktemp "$BACKUP_DIR/.openclaw-state.XXXXXX")"
trap 'rm -f -- "$TEMP_FILE"' EXIT
if ! tar -czf "$TEMP_FILE" \
    --exclude='*.log' \
    --exclude='*.tmp' \
    "${sources[@]}" 2>/dev/null; then
    echo "[ERROR] Backup archive creation failed" >&2
    exit 1
fi

if [[ ! -s "$TEMP_FILE" ]] || ! tar -tzf "$TEMP_FILE" >/dev/null 2>&1; then
    echo "[ERROR] Backup archive validation failed" >&2
    exit 1
fi

if ! ln -- "$TEMP_FILE" "$BACKUP_FILE"; then
    echo "[ERROR] Backup destination already exists or cannot be published" >&2
    exit 1
fi
rm -f -- "$TEMP_FILE"
trap - EXIT
SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[OK] Backup created: $BACKUP_FILE ($SIZE)"

# Retain the newest timestamped sets only after a validated publication.
shopt -s nullglob
candidates=("$BACKUP_DIR"/openclaw-state-*.tar.gz)
shopt -u nullglob
backups=()
for candidate in "${candidates[@]}"; do
    name="${candidate##*/}"
    if [[ ! "$name" =~ ^openclaw-state-[0-9]{8}_[0-9]{6}\.tar\.gz$ || ! -f "$candidate" || -L "$candidate" ]]; then
        echo "[WARN] Backup retention held: unexpected archive entry" >&2
        backups=()
        break
    fi
    backups+=("$candidate")
done
if [[ ${#backups[@]} -gt "$MAX_BACKUPS" ]]; then
    DELETE_COUNT=$((${#backups[@]} - MAX_BACKUPS))
    echo "[INFO] Cleaning up $DELETE_COUNT old backup(s)..."
    for ((i = 0; i < DELETE_COUNT; i++)); do
        rm -- "${backups[i]}"
    done
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup complete."
