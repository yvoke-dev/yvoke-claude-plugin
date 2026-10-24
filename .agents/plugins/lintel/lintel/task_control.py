"""Task lifecycle management commands (M1a-31).

Implements:
1. `task_resume`:
   - Moves orchestrator role to the calling conversation.
   - Pending run P.<role> never entered -> issues new nonce, updates runs.json,
     prints progress and fresh tool call again.
   - Active run A.<role> that is live -> prints progress and 'lintel task wait'.
   - Active run A.<role> that is stopped -> appends stopped event if not yet recorded,
     reports 'stopped, run lintel task unstick'.
   - No run pending or active -> prints progress and next action.
2. `task_wait`:
   - Decides from verdict in runs.json, running gate record (running.json),
     and agy state readers (inspect_agy_state).
   - 10s grace period after running gate clears, 15m fallback with no record.
   - Appends stopped event with cause code on first detection.
   - Returns on state change or bound ('still running, wait again').
3. `task_unstick`:
   - Refuses if no run pending or active (T-FIN-41).
   - Refuses if run looks live without --force (T-FIN-51).
   - With --force: human-only (dialog check H0), stop cause 'forced' (T-FIN-52).
   - Abandons run (counts as no round, voids nonce, reverts to from_state).
   - Prints fresh invoke for same role.
4. `task_abort`:
   - Refuses if run pending or active (T-FIN-31).
   - Refuses if uncommitted config/scripts/personas/steering edits exist (T-FIN-32).
   - Snapshots change set, restores paths as reset does, commits task folder as
     chore(lintel): task record, ends amend cycle, marks aborted, switches back.
5. `task_finish`:
   - Human-only: requires dialog check H0.
   - Refuses if run pending or active (T-HUM-05).
   - Requires state H.finish (T-FIN-21).
   - Writes summary.md, displays lessons.md, commits chore(lintel): task record,
     records in finished.json, switches back to recorded branch.
   - If --pr: opens PR with gh, glab, or prints push command and PR text (no AI attribution).
"""

import sys
sys.dont_write_bytecode = True

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

try:
    from lintel import (
        agy_readers,
        caller,
        config as config_mod,
        events,
        flow,
        git,
        loops,
        paths,
        progress,
        restore,
        reviews,
        state,
    )
    from lintel.agy_readers import AgyObservation, inspect_agy_state, resolve_invoking_context
    from lintel.events import append_event, create_event
    from lintel.progress import render_progress_md, render_summary_md, update_progress_md
    from lintel.restore import is_protected_reset_path, update_last_human_command
    from lintel.state import (
        StateError,
        atomic_write_json,
        check_or_clear_stale_running,
        get_process_start_time,
        is_pid_alive,
        is_pid_ours,
        load_state_file,
        resolve_state_root,
        state_lock,
        validate_state_doc,
    )
    from lintel.task_start import find_active_task_in_state
except ImportError:
    from . import (
        agy_readers,
        caller,
        config as config_mod,
        events,
        flow,
        git,
        loops,
        paths,
        progress,
        restore,
        reviews,
        state,
    )
    from .agy_readers import AgyObservation, inspect_agy_state, resolve_invoking_context
    from .events import append_event, create_event
    from .progress import render_progress_md, render_summary_md, update_progress_md
    from .restore import is_protected_reset_path, update_last_human_command
    from .state import (
        StateError,
        atomic_write_json,
        check_or_clear_stale_running,
        get_process_start_time,
        is_pid_alive,
        is_pid_ours,
        load_state_file,
        resolve_state_root,
        state_lock,
        validate_state_doc,
    )
    from .task_start import find_active_task_in_state


class TaskControlRefusalError(flow.FlowRefusalError):
    """Raised when a task control command is refused."""

    def __init__(self, message: str, code: int = 1, row: Optional[str] = None):
        super().__init__(message=message, code=code, row=row)


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Parse ISO-8601 timestamp string into timezone-aware datetime."""
    if not ts:
        return None
    clean = ts.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(clean)
    except Exception:
        return None


def is_run_pending_or_active(
    task_data: Dict[str, Any],
    state_dir: Path,
    task_id: str,
) -> bool:
    """Check if any persona run is currently pending or active (T-HUM-05, T-FIN-31)."""
    return restore.is_run_pending_or_active(task_data, state_dir, task_id)


def get_invoking_context(
    repo_root: Path,
    task_id: str,
    run_data: Dict[str, Any],
) -> Tuple[Optional[str], Optional[Path]]:
    """Determine the invoking conversation ID and artifact directory path."""
    audit_file = repo_root / ".lintel" / "state" / task_id / "audit.jsonl"
    if not audit_file.is_file():
        audit_file = repo_root / ".lintel" / "state" / "audit.jsonl"

    role = run_data.get("role")
    nonce = run_data.get("nonce")
    hints = run_data.get("hints")

    return resolve_invoking_context(
        audit_log_path=audit_file if audit_file.is_file() else None,
        hints=hints,
        role=role,
        nonce=nonce,
        base_dir=repo_root,
    )


def format_stop_message(cause: str, role: Optional[str] = None, run_state: Optional[str] = None) -> str:
    """Format user-facing message for a stopped run per T-FIN-44, T-FIN-45."""
    if run_state == "pending" and cause in ("killed", "permission-denied", "restart"):
        return "stopped before enter: check the agent file's frontmatter (`lintel doctor`), then `lintel task unstick`"
    if cause == "killed":
        return "stopped (killed)\nstopped, run `lintel task unstick`"
    if cause == "permission-denied":
        return "stopped (permission denied)\nstopped, run `lintel task unstick`"
    if cause == "restart":
        return "stopped (restart)\nstopped, run `lintel task unstick`"
    if cause == "ended-without-verdict":
        return "ended without a verdict; lintel task unstick"
    if cause == "never-entered":
        return "never entered: check the agent file's frontmatter (`lintel doctor`); lintel task unstick"
    if cause == "forced":
        return "forced, run `lintel task unstick`"
    return "stopped, run `lintel task unstick`"


def judge_run_status(
    repo_root: Path,
    task_id: str,
    task_data: Dict[str, Any],
    run_data: Dict[str, Any],
) -> Tuple[bool, Optional[str], Optional[str], Optional[AgyObservation]]:
    """Judge if a pending or active run looks live per docs/state-machine.md §9.13.

    Returns:
        (is_live: bool, stop_cause: Optional[str], stop_message: Optional[str], observation: Optional[AgyObservation])
    """
    run_state = run_data.get("state")
    role = run_data.get("role")
    nonce = run_data.get("nonce")
    run_id = run_data.get("id")

    # If run has already finished with verdict or abandoned
    if run_data.get("verdict") is not None or run_state in ("done", "abandoned"):
        return False, None, None, None

    # If stop was already recorded on the run
    if run_data.get("stop") is not None:
        rec_cause = run_data["stop"].get("cause") or "ended-without-verdict"
        msg = format_stop_message(rec_cause, role=role, run_state=run_state)
        return False, rec_cause, msg, None

    state_dir = repo_root / ".lintel" / "state"

    # Context resolution for agy inspection
    inv_cid, inv_dir = get_invoking_context(repo_root, task_id, run_data)

    # -------------------------------------------------------------------------
    # Case 1: Pending run (P.<role>)
    # -------------------------------------------------------------------------
    if run_state == "pending":
        obs = inspect_agy_state(
            invoking_artifact_dir=inv_dir,
            invoking_conversation_id=inv_cid,
            role=role,
            nonce=nonce,
            hints=run_data.get("hints"),
            base_dir=repo_root,
        )

        # Stop notice or child record killed
        if obs.status in ("killed", "permission-denied", "stopped-by-restart"):
            cause = obs.stop_cause or ("killed" if obs.status == "killed" else ("permission-denied" if obs.status == "permission-denied" else "restart"))
            msg = format_stop_message(cause, role=role, run_state="pending")
            return False, cause, msg, obs

        # Check 15 minutes since run.nonce_at
        nonce_at_str = run_data.get("nonce_at") or run_data.get("created_at")
        nonce_dt = _parse_iso(nonce_at_str)
        now_dt = datetime.now(timezone.utc)
        elapsed = (now_dt - nonce_dt).total_seconds() if nonce_dt else 0.0

        if elapsed < 15.0 * 60.0:
            # Pending run is live until 15 minutes
            return True, None, None, obs
        else:
            msg = format_stop_message("never-entered", role=role, run_state="pending")
            return False, "never-entered", msg, obs

    # -------------------------------------------------------------------------
    # Case 2: Active run (A.<role>)
    # -------------------------------------------------------------------------
    # Step A: Check running gate record (running.json)
    rec = check_or_clear_stale_running(state_dir)
    if rec is not None:
        # A live running record means the run is live!
        return True, None, None, None

    # Step B: Check 10s grace period after running record cleared
    cleared_at_str = run_data.get("gate_cleared_at")
    if cleared_at_str:
        cleared_dt = _parse_iso(cleared_at_str)
        if cleared_dt:
            elapsed_cleared = (datetime.now(timezone.utc) - cleared_dt).total_seconds()
            if elapsed_cleared < 10.0:
                # Within 10s grace period!
                return True, None, None, None

    # Step C: Inspect agy reader state
    obs = inspect_agy_state(
        invoking_artifact_dir=inv_dir,
        invoking_conversation_id=inv_cid,
        child_conversation_id=run_data.get("conversation_id"),
        role=role,
        nonce=nonce,
        hints=run_data.get("hints"),
        base_dir=repo_root,
    )

    if obs.status == "killed":
        msg = format_stop_message("killed", role=role, run_state="active")
        return False, "killed", msg, obs

    if obs.status == "permission-denied":
        msg = format_stop_message("permission-denied", role=role, run_state="active")
        return False, "permission-denied", msg, obs

    if obs.status == "stopped-by-restart":
        msg = format_stop_message("restart", role=role, run_state="active")
        return False, "restart", msg, obs

    if obs.status == "command-pending":
        return True, None, None, obs

    if obs.status == "ended-without-verdict":
        if cleared_at_str:
            # Gate cleared and 10s elapsed
            msg = format_stop_message("ended-without-verdict", role=role, run_state="active")
            return False, "ended-without-verdict", msg, obs
        else:
            # No running record ever existed: 15 minutes fallback since last transcript activity
            last_activity = obs.last_step_time or run_data.get("entered_at") or run_data.get("created_at")
            act_dt = _parse_iso(last_activity)
            elapsed_act = (datetime.now(timezone.utc) - act_dt).total_seconds() if act_dt else 0.0
            if elapsed_act < 15.0 * 60.0:
                return True, None, None, obs
            else:
                msg = format_stop_message("ended-without-verdict", role=role, run_state="active")
                return False, "ended-without-verdict", msg, obs

    # Otherwise quiet or active with recent activity
    return True, None, None, obs


def commit_task_record(
    repo_root: Union[str, Path],
    task_id: str,
    head_sha: str,
    event_seq: int,
    message: str = "chore(lintel): task record",
) -> str:
    """Commit the task folder to the task branch as chore(lintel): task record.

    Returns the new commit SHA.
    """
    root = Path(repo_root).resolve()
    task_folder = f".lintel/tasks/{task_id}"
    if (root / task_folder).is_dir():
        git.run_git(["add", "-A", "--", task_folder], root, check=False)
    tree_id = git.run_git(["write-tree"], root, check=True).stdout.strip()
    commit_sha = git.create_commit(
        repo_root=root,
        tree_id=tree_id,
        parent=head_sha,
        message=message,
        author_role="lintel",
        task_id=task_id,
        event_seq=event_seq,
    )
    git.update_ref(root, f"refs/heads/lintel/{task_id}", commit_sha)
    git.run_git(["reset", commit_sha], root, check=False)
    return commit_sha


def restore_working_tree_for_abort(
    repo_root: Path,
    task_id: str,
    task_data: Dict[str, Any],
    in_docs: bool = False,
) -> None:
    """Restore working tree files against recorded head as reset does (T-FIN-30)."""
    head_sha = task_data.get("head") or task_data.get("recorded_head")
    if not head_sha:
        return

    # 1. Load config at head
    res_cfg = git.run_git(["show", f"{head_sha}:.lintel/config.jsonc"], repo_root, check=False)
    if res_cfg.returncode == 0 and res_cfg.stdout.strip():
        cfg = config_mod.load_config(res_cfg.stdout, workspace_root=repo_root)
    else:
        cfg_file = repo_root / ".lintel" / "config.jsonc"
        cfg = config_mod.load_config(cfg_file, workspace_root=repo_root) if cfg_file.is_file() else {}

    cs = git.get_change_set(repo_root, head_sha, config=cfg)

    paths_to_checkout: List[str] = []
    paths_to_delete: List[str] = []

    for entry in cs.entries.values():
        p = entry.path
        if is_protected_reset_path(p):
            # In docs update, specs and steering are restored
            if in_docs and (p.startswith(".lintel/specs/") or p.startswith(".lintel/steering/")):
                if entry.is_added():
                    paths_to_delete.append(p)
                else:
                    paths_to_checkout.append(p)
            continue

        if entry.is_added():
            paths_to_delete.append(p)
        else:
            paths_to_checkout.append(p)

    # Restore unreleased locked files across all phases
    for p_info in (task_data.get("phases") or {}).values():
        if isinstance(p_info, dict):
            lfiles = set(p_info.get("locked_files", [])) - set(p_info.get("released", []))
            for lf in lfiles:
                if not is_protected_reset_path(lf):
                    paths_to_checkout.append(lf)

    # Restore approved shared/ documents by name
    res_shared = git.run_git(["ls-tree", "-r", "--name-only", head_sha, "--", "shared"], repo_root, check=False)
    if res_shared.returncode == 0 and res_shared.stdout.strip():
        for sp in res_shared.stdout.splitlines():
            sp = sp.strip()
            if sp and not is_protected_reset_path(sp):
                paths_to_checkout.append(sp)

    # Check out restored files from head_sha
    for rel_path in sorted(set(paths_to_checkout)):
        git.run_git(["checkout", head_sha, "--", rel_path], repo_root, check=False)

    # Delete untracked non-ignored files
    for rel_path in sorted(set(paths_to_delete)):
        target_file = repo_root / rel_path
        if target_file.is_file() or target_file.is_symlink():
            target_file.unlink()
        parent = target_file.parent
        while parent != repo_root:
            try:
                parent.rmdir()
                parent = parent.parent
            except OSError:
                break


# =============================================================================
# 1. task_resume
# =============================================================================

def task_resume(
    repo_root: Union[str, Path],
    conversation_id: Optional[str] = None,
) -> str:
    """Execute 'lintel task resume' (T-FIN-47, T-FIN-48, T-FIN-49, T-FIN-50)."""
    root = Path(repo_root).resolve()
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise TaskControlRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active_info
    state_dir = root / ".lintel" / "state"
    task_file = state_dir / task_id / "task.json"

    with state_lock(state_dir):
        task_data = load_state_file(task_file, doc_type="task")
        cid = conversation_id or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
        task_data["orchestrator"] = {
            "conversation_id": cid,
            "since": _now_iso(),
        }

        pos = flow.get_position(task_data)
        run_id = pos.run
        run_file = state_dir / task_id / "runs" / f"{run_id}.json" if run_id else None
        run_data = load_state_file(run_file, doc_type="run") if run_file and run_file.is_file() else None

        # Check if there is a pending or active run
        has_pending_or_active = (
            run_data is not None
            and run_data.get("state") in ("pending", "active")
            and pos.code.startswith(("P.", "A."))
        )

        if not has_pending_or_active:
            # T-FIN-50: No run pending or active
            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            validate_state_doc("task", task_data, file_path=task_file)
            atomic_write_json(task_file, task_data)

            ev = create_event(
                step=event_seq,
                event="resume",
                stage=pos.stage,
                row="T-FIN-50",
            )
            append_event(state_dir, task_id, ev, task_dir=root / ".lintel" / "tasks" / task_id)

            next_act = flow.format_next_action(task_data, repo_root=root)
            update_progress_md(root, task_data, next_act)
            progress_content = render_progress_md(root, task_id, task_data=task_data, next_action=next_act)
            out = progress_content
            sys.stdout.write(out + "\n")
            return out

        assert run_data is not None
        assert run_file is not None

        # T-FIN-47: Pending run P.<role> that never entered
        if run_data.get("state") == "pending":
            new_nonce = secrets.token_hex(16)
            now_str = _now_iso()
            run_data["nonce"] = new_nonce
            run_data["nonce_at"] = now_str
            role = run_data["role"]
            prompt_text = f"[lintel:{role}:{new_nonce}] Run: ./.lintel/bin/lintel enter {role} {new_nonce}"
            run_data["prompt"] = prompt_text
            validate_state_doc("run", run_data, file_path=run_file)
            atomic_write_json(run_file, run_data)

            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            validate_state_doc("task", task_data, file_path=task_file)
            atomic_write_json(task_file, task_data)

            ev = create_event(
                step=event_seq,
                event="resume",
                stage=pos.stage,
                role=role,
                run=run_id,
                row="T-FIN-47",
            )
            append_event(state_dir, task_id, ev, task_dir=root / ".lintel" / "tasks" / task_id)

            next_act = "lintel task wait"
            update_progress_md(root, task_data, next_act)
            progress_content = render_progress_md(root, task_id, task_data=task_data, next_action=next_act)
            tool_call_str = (
                f'Subagents: [{{TypeName: "lintel-{role}", Role: "lintel-{role}", '
                f'Model: "inherit", Workspace: "inherit", Prompt: "{prompt_text}"}}]'
            )
            out = f"{progress_content}\n\n{tool_call_str}\nThe orchestrator ends its turn, then lintel task wait"
            sys.stdout.write(out + "\n")
            return out

        # Active run A.<role>
        is_live, stop_cause, stop_msg, obs = judge_run_status(root, task_id, task_data, run_data)
        if is_live:
            # T-FIN-48: Live active run
            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            validate_state_doc("task", task_data, file_path=task_file)
            atomic_write_json(task_file, task_data)

            ev = create_event(
                step=event_seq,
                event="resume",
                stage=pos.stage,
                role=run_data.get("role"),
                run=run_id,
                row="T-FIN-48",
            )
            append_event(state_dir, task_id, ev, task_dir=root / ".lintel" / "tasks" / task_id)

            next_act = "lintel task wait"
            update_progress_md(root, task_data, next_act)
            progress_content = render_progress_md(root, task_id, task_data=task_data, next_action=next_act)
            out = f"{progress_content}\n\n{next_act}"
            sys.stdout.write(out + "\n")
            return out
        else:
            # T-FIN-49: Stopped active run
            cause_code = stop_cause or "ended-without-verdict"
            if run_data.get("stop") is None:
                run_data["stop"] = {"cause": cause_code, "at": _now_iso()}
                validate_state_doc("run", run_data, file_path=run_file)
                atomic_write_json(run_file, run_data)

                event_seq = task_data.get("event_seq", 0) + 1
                task_data["event_seq"] = event_seq
                row_id = "T-FIN-45" if cause_code in ("ended-without-verdict", "never-entered") else "T-FIN-44"
                ev_stop = create_event(
                    step=event_seq,
                    event="stopped",
                    stage=run_data.get("stage", ""),
                    role=run_data.get("role"),
                    run=run_id,
                    stop_cause=cause_code,
                    row=row_id,
                )
                append_event(state_dir, task_id, ev_stop, task_dir=root / ".lintel" / "tasks" / task_id)

            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            validate_state_doc("task", task_data, file_path=task_file)
            atomic_write_json(task_file, task_data)

            ev_res = create_event(
                step=event_seq,
                event="resume",
                stage=pos.stage,
                role=run_data.get("role"),
                run=run_id,
                row="T-FIN-49",
            )
            append_event(state_dir, task_id, ev_res, task_dir=root / ".lintel" / "tasks" / task_id)

            next_act = "lintel task unstick"
            update_progress_md(root, task_data, next_act)
            out = "stopped, run `lintel task unstick`"
            sys.stdout.write(out + "\n")
            return out


# =============================================================================
# 2. task_wait
# =============================================================================

def _handle_verdict_or_transition(
    root: Path,
    state_dir: Path,
    task_id: str,
    task_file: Path,
    run_file: Optional[Path],
    task_data: Dict[str, Any],
) -> str:
    """Format and report next action upon verdict or state transition (T-FIN-43)."""
    pos = flow.get_position(task_data)
    run_id = pos.run
    run_data = None
    if run_file and run_file.is_file():
        try:
            run_data = load_state_file(run_file, doc_type="run")
        except Exception:
            pass

    if run_data and run_data.get("verdict") and pos.code.startswith(("P.", "A.")):
        with state_lock(state_dir):
            task_data = load_state_file(task_file, doc_type="task")
            try:
                task_data, next_act, _ = flow.on_done(
                    task_data,
                    role=run_data["role"],
                    verdict=run_data["verdict"],
                    cause=run_data.get("cause"),
                    run_id=run_id,
                )
                validate_state_doc("task", task_data, file_path=task_file)
                atomic_write_json(task_file, task_data)
            except Exception:
                next_act = flow.format_next_action(task_data, repo_root=root)
    else:
        next_act = flow.format_next_action(task_data, repo_root=root)
    update_progress_md(root, task_data, next_act)
    sys.stdout.write(next_act + "\n")
    return next_act


def task_wait(
    repo_root: Union[str, Path],
    timeout: float = 60.0,
    poll_interval: float = 1.0,
) -> str:
    """Execute 'lintel task wait [--timeout s]' (T-FIN-42..46)."""
    root = Path(repo_root).resolve()
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise TaskControlRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active_info
    state_dir = root / ".lintel" / "state"
    task_file = state_dir / task_id / "task.json"

    start_time = time.monotonic()
    deadline = start_time + max(0.0, float(timeout))

    had_running_gate = False
    initial_gate_cleared_at = None
    cleared_at_initialized = False

    while True:
        task_data = load_state_file(task_file, doc_type="task")
        pos = flow.get_position(task_data)
        run_id = pos.run
        run_file = state_dir / task_id / "runs" / f"{run_id}.json" if run_id else None

        # 0. Check running gate record (running.json)
        running_gate = check_or_clear_stale_running(state_dir)
        if running_gate is not None:
            had_running_gate = True

        # State change: running gate record cleared (T-FIN-43)
        if had_running_gate and running_gate is None:
            task_data = load_state_file(task_file, doc_type="task")
            return _handle_verdict_or_transition(root, state_dir, task_id, task_file, run_file, task_data)

        # If a gate is currently running, wait for it even if pos.code is not P.* or A.*
        if running_gate is not None:
            now = time.monotonic()
            if now >= deadline:
                sys.stdout.write("still running, wait again\n")
                return "still running, wait again"
            sleep_time = min(poll_interval, deadline - now)
            if sleep_time > 0:
                time.sleep(sleep_time)
            continue

        # If no gate running and no run is pending or active, state has transitioned
        if not run_id or not pos.code.startswith(("P.", "A.")):
            return _handle_verdict_or_transition(root, state_dir, task_id, task_file, run_file, task_data)

        if not run_file or not run_file.is_file():
            sys.stdout.write("still running, wait again\n")
            return "still running, wait again"

        run_data = load_state_file(run_file, doc_type="run")

        if not cleared_at_initialized:
            initial_gate_cleared_at = run_data.get("gate_cleared_at")
            cleared_at_initialized = True
        elif run_data.get("gate_cleared_at") != initial_gate_cleared_at:
            # gate_cleared_at changed during waiting (T-FIN-43)
            task_data = load_state_file(task_file, doc_type="task")
            return _handle_verdict_or_transition(root, state_dir, task_id, task_file, run_file, task_data)

        # 1. Verdict recorded? (T-FIN-43)
        if run_data.get("verdict") is not None or run_data.get("state") in ("done", "abandoned"):
            return _handle_verdict_or_transition(root, state_dir, task_id, task_file, run_file, task_data)

        # 2. Check stopped vs live
        is_live, stop_cause, stop_msg, obs = judge_run_status(root, task_id, task_data, run_data)
        if not is_live and stop_cause is not None:
            # First time recording stop
            if run_data.get("stop") is None:
                with state_lock(state_dir):
                    run_data = load_state_file(run_file, doc_type="run")
                    run_data["stop"] = {"cause": stop_cause, "at": _now_iso()}
                    validate_state_doc("run", run_data, file_path=run_file)
                    atomic_write_json(run_file, run_data)

                    task_data = load_state_file(task_file, doc_type="task")
                    event_seq = task_data.get("event_seq", 0) + 1
                    task_data["event_seq"] = event_seq
                    validate_state_doc("task", task_data, file_path=task_file)
                    atomic_write_json(task_file, task_data)

                    row_id = "T-FIN-45" if stop_cause in ("ended-without-verdict", "never-entered") else "T-FIN-44"
                    ev = create_event(
                        step=event_seq,
                        event="stopped",
                        stage=run_data.get("stage", ""),
                        role=run_data.get("role"),
                        run=run_id,
                        stop_cause=stop_cause,
                        row=row_id,
                    )
                    append_event(state_dir, task_id, ev, task_dir=root / ".lintel" / "tasks" / task_id)
                    update_progress_md(root, task_data, "lintel task unstick")
            res_msg = stop_msg or "stopped, run `lintel task unstick`"
            sys.stdout.write(res_msg + "\n")
            return res_msg

        # 3. If live, check deadline
        now = time.monotonic()
        if now >= deadline:
            sys.stdout.write("still running, wait again\n")
            return "still running, wait again"

        sleep_time = min(poll_interval, deadline - now)
        if sleep_time > 0:
            time.sleep(sleep_time)


# =============================================================================
# 3. task_unstick
# =============================================================================

def task_unstick(
    repo_root: Union[str, Path],
    force: bool = False,
    skip_caller_check: bool = False,
    dialog_fn: Optional[Callable[..., str]] = None,
) -> str:
    """Execute 'lintel task unstick [--force]' (T-FIN-40, T-FIN-41, T-FIN-51, T-FIN-52, T-FIN-54)."""
    root = Path(repo_root).resolve()
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise TaskControlRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active_info
    state_dir = root / ".lintel" / "state"
    task_file = state_dir / task_id / "task.json"

    pos = flow.get_position(task_data)
    run_id = pos.run
    if not run_id and not pos.code.startswith(("P.", "A.")):
        raise TaskControlRefusalError(
            "Refused: no run is pending or active (T-FIN-41)",
            row="T-FIN-41",
        )

    run_file = state_dir / task_id / "runs" / f"{run_id}.json" if run_id else None
    if not run_file or not run_file.is_file():
        # Fallback to finding latest run
        runs_dir = state_dir / task_id / "runs"
        cand_runs = sorted(runs_dir.glob("*.json")) if runs_dir.is_dir() else []
        if cand_runs:
            run_file = cand_runs[-1]
            run_id = run_file.stem
        else:
            raise TaskControlRefusalError("Refused: no run is pending or active (T-FIN-41)", row="T-FIN-41")

    run_data = load_state_file(run_file, doc_type="run")

    # Check if run looks live
    is_live, detected_stop_cause, _, _ = judge_run_status(root, task_id, task_data, run_data)

    # --force is human-only: check caller dialog H0
    if force and not skip_caller_check:
        dummy_args = argparse.Namespace(subcommand="task", task_command="unstick", force=True)
        caller.check_caller(
            "task unstick --force",
            dummy_args,
            repo_root=root,
            dialog_fn=dialog_fn,
        )

    if is_live:
        if not force:
            raise TaskControlRefusalError(
                "Refused: run looks live; run 'lintel task wait', or 'human: lintel task unstick --force' when it is known to be stuck (T-FIN-51)",
                row="T-FIN-51",
            )
        stop_cause = "forced"
    else:
        stop_cause = "forced" if force else (detected_stop_cause or "ended-without-verdict")

    with state_lock(state_dir):
        task_data = load_state_file(task_file, doc_type="task")
        run_data = load_state_file(run_file, doc_type="run")

        # Record stopped event if not yet recorded
        if run_data.get("stop") is None:
            run_data["stop"] = {"cause": stop_cause, "at": _now_iso()}
            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            row_id = "T-FIN-52" if stop_cause == "forced" else "T-FIN-40"
            ev_stop = create_event(
                step=event_seq,
                event="stopped",
                stage=run_data.get("stage", ""),
                role=run_data.get("role"),
                run=run_id,
                stop_cause=stop_cause,
                row=row_id,
            )
            append_event(state_dir, task_id, ev_stop, task_dir=root / ".lintel" / "tasks" / task_id)

        # Abandon run
        run_data["state"] = "abandoned"
        run_data["abandoned_at"] = _now_iso()
        run_data["counted"] = False
        run_data["nonce"] = ""
        validate_state_doc("run", run_data, file_path=run_file)
        atomic_write_json(run_file, run_data)

        # Revert state to from_state
        from_state = run_data.get("from_state") or {}
        from_code = from_state.get("code")
        from_fr = from_state.get("fr", False)

        task_data, next_act, row_unstick = flow.on_unstick(
            task_data,
            force=(stop_cause == "forced"),
            is_live=False,
            is_human=True,
            from_state_code=from_code,
            from_state_fr=from_fr,
        )

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq
        ev_unstick = create_event(
            step=event_seq,
            event="unstick",
            stage=run_data.get("stage", ""),
            role=run_data.get("role"),
            run=run_id,
            row=row_unstick,
        )
        append_event(state_dir, task_id, ev_unstick, task_dir=root / ".lintel" / "tasks" / task_id)

        if stop_cause == "forced":
            update_last_human_command(state_dir, task_id, "task unstick --force", event_seq)

        validate_state_doc("task", task_data, file_path=task_file)
        atomic_write_json(task_file, task_data)

        update_progress_md(root, task_data, next_act)
        sys.stdout.write(next_act + "\n")
        return next_act


# =============================================================================
# 4. task_abort
# =============================================================================

def task_abort(
    repo_root: Union[str, Path],
) -> str:
    """Execute 'lintel task abort' (T-FIN-30, T-FIN-31, T-FIN-32)."""
    root = Path(repo_root).resolve()
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise TaskControlRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active_info
    state_dir = root / ".lintel" / "state"
    task_file = state_dir / task_id / "task.json"

    # Refusal T-FIN-31: run pending or active
    if is_run_pending_or_active(task_data, state_dir, task_id):
        raise TaskControlRefusalError(
            "Refused: a run is pending or active; lintel task wait, or lintel task unstick (T-FIN-31)",
            row="T-FIN-31",
        )

    # Refusal T-FIN-32: uncommitted config/scripts/personas/steering edits
    head_sha = task_data.get("head") or task_data.get("recorded_head")
    if not head_sha:
        raise TaskControlRefusalError("Refused: no recorded head commit (T-FIN-30)", row="T-FIN-30")

    pos = flow.get_position(task_data)
    in_docs = (pos.stage == "docs")

    # Load config at head
    res_cfg = git.run_git(["show", f"{head_sha}:.lintel/config.jsonc"], root, check=False)
    if res_cfg.returncode == 0 and res_cfg.stdout.strip():
        cfg = config_mod.load_config(res_cfg.stdout, workspace_root=root)
    else:
        cfg_file = root / ".lintel" / "config.jsonc"
        cfg = config_mod.load_config(cfg_file, workspace_root=root) if cfg_file.is_file() else {}

    cs = git.get_change_set(root, head_sha, config=cfg)
    for p in cs.entries.keys():
        norm = p.replace("\\", "/").strip("/")
        if norm in (".lintel/config.jsonc", "config.jsonc"):
            raise TaskControlRefusalError(
                "Refused: uncommitted edits in config, scripts, personas, or steering; the human commits it with amend --config or reverts it (T-FIN-32)",
                row="T-FIN-32",
            )
        for prefix in (".lintel/scripts/", "scripts/", ".lintel/personas/", "personas/"):
            if norm.startswith(prefix):
                raise TaskControlRefusalError(
                    "Refused: uncommitted edits in config, scripts, personas, or steering; the human commits it with amend --config or reverts it (T-FIN-32)",
                    row="T-FIN-32",
                )
        if not in_docs:
            for prefix in (".lintel/steering/", "steering/"):
                if norm.startswith(prefix):
                    raise TaskControlRefusalError(
                        "Refused: uncommitted edits in config, scripts, personas, or steering; the human commits it with amend --config or reverts it (T-FIN-32)",
                        row="T-FIN-32",
                    )

    # 1. Snapshot change set to refs/lintel/<id>/snapshot-<n>
    git.create_snapshot_ref(root, task_id, kind="abort", parent=head_sha)

    with state_lock(state_dir):
        task_data = load_state_file(task_file, doc_type="task")

        # 2. Restore change set paths and files
        restore_working_tree_for_abort(root, task_id, task_data, in_docs=in_docs)

        # 3. State update
        task_data["cycle"] = None
        task_data["status"] = "aborted"
        task_data["finished_at"] = _now_iso()

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq

        ev = create_event(
            step=event_seq,
            event="abort",
            stage=pos.stage,
            row="T-FIN-30",
        )
        append_event(state_dir, task_id, ev, task_dir=root / ".lintel" / "tasks" / task_id)
        update_progress_md(root, task_data, "none")

        # 4. Commit task folder as chore(lintel): task record
        commit_sha = commit_task_record(root, task_id, head_sha, event_seq)
        task_data["head"] = commit_sha

        validate_state_doc("task", task_data, file_path=task_file)
        atomic_write_json(task_file, task_data)

    # 5. Switch back to recorded branch
    recorded_branch = task_data.get("recorded_branch") or "main"
    git.run_git(["checkout", recorded_branch], root, check=True)

    msg = f"Task '{task_id}' aborted. Switched back to '{recorded_branch}'."
    sys.stdout.write(msg + "\n")
    return msg


# =============================================================================
# 5. task_finish
# =============================================================================

def task_finish(
    repo_root: Union[str, Path],
    pr: bool = False,
    skip_caller_check: bool = False,
    dialog_fn: Optional[Callable[..., str]] = None,
) -> str:
    """Execute 'lintel task finish [--pr]' (T-FIN-20, T-FIN-21, T-FIN-22)."""
    root = Path(repo_root).resolve()
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise TaskControlRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active_info
    state_dir = root / ".lintel" / "state"
    task_file = state_dir / task_id / "task.json"

    # Human-only caller check H0
    if not skip_caller_check:
        cmd_str = f"task finish{' --pr' if pr else ''}"
        dummy_args = argparse.Namespace(subcommand="task", task_command="finish", pr=pr)
        caller.check_caller(cmd_str, dummy_args, repo_root=root, dialog_fn=dialog_fn)

    # Refusal T-HUM-05: run pending or active
    if is_run_pending_or_active(task_data, state_dir, task_id):
        raise TaskControlRefusalError(
            "Refused: a run is pending or active; lintel task wait (T-HUM-05)",
            row="T-HUM-05",
        )

    # Refusal T-FIN-21: state must be H.finish
    pos = flow.get_position(task_data)
    if pos.code != "H.finish":
        expected_act = flow.format_next_action(task_data, repo_root=root)
        raise TaskControlRefusalError(
            f"Refused: the docs update is not complete (the state is not H.finish); expected '{expected_act}' (T-FIN-21)",
            row="T-FIN-21",
        )

    head_sha = task_data.get("head") or task_data.get("recorded_head")
    if not head_sha:
        raise TaskControlRefusalError("Refused: no recorded head commit (T-FIN-20)", row="T-FIN-20")

    # 1. Write summary.md
    summary_text = render_summary_md(root, task_id, task_data=task_data)
    summary_file = root / ".lintel" / "tasks" / task_id / "summary.md"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    summary_file.write_text(summary_text, encoding="utf-8")

    # 2. Display final reviewer's lessons.md in pager
    lessons_text: Optional[str] = None
    final_rev_dir = root / ".lintel" / "tasks" / task_id / "final-reviewer"
    if final_rev_dir.is_dir():
        for l_file in sorted(final_rev_dir.glob("*/lessons.md")):
            try:
                lessons_text = l_file.read_text(encoding="utf-8")
                break
            except Exception:
                pass

    sys.stdout.write("=== Proposed Lessons Learned ===\n")
    if lessons_text:
        sys.stdout.write(lessons_text.strip() + "\n\n")
    else:
        sys.stdout.write("(No lessons.md proposed by final reviewer)\n\n")

    with state_lock(state_dir):
        task_data = load_state_file(task_file, doc_type="task")

        # 3. Log finish event
        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq
        row_id = "T-FIN-22" if pr else "T-FIN-20"
        ev = create_event(
            step=event_seq,
            event="finish",
            stage=pos.stage,
            row=row_id,
        )
        append_event(state_dir, task_id, ev, task_dir=root / ".lintel" / "tasks" / task_id)
        update_progress_md(root, task_data, "none")

        # 4. Commit task folder as chore(lintel): task record
        commit_sha = commit_task_record(root, task_id, head_sha, event_seq)
        task_data["head"] = commit_sha
        task_data["status"] = "finished"
        task_data["finished_at"] = _now_iso()

        update_last_human_command(state_dir, task_id, f"task finish{' --pr' if pr else ''}", event_seq)

        validate_state_doc("task", task_data, file_path=task_file)
        atomic_write_json(task_file, task_data)

        # 5. Record finished task in .lintel/state/finished.json
        finished_file = state_dir / "finished.json"
        finished_data: Dict[str, Any] = {"version": 1, "tasks": []}
        if finished_file.is_file():
            try:
                finished_data = load_state_file(finished_file, doc_type="finished")
            except Exception:
                finished_data = {"version": 1, "tasks": []}

        tasks_list = finished_data.setdefault("tasks", [])
        tasks_list.append({
            "id": task_id,
            "title": task_data.get("title", ""),
            "branch": f"lintel/{task_id}",
            "recorded_branch": task_data.get("recorded_branch", "main"),
            "base_sha": task_data.get("base_sha", ""),
            "head": commit_sha,
            "finished_at": task_data["finished_at"],
            "pr": pr,
        })
        validate_state_doc("finished", finished_data, file_path=finished_file)
        atomic_write_json(finished_file, finished_data)

    recorded_branch = task_data.get("recorded_branch") or "main"
    pr_out: Optional[str] = None

    # 6. PR handling (T-FIN-22)
    if pr:
        task_title = task_data.get("title", f"lintel: {task_id}")
        gh_bin = shutil.which("gh")
        glab_bin = shutil.which("glab")

        if gh_bin:
            # gh pr create
            git.run_git(["push", "origin", f"lintel/{task_id}"], root, check=False)
            res = subprocess.run(
                [gh_bin, "pr", "create", "--title", task_title, "--body-file", str(summary_file)],
                cwd=str(root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            pr_out = res.stdout.strip() if (res.returncode == 0 and res.stdout.strip()) else (res.stdout.strip() or res.stderr.strip() or "gh pr create executed")

        elif glab_bin:
            # glab mr create
            git.run_git(["push", "origin", f"lintel/{task_id}"], root, check=False)
            res = subprocess.run(
                [glab_bin, "mr", "create", "--title", task_title, "--description", summary_text],
                cwd=str(root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            pr_out = res.stdout.strip() if (res.returncode == 0 and res.stdout.strip()) else (res.stdout.strip() or res.stderr.strip() or "glab mr create executed")

        else:
            # Neither tool available: print push command and PR text
            push_cmd = f"git push origin lintel/{task_id}"
            pr_out = f"Push command:\n{push_cmd}\n\nPR text:\n{summary_text}"

    # 7. Switch back to recorded branch
    git.run_git(["checkout", recorded_branch], root, check=True)

    if pr_out:
        sys.stdout.write(pr_out + "\n")
        return pr_out

    msg = f"Task '{task_id}' finished. Switched back to '{recorded_branch}'."
    sys.stdout.write(msg + "\n")
    return msg
