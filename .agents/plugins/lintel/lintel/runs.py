"""Persona runs: invoke, enter, done, answer (M1a-17).

Implements:
- invoke <role> [--phase s] [--from-report] [--pair <writer>]
- enter <role> <nonce>
- done --verdict <v> [--cause <c>]
- answer <role> <question-id> "<text>"
"""

import sys
sys.dont_write_bytecode = True

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

try:
    from lintel import amend, flow, loops, reviews
    from lintel.documents import (
        DocumentValidationError,
        validate_contract_doc,
        validate_plan_doc,
        validate_requirements_doc,
        validate_test_outline,
    )
    from lintel.events import append_event, create_event
    from lintel.findings import (
        FindingsValidationError,
        validate_findings,
        validate_responses,
    )
    from lintel.git import run_git
    from lintel.self_check import check_invoke_inputs
    from lintel.state import (
        StateError,
        atomic_write_json,
        load_state_file,
        resolve_state_root,
        state_lock,
        validate_state_doc,
    )
    from lintel.task_start import find_active_task_in_state
except ImportError:
    from . import amend, flow, loops, reviews
    from .documents import (
        DocumentValidationError,
        validate_contract_doc,
        validate_plan_doc,
        validate_requirements_doc,
        validate_test_outline,
    )
    from .events import append_event, create_event
    from .findings import (
        FindingsValidationError,
        validate_findings,
        validate_responses,
    )
    from .git import run_git
    from .self_check import check_invoke_inputs
    from .state import (
        StateError,
        atomic_write_json,
        load_state_file,
        resolve_state_root,
        state_lock,
        validate_state_doc,
    )
    from .task_start import find_active_task_in_state


class RunRefusalError(flow.FlowRefusalError):
    """Raised when a run command (invoke, enter, done, answer) is refused."""

    def __init__(self, message: str, code: int = 1, row: Optional[str] = None):
        super().__init__(message=message, code=code, row=row)


ALL_10_ROLES: Set[str] = {
    "po",
    "po-reviewer",
    "architect",
    "architect-reviewer",
    "qa",
    "qa-reviewer",
    "dev",
    "dev-reviewer",
    "arbiter",
    "final-reviewer",
}

ROLE_ALLOWED_VERDICTS: Dict[str, Set[str]] = {
    "po": {"complete", "needs-input", "no-change"},
    "architect": {"complete", "needs-input", "blocked", "no-change"},
    "qa": {"complete", "blocked"},
    "dev": {"complete", "needs-input", "blocked"},
    "po-reviewer": {"approve", "changes"},
    "architect-reviewer": {"approve", "changes"},
    "qa-reviewer": {"approve", "changes"},
    "dev-reviewer": {"approve", "changes"},
    "arbiter": {"ruled"},
    "final-reviewer": {"approve", "issues"},
}

ROLE_ALLOWED_BLOCKED_CAUSES: Dict[str, Set[str]] = {
    "architect": {"requirements", "environment"},
    "qa": {"requirements", "contract", "environment"},
    "dev": {"requirements", "contract", "test", "environment"},
    "po": set(),
    "po-reviewer": set(),
    "architect-reviewer": set(),
    "qa-reviewer": set(),
    "dev-reviewer": set(),
    "arbiter": set(),
    "final-reviewer": set(),
}

CAUSE_TO_HEADING: Dict[str, str] = {
    "requirements": "Requirements problem",
    "contract": "Contract defect",
    "test": "Suspected test problem",
}


def _now_iso() -> str:
    """Return current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_phase(phase_arg: Optional[str]) -> Optional[str]:
    """Normalize a phase argument: bare digit '1' becomes 'p1' (T-STG-22)."""
    if not phase_arg:
        return None
    phase_str = str(phase_arg).strip()
    if phase_str.isdigit():
        return f"p{phase_str}"
    return phase_str


def _expected_role_for_code(code: str) -> Optional[str]:
    """Determine expected persona role from state position code."""
    return flow.expected_role_for_state({"position": {"code": code, "stage": "r0"}})


def _expected_action_for_state(task_data: Dict[str, Any]) -> str:
    """Render the expected CLI action for the current position code."""
    return flow.format_next_action(task_data)


def _update_progress_md(repo_root: Path, task_data: Dict[str, Any], next_action: str) -> None:
    """Update progress.md in the task directory."""
    try:
        from lintel.progress import update_progress_md
    except ImportError:
        from .progress import update_progress_md
    update_progress_md(repo_root, task_data, next_action)


def _extract_report_section(report_text: str, heading_title: str) -> Optional[str]:
    """Extract a markdown section matching heading_title (e.g. 'Requirements problem')."""
    lines = report_text.splitlines()
    pattern = re.compile(rf"^#+\s+{re.escape(heading_title)}\b", re.IGNORECASE)

    start_idx: Optional[int] = None
    level: int = 2

    for idx, line in enumerate(lines):
        if pattern.search(line.strip()):
            start_idx = idx
            m = re.match(r"^(#+)", line.strip())
            if m:
                level = len(m.group(1))
            break

    if start_idx is None:
        return None

    # Collect lines until next heading of same or higher level
    end_pattern = re.compile(rf"^#{{1,{level}}}\s+")
    section_lines = [lines[start_idx]]
    for line in lines[start_idx + 1:]:
        if end_pattern.match(line.strip()):
            break
        section_lines.append(line)

    return "\n".join(section_lines).strip()


def _is_phase_locked(task_data: Dict[str, Any]) -> bool:
    """Determine whether the current phase is locked per @LOCKED macro."""
    phases = task_data.get("phases", {})
    pos = task_data.get("position", {})
    phase_num = pos.get("phase")
    if phase_num is not None:
        p_data = phases.get(str(phase_num))
        if isinstance(p_data, dict):
            lock_obj = p_data.get("lock")
            deliv_obj = p_data.get("delivered")
            if lock_obj and isinstance(lock_obj, dict) and lock_obj.get("commit"):
                if not deliv_obj or not deliv_obj.get("commit"):
                    return True
    return False


def _get_writer_lane_paths(repo_root: Path, role: str, stage: str, task_id: str) -> List[str]:
    """Return relative file paths in the writer's lane for role and stage."""
    if role == "po":
        if stage == "docs":
            specs_dir = repo_root / ".lintel" / "specs"
            if specs_dir.is_dir():
                found = [
                    p.relative_to(repo_root).as_posix()
                    for p in specs_dir.rglob("*")
                    if p.is_file() and not p.name.startswith(".")
                ]
                return found if found else [".lintel/specs/"]
            return [".lintel/specs/"]
        else:
            return ["shared/requirements.md"]
    elif role == "architect":
        if stage == "docs":
            steering_dir = repo_root / ".lintel" / "steering"
            if steering_dir.is_dir():
                found = [
                    p.relative_to(repo_root).as_posix()
                    for p in steering_dir.rglob("*")
                    if p.is_file() and not p.name.startswith(".") and p.name != "testing.md"
                ]
                return found if found else [".lintel/steering/"]
            return [".lintel/steering/"]
        else:
            paths = ["shared/plan.md"]
            if stage.startswith("p") or stage.startswith("r"):
                paths.extend([
                    f"shared/{stage}/design.md",
                    f"shared/{stage}/contract.md",
                ])
            return paths
    return []


def _check_writer_lane_changed(
    repo_root: Path,
    task_data: Dict[str, Any],
    role: str,
    stage: str,
) -> bool:
    """Check if the writer's lane content differs from the recorded head (T-VER-61)."""
    task_id = task_data.get("id", "")
    lane_paths = _get_writer_lane_paths(repo_root, role, stage, task_id)
    if not lane_paths:
        return False

    head_sha = task_data.get("head")
    if not head_sha:
        return False

    try:
        diff_cmd = ["diff", "--name-only", head_sha, "--"] + lane_paths
        res = run_git(diff_cmd, repo_root, check=False)
        if res.returncode == 0 and res.stdout.strip():
            return True

        status_cmd = ["status", "--porcelain", "--"] + lane_paths
        res_status = run_git(status_cmd, repo_root, check=False)
        if res_status.returncode == 0 and res_status.stdout.strip():
            return True
    except Exception:
        pass

    return False


def _compute_writer_lane_hash(
    repo_root: Path,
    role: str,
    stage: str,
    task_id: str,
) -> Optional[str]:
    """Compute sha256 lane hash over sorted tuples of (path, content) for the writer's lane."""
    lane_paths = _get_writer_lane_paths(repo_root, role, stage, task_id)
    if not lane_paths:
        return None

    hasher = hashlib.sha256()
    for rel_path in sorted(lane_paths):
        full_path = repo_root / rel_path
        if not full_path.is_file() and rel_path.startswith("shared/"):
            alt_path = repo_root / ".lintel" / "tasks" / task_id / rel_path
            if alt_path.is_file():
                full_path = alt_path
        if full_path.is_file():
            content = full_path.read_bytes()
            blob_hash = hashlib.sha256(content).hexdigest()
            hasher.update(f"{rel_path}:{blob_hash}\n".encode("utf-8"))
        else:
            hasher.update(f"{rel_path}:deleted\n".encode("utf-8"))

    return hasher.hexdigest()


# ===========================================================================
# 1. invoke
# ===========================================================================

def invoke(
    repo_root: Union[str, Path],
    role: str,
    phase: Optional[str] = None,
    from_report: bool = False,
    pair: Optional[str] = None,
) -> Dict[str, Any]:
    """Invoke a persona run conforming to T-RUN-01 through T-RUN-10."""
    root = Path(repo_root).resolve()

    # Verify role is valid
    if role not in ALL_10_ROLES:
        raise RunRefusalError(
            f"Refused: unknown role '{role}'; valid roles are: {sorted(ALL_10_ROLES)}"
        )

    # Find active task
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise RunRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)"
        )
    task_id, task_data = active_info
    task_state_file = root / ".lintel" / "state" / task_id / "task.json"

    # Stage inference and phase check (T-STG-20, T-STG-21, T-STG-22)
    current_stage = task_data["position"]["stage"]
    current_phase_num = task_data["position"].get("phase")

    if phase is not None:
        norm_phase = _normalize_phase(phase)
        if norm_phase != current_stage:
            raise RunRefusalError(
                f"Refused: --phase '{phase}' does not match current stage '{current_stage}' (T-STG-21)"
            )
    else:
        norm_phase = current_stage

    # T-RUN-03: one run at a time
    current_code = task_data["position"]["code"]
    if current_code.startswith("P.") or current_code.startswith("A.") or task_data["position"].get("run"):
        raise RunRefusalError(
            "Refused: a run is pending or active (one run at a time); lintel task wait (T-RUN-03)"
        )

    # T-RUN-10: stalled pair
    invoked_writer = None
    if role == "arbiter":
        invoked_writer = pair
    elif role in loops.REVIEWER_ROLES:
        invoked_writer = loops.REVIEWER_TO_WRITER.get(role)
    elif role != "final-reviewer":
        invoked_writer = role

    is_stalled = False
    stall_reason = None
    if invoked_writer:
        loops_data = loops.load_loops(root / ".lintel" / "state", task_id)
        is_stalled, stall_reason = loops.is_pair_stalled(loops_data, invoked_writer, current_stage)

    if (current_code == "D.stall" or is_stalled) and role != "arbiter":
        if not stall_reason:
            stall_reason = (task_data.get("escalation") or {}).get("reason", "no-progress")
        raise RunRefusalError(
            f"Refused: A stalled pair gets no further invoke: {stall_reason} (T-RUN-10); "
            f"human: lintel decide --phase {current_stage}",
            row="T-RUN-10",
        )

    # T-RUN-09: open questions check
    if current_code.startswith("Q."):
        # Check if the asking run has unanswered questions
        runs_dir = root / ".lintel" / "state" / task_id / "runs"
        open_ids: List[str] = []
        if runs_dir.is_dir():
            for rf in sorted(runs_dir.glob("*.json")):
                try:
                    rd = json.loads(rf.read_text(encoding="utf-8"))
                    if rd.get("role") == role and rd.get("questions"):
                        open_ids = [
                            q["id"] for q in rd["questions"] if q.get("answered_at") is None
                        ]
                        break
                except Exception:
                    pass
        if open_ids:
            raise RunRefusalError(
                f"Refused: writer '{role}' has unanswered questions: {', '.join(open_ids)} (T-RUN-09);\n"
                f"lintel answer {role} <question-id> \"<text>\""
            )

    # T-RUN-02: unexpected role check
    expected_role = flow.expected_role_for_state(task_data)
    if role != expected_role:
        expected_act = flow.format_next_action(task_data)
        raise RunRefusalError(
            f"invoke refuses a role the state machine doesn't expect next: expected '{expected_act}' (T-RUN-02)",
            row="T-RUN-02",
        )

    # T-RUN-08: --from-report flag check
    state_is_fr = bool(task_data["position"].get("fr") or "+FR" in current_code)
    if from_report and not state_is_fr:
        expected_act = _expected_action_for_state(task_data)
        raise RunRefusalError(
            f"Refused: --from-report given where state is not +FR (T-RUN-08);\n"
            f"expected spelling: {expected_act}"
        )
    if state_is_fr and not from_report:
        expected_act = _expected_action_for_state(task_data)
        raise RunRefusalError(
            f"Refused: --from-report missing where state is +FR (T-RUN-08);\n"
            f"expected spelling: {expected_act}"
        )

    # T-RUN-05..07: input checks
    docs_update = current_stage == "docs"
    diffs = check_invoke_inputs(root, task_data, docs_update=docs_update)

    if diffs.get("personas") or diffs.get("agents") or diffs.get("launcher"):
        all_diffs = (
            diffs.get("personas", []) + diffs.get("agents", []) + diffs.get("launcher", [])
        )
        diff_str = "\n".join(f"  - {d}" for d in all_diffs)
        hint = ""
        if diffs.get("agents"):
            hint = " For an agent-file frontmatter problem, 'lintel doctor' is suggested."
        raise RunRefusalError(
            f"Refused: persona, agent, or launcher files differ from recorded head (T-RUN-05):\n"
            f"{diff_str}\n"
            f"Commit them with 'amend --config' or revert them.{hint}"
        )

    if diffs.get("living_docs"):
        diff_str = "\n".join(f"  - {d}" for d in diffs["living_docs"])
        raise RunRefusalError(
            f"Refused: living docs differ from recorded head outside docs update (T-RUN-06):\n"
            f"{diff_str}\n"
            f"Revert them before invoke."
        )

    if diffs.get("request") or diffs.get("answers") or diffs.get("hashes"):
        h_diffs = diffs.get("request", []) + diffs.get("answers", []) + diffs.get("hashes", [])
        diff_str = "\n".join(f"  - {d}" for d in h_diffs)
        raise RunRefusalError(
            f"Refused: input file differs from CLI's last write (T-RUN-07):\n"
            f"{diff_str}"
        )

    # T-RUN-04: brief dependencies check
    # Check persona file
    persona_file = root / ".lintel" / "personas" / f"{role}.md"
    plugin_persona = Path(__file__).resolve().parent.parent / "personas" / f"{role}.md"
    if not persona_file.is_file() and not plugin_persona.is_file():
        raise RunRefusalError(
            f"Refused: persona file for role '{role}' is missing (T-RUN-04); lintel doctor"
        )

    copied_report_content: Optional[str] = None
    from_report_record: Optional[Dict[str, Any]] = None

    if from_report:
        route = task_data.get("route")
        if not route or not route.get("from_run"):
            raise RunRefusalError(
                "Refused: task route is missing for --from-report (T-RUN-04); lintel doctor"
            )
        from_run = route["from_run"]
        cause = route.get("cause", "")
        # Find from_run folder
        from_run_state_file = root / ".lintel" / "state" / task_id / "runs" / f"{from_run}.json"
        if not from_run_state_file.is_file():
            raise RunRefusalError(
                f"Refused: run record for '{from_run}' is missing (T-RUN-04); lintel doctor"
            )
        from_run_data = load_state_file(from_run_state_file, doc_type="run")
        from_report_file = root / from_run_data["folder"] / "report.md"
        if not from_report_file.is_file():
            raise RunRefusalError(
                f"Refused: report.md missing from run '{from_run}' (T-RUN-04); lintel doctor"
            )
        report_text = from_report_file.read_text(encoding="utf-8")

        if cause == "environment":
            if not report_text.strip():
                raise RunRefusalError(
                    f"Refused: report.md in '{from_run}' is empty (T-RUN-04); lintel doctor"
                )
            copied_report_content = report_text.strip()
            heading_name = "Environment problem"
        else:
            heading_name = CAUSE_TO_HEADING.get(cause, "Report section")
            copied = _extract_report_section(report_text, heading_name)
            if not copied:
                raise RunRefusalError(
                    f"Refused: section '{heading_name}' missing from '{from_run}/report.md' (T-RUN-04); lintel doctor"
                )
            copied_report_content = copied

        from_report_record = {"run": from_run, "heading": heading_name}

    # Determine writer and arbiter pair
    if role == "arbiter":
        if not pair:
            raise RunRefusalError("Refused: --pair <writer> required when invoking arbiter (T-STL-11)", row="T-STL-11")
        esc_writer = (task_data.get("escalation") or {}).get("writer")
        if pair != esc_writer:
            raise RunRefusalError(f"Refused: --pair '{pair}' does not match the stalled pair '{esc_writer}' (T-STL-11)", row="T-STL-11")
        writer = pair
        counter_key = f"arbiter-{writer}"
    elif role == "final-reviewer":
        writer = None
        counter_key = role
    elif role in loops.REVIEWER_ROLES:
        writer = loops.REVIEWER_TO_WRITER[role]
        counter_key = role
    else:
        writer = role
        counter_key = role

    # Allocate sequence number
    with state_lock(root / ".lintel" / "state"):
        task_data = load_state_file(task_state_file, doc_type="task")
        counters = task_data.setdefault("counters", {})
        run_counters = counters.setdefault("run", {})
        role_counters = run_counters.setdefault(counter_key, {})
        current_seq = role_counters.get(current_stage, 0)
        seq = current_seq + 1
        role_counters[current_stage] = seq

        # Determine run ID and relative folder path
        if role == "arbiter":
            run_id = f"arbiter-{writer}-{current_stage}-{seq}"
            rel_folder = f".lintel/tasks/{task_id}/arbiter/{writer}-{current_stage}-{seq}"
        else:
            run_id = f"{role}-{current_stage}-{seq}"
            rel_folder = f".lintel/tasks/{task_id}/{role}/{current_stage}-{seq}"

        run_dir = root / rel_folder
        run_dir.mkdir(parents=True, exist_ok=True)

        # Generate one-time nonce and timestamp
        nonce = secrets.token_hex(16)
        created_at = _now_iso()
        prompt_text = f"[lintel:{role}:{nonce}] Run: ./.lintel/bin/lintel enter {role} {nonce}"

        # Write brief.md
        try:
            from lintel.config import load_config
        except ImportError:
            from .config import load_config
            
        try:
            from lintel.personas import render_brief
        except ImportError:
            from .personas import render_brief

        plugin_dir = Path(__file__).resolve().parent.parent
        config_file = root / ".lintel" / "config.jsonc"
        if config_file.is_file():
            config_data = load_config(config_file, workspace_root=root, plugin_dir=plugin_dir)
        else:
            config_data = load_config({}, workspace_root=root, plugin_dir=plugin_dir)

        is_final_reject_revisit = (task_data.get("revisit") or {}).get("cause") == "final-reject"
        
        open_findings = None
        if writer:
            try:
                loops_data = loops.load_loops(root / ".lintel" / "state", task_id)
                lp = loops.get_or_create_loop(loops_data, writer, current_stage)
                open_findings = [f for f in lp.get("findings", []) if f.get("status") == "open"]
            except Exception:
                pass
                
        amends_list = None
        if current_phase_num is not None:
            amends_list = task_data.get("phases", {}).get(str(current_phase_num), {}).get("amends", [])
            
        brief_text, assigned_skills, is_test_side = render_brief(
            role=role,
            task_id=task_id,
            stage=current_stage,
            run_id=run_id,
            repo_root=root,
            plugin_dir=plugin_dir,
            config=config_data,
            copied_report_content=copied_report_content,
            amends_list=amends_list,
            current_phase_num=current_phase_num,
            task_data=task_data,
            open_findings=open_findings,
            is_final_reject_revisit=is_final_reject_revisit,
            from_report_record=from_report_record,
        )
        
        brief_file = run_dir / "brief.md"
        brief_file.write_text(brief_text, encoding="utf-8")
        
        if amends_list and role == "dev":
            last_amend = amends_list[-1]
            if not last_amend.get("briefed"):
                last_amend["briefed"] = True

        # Determine reroute flag (§9.4: fr and stage is not a revisit's)
        is_revisit = current_stage.startswith("r") and current_stage != "r0"
        is_reroute = bool(from_report and not is_revisit)

        # Determine round number for run
        run_round = None
        if writer:
            loops_data = loops.load_loops(root / ".lintel" / "state", task_id)
            lp = loops.get_or_create_loop(loops_data, writer, current_stage)
            if not lp.get("after_human_reject", False):
                run_round = lp.get("rounds", 0) + 1
            else:
                run_round = lp.get("rounds", 0)
        if run_round is None and role != "final-reviewer":
            run_round = 1

        # Create pending run document
        run_data: Dict[str, Any] = {
            "version": 1,
            "id": run_id,
            "role": role,
            "writer": writer,
            "stage": current_stage,
            "seq": seq,
            "phase": current_phase_num,
            "folder": rel_folder,
            "state": "pending",
            "from_state": {
                "code": task_data["position"]["code"],
                "fr": task_data["position"].get("fr", False),
            },
            "fr": from_report,
            "reroute": is_reroute,
            "round": run_round,
            "counted": False,
            "nonce": nonce,
            "nonce_at": created_at,
            "created_at": created_at,
            "entered_at": None,
            "done_at": None,
            "abandoned_at": None,
            "conversation_id": None,
            "verdict": None,
            "cause": None,
            "prompt": prompt_text,
            "hints": {
                "conversation_id": os.environ.get("ANTIGRAVITY_CONVERSATION_ID"),
                "app_data_dir": os.environ.get("ANTIGRAVITY_APP_DATA_DIR"),
            },
            "stop": None,
            "skills": assigned_skills,
            "steering": [],
            "test_side": is_test_side,
            "lane_hash": None,
            "from_report": from_report_record,
            "guidance": [],
            "questions": [],
            "gate_cleared_at": None,
        }

        # Save run file
        runs_state_dir = root / ".lintel" / "state" / task_id / "runs"
        runs_state_dir.mkdir(parents=True, exist_ok=True)
        run_file = runs_state_dir / f"{run_id}.json"
        validate_state_doc("run", run_data, file_path=run_file)
        atomic_write_json(run_file, run_data)

        # Handle amend cycle start if applicable (T-AMD-70, T-AMD-71)
        amend.handle_cycle_start(
            repo_root=root,
            task_data=task_data,
            from_report=from_report,
            current_stage=current_stage,
            current_phase_num=current_phase_num,
            run_id=run_id,
        )

        # Update task.json position via flow engine
        flow.on_invoke(task_data, role=role, from_report=from_report, pair=pair, run_id=run_id)
        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq

        validate_state_doc("task", task_data, file_path=task_state_file)
        atomic_write_json(task_state_file, task_data)

        # Append invoke event
        event_rec = create_event(
            step=event_seq,
            event="invoke",
            stage=current_stage,
            role=role,
            run=run_id,
            folder=rel_folder,
            row="T-RUN-01",
        )
        append_event(
            state_dir=root / ".lintel" / "state",
            task_id=task_id,
            event_data=event_rec,
            task_dir=root / ".lintel" / "tasks" / task_id,
        )

        _update_progress_md(root, task_data, "lintel task wait")

    # Golden format printed tool call (T-RUN-01)
    tool_call_str = (
        f'Subagents: [{{TypeName: "lintel-{role}", Role: "lintel-{role}", '
        f'Model: "inherit", Workspace: "inherit", Prompt: "{prompt_text}"}}]'
    )
    sys.stdout.write(tool_call_str + "\n")
    sys.stdout.write("The orchestrator ends its turn, then lintel task wait\n")

    return run_data


# ===========================================================================
# 2. enter
# ===========================================================================

def enter(
    repo_root: Union[str, Path],
    role: str,
    nonce: str,
    conversation_id: Optional[str] = None,
) -> str:
    """Persona entry point activating run conforming to T-RUN-11, T-RUN-12, T-RUN-13."""
    root = Path(repo_root).resolve()

    active_info = find_active_task_in_state(root)
    if not active_info:
        raise RunRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)"
        )
    task_id, task_data = active_info
    task_state_file = root / ".lintel" / "state" / task_id / "task.json"

    caller_conv = conversation_id or os.environ.get("ANTIGRAVITY_CONVERSATION_ID") or "default"

    with state_lock(root / ".lintel" / "state"):
        task_data = load_state_file(task_state_file, doc_type="task")
        runs_dir = root / ".lintel" / "state" / task_id / "runs"
        target_run_file: Optional[Path] = None
        target_run_data: Optional[Dict[str, Any]] = None

        if runs_dir.is_dir():
            for rf in sorted(runs_dir.glob("*.json")):
                try:
                    rd = json.loads(rf.read_text(encoding="utf-8"))
                    if rd.get("nonce") == nonce:
                        target_run_file = rf
                        target_run_data = rd
                        break
                except Exception:
                    pass

        # T-RUN-13: unknown nonce
        if not target_run_file or not target_run_data:
            raise RunRefusalError(
                f"Refused: nonce '{nonce}' is unknown or invalid (T-RUN-13)"
            )

        # Check role match
        if target_run_data.get("role") != role:
            raise RunRefusalError(
                f"Refused: nonce belongs to role '{target_run_data.get('role')}', not '{role}' (T-RUN-13)"
            )

        run_state = target_run_data.get("state")
        run_folder = target_run_data.get("folder", "")
        brief_rel_path = f"{run_folder}/brief.md"

        # T-RUN-13: run already done or abandoned
        if run_state in ("done", "abandoned"):
            raise RunRefusalError(
                f"Refused: run '{target_run_data['id']}' is already {run_state} (T-RUN-13)"
            )

        # T-RUN-12: repeat enter from same conversation
        if run_state == "active":
            existing_conv = target_run_data.get("conversation_id")
            if existing_conv == caller_conv:
                sys.stdout.write(f"{brief_rel_path}\n")
                return brief_rel_path
            # T-RUN-13: nonce used from another conversation
            raise RunRefusalError(
                f"Refused: nonce used from another conversation '{caller_conv}' (T-RUN-13)"
            )

        # T-RUN-11: pending run transitions to active
        if run_state == "pending":
            target_run_data["state"] = "active"
            target_run_data["entered_at"] = _now_iso()
            target_run_data["conversation_id"] = caller_conv

            validate_state_doc("run", target_run_data, file_path=target_run_file)
            atomic_write_json(target_run_file, target_run_data)

            # Update task position via flow engine
            flow.on_enter(task_data, role=role, run_id=target_run_data["id"])
            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq

            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

            # Append enter event
            event_rec = create_event(
                step=event_seq,
                event="enter",
                stage=target_run_data["stage"],
                role=role,
                run=target_run_data["id"],
                folder=run_folder,
                row="T-RUN-11",
            )
            append_event(
                state_dir=root / ".lintel" / "state",
                task_id=task_id,
                event_data=event_rec,
                task_dir=root / ".lintel" / "tasks" / task_id,
            )

            _update_progress_md(root, task_data, "persona running; lintel task wait")

            sys.stdout.write(f"{brief_rel_path}\n")
            return brief_rel_path

        raise RunRefusalError(
            f"Refused: run '{target_run_data['id']}' in unexpected state '{run_state}' (T-RUN-13)"
        )


# ===========================================================================
# 3. done
# ===========================================================================

def done(
    repo_root: Union[str, Path],
    verdict: Optional[str] = None,
    cause: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Optional[str]:
    """Record persona completion verdict conforming to T-RUN-14..21, T-VER-01..77, T-FIN-53."""
    root = Path(repo_root).resolve()

    if not verdict:
        raise RunRefusalError("Refused: --verdict is required (T-RUN-16)")

    active_info = find_active_task_in_state(root)
    if not active_info:
        raise RunRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)"
        )
    task_id, task_data = active_info
    task_state_file = root / ".lintel" / "state" / task_id / "task.json"

    with state_lock(root / ".lintel" / "state"):
        task_data = load_state_file(task_state_file, doc_type="task")
        pos = task_data.get("position", {})
        caller_conv = conversation_id or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
        current_run_id = pos.get("run")

        target_run_file: Optional[Path] = None
        target_run_data: Optional[Dict[str, Any]] = None

        if current_run_id:
            rf = root / ".lintel" / "state" / task_id / "runs" / f"{current_run_id}.json"
            if rf.is_file():
                target_run_file = rf
                target_run_data = load_state_file(rf, doc_type="run")

        if not target_run_data:
            runs_dir = root / ".lintel" / "state" / task_id / "runs"
            if runs_dir.is_dir():
                all_run_files = sorted(runs_dir.glob("*.json"))
                if caller_conv:
                    for rf in reversed(all_run_files):
                        try:
                            rd = json.loads(rf.read_text(encoding="utf-8"))
                            if rd.get("conversation_id") == caller_conv:
                                target_run_file = rf
                                target_run_data = rd
                                break
                        except Exception:
                            pass
                if not target_run_data and all_run_files:
                    try:
                        target_run_file = all_run_files[-1]
                        target_run_data = json.loads(target_run_file.read_text(encoding="utf-8"))
                    except Exception:
                        pass

        if not target_run_data or not target_run_file:
            expected_act = _expected_action_for_state(task_data)
            raise RunRefusalError(
                f"Refused: no run is currently active (T-RUN-18);\n"
                f"expected action: {expected_act}"
            )

        run_conv = target_run_data.get("conversation_id")
        if caller_conv and run_conv and caller_conv != run_conv:
            raise RunRefusalError(
                f"Refused: done called from another conversation '{caller_conv}' (expected '{run_conv}') (T-RUN-18)"
            )

        # T-RUN-14: same verdict repeat is a no-op that says so (wins over T-RUN-18)
        if target_run_data.get("verdict") == verdict:
            msg = f"done: run '{target_run_data['id']}' already recorded verdict '{verdict}' (no-op)"
            sys.stdout.write(msg + "\n")
            return msg

        # T-RUN-15: different verdict for already completed run
        if target_run_data.get("state") == "done" and target_run_data.get("verdict") != verdict:
            raise RunRefusalError(
                f"Refused: run '{target_run_data['id']}' already completed with verdict "
                f"'{target_run_data.get('verdict')}', cannot change to '{verdict}' (T-RUN-15)"
            )

        # T-RUN-18: run must be active
        if target_run_data.get("state") != "active":
            expected_act = _expected_action_for_state(task_data)
            raise RunRefusalError(
                f"Refused: run '{target_run_data['id']}' is in state '{target_run_data.get('state')}', expected 'active' (T-RUN-18);\n"
                f"expected action: {expected_act}"
            )

        run_data = target_run_data
        run_file = target_run_file
        role = run_data["role"]
        stage = run_data["stage"]
        phase = run_data.get("phase")
        run_folder = root / run_data["folder"]

        # T-RUN-16 / T-VER-31 / T-VER-51 / T-VER-60: role verdict set checks
        if role == "qa" and verdict == "needs-input":
            raise RunRefusalError(
                "Refused: QA has no needs-input (T-VER-31): a behaviour question is "
                "'blocked --cause requirements'; a test-environment question is 'blocked --cause environment'"
            )

        if role == "po" and verdict == "blocked":
            raise RunRefusalError(
                "Refused: role 'po' does not support verdict 'blocked' "
                "(PO asks through 'needs-input') (T-RUN-16, T-VER-51)"
            )

        if role in ("qa", "dev") and verdict == "no-change":
            raise RunRefusalError(
                f"Refused: role '{role}' does not support verdict 'no-change' (T-RUN-16, T-VER-60)"
            )

        if role.endswith("-reviewer") and verdict == "blocked":
            raise RunRefusalError(
                f"Refused: reviewer role '{role}' does not support verdict 'blocked' (T-RUN-16)"
            )

        allowed_verdicts = ROLE_ALLOWED_VERDICTS.get(role, set())
        if verdict not in allowed_verdicts:
            raise RunRefusalError(
                f"Refused: verdict '{verdict}' not allowed for role '{role}' (T-RUN-16); "
                f"allowed verdicts: {sorted(allowed_verdicts)}"
            )

        # T-RUN-17 / T-VER-51: blocked cause check
        if verdict == "blocked":
            allowed_causes = ROLE_ALLOWED_BLOCKED_CAUSES.get(role, set())
            if not cause:
                allowed_c = sorted(allowed_causes)
                raise RunRefusalError(
                    f"Refused: --cause required for blocked verdict (T-RUN-17); "
                    f"allowed causes for '{role}': {allowed_c}"
                )
            if cause not in allowed_causes:
                raise RunRefusalError(
                    f"Refused: cause '{cause}' not allowed for role '{role}' (T-VER-51); "
                    f"allowed causes: {sorted(allowed_causes)}"
                )

            # T-RUN-21: report.md section check
            report_file = run_folder / "report.md"
            expected_heading = CAUSE_TO_HEADING.get(cause, "report content") if cause != "environment" else "report content"
            if not report_file.is_file():
                raise RunRefusalError(
                    f"Refused: report.md is missing from run folder '{run_data['folder']}' "
                    f"(expected section '{expected_heading}') (T-RUN-21)"
                )
            report_text = report_file.read_text(encoding="utf-8")

            if cause == "environment":
                if not report_text.strip():
                    raise RunRefusalError(
                        "Refused: report.md is empty for cause 'environment' (T-RUN-21)"
                    )
            else:
                expected_heading = CAUSE_TO_HEADING.get(cause, "")
                if not _extract_report_section(report_text, expected_heading):
                    raise RunRefusalError(
                        f"Refused: report.md missing section '{expected_heading}' for cause '{cause}' (T-RUN-21)"
                    )

        # T-FIN-53: final reviewer files check
        if role == "final-reviewer":
            missing = [
                f for f in ("review.md", "lessons.md", "final-findings.md")
                if not (run_folder / f).is_file()
            ]
            if missing:
                raise RunRefusalError(
                    f"Refused: final reviewer done requires {', '.join(missing)} in run folder (T-FIN-53)"
                )

        # T-VER-30, T-VER-32: needs-input questions.md check
        parsed_question_ids: List[str] = []
        if verdict == "needs-input":
            questions_file = run_folder / "questions.md"
            if not questions_file.is_file():
                raise RunRefusalError(
                    "Refused: questions.md missing from run folder for needs-input verdict (T-VER-32)"
                )
            q_text = questions_file.read_text(encoding="utf-8")
            # Parse question IDs from headings like '### Q1: ...' or '### Q1'
            matches = re.findall(r"^#+\s*(Q\d+)\b", q_text, re.MULTILINE | re.IGNORECASE)
            if not matches:
                matches = re.findall(r"^(?:-\s*)?(Q\d+)\s*:", q_text, re.MULTILINE | re.IGNORECASE)
            if not matches:
                raise RunRefusalError(
                    "Refused: questions.md must contain at least one question heading (e.g. '### Q1: ...') (T-VER-30)"
                )
            seen_q: Set[str] = set()
            for m in matches:
                qid = m.upper()
                if qid not in seen_q:
                    seen_q.add(qid)
                    parsed_question_ids.append(qid)

        # T-VER-60..62: no-change checks
        if verdict == "no-change":
            if role not in ("po", "architect"):
                raise RunRefusalError(
                    f"Refused: verdict 'no-change' only allowed for po and architect (T-VER-60)"
                )
            if stage == "r0":
                raise RunRefusalError(
                    "Refused: no-change is not allowed in initial r0 (T-VER-62)"
                )
            if _check_writer_lane_changed(root, task_data, role, stage):
                raise RunRefusalError(
                    "Refused: writer's lane content changed; use 'complete' instead (T-VER-61)"
                )

        # Document checks on complete (M1a-14)
        if verdict == "complete":
            if role == "po":
                # Validate requirements.md
                req_file = root / ".lintel" / "tasks" / task_id / "shared" / "requirements.md"
                if not req_file.is_file():
                    req_file = root / "shared" / "requirements.md"
                if not req_file.is_file():
                    raise RunRefusalError(
                        "Refused: shared/requirements.md missing for po complete (T-VER-01)"
                    )
                try:
                    known_ids = set(task_data.get("known_requirement_ids", []))
                    validate_requirements_doc(
                        req_file.read_text(encoding="utf-8"),
                        known_ids=known_ids if known_ids else None,
                    )
                except DocumentValidationError as exc:
                    raise RunRefusalError(
                        f"Refused: requirements.md validation failed (T-VER-02/03): {exc}"
                    )

            elif role == "architect":
                plan_file = root / ".lintel" / "tasks" / task_id / "shared" / "plan.md"
                if not plan_file.is_file():
                    plan_file = root / "shared" / "plan.md"
                if plan_file.is_file():
                    try:
                        req_file = root / ".lintel" / "tasks" / task_id / "shared" / "requirements.md"
                        if not req_file.is_file():
                            req_file = root / "shared" / "requirements.md"
                        req_doc = (
                            validate_requirements_doc(req_file.read_text(encoding="utf-8"))
                            if req_file.is_file()
                            else None
                        )
                        validate_plan_doc(
                            plan_file.read_text(encoding="utf-8"),
                            requirements_doc=req_doc,
                            state_data=task_data,
                        )
                    except DocumentValidationError as exc:
                        raise RunRefusalError(
                            f"Refused: plan.md validation failed (T-VER-06): {exc}"
                        )

            elif role == "dev":
                # T-VER-08..09: requires recorded green check
                checks_file = root / ".lintel" / "state" / task_id / "checks.json"
                has_green_check = False
                if checks_file.is_file():
                    try:
                        checks_data = json.loads(checks_file.read_text(encoding="utf-8"))
                        for c in checks_data.get("checks", []):
                            if (
                                c.get("result") == "green"
                                or c.get("status") == "green"
                                or c.get("exit") == 0
                            ):
                                has_green_check = True
                                break
                    except Exception:
                        pass
                if not has_green_check:
                    raise RunRefusalError(
                        f"Refused: no recorded green check for DEV's current change set (T-VER-09);\n"
                        f"run 'lintel check --phase {stage}'"
                    )

            # T-VER-10: responses.json validation if present
            resp_file = run_folder / "responses.json"
            if resp_file.is_file():
                loops_data = loops.load_loops(root / ".lintel" / "state", task_id)
                lp = loops.get_or_create_loop(loops_data, role, stage)
                open_findings = lp.get("findings", [])
                arbiter_rulings = {f["id"]: f.get("arbiter_ruled") for f in open_findings if f.get("arbiter_ruled")}
                try:
                    validate_responses(
                        resp_file.read_text(encoding="utf-8"),
                        open_findings=open_findings,
                        arbiter_rulings=arbiter_rulings,
                    )
                except FindingsValidationError as exc:
                    if "ruled 'fix' by the arbiter" in str(exc):
                        raise RunRefusalError(
                            f"Refused: {exc} (T-VER-52)", row="T-VER-52"
                        )
                    raise RunRefusalError(
                        f"Refused: responses.json validation failed (T-VER-10): {exc}"
                    )
                try:
                    resp_dict = json.loads(resp_file.read_text(encoding="utf-8"))
                    loops.record_writer_responses(
                        state_dir=root / ".lintel" / "state",
                        task_id=task_id,
                        writer=role,
                        stage=stage,
                        responses=resp_dict,
                    )
                except Exception:
                    pass

        # Arbiter ruling checks (T-STL-07, T-STL-09)
        if role == "arbiter":
            if verdict != "ruled":
                raise RunRefusalError("Refused: arbiter must exit with verdict 'ruled' (T-STL-09)", row="T-STL-09")
            ruling_file = run_folder / "ruling.json"
            if not ruling_file.is_file():
                raise RunRefusalError("Refused: ruling.json not found (T-STL-09)", row="T-STL-09")
            
            loops_data = loops.load_loops(root / ".lintel" / "state", task_id)
            pair = (task_data.get("escalation") or {}).get("writer") or "dev"
            lp = loops.get_or_create_loop(loops_data, pair, stage)
            open_findings = lp.get("findings", [])
            
            try:
                from lintel.findings import validate_ruling
                ruling_dict = validate_ruling(
                    ruling_file.read_text(encoding="utf-8"),
                    open_findings=open_findings,
                )
                
                # Update loops with ruling and restart counters
                loops.record_arbiter_ruling(
                    state_dir=root / ".lintel" / "state",
                    task_id=task_id,
                    writer=pair,
                    stage=stage,
                    ruling=ruling_dict,
                )
            except Exception as exc:
                if "T-STL-09" not in str(exc):
                    raise RunRefusalError(f"Refused: ruling.json validation failed: {exc} (T-STL-09)", row="T-STL-09")
                raise RunRefusalError(str(exc), row="T-STL-09")

        # Reviewer checks on approve / changes (M1a-14, M1a-19, M1a-22, T-VER-22)
        f_list: List[Dict[str, Any]] = []
        if role in loops.REVIEWER_ROLES and verdict in ("approve", "changes"):
            findings_file = run_folder / "findings.json"
            if findings_file.is_file():
                loops_data = loops.load_loops(root / ".lintel" / "state", task_id)
                writer_role = loops.REVIEWER_TO_WRITER.get(role, role)
                lp = loops.get_or_create_loop(loops_data, writer_role, stage)
                prev_findings = lp.get("findings", [])
                try:
                    f_list = validate_findings(
                        findings_file.read_text(encoding="utf-8"),
                        previous_findings=prev_findings,
                    )
                except FindingsValidationError as exc:
                    err_str = str(exc)
                    if "cannot be marked withdrawn by a reviewer" in err_str or "Human finding" in err_str:
                        raise RunRefusalError(
                            f"Refused: reviewer cannot withdraw human finding (T-VER-22): {exc}",
                            row="T-VER-22",
                        )
                    raise RunRefusalError(
                        f"Refused: findings.json validation failed (T-VER-24): {exc}",
                        row="T-VER-24",
                    )
                open_bm = [
                    f for f in f_list
                    if f["severity"] in ("blocker", "major") and f["status"] == "open"
                ]
                if verdict == "approve" and open_bm:
                    open_ids = [f["id"] for f in open_bm]
                    raise RunRefusalError(
                        f"Refused: approve not allowed while blocker/major findings are open (T-VER-19): "
                        f"{', '.join(open_ids)}"
                    )
                if verdict == "changes" and not open_bm:
                    raise RunRefusalError(
                        "Refused: changes not allowed unless at least one blocker or major finding is open; "
                        "minor findings are advice and never cause another round (T-VER-21)"
                    )

        # Record valid verdict on run
        done_time = _now_iso()
        run_data["state"] = "done"
        run_data["verdict"] = verdict
        if cause:
            run_data["cause"] = cause
        run_data["done_at"] = done_time
        try:
            run_data["lane_hash"] = _compute_writer_lane_hash(root, role, stage, task_id)
        except Exception:
            pass

        if verdict == "needs-input":
            run_data["questions"] = [
                {"id": qid, "answered_at": None} for qid in parsed_question_ids
            ]

        # Review loop processing and stall detection (M1a-19)
        loops_res = None
        if role in loops.REVIEWER_ROLES and verdict in ("approve", "changes"):
            loops_res = loops.on_reviewer_done(
                state_dir=root / ".lintel" / "state",
                task_id=task_id,
                role=role,
                stage=stage,
                run_id=run_data["id"],
                verdict=verdict,
                findings=f_list,
                repo_root=root,
                run_data=run_data,
            )
        else:
            run_data["counted"] = False

        validate_state_doc("run", run_data, file_path=run_file)
        atomic_write_json(run_file, run_data)

        # Record review in reviews.json on reviewer approve (M1a-20)
        if (role in loops.REVIEWER_ROLES and verdict == "approve") or (
            role == "final-reviewer" and verdict in ("approve", "issues")
        ):
            pair = loops.REVIEWER_TO_WRITER.get(
                role, "final-reviewer" if role == "final-reviewer" else role.replace("-reviewer", "")
            )
            pos_dict = task_data.get("position", {})
            phase_num = pos_dict.get("phase")
            if phase_num is None and stage.startswith("p") and stage[1:].isdigit():
                phase_num = int(stage[1:])
            try:
                reviews.record_review(
                    repo_root=root,
                    task_id=task_id,
                    pair=pair,
                    stage=stage,
                    phase=phase_num,
                    run_id=run_data["id"],
                    verdict=verdict,
                    kind="reviewer",
                    task_data=task_data,
                )
            except Exception as exc:
                sys.stderr.write(f"Warning: failed to record review for {pair}: {exc}\n")

        # Determine next state and next action via flow engine
        is_stalled = bool(loops_res and loops_res["stalled"])
        stall_reason = loops_res["stall_reason"] if loops_res else None

        task_data, next_action, row_id = flow.on_done(
            task_data=task_data,
            role=role,
            verdict=verdict,
            cause=cause,
            run_id=run_data["id"],
            run_fr=bool(run_data.get("fr")),
            lane_changed=False,
            repo_root=root,
            stall=is_stalled,
            stall_reason=stall_reason,
        )

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq

        validate_state_doc("task", task_data, file_path=task_state_file)
        atomic_write_json(task_state_file, task_data)

        # Append done event
        event_rec = create_event(
            step=event_seq,
            event="done",
            stage=stage,
            role=role,
            run=run_data["id"],
            round=run_data.get("round"),
            verdict=verdict,
            cause=cause,
            folder=run_data["folder"],
            row=row_id,
            findings=loops.calculate_finding_counts(f_list) if role in loops.REVIEWER_ROLES else None,
        )
        append_event(
            state_dir=root / ".lintel" / "state",
            task_id=task_id,
            event_data=event_rec,
            task_dir=root / ".lintel" / "tasks" / task_id,
        )

        # If pair is stalled, emit stall event (T-STL-02, T-STL-03)
        if is_stalled and loops_res:
            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

            stall_event = create_event(
                step=event_seq,
                event="stall",
                stage=stage,
                role=role,
                run=run_data["id"],
                round=run_data.get("round"),
                stall_reason=stall_reason,
                row=loops_res["row_id"],
            )
            append_event(
                state_dir=root / ".lintel" / "state",
                task_id=task_id,
                event_data=stall_event,
                task_dir=root / ".lintel" / "tasks" / task_id,
            )

        _update_progress_md(root, task_data, next_action)

    sys.stdout.write(f"{next_action}\n")
    return next_action


# ===========================================================================
# 4. answer
# ===========================================================================

def answer(
    repo_root: Union[str, Path],
    role: str,
    question_id: str,
    text: str,
) -> str:
    """Record answer to a needs-input question conforming to T-RUN-19 and T-RUN-20."""
    root = Path(repo_root).resolve()

    active_info = find_active_task_in_state(root)
    if not active_info:
        raise RunRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)"
        )
    task_id, task_data = active_info
    task_state_file = root / ".lintel" / "state" / task_id / "task.json"

    with state_lock(root / ".lintel" / "state"):
        task_data = load_state_file(task_state_file, doc_type="task")
        pos = task_data.get("position", {})
        current_code = pos.get("code", "")

        # T-RUN-20: check if role is asking writer in state Q.<role> or Q.<role>+FR
        clean_code = current_code.replace("+FR", "")
        if not (clean_code.startswith("Q.") and clean_code[2:] == role):
            raise RunRefusalError(
                f"Refused: no question is open for role '{role}' "
                f"(current position code is '{current_code}') (T-RUN-20)"
            )

        # Find the asking run
        current_run_id = pos.get("run")
        runs_dir = root / ".lintel" / "state" / task_id / "runs"
        asking_run_file: Optional[Path] = None
        asking_run_data: Optional[Dict[str, Any]] = None

        if current_run_id:
            rf = runs_dir / f"{current_run_id}.json"
            if rf.is_file():
                asking_run_file = rf
                asking_run_data = load_state_file(rf, doc_type="run")

        if not asking_run_data and runs_dir.is_dir():
            for rf in sorted(runs_dir.glob("*.json"), reverse=True):
                try:
                    rd = json.loads(rf.read_text(encoding="utf-8"))
                    if rd.get("role") == role and rd.get("questions"):
                        asking_run_file = rf
                        asking_run_data = rd
                        break
                except Exception:
                    pass

        if not asking_run_data or not asking_run_file:
            raise RunRefusalError(
                f"Refused: could not locate asking run for role '{role}' (T-RUN-20)"
            )

        # Verify question ID exists in run's questions or questions.md
        q_ids = [q["id"].upper() for q in asking_run_data.get("questions", [])]
        qid_upper = question_id.strip().upper()

        if qid_upper not in q_ids:
            # Also check run folder questions.md
            q_file = root / asking_run_data["folder"] / "questions.md"
            file_qids: List[str] = []
            if q_file.is_file():
                file_text = q_file.read_text(encoding="utf-8")
                matches = re.findall(r"^#+\s*(Q\d+)\b", file_text, re.MULTILINE | re.IGNORECASE)
                if not matches:
                    matches = re.findall(r"^(?:-\s*)?(Q\d+)\s*:", file_text, re.MULTILINE | re.IGNORECASE)
                file_qids = [m.upper() for m in matches]
            if qid_upper not in file_qids:
                raise RunRefusalError(
                    f"Refused: question ID '{question_id}' not found in run's questions (T-RUN-20)"
                )
            # Add to run's questions list if was missing from state doc
            asking_run_data.setdefault("questions", []).append({"id": qid_upper, "answered_at": None})

        # Append answer verbatim to shared/answers.md
        task_shared_dir = root / ".lintel" / "tasks" / task_id / "shared"
        task_shared_dir.mkdir(parents=True, exist_ok=True)
        answers_file = task_shared_dir / "answers.md"

        existing_content = answers_file.read_text(encoding="utf-8") if answers_file.is_file() else ""
        new_entry = f"### {question_id}\n{text}\n"

        if existing_content:
            if not existing_content.endswith("\n\n"):
                if not existing_content.endswith("\n"):
                    existing_content += "\n\n"
                else:
                    existing_content += "\n"
            updated_content = existing_content + new_entry
        else:
            updated_content = new_entry

        answers_file.write_text(updated_content, encoding="utf-8")

        # Also write to repo/shared/answers.md if repo/shared exists
        repo_shared = root / "shared"
        if repo_shared.is_dir():
            (repo_shared / "answers.md").write_text(updated_content, encoding="utf-8")

        # Compute answers_hash and update in task.json
        answers_hash = hashlib.sha256(updated_content.encode("utf-8")).hexdigest()
        task_data["answers_hash"] = answers_hash

        # Mark question as answered in run record
        now_str = _now_iso()
        for q in asking_run_data["questions"]:
            if q["id"].upper() == qid_upper:
                q["answered_at"] = now_str

        validate_state_doc("run", asking_run_data, file_path=asking_run_file)
        atomic_write_json(asking_run_file, asking_run_data)

        # Check for remaining open questions
        open_questions = [
            q["id"] for q in asking_run_data["questions"] if q.get("answered_at") is None
        ]

        stage = pos.get("stage", "r0")
        all_answered = (len(open_questions) == 0)
        task_data, next_action, row_id = flow.on_answer(
            task_data=task_data,
            role=role,
            all_answered=all_answered,
        )
        if not all_answered:
            next_action = f"Next open question: {open_questions[0]}"

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq

        validate_state_doc("task", task_data, file_path=task_state_file)
        atomic_write_json(task_state_file, task_data)

        # Append answer event
        event_rec = create_event(
            step=event_seq,
            event="answer",
            stage=stage,
            role=role,
            run=asking_run_data["id"],
            folder=asking_run_data["folder"],
            row=row_id,
        )
        append_event(
            state_dir=root / ".lintel" / "state",
            task_id=task_id,
            event_data=event_rec,
            task_dir=root / ".lintel" / "tasks" / task_id,
        )

        _update_progress_md(root, task_data, next_action)

    sys.stdout.write(f"{next_action}\n")
    return next_action
