#!/usr/bin/env bash
#
# Dotfiles installer - installs dependencies
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

run_privileged() {
    if [[ $EUID -eq 0 ]]; then
        "$@"
    elif command -v sudo &>/dev/null; then
        sudo "$@"
    else
        error "This step requires root privileges. Re-run as root or install sudo."
        return 1
    fi
}

dotfiles_dir() {
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "$HOME/Library/Mobile Documents/com~apple~CloudDocs/dotfiles"
    else
        echo "$HOME/.dotfiles"
    fi
}

has_en_us_utf8_locale() {
    LC_ALL=C locale -a 2>/dev/null | tr '[:upper:]' '[:lower:]' | grep -Eq '^en_us\.(utf-?8|utf8)$'
}

sanitize_linux_locale_env() {
    if [[ "$(uname)" != "Linux" ]]; then
        return
    fi

    if [[ "${LC_ALL:-}" == "en_US.UTF-8" ]] && ! has_en_us_utf8_locale; then
        warn "LC_ALL=en_US.UTF-8 is set but not generated yet; using LANG=C.UTF-8 temporarily"
        unset LC_ALL
        export LANG=C.UTF-8
    fi
}

# Check for Homebrew (macOS)
check_homebrew() {
    if [[ "$(uname)" == "Darwin" ]]; then
        if command -v brew &>/dev/null; then
            success "Homebrew found"
        else
            warn "Homebrew not found - some installs may fail"
            info "Install from: https://brew.sh"
        fi
    fi
}

# Install base dependencies on Linux
install_linux_dependencies() {
    if [[ "$(uname)" != "Linux" ]]; then
        return
    fi

    if command -v apt-get &>/dev/null; then
        info "Installing Linux packages via apt..."
        run_privileged apt-get update
        run_privileged apt-get install -y zsh git curl locales ca-certificates gh unzip sqlite3
    elif command -v dnf &>/dev/null; then
        info "Installing Linux packages via dnf..."
        run_privileged dnf install -y zsh git curl glibc-langpack-en ca-certificates
    elif command -v yum &>/dev/null; then
        info "Installing Linux packages via yum..."
        run_privileged yum install -y zsh git curl glibc-langpack-en ca-certificates
    elif command -v pacman &>/dev/null; then
        info "Installing Linux packages via pacman..."
        run_privileged pacman -Sy --noconfirm zsh git curl ca-certificates
    elif command -v zypper &>/dev/null; then
        info "Installing Linux packages via zypper..."
        run_privileged zypper --non-interactive install zsh git curl glibc-locale ca-certificates
    elif command -v apk &>/dev/null; then
        info "Installing Linux packages via apk..."
        run_privileged apk add --no-cache zsh git curl ca-certificates musl-locales
    else
        warn "No supported package manager found. Ensure zsh, git, and curl are installed."
    fi
}

install_apt_package_set_best_effort() {
    local label="$1"
    shift
    local requested=("$@")
    local available=()
    local missing=()
    local failed=()
    local pkg

    for pkg in "${requested[@]}"; do
        if dpkg -s "$pkg" &>/dev/null; then
            continue
        fi
        if apt-cache show "$pkg" &>/dev/null; then
            available+=("$pkg")
        else
            missing+=("$pkg")
        fi
    done

    if [[ ${#available[@]} -gt 0 ]]; then
        info "Installing $label packages via apt..."
        if ! run_privileged apt-get install -y "${available[@]}"; then
            warn "Bulk install for $label failed; retrying package-by-package"
            for pkg in "${available[@]}"; do
                run_privileged apt-get install -y "$pkg" || failed+=("$pkg")
            done
        fi
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        warn "Skipped unavailable apt packages ($label): ${missing[*]}"
    fi
    if [[ ${#failed[@]} -gt 0 ]]; then
        warn "Failed to install apt packages ($label): ${failed[*]}"
    fi
}

install_debian_brew_equivalent_tools() {
    if [[ "$(uname)" != "Linux" ]] || ! command -v apt-get &>/dev/null; then
        return
    fi

    # CLI equivalents for the most-used Homebrew stack from .natiliusrc.
    install_apt_package_set_best_effort "brew-equivalent CLI" \
        git-lfs tig lazygit diff-so-fancy wget jq jc fzf bat eza fd-find ripgrep ack tree htop btop \
        zoxide tealdeer zsh-completions zsh-syntax-highlighting direnv vim neovim tmux tree-sitter \
        gnupg pinentry-curses openssh-client keychain nmap shellcheck ffmpeg imagemagick poppler-utils \
        p7zip-full yt-dlp hexyl mosh httpie speedtest-cli inetutils-telnet lynx croc podman awscli \
        nodejs npm pyenv pipenv rbenv make cmake pre-commit yamllint icdiff bats sqlite3 mackup
}

ensure_linux_command_shims() {
    if [[ "$(uname)" != "Linux" ]]; then
        return
    fi

    mkdir -p "$HOME/.local/bin"

    if ! command -v fd &>/dev/null && command -v fdfind &>/dev/null; then
        ln -sfn "$(command -v fdfind)" "$HOME/.local/bin/fd"
        success "Created command shim: fd -> fdfind"
    fi

    if ! command -v bat &>/dev/null && command -v batcat &>/dev/null; then
        ln -sfn "$(command -v batcat)" "$HOME/.local/bin/bat"
        success "Created command shim: bat -> batcat"
    fi
}

install_optional_linux_tools() {
    if [[ "$(uname)" != "Linux" ]]; then
        return
    fi

    if command -v eza &>/dev/null; then
        success "eza already installed"
        return
    fi

    info "Installing optional Linux tools (eza)..."
    if command -v apt-get &>/dev/null; then
        if command -v apt-cache &>/dev/null && apt-cache show eza &>/dev/null; then
            run_privileged apt-get install -y eza && success "eza installed" || warn "Failed to install eza via apt"
        else
            warn "eza package not available via apt on this distro/repo"
        fi
    elif command -v dnf &>/dev/null; then
        run_privileged dnf install -y eza && success "eza installed" || warn "Failed to install eza via dnf"
    elif command -v yum &>/dev/null; then
        run_privileged yum install -y eza && success "eza installed" || warn "Failed to install eza via yum"
    elif command -v pacman &>/dev/null; then
        run_privileged pacman -Sy --noconfirm eza && success "eza installed" || warn "Failed to install eza via pacman"
    elif command -v zypper &>/dev/null; then
        run_privileged zypper --non-interactive install eza && success "eza installed" || warn "Failed to install eza via zypper"
    elif command -v apk &>/dev/null; then
        run_privileged apk add --no-cache eza && success "eza installed" || warn "Failed to install eza via apk"
    else
        warn "No supported package manager found for optional eza install"
    fi
}

ensure_linux_locale() {
    if [[ "$(uname)" != "Linux" ]]; then
        return
    fi

    if has_en_us_utf8_locale; then
        success "en_US.UTF-8 locale already available"
        export LANG=en_US.UTF-8
        export LC_ALL=en_US.UTF-8
        return
    fi

    info "Configuring en_US.UTF-8 locale..."
    if [[ -f /etc/locale.gen ]]; then
        run_privileged sed -i 's/^[#[:space:]]*en_US.UTF-8[[:space:]]\+UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
    fi

    if command -v locale-gen &>/dev/null; then
        run_privileged locale-gen en_US.UTF-8 || run_privileged locale-gen
    fi

    if command -v update-locale &>/dev/null; then
        run_privileged update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 || true
    fi

    if has_en_us_utf8_locale; then
        export LANG=en_US.UTF-8
        export LC_ALL=en_US.UTF-8
        success "Locale setup completed"
    else
        warn "Could not verify en_US.UTF-8; continuing with LANG=C.UTF-8"
        unset LC_ALL
        export LANG=C.UTF-8
    fi
}

link_dotfile() {
    local src="$1"
    local dest="$2"
    local backup

    if [[ ! -e "$src" ]]; then
        warn "Missing source file: $src"
        return
    fi

    if [[ -L "$dest" ]]; then
        if [[ "$(readlink "$dest")" == "$src" ]]; then
            success "$dest already symlinked"
        else
            ln -sfn "$src" "$dest"
            success "Updated symlink: $dest"
        fi
        return
    fi

    if [[ -e "$dest" ]]; then
        if cmp -s "$src" "$dest"; then
            rm -f "$dest"
            ln -s "$src" "$dest"
            success "Replaced identical file with symlink: $dest"
            return
        fi

        backup="${dest}.pre-dotfiles.$(date +%Y%m%d%H%M%S).bak"
        mv "$dest" "$backup"
        warn "Existing file backed up: $backup"
    fi

    ln -s "$src" "$dest"
    success "Symlinked $dest"
}

setup_shell_symlinks() {
    local df_dir
    df_dir="$(dotfiles_dir)"

    link_dotfile "$df_dir/.aliases" "$HOME/.aliases"
    link_dotfile "$df_dir/.functions" "$HOME/.functions"
    link_dotfile "$df_dir/.exports" "$HOME/.exports"
    link_dotfile "$df_dir/.profile" "$HOME/.profile"
    link_dotfile "$df_dir/.bashrc" "$HOME/.bashrc"
    link_dotfile "$df_dir/.bash_profile" "$HOME/.bash_profile"
    link_dotfile "$df_dir/.zshrc" "$HOME/.zshrc"
    link_dotfile "$df_dir/.zshenv" "$HOME/.zshenv"
    link_dotfile "$df_dir/.zprofile" "$HOME/.zprofile"
    link_dotfile "$df_dir/bin" "$HOME/bin"
    link_dotfile "$df_dir/functions" "$HOME/functions"
}

setup_codex_dotfiles() {
    local df_dir codex_home codex_agents_src codex_config_src codex_hooks_src
    df_dir="$(dotfiles_dir)"
    codex_home="${CODEX_HOME:-$HOME/.codex}"
    codex_agents_src="$df_dir/.codex/AGENTS.md"
    codex_config_src="$df_dir/.codex/config.toml"
    codex_hooks_src="$df_dir/.codex/hooks.json"

    mkdir -p "$codex_home"

    if [[ -f "$codex_agents_src" ]]; then
        link_dotfile "$codex_agents_src" "$codex_home/AGENTS.md"
    else
        warn "Codex AGENTS file missing in dotfiles: $codex_agents_src"
    fi

    if [[ -f "$codex_config_src" ]]; then
        link_dotfile "$codex_config_src" "$codex_home/config.toml"
    else
        warn "Codex config file missing in dotfiles: $codex_config_src"
    fi

    if [[ -f "$codex_hooks_src" ]]; then
        link_dotfile "$codex_hooks_src" "$codex_home/hooks.json"
    else
        warn "Codex hooks file missing in dotfiles: $codex_hooks_src"
    fi

    if [[ -x "$df_dir/bin/install-agent-worktree-ops" ]]; then
        info "Installing agent worktree ops runtime"
        bash "$df_dir/bin/install-agent-worktree-ops"
        success "Agent worktree ops installed"
    else
        warn "Agent worktree ops installer missing in dotfiles: $df_dir/bin/install-agent-worktree-ops"
    fi

    if ! command -v codex &>/dev/null; then
        warn "Codex CLI not found; skipping feature enablement"
        return
    fi

    info "Ensuring Codex features are enabled"
    codex features enable codex_hooks >/dev/null
    codex features enable multi_agent >/dev/null
    codex features enable child_agents_md >/dev/null
    success "Codex features enabled"
}

setup_claude_dotfiles() {
    local df_dir claude_dir claude_settings_src claude_settings_dest
    df_dir="$(dotfiles_dir)"
    claude_dir="$HOME/.claude"
    claude_settings_src="$df_dir/.claude/settings.json"
    claude_settings_dest="$claude_dir/settings.json"

    if [[ ! -f "$claude_settings_src" ]]; then
        warn "Claude settings file missing in dotfiles: $claude_settings_src"
        return
    fi

    mkdir -p "$claude_dir"
    link_dotfile "$claude_settings_src" "$claude_settings_dest"
    success "Claude settings linked to $claude_settings_src"
}

setup_iterm_preferences() {
    if [[ "$(uname)" != "Darwin" ]]; then
        return
    fi

    local df_dir mackup_prefs_dir iterm_custom_dir mackup_plist local_plist custom_plist
    local iterm_dynamic_dir local_dynamic_dir
    df_dir="$(dotfiles_dir)"
    mackup_prefs_dir="$df_dir/Library/Preferences"
    iterm_custom_dir="$df_dir/iterm"
    mackup_plist="$mackup_prefs_dir/com.googlecode.iterm2.plist"
    local_plist="$HOME/Library/Preferences/com.googlecode.iterm2.plist"
    custom_plist="$iterm_custom_dir/com.googlecode.iterm2.plist"
    iterm_dynamic_dir="$df_dir/.config/iterm2/AppSupport/DynamicProfiles"
    local_dynamic_dir="$HOME/Library/Application Support/iTerm2/DynamicProfiles"

    mkdir -p "$mackup_prefs_dir" "$iterm_custom_dir" "$iterm_dynamic_dir"

    if [[ ! -f "$mackup_plist" ]]; then
        if [[ -f "$custom_plist" ]]; then
            cp "$custom_plist" "$mackup_plist"
            success "Seeded iTerm2 prefs from $custom_plist"
        elif [[ -f "$local_plist" ]]; then
            cp "$local_plist" "$mackup_plist"
            success "Seeded iTerm2 prefs from $local_plist"
        else
            warn "iTerm2 prefs not found; skipping prefs bootstrap"
            return
        fi
    fi

    /usr/libexec/PlistBuddy -c "Delete :PrefsCustomFolder" "$mackup_plist" >/dev/null 2>&1 || true
    /usr/libexec/PlistBuddy -c "Delete :LoadPrefsFromCustomFolder" "$mackup_plist" >/dev/null 2>&1 || true
    /usr/libexec/PlistBuddy -c "Delete :SavePrefsToCustomFolder" "$mackup_plist" >/dev/null 2>&1 || true
    /usr/libexec/PlistBuddy -c "Add :PrefsCustomFolder string $mackup_prefs_dir" "$mackup_plist"
    /usr/libexec/PlistBuddy -c "Add :LoadPrefsFromCustomFolder bool true" "$mackup_plist"
    /usr/libexec/PlistBuddy -c "Add :SavePrefsToCustomFolder bool true" "$mackup_plist"

    link_dotfile "$mackup_plist" "$local_plist"
    link_dotfile "$mackup_plist" "$custom_plist"

    mkdir -p "$HOME/Library/Application Support/iTerm2"
    link_dotfile "$iterm_dynamic_dir" "$local_dynamic_dir"

    killall cfprefsd >/dev/null 2>&1 || true
    success "iTerm2 prefs linked to $mackup_prefs_dir"
}

setup_ghostty_config() {
    local df_dir ghostty_config_dir local_ghostty_dir ghostty_config_file macos_ghostty_dir macos_ghostty_config
    df_dir="$(dotfiles_dir)"
    ghostty_config_dir="$df_dir/.config/ghostty"
    ghostty_config_file="$ghostty_config_dir/config.ghostty"
    local_ghostty_dir="$HOME/.config/ghostty"
    macos_ghostty_dir="$HOME/Library/Application Support/com.mitchellh.ghostty"
    macos_ghostty_config="$macos_ghostty_dir/config.ghostty"

    if [[ ! -d "$ghostty_config_dir" ]]; then
        warn "Ghostty config directory missing in dotfiles: $ghostty_config_dir"
        return
    fi

    mkdir -p "$HOME/.config"
    link_dotfile "$ghostty_config_dir" "$local_ghostty_dir"

    if [[ "$(uname)" == "Darwin" ]]; then
        mkdir -p "$macos_ghostty_dir"
        link_dotfile "$ghostty_config_file" "$macos_ghostty_config"
    fi

    success "Ghostty config linked to $ghostty_config_dir"
}

# Install Oh My Zsh
install_oh_my_zsh() {
    if [[ -d "$HOME/.oh-my-zsh" ]]; then
        success "Oh My Zsh already installed"
    else
        info "Installing Oh My Zsh..."
        git clone https://github.com/ohmyzsh/ohmyzsh.git "$HOME/.oh-my-zsh"
        success "Oh My Zsh installed"
    fi
}

# Install Oh My Zsh plugins
install_zsh_plugins() {
    local zsh_custom="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}"

    # zsh-autosuggestions
    local autosuggestions_dir="$zsh_custom/plugins/zsh-autosuggestions"
    if [[ -d "$autosuggestions_dir" ]]; then
        success "zsh-autosuggestions already installed"
    else
        info "Installing zsh-autosuggestions..."
        git clone https://github.com/zsh-users/zsh-autosuggestions "$autosuggestions_dir"
        success "zsh-autosuggestions installed"
    fi
}

# Install fzf
install_fzf() {
    if command -v fzf &>/dev/null; then
        success "fzf already installed"
    elif [[ -d "$HOME/.fzf/.git" ]]; then
        success "fzf already installed in ~/.fzf"
    elif [[ -d "$HOME/.fzf" ]]; then
        warn "~/.fzf already exists but is not a git checkout - skipping install"
    elif command -v brew &>/dev/null; then
        info "Installing fzf via brew..."
        brew install fzf
        success "fzf installed"
    else
        info "Installing fzf from git..."
        git clone --depth 1 https://github.com/junegunn/fzf.git "$HOME/.fzf"
        success "fzf installed"
    fi
}

# Install Spaceship theme
install_spaceship_theme() {
    local zsh_custom="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}"
    local theme_dir="$zsh_custom/themes/spaceship-prompt"

    if [[ -d "$theme_dir" ]]; then
        success "Spaceship theme already installed"
    else
        info "Installing Spaceship theme..."
        git clone https://github.com/spaceship-prompt/spaceship-prompt "$theme_dir"
        ln -sf "$theme_dir/spaceship.zsh-theme" "$zsh_custom/themes/spaceship.zsh-theme"
        success "Spaceship theme installed"
    fi
}

# Install vim-plug for vim and neovim
install_vim_plug() {
    # vim-plug for vim
    local vim_plug="$HOME/.vim/autoload/plug.vim"
    if [[ -f "$vim_plug" ]]; then
        success "vim-plug (vim) already installed"
    else
        info "Installing vim-plug for vim..."
        curl -fLo "$vim_plug" --create-dirs \
            https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
        success "vim-plug (vim) installed"
    fi

    # vim-plug for neovim
    local nvim_plug="${XDG_DATA_HOME:-$HOME/.local/share}/nvim/site/autoload/plug.vim"
    if [[ -f "$nvim_plug" ]]; then
        success "vim-plug (neovim) already installed"
    else
        info "Installing vim-plug for neovim..."
        curl -fLo "$nvim_plug" --create-dirs \
            https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
        success "vim-plug (neovim) installed"
    fi
}

# Install neovim plugins
install_nvim_plugins() {
    if command -v nvim &>/dev/null; then
        info "Installing neovim plugins..."
        nvim --headless +PlugInstall +qall 2>/dev/null || true
        success "Neovim plugins installed"
    else
        warn "Neovim not found - skipping plugin install"
    fi
}

# Install Vim Tokyo Night theme
install_vim_theme() {
    local theme_dir="$HOME/.vim/pack/themes/start/tokyonight.nvim"
    if [[ -d "$theme_dir" ]]; then
        success "Tokyo Night vim theme already installed"
    else
        info "Installing Tokyo Night vim theme..."
        mkdir -p "$HOME/.vim/pack/themes/start"
        git clone https://github.com/folke/tokyonight.nvim "$theme_dir"
        success "Tokyo Night vim theme installed"
    fi
}

# Setup openclaw symlinks (legacy clawdbot compatibility)
setup_openclaw_symlinks() {
    if [[ "$(uname)" != "Darwin" ]]; then
        info "Skipping OpenClaw dotfile symlinks on non-macOS"
        return
    fi

    local df_dir
    df_dir="$(dotfiles_dir)"

    # Symlink ~/.openclaw to dotfiles
    if [[ -d "$df_dir/.openclaw" ]]; then
        if [[ -L "$HOME/.openclaw" ]]; then
            success "~/.openclaw symlink exists"
        elif [[ -d "$HOME/.openclaw" ]]; then
            warn "~/.openclaw is a directory - skipping (backup and remove manually if needed)"
        else
            ln -sf "$df_dir/.openclaw" "$HOME/.openclaw"
            success "~/.openclaw symlinked"
        fi

        # Legacy: symlink ~/.clawdbot to .openclaw
        if [[ -L "$HOME/.clawdbot" ]]; then
            success "~/.clawdbot legacy symlink exists"
        elif [[ -d "$HOME/.clawdbot" ]]; then
            warn "~/.clawdbot is a directory - skipping (backup and remove manually if needed)"
        else
            ln -sf "$df_dir/.openclaw" "$HOME/.clawdbot"
            success "~/.clawdbot -> .openclaw legacy symlink created"
        fi
    else
        warn ".openclaw not found in dotfiles - skipping symlinks"
    fi

    # Symlink ~/clawd to dotfiles
    if [[ -d "$df_dir/clawd" ]]; then
        if [[ -L "$HOME/clawd" ]]; then
            success "~/clawd symlink exists"
        elif [[ -d "$HOME/clawd" ]]; then
            warn "~/clawd is a directory - skipping (backup and remove manually if needed)"
        else
            ln -sf "$df_dir/clawd" "$HOME/clawd"
            success "~/clawd symlinked"
        fi
    fi
}

main() {
    echo ""
    echo "╔═══════════════════════════════════════════╗"
    echo "║         Dotfiles Installer                ║"
    echo "╚═══════════════════════════════════════════╝"
    echo ""

    # Check prerequisites
    check_homebrew
    echo ""

    # Install dependencies
    info "Installing dependencies..."
    echo ""

    sanitize_linux_locale_env
    install_linux_dependencies
    install_debian_brew_equivalent_tools
    install_optional_linux_tools
    ensure_linux_locale
    ensure_linux_command_shims
    install_oh_my_zsh
    install_zsh_plugins
    install_spaceship_theme
    install_fzf
    install_vim_plug
    install_vim_theme
    install_nvim_plugins
    setup_shell_symlinks
    setup_codex_dotfiles
    setup_claude_dotfiles
    setup_ghostty_config
    setup_iterm_preferences
    setup_openclaw_symlinks

    echo ""
    success "Installation complete!"
    echo ""
    info "Restart your shell or run: source ~/.zshrc"
    if command -v zsh &>/dev/null; then
        info "To make zsh your default shell: chsh -s $(command -v zsh)"
    fi
}

main "$@"
