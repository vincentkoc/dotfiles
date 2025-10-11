source ~/.profile
# export GOPATH=$(go env GOPATH)
# export PATH=$PATH:$(go env GOPATH)/bin

if [ -f "$HOME/.cargo/env" ]; then
  . "$HOME/.cargo/env"
fi

if command -v brew >/dev/null 2>&1; then
  if [ -r "$(brew --prefix)/etc/profile.d/bash_completion.sh" ]; then
    source "$(brew --prefix)/etc/profile.d/bash_completion.sh"
  fi
fi
