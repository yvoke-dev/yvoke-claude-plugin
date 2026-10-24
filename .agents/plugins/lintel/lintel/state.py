"""State file management, locking, and validation (M1a-07)."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# Regex for stages: r0, p<N>, r<N>, final, docs
STAGE_PATTERN = re.compile(r"^(r0|final|docs|[rp]\d+)$")

VALID_RUN_STATES: Set[str] = {"pending", "active", "done", "abandoned"}
VALID_VERDICTS: Set[str] = {
    "complete",
    "needs-input",
    "no-change",
    "blocked",
    "approve",
    "changes",
    "ruled",
    "issues",
}
VALID_BLOCKED_CAUSES: Set[str] = {"requirements", "contract", "test", "environment"}
VALID_STOP_CAUSES: Set[str] = {
    "killed",
    "permission-denied",
    "restart",
    "ended-without-verdict",
    "never-entered",
    "forced",
}
VALID_STALL_REASONS: Set[str] = {"no-progress", "max-rounds", "ruling-human"}
VALID_DECIDE_OPTIONS: Set[str] = {
    "guidance",
    "accept",
    "retry",
    "reset",
    "to-final",
    "to-docs",
    "skip-docs",
    "abort",
}
VALID_DIALOG_OUTCOMES: Set[str] = {
    "ok",
    "not-me",
    "canceled",
    "gave-up",
    "error",
    "unavailable",
}
VALID_GATE_COMMANDS: Set[str] = {"baseline", "check", "check-start", "lock", "deliver"}
VALID_TASK_STATUSES: Set[str] = {"active", "finished", "aborted"}

VALID_EVENT_KINDS: Set[str] = {
    "start",
    "invoke",
    "enter",
    "done",
    "answer",
    "check",
    "approve",
    "reject",
    "lock",
    "amend",
    "deliver",
    "reset",
    "restore",
    "drift-ack",
    "audit-ack",
    "config",
    "release",
    "stall",
    "ruling",
    "decide",
    "finish",
    "abort",
    "unstick",
    "stopped",
    "resume",
}

VALID_FINDING_SEVERITIES: Set[str] = {"blocker", "major", "minor"}
VALID_FINDING_STATUSES: Set[str] = {"open", "fixed", "withdrawn"}


class StateError(Exception):
    """Raised when state loading, validation, or an invariant fails."""
    pass


class LockTimeoutError(StateError):
    """Raised when acquiring the state lock times out."""
    pass


class GateRefusedError(StateError):
    """Raised when a gate command is refused because another gate is running (T-STG-15)."""

    def __init__(self, running_info: Dict[str, Any], message: Optional[str] = None):
        self.running_info = running_info
        cmd = running_info.get("command")
        tree = running_info.get("tree_id")
        log = running_info.get("log") or running_info.get("log_path")
        msg = (
            message
            or f"Refused: gate command '{cmd}' is already running for tree '{tree}' (log: {log}); lintel task wait"
        )
        super().__init__(msg)


class GateRunningReport(StateError):
    """Reported when a second gate call for the same tree and command is made (single-flight, T-STG-16)."""

    def __init__(self, running_info: Dict[str, Any], message: Optional[str] = None):
        self.running_info = running_info
        cmd = running_info.get("command")
        tree = running_info.get("tree_id")
        log = running_info.get("log") or running_info.get("log_path")
        msg = (
            message
            or f"Gate '{cmd}' already running for tree '{tree}' (single-flight, log: {log}); lintel task wait"
        )
        super().__init__(msg)


# Thread-local storage for re-entrant lock support
_lock_state = threading.local()


def resolve_state_root(path: Union[str, Path]) -> Path:
    """Resolve the root state directory (.lintel/state)."""
    p = Path(path).resolve()
    if p.name == "state":
        return p
    if p.parent.name == "state":
        return p.parent
    return p


# ---------------------------------------------------------------------------
# Atomic JSON File Operations & Validation
# ---------------------------------------------------------------------------

def atomic_write_json(file_path: Union[str, Path], data: Dict[str, Any]) -> None:
    """Write data to temporary file in same directory, then atomically replace destination."""
    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        dir=path.parent,
        prefix=f".{path.name}.tmp-",
        delete=False,
        encoding="utf-8",
    )
    try:
        content = json.dumps(data, indent=2, sort_keys=True) + "\n"
        temp_file.write(content)
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_file.close()
        os.replace(temp_file.name, path)
    except Exception:
        if os.path.exists(temp_file.name):
            try:
                os.unlink(temp_file.name)
            except OSError:
                pass
        raise


def _infer_doc_type(path: Path) -> str:
    """Infer document type from file path."""
    name = path.name
    if name == "task.json":
        return "task"
    if name == "running.json":
        return "running"
    if name.endswith(".owner.json"):
        return "owner"
    if name == "loops.json":
        return "loops"
    if name == "reviews.json":
        return "reviews"
    if name == "acks.json":
        return "acks"
    if name == "checks.json":
        return "checks"
    if name == "finished.json":
        return "finished"
    if path.parent.name == "runs" or "/runs/" in str(path):
        return "run"
    return "generic"


def validate_state_doc(
    doc_type: str,
    data: Dict[str, Any],
    file_path: Optional[Union[str, Path]] = None,
) -> None:
    """Validate a state JSON document per docs/state-machine.md §9.
    
    Raises StateError pointing to 'lintel doctor' on validation failure.
    Unknown extra keys are ignored and permitted per §9.1.
    """
    target = str(file_path) if file_path else f"doc:{doc_type}"

    if not isinstance(data, dict):
        raise StateError(f"State file '{target}' must be a JSON object; run 'lintel doctor'")

    version = data.get("version")
    if version != 1 or not isinstance(version, int):
        raise StateError(
            f"State file '{target}' has unsupported version {version} (expected 1); run 'lintel doctor'"
        )

    if doc_type == "task":
        required = [
            "id",
            "title",
            "status",
            "created_at",
            "branch",
            "recorded_branch",
            "base_sha",
            "tmp",
            "interpreter",
            "head",
            "event_seq",
            "position",
            "phases",
            "delivered_phases",
            "counters",
            "known_requirement_ids",
        ]
        for key in required:
            if key not in data:
                raise StateError(
                    f"State file '{target}' missing required field '{key}'; run 'lintel doctor'"
                )
        if data["status"] not in VALID_TASK_STATUSES:
            raise StateError(
                f"State file '{target}' status '{data['status']}' outside enum; run 'lintel doctor'"
            )
        pos = data.get("position")
        if not isinstance(pos, dict) or "stage" not in pos or "code" not in pos:
            raise StateError(
                f"State file '{target}' position must contain 'stage' and 'code'; run 'lintel doctor'"
            )
        if not STAGE_PATTERN.match(pos["stage"]):
            raise StateError(
                f"State file '{target}' invalid stage '{pos['stage']}'; run 'lintel doctor'"
            )

        delivered_phases = data.get("delivered_phases")
        if not isinstance(delivered_phases, list) or not all(
            isinstance(x, int) and not isinstance(x, bool) for x in delivered_phases
        ):
            raise StateError(
                f"State file '{target}' 'delivered_phases' must be a list of integers; run 'lintel doctor'"
            )
        phases_data = data.get("phases")
        if not isinstance(phases_data, dict):
            raise StateError(
                f"State file '{target}' 'phases' must be a dictionary; run 'lintel doctor'"
            )
        expected_delivered = sorted(
            int(k)
            for k, v in phases_data.items()
            if isinstance(v, dict) and v.get("delivered") is not None and str(k).isdigit()
        )
        if delivered_phases != expected_delivered:
            raise StateError(
                f"State file '{target}' 'delivered_phases' {delivered_phases} does not match delivered phases {expected_delivered}; run 'lintel doctor'"
            )

    elif doc_type == "run":
        required = ["id", "role", "stage", "seq", "folder", "state", "nonce", "created_at", "prompt"]
        for key in required:
            if key not in data:
                raise StateError(
                    f"State file '{target}' missing required field '{key}'; run 'lintel doctor'"
                )
        if data["state"] not in VALID_RUN_STATES:
            raise StateError(
                f"State file '{target}' state '{data['state']}' outside enum; run 'lintel doctor'"
            )
        if data.get("verdict") is not None and data["verdict"] not in VALID_VERDICTS:
            raise StateError(
                f"State file '{target}' verdict '{data['verdict']}' outside enum; run 'lintel doctor'"
            )
        if data.get("cause") is not None and data["cause"] not in VALID_BLOCKED_CAUSES:
            raise StateError(
                f"State file '{target}' blocked cause '{data['cause']}' outside enum; run 'lintel doctor'"
            )
        stop = data.get("stop")
        if stop is not None:
            if not isinstance(stop, dict) or stop.get("cause") not in VALID_STOP_CAUSES:
                raise StateError(
                    f"State file '{target}' stop cause invalid; run 'lintel doctor'"
                )

    elif doc_type == "loops":
        if "loops" not in data or not isinstance(data["loops"], dict):
            raise StateError(f"State file '{target}' missing 'loops' dict; run 'lintel doctor'")
        for k, v in data["loops"].items():
            if not isinstance(v, dict):
                raise StateError(f"State file '{target}' loop '{k}' must be an object; run 'lintel doctor'")
            for count_field in ("rounds", "since_decision", "no_progress"):
                if count_field in v and (not isinstance(v[count_field], int) or isinstance(v[count_field], bool)):
                    raise StateError(f"State file '{target}' loop '{k}' field '{count_field}' must be integer; run 'lintel doctor'")
            if v.get("stalled") is not None:
                st = v["stalled"]
                if not isinstance(st, dict) or st.get("reason") not in VALID_STALL_REASONS:
                    raise StateError(f"State file '{target}' loop '{k}' invalid stalled reason; run 'lintel doctor'")
            if "after_human_reject" in v and not isinstance(v["after_human_reject"], bool):
                raise StateError(f"State file '{target}' loop '{k}' after_human_reject must be bool; run 'lintel doctor'")
            if "findings" in v and not isinstance(v["findings"], list):
                raise StateError(f"State file '{target}' loop '{k}' findings must be a list; run 'lintel doctor'")
            if "history" in v and not isinstance(v["history"], list):
                raise StateError(f"State file '{target}' loop '{k}' history must be a list; run 'lintel doctor'")

    elif doc_type == "reviews":
        if "reviews" not in data or not isinstance(data["reviews"], list):
            raise StateError(f"State file '{target}' missing 'reviews' list; run 'lintel doctor'")
        if "approvals" not in data or not isinstance(data["approvals"], list):
            raise StateError(f"State file '{target}' missing 'approvals' list; run 'lintel doctor'")

    elif doc_type == "acks":
        for k in ("drift", "audit", "last_human"):
            if k not in data or not isinstance(data[k], dict):
                raise StateError(f"State file '{target}' missing '{k}' dict; run 'lintel doctor'")

    elif doc_type == "checks":
        if "checks" not in data or not isinstance(data["checks"], list):
            raise StateError(f"State file '{target}' missing 'checks' list; run 'lintel doctor'")

    elif doc_type == "running":
        required = [
            "command",
            "pid",
            "pgid",
            "tree_id",
            "started_at",
            "bound_seconds",
        ]
        for key in required:
            if key not in data:
                raise StateError(
                    f"State file '{target}' missing required field '{key}'; run 'lintel doctor'"
                )
        if data["command"] not in VALID_GATE_COMMANDS:
            raise StateError(
                f"State file '{target}' invalid gate command '{data['command']}'; run 'lintel doctor'"
            )
        if not isinstance(data["pid"], int) or not isinstance(data["pgid"], int):
            raise StateError(f"State file '{target}' pid/pgid must be integers; run 'lintel doctor'")
        if "proc_start" not in data and "lstart" not in data:
            raise StateError(
                f"State file '{target}' missing process start time; run 'lintel doctor'"
            )
        if "log" not in data and "log_path" not in data:
            raise StateError(
                f"State file '{target}' missing log path; run 'lintel doctor'"
            )

    elif doc_type == "owner":
        required = ["pid", "pgid", "repo"]
        for key in required:
            if key not in data:
                raise StateError(
                    f"State file '{target}' missing required field '{key}'; run 'lintel doctor'"
                )
        if not isinstance(data["pid"], int) or not isinstance(data["pgid"], int):
            raise StateError(f"State file '{target}' pid/pgid must be integers; run 'lintel doctor'")
        if "proc_start" not in data and "lstart" not in data:
            raise StateError(
                f"State file '{target}' missing process start time; run 'lintel doctor'"
            )

    elif doc_type == "finished":
        if "tasks" not in data or not isinstance(data["tasks"], list):
            raise StateError(f"State file '{target}' missing 'tasks' list; run 'lintel doctor'")

    elif doc_type == "refusals":
        required = ["id", "at", "command", "outcome", "marker"]
        for key in required:
            if key not in data:
                raise StateError(
                    f"State record '{target}' missing required field '{key}'; run 'lintel doctor'"
                )
        if data["outcome"] not in VALID_DIALOG_OUTCOMES:
            raise StateError(
                f"State record '{target}' invalid outcome '{data['outcome']}'; run 'lintel doctor'"
            )

    elif doc_type == "events":
        required = ["step", "at", "event"]
        for key in required:
            if key not in data:
                raise StateError(
                    f"State record '{target}' missing required field '{key}'; run 'lintel doctor'"
                )
        if not isinstance(data["step"], int):
            raise StateError(
                f"State record '{target}' step must be an integer; run 'lintel doctor'"
            )
        if data["event"] not in VALID_EVENT_KINDS:
            raise StateError(
                f"State record '{target}' invalid event kind '{data['event']}'; run 'lintel doctor'"
            )
        if data.get("stop_cause") is not None and data["stop_cause"] not in VALID_STOP_CAUSES:
            raise StateError(
                f"State record '{target}' invalid stop cause '{data['stop_cause']}'; run 'lintel doctor'"
            )
        if data["event"] == "stopped":
            if not data.get("stop_cause"):
                raise StateError(
                    f"State record '{target}' stopped event missing stop_cause; run 'lintel doctor'"
                )
            if data["stop_cause"] not in VALID_STOP_CAUSES:
                raise StateError(
                    f"State record '{target}' invalid stop cause '{data['stop_cause']}'; run 'lintel doctor'"
                )
        if data.get("verdict") is not None and data["verdict"] not in VALID_VERDICTS:
            raise StateError(
                f"State record '{target}' invalid verdict '{data['verdict']}'; run 'lintel doctor'"
            )
        if data.get("cause") is not None and data["cause"] not in VALID_BLOCKED_CAUSES:
            raise StateError(
                f"State record '{target}' invalid blocked cause '{data['cause']}'; run 'lintel doctor'"
            )
        if data.get("stall_reason") is not None and data["stall_reason"] not in VALID_STALL_REASONS:
            raise StateError(
                f"State record '{target}' invalid stall reason '{data['stall_reason']}'; run 'lintel doctor'"
            )
        if data.get("decision") is not None and data["decision"] not in VALID_DECIDE_OPTIONS:
            raise StateError(
                f"State record '{target}' invalid decision '{data['decision']}'; run 'lintel doctor'"
            )


def load_state_file(
    file_path: Union[str, Path],
    doc_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Load and validate a versioned JSON state document.
    
    Refuses corrupted or invalid JSON with StateError without modifying state.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise StateError(f"State file '{path}' not found; run 'lintel doctor'")
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        raise StateError(f"State file '{path}' could not be read: {e}; run 'lintel doctor'")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise StateError(f"State file '{path}' is not valid JSON: {e}; run 'lintel doctor'")

    if not isinstance(data, dict):
        raise StateError(f"State file '{path}' must be a JSON object; run 'lintel doctor'")

    if doc_type is None:
        doc_type = _infer_doc_type(path)

    validate_state_doc(doc_type, data, file_path=path)
    return data


# ---------------------------------------------------------------------------
# Exclusive Lock Mechanism with Stale Lock Recovery
# ---------------------------------------------------------------------------

def _is_lock_stale(lock_file: Path) -> bool:
    """Check if lock file is stale (holding process dead or start time changed)."""
    try:
        content = lock_file.read_bytes()
        if not content:
            # Empty file: if mtime is older than 2 seconds, it is stale
            age = time.time() - lock_file.stat().st_mtime
            return age > 2.0
        data = json.loads(content.decode("utf-8"))
        pid = data.get("pid")
        if not pid or not isinstance(pid, int):
            return True
        expected_start = data.get("proc_start") or data.get("lstart")
        if not is_pid_alive(pid):
            return True
        if expected_start:
            actual_start = get_process_start_time(pid)
            if actual_start != expected_start:
                return True
        return False
    except (json.JSONDecodeError, OSError):
        try:
            age = time.time() - lock_file.stat().st_mtime
            return age > 2.0
        except OSError:
            return True


class _StateLockContext:
    def __init__(
        self,
        lock_file: Path,
        timeout: float = 10.0,
        poll_interval: float = 0.05,
    ):
        self.lock_file = lock_file
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.fd: Optional[int] = None
        self.acquired = False

    def __enter__(self) -> Path:
        # Re-entrant lock check in current thread
        if not hasattr(_lock_state, "locks"):
            _lock_state.locks = {}
        str_path = str(self.lock_file)
        if _lock_state.locks.get(str_path, 0) > 0:
            _lock_state.locks[str_path] += 1
            return self.lock_file

        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        start_time = time.time()

        while True:
            try:
                self.fd = os.open(
                    self.lock_file,
                    os.O_CREAT | os.O_EXCL | os.O_RDWR,
                    0o644,
                )
                my_pid = os.getpid()
                my_start = get_process_start_time(my_pid) or ""
                info = {
                    "version": 1,
                    "pid": my_pid,
                    "pgid": os.getpgrp(),
                    "proc_start": my_start,
                    "lstart": my_start,
                    "acquired_at": time.time(),
                }
                payload = (json.dumps(info) + "\n").encode("utf-8")
                os.write(self.fd, payload)
                self.acquired = True
                _lock_state.locks[str_path] = 1
                return self.lock_file
            except FileExistsError:
                if _is_lock_stale(self.lock_file):
                    try:
                        self.lock_file.unlink()
                    except OSError:
                        pass
                    continue
                if time.time() - start_time >= self.timeout:
                    raise LockTimeoutError(
                        f"Could not acquire state lock on {self.lock_file} within {self.timeout}s"
                    )
                time.sleep(self.poll_interval)

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        str_path = str(self.lock_file)
        if hasattr(_lock_state, "locks") and str_path in _lock_state.locks:
            _lock_state.locks[str_path] -= 1
            if _lock_state.locks[str_path] > 0:
                return
            del _lock_state.locks[str_path]

        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

        if self.acquired:
            try:
                if self.lock_file.is_file():
                    try:
                        data = json.loads(self.lock_file.read_text(encoding="utf-8"))
                        if data.get("pid") == os.getpid():
                            self.lock_file.unlink(missing_ok=True)
                    except Exception:
                        self.lock_file.unlink(missing_ok=True)
            except OSError:
                pass
            self.acquired = False


def state_lock(
    state_dir_or_file: Union[str, Path],
    timeout: float = 10.0,
    poll_interval: float = 0.05,
) -> _StateLockContext:
    """Exclusive lock for state transitions only.
    
    Held briefly during file modifications, never across test runs.
    Stale lock from killed processes is safely cleared and recovered.
    """
    path = Path(state_dir_or_file).resolve()
    if path.is_file() or path.suffix == ".lock":
        lock_file = path
    else:
        root = resolve_state_root(path)
        lock_file = root / "state.lock"
    return _StateLockContext(lock_file, timeout=timeout, poll_interval=poll_interval)


# ---------------------------------------------------------------------------
# Process Helpers & "Still Ours" Verification
# ---------------------------------------------------------------------------

def get_process_start_time(pid: int) -> Optional[str]:
    """Capture process start time using `ps -o lstart= -p <pid>`.
    
    Returns stripped start time string or None if process is dead or ps fails.
    """
    if pid <= 0:
        return None
    try:
        proc = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            out = proc.stdout.strip()
            return out if out else None
    except Exception:
        pass
    if is_pid_alive(pid):
        return f"proc-alive-{pid}"
    return None


def is_pid_alive(pid: int) -> bool:
    """Check if process exists in the operating system."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _get_pgid_members(pgid: int) -> List[int]:
    """Get list of member PIDs in a process group."""
    if pgid <= 0:
        return []
    try:
        proc = subprocess.run(
            ["ps", "-o", "pid=", "-g", str(pgid)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            pids = []
            for line in proc.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
            return pids
    except Exception:
        pass
    if is_pid_alive(pgid):
        return [pgid]
    return []


def _is_process_cwd_inside(pid: int, gate_dir: Union[str, Path]) -> bool:
    """Check if working directory of a process is inside gate_dir."""
    target_dir = Path(gate_dir).resolve()
    # On macOS, use lsof -a -p <pid> -d cwd -Fn
    try:
        proc = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.startswith("n/"):
                    cwd_path = Path(line[1:]).resolve()
                    if cwd_path == target_dir or target_dir in cwd_path.parents:
                        return True
    except Exception:
        pass
    # On Linux /proc
    try:
        proc_cwd = Path(f"/proc/{pid}/cwd")
        if proc_cwd.exists():
            resolved = proc_cwd.resolve()
            if resolved == target_dir or target_dir in resolved.parents:
                return True
    except Exception:
        pass
    return False


def is_pid_ours(
    pid: int,
    expected_start: str,
    gate_dir: Optional[Union[str, Path]] = None,
) -> bool:
    """'Still ours' check for a single process.
    
    Verifies process start time matches expected_start, or cwd is inside gate_dir.
    Ensures recycled PIDs on macOS are never signalled.
    """
    if not is_pid_alive(pid):
        return False
    actual_start = get_process_start_time(pid)
    if actual_start is not None and expected_start and actual_start == expected_start:
        return True
    if gate_dir:
        return _is_process_cwd_inside(pid, gate_dir)
    return False


def is_process_group_ours(
    pgid: int,
    leader_pid: int,
    expected_start: str,
    gate_dir: Optional[Union[str, Path]] = None,
) -> bool:
    """'Still ours' check for a process group.
    
    Verifies process leader's start time matches, or member's cwd is inside gate_dir.
    """
    if leader_pid > 0 and expected_start:
        if is_pid_alive(leader_pid):
            actual_start = get_process_start_time(leader_pid)
            if actual_start == expected_start:
                return True
    if gate_dir and pgid > 0:
        member_pids = _get_pgid_members(pgid)
        for mpid in member_pids:
            if _is_process_cwd_inside(mpid, gate_dir):
                return True
    return False


def signal_process(
    pid: int,
    sig: int,
    expected_start: str,
    gate_dir: Optional[Union[str, Path]] = None,
) -> bool:
    """Signal recorded process only if proven still ours. Return True if signalled."""
    if not is_pid_ours(pid, expected_start, gate_dir):
        return False
    try:
        os.kill(pid, sig)
        return True
    except OSError:
        return False


def signal_process_group(
    pgid: int,
    sig: int,
    leader_pid: int,
    expected_start: str,
    gate_dir: Optional[Union[str, Path]] = None,
) -> bool:
    """Signal recorded process group only if proven still ours. Return True if signalled."""
    if not is_process_group_ours(pgid, leader_pid, expected_start, gate_dir):
        return False
    try:
        os.killpg(pgid, sig)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# "Running" Record Management (running.json)
# ---------------------------------------------------------------------------

def get_running_record_path(state_dir: Union[str, Path]) -> Path:
    """Return path to .lintel/state/running.json."""
    root = resolve_state_root(state_dir)
    return root / "running.json"


def read_running_record(state_dir: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """Read running.json if it exists."""
    path = get_running_record_path(state_dir)
    if not path.is_file():
        return None
    return load_state_file(path, doc_type="running")


def _now_iso() -> str:
    """Return current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_gate_cleared(state_dir: Union[str, Path], rec: Dict[str, Any]) -> None:
    """Record gate_cleared_at in the corresponding run document if run_id is present."""
    if not isinstance(rec, dict):
        return
    run_id = rec.get("run") or rec.get("run_id")
    if not run_id:
        return
    task_id = rec.get("task")
    root = resolve_state_root(state_dir)
    target_file = None
    if task_id:
        c1 = root / task_id / "runs" / f"{run_id}.json"
        c2 = root / ".lintel" / "state" / task_id / "runs" / f"{run_id}.json"
        if c1.is_file():
            target_file = c1
        elif c2.is_file():
            target_file = c2
    if target_file is None:
        candidates = list(root.glob(f"*/runs/{run_id}.json")) + list(root.glob(f".lintel/state/*/runs/{run_id}.json"))
        if candidates:
            target_file = candidates[0]

    if target_file and target_file.is_file():
        try:
            run_data = load_state_file(target_file, doc_type="run")
            run_data["gate_cleared_at"] = _now_iso()
            validate_state_doc("run", run_data, file_path=target_file)
            atomic_write_json(target_file, run_data)
        except Exception:
            pass


def check_or_clear_stale_running(state_dir: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """Check running.json.
    
    If holding process is dead or start time changed, clear stale record and return None.
    If alive and start time matches, return the running dict.
    If state file fails validation, StateError propagates without modifying the file.
    """
    path = get_running_record_path(state_dir)
    if not path.is_file():
        return None
    rec = load_state_file(path, doc_type="running")

    pid = rec.get("pid")
    expected_start = rec.get("proc_start") or rec.get("lstart")
    gate_dir = rec.get("gate_dir")

    if not is_pid_ours(pid, expected_start or "", gate_dir):
        with state_lock(state_dir):
            if path.is_file():
                _record_gate_cleared(state_dir, rec)
                path.unlink(missing_ok=True)
        return None

    return rec


def enter_gate(
    state_dir: Union[str, Path],
    command: str,
    tree_id: str,
    log_path: str,
    bound_seconds: int = 600,
    task_id: Optional[str] = None,
    phase: Optional[int] = None,
    run_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    gate_dir: Optional[Union[str, Path]] = None,
    detached: bool = False,
    pid: Optional[int] = None,
    pgid: Optional[int] = None,
    proc_start: Optional[str] = None,
) -> Dict[str, Any]:
    """Enter a gate operation (check, check --start, lock, deliver, baseline).
    
    Enforces single-flight:
    - Same tree_id and command (treating check and check-start as matching) reports
      running gate (GateRunningReport).
    - Different tree_id or command is refused with clear error (GateRefusedError).
    Stale records are verified and cleared.
    """
    with state_lock(state_dir):
        running = check_or_clear_stale_running(state_dir)
        if running is not None:
            rec_cmd = running.get("command")
            rec_tree = running.get("tree_id")
            same_cmd = (rec_cmd == command) or ({rec_cmd, command} == {"check", "check-start"})
            if same_cmd and rec_tree == tree_id:
                raise GateRunningReport(running)
            else:
                raise GateRefusedError(running)

        cur_pid = pid if pid is not None else os.getpid()
        cur_pgid = pgid if pgid is not None else os.getpgrp()
        cur_start = proc_start or get_process_start_time(cur_pid) or ""
        started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        record: Dict[str, Any] = {
            "version": 1,
            "command": command,
            "task": task_id,
            "phase": phase,
            "pid": cur_pid,
            "pgid": cur_pgid,
            "proc_start": cur_start,
            "lstart": cur_start,
            "run": run_id,
            "run_id": run_id,
            "conversation_id": conversation_id,
            "tree_id": tree_id,
            "log": log_path,
            "log_path": log_path,
            "started_at": started_at,
            "bound_seconds": bound_seconds,
            "gate_dir": str(gate_dir) if gate_dir else "",
            "detached": detached,
        }

        target = get_running_record_path(state_dir)
        atomic_write_json(target, record)
        return record


def exit_gate(
    state_dir: Union[str, Path],
    pid: Optional[int] = None,
    force: bool = False,
) -> None:
    """Clear running.json on exit under state lock."""
    with state_lock(state_dir):
        path = get_running_record_path(state_dir)
        if not path.is_file():
            return
        rec: Optional[Dict[str, Any]] = None
        try:
            rec = load_state_file(path, doc_type="running")
        except Exception:
            pass

        should_unlink = False
        if force or pid is None:
            should_unlink = True
        elif rec and rec.get("pid") == pid:
            should_unlink = True

        if should_unlink:
            if rec:
                _record_gate_cleared(state_dir, rec)
            path.unlink(missing_ok=True)


class _RunningGateContext:
    def __init__(
        self,
        state_dir: Union[str, Path],
        command: str,
        tree_id: str,
        log_path: str,
        bound_seconds: int = 600,
        task_id: Optional[str] = None,
        phase: Optional[int] = None,
        run_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        gate_dir: Optional[Union[str, Path]] = None,
        detached: bool = False,
        pid: Optional[int] = None,
        pgid: Optional[int] = None,
        proc_start: Optional[str] = None,
    ):
        self.state_dir = state_dir
        self.command = command
        self.tree_id = tree_id
        self.log_path = log_path
        self.bound_seconds = bound_seconds
        self.task_id = task_id
        self.phase = phase
        self.run_id = run_id
        self.conversation_id = conversation_id
        self.gate_dir = gate_dir
        self.detached = detached
        self.pid = pid
        self.pgid = pgid
        self.proc_start = proc_start
        self.record: Optional[Dict[str, Any]] = None

    def __enter__(self) -> Dict[str, Any]:
        self.record = enter_gate(
            state_dir=self.state_dir,
            command=self.command,
            tree_id=self.tree_id,
            log_path=self.log_path,
            bound_seconds=self.bound_seconds,
            task_id=self.task_id,
            phase=self.phase,
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            gate_dir=self.gate_dir,
            detached=self.detached,
            pid=self.pid,
            pgid=self.pgid,
            proc_start=self.proc_start,
        )
        return self.record

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if not self.detached and self.record:
            exit_gate(self.state_dir, pid=self.record.get("pid"))


def running_gate(
    state_dir: Union[str, Path],
    command: str,
    tree_id: str,
    log_path: str,
    bound_seconds: int = 600,
    task_id: Optional[str] = None,
    phase: Optional[int] = None,
    run_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    gate_dir: Optional[Union[str, Path]] = None,
    detached: bool = False,
    pid: Optional[int] = None,
    pgid: Optional[int] = None,
    proc_start: Optional[str] = None,
) -> _RunningGateContext:
    """Context manager for gate runs writing running.json on entry and clearing on exit."""
    return _RunningGateContext(
        state_dir=state_dir,
        command=command,
        tree_id=tree_id,
        log_path=log_path,
        bound_seconds=bound_seconds,
        task_id=task_id,
        phase=phase,
        run_id=run_id,
        conversation_id=conversation_id,
        gate_dir=gate_dir,
        detached=detached,
        pid=pid,
        pgid=pgid,
        proc_start=proc_start,
    )


# ---------------------------------------------------------------------------
# Gate Owner Record Helpers (<tmp>/lintel-gate-*.owner.json)
# ---------------------------------------------------------------------------

def get_gate_owner_path(gate_dir: Union[str, Path]) -> Path:
    """Return path to owner record outside the gate worktree."""
    gdir = Path(gate_dir).resolve()
    return gdir.parent / f"{gdir.name}.owner.json"


def write_gate_owner(
    gate_dir: Union[str, Path],
    repo: Union[str, Path],
    pid: Optional[int] = None,
    pgid: Optional[int] = None,
    proc_start: Optional[str] = None,
) -> Path:
    """Write gate owner record outside worktree for worktree leftover sweep."""
    owner_file = get_gate_owner_path(gate_dir)
    cur_pid = pid if pid is not None else os.getpid()
    cur_pgid = pgid if pgid is not None else os.getpgrp()
    cur_start = proc_start or get_process_start_time(cur_pid) or ""
    data: Dict[str, Any] = {
        "version": 1,
        "pid": cur_pid,
        "pgid": cur_pgid,
        "proc_start": cur_start,
        "lstart": cur_start,
        "repo": str(Path(repo).resolve()),
    }
    atomic_write_json(owner_file, data)
    return owner_file


def read_gate_owner(owner_path_or_gate_dir: Union[str, Path]) -> Dict[str, Any]:
    """Read and validate gate owner record."""
    path = Path(owner_path_or_gate_dir).resolve()
    if not path.name.endswith(".owner.json"):
        path = get_gate_owner_path(path)
    return load_state_file(path, doc_type="owner")


def remove_gate_owner(gate_dir: Union[str, Path]) -> None:
    """Remove gate owner record if it exists."""
    owner_file = get_gate_owner_path(gate_dir)
    owner_file.unlink(missing_ok=True)
