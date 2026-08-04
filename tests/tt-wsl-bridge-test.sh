#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="/tmp/tt-wsl-bridge-test-$$"
runtime="$temporary/run/WSL"
croot="$temporary/mnt/c"
lockroot="$temporary/run/lock"
fakebin="$temporary/fakebin"
socket="$runtime/4242_interop"
socket_pid=""

cleanup() {
  if [[ -n "$socket_pid" ]]; then
    kill "$socket_pid" 2>/dev/null || true
    wait "$socket_pid" 2>/dev/null || true
  fi
  rm -rf "$temporary"
}
trap cleanup EXIT

mkdir -p "$runtime" "$lockroot" "$fakebin"
sed \
  -e "s#/run/WSL#$runtime#g" \
  -e "s#/mnt/c#$croot#g" \
  -e "s#/run/lock#$lockroot#g" \
  "$repo/bin/tt" >"$temporary/tt"
sed \
  -e "s#/run/WSL#$runtime#g" \
  -e "s#/mnt/c#$croot#g" \
  "$repo/bin/powershell.exe" >"$temporary/powershell.exe"
sed \
  -e "s#/run/WSL#$runtime#g" \
  -e "s#/mnt/c#$croot#g" \
  "$repo/bin/pwsh.exe" >"$temporary/pwsh.exe"
chmod +x "$temporary/tt" "$temporary/powershell.exe" "$temporary/pwsh.exe"

python3 - "$socket" <<'PY' &
import os
import socket
import sys
import time

path = sys.argv[1]
os.makedirs(os.path.dirname(path), exist_ok=True)
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(path)
server.listen(1)
while True:
    time.sleep(60)
PY
socket_pid="$!"
for _ in 1 2 3 4 5; do
  [[ -S "$socket" ]] && break
  sleep 0.1
done
[[ -S "$socket" ]]

cat >"$fakebin/uname" <<'SH'
#!/usr/bin/env bash
[[ "${1:-}" == "-r" ]] && printf '6.6.87.2-microsoft-standard-WSL2\n'
SH

cat >"$fakebin/id" <<'SH'
#!/usr/bin/env bash
case "${1:-}" in
  -u) printf '1000\n' ;;
  -g) printf '1000\n' ;;
  *) exit 2 ;;
esac
SH

cat >"$fakebin/findmnt" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
state="${TT_TEST_MOUNT_STATE:?}"
value="$(cat "$state" 2>/dev/null || printf absent)"
[[ "$value" != "absent" ]] || exit 1
if [[ "$*" == *"-o SOURCE,FSTYPE,OPTIONS"* ]]; then
  case "$value" in
    valid) printf 'C: drvfs ro,uid=1000,gid=1000,umask=022,fmask=011\n' ;;
    valid9p) printf 'C:\\134 9p ro,aname=drvfs;path=C:\\;uid=1000\n' ;;
    wrong-source) printf 'D: drvfs ro,uid=1000,gid=1000\n' ;;
    writable) printf 'C: drvfs rw,uid=1000,gid=1000\n' ;;
    *) printf '%s\n' "$value" ;;
  esac
fi
SH

cat >"$fakebin/mount" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${TT_TEST_MOUNT_LOG:?}"
printf 'valid\n' >"${TT_TEST_MOUNT_STATE:?}"
SH

cat >"$fakebin/sudo" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${TT_TEST_SUDO_LOG:?}"
[[ "${1:-}" == "-n" ]] && shift
exec "$@"
SH

cat >"$fakebin/flock" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "-x" ]] && shift
shift
exec "$@"
SH

cat >"$fakebin/tmux" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${TT_TEST_TMUX_LOG:?}"
if [[ "$*" == "show-option -gqv @tt_wsl_interop" ]]; then
  printf '%s\n' "${TT_TEST_STORED_SOCKET:-}"
fi
SH
chmod +x "$fakebin"/*

state="$temporary/mount.state"
mount_log="$temporary/mount.log"
sudo_log="$temporary/sudo.log"
tmux_log="$temporary/tmux.log"
export PATH="$fakebin:$PATH"
export TT_TMUX_BIN="$fakebin/tmux"
export TT_TEST_MOUNT_STATE="$state"
export TT_TEST_MOUNT_LOG="$mount_log"
export TT_TEST_SUDO_LOG="$sudo_log"
export TT_TEST_TMUX_LOG="$tmux_log"
export WSL_DISTRO_NAME=Ubuntu

expect_failure() {
  if "$@" >/dev/null 2>&1; then
    printf 'expected command to fail: %s\n' "$*" >&2
    exit 1
  fi
}

: >"$mount_log"
: >"$sudo_log"
: >"$tmux_log"
printf 'absent\n' >"$state"
expect_failure env -u WSL_DISTRO_NAME WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
expect_failure env -u WSL_INTEROP "$temporary/tt" wsl-bridge ensure
expect_failure env WSL_INTEROP="$runtime/9999_interop" "$temporary/tt" wsl-bridge ensure

rm -rf "$croot"
: >"$mount_log"
: >"$sudo_log"
: >"$tmux_log"
printf 'absent\n' >"$state"
WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
grep -Fq -- "-x $lockroot/tt-wsl-bridge.lock bash -c" "$sudo_log"
grep -Fqx -- "-t drvfs -o ro,uid=1000,gid=1000,umask=022,fmask=011 C: $croot" "$mount_log"
grep -Fqx -- "set-option -gq @tt_wsl_interop $socket" "$tmux_log"
if grep -Eq 'set-environment.*(WSL_INTEROP|WSLENV)' "$tmux_log"; then
  printf 'bridge wrote forbidden tmux global environment\n' >&2
  exit 1
fi

for bad_state in wrong-source writable; do
  : >"$tmux_log"
  printf '%s\n' "$bad_state" >"$state"
  expect_failure env WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
  if grep -Fq '@tt_wsl_interop' "$tmux_log"; then
    printf 'bridge stored socket after invalid mount: %s\n' "$bad_state" >&2
    exit 1
  fi
done

printf 'valid\n' >"$state"
for command in ls status sync; do
  : >"$sudo_log"
  WSL_INTEROP="$socket" "$temporary/tt" "$command" >/dev/null 2>&1 || true
  [[ ! -s "$sudo_log" ]] || {
    printf 'read-only tt command invoked wsl bridge: %s\n' "$command" >&2
    exit 1
  }
done

mkdir -p "$croot/Windows/System32/WindowsPowerShell/v1.0"
cat >"$croot/Windows/System32/WindowsPowerShell/v1.0/powershell.exe" <<'SH'
#!/usr/bin/env bash
printf 'interop=%s\n' "${WSL_INTEROP:-}" >"${TT_TEST_EXE_LOG:?}"
printf '<%s>\n' "$@" >>"${TT_TEST_EXE_LOG:?}"
exit "${TT_TEST_EXE_STATUS:-0}"
SH
chmod +x "$croot/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

exe_log="$temporary/exe.log"
export TT_TEST_EXE_LOG="$exe_log"
export TT_TEST_STORED_SOCKET="$socket"
export TT_TEST_EXE_STATUS=37
: >"$tmux_log"
if WSL_INTEROP="$socket" "$temporary/powershell.exe" "two words" '*' ';'; then
  printf 'powershell wrapper lost child exit status\n' >&2
  exit 1
else
  status="$?"
fi
[[ "$status" == "37" ]]
grep -Fqx "interop=$socket" "$exe_log"
grep -Fqx '<two words>' "$exe_log"
grep -Fqx '<*>' "$exe_log"
grep -Fqx '<;>' "$exe_log"
if grep -Fq 'show-option' "$tmux_log"; then
  printf 'powershell wrapper ignored live current socket\n' >&2
  exit 1
fi

export TT_TEST_EXE_STATUS=0
unset WSL_INTEROP
"$temporary/powershell.exe" fallback
grep -Fqx "interop=$socket" "$exe_log"
grep -Fqx '<fallback>' "$exe_log"

rm -rf "$croot/Program Files"
mkdir -p "$croot/Program Files/WindowsApps/Microsoft.PowerShell_7.6.4.0_arm64__8wekyb3d8bbwe"
cp "$croot/Windows/System32/WindowsPowerShell/v1.0/powershell.exe" \
  "$croot/Program Files/WindowsApps/Microsoft.PowerShell_7.6.4.0_arm64__8wekyb3d8bbwe/pwsh.exe"
chmod +x "$croot/Program Files/WindowsApps/Microsoft.PowerShell_7.6.4.0_arm64__8wekyb3d8bbwe/pwsh.exe"
"$temporary/pwsh.exe" appx
grep -Fqx '<appx>' "$exe_log"

mkdir -p "$croot/Program Files/WindowsApps/Microsoft.PowerShell_7.7.0.0_arm64__8wekyb3d8bbwe"
cp "$croot/Windows/System32/WindowsPowerShell/v1.0/powershell.exe" \
  "$croot/Program Files/WindowsApps/Microsoft.PowerShell_7.7.0.0_arm64__8wekyb3d8bbwe/pwsh.exe"
chmod +x "$croot/Program Files/WindowsApps/Microsoft.PowerShell_7.7.0.0_arm64__8wekyb3d8bbwe/pwsh.exe"
expect_failure "$temporary/pwsh.exe" ambiguous

printf 'writable\n' >"$state"
expect_failure "$temporary/powershell.exe" bad-mount

printf 'tt_wsl_bridge_test=passed\n'
