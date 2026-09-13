# shellcheck shell=bash
low_data_github_check() {
  local command="" subcommand="" endpoint="" header="" binary=0
  while (( $# > 0 )); do
    case "$1" in
      --hostname|--repo|-R|--cache-ttl|--ttl|--timeout)
        (( $# > 1 )) || return 0
        shift 2 ;;
      --) shift; break ;;
      -*) shift ;;
      *) command="$1"; shift; break ;;
    esac
  done
  if [[ -z "$command" && $# -gt 0 ]]; then
    command="$1"
    shift
  fi

  case "$command" in
    repo|release|run)
      while (( $# > 0 )); do
        case "$1" in
          --hostname|--repo|-R)
            (( $# > 1 )) || return 0
            shift 2 ;;
          --) shift; subcommand="${1:-}"; break ;;
          -*) shift ;;
          *) subcommand="$1"; break ;;
        esac
      done
      case "$command:$subcommand" in
        repo:clone|release:download|run:download) ;;
        *) return 0 ;;
      esac
      ;;
    api)
      while (( $# > 0 )); do
        header=""
        case "$1" in
          -H|--header)
            (( $# > 1 )) || return 0
            header="$2"; shift 2 ;;
          --header=*) header="${1#*=}"; shift ;;
          -H*) header="${1#-H}"; header="${header#=}"; shift ;;
          --hostname|-X|--method|-F|--field|-f|--raw-field|--input|--jq|-q|--template|-t|--cache)
            (( $# > 1 )) || return 0
            shift 2 ;;
          --)
            shift
            [[ -n "$endpoint" ]] || endpoint="${1:-}"
            break ;;
          -*) shift ;;
          *)
            [[ -n "$endpoint" ]] || endpoint="$1"
            shift ;;
        esac
        if [[ -n "$header" ]]; then
          header="$(printf '%s' "$header" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
          if [[ "$header" =~ ^[[:space:]]*accept[[:space:]]*: && "$header" == *application/octet-stream* ]]; then
            binary=1
          fi
        fi
      done
      endpoint="${endpoint%%\?*}"
      endpoint="${endpoint%%\#*}"
      case "$endpoint" in
        *://*) endpoint="${endpoint#*://}"; endpoint="${endpoint#*/}" ;;
      esac
      while [[ "$endpoint" == /* ]]; do endpoint="${endpoint#/}"; done
      endpoint="${endpoint#api/v3/}"
      endpoint="${endpoint%/}"
      if [[ "$endpoint" =~ ^repos/[^/]+/[^/]+/(tarball|zipball)(/.*)?$ ]] ||
         [[ "$endpoint" =~ ^repos/[^/]+/[^/]+/actions/artifacts/[^/]+/zip$ ]] ||
         { [[ "$binary" == 1 ]] && [[ "$endpoint" =~ ^repos/[^/]+/[^/]+/releases/assets/[0-9]+$ ]]; }; then
        :
      else
        return 0
      fi
      ;;
    *) return 0 ;;
  esac
  printf 'gh/ghx: low-data protection blocks this bulk download; explicit approval is required\n' >&2
  return 77
}
