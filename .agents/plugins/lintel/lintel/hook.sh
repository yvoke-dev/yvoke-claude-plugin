#!/bin/sh
# Lintel audit hook shim (M1b-01, PLAN §3.3, §11)
# Must print NOTHING (empty stdout and stderr) and exit 0 in every branch.
# Never search for an interpreter at hook time. Never use a bare python3.

# Save original stdout to fd 3 for explicit denies
exec 3>&1
# Redirect all stdout and stderr to /dev/null immediately
exec >/dev/null 2>&1

# Resolve temporary file location
TMP_DIR="${TMPDIR:-/tmp}"
TMP_DIR="${TMP_DIR%/}"
TMPFILE=$(mktemp "${TMP_DIR}/lintel-hook.XXXXXX" 2>/dev/null || echo "${TMP_DIR}/lintel-hook.$$.tmp")
TIMED_OUT="${TMPFILE}.timeout"

OUTPUT_FILE="${TMPFILE}.out"

# Always ensure cleanup on any exit or termination signal
cleanup() {
    rm -f "$TMPFILE" "$TIMED_OUT" "$OUTPUT_FILE" 2>/dev/null
}
trap cleanup EXIT HUP INT TERM

# Save incoming payload from stdin to the temporary file
cat > "$TMPFILE" 2>/dev/null || true

# Determine script, plugin, and project directories
DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
[ -z "$DIR" ] && DIR=$(dirname "$0")

PLUGIN_DIR="$(cd "$DIR/.." 2>/dev/null && pwd)"
[ -z "$PLUGIN_DIR" ] && PLUGIN_DIR="$DIR/.."

if [ -n "$LINTEL_PROJECT_ROOT" ]; then
    PROJECT_ROOT="$LINTEL_PROJECT_ROOT"
else
    PROJECT_ROOT="$(cd "$PLUGIN_DIR/../../.." 2>/dev/null && pwd)"
    [ -z "$PROJECT_ROOT" ] && PROJECT_ROOT="$PLUGIN_DIR/../../.."
fi

# Function to record failures to hook-errors.log silently
log_failure() {
    DESC="$1"
    CONV_ID="-"
    if [ -f "$TMPFILE" ]; then
        CID=$(sed -n 's/.*"conversationId"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$TMPFILE" 2>/dev/null | head -n 1)
        if [ -n "$CID" ]; then
            CONV_ID="$CID"
        fi
    fi
    TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date +"%Y-%m-%dT%H:%M:%SZ")
    if [ -n "$STATE_DIR" ]; then
        mkdir -p "$STATE_DIR" 2>/dev/null || true
        printf '%s %s %s\n' "$TS" "$CONV_ID" "$DESC" >> "$STATE_DIR/hook-errors.log" 2>/dev/null || true
    fi
}

# Project opt-in check:
# Vendored: <plugin>/../../../.lintel/config.jsonc exists
# Global install: <plugin>/python exists
STATE_DIR=""
PY_FILE=""


if [ -f "$PROJECT_ROOT/.lintel/config.jsonc" ]; then
    STATE_DIR="$PROJECT_ROOT/.lintel/state"
    PY_FILE="$STATE_DIR/python"
elif [ -f "$PLUGIN_DIR/python" ]; then
    if [ -d "$PROJECT_ROOT/.lintel/state" ]; then
        STATE_DIR="$PROJECT_ROOT/.lintel/state"
    else
        STATE_DIR="$PLUGIN_DIR"
    fi
    PY_FILE="$PLUGIN_DIR/python"
else
    # Neither exists: project not opted in. Exit silently with no error logged.
    exit 0
fi

# Resolve interpreter (read ONLY from recorded file)
PY=""
if [ -f "$PY_FILE" ]; then
    read -r PY < "$PY_FILE" 2>/dev/null || true
    PY=$(printf '%s' "$PY" | tr -d '\r\n')
fi

# Interpreter checks
if [ -z "$PY" ] || [ ! -e "$PY" ]; then
    log_failure "missing interpreter"
    exit 0
fi

# On Darwin, refuse stub interpreter (/usr/bin/python3)
if [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then
    if [ "$PY" = "/usr/bin/python3" ] || [ "$PY" -ef /usr/bin/python3 2>/dev/null ]; then
        log_failure "stub interpreter"
        exit 0
    fi
fi

if [ ! -x "$PY" ] || [ -d "$PY" ]; then
    log_failure "stub interpreter"
    exit 0
fi

# Run hook.py in background with watchdog
HOOK_PY="$DIR/hook.py"
PYTHONPATH="$PLUGIN_DIR${PYTHONPATH:+:$PYTHONPATH}" PYTHONDONTWRITEBYTECODE=1 "$PY" -B "$HOOK_PY" "$TMPFILE" > "$OUTPUT_FILE" 2>/dev/null 3>&- &
HOOK_PID=$!

(
    trap 'kill "$SLEEP_PID" 2>/dev/null; exit 0' TERM INT HUP
    sleep 4 &
    SLEEP_PID=$!
    wait "$SLEEP_PID" 2>/dev/null
    : > "$TIMED_OUT"
    kill -9 "$HOOK_PID" 2>/dev/null
) < /dev/null >/dev/null 2>&1 3>&- &
WATCHDOG_PID=$!

wait "$HOOK_PID" 2>/dev/null
STATUS=$?

kill "$WATCHDOG_PID" 2>/dev/null || true
wait "$WATCHDOG_PID" 2>/dev/null || true

if [ "$STATUS" -eq 2 ]; then
    cat "$OUTPUT_FILE" >&3
    exit 1
elif [ -f "$TIMED_OUT" ] || [ "$STATUS" -eq 137 ]; then
    log_failure "timeout"
elif [ "$STATUS" -ne 0 ]; then
    log_failure "crash"
fi

exit 0
