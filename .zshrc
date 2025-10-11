#
# Oh-my-zsh
#

# Bootstrap environment before loading oh-my-zsh so PATH + theme variables exist early
if [[ -r ~/.exports ]]; then
	source ~/.exports
fi
export ZSH="$HOME/.oh-my-zsh"
ZSH_THEME="spaceship"
ENABLE_CORRECTION="true"

# Add more useful plugins
plugins=(
	git
	z
	kubectl
	dirhistory
	zsh-autosuggestions
	docker
	brew
	macos
	npm
	pip
	rust
	golang
	vscode
	fzf
	history-substring-search
	colored-man-pages
	command-not-found
	# zsh-interactive-cd
)

# Performance improvements
DISABLE_AUTO_UPDATE="true"
COMPLETION_WAITING_DOTS="true"
ZSH_DISABLE_COMPFIX="true"

fpath=($ZSH/custom/completions $fpath)
source $ZSH/oh-my-zsh.sh

#
# Load dotfiles pre-ENV
#
for file in ~/.{path,bash_prompt,aliases,functions}; do
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
	if command -v pyenv &> /dev/null; then
		export PYENV_ROOT="$(pyenv root)"
		path=("$PYENV_ROOT/shims" $path)
		eval "$(pyenv init - --no-rehash)"
	fi

	# Node.js - nodenv
	if command -v nodenv &> /dev/null; then
		export NODENV_ROOT="$(nodenv root)"
		path=("$NODENV_ROOT/shims" $path)
		eval "$(nodenv init -)"
	fi

	# Ruby - rbenv
	if command -v rbenv &> /dev/null; then
		eval "$(rbenv init -)"
	fi

	# Java - jenv
	if command -v jenv &> /dev/null; then
		eval "$(jenv init -)"
	fi

	# ZSH Autocomplete
	if type brew &>/dev/null; then
		FPATH="$(brew --prefix)/share/zsh-completions:$FPATH"
		FPATH="$(brew --prefix)/share/zsh/site-functions:$FPATH"

		# Only regenerate completions once per day
		autoload -Uz compinit
		if [ $(date +'%j') != $(stat -f '%Sm' -t '%j' ~/.zcompdump) ]; then
			compinit
		else
			compinit -C
		fi
	fi

	# Better history search
	bindkey '^[[A' history-substring-search-up
	bindkey '^[[B' history-substring-search-down
fi

#
# System Color Prompt
#
if [ -f "$HOME/bin/system-colour.py" ]; then
	eval "$($HOME/bin/system-colour.py)"
	# Custom prompt with system color
	PROMPT='%F{36}%K{$SYSTEM_COLOUR_BG}%F{$SYSTEM_COLOUR_FG}%n@%M%k%f %F{blue}%~ %(?.%F{green}.%F{red})%#%f '
fi

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
path=(
	"/opt/homebrew/opt/postgresql@13/bin"
	"/opt/homebrew/opt/llvm/bin"
	$path
)
typeset -U path # Remove duplicates from PATH

# FZF Configuration
[ -f ~/.fzf.zsh ] && source ~/.fzf.zsh
export FZF_DEFAULT_COMMAND='fd --type f --hidden --follow --exclude .git'
export FZF_CTRL_T_COMMAND="$FZF_DEFAULT_COMMAND"

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
zstyle ':completion:*' list-colors ''
zstyle ':completion:*:*:kill:*:processes' list-colors '=(#b) #([0-9]#) ([0-9a-z-]#)*=01;34=0=01'

# Load syntax highlighting (should be last)
source /opt/homebrew/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh 2>/dev/null || true
