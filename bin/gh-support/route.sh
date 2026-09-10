#!/usr/bin/env bash
: "${wrapper_dir:?entrypoint must set wrapper_dir}"

gh_backend() {
  local name="$1" remaining="${PATH:-}" entry candidate done="" octopool=""
  if [[ "$name" == gh ]]; then
    octopool="$(gh_backend octopool)" || true
  fi
  while :; do
    case "$remaining" in
      *:*) entry="${remaining%%:*}"; remaining="${remaining#*:}" ;;
      *) entry="$remaining"; done=1 ;;
    esac
    candidate="${entry:-.}/$name"
    if [[ -x "$candidate" && -f "$candidate" &&
      ! "$candidate" -ef "$wrapper_dir/gh" && ! "$candidate" -ef "$wrapper_dir/ghx" ]] &&
      { [[ "$name" == octopool ]] || ! gh_backend_shim "$candidate" "$octopool"; }; then
      printf '%s/%s\n' "$(cd -P -- "${entry:-.}" && pwd)" "$name"
      return 0
    fi
    [[ -z "$done" ]] || return 1
  done
}

gh_backend_shim() {
  local target="$1" link count=0
  [[ -z "$2" || ! "$1" -ef "$2" ]] || return 0
  while [[ -L "$target" && $count -lt 40 ]]; do
    link="$(readlink "$target")" || return 0
    case "$link" in
      /*) target="$link" ;;
      *) target="${target%/*}/$link" ;;
    esac
    count=$((count + 1))
  done
  case "${target##*/}" in octopool | octopool-gh) return 0 ;; esac
  gh_entrypoint_copy "$1"
}

gh_entrypoint_copy() {
  local first="" second=""
  # Copies of the wrappers are not necessarily the same inode as this entrypoint.
  {
    IFS= read -r -n 512 first || true
    IFS= read -r -n 512 second || true
  } <"$1"
  [[ "$first" == '#!'* && ( "$second" == '# ghx-shim'* || "$second" == *'octopool gh'* ) ]]
}

gh_route_error() {
  printf '%s: %s\n' "${0##*/}" "$*" >&2
}

gh_cacheable_read() {
  local command="${1:-} ${2:-}" item="" repo="" fields=""
  case "$command" in
    "pr view" | "pr checks" | "issue view" | "run view") shift 2 ;;
    *) return 1 ;;
  esac
  # Unknown options stay native. This also excludes web, watch, logs, templates,
  # stdin-backed commands, and extensions without guessing their behavior.
  while (($#)); do
    case "$1" in
      -R | --repo | --json | -q | --jq)
        [[ $# -ge 2 && -n "$2" && "$2" != -* ]] || return 1
        case "$1" in
          -R | --repo) repo="$2" ;;
          --json) fields="$2" ;;
        esac
        shift 2 ;;
      --repo=*) repo="${1#*=}"; shift ;;
      -R?*) repo="${1#-R}"; shift ;;
      --json=*) fields="${1#*=}"; shift ;;
      --jq=* | -q?*) shift ;;
      *)
        [[ -z "$item" && "$1" =~ ^[0-9]+$ ]] || return 1
        item="$1"
        shift ;;
    esac
  done
  [[ -n "$item" && -n "$fields" && "$repo" == */* ]]
}

gh_jq_filter() {
  # gh's own formatter knows result types. Preserve scalars/arrays and convert
  # only objects to JSONL; the newline also terminates trailing jq comments.
  [[ -n "$1" ]] || return 0
  printf '(%s\n) | if type == "object" then tojson else . end' "$1"
}

gh_octopool_path() {
  local endpoint="$1" path query part key value
  local LC_ALL=C
  [[ "$endpoint" =~ ^repos/openclaw/(openclaw|octopool)(/|\?|$) ]] || return 1
  path="${endpoint%%\?*}"
  [[ "$path" =~ ^[a-zA-Z0-9_./-]+$ && "$path" != *..* && "$path" != *//* ]] || return 1
  path="${path#repos/openclaw/}"
  path="${path#*/}"
  case "$endpoint" in
    repos/openclaw/openclaw | repos/openclaw/octopool | repos/openclaw/openclaw\?* | repos/openclaw/octopool\?*)
      path="" ;;
  esac
  # Exclude permission-management endpoints, logs, and credential-bearing resources.
  [[ -z "$path" ||
    "$path" =~ ^pulls(/[0-9]+(/(files|commits|reviews))?)?$ ||
    "$path" =~ ^issues(/[0-9]+(/comments)?)?$ ||
    "$path" =~ ^commits(/[a-zA-Z0-9_-]+(/(check-runs|check-suites|status|statuses))?)?$ ||
    "$path" =~ ^check-runs/[0-9]+$ ||
    "$path" =~ ^check-suites/[0-9]+/check-runs$ ||
    "$path" =~ ^actions/runs(/[0-9]+(/jobs|/attempts/[0-9]+(/jobs)?)?)?$ ||
    "$path" =~ ^actions/jobs/[0-9]+$ ||
    "$path" =~ ^actions/workflows(/[0-9]+(/runs)?)?$ ]] || return 1
  [[ "$endpoint" == *\?* ]] || return 0
  query="${endpoint#*\?}"
  [[ -n "$query" && "$query" != *'&' ]] || return 1
  while [[ -n "$query" ]]; do
    part="${query%%&*}"
    [[ "$part" == *=* ]] || return 1
    key="${part%%=*}"
    value="${part#*=}"
    case "$key" in
      page) [[ "$value" =~ ^[1-9][0-9]*$ ]] || return 1 ;;
      per_page) [[ "$value" =~ ^([1-9]|[1-9][0-9]|100)$ ]] || return 1 ;;
      state) [[ "$value" == open || "$value" == closed || "$value" == all ]] || return 1 ;;
      filter) [[ "$value" == latest || "$value" == all ]] || return 1 ;;
      *) return 1 ;;
    esac
    [[ "$query" == *'&'* ]] || break
    query="${query#*&}"
  done
}

gh_octopool_read() {
  local endpoint="" paginate=0 slurp=0 jq=0
  [[ "${1:-}" == api ]] || return 1
  shift
  while (($#)); do
    case "$1" in
      --paginate) paginate=1; shift ;;
      --slurp) slurp=1; shift ;;
      --jq | -q)
        [[ $# -ge 2 && -n "$2" ]] || return 1
        jq=1
        shift 2 ;;
      --jq=?*) jq=1; shift ;;
      --method | -X)
        [[ $# -ge 2 && "$2" == GET ]] || return 1
        shift 2 ;;
      --method=GET) shift ;;
      -* | "")
        return 1 ;;
      *)
        [[ -z "$endpoint" ]] || return 1
        endpoint="$1"
        shift ;;
    esac
  done
  [[ "$slurp" == 0 || ( "$paginate" == 1 && "$jq" == 0 ) ]] || return 1
  gh_octopool_path "$endpoint"
}

gh_octopool_config() {
  local activation="${HOME:?}/.config/gh-routing/octopool.json" config octopool native
  [[ -f "$activation" && ! -L "$activation" ]] || return 1
  gh_jq_bin="$(command -v jq)" || return 1
  config="$("$gh_jq_bin" -ser '
    def absolute_path: type == "string" and startswith("/") and (test("[[:cntrl:]]") | not);
    select(length == 1) | .[0] |
    select(type == "object" and keys == ["enabled", "gh_path", "octopool_path"]
      and .enabled == true and (.octopool_path | absolute_path) and (.gh_path | absolute_path))
    | .octopool_path, .gh_path
  ' "$activation" 2>/dev/null)" || return 1
  octopool="${config%%$'\n'*}"
  native="${config#*$'\n'}"
  [[ -x "$octopool" && -f "$octopool" && -x "$native" && -f "$native" &&
    ! "$native" -ef "$wrapper_dir/gh" && ! "$native" -ef "$wrapper_dir/ghx" &&
    ! "$octopool" -ef "$wrapper_dir/gh" && ! "$octopool" -ef "$wrapper_dir/ghx" ]] || return 1
  ! gh_backend_shim "$native" "$octopool" || return 1
  ! gh_entrypoint_copy "$octopool" || return 1
  gh_bin="$native"
  octopool_config_bin="$octopool"
}

gh_octopool_ready() {
  local auth
  [[ "${GH_OCTOPOOL:-}" != 0 && -z "${GH_HOST:-}" && -z "${GH_REPO:-}" &&
    -n "$octopool_config_bin" ]] || return 1
  case "$(uname -s)" in
    Darwin) auth="$HOME/Library/Application Support/octopool/auth.json" ;;
    Linux) auth="${XDG_CONFIG_HOME:-$HOME/.config}/octopool/auth.json" ;;
    *) return 1 ;;
  esac
  [[ "$auth" == /* && -f "$auth" && ! -L "$auth" ]] || return 1
  "$gh_jq_bin" -se '
    length == 1 and (.[0] |
    type == "object" and .url == "https://octopool.dev" and .pool == "maintainers"
    and (.token | type == "string" and length > 0 and (test("[[:space:][:cntrl:]]") | not))
    )
  ' "$auth" >/dev/null 2>&1
}

gh_native() {
  local index next short prefix flag expression
  local args=("$@")
  if [[ "${1:-}" == api ]]; then
    for ((index = 1; index < ${#args[@]}; index += 1)); do
      case "${args[index]}" in
        --cache | --cache=*)
          if [[ "${no_cache:-0}" == 1 ]]; then
            gh_route_error "--no-cache/GHX_NO_CACHE=1 conflicts with api --cache"
            return 2
          fi
          [[ "${args[index]}" != --cache ]] || index=$((index + 1)) ;;
        --jq)
          next=$((index + 1))
          [[ $next -ge ${#args[@]} ]] || args[next]="$(gh_jq_filter "${args[next]}")"
          index="$next" ;;
        --jq=*) args[index]="--jq=$(gh_jq_filter "${args[index]#*=}")" ;;
        --field | --header | --hostname | --input | --method | --preview | --raw-field | --template)
          index=$((index + 1)) ;;
        --) break ;;
        --*) ;;
        -?*)
          short="${args[index]#-}"
          prefix="-"
          while [[ -n "$short" ]]; do
            flag="${short:0:1}"
            short="${short:1}"
            prefix+="$flag"
            case "$flag" in
              q)
                if [[ -n "$short" ]]; then
                  expression="$short"
                  if [[ "$expression" == =* ]]; then
                    prefix+="="
                    expression="${expression#=}"
                  fi
                  args[index]="$prefix$(gh_jq_filter "$expression")"
                else
                  next=$((index + 1))
                  [[ $next -ge ${#args[@]} ]] || args[next]="$(gh_jq_filter "${args[next]}")"
                  index="$next"
                fi
                break ;;
              F | H | X | p | f | t)
                [[ -n "$short" ]] || index=$((index + 1))
                break ;;
              i) ;;
              *) break ;;
            esac
          done ;;
      esac
    done
  fi
  if [[ -n "${octopool_bin:-}" ]]; then
    # Use only the saved host-local caller identity and the reviewed destination.
    unset OCTOPOOL_TOKEN OCTOPOOL_ADMIN_TOKEN OCTOPOOL_URL OCTOPOOL_POOL OCTOPOOL_GH_PATH GHX_GH_PATH
    export OCTOPOOL_URL=https://octopool.dev OCTOPOOL_POOL=maintainers OCTOPOOL_GH_PATH="$gh_bin"
    exec "$octopool_bin" gh "${args[@]}"
  fi
  exec "$gh_bin" "${args[@]}"
}

gh_route() {
  local no_cache="${GHX_NO_CACHE:-0}" ttl="" gh_bin ghx_bin="" octopool_bin=""
  local octopool_config_bin="" gh_jq_bin=""
  local controls=()
  while (($#)); do
    case "$1" in
      --no-cache) no_cache=1; shift ;;
      --ttl)
        [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]] ||
          { gh_route_error "--ttl requires integer seconds"; return 2; }
        ttl="$2"
        shift 2 ;;
      --no-cache=* | --ttl=*)
        gh_route_error "use --no-cache or --ttl <seconds> before the command"
        return 2 ;;
      *) break ;;
    esac
  done
  if [[ "$no_cache" == 1 && -n "$ttl" ]]; then
    gh_route_error "--ttl conflicts with an uncached request"
    return 2
  fi
  if ! gh_octopool_config; then
    gh_bin="$(gh_backend gh)" || {
      printf '%s: native GitHub CLI is required; install gh before using this wrapper\n' "${0##*/}" >&2
      return 127
    }
  fi
  ghx_bin="$(gh_backend ghx)" || true
  unset GH_FORCE_TTY
  export NO_COLOR=1 CLICOLOR=0 CLICOLOR_FORCE=0
  if [[ "${1:-}" != auth && ! -t 0 ]]; then
    export GH_PROMPT_DISABLED=1 GH_PAGER=cat
  fi
  case "${1:-}" in
    xdaemon | xcache | xversion | xhelp)
      [[ -n "$ghx_bin" ]] || { gh_route_error "ghx is not installed"; return 127; }
      [[ -z "$ttl" ]] || { gh_route_error "--ttl does not apply to proxy management"; return 2; }
      exec env GHX_GH_PATH="$gh_bin" "$ghx_bin" "$@" ;;
  esac
  if [[ "$no_cache" != 1 && -z "$ttl" ]] && gh_octopool_read "$@" && gh_octopool_ready; then
    octopool_bin="$octopool_config_bin"
    gh_native "$@"
  fi
  if [[ "$no_cache" != 1 && -z "${GH_REPO:-}" && -n "$ghx_bin" &&
    -f "${HOME:?}/.ghx/config.yaml" ]] && gh_cacheable_read "$@"; then
    [[ -z "$ttl" ]] || controls+=(--ttl "$ttl")
    exec env GHX_GH_PATH="$gh_bin" "$ghx_bin" "${controls[@]+"${controls[@]}"}" "$@"
  fi
  if [[ -n "$ttl" ]]; then
    gh_route_error "--ttl applies only to cacheable structured reads; use api --cache <duration> for an explicitly safe GET"
    return 2
  fi
  gh_native "$@"
}
