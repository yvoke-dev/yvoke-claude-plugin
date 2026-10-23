#!/bin/sh
# lintel-script: test-unit   contract: 1
#
# Runs ALL tests of the "unit" group (patterns: .lintel/config.jsonc, paths.tests).

unset LINTEL_GATE LINTEL_TEST_GROUP
cd "$(dirname "$0")/../.." || exit 2

for d in \
    "$HOME/.local/bin" \
    "$HOME/.claude/local" \
    "$HOME/.local/share/fnm/aliases/default/bin" \
    "$HOME/.local/share/fnm/aliases/default/lib/node_modules/corepack/shims" \
    "$HOME/Library/Application Support/fnm/aliases/default/bin" \
    "$HOME/Library/pnpm" \
    "/opt/homebrew/bin" \
    "/usr/local/bin"; do
    if [ -d "$d" ]; then
        PATH="$d:$PATH"
    fi
done
for nvm_bin in "$HOME"/.nvm/versions/node/*/bin; do
    if [ -d "$nvm_bin" ]; then
        PATH="$nvm_bin:$PATH"
    fi
done
export PATH

if [ ! -d node_modules ] || [ ! -f "plugins/yvoke/.claude-plugin/types/claude-code/index.d.ts" ] || grep -q "mcp__" "plugins/yvoke/.claude-plugin/types/claude-code-mcp/index.d.ts" 2>/dev/null; then
    sh ./.lintel/scripts/setup.sh || exit 2
fi

if ! command -v node >/dev/null 2>&1 || ! command -v claude >/dev/null 2>&1; then
    echo "lintel: node or claude CLI not found on PATH" >&2
    exit 2
fi

if [ ! -x ./node_modules/.bin/tsc ]; then
    echo "lintel: ./node_modules/.bin/tsc not found" >&2
    exit 2
fi

UNIT_FILES="$(find plugins/yvoke/tests -type f \( -name '*.test.ts' -o -name '*.test.tsx' \) ! -name '*.integration.*' ! -name '*.e2e.*' 2>/dev/null)"
if [ -z "$UNIT_FILES" ]; then
    exit 3
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/yvoke-test.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT
export CLAUDE_CONFIG_DIR="$TMP_DIR/claude-config"
mkdir -p "$CLAUDE_CONFIG_DIR"

VALIDATE_OUT="$TMP_DIR/validate.txt"
if ! claude plugin validate --strict plugins/yvoke >"$VALIDATE_OUT" 2>&1; then
    cat "$VALIDATE_OUT" >&2
    exit 2
fi

TSC_OUT="$TMP_DIR/tsc.txt"
if ! ./node_modules/.bin/tsc --noEmit >"$TSC_OUT" 2>&1; then
    cat "$TSC_OUT" >&2
    exit 2
fi

TEST_OUT="$TMP_DIR/test-out.txt"
claude plugin test plugins/yvoke >"$TEST_OUT" 2>&1
STATUS=$?

cat "$TEST_OUT"

if [ "$STATUS" -eq 0 ]; then
    exit 0
fi

if grep -Eq '[0-9]+[[:space:]]+(pass|fail|passed|failed)' "$TEST_OUT"; then
    exit 1
fi

exit 2
