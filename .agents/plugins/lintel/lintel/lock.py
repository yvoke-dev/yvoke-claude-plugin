"""Lintel lock subcommand (M1a-23).

Implements the five steps of 'lintel lock --phase N' (PLAN §3.6, docs/state-machine.md §3.6):
Step 1: Human dialog check (H0), NR check (T-HUM-05), state check (p<N>:H.lock), valid review check VR(qa) (T-AMD-02).
        If reject: human rejection handling (T-AMD-10), transitions to p<N>:W.qa.
Step 2: Lane & change checks against recorded head:
        - All changed files must be in QA's lane (tests and paths.build) (T-AMD-03).
        - Contract and stubs must not be changed (T-AMD-04).
        - No symlink pointing outside repo (T-AMD-23).
        - Every listed test group must have at least one added or changed file (T-AMD-05).
Step 3: Gate run:
        - Run in gate worktree using run_gate in run-all mode (stop_at_first_red=False).
        - Wrapped with running_gate context manager (command="lock", phase=N).
        - Log saved to gate/lock-pN-<seq>.log.
        - Exit code checks:
          - Each listed group must exit 1 (or exit 0 only if in allow_green) (T-AMD-06).
          - No group exits 2 (compile error / could not run / timeout) (T-AMD-07).
          - No group exits 3 if it has test files or for any listed group (T-AMD-08).
          - Gate worktree must not have modified/deleted tracked files or added files in tests path (T-AMD-09).
        - JUnit failure tracing:
          - Check JUnit XML reports from config["paths"]["testReport"].
          - If no reports found: "not checked" (data: "not-checked").
          - If reports found: parse failures/errors. If failure traces to new/modified tests: "traced".
            Else: "failures not traced to the new tests" (data: "untraced").
Step 4: Approval pager:
        - Show test outline, change set grouped by test group, each group's exit code, trace mark, and tail output.
        - Show QA pair audit findings.
        - Prompt confirmation. If cancelled -> abort without commit (T-HUM-08).
Step 5: Commit and state update:
        - Commit candidate as test(phase-N) as Lintel QA with Lintel-Step: lock trailer.
        - Commits ONLY QA's lane files (tests and paths.build).
        - Records lock commit SHA in task.phases.<N>.lock.
        - Records locked files in task.phases.<N>.locked_files.
        - Records task.phases.<N>.dev_window_since = _now_iso().
        - Sets task.position.code = "W.dev", locked = True.
        - Sets task.head = commit_sha.
        - Next action: lintel invoke dev --phase p<N>.
"""

import sys
sys.dont_write_bytecode = True

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
import xml.etree.ElementTree as ET

try:
    from lintel import config as config_mod, flow, gate, git, loops, paths, reviews
    from lintel.events import append_event, create_event
    from lintel.gate import GateMode, run_gate
    from lintel.state import (
        atomic_write_json,
        load_state_file,
        running_gate,
        state_lock,
        validate_state_doc,
    )
    from lintel.task_start import find_active_task_in_state
except ImportError:
    from . import config as config_mod, flow, gate, git, loops, paths, reviews
    from .events import append_event, create_event
    from .gate import GateMode, run_gate
    from .state import (
        atomic_write_json,
        load_state_file,
        running_gate,
        state_lock,
        validate_state_doc,
    )
    from .task_start import find_active_task_in_state


class LockRefusalError(flow.FlowRefusalError):
    """Raised when lock command is refused per state machine rules."""

    def __init__(self, message: str, code: int = 1, row: Optional[str] = None):
        super().__init__(message=message, code=code, row=row)


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_stub_files(contract_text: str) -> List[str]:
    """Extract stub file relative paths from ## Stub files section of contract.md."""
    stubs: List[str] = []
    m = re.search(r"##\s+Stub\s+files\s*\n(.*?)(?=\n##|\Z)", contract_text, re.DOTALL | re.IGNORECASE)
    if m:
        for line in m.group(1).splitlines():
            s_line = line.strip()
            if s_line.startswith("-"):
                sp = s_line[1:].strip().strip("`'\" \t")
                if sp:
                    stubs.append(sp)
    return stubs


def get_listed_groups(
    repo_root: Path,
    task_id: str,
    phase_num: int,
    config: dict,
    head_sha: Optional[str] = None,
) -> List[str]:
    """Return the listed test groups for the phase from design.md ## Test outline."""
    configured_groups = [
        g for g in config.get("paths", {}).get("tests", {}).keys()
        if g != "support" and config.get("paths", {}).get("tests", {}).get(g) is not None
    ]

    des_content: Optional[str] = None
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
        return sorted(configured_groups)

    # Extract test outline
    m = re.search(r"##\s+Test\s+outline\s*\n(.*?)(?=\n##|\Z)", des_content, re.DOTALL | re.IGNORECASE)
    if not m:
        return sorted(configured_groups)

    outline_text = m.group(1)
    listed: Set[str] = set()
    for line in outline_text.splitlines():
        s_line = line.strip()
        if not s_line.startswith("-"):
            continue
        rest = s_line[1:].strip()
        # Check if line is none line: e.g. "e2e: none: reason"
        none_m = re.match(r"^([a-zA-Z0-9_-]+)\s*:\s*none\b", rest, re.IGNORECASE)
        if none_m:
            continue
        # Test line: e.g. "R1 unit: something" or "R1  integration : something"
        test_m = re.match(r"^(R\d+)\s+([a-zA-Z0-9_-]+)\s*:\s*(.*)$", rest)
        if test_m:
            grp = test_m.group(2)
            if grp in configured_groups:
                listed.add(grp)
        else:
            # Fallback: scan words before colon
            parts = rest.split(":", 1)
            first_words = parts[0].strip().split()
            for w in first_words:
                if w in configured_groups:
                    listed.add(w)

    return sorted(listed) if listed else sorted(configured_groups)


_get_listed_groups = get_listed_groups


def trace_junit_failures(
    reports_dir: Path,
    new_test_files_by_group: Dict[str, List[str]],
    groups_exited_1: Set[str],
) -> Dict[str, str]:
    """Determine JUnit failure trace status per group.

    Returns dict mapping group_name -> ("traced" | "untraced" | "not-checked").
    """
    traces: Dict[str, str] = {}
    if not reports_dir.is_dir():
        for g in new_test_files_by_group:
            traces[g] = "not-checked"
        return traces

    xml_files = list(reports_dir.rglob("*.xml"))
    if not xml_files:
        for g in new_test_files_by_group:
            traces[g] = "not-checked"
        return traces

    # Parse all XML reports
    all_failures: List[Dict[str, Any]] = []

    for xf in xml_files:
        try:
            tree = ET.parse(str(xf))
            root_elem = tree.getroot()
        except Exception:
            continue

        testcases = root_elem.findall(".//testcase")
        for tc in testcases:
            tc_file = tc.attrib.get("file", "")
            tc_classname = tc.attrib.get("classname", "")
            tc_name = tc.attrib.get("name", "")

            failures = tc.findall("failure") + tc.findall("error")
            for f in failures:
                f_text = f.text or ""
                f_msg = f.attrib.get("message", "")
                failure_entry = {
                    "file": tc_file,
                    "classname": tc_classname,
                    "name": tc_name,
                    "text": f"{f_msg}\n{f_text}",
                    "xml_file": str(xf),
                }
                all_failures.append(failure_entry)

    # For each group:
    for g, new_files in new_test_files_by_group.items():
        if g not in groups_exited_1:
            traces[g] = "not-checked"
            continue

        if not all_failures:
            traces[g] = "not-checked"
            continue

        # Check if any failure traces to new_files of group g
        matched = False
        target_tokens = set()
        for nf in new_files:
            target_tokens.add(nf)
            target_tokens.add(Path(nf).name)
            target_tokens.add(Path(nf).stem)

        for fail in all_failures:
            fail_blob = f"{fail['file']} {fail['classname']} {fail['name']} {fail['text']} {fail['xml_file']}"
            for tok in target_tokens:
                if tok and tok in fail_blob:
                    matched = True
                    break
            if matched:
                break

        if matched:
            traces[g] = "traced"
        else:
            traces[g] = "untraced"

    return traces


def _format_lock_pager(
    repo_root: Path,
    task_id: str,
    phase_num: int,
    change_set: git.ChangeSet,
    gate_res: gate.GateResult,
    traces: Dict[str, str],
    listed_groups: List[str],
) -> str:
    """Format the review details for the lock pager per PLAN §3.6."""
    lines: List[str] = [
        f"=== Test Lock Review: Phase {phase_num} ===",
        "",
    ]

    # 1. Test outline from design.md
    outline_found = False
    for dname in (f"phase-{phase_num}", f"p{phase_num}"):
        des_file = repo_root / "shared" / dname / "design.md"
        if not des_file.is_file():
            des_file = repo_root / ".lintel" / "tasks" / task_id / "shared" / dname / "design.md"
        if des_file.is_file():
            text = des_file.read_text(encoding="utf-8")
            m = re.search(r"##\s+Test\s+outline\s*\n(.*?)(?=\n##|\Z)", text, re.DOTALL | re.IGNORECASE)
            if m:
                lines.append(f"--- Test Outline ({dname}/design.md) ---")
                lines.append(m.group(1).strip())
                lines.append("")
                outline_found = True
                break
    if not outline_found:
        lines.append("--- Test Outline ---")
        lines.append(f"Listed test groups: {', '.join(listed_groups)}")
        lines.append("")

    # 2. Change set grouped by test group
    lines.append("--- Change Set by Test Group ---")
    by_group: Dict[str, List[Tuple[str, str]]] = {}
    non_test_files: List[Tuple[str, str]] = []
    for p in change_set.paths:
        entry = change_set.get(p)
        if not entry:
            continue
        st = entry.status
        grp = entry.test_group
        if grp:
            by_group.setdefault(grp, []).append((st, p))
        else:
            non_test_files.append((st, p))

    for grp in sorted(by_group.keys()):
        lines.append(f"Group '{grp}':")
        for st, p in by_group[grp]:
            lines.append(f"  [{st}] {p}")

    if non_test_files:
        lines.append("Build / Config Files:")
        for st, p in non_test_files:
            lines.append(f"  [{st}] {p}")
    lines.append("")

    # 3. Gate results: exit code, trace mark, tail lines of output
    lines.append("--- Gate Run Results ---")
    for gname, gres in sorted(gate_res.groups.items()):
        trace_val = traces.get(gname, "not-checked")
        if trace_val == "traced":
            trace_mark_str = "traced"
        elif trace_val == "untraced":
            trace_mark_str = "failures not traced to the new tests"
        else:
            trace_mark_str = "not checked"

        lines.append(f"- Group '{gname}': exit {gres.exit_code} (status: {gres.status}, trace: {trace_mark_str})")
        if gres.output:
            out_lines = gres.output.rstrip().splitlines()
            tail_lines = out_lines[-10:] if len(out_lines) > 10 else out_lines
            lines.append("  Tail output:")
            for tl in tail_lines:
                lines.append(f"    {tl}")
    lines.append("")

    # 4. QA pair findings and audit findings
    try:
        loops_doc = loops.load_loops(repo_root / ".lintel" / "state", task_id)
        l_key = f"qa.p{phase_num}"
        loop_entry = loops_doc.get("loops", {}).get(l_key, {})
        qa_findings = loop_entry.get("findings", [])
        lines.append("--- QA Pair Findings ---")
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
        lines.append("--- QA Pair Findings ---")
        lines.append("(No open reviewer findings)")

    lines.append("")
    lines.append("--- Audit Findings ---")
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

def lock(
    repo_root: Union[str, Path],
    phase: Optional[Union[int, str]] = None,
    allow_green: Optional[Sequence[str]] = None,
    reject: Optional[str] = None,
    confirm: Optional[bool] = None,
) -> str:
    """Execute 'lintel lock --phase N' per PLAN §3.6 and docs/state-machine.md §3.6."""
    root = Path(repo_root).resolve()
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise LockRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active_info
    state_dir = root / ".lintel" / "state"
    task_state_file = state_dir / task_id / "task.json"

    # Reload fresh state
    task_data = load_state_file(task_state_file, doc_type="task")
    pos = flow.get_position(task_data)
    stage = pos.stage

    # 1. State check (T-HUM-14)
    if pos.code != "H.lock":
        exp = flow.format_next_action(task_data, repo_root=root)
        raise LockRefusalError(
            f"Refused: lock not expected in state '{pos.code}' (T-HUM-14);\nexpected action: {exp}",
            row="T-HUM-14",
        )

    # 2. Target phase check (T-STG-21)
    p_num_expected = pos.phase or (int(stage[1:]) if stage.startswith("p") and stage[1:].isdigit() else 1)
    if phase is not None:
        p_str = str(phase).lstrip("p")
        p_num_arg = int(p_str) if p_str.isdigit() else None
        if p_num_arg is not None and p_num_arg != p_num_expected:
            raise LockRefusalError(
                f"Refused: --phase {phase} does not match current phase {p_num_expected} (T-STG-21)",
                row="T-STG-21",
            )
    p_num = p_num_expected

    # 3. NR check (T-HUM-05)
    if pos.run is not None:
        run_file = state_dir / task_id / "runs" / f"{pos.run}.json"
        if run_file.is_file():
            try:
                rd = json.loads(run_file.read_text(encoding="utf-8"))
                if rd.get("state") in ("pending", "active"):
                    raise LockRefusalError(
                        "Refused: a run is pending or active; lintel task wait (T-HUM-05)",
                        row="T-HUM-05",
                    )
            except LockRefusalError:
                raise
            except Exception:
                pass

    # 4. Valid review check VR(qa) (T-AMD-02)
    valid_rev = reviews.get_valid_review(root, task_id, "qa", task_data=task_data)
    if not valid_rev:
        next_act = f"lintel invoke qa-reviewer --phase p{p_num}"
        raise LockRefusalError(
            f"Refused: stale or missing QA review (T-AMD-02);\nnext action: {next_act}",
            row="T-AMD-02",
        )

    # 5. Handle human rejection (T-AMD-10)
    if reject is not None:
        why = reject.strip() or "Test suite rejected"
        folder_rel = None
        if valid_rev and valid_rev.get("run"):
            run_file = state_dir / task_id / "runs" / f"{valid_rev['run']}.json"
            if run_file.is_file():
                try:
                    rd = json.loads(run_file.read_text(encoding="utf-8"))
                    folder_rel = rd.get("folder")
                except Exception:
                    pass
        if not folder_rel:
            folder_rel = f".lintel/tasks/{task_id}/qa/p{p_num}-reject"

        run_folder = root / folder_rel
        run_folder.mkdir(parents=True, exist_ok=True)
        rejection_file = run_folder / "rejection.md"
        rejection_file.write_text(why + "\n", encoding="utf-8")
        rejection_rel_path = f"{folder_rel}/rejection.md"

        loops.record_human_reject(
            state_dir=state_dir,
            task_id=task_id,
            writer="qa",
            stage=f"p{p_num}",
            reason=why,
            file_path=rejection_rel_path,
            where="tests",
        )

        with state_lock(state_dir):
            task_data = load_state_file(task_state_file, doc_type="task")
            task_data, next_action, row_id = flow.on_lock(task_data, phase=p_num, reject_reason=why)
            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

            event_rec = create_event(
                step=event_seq,
                event="reject",
                stage=f"p{p_num}",
                role="qa",
                row=row_id,
            )
            append_event(
                state_dir=state_dir,
                task_id=task_id,
                event_data=event_rec,
                task_dir=root / ".lintel" / "tasks" / task_id,
            )

        reviews.update_progress_md(root, task_data, next_action)
        sys.stdout.write(f"Rejected tests for phase {p_num}: {why}\n{next_action}\n")
        return next_action

    # -----------------------------------------------------------------------
    # Step 2: Lane & change checks against recorded head
    # -----------------------------------------------------------------------
    head_sha = task_data["head"]

    # Load config from recorded head's tree
    head_cfg_proc = git.run_git(["show", f"{head_sha}:.lintel/config.jsonc"], root, check=False)
    if head_cfg_proc.returncode == 0 and head_cfg_proc.stdout.strip():
        try:
            cfg = config_mod.load_config(head_cfg_proc.stdout, workspace_root=root)
        except Exception:
            cfg = config_mod.load_config({}, workspace_root=root)
    else:
        cfg_file = root / ".lintel" / "config.jsonc"
        if cfg_file.is_file():
            cfg = config_mod.load_config(cfg_file.read_text(encoding="utf-8"), workspace_root=root)
        else:
            cfg = config_mod.load_config({}, workspace_root=root)

    # Change set against recorded head
    try:
        change_set = git.get_change_set(root, head_commit=head_sha, config=cfg)
    except git.GitError as ge:
        err_msg = str(ge)
        if "symlink" in err_msg.lower() and "points outside" in err_msg.lower():
            raise LockRefusalError(f"{err_msg} (T-AMD-23)", row="T-AMD-23")
        raise

    # Symlink check (T-AMD-23)
    for p, entry in change_set.entries.items():
        if entry.status != "D":
            full_p = root / p
            if full_p.is_symlink():
                try:
                    target_str = os.readlink(full_p)
                    if git._is_symlink_outside(root, p, target_str):
                        raise LockRefusalError(
                            f"Refused: symlink '{p}' points outside repository to '{target_str}' (T-AMD-23)",
                            row="T-AMD-23",
                        )
                except OSError:
                    pass

    # Contract and stubs check (T-AMD-04)
    contract_paths = {
        f"shared/phase-{p_num}/contract.md",
        f"shared/p{p_num}/contract.md",
    }
    stub_paths: Set[str] = set()
    for c_rel in contract_paths:
        # Check head
        head_show = git.run_git(["show", f"{head_sha}:{c_rel}"], root, check=False)
        if head_show.returncode == 0:
            stub_paths.update(_extract_stub_files(head_show.stdout))
        # Check disk
        c_disk = root / c_rel
        if not c_disk.is_file():
            c_disk = root / ".lintel" / "tasks" / task_id / c_rel
        if c_disk.is_file():
            stub_paths.update(_extract_stub_files(c_disk.read_text(encoding="utf-8")))

    changed_contract_or_stubs: List[str] = []
    for p in change_set.paths:
        if p in contract_paths or p in stub_paths:
            changed_contract_or_stubs.append(p)

    if changed_contract_or_stubs:
        changed_list = "\n".join(f"  - {s}" for s in changed_contract_or_stubs)
        raise LockRefusalError(
            f"Refused: contract or stub files were changed (T-AMD-04):\n{changed_list}\n"
            f"A contract defect goes back to the architect (QA finishes blocked --cause contract);\n"
            f"human: lintel lock --phase p{p_num} --reject \"contract or stub files were changed\"",
            row="T-AMD-04",
        )

    # QA's lane check (tests and paths.build) (T-AMD-03)
    raw_build = cfg.get("paths", {}).get("build", [])
    build_patterns = [raw_build] if isinstance(raw_build, str) else list(raw_build)

    stray_files: List[str] = []
    for p, entry in change_set.entries.items():
        is_tests = (str(entry.path_class) == "tests")
        is_build = paths.match_globs(p, build_patterns) if build_patterns else False
        if not is_tests and not is_build:
            stray_files.append(p)

    if stray_files:
        stray_list = "\n".join(f"  - {f}" for f in stray_files)
        raise LockRefusalError(
            f"Refused: change set contains files outside QA's lane (T-AMD-03):\n{stray_list}\n"
            f"Revert stray files or commit config fixes with 'lintel amend --config', then run 'lock' again;\n"
            f"human: lintel lock --phase p{p_num} --reject \"files outside QA's lane: {', '.join(stray_files)}\"",
            row="T-AMD-03",
        )

    # Listed groups have added/changed files check (T-AMD-05)
    listed_groups = _get_listed_groups(root, task_id, p_num, cfg, head_sha=head_sha)
    for grp in listed_groups:
        grp_changes = [
            e for e in change_set.entries.values()
            if e.test_group == grp and e.status in ("A", "M")
        ]
        if not grp_changes:
            raise LockRefusalError(
                f"Refused: listed test group '{grp}' has no added or changed files (T-AMD-05);\n"
                f"human: lintel lock --phase p{p_num} --reject \"listed test group '{grp}' has no added or changed files\"",
                row="T-AMD-05",
            )

    # -----------------------------------------------------------------------
    # Step 3: Gate run in gate worktree (RUN_ALL mode)
    # -----------------------------------------------------------------------
    counters = task_data.setdefault("counters", {})
    gate_counters = counters.setdefault("gate", {})
    seq = gate_counters.get(f"lock-p{p_num}", 0) + 1
    gate_counters[f"lock-p{p_num}"] = seq

    with state_lock(state_dir):
        validate_state_doc("task", task_data, file_path=task_state_file)
        atomic_write_json(task_state_file, task_data)

    log_rel = f"gate/lock-p{p_num}-{seq}.log"
    log_file = root / ".lintel" / "tasks" / task_id / log_rel
    log_file.parent.mkdir(parents=True, exist_ok=True)

    reports_dir = root / ".lintel" / "tasks" / task_id / "gate" / f"reports-lock-p{p_num}-{seq}"
    reports_dir.mkdir(parents=True, exist_ok=True)

    default_timeout = int(cfg.get("workflow", {}).get("testTimeoutSeconds", 600))

    gate_res = run_gate(
        repo_root=root,
        command="lock",
        mode=GateMode.RUN_ALL,
        parent_commit=head_sha,
        log_path=log_file,
        reports_dir=reports_dir,
        state_dir=state_dir,
        task_id=task_id,
        phase=p_num,
        config=cfg,
    )

    # JUnit failure tracing
    new_test_files_by_grp = {
        g: [e.path for e in change_set.entries.values() if e.test_group == g and e.status in ("A", "M")]
        for g in cfg.get("paths", {}).get("tests", {}).keys()
        if g != "support" and cfg.get("paths", {}).get("tests", {}).get(g) is not None
    }
    groups_exited_1 = {g for g, res in gate_res.groups.items() if res.exit_code == 1}
    traces = trace_junit_failures(reports_dir, new_test_files_by_grp, groups_exited_1)

    # Exit code checks:
    # 1. Setup script failure
    if gate_res.setup_result and gate_res.setup_result.exit_code != 0:
        raise LockRefusalError(
            f"Refused: setup.sh failed with exit {gate_res.setup_result.exit_code} (could not run) (T-AMD-07);\n"
            f"human: lintel lock --phase p{p_num} --reject \"setup.sh failed with exit {gate_res.setup_result.exit_code}\"",
            row="T-AMD-07",
        )

    # 2. Exit 2 for any group (T-AMD-07)
    for gname, gres in gate_res.groups.items():
        if gres.exit_code == 2 or gres.timed_out:
            raise LockRefusalError(
                f"Refused: test group '{gname}' exited with code 2 (could not run or timeout) (T-AMD-07);\n"
                f"human: lintel lock --phase p{p_num} --reject \"test group '{gname}' exited with code 2\", or QA finishes blocked --cause contract or environment",
                row="T-AMD-07",
            )

    # 3. Exit 3 for listed group or group with test files (T-AMD-08)
    for gname, gres in gate_res.groups.items():
        if gres.exit_code == 3:
            if gname in listed_groups or gres.test_files_count > 0:
                raise LockRefusalError(
                    f"Refused: test group '{gname}' exited with code 3 (no tests found) (T-AMD-08);\n"
                    f"human: lintel lock --phase p{p_num} --reject \"test group '{gname}' exited with code 3\"",
                    row="T-AMD-08",
                )

    # 4. Each listed group must exit 1 (or exit 0 only with --allow-green) (T-AMD-06)
    normalized_allow_green: Set[str] = set()
    if allow_green:
        for item in allow_green:
            for g in item.split(","):
                g = g.strip()
                if g:
                    normalized_allow_green.add(g)

    for gname in listed_groups:
        gres = gate_res.groups.get(gname)
        if gres:
            if gres.exit_code == 0 and gname not in normalized_allow_green:
                raise LockRefusalError(
                    f"Refused: listed test group '{gname}' passed against stubs (exit 0) without --allow-green (T-AMD-06);\n"
                    f"human: lintel lock --phase p{p_num} --reject \"listed test group '{gname}' passed against stubs\"\n"
                    f"or run: lintel lock --phase p{p_num} --allow-green {gname}",
                    row="T-AMD-06",
                )
            elif gres.exit_code not in (0, 1) or (gres.exit_code == 0 and gname not in normalized_allow_green):
                raise LockRefusalError(
                    f"Refused: listed test group '{gname}' did not fail on tests (exit {gres.exit_code}) (T-AMD-06);\n"
                    f"human: lintel lock --phase p{p_num} --reject \"listed test group '{gname}' exit {gres.exit_code}\"",
                    row="T-AMD-06",
                )

    # 5. Worktree modified/deleted tracked files or added files in tests path (T-AMD-09)
    if not gate_res.tests_clean:
        v_list = "\n".join(f"  - {v}" for v in gate_res.tests_violations)
        raise LockRefusalError(
            f"Refused: gate run dirtied test files or worktree (T-AMD-09):\n{v_list}\n"
            f"human: lintel lock --phase p{p_num} --reject \"gate run dirtied test files\"",
            row="T-AMD-09",
        )

    # -----------------------------------------------------------------------
    # Step 4: Approval pager & human confirmation (T-HUM-08)
    # -----------------------------------------------------------------------
    pager_text = _format_lock_pager(
        repo_root=root,
        task_id=task_id,
        phase_num=p_num,
        change_set=change_set,
        gate_res=gate_res,
        traces=traces,
        listed_groups=listed_groups,
    )
    sys.stdout.write(pager_text + "\n")

    do_confirm = confirm if confirm is not None else reviews.confirm_approval(
        f"Confirm test lock for phase {p_num}? [y/N]: "
    )
    if not do_confirm:
        act = f"human: lintel lock --phase p{p_num}"
        sys.stdout.write(f"Approval canceled (T-HUM-08).\n{act}\n")
        return act
    from lintel.audit import accept_findings_for_command
    accept_findings_for_command(repo_root, "qa", "lock", p_num)

    # -----------------------------------------------------------------------
    # Step 5: Commit and state update
    # -----------------------------------------------------------------------
    lane_files = [
        p for p, e in change_set.entries.items()
        if str(e.path_class) == "tests" or (build_patterns and paths.match_globs(p, build_patterns))
    ]

    event_seq = task_data.get("event_seq", 0) + 1
    task_data["event_seq"] = event_seq

    commit_sha = reviews.create_lane_commit(
        repo_root=root,
        task_id=task_id,
        author_role="qa",
        message=f"test(phase-{p_num})\n\nLintel-Step: lock",
        lane_files=lane_files,
        event_seq=event_seq,
        parent_sha=head_sha,
    )

    # Record locked files: all files in the lock commit tree that are in tests class
    ls_tree_proc = git.run_git(["ls-tree", "-r", "--name-only", commit_sha], root, check=True)
    locked_files_list: List[str] = []
    for line in ls_tree_proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        cls = paths.classify_path(line, config=cfg, repo_root=root, is_tracked=True)
        if str(cls) == "tests":
            locked_files_list.append(line)
    locked_files = sorted(set(locked_files_list))

    with state_lock(state_dir):
        task_data = load_state_file(task_state_file, doc_type="task")
        phases = task_data.setdefault("phases", {})
        p_info = phases.setdefault(str(p_num), {})
        p_info["lock"] = {
            "commit": commit_sha,
            "at": _now_iso(),
            "allow_green": sorted(normalized_allow_green),
            "groups": {
                g: {
                    "exit": res.exit_code,
                    "trace": traces.get(g, "not-checked"),
                }
                for g, res in gate_res.groups.items()
            },
        }
        p_info["locked_files"] = locked_files
        p_info["dev_window_since"] = _now_iso()
        task_data["locked"] = True
        task_data["head"] = commit_sha
        task_data["recorded_head"] = commit_sha

        pos = flow.get_position(task_data)
        pos.code = "W.dev"
        pos.stage = f"p{p_num}"
        pos.phase = p_num
        pos.fr = False
        task_data["position"] = pos.to_dict()

        task_data["event_seq"] = event_seq
        validate_state_doc("task", task_data, file_path=task_state_file)
        atomic_write_json(task_state_file, task_data)

        # Record approval in reviews.json
        reviews.record_approval(
            state_dir=state_dir,
            task_id=task_id,
            step="lock",
            kind=None,
            pair="qa",
            stage=f"p{p_num}",
            phase=p_num,
            review_id=valid_rev["id"],
            lane_hash=valid_rev.get("lane_hash"),


            commit_sha=commit_sha,
            head_sha=commit_sha,
            event_seq=event_seq,
        )

        # Append lock event
        event_rec = create_event(
            step=event_seq,
            event="lock",
            stage=f"p{p_num}",
            role="qa",
            row="T-AMD-01",
        )
        append_event(
            state_dir=state_dir,
            task_id=task_id,
            event_data=event_rec,
            task_dir=root / ".lintel" / "tasks" / task_id,
        )

    next_action = f"lintel invoke dev --phase p{p_num}"
    reviews.update_progress_md(root, task_data, next_action)
    sys.stdout.write(f"Locked phase {p_num}: {commit_sha}\n{next_action}\n")
    return next_action
