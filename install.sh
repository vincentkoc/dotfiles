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

# Install vim-plug
install_vim_plug() {
    local plug_file="$HOME/.vim/autoload/plug.vim"
    if [[ -f "$plug_file" ]]; then
        success "vim-plug already installed"
    else
        info "Installing vim-plug..."
        curl -fLo "$plug_file" --create-dirs \
            https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
        success "vim-plug installed"
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

    install_oh_my_zsh
    install_zsh_plugins
    install_spaceship_theme
    install_fzf
    install_vim_plug
    install_vim_theme

    echo ""
    success "Installation complete!"
    echo ""
    info "Restart your shell or run: source ~/.zshrc"
}

main "$@"
