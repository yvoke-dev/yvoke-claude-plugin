"""Event logging and atomic JSONL appends (M1a-07)."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any, Dict, List, Optional, Union

try:
    from lintel.state import (
        StateError,
        VALID_BLOCKED_CAUSES,
        VALID_DECIDE_OPTIONS,
        VALID_EVENT_KINDS,
        VALID_STALL_REASONS,
        VALID_STOP_CAUSES,
        VALID_VERDICTS,
        resolve_state_root,
        validate_state_doc,
    )
except ImportError:
    from .state import (
        StateError,
        VALID_BLOCKED_CAUSES,
        VALID_DECIDE_OPTIONS,
        VALID_EVENT_KINDS,
        VALID_STALL_REASONS,
        VALID_STOP_CAUSES,
        VALID_VERDICTS,
        resolve_state_root,
        validate_state_doc,
    )


def get_events_log_path(state_dir: Union[str, Path], task_id: str) -> Path:
    """Return path to .lintel/state/<task_id>/events.jsonl."""
    root = resolve_state_root(state_dir)
    return root / task_id / "events.jsonl"


def create_event(
    step: int,
    event: str,
    stage: str,
    role: Optional[str] = None,
    run: Optional[str] = None,
    round: Optional[int] = None,
    verdict: Optional[str] = None,
    cause: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    findings: Optional[Dict[str, Any]] = None,
    gate: Optional[Dict[str, Any]] = None,
    stall_reason: Optional[str] = None,
    decision: Optional[str] = None,
    stop_cause: Optional[str] = None,
    folder: Optional[str] = None,
    row: Optional[str] = None,
    at: Optional[str] = None,
) -> Dict[str, Any]:
    """Construct a versioned event record conforming to docs/state-machine.md §9.10."""
    if event not in VALID_EVENT_KINDS:
        raise StateError(f"Invalid event kind '{event}'; run 'lintel doctor'")

    now_str = at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if event == "stopped":
        if not stop_cause:
            raise StateError("stopped event requires stop_cause; run 'lintel doctor'")
        if stop_cause not in VALID_STOP_CAUSES:
            raise StateError(f"Invalid stop cause '{stop_cause}'; run 'lintel doctor'")
        # The stopped record holds IDs and a cause code only.
        return {
            "version": 1,
            "step": step,
            "at": now_str,
            "row": row,
            "event": "stopped",
            "stage": stage,
            "role": role,
            "run": run,
            "stop_cause": stop_cause,
        }

    if stop_cause is not None and stop_cause not in VALID_STOP_CAUSES:
        raise StateError(f"Invalid stop cause '{stop_cause}'; run 'lintel doctor'")
    if verdict is not None and verdict not in VALID_VERDICTS:
        raise StateError(f"Invalid verdict '{verdict}'; run 'lintel doctor'")
    if cause is not None and cause not in VALID_BLOCKED_CAUSES:
        raise StateError(f"Invalid blocked cause '{cause}'; run 'lintel doctor'")
    if stall_reason is not None and stall_reason not in VALID_STALL_REASONS:
        raise StateError(f"Invalid stall reason '{stall_reason}'; run 'lintel doctor'")
    if decision is not None and decision not in VALID_DECIDE_OPTIONS:
        raise StateError(f"Invalid decision '{decision}'; run 'lintel doctor'")

    rec: Dict[str, Any] = {
        "version": 1,
        "step": step,
        "at": now_str,
        "row": row,
        "event": event,
        "stage": stage,
        "role": role,
        "run": run,
        "round": round,
        "verdict": verdict,
        "cause": cause,
        "duration_seconds": duration_seconds,
        "findings": findings,
        "gate": gate,
        "stall_reason": stall_reason,
        "decision": decision,
        "stop_cause": stop_cause,
        "folder": folder,
    }
    return rec


def append_event(
    state_dir: Union[str, Path],
    task_id: str,
    event_data: Dict[str, Any],
    task_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Append one event record to .lintel/state/<task_id>/events.jsonl.
    
    Atomic line append: single write per line in 'a' mode with trailing newline,
    ensuring valid line-oriented JSONL even under interleaved appends.
    Copies to task folder after step.
    """
    event_file = get_events_log_path(state_dir, task_id)
    event_file.parent.mkdir(parents=True, exist_ok=True)

    if "version" not in event_data:
        event_data["version"] = 1
    if "at" not in event_data:
        event_data["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    validate_state_doc("events", event_data, file_path=event_file)

    line = json.dumps(event_data, sort_keys=True) + "\n"
    encoded = line.encode("utf-8")

    # Single write in O_APPEND mode ensures kernel-level atomic append
    fd = os.open(event_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)

    # Copy to task folder if task_dir provided or if standard task folder exists
    target_task_dir: Optional[Path] = None
    if task_dir is not None:
        target_task_dir = Path(task_dir)
    else:
        root = resolve_state_root(state_dir)
        default_dir = root.parent / "tasks" / task_id
        if default_dir.is_dir():
            target_task_dir = default_dir

    if target_task_dir is not None:
        target_task_dir.mkdir(parents=True, exist_ok=True)
        dest = target_task_dir / "events.jsonl"
        tmp_dest = (
            dest.parent
            / f".{dest.name}.tmp-{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
        )
        try:
            shutil.copy2(event_file, tmp_dest)
            os.replace(tmp_dest, dest)
        finally:
            if tmp_dest.exists():
                try:
                    tmp_dest.unlink()
                except OSError:
                    pass

    return event_data


def read_events(event_file: Union[str, Path]) -> List[Dict[str, Any]]:
    """Read and validate all events from events.jsonl.
    
    Raises StateError if any line is corrupted or schema-invalid.
    """
    path = Path(event_file).resolve()
    if not path.is_file():
        return []
    results: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                rec = json.loads(line_str)
            except json.JSONDecodeError as e:
                raise StateError(
                    f"Events file '{path}' line {idx} is invalid JSON: {e}; run 'lintel doctor'"
                )
            if not isinstance(rec, dict):
                raise StateError(
                    f"Events file '{path}' line {idx} must be a JSON object; run 'lintel doctor'"
                )
            validate_state_doc("events", rec, file_path=path)
            results.append(rec)
    return results
