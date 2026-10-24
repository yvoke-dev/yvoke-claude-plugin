#!/bin/sh
# lintel-script: build   contract: 1
#
# Compiles and validates the project without running tests (used by QA and DEV personas).

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

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/yvoke-build.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT
export CLAUDE_CONFIG_DIR="$TMP_DIR/claude-config"
mkdir -p "$CLAUDE_CONFIG_DIR"

node scripts/check-docs.mjs || exit 1
claude plugin validate --strict . || exit 1

VALIDATE_OUT="$TMP_DIR/validate.txt"
if ! claude plugin validate --strict plugins/yvoke >"$VALIDATE_OUT" 2>&1; then
    cat "$VALIDATE_OUT" >&2
    exit 1
fi
cat "$VALIDATE_OUT"

if ! grep -q "calls:" "$VALIDATE_OUT"; then
    echo "error: claude plugin validate printed no calls: line" >&2
    exit 1
fi

if grep -E 'calls:.*\$\.(fs|process|http)\b' "$VALIDATE_OUT"; then
    echo "error: The mod calls $.fs, $.process or $.http; see .lintel/steering/conventions.md." >&2
    exit 1
fi

./node_modules/.bin/tsc --noEmit || exit 1
exit 0
