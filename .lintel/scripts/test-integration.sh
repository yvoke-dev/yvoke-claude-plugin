#!/bin/sh
# lintel-script: test-integration   contract: 1
#
# Runs ALL tests of the "integration" group (patterns: .lintel/config.jsonc, paths.tests): the plugin in
# a real headless Claude Code session, against a stub Yvoke server and a fake model API (tests/integration/).
# It needs only node and claude: no node_modules, no generated types, so it skips setup.sh.
# CLAUDE_BIN names another Claude Code binary (default: claude on PATH).
# Arguments select test files; with none, every *.test.mjs under tests/integration/ runs.

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

if [ "$#" -gt 0 ]; then
    INT_FILES="$*"
else
    INT_FILES="$(find tests/integration -type f -name '*.test.mjs' 2>/dev/null | sort)"
fi
if [ -z "$INT_FILES" ]; then
    exit 3
fi

CLAUDE_CMD="${CLAUDE_BIN:-claude}"
if ! command -v node >/dev/null 2>&1; then
    echo "lintel: node not found on PATH" >&2
    exit 2
fi
if ! command -v "$CLAUDE_CMD" >/dev/null 2>&1; then
    echo "lintel: $CLAUDE_CMD not found (set CLAUDE_BIN to a Claude Code binary)" >&2
    exit 2
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/yvoke-int-run.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT
OUT="$TMP_DIR/out.txt"

# Unquoted on purpose: one word per file. Paths in this repo have no spaces.
# shellcheck disable=SC2086
node --test --test-reporter=spec $INT_FILES >"$OUT" 2>&1
STATUS=$?
cat "$OUT"

if [ "$STATUS" -eq 0 ]; then
    exit 0
fi
# The test's own setup failed (the plugin did not install, claude would not start): not a test failure.
if grep -q 'yvoke-integration: harness error' "$OUT"; then
    exit 2
fi
# node --test exits 1 for a failing test and for a file that would not even load; only the summary tells.
if grep -Eq '^ℹ tests [1-9]' "$OUT" && grep -Eq '^ℹ fail [1-9]' "$OUT"; then
    exit 1
fi
exit 2
