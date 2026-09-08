#!/usr/bin/env bash
: "${wrapper_dir:?entrypoint must set wrapper_dir}"

gh_backend() {
  local name="$1" remaining="${PATH:-}" entry candidate done=""
  while :; do
    case "$remaining" in
      *:*) entry="${remaining%%:*}"; remaining="${remaining#*:}" ;;
      *) entry="$remaining"; done=1 ;;
    esac
    candidate="${entry:-.}/$name"
    if [[ -x "$candidate" && ! -d "$candidate" &&
      ! "$candidate" -ef "$wrapper_dir/gh" && ! "$candidate" -ef "$wrapper_dir/ghx" ]]; then
      printf '%s/%s\n' "$(cd -P -- "${entry:-.}" && pwd)" "$name"
      return 0
    fi
    [[ -z "$done" ]] || return 1
  done
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

gh_native() {
  local index next short prefix flag expression
  local args=("$@")
  if [[ "${1:-}" == api ]]; then
    for ((index = 1; index < ${#args[@]}; index += 1)); do
      case "${args[index]}" in
        --jq)
          next=$((index + 1))
          [[ $next -ge ${#args[@]} ]] || args[next]="$(gh_jq_filter "${args[next]}")"
          index="$next" ;;
        --jq=*) args[index]="--jq=$(gh_jq_filter "${args[index]#*=}")" ;;
        --cache | --field | --header | --hostname | --input | --method | --preview | --raw-field | --template)
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
  exec "$gh_bin" "${args[@]}"
}

gh_route() {
  local no_cache="${GHX_NO_CACHE:-0}" ttl="" gh_bin ghx_bin="" arg
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
  gh_bin="$(gh_backend gh)" || {
    printf '%s: native GitHub CLI is required; install gh before using this wrapper\n' "${0##*/}" >&2
    return 127
  }
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
  if [[ "$no_cache" != 1 && -z "${GH_REPO:-}" && -n "$ghx_bin" &&
    -f "${HOME:?}/.ghx/config.yaml" ]] && gh_cacheable_read "$@"; then
    [[ -z "$ttl" ]] || controls+=(--ttl "$ttl")
    exec env GHX_GH_PATH="$gh_bin" "$ghx_bin" "${controls[@]+"${controls[@]}"}" "$@"
  fi
  if [[ -n "$ttl" ]]; then
    gh_route_error "--ttl applies only to cacheable structured reads; use api --cache <duration> for an explicitly safe GET"
    return 2
  fi
  if [[ "$no_cache" == 1 && "${1:-}" == api ]]; then
    for arg in "$@"; do
      case "$arg" in
        --cache | --cache=*)
          gh_route_error "--no-cache/GHX_NO_CACHE=1 conflicts with api --cache"
          return 2 ;;
      esac
    done
  fi
  gh_native "$@"
}
