up() {
    echo -e "\033[0;36mPlease provide local password (may auto-skip)...\033[0m"
    sudo -v

    while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &

    echo -e "\n🔄 Starting system update...\n"

    declare -a failed_updates=()

    if [ "$(uname)" = "Darwin" ]; then
        echo "📱 Checking for macOS updates..."
        if sudo softwareupdate -i -a; then
            echo "✅ macOS updates complete"
        else
            failed_updates+=("macOS")
        fi
    fi

    if command -v brew &> /dev/null; then
        echo "🍺 Updating Homebrew packages..."
        if brew update && \
           brew upgrade && \
           brew upgrade --cask && \
           brew cleanup && \
           brew doctor; then
            echo "✅ Homebrew updates complete"
        else
            failed_updates+=("Homebrew")
        fi
    fi

    if command -v npm &> /dev/null; then
        echo "📦 Updating NPM..."
        if npm install -g npm@latest && \
           npm update -g; then
            echo "✅ NPM updates complete"
        else
            failed_updates+=("NPM")
        fi
    fi

    if command -v pnpm &> /dev/null; then
        echo "📦 Updating PNPM..."
        if pnpm update -g; then
            echo "✅ PNPM updates complete"
        else
            failed_updates+=("PNPM")
        fi
    fi

    if command -v yarn &> /dev/null; then
        echo "🧶 Updating Yarn..."
        local yarn_update_success=true
        local yarn_version
        yarn_version=$(yarn --version 2>/dev/null)

        if [[ $yarn_version == 1.* ]]; then
            if command -v npm &> /dev/null; then
                if ! npm install -g yarn@latest --no-audit --no-fund; then
                    yarn_update_success=false
                fi
            else
                yarn_update_success=false
            fi
            if $yarn_update_success; then
                if ! yarn global upgrade; then
                    yarn_update_success=false
                fi
            fi
        else
            if command -v corepack &> /dev/null; then
                if ! COREPACK_ENABLE_DOWNLOAD_PROMPT=0 corepack prepare yarn@stable --activate; then
                    yarn_update_success=false
                fi
            else
                yarn_update_success=false
            fi
        fi

        if $yarn_update_success; then
            echo "✅ Yarn updates complete"
        else
            failed_updates+=("Yarn")
        fi
    fi

    if command -v pip &> /dev/null; then
        echo "🐍 Updating Python packages..."
        local pip_update_success=true
        if ! pip install --upgrade pip setuptools wheel; then
            pip_update_success=false
        fi
        if $pip_update_success; then
            local pip_python
            pip_python=$(command -v python3 || command -v python || true)
            if [[ -z "$pip_python" ]]; then
                pip_update_success=false
            else
                local outdated_json
                if outdated_json=$(pip list --outdated --format=json 2>/dev/null); then
                    if [[ "$outdated_json" != "[]" ]]; then
                        local pip_outdated_packages
                        if pip_outdated_packages=$("$pip_python" -c 'import json, sys
try:
    data = sys.stdin.read()
    if not data.strip():
        sys.exit(0)
    entries = json.loads(data)
except Exception:
    sys.exit(1)
names = [item.get("name") for item in entries if isinstance(item, dict) and item.get("name")]
sys.stdout.write("\n".join(names))
' <<< "$outdated_json"); then
                            if [[ -n "$pip_outdated_packages" ]]; then
                                while IFS= read -r pkg; do
                                    [[ -z "$pkg" ]] && continue
                                    if ! pip install -U "$pkg"; then
                                        pip_update_success=false
                                        break
                                    fi
                                done <<< "$pip_outdated_packages"
                            fi
                        else
                            pip_update_success=false
                        fi
                    fi
                else
                    pip_update_success=false
                fi
            fi
        fi
        if $pip_update_success; then
            echo "✅ Python packages updated"
        else
            failed_updates+=("Python pip")
        fi
    fi

    if command -v pip3 &> /dev/null; then
        echo "🐍 Updating Python3 packages..."
        local pip3_update_success=true
        if ! pip3 install --upgrade pip setuptools wheel; then
            pip3_update_success=false
        fi
        if $pip3_update_success; then
            local pip3_python
            pip3_python=$(command -v python3 || command -v python || true)
            if [[ -z "$pip3_python" ]]; then
                pip3_update_success=false
            else
                local outdated3_json
                if outdated3_json=$(pip3 list --outdated --format=json 2>/dev/null); then
                    if [[ "$outdated3_json" != "[]" ]]; then
                        local pip3_outdated_packages
                        if pip3_outdated_packages=$("$pip3_python" -c 'import json, sys
try:
    data = sys.stdin.read()
    if not data.strip():
        sys.exit(0)
    entries = json.loads(data)
except Exception:
    sys.exit(1)
names = [item.get("name") for item in entries if isinstance(item, dict) and item.get("name")]
sys.stdout.write("\n".join(names))
' <<< "$outdated3_json"); then
                            if [[ -n "$pip3_outdated_packages" ]]; then
                                while IFS= read -r pkg; do
                                    [[ -z "$pkg" ]] && continue
                                    if ! pip3 install -U "$pkg"; then
                                        pip3_update_success=false
                                        break
                                    fi
                                done <<< "$pip3_outdated_packages"
                            fi
                        else
                            pip3_update_success=false
                        fi
                    fi
                else
                    pip3_update_success=false
                fi
            fi
        fi
        if $pip3_update_success; then
            echo "✅ Python3 packages updated"
        else
            failed_updates+=("Python pip3")
        fi
    fi

    if command -v gem &> /dev/null; then
        echo "💎 Updating Ruby Gems..."
        if sudo gem update --system --no-document && \
           sudo gem update --no-document && \
           sudo gem cleanup; then
            echo "✅ Ruby Gems updated"
        else
            failed_updates+=("Ruby Gems")
        fi
    fi

    if command -v rustup &> /dev/null; then
        echo "🦀 Updating Rust..."
        if rustup update; then
            echo "✅ Rust updated"
        else
            failed_updates+=("Rust")
        fi
    fi

    if command -v cargo &> /dev/null; then
        echo "📦 Updating Cargo packages..."
        if cargo install-update -a; then
            echo "✅ Cargo packages updated"
        else
            failed_updates+=("Cargo")
        fi
    fi

    if command -v composer &> /dev/null; then
        echo "🎼 Updating Composer packages..."
        if composer self-update && \
           composer global update; then
            echo "✅ Composer packages updated"
        else
            failed_updates+=("Composer")
        fi
    fi

    if command -v go &> /dev/null; then
        echo "🐹 Updating Go packages..."
        if go get -u all; then
            echo "✅ Go packages updated"
        else
            failed_updates+=("Go")
        fi
    fi

    if command -v deno &> /dev/null; then
        echo "🦕 Updating Deno..."
        if deno upgrade; then
            echo "✅ Deno updated"
        else
            failed_updates+=("Deno")
        fi
    fi

    if command -v bun &> /dev/null; then
        echo "🥟 Updating Bun..."
        if bun upgrade; then
            echo "✅ Bun updated"
        else
            failed_updates+=("Bun")
        fi
    fi

    if command -v flutter &> /dev/null; then
        echo "📱 Updating Flutter..."
        if flutter upgrade && \
           flutter pub get; then
            echo "✅ Flutter updated"
        else
            failed_updates+=("Flutter")
        fi
    fi

    if command -v updatedb &> /dev/null; then
        echo "🔍 Updating locate database..."
        if sudo updatedb 2> /dev/null; then
            echo "✅ Locate database updated"
        else
            failed_updates+=("updatedb")
        fi
    fi

    if command -v tldr &> /dev/null; then
        echo "📚 Updating TLDR pages..."
        if tldr --update; then
            echo "✅ TLDR pages updated"
        else
            failed_updates+=("TLDR")
        fi
    fi

    echo -e "\n📋 Update Summary:"
    if [ ${#failed_updates[@]} -eq 0 ]; then
        echo -e "\n✅ All updates completed successfully!\n"
    else
        echo -e "\n⚠️  The following updates had issues:"
        printf '%s\n' "${failed_updates[@]}"
        echo -e "\nAll other updates completed successfully.\n"
    fi

    echo "🧹 Cleaning up system..."
    if [ "$(uname)" = "Darwin" ]; then
        sudo rm -rf /private/var/log/asl/*.asl
        sudo rm -rf ~/Library/Caches/*
        sudo rm -rf ~/Library/Logs/*
    fi

    if [ "$(uname)" = "Darwin" ]; then
        sudo dscacheutil -flushcache
        sudo killall -HUP mDNSResponder
    fi

    if [ "$(uname)" = "Darwin" ] && command -v mole &> /dev/null; then
        echo "🐾 Running Mole clean..."
        if ! mole clean; then
            failed_updates+=("Mole")
        fi
    fi

    echo -e "\n✨ System update and cleanup complete!\n"
}
