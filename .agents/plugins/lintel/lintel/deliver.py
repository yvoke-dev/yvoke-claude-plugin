"""Lintel check and deliver subcommands (M1a-27).

Implements:
1. `check`:
   - Phase validation: phase must be locked and not delivered (T-AMD-22).
   - Candidate change set and candidate tree ID against recorded head.
   - Symlink check: refuse if candidate change set holds symlink pointing outside repo (T-AMD-23).
   - Single-flight check (T-STG-15, T-STG-16):
     - If running.json is live for the same tree and command (check or check-start), report running gate.
     - If running.json is live for another tree or gate command, refuse (T-STG-15).
   - Normal `check`:
     - Writes running.json on entry (command="check", detached=False) via running_gate context manager.
     - Runs gate in worktree in stop_at_first_red mode (GateMode.STOP_AT_FIRST_RED).
     - Logs to gate/check-pN-<seq>.log.
     - Records result in checks.json bound to candidate_tree.
     - Output: short summary (green/red, exit code per group, tail of output, log path; test paths if full).
   - `check --start`:
     - Detaches via background process with setsid (T-AMD-21).
     - Writes running.json on entry (command="check-start", detached=True).
     - Returns in under a second printing: "started, run `lintel task wait`".
2. `deliver`:
   - Check active task and state must be p<N>:O.deliver (T-AMD-38).
   - NR check: refuse if run is pending or active (T-HUM-05).
   - Target phase check (T-STG-21).
   - Candidate change set & symlink check (T-AMD-23).
   - Rejection checks in order (PLAN §3.6, T-AMD-31..37):
     1. Valid DEV review VR(dev) (T-AMD-31): next dev-reviewer, state unchanged.
     2. Lane checks:
        - tests file changed/added/deleted (T-AMD-32): sets p<N>:W.dev, next: restore-tests, then dev.
        - locked file changed/deleted unless in task.phases.<N>.released (T-AMD-32).
        - approved shared/ documents changed (T-AMD-32).
        - untracked test-output files outside gitignore (T-AMD-32).
        - .lintel/ or plugin dir changed (T-AMD-33): sets p<N>:W.dev, next: revert files, then dev.
        - listed group no longer exists in config (T-AMD-34): state unchanged, next: amend --config.
     3. Drift check: candidate drift files match acks.json:drift.<N>.paths exactly (T-AMD-35): state unchanged.
     4. Audit check: DEV side crossings match audit --ack (no-op until M1b).
     5. Green gate check:
        - Look up recorded check in checks.json for candidate_tree: reuse if green!
        - Else run gate in gate worktree using running_gate(command="deliver").
        - If any group is red -> reject with T-AMD-37: sets p<N>:W.dev, next: dev.
     6. Successful delivery (T-AMD-30):
        - Commit candidate as feat(phase-N) as Lintel DEV <dev@noreply.local> with trailer Lintel-Step: <task_id>/<event_seq>.
        - Advance task branch and recorded head (task.head).
        - Mark phase delivered: task.phases.<N>.delivered, append N to task.delivered_phases.
        - Clear task.locked and task.phases.<N>.locked.
        - End amend cycle (task.cycle = None).
        - Set next state: r<N>:W.po (revisit cause next-phase per T-RVT-01).
        - Log event deliver in events.jsonl.
        - Summary output with log path and next action.
"""

import sys
sys.dont_write_bytecode = True

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

try:
    from lintel import (
        caller,
        config as config_mod,
        drift,
        events,
        flow,
        gate,
        git,
        lock,
        loops,
        paths,
        reviews,
        state,
        task_start,
    )
    from lintel.events import append_event, create_event
    from lintel.gate import GateMode, run_gate
    from lintel.state import (
        GateRefusedError,
        GateRunningReport,
        StateError,
        atomic_write_json,
        check_or_clear_stale_running,
        enter_gate,
        exit_gate,
        get_process_start_time,
        load_state_file,
        resolve_state_root,
        running_gate,
        state_lock,
        validate_state_doc,
    )
    from lintel.task_start import find_active_task_in_state
except ImportError:
    from . import (
        caller,
        config as config_mod,
        drift,
        events,
        flow,
        gate,
        git,
        lock,
        loops,
        paths,
        reviews,
        state,
        task_start,
    )
    from .events import append_event, create_event
    from .gate import GateMode, run_gate
    from .state import (
        GateRefusedError,
        GateRunningReport,
        StateError,
        atomic_write_json,
        check_or_clear_stale_running,
        enter_gate,
        exit_gate,
        get_process_start_time,
        load_state_file,
        resolve_state_root,
        running_gate,
        state_lock,
        validate_state_doc,
    )
    from .task_start import find_active_task_in_state


class DeliverRefusalError(flow.FlowRefusalError):
    """Raised when check or deliver command is refused or rejected."""

    def __init__(self, message: str, code: int = 1, row: Optional[str] = None):
        super().__init__(message=message, code=code, row=row)


class CheckRefusalError(DeliverRefusalError):
    """Raised when check command is refused."""
    pass


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# checks.json management (§9.9)
# ---------------------------------------------------------------------------

def get_checks_path(state_dir: Union[str, Path], task_id: str) -> Path:
    """Return path to .lintel/state/<task_id>/checks.json."""
    root = resolve_state_root(state_dir)
    return root / task_id / "checks.json"


def load_checks(state_dir: Union[str, Path], task_id: str) -> Dict[str, Any]:
    """Load checks.json for task_id, initializing if absent."""
    path = get_checks_path(state_dir, task_id)
    if not path.is_file():
        return {"version": 1, "checks": []}
    return load_state_file(path, doc_type="checks")


def record_check(
    state_dir: Union[str, Path],
    task_id: str,
    check_entry: Dict[str, Any],
) -> None:
    """Record a check result entry into checks.json under state lock."""
    path = get_checks_path(state_dir, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with state_lock(state_dir):
        doc = load_checks(state_dir, task_id)
        doc.setdefault("checks", []).append(check_entry)
        validate_state_doc("checks", doc, file_path=path)
        atomic_write_json(path, doc)


def find_recorded_check(
    state_dir: Union[str, Path],
    task_id: str,
    tree_id: str,
) -> Optional[Dict[str, Any]]:
    """Look up recorded check for a given candidate tree SHA in checks.json."""
    doc = load_checks(state_dir, task_id)
    # Search in reverse order to find latest check for this tree
    for c in reversed(doc.get("checks", [])):
        if c.get("tree_id") == tree_id:
            return c
    return None


# ---------------------------------------------------------------------------
# Shared Candidate & Lane Helpers
# ---------------------------------------------------------------------------

def _load_head_config(repo_root: Path, head_sha: str) -> dict:
    """Load configuration dictionary from recorded head commit."""
    proc = git.run_git(["show", f"{head_sha}:.lintel/config.jsonc"], repo_root, check=False)
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            return config_mod.load_config(proc.stdout, workspace_root=repo_root)
        except Exception:
            pass
    cfg_file = repo_root / ".lintel" / "config.jsonc"
    if cfg_file.is_file():
        try:
            return config_mod.load_config(cfg_file.read_text(encoding="utf-8"), workspace_root=repo_root)
        except Exception:
            pass
    return config_mod.load_config({}, workspace_root=repo_root)


def _check_candidate_symlinks(repo_root: Path, change_set: git.ChangeSet) -> None:
    """Refuse if any symlink in candidate change set points outside repository (T-AMD-23)."""
    for p, entry in change_set.entries.items():
        if entry.status != "D":
            full_p = repo_root / p
            if full_p.is_symlink():
                try:
                    target_str = os.readlink(full_p)
                    if git._is_symlink_outside(repo_root, p, target_str):
                        raise CheckRefusalError(
                            f"Refused: symlink '{p}' points outside repository to '{target_str}' (T-AMD-23)",
                            row="T-AMD-23",
                        )
                except OSError:
                    pass


def _get_outline_groups(repo_root: Path, task_id: str, phase_num: int, head_sha: Optional[str] = None) -> List[str]:
    """Extract test groups named in design.md ## Test outline without filtering by active config."""
    des_content = None
    for dname in (f"phase-{phase_num}", f"p{phase_num}"):
        cand = repo_root / "shared" / dname / "design.md"
        if not cand.is_file():
            cand = repo_root / ".lintel" / "tasks" / task_id / "shared" / dname / "design.md"
        if cand.is_file():
            des_content = cand.read_text(encoding="utf-8")
            break
    if des_content is None and head_sha:
        for dname in (f"phase-{phase_num}", f"p{phase_num}"):
            res = git.run_git(["show", f"{head_sha}:shared/{dname}/design.md"], repo_root, check=False)
            if res.returncode == 0 and res.stdout.strip():
                des_content = res.stdout
                break
    if not des_content:
        return []
    m = re.search(r"##\s+Test\s+outline\s*\n(.*?)(?=\n##|\Z)", des_content, re.DOTALL | re.IGNORECASE)
    if not m:
        return []
    groups = set()
    for line in m.group(1).splitlines():
        s_line = line.strip()
        if not s_line.startswith("-"):
            continue
        rest = s_line[1:].strip()
        none_m = re.match(r"^([a-zA-Z0-9_-]+)\s*:\s*none\b", rest, re.IGNORECASE)
        if none_m:
            continue
        test_m = re.match(r"^(R\d+)\s+([a-zA-Z0-9_-]+)\s*:\s*(.*)$", rest)
        if test_m:
            groups.add(test_m.group(2))
    return sorted(groups)


def _format_groups_summary(groups: Dict[str, Any]) -> str:
    """Format group exit codes as key=code summary."""
    parts = []
    for gname in sorted(groups.keys()):
        gres = groups[gname]
        if isinstance(gres, int):
            code = gres
        elif isinstance(gres, dict):
            code = gres.get("exit")
        else:
            code = getattr(gres, "exit_code", None)
        parts.append(f"{gname}={code}")
    return ", ".join(parts) if parts else "none"


# ---------------------------------------------------------------------------
# Background Worker for check --start
# ---------------------------------------------------------------------------

def _run_check_worker(
    repo_root: str,
    task_id: str,
    phase: int,
    candidate_tree: str,
    log_path: str,
    log_rel: str,
    seq: int,
    run_id: Optional[str] = None,
) -> None:
    """Background worker process executed by check --start after detaching."""
    root = Path(repo_root)
    state_dir = root / ".lintel" / "state"
    log_file = Path(log_path)
    reports_dir = root / ".lintel" / "tasks" / task_id / "gate" / f"reports-check-p{phase}-{seq}"
    reports_dir.mkdir(parents=True, exist_ok=True)

    task_data = load_state_file(state_dir / task_id / "task.json", doc_type="task")
    head_sha = task_data["head"]
    cfg = _load_head_config(root, head_sha)

    started_at = _now_iso()
    try:
        gate_res = run_gate(
            repo_root=root,
            command="check",
            mode=GateMode.STOP_AT_FIRST_RED,
            parent_commit=head_sha,
            log_path=log_file,
            reports_dir=reports_dir,
            state_dir=None,
            task_id=task_id,
            phase=phase,
            config=cfg,
        )
        finished_at = _now_iso()
        overall_green = gate_res.overall_green and gate_res.tests_clean
        res_str = "green" if overall_green else "red"

        check_entry = {
            "tree_id": candidate_tree,
            "phase": phase,
            "seq": seq,
            "result": res_str,
            "groups": {
                gname: {
                    "exit": gres.exit_code,
                    "status": gres.status,
                    "seconds": float(getattr(gres, "duration_seconds", 0.0)),
                }
                for gname, gres in gate_res.groups.items()
            },
            "log": log_rel,
            "started_at": started_at,
            "finished_at": finished_at,
            "run": run_id,
        }
        record_check(state_dir, task_id, check_entry)
    finally:
        # Clear running record on exit
        exit_gate(state_dir, pid=os.getpid(), force=True)


# ---------------------------------------------------------------------------
# 1. Check Command Implementation
# ---------------------------------------------------------------------------

def check(
    repo_root: Union[str, Path],
    phase: Optional[Union[int, str]] = None,
    start: bool = False,
    full: bool = False,
    run_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    config: Optional[dict] = None,
    skip_caller_check: bool = False,
) -> str:
    """Execute 'lintel check --phase N [--start] [--full]' per PLAN §3.5/§3.6 and T-AMD-20..23."""
    root = Path(repo_root).resolve()
    state_dir = root / ".lintel" / "state"

    # Step 0: Check running.json on entry (T-STG-15 / T-STG-16)
    running = check_or_clear_stale_running(state_dir)
    if running is not None:
        rec_cmd = running.get("command")
        rec_tree = running.get("tree_id")
        rec_pid = running.get("pid")
        rec_log = running.get("log") or running.get("log_path") or ""

        if rec_cmd in ("check", "check-start"):
            try:
                active_info = find_active_task_in_state(root)
                if active_info:
                    _, tdata = active_info
                    hsha = tdata.get("head")
                    if hsha:
                        hcfg = config or _load_head_config(root, hsha)
                        cs = git.get_change_set(root, head_commit=hsha, config=hcfg)
                        if cs.tree_id == rec_tree:
                            report_msg = f"running gate for this tree (pid {rec_pid}), wait or run lintel task wait"
                            sys.stdout.write(report_msg + "\n")
                            return report_msg
            except Exception:
                pass
        raise CheckRefusalError(
            f"Refused: another gate run is active: {rec_cmd} for tree {rec_tree} (log: {rec_log}); lintel task wait (T-STG-15)",
            row="T-STG-15",
        )

    # Step 1: Active task check
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise CheckRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active_info
    task_state_file = state_dir / task_id / "task.json"
    task_data = load_state_file(task_state_file, doc_type="task")

    pos = flow.get_position(task_data)
    stage = pos.stage

    # Step 2: Determine phase and validate
    p_num_expected = pos.phase or (int(stage[1:]) if stage.startswith("p") and stage[1:].isdigit() else 1)
    if phase is not None:
        p_str = str(phase).lstrip("p")
        p_num_arg = int(p_str) if p_str.isdigit() else None
        if p_num_arg is not None and p_num_arg != p_num_expected:
            raise CheckRefusalError(
                f"Refused: --phase {phase} does not match current phase {p_num_expected} (T-STG-21)",
                row="T-STG-21",
            )
    p_num = p_num_expected

    # Step 3: Phase validation: must be locked and not delivered (T-AMD-22)
    is_locked = flow.is_phase_locked(task_data, p_num)
    is_delivered = flow.is_phase_delivered(task_data, p_num)
    if not is_locked or is_delivered:
        raise CheckRefusalError(
            f"Refused: phase p{p_num} is not locked or is already delivered (T-AMD-22)",
            row="T-AMD-22",
        )

    # Step 4: Candidate change set & candidate tree ID against recorded head
    head_sha = task_data["head"]
    cfg = config or _load_head_config(root, head_sha)

    try:
        change_set = git.get_change_set(root, head_commit=head_sha, config=cfg)
    except git.GitError as ge:
        err_msg = str(ge)
        if "symlink" in err_msg.lower() and "points outside" in err_msg.lower():
            raise CheckRefusalError(f"{err_msg} (T-AMD-23)", row="T-AMD-23")
        raise

    candidate_tree = change_set.tree_id

    # Symlink check (T-AMD-23)
    _check_candidate_symlinks(root, change_set)

    # Resolve log path & increment gate counter
    with state_lock(state_dir):
        task_data = load_state_file(task_state_file, doc_type="task")
        counters = task_data.setdefault("counters", {})
        gate_counters = counters.setdefault("gate", {})
        seq = gate_counters.get(f"check-p{p_num}", 0) + 1
        gate_counters[f"check-p{p_num}"] = seq
        validate_state_doc("task", task_data, file_path=task_state_file)
        atomic_write_json(task_state_file, task_data)

    log_rel = f"gate/check-p{p_num}-{seq}.log"
    log_file = root / ".lintel" / "tasks" / task_id / log_rel
    log_file.parent.mkdir(parents=True, exist_ok=True)
    reports_dir = root / ".lintel" / "tasks" / task_id / "gate" / f"reports-check-p{p_num}-{seq}"
    reports_dir.mkdir(parents=True, exist_ok=True)

    default_timeout = int(cfg.get("workflow", {}).get("testTimeoutSeconds", 600))

    # Step 6: check --start (detaches, writes running.json, returns in < 1s)
    if start:
        worker_code = (
            f"from lintel.deliver import _run_check_worker; "
            f"_run_check_worker({repr(str(root))}, {repr(task_id)}, {p_num}, "
            f"{repr(candidate_tree)}, {repr(str(log_file))}, {repr(log_rel)}, {seq}, {repr(run_id)})"
        )
        env = dict(os.environ)
        plugin_dir = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = f"{plugin_dir}:{env.get('PYTHONPATH', '')}" if env.get("PYTHONPATH") else plugin_dir

        proc = subprocess.Popen(
            [sys.executable, "-B", "-c", worker_code],
            cwd=str(root),
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )

        cur_start = get_process_start_time(proc.pid) or f"proc-alive-{proc.pid}"
        enter_gate(
            state_dir=state_dir,
            command="check-start",
            tree_id=candidate_tree,
            log_path=log_rel,
            bound_seconds=default_timeout,
            task_id=task_id,
            phase=p_num,
            run_id=run_id,
            conversation_id=conversation_id,
            detached=True,
            pid=proc.pid,
            pgid=proc.pid,
            proc_start=cur_start,
        )

        msg = "started, run lintel task wait"
        sys.stdout.write(msg + "\n")
        return msg

    # Step 7: Synchronous check run in gate worktree (STOP_AT_FIRST_RED)
    started_at = _now_iso()
    try:
        with running_gate(
            state_dir=state_dir,
            command="check",
            tree_id=candidate_tree,
            log_path=log_rel,
            bound_seconds=default_timeout,
            task_id=task_id,
            phase=p_num,
            run_id=run_id,
            conversation_id=conversation_id,
        ):
            gate_res = run_gate(
                repo_root=root,
                command="check",
                mode=GateMode.STOP_AT_FIRST_RED,
                parent_commit=head_sha,
                log_path=log_file,
                reports_dir=reports_dir,
                state_dir=None,
                task_id=task_id,
                phase=p_num,
                run_id=run_id,
                conversation_id=conversation_id,
                config=cfg,
            )
            finished_at = _now_iso()

            overall_green = gate_res.overall_green and gate_res.tests_clean
            res_str = "green" if overall_green else "red"

            check_entry = {
                "tree_id": candidate_tree,
                "phase": p_num,
                "seq": seq,
                "result": res_str,
                "groups": {
                    gname: {
                        "exit": gres.exit_code,
                        "status": gres.status,
                        "seconds": float(getattr(gres, "duration_seconds", 0.0)),
                    }
                    for gname, gres in gate_res.groups.items()
                },
                "log": log_rel,
                "started_at": started_at,
                "finished_at": finished_at,
                "run": run_id,
            }
            record_check(state_dir, task_id, check_entry)
    except GateRunningReport as gr:
        rec_pid = gr.running_info.get("pid")
        report_msg = f"running gate for this tree (pid {rec_pid}), wait or run lintel task wait"
        sys.stdout.write(report_msg + "\n")
        return report_msg
    except GateRefusedError as gr:
        rec_cmd = gr.running_info.get("command")
        rec_tree = gr.running_info.get("tree_id")
        rec_log = gr.running_info.get("log") or gr.running_info.get("log_path") or ""
        raise CheckRefusalError(
            f"Refused: another gate run is active: {rec_cmd} for tree {rec_tree} (log: {rec_log}); lintel task wait (T-STG-15)",
            row="T-STG-15",
        )

    # Format printed summary
    out_lines = [
        f"Check result: {res_str.upper()}",
        f"Exit codes:   {_format_groups_summary(gate_res.groups)}",
        f"Log:          {log_rel}",
    ]
    if not overall_green:
        # Include tail of output for red run
        tail_lines = []
        if gate_res.log_content:
            raw_lines = [l for l in gate_res.log_content.splitlines() if l.strip()]
            tail_lines = raw_lines[-15:]
        if tail_lines:
            out_lines.append("Tail of output:")
            for tl in tail_lines:
                out_lines.append(f"  {tl}")

    if full:
        out_lines.append("")
        out_lines.append("--- Test Paths ---")
        for gname in sorted(cfg.get("paths", {}).get("tests", {}).keys()):
            if gname == "support":
                continue
            g_files = [e.path for e in change_set.entries.values() if e.test_group == gname]
            out_lines.append(f"Group {gname} ({len(g_files)} files):")
            for gf in sorted(g_files):
                out_lines.append(f"  - {gf}")

    output_str = "\n".join(out_lines)
    sys.stdout.write(output_str + "\n")
    return output_str


# ---------------------------------------------------------------------------
# 2. Deliver Command Implementation
# ---------------------------------------------------------------------------

def deliver(
    repo_root: Union[str, Path],
    phase: Optional[Union[int, str]] = None,
    full: bool = False,
    skip_caller_check: bool = False,
) -> str:
    """Execute 'lintel deliver --phase N [--full]' per PLAN §3.6 and T-AMD-30..38."""
    root = Path(repo_root).resolve()
    state_dir = root / ".lintel" / "state"

    # Step 0: Check running.json on entry before checks or git ops (T-STG-15 / T-STG-16)
    running = check_or_clear_stale_running(state_dir)
    if running is not None:
        rec_cmd = running.get("command")
        rec_tree = running.get("tree_id")
        rec_pid = running.get("pid")
        rec_log = running.get("log") or running.get("log_path") or ""

        # If rec_cmd is deliver, check if it's single flight for the same tree
        if rec_cmd == "deliver":
            try:
                active_info = find_active_task_in_state(root)
                if active_info:
                    _, tdata = active_info
                    hsha = tdata.get("head")
                    if hsha:
                        hcfg = _load_head_config(root, hsha)
                        cs = git.get_change_set(root, head_commit=hsha, config=hcfg)
                        if cs.tree_id == rec_tree:
                            report_msg = f"running gate for this tree (pid {rec_pid}), wait or run lintel task wait"
                            sys.stdout.write(report_msg + "\n")
                            return report_msg
            except Exception:
                pass

        raise DeliverRefusalError(
            f"Refused: another gate run is active: {rec_cmd} for tree {rec_tree} (log: {rec_log}); lintel task wait (T-STG-15)",
            row="T-STG-15",
        )

    # Step 1: Active task check
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise DeliverRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active_info
    task_state_file = state_dir / task_id / "task.json"
    task_data = load_state_file(task_state_file, doc_type="task")

    pos = flow.get_position(task_data)
    stage = pos.stage

    # Step 2: State check: must be p<N>:O.deliver (T-AMD-38)
    if pos.code != "O.deliver":
        exp = flow.format_next_action(task_data, repo_root=root)
        raise DeliverRefusalError(
            f"Refused: deliver not expected in state '{pos.code}' (T-AMD-38);\nexpected action: {exp}",
            row="T-AMD-38",
        )

    # Step 3: NR check: refuse if run is pending or active (T-HUM-05)
    if pos.run is not None:
        run_file = state_dir / task_id / "runs" / f"{pos.run}.json"
        if run_file.is_file():
            try:
                rd = json.loads(run_file.read_text(encoding="utf-8"))
                if rd.get("state") in ("pending", "active"):
                    raise DeliverRefusalError(
                        "Refused: a run is pending or active; lintel task wait (T-HUM-05)",
                        row="T-HUM-05",
                    )
            except DeliverRefusalError:
                raise
            except Exception:
                pass

    # Step 4: Target phase check (T-STG-21)
    p_num_expected = pos.phase or (int(stage[1:]) if stage.startswith("p") and stage[1:].isdigit() else 1)
    if phase is not None:
        p_str = str(phase).lstrip("p")
        p_num_arg = int(p_str) if p_str.isdigit() else None
        if p_num_arg is not None and p_num_arg != p_num_expected:
            raise DeliverRefusalError(
                f"Refused: --phase {phase} does not match current phase {p_num_expected} (T-STG-21)",
                row="T-STG-21",
            )
    p_num = p_num_expected

    # Step 5: Phase must be locked and not already delivered (T-AMD-22)
    is_locked = flow.is_phase_locked(task_data, p_num)
    is_delivered = flow.is_phase_delivered(task_data, p_num)
    if not is_locked or is_delivered:
        raise DeliverRefusalError(
            f"Refused: phase p{p_num} is not locked or is already delivered (T-AMD-22)",
            row="T-AMD-22",
        )

    head_sha = task_data["head"]
    cfg = _load_head_config(root, head_sha)

    # Step 6: Candidate change set & symlink check (T-AMD-23)
    try:
        change_set = git.get_change_set(root, head_commit=head_sha, config=cfg)
    except git.GitError as ge:
        err_msg = str(ge)
        if "symlink" in err_msg.lower() and "points outside" in err_msg.lower():
            raise DeliverRefusalError(f"{err_msg} (T-AMD-23)", row="T-AMD-23")
        raise

    candidate_tree = change_set.tree_id
    _check_candidate_symlinks(root, change_set)

    # Determine gate log and check reuse
    reused_check = find_recorded_check(state_dir, task_id, candidate_tree)
    if reused_check and reused_check.get("result") == "green":
        gate_log_rel = reused_check.get("log", "")
    else:
        counters = task_data.get("counters", {})
        gate_counters = counters.get("gate", {})
        deliver_seq = gate_counters.get(f"deliver-p{p_num}", 0) + 1
        gate_log_rel = f"gate/deliver-p{p_num}-{deliver_seq}.log"

    default_timeout = int(cfg.get("workflow", {}).get("testTimeoutSeconds", 600))

    try:
        with running_gate(
            state_dir=state_dir,
            command="deliver",
            tree_id=candidate_tree,
            log_path=gate_log_rel,
            bound_seconds=default_timeout,
            task_id=task_id,
            phase=p_num,
        ):
            return _deliver_candidate(
                root=root,
                state_dir=state_dir,
                task_id=task_id,
                task_data=task_data,
                task_state_file=task_state_file,
                p_num=p_num,
                candidate_tree=candidate_tree,
                change_set=change_set,
                head_sha=head_sha,
                cfg=cfg,
                reused_check=reused_check,
                gate_log_rel=gate_log_rel,
            )
    except GateRunningReport as gr:
        rec_pid = gr.running_info.get("pid")
        report_msg = f"running gate for this tree (pid {rec_pid}), wait or run lintel task wait"
        sys.stdout.write(report_msg + "\n")
        return report_msg
    except GateRefusedError as gr:
        rec_cmd = gr.running_info.get("command")
        rec_tree = gr.running_info.get("tree_id")
        rec_log = gr.running_info.get("log") or gr.running_info.get("log_path") or ""
        raise DeliverRefusalError(
            f"Refused: another gate run is active: {rec_cmd} for tree {rec_tree} (log: {rec_log}); lintel task wait (T-STG-15)",
            row="T-STG-15",
        )


def _deliver_candidate(
    root: Path,
    state_dir: Path,
    task_id: str,
    task_data: Dict[str, Any],
    task_state_file: Path,
    p_num: int,
    candidate_tree: str,
    change_set: Any,
    head_sha: str,
    cfg: Dict[str, Any],
    reused_check: Optional[Dict[str, Any]],
    gate_log_rel: str,
) -> str:
    # -----------------------------------------------------------------------
    # Step 7: Rejection checks in order (PLAN §3.6, T-AMD-31..37)
    # -----------------------------------------------------------------------

    # Rejection 1: Valid DEV review VR(dev) (T-AMD-31)
    valid_rev = reviews.get_valid_review(root, task_id, "dev", task_data=task_data)
    if not valid_rev:
        next_act = f"lintel invoke dev-reviewer --phase p{p_num}"
        raise DeliverRefusalError(
            f"Rejected: no valid DEV review (T-AMD-31);\nnext action: {next_act}",
            row="T-AMD-31",
        )

    # Rejection 2: Lane checks
    p_info = task_data.get("phases", {}).get(str(p_num), {})
    locked_files = set(p_info.get("locked_files", []))
    released_files = set(p_info.get("released", []))

    # 2a. A tests file changed, added, or deleted (T-AMD-32)
    tests_changed = [
        p for p, e in change_set.entries.items()
        if str(e.path_class) == "tests" or (e.path_class and getattr(e.path_class, "base_class", "") == "tests")
    ]

    # 2b. A locked file of the phase is changed or deleted unless released (T-AMD-32)
    locked_violated = [
        p for p in change_set.paths
        if p in locked_files and p not in released_files
    ]

    # 2c. Approved shared/ documents changed (T-AMD-32)
    shared_diff_proc = git.run_git(
        ["diff", "--name-only", head_sha, "--", "shared/"],
        root,
        check=False,
    )
    shared_changed = [
        l.strip() for l in shared_diff_proc.stdout.splitlines()
        if l.strip() and not Path(l.strip()).name.startswith(".")
    ]

    # 2d. Untracked test-output files outside gitignore (T-AMD-32)
    test_output_changed = [
        p for p, e in change_set.entries.items()
        if str(e.path_class) == "test-output" or (e.path_class and getattr(e.path_class, "base_class", "") == "test-output")
    ]

    lane_violations = sorted(set(tests_changed + locked_violated + shared_changed + test_output_changed))
    if lane_violations:
        # Rejection T-AMD-32: sets state to p<N>:W.dev
        with state_lock(state_dir):
            task_data = load_state_file(task_state_file, doc_type="task")
            pos = flow.get_position(task_data)
            pos.code = "W.dev"
            pos.stage = f"p{p_num}"
            pos.phase = p_num
            pos.fr = False
            task_data["position"] = pos.to_dict()

            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

            event_rec = create_event(
                step=event_seq,
                event="reject",
                stage=f"p{p_num}",
                role="dev",
                row="T-AMD-32",
            )
            append_event(
                state_dir=state_dir,
                task_id=task_id,
                event_data=event_rec,
                task_dir=root / ".lintel" / "tasks" / task_id,
            )

        next_act = f"lintel restore-tests, then lintel invoke dev --phase p{p_num}"
        reviews.update_progress_md(root, task_data, next_act)
        v_details = "\n".join(f"  - {f}" for f in lane_violations)
        raise DeliverRefusalError(
            f"Rejected: lane violation: tests, locked files, shared docs, or test-output modified (T-AMD-32):\n"
            f"{v_details}\n"
            f"next action: {next_act}\n"
            f"(If a named file is production code, fix the glob and release it with 'amend --config --release <path>' instead)",
            row="T-AMD-32",
        )

    # 2e. .lintel/ or plugin dir changed (T-AMD-33)
    lintel_violations = []
    for p in change_set.paths:
        if p.startswith(".lintel/") and not p.startswith(".lintel/tasks/"):
            lintel_violations.append(p)
        elif p.startswith(".agents/plugins/lintel/") or p.startswith("plugin/"):
            lintel_violations.append(p)

    if lintel_violations:
        # Rejection T-AMD-33: sets state to p<N>:W.dev
        with state_lock(state_dir):
            task_data = load_state_file(task_state_file, doc_type="task")
            pos = flow.get_position(task_data)
            pos.code = "W.dev"
            pos.stage = f"p{p_num}"
            pos.phase = p_num
            pos.fr = False
            task_data["position"] = pos.to_dict()

            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

            event_rec = create_event(
                step=event_seq,
                event="reject",
                stage=f"p{p_num}",
                role="dev",
                row="T-AMD-33",
            )
            append_event(
                state_dir=state_dir,
                task_id=task_id,
                event_data=event_rec,
                task_dir=root / ".lintel" / "tasks" / task_id,
            )

        next_act = f"the human reverts the listed files, then lintel invoke dev --phase p{p_num}"
        reviews.update_progress_md(root, task_data, next_act)
        v_details = "\n".join(f"  - {f}" for f in lintel_violations)
        raise DeliverRefusalError(
            f"Rejected: .lintel/ or plugin directory changed (T-AMD-33):\n{v_details}\n"
            f"next action: {next_act}",
            row="T-AMD-33",
        )

    # 2f. A listed group no longer exists in config (T-AMD-34)
    lock_groups = set(p_info.get("lock", {}).get("groups", {}).keys())
    outline_groups = set(_get_outline_groups(root, task_id, p_num, head_sha))
    all_listed = sorted(lock_groups | outline_groups)
    config_tests = cfg.get("paths", {}).get("tests", {})
    missing_groups = [g for g in all_listed if g not in config_tests or config_tests.get(g) is None]
    if missing_groups:
        # State UNCHANGED (O.deliver)
        next_act = "human: lintel amend --config"
        raise DeliverRefusalError(
            f"Rejected: listed test group(s) no longer exist in config (T-AMD-34): {', '.join(missing_groups)}\n"
            f"next action: {next_act}",
            row="T-AMD-34",
        )

    # Rejection 3: Drift check (T-AMD-35)
    drift_matches, current_drift, acked_drift = drift.check_drift_acked(
        repo_root=root, phase=p_num, config=cfg
    )
    # Files released via amend --config --release are exempt
    effective_drift = {p: b for p, b in current_drift.items() if p not in released_files}
    if effective_drift != acked_drift:
        # State UNCHANGED (O.deliver)
        next_act = f"human: lintel drift --phase p{p_num} --ack, or human: lintel drift --phase p{p_num} --reject \"<why>\""
        raise DeliverRefusalError(
            f"Rejected: candidate drift files do not match acknowledged drift (T-AMD-35);\n"
            f"next action: {next_act}",
            row="T-AMD-35",
        )

    # Rejection 4: Audit check (T-AMD-36)
    import lintel.audit as audit_module
    ackable = audit_module.get_ackable_findings(repo_root=root, pair="dev")
    if ackable:
        next_act = f"human: lintel audit --phase p{p_num} --ack, human: lintel audit --phase p{p_num} --reject \"<why>\" or human: lintel reset --phase p{p_num}"
        raise DeliverRefusalError(
            f"Rejected: DEV side audit crossings not acknowledged (T-AMD-36); next action: {next_act}",
            row="T-AMD-36",
        )

    # Rejection 5: Green gate check (T-AMD-37)
    reused_check = find_recorded_check(state_dir, task_id, candidate_tree)
    gate_res_summary: Dict[str, Any] = {}
    gate_log_rel: str = ""

    if reused_check and reused_check.get("result") == "green":
        # Reuse green check!
        gate_log_rel = reused_check.get("log", "")
        gate_res_summary = {
            gname: {
                "exit": int(gdata.get("exit", 0)),
                "seconds": float(gdata.get("seconds", 0.0)),
            }
            for gname, gdata in reused_check.get("groups", {}).items()
        }
    else:
        # Run gate in gate worktree using running_gate(command="deliver")
        with state_lock(state_dir):
            task_data = load_state_file(task_state_file, doc_type="task")
            counters = task_data.setdefault("counters", {})
            gate_counters = counters.setdefault("gate", {})
            seq = gate_counters.get(f"deliver-p{p_num}", 0) + 1
            gate_counters[f"deliver-p{p_num}"] = seq
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

        gate_log_rel = f"gate/deliver-p{p_num}-{seq}.log"
        gate_log_file = root / ".lintel" / "tasks" / task_id / gate_log_rel
        gate_log_file.parent.mkdir(parents=True, exist_ok=True)
        reports_dir = root / ".lintel" / "tasks" / task_id / "gate" / f"reports-deliver-p{p_num}-{seq}"
        reports_dir.mkdir(parents=True, exist_ok=True)

        started_at = _now_iso()
        gate_res = run_gate(
            repo_root=root,
            command="deliver",
            mode=GateMode.STOP_AT_FIRST_RED,
            parent_commit=head_sha,
            log_path=gate_log_file,
            reports_dir=reports_dir,
            state_dir=None,
            task_id=task_id,
            phase=p_num,
            config=cfg,
        )
        finished_at = _now_iso()

        overall_green = gate_res.overall_green and gate_res.tests_clean
        res_str = "green" if overall_green else "red"

        check_entry = {
            "tree_id": candidate_tree,
            "phase": p_num,
            "seq": seq,
            "result": res_str,
            "groups": {
                gname: {
                    "exit": gres.exit_code,
                    "status": gres.status,
                    "seconds": float(getattr(gres, "duration_seconds", 0.0)),
                }
                for gname, gres in gate_res.groups.items()
            },
            "log": gate_log_rel,
            "started_at": started_at,
            "finished_at": finished_at,
            "run": None,
        }
        record_check(state_dir, task_id, check_entry)

        if not overall_green:
            # Rejection T-AMD-37: red gate run sends DEV back
            with state_lock(state_dir):
                task_data = load_state_file(task_state_file, doc_type="task")
                pos = flow.get_position(task_data)
                pos.code = "W.dev"
                pos.stage = f"p{p_num}"
                pos.phase = p_num
                pos.fr = False
                task_data["position"] = pos.to_dict()

                event_seq = task_data.get("event_seq", 0) + 1
                task_data["event_seq"] = event_seq
                validate_state_doc("task", task_data, file_path=task_state_file)
                atomic_write_json(task_state_file, task_data)

                event_rec = create_event(
                    step=event_seq,
                    event="reject",
                    stage=f"p{p_num}",
                    role="dev",
                    row="T-AMD-37",
                )
                append_event(
                    state_dir=state_dir,
                    task_id=task_id,
                    event_data=event_rec,
                    task_dir=root / ".lintel" / "tasks" / task_id,
                )

            next_act = f"lintel invoke dev --phase p{p_num}"
            reviews.update_progress_md(root, task_data, next_act)
            grp_summary = _format_groups_summary(gate_res.groups)
            raise DeliverRefusalError(
                f"Rejected: test run was red (T-AMD-37);\n"
                f"Exit codes: {grp_summary}\n"
                f"Log:        {gate_log_rel}\n"
                f"next action: {next_act}",
                row="T-AMD-37",
            )

        gate_res_summary = {
            gname: {
                "exit": int(gres.exit_code),
                "seconds": float(getattr(gres, "duration_seconds", 0.0)),
            }
            for gname, gres in gate_res.groups.items()
        }

    # -----------------------------------------------------------------------
    # Step 8: Successful Delivery (T-AMD-30)
    # -----------------------------------------------------------------------
    event_seq = task_data.get("event_seq", 0) + 1
    task_data["event_seq"] = event_seq

    commit_msg = f"feat(phase-{p_num})\n\nLintel-Step: {task_id}/{event_seq}"
    commit_sha = git.create_commit(
        repo_root=root,
        tree_id=candidate_tree,
        parent=head_sha,
        message=commit_msg,
        author_role="dev",
        task_id=task_id,
        event_seq=event_seq,
    )

    # Advance task branch & update head
    git.advance_branch(root, task_id, commit_sha, expected_tip=head_sha, state_dir=state_dir)

    with state_lock(state_dir):
        task_data = load_state_file(task_state_file, doc_type="task")
        task_data["head"] = commit_sha
        task_data["recorded_head"] = commit_sha

        # Mark phase delivered
        phases = task_data.setdefault("phases", {})
        phase_record = phases.setdefault(str(p_num), {})
        phase_record["delivered"] = {
            "commit": commit_sha,
            "at": _now_iso(),
            "groups": gate_res_summary,
            "log": gate_log_rel,
            "drift_count": len(current_drift),
            "audit_count": 0,
        }
        phase_record["locked"] = False

        deliv_list = task_data.setdefault("delivered_phases", [])
        if p_num not in deliv_list:
            deliv_list.append(p_num)

        # Clear task.locked and task.cycle
        task_data["locked"] = False
        task_data["cycle"] = None

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

        # Transition state: r<N>:W.po
        pos = flow.get_position(task_data)
        pos.code = "W.po"
        pos.stage = f"r{p_num}"
        pos.phase = p_num
        pos.fr = False
        task_data["position"] = pos.to_dict()

        task_data["event_seq"] = event_seq
        validate_state_doc("task", task_data, file_path=task_state_file)
        atomic_write_json(task_state_file, task_data)

        # Append deliver event
        event_rec = create_event(
            step=event_seq,
            event="deliver",
            stage=f"p{p_num}",
            role="dev",
            row="T-AMD-30",
        )
        append_event(
            state_dir=state_dir,
            task_id=task_id,
            event_data=event_rec,
            task_dir=root / ".lintel" / "tasks" / task_id,
        )

    next_action = f"lintel invoke po --phase r{p_num}"
    reviews.update_progress_md(root, task_data, next_action)

    # Format orchestrator summary
    summary_lines = [
        f"Delivered phase {p_num}: {commit_sha}",
        f"Status:       GREEN",
        f"Exit codes:   {_format_groups_summary(gate_res_summary)}",
        f"Lane files:   {len(change_set.paths)} modified",
        f"Drift files:  {len(current_drift)} acknowledged",
        f"Audit counts: 0 crossings",
        f"Log:          {gate_log_rel}",
        f"Next action:  {next_action}",
    ]
    summary_str = "\n".join(summary_lines)
    sys.stdout.write(summary_str + "\n")
    return next_action
