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
volume_uuid="$(printf '%s-%s-%s-%s-%s' 11111111 2222 3333 4444 555555555555)"
other_uuid="$(printf '%s-%s-%s-%s-%s' aaaaaaaa bbbb cccc dddd eeeeeeeeeeee)"
marker_id="studio-a.external-worktree-storage.v2"
other_marker_id="studio-b.external-worktree-storage.v2"
openclaw_managed="$mount_point/openclaw-managed"
openclaw_pnpm_store="$mount_point/.pnpm-store/openclaw"

create_storage_children() {
  mkdir -p "$openclaw_managed" "$openclaw_pnpm_store"
  chmod 0700 "$mount_point" "$openclaw_managed" \
    "$mount_point/.pnpm-store" "$openclaw_pnpm_store"
}

mkdir -p "$mount_point"
create_storage_children

write_config() {
  local uuid="${1:-$volume_uuid}"
  cat >"$config" <<EOF
{"backing_directory_mode":"0000","case_sensitive":false,"children":{"openclaw_managed":{"cleanup_authority":"openclaw_registered_worktrees","mode":"0700","ownership":"home_owner","relative_path":"openclaw-managed"},"openclaw_pnpm_store":{"cleanup_authority":"dotfiles-private","disposable":true,"mode":"0700","ownership":"home_owner","relative_path":".pnpm-store/openclaw"}},"device_location":"External","encrypted":true,"filesystem":"apfs","marker_id":"$marker_id","minimum_free_gib":200,"minimum_free_percent":10,"mount_point":"$mount_point","owners":true,"required":true,"schema_version":"external-worktree-storage.v2","spotlight":"disabled","time_machine_excluded":true,"volume_uuid":"$uuid"}
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
  local free_gib="${7:-1000}"
  local free_percent="${8:-50}"
  cat >"$observed" <<EOF
{"case_sensitive":false,"device":200,"device_location":"External","encrypted":true,"filesystem":"$filesystem","free_gib":$free_gib,"free_percent":$free_percent,"marker_id":"$marker_id","mode":"$mode","mount_point":"$mount_point","mounted":$mounted,"owner_gid":$owner_gid,"owner_uid":$owner_uid,"ownership_enabled":true,"parent_device":100,"spotlight":"disabled","time_machine_excluded":true,"volume_uuid":"$uuid"}
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
write_observed "$volume_uuid" apfs true "$(id -u)" "$(id -g)" 0700
[[ "$(run_guard --print-mount-point)" == "$mount_point" ]]
run_guard --json >"$temporary/ready.json"
HOME="$home" python3 - "$guard" "$config" <<'PY'
import importlib.util
import importlib.machinery
import os
import pathlib
import sys

loader = importlib.machinery.SourceFileLoader("storage_guard", sys.argv[1])
spec = importlib.util.spec_from_loader("storage_guard", loader)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)
assert str(module.DEFAULT_CONFIG) == (
    "/Library/Application Support/agent-worktree-ops/"
    "external-worktree-storage.json"
)
assert module.SCHEMA == "external-worktree-storage.v2"
assert module.EXPECTED_CHILDREN == {
    "openclaw_managed": {
        "relative_path": "openclaw-managed",
        "ownership": "home_owner",
        "mode": "0700",
        "cleanup_authority": "openclaw_registered_worktrees",
    },
    "openclaw_pnpm_store": {
        "relative_path": ".pnpm-store/openclaw",
        "ownership": "home_owner",
        "mode": "0700",
        "cleanup_authority": "dotfiles-private",
        "disposable": True,
    },
}
source = pathlib.Path(sys.argv[1]).read_text()
assert "com.apple.metadata:com_apple_backup_excludeItem" not in source
assert '"/usr/bin/tmutil", "isexcluded"' in source
assert module.normalized_device_location(
    {
        "BusProtocol": "USB",
        "Internal": False,
        "RemovableMediaOrExternalDevice": True,
    }
) == "External"
assert module.normalized_device_location(
    {
        "BusProtocol": "USB",
        "Internal": True,
        "RemovableMediaOrExternalDevice": False,
    }
) is None
assert module.normalized_device_location(
    {
        "BusProtocol": "Disk Image",
        "Internal": False,
        "RemovableMediaOrExternalDevice": True,
    }
) is None
assert module.normalized_device_location(
    {
        "Internal": False,
        "RemovableMediaOrExternalDevice": True,
    }
) is None
assert module.normalized_device_location({}) is None
assert module.normalized_encryption({"Encryption": True}) is True
assert module.normalized_encryption({"Encryption": False, "Encrypted": True}) is False
assert module.normalized_encryption({"Encrypted": True}) is True
assert module.normalized_encryption({}) is None
if os.getuid() != 0:
    try:
        module.load_config(pathlib.Path(sys.argv[2]), production_default=True)
    except module.GuardError as error:
        assert "wrong owner" in str(error)
    else:
        raise AssertionError("production config must be root-owned")
PY
python3 - "$temporary/ready.json" <<'PY'
import json
import pathlib
import sys

value = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert value["configured"] is True
assert value["schema_version"] == "external-worktree-storage.v2"
assert value["children"] == {
    "openclaw_managed": {
        "cleanup_authority": "openclaw_registered_worktrees",
        "mode": "0700",
        "ownership": "home_owner",
        "relative_path": "openclaw-managed",
    },
    "openclaw_pnpm_store": {
        "cleanup_authority": "dotfiles-private",
        "disposable": True,
        "mode": "0700",
        "ownership": "home_owner",
        "relative_path": ".pnpm-store/openclaw",
    },
}
PY
if HOME="$home" WORKTREE_STORAGE_CONFIG="$config" "$guard" \
  >"$temporary/override.out" 2>&1; then
  echo "runtime config overrides must be test-only" >&2
  exit 1
fi
grep -Fq "override requires guard test mode" "$temporary/override.out"

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

assert_invalid_config() {
  local name="$1"
  local expected="$2"
  if run_guard >"$temporary/$name.out" 2>&1; then
    echo "$name config must fail" >&2
    exit 1
  fi
  grep -Fq "$expected" "$temporary/$name.out"
}

mutate_config() {
  local expression="$1"
  python3 - "$config" "$expression" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
exec(sys.argv[2], {"value": value})
path.write_text(json.dumps(value, sort_keys=True) + "\n")
PY
}

write_config
mutate_config 'value["schema_version"] = "external-worktree-storage.v1"'
assert_invalid_config v1-schema "required config missing or invalid"

write_config
mutate_config 'value.pop("required")'
assert_invalid_config missing-required "required config missing or invalid"

write_config
mutate_config 'value["unknown"] = True'
assert_invalid_config unknown-field "required config missing or invalid"

write_config
mutate_config 'value.pop("children")'
assert_invalid_config missing-children "required config missing or invalid"

write_config
mutate_config 'value["children"]["openclaw_managed"]["extra"] = True'
assert_invalid_config unknown-child-field "required config missing or invalid"

write_config
mutate_config 'value["children"]["openclaw_managed"].pop("mode")'
assert_invalid_config missing-child-field "required config missing or invalid"

write_config
mutate_config 'value["children"]["openclaw_pnpm_store"]["relative_path"] = "openclaw-managed/cache"'
assert_invalid_config overlapping-child "required config missing or invalid"

write_config
mutate_config 'value["children"]["openclaw_managed"]["relative_path"] = "../escape"'
assert_invalid_config escaping-child "required config missing or invalid"

write_config

write_observed "$other_uuid" apfs true "$(id -u)" "$(id -g)" 0700
if run_guard >"$temporary/wrong-uuid.out" 2>&1; then
  echo "wrong UUID must fail" >&2
  exit 1
fi
grep -Fq "UUID does not match policy" "$temporary/wrong-uuid.out"

write_observed "$volume_uuid" exfat true "$(id -u)" "$(id -g)" 0700
if run_guard >"$temporary/exfat.out" 2>&1; then
  echo "ExFAT must fail" >&2
  exit 1
fi
grep -Fq "not APFS" "$temporary/exfat.out"

write_observed "$volume_uuid" apfs true "$(id -u)" "$(id -g)" 0700 199 50
if run_guard >"$temporary/low-gib.out" 2>&1; then
  echo "minimum_free_gib must fail before mutation" >&2
  exit 1
fi
grep -Fq "minimum_free_gib" "$temporary/low-gib.out"

write_observed "$volume_uuid" apfs true "$(id -u)" "$(id -g)" 0700 1000 9
if run_guard >"$temporary/low-percent.out" 2>&1; then
  echo "minimum_free_percent must fail before mutation" >&2
  exit 1
fi
grep -Fq "minimum_free_percent" "$temporary/low-percent.out"

write_observed "$volume_uuid" apfs true "$(id -u)" "$(id -g)" 0700
python3 - "$observed" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["device_location"] = "Internal"
path.write_text(json.dumps(value))
PY
if run_guard >"$temporary/internal-device.out" 2>&1; then
  echo "internal device location must fail" >&2
  exit 1
fi
grep -Fq "not an external device" "$temporary/internal-device.out"

write_observed "$volume_uuid" apfs true "$(id -u)" "$(id -g)" 0700
python3 - "$observed" "$other_marker_id" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["marker_id"] = sys.argv[2]
path.write_text(json.dumps(value))
PY
if run_guard >"$temporary/wrong-marker.out" 2>&1; then
  echo "wrong host marker must fail" >&2
  exit 1
fi
grep -Fq "marker does not match" "$temporary/wrong-marker.out"

write_observed "$volume_uuid" apfs true "$(id -u)" "$(id -g)" 0700
python3 - "$observed" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["spotlight"] = "enabled_or_unknown"
path.write_text(json.dumps(value))
PY
if run_guard >"$temporary/spotlight.out" 2>&1; then
  echo "enabled Spotlight must fail" >&2
  exit 1
fi
grep -Fq "Spotlight indexing is not disabled" "$temporary/spotlight.out"

write_observed "$volume_uuid" apfs true "$(id -u)" "$(id -g)" 0700
python3 - "$observed" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["time_machine_excluded"] = False
path.write_text(json.dumps(value))
PY
if run_guard >"$temporary/time-machine.out" 2>&1; then
  echo "missing persistent Time Machine exclusion must fail" >&2
  exit 1
fi
grep -Fq "persistent Time Machine exclusion" "$temporary/time-machine.out"

wheel_gid="$(python3 - <<'PY'
import grp
try:
    print(grp.getgrnam("wheel").gr_gid)
except KeyError:
    print(0)
PY
)"
write_observed "$volume_uuid" apfs false 0 "$wheel_gid" 0000
if run_guard >"$temporary/absent.out" 2>&1; then
  echo "absent mount must fail" >&2
  exit 1
fi
grep -Fq "not mounted" "$temporary/absent.out"

write_observed "$volume_uuid" apfs false "$(id -u)" "$(id -g)" 0700
if run_guard >"$temporary/fallback.out" 2>&1; then
  echo "writable fallback must fail" >&2
  exit 1
fi
grep -Fq "fallback is writable" "$temporary/fallback.out"

write_observed "$volume_uuid" apfs true "$(id -u)" "$(id -g)" 0700
run_guard
write_observed "$volume_uuid" apfs false 0 "$wheel_gid" 0000
if run_guard >"$temporary/hot-disappearance.out" 2>&1; then
  echo "hot disappearance must fail closed" >&2
  exit 1
fi
grep -Fq "not mounted" "$temporary/hot-disappearance.out"

rm -rf "$mount_point"
ln -s /Volumes/ExternalWorktrees "$mount_point"
write_observed "$volume_uuid" apfs true "$(id -u)" "$(id -g)" 0700
if run_guard >"$temporary/symlink.out" 2>&1; then
  echo "symlinked canonical mount point must fail" >&2
  exit 1
fi
grep -Fq "symlinked component" "$temporary/symlink.out"

rm "$mount_point"
mkdir "$mount_point"
create_storage_children
write_config pending
if run_guard >"$temporary/pending.out" 2>&1; then
  echo "pending UUID must fail" >&2
  exit 1
fi
grep -Fq "required config missing or invalid" "$temporary/pending.out"

write_config
chmod 0666 "$config"
write_observed "$volume_uuid" apfs false "$(id -u)" "$(id -g)" 0700
if run_guard >"$temporary/writable-config.out" 2>&1; then
  echo "writable runtime config must fail" >&2
  exit 1
fi
grep -Fq "permissions are unsafe" "$temporary/writable-config.out"
chmod 0600 "$config"

write_config
write_observed "$volume_uuid" apfs true "$(id -u)" "$(id -g)" 0700
rm -rf "$openclaw_managed"
if run_guard >"$temporary/missing-child.out" 2>&1; then
  echo "missing storage child must fail" >&2
  exit 1
fi
grep -Fq "required storage child is missing: openclaw_managed" \
  "$temporary/missing-child.out"
mkdir "$openclaw_managed"
chmod 0700 "$openclaw_managed"

chmod 0755 "$openclaw_managed"
if run_guard >"$temporary/child-mode.out" 2>&1; then
  echo "wrong storage child mode must fail" >&2
  exit 1
fi
grep -Fq "storage child must use mode 0700: openclaw_managed" \
  "$temporary/child-mode.out"
chmod 0700 "$openclaw_managed"

rm -rf "$openclaw_managed"
printf 'not a directory\n' >"$openclaw_managed"
if run_guard >"$temporary/child-file.out" 2>&1; then
  echo "storage child file must fail" >&2
  exit 1
fi
grep -Fq "storage child is not a real directory: openclaw_managed" \
  "$temporary/child-file.out"
rm "$openclaw_managed"
mkdir "$openclaw_managed"
chmod 0700 "$openclaw_managed"

rm -rf "$openclaw_managed"
ln -s "$openclaw_pnpm_store" "$openclaw_managed"
if run_guard >"$temporary/child-symlink.out" 2>&1; then
  echo "symlinked storage child must fail" >&2
  exit 1
fi
grep -Fq "storage child has a symlinked component: openclaw_managed" \
  "$temporary/child-symlink.out"
rm "$openclaw_managed"
mkdir "$openclaw_managed"
chmod 0700 "$openclaw_managed"

HOME="$home" python3 - "$guard" "$config" <<'PY'
import importlib.machinery
import importlib.util
import os
import pathlib
import sys
from unittest import mock

loader = importlib.machinery.SourceFileLoader("storage_guard_metadata", sys.argv[1])
spec = importlib.util.spec_from_loader("storage_guard_metadata", loader)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)
config = module.load_config(pathlib.Path(sys.argv[2]), production_default=False)
assert config is not None
root = pathlib.Path(config["mount_point"])
child = root / "openclaw-managed"
real_lstat = module.os.lstat

def changed_lstat(path, *, uid_delta=0, gid_delta=0, device_delta=0):
    metadata = real_lstat(path)
    if pathlib.Path(path) != child:
        return metadata
    values = list(metadata)
    values[2] = metadata.st_dev + device_delta
    values[4] = metadata.st_uid + uid_delta
    values[5] = metadata.st_gid + gid_delta
    return os.stat_result(values)

for error, kwargs in (
    ("wrong owner", {"uid_delta": 1}),
    ("wrong owner", {"gid_delta": 1}),
    ("crosses filesystems", {"device_delta": 1}),
):
    with mock.patch.object(
        module.os, "lstat", side_effect=lambda path, kwargs=kwargs: changed_lstat(path, **kwargs)
    ):
        try:
            module.validate_storage_children(config, root)
        except module.GuardError as caught:
            assert error in str(caught), caught
        else:
            raise AssertionError(error)
PY

write_observed "$volume_uuid" apfs false "$(id -u)" "$(id -g)" 0700
real_config="$temporary/real-config.json"
mv "$config" "$real_config"
ln -s "$real_config" "$config"
if run_guard >"$temporary/symlink-config.out" 2>&1; then
  echo "symlinked runtime config must fail" >&2
  exit 1
fi
grep -Fq "symlinked component" "$temporary/symlink-config.out"
rm "$config"

write_observed "$volume_uuid" apfs false 0 "$wheel_gid" 0000
if run_guard >"$temporary/missing-sealed.out" 2>&1; then
  echo "missing config with sealed fallback must fail" >&2
  exit 1
fi
grep -Fq "required config missing" "$temporary/missing-sealed.out"

write_observed "$volume_uuid" apfs true "$(id -u)" "$(id -g)" 0700
if run_guard >"$temporary/missing-mounted.out" 2>&1; then
  echo "missing config with a direct mount must fail" >&2
  exit 1
fi
grep -Fq "required config missing" "$temporary/missing-mounted.out"

write_observed "$volume_uuid" apfs false "$(id -u)" "$(id -g)" 0700
run_guard
if run_guard --require-config \
  >"$temporary/unconfigured.out" 2>&1; then
  echo "required unconfigured storage must fail" >&2
  exit 1
fi
grep -Fq "required config missing" "$temporary/unconfigured.out"

printf 'worktree storage tests passed\n'
