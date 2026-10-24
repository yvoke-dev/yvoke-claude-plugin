#!/bin/sh
# lintel-script: test-e2e   contract: 1
#
# Runs ALL tests of the "e2e" group (patterns: .lintel/config.jsonc, paths.tests).

unset LINTEL_GATE LINTEL_TEST_GROUP
cd "$(dirname "$0")/../.." || exit 2

E2E_FILES="$(find plugins -type f \( -name '*.e2e.ts' -o -name '*.e2e-spec.ts' \) 2>/dev/null)"
if [ -z "$E2E_FILES" ]; then
    exit 3
fi
exit 3
