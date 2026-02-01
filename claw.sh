#!/usr/bin/env bash
#
# OpenClaw setup script - handles migration from clawdbot to openclaw
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

main() {
    echo ""
    echo "╔═══════════════════════════════════════════╗"
    echo "║         OpenClaw Setup                    ║"
    echo "╚═══════════════════════════════════════════╝"
    echo ""

    setup_config_symlinks
    echo ""
    setup_node_modules_symlink

    echo ""
    success "OpenClaw setup complete!"
}

main "$@"
