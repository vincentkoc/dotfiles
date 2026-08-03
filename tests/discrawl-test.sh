#!/usr/bin/env bash
# shellcheck disable=SC1091,SC2016,SC2034,SC2329

set -u

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
wrapper="$repo_root/bin/discrawl"
installer="$repo_root/install.sh"
tmp_root="$(mktemp -d)"
tests_run=0
tests_failed=0

cleanup() {
    rm -rf "$tmp_root"
}
trap cleanup EXIT

pass() {
    tests_run=$((tests_run + 1))
    printf 'ok %d - %s\n' "$tests_run" "$1"
}

fail() {
    tests_run=$((tests_run + 1))
    tests_failed=$((tests_failed + 1))
    printf 'not ok %d - %s\n' "$tests_run" "$1"
}

make_platform_path() {
    local directory="$1"
    local platform="$2"

    mkdir -p "$directory"
    ln -sf /bin/bash "$directory/bash"
    cat >"$directory/uname" <<EOF
#!/bin/bash
printf '%s\n' '$platform'
EOF
    chmod +x "$directory/uname"
}

make_backend() {
    local path="$1"
    local body="$2"

    mkdir -p "$(dirname "$path")"
    {
        printf '%s\n' '#!/bin/bash'
        printf '%s\n' "$body"
    } >"$path"
    chmod +x "$path"
}

run_wrapper_test() {
    local name="$1"
    local script="$2"
    local case_dir="$tmp_root/case-$tests_run"
    local status

    mkdir -p "$case_dir"
    if (
        set -u
        CASE_DIR="$case_dir"
        export CASE_DIR
        eval "$script"
    ); then
        status=0
    else
        status=$?
    fi

    if [[ $status -eq 0 ]]; then
        pass "$name"
    else
        fail "$name"
    fi
}

run_wrapper_test "preserves argument boundaries" '
    make_platform_path "$CASE_DIR/path" Linux
    make_backend "$CASE_DIR/path/discrawl" '"'"'
        [[ $# -eq 3 ]] || exit 80
        [[ $1 == "" ]] || exit 81
        [[ $2 == "two words" ]] || exit 82
        [[ $3 == "x*y" ]] || exit 83
    '"'"'
    HOME="$CASE_DIR/home" PATH="$CASE_DIR/path" "$wrapper" "" "two words" "x*y"
'

run_wrapper_test "explicit token wins and skips the env file" '
    make_platform_path "$CASE_DIR/path" Linux
    make_backend "$CASE_DIR/path/discrawl" '"'"'
        [[ ${DISCRAWL_REMOTE_TOKEN:-} == explicit-token ]]
    '"'"'
    printf "%s\n" "this is not valid shell (" >"$CASE_DIR/crawl.env"
    HOME="$CASE_DIR/home" PATH="$CASE_DIR/path" \
        OPENCLAW_CRAWL_ENV="$CASE_DIR/crawl.env" \
        DISCRAWL_REMOTE_TOKEN=explicit-token "$wrapper"
'

run_wrapper_test "loads the default crawl environment" '
    make_platform_path "$CASE_DIR/path" Linux
    make_backend "$CASE_DIR/path/discrawl" '"'"'
        [[ ${DISCRAWL_REMOTE_TOKEN:-} == default-token ]]
    '"'"'
    mkdir -p "$CASE_DIR/home/.config/openclaw-crawl"
    {
        printf "%s\n" "DISCRAWL_REMOTE_TOKEN=default-token"
        printf "%s\n" "printf \"%s\\n\" default-token"
        printf "%s\n" "printf \"%s\\n\" \"\$OPENCLAW_CRAWL_ENV\" >&2"
    } >"$CASE_DIR/home/.config/openclaw-crawl/remote.env"
    HOME="$CASE_DIR/home" PATH="$CASE_DIR/path" "$wrapper" \
        >"$CASE_DIR/stdout" 2>"$CASE_DIR/stderr"
    [[ ! -s "$CASE_DIR/stdout" ]]
    [[ ! -s "$CASE_DIR/stderr" ]]
'

run_wrapper_test "honors XDG_CONFIG_HOME" '
    make_platform_path "$CASE_DIR/path" Linux
    make_backend "$CASE_DIR/path/discrawl" '"'"'
        [[ ${DISCRAWL_REMOTE_TOKEN:-} == xdg-token ]]
    '"'"'
    mkdir -p "$CASE_DIR/xdg/openclaw-crawl"
    printf "%s\n" "DISCRAWL_REMOTE_TOKEN=xdg-token" >"$CASE_DIR/xdg/openclaw-crawl/remote.env"
    HOME="$CASE_DIR/home" XDG_CONFIG_HOME="$CASE_DIR/xdg" PATH="$CASE_DIR/path" "$wrapper"
'

run_wrapper_test "honors OPENCLAW_CRAWL_ENV" '
    make_platform_path "$CASE_DIR/path" Linux
    make_backend "$CASE_DIR/path/discrawl" '"'"'
        [[ ${DISCRAWL_REMOTE_TOKEN:-} == override-token ]]
    '"'"'
    printf "%s\n" "DISCRAWL_REMOTE_TOKEN=override-token" >"$CASE_DIR/override.env"
    HOME="$CASE_DIR/home" OPENCLAW_CRAWL_ENV="$CASE_DIR/override.env" \
        PATH="$CASE_DIR/path" "$wrapper"
'

run_wrapper_test "missing env file is allowed" '
    make_platform_path "$CASE_DIR/path" Linux
    make_backend "$CASE_DIR/path/discrawl" '"'"'
        [[ ! ${DISCRAWL_REMOTE_TOKEN+x} ]]
    '"'"'
    HOME="$CASE_DIR/home" OPENCLAW_CRAWL_ENV="$CASE_DIR/missing.env" \
        PATH="$CASE_DIR/path" "$wrapper"
'

run_wrapper_test "malformed env fails without running the backend" '
    make_platform_path "$CASE_DIR/path" Linux
    make_backend "$CASE_DIR/path/discrawl" '"'"'
        touch "$CASE_DIR/backend-ran"
    '"'"'
    printf "%s\n" "DISCRAWL_REMOTE_TOKEN=(" >"$CASE_DIR/bad.env"
    set +e
    HOME="$CASE_DIR/home" OPENCLAW_CRAWL_ENV="$CASE_DIR/bad.env" \
        PATH="$CASE_DIR/path" "$wrapper" >"$CASE_DIR/stdout" 2>"$CASE_DIR/stderr"
    status=$?
    set -e
    [[ $status -ne 0 ]]
    [[ ! -e "$CASE_DIR/backend-ran" ]]
    [[ ! -s "$CASE_DIR/stdout" ]]
    ! grep -F "$CASE_DIR" "$CASE_DIR/stderr"
'

run_wrapper_test "symlink recursion exits 127" '
    make_platform_path "$CASE_DIR/path" Linux
    ln -s "$wrapper" "$CASE_DIR/path/discrawl"
    set +e
    HOME="$CASE_DIR/home" PATH="$CASE_DIR/path" "$CASE_DIR/path/discrawl" \
        >"$CASE_DIR/stdout" 2>"$CASE_DIR/stderr"
    status=$?
    set -e
    [[ $status -eq 127 ]]
    [[ ! -s "$CASE_DIR/stdout" ]]
    grep -Fx "discrawl: no usable backend found" "$CASE_DIR/stderr" >/dev/null
'

run_wrapper_test "Linux PATH finds a real local backend" '
    make_platform_path "$CASE_DIR/tools" Linux
    make_backend "$CASE_DIR/home/.local/bin/discrawl" '"'"'
        printf "%s\n" local-backend
    '"'"'
    output="$(HOME="$CASE_DIR/home" PATH="$CASE_DIR/home/.local/bin:$CASE_DIR/tools" "$wrapper")"
    [[ $output == local-backend ]]
'

run_wrapper_test "Darwin prefers Homebrew over PATH" '
    make_platform_path "$CASE_DIR/path" Darwin
    make_backend "$CASE_DIR/opt/discrawl" '"'"'printf "%s\n" preferred'"'"'
    make_backend "$CASE_DIR/path/discrawl" '"'"'printf "%s\n" fallback'"'"'
    sed \
        -e "s#/opt/homebrew/bin/discrawl#$CASE_DIR/opt/discrawl#g" \
        -e "s#/usr/local/bin/discrawl#$CASE_DIR/usr-local/discrawl#g" \
        "$wrapper" >"$CASE_DIR/wrapper"
    chmod +x "$CASE_DIR/wrapper"
    output="$(HOME="$CASE_DIR/home" PATH="$CASE_DIR/path" "$CASE_DIR/wrapper")"
    [[ $output == preferred ]]
'

run_wrapper_test "Darwin falls back to PATH" '
    make_platform_path "$CASE_DIR/path" Darwin
    make_backend "$CASE_DIR/path/discrawl" '"'"'printf "%s\n" fallback'"'"'
    sed \
        -e "s#/opt/homebrew/bin/discrawl#$CASE_DIR/missing-opt/discrawl#g" \
        -e "s#/usr/local/bin/discrawl#$CASE_DIR/missing-local/discrawl#g" \
        "$wrapper" >"$CASE_DIR/wrapper"
    chmod +x "$CASE_DIR/wrapper"
    output="$(HOME="$CASE_DIR/home" PATH="$CASE_DIR/path" "$CASE_DIR/wrapper")"
    [[ $output == fallback ]]
'

run_wrapper_test "no backend exits 127 with plain stderr" '
    make_platform_path "$CASE_DIR/path" Linux
    set +e
    HOME="$CASE_DIR/home" PATH="$CASE_DIR/path" "$wrapper" \
        >"$CASE_DIR/stdout" 2>"$CASE_DIR/stderr"
    status=$?
    set -e
    [[ $status -eq 127 ]]
    [[ ! -s "$CASE_DIR/stdout" ]]
    grep -Fx "discrawl: no usable backend found" "$CASE_DIR/stderr" >/dev/null
'

run_installer_test() {
    local name="$1"
    local script="$2"
    local case_dir="$tmp_root/installer-$tests_run"
    local status

    mkdir -p "$case_dir"
    sed \
        -e "s#/opt/homebrew/bin/discrawl#$case_dir/opt/discrawl#g" \
        -e "s#/usr/local/bin/discrawl#$case_dir/usr-local/discrawl#g" \
        "$installer" >"$case_dir/install.sh"
    if (
        set -u
        CASE_DIR="$case_dir"
        export CASE_DIR
        source "$case_dir/install.sh"
        dotfiles_dir() { printf "%s\n" "$CASE_DIR/dotfiles"; }
        eval "$script"
    ); then
        status=0
    else
        status=$?
    fi

    if [[ $status -eq 0 ]]; then
        pass "$name"
    else
        fail "$name"
    fi
}

run_installer_test "installer backs up and replaces an existing command" '
    make_platform_path "$CASE_DIR/path" Darwin
    mkdir -p "$CASE_DIR/dotfiles/bin" "$CASE_DIR/home/.local/bin"
    cp "$wrapper" "$CASE_DIR/dotfiles/bin/discrawl"
    chmod +x "$CASE_DIR/dotfiles/bin/discrawl"
    make_backend "$CASE_DIR/path/discrawl" "exit 0"
    printf "%s\n" old-command >"$CASE_DIR/home/.local/bin/discrawl"
    HOME="$CASE_DIR/home" PATH="$CASE_DIR/path:/usr/bin:/bin" setup_discrawl_shim
    [[ "$CASE_DIR/home/.local/bin/discrawl" -ef "$CASE_DIR/dotfiles/bin/discrawl" ]]
    backup="$(find "$CASE_DIR/home/.local/bin" -name "discrawl.pre-dotfiles.*.bak" -print -quit)"
    [[ -n "$backup" ]]
    grep -Fx old-command "$backup" >/dev/null
'

run_installer_test "installer preserves an existing symlink as a backup" '
    make_platform_path "$CASE_DIR/path" Darwin
    mkdir -p "$CASE_DIR/dotfiles/bin" "$CASE_DIR/home/.local/bin"
    cp "$wrapper" "$CASE_DIR/dotfiles/bin/discrawl"
    chmod +x "$CASE_DIR/dotfiles/bin/discrawl"
    make_backend "$CASE_DIR/path/discrawl" "exit 0"
    printf "%s\n" old-target >"$CASE_DIR/old-target"
    ln -s "$CASE_DIR/old-target" "$CASE_DIR/home/.local/bin/discrawl"
    HOME="$CASE_DIR/home" PATH="$CASE_DIR/path:/usr/bin:/bin" setup_discrawl_shim
    backup="$(find "$CASE_DIR/home/.local/bin" -name "discrawl.pre-dotfiles.*.bak" -print -quit)"
    [[ -L "$backup" ]]
    [[ "$(readlink "$backup")" == "$CASE_DIR/old-target" ]]
'

run_installer_test "installer replaces a destination symlink to the preferred backend" '
    make_platform_path "$CASE_DIR/path" Darwin
    mkdir -p "$CASE_DIR/dotfiles/bin" "$CASE_DIR/home/.local/bin"
    cp "$wrapper" "$CASE_DIR/dotfiles/bin/discrawl"
    chmod +x "$CASE_DIR/dotfiles/bin/discrawl"
    make_backend "$CASE_DIR/opt/discrawl" "exit 0"
    ln -s "$CASE_DIR/opt/discrawl" "$CASE_DIR/home/.local/bin/discrawl"
    HOME="$CASE_DIR/home" PATH="$CASE_DIR/path:/usr/bin:/bin" setup_discrawl_shim
    [[ "$CASE_DIR/home/.local/bin/discrawl" -ef "$CASE_DIR/dotfiles/bin/discrawl" ]]
    backup="$(find "$CASE_DIR/home/.local/bin" -name "discrawl.pre-dotfiles.*.bak" -print -quit)"
    [[ -L "$backup" ]]
    [[ "$(readlink "$backup")" == "$CASE_DIR/opt/discrawl" ]]
'

run_installer_test "installer ignores the destination backend through a relative PATH" '
    make_platform_path "$CASE_DIR/path" Darwin
    mkdir -p "$CASE_DIR/dotfiles/bin" "$CASE_DIR/home/.local/bin"
    cp "$wrapper" "$CASE_DIR/dotfiles/bin/discrawl"
    chmod +x "$CASE_DIR/dotfiles/bin/discrawl"
    make_backend "$CASE_DIR/home/.local/bin/discrawl" "exit 0"
    (
        cd "$CASE_DIR/home"
        HOME="$CASE_DIR/home" PATH="$CASE_DIR/path:.local/bin/:/usr/bin:/bin" \
            setup_discrawl_shim >"$CASE_DIR/stdout" 2>"$CASE_DIR/stderr"
    )
    grep -F "Skipping Discrawl wrapper: no usable backend found" "$CASE_DIR/stdout" >/dev/null
    [[ -f "$CASE_DIR/home/.local/bin/discrawl" ]]
    [[ ! -L "$CASE_DIR/home/.local/bin/discrawl" ]]
    ! find "$CASE_DIR/home/.local/bin" -name "*.bak*" -print -quit | grep -q .
    [[ ! -s "$CASE_DIR/stderr" ]]
'

run_installer_test "installer ignores the destination through a symlinked PATH directory" '
    make_platform_path "$CASE_DIR/path" Darwin
    mkdir -p "$CASE_DIR/dotfiles/bin" "$CASE_DIR/home/.local/bin"
    cp "$wrapper" "$CASE_DIR/dotfiles/bin/discrawl"
    chmod +x "$CASE_DIR/dotfiles/bin/discrawl"
    make_backend "$CASE_DIR/backend/discrawl" "exit 0"
    ln -s "$CASE_DIR/backend/discrawl" "$CASE_DIR/home/.local/bin/discrawl"
    ln -s "$CASE_DIR/home/.local/bin" "$CASE_DIR/alias-bin"
    HOME="$CASE_DIR/home" PATH="$CASE_DIR/path:$CASE_DIR/alias-bin:/usr/bin:/bin" \
        setup_discrawl_shim >"$CASE_DIR/stdout" 2>"$CASE_DIR/stderr"
    grep -F "Skipping Discrawl wrapper: no usable backend found" "$CASE_DIR/stdout" >/dev/null
    [[ -L "$CASE_DIR/home/.local/bin/discrawl" ]]
    [[ "$(readlink "$CASE_DIR/home/.local/bin/discrawl")" == "$CASE_DIR/backend/discrawl" ]]
    ! find "$CASE_DIR/home/.local/bin" -name "*.bak*" -print -quit | grep -q .
    [[ ! -s "$CASE_DIR/stderr" ]]
'

run_installer_test "installer no-ops when the wrapper is already linked" '
    make_platform_path "$CASE_DIR/path" Darwin
    mkdir -p "$CASE_DIR/dotfiles/bin" "$CASE_DIR/home/.local/bin"
    cp "$wrapper" "$CASE_DIR/dotfiles/bin/discrawl"
    chmod +x "$CASE_DIR/dotfiles/bin/discrawl"
    ln -s "$CASE_DIR/dotfiles/bin/discrawl" "$CASE_DIR/home/.local/bin/discrawl"
    HOME="$CASE_DIR/home" PATH="$CASE_DIR/path:/usr/bin:/bin" setup_discrawl_shim
    [[ "$CASE_DIR/home/.local/bin/discrawl" -ef "$CASE_DIR/dotfiles/bin/discrawl" ]]
'

run_installer_test "installer warns and skips replacement without a backend" '
    make_platform_path "$CASE_DIR/path" Darwin
    mkdir -p "$CASE_DIR/dotfiles/bin" "$CASE_DIR/home/.local/bin"
    cp "$wrapper" "$CASE_DIR/dotfiles/bin/discrawl"
    chmod +x "$CASE_DIR/dotfiles/bin/discrawl"
    printf "%s\n" keep-me >"$CASE_DIR/home/.local/bin/discrawl"
    HOME="$CASE_DIR/home" PATH="$CASE_DIR/path:/usr/bin:/bin" setup_discrawl_shim \
        >"$CASE_DIR/stdout" 2>"$CASE_DIR/stderr"
    grep -Fx keep-me "$CASE_DIR/home/.local/bin/discrawl" >/dev/null
    ! find "$CASE_DIR/home/.local/bin" -name "*.bak*" -print -quit | grep -q .
    grep -F "Skipping Discrawl wrapper: no usable backend found" "$CASE_DIR/stdout" >/dev/null
    [[ ! -s "$CASE_DIR/stderr" ]]
'

printf '1..%d\n' "$tests_run"
if [[ $tests_failed -ne 0 ]]; then
    printf '%d test(s) failed\n' "$tests_failed" >&2
    exit 1
fi
