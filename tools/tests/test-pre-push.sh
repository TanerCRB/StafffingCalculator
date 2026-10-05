#!/usr/bin/env sh
# Integration checks for .githooks/pre-push. Run with `sh tools/tests/test-pre-push.sh`.
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)
hook="$repo_root/.githooks/pre-push"
tmp_root=$(mktemp -d "${TMPDIR:-/tmp}/staffing-pre-push.XXXXXX")
trap 'rm -rf "$tmp_root"' EXIT HUP INT TERM

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_contains() {
    case "$1" in
        *"$2"*) ;;
        *) fail "expected output to contain: $2" ;;
    esac
}

make_fixture() {
    fixture="$tmp_root/$1"
    main="$fixture/main"
    checkout="$fixture/checkout"
    fake_bin="$fixture/bin"
    mkdir -p "$main/.git" "$checkout/backend" "$checkout/frontend" "$fake_bin"
    : > "$checkout/backend/pyproject.toml"
    printf '{"scripts":{"test":"vitest run"}}\n' > "$checkout/frontend/package.json"
    cat > "$fake_bin/git" <<'FAKE_GIT'
#!/bin/sh
case "${1:-} ${2:-}" in
    "rev-parse --show-toplevel") printf '%s\n' "$HOOK_REPO_ROOT" ;;
    "rev-parse --git-common-dir") printf '%s\n' "$HOOK_COMMON_GIT_DIR" ;;
    *) exit 2 ;;
esac
FAKE_GIT
    cat > "$fake_bin/node" <<'FAKE_NODE'
#!/bin/sh
if [ "${1:-}" = "-e" ]; then exit 0; fi
exit 0
FAKE_NODE
    cat > "$fake_bin/pnpm" <<'FAKE_PNPM'
#!/bin/sh
printf '%s\n' "$*" >> "$HOOK_CALL_LOG"
exit "${HOOK_PNPM_EXIT:-0}"
FAKE_PNPM
    chmod +x "$fake_bin/git" "$fake_bin/node" "$fake_bin/pnpm"
    HOOK_REPO_ROOT=$checkout
    HOOK_COMMON_GIT_DIR=$main/.git
    HOOK_CALL_LOG=$fixture/calls.log
    HOOK_INPUT=
    PATH=$fake_bin:$PATH
    export HOOK_REPO_ROOT HOOK_COMMON_GIT_DIR HOOK_CALL_LOG HOOK_INPUT PATH
}

run_hook() {
    hook_output=$(printf '%s\n' "${HOOK_INPUT:-}" | sh "$hook" 2>&1) && hook_status=0 || hook_status=$?
}

# A POSIX virtualenv in the main checkout is found from a linked worktree.
make_fixture posix-worktree
mkdir -p "$main/backend/.venv/bin" "$checkout/frontend/node_modules"
cat > "$main/backend/.venv/bin/pytest" <<'FAKE_PYTEST'
#!/bin/sh
printf 'pytest\n' >> "$HOOK_CALL_LOG"
exit "${HOOK_PYTEST_EXIT:-0}"
FAKE_PYTEST
chmod +x "$main/backend/.venv/bin/pytest"
HOOK_PYTEST_EXIT=0 HOOK_PNPM_EXIT=0
export HOOK_PYTEST_EXIT HOOK_PNPM_EXIT
run_hook
[ "$hook_status" -eq 0 ] || fail "passing checks returned $hook_status: $hook_output"
assert_contains "$(cat "$HOOK_CALL_LOG")" "pytest"
assert_contains "$(cat "$HOOK_CALL_LOG")" "test"
HOOK_PYTEST_EXIT=1
export HOOK_PYTEST_EXIT
run_hook
[ "$hook_status" -ne 0 ] || fail "failing pytest returned zero"

# A Windows Scripts layout is searched as well as POSIX bin/pytest.
make_fixture windows-layout
mkdir -p "$checkout/backend/.venv/Scripts" "$checkout/frontend/node_modules"
cat > "$checkout/backend/.venv/Scripts/pytest" <<'FAKE_PYTEST'
#!/bin/sh
printf 'windows-pytest\n' >> "$HOOK_CALL_LOG"
exit 0
FAKE_PYTEST
chmod +x "$checkout/backend/.venv/Scripts/pytest"
HOOK_PYTEST_EXIT=0 HOOK_PNPM_EXIT=0
export HOOK_PYTEST_EXIT HOOK_PNPM_EXIT
run_hook
[ "$hook_status" -eq 0 ] || fail "Windows Scripts layout failed: $hook_output"
assert_contains "$(cat "$HOOK_CALL_LOG")" "windows-pytest"

# Git for Windows returns drive-letter paths; resolving the path from the checkout normalizes it.
if command -v cygpath >/dev/null 2>&1; then
    HOOK_REPO_ROOT=$(cygpath -w "$HOOK_REPO_ROOT")
    HOOK_COMMON_GIT_DIR=$(cygpath -w "$HOOK_COMMON_GIT_DIR")
    export HOOK_REPO_ROOT HOOK_COMMON_GIT_DIR
    rm -f "$HOOK_CALL_LOG"
    run_hook
    [ "$hook_status" -eq 0 ] || fail "Windows Git directory path failed: $hook_output"
    assert_contains "$(cat "$HOOK_CALL_LOG")" "windows-pytest"
fi

# An unavailable backend environment gives an actionable setup command.
make_fixture missing-pytest
HOOK_PYTEST_EXIT=0
export HOOK_PYTEST_EXIT
run_hook
[ "$hook_status" -ne 0 ] || fail "missing pytest returned zero"
assert_contains "$hook_output" "uv sync --extra dev && uv run pytest"

# Missing frontend dependencies produce install guidance and stop before pnpm test.
make_fixture missing-frontend
mkdir -p "$checkout/backend/.venv/bin"
cat > "$checkout/backend/.venv/bin/pytest" <<'FAKE_PYTEST'
#!/bin/sh
exit 0
FAKE_PYTEST
chmod +x "$checkout/backend/.venv/bin/pytest"
HOOK_PYTEST_EXIT=0 HOOK_PNPM_EXIT=0
export HOOK_PYTEST_EXIT HOOK_PNPM_EXIT
run_hook
[ "$hook_status" -ne 0 ] || fail "missing frontend dependencies returned zero"
assert_contains "$hook_output" "pnpm install --frozen-lockfile"
[ ! -f "$HOOK_CALL_LOG" ] || fail "pnpm ran despite missing frontend dependencies"

# A frontend test failure also propagates after dependencies are present.
make_fixture failing-frontend
mkdir -p "$checkout/backend/.venv/bin" "$checkout/frontend/node_modules"
cat > "$checkout/backend/.venv/bin/pytest" <<'FAKE_PYTEST'
#!/bin/sh
exit 0
FAKE_PYTEST
chmod +x "$checkout/backend/.venv/bin/pytest"
HOOK_PYTEST_EXIT=0 HOOK_PNPM_EXIT=1
export HOOK_PYTEST_EXIT HOOK_PNPM_EXIT
run_hook
[ "$hook_status" -ne 0 ] || fail "failing pnpm test returned zero"

# A direct push to main is refused before any check runs.
rm -f "$HOOK_CALL_LOG"
HOOK_INPUT='refs/heads/topic 1111111111111111111111111111111111111111 refs/heads/main 2222222222222222222222222222222222222222'
export HOOK_INPUT
run_hook
[ "$hook_status" -ne 0 ] || fail "direct push to main returned zero"
assert_contains "$hook_output" "REFUSED: direct push to main"
[ ! -f "$HOOK_CALL_LOG" ] || fail "checks ran before the direct-push refusal"

echo "PASS: pre-push discovery, dependency guidance, exit status, and main-branch refusal"
