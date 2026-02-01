#!/usr/bin/env bash
#
# Exclude common dev folders from iCloud sync
# Uses com.apple.fileprovider.ignore extended attribute
#

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
NC='\033[0m'

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# Folders to exclude from iCloud sync
EXCLUDE_PATTERNS=(
    # Python
    ".venv"
    "venv"
    "__pycache__"
    ".pytest_cache"
    "*.egg-info"
    ".eggs"
    ".mypy_cache"
    ".ruff_cache"

    # Node.js
    "node_modules"
    ".npm"
    ".pnpm-store"
    ".yarn"

    # Build/Dist
    "dist"
    "build"
    ".next"
    ".nuxt"
    ".output"
    ".turbo"

    # Cache
    ".cache"
    ".parcel-cache"

    # IDE/Editor
    ".idea"
    "*.swp"
    "*.swo"

    # Misc
    ".git"
    ".terraform"
    "vendor"
    "target"
    "coverage"
    ".coverage"
)

# Mark a path to be ignored by iCloud
ignore_path() {
    local path="$1"
    if [[ -e "$path" ]]; then
        xattr -w com.apple.fileprovider.ignore 1 "$path" 2>/dev/null && \
            success "Ignored: $path" || \
            warn "Failed to ignore: $path"
    fi
}

# Find and ignore matching folders in a directory
scan_and_ignore() {
    local search_dir="$1"

    if [[ ! -d "$search_dir" ]]; then
        warn "Directory not found: $search_dir"
        return
    fi

    info "Scanning: $search_dir"

    for pattern in "${EXCLUDE_PATTERNS[@]}"; do
        # Find matching directories (max depth 5 to avoid going too deep)
        while IFS= read -r -d '' found; do
            ignore_path "$found"
        done < <(find "$search_dir" -maxdepth 5 -type d -name "$pattern" -print0 2>/dev/null)
    done
}

# Check a single path
check_single() {
    local path="$1"
    local attr=$(xattr -p com.apple.fileprovider.ignore "$path" 2>/dev/null || echo "0")
    if [[ "$attr" == "1" ]]; then
        echo "Ignored: $path"
    else
        echo "Syncing: $path"
    fi
}

usage() {
    cat <<EOF
Usage: $(basename "$0") [command] [path]

Commands:
    scan [dir]     Scan directory and ignore common dev folders (default: current dir)
    ignore <path>  Mark a specific path to be ignored by iCloud
    check <path>   Check if a path is ignored
    help           Show this help

Examples:
    $(basename "$0") scan ~/GIT
    $(basename "$0") scan .
    $(basename "$0") ignore ./node_modules
    $(basename "$0") check ./venv
EOF
}

main() {
    if [[ "$(uname)" != "Darwin" ]]; then
        warn "This script only works on macOS"
        exit 1
    fi

    local cmd="${1:-scan}"
    local path="${2:-.}"

    case "$cmd" in
        scan)
            scan_and_ignore "$path"
            echo ""
            success "Done! Ignored folders will not sync to iCloud."
            ;;
        ignore)
            if [[ -z "$2" ]]; then
                warn "Please specify a path"
                exit 1
            fi
            ignore_path "$2"
            ;;
        check)
            if [[ -z "$2" ]]; then
                warn "Please specify a path"
                exit 1
            fi
            check_single "$2"
            ;;
        help|--help|-h)
            usage
            ;;
        *)
            warn "Unknown command: $cmd"
            usage
            exit 1
            ;;
    esac
}

main "$@"
