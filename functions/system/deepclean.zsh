deepclean() {
    local apply=0
    local skip_mole=0
    local repo=""
    local root="${DOTFILES_WORKTREES_ROOT:-$HOME/.codex/worktrees}"
    local -a repo_roots

    while (( $# )); do
        case "$1" in
            --apply) apply=1 ;;
            --dry-run) apply=0 ;;
            --skip-mole) skip_mole=1 ;;
            --repo)
                shift
                repo="${1:-}"
                [[ -n "$repo" ]] || { echo "deepclean: --repo requires a path" >&2; return 2; }
                ;;
            -h|--help)
                cat <<'EOF'
Usage: deepclean [--dry-run|--apply] [--repo <path>] [--skip-mole]

Preview or apply safe recurring cleanup:
  - audit/maintain Codex agent worktrees with repository-native retention rules;
  - run Mole project/cache cleanup on macOS.

The default is --dry-run. This command does not kill Codex, Claude, tmux,
terminal, mosh, SSH, Crabbox, Blacksmith, or Testbox processes.
EOF
                return 0
                ;;
            *) echo "deepclean: unknown argument: $1" >&2; return 2 ;;
        esac
        shift
    done

    if [[ -n "$repo" ]]; then
        repo_roots=("${repo:A}")
    elif [[ -d "$root" ]]; then
        repo_roots=("${(@f)$(find "$root" -mindepth 2 -maxdepth 2 -type d 2>/dev/null | while IFS= read -r candidate; do
            common=$(git -C "$candidate" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || continue
            [[ "${common:t}" == .git ]] && common="${common:h}"
            printf '%s\n' "$common"
        done | sort -u)}")
    fi

    echo "deepclean mode=$([[ $apply == 1 ]] && echo apply || echo preview)"
    echo "worktree_root=$root"
    echo "repositories=${#repo_roots}"

    local repo_root
    for repo_root in "${repo_roots[@]}"; do
        [[ -n "$repo_root" ]] || continue
        echo
        echo "== worktrees: $repo_root =="
        if (( apply )); then
            if command -v agent-worktree-maintain >/dev/null 2>&1; then
                agent-worktree-maintain --repo "$repo_root" --force || return
            else
                echo "deepclean: agent-worktree-maintain missing" >&2
                return 127
            fi
        elif command -v agent-worktree-clean >/dev/null 2>&1; then
            agent-worktree-clean --repo "$repo_root" || return
        elif command -v gwt >/dev/null 2>&1; then
            (DOTFILES_CD_SKIP_LISTING=1; builtin cd "$repo_root" && gwt audit) || return
        else
            echo "deepclean: worktree audit tools missing" >&2
            return 127
        fi
    done

    if [[ "$OSTYPE" == darwin* && $skip_mole == 0 ]]; then
        echo
        echo "== mole =="
        if ! command -v mole >/dev/null 2>&1; then
            echo "deepclean: mole missing; skipping" >&2
        elif (( apply )); then
            mole clean || return
            mole purge || return
        else
            mole clean --dry-run || return
            mole purge --dry-run || return
        fi
    fi

    echo
    echo "deepclean complete"
}
