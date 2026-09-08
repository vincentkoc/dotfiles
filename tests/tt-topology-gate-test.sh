#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tt="$repo/bin/tt"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
fake_tmux="$temporary/tmux"
tmux_log="$temporary/tmux.log"
tmux_tmpdir="$temporary/tmux-root"
mkdir -p "$tmux_tmpdir"
chmod 700 "$tmux_tmpdir"

cat >"$fake_tmux" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$TT_TEST_TMUX_LOG"
case "${1:-}" in
  has-session)
    [[ "${TT_TEST_EXISTING_SESSION:-}" == "${*: -1}" ]] && exit 0
    exit 1
    ;;
  list-sessions)
    if [[ "${TT_TEST_MULTI_SESSION:-0}" == "1" ]]; then
      printf 'first\nsecond\n'
      exit 0
    fi
    exit 1
    ;;
  new-session|attach)
    exit 0
    ;;
esac
SH
chmod +x "$fake_tmux"

invoke() {
  env -u TMUX \
    HOME="$temporary/home" \
    TT_LOGIN_SHELL=/bin/sh \
    TT_TMUX_BIN="$fake_tmux" \
    TT_TEST_TMUX_LOG="$tmux_log" \
    TMUX_TMPDIR="$tmux_tmpdir" \
    CODEX_THREAD_ID=test-thread \
    "$@"
}

if invoke "$tt" shell guarded >"$temporary/blocked.out" 2>&1; then
  printf 'agent creation unexpectedly bypassed the topology gate\n' >&2
  exit 1
fi
grep -Fq 'TT_OPERATOR_TMUX_SCOPE=create:guarded' "$temporary/blocked.out"
if grep -Fq 'new-session' "$tmux_log"; then
  printf 'blocked invocation created a session\n' >&2
  exit 1
fi

: >"$tmux_log"
TT_TEST_EXISTING_SESSION=existing invoke "$tt" shell existing
grep -Fxq 'attach -t existing' "$tmux_log"
if grep -Fq 'new-session' "$tmux_log"; then
  printf 'existing-session attach created a session\n' >&2
  exit 1
fi

TT_OPERATOR_TMUX_SCOPE=create:guarded invoke "$tt" shell guarded
grep -Fxq 'new-session -d -s guarded' "$tmux_log"

: >"$tmux_log"
if TT_TEST_MULTI_SESSION=1 TT_OPERATOR_TMUX_SCOPE=reset:guarded \
  invoke "$tt" reset shell guarded >"$temporary/reset.out" 2>&1; then
  printf 'agent server-wide reset unexpectedly accepted an exact-target grant\n' >&2
  exit 1
fi
grep -Fq 'agents cannot use the legacy server-wide reset route' "$temporary/reset.out"
if grep -Eq 'kill-server|new-session' "$tmux_log"; then
  printf 'blocked agent reset mutated mocked multi-session server state\n' >&2
  exit 1
fi
if TT_OPERATOR_TMUX_SCOPE=reset:guarded invoke "$tt" shell guarded \
  >"$temporary/reset-create.out" 2>&1; then
  printf 'reset grant unexpectedly authorized direct session creation\n' >&2
  exit 1
fi

: >"$tmux_log"
if env -u TMUX -u TMUX_TMPDIR \
  HOME="$temporary/home" \
  TT_LOGIN_SHELL=/bin/sh \
  TT_TMUX_BIN="$fake_tmux" \
  TT_TEST_TMUX_LOG="$tmux_log" \
  TT_ISOLATED_FIXTURE=1 \
  CODEX_THREAD_ID=test-thread \
  "$tt" shell spoofed >"$temporary/spoofed.out" 2>&1; then
  printf 'fixture flag without isolated TMUX_TMPDIR bypassed the gate\n' >&2
  exit 1
fi
if grep -Fq 'new-session' "$tmux_log"; then
  printf 'fixture spoof created a session\n' >&2
  exit 1
fi

: >"$tmux_log"
TT_ISOLATED_FIXTURE=1 invoke "$tt" shell fixture
grep -Fxq 'new-session -d -s fixture' "$tmux_log"

: >"$tmux_log"
if CODEX_THREAD_ID=test-thread TT_TMUX_BIN="$fake_tmux" \
  TT_TEST_TMUX_LOG="$tmux_log" TMUX_TMPDIR="$tmux_tmpdir" \
  "$repo/bin/tmux-studio" studio \
  >"$temporary/wrapper.out" 2>&1; then
  printf 'legacy studio wrapper unexpectedly bypassed the topology gate\n' >&2
  exit 1
fi
grep -Fq 'TT_OPERATOR_TMUX_SCOPE=create:studio' "$temporary/wrapper.out"
if grep -Fq 'new-session' "$tmux_log"; then
  printf 'blocked wrapper created a session\n' >&2
  exit 1
fi

grep -Fq 'topology-authorize remote-create "$target"' "$repo/bin/quickssh"
grep -Fq 'topology-authorize remote-create "$target:$scope_session"' "$repo/bin/mttc"
grep -Fq 'exec tt shell "$SESSION_NAME"' "$repo/bin/tm"

printf 'tt_topology_gate_test=passed\n'
