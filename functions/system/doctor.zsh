doctor() {
    echo -e "\n🏥 Starting system diagnostics...\n"

    declare -a found_issues=()

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
                local count=$(find "$icloud_dir" -maxdepth 4 -type d -name "$pattern" 2>/dev/null | wc -l | tr -d ' ')
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
        echo "5. For disk issues: 'diskutil repairVolume /'"
        echo "6. For package manager issues: Try running 'up' to update all packages"
        echo -e "\n"
    fi
}
