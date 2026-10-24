"""Decide command implementation (M1a-29).

Implements:
1. decide (PLAN §3.9, docs/state-machine.md §2.6, §3.8 T-STL-20..37, T-HUM-40, T-STG-21):
   - Human-only: confirmed by macOS dialog (H0), refused if non-ok (check_caller).
   - NR check: refused if run is pending or active (T-HUM-05).
   - Check active task (T-STG-09).
   - Guard T-STL-21: state must be an escalation D.*.
   - Stage validation (T-STL-20, T-STG-21, T-STG-22).
   - Pair validation (T-STL-23):
     - D.final-no-change: no --pair allowed.
     - Other kinds: must match escalation writer.
     - If omitted, implied from escalation.
   - Escalation x options table validation (§2.6, T-STL-22):
     - Exactly one option must be chosen.
     - D.stall: --guidance, --accept, --reset (DEV locked & undelivered), --abort, (docs also --skip-docs).
     - D.requirements: --guidance (defers), --abort, (docs also --skip-docs).
     - D.po-no-change: --guidance (to blocked writer), --abort.
     - D.environment: --retry, --guidance, --abort, (docs also --skip-docs).
     - D.repeat-blocked: --guidance, --reset (DEV locked & undelivered), --abort.
     - D.final-no-change: --to final, --to docs, --abort (no --pair).
   - Pager display & confirmation (T-HUM-08):
     - reason, ruling, open findings, last reports.
     - auto-confirm in non-interactive/tests.
   - State side effects & transitions (via flow.on_decide):
     - stall counters restart in loops.json.
     - blocked memory cleared.
     - escalation cleared.
     - option-specific routines:
       - --reset: T-AMD-60 reset routine.
       - --skip-docs: T-STL-35 snapshot & restore specs and steering, outcome="skipped", H.finish.
       - --abort: T-FIN-30 abort routine.
       - --accept: T-STL-30 valid review recorded, advance to approval state.
       - --retry: T-STL-31 advance to W.<writer>.
       - --to: T-STL-33 / T-STL-34 advance to final or docs.
       - --guidance: T-STL-24..27 guidance saved, advance to writer (defers requirements problem).
     - Updates acks.last_human.
     - Logs decide event in events.jsonl.
     - Updates progress.md.
     - Prints next action.
"""

import sys
sys.dont_write_bytecode = True

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

try:
    from lintel import (
        caller,
        config as config_mod,
        flow,
        git,
        loops,
        paths,
        restore,
        reviews,
        state,
    )
    from lintel.events import append_event, create_event
    from lintel.restore import is_protected_reset_path, update_last_human_command
    from lintel.state import (
        atomic_write_json,
        load_state_file,
        resolve_state_root,
        state_lock,
        validate_state_doc,
    )
    from lintel.task_start import find_active_task_in_state
except ImportError:
    from . import (
        caller,
        config as config_mod,
        flow,
        git,
        loops,
        paths,
        restore,
        reviews,
        state,
    )
    from .events import append_event, create_event
    from .restore import is_protected_reset_path, update_last_human_command
    from .state import (
        atomic_write_json,
        load_state_file,
        resolve_state_root,
        state_lock,
        validate_state_doc,
    )
    from .task_start import find_active_task_in_state


class DecideRefusalError(flow.FlowRefusalError):
    """Raised when decide is refused per state machine rules."""

    def __init__(self, message: str, code: int = 1, row: Optional[str] = None):
        super().__init__(message=message, code=code, row=row)


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_run_pending_or_active(
    task_data: Dict[str, Any],
    state_dir: Path,
    task_id: str,
) -> bool:
    """Check if any persona run is currently pending or active (T-HUM-05)."""
    return restore.is_run_pending_or_active(task_data, state_dir, task_id)


def format_decide_pager(
    repo_root: Path,
    task_id: str,
    stage: str,
    kind: str,
    writer: Optional[str],
    task_data: Dict[str, Any],
) -> str:
    """Format escalation review details for the decide pager per PLAN §3.9."""
    lines: List[str] = [f"=== Escalation Decision: D.{kind} ({stage}) ==="]

    esc = task_data.get("escalation") or {}
    reason = esc.get("reason") or esc.get("cause") or kind
    lines.append("\n--- Escalation Reason ---")
    lines.append(f"Kind: D.{kind}")
    lines.append(f"Stage: {stage}")
    if writer:
        lines.append(f"Writer: {writer}")
    lines.append(f"Reason: {reason}")

    # Ruling (if any)
    ruling = esc.get("ruling")
    if not ruling and writer:
        arb_file = repo_root / ".lintel" / "tasks" / task_id / "arbiter" / f"{writer}-{stage}-1" / "ruling.json"
        if arb_file.is_file():
            try:
                ruling = arb_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass
    if ruling:
        lines.append("\n--- Ruling ---")
        lines.append(str(ruling))

    # Open findings
    state_dir = repo_root / ".lintel" / "state"
    loops_file = state_dir / task_id / "loops.json"
    open_findings = []
    if loops_file.is_file() and writer:
        try:
            ld = json.loads(loops_file.read_text(encoding="utf-8"))
            loop_key = f"{writer}.{stage}"
            loop = ld.get("loops", {}).get(loop_key, {})
            open_findings = [f for f in loop.get("findings", []) if f.get("status") == "open"]
        except Exception:
            pass
    lines.append("\n--- Open Findings ---")
    if open_findings:
        for f in open_findings:
            fid = f.get("id", "?")
            sev = f.get("severity", "?")
            summ = f.get("summary", "")
            lines.append(f"- [{sev}] {fid}: {summ}")
    else:
        lines.append("(No open findings)")

    # Last reports
    lines.append("\n--- Last Reports ---")
    run_id = esc.get("run")
    report_shown = False
    if run_id:
        run_file = state_dir / task_id / "runs" / f"{run_id}.json"
        if run_file.is_file():
            try:
                rd = json.loads(run_file.read_text(encoding="utf-8"))
                folder_rel = rd.get("folder")
                if folder_rel:
                    rep_path = repo_root / folder_rel / "report.md"
                    if rep_path.is_file():
                        lines.append(f"[{run_id} report.md]")
                        lines.append(rep_path.read_text(encoding="utf-8").strip())
                        report_shown = True
            except Exception:
                pass
    if not report_shown:
        lines.append("(No last report available)")

    return "\n".join(lines)


def _check_abort_protected_paths(repo_root: Path, head_sha: str, in_docs: bool) -> List[str]:
    """Check for uncommitted edits in config, scripts, personas, and steering (T-FIN-32)."""
    check_paths = [".lintel/config.jsonc", ".lintel/scripts", ".lintel/personas"]
    if not in_docs:
        check_paths.append(".lintel/steering")

    dirty: List[str] = []
    diff_res = git.run_git(["diff", "--name-only", head_sha, "--", *check_paths], repo_root, check=False)
    if diff_res.returncode == 0 and diff_res.stdout.strip():
        dirty.extend(diff_res.stdout.splitlines())

    stat_res = git.run_git(["status", "--porcelain", "--", *check_paths], repo_root, check=False)
    if stat_res.returncode == 0 and stat_res.stdout.strip():
        for line in stat_res.stdout.splitlines():
            line = line.strip()
            if line:
                parts = line.split(maxsplit=1)
                if len(parts) == 2 and parts[1] not in dirty:
                    dirty.append(parts[1])

    return [p.strip() for p in dirty if p.strip()]


def decide(
    repo_root: Optional[Union[str, Path]] = None,
    phase: Optional[Union[int, str]] = None,
    pair: Optional[str] = None,
    guidance: Optional[str] = None,
    accept: bool = False,
    retry: bool = False,
    reset: bool = False,
    to: Optional[str] = None,
    skip_docs: bool = False,
    abort: bool = False,
    skip_caller_check: bool = False,
    dialog_fn: Optional[Callable] = None,
    confirm: Optional[bool] = None,
) -> str:
    """Resolve an escalation per PLAN §3.9 and docs/state-machine.md §2.6."""
    if repo_root is None:
        root = Path.cwd()
    else:
        root = Path(repo_root).resolve()

    state_dir = root / ".lintel" / "state"

    # 1. Check active task (T-STG-09)
    active = find_active_task_in_state(root)
    if not active:
        raise DecideRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active
    task_state_file = state_dir / task_id / "task.json"
    task_data = load_state_file(task_state_file, doc_type="task")

    # 2. Human-only caller check H0 (unless skip_caller_check)
    if not skip_caller_check:
        cmd_parts = ["decide"]
        if phase is not None:
            cmd_parts.append(f"--phase {phase}")
        if pair is not None:
            cmd_parts.append(f"--pair {pair}")
        if guidance is not None:
            cmd_parts.append(f'--guidance "{guidance}"')
        if accept:
            cmd_parts.append("--accept")
        if retry:
            cmd_parts.append("--retry")
        if reset:
            cmd_parts.append("--reset")
        if to is not None:
            cmd_parts.append(f"--to {to}")
        if skip_docs:
            cmd_parts.append("--skip-docs")
        if abort:
            cmd_parts.append("--abort")
        cmd_str = " ".join(cmd_parts)
        args_ns = argparse.Namespace(
            subcommand="decide",
            phase=phase,
            pair=pair,
            guidance=guidance,
            accept=accept,
            retry=retry,
            reset=reset,
            to=to,
            skip_docs=skip_docs,
            abort=abort,
        )
        caller.check_caller(cmd_str, args_ns, repo_root=root, dialog_fn=dialog_fn)

    # 3. NR check: refuse if run is pending or active (T-HUM-05)
    if _is_run_pending_or_active(task_data, state_dir, task_id):
        raise DecideRefusalError(
            "Refused: a run is pending or active; run 'lintel task wait' (T-HUM-05)",
            row="T-HUM-05",
        )

    # 4. Guard T-STL-21: state must be an escalation D.*
    pos = flow.get_position(task_data)
    clean_code = pos.code.replace("+FR", "")
    if not clean_code.startswith("D."):
        expected_act = flow.format_next_action(task_data, repo_root=root)
        raise DecideRefusalError(
            f"Refused: no escalation pending; expected '{expected_act}' (T-STL-21)",
            row="T-STL-21",
        )

    esc = task_data.get("escalation") or {}
    esc_stage = esc.get("stage") or pos.stage
    kind = esc.get("kind") or clean_code[2:]
    if kind.startswith("D."):
        kind = kind[2:]

    # 5. Stage check (T-STL-20, T-STG-21, T-STG-22)
    if phase is not None:
        norm_phase = flow.normalize_phase_arg(phase)
        if norm_phase != esc_stage:
            raise DecideRefusalError(
                f"Refused: phase argument '{phase}' does not match expected stage '{esc_stage}' (T-STG-21)",
                row="T-STG-21",
            )

    # 6. --pair validation (T-STL-23)
    if kind == "final-no-change":
        if pair is not None:
            raise DecideRefusalError(
                "Refused: no --pair allowed on final-no-change (T-STL-23)",
                row="T-STL-23",
            )
        expected_writer = None
    else:
        if kind == "po-no-change":
            expected_writer = (task_data.get("route") or {}).get("writer") or "dev"
        else:
            expected_writer = esc.get("writer") or "dev"

        if pair is not None and pair != expected_writer:
            raise DecideRefusalError(
                f"Refused: --pair '{pair}' does not match escalation writer '{expected_writer}' (T-STL-23)",
                row="T-STL-23",
            )
        if pair is None:
            pair = expected_writer

    # 7. Escalation x options validation (§2.6, T-STL-22)
    selected_opts: List[str] = []
    if guidance is not None:
        selected_opts.append("guidance")
    if accept:
        selected_opts.append("accept")
    if retry:
        selected_opts.append("retry")
    if reset:
        selected_opts.append("reset")
    if to is not None:
        selected_opts.append(f"to_{to}")
    if skip_docs:
        selected_opts.append("skip_docs")
    if abort:
        selected_opts.append("abort")

    if len(selected_opts) == 0:
        raise DecideRefusalError(
            "Refused: must specify a decide option (T-STL-22)",
            row="T-STL-22",
        )
    if len(selected_opts) > 1:
        raise DecideRefusalError(
            "Refused: exactly one decide option must be specified (T-STL-22)",
            row="T-STL-22",
        )

    opt = selected_opts[0]
    in_docs = (esc_stage == "docs")

    # Options validity per escalation kind (§2.6)
    if kind == "stall":
        allowed = {"guidance", "accept", "abort", "reset"}
        if in_docs:
            allowed.add("skip_docs")
    elif kind == "requirements":
        allowed = {"guidance", "abort"}
        if in_docs:
            allowed.add("skip_docs")
    elif kind == "po-no-change":
        allowed = {"guidance", "abort"}
    elif kind == "environment":
        allowed = {"retry", "guidance", "abort"}
        if in_docs:
            allowed.add("skip_docs")
    elif kind == "repeat-blocked":
        allowed = {"guidance", "abort", "reset"}
    elif kind == "final-no-change":
        allowed = {"to_final", "to_docs", "abort"}
    else:
        allowed = {"abort"}

    # Validate option against allowed set
    if opt.startswith("to_"):
        if opt not in ("to_final", "to_docs"):
            raise DecideRefusalError(
                "Refused: --to must be 'final' or 'docs' (T-STL-22)",
                row="T-STL-22",
            )
        if kind != "final-no-change":
            raise DecideRefusalError(
                "Refused: --to only allowed on D.final-no-change (T-STL-22)",
                row="T-STL-22",
            )

    if opt not in allowed:
        if opt == "skip_docs" and not in_docs:
            raise DecideRefusalError(
                "Refused: --skip-docs only allowed in docs update (T-STL-35)",
                row="T-STL-35",
            )
        opt_display = f"--{opt.replace('_', '-')}"
        allowed_display = ", ".join(f"--{a.replace('_', '-')}" for a in sorted(allowed))
        raise DecideRefusalError(
            f"Refused: option '{opt_display}' not allowed on D.{kind}; allowed options: {allowed_display} (T-STL-22)",
            row="T-STL-22",
        )

    # Detailed guards for specific options
    p_num = pos.phase
    if p_num is None and esc_stage.startswith("p") and esc_stage[1:].isdigit():
        p_num = int(esc_stage[1:])

    if opt == "reset":
        # DEV only, locked and undelivered
        if expected_writer != "dev":
            raise DecideRefusalError(
                "Refused: --reset only allowed for DEV pair (T-STL-22)",
                row="T-STL-22",
            )
        if p_num is None or not (flow.is_phase_locked(task_data, p_num) and not flow.is_phase_delivered(task_data, p_num)):
            raise DecideRefusalError(
                "Refused: --reset only allowed for DEV pair when phase is locked and not delivered (T-STL-22)",
                row="T-STL-22",
            )

    # 8. Pager display & human confirmation (T-HUM-08)
    pager_text = format_decide_pager(
        repo_root=root,
        task_id=task_id,
        stage=esc_stage,
        kind=kind,
        writer=expected_writer,
        task_data=task_data,
    )
    sys.stdout.write(pager_text + "\n")

    do_confirm = confirm if confirm is not None else reviews.confirm_approval(
        f"Confirm decision for D.{kind}? [y/N]: "
    )
    if not do_confirm:
        act = flow.format_next_action(task_data, repo_root=root)
        sys.stdout.write(f"Decision canceled (T-HUM-08).\n{act}\n")
        return act

    # 9. Execute option routine and state transition
    head_sha = task_data.get("head") or task_data.get("recorded_head") or ""

    if opt == "abort":
        # T-FIN-30 / T-STL-36
        dirty_protected = _check_abort_protected_paths(root, head_sha, in_docs=in_docs)
        if dirty_protected:
            raise DecideRefusalError(
                f"Refused: uncommitted edits in protected paths: {', '.join(dirty_protected)} (T-FIN-32)",
                row="T-FIN-32",
            )

        # 1. Snapshot change set to refs/lintel/<id>/snapshot-<n>
        git.create_snapshot_ref(root, task_id, kind="abort", parent=head_sha)

        # 2. Restore change set paths and locked files to recorded head
        res_cfg = git.run_git(["show", f"{head_sha}:.lintel/config.jsonc"], root, check=False)
        if res_cfg.returncode == 0 and res_cfg.stdout.strip():
            cfg = config_mod.load_config(res_cfg.stdout, workspace_root=root)
        else:
            cfg_file = root / ".lintel" / "config.jsonc"
            cfg = config_mod.load_config(cfg_file, workspace_root=root) if cfg_file.is_file() else {}

        cs = git.get_change_set(root, head_sha, config=cfg)
        for entry in cs.entries.values():
            if not is_protected_reset_path(entry.path):
                if entry.is_added():
                    target_file = root / entry.path
                    if target_file.is_file() or target_file.is_symlink():
                        target_file.unlink()
                else:
                    git.run_git(["checkout", head_sha, "--", entry.path], root, check=False)

        if in_docs:
            git.run_git(["checkout", head_sha, "--", ".lintel/specs"], root, check=False)
            git.run_git(["checkout", head_sha, "--", ".lintel/steering"], root, check=False)

        # 3. Commit task folder to task branch as chore(lintel): task record
        task_dir = root / ".lintel" / "tasks" / task_id
        if task_dir.is_dir():
            git.run_git(["add", "-f", "--", f".lintel/tasks/{task_id}"], root, check=False)
            tree_res = git.run_git(["write-tree"], root, check=False)
            if tree_res.returncode == 0:
                t_tree = tree_res.stdout.strip()
                c_sha = git.create_commit(
                    repo_root=root,
                    tree_id=t_tree,
                    parent=head_sha,
                    message="chore(lintel): task record",
                    author_role="lintel",
                )
                git.run_git(["update-ref", f"refs/heads/lintel/{task_id}", c_sha], root, check=False)

        # 4. Switch back to recorded branch
        rec_branch = task_data.get("recorded_branch") or "main"
        git.run_git(["checkout", rec_branch], root, check=False)

        # 5. State updates
        task_data["status"] = "aborted"
        task_data["finished_at"] = _now_iso()
        task_data["cycle"] = None
        task_data["escalation"] = None
        task_data["memory"] = []
        flow.set_position(task_data, stage=esc_stage, code="NONE", phase=pos.phase, fr=False, run=None)

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq
        event_rec = create_event(
            step=event_seq,
            event="decide",
            stage=esc_stage,
            role=expected_writer,
            decision="abort",
            row="T-STL-36",
        )
        append_event(state_dir=state_dir, task_id=task_id, event_data=event_rec, task_dir=task_dir)

        update_last_human_command(state_dir, task_id, f"decide --phase {esc_stage} --abort", event_seq)

        with state_lock(state_dir):
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

        msg = "Task aborted."
        sys.stdout.write(msg + "\n")
        return msg

    if opt == "reset":
        # T-AMD-60 / T-STL-32
        # Snapshot
        git.create_snapshot_ref(root, task_id, kind="reset", parent=head_sha)

        # Reload state
        task_data = load_state_file(task_state_file, doc_type="task")

        # Load config
        res_cfg = git.run_git(["show", f"{head_sha}:.lintel/config.jsonc"], root, check=False)
        if res_cfg.returncode == 0 and res_cfg.stdout.strip():
            cfg = config_mod.load_config(res_cfg.stdout, workspace_root=root)
        else:
            cfg_file = root / ".lintel" / "config.jsonc"
            cfg = config_mod.load_config(cfg_file, workspace_root=root) if cfg_file.is_file() else {}

        cs = git.get_change_set(root, head_sha, config=cfg)
        paths_to_checkout: List[str] = []
        paths_to_delete: List[str] = []
        for entry in cs.entries.values():
            if is_protected_reset_path(entry.path):
                continue
            if entry.is_added():
                paths_to_delete.append(entry.path)
            else:
                paths_to_checkout.append(entry.path)

        # Restore locked files
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

        for rel_path in sorted(set(paths_to_checkout)):
            git.run_git(["checkout", head_sha, "--", rel_path], root, check=False)

        for rel_path in sorted(set(paths_to_delete)):
            target_file = root / rel_path
            if target_file.is_file() or target_file.is_symlink():
                target_file.unlink()
            parent_d = target_file.parent
            while parent_d != root:
                try:
                    parent_d.rmdir()
                    parent_d = parent_d.parent
                except OSError:
                    break

        # State updates per T-AMD-60 / T-STL-32
        task_data["cycle"] = None
        task_data["memory"] = []
        task_data["escalation"] = None
        loops.reset_stall_counters(state_dir, task_id, writer="dev", stage=f"p{p_num}")
        phases_rec = task_data.setdefault("phases", {})
        phases_rec.setdefault(str(p_num), {})["dev_window_since"] = _now_iso()

        flow.set_position(task_data, stage=f"p{p_num}", code="W.dev", phase=p_num, fr=False, run=None)

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq
        event_rec = create_event(
            step=event_seq,
            event="decide",
            stage=f"p{p_num}",
            role="dev",
            decision="reset",
            row="T-STL-32",
        )
        append_event(state_dir=state_dir, task_id=task_id, event_data=event_rec, task_dir=root / ".lintel" / "tasks" / task_id)

        update_last_human_command(state_dir, task_id, f"decide --phase p{p_num} --reset", event_seq)

        with state_lock(state_dir):
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

        next_action = f"lintel invoke dev --phase p{p_num}"
        reviews.update_progress_md(root, task_data, next_action)
        sys.stdout.write(next_action + "\n")
        return next_action

    if opt == "skip_docs":
        # T-STL-35
        # 1. Snapshot uncommitted edits in .lintel/specs/ and .lintel/steering/
        git.create_snapshot_ref(root, task_id, kind="skip-docs", parent=head_sha)

        # 2. Restore both to recorded head
        git.run_git(["checkout", head_sha, "--", ".lintel/specs"], root, check=False)
        git.run_git(["checkout", head_sha, "--", ".lintel/steering"], root, check=False)

        # Remove untracked files in specs or steering that were not in head
        head_paths: Set[str] = set()
        res_tree = git.run_git(["ls-tree", "-r", "--name-only", head_sha, "--", ".lintel/specs", ".lintel/steering"], root, check=False)
        if res_tree.returncode == 0 and res_tree.stdout.strip():
            head_paths = set(res_tree.stdout.splitlines())

        for sdir in (root / ".lintel" / "specs", root / ".lintel" / "steering"):
            if sdir.is_dir():
                for p in sdir.rglob("*"):
                    if p.is_file():
                        rel = p.relative_to(root).as_posix()
                        if rel not in head_paths:
                            p.unlink()

        # 3. State updates
        docs_up = task_data.get("docs_update")
        if not isinstance(docs_up, dict):
            docs_up = {}
            task_data["docs_update"] = docs_up
        docs_up["outcome"] = "skipped"
        task_data["memory"] = []
        task_data["escalation"] = None
        loops.reset_stall_counters(state_dir, task_id, writer=expected_writer or "po", stage="docs")

        flow.set_position(task_data, stage="docs", code="H.finish", phase=None, fr=False, run=None)

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq
        event_rec = create_event(
            step=event_seq,
            event="decide",
            stage="docs",
            role=expected_writer,
            decision="skip-docs",
            row="T-STL-35",
        )
        append_event(state_dir=state_dir, task_id=task_id, event_data=event_rec, task_dir=root / ".lintel" / "tasks" / task_id)

        update_last_human_command(state_dir, task_id, "decide --phase docs --skip-docs", event_seq)

        with state_lock(state_dir):
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

        next_action = "human: lintel task finish [--pr]"
        reviews.update_progress_md(root, task_data, next_action)
        sys.stdout.write(next_action + "\n")
        return next_action

    if opt == "to_final":
        # T-STL-33
        task_data["memory"] = []
        task_data["escalation"] = None
        loops.reset_stall_counters(state_dir, task_id, writer="final-reviewer", stage="final")

        flow.set_position(task_data, stage="final", code="W.final-reviewer", phase=None, fr=False, run=None)

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq
        event_rec = create_event(
            step=event_seq,
            event="decide",
            stage="final",
            role="final-reviewer",
            decision="to-final",
            row="T-STL-33",
        )
        append_event(state_dir=state_dir, task_id=task_id, event_data=event_rec, task_dir=root / ".lintel" / "tasks" / task_id)

        update_last_human_command(state_dir, task_id, "decide --to final", event_seq)

        with state_lock(state_dir):
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

        next_action = "lintel invoke final-reviewer --phase final"
        reviews.update_progress_md(root, task_data, next_action)
        sys.stdout.write(next_action + "\n")
        return next_action

    if opt == "to_docs":
        # T-STL-34
        task_data["docs_update"] = {
            "po_finished": False,
            "architect_finished": False,
            "po_changed": None,
            "architect_changed": None,
            "outcome": None,
        }
        task_data["memory"] = []
        task_data["escalation"] = None
        loops.reset_stall_counters(state_dir, task_id, writer="po", stage="docs")

        flow.set_position(task_data, stage="docs", code="W.po", phase=None, fr=False, run=None)

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq
        event_rec = create_event(
            step=event_seq,
            event="decide",
            stage="docs",
            role="po",
            decision="to-docs",
            row="T-STL-34",
        )
        append_event(state_dir=state_dir, task_id=task_id, event_data=event_rec, task_dir=root / ".lintel" / "tasks" / task_id)

        update_last_human_command(state_dir, task_id, "decide --to docs", event_seq)

        with state_lock(state_dir):
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

        next_action = "lintel invoke po --phase docs"
        reviews.update_progress_md(root, task_data, next_action)
        sys.stdout.write(next_action + "\n")
        return next_action

    if opt == "retry":
        # T-STL-31
        task_data["memory"] = []
        task_data["escalation"] = None
        loops.reset_stall_counters(state_dir, task_id, writer=expected_writer, stage=esc_stage)

        flow.set_position(task_data, stage=esc_stage, code=f"W.{expected_writer}", phase=pos.phase, fr=False, run=None)

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq
        event_rec = create_event(
            step=event_seq,
            event="decide",
            stage=esc_stage,
            role=expected_writer,
            decision="retry",
            row="T-STL-31",
        )
        append_event(state_dir=state_dir, task_id=task_id, event_data=event_rec, task_dir=root / ".lintel" / "tasks" / task_id)

        update_last_human_command(state_dir, task_id, f"decide --phase {esc_stage} --retry", event_seq)

        with state_lock(state_dir):
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

        next_action = f"lintel invoke {expected_writer} --phase {esc_stage}"
        reviews.update_progress_md(root, task_data, next_action)
        sys.stdout.write(next_action + "\n")
        return next_action

    if opt == "accept":
        # T-STL-30
        task_data["memory"] = []
        task_data["escalation"] = None
        loops.reset_stall_counters(state_dir, task_id, writer=expected_writer, stage=esc_stage)

        # Record valid review in reviews.json
        phase_num = pos.phase
        if phase_num is None and esc_stage.startswith("p") and esc_stage[1:].isdigit():
            phase_num = int(esc_stage[1:])

        reviews.record_review(
            repo_root=root,
            task_id=task_id,
            pair=expected_writer,
            stage=esc_stage,
            phase=phase_num,
            verdict="accept",
            kind="accept",
            task_data=task_data,
        )

        # Determine target state
        if kind == "po-no-change":
            target_code = "W.architect"
        elif esc_stage == "docs":
            if expected_writer == "architect":
                target_code = "H.docs"
            elif expected_writer == "po":
                if task_data.get("docs_update", {}).get("architect_finished"):
                    target_code = "H.docs"
                else:
                    target_code = "W.architect"
            else:
                target_code = "H.docs"
        else:
            target_code = "H.req"
            if expected_writer == "architect":
                target_code = "H.plan"
            elif expected_writer == "qa":
                target_code = "H.amend" if flow.is_amend_cycle_open(task_data) else "H.lock"
            elif expected_writer == "dev":
                target_code = "O.deliver"

        flow.set_position(task_data, stage=esc_stage, code=target_code, phase=pos.phase, fr=False, run=None)

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq
        event_rec = create_event(
            step=event_seq,
            event="decide",
            stage=esc_stage,
            role=expected_writer,
            decision="accept",
            row="T-STL-30",
        )
        append_event(state_dir=state_dir, task_id=task_id, event_data=event_rec, task_dir=root / ".lintel" / "tasks" / task_id)

        update_last_human_command(state_dir, task_id, f"decide --phase {esc_stage} --accept", event_seq)

        with state_lock(state_dir):
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

        next_action = flow.format_next_action(task_data, repo_root=root)
        reviews.update_progress_md(root, task_data, next_action)
        sys.stdout.write(next_action + "\n")
        return next_action

    if opt == "guidance":
        # T-STL-24, T-STL-25, T-STL-26, T-STL-27
        if kind == "requirements":
            route = task_data.get("route") or {}
            blocked_w = route.get("writer") or expected_writer
            run_id = route.get("from_run") or esc.get("run") or "run"

            dp_list = task_data.setdefault("deferred", [])
            dp_id = f"DP{len(dp_list) + 1}"
            dp_list.append({
                "id": dp_id,
                "stage": esc_stage,
                "writer": blocked_w,
                "run": run_id,
                "heading": "Requirements problem",
                "revisit_stage": None,
                "in_docs": in_docs,
            })

            if in_docs:
                target_writer = "architect"
                target_code = "W.architect"
                row_id = "T-RVT-05"
            else:
                target_writer = blocked_w
                target_code = f"W.{blocked_w}"
                row_id = "T-RVT-03"

        elif kind == "po-no-change":
            route = task_data.get("route") or {}
            blocked_w = route.get("writer") or expected_writer
            target_writer = blocked_w
            target_code = f"W.{blocked_w}"
            row_id = "T-STL-26"

        else:
            target_writer = expected_writer or "architect"
            target_code = f"W.{target_writer}"
            row_id = "T-STL-27" if kind == "environment" else "T-STL-24"

        task_data["memory"] = []
        task_data["escalation"] = None
        loops.reset_stall_counters(state_dir, task_id, writer=target_writer, stage=esc_stage)

        # Save guidance in loops.json
        if guidance:
            loops_data = loops.load_loops(state_dir, task_id)
            lp = loops.get_or_create_loop(loops_data, target_writer, esc_stage)
            lp.setdefault("guidance", []).append(guidance)
            loops.save_loops(state_dir, task_id, loops_data)

            # Save in run folder if available
            run_id = esc.get("run")
            if run_id:
                run_file = state_dir / task_id / "runs" / f"{run_id}.json"
                if run_file.is_file():
                    try:
                        rd = json.loads(run_file.read_text(encoding="utf-8"))
                        f_rel = rd.get("folder")
                        if f_rel:
                            guid_file = root / f_rel / "guidance.md"
                            guid_file.write_text(guidance, encoding="utf-8")
                    except Exception:
                        pass

        flow.set_position(task_data, stage=esc_stage, code=target_code, phase=pos.phase, fr=False, run=None)

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq
        event_rec = create_event(
            step=event_seq,
            event="decide",
            stage=esc_stage,
            role=target_writer,
            decision="guidance",
            row=row_id,
        )
        append_event(state_dir=state_dir, task_id=task_id, event_data=event_rec, task_dir=root / ".lintel" / "tasks" / task_id)

        update_last_human_command(state_dir, task_id, f"decide --phase {esc_stage} --guidance", event_seq)

        with state_lock(state_dir):
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

        next_action = flow.format_next_action(task_data, repo_root=root)
        reviews.update_progress_md(root, task_data, next_action)
        sys.stdout.write(next_action + "\n")
        return next_action

    raise DecideRefusalError(f"Refused: unknown decide option '{opt}' (T-STL-22)", row="T-STL-22")
