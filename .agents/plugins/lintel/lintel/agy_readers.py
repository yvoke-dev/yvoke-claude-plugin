"""Best-effort readers of agy state next to a conversation (M1a-35, PLAN §3.2 step 4, §3.5 State).

Provides readers of what agy writes next to a conversation:
- The invoking conversation's .system_generated/subagents/<child>.json
  (typeName, role, state, spawnStepIndex)
- Mailbox .system_generated/messages/*.json
  (notices: "explicitly canceled", "stopped due to server restart")
- Persona transcript_full.jsonl (and fallback transcript.jsonl) tail
  (skipping partial last lines; permission denials; model steps with no tool call)
- Running background command marker in tasks/task-N.log and transcript steps
  (running step with no later finished notice)

Returns an AgyObservation / PersonaObservation:
- status: killed, permission-denied, stopped-by-restart, ended-without-verdict,
          command-pending, quiet, unknown.
- stop_cause: killed, permission-denied, restart, ended-without-verdict, or None.
Robustness: any missing, unreadable, or unexpected file yields "unknown", never raises an exception.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

sys.dont_write_bytecode = True


@dataclass
class AgyObservation:
    """Standardized observation outcome for an agy persona run."""
    status: str  # "killed", "permission-denied", "stopped-by-restart", "ended-without-verdict", "command-pending", "quiet", "unknown"
    child_conversation_id: Optional[str] = None
    subagent_state: Optional[str] = None  # Raw state string from child record (e.g. "SUBAGENT_STATE_ALIVE", "SUBAGENT_STATE_KILLED")
    last_step_time: Optional[str] = None  # ISO timestamp string
    last_step_tool: Optional[str] = None  # Tool name if any in last step
    last_step_index: Optional[int] = None
    last_step_content: Optional[str] = None  # Preview snippet of last step content
    stop_cause: Optional[str] = None  # Matching state-machine enumerations: "killed", "permission-denied", "restart", "ended-without-verdict", or None
    details: Optional[str] = None


PersonaObservation = AgyObservation


def resolve_invoking_context(
    audit_log_path: Optional[Union[str, Path]] = None,
    hints: Optional[Union[Dict[str, Any], str, Path]] = None,
    role: Optional[str] = None,
    nonce: Optional[str] = None,
    base_dir: Optional[Union[str, Path]] = None,
) -> Tuple[Optional[str], Optional[Path]]:
    """Resolve the invoking conversation id and its artifact directory path.

    Order of precedence:
    1. The run's invoke_subagent event in audit_log_path (conversationId, artifactDirectoryPath).
    2. run.hints (conversation_id, app_data_dir -> <app_data_dir>/brain/<conversation_id>).

    Returns:
        (invoking_conversation_id, invoking_artifact_dir) or (None, None).
    """
    # 1. Try audit log
    if audit_log_path:
        audit_file = Path(audit_log_path)
        if not audit_file.is_file() and not audit_file.is_absolute() and base_dir:
            candidate = (Path(base_dir) / audit_file).resolve()
            if candidate.is_file():
                audit_file = candidate
        if audit_file.is_file():
            try:
                for line in audit_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue

                    tool = entry.get("tool") or entry.get("toolName")
                    if tool not in ("invoke_subagent", "invoke"):
                        continue

                    # Check role / nonce match if specified
                    marker = entry.get("marker") or {}
                    marker_role = marker.get("role")
                    marker_nonce = marker.get("nonce")
                    prompt = entry.get("prompt") or ""
                    target = entry.get("target") or ""

                    role_matches = True
                    if role:
                        role_matches = (
                            marker_role == role
                            or target in (role, f"lintel-{role}")
                            or f"[lintel:{role}:" in prompt
                            or f"enter {role} " in prompt
                        )

                    nonce_matches = True
                    if nonce:
                        nonce_matches = (
                            marker_nonce == nonce
                            or f":{nonce}]" in prompt
                            or f" {nonce}" in prompt
                        )

                    if role_matches and nonce_matches:
                        conv_id = entry.get("conversationId") or entry.get("conv")
                        art_dir = entry.get("artifactDirectoryPath")
                        if conv_id and art_dir:
                            art_path = Path(art_dir)
                            if not art_path.is_absolute():
                                root = Path(base_dir) if base_dir else audit_file.parent
                                art_path = (root / art_path).resolve()
                            return str(conv_id), art_path
            except Exception:
                pass

    # 2. Fall back to hints
    if hints:
        hints_dict: Optional[Dict[str, Any]] = None
        hints_base = Path(base_dir) if base_dir else None
        if isinstance(hints, (str, Path)):
            hp = Path(hints)
            if not hp.is_file() and not hp.is_absolute() and hints_base:
                candidate = (hints_base / hp).resolve()
                if candidate.is_file():
                    hp = candidate
            if hp.is_file():
                try:
                    hints_dict = json.loads(hp.read_text(encoding="utf-8", errors="replace"))
                    hints_base = hp.parent
                except Exception:
                    pass
        elif isinstance(hints, dict):
            hints_dict = hints

        if hints_dict:
            conv_id = (
                hints_dict.get("conversation_id")
                or hints_dict.get("invoking_conversation_id")
                or hints_dict.get("invoking_id")
            )
            inv_art_dir = hints_dict.get("invoking_artifact_dir")
            if inv_art_dir:
                art_path = Path(inv_art_dir)
                if not art_path.is_absolute() and hints_base:
                    art_path = (hints_base / art_path).resolve()
                return str(conv_id) if conv_id else None, art_path

            app_data_dir = hints_dict.get("app_data_dir")
            if conv_id and app_data_dir:
                app_dir_path = Path(app_data_dir)
                if not app_dir_path.is_absolute() and hints_base:
                    app_dir_path = (hints_base / app_dir_path).resolve()
                art_path = app_dir_path / "brain" / str(conv_id)
                return str(conv_id), art_path

    return None, None


def read_subagent_record(subagent_file_path: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """Safely read a child subagent json record (.system_generated/subagents/<child>.json)."""
    p = Path(subagent_file_path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def find_child_record(
    invoking_artifact_dir: Union[str, Path],
    child_id: Optional[str] = None,
    role: Optional[str] = None,
    nonce: Optional[str] = None,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Find the target child conversation id and its record dict under invoking artifact dir.

    Returns:
        (child_conversation_id, record_dict) or (None, None).
    """
    subagents_dir = Path(invoking_artifact_dir) / ".system_generated" / "subagents"
    if not subagents_dir.is_dir():
        # If child_id was given, we might still know the child_id even without subagent record
        if child_id:
            return str(child_id), None
        return None, None

    # If child_id explicitly given:
    if child_id:
        target_file = subagents_dir / f"{child_id}.json"
        record = read_subagent_record(target_file)
        if record:
            return str(child_id), record
        # Search all records in case filename differs
        try:
            for entry in subagents_dir.glob("*.json"):
                rec = read_subagent_record(entry)
                if rec and rec.get("conversationId") == child_id:
                    return str(child_id), rec
        except Exception:
            pass
        return str(child_id), None

    # Search by role / nonce
    candidates: List[Tuple[int, float, str, Dict[str, Any]]] = []
    try:
        for entry in subagents_dir.glob("*.json"):
            rec = read_subagent_record(entry)
            if not rec:
                continue
            rec_cid = rec.get("conversationId") or entry.stem
            descriptor = rec.get("subagentDescriptor") or {}
            rec_role = descriptor.get("role")
            rec_type = descriptor.get("typeName")
            spawn_idx = rec.get("spawnStepIndex") or 0

            matches = True
            if role:
                clean_role = role.replace("lintel-", "")
                matches = (
                    rec_role in (role, clean_role, f"lintel-{clean_role}")
                    or rec_type in (role, clean_role, f"lintel-{clean_role}")
                )

            if matches:
                try:
                    mtime = entry.stat().st_mtime
                except Exception:
                    mtime = 0.0
                candidates.append((spawn_idx, mtime, str(rec_cid), rec))
    except Exception:
        pass

    if candidates:
        # Sort by spawnStepIndex desc, then mtime desc
        candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
        chosen = candidates[0]
        return chosen[2], chosen[3]

    return None, None


def read_mailbox_notices(
    invoking_artifact_dir: Union[str, Path],
    child_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Inspect invoking mailbox messages (.system_generated/messages/*.json) for stop notices.

    Checks:
    - "stopped due to server restart" -> restart notice
    - "explicitly canceled" -> killed notice for child_id
    """
    result: Dict[str, Any] = {
        "restart": False,
        "explicitly_canceled": False,
        "messages": [],
    }
    messages_dir = Path(invoking_artifact_dir) / ".system_generated" / "messages"
    if not messages_dir.is_dir():
        return result

    try:
        for msg_file in messages_dir.glob("*.json"):
            if msg_file.name in ("read.json", "undelivered.json"):
                continue
            try:
                msg_data = json.loads(msg_file.read_text(encoding="utf-8", errors="replace"))
                if not isinstance(msg_data, dict):
                    continue
                content = str(msg_data.get("content") or "")
                sender = str(msg_data.get("sender") or "")

                content_lower = content.lower()
                if "stopped due to server restart" in content_lower:
                    result["restart"] = True
                    result["messages"].append(content)

                if "explicitly canceled" in content_lower:
                    if not child_id or child_id in content:
                        result["explicitly_canceled"] = True
                        result["messages"].append(content)
            except Exception:
                continue
    except Exception:
        pass

    return result


def read_transcript_tail(
    transcript_path: Union[str, Path],
    max_bytes: int = 65536,
) -> List[Dict[str, Any]]:
    """Safely read parsed step JSON objects from the tail of a transcript file.

    Handles files up to max_bytes from end, skipping partial or incomplete lines.
    """
    p = Path(transcript_path)
    if not p.is_file():
        return []

    try:
        size = p.stat().st_size
        if size == 0:
            return []

        seek_pos = max(0, size - max_bytes)
        with p.open("rb") as f:
            if seek_pos > 0:
                f.seek(seek_pos)
            data = f.read()

        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if not lines:
            return []

        # If we seeked into the middle of the file, discard the first line fragment
        if seek_pos > 0 and len(lines) > 1:
            lines = lines[1:]

        steps: List[Dict[str, Any]] = []
        for i, line in enumerate(lines):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                step_obj = json.loads(line_str)
                if isinstance(step_obj, dict):
                    steps.append(step_obj)
            except Exception:
                # If the last line is partial / incomplete, safely skip it
                if i == len(lines) - 1:
                    continue
                # For inner unparseable lines, skip
                continue

        # Sort steps by step_index if present
        steps.sort(key=lambda s: s.get("step_index", 0))
        return steps
    except Exception:
        return []


def check_background_task_running(
    child_artifact_dir: Optional[Union[str, Path]],
    steps: Sequence[Dict[str, Any]],
) -> bool:
    """Check if a background command is currently pending execution.

    True if a step indicated background execution started and no later step records finished.
    """
    if not steps:
        return False

    running_task_id: Optional[str] = None
    running_task_idx = -1

    task_start_pattern = re.compile(
        r"Tool is running as a background task with task id:?\s*([^\s\n]+)",
        re.IGNORECASE,
    )

    for idx, step in enumerate(steps):
        content = str(step.get("content") or "") + " " + str(step.get("error") or "")
        m = task_start_pattern.search(content)
        if m:
            running_task_id = m.group(1).strip("\"'")
            running_task_idx = idx

    if running_task_id is None:
        return False

    # Extract short task name (e.g., 'task-8' from 'conv/task-8')
    short_task_name = running_task_id.split("/")[-1]

    # Check if any later step recorded finished
    for step in steps[running_task_idx + 1:]:
        step_text = str(step.get("content") or "") + " " + str(step.get("error") or "")
        if (
            f'Task id "{running_task_id}" finished' in step_text
            or f'Task id "{short_task_name}" finished' in step_text
            or f"Task id '{running_task_id}' finished" in step_text
            or f"Task id '{short_task_name}' finished" in step_text
            or f'task id "{running_task_id}" finished' in step_text.lower()
            or f'task id "{short_task_name}" finished' in step_text.lower()
        ):
            # Task finished
            return False

    # Task was started and not finished in transcript
    # Check if task log exists or task marker was verified
    if child_artifact_dir:
        tasks_dir = Path(child_artifact_dir) / ".system_generated" / "tasks"
        log_file = tasks_dir / f"{short_task_name}.log"
        if log_file.is_file():
            return True
        # If any task log exists in tasks_dir, also confirmed
        if tasks_dir.is_dir() and list(tasks_dir.glob("*.log")):
            return True

    return True


def inspect_agy_state(
    invoking_artifact_dir: Optional[Union[str, Path]] = None,
    invoking_conversation_id: Optional[str] = None,
    child_conversation_id: Optional[str] = None,
    role: Optional[str] = None,
    nonce: Optional[str] = None,
    audit_log_path: Optional[Union[str, Path]] = None,
    hints: Optional[Union[Dict[str, Any], str, Path]] = None,
    hints_path: Optional[Union[str, Path]] = None,
    child_artifact_dir: Optional[Union[str, Path]] = None,
    transcript_path: Optional[Union[str, Path]] = None,
    base_dir: Optional[Union[str, Path]] = None,
) -> AgyObservation:
    """Best-effort reader of agy state next to a conversation.

    Inputs:
    - Invoking conversation id and artifact dir (provided or derived from audit log / hints).
    - Child conversation id, role, or nonce to inspect.
    - Optional direct paths to child artifact dir or transcript.

    Returns:
        AgyObservation with standardized status and stop_cause.
    """
    try:
        if hints is None and hints_path is not None:
            hints = hints_path

        # 1. Resolve invoking context if not directly provided
        if not invoking_artifact_dir:
            resolved_cid, resolved_dir = resolve_invoking_context(
                audit_log_path=audit_log_path,
                hints=hints,
                role=role,
                nonce=nonce,
                base_dir=base_dir,
            )
            if resolved_cid and not invoking_conversation_id:
                invoking_conversation_id = resolved_cid
            if resolved_dir:
                invoking_artifact_dir = resolved_dir

        if not invoking_artifact_dir:
            return AgyObservation(
                status="unknown",
                details="Could not resolve invoking artifact directory from audit log, hints, or arguments.",
            )

        inv_dir = Path(invoking_artifact_dir)
        if not inv_dir.is_dir():
            return AgyObservation(
                status="unknown",
                details=f"Invoking artifact directory does not exist: {inv_dir}",
            )

        # 2. Find child conversation and record
        child_id, child_record = find_child_record(
            invoking_artifact_dir=inv_dir,
            child_id=child_conversation_id,
            role=role,
            nonce=nonce,
        )

        if not child_id:
            return AgyObservation(
                status="unknown",
                details="Target child conversation could not be identified in invoking subagents.",
            )

        raw_state = child_record.get("state") if child_record else None

        # 3. Read mailbox notices
        mailbox_info = read_mailbox_notices(inv_dir, child_id=child_id)

        # 4. Locate child transcript
        target_transcript: Optional[Path] = None
        if transcript_path:
            tp = Path(transcript_path)
            if not tp.is_absolute() and base_dir:
                tp = (Path(base_dir) / tp).resolve()
            if tp.is_file():
                target_transcript = tp

        child_art_dir = (
            Path(child_artifact_dir)
            if child_artifact_dir
            else inv_dir.parent / child_id
        )
        if not child_art_dir.is_absolute() and base_dir:
            child_art_dir = (Path(base_dir) / child_art_dir).resolve()

        if not target_transcript and child_art_dir.is_dir():
            candidate_full = child_art_dir / ".system_generated" / "logs" / "transcript_full.jsonl"
            candidate_std = child_art_dir / ".system_generated" / "logs" / "transcript.jsonl"
            if candidate_full.is_file():
                target_transcript = candidate_full
            elif candidate_std.is_file():
                target_transcript = candidate_std

        # 5. Read transcript steps
        steps: List[Dict[str, Any]] = []
        if target_transcript:
            steps = read_transcript_tail(target_transcript)

        last_step: Optional[Dict[str, Any]] = steps[-1] if steps else None
        last_step_time = last_step.get("created_at") if last_step else None
        last_step_index = last_step.get("step_index") if last_step else None
        tool_calls = last_step.get("tool_calls") or [] if last_step else []
        last_step_tool = tool_calls[0].get("name") if tool_calls else None
        last_step_content: Optional[str] = None
        if last_step:
            text = str(last_step.get("content") or last_step.get("error") or "")
            last_step_content = text[:100]

        # 6. Apply precedence to determine status and stop_cause

        # a. Child record explicitly KILLED
        if raw_state and ("KILLED" in str(raw_state).upper() or str(raw_state).lower() == "killed"):
            return AgyObservation(
                status="killed",
                child_conversation_id=child_id,
                subagent_state=raw_state,
                last_step_time=last_step_time,
                last_step_tool=last_step_tool,
                last_step_index=last_step_index,
                last_step_content=last_step_content,
                stop_cause="killed",
                details="Subagent record state is KILLED",
            )

        # b. Transcript last step has status == "ERROR" naming permission denied
        if last_step and str(last_step.get("status")).upper() == "ERROR":
            err_text = (
                str(last_step.get("error") or "") + " " + str(last_step.get("content") or "")
            ).lower()
            if "permission check failed" in err_text or "user denied" in err_text:
                return AgyObservation(
                    status="permission-denied",
                    child_conversation_id=child_id,
                    subagent_state=raw_state,
                    last_step_time=last_step_time,
                    last_step_tool=last_step_tool,
                    last_step_index=last_step_index,
                    last_step_content=last_step_content,
                    stop_cause="permission-denied",
                    details="Transcript error: permission check failed / user denied",
                )

        # c. Mailbox notices
        if mailbox_info.get("restart"):
            return AgyObservation(
                status="stopped-by-restart",
                child_conversation_id=child_id,
                subagent_state=raw_state,
                last_step_time=last_step_time,
                last_step_tool=last_step_tool,
                last_step_index=last_step_index,
                last_step_content=last_step_content,
                stop_cause="restart",
                details="Mailbox notice: stopped due to server restart",
            )

        if mailbox_info.get("explicitly_canceled"):
            return AgyObservation(
                status="permission-denied",
                child_conversation_id=child_id,
                subagent_state=raw_state,
                last_step_time=last_step_time,
                last_step_tool=last_step_tool,
                last_step_index=last_step_index,
                last_step_content=last_step_content,
                stop_cause="permission-denied",
                details="Mailbox notice: explicitly canceled",
            )

        # d. Background command pending
        if check_background_task_running(child_art_dir, steps):
            return AgyObservation(
                status="command-pending",
                child_conversation_id=child_id,
                subagent_state=raw_state,
                last_step_time=last_step_time,
                last_step_tool=last_step_tool,
                last_step_index=last_step_index,
                last_step_content=last_step_content,
                stop_cause=None,
                details="Background command task is running",
            )

        # e. Model step with no tool call and not in error (ended without verdict)
        if last_step:
            src = str(last_step.get("source") or "").upper()
            typ = str(last_step.get("type") or "").upper()
            st = str(last_step.get("status") or "").upper()
            if src == "MODEL" and typ == "PLANNER_RESPONSE" and not tool_calls and st != "ERROR":
                for prev_step in reversed(steps):
                    if str(prev_step.get("status")).upper() == "ERROR":
                        err_text = (
                            str(prev_step.get("error") or "") + " " + str(prev_step.get("content") or "")
                        ).lower()
                        if "permission check failed" in err_text or "user denied" in err_text:
                            return AgyObservation(
                                status="permission-denied",
                                child_conversation_id=child_id,
                                subagent_state=raw_state,
                                last_step_time=last_step_time,
                                last_step_tool=last_step_tool,
                                last_step_index=last_step_index,
                                last_step_content=last_step_content,
                                stop_cause="permission-denied",
                                details="Transcript error before turn end: permission check failed / user denied",
                            )
                return AgyObservation(
                    status="ended-without-verdict",
                    child_conversation_id=child_id,
                    subagent_state=raw_state,
                    last_step_time=last_step_time,
                    last_step_tool=last_step_tool,
                    last_step_index=last_step_index,
                    last_step_content=last_step_content,
                    stop_cause="ended-without-verdict",
                    details="Final model step with no tool calls and no verdict",
                )

        # f. Active persona with recent activity and no stop
        if steps:
            return AgyObservation(
                status="quiet",
                child_conversation_id=child_id,
                subagent_state=raw_state,
                last_step_time=last_step_time,
                last_step_tool=last_step_tool,
                last_step_index=last_step_index,
                last_step_content=last_step_content,
                stop_cause=None,
                details="Persona active, no terminal stop observed",
            )

        # g. Transcript missing or empty, child record not terminal -> unknown
        return AgyObservation(
            status="unknown",
            child_conversation_id=child_id,
            subagent_state=raw_state,
            stop_cause=None,
            details="No readable transcript steps or conclusive state found",
        )

    except Exception as exc:
        return AgyObservation(
            status="unknown",
            details=f"Unexpected error inspecting agy state: {exc}",
        )
