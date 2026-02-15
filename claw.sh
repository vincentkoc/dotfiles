#!/usr/bin/env bash
#
# OpenClaw setup script - handles migration from clawdbot to openclaw
# Also manages runtime state (moves off iCloud to avoid SQLite/cron issues)
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Detect dotfiles location
if [[ "$(uname)" == "Darwin" ]]; then
    DOTFILES_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/dotfiles"
else
    DOTFILES_DIR="$HOME/.dotfiles"
fi

# OpenClaw runtime state (mutable) should NOT live on iCloud Drive.
# We keep config in dotfiles/.openclaw but move hot state to local disk and symlink back.
OPENCLAW_LOCAL_STATE_BASE="${OPENCLAW_LOCAL_STATE_BASE:-$HOME/.openclaw_state}"
OPENCLAW_ICLOUD_STATE_DIR="$DOTFILES_DIR/.openclaw"
OPENCLAW_BACKUP_DIR="$DOTFILES_DIR/.openclaw_backups"
OPENCLAW_ALLOW_GIT_NOSYNC="${OPENCLAW_ALLOW_GIT_NOSYNC:-0}"

ensure_dir() {
    local d="$1"
    mkdir -p "$d"
    chmod 700 "$d" 2>/dev/null || true
}

# iCloud Drive: mark a path as "do not sync" by renaming to *.nosync
mark_nosync_path() {
    local p="$1"
    if [[ ! -e "$p" ]]; then
        warn "Not found: $p"
        return
    fi
    if [[ "$p" == *.nosync ]]; then
        success "Already nosync: $p"
        return
    fi
    mv "$p" "${p}.nosync"
    success "Marked nosync: ${p}.nosync"
}

mark_nosync_globs() {
    local root="$1"
    local pattern="$2"
    local matches=()

    while IFS= read -r -d '' d; do
        matches+=("$d")
    done < <(find "$root" -type d -name "$pattern" -print0 2>/dev/null || true)

    if [[ ${#matches[@]} -eq 0 ]]; then
        info "No matches for $pattern under $root"
        return
    fi

    for d in "${matches[@]}"; do
        mark_nosync_path "$d"
    done
}

# Mark OpenClaw/Clawd paths as nosync (no symlinks).
mark_openclaw_nosync() {
    info "Marking OpenClaw/Clawd paths as nosync (rename to *.nosync)..."

    if [[ ! -d "$OPENCLAW_ICLOUD_STATE_DIR" ]]; then
        warn "Missing: $OPENCLAW_ICLOUD_STATE_DIR"
    fi

    # .openclaw/workspace* (workspace, workspace-main, etc.)
    if [[ -d "$OPENCLAW_ICLOUD_STATE_DIR" ]]; then
        mark_nosync_globs "$OPENCLAW_ICLOUD_STATE_DIR" "workspace*"
    fi

    # .openclaw/logs
    mark_nosync_path "$OPENCLAW_ICLOUD_STATE_DIR/logs"

    # .openclaw/extensions/openclaw-supermemory
    mark_nosync_path "$OPENCLAW_ICLOUD_STATE_DIR/extensions/openclaw-supermemory"

    # .openclaw/agents/**/sessions (each sessions folder)
    local agent_sessions=()
    while IFS= read -r -d '' d; do
        agent_sessions+=("$d")
    done < <(find "$OPENCLAW_ICLOUD_STATE_DIR/agents" -mindepth 2 -maxdepth 2 -type d -name "sessions" -print0 2>/dev/null || true)
    if [[ ${#agent_sessions[@]} -eq 0 ]]; then
        info "No agent sessions folders under $OPENCLAW_ICLOUD_STATE_DIR/agents"
    else
        for d in "${agent_sessions[@]}"; do
            mark_nosync_path "$d"
        done
    fi

    # Any folder including ".bak" in .openclaw or clawd
    for root in "$OPENCLAW_ICLOUD_STATE_DIR" "$DOTFILES_DIR/clawd"; do
        if [[ -d "$root" ]]; then
            mark_nosync_globs "$root" "*\.bak*"
        fi
    done

    # __pycache__, .git, .venv, node_modules across .openclaw and clawd
    for root in "$OPENCLAW_ICLOUD_STATE_DIR" "$DOTFILES_DIR/clawd"; do
        if [[ ! -d "$root" ]]; then
            continue
        fi

        # Skip .git by default to avoid breaking repos.
        local names=( "__pycache__" ".venv" "node_modules" )
        local n
        for n in "${names[@]}"; do
            mark_nosync_globs "$root" "$n"
        done

        if [[ "$OPENCLAW_ALLOW_GIT_NOSYNC" == "1" ]]; then
            mark_nosync_globs "$root" ".git"
        else
            warn "Skipping .git under $root (set OPENCLAW_ALLOW_GIT_NOSYNC=1 to rename)"
        fi
    done

    success "Nosync marking completed."
}

# Setup config directory symlinks
setup_config_symlinks() {
    info "Setting up config symlinks..."

    # ~/.openclaw -> dotfiles/.openclaw
    if [[ -d "$DOTFILES_DIR/.openclaw" ]]; then
        if [[ -L "$HOME/.openclaw" ]]; then
            success "~/.openclaw symlink exists"
        elif [[ -d "$HOME/.openclaw" ]]; then
            warn "~/.openclaw is a directory - backup and remove manually"
        else
            ln -sf "$DOTFILES_DIR/.openclaw" "$HOME/.openclaw"
            success "~/.openclaw symlinked"
        fi
    else
        warn ".openclaw not found in dotfiles"
    fi

    # ~/.clawdbot -> dotfiles/.openclaw (legacy)
    if [[ -d "$DOTFILES_DIR/.openclaw" ]]; then
        if [[ -L "$HOME/.clawdbot" ]]; then
            success "~/.clawdbot legacy symlink exists"
        elif [[ -d "$HOME/.clawdbot" ]]; then
            warn "~/.clawdbot is a directory - backup and remove manually"
        else
            ln -sf "$DOTFILES_DIR/.openclaw" "$HOME/.clawdbot"
            success "~/.clawdbot -> .openclaw legacy symlink created"
        fi
    fi

    # clawdbot.json -> openclaw.json (legacy config file)
    if [[ -f "$DOTFILES_DIR/.openclaw/openclaw.json" ]]; then
        if [[ -L "$DOTFILES_DIR/.openclaw/clawdbot.json" ]]; then
            success "clawdbot.json -> openclaw.json symlink exists"
        elif [[ -f "$DOTFILES_DIR/.openclaw/clawdbot.json" ]]; then
            warn "clawdbot.json exists as file - remove manually if needed"
        else
            ln -sf openclaw.json "$DOTFILES_DIR/.openclaw/clawdbot.json"
            success "clawdbot.json -> openclaw.json legacy symlink created"
        fi
    fi

    # ~/clawd -> dotfiles/clawd
    if [[ -d "$DOTFILES_DIR/clawd" ]]; then
        if [[ -L "$HOME/clawd" ]]; then
            success "~/clawd symlink exists"
        elif [[ -d "$HOME/clawd" ]]; then
            warn "~/clawd is a directory - backup and remove manually"
        else
            ln -sf "$DOTFILES_DIR/clawd" "$HOME/clawd"
            success "~/clawd symlinked"
        fi
    fi
}

# Setup node_modules symlink for legacy clawdbot package
setup_node_modules_symlink() {
    info "Setting up node_modules symlinks..."

    local node_modules=""

    # Try nodenv first
    if command -v nodenv &>/dev/null; then
        local ver=$(nodenv version-name 2>/dev/null)
        if [[ -n "$ver" && "$ver" != "system" ]]; then
            node_modules="$HOME/.nodenv/versions/$ver/lib/node_modules"
        fi
    fi

    # Fallback to nvm
    if [[ -z "$node_modules" && -d "$HOME/.nvm" ]]; then
        local nvm_current=$(node -v 2>/dev/null)
        if [[ -n "$nvm_current" ]]; then
            node_modules="$HOME/.nvm/versions/node/$nvm_current/lib/node_modules"
        fi
    fi

    # Fallback to global npm
    if [[ -z "$node_modules" ]]; then
        node_modules=$(npm root -g 2>/dev/null || echo "")
    fi

    if [[ -z "$node_modules" || ! -d "$node_modules" ]]; then
        warn "Could not find node_modules directory"
        return
    fi

    # Check if openclaw is installed
    if [[ ! -d "$node_modules/openclaw" ]]; then
        warn "openclaw not installed in $node_modules"
        info "Run: npm install -g openclaw"
        return
    fi

    # Create clawdbot -> openclaw symlink
    if [[ -L "$node_modules/clawdbot" ]]; then
        success "clawdbot -> openclaw symlink exists"
    elif [[ -d "$node_modules/clawdbot" ]]; then
        warn "clawdbot package exists - uninstall with: npm uninstall -g clawdbot"
    else
        ln -sf "$node_modules/openclaw" "$node_modules/clawdbot"
        success "clawdbot -> openclaw node_modules symlink created"
    fi

    info "node_modules: $node_modules"
}

# Move mutable gateway state off iCloud Drive and symlink it back.
# This addresses cron.list timeouts + SQLite 'readonly database' issues.
move_state_off_icloud() {
    info "Moving OpenClaw runtime state off iCloud (cron + memory)..."

    if [[ ! -d "$OPENCLAW_ICLOUD_STATE_DIR" ]]; then
        warn "iCloud state dir not found: $OPENCLAW_ICLOUD_STATE_DIR"
        return
    fi

    ensure_dir "$OPENCLAW_LOCAL_STATE_BASE"

    for sub in cron memory; do
        local src="$OPENCLAW_ICLOUD_STATE_DIR/$sub"
        local dst="$OPENCLAW_LOCAL_STATE_BASE/$sub"

        # If already a symlink, assume done.
        if [[ -L "$src" ]]; then
            success "$src already symlinked"
            continue
        fi

        # If destination doesn't exist, create.
        if [[ ! -d "$dst" ]]; then
            ensure_dir "$dst"
        fi

        # If source exists as a real dir, move contents to local.
        if [[ -d "$src" ]]; then
            local backup="$OPENCLAW_ICLOUD_STATE_DIR/${sub}.bak.$(date +%Y%m%d_%H%M%S)"
            info "Backing up $src -> $backup"
            cp -R "$src" "$backup"

            info "Moving contents to local state: $dst"
            cp -R "$src/"* "$dst/" 2>/dev/null || true
            rm -rf "$src"
        fi

        # Create symlink back.
        ln -sfn "$dst" "$src"
        success "Symlinked $src -> $dst"
    done

    info ""
    info "NOTE: Restart the gateway after this so it reopens the DB from local disk."
}

# Run backup immediately
backup_state_now() {
    local backup_script="$DOTFILES_DIR/bin/openclaw-state-backup.sh"
    if [[ ! -x "$backup_script" ]]; then
        warn "Backup script not found or not executable: $backup_script"
        warn "Expected it at dotfiles/bin/openclaw-state-backup.sh"
        return 1
    fi
    "$backup_script"
}

# Print crontab entry for manual install
print_backup_crontab() {
    local backup_script="$DOTFILES_DIR/bin/openclaw-state-backup.sh"
    cat <<EOF
# OpenClaw state backup (cron + memory) -> iCloud dotfiles
# Install with: crontab -e
# Runs 3x/day at 7:15, 12:15, 18:15
15 7,12,18 * * * "$backup_script" >> "$OPENCLAW_BACKUP_DIR/backup.log" 2>&1
EOF
}

# Install/update crontab automatically
install_backup_crontab() {
    info "Installing crontab entries for OpenClaw state backups (3x/day)..."

    local backup_script="$DOTFILES_DIR/bin/openclaw-state-backup.sh"
    if [[ ! -x "$backup_script" ]]; then
        error "Missing backup script: $backup_script"
        return 1
    fi

    ensure_dir "$OPENCLAW_BACKUP_DIR"

    local marker_begin="# BEGIN OPENCLAW STATE BACKUP"
    local marker_end="# END OPENCLAW STATE BACKUP"

    local current
    current="$(crontab -l 2>/dev/null || true)"

    # Remove any existing block
    local cleaned
    cleaned="$(printf "%s\n" "$current" | awk -v b="$marker_begin" -v e="$marker_end" '
        $0==b {skip=1; next}
        $0==e {skip=0; next}
        skip!=1 {print}
    ')"

    local block
    block=$(cat <<EOF
$marker_begin
15 7,12,18 * * * "$backup_script" >> "$OPENCLAW_BACKUP_DIR/backup.log" 2>&1
$marker_end
EOF
)

    printf "%s\n\n%s\n" "$cleaned" "$block" | crontab -
    success "Crontab installed/updated."
    info "Verify with: crontab -l"
}

# Show usage
usage() {
    cat <<EOF
Usage: ./claw.sh <command>

Commands:
  setup                    Set up symlinks (config + node_modules)
  move-state-off-icloud    Migrate cron+memory state to local disk + symlink back
  mark-nosync              Rename selected paths to *.nosync (no iCloud sync)
  backup-state-now         Run one backup immediately
  print-backup-crontab     Print the crontab lines (for manual install)
  install-backup-crontab   Install/update crontab block automatically
  help                     Show this help

Paths:
  Local state dir:  $OPENCLAW_LOCAL_STATE_BASE
  iCloud state dir: $OPENCLAW_ICLOUD_STATE_DIR
  Backups dir:      $OPENCLAW_BACKUP_DIR
EOF
}

main() {
    local cmd="${1:-setup}"

    echo ""
    echo "╔═══════════════════════════════════════════╗"
    echo "║         OpenClaw Setup                    ║"
    echo "╚═══════════════════════════════════════════╝"
    echo ""

    case "$cmd" in
        setup)
            setup_config_symlinks
            echo ""
            setup_node_modules_symlink
            ;;
        move-state-off-icloud)
            move_state_off_icloud
            ;;
        mark-nosync)
            mark_openclaw_nosync
            ;;
        backup-state-now)
            backup_state_now
            ;;
        print-backup-crontab)
            print_backup_crontab
            ;;
        install-backup-crontab)
            install_backup_crontab
            ;;
        -h|--help|help)
            usage
            ;;
        *)
            error "Unknown command: $cmd"
            echo ""
            usage
            return 2
            ;;
    esac

    echo ""
    success "Done."
}

main "$@"
