"""Review loops, stall detection, and progress evaluation (M1a-19).

Adheres to docs/state-machine.md §9.5 (loops.json, doc name 'loops'):
- Loop key format: <writer>.<stage> (e.g. dev.p1, po.r0, architect.p1, po.docs).
- Round counting (PLAN §3.9, T-VER-25, T-STL-10):
  - A round is one writer run plus one reviewer run; counted when reviewer's
    'done' ('approve' or 'changes') is recorded.
  - Non-round runs: needs-input, blocked, abandoned runs (task unstick), and
    human rejections (--reject) are NOT counted toward rounds (run.counted == False).
  - A round immediately following a human --reject has after_human_reject = True
    and is NOT counted toward rounds or progress (resets after_human_reject = False
    after the round).
- Progress evaluation from round 2 on (T-STL-01):
  - In round 1: baseline findings recorded, no_progress = 0.
  - In round >= 2:
    - Evaluates blocker and major findings in findings.json compared to previous loop findings.
    - A round makes progress (progress = True) iff at least one blocker or major finding
      became fixed for the first time (fixed_once was not previously True), OR became withdrawn.
    - Flip detection: If a finding flips open -> fixed -> open -> fixed, fixed_once was set
      on the first fix, so subsequent fixes do NOT count as new progress!
    - A dispute kept open (open finding with disputed response, still open by reviewer) makes no progress.
    - Minor findings are advice only: fixing or withdrawing minor findings does NOT count as progress.
    - If progress is True: no_progress = 0.
    - If progress is False: no_progress += 1.
- Stall detection (T-STL-02, T-STL-03, T-STL-04):
  - Reads workflow.stallRounds (default 3) and workflow.maxRounds (default 25) from config.
  - If no_progress >= stallRounds: pair is stalled with reason = "no-progress".
  - If since_decision >= maxRounds: pair is stalled with reason = "max-rounds".
  - Emits 'stall' event with reason code.
  - Next action is human: lintel decide --phase <ph>.
- Counter restarts (T-STL-20, T-STL-24..37, T-AMD-60):
  - On human decide or reset: since_decision = 0, no_progress = 0, stalled = None.
- Stalled pair refusal on invoke (T-RUN-10):
  - If a pair is stalled, invoke refuses with "A stalled pair gets no further invoke",
    naming the stall reason and next action human: lintel decide --phase <ph>.
"""

import sys
sys.dont_write_bytecode = True

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    from lintel.config import load_config
    from lintel.events import append_event, create_event
    from lintel.state import (
        StateError,
        VALID_STALL_REASONS,
        atomic_write_json,
        load_state_file,
        resolve_state_root,
        state_lock,
        validate_state_doc,
    )
except ImportError:
    from .config import load_config
    from .events import append_event, create_event
    from .state import (
        StateError,
        VALID_STALL_REASONS,
        atomic_write_json,
        load_state_file,
        resolve_state_root,
        state_lock,
        validate_state_doc,
    )

REVIEWER_ROLES: Set[str] = {
    "po-reviewer",
    "architect-reviewer",
    "qa-reviewer",
    "dev-reviewer",
}

REVIEWER_TO_WRITER: Dict[str, str] = {
    "po-reviewer": "po",
    "architect-reviewer": "architect",
    "qa-reviewer": "qa",
    "dev-reviewer": "dev",
}


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_loop_key(writer_or_role: str, stage: str) -> str:
    """Return loop key in format <writer>.<stage> (e.g. 'dev.p1', 'po.r0')."""
    writer = REVIEWER_TO_WRITER.get(writer_or_role, writer_or_role)
    return f"{writer}.{stage}"


def init_loops_doc() -> Dict[str, Any]:
    """Initialize empty loops state document conforming to §9.5."""
    return {
        "version": 1,
        "loops": {},
    }


def get_loops_file_path(state_dir: Union[str, Path], task_id: str) -> Path:
    """Return path to .lintel/state/<task_id>/loops.json."""
    root = resolve_state_root(state_dir)
    return root / task_id / "loops.json"


def load_loops(state_dir: Union[str, Path], task_id: str) -> Dict[str, Any]:
    """Load loops.json for task, or return initial document if file does not exist."""
    path = get_loops_file_path(state_dir, task_id)
    if not path.is_file():
        return init_loops_doc()
    return load_state_file(path, doc_type="loops")


def save_loops(
    state_dir: Union[str, Path],
    task_id: str,
    loops_data: Dict[str, Any],
) -> None:
    """Validate and atomically write loops.json under state lock."""
    path = get_loops_file_path(state_dir, task_id)
    validate_state_doc("loops", loops_data, file_path=path)
    atomic_write_json(path, loops_data)


def get_or_create_loop(
    loops_data: Dict[str, Any],
    writer_or_role: str,
    stage: str,
) -> Dict[str, Any]:
    """Get or create the loop entry for <writer>.<stage>."""
    key = get_loop_key(writer_or_role, stage)
    loops = loops_data.setdefault("loops", {})
    if key not in loops:
        loops[key] = {
            "rounds": 0,
            "since_decision": 0,
            "no_progress": 0,
            "stalled": None,
            "after_human_reject": False,
            "findings": [],
            "history": [],
            "guidance": [],
        }
    return loops[key]


def is_pair_stalled(
    loops_data: Dict[str, Any],
    writer_or_role: str,
    stage: str,
) -> Tuple[bool, Optional[str]]:
    """Check if loop for writer and stage is stalled.
    
    Returns (is_stalled, reason_or_none).
    """
    key = get_loop_key(writer_or_role, stage)
    loops = loops_data.get("loops", {})
    if key in loops:
        st = loops[key].get("stalled")
        if st and isinstance(st, dict):
            return True, st.get("reason", "no-progress")
    return False, None


def calculate_finding_counts(
    findings: List[Dict[str, Any]],
) -> Dict[str, Dict[str, int]]:
    """Compute finding counts by severity and status for event logging and history."""
    counts: Dict[str, Dict[str, int]] = {
        "blocker": {"open": 0, "fixed": 0, "withdrawn": 0},
        "major": {"open": 0, "fixed": 0, "withdrawn": 0},
        "minor": {"open": 0, "fixed": 0, "withdrawn": 0},
    }
    for f in findings:
        sev = f.get("severity")
        st = f.get("status")
        if sev in counts and st in counts[sev]:
            counts[sev][st] += 1
    return counts


def evaluate_progress(
    previous_findings: List[Dict[str, Any]],
    current_findings: List[Dict[str, Any]],
) -> bool:
    """Evaluate whether the current reviewer findings show progress (T-STL-01).

    Progress rules from round 2 on:
    - Evaluates blocker and major findings in findings.json compared to previous findings.
    - A round makes progress (progress = True) iff at least one blocker or major finding
      became fixed for the first time (fixed_once was not previously True), OR became withdrawn.
    - Flip detection: If a finding flips open -> fixed -> open -> fixed, fixed_once was set
      on the first fix, so subsequent fixes do NOT count as new progress!
    - A dispute kept open (open finding with disputed response, still open by reviewer) makes no progress.
    - Minor findings are advice only: fixing or withdrawing minor findings does NOT count as progress.
    """
    prev_map = {f["id"]: f for f in previous_findings if "id" in f}

    for curr in current_findings:
        sev = curr.get("severity")
        # Minor findings are advice only; fixing or withdrawing minor findings does NOT count as progress
        if sev not in ("blocker", "major"):
            continue

        fid = curr.get("id")
        status = curr.get("status")
        prev = prev_map.get(fid)

        # Human-authored findings never count toward progress (T-STL-10)
        is_human = (curr.get("author") == "human") or (prev and prev.get("author") == "human")
        if is_human:
            continue

        # 1. Became fixed for the first time
        if status == "fixed":
            prev_fixed_once = prev.get("fixed_once", False) if prev else False
            if not prev_fixed_once:
                return True

        # 2. Became withdrawn
        elif status == "withdrawn":
            prev_status = prev.get("status") if prev else None
            if prev_status != "withdrawn":
                return True

    return False


def merge_findings(
    previous_findings: List[Dict[str, Any]],
    current_findings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge current reviewer findings into the loop's findings history.
    
    Preserves fixed_once flag, author, response, ruling, and file path.
    """
    prev_map = {f["id"]: f for f in previous_findings if "id" in f}
    merged: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    for curr in current_findings:
        fid = curr.get("id")
        if not fid:
            continue
        seen_ids.add(fid)
        prev = prev_map.get(fid)

        was_fixed_once = prev.get("fixed_once", False) if prev else False
        is_fixed_once = was_fixed_once or (curr.get("status") == "fixed")

        author = curr.get("author") or (prev.get("author") if prev else "reviewer")
        response = curr.get("response") or (prev.get("response") if prev else None)
        ruling = prev.get("ruling") if prev else None
        file_path = curr.get("file") or (prev.get("file") if prev else None)
        where = curr.get("where") or (prev.get("where") if prev else "")
        summary = curr.get("summary") or (prev.get("summary") if prev else "")

        merged.append({
            "id": fid,
            "severity": curr["severity"],
            "status": curr["status"],
            "author": author,
            "fixed_once": is_fixed_once,
            "response": response,
            "ruling": ruling,
            "file": file_path,
            "where": where,
            "summary": summary,
        })

    # Preserve any previous findings not present in current findings (e.g. human findings)
    for prev in previous_findings:
        fid = prev.get("id")
        if fid and fid not in seen_ids:
            merged.append(copy.deepcopy(prev))

    return merged


def reset_loop_dict(loop: Dict[str, Any]) -> None:
    """Reset since_decision and no_progress to 0 and clear stalled in-memory."""
    loop["since_decision"] = 0
    loop["no_progress"] = 0
    loop["stalled"] = None


def reset_stall_counters(
    state_dir: Union[str, Path],
    task_id: str,
    writer: Optional[str] = None,
    stage: Optional[str] = None,
    loop_key: Optional[str] = None,
) -> None:
    """Reset since_decision and no_progress to 0 and clear stalled for loop(s) on disk (T-STL-20, T-AMD-60)."""
    with state_lock(state_dir):
        loops_data = load_loops(state_dir, task_id)
        target_keys: Set[str] = set()
        if loop_key:
            target_keys.add(loop_key)
        elif writer and stage:
            target_keys.add(get_loop_key(writer, stage))
        elif writer:
            prefix = f"{writer}."
            for k in loops_data.get("loops", {}):
                if k.startswith(prefix):
                    target_keys.add(k)
        else:
            target_keys = set(loops_data.get("loops", {}).keys())

        for k in target_keys:
            if k in loops_data.get("loops", {}):
                reset_loop_dict(loops_data["loops"][k])

        save_loops(state_dir, task_id, loops_data)


def record_human_reject(
    state_dir: Union[str, Path],
    task_id: str,
    writer: str,
    stage: str,
    reason: str,
    file_path: Optional[str] = None,
    finding_id: Optional[str] = None,
    where: Optional[str] = None,
) -> Dict[str, Any]:
    """Record human --reject on a pair's loop (T-STL-10, T-HUM-16).

    Sets after_human_reject = True and records a human blocker finding.
    The human's finding counts for neither progress nor the round counters.
    """
    with state_lock(state_dir):
        loops_data = load_loops(state_dir, task_id)
        loop = get_or_create_loop(loops_data, writer, stage)
        loop["after_human_reject"] = True

        if finding_id:
            fid = finding_id
            existing = next((f for f in loop["findings"] if f.get("id") == fid), None)
        else:
            fixed_human = [
                f for f in loop["findings"]
                if f.get("author") == "human" and f.get("status") == "fixed"
            ]
            if fixed_human:
                existing = fixed_human[-1]
                fid = existing.get("id")
            else:
                existing = None
                human_count = sum(
                    1 for f in loop["findings"]
                    if f.get("author") == "human" or str(f.get("id", "")).startswith("H-")
                )
                fid = f"H-{human_count + 1}"

        if existing:
            existing["status"] = "open"
            existing["author"] = "human"
            existing["summary"] = reason
            if file_path:
                existing["file"] = file_path
            if where:
                existing["where"] = where
        else:
            human_finding = {
                "id": fid,
                "severity": "blocker",
                "status": "open",
                "author": "human",
                "fixed_once": False,
                "response": None,
                "ruling": None,
                "file": file_path,
                "where": where or "human: --reject",
                "summary": reason,
            }
            loop["findings"].append(human_finding)

        save_loops(state_dir, task_id, loops_data)
        return loop


def record_writer_responses(
    state_dir: Union[str, Path],
    task_id: str,
    writer: str,
    stage: str,
    responses: Union[Dict[str, Any], List[Dict[str, Any]]],
) -> None:
    """Record writer's latest responses from responses.json on findings in loop."""
    resp_map: Dict[str, str] = {}
    if isinstance(responses, dict):
        for fid, val in responses.items():
            if isinstance(val, dict):
                resp_map[fid] = val.get("status", "")
            elif isinstance(val, str):
                resp_map[fid] = val
    elif isinstance(responses, list):
        for item in responses:
            if isinstance(item, dict) and "id" in item:
                resp_map[item["id"]] = item.get("status", "")

    if not resp_map:
        return

    with state_lock(state_dir):
        loops_data = load_loops(state_dir, task_id)
        loop = get_or_create_loop(loops_data, writer, stage)
        for f in loop.get("findings", []):
            fid = f.get("id")
            if fid in resp_map:
                f["response"] = resp_map[fid]
        save_loops(state_dir, task_id, loops_data)


def on_reviewer_done(
    state_dir: Union[str, Path],
    task_id: str,
    role: str,
    stage: str,
    run_id: str,
    verdict: str,
    findings: List[Dict[str, Any]],
    repo_root: Optional[Union[str, Path]] = None,
    run_data: Optional[Dict[str, Any]] = None,
    stall_rounds_override: Optional[int] = None,
    max_rounds_override: Optional[int] = None,
) -> Dict[str, Any]:
    """Process reviewer done verdict: round counting, progress evaluation, and stall detection.

    Returns dict containing:
    - counted: bool (True iff this run counted as a round)
    - round: Optional[int] (round number assigned)
    - progress: Optional[bool] (True if round made progress, False if not, None if not counted)
    - no_progress: int (consecutive rounds without progress)
    - since_decision: int (rounds since start or last ruling/decision)
    - stalled: bool (True if pair is stalled)
    - stall_reason: Optional[str] ('no-progress' or 'max-rounds' if stalled)
    - row_id: str ('T-STL-02', 'T-STL-03', 'T-STL-10', 'T-VER-20', or 'T-VER-11')
    """
    writer = REVIEWER_TO_WRITER.get(role, role[:-9] if role.endswith("-reviewer") else role)
    with state_lock(state_dir):
        loops_data = load_loops(state_dir, task_id)
        loop = get_or_create_loop(loops_data, writer, stage)

        # Check if this round immediately follows a human --reject (T-STL-10)
        if loop.get("after_human_reject", False):
            loop["after_human_reject"] = False
            loop["findings"] = merge_findings(loop["findings"], findings)
            save_loops(state_dir, task_id, loops_data)
            if run_data is not None:
                run_data["counted"] = False
                run_data["round"] = loop["rounds"]
            return {
                "counted": False,
                "round": loop["rounds"],
                "progress": None,
                "no_progress": loop["no_progress"],
                "since_decision": loop["since_decision"],
                "stalled": False,
                "stall_reason": None,
                "row_id": "T-STL-10",
            }

        # Normal counted round
        loop["rounds"] += 1
        loop["since_decision"] += 1
        current_round = loop["rounds"]
        if run_data is not None:
            run_data["counted"] = True
            run_data["round"] = current_round

        # Progress evaluation (T-STL-01)
        if current_round == 1:
            # In round 1: baseline findings recorded, no_progress = 0
            progress = True
            loop["no_progress"] = 0
        else:
            # In round >= 2: evaluate blocker/major findings against previous loop findings
            progress = evaluate_progress(loop["findings"], findings)
            if progress:
                loop["no_progress"] = 0
            else:
                loop["no_progress"] += 1

        # Update findings history
        loop["findings"] = merge_findings(loop["findings"], findings)

        # Record round in history
        counts = calculate_finding_counts(loop["findings"])
        loop["history"].append({
            "round": current_round,
            "run": run_id,
            "progress": progress,
            "counts": counts,
        })

        # Stall detection (T-STL-02, T-STL-03)
        is_stalled = False
        stall_reason = None
        stall_rounds = stall_rounds_override or 3
        max_rounds = max_rounds_override or 25

        if stall_rounds_override is None or max_rounds_override is None:
            if repo_root:
                try:
                    cfg = load_config(workspace_root=Path(repo_root))
                    wf = cfg.get("workflow", {})
                    if stall_rounds_override is None:
                        stall_rounds = wf.get("stallRounds", 3)
                    if max_rounds_override is None:
                        max_rounds = wf.get("maxRounds", 25)
                except Exception:
                    pass

        # Stall rules apply when reviewer requests changes
        if verdict == "changes":
            if loop["no_progress"] >= stall_rounds:
                is_stalled = True
                stall_reason = "no-progress"
            elif loop["since_decision"] >= max_rounds:
                is_stalled = True
                stall_reason = "max-rounds"

        if is_stalled:
            loop["stalled"] = {
                "reason": stall_reason,
                "at": _now_iso(),
            }
            row_id = "T-STL-02" if stall_reason == "no-progress" else "T-STL-03"
        else:
            row_id = "T-VER-20" if verdict == "changes" else "T-VER-11"

        save_loops(state_dir, task_id, loops_data)

        return {
            "counted": True,
            "round": current_round,
            "progress": progress,
            "no_progress": loop["no_progress"],
            "since_decision": loop["since_decision"],
            "stalled": is_stalled,
            "stall_reason": stall_reason,
            "row_id": row_id,
        }

def record_arbiter_ruling(
    state_dir: Union[str, Path],
    task_id: str,
    writer: str,
    stage: str,
    ruling: Dict[str, Dict[str, str]],
) -> None:
    """Record arbiter ruling and restart counters (T-STL-07)."""
    with state_lock(state_dir):
        loops_data = load_loops(state_dir, task_id)
        loop = get_or_create_loop(loops_data, writer, stage)
        
        for f in loop.get("findings", []):
            fid = f["id"]
            if fid in ruling:
                status = ruling[fid]["status"]
                f["arbiter_ruled"] = status
                if status == "withdraw":
                    f["status"] = "withdrawn"
                elif status == "fix" or status == "human":
                    f["status"] = "open"
        
        loop["history"].append({
            "event": "ruling",
            "at": _now_iso(),
        })
        
        loop["no_progress"] = 0
        loop["since_decision"] = 0
        loop["stalled"] = None
        
        save_loops(state_dir, task_id, loops_data)
