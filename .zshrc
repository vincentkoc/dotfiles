export PYENV_ROOT="$(pyenv root)"
export PATH="$PYENV_ROOT/shims:/usr/local/sbin:$PATH"
eval "$(pyenv init -)"
