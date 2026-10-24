"""amend --config and releases command (M1a-25).

Implements:
1. amend --config [--release <path>]... (human-only, outside a run):
   - Dialog check: check_caller(repo_root, command="amend --config") (H0).
   - NR check: refuse if a run is pending or active (T-HUM-05).
   - Paths subject to config amend:
     - .lintel/config.jsonc
     - .lintel/scripts/**
     - .lintel/personas/**
     - .lintel/steering/** (EXCLUDED in docs update stage, T-HUM-22).
   - Compare working tree against recorded head (task.head):
     - Compute unified diffs for modified, added, deleted files in config paths.
   - Detect files whose class or test group moves:
     - Compare file classifications under head config vs working-tree config.
     - Identify files moved out of tests, moved test group, or other class changes.
   - Agent audit writes to those paths: empty in M1a.
   - --release <path> handling:
     - Refusal T-HUM-23: For each path in --release:
       - Must be a locked file of the phase (task.phases.<n>.locked_files).
       - Must NOT still be in tests!
     - With NO config diff:
       - If --release paths given:
         - Validate T-HUM-23.
         - Record releases in task.phases.<n>.released.
         - Commit nothing!
         - Keep recorded head task.head unchanged (T-HUM-21).
         - Next action: next action of current state.
       - If no --release paths and no config diff:
         - Print message and next action, commit nothing.
     - With config diff:
       - Show approval pager / diff display.
       - Confirmation prompt (with bypass/auto-confirm when interactive=False or auto_confirm=True).
       - Record --release paths in task.phases.<n>.released for locked files moved out of tests.
       - Commit exactly those paths with git commit-tree:
         - Message: chore(lintel): config
         - Author: Lintel <lintel@noreply.local>
         - Trailer: Lintel-Step: <task_id>/<event_seq>
         - Move recorded head task.head to new commit SHA.
       - Review validity (D1):
         - Reviews stay valid across chore(lintel): config unless lane paths change!
         - If files moved in/out of tests, QA reviews are voided.
       - Next action: print the next action of current state.
"""

import sys
sys.dont_write_bytecode = True

import argparse
from datetime import datetime, timezone
import difflib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

try:
    from lintel import caller, config as config_mod, flow, git, paths, reviews, state
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
    from . import caller, config as config_mod, flow, git, paths, reviews, state
    from .events import append_event, create_event
    from .state import (
        atomic_write_json,
        load_state_file,
        resolve_state_root,
        state_lock,
        validate_state_doc,
    )
    from .task_start import find_active_task_in_state


class ConfigAmendRefusalError(flow.FlowRefusalError):
    """Raised when amend --config or --release is refused per state machine rules."""

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


def _is_config_amend_path(rel_path: str, is_docs_stage: bool = False) -> bool:
    """Check if relative path is eligible for config amend."""
    norm = rel_path.replace("\\", "/").strip("/")
    if norm in (".lintel/config.jsonc", ".lintel/config.json"):
        return True
    if norm.startswith(".lintel/scripts/"):
        return True
    if norm.startswith(".lintel/personas/"):
        return True
    if norm.startswith(".lintel/steering/"):
        # Excluded during docs update per T-HUM-22
        return not is_docs_stage
    return False


def _collect_candidate_paths(
    repo_root: Path,
    head_sha: str,
    is_docs_stage: bool = False,
) -> List[str]:
    """Collect candidate paths for config amend from head commit and working tree."""
    candidates: Set[str] = set()

    # 1. From recorded head
    res = git.run_git(["ls-tree", "-r", "--name-only", head_sha], repo_root, check=False)
    if res.returncode == 0:
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            if _is_config_amend_path(line, is_docs_stage=is_docs_stage):
                candidates.add(line)

    # 2. From working tree
    for fixed in (".lintel/config.jsonc", ".lintel/config.json"):
        if (repo_root / fixed).is_file():
            candidates.add(fixed)

    for prefix_dir in (".lintel/scripts", ".lintel/personas"):
        full_d = repo_root / prefix_dir
        if full_d.is_dir():
            for p in full_d.rglob("*"):
                if p.is_file() and not p.name.startswith("."):
                    rel = p.relative_to(repo_root).as_posix()
                    candidates.add(rel)

    if not is_docs_stage:
        steering_d = repo_root / ".lintel/steering"
        if steering_d.is_dir():
            for p in steering_d.rglob("*"):
                if p.is_file() and not p.name.startswith("."):
                    rel = p.relative_to(repo_root).as_posix()
                    candidates.add(rel)

    return sorted(candidates)


def _compute_diffs(
    repo_root: Path,
    head_sha: str,
    candidate_paths: Sequence[str],
) -> Tuple[Dict[str, str], List[str], List[str], List[str]]:
    """Compute unified diffs for candidate paths against head commit.

    Returns:
        (diffs_by_path, modified_paths, added_paths, deleted_paths)
    """
    diffs_by_path: Dict[str, str] = {}
    modified: List[str] = []
    added: List[str] = []
    deleted: List[str] = []

    for rel_path in candidate_paths:
        # Get head content
        head_res = git.run_git(["show", f"{head_sha}:{rel_path}"], repo_root, check=False)
        in_head = (head_res.returncode == 0)
        head_text = head_res.stdout if in_head else None

        # Get working tree content
        wt_file = repo_root / rel_path
        in_wt = wt_file.is_file()
        wt_text = None
        if in_wt:
            try:
                wt_text = wt_file.read_text(encoding="utf-8")
            except Exception:
                wt_text = wt_file.read_bytes().decode("utf-8", errors="replace")

        if in_head and in_wt:
            if head_text != wt_text:
                head_lines = (head_text or "").splitlines(keepends=True)
                wt_lines = (wt_text or "").splitlines(keepends=True)
                diff = difflib.unified_diff(
                    head_lines,
                    wt_lines,
                    fromfile=f"a/{rel_path}",
                    tofile=f"b/{rel_path}",
                )
                diff_text = "".join(diff)
                diffs_by_path[rel_path] = diff_text
                modified.append(rel_path)
        elif not in_head and in_wt:
            wt_lines = (wt_text or "").splitlines(keepends=True)
            diff = difflib.unified_diff(
                [],
                wt_lines,
                fromfile="/dev/null",
                tofile=f"b/{rel_path}",
            )
            diff_text = "".join(diff)
            diffs_by_path[rel_path] = diff_text
            added.append(rel_path)
        elif in_head and not in_wt:
            head_lines = (head_text or "").splitlines(keepends=True)
            diff = difflib.unified_diff(
                head_lines,
                [],
                fromfile=f"a/{rel_path}",
                tofile="/dev/null",
            )
            diff_text = "".join(diff)
            diffs_by_path[rel_path] = diff_text
            deleted.append(rel_path)

    return diffs_by_path, modified, added, deleted


def _detect_class_and_group_movements(
    repo_root: Path,
    head_config: Dict[str, Any],
    wt_config: Dict[str, Any],
) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str, str]], List[Tuple[str, str, str]]]:
    """Detect repo files whose class or test group changes between head config and wt config.

    Returns:
        (moved_out_of_tests, moved_test_groups, moved_other_classes)
        Each element is a tuple of (path, from_desc, to_desc).
    """
    moved_out_of_tests: List[Tuple[str, str, str]] = []
    moved_test_groups: List[Tuple[str, str, str]] = []
    moved_other: List[Tuple[str, str, str]] = []

    # Gather tracked files + existing untracked files
    files_to_check: Set[str] = set()
    res = git.run_git(["ls-files"], repo_root, check=False)
    if res.returncode == 0:
        for line in res.stdout.splitlines():
            line = line.strip()
            if line and not line.startswith(".git/"):
                files_to_check.add(line)

    for p in repo_root.rglob("*"):
        if p.is_file():
            rel = p.relative_to(repo_root).as_posix()
            if not rel.startswith(".git/") and not rel.startswith(".lintel/tasks/"):
                files_to_check.add(rel)

    for rel_path in sorted(files_to_check):
        cls_head = paths.classify_path(rel_path, config=head_config, repo_root=repo_root, is_tracked=True)
        cls_wt = paths.classify_path(rel_path, config=wt_config, repo_root=repo_root, is_tracked=True)

        head_base = str(cls_head.base_class if hasattr(cls_head, "base_class") else cls_head)
        wt_base = str(cls_wt.base_class if hasattr(cls_wt, "base_class") else cls_wt)
        head_group = getattr(cls_head, "group", None)
        wt_group = getattr(cls_wt, "group", None)

        if head_base == "tests" and wt_base != "tests":
            moved_out_of_tests.append((rel_path, f"tests:{head_group or 'unknown'}", wt_base))
        elif head_base == "tests" and wt_base == "tests":
            if head_group != wt_group:
                moved_test_groups.append((rel_path, f"tests:{head_group}", f"tests:{wt_group}"))
        elif head_base != wt_base:
            moved_other.append((rel_path, head_base, wt_base))

    return moved_out_of_tests, moved_test_groups, moved_other


def format_amend_config_pager(
    diffs_by_path: Dict[str, str],
    moved_out_of_tests: Sequence[Tuple[str, str, str]],
    moved_test_groups: Sequence[Tuple[str, str, str]],
    moved_other: Sequence[Tuple[str, str, str]],
    released_files: Sequence[str] = (),
) -> str:
    """Format pager display for amend --config approval."""
    lines: List[str] = [
        "=" * 72,
        "lintel amend --config",
        "=" * 72,
        "",
        "Configuration diffs:",
    ]
    if diffs_by_path:
        for path, diff_text in sorted(diffs_by_path.items()):
            lines.append(f"--- {path} ---")
            lines.append(diff_text.rstrip())
            lines.append("")
    else:
        lines.append("  (No configuration diffs)")
        lines.append("")

    lines.append("Path classifications and test group movements:")
    if moved_out_of_tests:
        lines.append("  Moved OUT of tests:")
        for p, from_desc, to_desc in moved_out_of_tests:
            lines.append(f"    * {p} ({from_desc} -> {to_desc})")
    if moved_test_groups:
        lines.append("  Test group changed:")
        for p, from_desc, to_desc in moved_test_groups:
            lines.append(f"    * {p} ({from_desc} -> {to_desc})")
    if moved_other:
        lines.append("  Other class changed:")
        for p, from_desc, to_desc in moved_other:
            lines.append(f"    * {p} ({from_desc} -> {to_desc})")
    if not (moved_out_of_tests or moved_test_groups or moved_other):
        lines.append("  (No path classifications or test groups moved)")
    lines.append("")

    lines.append("Agent audit writes to configuration paths:")
    lines.append("  (None in M1a)")
    lines.append("")

    if released_files:
        lines.append("Released files (will be marked released in phase state):")
        for rf in sorted(released_files):
            lines.append(f"  * {rf}")
        lines.append("")

    lines.append("=" * 72)
    return "\n".join(lines)


def amend_config(
    repo_root: Union[str, Path],
    release: Optional[Sequence[str]] = None,
    phase: Optional[Union[int, str]] = None,
    skip_caller_check: bool = False,
    interactive: bool = False,
    auto_confirm: bool = True,
    dialog_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Execute amend --config [--release <path>]... (PLAN §3.5, §3.6, T-HUM-20..23)."""
    root = Path(repo_root).resolve()
    state_dir = root / ".lintel" / "state"

    # Step 1: Active task check (T-STG-09)
    task_info = find_active_task_in_state(root)
    if not task_info:
        raise ConfigAmendRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = task_info
    task_file = state_dir / task_id / "task.json"

    # Step 2: Caller check for human-only command (H0, T-HUM-01..04)
    if not skip_caller_check:
        cmd_tokens = ["amend", "--config"]
        if release:
            for r in release:
                cmd_tokens.extend(["--release", str(r)])
        cmd_str = " ".join(cmd_tokens)
        dummy_args = argparse.Namespace(
            subcommand="amend",
            config=True,
            release=list(release or []),
            phase=str(phase) if phase is not None else None,
            full=False,
            task_id=task_id,
        )
        caller.check_caller(cmd_str, dummy_args, repo_root=root, dialog_fn=dialog_fn)

    # Step 3: NR check - refuse if run pending or active (T-HUM-05)
    if is_run_pending_or_active(task_data, state_dir, task_id):
        raise ConfigAmendRefusalError(
            "Refused: a run is pending or active; lintel task wait (T-HUM-05)",
            row="T-HUM-05",
        )

    # Step 4: Check position and stage (T-HUM-22)
    pos = flow.get_position(task_data)
    stage = pos.stage
    is_docs_stage = (stage == "docs")

    head_sha = task_data.get("head")
    if not head_sha:
        raise ConfigAmendRefusalError("Refused: task has no recorded head commit (T-STG-09)", row="T-STG-09")

    # Step 5: Collect candidate config paths
    candidate_paths = _collect_candidate_paths(root, head_sha, is_docs_stage=is_docs_stage)

    # Step 6: Compute unified diffs against recorded head
    diffs_by_path, modified, added, deleted = _compute_diffs(root, head_sha, candidate_paths)
    changed_config_paths = sorted(modified + added + deleted)
    has_config_diff = len(changed_config_paths) > 0

    # Step 7: Load configs to detect movements
    # Head config:
    head_config: Dict[str, Any] = {}
    head_cfg_res = git.run_git(["show", f"{head_sha}:.lintel/config.jsonc"], root, check=False)
    if head_cfg_res.returncode == 0 and head_cfg_res.stdout.strip():
        try:
            head_config = config_mod.load_config(head_cfg_res.stdout, workspace_root=root)
        except Exception:
            head_config = {}

    # Working tree config:
    wt_config: Dict[str, Any] = {}
    wt_cfg_file = root / ".lintel" / "config.jsonc"
    if wt_cfg_file.is_file():
        try:
            wt_config = config_mod.load_config(wt_cfg_file, workspace_root=root)
        except Exception as e:
            if has_config_diff and ".lintel/config.jsonc" in changed_config_paths:
                raise ConfigAmendRefusalError(
                    f"Refused: working-tree configuration is invalid: {e}",
                    row="T-HUM-20",
                )
            wt_config = head_config
    else:
        wt_config = head_config

    moved_out_of_tests, moved_test_groups, moved_other = _detect_class_and_group_movements(
        root, head_config, wt_config
    )

    # Step 8: Normalize and validate --release paths (T-HUM-23)
    normalized_releases: List[str] = []
    if release:
        # Determine target phase
        p_num: Optional[int] = None
        if phase is not None:
            p_clean = str(phase).lstrip("p").lstrip("r")
            if p_clean.isdigit():
                p_num = int(p_clean)
        if p_num is None and pos.phase is not None:
            p_num = pos.phase
        if p_num is None and stage.startswith(("p", "r")) and stage[1:].isdigit():
            p_num = int(stage[1:])
        if p_num is None:
            # Look in task.phases for locked phase
            phases_dict = task_data.get("phases", {})
            candidates = [int(k) for k, v in phases_dict.items() if str(k).isdigit() and v.get("locked_files") is not None]
            if candidates:
                p_num = max(candidates)

        if p_num is None:
            raise ConfigAmendRefusalError(
                f"Refused: cannot release '{release[0]}': no phase is locked (T-HUM-23)",
                row="T-HUM-23",
            )

        phase_data = task_data.get("phases", {}).get(str(p_num), {})
        locked_files = set(phase_data.get("locked_files", []))

        cfg_to_check = wt_config if has_config_diff else head_config

        for r in release:
            norm_r = os.path.normpath(r).replace("\\", "/").lstrip("/")
            # T-HUM-23: Must be a locked file of the phase
            if norm_r not in locked_files:
                raise ConfigAmendRefusalError(
                    f"Refused: path '{norm_r}' is not a locked file of phase {p_num} (T-HUM-23)",
                    row="T-HUM-23",
                )

            # T-HUM-23: Must NOT still be tests
            r_cls = paths.classify_path(norm_r, config=cfg_to_check, repo_root=root, is_tracked=True)
            if str(r_cls.base_class if hasattr(r_cls, "base_class") else r_cls) == "tests":
                raise ConfigAmendRefusalError(
                    f"Refused: path '{norm_r}' is still classified as tests (T-HUM-23)",
                    row="T-HUM-23",
                )

            normalized_releases.append(norm_r)

    # Step 9: Execute based on whether there is a config diff
    if not has_config_diff:
        # No config diff
        if not normalized_releases:
            # No changes and no releases: no-op
            next_action = flow.format_next_action(task_data, repo_root=root)
            sys.stdout.write("No configuration changes detected.\n")
            sys.stdout.write(f"Next action: {next_action}\n")
            return {
                "status": "no-op",
                "commit": None,
                "released": [],
                "next_action": next_action,
            }

        # No config diff, but releases given (T-HUM-21):
        # Record releases; commit nothing; task.head stays!
        with state_lock(state_dir):
            task_data = load_state_file(task_file, doc_type="task")
            p_str = str(p_num)
            p_info = task_data.setdefault("phases", {}).setdefault(p_str, {})
            current_released = set(p_info.get("released", []))
            current_released.update(normalized_releases)
            p_info["released"] = sorted(current_released)

            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq

            validate_state_doc("task", task_data, file_path=task_file)
            atomic_write_json(task_file, task_data)

        update_last_human_command(state_dir, task_id, "amend --config", event_seq)
        append_event(
            state_dir,
            task_id,
            create_event(
                step=event_seq,
                event="release",
                stage=stage,
                row="T-HUM-21",
            ),
        )

        next_action = flow.format_next_action(task_data, repo_root=root)
        reviews.update_progress_md(root, task_data, next_action)
        sys.stdout.write(f"Recorded release of {len(normalized_releases)} file(s) for phase {p_num}: {', '.join(normalized_releases)}\n")
        sys.stdout.write(f"Next action: {next_action}\n")
        return {
            "status": "released",
            "commit": None,
            "released": normalized_releases,
            "next_action": next_action,
        }

    # Step 10: Has config diff -> show pager & prompt confirmation
    pager_text = format_amend_config_pager(
        diffs_by_path,
        moved_out_of_tests,
        moved_test_groups,
        moved_other,
        released_files=normalized_releases,
    )
    sys.stdout.write(pager_text + "\n")

    if interactive and not auto_confirm:
        sys.stdout.write("Proceed with amend --config? [y/N]: ")
        sys.stdout.flush()
        ans = sys.stdin.readline().strip().lower()
        if ans not in ("y", "yes"):
            next_action = flow.format_next_action(task_data, repo_root=root)
            sys.stdout.write("Amend cancelled.\n")
            sys.stdout.write(f"Next action: {next_action}\n")
            return {
                "status": "cancelled",
                "commit": None,
                "released": [],
                "next_action": next_action,
            }

    # Step 11: Create chore(lintel): config commit using temporary git index
    with state_lock(state_dir):
        task_data = load_state_file(task_file, doc_type="task")
        if normalized_releases and p_num is not None:
            p_str = str(p_num)
            p_info = task_data.setdefault("phases", {}).setdefault(p_str, {})
            current_released = set(p_info.get("released", []))
            current_released.update(normalized_releases)
            p_info["released"] = sorted(current_released)

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq
        validate_state_doc("task", task_data, file_path=task_file)
        atomic_write_json(task_file, task_data)

    with tempfile.NamedTemporaryFile(prefix="lintel-config-index-", delete=False) as tf:
        temp_index_path = tf.name

    try:
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = temp_index_path

        # 1. Populate temp index with recorded head
        git.run_git(["read-tree", task_data["head"]], root, env=env, check=True)

        # 2. Stage exactly the changed config paths
        for rel_path in changed_config_paths:
            full_path = root / rel_path
            if full_path.is_file():
                git.run_git(["add", "--", rel_path], root, env=env, check=True)
            else:
                git.run_git(["rm", "--cached", "--ignore-unmatch", "--", rel_path], root, env=env, check=True)

        # 3. Write tree
        write_tree_proc = git.run_git(["write-tree"], root, env=env, check=True)
        new_tree = write_tree_proc.stdout.strip()

        # 4. Commit tree as chore(lintel): config
        commit_sha = git.create_commit(
            repo_root=root,
            tree_id=new_tree,
            parent=task_data["head"],
            message="chore(lintel): config",
            author_role="lintel",
            task_id=task_id,
            event_seq=event_seq,
        )

        # 5. Advance branch using compare-and-swap update-ref
        git.advance_branch(
            repo_root=root,
            task_id=task_id,
            new_commit=commit_sha,
        )
    finally:
        if os.path.exists(temp_index_path):
            try:
                os.unlink(temp_index_path)
            except OSError:
                pass

    # Step 12: Update last_human and audit event
    update_last_human_command(state_dir, task_id, "amend --config", event_seq)
    append_event(
        state_dir,
        task_id,
        create_event(
            step=event_seq,
            event="config",
            stage=stage,
            row="T-HUM-20",
        ),
    )

    # Step 13: Review validity (D1)
    # If test paths moved out of tests or into tests, void QA review
    if moved_out_of_tests or any(to_desc == "tests" for _, _, to_desc in moved_other):
        reviews.void_reviews(state_dir, task_id, pair="qa")

    task_data = load_state_file(task_file, doc_type="task")
    next_action = flow.format_next_action(task_data, repo_root=root)
    reviews.update_progress_md(root, task_data, next_action)
    sys.stdout.write(f"Committed chore(lintel): config ({commit_sha[:8]})\n")
    sys.stdout.write(f"Next action: {next_action}\n")

    return {
        "status": "committed",
        "commit": commit_sha,
        "released": normalized_releases,
        "next_action": next_action,
    }
