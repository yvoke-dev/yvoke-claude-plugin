#!/bin/sh
# lintel-script: teardown   contract: 1
#
# Undoes shared services or resources started by setup.sh.
#   Called:   from the repo root (or a clean copy of it), always after the last group
#             under the gate (including after a timeout), and by personas at end of run
#   Cleans:   services started by setup.sh (e.g. docker-compose down, test containers)
#   Exit:     exit code is logged only; never decides whether the test run passed or failed
#   Output:   anything to stdout/stderr
#   Env:      LINTEL_GATE=1 under the gate
#   Must not: change tracked files

exit 0
