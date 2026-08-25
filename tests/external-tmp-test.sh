#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command_path="$root/bin/external-tmp"
temporary="$(mktemp -d)"
temporary="$(cd "$temporary" && pwd -P)"
trap 'rm -rf "$temporary"' EXIT

home="$temporary/home"
mount_point="$home/.codex/worktrees"
config="$temporary/config.json"
observed="$temporary/observed.json"
probe="$temporary/probe.sh"
result="$temporary/result"
mkdir -p "$mount_point"
chmod 0700 "$mount_point"
cat >"$config" <<EOF
{"case_sensitive":false,"encrypted":true,"filesystem":"apfs","mount_point":"$mount_point","required":true,"schema_version":"external-worktree-storage.v1","volume_uuid":"11111111-2222-3333-4444-555555555555"}
EOF
chmod 0600 "$config"
cat >"$observed" <<EOF
{"case_sensitive":false,"device":200,"encrypted":true,"filesystem":"apfs","mode":"0700","mount_point":"$mount_point","mounted":true,"owner_gid":$(id -g),"owner_uid":$(id -u),"ownership_enabled":true,"parent_device":100,"volume_uuid":"11111111-2222-3333-4444-555555555555"}
EOF
cat >"$probe" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n%s\n%s\n' "$TMPDIR" "$TMP" "$TEMP" >"$1"
touch "$TMPDIR/child-created"
EOF
chmod 0755 "$probe"

HOME="$home" \
  WORKTREE_STORAGE_CONFIG="$config" \
  WORKTREE_STORAGE_GUARD_TESTING=1 \
  WORKTREE_STORAGE_GUARD_OBSERVED="$observed" \
  "$command_path" "$probe" "$result"

tmpdir_path="$(sed -n '1p' "$result")"
tmp_path="$(sed -n '2p' "$result")"
temp_path="$(sed -n '3p' "$result")"
[[ "$tmpdir_path" == "$mount_point/.scratch/tmp/"external-tmp.*"/" ]]
[[ "$tmp_path" == "${tmpdir_path%/}" ]]
[[ "$temp_path" == "${tmpdir_path%/}" ]]
[[ -z "$(find "$mount_point/.scratch/tmp" -mindepth 1 -print -quit)" ]]

fake_guard="$temporary/fake-guard"
counter="$temporary/guard-counter"
child_marker="$temporary/child-ran"
cat >"$fake_guard" <<EOF
#!/usr/bin/env bash
set -euo pipefail
count=0
[[ ! -f "$counter" ]] || count="\$(cat "$counter")"
count=\$((count + 1))
printf '%s\n' "\$count" >"$counter"
if [[ "\${1:-}" == "--require-config" ]]; then
  printf '%s\n' "$mount_point"
  exit 0
fi
if (( count >= 2 )); then
  printf 'simulated hot disappearance\n' >&2
  exit 78
fi
EOF
chmod 0755 "$fake_guard"
if HOME="$home" WORKTREE_STORAGE_GUARD="$fake_guard" \
  "$command_path" /usr/bin/touch "$child_marker" >"$temporary/hot.out" 2>&1; then
  echo "external-tmp must stop when storage disappears before child execution" >&2
  exit 1
fi
[[ ! -e "$child_marker" ]]
grep -Fq "simulated hot disappearance" "$temporary/hot.out"

if HOME="$home" WORKTREE_STORAGE_CONFIG="$temporary/missing" \
  "$command_path" /usr/bin/true >"$temporary/missing.out" 2>&1; then
  echo "external-tmp must require configured storage" >&2
  exit 1
fi
grep -Fq "not configured" "$temporary/missing.out"

printf 'external tmp tests passed\n'
