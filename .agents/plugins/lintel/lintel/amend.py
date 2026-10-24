"""Amend cycle and amend command (M1a-28).

Implements:
1. Cycle Start & Reroute inside cycle (T-AMD-70, T-AMD-71):
   - At the first `invoke ... --from-report` after the lock when no cycle is open (T-AMD-70):
     - Check: stage starts with 'p', phase is locked, and no amend cycle is open.
     - DEV's edits to `tests` and approved `shared/` documents against `task.head` are detected.
     - If DEV edited any of those paths:
       - Snapshot DEV's edits to `refs/lintel/<id>/snapshot-<n>` using `git.create_snapshot_ref`.
       - Record a finding for DEV in loops.json for editing files outside its lane.
       - Restore `tests` and approved `shared/` documents to `task.head`.
     - Cycle opened: `task_data["cycle"] = {"phase": phase, "cause": route["cause"], ...}`.
     - QA's lane becomes `tests` only.
   - A reroute inside an open cycle (T-AMD-71):
     - Restores nothing.

2. `amend` command (PLAN §3.5, §3.6, T-AMD-73, T-AMD-74, T-AMD-75, T-AMD-76, T-AMD-77, T-AMD-78):
   - Human check: `caller.check_caller("amend", args, repo_root=root)` (H0).
   - NR check: refuse if run is pending or active (T-HUM-05).
   - State guard: refuse with T-AMD-77 if no amend cycle is open or state is not `p<N>:H.amend`.
   - Phase inference & validation against current phase.
   - Valid review check VR(qa): `reviews.is_review_valid(root, task_id, "qa")` (T-AMD-75).
   - If `--reject "<why>"` (T-AMD-78):
     - Record human blocker finding on QA pair in `loops.json`.
     - Set `after_human_reject = True`.
     - State becomes `p<N>:W.qa`, cycle stays open.
     - Next action: `lintel invoke qa --phase p<N>`.
     - Event `reject` logged to `events.jsonl` (row T-AMD-78).
   - If normal `amend` (approval):
     - Group check (T-AMD-76):
       - Parent of lock commit: `git rev-parse <lock_commit>^`.
       - For each listed group in the phase's test outline, verify that after applying QA's test
         changes, at least one file differs from the lock commit's parent.
       - If any listed group would have no file differing: refuse with T-AMD-76 naming `amend --reject`.
     - Check if QA made test changes against `task.head`:
       - If QA made test changes (T-AMD-73):
         - Pager: DEV's claim, QA's test changes diff, agent-config files DEV touched, QA audit findings.
         - Confirmation prompt.
         - Commit QA's test changes applied to `task.head` as `test(phase-N): amend` by `Lintel QA`.
         - Advance `task.head` to commit SHA.
         - Update `task.phases.<N>.locked_files`: grows with the amend commit's test files.
         - Append commit SHA to `task.phases.<N>.amend_commits` and `amends` list.
         - DEV review becomes stale; DEV audit window is NOT restarted.
         - Close cycle: `task_data["cycle"] = None`.
         - Transition to `p<N>:W.dev`. Next action: `lintel invoke dev --phase p<N>`.
         - Event `amend` logged to `events.jsonl` (row T-AMD-73).
       - If QA made no test changes ("the tests stand", T-AMD-74):
         - Pager: DEV's claim, "The tests stand.", agent-config files DEV touched, QA audit findings.
         - Confirmation prompt.
         - Commit nothing, `task.head` stays.
         - Append to `task.phases.<N>.amends` with `kind="tests-stand"`.
         - For a `test` cause, task memory gains `(dev, test, phase)`.
         - Close cycle: `task_data["cycle"] = None`.
         - Transition to `p<N>:W.dev`. Next action: `lintel invoke dev --phase p<N>`.
         - Event `amend` logged to `events.jsonl` (row T-AMD-74).
"""

import sys
sys.dont_write_bytecode = True

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

try:
    from lintel import (
        caller,
        config as config_mod,
        flow,
        git,
        lock,
        loops,
        paths as paths_mod,
        restore,
        reviews,
        state,
    )
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
    from . import (
        caller,
        config as config_mod,
        flow,
        git,
        lock,
        loops,
        paths as paths_mod,
        restore,
        reviews,
        state,
    )
    from .events import append_event, create_event
    from .state import (
        atomic_write_json,
        load_state_file,
        resolve_state_root,
        state_lock,
        validate_state_doc,
    )
    from .task_start import find_active_task_in_state


class AmendRefusalError(flow.FlowRefusalError):
    """Raised when amend command or cycle start is refused per state machine rules."""

    def __init__(self, message: str, code: int = 1, row: Optional[str] = None):
        super().__init__(message=message, code=code, row=row)


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -----------------------------------------------------------------------------
# 1. Cycle Start & Reroute handling (T-AMD-70, T-AMD-71)
# -----------------------------------------------------------------------------

def detect_dev_lane_violations(
    repo_root: Union[str, Path],
    task_data: Dict[str, Any],
    phase_num: int,
) -> List[str]:
    """Detect if DEV edited any `tests` or approved `shared/` documents against task.head.

    Returns a sorted list of relative paths that were modified, added, or deleted outside DEV's lane.
    """
    root = Path(repo_root).resolve()
    head_sha = task_data.get("head")
    if not head_sha:
        return []

    # Load config at head
    res_cfg = git.run_git(["show", f"{head_sha}:.lintel/config.jsonc"], root, check=False)
    if res_cfg.returncode == 0 and res_cfg.stdout.strip():
        cfg = config_mod.load_config(res_cfg.stdout, workspace_root=root)
    else:
        cfg_file = root / ".lintel" / "config.jsonc"
        cfg = config_mod.load_config(cfg_file, workspace_root=root) if cfg_file.is_file() else {}

    cs = git.get_change_set(root, head_sha, config=cfg)

    # Gather unreleased locked files
    locked_files_all: Set[str] = set()
    for p_info in task_data.get("phases", {}).values():
        if isinstance(p_info, dict):
            lfiles = p_info.get("locked_files", [])
            relfiles = p_info.get("released", [])
            locked_files_all.update(set(lfiles) - set(relfiles))

    # Gather approved shared files committed at head
    approved_shared: Set[str] = set()
    res_shared = git.run_git(["ls-tree", "-r", "--name-only", head_sha, "--", "shared"], root, check=False)
    if res_shared.returncode == 0 and res_shared.stdout.strip():
        for line in res_shared.stdout.splitlines():
            s = line.strip()
            if s:
                approved_shared.add(s)

    violation_paths: List[str] = []
    for entry in cs.entries.values():
        p = entry.path
        pclass = str(entry.path_class) if entry.path_class else ""
        is_test = (
            pclass == "tests"
            or (entry.path_class and getattr(entry.path_class, "base_class", None) == "tests")
            or p.startswith("tests/")
        )
        is_locked = (p in locked_files_all)
        is_approved_shared = (
            p in approved_shared
            or p in ("shared/requirements.md", "shared/plan.md")
            or p.startswith(f"shared/phase-{phase_num}/")
            or p.startswith(f"shared/p{phase_num}/")
        )
        is_untracked_shared = (p.startswith("shared/") and entry.is_added())

        if is_test or is_locked or is_approved_shared or is_untracked_shared:
            violation_paths.append(p)

    return sorted(set(violation_paths))


def handle_cycle_start(
    repo_root: Union[str, Path],
    task_data: Dict[str, Any],
    from_report: bool,
    current_stage: str,
    current_phase_num: Optional[int],
    run_id: Optional[str] = None,
) -> bool:
    """Handle cycle start at the first `invoke … --from-report` after lock (T-AMD-70, T-AMD-71).

    If phase is locked and no cycle is currently open:
    - Detects DEV's edits to `tests` and approved `shared/` documents.
    - If DEV edited any of those paths:
      - Snapshot to refs/lintel/<id>/snapshot-<n> using git.create_snapshot_ref(kind="cycle").
      - Record an audit / lane crossing finding for DEV in loops.json.
      - Restores `tests` and approved `shared/` documents to task.head using restore routine.
    - Cycle will be opened by flow.on_invoke.

    If cycle is already open (T-AMD-71):
    - Restores nothing.
    """
    root = Path(repo_root).resolve()
    if not from_report:
        return False

    if not (current_stage.startswith("p") and current_phase_num is not None):
        return False

    if not flow.is_phase_locked(task_data, current_phase_num):
        return False

    # A reroute inside an open cycle restores nothing (T-AMD-71)
    if flow.is_amend_cycle_open(task_data):
        return False

    # This is the first invoke --from-report after the lock with no open cycle (T-AMD-70)
    task_id = task_data["id"]
    violations = detect_dev_lane_violations(root, task_data, current_phase_num)

    if violations:
        # 1. Snapshot DEV's edits to refs/lintel/<id>/snapshot-<n>
        state_dir = root / ".lintel" / "state"
        task_state_file = state_dir / task_id / "task.json"
        git.create_snapshot_ref(
            repo_root=root,
            task_id=task_id,
            kind="cycle",
            state_dir=state_dir,
            task_path=task_state_file,
            message=f"snapshot(cycle): {task_id} DEV lane crossing before amend cycle",
        )
        if task_state_file.is_file():
            updated = load_state_file(task_state_file, doc_type="task")
            task_data["counters"] = updated.get("counters", {})
            task_data["snapshots"] = updated.get("snapshots", [])

        # 2. Record finding for DEV in loops.json
        loops_data = loops.load_loops(state_dir, task_id)
        dev_loop = loops.get_or_create_loop(loops_data, "dev", current_stage)
        f_num = len(dev_loop.get("findings", [])) + 1
        finding_id = f"lane-crossing-{f_num}"
        dev_loop.setdefault("findings", []).append({
            "id": finding_id,
            "severity": "blocker",
            "status": "open",
            "author": "reviewer",
            "fixed_once": False,
            "response": None,
            "ruling": None,
            "file": None,
            "where": ", ".join(violations),
            "summary": f"DEV edited files outside its lane: {', '.join(violations)}",
        })
        loops.save_loops(state_dir, task_id, loops_data)

        # 3. Restore tests and approved shared/ documents to task.head
        restore.shared_restore_routine(root, task_id, task_data)

    return True


# -----------------------------------------------------------------------------
# 2. QA Amend test paths & listed groups check (T-AMD-76)
# -----------------------------------------------------------------------------

def get_qa_amend_test_paths(
    repo_root: Union[str, Path],
    head_sha: str,
    config: Optional[Dict[str, Any]] = None,
    task_data: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Return sorted list of test paths in the change set against head_sha (QA's lane)."""
    root = Path(repo_root).resolve()
    cfg = config
    if cfg is None:
        cfg_file = root / ".lintel" / "config.jsonc"
        cfg = config_mod.load_config(cfg_file, workspace_root=root) if cfg_file.is_file() else {}

    cs = git.get_change_set(root, head_sha, config=cfg)

    locked_files_all: Set[str] = set()
    if task_data:
        for p_info in task_data.get("phases", {}).values():
            if isinstance(p_info, dict):
                lfiles = p_info.get("locked_files", [])
                relfiles = p_info.get("released", [])
                locked_files_all.update(set(lfiles) - set(relfiles))

    qa_paths: List[str] = []
    for entry in cs.entries.values():
        p = entry.path
        pclass = str(entry.path_class) if entry.path_class else ""
        is_test = (
            pclass == "tests"
            or (entry.path_class and getattr(entry.path_class, "base_class", None) == "tests")
            or p.startswith("tests/")
            or (p in locked_files_all)
        )
        if is_test:
            qa_paths.append(p)

    return sorted(set(qa_paths))


def check_listed_groups_differ(
    repo_root: Path,
    task_data: Dict[str, Any],
    phase_num: int,
    candidate_test_paths: Sequence[str],
) -> None:
    """Verify that after applying QA's test changes, every listed group has at least one file

    differing from the lock commit's parent (T-AMD-76).
    Refuses naming `human: lintel amend --phase p<N> --reject "<the refusal>"`.
    """
    p_str = str(phase_num)
    lock_info = task_data.get("phases", {}).get(p_str, {}).get("lock")
    if not lock_info or not lock_info.get("commit"):
        return

    lock_commit = lock_info["commit"]
    res_parent = git.run_git(["rev-parse", f"{lock_commit}^"], repo_root, check=False)
    if res_parent.returncode == 0 and res_parent.stdout.strip():
        lock_parent = res_parent.stdout.strip()
    else:
        # Fallback to empty tree object hash
        lock_parent = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

    cfg_file = repo_root / ".lintel" / "config.jsonc"
    cfg = config_mod.load_config(cfg_file, workspace_root=repo_root) if cfg_file.is_file() else {}

    listed_groups = lock.get_listed_groups(
        repo_root,
        task_data["id"],
        phase_num,
        config=cfg,
        head_sha=task_data.get("head"),
    )
    if not listed_groups:
        return

    head_sha = task_data["head"]

    # Build candidate tree
    if candidate_test_paths:
        with tempfile.NamedTemporaryFile(prefix="lintel-amend-check-", delete=False) as tf:
            tmp_idx = tf.name
        try:
            env = dict(os.environ)
            env["GIT_INDEX_FILE"] = tmp_idx
            git.run_git(["read-tree", head_sha], repo_root, env=env, check=True)
            for p in candidate_test_paths:
                full_p = repo_root / p
                if full_p.is_file():
                    git.run_git(["add", "--", p], repo_root, env=env, check=True)
                else:
                    git.run_git(["rm", "--cached", "--ignore-unmatch", "--", p], repo_root, env=env, check=True)
            wtree = git.run_git(["write-tree"], repo_root, env=env, check=True)
            candidate_tree_sha = wtree.stdout.strip()
        finally:
            if os.path.exists(tmp_idx):
                try:
                    os.unlink(tmp_idx)
                except OSError:
                    pass
    else:
        res_tree = git.run_git(["rev-parse", f"{head_sha}^{{tree}}"], repo_root, check=True)
        candidate_tree_sha = res_tree.stdout.strip()

    # Diff candidate_tree_sha against lock_parent
    res_diff = git.run_git(["diff", "--name-only", lock_parent, candidate_tree_sha], repo_root, check=True)
    diff_paths = [line.strip() for line in res_diff.stdout.splitlines() if line.strip()]

    groups_with_diff: Set[str] = set()
    for dp in diff_paths:
        grp = paths_mod.assign_test_group(dp, config=cfg)
        if grp:
            groups_with_diff.add(grp)
        else:
            cls = paths_mod.classify_path(dp, config=cfg, repo_root=repo_root, is_tracked=True)
            if hasattr(cls, "group") and cls.group:
                groups_with_diff.add(cls.group)

    for lg in listed_groups:
        if lg not in groups_with_diff:
            refusal_reason = f"listed test group '{lg}' has no added or changed files differing from lock parent"
            raise AmendRefusalError(
                f"Refused: listed test group '{lg}' would have no file differing from lock commit's parent (T-AMD-76);\n"
                f"human: lintel amend --phase p{phase_num} --reject \"{refusal_reason}\"",
                row="T-AMD-76",
            )


# -----------------------------------------------------------------------------
# 3. Pager Formatting
# -----------------------------------------------------------------------------

def format_amend_pager(
    repo_root: Path,
    task_id: str,
    phase_num: int,
    candidate_test_paths: Sequence[str],
    task_data: Dict[str, Any],
) -> str:
    """Format pager display for human review at amend (PLAN §3.5, §3.6)."""
    lines: List[str] = [f"=== Amend Review: Phase {phase_num} ==="]

    head_sha = task_data.get("head")

    # 1. DEV's claim
    lines.append("\n--- DEV's Claim ---")
    route = task_data.get("route") or {}
    dev_claim_found = False
    from_run = route.get("from_run")
    if from_run:
        rf = repo_root / ".lintel" / "state" / task_id / "runs" / f"{from_run}.json"
        if rf.is_file():
            try:
                rd = json.loads(rf.read_text(encoding="utf-8"))
                rep_file = repo_root / rd.get("folder", "") / "report.md"
                if rep_file.is_file():
                    rep_text = rep_file.read_text(encoding="utf-8")
                    cause = route.get("cause")
                    heading = "Contract defect" if cause == "contract" else "Suspected test problem"
                    extracted = lock._extract_report_section(rep_text, heading) if hasattr(lock, "_extract_report_section") else None
                    if not extracted:
                        m = re.search(r"##\s+" + re.escape(heading) + r"\s*\n(.*?)(?=\n##|\Z)", rep_text, re.DOTALL | re.IGNORECASE)
                        if m:
                            extracted = m.group(1).strip()
                    if extracted:
                        lines.append(extracted)
                        dev_claim_found = True
                    else:
                        lines.append(rep_text.strip())
                        dev_claim_found = True
            except Exception:
                pass
    if not dev_claim_found:
        lines.append(f"DEV blocked with cause '{route.get('cause', 'test')}' (run {from_run or 'unknown'})")

    # 2. QA's test changes
    lines.append("\n--- QA's Test Changes ---")
    if candidate_test_paths and head_sha:
        diff_res = git.run_git(
            ["diff", head_sha, "--"] + list(candidate_test_paths),
            repo_root,
            check=False,
        )
        if diff_res.returncode == 0 and diff_res.stdout.strip():
            lines.append(diff_res.stdout.rstrip())
        else:
            lines.append("\n".join(f"- {p}" for p in candidate_test_paths))
    else:
        lines.append("The tests stand.")

    # 3. agent-config files DEV's change set touches
    lines.append("\n--- Agent-Config Files Touched by DEV ---")
    cfg_file = repo_root / ".lintel" / "config.jsonc"
    cfg = config_mod.load_config(cfg_file, workspace_root=repo_root) if cfg_file.is_file() else {}
    if head_sha:
        cs = git.get_change_set(repo_root, head_sha, config=cfg)
        agent_config_files: List[str] = []
        for entry in cs.entries.values():
            p = entry.path
            cls = paths_mod.classify_path(p, config=cfg, repo_root=repo_root, is_tracked=True)
            if getattr(cls, "base_class", None) == "agent-config" or str(cls) == "agent-config":
                agent_config_files.append(p)
        if agent_config_files:
            for acf in sorted(agent_config_files):
                lines.append(f"- {acf}")
        else:
            lines.append("(No agent-config files touched)")
    else:
        lines.append("(No agent-config files touched)")

    # 4. QA pair findings & audit findings
    lines.append("\n--- QA Pair Findings ---")
    try:
        loops_doc = loops.load_loops(repo_root / ".lintel" / "state", task_id)
        l_key = f"qa.p{phase_num}"
        loop_entry = loops_doc.get("loops", {}).get(l_key, {})
        qa_findings = loop_entry.get("findings", [])
        if qa_findings:
            for f in qa_findings:
                fid = f.get("id", "")
                author = f.get("author", "reviewer")
                sev = f.get("severity", "")
                st = f.get("status", "")
                summ = f.get("summary", "")
                author_tag = f" [{author}]" if author == "human" else ""
                lines.append(f"- [{st.upper()}] {fid}{author_tag} ({sev}): {summ}")
        else:
            lines.append("(No open reviewer findings)")
    except Exception:
        lines.append("(No open reviewer findings)")

    lines.append("\n--- Audit Findings ---")
    try:
        from lintel.audit import get_ackable_findings
        acks = get_ackable_findings(repo_root, "qa")
        if acks:
            for f in acks:
                lines.append(f"- [{f.kind.upper()}] {f.id} ({f.category}): {f.message}")
        else:
            lines.append("(No unacknowledged audit findings)")
    except Exception:
        lines.append("(No unacknowledged audit findings)")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# 4. Main `amend` Command
# -----------------------------------------------------------------------------

def amend(
    repo_root: Union[str, Path],
    phase: Optional[Union[int, str]] = None,
    reject: Optional[str] = None,
    skip_caller_check: bool = False,
    dialog_fn: Optional[Any] = None,
    auto_confirm: bool = False,
) -> str:
    """Execute `lintel amend [--phase N] [--reject "<why>"]` (PLAN §3.5, §3.6)."""
    root = Path(repo_root).resolve()

    active_info = find_active_task_in_state(root)
    if not active_info:
        raise AmendRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)"
        )
    task_id, task_data = active_info
    state_dir = root / ".lintel" / "state"
    task_state_file = state_dir / task_id / "task.json"

    # Step 1: Infer and validate phase
    pos = flow.get_position(task_data)
    stage = pos.stage
    clean_code = pos.code
    p_curr = pos.phase
    if p_curr is None and stage.startswith("p") and stage[1:].isdigit():
        p_curr = int(stage[1:])
    if p_curr is None:
        p_curr = 1

    if phase is not None:
        p_req = int(phase.lstrip("p")) if isinstance(phase, str) and phase.startswith("p") else int(phase)
        if p_req != p_curr:
            raise AmendRefusalError(
                f"Refused: requested phase p{p_req} does not match current phase p{p_curr}"
            )

    # Step 2: Human caller check (H0)
    if not skip_caller_check:
        cmd_str = f"amend --phase p{p_curr}" + (f" --reject \"{reject}\"" if reject else "")
        dummy_args = argparse.Namespace(
            subcommand="amend",
            phase=f"p{p_curr}",
            reject=reject,
            full=False,
            config=False,
            release=None,
        )
        caller.check_caller(cmd_str, dummy_args, repo_root=root, dialog_fn=dialog_fn)

    # Step 3: NR check (T-HUM-05)
    if restore.is_run_pending_or_active(task_data, state_dir, task_id):
        raise AmendRefusalError(
            "Refused: a run is pending or active (T-HUM-05); lintel task wait",
            row="T-HUM-05",
        )

    # Step 4: State guard (T-AMD-77)
    if clean_code != "H.amend" or not flow.is_amend_cycle_open(task_data):
        expected_act = flow.format_next_action(task_data)
        raise AmendRefusalError(
            f"Refused: amend not allowed outside amend cycle (T-AMD-77); expected '{expected_act}'",
            row="T-AMD-77",
        )

    # Step 5: Valid QA review check VR(qa) (T-AMD-75)
    valid_rev = reviews.get_valid_review(root, task_id, "qa", task_data=task_data)
    if not valid_rev:
        raise AmendRefusalError(
            f"Refused: no valid QA review (T-AMD-75); lintel invoke qa-reviewer --phase p{p_curr}",
            row="T-AMD-75",
        )

    # Step 6: Handle --reject "<why>" (T-AMD-78)
    if reject is not None:
        why = reject.strip() or "Amend rejected"
        folder_rel = None
        if valid_rev and valid_rev.get("run"):
            rf = state_dir / task_id / "runs" / f"{valid_rev['run']}.json"
            if rf.is_file():
                try:
                    rd = json.loads(rf.read_text(encoding="utf-8"))
                    folder_rel = rd.get("folder")
                except Exception:
                    pass
        if not folder_rel:
            folder_rel = f".lintel/tasks/{task_id}/qa/p{p_curr}-reject"

        run_folder = root / folder_rel
        run_folder.mkdir(parents=True, exist_ok=True)
        rejection_file = run_folder / "rejection.md"
        rejection_file.write_text(why + "\n", encoding="utf-8")
        rejection_rel_path = f"{folder_rel}/rejection.md"

        # Record blocker finding authored by human in loops.json under loops.qa.p<N>
        loops.record_human_reject(
            state_dir=state_dir,
            task_id=task_id,
            writer="qa",
            stage=f"p{p_curr}",
            reason=why,
            file_path=rejection_rel_path,
            where="tests",
        )

        with state_lock(state_dir):
            task_data = load_state_file(task_state_file, doc_type="task")
            task_data, next_action, row_id = flow.on_amend(task_data, phase=p_curr, reject_reason=why)
            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

            event_rec = create_event(
                step=event_seq,
                event="reject",
                stage=f"p{p_curr}",
                role="qa",
                row=row_id,
            )
            append_event(
                state_dir=state_dir,
                task_id=task_id,
                event_data=event_rec,
                task_dir=root / ".lintel" / "tasks" / task_id,
            )

        restore.update_last_human_command(state_dir, task_id, f"amend --reject \"{why}\"", event_seq)
        reviews.update_progress_md(root, task_data, next_action)
        sys.stdout.write(f"Rejected QA test changes: {why}\n{next_action}\n")
        return next_action

    # -------------------------------------------------------------------------
    # Approval flow (T-AMD-73, T-AMD-74, T-AMD-76)
    # -------------------------------------------------------------------------

    head_sha = task_data["head"]
    qa_test_paths = get_qa_amend_test_paths(root, head_sha, task_data=task_data)

    # Group check (T-AMD-76)
    check_listed_groups_differ(root, task_data, p_curr, qa_test_paths)

    # Format review pager
    pager_text = format_amend_pager(root, task_id, p_curr, qa_test_paths, task_data)
    if sys.stdout.isatty():
        sys.stdout.write(pager_text + "\n\n")

    # Confirmation prompt
    if not auto_confirm and sys.stdin.isatty():
        try:
            ans = input("Proceed with amend? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"
        if ans not in ("y", "yes"):
            sys.stdout.write("Amend canceled by user.\n")
            return flow.format_next_action(task_data)

    from lintel.audit import accept_findings_for_command
    accept_findings_for_command(root, "qa", "amend", p_curr)

    # Execute amend commit or tests-stand
    with state_lock(state_dir):
        task_data = load_state_file(task_state_file, doc_type="task")
        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq

        phases = task_data.setdefault("phases", {})
        p_info = phases.setdefault(str(p_curr), {})

        # Find latest architect run for contract cause
        route = task_data.get("route") or {}
        cycle_cause = (task_data.get("cycle") or {}).get("cause") or route.get("cause") or "test"
        architect_run_id = None
        if cycle_cause == "contract":
            runs_dir = state_dir / task_id / "runs"
            if runs_dir.is_dir():
                arch_runs = []
                for rf in sorted(runs_dir.glob("architect-*.json")):
                    try:
                        rd = json.loads(rf.read_text(encoding="utf-8"))
                        if rd.get("role") == "architect" and (rd.get("stage") == f"p{p_curr}" or rd.get("phase") == p_curr):
                            arch_runs.append(rd.get("id"))
                    except Exception:
                        pass
                if arch_runs:
                    architect_run_id = arch_runs[-1]

        if qa_test_paths:
            # T-AMD-73: commit QA test changes applied to head
            commit_sha = reviews.create_lane_commit(
                repo_root=root,
                task_id=task_id,
                author_role="qa",
                message=f"test(phase-{p_curr}): amend",
                lane_files=qa_test_paths,
                event_seq=event_seq,
                parent_sha=task_data["head"],
            )
            task_data["head"] = commit_sha

            # Grow locked_files set with tests at this amend commit
            cfg_file = root / ".lintel" / "config.jsonc"
            cfg = config_mod.load_config(cfg_file, workspace_root=root) if cfg_file.is_file() else {}
            ls_proc = git.run_git(["ls-tree", "-r", "--name-only", commit_sha], root, check=True)
            existing_locked = set(p_info.get("locked_files", []))
            for line in ls_proc.stdout.splitlines():
                s = line.strip()
                if not s:
                    continue
                cls = paths_mod.classify_path(s, config=cfg, repo_root=root, is_tracked=True)
                if getattr(cls, "base_class", None) == "tests" or str(cls) == "tests":
                    existing_locked.add(s)
            p_info["locked_files"] = sorted(existing_locked)

            p_info.setdefault("amend_commits", []).append(commit_sha)
            amend_rec = {
                "kind": "amend",
                "commit": commit_sha,
                "cause": cycle_cause,
                "blocked_run": route.get("from_run"),
                "architect_run": architect_run_id,
                "at": _now_iso(),
            }
            p_info.setdefault("amends", []).append(amend_rec)

            reviews.record_approval(
                state_dir=state_dir,
                task_id=task_id,
                step="amend",
                kind="amend",
                pair="qa",
                stage=f"p{p_curr}",
                phase=p_curr,
                review_id=valid_rev["id"],
                lane_hash=valid_rev.get("lane_hash"),
                commit_sha=commit_sha,
                head_sha=commit_sha,
                event_seq=event_seq,
            )

            task_data, next_action, row_id = flow.on_amend(task_data, phase=p_curr, tests_stand=False)
            event_rec = create_event(
                step=event_seq,
                event="amend",
                stage=f"p{p_curr}",
                role="qa",
                row=row_id,
            )
        else:
            # T-AMD-74: "the tests stand"
            amend_rec = {
                "kind": "tests-stand",
                "commit": None,
                "cause": cycle_cause,
                "blocked_run": route.get("from_run"),
                "architect_run": architect_run_id,
                "at": _now_iso(),
            }
            p_info.setdefault("amends", []).append(amend_rec)

            reviews.record_approval(
                state_dir=state_dir,
                task_id=task_id,
                step="amend",
                kind="tests-stand",
                pair="qa",
                stage=f"p{p_curr}",
                phase=p_curr,
                review_id=valid_rev["id"],
                lane_hash=valid_rev.get("lane_hash"),
                commit_sha=None,
                head_sha=task_data["head"],
                event_seq=event_seq,
            )

            task_data, next_action, row_id = flow.on_amend(task_data, phase=p_curr, tests_stand=True)
            event_rec = create_event(
                step=event_seq,
                event="amend",
                stage=f"p{p_curr}",
                role="qa",
                row=row_id,
            )

        validate_state_doc("task", task_data, file_path=task_state_file)
        atomic_write_json(task_state_file, task_data)

        append_event(
            state_dir=state_dir,
            task_id=task_id,
            event_data=event_rec,
            task_dir=root / ".lintel" / "tasks" / task_id,
        )

    restore.update_last_human_command(state_dir, task_id, "amend", event_seq)
    reviews.update_progress_md(root, task_data, next_action)

    if qa_test_paths:
        sys.stdout.write(f"Amended phase {p_curr} tests: committed {task_data['head'][:8]}.\n{next_action}\n")
    else:
        sys.stdout.write(f"Amended phase {p_curr}: the tests stand.\n{next_action}\n")

    return next_action
