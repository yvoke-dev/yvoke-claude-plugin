#!/bin/sh
# lintel-script: test-e2e   contract: 1
#
# Runs ALL tests of the "e2e" group (patterns: .lintel/config.jsonc, paths.tests).
# There is no e2e runner: the live-session tests are the integration group. A file that looks like an
# e2e test must not be passed over as "no tests", so it fails the run until a runner exists.

unset LINTEL_GATE LINTEL_TEST_GROUP
cd "$(dirname "$0")/../.." || exit 2

E2E_FILES="$(find plugins tests -type f \( -name '*.e2e.ts' -o -name '*.e2e-spec.ts' -o -name '*.e2e.mjs' \) -not -path '*/node_modules/*' 2>/dev/null)"
if [ -z "$E2E_FILES" ]; then
    exit 3
fi
echo "lintel: e2e test files exist but no e2e runner is configured; wire one in .lintel/scripts/test-e2e.sh:" >&2
echo "$E2E_FILES" >&2
exit 2
