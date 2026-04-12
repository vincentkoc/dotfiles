#
# Oh-my-zsh
#

# Profiling support (run `zprof` after startup when investigating)
if [[ -n "$DOTFILES_ZSH_PROFILE" ]]; then
  zmodload zsh/zprof
fi

# Disable terminal bell in zsh (prevents iTerm bell icon/sound).
setopt NO_BEEP

# Faster deletes (ctrl-backspace, alt-backspace) if terminals send ^H or meta-backspace.
bindkey '^H' backward-kill-word
bindkey '^[^?' backward-kill-word
bindkey '^?' backward-delete-char

# Bootstrap environment before loading oh-my-zsh so PATH + theme variables exist early
if [[ -r ~/.exports ]]; then
	source ~/.exports
fi

# Load dotfiles .env early (auto-export all vars). Use KEY=VALUE (no "export" needed).
if [[ "$(uname)" == "Darwin" ]]; then
	DOTFILES_ENV="$HOME/Library/Mobile Documents/com~apple~CloudDocs/dotfiles/.env"
else
	DOTFILES_ENV="$HOME/.dotfiles/.env"
fi
if [[ -r "$DOTFILES_ENV" ]]; then
	set -a
	source "$DOTFILES_ENV"
	set +a
fi
unset DOTFILES_ENV

# Disable app telemetry and OTEL exporters for local CLIs and inherited MCP processes.
export CLAUDE_CODE_ENABLE_TELEMETRY=0
export OTEL_SDK_DISABLED=true
export OTEL_TRACES_EXPORTER=none
export OTEL_METRICS_EXPORTER=none
export OTEL_LOGS_EXPORTER=none

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
SPACESHIP_RUBY_SHOW=false
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
# Keep package section fast by skipping heavyweight managers in prompt rendering.
SPACESHIP_PACKAGE_ORDER=(npm lerna cargo composer python dart)

# Avoid remote Docker prompt probes on flaky links unless explicitly requested.
if [[ -n "${DOCKER_HOST:-}" && "$DOCKER_HOST" == tcp://* ]] && [[ -z "${DOTFILES_FORCE_REMOTE_DOCKER_PROMPT:-}" ]]; then
    SPACESHIP_DOCKER_SHOW=false
    SPACESHIP_DOCKER_CONTEXT_SHOW=false
    SPACESHIP_DOCKER_COMPOSE_SHOW=false
fi

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
DISABLE_AUTO_TITLE="true"
ZSH_DISABLE_COMPFIX="true"
COMPLETION_WAITING_DOTS="true"

ZSH_AUTOSUGGEST_USE_ASYNC=1
ZSH_AUTOSUGGEST_BUFFER_MAX_SIZE=20
ZSH_AUTOSUGGEST_STRATEGY=(history)
ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE="fg=#565f89,bold"
HISTORY_SUBSTRING_SEARCH_HIGHLIGHT_FOUND="bg=#9ece6a,fg=#1a1b26"
HISTORY_SUBSTRING_SEARCH_HIGHLIGHT_NOT_FOUND="bg=#f7768e,fg=#1a1b26"

fpath=($ZSH/custom/completions $fpath)
# Ensure OMZ cache/completion paths are stable and writable.
ZSH_CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/oh-my-zsh"
[[ -d "$ZSH_CACHE_DIR/completions" ]] || mkdir -p "$ZSH_CACHE_DIR/completions" 2>/dev/null

# Avoid invoking docker CLI for completion generation on every startup.
zstyle ':omz:plugins:docker' legacy-completion yes

# Add Homebrew completion paths before OMZ triggers compinit.
if [[ -n "${HOMEBREW_PREFIX:-}" ]]; then
	[[ -d "$HOMEBREW_PREFIX/share/zsh-completions" ]] && fpath=("$HOMEBREW_PREFIX/share/zsh-completions" $fpath)
	[[ -d "$HOMEBREW_PREFIX/share/zsh/site-functions" ]] && fpath=("$HOMEBREW_PREFIX/share/zsh/site-functions" $fpath)
elif [[ -d /opt/homebrew/share/zsh/site-functions ]] || [[ -d /opt/homebrew/share/zsh-completions ]]; then
	[[ -d /opt/homebrew/share/zsh-completions ]] && fpath=(/opt/homebrew/share/zsh-completions $fpath)
	[[ -d /opt/homebrew/share/zsh/site-functions ]] && fpath=(/opt/homebrew/share/zsh/site-functions $fpath)
elif [[ -d /usr/local/share/zsh/site-functions ]] || [[ -d /usr/local/share/zsh-completions ]]; then
	[[ -d /usr/local/share/zsh-completions ]] && fpath=(/usr/local/share/zsh-completions $fpath)
	[[ -d /usr/local/share/zsh/site-functions ]] && fpath=(/usr/local/share/zsh/site-functions $fpath)
fi
source $ZSH/oh-my-zsh.sh

#
# Load dotfiles pre-ENV
#
for file in ~/.{aliases,functions}; do
	[ -r "$file" ] && [ -f "$file" ] && source "$file";
done;
unset file;

# Force pip to follow the active python (venv-safe)
unalias pip pip3 2>/dev/null
pip() {
	local py
	if command -v python >/dev/null 2>&1; then
		py=python
	elif command -v python3 >/dev/null 2>&1; then
		py=python3
	else
		echo "pip: python not found" >&2
		return 127
	fi
	"$py" -m pip "$@"
}
alias pip3='pip'

# If not running interactively, don't do anything
[ -z "$PS1" ] && return

# Terminal title: repo/branch context, including linked worktree name.
if [[ "${TERM_PROGRAM:-}" == "iTerm.app" || "${TERM_PROGRAM:-}" == "ghostty" ]]; then
	autoload -Uz add-zsh-hook

	_dotfiles_terminal_title() {
		local title cwd repo branch git_dir wt_name
		cwd="${PWD/#$HOME/~}"
		title="$cwd"

		if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
			repo=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)")
			branch=$(git symbolic-ref --short HEAD 2>/dev/null || git rev-parse --short HEAD 2>/dev/null)
			git_dir=$(git rev-parse --absolute-git-dir 2>/dev/null)
			if [[ "$git_dir" == *"/worktrees/"* ]]; then
				wt_name=$(basename "$git_dir")
				title="$repo:$branch [$wt_name] - $cwd"
			else
				title="$repo:$branch - $cwd"
			fi
		fi

		# OSC 0 updates the visible terminal title in iTerm and Ghostty.
		print -Pn "\e]0;${title}\a"
	}

	add-zsh-hook chpwd _dotfiles_terminal_title
	add-zsh-hook precmd _dotfiles_terminal_title
fi

# Keep tmux pane titles aligned with the current repo/worktree.
if [[ -n "${TMUX:-}" ]]; then
	autoload -Uz add-zsh-hook
	_dotfiles_codex_pane_title() {
		command -v tmux-codex-title >/dev/null 2>&1 && tmux-codex-title >/dev/null 2>&1 || true
	}
	add-zsh-hook chpwd _dotfiles_codex_pane_title
	add-zsh-hook precmd _dotfiles_codex_pane_title
fi

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
	if _nodenv_cmd=$(command -v nodenv 2>/dev/null) && [[ -n "$_nodenv_cmd" && -e "$_nodenv_cmd" && -x "$_nodenv_cmd" ]]; then
		export NODENV_ROOT="${NODENV_ROOT:-$HOME/.nodenv}"
		nodenv() {
			unset -f nodenv
			eval "$(command nodenv init -)"
			nodenv "$@"
		}
	fi
	unset _nodenv_cmd

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

# Load syntax highlighting (deferred until after first prompt)
typeset -gA ZSH_HIGHLIGHT_STYLES
ZSH_HIGHLIGHT_STYLES[comment]='fg=#565f89'
ZSH_HIGHLIGHT_STYLES[alias]='fg=#9ece6a'
ZSH_HIGHLIGHT_STYLES[command]='fg=#7aa2f7'
ZSH_HIGHLIGHT_STYLES[builtin]='fg=#7dcfff'
ZSH_HIGHLIGHT_STYLES[function]='fg=#bb9af7'
ZSH_HIGHLIGHT_STYLES[path]='fg=#ff9e64'
ZSH_HIGHLIGHT_STYLES[single-quoted-argument]='fg=#c0caf5'
ZSH_HIGHLIGHT_STYLES[double-quoted-argument]='fg=#c0caf5'
_dotfiles_resolve_zsh_highlighting_source() {
    if [[ -n "${ZSH_HIGHLIGHTING_SOURCE:-}" ]] && [[ -f "$ZSH_HIGHLIGHTING_SOURCE" ]]; then
        printf '%s\n' "$ZSH_HIGHLIGHTING_SOURCE"
        return
    fi
    if [[ -f /usr/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh ]]; then
        printf '%s\n' /usr/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
    elif [[ -f /usr/share/zsh/plugins/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh ]]; then
        printf '%s\n' /usr/share/zsh/plugins/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
    elif [[ -f /opt/homebrew/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh ]]; then
        printf '%s\n' /opt/homebrew/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
    elif [[ -f /usr/local/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh ]]; then
        printf '%s\n' /usr/local/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
    elif [[ -n "${HOMEBREW_PREFIX:-}" ]] && [[ -f "$HOMEBREW_PREFIX/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh" ]]; then
        printf '%s\n' "$HOMEBREW_PREFIX/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh"
    fi
}

_dotfiles_load_zsh_highlighting_deferred() {
    if [[ -z "${_DOTFILES_HIGHLIGHT_DEFERRED_PROMPT_SEEN:-}" ]]; then
        typeset -g _DOTFILES_HIGHLIGHT_DEFERRED_PROMPT_SEEN=1
        return
    fi

    add-zsh-hook -d precmd _dotfiles_load_zsh_highlighting_deferred
    local _dotfiles_highlighting_source
    _dotfiles_highlighting_source="$(_dotfiles_resolve_zsh_highlighting_source)"
    [[ -n "$_dotfiles_highlighting_source" ]] && source "$_dotfiles_highlighting_source" 2>/dev/null || true

    unset _dotfiles_highlighting_source
    unset -f _dotfiles_load_zsh_highlighting_deferred _dotfiles_resolve_zsh_highlighting_source
}

autoload -Uz add-zsh-hook
add-zsh-hook precmd _dotfiles_load_zsh_highlighting_deferred

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

# bun completions
[[ -s "$HOME/.bun/_bun" ]] && source "$HOME/.bun/_bun"

# bun
export BUN_INSTALL="$HOME/.bun"
export PATH="$BUN_INSTALL/bin:$PATH"

# OpenClaw completion
[[ -r "$HOME/.openclaw/completions/openclaw.zsh" ]] && source "$HOME/.openclaw/completions/openclaw.zsh"
