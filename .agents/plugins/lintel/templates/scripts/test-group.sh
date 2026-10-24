#!/bin/sh
# lintel-script: test-group   contract: 1
#
# Runs ALL tests of the "<group>" group (patterns: .lintel/config.jsonc, paths.tests).
#   Called:   from the repo root (or a clean copy of it), with no arguments, after setup.sh
#   Optional: arguments select a subset (a file, class or pattern); personas use this,
#             the gate never does
#   Exit:     0 = all tests passed
#             1 = tests ran and at least one failed
#             2 = tests could not run (compile error, missing service, bad env)
#             3 = no tests of this group were found
#   Output:   anything to stdout/stderr; the gate keeps the tail for the human
#   Env:      LINTEL_GATE=1 under the gate, LINTEL_TEST_GROUP=<group>; unset both before
#             starting the runner, so the code under test doesn't see them
#   Must not: change tracked files; run another group's tests
#   Setup:    what only this group needs (start the app for e2e) goes here, and is undone
#             here too (trap ... EXIT); setup.sh holds what all groups share
#
# Examples:  npm run test:integration   |   go test -tags=integration ./...   |   pytest tests/integration
# Map the runner's exit codes to these: Maven, for example, exits 1 for a compile error and a
# test failure alike, so run `test-compile` first (exit 2 on failure), then the tests, and
# exit 3 when no surefire/failsafe report was written. lintel:<stack>-testing has the recipe.

echo "lintel: .lintel/scripts/test-<group>.sh is not configured yet" >&2
exit 2
