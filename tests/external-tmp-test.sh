#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command_path="$root/bin/external-tmp"
temporary="$(mktemp -d)"
temporary="$(cd "$temporary" && pwd -P)"
trap 'rm -rf "$temporary"' EXIT

home="$temporary/home"
mount_point="$home/.codex/worktrees"
fake_guard="$temporary/fake-guard"
guard_state="$temporary/guard-state"
result="$temporary/result"
child_marker="$temporary/child-ran"
mkdir -p "$mount_point"
chmod 0700 "$home" "$home/.codex" "$mount_point"

write_guard() {
  cat >"$fake_guard" <<'EOF'
#!/usr/bin/env python3
import json
import os
import pathlib
import sys

mount = pathlib.Path(os.environ["TEST_MOUNT_POINT"])
state_path = pathlib.Path(os.environ["TEST_GUARD_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {}
count = int(state.get("count", 0)) + 1
state["count"] = count
state_path.write_text(json.dumps(state))
scenario = os.environ.get("TEST_GUARD_SCENARIO", "ready")
if scenario == "malformed":
    print("{")
    raise SystemExit(0)
if scenario == "unconfigured":
    print(json.dumps({
        "configured": False,
        "schema_version": "external-worktree-storage.v2",
    }, sort_keys=True))
    raise SystemExit(0)
if scenario == "fail-second" and count >= 2:
    print("simulated hot disappearance", file=sys.stderr)
    raise SystemExit(78)
if scenario == "replace-mount-second" and count == 2:
    moved = pathlib.Path(str(mount) + ".moved")
    mount.rename(moved)
    mount.mkdir(mode=0o700)
    state["moved"] = str(moved)
    state_path.write_text(json.dumps(state))
attestation = {
    "configured": True,
    "mount_point": str(mount),
    "schema_version": "external-worktree-storage.v2",
}
print(json.dumps(attestation, sort_keys=True))
EOF
  chmod 0755 "$fake_guard"
}

reset_fixture() {
  rm -f "$guard_state" "$child_marker" "$result"
  rm -rf "$mount_point" "$mount_point.moved"
  mkdir -p "$mount_point"
  chmod 0700 "$mount_point"
}

run_external_tmp() {
  HOME="$home" \
    WORKTREE_STORAGE_GUARD="$fake_guard" \
    TEST_MOUNT_POINT="$mount_point" \
    TEST_GUARD_STATE="$guard_state" \
    "$command_path" "$@"
}

write_guard

check_json="$(run_external_tmp --check --json)"
[[ "$check_json" == \
'{"configured":true,"mount_point":"'"$mount_point"'","ready":true,"schema_version":"external-tmp-check.v1","scratch_namespace":".scratch/tmp","storage_schema":"external-worktree-storage.v2","tmpdir_scope":"invocation"}' ]]
[[ ! -e "$mount_point/.scratch" ]]

if run_external_tmp --check >"$temporary/check-args.out" 2>&1; then
  echo "check without JSON must fail" >&2
  exit 1
fi
grep -Fq -- "--check requires --json" "$temporary/check-args.out"

reset_fixture
TEST_GUARD_SCENARIO=unconfigured run_external_tmp --check --json \
  >"$temporary/unconfigured.json"
[[ "$(cat "$temporary/unconfigured.json")" == \
'{"configured":false,"mount_point":null,"ready":false,"schema_version":"external-tmp-check.v1","scratch_namespace":".scratch/tmp","storage_schema":"external-worktree-storage.v2","tmpdir_scope":"invocation"}' ]]
[[ ! -e "$mount_point/.scratch" ]]

reset_fixture
cat >"$temporary/probe.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n%s\n%s\n' "$TMPDIR" "$TMP" "$TEMP" >"$1"
mkdir "$TMPDIR/nested"
touch "$TMPDIR/child-created" "$TMPDIR/nested/child-created"
ln -s / "$TMPDIR/nested/root-link"
EOF
chmod 0755 "$temporary/probe.sh"
run_external_tmp "$temporary/probe.sh" "$result"
tmpdir_path="$(sed -n '1p' "$result")"
tmp_path="$(sed -n '2p' "$result")"
temp_path="$(sed -n '3p' "$result")"
[[ "$tmpdir_path" == "$mount_point/.scratch/tmp/"external-tmp.*"/" ]]
[[ "$tmp_path" == "${tmpdir_path%/}" ]]
[[ "$temp_path" == "${tmpdir_path%/}" ]]
[[ -z "$(find "$mount_point/.scratch/tmp" -mindepth 1 -print -quit)" ]]
[[ "$(stat -f '%u %Lp' "$mount_point/.scratch")" == "$(id -u) 700" ]]
[[ "$(stat -f '%u %Lp' "$mount_point/.scratch/tmp")" == "$(id -u) 700" ]]

reset_fixture
mkdir "$mount_point/.scratch"
chmod 0755 "$mount_point/.scratch"
if run_external_tmp /usr/bin/touch "$child_marker" \
  >"$temporary/mode.out" 2>&1; then
  echo "mode drift must fail" >&2
  exit 1
fi
[[ ! -e "$child_marker" ]]
grep -Fq "scratch namespace must use mode 0700" "$temporary/mode.out"

reset_fixture
ln -s "$temporary" "$mount_point/.scratch"
if run_external_tmp /usr/bin/touch "$child_marker" \
  >"$temporary/symlink.out" 2>&1; then
  echo "symlinked scratch namespace must fail" >&2
  exit 1
fi
[[ ! -e "$child_marker" ]]
grep -Fq "scratch namespace is not a real directory" "$temporary/symlink.out"

reset_fixture
mv "$home/.codex" "$home/.codex-real"
ln -s .codex-real "$home/.codex"
if run_external_tmp /usr/bin/touch "$child_marker" \
  >"$temporary/component-symlink.out" 2>&1; then
  echo "symlinked storage component must fail" >&2
  exit 1
fi
[[ ! -e "$child_marker" ]]
grep -Fq "symlinked component" "$temporary/component-symlink.out"
rm "$home/.codex"
mv "$home/.codex-real" "$home/.codex"

reset_fixture
if TEST_GUARD_SCENARIO=fail-second run_external_tmp \
  /usr/bin/touch "$child_marker" >"$temporary/hot.out" 2>&1; then
  echo "hot guard failure must stop before child execution" >&2
  exit 1
fi
[[ ! -e "$child_marker" ]]
grep -Fq "simulated hot disappearance" "$temporary/hot.out"

reset_fixture
if TEST_GUARD_SCENARIO=replace-mount-second run_external_tmp \
  /usr/bin/touch "$child_marker" >"$temporary/identity.out" 2>&1; then
  echo "mount identity drift must stop before child execution" >&2
  exit 1
fi
[[ ! -e "$child_marker" ]]
grep -Fq "configured storage identity changed" "$temporary/identity.out"

reset_fixture
cat >"$temporary/replace-scratch.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
path="${TMPDIR%/}"
printf '%s\n' "$path" >"$1"
mv "$path" "$path.moved"
mkdir -m 0700 "$path"
touch "$path/replacement"
EOF
chmod 0755 "$temporary/replace-scratch.sh"
run_external_tmp "$temporary/replace-scratch.sh" "$result" \
  >"$temporary/replacement.out" 2>&1
created_path="$(cat "$result")"
[[ -f "$created_path/replacement" ]]
[[ -d "$created_path.moved" ]]
grep -Fq "invocation scratch identity changed" "$temporary/replacement.out"

reset_fixture
cat >"$temporary/status.sh" <<'EOF'
#!/usr/bin/env bash
exit 37
EOF
chmod 0755 "$temporary/status.sh"
set +e
run_external_tmp "$temporary/status.sh"
status=$?
set -e
[[ "$status" -eq 37 ]]
[[ -z "$(find "$mount_point/.scratch/tmp" -mindepth 1 -print -quit)" ]]

reset_fixture
cat >"$temporary/signal-child.py" <<'PY'
#!/usr/bin/env python3
import os
import pathlib
import signal
import sys

ready, received = map(pathlib.Path, sys.argv[1:])

def stop(signum, _frame):
    received.write_text(str(signum))
    raise SystemExit(128 + signum)

for watched in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
    signal.signal(watched, stop)
ready.write_text(f"{os.getpid()} {os.environ['TMPDIR']}")
while True:
    signal.pause()
PY
chmod 0755 "$temporary/signal-child.py"

for signal_case in HUP:1 INT:2 TERM:15; do
  signal_name="${signal_case%%:*}"
  signal_number="${signal_case##*:}"
  ready="$temporary/$signal_name.ready"
  received="$temporary/$signal_name.received"
  reset_fixture
  rm -f "$ready" "$received"
  HOME="$home" \
    WORKTREE_STORAGE_GUARD="$fake_guard" \
    TEST_MOUNT_POINT="$mount_point" \
    TEST_GUARD_STATE="$guard_state" \
    "$command_path" "$temporary/signal-child.py" "$ready" "$received" &
  wrapper_pid=$!
  for _ in {1..100}; do
    [[ -f "$ready" ]] && break
    sleep 0.02
  done
  [[ -f "$ready" ]]
  child_pid="$(cut -d' ' -f1 "$ready")"
  child_tmpdir="$(cut -d' ' -f2- "$ready")"
  kill -s "$signal_name" "$wrapper_pid"
  set +e
  wait "$wrapper_pid"
  status=$?
  set -e
  [[ "$status" -eq $((128 + signal_number)) ]]
  if [[ ! -f "$received" ]]; then
    echo "child did not receive $signal_name" >&2
    exit 1
  fi
  [[ "$(cat "$received")" == "$signal_number" ]]
  if kill -0 "$child_pid" 2>/dev/null; then
    echo "forwarded child was not reaped for $signal_name" >&2
    exit 1
  fi
  [[ ! -e "${child_tmpdir%/}" ]]
  [[ -z "$(find "$mount_point/.scratch/tmp" -mindepth 1 -print -quit)" ]]
done

reset_fixture
cat >"$temporary/natural-signal.py" <<'PY'
#!/usr/bin/env python3
import os
import signal
os.kill(os.getpid(), signal.SIGTERM)
PY
chmod 0755 "$temporary/natural-signal.py"
set +e
run_external_tmp "$temporary/natural-signal.py"
status=$?
set -e
[[ "$status" -eq 143 ]]
[[ -z "$(find "$mount_point/.scratch/tmp" -mindepth 1 -print -quit)" ]]

reset_fixture
if TEST_GUARD_SCENARIO=malformed run_external_tmp --check --json \
  >"$temporary/malformed.out" 2>&1; then
  echo "malformed guard output must fail" >&2
  exit 1
fi
grep -Fq "malformed JSON" "$temporary/malformed.out"

python3 - "$command_path" <<'PY'
import importlib.machinery
import importlib.util
import os
import stat
import sys
from unittest import mock

loader = importlib.machinery.SourceFileLoader("external_tmp", sys.argv[1])
spec = importlib.util.spec_from_loader("external_tmp", loader)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
assert spec.loader
spec.loader.exec_module(module)

metadata = os.stat(".")
cross_device = list(metadata)
cross_device[0] = (metadata.st_mode & ~0o777) | 0o700
cross_device[2] = metadata.st_dev + 1
cross_device[4] = os.getuid()
with mock.patch.object(module.os, "stat", return_value=os.stat_result(cross_device)):
    try:
        module.open_child(
            1,
            "fixture",
            "fixture",
            metadata.st_dev,
            False,
        )
    except module.ExternalTmpError as error:
        assert "crosses filesystems" in str(error)
    else:
        raise AssertionError("cross-device namespace must fail")

wrong_owner = list(metadata)
wrong_owner[0] = (metadata.st_mode & ~0o777) | 0o700
wrong_owner[4] = os.getuid() + 1
try:
    module.directory_identity(
        os.stat_result(wrong_owner),
        "fixture",
    )
except module.ExternalTmpError as error:
    assert "wrong owner" in str(error)
else:
    raise AssertionError("wrong ownership must fail")

assert stat.S_IMODE(metadata.st_mode) != 0
PY

printf 'external tmp tests passed\n'
