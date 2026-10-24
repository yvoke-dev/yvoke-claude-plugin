"""Progress, events, and summary rendering (M1a-30, PLAN §3.10).

Provides:
- render_progress_md: Renders .lintel/tasks/<task_id>/progress.md.
- render_summary_md: Renders .lintel/tasks/<task_id>/summary.md.
- update_progress_md: Unified atomic writer for progress.md across CLI commands.
- extract_next_action: Utility to extract next action from progress markdown.
"""

import sys
sys.dont_write_bytecode = True

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    from lintel import flow
    from lintel.state import load_state_file, atomic_write_json, resolve_state_root, StateError
    from lintel.events import read_events, get_events_log_path
    from lintel import documents
except ImportError:
    from . import flow
    from .state import load_state_file, atomic_write_json, resolve_state_root, StateError
    from .events import read_events, get_events_log_path
    from . import documents


def _get_last_gate_result(
    repo_root: Path,
    task_id: str,
    task_data: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Determine the most recent gate result across checks.json, phases, and baseline."""
    candidates: List[Dict[str, Any]] = []
    state_dir = repo_root / ".lintel" / "state"

    # 1. checks.json
    checks_file = state_dir / task_id / "checks.json"
    if checks_file.is_file():
        try:
            cdoc = load_state_file(checks_file, doc_type="checks")
            checks = cdoc.get("checks", [])
            if checks:
                last_c = checks[-1]
                candidates.append({
                    "kind": "check",
                    "result": last_c.get("result", "unknown"),
                    "log": last_c.get("log", ""),
                    "at": last_c.get("finished_at") or last_c.get("started_at") or "",
                    "groups": last_c.get("groups", {}),
                    "seq": last_c.get("seq", 0),
                })
        except Exception:
            pass

    # 2. Phases (delivered and lock)
    for p_key, p_info in (task_data.get("phases") or {}).items():
        if isinstance(p_info, dict):
            deliv = p_info.get("delivered")
            if isinstance(deliv, dict) and deliv.get("commit"):
                candidates.append({
                    "kind": "deliver",
                    "result": "green",
                    "log": deliv.get("log", ""),
                    "at": deliv.get("at", ""),
                    "groups": deliv.get("groups", {}),
                    "phase": p_key,
                })
            lock = p_info.get("lock")
            if isinstance(lock, dict) and lock.get("commit"):
                candidates.append({
                    "kind": "lock",
                    "result": "green",
                    "log": "",
                    "at": lock.get("at", ""),
                    "groups": lock.get("groups", {}),
                    "phase": p_key,
                })

    # 3. Baseline
    base = task_data.get("baseline")
    if isinstance(base, dict) and base.get("at"):
        candidates.append({
            "kind": "baseline",
            "result": "green",
            "log": base.get("log", ""),
            "at": base.get("at", ""),
            "groups": base.get("groups", {}),
        })

    # 4. events.jsonl
    events_file = get_events_log_path(state_dir, task_id)
    if events_file.is_file():
        try:
            events = read_events(events_file)
            for ev in reversed(events):
                if ev.get("event") in ("check", "lock", "deliver", "start") and ev.get("gate"):
                    candidates.append({
                        "kind": ev.get("event"),
                        "result": "green" if ev.get("verdict") != "red" else "red",
                        "log": ev.get("folder", ""),
                        "at": ev.get("at", ""),
                        "groups": ev.get("gate", {}),
                        "duration": ev.get("duration_seconds"),
                    })
                    break
        except Exception:
            pass

    if not candidates:
        return None

    # Pick candidate with latest timestamp
    candidates.sort(key=lambda c: str(c.get("at", "")), reverse=True)
    return candidates[0]


def render_progress_md(
    repo_root: Path,
    task_id: str,
    task_data: Optional[Dict[str, Any]] = None,
    next_action: Optional[str] = None,
) -> str:
    """Render .lintel/tasks/<task_id>/progress.md.

    Sections:
    - Position: Task ID, title, branch, recorded branch, base SHA, stage and step code, next action.
    - Last gate result: Short summary of the latest gate check/lock/deliver.
    - Plan: Every phase with approvals, lock and delivery commits, rounds per pair.
    - Open items: Numbers of unanswered questions, stalls, unacknowledged drift and audit findings.
    - Journal: Last 30 events from events.jsonl.

    CRITICAL CONSTRAINT: Holds IDs, counts, hashes, commits, and enumerated status/role/stage codes only!
    Zero free text (no finding text, question text, guidance text, rejection rationale, test output).
    """
    root = Path(repo_root).resolve()
    state_dir = root / ".lintel" / "state"

    if task_data is None:
        task_file = state_dir / task_id / "task.json"
        if task_file.is_file():
            try:
                task_data = load_state_file(task_file, doc_type="task")
            except Exception:
                task_data = {}
        else:
            task_data = {}

    clean_title = task_data.get("title", task_id)
    status = task_data.get("status", "active")
    branch = task_data.get("branch", f"lintel/{task_id}")
    recorded_branch = task_data.get("recorded_branch", "")
    base_sha = task_data.get("base_sha", "")

    pos = task_data.get("position") or {}
    stage = pos.get("stage", "r0")
    code = pos.get("code", "NONE")
    fr = pos.get("fr", False) or "+FR" in code
    code_str = code if ("+FR" in code or not fr) else f"{code}+FR"
    stage_code = f"{stage}:{code_str}"

    if next_action is None:
        try:
            check_root = root if (state_dir / task_id).is_dir() else None
            next_action = flow.format_next_action(task_data, repo_root=check_root)
        except Exception:
            next_action = "lintel task status"

    # 1. Header & Position
    lines: List[str] = [
        f"# Task: {clean_title} ({task_id})",
        "",
        "## Position",
        f"- **Task ID:** {task_id}",
        f"- **Title:** {clean_title}",
        f"- **Status:** {status}",
        f"- **Branch:** `{branch}`",
        f"- **Recorded branch:** `{recorded_branch}`",
        f"- **Base SHA:** `{base_sha}`",
        f"- **Position:** stage `{stage}`, code `{code_str}` (`{stage_code}`)",
        f"- **Next action:** `{next_action}`",
        "",
    ]

    # 2. Last gate result
    lines.append("## Last gate result")
    gate_res = _get_last_gate_result(root, task_id, task_data)
    if gate_res is None:
        lines.append("- None")
    else:
        lines.append(f"- **Kind:** {gate_res.get('kind', 'unknown')}")
        lines.append(f"- **Result:** {gate_res.get('result', 'unknown')}")
        if gate_res.get("log"):
            lines.append(f"- **Log:** {gate_res['log']}")
        if gate_res.get("duration") is not None:
            lines.append(f"- **Duration:** {gate_res['duration']}s")
        groups = gate_res.get("groups", {})
        if groups:
            lines.append("- **Groups:**")
            for gname in sorted(groups.keys()):
                gdata = groups[gname]
                if isinstance(gdata, dict):
                    exit_code = gdata.get("exit", 0)
                    st = gdata.get("status")
                    secs = gdata.get("seconds")
                    details: List[str] = []
                    if st:
                        details.append(f"status: {st}")
                    if secs is not None:
                        details.append(f"{secs}s")
                    det_str = f" ({', '.join(details)})" if details else ""
                    lines.append(f"  - {gname}: exit {exit_code}{det_str}")
                else:
                    lines.append(f"  - {gname}: {gdata}")
    lines.append("")

    # 3. Plan
    reviews_file = state_dir / task_id / "reviews.json"
    reviews_data: Dict[str, Any] = {}
    if reviews_file.is_file():
        try:
            reviews_data = load_state_file(reviews_file, doc_type="reviews")
        except Exception:
            pass

    loops_file = state_dir / task_id / "loops.json"
    loops_data: Dict[str, Any] = {}
    if loops_file.is_file():
        try:
            loops_data = load_state_file(loops_file, doc_type="loops")
        except Exception:
            pass
    loops = loops_data.get("loops", {})

    phases = task_data.get("phases") or {}
    lines.append("## Plan")
    if not phases:
        lines.append("- No phases planned yet.")
        po_r0 = loops.get("po.r0", {}).get("rounds", 0)
        arch_r0 = loops.get("architect.r0", {}).get("rounds", 0)
        lines.append(f"- **Stage r0 rounds:** po: {po_r0}, architect: {arch_r0}")
    else:
        sorted_p_keys = sorted(phases.keys(), key=lambda k: int(k) if str(k).isdigit() else str(k))
        for p_key in sorted_p_keys:
            p_info = phases[p_key]
            p_num = int(p_key) if str(p_key).isdigit() else p_key
            lines.append(f"\n### Phase {p_key}")

            design_commits = [c.get("sha") for c in p_info.get("design_commits", []) if isinstance(c, dict) and c.get("sha")]
            design_str = ", ".join(design_commits) if design_commits else "none"
            lines.append(f"- **Design commits:** {design_str}")

            lock_sha = p_info.get("lock", {}).get("commit") if isinstance(p_info.get("lock"), dict) else None
            lines.append(f"- **Lock commit:** {lock_sha or 'none'}")

            amends = p_info.get("amends", [])
            amend_strs = []
            for a in amends:
                if isinstance(a, dict):
                    if a.get("commit"):
                        amend_strs.append(f"{a['commit']} ({a.get('kind', 'amend')})")
                    elif a.get("kind"):
                        amend_strs.append(a["kind"])
            amends_str = ", ".join(amend_strs) if amend_strs else "none"
            lines.append(f"- **Amend commits:** {amends_str}")

            deliv_sha = p_info.get("delivered", {}).get("commit") if isinstance(p_info.get("delivered"), dict) else None
            lines.append(f"- **Delivery commit:** {deliv_sha or 'none'}")

            qa_rnds = loops.get(f"qa.p{p_num}", {}).get("rounds", 0)
            dev_rnds = loops.get(f"dev.p{p_num}", {}).get("rounds", 0)
            arch_rnds = loops.get(f"architect.p{p_num}", {}).get("rounds", 0)
            pair_rnds = [f"qa: {qa_rnds}", f"dev: {dev_rnds}"]
            if arch_rnds > 0:
                pair_rnds.append(f"architect: {arch_rnds}")
            lines.append(f"- **Rounds per pair:** {', '.join(pair_rnds)}")
    lines.append("")

    # 4. Open items
    open_questions_count = 0
    runs_dir = state_dir / task_id / "runs"
    if runs_dir.is_dir():
        for rf in runs_dir.glob("*.json"):
            try:
                rdata = json.loads(rf.read_text(encoding="utf-8"))
                for q in rdata.get("questions", []):
                    if isinstance(q, dict) and q.get("answered_at") is None:
                        open_questions_count += 1
            except Exception:
                pass

    stall_count = 0
    for l_key, l_val in loops.items():
        if isinstance(l_val, dict) and l_val.get("stalled"):
            stall_count += 1
    if stall_count == 0 and (task_data.get("escalation") or {}).get("kind") == "stall":
        stall_count = 1

    unacked_drift_count = 0
    head_sha = task_data.get("head")
    p_num = pos.get("phase")
    if head_sha and p_num is not None:
        try:
            from lintel import drift
            matches, cur_map, ack_map = drift.check_drift_matches_ack(root, phase=p_num)
            for path_name in cur_map:
                if cur_map.get(path_name) != ack_map.get(path_name):
                    unacked_drift_count += 1
        except Exception:
            pass

    unacked_audit_count = 0
    refusals_file = state_dir / "refusals.jsonl"
    acked_audit_ids = set()
    acks_file = state_dir / task_id / "acks.json"
    if acks_file.is_file():
        try:
            adata = load_state_file(acks_file, doc_type="acks")
            acked_audit_ids.update(adata.get("audit", {}).get("acked_ids", []))
        except Exception:
            pass

    if refusals_file.is_file():
        try:
            with open(refusals_file, "r", encoding="utf-8") as f:
                for rline in f:
                    rline = rline.strip()
                    if not rline:
                        continue
                    r_entry = json.loads(rline)
                    r_id = r_entry.get("id")
                    if r_entry.get("task") in (None, task_id):
                        if r_id and r_id not in acked_audit_ids:
                            unacked_audit_count += 1
        except Exception:
            pass

    lines.extend([
        "## Open items",
        f"- **Unanswered questions:** {open_questions_count}",
        f"- **Stalls:** {stall_count}",
        f"- **Unacknowledged drift:** {unacked_drift_count}",
        f"- **Unacknowledged audit findings:** {unacked_audit_count}",
        "",
    ])

    # 5. Journal
    events_file = get_events_log_path(state_dir, task_id)
    events: List[Dict[str, Any]] = []
    if events_file.is_file():
        try:
            events = read_events(events_file)
        except Exception:
            try:
                with open(events_file, "r", encoding="utf-8") as f:
                    for l in f:
                        l = l.strip()
                        if l:
                            try:
                                events.append(json.loads(l))
                            except Exception:
                                pass
            except Exception:
                pass

    recent_events = events[-30:] if len(events) > 30 else events
    lines.append("## Journal")
    if not recent_events:
        lines.append("- No events recorded yet.")
    else:
        for ev in recent_events:
            parts = [f"stage: {ev.get('stage', '')}"]
            if ev.get("role"):
                parts.append(f"role: {ev['role']}")
            if ev.get("run"):
                parts.append(f"run: {ev['run']}")
            if ev.get("round") is not None:
                parts.append(f"round: {ev['round']}")
            if ev.get("verdict"):
                parts.append(f"verdict: {ev['verdict']}")
            if ev.get("cause"):
                parts.append(f"cause: {ev['cause']}")
            if ev.get("stop_cause"):
                parts.append(f"stop_cause: {ev['stop_cause']}")
            if ev.get("decision"):
                parts.append(f"decision: {ev['decision']}")
            if ev.get("stall_reason"):
                parts.append(f"stall_reason: {ev['stall_reason']}")
            if ev.get("row"):
                parts.append(f"row: {ev['row']}")
            details = ", ".join(parts)
            lines.append(f"- [step {ev.get('step', '?')}] {ev.get('at', '')}: {ev.get('event', '')} ({details})")
    lines.append("")

    return "\n".join(lines)


def render_summary_md(
    repo_root: Path,
    task_id: str,
    task_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Render .lintel/tasks/<task_id>/summary.md.

    Sections:
    - Task title, ID, base SHA, branch.
    - Requirements: list of requirement IDs, marking any that are Behaviour: unchanged.
    - Phases: each phase's requirement IDs, its design, test, amend, and feat commits, rounds per pair,
      and delivery exit code and duration per test group.
    - Counts: stalls, arbiter rulings, human decisions, human rejections, acked drift findings, acked audit crossings.
    - Docs update: outcome, specs changed, steering changed.
    - Deferred requirement problems: IDs with stage deferred in.
    - Path to final reviewer's lessons.md.

    CRITICAL CONSTRAINT: Holds IDs, counts, and SHAs only!
    Zero free text (no finding text, question text, guidance text, rejection text, test output).
    """
    root = Path(repo_root).resolve()
    state_dir = root / ".lintel" / "state"

    if task_data is None:
        task_file = state_dir / task_id / "task.json"
        if task_file.is_file():
            try:
                task_data = load_state_file(task_file, doc_type="task")
            except Exception:
                task_data = {}
        else:
            task_data = {}

    clean_title = task_data.get("title", task_id)
    base_sha = task_data.get("base_sha", "")
    branch = task_data.get("branch", f"lintel/{task_id}")

    lines: List[str] = [
        f"# Task Summary: {clean_title} ({task_id})",
        "",
        f"- **Task ID:** {task_id}",
        f"- **Title:** {clean_title}",
        f"- **Base SHA:** {base_sha}",
        f"- **Branch:** {branch}",
        "",
    ]

    # 1. Requirements
    lines.append("## Requirements")
    req_file = root / ".lintel" / "tasks" / task_id / "shared" / "requirements.md"
    parsed_reqs: List[str] = []
    if req_file.is_file():
        try:
            req_content = req_file.read_text(encoding="utf-8")
            try:
                rdoc = documents.validate_requirements_doc(req_content)
                for r in sorted(rdoc.requirements.values(), key=lambda x: x.number):
                    if r.behaviour_unchanged:
                        parsed_reqs.append(f"- {r.id}: Behaviour: unchanged")
                    elif r.is_withdrawn:
                        parsed_reqs.append(f"- {r.id}: Status: withdrawn")
                    else:
                        parsed_reqs.append(f"- {r.id}")
            except Exception:
                # Fallback regex parse
                for m in re.finditer(r"^###\s+R(\d+)\s*:\s*(.*)$", req_content, re.MULTILINE):
                    rnum = int(m.group(1))
                    rid = f"R{rnum}"
                    # Check text between this heading and next for Behaviour: unchanged
                    start_idx = m.end()
                    next_h = re.search(r"^###\s+R\d+", req_content[start_idx:], re.MULTILINE)
                    section_chunk = req_content[start_idx:start_idx + next_h.start()] if next_h else req_content[start_idx:]
                    if re.search(r"^Behaviou?r:\s*unchanged\s*$", section_chunk, re.MULTILINE | re.IGNORECASE):
                        parsed_reqs.append(f"- {rid}: Behaviour: unchanged")
                    elif re.search(r"^Status:\s*withdrawn", section_chunk, re.MULTILINE | re.IGNORECASE):
                        parsed_reqs.append(f"- {rid}: Status: withdrawn")
                    else:
                        parsed_reqs.append(f"- {rid}")
        except Exception:
            pass

    if not parsed_reqs:
        known_ids = task_data.get("known_requirement_ids", [])
        if known_ids:
            for rid in known_ids:
                parsed_reqs.append(f"- {rid}")
        else:
            parsed_reqs.append("- None")

    lines.extend(parsed_reqs)
    lines.append("")

    # 2. Phases
    lines.append("## Phases")
    plan_file = root / ".lintel" / "tasks" / task_id / "shared" / "plan.md"
    plan_phases: Dict[int, List[str]] = {}
    if plan_file.is_file():
        try:
            p_content = plan_file.read_text(encoding="utf-8")
            try:
                pdoc = documents.parse_plan_doc(p_content)
                for pnum, ph in pdoc.phases.items():
                    plan_phases[pnum] = list(ph.requirements)
            except Exception:
                for pm in re.finditer(r"^##\s+Phase\s+(\d+)\s*:\s*(.*)$", p_content, re.MULTILINE):
                    pnum = int(pm.group(1))
                    start = pm.end()
                    next_pm = re.search(r"^##\s+Phase\s+\d+", p_content[start:], re.MULTILINE)
                    sub = p_content[start:start + next_pm.start()] if next_pm else p_content[start:]
                    req_m = re.search(r"^Requirements:\s*(.*)$", sub, re.MULTILINE)
                    if req_m:
                        req_list = [r.strip() for r in req_m.group(1).split(",") if r.strip()]
                        plan_phases[pnum] = req_list
        except Exception:
            pass

    loops_file = state_dir / task_id / "loops.json"
    loops_data: Dict[str, Any] = {}
    if loops_file.is_file():
        try:
            loops_data = load_state_file(loops_file, doc_type="loops")
        except Exception:
            pass
    loops = loops_data.get("loops", {})

    phases = task_data.get("phases") or {}
    if not phases:
        lines.append("- None")
    else:
        sorted_p_keys = sorted(phases.keys(), key=lambda k: int(k) if str(k).isdigit() else str(k))
        for p_key in sorted_p_keys:
            p_info = phases[p_key]
            p_num = int(p_key) if str(p_key).isdigit() else p_key
            lines.append(f"\n### Phase {p_key}")

            req_list = plan_phases.get(p_num, [])
            req_str = ", ".join(req_list) if req_list else "none"
            lines.append(f"- **Requirements:** {req_str}")

            design_commits = [c.get("sha") for c in p_info.get("design_commits", []) if isinstance(c, dict) and c.get("sha")]
            design_str = ", ".join(design_commits) if design_commits else "none"
            lines.append(f"- **Design commits:** {design_str}")

            lock_sha = p_info.get("lock", {}).get("commit") if isinstance(p_info.get("lock"), dict) else None
            lines.append(f"- **Lock commit:** {lock_sha or 'none'}")

            amends = p_info.get("amends", [])
            amend_strs = []
            for a in amends:
                if isinstance(a, dict):
                    if a.get("commit"):
                        amend_strs.append(f"{a['commit']} ({a.get('kind', 'amend')})")
                    elif a.get("kind"):
                        amend_strs.append(a["kind"])
            amends_str = ", ".join(amend_strs) if amend_strs else "none"
            lines.append(f"- **Amend commits:** {amends_str}")

            deliv = p_info.get("delivered")
            deliv_sha = deliv.get("commit") if isinstance(deliv, dict) else None
            lines.append(f"- **Delivery commit:** {deliv_sha or 'none'}")

            qa_rnds = loops.get(f"qa.p{p_num}", {}).get("rounds", 0)
            dev_rnds = loops.get(f"dev.p{p_num}", {}).get("rounds", 0)
            lines.append(f"- **Rounds per pair:** qa: {qa_rnds}, dev: {dev_rnds}")

            deliv_groups = deliv.get("groups", {}) if isinstance(deliv, dict) else {}
            if deliv_groups:
                lines.append("- **Delivery gate results:**")
                for gname in sorted(deliv_groups.keys()):
                    gdata = deliv_groups[gname]
                    if isinstance(gdata, dict):
                        lines.append(f"  - {gname}: exit {gdata.get('exit', 0)} ({gdata.get('seconds', 0.0)}s)")
                    else:
                        lines.append(f"  - {gname}: {gdata}")
    lines.append("")

    # 3. Counts
    events_file = get_events_log_path(state_dir, task_id)
    events: List[Dict[str, Any]] = []
    if events_file.is_file():
        try:
            events = read_events(events_file)
        except Exception:
            pass

    stall_count = sum(1 for e in events if e.get("event") == "stall")
    ruling_count = sum(1 for e in events if e.get("event") == "ruling")
    decision_count = sum(1 for e in events if e.get("event") == "decide")
    rejection_count = sum(1 for e in events if e.get("event") == "reject")

    acks_file = state_dir / task_id / "acks.json"
    acks_data: Dict[str, Any] = {}
    if acks_file.is_file():
        try:
            acks_data = load_state_file(acks_file, doc_type="acks")
        except Exception:
            pass

    drift_paths_count = 0
    drift_acks = acks_data.get("drift", {})
    if isinstance(drift_acks, dict):
        for p_val in drift_acks.values():
            if isinstance(p_val, dict) and "paths" in p_val:
                drift_paths_count += len(p_val.get("paths", {}))
    if drift_paths_count == 0:
        for p_val in phases.values():
            if isinstance(p_val, dict) and isinstance(p_val.get("delivered"), dict):
                drift_paths_count += p_val["delivered"].get("drift_count", 0)

    acked_audit_count = len(acks_data.get("audit", {}).get("acked_ids", []))
    if acked_audit_count == 0:
        for p_val in phases.values():
            if isinstance(p_val, dict) and isinstance(p_val.get("delivered"), dict):
                acked_audit_count += p_val["delivered"].get("audit_count", 0)

    lines.extend([
        "## Counts",
        f"- **Stalls:** {stall_count}",
        f"- **Arbiter rulings:** {ruling_count}",
        f"- **Human decisions:** {decision_count}",
        f"- **Human rejections:** {rejection_count}",
        f"- **Acked drift findings:** {drift_paths_count}",
        f"- **Acked audit crossings:** {acked_audit_count}",
        "",
    ])

    # 4. Docs update
    docs_up = task_data.get("docs_update") or {}
    docs_outcome = docs_up.get("outcome", "none")
    specs_changed = "yes" if docs_up.get("po_changed") else "no"
    steering_changed = "yes" if docs_up.get("architect_changed") else "no"

    lines.extend([
        "## Docs update",
        f"- **Outcome:** {docs_outcome}",
        f"- **Specs changed:** {specs_changed}",
        f"- **Steering changed:** {steering_changed}",
        "",
    ])

    # 5. Deferred requirement problems
    lines.append("## Deferred requirement problems")
    deferred = task_data.get("deferred") or []
    if not deferred:
        lines.append("- None")
    else:
        for item in deferred:
            if isinstance(item, dict):
                lines.append(f"- {item.get('id', '')} (stage: {item.get('stage', '')})")
    lines.append("")

    # 6. Lessons path
    lessons_path: Optional[str] = None
    final_rev_dir = root / ".lintel" / "tasks" / task_id / "final-reviewer"
    if final_rev_dir.is_dir():
        for l_file in sorted(final_rev_dir.glob("*/lessons.md")):
            try:
                lessons_path = str(l_file.relative_to(root))
                break
            except ValueError:
                lessons_path = str(l_file)
                break

    if not lessons_path:
        lessons_path = f".lintel/tasks/{task_id}/final-reviewer/final-1/lessons.md"

    lines.extend([
        "## Lessons",
        f"- **Path:** {lessons_path}",
        "",
    ])

    return "\n".join(lines)


def update_progress_md(
    repo_root: Path,
    task_data: Dict[str, Any],
    next_action: str,
) -> None:
    """Render and write progress.md atomically."""
    task_id = task_data["id"]
    content = render_progress_md(repo_root, task_id, task_data=task_data, next_action=next_action)
    dest = repo_root / ".lintel" / "tasks" / task_id / "progress.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest.parent / f".{dest.name}.tmp-{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
    try:
        tmp_dest.write_text(content, encoding="utf-8")
        os.replace(tmp_dest, dest)
    finally:
        if tmp_dest.exists():
            try:
                tmp_dest.unlink()
            except OSError:
                pass


def extract_next_action(progress_content: str) -> str:
    """Extract next action command from rendered progress.md content."""
    match = re.search(r"-\s+\*\*Next action:\*\*\s+`?([^`\n]+)`?", progress_content)
    if match:
        return match.group(1).strip()
    return ""
