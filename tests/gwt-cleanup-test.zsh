#!/usr/bin/env zsh
set -euo pipefail

root="${0:A:h:h}"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

home="$temporary/home"
repo="$temporary/repo"
foreign="$temporary/foreign"
worktrees_root="$home/worktrees"
runtime="$home/Library/Application Support/agent-worktree-ops"
gwt_fixture_root="$temporary/gwt-fixture"
gwt_source="$gwt_fixture_root/functions/gwt/gwt.zsh"
mkdir -p "$home" "$runtime" "${gwt_source:h}"
cp "$root/functions/gwt/gwt.zsh" "$gwt_source"
cp "$root/bin/agent-worktree-ops/agent-worktree-clean" "$runtime/agent-worktree-clean"
chmod +x "$runtime/agent-worktree-clean"

git init -b main "$repo" >/dev/null
git -C "$repo" config user.name "Worktree Test"
git -C "$repo" config user.email "worktree@example.test"
git -C "$repo" config remote.origin.url "git@example.test:owner/repo.git"
print fixture >"$repo/README.md"
git -C "$repo" add README.md
git -C "$repo" commit -m fixture >/dev/null

remove_path="$temporary/remove-me"
stale_path="$temporary/stale"
git -C "$repo" worktree add -b remove-me "$remove_path" main >/dev/null
git -C "$repo" worktree add -b stale "$stale_path" main >/dev/null
rm -rf "$stale_path"

HOME="$home" DOTFILES_WORKTREES_ROOT="$worktrees_root" zsh -f -c '
  source "$1"
  cd "$2"
  gwt rm remove-me
 ' zsh "$gwt_source" "$repo"

[[ ! -d "$remove_path" ]]
git -C "$repo" worktree list --porcelain | grep -Fq "worktree ${stale_path:A}"

if HOME="$home" DOTFILES_WORKTREES_ROOT="$worktrees_root" zsh -f -c '
  source "$1"
  cd "$2"
  gwt audit --apply
 ' zsh "$gwt_source" "$repo" >"$temporary/audit-apply.out" 2>&1; then
  print -u2 'expected gwt audit --apply to fail'
  exit 1
fi
grep -Fq "audit is immutable" "$temporary/audit-apply.out"

metadata_before="$(git -C "$repo" worktree list --porcelain | shasum -a 256)"
if HOME="$home" DOTFILES_WORKTREES_ROOT="$worktrees_root" zsh -f -c '
  source "$1"
  cd "$2"
  gwt audit --app
 ' zsh "$gwt_source" "$repo" >"$temporary/audit-app.out" 2>&1; then
  print -u2 'expected abbreviated --app to fail'
  exit 1
fi
if HOME="$home" DOTFILES_WORKTREES_ROOT="$worktrees_root" zsh -f -c '
  source "$1"
  cd "$2"
  gwt audit --rep "$3"
 ' zsh "$gwt_source" "$repo" "$temporary/other" >"$temporary/audit-rep.out" 2>&1; then
  print -u2 'expected abbreviated --rep to fail'
  exit 1
fi
metadata_after="$(git -C "$repo" worktree list --porcelain | shasum -a 256)"
[[ "$metadata_before" == "$metadata_after" ]]
grep -Fq 'unrecognized arguments: --app' "$temporary/audit-app.out"
grep -Fq 'unrecognized arguments: --rep' "$temporary/audit-rep.out"

for command_name in audit clean; do
  if HOME="$home" DOTFILES_WORKTREES_ROOT="$worktrees_root" zsh -f -c '
    source "$1"
    cd "$2"
    gwt "$3" --repo "$4"
  ' zsh "$gwt_source" "$repo" "$command_name" "$temporary/other"; then
    print -u2 "expected gwt $command_name --repo to fail"
    exit 1
  fi
done >"$temporary/scope.out" 2>&1
grep -Fq 'controls repository scope' "$temporary/scope.out"

current_path="$temporary/current"
current_link="$temporary/current-link"
git -C "$repo" worktree add -b current "$current_path" main >/dev/null
ln -s "$current_path" "$current_link"
if HOME="$home" DOTFILES_WORKTREES_ROOT="$worktrees_root" zsh -f -c '
  source "$1"
  cd "$2"
  gwt rm current
 ' zsh "$gwt_source" "$current_link" >"$temporary/current.out" 2>&1; then
  print -u2 'expected removal from a symlinked current worktree to fail'
  exit 1
fi
grep -Fq 'cannot remove the worktree you are currently in' "$temporary/current.out"
[[ -d "$current_path" ]]

git init -b main "$foreign" >/dev/null
git -C "$foreign" config user.name "Worktree Test"
git -C "$foreign" config user.email "worktree@example.test"
print foreign >"$foreign/README.md"
git -C "$foreign" add README.md
git -C "$foreign" commit -m foreign >/dev/null

if HOME="$home" DOTFILES_WORKTREES_ROOT="$worktrees_root" zsh -f -c '
  source "$1"
  cd "$2"
  gwt rm "$3"
 ' zsh "$gwt_source" "$repo" "$foreign" >"$temporary/rm.out" 2>&1; then
  print -u2 'expected foreign absolute path removal to fail'
  exit 1
fi
grep -Fq 'refusing path not registered to this repository' "$temporary/rm.out"

existing_path="$worktrees_root/owner-repo/existing-branch"
mkdir -p "${existing_path:h}"
git clone -q "$foreign" "$existing_path"
if HOME="$home" DOTFILES_WORKTREES_ROOT="$worktrees_root" zsh -f -c '
  source "$1"
  cd "$2"
  gwt new existing/branch main
 ' zsh "$gwt_source" "$repo" >"$temporary/new.out" 2>&1; then
  print -u2 'expected foreign existing path reuse to fail'
  exit 1
fi
grep -Fq 'refusing existing path not registered to this repository' "$temporary/new.out"

cat >"$runtime/agent-worktree-maintain" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >"$TEST_MAINTAIN_ARGS"
EOF
chmod +x "$runtime/agent-worktree-maintain"

HOME="$home" \
DOTFILES_WORKTREES_ROOT="$worktrees_root" \
TEST_MAINTAIN_ARGS="$temporary/maintain.args" \
zsh -f -c '
  source "$1"
  cd "$2"
  gwt clean --min-age-days 7
 ' zsh "$gwt_source" "$repo" >"$temporary/clean.out"

grep -Fq 'running immediate forced maintenance' "$temporary/clean.out"
grep -Fq -- '--force --min-age-days 7' "$temporary/maintain.args"
grep -Fq -- "--repo ${repo:A}" "$temporary/maintain.args"

authority_tool="authority-probe"

write_probe() {
  local probe_path="$1"
  local marker="$2"
  mkdir -p "${probe_path:h}"
  cat >"$probe_path" <<EOF
#!/usr/bin/env bash
printf '$marker\\n'
EOF
  chmod +x "$probe_path"
}

symlink_home="$temporary/symlink-home"
symlink_checkout="$temporary/symlink-checkout"
symlink_source="$symlink_checkout/functions/gwt/gwt.zsh"
symlink_helper="$symlink_checkout/bin/agent-worktree-ops/$authority_tool"
mkdir -p "$symlink_home" "${symlink_source:h}"
cp "$root/functions/gwt/gwt.zsh" "$symlink_source"
write_probe "$symlink_helper" checkout
ln -s "$symlink_checkout/functions" "$symlink_home/functions"
resolved="$(
  HOME="$symlink_home" zsh -f -c '
    source "$1"
    [[ "$DOTFILES_GWT_SOURCE_PATH" == "$2" ]]
    [[ "$DOTFILES_GWT_CHECKOUT_ROOT" == "$3" ]]
    _gwt_tool_path "$4"
  ' zsh "$symlink_home/functions/gwt/gwt.zsh" "${symlink_source:A}" "${symlink_checkout:A}" "$authority_tool"
)"
[[ "$resolved" == "${symlink_helper:A}" ]]
[[ "$("$resolved")" == checkout ]]

wsl_home="$temporary/wsl-home"
wsl_source="$wsl_home/.dotfiles/functions/gwt/gwt.zsh"
wsl_helper="$wsl_home/.dotfiles/bin/agent-worktree-ops/$authority_tool"
mkdir -p "${wsl_source:h}"
cp "$root/functions/gwt/gwt.zsh" "$wsl_source"
write_probe "$wsl_helper" wsl
resolved="$(
  HOME="$wsl_home" zsh -f -c '
    source "$1"
    _gwt_tool_path "$2"
  ' zsh "$wsl_source" "$authority_tool"
)"
[[ "$resolved" == "${wsl_helper:A}" ]]
[[ "$("$resolved")" == wsl ]]

cloud_home="$temporary/cloud-home"
cloud_checkout="$cloud_home/Library/Mobile Documents/com~apple~CloudDocs/dotfiles"
cloud_source="$cloud_checkout/functions/gwt/gwt.zsh"
cloud_helper="$cloud_checkout/bin/agent-worktree-ops/$authority_tool"
cloud_runtime="$cloud_home/Library/Application Support/agent-worktree-ops/$authority_tool"
mkdir -p "${cloud_source:h}"
cp "$root/functions/gwt/gwt.zsh" "$cloud_source"
write_probe "$cloud_helper" cloud
write_probe "$cloud_runtime" runtime
resolved="$(
  HOME="$cloud_home" zsh -f -c '
    source "$1"
    _gwt_tool_path "$2"
  ' zsh "$cloud_source" "$authority_tool"
)"
[[ "$resolved" == "$cloud_runtime" ]]
[[ "$("$resolved")" == runtime ]]

runtime_home="$temporary/runtime-home"
runtime_checkout="$temporary/runtime-checkout"
runtime_source="$runtime_checkout/functions/gwt/gwt.zsh"
runtime_helper="$runtime_home/Library/Application Support/agent-worktree-ops/$authority_tool"
mkdir -p "${runtime_source:h}"
cp "$root/functions/gwt/gwt.zsh" "$runtime_source"
write_probe "$runtime_helper" runtime
resolved="$(
  HOME="$runtime_home" zsh -f -c '
    source "$1"
    _gwt_tool_path "$2"
  ' zsh "$runtime_source" "$authority_tool"
)"
[[ "$resolved" == "$runtime_helper" ]]

closed_home="$temporary/closed-home"
closed_checkout="$temporary/closed-checkout"
closed_source="$closed_checkout/functions/gwt/gwt.zsh"
mkdir -p "${closed_source:h}"
cp "$root/functions/gwt/gwt.zsh" "$closed_source"
if resolved="$(
  HOME="$closed_home" zsh -f -c '
    source "$1"
    _gwt_tool_path "$2"
  ' zsh "$closed_source" "$authority_tool"
)"; then
  print -u2 'expected tool resolution to fail without adjacent or installed helpers'
  exit 1
fi
[[ -z "$resolved" ]]

for stale_fallback in \
  '$HOME/GIT/_Perso/dotfiles/bin/agent-worktree-ops' \
  '$HOME/.dotfiles/bin/agent-worktree-ops' \
  'CloudDocs/dotfiles/bin/agent-worktree-ops' \
  '$HOME/bin/$tool_name'; do
  if grep -Fq "$stale_fallback" "$root/functions/gwt/gwt.zsh"; then
    print -u2 "stale helper fallback remains: $stale_fallback"
    exit 1
  fi
done

print 'gwt cleanup tests passed'
