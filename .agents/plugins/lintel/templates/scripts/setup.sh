#!/bin/sh
# lintel-script: setup   contract: 1
#
# Prepares shared environment before test groups or build scripts run.
#   Called:   from the repo root (or a clean copy of it), before the first test group
#             under the gate, and before a persona's first test or build script in a run
#   Prepares: dependencies, .env.test, generated sources, services all groups share
#   Must be:  idempotent (safe to run multiple times)
#   Exit:     0 = setup completed successfully; any non-zero exit means the run could not proceed
#   Output:   anything to stdout/stderr
#   Env:      LINTEL_GATE=1 under the gate
#   Must not: change tracked files

exit 0
