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

# Check if there's anything to backup
if [[ ! -d "$OPENCLAW_LOCAL_STATE/cron" && ! -d "$OPENCLAW_LOCAL_STATE/memory" ]]; then
    echo "[WARN] No cron or memory directories found in $OPENCLAW_LOCAL_STATE"
    exit 0
fi

# Create the backup
cd "$OPENCLAW_LOCAL_STATE"
tar -czf "$BACKUP_FILE" \
    --exclude='*.log' \
    --exclude='*.tmp' \
    cron memory 2>/dev/null || true

if [[ -f "$BACKUP_FILE" ]]; then
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "[OK] Backup created: $BACKUP_FILE ($SIZE)"
else
    echo "[ERROR] Failed to create backup"
    exit 1
fi

# Cleanup old backups (keep last MAX_BACKUPS)
cd "$BACKUP_DIR"
BACKUP_COUNT=$(ls -1 openclaw-state-*.tar.gz 2>/dev/null | wc -l | tr -d ' ')

if [[ "$BACKUP_COUNT" -gt "$MAX_BACKUPS" ]]; then
    DELETE_COUNT=$((BACKUP_COUNT - MAX_BACKUPS))
    echo "[INFO] Cleaning up $DELETE_COUNT old backup(s)..."
    ls -1t openclaw-state-*.tar.gz | tail -n "$DELETE_COUNT" | xargs rm -f
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup complete."
