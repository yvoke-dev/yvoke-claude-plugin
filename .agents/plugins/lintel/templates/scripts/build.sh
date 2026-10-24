#!/bin/sh
# lintel-script: build   contract: 1
#
# Compiles the project without running tests (used by QA and DEV personas).
#   Called:   from the repo root (or a clean copy of it), with no arguments
#   Exit:     0 = compilation succeeded
#             1 = compilation failed (syntax or type error)
#             2 = build could not run (missing toolchain, bad env)
#             (no exit 3: compilation is not grouped)
#   Output:   anything to stdout/stderr
#   Env:      LINTEL_GATE=1 under the gate
#   Must not: change tracked files; run tests
#
# Examples:  mvn test-compile   |   go build ./...   |   tsc --noEmit

echo "lintel: .lintel/scripts/build.sh is not configured yet" >&2
exit 2
