#
# Oh-my-zsh
#

# Profiling support (run `zprof` after startup when investigating)
if [[ -n "$DOTFILES_ZSH_PROFILE" ]]; then
  zmodload zsh/zprof
fi

# Bootstrap environment before loading oh-my-zsh so PATH + theme variables exist early
if [[ -r ~/.exports ]]; then
	source ~/.exports
fi

# Tokyo Night palette shared across terminals, prompt, and tooling
typeset -gx TOKYONIGHT_BG="#1a1b26"
typeset -gx TOKYONIGHT_BG_DARK="#16161e"
typeset -gx TOKYONIGHT_BG_DIM="#1f2335"
typeset -gx TOKYONIGHT_BG_HIGHLIGHT="#292e42"
typeset -gx TOKYONIGHT_FG="#c0caf5"
typeset -gx TOKYONIGHT_FG_DIM="#a9b1d6"
typeset -gx TOKYONIGHT_BLUE="#7aa2f7"
typeset -gx TOKYONIGHT_CYAN="#7dcfff"
typeset -gx TOKYONIGHT_MAGENTA="#bb9af7"
typeset -gx TOKYONIGHT_PURPLE="#9d7cd8"
typeset -gx TOKYONIGHT_GREEN="#9ece6a"
typeset -gx TOKYONIGHT_ORANGE="#ff9e64"
typeset -gx TOKYONIGHT_RED="#f7768e"
typeset -gx TOKYONIGHT_YELLOW="#e0af68"

autoload -Uz colors && colors
setopt prompt_subst

export ZSH="$HOME/.oh-my-zsh"
ZSH_THEME="spaceship"
ENABLE_CORRECTION="true"

# Spaceship prompt tuned for Tokyo Night palette
SPACESHIP_PROMPT_ORDER=(dir git package python node docker exit_code char)
SPACESHIP_DIR_TRUNC=3
SPACESHIP_DIR_COLOR="cyan"
SPACESHIP_PACKAGE_SHOW=true
SPACESHIP_PACKAGE_PREFIX="pkg "
SPACESHIP_PYTHON_SHOW=true
SPACESHIP_PYTHON_SYMBOL="py"
SPACESHIP_NODE_SHOW_VERSION=true
SPACESHIP_NODE_SYMBOL="node"
SPACESHIP_KUBECTL_SHOW=false
SPACESHIP_GIT_BRANCH_COLOR="magenta"
SPACESHIP_GIT_STATUS_COLOR="yellow"
SPACESHIP_EXEC_TIME_SHOW=false
SPACESHIP_CHAR_PREFIX=""
SPACESHIP_CHAR_SYMBOL=">"
SPACESHIP_CHAR_COLOR_SUCCESS="green"
SPACESHIP_CHAR_COLOR_FAILURE="red"
SPACESHIP_CHAR_SUFFIX=" "

SPACESHIP_PROMPT_SEPARATE_LINE=false
SPACESHIP_PROMPT_ADD_NEWLINE=true
SPACESHIP_CLIPBOARD_SHOW=false

# Right prompt for host only (hide in tmux since tmux shows it)
if [[ -n "$TMUX" ]]; then
    SPACESHIP_RPROMPT_ORDER=()
    SPACESHIP_HOST_SHOW=false
else
    SPACESHIP_RPROMPT_ORDER=(host)
    SPACESHIP_HOST_SHOW="always"
fi
SPACESHIP_TIME_SHOW=false
SPACESHIP_HOST_PREFIX=""
SPACESHIP_HOST_SUFFIX=""
SPACESHIP_HOST_COLOR="242"

# Add more useful plugins
plugins=(
	git
	z
	kubectl
	dirhistory
	zsh-autosuggestions
	docker
	npm
	pip
	rust
	golang
	vscode
	colored-man-pages
	command-not-found
)
# macOS-only plugins
[[ "$OSTYPE" == "darwin"* ]] && plugins+=(brew macos)

# Performance improvements
DISABLE_AUTO_UPDATE="true"
COMPLETION_WAITING_DOTS="true"

ZSH_AUTOSUGGEST_USE_ASYNC=1
ZSH_AUTOSUGGEST_BUFFER_MAX_SIZE=20
ZSH_AUTOSUGGEST_STRATEGY=(history)
ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE="fg=#565f89,bold"
HISTORY_SUBSTRING_SEARCH_HIGHLIGHT_FOUND="bg=#9ece6a,fg=#1a1b26"
HISTORY_SUBSTRING_SEARCH_HIGHLIGHT_NOT_FOUND="bg=#f7768e,fg=#1a1b26"

fpath=($ZSH/custom/completions $fpath)
source $ZSH/oh-my-zsh.sh

#
# Load dotfiles pre-ENV
#
for file in ~/.{aliases,functions}; do
	[ -r "$file" ] && [ -f "$file" ] && source "$file";
done;
unset file;

# If not running interactively, don't do anything
[ -z "$PS1" ] && return

#
# Mac Specific
#
if [[ $OSTYPE == 'darwin'* ]]; then
	# Architecture and Security
	# Uncomment if needed for x86 compatibility
	# export ARCHFLAGS="-arch x86_64"
	export GPG_TTY=$(tty)

	# Version Managers - Lazy Loading
	# Python - pyenv
	if command -v pyenv >/dev/null 2>&1; then
		export PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"
		path=("$PYENV_ROOT/shims" $path)
		pyenv() {
			unset -f pyenv
			eval "$(command pyenv init - --no-rehash)"
			pyenv "$@"
		}
	fi

	# Fallback: alias python to python3 if python not available
	if ! command -v python >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
		alias python='python3'
		alias pip='pip3'
	fi

	# Node.js - nodenv
	if command -v nodenv >/dev/null 2>&1; then
		export NODENV_ROOT="${NODENV_ROOT:-$HOME/.nodenv}"
		path=("$NODENV_ROOT/shims" $path)
		# Add active node version bin to PATH (for npm global packages)
		if [[ -f "$NODENV_ROOT/version" ]]; then
			_nodenv_ver=$(< "$NODENV_ROOT/version")
			[[ -d "$NODENV_ROOT/versions/$_nodenv_ver/bin" ]] && path=("$NODENV_ROOT/versions/$_nodenv_ver/bin" $path)
			unset _nodenv_ver
		fi
		nodenv() {
			unset -f nodenv
			eval "$(command nodenv init -)"
			nodenv "$@"
		}
	fi

	# Ruby - rbenv
	if command -v rbenv >/dev/null 2>&1; then
		rbenv() {
			unset -f rbenv
			eval "$(command rbenv init -)"
			rbenv "$@"
		}
	fi

	# Java - jenv
	if command -v jenv >/dev/null 2>&1; then
		export JENV_ROOT="${JENV_ROOT:-$HOME/.jenv}"
		path=("$JENV_ROOT/bin" $path)
		jenv() {
			unset -f jenv
			eval "$(command jenv init -)"
			jenv "$@"
		}
	fi

	# ZSH Autocomplete
	if type brew &>/dev/null; then
		FPATH="$(brew --prefix)/share/zsh-completions:$FPATH"
		FPATH="$(brew --prefix)/share/zsh/site-functions:$FPATH"

		# Only regenerate completions once per day
		autoload -Uz compinit
		zcompdump="${ZDOTDIR:-$HOME}/.zcompdump"
		if [[ -f "$zcompdump" ]]; then
			dump_mtime=""
			if dump_mtime=$(stat -f '%m' "$zcompdump" 2>/dev/null); then
				:
			elif dump_mtime=$(stat -c '%Y' "$zcompdump" 2>/dev/null); then
				:
			fi
			if [[ -n "$dump_mtime" && $(( $(date +%s) - dump_mtime )) -lt 86400 ]]; then
				compinit -C
			else
				compinit
			fi
		else
			compinit
		fi
	fi

	# Better history search
	bindkey '^[[A' history-beginning-search-backward
	bindkey '^[[B' history-beginning-search-forward
	bindkey '^[[1;5A' history-beginning-search-backward
	bindkey '^[[1;5B' history-beginning-search-forward
	# Cursor movement shortcuts (Ctrl+Arrow)
	bindkey '^[[1;5C' forward-word
	bindkey '^[[1;5D' backward-word
	bindkey '^[[1;5H' beginning-of-line
	bindkey '^[[1;5F' end-of-line
	bindkey '^[^?' backward-kill-word      # Option+Backspace
	bindkey '^[3~' kill-word               # Option+Delete
	bindkey '^[^H' backward-kill-word      # Alternate Option+Backspace code
	# Delete entire command (Ctrl+U or mapped Cmd+Backspace)
	bindkey '^U' backward-kill-line
	bindkey '^[[3;9~' backward-kill-line
fi

# #
# # System Color Prompt
# #
# if [ -f "$HOME/bin/system-colour.py" ]; then
# 	eval "$($HOME/bin/system-colour.py)"
# 	# Custom prompt with system color
# 	PROMPT='%F{36}%K{$SYSTEM_COLOUR_BG}%F{$SYSTEM_COLOUR_FG}%n@%M%k%f %F{blue}%~ %(?.%F{green}.%F{red})%#%f '
# fi

#
# Load dotfiles post-ENV
#
for file in ~/.{extra,extra/user.sh}; do
	[ -r "$file" ] && [ -f "$file" ] && source "$file";
done;
unset file;

#
# Path additions
#
if [[ -d /opt/homebrew/opt/postgresql@13/bin ]]; then
	path=(
		"/opt/homebrew/opt/postgresql@13/bin"
		$path
	)
fi

if [[ -d /opt/homebrew/opt/llvm/bin ]]; then
	path=(
		"/opt/homebrew/opt/llvm/bin"
		$path
	)
fi
typeset -U path # Remove duplicates from PATH

# FZF Configuration
if [ -f ~/.fzf.zsh ]; then
	source ~/.fzf.zsh
fi

# OpenClaw completion
if command -v openclaw >/dev/null 2>&1; then
	source <(openclaw completion --shell zsh)
fi

if command -v fd >/dev/null 2>&1; then
	export FZF_DEFAULT_COMMAND='fd --type f --hidden --follow --exclude .git'
	export FZF_CTRL_T_COMMAND="$FZF_DEFAULT_COMMAND"
elif command -v rg >/dev/null 2>&1; then
	export FZF_DEFAULT_COMMAND='rg --files --hidden --follow --glob "!.git"'
	export FZF_CTRL_T_COMMAND="$FZF_DEFAULT_COMMAND"
fi

# Better command history
setopt EXTENDED_HISTORY          # Write the history file in the ':start:elapsed;command' format.
setopt INC_APPEND_HISTORY       # Write to the history file immediately, not when the shell exits.
setopt SHARE_HISTORY           # Share history between all sessions.
setopt HIST_EXPIRE_DUPS_FIRST  # Expire a duplicate event first when trimming history.
setopt HIST_IGNORE_DUPS        # Do not record an event that was just recorded again.
setopt HIST_IGNORE_ALL_DUPS    # Delete an old recorded event if a new event is a duplicate.
setopt HIST_FIND_NO_DUPS       # Do not display a previously found event.
setopt HIST_SAVE_NO_DUPS       # Do not write a duplicate event to the history file.
setopt HIST_VERIFY            # Do not execute immediately upon history expansion.

# Directory navigation
setopt AUTO_PUSHD              # Push the current directory visited on the stack.
setopt PUSHD_IGNORE_DUPS       # Do not store duplicates in the stack.
setopt PUSHD_SILENT           # Do not print the directory stack after pushd or popd.

# Completion improvements
zstyle ':completion:*' menu select
zstyle ':completion:*' matcher-list 'm:{a-zA-Z}={A-Za-z}' # Case insensitive completion
zstyle ':completion:*' special-dirs true
if [[ -n "${LS_COLORS:-}" ]]; then
	zstyle ':completion:*' list-colors "${(s.:.)LS_COLORS}"
fi
zstyle ':completion:*:*:kill:*:processes' list-colors '=(#b) #([0-9]#) ([0-9a-z-]#)*=01;34=0=01'

# Load syntax highlighting (should be last)
typeset -gA ZSH_HIGHLIGHT_STYLES
ZSH_HIGHLIGHT_STYLES[comment]='fg=#565f89'
ZSH_HIGHLIGHT_STYLES[alias]='fg=#9ece6a'
ZSH_HIGHLIGHT_STYLES[command]='fg=#7aa2f7'
ZSH_HIGHLIGHT_STYLES[builtin]='fg=#7dcfff'
ZSH_HIGHLIGHT_STYLES[function]='fg=#bb9af7'
ZSH_HIGHLIGHT_STYLES[path]='fg=#ff9e64'
ZSH_HIGHLIGHT_STYLES[single-quoted-argument]='fg=#c0caf5'
ZSH_HIGHLIGHT_STYLES[double-quoted-argument]='fg=#c0caf5'
if [[ -z "${ZSH_HIGHLIGHTING_SOURCE:-}" ]]; then
    # Linux paths (apt/dnf/pacman)
    if [[ -f /usr/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh ]]; then
        ZSH_HIGHLIGHTING_SOURCE=/usr/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
    elif [[ -f /usr/share/zsh/plugins/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh ]]; then
        ZSH_HIGHLIGHTING_SOURCE=/usr/share/zsh/plugins/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
    # macOS Homebrew paths
    elif [[ -f /opt/homebrew/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh ]]; then
        ZSH_HIGHLIGHTING_SOURCE=/opt/homebrew/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
    elif [[ -f /usr/local/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh ]]; then
        ZSH_HIGHLIGHTING_SOURCE=/usr/local/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
    elif command -v brew >/dev/null 2>&1; then
        _brew_prefix=$(brew --prefix 2>/dev/null || true)
        if [[ -n "$_brew_prefix" && -f "$_brew_prefix/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh" ]]; then
            ZSH_HIGHLIGHTING_SOURCE="$_brew_prefix/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh"
        fi
        unset _brew_prefix
    fi
fi
[[ -n "$ZSH_HIGHLIGHTING_SOURCE" ]] && source "$ZSH_HIGHLIGHTING_SOURCE" 2>/dev/null || true
unset ZSH_HIGHLIGHTING_SOURCE

if [[ $OSTYPE == 'darwin'* ]] && [[ -d /Applications/screenpipe.app/Contents/MacOS ]]; then
	export PATH="$PATH:/Applications/screenpipe.app/Contents/MacOS"
fi

# Completion helpers
if (( $+commands[make] )) && [[ -z ${functions[_make]+x} ]]; then
	autoload -Uz _make
	compdef _make make gmake
fi

# direnv hook (auto-load .envrc)
if command -v direnv >/dev/null 2>&1; then
    eval "$(direnv hook zsh)"
fi
