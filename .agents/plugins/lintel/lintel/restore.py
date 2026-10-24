"""Restore and reset commands (M1a-24).

Implements:
1. Shared restore routine:
   - Restores tests paths, locked files that are no longer tests, and approved shared/
     documents to recorded head (task.head).
   - Deletes untracked test-output files outside gitignored paths.
   - Computes count of restored/deleted files and which lanes changed.
2. restore_tests(repo_root: Path):
   - Check if in DEV's turn (locked == True, cycle == False/None, not delivered):
     - Inside DEV's turn (T-AMD-40):
       - Open to anyone (no dialog check H0).
       - Runs restore.
       - Voids DEV's review in reviews.json (voided_at = _now_iso()).
       - If no run active: state becomes p<N>:W.dev. If run active: state unchanged.
       - Prints count and: "then DEV again, which must reach a green check before complete, then the DEV reviewer".
     - Outside DEV's turn:
       - Human-only: requires dialog H0 (check_caller), fails closed on non-ok.
       - Refuse if run pending or active (NR, T-HUM-05).
       - Runs restore.
       - If restore changes something in the lane of the pair the state belongs to
         (PO requirements.md, architect approved plan.md/phase-N/, QA tests):
         - Transitions state to W.<x> of that pair (T-AMD-41), e.g. H.lock -> W.qa.
         - Voids that pair's review (voided_at).
         - Next action: lintel invoke <x> --phase <ph>.
       - If restore changes nothing in that pair's lane, or state is already writer/question/escalation:
         - State unchanged (T-AMD-42).
         - Any pair whose lane was restored has its review voided (D1).
         - Prints count and state's own next action.
3. reset(repo_root: Path, phase: Optional[Union[int, str]] = None):
   - Human-only: requires dialog H0 (check_caller).
   - Refuse if run pending or active (NR, T-HUM-05).
   - Guard: phase must be locked and not delivered (T-AMD-61).
   - Routine (T-AMD-60):
     - Snapshot change set to refs/lintel/<id>/snapshot-<n>.
     - Restore change set paths and locked files to recorded head.
     - Remove untracked non-ignored files in change set.
     - Restore approved shared/ documents by name.
     - Leave ignored files, .lintel/config.jsonc, scripts/, personas/, steering/ alone
       (listing any uncommitted edits).
     - End any amend cycle (cycle = None).
     - Clear DEV pair stall counters (loops.<pair>.since_decision = 0, no_progress = 0, stalled = None)
       and blocked memory for dev.
     - Abandon pending D.* escalation if any.
     - Restart DEV audit window (task.phases.<N>.dev_window_since = _now_iso()).
     - State becomes p<N>:W.dev.
     - Next action: lintel invoke dev --phase p<N>.
"""

import sys
sys.dont_write_bytecode = True

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    from lintel import caller, config as config_mod, flow, git, loops, paths, reviews, state
    from lintel.events import append_event, create_event
    from lintel.state import (
        atomic_write_json,
        load_state_file,
        resolve_state_root,
        state_lock,
        validate_state_doc,
    )
    from lintel.task_start import find_active_task_in_state
except ImportError:
    from . import caller, config as config_mod, flow, git, loops, paths, reviews, state
    from .events import append_event, create_event
    from .state import (
        atomic_write_json,
        load_state_file,
        resolve_state_root,
        state_lock,
        validate_state_doc,
    )
    from .task_start import find_active_task_in_state


class RestoreRefusalError(flow.FlowRefusalError):
    """Raised when restore-tests or reset is refused per state machine rules."""

    def __init__(self, message: str, code: int = 1, row: Optional[str] = None):
        super().__init__(message=message, code=code, row=row)


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_run_pending_or_active(
    task_data: Dict[str, Any],
    state_dir: Path,
    task_id: str,
) -> bool:
    """Check if any persona run is currently pending or active (T-HUM-05)."""
    pos = flow.get_position(task_data)
    clean_code = pos.code
    if clean_code.startswith("P.") or clean_code.startswith("A."):
        return True
    if pos.run is not None:
        run_file = state_dir / task_id / "runs" / f"{pos.run}.json"
        if run_file.is_file():
            try:
                rd = json.loads(run_file.read_text(encoding="utf-8"))
                if rd.get("state") in ("pending", "active"):
                    return True
            except Exception:
                pass
    runs_dir = state_dir / task_id / "runs"
    if runs_dir.is_dir():
        for rf in runs_dir.glob("*.json"):
            try:
                rd = json.loads(rf.read_text(encoding="utf-8"))
                if rd.get("state") in ("pending", "active"):
                    return True
            except Exception:
                pass
    return False


def is_protected_reset_path(rel_path: str) -> bool:
    """Check if path is protected and must be left alone during reset (T-AMD-60)."""
    norm = rel_path.replace("\\", "/").strip("/")
    if norm in (".lintel/config.jsonc", "config.jsonc"):
        return True
    for prefix in (
        ".lintel/scripts/",
        "scripts/",
        ".lintel/personas/",
        "personas/",
        ".lintel/steering/",
        "steering/",
        ".lintel/tasks/",
    ):
        if norm.startswith(prefix):
            return True
    return False


def update_last_human_command(
    state_dir: Path,
    task_id: str,
    command: str,
    event_seq: int,
) -> None:
    """Update last_human record in acks.json for audit context."""
    acks_file = state_dir / task_id / "acks.json"
    acks_data: Dict[str, Any]
    if acks_file.is_file():
        try:
            acks_data = load_state_file(acks_file, doc_type="acks")
        except Exception:
            acks_data = {
                "version": 1,
                "drift": {},
                "audit": {"acks": [], "acked_ids": []},
                "last_human": {},
            }
    else:
        acks_data = {
            "version": 1,
            "drift": {},
            "audit": {"acks": [], "acked_ids": []},
            "last_human": {},
        }
    acks_data["last_human"] = {
        "at": _now_iso(),
        "event_seq": event_seq,
        "command": command,
    }
    with state_lock(state_dir):
        validate_state_doc("acks", acks_data, file_path=acks_file)
        atomic_write_json(acks_file, acks_data)


# -----------------------------------------------------------------------------
# 1. Shared restore routine
# -----------------------------------------------------------------------------

def shared_restore_routine(
    repo_root: Union[str, Path],
    task_id: str,
    task_data: Dict[str, Any],
) -> Tuple[int, Set[str]]:
    """Execute shared restore routine across restore-tests and reset.

    Restores:
    - tests paths to recorded head (task.head)
    - locked files that are no longer tests to recorded head
    - approved shared/ documents to recorded head
    - deletes untracked test-output files outside gitignored paths
    - deletes untracked tests or shared files outside gitignored paths

    Returns:
    - total count of files restored or deleted
    - set of changed lanes (e.g. {'qa', 'po', 'architect'})
    """
    root = Path(repo_root).resolve()
    head_sha = task_data.get("head") or task_data.get("recorded_head")
    if not head_sha:
        return 0, set()

    # Load config at recorded head
    res_cfg = git.run_git(["show", f"{head_sha}:.lintel/config.jsonc"], root, check=False)
    if res_cfg.returncode == 0 and res_cfg.stdout.strip():
        cfg = config_mod.load_config(res_cfg.stdout, workspace_root=root)
    else:
        cfg_file = root / ".lintel" / "config.jsonc"
        cfg = config_mod.load_config(cfg_file, workspace_root=root) if cfg_file.is_file() else {}

    # Get working tree change set diffed against head_sha
    cs = git.get_change_set(root, head_sha, config=cfg)

    # Gather unreleased locked files across all phases
    locked_files_all: Set[str] = set()
    for p_str, p_info in task_data.get("phases", {}).items():
        if isinstance(p_info, dict):
            lfiles = p_info.get("locked_files", [])
            relfiles = p_info.get("released", [])
            locked_files_all.update(set(lfiles) - set(relfiles))

    # Gather approved shared/ files committed at head_sha
    approved_shared_files: Set[str] = set()
    res_shared = git.run_git(["ls-tree", "-r", "--name-only", head_sha, "--", "shared"], root, check=False)
    if res_shared.returncode == 0 and res_shared.stdout.strip():
        for line in res_shared.stdout.splitlines():
            line = line.strip()
            if line:
                approved_shared_files.add(line)

    paths_to_checkout: List[str] = []
    paths_to_delete: List[str] = []
    changed_lanes: Set[str] = set()

    for entry in cs.entries.values():
        p = entry.path
        pclass = str(entry.path_class) if entry.path_class else ""
        is_test = (pclass == "tests" or (entry.path_class and entry.path_class.base_class == "tests") or p.startswith("tests/"))
        is_locked = (p in locked_files_all)
        is_approved_shared = (p in approved_shared_files or (p.startswith("shared/") and not entry.is_added()))
        is_untracked_shared = (p.startswith("shared/") and entry.is_added())
        is_test_output = (pclass == "test-output" or (entry.path_class and entry.path_class.base_class == "test-output"))

        if is_test_output and entry.is_added():
            paths_to_delete.append(p)
            continue

        if is_test:
            if entry.is_added():
                paths_to_delete.append(p)
                changed_lanes.add("qa")
            else:
                paths_to_checkout.append(p)
                changed_lanes.add("qa")
            continue

        if is_locked:
            if entry.is_added():
                paths_to_delete.append(p)
                changed_lanes.add("qa")
            else:
                paths_to_checkout.append(p)
                changed_lanes.add("qa")
            continue

        if is_approved_shared:
            paths_to_checkout.append(p)
            if p == "shared/requirements.md":
                changed_lanes.add("po")
            elif p.startswith("shared/plan.md") or p.startswith("shared/phase-") or p.startswith("shared/p"):
                changed_lanes.add("architect")
            continue

        if is_untracked_shared:
            paths_to_delete.append(p)
            if p == "shared/requirements.md":
                changed_lanes.add("po")
            elif p.startswith("shared/plan.md") or p.startswith("shared/phase-") or p.startswith("shared/p"):
                changed_lanes.add("architect")
            continue

    # Execute checkouts from head_sha
    for rel_path in sorted(set(paths_to_checkout)):
        git.run_git(["checkout", head_sha, "--", rel_path], root, check=False)

    # Execute deletions
    for rel_path in sorted(set(paths_to_delete)):
        target_file = root / rel_path
        if target_file.is_file() or target_file.is_symlink():
            target_file.unlink()
        parent = target_file.parent
        while parent != root:
            try:
                parent.rmdir()
                parent = parent.parent
            except OSError:
                break

    total_count = len(paths_to_checkout) + len(paths_to_delete)
    return total_count, changed_lanes


# -----------------------------------------------------------------------------
# 2. restore_tests
# -----------------------------------------------------------------------------

def restore_tests(
    repo_root: Union[str, Path],
    skip_caller_check: bool = False,
) -> str:
    """Execute 'lintel restore-tests' (T-AMD-40, T-AMD-41, T-AMD-42, T-HUM-01, T-HUM-05)."""
    root = Path(repo_root).resolve()
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise RestoreRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active_info
    state_dir = root / ".lintel" / "state"
    task_state_file = state_dir / task_id / "task.json"

    in_dev_turn = flow.is_dev_turn(task_data)

    if in_dev_turn:
        # Inside DEV's turn (T-AMD-40): open to anyone, no dialog check H0!
        total_count, changed_lanes = shared_restore_routine(root, task_id, task_data)

        # Void DEV's review
        reviews.void_reviews(state_dir, task_id, "dev")
        if "qa" in changed_lanes:
            reviews.void_reviews(state_dir, task_id, "qa")

        pos = flow.get_position(task_data)
        stage = pos.stage
        phase_num = pos.phase or (int(stage[1:]) if stage.startswith("p") and stage[1:].isdigit() else 1)

        run_active = is_run_pending_or_active(task_data, state_dir, task_id)
        if not run_active:
            flow.set_position(task_data, stage=f"p{phase_num}", code="W.dev", phase=phase_num, fr=False, run=None)

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq
        event_rec = create_event(
            step=event_seq,
            event="restore",
            stage=f"p{phase_num}",
            role="dev",
            row="T-AMD-40",
        )
        append_event(
            state_dir=state_dir,
            task_id=task_id,
            event_data=event_rec,
            task_dir=root / ".lintel" / "tasks" / task_id,
        )

        with state_lock(state_dir):
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

        next_action = flow.format_next_action(task_data)
        reviews.update_progress_md(root, task_data, next_action)

        msg = (
            f"{total_count} file(s) restored.\n"
            f"then DEV again, which must reach a green check before complete, then the DEV reviewer\n"
            f"{next_action}"
        )
        sys.stdout.write(msg + "\n")
        return msg

    # Outside DEV's turn (T-AMD-41, T-AMD-42): human-only (H0)
    if not skip_caller_check:
        dummy_args = argparse.Namespace(subcommand="restore-tests", full=False)
        caller.check_caller("restore-tests", dummy_args, repo_root=root)

    # NR check: no run pending or active (T-HUM-05)
    if is_run_pending_or_active(task_data, state_dir, task_id):
        raise RestoreRefusalError(
            "Refused: a run is pending or active; lintel task wait (T-HUM-05)",
            row="T-HUM-05",
        )

    total_count, changed_lanes = shared_restore_routine(root, task_id, task_data)

    pos = flow.get_position(task_data)
    stage = pos.stage
    code = pos.code

    state_pair: Optional[str] = None
    if code in ("R.po", "H.req"):
        state_pair = "po"
    elif code in ("R.architect", "H.plan"):
        state_pair = "architect"
    elif code in ("R.qa", "H.lock", "H.amend"):
        state_pair = "qa"

    event_seq = task_data.get("event_seq", 0) + 1
    task_data["event_seq"] = event_seq

    if state_pair and state_pair in changed_lanes:
        # T-AMD-41: restore changed that pair's lane -> W.<x> runs again
        flow.set_position(
            task_data,
            stage=stage,
            code=f"W.{state_pair}",
            phase=pos.phase,
            fr=pos.fr,
            run=None,
        )
        reviews.void_reviews(state_dir, task_id, state_pair)
        for other_pair in changed_lanes:
            if other_pair != state_pair:
                reviews.void_reviews(state_dir, task_id, other_pair)

        row_id = "T-AMD-41"
        next_action = f"lintel invoke {state_pair} --phase {stage}"
        role_label = state_pair
    else:
        # T-AMD-42: restore changed nothing in that pair's lane -> state unchanged
        for other_pair in changed_lanes:
            reviews.void_reviews(state_dir, task_id, other_pair)

        row_id = "T-AMD-42"
        next_action = flow.format_next_action(task_data)
        role_label = state_pair or code

    event_rec = create_event(
        step=event_seq,
        event="restore",
        stage=stage,
        role=role_label,
        row=row_id,
    )
    append_event(
        state_dir=state_dir,
        task_id=task_id,
        event_data=event_rec,
        task_dir=root / ".lintel" / "tasks" / task_id,
    )

    update_last_human_command(state_dir, task_id, "restore-tests", event_seq)

    with state_lock(state_dir):
        validate_state_doc("task", task_data, file_path=task_state_file)
        atomic_write_json(task_state_file, task_data)

    reviews.update_progress_md(root, task_data, next_action)

    msg = f"{total_count} file(s) restored.\n{next_action}"
    sys.stdout.write(msg + "\n")
    return msg


# -----------------------------------------------------------------------------
# 3. reset
# -----------------------------------------------------------------------------

def reset(
    repo_root: Union[str, Path],
    phase: Optional[Union[int, str]] = None,
    skip_caller_check: bool = False,
) -> str:
    """Execute 'lintel reset --phase N' (T-AMD-60, T-AMD-61, T-HUM-01, T-HUM-05)."""
    root = Path(repo_root).resolve()
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise RestoreRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active_info
    state_dir = root / ".lintel" / "state"
    task_state_file = state_dir / task_id / "task.json"

    # Human-only: check caller dialog H0
    if not skip_caller_check:
        cmd_str = f"reset{' --phase ' + str(phase) if phase is not None else ''}"
        dummy_args = argparse.Namespace(subcommand="reset", phase=str(phase) if phase is not None else None, full=False)
        caller.check_caller(cmd_str, dummy_args, repo_root=root)

    # NR check: no run pending or active (T-HUM-05)
    if is_run_pending_or_active(task_data, state_dir, task_id):
        raise RestoreRefusalError(
            "Refused: a run is pending or active; lintel task wait (T-HUM-05)",
            row="T-HUM-05",
        )

    # Determine phase number
    pos = flow.get_position(task_data)
    if phase is not None:
        p_str = str(phase).strip()
        if p_str.startswith("p") and p_str[1:].isdigit():
            p_num = int(p_str[1:])
        elif p_str.isdigit():
            p_num = int(p_str)
        else:
            raise RestoreRefusalError(f"Refused: invalid phase '{phase}' (T-STG-21)", row="T-STG-21")
        if pos.stage.startswith("p") and pos.stage[1:].isdigit():
            cur_p = int(pos.stage[1:])
            if p_num != cur_p:
                raise RestoreRefusalError(
                    f"Refused: --phase '{phase}' does not match current phase '{cur_p}' (T-STG-21)",
                    row="T-STG-21",
                )
    else:
        p_num = pos.phase
        if p_num is None and pos.stage.startswith("p") and pos.stage[1:].isdigit():
            p_num = int(pos.stage[1:])
        if p_num is None:
            raise RestoreRefusalError(
                "Refused: reset only allowed while phase is locked and not delivered (T-AMD-61)",
                row="T-AMD-61",
            )

    # Guard: phase must be locked and not delivered (T-AMD-61)
    is_locked = flow.is_phase_locked(task_data, p_num)
    is_delivered = flow.is_phase_delivered(task_data, p_num)
    if not is_locked or is_delivered:
        raise RestoreRefusalError(
            "Refused: reset only allowed while phase is locked and not delivered (T-AMD-61)",
            row="T-AMD-61",
        )

    # Routine (T-AMD-60):
    head_sha = task_data.get("head") or task_data.get("recorded_head")
    if not head_sha:
        raise RestoreRefusalError("Refused: no recorded head commit (T-AMD-61)", row="T-AMD-61")

    # 1. Snapshot change set to refs/lintel/<id>/snapshot-<n>
    snap_ref = git.create_snapshot_ref(
        repo_root=root,
        task_id=task_id,
        kind="reset",
        parent=head_sha,
    )

    # Reload task_data to incorporate snapshot counters
    task_data = load_state_file(task_state_file, doc_type="task")

    # Load config at recorded head
    res_cfg = git.run_git(["show", f"{head_sha}:.lintel/config.jsonc"], root, check=False)
    if res_cfg.returncode == 0 and res_cfg.stdout.strip():
        cfg = config_mod.load_config(res_cfg.stdout, workspace_root=root)
    else:
        cfg_file = root / ".lintel" / "config.jsonc"
        cfg = config_mod.load_config(cfg_file, workspace_root=root) if cfg_file.is_file() else {}

    # Get working tree change set diffed against head_sha
    cs = git.get_change_set(root, head_sha, config=cfg)

    # Collect uncommitted edits to protected paths (.lintel/config.jsonc, scripts, personas, steering)
    uncommitted_protected: List[str] = []
    paths_to_checkout: List[str] = []
    paths_to_delete: List[str] = []

    for entry in cs.entries.values():
        p = entry.path
        if is_protected_reset_path(p):
            uncommitted_protected.append(p)
            continue

        if entry.is_added():
            paths_to_delete.append(p)
        else:
            paths_to_checkout.append(p)

    # Restore locked files that are no longer tests
    p_info = task_data.get("phases", {}).get(str(p_num), {})
    locked_files = set(p_info.get("locked_files", [])) - set(p_info.get("released", []))
    for lf in locked_files:
        if not is_protected_reset_path(lf):
            paths_to_checkout.append(lf)

    # Restore approved shared/ documents by name
    res_shared = git.run_git(["ls-tree", "-r", "--name-only", head_sha, "--", "shared"], root, check=False)
    if res_shared.returncode == 0 and res_shared.stdout.strip():
        for sp in res_shared.stdout.splitlines():
            sp = sp.strip()
            if sp and not is_protected_reset_path(sp):
                paths_to_checkout.append(sp)

    # Execute checkouts from head_sha
    for rel_path in sorted(set(paths_to_checkout)):
        git.run_git(["checkout", head_sha, "--", rel_path], root, check=False)

    # Execute deletions of untracked non-ignored files
    for rel_path in sorted(set(paths_to_delete)):
        target_file = root / rel_path
        if target_file.is_file() or target_file.is_symlink():
            target_file.unlink()
        parent = target_file.parent
        while parent != root:
            try:
                parent.rmdir()
                parent = parent.parent
            except OSError:
                break

    # State updates per T-AMD-60:
    # 1. End any amend cycle
    task_data["cycle"] = None

    # 2. Clear DEV pair stall counters
    loops.reset_stall_counters(state_dir, task_id, writer="dev", stage=f"p{p_num}")

    # 3. Clear blocked memory for dev
    task_data["memory"] = [m for m in task_data.get("memory", []) if m.get("writer") != "dev"]

    # 4. Abandon pending D.* escalation if any
    task_data["escalation"] = None

    # 5. Restart DEV audit window
    phases = task_data.setdefault("phases", {})
    phases.setdefault(str(p_num), {})["dev_window_since"] = _now_iso()

    # 6. State becomes p<N>:W.dev
    flow.set_position(task_data, stage=f"p{p_num}", code="W.dev", phase=p_num, fr=False, run=None)

    # 7. Event reset
    event_seq = task_data.get("event_seq", 0) + 1
    task_data["event_seq"] = event_seq
    event_rec = create_event(
        step=event_seq,
        event="reset",
        stage=f"p{p_num}",
        role="dev",
        row="T-AMD-60",
    )
    append_event(
        state_dir=state_dir,
        task_id=task_id,
        event_data=event_rec,
        task_dir=root / ".lintel" / "tasks" / task_id,
    )

    # 8. Update last_human in acks.json
    human_cmd = f"reset{' --phase ' + str(phase) if phase is not None else ''}"
    update_last_human_command(state_dir, task_id, human_cmd, event_seq)

    # 9. Write task.json
    with state_lock(state_dir):
        validate_state_doc("task", task_data, file_path=task_state_file)
        atomic_write_json(task_state_file, task_data)

    next_action = f"lintel invoke dev --phase p{p_num}"
    reviews.update_progress_md(root, task_data, next_action)

    out_lines: List[str] = []
    if uncommitted_protected:
        out_lines.append(f"Uncommitted edits left alone: {', '.join(sorted(uncommitted_protected))}")
    out_lines.append(next_action)
    msg = "\n".join(out_lines)
    sys.stdout.write(msg + "\n")
    return msg
