#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
guard="$root/bin/agent-worktree-ops/worktree-storage-guard"
temporary="$(mktemp -d)"
temporary="$(cd "$temporary" && pwd -P)"
trap 'rm -rf "$temporary"' EXIT

home="$temporary/home"
config="$temporary/external-worktree-storage.json"
observed="$temporary/observed.json"
mount_point="$home/.codex/worktrees"
mkdir -p "$mount_point"
chmod 0700 "$mount_point"

write_config() {
  local uuid="${1:-11111111-2222-3333-4444-555555555555}"
  cat >"$config" <<EOF
{"case_sensitive":false,"encrypted":true,"filesystem":"apfs","mount_point":"$mount_point","required":true,"schema_version":"external-worktree-storage.v1","volume_uuid":"$uuid"}
EOF
  chmod 0600 "$config"
}

write_observed() {
  local uuid="$1"
  local filesystem="$2"
  local mounted="$3"
  local owner_uid="$4"
  local owner_gid="$5"
  local mode="$6"
  cat >"$observed" <<EOF
{"case_sensitive":false,"device":200,"encrypted":true,"filesystem":"$filesystem","mode":"$mode","mount_point":"$mount_point","mounted":$mounted,"owner_gid":$owner_gid,"owner_uid":$owner_uid,"ownership_enabled":true,"parent_device":100,"volume_uuid":"$uuid"}
EOF
}

run_guard() {
  HOME="$home" \
    WORKTREE_STORAGE_CONFIG="$config" \
    WORKTREE_STORAGE_GUARD_TESTING=1 \
    WORKTREE_STORAGE_GUARD_OBSERVED="$observed" \
    "$guard" "$@"
}

write_config
write_observed "11111111-2222-3333-4444-555555555555" apfs true "$(id -u)" "$(id -g)" 0700
[[ "$(run_guard --print-mount-point)" == "$mount_point" ]]

owner_repo="$temporary/owner-repo"
managed_worktree="$mount_point/owner-repo/feature"
git init -q -b main "$owner_repo"
git -C "$owner_repo" config user.name "Storage Test"
git -C "$owner_repo" config user.email "storage@example.test"
printf 'fixture\n' >"$owner_repo/README.md"
git -C "$owner_repo" add README.md
git -C "$owner_repo" commit -qm fixture
mkdir -p "$(dirname "$managed_worktree")"
git -C "$owner_repo" worktree add -q -b feature "$managed_worktree" main
owner_common="$(git -C "$owner_repo" rev-parse --path-format=absolute --git-common-dir)"
worktree_common="$(git -C "$managed_worktree" rev-parse --path-format=absolute --git-common-dir)"
[[ "$owner_common" == "$worktree_common" ]]
git -C "$owner_repo" worktree remove "$managed_worktree"

write_observed "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" apfs true "$(id -u)" "$(id -g)" 0700
if run_guard >"$temporary/wrong-uuid.out" 2>&1; then
  echo "wrong UUID must fail" >&2
  exit 1
fi
grep -Fq "UUID does not match policy" "$temporary/wrong-uuid.out"

write_observed "11111111-2222-3333-4444-555555555555" exfat true "$(id -u)" "$(id -g)" 0700
if run_guard >"$temporary/exfat.out" 2>&1; then
  echo "ExFAT must fail" >&2
  exit 1
fi
grep -Fq "not APFS" "$temporary/exfat.out"

wheel_gid="$(python3 - <<'PY'
import grp
try:
    print(grp.getgrnam("wheel").gr_gid)
except KeyError:
    print(0)
PY
)"
write_observed "11111111-2222-3333-4444-555555555555" apfs false 0 "$wheel_gid" 0000
if run_guard >"$temporary/absent.out" 2>&1; then
  echo "absent mount must fail" >&2
  exit 1
fi
grep -Fq "not mounted" "$temporary/absent.out"

write_observed "11111111-2222-3333-4444-555555555555" apfs false "$(id -u)" "$(id -g)" 0700
if run_guard >"$temporary/fallback.out" 2>&1; then
  echo "writable fallback must fail" >&2
  exit 1
fi
grep -Fq "fallback is writable" "$temporary/fallback.out"

write_observed "11111111-2222-3333-4444-555555555555" apfs true "$(id -u)" "$(id -g)" 0700
run_guard
write_observed "11111111-2222-3333-4444-555555555555" apfs false 0 "$wheel_gid" 0000
if run_guard >"$temporary/hot-disappearance.out" 2>&1; then
  echo "hot disappearance must fail closed" >&2
  exit 1
fi
grep -Fq "not mounted" "$temporary/hot-disappearance.out"

rm -rf "$mount_point"
ln -s /Volumes/ExternalWorktrees "$mount_point"
write_observed "11111111-2222-3333-4444-555555555555" apfs true "$(id -u)" "$(id -g)" 0700
if run_guard >"$temporary/symlink.out" 2>&1; then
  echo "symlinked canonical mount point must fail" >&2
  exit 1
fi
grep -Fq "symlinked component" "$temporary/symlink.out"

rm "$mount_point"
mkdir "$mount_point"
write_config pending
if run_guard >"$temporary/pending.out" 2>&1; then
  echo "pending UUID must fail" >&2
  exit 1
fi
grep -Fq "policy is incomplete" "$temporary/pending.out"

rm "$config"
HOME="$home" WORKTREE_STORAGE_CONFIG="$config" "$guard"
if HOME="$home" WORKTREE_STORAGE_CONFIG="$config" "$guard" --require-config \
  >"$temporary/unconfigured.out" 2>&1; then
  echo "required unconfigured storage must fail" >&2
  exit 1
fi
grep -Fq "not configured" "$temporary/unconfigured.out"

printf 'worktree storage tests passed\n'
