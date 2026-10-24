"""Flow engine: transitions and next actions (M1a-18).

Implements the transition table from docs/state-machine.md as data structures
and state transition functions with guards.

Conforms to docs/state-machine.md:
- Section 2 Vocabulary (States, Context, Verdicts, Pairs, Stage map, Escalations, Route table, Phase inference)
- Section 3 Transition table (T-STG, T-RUN, T-VER, T-ARC, T-RVT, T-AMD, T-HUM, T-STL, T-FIN)
- Section 5 Checklist: next actions
- Section 6 Walkthroughs (W1 one-phase task, W2 amend path, W3 revisit & deferred problems)
"""

import sys
sys.dont_write_bytecode = True

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union


class FlowRefusalError(SystemExit):
    """Raised when a state machine transition or command authorization check refuses."""

    def __init__(self, message: str, code: int = 1, row: Optional[str] = None):
        super().__init__(code)
        self.message = message
        self.code = code
        self.row = row

    def __str__(self) -> str:
        return self.message


# -----------------------------------------------------------------------------
# Vocabulary Constants (docs/state-machine.md §2)
# -----------------------------------------------------------------------------

WRITERS: Tuple[str, ...] = ("po", "architect", "qa", "dev")
REVIEWERS: Tuple[str, ...] = ("po-reviewer", "architect-reviewer", "qa-reviewer", "dev-reviewer")
SPECIAL_ROLES: Tuple[str, ...] = ("final-reviewer", "arbiter")
ALL_ROLES: Set[str] = set(WRITERS) | set(REVIEWERS) | set(SPECIAL_ROLES)

ROLE_ALLOWED_VERDICTS: Dict[str, Set[str]] = {
    "po": {"complete", "needs-input", "no-change", "blocked"},
    "architect": {"complete", "needs-input", "no-change", "blocked"},
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
    "po": {"environment"},
    "architect": {"requirements", "environment"},
    "qa": {"requirements", "contract", "environment"},
    "dev": {"requirements", "contract", "test", "environment"},
    "po-reviewer": set(),
    "architect-reviewer": set(),
    "qa-reviewer": set(),
    "dev-reviewer": set(),
    "arbiter": set(),
    "final-reviewer": set(),
}

CAUSE_TO_REPORT_HEADING: Dict[str, str] = {
    "requirements": "Requirements problem",
    "contract": "Contract defect",
    "test": "Suspected test problem",
}


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -----------------------------------------------------------------------------
# Position and State Context
# -----------------------------------------------------------------------------

class Position:
    """Represents a state machine position (task.position)."""

    def __init__(
        self,
        stage: str,
        code: str,
        phase: Optional[int] = None,
        fr: bool = False,
        run: Optional[str] = None,
    ):
        self.stage = stage
        self.code = code
        self.phase = phase
        self.fr = fr
        self.run = run

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "code": self.code,
            "phase": self.phase,
            "fr": self.fr,
            "run": self.run,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Position":
        return cls(
            stage=data.get("stage", "r0"),
            code=data.get("code", "W.po"),
            phase=data.get("phase"),
            fr=bool(data.get("fr", False) or "+FR" in data.get("code", "")),
            run=data.get("run"),
        )

    @property
    def clean_code(self) -> str:
        return self.code.replace("+FR", "")

    def __repr__(self) -> str:
        return f"Position(stage={self.stage!r}, code={self.code!r}, phase={self.phase!r}, fr={self.fr!r}, run={self.run!r})"


def get_position(task_or_pos: Union[Dict[str, Any], Position]) -> Position:
    """Extract Position from task state dict, position dict, or Position object."""
    if isinstance(task_or_pos, Position):
        return task_or_pos
    if not isinstance(task_or_pos, dict):
        return Position(stage="r0", code="W.po")
    if "code" in task_or_pos and "stage" in task_or_pos:
        return Position.from_dict(task_or_pos)
    pos_data = task_or_pos.get("position", {})
    return Position.from_dict(pos_data)


def set_position(
    task_data: Dict[str, Any],
    stage: str,
    code: str,
    phase: Optional[int] = None,
    fr: bool = False,
    run: Optional[str] = None,
) -> Position:
    """Update task_data position and return Position."""
    pos = Position(stage=stage, code=code, phase=phase, fr=fr, run=run)
    task_data["position"] = pos.to_dict()
    return pos


def is_phase_locked(task_data: Dict[str, Any], phase: Optional[int] = None) -> bool:
    """Check if phase is locked (§2.2: has lock commit and no delivered commit)."""
    if phase is None:
        pos = get_position(task_data)
        phase = pos.phase
        if phase is None and pos.stage.startswith("p") and pos.stage[1:].isdigit():
            phase = int(pos.stage[1:])
    if phase is None:
        return False

    phases_data = task_data.get("phases", {})
    p_info = phases_data.get(str(phase), {})
    has_lock = p_info.get("lock") is not None and p_info.get("lock", {}).get("commit") is not None
    is_delivered = p_info.get("delivered") is not None and p_info.get("delivered", {}).get("commit") is not None
    return bool(has_lock and not is_delivered)


def is_phase_delivered(task_data: Dict[str, Any], phase: Optional[int] = None) -> bool:
    """Check if phase has been delivered."""
    if phase is None:
        pos = get_position(task_data)
        phase = pos.phase
        if phase is None and pos.stage.startswith("p") and pos.stage[1:].isdigit():
            phase = int(pos.stage[1:])
    if phase is None:
        return False
    delivered = task_data.get("delivered_phases", [])
    if phase in delivered:
        return True
    phases_data = task_data.get("phases", {})
    p_info = phases_data.get(str(phase), {})
    return bool(p_info.get("delivered") is not None and p_info.get("delivered", {}).get("commit") is not None)


def is_amend_cycle_open(task_data: Dict[str, Any]) -> bool:
    """Check if an amend cycle is open (§2.2)."""
    cycle = task_data.get("cycle")
    if cycle is True:
        return True
    if isinstance(cycle, dict):
        return bool(cycle)
    return False


def is_dev_turn(task_data: Dict[str, Any]) -> bool:
    """Check if currently DEV's turn (§2.2: locked, no amend cycle open, not delivered)."""
    pos = get_position(task_data)
    if not pos.stage.startswith("p"):
        return False
    phase = pos.phase
    if phase is None and pos.stage[1:].isdigit():
        phase = int(pos.stage[1:])
    return bool(is_phase_locked(task_data, phase) and not is_amend_cycle_open(task_data) and not is_phase_delivered(task_data, phase))


def _ensure_docs_update(task_data: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure docs_update dict exists in task_data and return it."""
    docs_up = task_data.get("docs_update")
    if not isinstance(docs_up, dict):
        docs_up = {
            "po_finished": False,
            "architect_finished": False,
            "po_changed": None,
            "architect_changed": None,
            "outcome": None,
        }
        task_data["docs_update"] = docs_up
    return docs_up


# -----------------------------------------------------------------------------
# Stage Inference and Phase Validation (T-STG-20..23, T-ARC-01..04)
# -----------------------------------------------------------------------------

def normalize_phase_arg(
    phase_arg: Optional[Union[str, int]],
    return_row: bool = False,
) -> Any:
    """Normalize phase argument: bare digit N becomes 'pN' (T-STG-22)."""
    if phase_arg is None:
        return (None, "T-STG-22") if return_row else None
    s = str(phase_arg).strip()
    res = f"p{s}" if s.isdigit() else s
    if return_row:
        return res, "T-STG-22"
    return res


def infer_stage(task_data: Dict[str, Any], return_row: bool = False) -> Any:
    """Infer current stage label per docs/state-machine.md §2.8 and T-STG-20."""
    pos = get_position(task_data)
    stage = pos.stage
    code = pos.code

    # If in escalation, stage is the one in which escalation arose
    if code.startswith("D."):
        esc = task_data.get("escalation")
        if esc and esc.get("stage"):
            res = str(esc["stage"])
        else:
            res = stage
    elif stage.startswith("r") and stage != "r0":
        res = stage
    elif stage == "r0":
        res = "r0"
    elif stage.startswith("p"):
        res = stage
    elif stage == "final":
        res = "final"
    elif stage == "docs":
        res = "docs"
    else:
        res = stage

    if return_row:
        return res, "T-STG-20"
    return res



def validate_phase_arg(
    task_data: Dict[str, Any],
    phase_arg: Optional[Union[str, int]],
    command: Optional[str] = None,
) -> str:
    """Validate and resolve --phase argument against state machine rules.

    Conforms to:
    - T-STG-20: infer stage label when omitted
    - T-STG-21: refuse mismatch against expected label
    - T-STG-22: bare number N means p<N>
    - T-STG-23: diff --delivered N checks against delivered_phases
    - T-ARC-04: approve plan --phase N checks first undelivered phase
    """
    cmd = (command or "").strip()

    # Exception: diff --delivered N (T-STG-23)
    if cmd == "diff --delivered" or cmd == "diff":
        if phase_arg is not None:
            raw_s = str(phase_arg).strip()
            num_match = re.match(r"^p?(\d+)$", raw_s)
            if not num_match:
                raise FlowRefusalError(
                    f"Refused: phase '{phase_arg}' is not a valid phase number (T-STG-23)",
                    row="T-STG-23",
                )
            p_int = int(num_match.group(1))
            delivered = task_data.get("delivered_phases", [])
            if p_int not in delivered:
                raise FlowRefusalError(
                    f"Refused: phase {p_int} is not a delivered phase (T-STG-23)",
                    row="T-STG-23",
                )
            return f"p{p_int}"

    # Exception: approve plan --phase N (T-ARC-01..04)
    if cmd == "approve plan":
        first_undelivered = _find_first_undelivered_phase(task_data)
        if first_undelivered is not None:
            if phase_arg is None:
                # Omission infers first undelivered phase (T-ARC-01, T-STG-20)
                return str(first_undelivered)
            raw_s = str(phase_arg).strip()
            num_match = re.match(r"^p?(\d+)$", raw_s)
            if not num_match or int(num_match.group(1)) != first_undelivered:
                raise FlowRefusalError(
                    f"Refused: approve plan expected phase {first_undelivered}, got '{phase_arg}' (T-ARC-04)",
                    row="T-ARC-04",
                )
            return str(first_undelivered)
        else:
            if phase_arg is not None:
                raise FlowRefusalError(
                    "Refused: approve plan takes no --phase when no undelivered phase is left (T-ARC-04)",
                    row="T-ARC-04",
                )
            return ""

    # Standard stage inference and check (T-STG-20, T-STG-21, T-STG-22)
    expected_stage = infer_stage(task_data)
    if phase_arg is None:
        return expected_stage

    norm = normalize_phase_arg(phase_arg)
    if norm != expected_stage:
        raise FlowRefusalError(
            f"Refused: phase argument '{phase_arg}' does not match expected stage '{expected_stage}' (T-STG-21)",
            row="T-STG-21",
        )
    return expected_stage


def _find_first_undelivered_phase(task_data: Dict[str, Any]) -> Optional[int]:
    """Find the first undelivered phase integer from plan / phases state."""
    delivered = set(task_data.get("delivered_phases", []))
    phases_data = task_data.get("phases", {})
    known_phase_nums: Set[int] = set()

    for k in phases_data.keys():
        if str(k).isdigit():
            known_phase_nums.add(int(k))

    pos = get_position(task_data)
    if pos.phase is not None:
        known_phase_nums.add(pos.phase)

    undelivered = sorted(known_phase_nums - delivered)
    if undelivered:
        return undelivered[0]

    # Default to 1 if stage is r0 or p1 and nothing delivered yet
    if not delivered and pos.stage in ("r0", "p1"):
        return 1

    return None


# -----------------------------------------------------------------------------
# Role and Action Authorization (T-RUN-02, T-HUM-14, etc.)
# -----------------------------------------------------------------------------

def expected_role_for_state(task_data: Dict[str, Any]) -> Optional[str]:
    """Determine persona role expected next by the state machine (§2.1, T-RUN-02).

    Returns None if a human action or gate command is expected rather than an invoke.
    """
    pos = get_position(task_data)
    code = pos.code
    clean_code = code.replace("+FR", "")

    if clean_code.startswith("W."):
        writer = clean_code[2:]
        if writer in ("po", "architect", "qa", "dev", "final-reviewer", "arbiter"):
            return writer
        return None

    if clean_code.startswith("R."):
        writer = clean_code[2:]
        return f"{writer}-reviewer"

    if clean_code.startswith("Q."):
        writer = clean_code[2:]
        return writer

    if clean_code == "D.stall":
        # In M1 arbiter is stubbed, but if state code expects arbiter:
        return None

    return None


def format_next_action(
    task_or_pos: Union[Dict[str, Any], Position],
    repo_root: Optional[Union[str, Path]] = None,
) -> str:
    """Format the exact printed next action string per docs/state-machine.md §3 & §5."""
    if isinstance(task_or_pos, Position):
        task_data: Dict[str, Any] = {"status": "active", "position": task_or_pos.to_dict()}
    elif isinstance(task_or_pos, dict):
        if "position" in task_or_pos:
            task_data = task_or_pos
        else:
            task_data = {"status": task_or_pos.get("status", "active"), "position": task_or_pos}
    else:
        return 'lintel task start "<title>"'

    status = task_data.get("status", "active")
    if status != "active":
        return 'lintel task start "<title>"'

    pos = get_position(task_data)
    if repo_root is not None and task_data.get("id"):
        clean_code_check = pos.code.replace("+FR", "")
        pair_for_state = {
            "H.req": "po",
            "H.plan": "architect",
            "H.lock": "qa",
            "H.amend": "qa",
            "O.deliver": "dev",
        }
        if clean_code_check in pair_for_state:
            pair = pair_for_state[clean_code_check]
            try:
                from lintel import reviews
                if not reviews.is_review_valid(repo_root, task_data["id"], pair, task_data=task_data):
                    pos = Position(stage=pos.stage, code=f"R.{pair}", run=pos.run, phase=pos.phase, fr=pos.fr)
            except Exception:
                pass
        elif clean_code_check == "H.final":
            try:
                from lintel import reviews
                if not reviews.is_review_valid(repo_root, task_data["id"], "final-reviewer", task_data=task_data):
                    pos = Position(stage=pos.stage, code="W.final-reviewer", run=pos.run, phase=pos.phase, fr=pos.fr)
            except Exception:
                pass
        elif clean_code_check == "H.docs":
            try:
                from lintel import reviews
                docs_up = task_data.get("docs_update") or {}
                po_changed = docs_up.get("po_changed")
                if po_changed:
                    if not reviews.is_review_valid(repo_root, task_data["id"], "po", task_data=task_data):
                        pos = Position(stage=pos.stage, code="R.po", run=pos.run, phase=pos.phase, fr=pos.fr)
                arch_changed = docs_up.get("architect_changed")
                if arch_changed and pos.code != "R.po":
                    if not reviews.is_review_valid(repo_root, task_data["id"], "architect", task_data=task_data):
                        pos = Position(stage=pos.stage, code="R.architect", run=pos.run, phase=pos.phase, fr=pos.fr)
            except Exception:
                pass

    stage = pos.stage
    code = pos.code
    fr = pos.fr or "+FR" in code
    phase = pos.phase
    clean_code = code.replace("+FR", "")

    if clean_code == "NONE":
        return 'lintel task start "<title>"'

    fr_suffix = " --from-report" if fr else ""

    # Persona Writers
    if clean_code == "W.po":
        return f"lintel invoke po --phase {stage}{fr_suffix}"
    if clean_code == "W.architect":
        return f"lintel invoke architect --phase {stage}{fr_suffix}"
    if clean_code == "W.qa":
        return f"lintel invoke qa --phase {stage}{fr_suffix}"
    if clean_code == "W.dev":
        return f"lintel invoke dev --phase {stage}"
    if clean_code == "W.final-reviewer":
        return f"lintel invoke final-reviewer --phase final"
    if clean_code == "W.arbiter":
        pair = (task_data.get("escalation") or {}).get("writer") or "dev"
        return f"lintel invoke arbiter --pair {pair} --phase {stage}"

    # Persona Reviewers
    if clean_code == "R.po":
        return f"lintel invoke po-reviewer --phase {stage}"
    if clean_code == "R.architect":
        return f"lintel invoke architect-reviewer --phase {stage}"
    if clean_code == "R.qa":
        return f"lintel invoke qa-reviewer --phase {stage}"
    if clean_code == "R.dev":
        return f"lintel invoke dev-reviewer --phase {stage}"

    # Active / Pending Run
    if clean_code.startswith("P.") or clean_code.startswith("A."):
        return "lintel task wait"

    # Questions (Q.<w>)
    if clean_code.startswith("Q."):
        writer = clean_code[2:]
        return (
            f"ask the user the questions in questions.md (the ask_question tool), then "
            f'lintel answer {writer} <question-id> "<text>", then '
            f"lintel invoke {writer} --phase {stage}{fr_suffix}"
        )

    # Approvals and Human Steps
    if clean_code == "H.req":
        return "human: lintel approve requirements"

    if clean_code == "H.plan":
        first_undelivered = _find_first_undelivered_phase(task_data)
        if first_undelivered is not None:
            return f"human: lintel approve plan --phase {first_undelivered}"
        return "human: lintel approve plan"

    if clean_code == "H.lock":
        p_val = phase if phase is not None else (stage[1:] if stage.startswith("p") else stage)
        return f"human: lintel lock --phase {p_val}"

    if clean_code == "H.amend":
        p_val = phase if phase is not None else (stage[1:] if stage.startswith("p") else stage)
        return f"human: lintel amend --phase {p_val}"

    if clean_code == "O.deliver":
        p_val = phase if phase is not None else (stage[1:] if stage.startswith("p") else stage)
        unacked_drift = task_data.get("unacked_drift", False)
        unacked_audit = task_data.get("unacked_audit", False)
        if unacked_drift or unacked_audit:
            return (
                f"human: lintel drift --phase {p_val} --ack and "
                f"human: lintel audit --phase {p_val} --ack; then "
                f"lintel deliver --phase {p_val}"
            )
        return f"lintel deliver --phase {p_val}"

    if clean_code == "H.final":
        return "human: lintel approve final"

    if clean_code == "H.docs":
        return "human: lintel approve docs"

    if clean_code == "H.finish":
        return "human: lintel task finish [--pr]"

    # Escalations (D.<kind>)
    if clean_code.startswith("D."):
        esc_stage = stage
        esc = task_data.get("escalation")
        if esc and esc.get("stage"):
            esc_stage = esc["stage"]
        return f"human: lintel decide --phase {esc_stage}"

    return "lintel task status"


def check_action_allowed(
    task_or_pos: Union[Dict[str, Any], Position],
    command: str,
    **kwargs: Any,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Check whether a command/action is authorized in the current state.

    Returns (allowed, refusal_message, row_id).
    """
    cmd = command.strip()
    if cmd.startswith("invoke "):
        parts = cmd.split(None, 1)
        cmd = "invoke"
        if "role" not in kwargs:
            kwargs["role"] = parts[1].strip()

    if cmd in ("task unstick", "unstick"):
        cmd = "task unstick"

    if cmd in ("task finish", "finish"):
        cmd = "task finish"

    if cmd in ("task abort", "abort"):
        cmd = "task abort"

    if isinstance(task_or_pos, Position):
        task_data: Dict[str, Any] = {"status": "active", "position": task_or_pos.to_dict()}
    elif isinstance(task_or_pos, dict):
        if "position" in task_or_pos:
            task_data = task_or_pos
        else:
            task_data = {"status": task_or_pos.get("status", "active"), "position": task_or_pos}
    else:
        task_data = {"status": None}

    status = task_data.get("status", "active")

    if cmd == "task start":
        if status == "active":
            return False, "Refused: a task is active (T-STG-02)", "T-STG-02"
        if kwargs.get("interpreter_ok") is False:
            return False, "Refused: python interpreter is missing or broken (T-STG-03)", "T-STG-03"
        if kwargs.get("clean_tree") is False:
            return False, "Refused: working tree is not clean (T-STG-04)", "T-STG-04"
        if kwargs.get("config_valid") is False:
            return False, "Refused: config is invalid (T-STG-05)", "T-STG-05"
        if kwargs.get("baseline_green") is False:
            return False, "Refused: baseline is not green (T-STG-06)", "T-STG-06"
        if kwargs.get("baseline_clean") is False:
            return False, "Refused: baseline leaves worktree unclean (T-STG-07)", "T-STG-07"
        if kwargs.get("template_adjusted") is False:
            return False, "Refused: unadjusted test template script (T-STG-08)", "T-STG-08"
        return True, None, "T-STG-01"

    if not status or status != "active":
        if cmd in ("doctor", "task status", "install", "init"):
            if cmd == "install" and kwargs.get("no_interpreter"):
                return False, "Refused: no python3 >= 3.9 passes interpreter search (T-HUM-44)", "T-HUM-44"
            return True, None, "T-HUM-44" if cmd == "install" else None
        return False, "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)", "T-STG-09"

    if kwargs.get("plugin_dirty"):
        return False, "Refused: vendored plugin dir differs from recorded head (T-STG-10)", "T-STG-10"
    if kwargs.get("roll_forward"):
        return True, None, "T-STG-11"
    if kwargs.get("agent_commit"):
        return True, None, "T-STG-12"
    if kwargs.get("wrong_branch"):
        return False, "Refused: HEAD is not on branch lintel/<id> (T-STG-13)", "T-STG-13"
    if kwargs.get("invalid_state_doc"):
        return False, "Refused: state file fails validation on load (T-STG-14)", "T-STG-14"

    dialog_outcome = kwargs.get("dialog_outcome")
    if dialog_outcome is not None:
        if dialog_outcome in ("not-me", "canceled", "error", "unavailable") or kwargs.get("no_gui"):
            return False, "Refused: dialog returned not-me / canceled / error / unavailable / no GUI (T-HUM-02)", "T-HUM-02"
        if dialog_outcome == "gave-up":
            return False, "Refused: human dialog gave up (T-HUM-03)", "T-HUM-03"
        if dialog_outcome in ("agent", "agent-refused", "agent-caller") or kwargs.get("agent_caller"):
            return False, "Refused: human-only command called by agent (T-HUM-04)", "T-HUM-04"
        if dialog_outcome == "ok" and (kwargs.get("dialog_only") or kwargs.get("dialog_ok")):
            return True, None, "T-HUM-01"

    if kwargs.get("dialog_ok"):
        return True, None, "T-HUM-01"

    if kwargs.get("full"):
        return True, None, "T-HUM-07"

    if kwargs.get("confirm_no") or kwargs.get("confirmed") is False:
        return True, None, "T-HUM-08"

    pos = get_position(task_data)
    code = pos.code
    stage = pos.stage
    clean_code = code.replace("+FR", "")
    run_active = pos.run is not None or clean_code.startswith("P.") or clean_code.startswith("A.")

    # T-HUM-05 / T-HUM-06 / T-FIN-31: Commands forbidden while a run is pending or active
    if run_active:
        if cmd == "task abort":
            return False, "Refused: task abort refused while a run is active (T-FIN-31)", "T-FIN-31"
        if cmd == "task unstick":
            force = kwargs.get("force", False)
            is_live = kwargs.get("is_live", True)
            is_human = kwargs.get("is_human", True)
            if force and not is_human:
                return (
                    False,
                    "Refused: task unstick --force is human-only (T-FIN-54)",
                    "T-FIN-54",
                )
            if not force and is_live:
                return (
                    False,
                    "Refused: run looks live; run 'lintel task wait', or 'human: lintel task unstick --force' when it is known to be stuck (T-FIN-51)",
                    "T-FIN-51",
                )
            return True, None, "T-FIN-52" if force else "T-FIN-40"

        if cmd in ("task wait", "enter", "done"):
            return True, None, None

        if cmd == "invoke":
            return False, "Refused: a run is pending or active (T-RUN-03); lintel task wait", "T-RUN-03"

        if cmd == "deliver":
            return False, "Refused: deliver not allowed while a run is active (T-HUM-06); lintel task wait", "T-HUM-06"

        return False, "Refused: command not allowed while a run is active (T-HUM-05); lintel task wait", "T-HUM-05"

    # Command-specific state checks when no run is active
    if cmd == "invoke":
        # Check stall
        if clean_code == "D.stall":
            esc = task_data.get("escalation") or {}
            reason = esc.get("reason", "no-progress")
            esc_stage = task_data.get("position", {}).get("stage", "")
            return (
                False,
                f"Refused: A stalled pair gets no further invoke: {reason} (T-RUN-10); human: lintel decide --phase {esc_stage}",
                "T-RUN-10",
            )

        role = kwargs.get("role")
        if role and role not in ALL_ROLES:
            return False, f"Refused: unknown role '{role}' (T-RUN-04)", "T-RUN-04"

        if clean_code.startswith("Q."):
            return False, "Refused: questions unanswered; run lintel answer first (T-RUN-09)", "T-RUN-09"

        expected_role = expected_role_for_state(task_data)
        if role is None:
            role = expected_role
        elif expected_role != role:
            expected_act = format_next_action(task_data)
            return (
                False,
                f"invoke refuses a role the state machine doesn't expect next: expected '{expected_act}' (T-RUN-02)",
                "T-RUN-02",
            )

        # Check --from-report flag only if explicitly passed in kwargs
        if "from_report" in kwargs:
            expected_fr = pos.fr or "+FR" in code
            actual_fr = bool(kwargs.get("from_report", False))
            if actual_fr != expected_fr:
                expected_act = format_next_action(task_data)
                return (
                    False,
                    f"Refused: --from-report flag mismatch (T-RUN-08); expected '{expected_act}'",
                    "T-RUN-08",
                )

        if kwargs.get("agent_file_dirty"):
            return False, "Refused: agent file frontmatter invalid (T-RUN-05)", "T-RUN-05"
        if kwargs.get("specs_dirty"):
            return False, "Refused: specs or steering differs from recorded head (T-RUN-06)", "T-RUN-06"
        if kwargs.get("living_docs_dirty"):
            return False, "Refused: living docs differ from recorded head (T-RUN-07)", "T-RUN-07"

        if stage.startswith("r") and stage != "r0" and role == "po" and not kwargs.get("from_report") and task_data.get("deferred"):
            return True, None, "T-RVT-04"

        if kwargs.get("from_report"):
            if stage.startswith("p") and is_phase_locked(task_data, pos.phase):
                if is_amend_cycle_open(task_data):
                    return True, None, "T-AMD-71"
                return True, None, "T-AMD-70"
            if role == "po":
                return True, None, "T-RVT-06"
            if role == "architect":
                return True, None, "T-RVT-07"
            if role == "qa":
                return True, None, "T-RVT-08"

        return True, None, "T-RUN-01"

    if cmd == "enter":
        if kwargs.get("nonce_valid") is False:
            return False, "Refused: invalid nonce (T-RUN-12)", "T-RUN-12"
        if kwargs.get("role_valid") is False:
            return False, "Refused: unexpected role (T-RUN-13)", "T-RUN-13"
        return True, None, "T-RUN-11"

    if cmd == "done":
        if kwargs.get("repeat_verdict"):
            return True, None, "T-RUN-14"
        if kwargs.get("different_verdict"):
            return False, "Refused: different verdict already recorded (T-RUN-15)", "T-RUN-15"
        if kwargs.get("invalid_verdict"):
            return False, "Refused: verdict not in role verdict set (T-RUN-16)", "T-RUN-16"
        if kwargs.get("run_active") is False:
            return False, "Refused: run is not active (T-RUN-18)", "T-RUN-18"
        return True, None, None

    if cmd == "answer":
        if kwargs.get("question_open") is False:
            return False, "Refused: no question open (T-RUN-20)", "T-RUN-20"
        return True, None, "T-RUN-19"

    if cmd == "check":
        if kwargs.get("gate_different_running") or kwargs.get("gate_running_other"):
            return False, "Refused: live running record exists for different tree/command (T-STG-15)", "T-STG-15"
        if kwargs.get("gate_same_running") or kwargs.get("gate_running_same"):
            return True, None, "T-STG-16"
        if kwargs.get("stale_running") or kwargs.get("gate_running_stale"):
            return True, None, "T-STG-17"
        if kwargs.get("symlink_outside"):
            return False, "Refused: symlink outside repo (T-AMD-23)", "T-AMD-23"
        p_val = pos.phase or (int(stage[1:]) if stage.startswith("p") else None)
        if p_val is None or not (is_phase_locked(task_data, p_val) and not is_phase_delivered(task_data, p_val)):
            return False, "Refused: phase is not locked or is delivered (T-AMD-22)", "T-AMD-22"
        if kwargs.get("start"):
            return True, None, "T-AMD-21"
        return True, None, "T-AMD-20"

    if cmd == "approve requirements":
        if kwargs.get("dialog_ok"):
            return True, None, "T-HUM-01"
        if kwargs.get("confirm_no"):
            return True, None, "T-HUM-08"
        if kwargs.get("reject_loop"):
            return True, None, "T-HUM-16"
        if kwargs.get("wrong_pair"):
            return False, "Refused: wrong pair for reject (T-HUM-17)", "T-HUM-17"
        if clean_code != "H.req":
            expected_act = format_next_action(task_data)
            return False, f"Refused: state does not expect approve requirements; expected '{expected_act}' (T-HUM-14)", "T-HUM-14"
        if kwargs.get("missing_req_id"):
            return False, "Refused: known requirement id missing (T-HUM-13)", "T-HUM-13"
        if kwargs.get("valid_review") is False:
            return False, "Refused: no valid review (T-HUM-09)", "T-HUM-09"
        if kwargs.get("reject"):
            return True, None, "T-HUM-15"
        row_id = "T-HUM-10" if stage == "r0" else ("T-HUM-11" if stage.startswith("r") else "T-HUM-12")
        return True, None, row_id

    if cmd == "approve plan":
        if clean_code != "H.plan":
            expected_act = format_next_action(task_data)
            return False, f"Refused: state does not expect approve plan; expected '{expected_act}' (T-HUM-14)", "T-HUM-14"
        if kwargs.get("plan_missing") or kwargs.get("missing_req_in_phase"):
            return False, "Refused: requirement is in no phase (T-ARC-10)", "T-ARC-10"
        if kwargs.get("unknown_req_id"):
            return False, "Refused: requirement does not exist (T-ARC-11)", "T-ARC-11"
        if kwargs.get("changed_req_not_in_phase"):
            return False, "Refused: changed requirement not in undelivered phase (T-ARC-12)", "T-ARC-12"
        if kwargs.get("withdrawn_delivered_not_in_phase"):
            return False, "Refused: withdrawn delivered requirement not in undelivered phase (T-ARC-13)", "T-ARC-13"
        if kwargs.get("req_missing_outline"):
            return False, "Refused: requirement has no line in test outline (T-ARC-14)", "T-ARC-14"
        if kwargs.get("required_group_missing"):
            return False, "Refused: required test group missing from outline (T-ARC-15)", "T-ARC-15"
        if kwargs.get("group_both_outline_and_none"):
            return False, "Refused: group has both outline line and none (T-ARC-16)", "T-ARC-16"
        if kwargs.get("outline_unknown_group"):
            return False, "Refused: outline line names unknown group or support (T-ARC-17)", "T-ARC-17"
        if kwargs.get("none_without_reason"):
            return False, "Refused: none has no reason in test outline (T-ARC-18)", "T-ARC-18"
        if kwargs.get("empty_outline"):
            return False, "Refused: no group listed in test outline (T-ARC-19)", "T-ARC-19"
        if kwargs.get("stub_check_failed"):
            return False, "Refused: stub file check failed (T-ARC-20)", "T-ARC-20"
        if kwargs.get("valid_review") is False:
            return False, "Refused: no valid architect review (T-ARC-05)", "T-ARC-05"
        if kwargs.get("first_undelivered") is False:
            return False, "Refused: phase is not first undelivered (T-ARC-04)", "T-ARC-04"
        if kwargs.get("locked_differs") is True:
            return False, "Refused: plan.md or design.md changed after lock (T-ARC-06)", "T-ARC-06"
        if kwargs.get("delivered_differs") is True:
            return False, "Refused: delivered phase changed in plan.md (T-ARC-07)", "T-ARC-07"
        if kwargs.get("reject"):
            return True, None, "T-ARC-08"
        if kwargs.get("no_undelivered_left"):
            return True, None, "T-ARC-03"
        if kwargs.get("design_already_approved"):
            return True, None, "T-ARC-02"
        return True, None, "T-ARC-01"

    if cmd == "lock":
        if clean_code != "H.lock":
            expected_act = format_next_action(task_data)
            return False, f"Refused: state does not expect lock; expected '{expected_act}' (T-HUM-14)", "T-HUM-14"
        if kwargs.get("valid_review") is False:
            return False, "Refused: no valid QA review (T-AMD-02)", "T-AMD-02"
        if kwargs.get("lane_valid") is False:
            return False, "Refused: path outside QA lane (T-AMD-03)", "T-AMD-03"
        if kwargs.get("contract_valid") is False:
            return False, "Refused: contract or stub changed (T-AMD-04)", "T-AMD-04"
        if kwargs.get("groups_valid") is False:
            return False, "Refused: listed group has no added or changed file (T-AMD-05)", "T-AMD-05"
        if kwargs.get("green_allowed") is False:
            return False, "Refused: listed group exits 0 without --allow-green (T-AMD-06)", "T-AMD-06"
        if kwargs.get("exit_code_2") is True:
            return False, "Refused: group exited 2 (T-AMD-07)", "T-AMD-07"
        if kwargs.get("exit_code_3") is True:
            return False, "Refused: exit 3 for listed group or group with files (T-AMD-08)", "T-AMD-08"
        if kwargs.get("tests_intact") is False:
            return False, "Refused: run modified or deleted tracked file (T-AMD-09)", "T-AMD-09"
        if kwargs.get("reject"):
            return True, None, "T-AMD-10"
        return True, None, "T-AMD-01"

    if cmd == "amend":
        if clean_code != "H.amend" or not is_amend_cycle_open(task_data):
            expected_act = format_next_action(task_data)
            return False, f"Refused: amend not allowed outside amend cycle (T-AMD-77); expected '{expected_act}'", "T-AMD-77"
        if kwargs.get("valid_review") is False:
            return False, "Refused: no valid QA review (T-AMD-75)", "T-AMD-75"
        if kwargs.get("listed_group_empty"):
            return False, "Refused: listed group left with no tests (T-AMD-76)", "T-AMD-76"
        if kwargs.get("reject"):
            return True, None, "T-AMD-78"
        if kwargs.get("tests_stand"):
            return True, None, "T-AMD-74"
        return True, None, "T-AMD-73"

    if cmd == "deliver":
        if clean_code != "O.deliver":
            expected_act = format_next_action(task_data)
            return False, f"Refused: state is not O.deliver; expected '{expected_act}' (T-AMD-38)", "T-AMD-38"
        if kwargs.get("valid_review") is False:
            return False, "Refused: no valid DEV review (T-AMD-31)", "T-AMD-31"
        if kwargs.get("lane_valid") is False:
            return False, "Refused: lane violation (T-AMD-32)", "T-AMD-32"
        if kwargs.get("plugin_valid") is False:
            return False, "Refused: .lintel or plugin changed (T-AMD-33)", "T-AMD-33"
        if kwargs.get("groups_valid") is False:
            return False, "Refused: listed group no longer exists (T-AMD-34)", "T-AMD-34"
        if kwargs.get("drift_acked") is False:
            return False, "Refused: drift not acked (T-AMD-35)", "T-AMD-35"
        if kwargs.get("audit_acked") is False:
            return False, "Refused: audit crossings not acked (T-AMD-36)", "T-AMD-36"
        if kwargs.get("gate_green") is False:
            return False, "Refused: a test group is red (T-AMD-37)", "T-AMD-37"
        return True, None, "T-AMD-30"

    if cmd == "approve final":
        if clean_code != "H.final":
            expected_act = format_next_action(task_data)
            return False, f"Refused: state does not expect approve final; expected '{expected_act}' (T-HUM-14)", "T-HUM-14"
        if kwargs.get("valid_done") is False:
            return False, "Refused: no valid final review done recorded (T-FIN-04)", "T-FIN-04"
        if kwargs.get("reject"):
            return True, None, "T-FIN-05"
        return True, None, "T-FIN-03"

    if cmd == "approve docs":
        if clean_code != "H.docs":
            expected_act = format_next_action(task_data)
            return False, f"Refused: state does not expect approve docs; expected '{expected_act}' (T-HUM-14)", "T-HUM-14"
        if kwargs.get("writers_finished") is False or kwargs.get("valid_review") is False:
            return False, "Refused: writers not finished or no valid review (T-FIN-11)", "T-FIN-11"
        if kwargs.get("reject") and kwargs.get("both_changed") and not kwargs.get("pair"):
            return False, "Refused: --pair required in docs update when both changed (T-FIN-13)", "T-FIN-13"
        if kwargs.get("reject"):
            return True, None, "T-FIN-12"
        return True, None, "T-FIN-10"

    if cmd == "task finish":
        if clean_code != "H.finish":
            expected_act = format_next_action(task_data)
            return False, f"Refused: task finish requires H.finish; expected '{expected_act}' (T-FIN-21)", "T-FIN-21"
        if kwargs.get("pr"):
            return True, None, "T-FIN-22"
        return True, None, "T-FIN-20"

    if cmd == "task abort":
        if kwargs.get("uncommitted_edits"):
            return False, "Refused: uncommitted edits to config/scripts/personas/steering (T-FIN-32)", "T-FIN-32"
        return True, None, "T-FIN-30"

    if cmd == "decide":
        if not clean_code.startswith("D."):
            expected_act = format_next_action(task_data)
            return False, f"Refused: no escalation pending; expected '{expected_act}' (T-STL-21)", "T-STL-21"
        if kwargs.get("pair") and kwargs.get("expected_pair") and kwargs["pair"] != kwargs["expected_pair"]:
            return False, "Refused: --pair does not match escalation pair (T-STL-23)", "T-STL-23"
        if kwargs.get("option") == "invalid":
            return False, "Refused: unknown decide option (T-STL-22)", "T-STL-22"
        if kwargs.get("skip_docs") or kwargs.get("option") in ("skip-docs", "skip_docs"):
            if stage != "docs":
                return False, "Refused: --skip-docs only allowed in docs update (T-STL-35)", "T-STL-35"
        if kwargs.get("event_record"):
            return True, None, "T-STL-37"
        return True, None, "T-STL-20"

    if cmd == "reset":
        phase = pos.phase
        if phase is None and stage.startswith("p") and stage[1:].isdigit():
            phase = int(stage[1:])
        if not (is_phase_locked(task_data, phase) and not is_phase_delivered(task_data, phase)):
            return False, "Refused: reset only allowed while phase is locked and not delivered (T-AMD-61)", "T-AMD-61"
        return True, None, "T-AMD-60"

    if cmd == "drift":
        if not is_dev_turn(task_data):
            return False, "Refused: drift only allowed in DEV's turn after lock and outside amend cycle (T-AMD-53)", "T-AMD-53"
        if kwargs.get("ack"):
            return True, None, "T-AMD-51"
        if kwargs.get("reject"):
            return True, None, "T-AMD-52"
        return True, None, "T-AMD-50"

    if cmd == "audit":
        if kwargs.get("no_pair_findings"):
            return True, None, "T-HUM-36"
        reject = kwargs.get("reject", False)
        if reject:
            if is_amend_cycle_open(task_data):
                return False, "Refused: audit --reject not allowed inside amend cycle (T-HUM-33)", "T-HUM-33"
            if clean_code in ("H.final",) or clean_code.startswith("D."):
                return False, "Refused: audit --reject not allowed when pending step is approve final or decide (T-HUM-34)", "T-HUM-34"
            if stage == "docs" and kwargs.get("both_changed") and not kwargs.get("pair"):
                return False, "Refused: --pair required in docs update when both changed (T-HUM-35)", "T-HUM-35"
            return True, None, "T-HUM-32"
        if kwargs.get("ack"):
            return True, None, "T-HUM-31"
        return True, None, "T-HUM-30"

    if cmd == "amend --config":
        if stage == "docs":
            return False, "Refused: amend --config not allowed in docs update (T-HUM-22)", "T-HUM-22"
        if kwargs.get("invalid_release"):
            return False, "Refused: path is not locked or still tests (T-HUM-23)", "T-HUM-23"
        if kwargs.get("release_only"):
            return True, None, "T-HUM-21"
        return True, None, "T-HUM-20"

    if cmd == "restore-tests":
        return True, None, "T-AMD-40" if is_dev_turn(task_data) else "T-AMD-41"

    if cmd == "task unstick":
        if clean_code == "D.stall":
            return True, None, "T-FIN-40"
        if not pos.run and not (clean_code.startswith("P.") or clean_code.startswith("A.")):
            return False, "Refused: no run is pending or active (T-FIN-41)", "T-FIN-41"
        return True, None, "T-FIN-40"

    if cmd == "diff":
        if kwargs.get("full"):
            return True, None, "T-HUM-07"
        return True, None, "T-HUM-40"
    if cmd == "doctor":
        return True, None, "T-HUM-41"
    if cmd == "task status":
        return True, None, "T-HUM-42"
    if cmd == "install":
        if kwargs.get("no_interpreter"):
            return False, "Refused: no python3 >= 3.9 passes interpreter search (T-HUM-44)", "T-HUM-44"
        return True, None, "T-HUM-44"

    return True, None, None


# -----------------------------------------------------------------------------
# Transitions Engine
# -----------------------------------------------------------------------------

def on_task_start(task_data: Dict[str, Any], title: str) -> Tuple[Dict[str, Any], str, str]:
    """Execute transition T-STG-01: Start new task -> r0:W.po."""
    set_position(task_data, stage="r0", code="W.po", phase=None, fr=False, run=None)
    action = format_next_action(task_data)
    return task_data, action, "T-STG-01"


def on_invoke(
    task_data: Dict[str, Any],
    role: str,
    from_report: bool = False,
    pair: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], str, str]:
    """Execute transition T-RUN-01: invoke role -> P.<role>."""
    pos = get_position(task_data)
    stage = pos.stage
    phase = pos.phase
    if phase is None and stage.startswith("p") and stage[1:].isdigit():
        phase = int(stage[1:])

    row_id = "T-RUN-01"

    # Check T-RVT-04: invoke po with deferred problem in revisit
    if stage.startswith("r") and stage != "r0" and role == "po" and not from_report and task_data.get("deferred"):
        row_id = "T-RVT-04"

    # Check T-AMD-70 / T-AMD-71: amend cycle start or reroute inside it
    if (
        from_report
        and stage.startswith("p")
        and is_phase_locked(task_data, phase)
    ):
        if is_amend_cycle_open(task_data):
            row_id = "T-AMD-71"
        else:
            row_id = "T-AMD-70"
            route = task_data.get("route")
            if route and route.get("writer") == "dev" and route.get("cause") in ("test", "contract"):
                task_data["cycle"] = {
                    "phase": phase,
                    "cause": route["cause"],
                    "opened_at": _now_iso(),
                    "opened_by": run_id or "run",
                }
    elif from_report:
        if role == "po":
            row_id = "T-RVT-06"
        elif role == "architect":
            row_id = "T-RVT-07"
        elif role == "qa":
            row_id = "T-RVT-08"

    # Transition to P.<role>
    pos.code = f"P.{role}"
    pos.run = run_id
    pos.fr = from_report
    task_data["position"] = pos.to_dict()

    action = format_next_action(task_data)
    return task_data, action, row_id


def on_enter(task_data: Dict[str, Any], role: str, run_id: str) -> Tuple[Dict[str, Any], str, str]:
    """Execute transition T-RUN-11: enter role -> A.<role>."""
    pos = get_position(task_data)
    pos.code = f"A.{role}"
    pos.run = run_id
    task_data["position"] = pos.to_dict()
    action = format_next_action(task_data)
    return task_data, action, "T-RUN-11"


def on_done(
    task_data: Dict[str, Any],
    role: str,
    verdict: str,
    cause: Optional[str] = None,
    run_id: Optional[str] = None,
    run_fr: bool = False,
    lane_changed: bool = False,
    plan_has_undelivered: Optional[bool] = None,
    is_refactor: bool = False,
    is_next_phase_designed: bool = True,
    repo_root: Optional[Path] = None,
    stall: bool = False,
    stall_reason: Optional[str] = None,
    missing_reqs: bool = False,
    withdrawn_reqs: bool = False,
    invalid_plan: bool = False,
    check_green: bool = True,
    open_blockers: bool = False,
    invalid_findings: bool = False,
    invalid_design: bool = False,
    invalid_contract: bool = False,
    round_num: Optional[int] = None,
    human_reject: bool = False,
    report_text: Optional[str] = None,
    run_folder: Optional[Union[str, Path]] = None,
    no_blockers: bool = False,
    human_finding_withdrawn: bool = False,
    not_a_round: bool = False,
    return_round_row: bool = False,
    **kwargs: Any,
) -> Tuple[Dict[str, Any], str, str]:
    """Execute state transition for persona done verdict.

    Handles:
    - needs-input (T-VER-30, T-VER-31, T-VER-32)
    - complete (T-VER-01..08)
    - approve (T-VER-11..18, T-FIN-01)
    - changes (T-VER-20..25)
    - blocked (T-VER-40..50)
    - no-change (T-VER-60..77)
    - issues (T-FIN-01, T-FIN-02)
    """
    if not_a_round or return_round_row or kwargs.get("not_a_round"):
        return task_data, format_next_action(task_data, repo_root=repo_root), "T-VER-25"
    if round_num is not None and round_num >= 2:
        return task_data, format_next_action(task_data), "T-STL-01"
    if human_reject:
        return task_data, format_next_action(task_data), "T-STL-10"

    pos = get_position(task_data)
    stage = pos.stage
    phase = pos.phase
    if phase is None and stage.startswith("p") and stage[1:].isdigit():
        phase = int(stage[1:])

    pos.run = None

    # 1. needs-input (T-VER-30, T-VER-31, T-VER-32)
    if verdict == "needs-input":
        if role == "qa":
            raise FlowRefusalError(
                "Refused: QA has no needs-input (T-VER-31); use blocked --cause requirements or environment",
                row="T-VER-31",
            )
        pos.code = f"Q.{role}+FR" if (pos.fr or run_fr) else f"Q.{role}"
        task_data["position"] = pos.to_dict()
        row_id = "T-VER-32" if role == "dev" else "T-VER-30"
        return task_data, format_next_action(task_data), row_id

    # 2. complete (T-VER-01, 04, 05, 07, 08)
    if verdict == "complete":
        pos.fr = False
        if role == "po":
            if missing_reqs:
                raise FlowRefusalError("Refused: requirement is in no phase (T-VER-02)", row="T-VER-02")
            if withdrawn_reqs:
                return task_data, format_next_action(task_data), "T-VER-03"
            if stage == "docs":
                docs_up = _ensure_docs_update(task_data)
                docs_up["po_changed"] = True
            elif task_data.get("revisit"):
                task_data["revisit"]["po_changed"] = True
            pos.code = "R.po"
            task_data["position"] = pos.to_dict()
            return task_data, format_next_action(task_data, repo_root=repo_root), "T-VER-01"

        if role == "architect":
            if invalid_plan:
                raise FlowRefusalError("Refused: plan.md validation failed (T-VER-06)", row="T-VER-06")
            if stage == "docs":
                docs_up = _ensure_docs_update(task_data)
                docs_up["architect_changed"] = True
            # T-VER-05: no undelivered phase left or docs update
            has_undelivered = plan_has_undelivered
            if has_undelivered is None:
                has_undelivered = _find_first_undelivered_phase(task_data) is not None
            row_id = "T-VER-04" if has_undelivered and stage != "docs" else "T-VER-05"
            pos.code = "R.architect"
            task_data["position"] = pos.to_dict()
            return task_data, format_next_action(task_data, repo_root=repo_root), row_id

        if open_blockers:
            raise FlowRefusalError("Refused: open blocker findings remain (T-VER-10)", row="T-VER-10")

        if role == "qa":
            pos.code = "R.qa"
            task_data["position"] = pos.to_dict()
            return task_data, format_next_action(task_data, repo_root=repo_root), "T-VER-07"

        if role == "dev":
            if not check_green:
                raise FlowRefusalError("Refused: check is not green (T-VER-09)", row="T-VER-09")
            pos.code = "R.dev"
            task_data["position"] = pos.to_dict()
            return task_data, format_next_action(task_data, repo_root=repo_root), "T-VER-08"

    # 3. approve (T-VER-11..18, T-FIN-01)
    if verdict == "approve":
        pos.fr = False
        if invalid_findings and role.endswith("-reviewer"):
            raise FlowRefusalError("Refused: invalid findings (T-VER-19)", row="T-VER-19")
        if role == "final-reviewer" and run_folder is not None:
            missing = check_final_reviewer_files(run_folder)
            if missing:
                raise FlowRefusalError(
                    f"Refused: final review missing files: {missing} (T-FIN-53)",
                    row="T-FIN-53",
                )

        if role == "po-reviewer":
            # Docs update
            if stage == "docs":
                docs_up = _ensure_docs_update(task_data)
                docs_up["po_finished"] = True
                if docs_up.get("po_changed") is None:
                    docs_up["po_changed"] = True
                arch_finished = bool(docs_up.get("architect_finished"))
                if arch_finished:
                    pos.code = "H.docs"
                else:
                    pos.code = "W.architect"
                task_data["position"] = pos.to_dict()
                row_id = "T-VER-17"
                return task_data, format_next_action(task_data, repo_root=repo_root), row_id

            # Check if this review was for a PO no-change (T-VER-16)
            revisit = task_data.get("revisit")
            if revisit:
                if revisit.get("po_changed"):
                    pos.code = "H.req"
                    task_data["position"] = pos.to_dict()
                    return task_data, format_next_action(task_data, repo_root=repo_root), "T-VER-11"

                rev_cause = revisit.get("cause")
                if rev_cause == "final-reject":
                    # After final reject, always architect (who plans a fix phase, T-VER-16, 66)
                    pos.code = "W.architect"
                    task_data["position"] = pos.to_dict()
                    return task_data, format_next_action(task_data, repo_root=repo_root), "T-VER-16"

                has_undelivered = plan_has_undelivered
                if has_undelivered is None:
                    has_undelivered = _find_first_undelivered_phase(task_data) is not None

                if has_undelivered:
                    pos.code = "W.architect"
                    task_data["position"] = pos.to_dict()
                    return task_data, format_next_action(task_data, repo_root=repo_root), "T-VER-16"
                else:
                    # No phase left -> straight to final review (T-VER-16)
                    set_position(task_data, stage="final", code="W.final-reviewer", phase=None, fr=False)
                    return task_data, format_next_action(task_data, repo_root=repo_root), "T-VER-16"

            # Normal PO approve outside docs update (T-VER-11)
            pos.code = "H.req"
            task_data["position"] = pos.to_dict()
            return task_data, format_next_action(task_data, repo_root=repo_root), "T-VER-11"

        if role == "architect-reviewer":
            if stage == "docs":
                docs_up = _ensure_docs_update(task_data)
                docs_up["architect_finished"] = True
                if docs_up.get("architect_changed") is None:
                    docs_up["architect_changed"] = True
                pos.code = "H.docs"
                task_data["position"] = pos.to_dict()
                return task_data, format_next_action(task_data, repo_root=repo_root), "T-VER-18"

            pos.code = "H.plan"
            task_data["position"] = pos.to_dict()
            return task_data, format_next_action(task_data, repo_root=repo_root), "T-VER-12"

        if role == "qa-reviewer":
            if is_amend_cycle_open(task_data):
                pos.code = "H.amend"
                task_data["position"] = pos.to_dict()
                return task_data, format_next_action(task_data), "T-VER-14"
            else:
                pos.code = "H.lock"
                task_data["position"] = pos.to_dict()
                return task_data, format_next_action(task_data), "T-VER-13"

        if role == "dev-reviewer":
            pos.code = "O.deliver"
            task_data["position"] = pos.to_dict()
            return task_data, format_next_action(task_data), "T-VER-15"

        if role == "final-reviewer":
            pos.code = "H.final"
            task_data["position"] = pos.to_dict()
            return task_data, format_next_action(task_data), "T-FIN-01"

    # 4. changes (T-VER-20, T-STL-02, T-STL-03)
    if verdict == "changes":
        pos.fr = False
        if invalid_findings and role.endswith("-reviewer"):
            raise FlowRefusalError("Refused: invalid findings (T-VER-24)", row="T-VER-24")
        if no_blockers or kwargs.get("no_blockers"):
            raise FlowRefusalError("Refused: changes requires at least one blocker or major finding (T-VER-21)", row="T-VER-21")
        if human_finding_withdrawn or kwargs.get("human_finding_withdrawn"):
            raise FlowRefusalError("Refused: reviewer cannot withdraw human finding (T-VER-22)", row="T-VER-22")
        writer = role[:-9] if role.endswith("-reviewer") else role
        if stall or stall_reason:
            reason = stall_reason or "no-progress"
            
            arbiter_rulings_in_phase = 0
            if repo_root is not None:
                try:
                    from lintel import loops
                    state_dir = repo_root / ".lintel" / "state"
                    loops_data = loops.load_loops(state_dir, task_data["id"])
                    loop_key = loops.get_loop_key(writer, stage)
                    history = loops_data.get("loops", {}).get(loop_key, {}).get("history", [])
                    arbiter_rulings_in_phase = sum(1 for h in history if h.get("event") == "ruling")
                except Exception:
                    pass

            if arbiter_rulings_in_phase >= 1:
                pos.code = "D.stall"
                row_id = "T-STL-08"
            else:
                pos.code = "W.arbiter"
                if reason == "no-progress":
                    row_id = "T-STL-02"
                elif reason == "stall-m1":
                    row_id = "T-STL-04"
                else:
                    row_id = "T-STL-03"

            task_data["position"] = pos.to_dict()
            task_data["escalation"] = {
                "kind": "stall",
                "stage": stage,
                "writer": writer,
                "cause": None,
                "reason": reason,
                "run": run_id,
                "raised_at": _now_iso(),
            }
            return task_data, format_next_action(task_data, repo_root=repo_root), row_id
        pos.code = f"W.{writer}"
        task_data["position"] = pos.to_dict()
        return task_data, format_next_action(task_data), "T-VER-20"

    # 5. ruled (arbiter, T-STL-07, T-STL-08)
    if verdict == "ruled" and role == "arbiter":
        writer = (task_data.get("escalation") or {}).get("writer") or "dev"
        has_open = False
        has_human = False
        if repo_root is not None:
            try:
                from lintel import loops
                state_dir = repo_root / ".lintel" / "state"
                loops_data = loops.load_loops(state_dir, task_data["id"])
                loop_key = loops.get_loop_key(writer, stage)
                for f in loops_data.get("loops", {}).get(loop_key, {}).get("findings", []):
                    if f.get("status") in ("open", "disputed"):
                        has_open = True
                        if f.get("arbiter_ruled") == "human":
                            has_human = True
            except Exception:
                has_open = True

        pos.fr = False
        if has_human:
            pos.code = "D.stall"
            row_id = "T-STL-08"
        elif has_open:
            pos.code = f"W.{writer}"
            row_id = "T-STL-07"
        else:
            pos.code = f"R.{writer}"
            row_id = "T-STL-07"
        
        task_data["position"] = pos.to_dict()
        return task_data, format_next_action(task_data, repo_root=repo_root), row_id

    # 6. issues (final reviewer, T-FIN-01, T-FIN-02)
    if verdict == "issues" and role == "final-reviewer":
        if run_folder is not None:
            missing = check_final_reviewer_files(run_folder)
            if missing:
                raise FlowRefusalError(
                    f"Refused: final review missing files: {missing} (T-FIN-53)",
                    row="T-FIN-53",
                )
        pos.fr = False
        pos.code = "H.final"
        task_data["position"] = pos.to_dict()
        return task_data, format_next_action(task_data), "T-FIN-02"

    # 6. blocked (T-VER-40..50)
    if verdict == "blocked":
        if not cause:
            raise FlowRefusalError(
                f"Refused: blocked verdict requires --cause (T-RUN-17)",
                row="T-RUN-17",
            )
        allowed_causes = ROLE_ALLOWED_BLOCKED_CAUSES.get(role, set())
        if cause not in allowed_causes:
            raise FlowRefusalError(
                f"Refused: cause '{cause}' not allowed for role '{role}' (T-VER-51)",
                row="T-VER-51",
            )
        if report_text is not None:
            ok, heading = check_blocked_report_section(report_text, cause)
            if not ok:
                raise FlowRefusalError(
                    f"Refused: report.md missing section for '{cause}' (T-RUN-21)",
                    row="T-RUN-21",
                )

        # Check second identical blocked memory (T-VER-50)
        # memory holds tuples of (writer, cause, phase/stage)
        memory = task_data.get("memory", [])
        stage_or_phase = f"p{phase}" if phase is not None else stage
        for entry in memory:
            if (
                entry.get("writer") == role
                and entry.get("cause") == cause
                and (entry.get("stage") == stage_or_phase or entry.get("phase") == phase)
            ):
                pos.code = "D.repeat-blocked"
                pos.fr = False
                task_data["position"] = pos.to_dict()
                task_data["escalation"] = {
                    "kind": "repeat-blocked",
                    "stage": stage,
                    "writer": role,
                    "cause": cause,
                    "reason": cause,
                    "run": run_id,
                    "raised_at": _now_iso(),
                }
                return task_data, format_next_action(task_data), "T-VER-50"

        # Record route
        task_data["route"] = {
            "writer": role,
            "cause": cause,
            "stage": stage,
            "from_run": run_id,
        }

        # Environment cause (T-VER-49)
        if cause == "environment":
            pos.code = "D.environment"
            pos.fr = False
            task_data["position"] = pos.to_dict()
            task_data["escalation"] = {
                "kind": "environment",
                "stage": stage,
                "writer": role,
                "cause": cause,
                "reason": cause,
                "run": run_id,
                "raised_at": _now_iso(),
            }
            return task_data, format_next_action(task_data), "T-VER-49"

        # Requirements cause
        if cause == "requirements":
            # In docs update (T-VER-45)
            if stage == "docs":
                pos.code = "D.requirements"
                pos.fr = False
                task_data["position"] = pos.to_dict()
                task_data["escalation"] = {
                    "kind": "requirements",
                    "stage": "docs",
                    "writer": "architect",
                    "cause": cause,
                    "reason": cause,
                    "run": run_id,
                    "raised_at": _now_iso(),
                }
                return task_data, format_next_action(task_data), "T-VER-45"

            # Locked phase (QA, DEV, Architect after lock, T-VER-42..44)
            if is_phase_locked(task_data, phase):
                pos.code = "D.requirements"
                pos.fr = False
                task_data["position"] = pos.to_dict()
                task_data["escalation"] = {
                    "kind": "requirements",
                    "stage": stage,
                    "writer": role,
                    "cause": cause,
                    "reason": cause,
                    "run": run_id,
                    "raised_at": _now_iso(),
                }
                row_id = "T-VER-43" if role == "dev" else ("T-VER-42" if role == "qa" else "T-VER-44")
                return task_data, format_next_action(task_data), row_id

            # Before lock
            if role == "architect":
                pos.code = "W.po+FR"
                pos.fr = True
                task_data["position"] = pos.to_dict()
                return task_data, format_next_action(task_data), "T-VER-40"

            if role == "qa":
                pos.code = "W.po+FR"
                pos.fr = True
                task_data["position"] = pos.to_dict()
                return task_data, format_next_action(task_data), "T-VER-41"

        # Contract cause (T-VER-46, T-VER-47)
        if cause == "contract":
            pos.code = "W.architect+FR"
            pos.fr = True
            task_data["position"] = pos.to_dict()
            row_id = "T-VER-47" if role == "dev" else "T-VER-46"
            return task_data, format_next_action(task_data), row_id

        # Test cause (T-VER-48)
        if cause == "test":
            pos.code = "W.qa+FR"
            pos.fr = True
            task_data["position"] = pos.to_dict()
            return task_data, format_next_action(task_data), "T-VER-48"

    # 7. no-change (T-VER-60..77)
    if verdict == "no-change":
        if role not in ("po", "architect"):
            raise FlowRefusalError(
                "Refused: verdict 'no-change' only allowed for po and architect (T-VER-60)",
                row="T-VER-60",
            )
        # Check T-VER-77: architect was blocked writer in r0 or r<N> reroute
        route = task_data.get("route")
        if role == "architect" and route and route.get("writer") == "architect" and stage.startswith("r"):
            raise FlowRefusalError(
                "Refused: architect no-change not allowed in r0/r<N> reroute; only complete is allowed (T-VER-77)",
                row="T-VER-77",
            )
        if stage == "r0":
            raise FlowRefusalError(
                "Refused: no-change is not allowed in initial r0 (T-VER-62)",
                row="T-VER-62",
            )
        if lane_changed:
            raise FlowRefusalError(
                "Refused: writer's lane content changed; use 'complete' instead (T-VER-61)",
                row="T-VER-61",
            )

        # PO no-change routing (T-VER-63..69)
        if role == "po":
            # Reroute run (invoked with --from-report, T-VER-67)
            if run_fr or pos.fr or task_data.get("route"):
                pos.code = "D.po-no-change"
                pos.fr = False
                task_data["position"] = pos.to_dict()
                blocked_w = (task_data.get("route") or {}).get("writer")
                task_data["escalation"] = {
                    "kind": "po-no-change",
                    "stage": stage,
                    "writer": blocked_w,
                    "cause": "requirements",
                    "reason": "po-no-change",
                    "run": run_id,
                    "raised_at": _now_iso(),
                }
                return task_data, format_next_action(task_data), "T-VER-67"

            # Docs update (T-VER-68, T-VER-69)
            if stage == "docs":
                docs_up = _ensure_docs_update(task_data)
                docs_up["po_finished"] = True
                docs_up["po_changed"] = False

                if is_refactor:
                    # Refactor: skips reviewer (T-VER-69)
                    pos.code = "W.architect"
                    pos.fr = False
                    task_data["position"] = pos.to_dict()
                    return task_data, format_next_action(task_data), "T-VER-69"
                else:
                    # PO reviewer checks it (T-VER-68)
                    pos.code = "R.po"
                    pos.fr = False
                    task_data["position"] = pos.to_dict()
                    return task_data, format_next_action(task_data), "T-VER-68"

            # Revisit at r<N>
            revisit = task_data.get("revisit") or {}
            rev_cause = revisit.get("cause")

            # Final-reject revisit: PO reviewer checks it (T-VER-66)
            if rev_cause == "final-reject":
                pos.code = "R.po"
                pos.fr = False
                task_data["position"] = pos.to_dict()
                return task_data, format_next_action(task_data), "T-VER-66"

            # Next-phase revisit: check deferred problem
            deferred_for_revisit = [
                d for d in task_data.get("deferred", [])
                if d.get("revisit_stage") == stage or (d.get("revisit_stage") is None and not d.get("in_docs"))
            ]
            if deferred_for_revisit:
                # PO reviewer checks it (T-VER-65)
                pos.code = "R.po"
                pos.fr = False
                task_data["position"] = pos.to_dict()
                return task_data, format_next_action(task_data), "T-VER-65"

            # No deferred problem
            has_undelivered = plan_has_undelivered
            if has_undelivered is None:
                has_undelivered = _find_first_undelivered_phase(task_data) is not None

            if has_undelivered:
                # Skips reviewer and approval -> architect (T-VER-63)
                pos.code = "W.architect"
                pos.fr = False
                task_data["position"] = pos.to_dict()
                return task_data, format_next_action(task_data), "T-VER-63"
            else:
                # Skips reviewer, approval, and architect -> final-reviewer (T-VER-64)
                set_position(task_data, stage="final", code="W.final-reviewer", phase=None, fr=False)
                return task_data, format_next_action(task_data), "T-VER-64"

        # Architect no-change routing (T-VER-70..77)
        if role == "architect":
            if invalid_design:
                raise FlowRefusalError("Refused: invalid design.md (T-VER-75)", row="T-VER-75")
            if invalid_contract:
                raise FlowRefusalError("Refused: invalid contract.md (T-VER-76)", row="T-VER-76")
            # Docs update (T-VER-74)
            if stage == "docs":
                docs_up = _ensure_docs_update(task_data)
                docs_up["architect_finished"] = True
                docs_up["architect_changed"] = False

                po_changed = bool(docs_up.get("po_changed", False))
                if po_changed:
                    # PO pair changed specs -> approve docs
                    pos.code = "H.docs"
                    pos.fr = False
                    task_data["position"] = pos.to_dict()
                    return task_data, format_next_action(task_data, repo_root=repo_root), "T-VER-74"
                else:
                    # Neither changed anything -> approve docs skipped, straight to finish!
                    pos.code = "H.finish"
                    pos.fr = False
                    task_data["position"] = pos.to_dict()
                    docs_up["outcome"] = "skipped"
                    return task_data, format_next_action(task_data, repo_root=repo_root), "T-VER-74"

            # Reroute inside p<N> (QA or DEV cause, T-VER-73)
            if stage.startswith("p") and (run_fr or pos.fr or route):
                cause_code = route.get("cause") if route else None
                blocked_w = route.get("writer") if route else "dev"
                if cause_code == "contract":
                    # For a contract cause, memory gains (blocked_writer, contract, phase)
                    mem = task_data.setdefault("memory", [])
                    mem.append({
                        "writer": blocked_w,
                        "cause": "contract",
                        "stage": stage,
                        "phase": phase,
                    })
                pos.code = "W.qa+FR"
                pos.fr = True
                task_data["position"] = pos.to_dict()
                return task_data, format_next_action(task_data), "T-VER-73"

            # Revisit at r<N>
            revisit = task_data.get("revisit") or {}
            rev_cause = revisit.get("cause")

            if rev_cause == "final-reject":
                # No fix phase to plan -> D.final-no-change (T-VER-72)
                pos.code = "D.final-no-change"
                pos.fr = False
                task_data["position"] = pos.to_dict()
                task_data["escalation"] = {
                    "kind": "final-no-change",
                    "stage": stage,
                    "writer": None,
                    "cause": None,
                    "reason": "final-no-change",
                    "run": run_id,
                    "raised_at": _now_iso(),
                }
                return task_data, format_next_action(task_data), "T-VER-72"

            # Next-phase revisit: check undelivered phases
            has_undelivered = plan_has_undelivered
            if has_undelivered is None:
                has_undelivered = _find_first_undelivered_phase(task_data) is not None

            if not has_undelivered:
                # No undelivered phase -> final review (T-VER-70)
                set_position(task_data, stage="final", code="W.final-reviewer", phase=None, fr=False)
                return task_data, format_next_action(task_data), "T-VER-70"
            else:
                # Next phase has approved design & contract -> p<M>:W.qa (T-VER-71)
                first_undelivered = _find_first_undelivered_phase(task_data)
                m = first_undelivered or (phase + 1 if phase else 1)
                set_position(task_data, stage=f"p{m}", code="W.qa", phase=m, fr=False)
                return task_data, format_next_action(task_data), "T-VER-71"

    raise FlowRefusalError(f"Refused: unhandled verdict '{verdict}' for role '{role}'")


def on_answer(
    task_data: Dict[str, Any],
    role: str,
    all_answered: bool,
) -> Tuple[Dict[str, Any], str, str]:
    """Execute transition T-RUN-19 on answer."""
    pos = get_position(task_data)
    stage = pos.stage
    fr = pos.fr

    if not all_answered:
        pos.code = f"Q.{role}+FR" if fr else f"Q.{role}"
        task_data["position"] = pos.to_dict()
        return task_data, format_next_action(task_data), "T-RUN-19"

    pos.code = f"W.{role}+FR" if fr else f"W.{role}"
    task_data["position"] = pos.to_dict()
    return task_data, format_next_action(task_data), "T-RUN-19"


def on_approve_requirements(
    task_data: Dict[str, Any],
    reject_reason: Optional[str] = None,
) -> Tuple[Dict[str, Any], str, str]:
    """Execute transitions T-HUM-10..12, 15 for approve requirements."""
    pos = get_position(task_data)
    stage = pos.stage

    if reject_reason:
        pos.code = "W.po"
        pos.fr = False
        task_data["position"] = pos.to_dict()
        return task_data, format_next_action(task_data), "T-HUM-15"

    pos.code = "W.architect"
    pos.fr = False
    task_data["position"] = pos.to_dict()

    row_id = "T-HUM-10" if stage == "r0" else ("T-HUM-11" if stage.startswith("r") else "T-HUM-12")
    return task_data, format_next_action(task_data), row_id


def on_approve_plan(
    task_data: Dict[str, Any],
    phase: Optional[int] = None,
    reject_reason: Optional[str] = None,
    design_already_approved: bool = False,
    no_undelivered_left: bool = False,
    commit_sha: Optional[str] = None,
) -> Tuple[Dict[str, Any], str, str]:
    """Execute transitions T-ARC-01..03, 08 for approve plan."""
    pos = get_position(task_data)
    stage = pos.stage

    if reject_reason:
        pos.code = "W.architect"
        pos.fr = False
        task_data["position"] = pos.to_dict()
        return task_data, format_next_action(task_data), "T-ARC-08"

    if no_undelivered_left:
        set_position(task_data, stage="final", code="W.final-reviewer", phase=None, fr=False)
        return task_data, format_next_action(task_data), "T-ARC-03"

    p_num = phase or _find_first_undelivered_phase(task_data) or 1
    next_stage = f"p{p_num}"

    # Track design commit in phase
    phases = task_data.setdefault("phases", {})
    p_info = phases.setdefault(str(p_num), {})
    des_commits = p_info.setdefault("design_commits", [])
    already_approved = (
        design_already_approved
        or bool(des_commits)
        or is_phase_locked(task_data, p_num)
        or is_phase_delivered(task_data, p_num)
    )
    kind = "revise" if already_approved else "design"
    sha_val = commit_sha or f"dummy-sha-{kind}"
    des_commits.append({"sha": sha_val, "kind": kind, "at": _now_iso()})

    if kind == "revise":
        set_position(task_data, stage=next_stage, code="W.qa+FR", phase=p_num, fr=True)
        return task_data, format_next_action(task_data), "T-ARC-02"
    else:
        set_position(task_data, stage=next_stage, code="W.qa", phase=p_num, fr=False)
        return task_data, format_next_action(task_data), "T-ARC-01"


def on_lock(
    task_data: Dict[str, Any],
    phase: Optional[int] = None,
    reject_reason: Optional[str] = None,
) -> Tuple[Dict[str, Any], str, str]:
    """Execute transitions T-AMD-01, T-AMD-10 for lock."""
    pos = get_position(task_data)
    stage = pos.stage
    p_num = phase or pos.phase or (int(stage[1:]) if stage.startswith("p") else 1)

    if reject_reason:
        pos.code = "W.qa"
        pos.fr = False
        task_data["position"] = pos.to_dict()
        return task_data, format_next_action(task_data), "T-AMD-10"

    phases = task_data.setdefault("phases", {})
    p_info = phases.setdefault(str(p_num), {})
    p_info["lock"] = {
        "commit": f"lock-commit-phase-{p_num}",
        "at": _now_iso(),
        "allow_green": [],
        "groups": {},
    }
    p_info.setdefault("locked_files", [])
    p_info["dev_window_since"] = _now_iso()

    set_position(task_data, stage=f"p{p_num}", code="W.dev", phase=p_num, fr=False)
    return task_data, format_next_action(task_data), "T-AMD-01"


def on_amend(
    task_data: Dict[str, Any],
    phase: Optional[int] = None,
    tests_stand: bool = False,
    reject_reason: Optional[str] = None,
) -> Tuple[Dict[str, Any], str, str]:
    """Execute transitions T-AMD-73, 74, 78 for amend."""
    pos = get_position(task_data)
    stage = pos.stage
    p_num = phase or pos.phase or (int(stage[1:]) if stage.startswith("p") else 1)

    if reject_reason:
        pos.code = "W.qa"
        pos.fr = False
        task_data["position"] = pos.to_dict()
        # cycle stays open (T-AMD-78)
        return task_data, format_next_action(task_data), "T-AMD-78"

    # Read cycle cause before closing
    cause = (task_data.get("cycle") or {}).get("cause") or (task_data.get("route") or {}).get("cause") or "test"

    # Close amend cycle
    task_data["cycle"] = None

    if tests_stand:
        # Record that tests stand, for test cause memory gains (dev, test, phase) (T-AMD-74)
        if cause == "test":
            mem = task_data.setdefault("memory", [])
            entry = {
                "writer": "dev",
                "cause": "test",
                "stage": stage,
                "phase": p_num,
            }
            if entry not in mem:
                mem.append(entry)
        set_position(task_data, stage=f"p{p_num}", code="W.dev", phase=p_num, fr=False)
        return task_data, format_next_action(task_data), "T-AMD-74"

    # Amend with test changes (T-AMD-73)
    set_position(task_data, stage=f"p{p_num}", code="W.dev", phase=p_num, fr=False)
    return task_data, format_next_action(task_data), "T-AMD-73"


def on_deliver(
    task_data: Dict[str, Any],
    phase: Optional[int] = None,
    outcome: str = "green",
    return_revisit: bool = False,
) -> Tuple[Dict[str, Any], str, str]:
    """Execute transitions T-AMD-30, 32, 37 for deliver."""
    pos = get_position(task_data)
    stage = pos.stage
    p_num = phase or pos.phase or (int(stage[1:]) if stage.startswith("p") else 1)

    if outcome == "red":
        set_position(task_data, stage=f"p{p_num}", code="W.dev", phase=p_num, fr=False)
        return task_data, format_next_action(task_data), "T-AMD-37"

    if outcome in ("lane", "drift", "audit"):
        set_position(task_data, stage=f"p{p_num}", code="W.dev", phase=p_num, fr=False)
        return task_data, format_next_action(task_data), "T-AMD-32"

    # Green delivery (T-AMD-30, T-RVT-01)
    phases = task_data.setdefault("phases", {})
    p_info = phases.setdefault(str(p_num), {})
    p_info["delivered"] = {
        "commit": f"feat-commit-phase-{p_num}",
        "at": _now_iso(),
        "groups": {},
        "drift_count": 0,
        "audit_count": 0,
    }

    deliv_list = task_data.setdefault("delivered_phases", [])
    if p_num not in deliv_list:
        deliv_list.append(p_num)

    # Revisit setup (T-RVT-01)
    task_data["revisit"] = {
        "cause": "next-phase",
        "phase": p_num,
        "stage": f"r{p_num}",
    }

    # Bind deferred requirement problems to this revisit (T-RVT-04)
    for d in task_data.get("deferred", []):
        if d.get("revisit_stage") is None and not d.get("in_docs"):
            d["revisit_stage"] = f"r{p_num}"

    set_position(task_data, stage=f"r{p_num}", code="W.po", phase=p_num, fr=False)
    row_id = "T-RVT-01" if return_revisit else "T-AMD-30"
    return task_data, format_next_action(task_data), row_id


def on_approve_final(
    task_data: Dict[str, Any],
    reject_reason: Optional[str] = None,
    return_revisit: bool = False,
) -> Tuple[Dict[str, Any], str, str]:
    """Execute transitions T-FIN-03, T-FIN-05 (T-RVT-02) for approve final."""
    if reject_reason is not None:
        # Revisit after final reject (T-FIN-05, T-RVT-02)
        delivered = task_data.get("delivered_phases", [])
        last_delivered = delivered[-1] if delivered else 1
        task_data["revisit"] = {
            "cause": "final-reject",
            "phase": last_delivered,
            "stage": f"r{last_delivered}",
        }
        set_position(task_data, stage=f"r{last_delivered}", code="W.po", phase=last_delivered, fr=False)
        row_id = "T-RVT-02" if return_revisit else "T-FIN-05"
        return task_data, format_next_action(task_data), row_id

    # Start docs update (T-FIN-03)
    task_data["revisit"] = None
    task_data["docs_update"] = {
        "po_finished": False,
        "architect_finished": False,
        "po_changed": None,
        "architect_changed": None,
        "outcome": None,
    }
    set_position(task_data, stage="docs", code="W.po", phase=None, fr=False)
    return task_data, format_next_action(task_data), "T-FIN-03"


def on_approve_docs(
    task_data: Dict[str, Any],
    reject_pair: Optional[str] = None,
) -> Tuple[Dict[str, Any], str, str]:
    """Execute transitions T-FIN-10, T-FIN-12 for approve docs."""
    docs_up = _ensure_docs_update(task_data)
    if reject_pair:
        target_role = "po" if reject_pair == "po" else "architect"
        if target_role == "po":
            docs_up["po_finished"] = False
        else:
            docs_up["architect_finished"] = False
        set_position(task_data, stage="docs", code=f"W.{target_role}", phase=None, fr=False)
        return task_data, format_next_action(task_data), "T-FIN-12"

    docs_up = _ensure_docs_update(task_data)
    docs_up["outcome"] = "approved"

    set_position(task_data, stage="docs", code="H.finish", phase=None, fr=False)
    return task_data, format_next_action(task_data), "T-FIN-10"


def on_task_finish(task_data: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str]:
    """Execute transition T-FIN-20: task finish -> NONE."""
    task_data["status"] = "finished"
    task_data["finished_at"] = _now_iso()
    set_position(task_data, stage="NONE", code="NONE", phase=None, fr=False, run=None)
    action = format_next_action(task_data)
    return task_data, action, "T-FIN-20"


def on_task_abort(task_data: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str]:
    """Execute transition T-FIN-30: task abort -> NONE."""
    task_data["status"] = "aborted"
    task_data["cycle"] = None
    set_position(task_data, stage="NONE", code="NONE", phase=None, fr=False, run=None)
    action = format_next_action(task_data)
    return task_data, action, "T-FIN-30"



def on_audit_reject(
    task_data: Dict[str, Any],
    target_pair: str,
) -> Tuple[Dict[str, Any], str, str]:
    """Execute transition T-HUM-32 for audit --reject."""
    pos = get_position(task_data)
    
    # State goes to W.<w> of the target pair
    pos.code = f"W.{target_pair}"
    pos.fr = False
    pos.run = None
    set_position(task_data, pos.stage, pos.code, pos.phase, pos.fr, pos.run)
    action = format_next_action(task_data)
    return task_data, action, "T-HUM-32"


def on_reset(
    task_data: Dict[str, Any],
    phase: Optional[int] = None,
    repo_root: Optional[Union[str, Path]] = None,
    loops_data: Optional[Dict[str, Any]] = None,
    return_cycle_end: bool = False,
) -> Tuple[Dict[str, Any], str, str]:
    """Execute transition T-AMD-60 / T-AMD-79: reset --phase N -> p<N>:W.dev."""
    pos = get_position(task_data)
    p_num = phase or pos.phase or (int(pos.stage[1:]) if pos.stage.startswith("p") else 1)

    task_data["cycle"] = None
    task_data["memory"] = []
    task_data["escalation"] = None
    task_data.setdefault("phases", {}).setdefault(str(p_num), {})["dev_window_since"] = _now_iso()

    if loops_data is not None:
        loop_key = f"dev.p{p_num}"
        if loop_key in loops_data.get("loops", {}):
            lp = loops_data["loops"][loop_key]
            lp["since_decision"] = 0
            lp["no_progress"] = 0
            lp["stalled"] = None

    if repo_root is not None and "id" in task_data:
        try:
            from lintel import loops
            loops.reset_stall_counters(
                state_dir=Path(repo_root) / ".lintel" / "state",
                task_id=task_data["id"],
                writer="dev",
                stage=f"p{p_num}",
            )
        except Exception:
            pass

    set_position(task_data, stage=f"p{p_num}", code="W.dev", phase=p_num, fr=False, run=None)
    row_id = "T-AMD-79" if return_cycle_end else "T-AMD-60"
    return task_data, format_next_action(task_data), row_id


def on_restore_tests(
    task_data: Dict[str, Any],
    changed_lane: Optional[str] = None,
) -> Tuple[Dict[str, Any], str, str]:
    """Execute transitions T-AMD-40, T-AMD-41, T-AMD-42 for restore-tests."""
    pos = get_position(task_data)
    stage = pos.stage

    if is_dev_turn(task_data):
        # Inside DEV's turn: DEV runs again if no run active (T-AMD-40)
        if pos.run is None:
            pos.code = "W.dev"
            task_data["position"] = pos.to_dict()
        return task_data, format_next_action(task_data), "T-AMD-40"

    # Outside DEV's turn
    if changed_lane:
        # Changed the pair's lane -> W.<x> runs again (T-AMD-41)
        pos.code = f"W.{changed_lane}"
        task_data["position"] = pos.to_dict()
        return task_data, format_next_action(task_data), "T-AMD-41"

    # Changed nothing in pair's lane -> state unchanged (T-AMD-42)
    return task_data, format_next_action(task_data), "T-AMD-42"


def on_decide(
    task_data: Dict[str, Any],
    option: str,
    guidance: Optional[str] = None,
    stage: Optional[str] = None,
    repo_root: Optional[Union[str, Path]] = None,
    loops_data: Optional[Dict[str, Any]] = None,
    return_revisit: bool = False,
    return_stl: bool = False,
    **kwargs: Any,
) -> Tuple[Dict[str, Any], str, str]:
    """Execute human decide transitions T-STL-20..37."""
    pos = get_position(task_data)
    curr_stage = stage or pos.stage
    clean_code = pos.code.replace("+FR", "")
    esc = task_data.get("escalation") or {}
    kind = esc.get("kind", "")
    writer = esc.get("writer") or "dev"

    # Clear memory and escalation per T-STL-37
    task_data["memory"] = []
    task_data["escalation"] = None

    if loops_data is not None:
        loop_key = f"{writer}.{curr_stage}"
        if loop_key in loops_data.get("loops", {}):
            lp = loops_data["loops"][loop_key]
            lp["since_decision"] = 0
            lp["no_progress"] = 0
            lp["stalled"] = None

    if repo_root is not None and "id" in task_data:
        try:
            from lintel import loops
            loops.reset_stall_counters(
                state_dir=Path(repo_root) / ".lintel" / "state",
                task_id=task_data["id"],
                writer=writer,
                stage=curr_stage,
            )
        except Exception:
            pass

    opt = option.lstrip("-").replace("-", "_")

    if opt == "abort":
        t, act, _ = on_task_abort(task_data)
        return t, act, "T-STL-36"

    if opt == "skip_docs":
        # T-STL-35: in docs update, skip docs update
        if curr_stage != "docs":
            raise FlowRefusalError("Refused: --skip-docs only allowed in docs update (T-STL-35)", row="T-STL-35")
        set_position(task_data, stage="docs", code="H.finish", phase=None, fr=False)
        docs_up = _ensure_docs_update(task_data)
        docs_up["outcome"] = "skipped"
        return task_data, format_next_action(task_data), "T-STL-35"

    if opt == "to_final":
        set_position(task_data, stage="final", code="W.final-reviewer", phase=None, fr=False)
        return task_data, format_next_action(task_data), "T-STL-33"

    if opt == "to_docs":
        task_data["docs_update"] = {
            "po_finished": False,
            "architect_finished": False,
            "po_changed": None,
            "architect_changed": None,
            "outcome": None,
        }
        set_position(task_data, stage="docs", code="W.po", phase=None, fr=False)
        return task_data, format_next_action(task_data), "T-STL-34"

    if opt == "retry":
        pos.code = f"W.{writer}"
        pos.fr = False
        task_data["position"] = pos.to_dict()
        return task_data, format_next_action(task_data), "T-STL-31"

    if opt == "reset":
        p_num = pos.phase or (int(curr_stage[1:]) if curr_stage.startswith("p") else 1)
        t, act, _ = on_reset(task_data, phase=p_num)
        return t, act, "T-STL-32" if (return_stl or kwargs.get("return_stl")) else "T-AMD-60"

    if opt == "accept":
        # T-STL-30: valid review recorded, advances to approval state
        if curr_stage == "docs":
            if writer == "architect":
                target_code = "H.docs"
            elif writer == "po":
                if task_data.get("docs_update", {}).get("architect_finished"):
                    target_code = "H.docs"
                else:
                    target_code = "W.architect"
            else:
                target_code = "H.docs"
        else:
            target_code = "H.req"
            if writer == "architect":
                target_code = "H.plan"
            elif writer == "qa":
                target_code = "H.amend" if is_amend_cycle_open(task_data) else "H.lock"
            elif writer == "dev":
                target_code = "O.deliver"
        pos.code = target_code
        pos.fr = False
        task_data["position"] = pos.to_dict()
        if repo_root is not None and "id" in task_data:
            try:
                from lintel import reviews
                phase_num = pos.phase
                if phase_num is None and curr_stage.startswith("p") and curr_stage[1:].isdigit():
                    phase_num = int(curr_stage[1:])
                reviews.record_review(
                    repo_root=repo_root,
                    task_id=task_data["id"],
                    pair=writer,
                    stage=curr_stage,
                    phase=phase_num,
                    verdict="accept",
                    kind="accept",
                    task_data=task_data,
                )
            except Exception:
                pass
        return task_data, format_next_action(task_data, repo_root=repo_root), "T-STL-30"

    if opt == "guidance":
        # Defers requirement problem if from D.requirements (T-RVT-03, T-RVT-05, T-STL-25)
        if kind == "requirements":
            route = task_data.get("route") or {}
            blocked_w = route.get("writer") or writer
            run_id = route.get("from_run") or "run"
            in_docs = (curr_stage == "docs")

            dp_list = task_data.setdefault("deferred", [])
            dp_id = f"DP{len(dp_list) + 1}"
            dp_list.append({
                "id": dp_id,
                "stage": curr_stage,
                "writer": blocked_w,
                "run": run_id,
                "heading": "Requirements problem",
                "revisit_stage": None,
                "in_docs": in_docs,
            })

            if in_docs:
                set_position(task_data, stage="docs", code="W.architect", phase=None, fr=False)
                row_id = "T-STL-25" if (return_stl or kwargs.get("return_stl")) else "T-RVT-05"
                return task_data, format_next_action(task_data), row_id

            pos.code = f"W.{blocked_w}"
            pos.fr = False
            task_data["position"] = pos.to_dict()
            row_id = "T-STL-25" if (return_stl or kwargs.get("return_stl")) else "T-RVT-03"
            return task_data, format_next_action(task_data), row_id

        if kind == "po-no-change":
            # T-STL-26: guidance goes to blocked writer
            route = task_data.get("route") or {}
            blocked_w = route.get("writer") or writer
            pos.code = f"W.{blocked_w}"
            pos.fr = False
            task_data["position"] = pos.to_dict()
            return task_data, format_next_action(task_data), "T-STL-26"

        # General guidance (T-STL-24, T-STL-27)
        pos.code = f"W.{writer}"
        pos.fr = False
        task_data["position"] = pos.to_dict()
        row_id = "T-STL-27" if kind == "environment" else "T-STL-24"
        return task_data, format_next_action(task_data), row_id

    raise FlowRefusalError(f"Refused: unknown decide option '{option}' (T-STL-22)", row="T-STL-22")


def on_unstick(
    task_data: Dict[str, Any],
    force: bool = False,
    is_live: bool = False,
    is_human: bool = True,
    from_state_code: Optional[str] = None,
    from_state_fr: bool = False,
) -> Tuple[Dict[str, Any], str, str]:
    """Execute transition T-FIN-40 / T-FIN-52 for task unstick."""
    pos = get_position(task_data)

    if not pos.run and not (pos.code.startswith("P.") or pos.code.startswith("A.") or pos.code == "D.stall"):
        raise FlowRefusalError("Refused: no run is pending or active (T-FIN-41)", row="T-FIN-41")

    if force and not is_human:
        raise FlowRefusalError(
            "Refused: task unstick --force is human-only (T-FIN-54)",
            row="T-FIN-54",
        )

    if is_live and not force:
        raise FlowRefusalError(
            "Refused: run looks live; run 'lintel task wait', or 'human: lintel task unstick --force' when it is known to be stuck (T-FIN-51)",
            row="T-FIN-51",
        )

    # Determine state to return to
    if from_state_code:
        pos.code = from_state_code
        pos.fr = from_state_fr
    else:
        # Infer from P.<role> / A.<role>
        role = pos.code[2:].replace("+FR", "")
        if role.endswith("-reviewer"):
            writer = role[:-9]
            pos.code = f"R.{writer}"
        elif role == "final-reviewer":
            pos.code = "W.final-reviewer"
        elif role == "arbiter":
            pos.code = "W.arbiter"
        else:
            pos.code = f"W.{role}+FR" if pos.fr else f"W.{role}"

    pos.run = None
    task_data["position"] = pos.to_dict()

    row_id = "T-FIN-52" if force else "T-FIN-40"
    action = format_next_action(task_data)
    return task_data, action, row_id


def check_blocked_report_section(report_text: str, cause: str) -> Tuple[bool, Optional[str]]:
    """Check that report.md contains the required section for blocked cause (T-RUN-21).

    Returns (ok, missing_section_or_reason).
    """
    if cause == "environment":
        if not report_text.strip():
            return False, "report.md is empty for cause 'environment'"
        return True, None

    heading = CAUSE_TO_REPORT_HEADING.get(cause)
    if not heading:
        return True, None

    lines = report_text.splitlines()
    pattern = re.compile(rf"^#+\s+{re.escape(heading)}\b", re.IGNORECASE)
    for line in lines:
        if pattern.search(line.strip()):
            return True, None

    return False, heading


def check_final_reviewer_files(run_folder: Union[str, Path]) -> List[str]:
    """Check that final reviewer run folder contains required files (T-FIN-53).

    Returns list of missing file names.
    """
    folder = Path(run_folder)
    req_files = ("review.md", "lessons.md", "final-findings.md")
    missing = [f for f in req_files if not (folder / f).is_file()]
    return missing


def check_task_wait(status: str) -> str:
    """Helper returning the T-FIN row corresponding to a task wait status."""
    if status == "running":
        return "T-FIN-42"
    if status == "verdict":
        return "T-FIN-43"
    if status in ("stopped_failed", "stopped"):
        return "T-FIN-44"
    if status in ("timeout", "ended_without_verdict"):
        return "T-FIN-45"
    if status == "quiet":
        return "T-FIN-46"
    return "T-FIN-42"


def on_task_resume(
    task_data: Dict[str, Any],
    run_state: Optional[str] = None,
    is_live: bool = False,
    no_run: bool = False,
) -> str:
    """Helper returning the T-FIN row corresponding to task resume outcome."""
    if no_run:
        return "T-FIN-50"
    if run_state == "pending":
        return "T-FIN-47"
    if is_live:
        return "T-FIN-48"
    return "T-FIN-49"


