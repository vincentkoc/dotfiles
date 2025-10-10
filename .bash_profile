source ~/.profile
# export GOPATH=$(go env GOPATH)
# export PATH=$PATH:$(go env GOPATH)/bin

if [ -f "$HOME/.cargo/env" ]; then
  . "$HOME/.cargo/env"
fi
