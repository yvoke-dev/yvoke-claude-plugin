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

if ! command -v claude >/dev/null 2>&1; then
    echo "lintel: claude CLI not found on PATH" >&2
    exit 2
fi

if [ "$#" -eq 0 ]; then
    UNIT_FILES="$(find plugins/yvoke/tests -type f -name '*.test.ts' ! -name '*.integration.*' ! -name '*.e2e.*' 2>/dev/null)"
    if [ -z "$UNIT_FILES" ]; then
        exit 3
    fi
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/yvoke-test.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT
export CLAUDE_CONFIG_DIR="$TMP_DIR/claude-config"
mkdir -p "$CLAUDE_CONFIG_DIR"

TEST_OUT="$TMP_DIR/test-out.txt"
if [ "$#" -eq 0 ]; then
    claude plugin test plugins/yvoke >"$TEST_OUT" 2>&1
    STATUS=$?
else
    claude plugin test "$@" >"$TEST_OUT" 2>&1
    STATUS=$?
fi

cat "$TEST_OUT"

if [ "$STATUS" -eq 0 ]; then
    exit 0
fi

if grep -Eq '[0-9]+[[:space:]]+(pass|fail|passed|failed)' "$TEST_OUT"; then
    exit 1
fi

exit 2
