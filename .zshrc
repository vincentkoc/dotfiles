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
if [[ -z "${DOTFILES_EXPORTS_LOADED:-}" && -r ~/.exports ]]; then
	source ~/.exports
	export DOTFILES_EXPORTS_LOADED=1
fi

# Load dotfiles .env early (auto-export all vars). Use KEY=VALUE (no "export" needed).
if [[ "$OSTYPE" == darwin* ]]; then
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

# Keep OpenClaw local checks civil on this 24 GB laptop. Repo scripts still allow
# explicit overrides when a wider run is worth the heat.
export OPENCLAW_LOCAL_CHECK_MODE="${OPENCLAW_LOCAL_CHECK_MODE:-throttled}"
export OPENCLAW_TEST_PROJECTS_SERIAL="${OPENCLAW_TEST_PROJECTS_SERIAL:-1}"
export OPENCLAW_VITEST_MAX_WORKERS="${OPENCLAW_VITEST_MAX_WORKERS:-1}"

# Process launch is expensive on this Mac, so do not make `cd` spawn `ls`.
export DOTFILES_CD_SKIP_LISTING="${DOTFILES_CD_SKIP_LISTING:-1}"

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
ENABLE_CORRECTION="false"

# Spaceship prompt tuned for Tokyo Night palette
SPACESHIP_PROMPT_ORDER=(dir git exit_code char)
SPACESHIP_DIR_TRUNC=3
SPACESHIP_DIR_TRUNC_REPO=false
SPACESHIP_DIR_COLOR="cyan"
SPACESHIP_PACKAGE_SHOW=false
SPACESHIP_PACKAGE_PREFIX="pkg "
SPACESHIP_RUBY_SHOW=false
SPACESHIP_PYTHON_SHOW=false
SPACESHIP_PYTHON_SYMBOL="py"
SPACESHIP_NODE_SHOW=false
SPACESHIP_NODE_SHOW_VERSION=false
SPACESHIP_NODE_SYMBOL="node"
SPACESHIP_KUBECTL_SHOW=false
SPACESHIP_GIT_SHOW=true
SPACESHIP_GIT_STATUS_SHOW=false
SPACESHIP_GIT_BRANCH_COLOR="magenta"
SPACESHIP_GIT_STATUS_COLOR="yellow"
SPACESHIP_DOCKER_SHOW=false
SPACESHIP_DOCKER_CONTEXT_SHOW=false
SPACESHIP_DOCKER_COMPOSE_SHOW=false
SPACESHIP_EXEC_TIME_SHOW=false
SPACESHIP_CHAR_PREFIX=""
SPACESHIP_CHAR_SYMBOL=">"
SPACESHIP_CHAR_COLOR_SUCCESS="green"
SPACESHIP_CHAR_COLOR_FAILURE="red"
SPACESHIP_CHAR_SUFFIX=" "

SPACESHIP_PROMPT_SEPARATE_LINE=false
SPACESHIP_PROMPT_ADD_NEWLINE=true
SPACESHIP_CLIPBOARD_SHOW=false
SPACESHIP_PROMPT_ASYNC=false
# Keep package section fast by skipping heavyweight managers in prompt rendering.
SPACESHIP_PACKAGE_ORDER=(npm lerna cargo composer python dart)

# Avoid remote Docker prompt probes on flaky links unless explicitly requested.
if [[ -n "${DOCKER_HOST:-}" && "$DOCKER_HOST" == tcp://* ]] && [[ -z "${DOTFILES_FORCE_REMOTE_DOCKER_PROMPT:-}" ]]; then
    SPACESHIP_DOCKER_SHOW=false
    SPACESHIP_DOCKER_CONTEXT_SHOW=false
    SPACESHIP_DOCKER_COMPOSE_SHOW=false
fi

# Right prompts are noisy in direct Ghostty because every line edit has to
# repaint both sides of the prompt. Keep the terminal smooth by default.
if [[ -n "$TMUX" || ( "${TERM_PROGRAM:-}" == "ghostty" && "${DOTFILES_ZSH_SMOOTH_REDRAW:-1}" == "1" ) ]]; then
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

# Keep OMZ lean; command completions still come from fpath/compinit.
plugins=(
	git
	z
	dirhistory
	zsh-autosuggestions
	colored-man-pages
)

# Performance improvements
DISABLE_AUTO_UPDATE="true"
DISABLE_AUTO_TITLE="true"
ZSH_DISABLE_COMPFIX="true"
COMPLETION_WAITING_DOTS="true"

if [[ "${TERM_PROGRAM:-}" == "ghostty" && -z "${TMUX:-}" && "${DOTFILES_ZSH_SMOOTH_REDRAW:-1}" == "1" ]]; then
	unset ZSH_AUTOSUGGEST_USE_ASYNC
else
	ZSH_AUTOSUGGEST_USE_ASYNC=1
fi
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
	ZSH_COMPDUMP="${ZSH_COMPDUMP:-$ZSH_CACHE_DIR/.zcompdump-${HOST:-localhost}-${ZSH_VERSION}}"
	if [[ "${DOTFILES_USE_OMZ:-0}" == "1" ]]; then
		source $ZSH/oh-my-zsh.sh
	else
		autoload -Uz compinit
		compinit -C -d "$ZSH_COMPDUMP"

		[[ -r "$ZSH/plugins/z/z.plugin.zsh" ]] && source "$ZSH/plugins/z/z.plugin.zsh"
		[[ -r "$ZSH/custom/plugins/zsh-autosuggestions/zsh-autosuggestions.zsh" ]] && source "$ZSH/custom/plugins/zsh-autosuggestions/zsh-autosuggestions.zsh"
		[[ -r "$ZSH/custom/themes/spaceship.zsh-theme" ]] && source "$ZSH/custom/themes/spaceship.zsh-theme"
	fi

	if [[ "${TERM_PROGRAM:-}" == "ghostty" && -z "${TMUX:-}" && "${DOTFILES_ZSH_SMOOTH_REDRAW:-1}" == "1" ]]; then
		unset ZSH_AUTOSUGGEST_USE_ASYNC
	fi

# Spaceship's async init is brittle when zpty startup flakes. Fall back to a
# sync prompt for this shell instead of spamming worker errors.
if (( ${+functions[spaceship::worker::init]} )); then
	_dotfiles_spaceship_disable_async() {
		async_stop_worker "spaceship" >/dev/null 2>&1
		SPACESHIP_JOBS=()
		typeset -g SPACESHIP_PROMPT_ASYNC=false
	}

	_dotfiles_spaceship_worker_alive() {
		zpty -t spaceship &>/dev/null
	}

	spaceship::worker::init() {
		if spaceship::is_prompt_async; then
			_dotfiles_spaceship_disable_async
			typeset -g SPACESHIP_PROMPT_ASYNC=true
			if ! async_start_worker "spaceship" -n -u 2>/dev/null || ! zpty -t spaceship &>/dev/null; then
				_dotfiles_spaceship_disable_async
				return 0
			fi
			async_worker_eval "spaceship" setopt extendedglob 2>/dev/null || {
				_dotfiles_spaceship_disable_async
				return 0
			}
			async_worker_eval "spaceship" spaceship::worker::renice 2>/dev/null || {
				_dotfiles_spaceship_disable_async
				return 0
			}
			async_register_callback "spaceship" spaceship::core::async_callback
		fi
	}

	spaceship::worker::flush() {
		if spaceship::is_prompt_async; then
			_dotfiles_spaceship_worker_alive || {
				_dotfiles_spaceship_disable_async
				return 0
			}
			async_flush_jobs "spaceship" 2>/dev/null || _dotfiles_spaceship_disable_async
		fi
	}

	spaceship::worker::eval() {
		if spaceship::is_prompt_async; then
			_dotfiles_spaceship_worker_alive || {
				_dotfiles_spaceship_disable_async
				return 0
			}
			async_worker_eval "spaceship" "$@" 2>/dev/null || _dotfiles_spaceship_disable_async
		fi
	}

	spaceship::worker::run() {
		if spaceship::is_prompt_async; then
			_dotfiles_spaceship_worker_alive || {
				_dotfiles_spaceship_disable_async
				return 0
			}
			SPACESHIP_JOBS+=("$1")
			async_job "spaceship" "$@" 2>/dev/null || _dotfiles_spaceship_disable_async
		fi
	}
	fi

# Spaceship's stock git section shells out for status-ish data. Keep git in the
# prompt, but make it branch-only and filesystem-backed so pressing enter stays
# instant in big repos.
_dotfiles_git_root_fast() {
	local dir="${PWD:A}"
	while [[ "$dir" != "/" && -n "$dir" ]]; do
		if [[ -d "$dir/.git" || -f "$dir/.git" ]]; then
			printf '%s\n' "$dir"
			return 0
		fi
		dir="${dir:h}"
	done
	return 1
}

_dotfiles_git_dir_fast() {
	local repo_root="$1"
	local git_file git_dir
	if [[ -d "$repo_root/.git" ]]; then
		printf '%s\n' "$repo_root/.git"
		return 0
	fi
	[[ -f "$repo_root/.git" ]] || return 1
	IFS= read -r git_file < "$repo_root/.git" || return 1
	git_dir="${git_file#gitdir: }"
	[[ "$git_dir" != "$git_file" && -n "$git_dir" ]] || return 1
	[[ "$git_dir" = /* ]] || git_dir="$repo_root/$git_dir"
	printf '%s\n' "${git_dir:A}"
}

_dotfiles_git_branch_fast() {
	local git_dir="$1"
	local head ref
	IFS= read -r head < "$git_dir/HEAD" || return 1
	if [[ "$head" == ref:\ * ]]; then
		ref="${head#ref: }"
		printf '%s\n' "${ref#refs/heads/}"
		return 0
	fi
	git --git-dir="$git_dir" rev-parse --short HEAD 2>/dev/null
}

if (( ${+functions[spaceship_git]} )); then
	spaceship_git() {
		[[ $SPACESHIP_GIT_SHOW == false ]] && return

		local repo_root git_dir branch
		repo_root=$(_dotfiles_git_root_fast) || return
		git_dir=$(_dotfiles_git_dir_fast "$repo_root") || return
		branch=$(_dotfiles_git_branch_fast "$git_dir") || return

		spaceship::section \
			--color 'white' \
			--prefix "$SPACESHIP_GIT_PREFIX" \
			--suffix "$SPACESHIP_GIT_SUFFIX" \
			--symbol "$SPACESHIP_GIT_SYMBOL" \
			"$branch"
	}
fi

# `z foo` should jump, not trigger spelling correction on the argument.
alias z='nocorrect zshz 2>&1'

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

termfix() {
	printf '\033[?1000l\033[?1002l\033[?1003l\033[?1006l\033[?1015l\033[?2004l'
	stty sane 2>/dev/null || true
	reset
}

codex-resume() {
	if [[ $# -gt 0 ]]; then
		env -u NO_COLOR CLICOLOR=1 CLICOLOR_FORCE=1 FORCE_COLOR=3 COLORTERM=truecolor codex resume --no-alt-screen "$@"
	else
		env -u NO_COLOR CLICOLOR=1 CLICOLOR_FORCE=1 FORCE_COLOR=3 COLORTERM=truecolor codex resume --all --no-alt-screen
	fi
}

codex-last() {
	env -u NO_COLOR CLICOLOR=1 CLICOLOR_FORCE=1 FORCE_COLOR=3 COLORTERM=truecolor codex resume --last --no-alt-screen "$@"
}

codex-tmux() {
	env -u NO_COLOR CLICOLOR=1 CLICOLOR_FORCE=1 FORCE_COLOR=3 COLORTERM=truecolor codex --no-alt-screen "$@"
}

cx() {
	codex-tmux "$@"
}

# Terminal title: repo/branch context, including linked worktree name.
if [[ -z "${TMUX:-}" && ( "${TERM_PROGRAM:-}" == "iTerm.app" || "${TERM_PROGRAM:-}" == "ghostty" ) ]]; then
	autoload -Uz add-zsh-hook

	_dotfiles_terminal_git_root() {
		local dir="${PWD:A}"
		while [[ "$dir" != "/" && -n "$dir" ]]; do
			if [[ -d "$dir/.git" || -f "$dir/.git" ]]; then
				printf '%s\n' "$dir"
				return 0
			fi
			dir="${dir:h}"
		done
		return 1
	}

	_dotfiles_terminal_git_dir() {
		local repo_root="$1"
		local git_file git_dir
		if [[ -d "$repo_root/.git" ]]; then
			printf '%s\n' "$repo_root/.git"
			return 0
		fi
		[[ -f "$repo_root/.git" ]] || return 1
		IFS= read -r git_file < "$repo_root/.git" || return 1
		git_dir="${git_file#gitdir: }"
		[[ "$git_dir" != "$git_file" && -n "$git_dir" ]] || return 1
		[[ "$git_dir" = /* ]] || git_dir="$repo_root/$git_dir"
		printf '%s\n' "${git_dir:A}"
	}

	_dotfiles_terminal_git_branch() {
		local git_dir="$1"
		local head ref
		IFS= read -r head < "$git_dir/HEAD" || return 1
		if [[ "$head" == ref:\ * ]]; then
			ref="${head#ref: }"
			printf '%s\n' "${ref#refs/heads/}"
			return 0
		fi
		git --git-dir="$git_dir" rev-parse --short HEAD 2>/dev/null
	}

	_dotfiles_terminal_title() {
		local title cwd repo repo_root branch git_dir wt_name
		cwd="${PWD/#$HOME/~}"
		title="$cwd"

		if repo_root=$(_dotfiles_terminal_git_root) && git_dir=$(_dotfiles_terminal_git_dir "$repo_root") && branch=$(_dotfiles_terminal_git_branch "$git_dir"); then
			repo="${repo_root:t}"
			if [[ "$git_dir" == *"/worktrees/"* ]]; then
				wt_name="${git_dir:t}"
				title="$repo:$branch [$wt_name] - $cwd"
			else
				title="$repo:$branch - $cwd"
			fi
		fi

		[[ "$title" == "${_dotfiles_terminal_title_last:-}" ]] && return
		typeset -g _dotfiles_terminal_title_last="$title"

		# OSC 0 updates the visible terminal title in iTerm and Ghostty.
		print -Pn "\e]0;${title}\a"
	}

	add-zsh-hook chpwd _dotfiles_terminal_title
	add-zsh-hook precmd _dotfiles_terminal_title
fi

# Keep tmux pane titles aligned with the current repo/worktree.
if [[ -n "${TMUX:-}" ]]; then
	autoload -Uz add-zsh-hook
	_dotfiles_tmux_sync_context() {
		command -v tt >/dev/null 2>&1 && tt sync >/dev/null 2>&1 || true
	}
	add-zsh-hook chpwd _dotfiles_tmux_sync_context

	git() {
		command git "$@"
		local git_status=$?
		case "${1:-}" in
			checkout|switch|worktree|rebase)
				_dotfiles_tmux_sync_context
				;;
		esac
		return "$git_status"
	}
fi

#
# Mac Specific
#
if [[ $OSTYPE == 'darwin'* ]]; then
	# Architecture and Security
	# Uncomment if needed for x86 compatibility
	# export ARCHFLAGS="-arch x86_64"
		if [[ -n "${TTY:-}" ]]; then
			export GPG_TTY="$TTY"
		elif [[ -t 0 ]]; then
			export GPG_TTY="$(tty 2>/dev/null)"
		fi

	# Version Managers - Lazy Loading
	# Python - pyenv
		if (( $+commands[pyenv] )); then
		export PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"
		path=("$PYENV_ROOT/shims" $path)
		pyenv() {
			unset -f pyenv
			eval "$(command pyenv init - --no-rehash)"
			pyenv "$@"
		}
	fi

	# Fallback: alias python to python3 if python not available
		if (( ! $+commands[python] && $+commands[python3] )); then
		alias python='python3'
		alias pip='pip3'
	fi

	# Node.js - nodenv
		if (( $+commands[nodenv] )); then
		export NODENV_ROOT="${NODENV_ROOT:-$HOME/.nodenv}"
		nodenv() {
			unset -f nodenv
			eval "$(command nodenv init -)"
			nodenv "$@"
		}
	fi
	unset _nodenv_cmd

	# Ruby - rbenv
		if (( $+commands[rbenv] )); then
		rbenv() {
			unset -f rbenv
			eval "$(command rbenv init -)"
			rbenv "$@"
		}
	fi

	# Java - jenv
		if (( $+commands[jenv] )); then
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

if (( $+commands[fd] )); then
	export FZF_DEFAULT_COMMAND='fd --type f --hidden --follow --exclude .git'
	export FZF_CTRL_T_COMMAND="$FZF_DEFAULT_COMMAND"
elif (( $+commands[rg] )); then
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
if (( $+commands[direnv] )); then
    _dotfiles_direnv_should_export() {
        [[ -n "${DIRENV_DIR:-}${DIRENV_FILE:-}${DIRENV_DIFF:-}" ]] && return 0

        local dir="${PWD:A}"
        while [[ "$dir" != "/" && -n "$dir" ]]; do
            [[ -f "$dir/.envrc" || -f "$dir/.env" ]] && return 0
            dir="${dir:h}"
        done
        return 1
    }

    _direnv_hook() {
        _dotfiles_direnv_should_export || return 0
        trap -- '' SIGINT
        eval "$(direnv export zsh)"
        trap - SIGINT
    }
    typeset -ag precmd_functions
    if (( ! ${precmd_functions[(I)_direnv_hook]} )); then
        precmd_functions=(_direnv_hook $precmd_functions)
    fi
    typeset -ag chpwd_functions
    if (( ! ${chpwd_functions[(I)_direnv_hook]} )); then
        chpwd_functions=(_direnv_hook $chpwd_functions)
    fi
fi

# bun completions
[[ -s "$HOME/.bun/_bun" ]] && source "$HOME/.bun/_bun"

# bun
export BUN_INSTALL="$HOME/.bun"
export PATH="$BUN_INSTALL/bin:$PATH"

# Prefer user-installed CLI shims over stale Homebrew globals.
typeset -U path PATH
path=("$HOME/.local/bin" ${path:#$HOME/.local/bin})
export PATH

# OpenClaw completion
[[ -r "$HOME/.openclaw/completions/openclaw.zsh" ]] && source "$HOME/.openclaw/completions/openclaw.zsh"
