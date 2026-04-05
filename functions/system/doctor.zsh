_doctor_usage() {
    cat <<'EOF'
Usage: doctor [--repo <path>] [--clean-worktrees] [--vacuum-codex] [--fix]

Options:
  --repo <path>         Repo root for agent worktree diagnostics/cleanup.
  --clean-worktrees     Run agent-worktree-maintain --force for the target repo.
  --vacuum-codex        Vacuum Codex log sqlite files when no Codex process is active.
  --fix                 Run both --clean-worktrees and --vacuum-codex.
  --help                Show this help.
EOF
}

_doctor_kib_for_path() {
    local target="$1"
    [[ -e "$target" ]] || {
        printf '0\n'
        return 0
    }
    du -sk "$target" 2>/dev/null | awk '{print $1 + 0}'
}

_doctor_human_kib() {
    local kib="${1:-0}"
    awk "BEGIN {
        kib = $kib + 0
        if (kib >= 1024 * 1024) printf \"%.1f GiB\", kib / 1024 / 1024
        else if (kib >= 1024) printf \"%.1f MiB\", kib / 1024
        else printf \"%d KiB\", kib
    }"
}

_doctor_codex_home() {
    printf '%s\n' "${CODEX_HOME:-$HOME/.codex}"
}

_doctor_codex_logs_db() {
    local codex_home="$1"
    local latest

    latest=$(find "$codex_home" -maxdepth 1 -type f -name 'logs*.sqlite' -print 2>/dev/null | sort | tail -n 1)
    [[ -n "$latest" ]] || return 1
    printf '%s\n' "$latest"
}

_doctor_codex_process_active() {
    pgrep -fal '/Codex\.app|/codex($| )| codex($| )|app-server' >/dev/null 2>&1
}

_doctor_repo_root() {
    local explicit_repo="${1:-}"

    if [[ -n "$explicit_repo" ]]; then
        git -C "$explicit_repo" rev-parse --show-toplevel 2>/dev/null
        return $?
    fi

    git rev-parse --show-toplevel 2>/dev/null
}

_doctor_check_agent_worktrees() {
    local repo_root="$1"
    local apply_fix="$2"
    local found_issues_name_ref="$3"
    local ls_raw="" worktree_count="" by_source="" maintainer=""

    if ! command -v gwt >/dev/null 2>&1; then
        echo "🪵 Agent worktrees: gwt not loaded"
        return 0
    fi

    echo "🪵 Checking agent worktrees..."
    ls_raw=$(git -C "$repo_root" rev-parse --is-inside-work-tree >/dev/null 2>&1 && (DOTFILES_CD_SKIP_LISTING=1; builtin cd "$repo_root" && gwt ls --raw) 2>/dev/null || true)
    worktree_count=$(printf '%s\n' "$ls_raw" | awk 'NF {count++} END {print count + 0}')
    if [[ -n "$ls_raw" ]]; then
        by_source=$(printf '%s\n' "$ls_raw" | awk -F'\t' '
            NF >= 5 { count[$5]++ }
            END {
                first = 1
                for (source in count) {
                    if (!first) printf ", "
                    printf "%s=%d", source, count[source]
                    first = 0
                }
            }
        ')
    fi

    echo "   worktrees: $worktree_count"
    [[ -n "$by_source" ]] && echo "   by source: $by_source"

    if (( worktree_count >= 20 )); then
        eval "$found_issues_name_ref+=(\"Agent worktrees\")"
        echo "   ⚠️  Worktree count is high for one repo"
        echo "   💡 Run 'gwt audit' or 'gwt clean'"
    else
        echo "   ✅ Worktree count looks reasonable"
    fi

    if [[ "$apply_fix" != "1" ]]; then
        return 0
    fi

    if ! maintainer=$(command -v agent-worktree-maintain 2>/dev/null); then
        echo "   ⚠️  agent-worktree-maintain not found; skipping cleanup"
        eval "$found_issues_name_ref+=(\"Agent worktree maintainer missing\")"
        return 0
    fi

    echo "   🧹 Running agent-worktree-maintain --force..."
    if "$maintainer" --repo "$repo_root" --force >/dev/null; then
        echo "   ✅ Agent worktree cleanup completed"
    else
        echo "   ⚠️  Agent worktree cleanup failed"
        eval "$found_issues_name_ref+=(\"Agent worktree cleanup\")"
    fi
}

_doctor_check_codex_storage() {
    local apply_vacuum="$1"
    local found_issues_name_ref="$2"
    local codex_home logs_db page_size freelist_count dead_kib total_kib
    local sessions_kib archived_kib log_kib total_state_kib
    local dead_human total_human state_human

    codex_home=$(_doctor_codex_home)
    [[ -d "$codex_home" ]] || return 0

    echo "🤖 Checking Codex local state..."

    sessions_kib=$(_doctor_kib_for_path "$codex_home/sessions")
    archived_kib=$(_doctor_kib_for_path "$codex_home/archived_sessions")
    log_kib=$(_doctor_kib_for_path "$codex_home/log")
    total_state_kib=$((sessions_kib + archived_kib + log_kib))
    state_human=$(_doctor_human_kib "$total_state_kib")
    echo "   state dirs: $state_human"

    logs_db=$(_doctor_codex_logs_db "$codex_home" 2>/dev/null || true)
    if [[ -n "$logs_db" && -f "$logs_db" && -x "$(command -v sqlite3)" ]]; then
        page_size=$(sqlite3 "$logs_db" 'PRAGMA page_size;' 2>/dev/null | tr -d '[:space:]')
        freelist_count=$(sqlite3 "$logs_db" 'PRAGMA freelist_count;' 2>/dev/null | tr -d '[:space:]')
        total_kib=$(_doctor_kib_for_path "$logs_db")
        if [[ "$page_size" =~ ^[0-9]+$ && "$freelist_count" =~ ^[0-9]+$ ]]; then
            dead_kib=$((page_size * freelist_count / 1024))
        else
            dead_kib=0
        fi

        total_human=$(_doctor_human_kib "$total_kib")
        dead_human=$(_doctor_human_kib "$dead_kib")
        echo "   logs db: $logs_db"
        echo "   logs db size: $total_human"
        echo "   logs db dead space: $dead_human"

        if (( dead_kib >= 512 * 1024 )); then
            eval "$found_issues_name_ref+=(\"Codex sqlite freelist\")"
            echo "   ⚠️  Codex logs sqlite has significant dead space"
            echo "   💡 Run 'doctor --vacuum-codex' after closing Codex"
        else
            echo "   ✅ Codex sqlite dead space looks reasonable"
        fi
    fi

    if [[ "$apply_vacuum" != "1" ]]; then
        return 0
    fi

    if _doctor_codex_process_active; then
        echo "   ⚠️  Codex appears active; skipping sqlite vacuum"
        eval "$found_issues_name_ref+=(\"Codex vacuum skipped\")"
        return 0
    fi

    if [[ -z "$logs_db" || ! -f "$logs_db" ]]; then
        echo "   ℹ️  No Codex logs sqlite found; skipping vacuum"
        return 0
    fi

    echo "   🧹 Vacuuming Codex log sqlite..."
    if sqlite3 "$logs_db" 'VACUUM;' >/dev/null 2>&1; then
        echo "   ✅ Codex log vacuum completed"
    else
        echo "   ⚠️  Codex log vacuum failed"
        eval "$found_issues_name_ref+=(\"Codex vacuum\")"
    fi
}

doctor() {
    local repo_arg=""
    local run_clean_worktrees=0
    local run_vacuum_codex=0

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --repo)
                repo_arg="$2"
                shift 2
                ;;
            --clean-worktrees)
                run_clean_worktrees=1
                shift
                ;;
            --vacuum-codex)
                run_vacuum_codex=1
                shift
                ;;
            --fix)
                run_clean_worktrees=1
                run_vacuum_codex=1
                shift
                ;;
            --help|-h)
                _doctor_usage
                return 0
                ;;
            *)
                echo "doctor: unknown argument: $1" >&2
                _doctor_usage >&2
                return 1
                ;;
        esac
    done

    echo -e "\n🏥 Starting system diagnostics...\n"

    declare -a found_issues=()
    local repo_root=""

    if command -v brew &> /dev/null; then
        echo "🍺 Running Homebrew diagnostics..."
        if ! brew doctor; then
            found_issues+=("Homebrew")
        fi
    fi

    if command -v rbenv &> /dev/null; then
        echo "💎 Checking Ruby environment..."
        if ! curl -fsSL https://github.com/rbenv/rbenv-installer/raw/main/bin/rbenv-doctor | bash; then
            found_issues+=("Ruby/rbenv")
        fi
    fi

    if command -v npm &> /dev/null; then
        echo "📦 Checking NPM..."
        if ! npm doctor; then
            found_issues+=("NPM")
        fi
    fi

    if command -v yarn &> /dev/null; then
        echo "🧶 Checking Yarn..."
        if yarn --version > /dev/null 2>&1; then
            echo "   ✅ Yarn $(yarn --version 2>/dev/null)"
        else
            found_issues+=("Yarn")
        fi
    fi

    if command -v pnpm &> /dev/null; then
        echo "📦 Checking PNPM..."
        local pnpm_ok=true
        if ! env COREPACK_ENABLE_DOWNLOAD_PROMPT=0 pnpm --version > /dev/null 2>&1; then
            if command -v corepack &> /dev/null; then
                if env COREPACK_ENABLE_DOWNLOAD_PROMPT=0 corepack prepare pnpm@latest --activate > /dev/null 2>&1; then
                    env COREPACK_ENABLE_DOWNLOAD_PROMPT=0 pnpm --version > /dev/null 2>&1 || pnpm_ok=false
                else
                    pnpm_ok=false
                fi
            else
                pnpm_ok=false
            fi
        fi
        if $pnpm_ok && command -v pnpm &> /dev/null; then
            if ! env COREPACK_ENABLE_DOWNLOAD_PROMPT=0 pnpm doctor > /dev/null 2>&1; then
                pnpm_ok=false
            fi
        fi
        if ! $pnpm_ok; then
            found_issues+=("PNPM")
        fi
    fi

    if command -v pip &> /dev/null; then
        echo "🐍 Checking pip..."
        if ! pip check; then
            found_issues+=("Python pip dependencies")
        fi
    fi

    if command -v flutter &> /dev/null; then
        echo "📱 Checking Flutter..."
        if ! flutter doctor; then
            found_issues+=("Flutter")
        fi
    fi

    if command -v git &> /dev/null; then
        echo "🌿 Checking Git configuration..."
        if git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
            if ! git fsck > /dev/null 2>&1; then
                found_issues+=("Git repository integrity")
            fi
        else
            echo "Skipping git fsck (not inside a repository)."
        fi
    fi

    if command -v docker &> /dev/null; then
        echo "🐳 Checking Docker..."
        if ! docker info > /dev/null 2>&1; then
            found_issues+=("Docker")
        fi
    fi

    if command -v composer &> /dev/null; then
        echo "🎼 Checking Composer..."
        if ! composer diagnose; then
            found_issues+=("Composer")
        fi
    fi

    if [[ "$(uname)" == "Darwin" ]]; then
        echo "🍎 Checking macOS system integrity..."
        if ! system_profiler SPSoftwareDataType > /dev/null; then
            found_issues+=("macOS System")
        fi

        if command -v verify_system > /dev/null 2>&1; then
            echo "🔍 Verifying system integrity..."
            if sudo -n true 2>/dev/null; then
                if ! sudo verify_system > /dev/null 2>&1; then
                    found_issues+=("macOS System Integrity")
                fi
            else
                echo "Skipping verify_system (sudo password required)."
            fi
        fi
    fi

    if [[ "$(uname)" == "Darwin" ]] && command -v diskutil &> /dev/null; then
        echo "💽 Checking disk health..."
        if ! diskutil verifyVolume / > /dev/null 2>&1; then
            found_issues+=("Disk health")
        fi
    fi

    echo "🌐 Checking network connectivity..."
    if ! ping -c 1 -W 2 8.8.8.8 > /dev/null 2>&1; then
        found_issues+=("Network connectivity")
    fi

    if ! timeout 3 nslookup github.com > /dev/null 2>&1 && ! host -W 2 github.com > /dev/null 2>&1; then
        found_issues+=("DNS resolution")
    fi

    if [[ "$(uname)" == "Darwin" ]] && command -v mole &> /dev/null; then
        echo "🐾 Running Mole clean..."
        if ! mole clean; then
            found_issues+=("Mole clean")
        fi
    fi

    if [[ "$(uname)" == "Darwin" ]]; then
        local icloud_dir="$HOME/Library/Mobile Documents/com~apple~CloudDocs"
        if [[ -d "$icloud_dir" ]]; then
            echo "☁️  Checking iCloud sync health..."
            local bad_folders=0
            local patterns=(".venv" "venv" "__pycache__" "node_modules" ".next" "dist" "dist-runtime" "build" ".cache" ".worktrees" "worktrees")
            for pattern in "${patterns[@]}"; do
                local count
                count=$(find "$icloud_dir" -maxdepth 4 -type d -name "$pattern" 2>/dev/null | wc -l | tr -d ' ')
                if [[ "$count" -gt 0 ]]; then
                    echo "   ⚠️  Found $count '$pattern' folder(s) syncing to iCloud"
                    bad_folders=$((bad_folders + count))
                fi
            done
            if [[ "$bad_folders" -gt 0 ]]; then
                found_issues+=("iCloud syncing dev folders")
                echo "   💡 Run 'icloud-ignore' to exclude dev folders from sync"
            else
                echo "   ✅ No dev folders detected in iCloud (shallow scan)"
            fi
        fi
    fi

    repo_root=$(_doctor_repo_root "$repo_arg" 2>/dev/null || true)
    if [[ -n "$repo_root" ]]; then
        _doctor_check_agent_worktrees "$repo_root" "$run_clean_worktrees" found_issues
    elif (( run_clean_worktrees == 1 )); then
        echo "🪵 Agent worktrees: no git repo context; skipping cleanup"
        found_issues+=("Agent worktree cleanup skipped")
    fi

    _doctor_check_codex_storage "$run_vacuum_codex" found_issues

    echo -e "\n📋 Diagnostic Summary:"
    if [ ${#found_issues[@]} -eq 0 ]; then
        echo -e "\n✅ All systems are running normally!\n"
    else
        echo -e "\n⚠️  Issues were found in the following systems:"
        printf '%s\n' "${found_issues[@]}"
        echo -e "\nPlease review the output above for detailed information about each issue.\n"
    fi

    if [ ${#found_issues[@]} -gt 0 ]; then
        echo "🔧 Suggested fixes:"
        echo "1. For Homebrew issues: 'brew doctor'"
        echo "2. For Ruby issues: 'rbenv doctor'"
        echo "3. For Node issues: 'npm doctor'"
        echo "4. For Flutter issues: 'flutter doctor'"
        echo "5. For worktree pressure: 'gwt audit' or 'doctor --clean-worktrees'"
        echo "6. For Codex sqlite bloat: close Codex, then run 'doctor --vacuum-codex'"
        echo "7. For package manager issues: try running 'up'"
        echo -e "\n"
    fi
}
