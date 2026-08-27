#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
helper="$root/bin/claude-managed-settings"
fixture="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/claude-managed-settings-test.XXXXXX")" && pwd -P)"
trap 'rm -rf "$fixture"' EXIT

new_repo() {
  local name="$1"
  local repo="$fixture/$name/dotfiles"
  mkdir -p "$repo/.claude"
  printf '{"hooks":{"baseline":true}}\n' >"$repo/.claude/settings.json"
  git -C "$repo" init -q -b master
  git -C "$repo" add .claude/settings.json
  git -C "$repo" \
    -c user.name=Test \
    -c user.email=test@example.com \
    -c commit.gpgsign=false \
    commit -qm 'test: baseline'
  printf '%s\n' "$repo"
}

run_helper() {
  local repo="$1"
  local claude_dir="$2"
  shift 2
  "$@" "$helper" --dotfiles-dir "$repo" --claude-dir "$claude_dir"
}

assert_repo_clean() {
  local repo="$1"
  [[ -z "$(git -C "$repo" status --porcelain)" ]]
}

legacy_repo="$(new_repo legacy)"
legacy_claude="$fixture/legacy/home/.claude"
mkdir -p "$legacy_claude"
ln -s "$legacy_repo/.claude/settings.json" "$legacy_claude/settings.json"
legacy_baseline_hash="$(shasum -a 256 "$legacy_repo/.claude/settings.json" | awk '{print $1}')"
legacy_output="$(run_helper "$legacy_repo" "$legacy_claude" env)"
[[ "$legacy_output" == "claude_managed_settings=ready state=migrated" ]]
[[ "$(readlink "$legacy_claude/settings.json")" == settings.managed.json ]]
cmp -s "$legacy_repo/.claude/settings.json" "$legacy_claude/settings.managed.json"
[[ "$(shasum -a 256 "$legacy_repo/.claude/settings.json" | awk '{print $1}')" == "$legacy_baseline_hash" ]]
managed_hash="$(shasum -a 256 "$legacy_claude/settings.managed.json" | awk '{print $1}')"
[[ "$(run_helper "$legacy_repo" "$legacy_claude" env)" == "claude_managed_settings=ready state=managed" ]]
[[ "$(shasum -a 256 "$legacy_claude/settings.managed.json" | awk '{print $1}')" == "$managed_hash" ]]
assert_repo_clean "$legacy_repo"

managed_repo="$(new_repo managed)"
managed_claude="$fixture/managed/home/.claude"
mkdir -p "$managed_claude"
printf '{"hooks":{"machine":true}}\n' >"$managed_claude/settings.managed.json"
ln -s settings.managed.json "$managed_claude/settings.json"
managed_before="$(shasum -a 256 "$managed_claude/settings.managed.json" | awk '{print $1}')"
[[ "$(run_helper "$managed_repo" "$managed_claude" env)" == "claude_managed_settings=ready state=managed" ]]
[[ "$(shasum -a 256 "$managed_claude/settings.managed.json" | awk '{print $1}')" == "$managed_before" ]]
assert_repo_clean "$managed_repo"

regular_repo="$(new_repo regular)"
regular_claude="$fixture/regular/home/.claude"
mkdir -p "$regular_claude"
printf '{"hooks":{"machine":true}}\n' >"$regular_claude/settings.json"
regular_before="$(shasum -a 256 "$regular_claude/settings.json" | awk '{print $1}')"
[[ "$(run_helper "$regular_repo" "$regular_claude" env)" == "claude_managed_settings=ready state=regular" ]]
[[ "$(shasum -a 256 "$regular_claude/settings.json" | awk '{print $1}')" == "$regular_before" ]]
[[ ! -e "$regular_claude/settings.managed.json" ]]
assert_repo_clean "$regular_repo"

absent_repo="$(new_repo absent)"
absent_claude="$fixture/absent/home/.claude"
mkdir -p "$(dirname "$absent_claude")"
[[ "$(run_helper "$absent_repo" "$absent_claude" env)" == "claude_managed_settings=ready state=created" ]]
[[ "$(readlink "$absent_claude/settings.json")" == settings.managed.json ]]
cmp -s "$absent_repo/.claude/settings.json" "$absent_claude/settings.managed.json"
assert_repo_clean "$absent_repo"

for link_target in /tmp/absolute.json ../escape.json missing.json intermediate.json; do
  name="$(printf '%s' "$link_target" | tr '/.' '__')"
  repo="$(new_repo "hostile-$name")"
  claude="$fixture/hostile-$name/home/.claude"
  mkdir -p "$claude"
  if [[ "$link_target" == intermediate.json ]]; then
    ln -s final.json "$claude/intermediate.json"
  fi
  ln -s "$link_target" "$claude/settings.json"
  if output="$(run_helper "$repo" "$claude" env 2>&1)"; then
    printf 'expected hostile link rejection: %s\n' "$link_target" >&2
    exit 1
  fi
  [[ "$output" == "claude_managed_settings=blocked reason=target_incompatible" ]]
  [[ "$(readlink "$claude/settings.json")" == "$link_target" ]]
  assert_repo_clean "$repo"
done

broken_repo="$(new_repo broken-managed)"
broken_claude="$fixture/broken-managed/home/.claude"
mkdir -p "$broken_claude"
ln -s settings.managed.json "$broken_claude/settings.json"
if broken_output="$(run_helper "$broken_repo" "$broken_claude" env 2>&1)"; then
  printf 'expected broken managed link rejection\n' >&2
  exit 1
fi
[[ "$broken_output" == "claude_managed_settings=blocked reason=managed_target_incompatible" ]]

hardlink_repo="$(new_repo hardlink)"
hardlink_claude="$fixture/hardlink/home/.claude"
mkdir -p "$hardlink_claude"
printf '{}\n' >"$hardlink_claude/source.json"
ln "$hardlink_claude/source.json" "$hardlink_claude/settings.json"
if hardlink_output="$(run_helper "$hardlink_repo" "$hardlink_claude" env 2>&1)"; then
  printf 'expected settings hard-link rejection\n' >&2
  exit 1
fi
[[ "$hardlink_output" == "claude_managed_settings=blocked reason=target_incompatible" ]]

managed_hardlink_repo="$(new_repo managed-hardlink)"
managed_hardlink_claude="$fixture/managed-hardlink/home/.claude"
mkdir -p "$managed_hardlink_claude"
printf '{}\n' >"$managed_hardlink_claude/source.json"
ln "$managed_hardlink_claude/source.json" "$managed_hardlink_claude/settings.managed.json"
ln -s settings.managed.json "$managed_hardlink_claude/settings.json"
if managed_hardlink_output="$(run_helper "$managed_hardlink_repo" "$managed_hardlink_claude" env 2>&1)"; then
  printf 'expected managed-target hard-link rejection\n' >&2
  exit 1
fi
[[ "$managed_hardlink_output" == "claude_managed_settings=blocked reason=managed_target_incompatible" ]]

nonregular_repo="$(new_repo nonregular)"
nonregular_claude="$fixture/nonregular/home/.claude"
mkdir -p "$nonregular_claude/settings.managed.json"
ln -s settings.managed.json "$nonregular_claude/settings.json"
if nonregular_output="$(run_helper "$nonregular_repo" "$nonregular_claude" env 2>&1)"; then
  printf 'expected nonregular managed-target rejection\n' >&2
  exit 1
fi
[[ "$nonregular_output" == "claude_managed_settings=blocked reason=managed_target_incompatible" ]]

invalid_repo="$(new_repo invalid)"
invalid_claude="$fixture/invalid/home/.claude"
mkdir -p "$invalid_claude"
printf '{broken\n' >"$invalid_claude/settings.json"
if invalid_output="$(run_helper "$invalid_repo" "$invalid_claude" env 2>&1)"; then
  printf 'expected invalid JSON rejection\n' >&2
  exit 1
fi
[[ "$invalid_output" == "claude_managed_settings=blocked reason=invalid_json" ]]

invalid_baseline_repo="$(new_repo invalid-baseline)"
invalid_baseline_claude="$fixture/invalid-baseline/home/.claude"
printf '{broken\n' >"$invalid_baseline_repo/.claude/settings.json"
git -C "$invalid_baseline_repo" add .claude/settings.json
git -C "$invalid_baseline_repo" \
  -c user.name=Test \
  -c user.email=test@example.com \
  -c commit.gpgsign=false \
  commit -qm 'test: invalid baseline'
if invalid_baseline_output="$(run_helper "$invalid_baseline_repo" "$invalid_baseline_claude" env 2>&1)"; then
  printf 'expected invalid tracked baseline rejection\n' >&2
  exit 1
fi
[[ "$invalid_baseline_output" == "claude_managed_settings=blocked reason=invalid_json" ]]
[[ ! -e "$invalid_baseline_claude" ]]

for initial in absent legacy; do
  for point in before_managed_target after_managed_target before_link_publish after_link_publish; do
    name="interrupt-$initial-${point//_/-}"
    repo="$(new_repo "$name")"
    claude="$fixture/$name/home/.claude"
    mkdir -p "$claude"
    if [[ "$initial" == legacy ]]; then
      ln -s "$repo/.claude/settings.json" "$claude/settings.json"
    fi
    set +e
    run_helper "$repo" "$claude" env CLAUDE_MANAGED_SETTINGS_FAILPOINT="$point" >/dev/null 2>&1
    status=$?
    set -e
    [[ "$status" == 86 ]]
    recovered="$(run_helper "$repo" "$claude" env)"
    case "$recovered" in
      "claude_managed_settings=ready state=created" | \
      "claude_managed_settings=ready state=migrated" | \
      "claude_managed_settings=ready state=managed") ;;
      *)
        printf 'unexpected recovery result: %s\n' "$recovered" >&2
        exit 1
        ;;
    esac
    [[ "$(readlink "$claude/settings.json")" == settings.managed.json ]]
    cmp -s "$repo/.claude/settings.json" "$claude/settings.managed.json"
    [[ ! -e "$claude/.settings.json.managed-link" ]]
    [[ ! -e "$claude/.settings.managed.json.seed" ]]
    assert_repo_clean "$repo"
  done
done

dirty_repo="$(new_repo dirty-baseline)"
dirty_claude="$fixture/dirty-baseline/home/.claude"
printf ' ' >>"$dirty_repo/.claude/settings.json"
if dirty_output="$(run_helper "$dirty_repo" "$dirty_claude" env 2>&1)"; then
  printf 'expected dirty baseline rejection\n' >&2
  exit 1
fi
[[ "$dirty_output" == "claude_managed_settings=blocked reason=baseline_dirty" ]]
[[ ! -e "$dirty_claude" ]]

setup_home="$fixture/setup-integration/home"
setup_repo="$(new_repo setup-integration)"
mkdir -p "$setup_repo/bin"
cp "$helper" "$setup_repo/bin/claude-managed-settings"
chmod 0755 "$setup_repo/bin/claude-managed-settings"
git -C "$setup_repo" add bin/claude-managed-settings
git -C "$setup_repo" \
  -c user.name=Test \
  -c user.email=test@example.com \
  -c commit.gpgsign=false \
  commit -qm 'test: helper'
mkdir -p "$setup_home/GIT/_Perso"
mv "$setup_repo" "$setup_home/GIT/_Perso/dotfiles"
fake_bin="$fixture/setup-integration/bin"
mkdir -p "$fake_bin"
cat >"$fake_bin/uname" <<'EOF'
#!/usr/bin/env bash
printf 'Linux\n'
EOF
chmod 0755 "$fake_bin/uname"
HOME="$setup_home" PATH="$fake_bin:$PATH" bash -c '
  source "$1/install.sh"
  setup_claude_dotfiles
' _ "$root" >/dev/null
[[ "$(readlink "$setup_home/.claude/settings.json")" == settings.managed.json ]]
cmp -s \
  "$setup_home/GIT/_Perso/dotfiles/.claude/settings.json" \
  "$setup_home/.claude/settings.managed.json"
assert_repo_clean "$setup_home/GIT/_Perso/dotfiles"

printf 'claude_managed_settings_test=passed\n'
