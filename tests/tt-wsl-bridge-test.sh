#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="/tmp/tt-wsl-bridge-test-$$"
runtime="$temporary/run/WSL"
croot="$temporary/mnt/c"
lockroot="$temporary/run/lock"
markerroot="$temporary/run/tt-wsl-bridge"
procroot="$temporary/proc"
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

mkdir -p "$runtime" "$lockroot" "$markerroot" "$procroot" "$fakebin"
sed \
  -e "s#/run/WSL#$runtime#g" \
  -e "s#/mnt/c#$croot#g" \
  -e "s#/run/lock#$lockroot#g" \
  -e "s#/run/tt-wsl-bridge#$markerroot#g" \
  -e "s#/proc#$procroot#g" \
  -e 's/local pid="[$][$]"/local pid="1234"/' \
  -e "s#/usr/bin/flock#$fakebin/flock#g" \
  -e "s#/usr/bin/nsenter#$fakebin/nsenter#g" \
  -e "s#/usr/bin/findmnt#$fakebin/findmnt#g" \
  -e "s#/usr/bin/mount#$fakebin/mount#g" \
  -e "s#/usr/bin/umount#$fakebin/umount#g" \
  -e "s#/usr/bin/stat#$fakebin/stat#g" \
  -e "s#/usr/bin/readlink#$fakebin/readlink#g" \
  -e "s#/usr/bin/setpriv#$fakebin/setpriv#g" \
  -e "s#/usr/bin/bash#/bin/bash#g" \
  -e "s#/usr/bin/mkdir#/bin/mkdir#g" \
  -e "s#/usr/bin/rm#/bin/rm#g" \
  -e "s#/usr/bin/chmod#/bin/chmod#g" \
  -e "s#/usr/bin/mv#$fakebin/mv#g" \
  "$repo/bin/tt" >"$temporary/tt"
sed \
  -e "s#/run/WSL#$runtime#g" \
  -e "s#/mnt/c#$croot#g" \
  "$repo/bin/powershell.exe" >"$temporary/powershell.exe"
sed \
  -e "s#/run/WSL#$runtime#g" \
  -e "s#/mnt/c#$croot#g" \
  -e "s#exec /init#exec $fakebin/init#g" \
  -e "s#stat -c %u /init#stat -c %u $fakebin/init#g" \
  -e "s#-x /init#-x $fakebin/init#g" \
  -e "s#! -L /init#! -L $fakebin/init#g" \
  "$repo/bin/pwsh.exe" >"$temporary/pwsh.exe"
chmod +x "$temporary/tt" "$temporary/powershell.exe" "$temporary/pwsh.exe"

make_proc() {
  local pid="$1"
  local comm="$2"
  local starttime="$3"
  local namespace="$4"
  local exe="$5"

  mkdir -p "$procroot/$pid/ns"
  printf '%s\n' "$comm" >"$procroot/$pid/comm"
  printf '%s (%s) S' "$pid" "$comm" >"$procroot/$pid/stat"
  for _ in {1..18}; do
    printf ' 0' >>"$procroot/$pid/stat"
  done
  printf ' %s 0\n' "$starttime" >>"$procroot/$pid/stat"
  ln -s "$namespace" "$procroot/$pid/ns/mnt"
  ln -s "$exe" "$procroot/$pid/exe"
}

write_proc_stat() {
  local pid="$1"
  local comm="$2"
  local starttime="$3"

  printf '%s (%s) S' "$pid" "$comm" >"$procroot/$pid/stat"
  for _ in {1..18}; do
    printf ' 0' >>"$procroot/$pid/stat"
  done
  printf ' %s 0\n' "$starttime" >>"$procroot/$pid/stat"
}

file_mode() {
  if /usr/bin/stat -c %a "$1" >/dev/null 2>&1; then
    /usr/bin/stat -c %a "$1"
  else
    /usr/bin/stat -f %Lp "$1"
  fi
}

make_proc 1234 bash 111111 'mnt:[7001]' /usr/bin/bash
make_proc 2222 'tmux: server' 222222 'mnt:[7001]' "$fakebin/tmux"
make_proc 3333 bash 333333 'mnt:[7001]' /usr/bin/bash

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

cat >"$fakebin/init" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
target="$1"
shift
exec /bin/bash "$target" "$@"
SH

cat >"$fakebin/findmnt" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *"-M / -o PROPAGATION"* ]]; then
  printf '%s\n' "${TT_TEST_PROPAGATION:-private}"
  exit 0
fi
state="${TT_TEST_MOUNT_STATE:?}"
value="$(cat "$state" 2>/dev/null || printf absent)"
[[ "$value" != "absent" ]] || exit 1
if [[ "$*" == *"-o SOURCE,FSTYPE,OPTIONS"* ]]; then
  case "$value" in
    valid) printf 'C: drvfs ro,uid=1000,gid=1000,umask=022,fmask=011\n' ;;
    valid9p) printf 'C: 9p ro,aname=drvfs;path=C:;uid=1000\n' ;;
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

cat >"$fakebin/umount" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${TT_TEST_UMOUNT_LOG:?}"
printf 'absent\n' >"${TT_TEST_MOUNT_STATE:?}"
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

cat >"$fakebin/nsenter" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "-t" ]] && shift 2
[[ "${1:-}" == "-m" ]] && shift
[[ "${1:-}" == "--" ]] && shift
exec "$@"
SH

cat >"$fakebin/stat" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-c" ]]; then
  case "${2:-}" in
    %u)
      if [[ "${3:-}" == "${TT_TEST_INIT_PATH:-}" || "${3:-}" == "${TT_TEST_MARKER_ROOT:-}"* ]]; then
        printf '0\n'
      else
        printf '1000\n'
      fi
      exit 0
      ;;
    %a)
      if /usr/bin/stat -c %a "$3" >/dev/null 2>&1; then
        /usr/bin/stat -c %a "$3"
      else
        /usr/bin/stat -f %Lp "$3"
      fi
      exit 0
      ;;
    %s)
      if /usr/bin/stat -c %s "$3" >/dev/null 2>&1; then
        /usr/bin/stat -c %s "$3"
      else
        /usr/bin/stat -f %z "$3"
      fi
      exit 0
      ;;
  esac
fi
exec /usr/bin/stat "$@"
SH

cat >"$fakebin/mv" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-fT" ]]; then
  shift
fi
exec /bin/mv -f "$@"
SH

cat >"$fakebin/readlink" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-f" ]]; then
  printf '%s\n' "$2"
  exit 0
fi
exec /usr/bin/readlink "$@"
SH

cat >"$fakebin/setpriv" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --reuid|--regid)
      shift 2
      ;;
    --clear-groups)
      shift
      ;;
    *)
      exit 2
      ;;
  esac
done
exec "$@"
SH

cat >"$fakebin/tmux" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${TT_TEST_TMUX_LOG:?}"
if [[ "${1:-}" == "-S" ]]; then
  shift 2
fi
server_state="$(cat "${TT_TEST_SERVER_STATE:?}" 2>/dev/null || printf none)"
case "$*" in
  "display-message -p #{pid}")
    [[ "$server_state" == "live" ]] || exit 1
    printf '2222\n'
    ;;
  "display-message -p #{socket_path}")
    [[ "$server_state" == "live" ]] || exit 1
    printf '%s\n' "${TT_TEST_TMUX_SOCKET:?}"
    ;;
  "list-panes -a -F #{pane_id}|#{pane_pid}|#{pane_dead}")
    [[ "$server_state" == "live" ]] || exit 1
    count=0
    if [[ -n "${TT_TEST_PANE_CALLS:-}" ]]; then
      count="$(cat "$TT_TEST_PANE_CALLS" 2>/dev/null || printf 0)"
      count=$((count + 1))
      printf '%s\n' "$count" >"$TT_TEST_PANE_CALLS"
    fi
    if [[ "${TT_TEST_PANE_CHURN:-}" == "1" && "$count" -ge 3 ]]; then
      printf '%%1|3333|0\n%%9|9999|1\n'
    else
      printf '%b\n' "${TT_TEST_PANE_ROWS:-%1|3333|0}"
    fi
    ;;
  "show-option -gqv @tt_wsl_interop")
    printf '%s\n' "${TT_TEST_STORED_SOCKET:-}"
    ;;
  new-session*|"new -d "*)
    printf 'live\n' >"${TT_TEST_SERVER_STATE:?}"
    ;;
  "has-session "*)
    exit 1
    ;;
  "list-sessions")
    [[ "$server_state" == "live" ]] || exit 1
    printf 'test: 1 windows\n'
    ;;
  "kill-server")
    printf 'none\n' >"${TT_TEST_SERVER_STATE:?}"
    ;;
  "set-option -gq @tt_wsl_interop "*)
    if [[ "${TT_TEST_SET_OPTION_FAIL:-}" == "1" ]]; then
      if [[ -n "${TT_TEST_SERVER_STAT:-}" ]]; then
        sed 's/ 222222 0$/ 999999 0/' "$TT_TEST_SERVER_STAT" >"$TT_TEST_SERVER_STAT.tmp"
        /bin/mv -f "$TT_TEST_SERVER_STAT.tmp" "$TT_TEST_SERVER_STAT"
      fi
      exit 1
    fi
    ;;
esac
SH
chmod +x "$fakebin"/*

state="$temporary/mount.state"
server_state="$temporary/server.state"
mount_log="$temporary/mount.log"
umount_log="$temporary/umount.log"
sudo_log="$temporary/sudo.log"
tmux_log="$temporary/tmux.log"
export PATH="$fakebin:$PATH"
export TT_TMUX_BIN="$fakebin/tmux"
export TT_TEST_MOUNT_STATE="$state"
export TT_TEST_SERVER_STATE="$server_state"
export TT_TEST_MOUNT_LOG="$mount_log"
export TT_TEST_UMOUNT_LOG="$umount_log"
export TT_TEST_SUDO_LOG="$sudo_log"
export TT_TEST_TMUX_LOG="$tmux_log"
export TT_TEST_TMUX_SOCKET="$socket"
export TT_TEST_INIT_PATH="$fakebin/init"
export TT_TEST_MARKER_ROOT="$markerroot"
export TT_TEST_SERVER_STAT="$procroot/2222/stat"
export WSL_DISTRO_NAME=Ubuntu

expect_failure() {
  if "$@" >/dev/null 2>&1; then
    printf 'expected command to fail: %s\n' "$*" >&2
    exit 1
  fi
}

write_record() {
  local path="$1"
  local value="$2"
  local mode="${3:-600}"

  mkdir -p "$markerroot"
  printf '%s\n' "$value" >"$path"
  chmod "$mode" "$path"
}

reset_bridge_fixture() {
  rm -rf "$croot" "$markerroot" "$procroot/4444" "$procroot/5555"
  mkdir -p "$markerroot"
  chmod 700 "$markerroot"
  : >"$mount_log"
  : >"$umount_log"
  : >"$sudo_log"
  : >"$tmux_log"
  printf 'absent\n' >"$state"
  printf 'live\n' >"$server_state"
  write_proc_stat 2222 'tmux: server' 222222
}

: >"$mount_log"
: >"$umount_log"
: >"$sudo_log"
: >"$tmux_log"
printf 'absent\n' >"$state"
printf 'live\n' >"$server_state"
expect_failure env -u WSL_DISTRO_NAME WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
expect_failure env -u WSL_INTEROP "$temporary/tt" wsl-bridge ensure
expect_failure env WSL_INTEROP="$runtime/9999_interop" "$temporary/tt" wsl-bridge ensure

printf 'none\n' >"$server_state"
expect_failure env WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
[[ ! -s "$mount_log" ]]

rm -rf "$croot"
rm -rf "$markerroot"
: >"$mount_log"
: >"$umount_log"
: >"$sudo_log"
: >"$tmux_log"
printf 'absent\n' >"$state"
printf 'live\n' >"$server_state"
WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
grep -Fq -- "-x $lockroot/tt-wsl-bridge.lock /bin/bash -c" "$sudo_log"
grep -Fqx -- "-t drvfs -o ro,uid=1000,gid=1000,umask=022,fmask=011 C: $croot" "$mount_log"
grep -Fqx -- "set-option -gq @tt_wsl_interop $socket" "$tmux_log"
grep -Fqx -- "2222|222222|mnt:[7001]" "$markerroot/mount-1000"
[[ "$(file_mode "$markerroot/mount-1000")" == "600" ]]
marker_mode="$(file_mode "$markerroot")"
(( (8#$marker_mode & 8#022) == 0 ))
[[ ! -L "$markerroot" ]]
[[ -z "$(find "$markerroot" -maxdepth 1 -name '.record.*' -print -quit)" ]]
if grep -Eq 'set-environment.*(WSL_INTEROP|WSLENV)' "$tmux_log"; then
  printf 'bridge wrote forbidden tmux global environment\n' >&2
  exit 1
fi

TT_TEST_PANE_ROWS=$'%1|3333|0\n%2|4444|1' \
  WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure

for bad_state in wrong-source writable; do
  rm -rf "$markerroot"
  : >"$tmux_log"
  printf '%s\n' "$bad_state" >"$state"
  expect_failure env WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
  if grep -Fq '@tt_wsl_interop' "$tmux_log"; then
    printf 'bridge stored socket after invalid mount: %s\n' "$bad_state" >&2
    exit 1
  fi
done

rm -rf "$croot" "$markerroot"
: >"$mount_log"
printf 'absent\n' >"$state"
expect_failure env TT_TEST_PROPAGATION=shared:42 WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
[[ ! -s "$mount_log" ]]

rm "$procroot/3333/ns/mnt"
ln -s 'mnt:[7002]' "$procroot/3333/ns/mnt"
expect_failure env WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
rm "$procroot/3333/ns/mnt"
ln -s 'mnt:[7001]' "$procroot/3333/ns/mnt"

rm -rf "$croot" "$markerroot"
: >"$mount_log"
: >"$tmux_log"
printf 'absent\n' >"$state"
printf 'none\n' >"$server_state"
WSL_INTEROP="$socket" TT_LOGIN_SHELL=/bin/sh "$temporary/tt" shell bridge-create >/dev/null
grep -Fqx -- "set-option -gq @tt_wsl_interop $socket" "$tmux_log"
grep -Fqx -- "2222|222222|mnt:[7001]" "$markerroot/mount-1000"
[[ ! -e "$markerroot/pending-1000" ]]

reset_bridge_fixture
chmod 777 "$markerroot"
expect_failure env WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
[[ ! -s "$mount_log" ]]
chmod 700 "$markerroot"

write_record "$markerroot/mount-1000" 'garbage'
expect_failure env WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
[[ ! -s "$mount_log" ]]

write_record "$markerroot/mount-1000" '5555|555555|mnt:[8000]' 666
expect_failure env WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
[[ ! -s "$mount_log" ]]

write_record "$markerroot/real-record" '5555|555555|mnt:[8000]'
rm -f "$markerroot/mount-1000"
ln -s "$markerroot/real-record" "$markerroot/mount-1000"
expect_failure env WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
[[ ! -s "$mount_log" ]]

reset_bridge_fixture
printf 'valid\n' >"$state"
write_record "$markerroot/mount-1000" '5555|555555|mnt:[7001]'
WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
grep -Fqx -- '2222|222222|mnt:[7001]' "$markerroot/mount-1000"
[[ ! -s "$umount_log" ]]

reset_bridge_fixture
printf 'valid\n' >"$state"
write_record "$markerroot/pending-1000" '5555|555555|mnt:[7001]'
WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
grep -Fqx -- '2222|222222|mnt:[7001]' "$markerroot/mount-1000"
[[ ! -e "$markerroot/pending-1000" ]]
[[ ! -s "$umount_log" ]]

reset_bridge_fixture
printf 'valid\n' >"$state"
write_record "$markerroot/mount-1000" '5555|555555|mnt:[8000]'
expect_failure env WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
grep -Fqx -- '5555|555555|mnt:[8000]' "$markerroot/mount-1000"
[[ ! -s "$umount_log" ]]

reset_bridge_fixture
write_record "$markerroot/mount-1000" '5555|555555|mnt:[7001]'
WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
grep -Fqx -- '2222|222222|mnt:[7001]' "$markerroot/mount-1000"
[[ -s "$mount_log" ]]

reset_bridge_fixture
make_proc 4444 bash 444444 'mnt:[8000]' /usr/bin/bash
write_record "$markerroot/mount-1000" '4444|444444|mnt:[8000]'
expect_failure env WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
grep -Fqx -- '4444|444444|mnt:[8000]' "$markerroot/mount-1000"
[[ ! -s "$mount_log" ]]

reset_bridge_fixture
make_proc 4444 bash 444444 'mnt:[8000]' /usr/bin/bash
write_record "$markerroot/mount-1000" '5555|555555|mnt:[8000]'
expect_failure env WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
grep -Fqx -- '5555|555555|mnt:[8000]' "$markerroot/mount-1000"
[[ ! -s "$mount_log" ]]

rm -rf "$procroot/4444"
WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
grep -Fqx -- '2222|222222|mnt:[7001]' "$markerroot/mount-1000"
[[ -s "$mount_log" ]]

reset_bridge_fixture
write_record "$markerroot/pending-1000" '5555|555555|mnt:[8000]'
WSL_INTEROP="$socket" "$temporary/tt" wsl-bridge ensure
grep -Fqx -- '2222|222222|mnt:[7001]' "$markerroot/mount-1000"
[[ ! -e "$markerroot/pending-1000" ]]

reset_bridge_fixture
pane_calls="$temporary/pane.calls"
pane_stderr="$temporary/pane.stderr"
printf '0\n' >"$pane_calls"
if TT_TEST_PANE_CALLS="$pane_calls" TT_TEST_PANE_CHURN=1 WSL_INTEROP="$socket" \
  "$temporary/tt" wsl-bridge ensure >/dev/null 2>"$pane_stderr"; then
  printf 'pane churn unexpectedly succeeded\n' >&2
  exit 1
fi
grep -Fq -- 'tmux pane set changed during bridge setup' "$pane_stderr"
if grep -Fq -- 'WSL bridge cleanup failed' "$pane_stderr"; then
  printf 'pane churn rollback reported a cleanup failure\n' >&2
  exit 1
fi
grep -Fqx -- 'absent' "$state"
[[ ! -e "$markerroot/mount-1000" ]]
[[ -s "$umount_log" ]]

reset_bridge_fixture
identity_stderr="$temporary/identity.stderr"
if TT_TEST_SET_OPTION_FAIL=1 WSL_INTEROP="$socket" \
  "$temporary/tt" wsl-bridge ensure >/dev/null 2>"$identity_stderr"; then
  printf 'server identity mutation unexpectedly succeeded\n' >&2
  exit 1
fi
grep -Fq -- 'WSL bridge cleanup failed after tmux socket option update' "$identity_stderr"
grep -Fqx -- 'valid' "$state"
grep -Fqx -- '2222|222222|mnt:[7001]' "$markerroot/mount-1000"
[[ ! -s "$umount_log" ]]
write_proc_stat 2222 'tmux: server' 222222

reset_bridge_fixture
printf 'valid\n' >"$state"
write_record "$markerroot/mount-1000" '2222|222222|mnt:[7001]'
write_proc_stat 2222 'tmux: server' 999999
expect_failure env WSL_INTEROP="$socket" TT_LOGIN_SHELL=/bin/sh \
  "$temporary/tt" reset shell reset-proof
grep -Fqx -- 'valid' "$state"
grep -Fqx -- '2222|222222|mnt:[7001]' "$markerroot/mount-1000"
if grep -Fqx -- 'kill-server' "$tmux_log"; then
  printf 'reset killed a server whose identity could not be validated\n' >&2
  exit 1
fi
write_proc_stat 2222 'tmux: server' 222222

reset_bridge_fixture
printf 'valid\n' >"$state"
write_record "$markerroot/mount-1000" '2222|222222|mnt:[7001]'
WSL_INTEROP="$socket" TT_LOGIN_SHELL=/bin/sh \
  "$temporary/tt" reset shell reset-proof >/dev/null
grep -Fqx -- 'valid' "$state"
grep -Fqx -- 'live' "$server_state"
grep -Fqx -- '2222|222222|mnt:[7001]' "$markerroot/mount-1000"
[[ ! -e "$markerroot/pending-1000" ]]
[[ -s "$mount_log" ]]
[[ -s "$umount_log" ]]
grep -Fqx -- 'kill-server' "$tmux_log"

printf 'valid\n' >"$state"
printf 'live\n' >"$server_state"
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
printf 'argc=%s\n' "$#" >>"${TT_TEST_EXE_LOG:?}"
if [[ "$#" -gt 0 ]]; then
  printf '<%s>\n' "$@" >>"${TT_TEST_EXE_LOG:?}"
fi
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
chmod 444 "$croot/Program Files/WindowsApps/Microsoft.PowerShell_7.6.4.0_arm64__8wekyb3d8bbwe/pwsh.exe"
"$temporary/pwsh.exe"
[[ "$(wc -l <"$exe_log" | tr -d ' ')" == "2" ]]
grep -Fqx "interop=$socket" "$exe_log"
grep -Fqx 'argc=0' "$exe_log"

# shellcheck disable=SC1003,SC2016
args=(
  ""
  "two words"
  "single'quote"
  'double"quote'
  'trailing\\'
  '$dollar'
  ';semicolon'
  '*wildcard'
  $'line one\nline two'
  'naive-cafe'
  '日本語'
)
"$temporary/pwsh.exe" "${args[@]}"
expected="$temporary/expected-args"
{
  printf 'interop=%s\n' "$socket"
  printf 'argc=%s\n' "${#args[@]}"
  printf '<%s>\n' "${args[@]}"
} >"$expected"
cmp "$expected" "$exe_log"

export TT_TEST_EXE_STATUS=29
if "$temporary/pwsh.exe" exit-status; then
  printf 'pwsh wrapper lost child exit status\n' >&2
  exit 1
else
  status="$?"
fi
[[ "$status" == "29" ]]
export TT_TEST_EXE_STATUS=0

saved_socket="$TT_TEST_STORED_SOCKET"
export TT_TEST_STORED_SOCKET="$runtime/9999_interop"
expect_failure env -u WSL_INTEROP "$temporary/pwsh.exe" stale-socket
export TT_TEST_STORED_SOCKET="$saved_socket"

mkdir -p "$croot/Program Files/WindowsApps/Microsoft.PowerShell_7.7.0.0_arm64__8wekyb3d8bbwe"
cp "$croot/Windows/System32/WindowsPowerShell/v1.0/powershell.exe" \
  "$croot/Program Files/WindowsApps/Microsoft.PowerShell_7.7.0.0_arm64__8wekyb3d8bbwe/pwsh.exe"
chmod 444 "$croot/Program Files/WindowsApps/Microsoft.PowerShell_7.7.0.0_arm64__8wekyb3d8bbwe/pwsh.exe"
expect_failure "$temporary/pwsh.exe" ambiguous

rm -rf "$croot/Program Files/WindowsApps/Microsoft.PowerShell_"*
expect_failure "$temporary/pwsh.exe" missing

printf 'writable\n' >"$state"
expect_failure "$temporary/powershell.exe" bad-mount

printf 'tt_wsl_bridge_test=passed\n'
