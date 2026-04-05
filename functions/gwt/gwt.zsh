[[ -n "${DOTFILES_GWT_LOADED:-}" ]] && return 0
typeset -g DOTFILES_GWT_LOADED=1

_gwt_slug_from_origin() {
    local origin="$1"
    local cleaned slug

    [[ -n "$origin" ]] || return 1

    cleaned=${origin%.git}
    cleaned=${cleaned#ssh://}
    cleaned=${cleaned#https://}
    cleaned=${cleaned#http://}
    cleaned=${cleaned#*:*@}
    cleaned=${cleaned#git@}
    if [[ "${cleaned%%/*}" == *:* ]]; then
        cleaned="${cleaned/:/\/}"
    fi
    cleaned=${cleaned#*/}
    slug=$(printf '%s' "$cleaned" | tr -s '/' '-' | tr -cd '[:alnum:]._-')
    [[ -n "$slug" ]] || return 1
    printf '%s\n' "$slug"
}

_gwt_clone_dest_name() {
    local origin="$1"
    local trimmed="${origin%/}"
    trimmed=${trimmed##*:}
    trimmed=${trimmed##*/}
    trimmed=${trimmed%.git}
    printf '%s\n' "$trimmed"
}

_gwt_repo_slug() {
    local repo_root="${1:-}"
    local top origin slug

    if [[ -n "$repo_root" ]]; then
        top=$(git -C "$repo_root" rev-parse --show-toplevel 2>/dev/null) || return 1
        origin=$(git -C "$repo_root" config --get remote.origin.url 2>/dev/null || true)
    else
        top=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
        origin=$(git config --get remote.origin.url 2>/dev/null || true)
    fi

    if [[ -n "$origin" ]]; then
        slug=$(_gwt_slug_from_origin "$origin" 2>/dev/null || true)
    fi

    if [[ -z "$slug" ]]; then
        slug=$(basename "$top")
    fi

    printf '%s\n' "$slug"
}

_gwt_sparse_root() {
    printf '%s\n' "${DOTFILES_GIT_SPARSE_ROOT:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/dotfiles/git-sparse}"
}

_gwt_sparse_repo_dir() {
    local repo_root="${1:-}"
    local repo_slug
    repo_slug=$(_gwt_repo_slug "$repo_root") || return 1
    printf '%s/%s\n' "$(_gwt_sparse_root)" "$repo_slug"
}

_gwt_sparse_repo_dir_from_slug() {
    local repo_slug="$1"
    printf '%s/%s\n' "$(_gwt_sparse_root)" "$repo_slug"
}

_gwt_sparse_default_profile() {
    local repo_root="$1"
    local repo_dir default_file
    repo_dir=$(_gwt_sparse_repo_dir "$repo_root") || return 1
    default_file="$repo_dir/default.profile"
    [[ -f "$default_file" ]] || return 1
    awk 'NF {print; exit}' "$default_file"
}

_gwt_sparse_clone_filter() {
    local repo_slug="$1"
    local filter_file repo_dir
    repo_dir=$(_gwt_sparse_repo_dir_from_slug "$repo_slug")
    filter_file="$repo_dir/clone.filter"
    [[ -f "$filter_file" ]] || return 1
    awk 'NF {print; exit}' "$filter_file"
}

_gwt_sparse_profile_file() {
    local repo_root="$1"
    local profile="$2"
    local repo_dir
    repo_dir=$(_gwt_sparse_repo_dir "$repo_root") || return 1

    if [[ -f "$repo_dir/$profile.paths" ]]; then
        printf '%s\n' "$repo_dir/$profile.paths"
        return 0
    fi

    if [[ -f "$repo_dir/$profile.patterns" ]]; then
        printf '%s\n' "$repo_dir/$profile.patterns"
        return 0
    fi

    return 1
}

_gwt_sparse_profile_mode() {
    local profile_file="$1"
    case "$profile_file" in
        *.paths) printf '%s\n' "cone" ;;
        *.patterns) printf '%s\n' "no-cone" ;;
        *) return 1 ;;
    esac
}

_gwt_sparse_list_profiles() {
    local repo_root="$1"
    local repo_dir
    repo_dir=$(_gwt_sparse_repo_dir "$repo_root") || return 1
    [[ -d "$repo_dir" ]] || return 0

    find "$repo_dir" -maxdepth 1 -type f \( -name '*.paths' -o -name '*.patterns' \) -print \
        | while IFS= read -r file; do
            basename "$file" | sed -E 's/\.(paths|patterns)$//'
        done | sort -u
}

_gwt_sparse_enable_worktree_config() {
    local repo_root="$1"
    git -C "$repo_root" config extensions.worktreeConfig true >/dev/null 2>&1 || return 1
}

_gwt_sparse_apply_profile() {
    local worktree_path="$1"
    local profile="$2"
    local repo_root repo_dir profile_file mode

    repo_root=$(git -C "$worktree_path" rev-parse --show-toplevel 2>/dev/null) || return 1
    repo_dir=$(_gwt_sparse_repo_dir "$repo_root") || return 1
    _gwt_sparse_enable_worktree_config "$repo_root" || return 1

    if [[ "$profile" == "full" ]]; then
        git -C "$worktree_path" sparse-checkout disable >/dev/null 2>&1 || true
        git -C "$worktree_path" config --worktree dotfiles.sparseProfile full || return 1
        git -C "$worktree_path" config --worktree --unset-all dotfiles.sparseProfileFile >/dev/null 2>&1 || true
        echo "gwt: sparse profile full (disabled) for $worktree_path"
        return 0
    fi

    if ! profile_file=$(_gwt_sparse_profile_file "$repo_root" "$profile"); then
        echo "gwt: sparse profile '$profile' not found for repo config $repo_dir" >&2
        return 1
    fi

    mode=$(_gwt_sparse_profile_mode "$profile_file") || return 1
    if [[ "$mode" == "cone" ]]; then
        git -C "$worktree_path" sparse-checkout init --cone --sparse-index || return 1
        git -C "$worktree_path" sparse-checkout set --stdin < "$profile_file" || return 1
    else
        git -C "$worktree_path" sparse-checkout init --no-cone || return 1
        git -C "$worktree_path" sparse-checkout set --no-cone --stdin < "$profile_file" || return 1
    fi

    git -C "$worktree_path" config --worktree dotfiles.sparseProfile "$profile" || return 1
    git -C "$worktree_path" config --worktree dotfiles.sparseProfileFile "$profile_file" || return 1
    echo "gwt: sparse profile $profile ($mode) from $profile_file"
}

_gwt_sparse_apply_default_profile() {
    local worktree_path="$1"
    local explicit_profile="${2:-}"
    local repo_root default_profile

    repo_root=$(git -C "$worktree_path" rev-parse --show-toplevel 2>/dev/null) || return 1

    if [[ -n "$explicit_profile" ]]; then
        _gwt_sparse_apply_profile "$worktree_path" "$explicit_profile" || return 1
        return 0
    fi

    default_profile=$(_gwt_sparse_default_profile "$repo_root" 2>/dev/null || true)
    [[ -n "$default_profile" ]] || return 0
    _gwt_sparse_apply_profile "$worktree_path" "$default_profile" || return 1
}

_gwt_sparse_status() {
    local worktree_path="${1:-}"
    local repo_root repo_dir repo_slug sparse_enabled current_profile profile_file

    if [[ -z "$worktree_path" ]]; then
        worktree_path=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
    fi

    repo_root=$(git -C "$worktree_path" rev-parse --show-toplevel 2>/dev/null) || return 1
    repo_slug=$(_gwt_repo_slug "$repo_root") || return 1
    repo_dir=$(_gwt_sparse_repo_dir "$repo_root") || return 1
    sparse_enabled=$(git -C "$worktree_path" config --bool core.sparseCheckout 2>/dev/null || echo false)
    current_profile=$(git -C "$worktree_path" config --worktree --get dotfiles.sparseProfile 2>/dev/null || true)
    profile_file=$(git -C "$worktree_path" config --worktree --get dotfiles.sparseProfileFile 2>/dev/null || true)

    if [[ -z "$current_profile" ]]; then
        if [[ "$sparse_enabled" == "true" ]]; then
            current_profile="custom"
        else
            current_profile="full"
        fi
    fi

    printf 'repo_slug=%s\n' "$repo_slug"
    printf 'worktree=%s\n' "$worktree_path"
    printf 'sparse_enabled=%s\n' "$sparse_enabled"
    printf 'profile=%s\n' "$current_profile"
    printf 'profile_file=%s\n' "${profile_file:-"(generated)"}"
    printf 'profile_dir=%s\n' "$repo_dir"
    printf 'available_profiles=%s\n' "$(_gwt_sparse_list_profiles "$repo_root" | tr '\n' ' ' | sed 's/ $//')"
}

_gwt_sparse_add_paths() {
    local worktree_path="$1"
    shift
    local repo_root

    [[ $# -gt 0 ]] || {
        echo "Usage: gwt sparse add <path...>" >&2
        return 1
    }

    repo_root=$(git -C "$worktree_path" rev-parse --show-toplevel 2>/dev/null) || return 1
    _gwt_sparse_enable_worktree_config "$repo_root" || return 1
    if [[ "$(git -C "$worktree_path" config --bool core.sparseCheckout 2>/dev/null || echo false)" != "true" ]]; then
        git -C "$worktree_path" sparse-checkout init --cone --sparse-index || return 1
    fi
    git -C "$worktree_path" sparse-checkout add "$@" || return 1
    git -C "$worktree_path" config --worktree dotfiles.sparseProfile custom >/dev/null 2>&1 || true
    git -C "$worktree_path" config --worktree --unset-all dotfiles.sparseProfileFile >/dev/null 2>&1 || true
}

_gwt_repo_local_worktree_roots() {
    local repo_root="$1"
    printf '%s\n' "$repo_root/.claude/worktrees"
    printf '%s\n' "$repo_root/.worktrees"
}

_gwt_codex_repo_worktree_root() {
    local repo_root="$1"
    local repo_slug root
    repo_slug=$(_gwt_repo_slug "$repo_root") || return 1
    root="${DOTFILES_WORKTREES_ROOT:-$HOME/.codex/worktrees}"
    printf '%s/%s\n' "$root" "$repo_slug"
}

_gwt_discover_worktree_paths() {
    local repo_root="${1:-}"
    local codex_repo_root local_root worktree_path
    local discovered

    if [[ -z "$repo_root" ]]; then
        repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
    else
        repo_root=$(git -C "$repo_root" rev-parse --show-toplevel 2>/dev/null) || return 1
    fi

    discovered=$({
        git -C "$repo_root" worktree list --porcelain 2>/dev/null | awk '/^worktree / {print substr($0, 10)}'

        codex_repo_root=$(_gwt_codex_repo_worktree_root "$repo_root" 2>/dev/null || true)
        if [[ -n "$codex_repo_root" && -d "$codex_repo_root" ]]; then
            find "$codex_repo_root" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null
        fi

        while IFS= read -r local_root; do
            [[ -d "$local_root" ]] || continue
            find "$local_root" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null
        done < <(_gwt_repo_local_worktree_roots "$repo_root")
    } | awk 'NF && !seen[$0]++') || return 1

    while IFS= read -r worktree_path; do
        [[ -n "$worktree_path" && -d "$worktree_path" ]] || continue
        printf '%s\n' "$worktree_path"
    done <<< "$discovered"

    return 0
}

_gwt_gitdir_for_path() {
    local worktree_path="$1"
    local gitfile gitdir_line gitdir

    if [[ -d "$worktree_path/.git" ]]; then
        printf '%s\n' "$worktree_path/.git"
        return 0
    fi

    gitfile="$worktree_path/.git"
    [[ -f "$gitfile" ]] || return 1

    IFS= read -r gitdir_line < "$gitfile" || return 1
    gitdir="${gitdir_line#gitdir: }"
    [[ -n "$gitdir" ]] || return 1

    if [[ "$gitdir" != /* ]]; then
        gitdir="$(cd "$(dirname "$gitfile")" && cd "$gitdir" && pwd)"
    fi

    printf '%s\n' "$gitdir"
}

_gwt_worktree_branch_label() {
    local worktree_path="$1"
    local gitdir head_line branch

    gitdir=$(_gwt_gitdir_for_path "$worktree_path" 2>/dev/null || true)
    if [[ -n "$gitdir" && -f "$gitdir/HEAD" ]]; then
        IFS= read -r head_line < "$gitdir/HEAD" || head_line=""
        if [[ "$head_line" == ref:\ refs/heads/* ]]; then
            branch="${head_line#ref: refs/heads/}"
            printf '%s\n' "$branch"
            return 0
        fi
        printf '%s\n' "detached"
        return 0
    fi

    printf '%s\n' "unmanaged"
}

_gwt_worktree_source_label() {
    local repo_root="$1"
    local worktree_path="$2"
    local codex_repo_root

    codex_repo_root=$(_gwt_codex_repo_worktree_root "$repo_root" 2>/dev/null || true)
    if [[ "$worktree_path" == "$repo_root" ]]; then
        printf '%s\n' "main"
    elif [[ -n "$codex_repo_root" && "$worktree_path" == "$codex_repo_root/"* ]]; then
        printf '%s\n' "codex"
    elif [[ "$worktree_path" == "$repo_root/.claude/worktrees/"* ]]; then
        printf '%s\n' "claude"
    elif [[ "$worktree_path" == "$repo_root/.worktrees/"* ]]; then
        printf '%s\n' "local"
    else
        printf '%s\n' "git"
    fi
}

_gwt_tool_path() {
    local tool_name="$1"
    local candidate

    for candidate in \
        "$HOME/Library/Application Support/agent-worktree-ops/$tool_name" \
        "$HOME/Library/Mobile Documents/com~apple~CloudDocs/dotfiles/bin/agent-worktree-ops/$tool_name" \
        "$HOME/bin/$tool_name"; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    return 1
}

_gwt_should_use_color() {
    local mode="${1:-auto}"

    case "$mode" in
        always) return 0 ;;
        never) return 1 ;;
    esac

    [[ -n "${NO_COLOR:-}" ]] && return 1
    [[ "${CLICOLOR_FORCE:-0}" == "1" ]] && return 0
    [[ -t 1 && "${CLICOLOR:-1}" != "0" ]]
}

_gwt_display_path() {
    local repo_root="$1"
    local worktree_path="$2"
    local display_path="$worktree_path"

    if [[ "$display_path" == "$repo_root" ]]; then
        display_path="."
    elif [[ "$display_path" == "$HOME/"* ]]; then
        display_path="~${display_path#$HOME}"
    fi

    printf '%s\n' "$display_path"
}

_gwt_truncate_cell() {
    local value="$1"
    local max_width="$2"

    if (( ${#value} <= max_width )); then
        printf '%s\n' "$value"
        return 0
    fi

    if (( max_width <= 3 )); then
        printf '%s\n' "${value:0:max_width}"
        return 0
    fi

    printf '%s\n' "...${value: -$((max_width - 3))}"
}

_gwt_render_ls() {
    local repo_root="$1"
    local table="$2"
    local color_mode="${3:-auto}"
    local path_width=72
    local mod_width=16
    local branch_width=36
    local reset="" bold="" dim="" cyan="" green="" magenta="" blue="" yellow="" red=""
    local epoch worktree_path human branch source display_path
    local path_color source_color
    local display_path_padded human_padded branch_padded source_padded

    if _gwt_should_use_color "$color_mode"; then
        reset=$'\033[0m'
        bold=$'\033[1m'
        dim=$'\033[2m'
        cyan=$'\033[36m'
        green=$'\033[32m'
        magenta=$'\033[35m'
        blue=$'\033[34m'
        yellow=$'\033[33m'
        red=$'\033[31m'
    fi

    printf '%b%-*s%b  %b%-*s%b  %b%-*s%b  %b%s%b\n' \
        "$bold" "$path_width" "PATH" "$reset" \
        "$bold" "$mod_width" "LAST MOD" "$reset" \
        "$bold" "$branch_width" "BRANCH" "$reset" \
        "$bold" "SOURCE" "$reset"

    while IFS=$'\t' read -r epoch worktree_path human branch source; do
        [[ -n "$worktree_path" ]] || continue
        display_path=$(_gwt_display_path "$repo_root" "$worktree_path")
        display_path=$(_gwt_truncate_cell "$display_path" "$path_width")
        branch=$(_gwt_truncate_cell "$branch" "$branch_width")

        display_path_padded=$(printf "%-${path_width}s" "$display_path")
        human_padded=$(printf "%-${mod_width}s" "$human")
        branch_padded=$(printf "%-${branch_width}s" "$branch")
        source_padded=$(printf "%s" "$source")

        case "$source" in
            main)
                path_color="$green"
                source_color="$green"
                ;;
            codex)
                path_color="$magenta"
                source_color="$magenta"
                ;;
            claude)
                path_color="$blue"
                source_color="$blue"
                ;;
            local)
                path_color="$yellow"
                source_color="$yellow"
                ;;
            *)
                path_color="$cyan"
                source_color="$cyan"
                ;;
        esac

        printf '%b%s%b  %b%s%b  %b%s%b  %b%s%b\n' \
            "$path_color" "$display_path_padded" "$reset" \
            "$dim" "$human_padded" "$reset" \
            "$yellow" "$branch_padded" "$reset" \
            "$source_color" "$source_padded" "$reset"
    done <<< "$table"
}

# Resolve a target (path, branch, or folder name) to a concrete worktree path.
_gwt_find_path() {
    local target="$1"
    local worktree_path branch repo_root

    if [[ -d "$target" ]]; then
        printf '%s\n' "$target"
        return 0
    fi

    repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || return 1

    while IFS= read -r worktree_path; do
        branch=$(_gwt_worktree_branch_label "$worktree_path")
        if [[ "$worktree_path" == */"$target" || "$branch" == "$target" || "$(basename "$worktree_path")" == "$target" ]]; then
            printf '%s\n' "$worktree_path"
            return 0
        fi
    done < <(_gwt_discover_worktree_paths "$repo_root")

    return 1
}

# Build an fzf-friendly worktree table sorted by most recent commit timestamp.
_gwt_fzf_table() {
    local repo_root="${1:-}"
    local worktree_path branch epoch human source
    local stat_line codex_repo_root
    local table

    if [[ -z "$repo_root" ]]; then
        repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
    else
        repo_root=$(git -C "$repo_root" rev-parse --show-toplevel 2>/dev/null) || return 1
    fi
    codex_repo_root=$(_gwt_codex_repo_worktree_root "$repo_root" 2>/dev/null || true)

    table=$(
    while IFS= read -r worktree_path; do
        if [[ "$OSTYPE" == "darwin"* ]]; then
            stat_line=$(stat -f $'%m\t%Sm' -t "%Y-%m-%d %H:%M" "$worktree_path" 2>/dev/null || printf '0\t-\n')
        else
            stat_line=$(stat -c $'%Y\t%y' "$worktree_path" 2>/dev/null || printf '0\t-\n')
        fi
        epoch="${stat_line%%$'\t'*}"
        human="${stat_line#*$'\t'}"
        [[ "$epoch" =~ ^[0-9]+$ ]] || epoch=0
        if [[ "$human" == "$stat_line" || -z "$human" || "$epoch" -le 0 ]]; then
            human="-"
        fi

        if [[ "$worktree_path" == "$repo_root" ]]; then
            source="main"
            branch=$(_gwt_worktree_branch_label "$worktree_path")
        elif [[ -n "$codex_repo_root" && "$worktree_path" == "$codex_repo_root/"* ]]; then
            source="codex"
            branch=$(basename "$worktree_path")
        elif [[ "$worktree_path" == "$repo_root/.claude/worktrees/"* ]]; then
            source="claude"
            branch=$(basename "$worktree_path")
        elif [[ "$worktree_path" == "$repo_root/.worktrees/"* ]]; then
            source="local"
            branch=$(basename "$worktree_path")
        else
            source="git"
            branch=$(_gwt_worktree_branch_label "$worktree_path")
        fi
        printf '%s\t%s\t%s\t%s\t%s\n' "$epoch" "$worktree_path" "$human" "$branch" "$source"
    done < <(_gwt_discover_worktree_paths "$repo_root") | /usr/bin/sort -t$'\t' -k1,1nr
    ) || return 1

    [[ -z "$table" ]] || printf '%s\n' "$table"
    return 0
}

_gwt_default_start_point() {
    local requested="$1"
    if [[ -n "$requested" ]]; then
        printf '%s\n' "$requested"
        return 0
    fi

    local remote="origin"
    local remote_head=""
    if remote_head=$(git symbolic-ref --quiet --short "refs/remotes/$remote/HEAD" 2>/dev/null); then
        git fetch --quiet "$remote" "${remote_head#${remote}/}" >/dev/null 2>&1 || true
        if git show-ref --verify --quiet "refs/remotes/$remote_head"; then
            printf '%s\n' "$remote_head"
            return 0
        fi
    fi

    if git show-ref --verify --quiet "refs/remotes/$remote/main"; then
        git fetch --quiet "$remote" main >/dev/null 2>&1 || true
        printf '%s\n' "$remote/main"
        return 0
    fi

    local upstream=""
    if upstream=$(git rev-parse --abbrev-ref --symbolic-full-name "@{upstream}" 2>/dev/null); then
        local up_remote="${upstream%%/*}"
        local up_branch="${upstream#*/}"
        git fetch --quiet "$up_remote" "$up_branch" >/dev/null 2>&1 || true
        if git show-ref --verify --quiet "refs/remotes/$upstream"; then
            printf '%s\n' "$upstream"
            return 0
        fi
    fi

    printf 'HEAD\n'
}

_gwt_resolve_start_point() {
    local requested="$1"
    local candidate=""
    local remote=""
    local remote_branch=""
    local seen="|"
    local -a candidates

    if [[ -z "$requested" ]]; then
        _gwt_default_start_point ""
        return $?
    fi

    candidates=("$requested")
    if [[ "$requested" != */* && "$requested" != refs/* ]]; then
        candidates+=("origin/$requested")
    fi

    case "$requested" in
        main)
            candidates+=("master" "origin/main" "origin/master")
            ;;
        master)
            candidates+=("main" "origin/master" "origin/main")
            ;;
    esac

    for candidate in "${candidates[@]}"; do
        [[ -z "$candidate" ]] && continue
        if [[ "$seen" == *"|$candidate|"* ]]; then
            continue
        fi
        seen="${seen}${candidate}|"

        if [[ "$candidate" == */* && "$candidate" != refs/* ]]; then
            remote="${candidate%%/*}"
            remote_branch="${candidate#*/}"
            if [[ -n "$remote" && -n "$remote_branch" && "$remote" != "$candidate" ]]; then
                git fetch --quiet "$remote" "$remote_branch" >/dev/null 2>&1 || true
            fi
        fi

        if git rev-parse --verify --quiet "${candidate}^{commit}" >/dev/null 2>&1; then
            if [[ "$candidate" != "$requested" ]]; then
                echo "gwt: start-point '$requested' not found, using '$candidate'" >&2
            fi
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    echo "gwt: start-point '$requested' is not a valid ref or commit" >&2
    return 1
}

_gwt_is_pnpm_repo() {
    local repo_root="$1"

    [[ -f "$repo_root/pnpm-lock.yaml" || -f "$repo_root/pnpm-workspace.yaml" ]] && return 0
    [[ -f "$repo_root/package.json" ]] || return 1

    grep -Eq '"packageManager"[[:space:]]*:[[:space:]]*"pnpm(@|")' "$repo_root/package.json"
}

_gwt_shared_install_source() {
    local repo_root="$1"
    local main_worktree=""
    local worktrees_root=""

    main_worktree=$(git worktree list --porcelain | awk '/^worktree / {print substr($0, 10); exit}')
    if [[ -n "$main_worktree" && -d "$main_worktree/node_modules" ]]; then
        printf '%s\n' "$main_worktree"
        return 0
    fi

    worktrees_root="${DOTFILES_WORKTREES_ROOT:-$HOME/.codex/worktrees}"
    if [[ -d "$repo_root/node_modules" && "$repo_root" != "$worktrees_root/"* ]]; then
        printf '%s\n' "$repo_root"
        return 0
    fi

    return 1
}

_gwt_find_workspace_node_modules() {
    local repo_root="$1"

    find "$repo_root" \
        \( -path "$repo_root/.git" -o -path "$repo_root/.git/*" \) -prune -o \
        -type d -name node_modules -print -prune
}

_gwt_link_shared_node_modules() {
    local source_root="$1"
    local target_root="$2"
    local source_path relative_path target_path

    while IFS= read -r source_path; do
        [[ -n "$source_path" ]] || continue

        relative_path="${source_path#$source_root/}"
        target_path="$target_root/$relative_path"

        if [[ -L "$target_path" ]]; then
            continue
        fi

        if [[ -e "$target_path" ]]; then
            echo "gwt: refusing to replace existing path $target_path" >&2
            return 1
        fi

        mkdir -p "$(dirname "$target_path")" || return 1
        ln -s "$source_path" "$target_path" || return 1
    done < <(_gwt_find_workspace_node_modules "$source_root")
}

_gwt_bootstrap_worktree() {
    local worktree_path="$1"
    local install_source=""

    _gwt_is_pnpm_repo "$worktree_path" || return 0

    if ! install_source=$(_gwt_shared_install_source "$worktree_path"); then
        echo "gwt: pnpm repo detected, but no canonical node_modules tree exists yet" >&2
        echo "gwt: run pnpm install once in the main checkout, then create worktrees from there" >&2
        return 0
    fi

    if [[ "$install_source" == "$worktree_path" ]]; then
        return 0
    fi

    echo "gwt: linking shared pnpm install from $install_source"
    _gwt_link_shared_node_modules "$install_source" "$worktree_path" || return 1
}

# gwt: opinionated wrapper around `git worktree`.
# oh-my-zsh git plugin defines `gwt` alias; remove it so the function can load cleanly.
unalias gwt 2>/dev/null
gwt() {
    local subcommand="$1"
    shift || true

    case "$subcommand" in
        ""|help|-h|--help)
            cat <<'EOF'
Usage: gwt <command> [args]

Commands:
  gwt clone <repo> [dest] [--profile <name>|--full]
  gwt new <branch> [start-point]   Create/add worktree under ~/.codex/worktrees
  gwt new <branch> [start-point] [--profile <name>|--full]
  gwt ls [--raw|--plain|--color|--no-color]
  gwt audit [agent-worktree-clean args...]
  gwt clean [agent-worktree-maintain args...]
  gwt cd [branch|name|path]         Jump into a worktree (fzf picker when empty)
  gwt rm <branch|name|path> [--force] Remove a worktree safely
  gwt prune                         Prune stale worktree metadata
  gwt sparse status                 Show sparse-checkout state for the current worktree
  gwt sparse list                   List available sparse profiles for the current repo
  gwt sparse set <profile>          Apply a sparse profile to the current worktree
  gwt sparse add <path...>          Expand the current sparse checkout with extra paths
  gwt sparse full                   Disable sparse checkout for the current worktree
  gwt root                          Print configured worktree root
EOF
            ;;
        root)
            printf '%s\n' "${DOTFILES_WORKTREES_ROOT:-$HOME/.codex/worktrees}"
            ;;
        clone)
            local repo_url="" dest="" profile=""
            local clone_slug="" clone_filter=""
            local clone_profile_dir=""
            local use_sparse=true
            local -a clone_args

            while [[ $# -gt 0 ]]; do
                case "$1" in
                    --profile)
                        profile="$2"
                        shift 2
                        ;;
                    --full|--no-sparse)
                        profile="full"
                        use_sparse=false
                        shift
                        ;;
                    -*)
                        echo "Usage: gwt clone <repo> [dest] [--profile <name>|--full]"
                        return 1
                        ;;
                    *)
                        if [[ -z "$repo_url" ]]; then
                            repo_url="$1"
                        elif [[ -z "$dest" ]]; then
                            dest="$1"
                        else
                            echo "Usage: gwt clone <repo> [dest] [--profile <name>|--full]"
                            return 1
                        fi
                        shift
                        ;;
                esac
            done

            if [[ -z "$repo_url" ]]; then
                echo "Usage: gwt clone <repo> [dest] [--profile <name>|--full]"
                return 1
            fi

            clone_slug=$(_gwt_slug_from_origin "$repo_url" 2>/dev/null || _gwt_clone_dest_name "$repo_url")
            [[ -n "$dest" ]] || dest=$(_gwt_clone_dest_name "$repo_url")
            if [[ -e "$dest" ]]; then
                echo "gwt: destination already exists at $dest"
                return 1
            fi

            clone_args=(clone)
            clone_filter=$(_gwt_sparse_clone_filter "$clone_slug" 2>/dev/null || true)
            clone_profile_dir=$(_gwt_sparse_repo_dir_from_slug "$clone_slug")
            if [[ -n "$clone_filter" ]]; then
                clone_args+=("--filter=$clone_filter")
            fi

            if [[ -n "$profile" && "$profile" != "full" ]]; then
                clone_args+=(--sparse)
            elif [[ "$use_sparse" == true && -d "$clone_profile_dir" ]]; then
                clone_args+=(--sparse)
            fi

            git "${clone_args[@]}" "$repo_url" "$dest" || return 1
            dest=$(cd "$dest" && pwd) || return 1

            if [[ -n "$profile" ]]; then
                _gwt_sparse_apply_profile "$dest" "$profile" || return 1
            else
                _gwt_sparse_apply_default_profile "$dest" || return 1
            fi

            cd "$dest" || return 1
            ;;
        *)
            if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
                echo "gwt: not inside a git repository"
                return 1
            fi
            ;;
    esac

    case "$subcommand" in
        ""|help|-h|--help|root|clone)
            ;;
        ls|list)
            local repo_root list_table color_mode="auto"
            repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
            while [[ $# -gt 0 ]]; do
                case "$1" in
                    --raw)
                        color_mode="raw"
                        shift
                        ;;
                    --plain|--no-color)
                        color_mode="never"
                        shift
                        ;;
                    --color)
                        color_mode="always"
                        shift
                        ;;
                    *)
                        echo "Usage: gwt ls [--raw|--plain|--color|--no-color]"
                        return 1
                        ;;
                esac
            done
            list_table=$(_gwt_fzf_table "$repo_root") || return 1
            if [[ "$color_mode" == "raw" ]]; then
                printf '%s\n' "$list_table"
            else
                _gwt_render_ls "$repo_root" "$list_table" "$color_mode"
            fi
            ;;
        audit)
            local repo_root cleaner_path audit_table worktree_count
            repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
            cleaner_path=$(_gwt_tool_path agent-worktree-clean) || {
                echo "gwt: agent-worktree-clean not found" >&2
                return 1
            }
            audit_table=$(_gwt_fzf_table "$repo_root" 2>/dev/null || true)
            if [[ -n "$audit_table" ]]; then
                worktree_count=$(printf '%s\n' "$audit_table" | awk 'NF {count++} END {print count+0}')
            else
                worktree_count=0
            fi
            printf 'repo=%s\n' "$repo_root"
            printf 'free=%s\n' "$(df -h "$repo_root" | awk 'NR==2 {print $4}')"
            printf 'worktrees=%s\n' "$worktree_count"
            if [[ -n "$audit_table" ]]; then
                printf 'by_source=%s\n' "$(printf '%s\n' "$audit_table" | awk -F'\t' 'NF {count[$5]++} END { first=1; for (source in count) { if (!first) printf ", "; printf "%s=%d", source, count[source]; first=0 } }')"
                printf '\n'
                _gwt_render_ls "$repo_root" "$(printf '%s\n' "$audit_table" | sed -n '1,12p')" "auto"
            fi
            printf '\n'
            "$cleaner_path" --repo "$repo_root" "$@" || return 1
            ;;
        clean)
            local repo_root maintainer_path
            repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
            maintainer_path=$(_gwt_tool_path agent-worktree-maintain) || {
                echo "gwt: agent-worktree-maintain not found" >&2
                return 1
            }
            "$maintainer_path" --repo "$repo_root" --force "$@" || return 1
            ;;
        new|add)
            local branch=""
            local start_point=""
            local profile=""
            local root repo_slug branch_slug worktree_parent worktree_path

            while [[ $# -gt 0 ]]; do
                case "$1" in
                    --profile)
                        profile="$2"
                        shift 2
                        ;;
                    --full|--no-sparse)
                        profile="full"
                        shift
                        ;;
                    *)
                        if [[ -z "$branch" ]]; then
                            branch="$1"
                        elif [[ -z "$start_point" ]]; then
                            start_point="$1"
                        else
                            echo "Usage: gwt new <branch> [start-point] [--profile <name>|--full]"
                            return 1
                        fi
                        shift
                        ;;
                esac
            done

            if [[ -z "$branch" ]]; then
                echo "Usage: gwt new <branch> [start-point] [--profile <name>|--full]"
                return 1
            fi

            root="${DOTFILES_WORKTREES_ROOT:-$HOME/.codex/worktrees}"
            repo_slug=$(_gwt_repo_slug) || return 1
            branch_slug=$(printf '%s' "$branch" | tr '/' '-' | tr -cd '[:alnum:]._-')
            [[ -z "$branch_slug" ]] && branch_slug="$branch"

            worktree_parent="$root/$repo_slug"
            worktree_path="$worktree_parent/$branch_slug"
            mkdir -p "$worktree_parent" || return 1

            if [[ -d "$worktree_path" ]]; then
                echo "gwt: worktree already exists at $worktree_path"
                cd "$worktree_path" || return 1
                return 0
            fi

            if git show-ref --verify --quiet "refs/heads/$branch"; then
                git worktree add "$worktree_path" "$branch" || return 1
            else
                local resolved_start_point
                resolved_start_point=$(_gwt_resolve_start_point "$start_point") || return 1
                git worktree add -b "$branch" "$worktree_path" "$resolved_start_point" || return 1
            fi

            _gwt_sparse_apply_default_profile "$worktree_path" "$profile" || return 1
            _gwt_bootstrap_worktree "$worktree_path" || return 1
            cd "$worktree_path" || return 1
            ;;
        cd)
            local target="$1"
            local selected target_path

            if [[ -z "$target" ]]; then
                if command -v fzf > /dev/null 2>&1; then
                    selected=$(_gwt_fzf_table "$(git rev-parse --show-toplevel 2>/dev/null)" | fzf \
                        --delimiter=$'\t' \
                        --with-nth=2,3,4,5 \
                        --prompt='worktree> ' \
                        --header=$'PATH\tLAST MOD\tBRANCH\tSOURCE')
                    [[ -z "$selected" ]] && return 1
                    target=$(printf '%s\n' "$selected" | cut -f2)
                else
                    echo "Usage: gwt cd <branch|name|path> (install fzf for interactive picker)"
                    return 1
                fi
            fi

            if ! target_path=$(_gwt_find_path "$target"); then
                echo "gwt: no matching worktree found for '$target'"
                return 1
            fi

            cd "$target_path" || return 1
            ;;
        rm|remove)
            local target=""
            local force_remove=false
            local arg target_path main_worktree

            for arg in "$@"; do
                case "$arg" in
                    --force|-f) force_remove=true ;;
                    *) target="$arg" ;;
                esac
            done

            if [[ -z "$target" ]]; then
                echo "Usage: gwt rm <branch|name|path> [--force]"
                return 1
            fi

            if ! target_path=$(_gwt_find_path "$target"); then
                echo "gwt: no matching worktree found for '$target'"
                return 1
            fi

            main_worktree=$(git worktree list | head -n 1 | awk '{print $1}')
            if [[ "$target_path" == "$main_worktree" ]]; then
                echo "gwt: refusing to remove the main worktree ($target_path)"
                return 1
            fi

            if [[ "$(pwd)/" == "$target_path/"* ]]; then
                echo "gwt: cannot remove the worktree you are currently in"
                return 1
            fi

            if ! $force_remove && [[ -n "$(git -C "$target_path" status --porcelain 2>/dev/null)" ]]; then
                echo "gwt: worktree has uncommitted changes. Re-run with --force to remove."
                return 1
            fi

            if $force_remove; then
                git worktree remove --force "$target_path" || return 1
            else
                git worktree remove "$target_path" || return 1
            fi

            git worktree prune
            ;;
        prune)
            git worktree prune
            ;;
        sparse)
            local sparse_action="${1:-status}"
            shift || true
            case "$sparse_action" in
                status|show)
                    _gwt_sparse_status || return 1
                    ;;
                list|profiles)
                    _gwt_sparse_list_profiles "$(git rev-parse --show-toplevel)" || return 1
                    ;;
                set)
                    [[ -n "$1" ]] || {
                        echo "Usage: gwt sparse set <profile>"
                        return 1
                    }
                    _gwt_sparse_apply_profile "$(git rev-parse --show-toplevel)" "$1" || return 1
                    ;;
                add|expand)
                    _gwt_sparse_add_paths "$(git rev-parse --show-toplevel)" "$@" || return 1
                    ;;
                full|disable)
                    _gwt_sparse_apply_profile "$(git rev-parse --show-toplevel)" "full" || return 1
                    ;;
                *)
                    echo "Usage: gwt sparse <status|list|set|add|full>"
                    return 1
                    ;;
            esac
            ;;
        *)
            echo "gwt: unknown command '$subcommand' (run 'gwt help')"
            return 1
            ;;
    esac
}
