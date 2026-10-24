"""Task start and task status implementations (M1a-16).

Implements:
- task start "<title>":
  1. Refuse on active task (T-STG-02).
  2. Refuse on missing or broken interpreter (T-STG-03).
  3. Refuse on dirty tree (T-STG-04); untracked .lintel/tasks/ is tolerated.
  4. Refuse on invalid config or overlapping groups (T-STG-05).
  5. Refuse on unadjusted test template script (T-STG-08).
  6. Run baseline in gate worktree under <tmp> (T-STG-01, T-STG-06, T-STG-07).
  7. Generate task ID <yyyymmdd>-<slug> with collision suffix (-2, -3).
  8. Check for unmerged finished task branches and warn.
  9. Create branch lintel/<id> from base SHA and switch to it.
  10. Create state and task directories, request.md, task.json, progress.md, events.jsonl.
  11. Print next action: lintel invoke po --phase r0.
- task status:
  1. Print progress.md if active task exists.
  2. Print "No active task." and exit 0 if none active.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

try:
    from lintel.config import ConfigError, load_config
    from lintel.events import append_event, create_event
    from lintel.find_python import run_bounded
    from lintel.flow import format_next_action
    from lintel.gate import GateMode, GateResult, resolve_tmp_root, run_gate
    from lintel.git import GitError, run_git
    from lintel.paths import detect_test_group_overlaps
    from lintel.progress import render_progress_md, update_progress_md
    from lintel.scripts import is_unadjusted_template
    from lintel.state import (
        StateError,
        atomic_write_json,
        resolve_state_root,
        validate_state_doc,
    )
except ImportError:
    from .config import ConfigError, load_config
    from .events import append_event, create_event
    from .find_python import run_bounded
    from .flow import format_next_action
    from .gate import GateMode, GateResult, resolve_tmp_root, run_gate
    from .git import GitError, run_git
    from .paths import detect_test_group_overlaps
    from .progress import render_progress_md, update_progress_md
    from .scripts import is_unadjusted_template
    from .state import (
        StateError,
        atomic_write_json,
        resolve_state_root,
        validate_state_doc,
    )


class TaskRefusalError(SystemExit):
    """Raised when task start or a task lifecycle command refuses with non-zero exit."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(code)
        self.message = message

    def __str__(self) -> str:
        return self.message


def slugify(title: str, max_length: int = 40) -> str:
    """Derive lowercase [a-z0-9-] slug from title, at most max_length chars."""
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        s = "task"
    if len(s) > max_length:
        s = s[:max_length].rstrip("-")
    if not s:
        s = "task"
    return s


def find_active_task_in_state(repo_root: Path) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Check .lintel/state/*/task.json for any task with status == 'active'."""
    state_dir = repo_root / ".lintel" / "state"
    if not state_dir.is_dir():
        return None

    try:
        entries = sorted(state_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        return None

    for entry in entries:
        if entry.is_dir():
            task_json = entry / "task.json"
            if task_json.is_file():
                try:
                    data = json.loads(task_json.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("status") == "active":
                        return entry.name, data
                except Exception:
                    pass
    return None


def check_recorded_interpreter(repo_root: Path) -> str:
    """Verify that .lintel/state/python exists, is executable, and passes bounded run (T-STG-03)."""
    py_file = repo_root / ".lintel" / "state" / "python"
    if not py_file.is_file():
        msg = (
            "Refused: Python interpreter record .lintel/state/python is missing (T-STG-03);\n"
            "'lintel doctor' writes the file."
        )
        sys.stderr.write(msg + "\n")
        raise TaskRefusalError(msg)

    try:
        interp_path = py_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        msg = (
            f"Refused: could not read .lintel/state/python (T-STG-03): {exc};\n"
            "'lintel doctor' writes the file."
        )
        sys.stderr.write(msg + "\n")
        raise TaskRefusalError(msg)

    if not interp_path or not os.path.exists(interp_path) or not os.access(interp_path, os.X_OK):
        msg = (
            f"Refused: interpreter recorded in .lintel/state/python ('{interp_path}') "
            f"does not exist or is not executable (T-STG-03);\n"
            "'lintel doctor' writes the file."
        )
        sys.stderr.write(msg + "\n")
        raise TaskRefusalError(msg)

    # One bounded execution check: sys.version_info >= (3, 9)
    rc, _ = run_bounded(
        [interp_path, "-B", "-c", "import sys; assert sys.version_info >= (3, 9)"],
        timeout=5,
    )
    if rc != 0:
        msg = (
            f"Refused: interpreter '{interp_path}' failed bounded execution check "
            f"(sys.version_info >= (3, 9)) (T-STG-03);\n"
            "'lintel doctor' writes the file."
        )
        sys.stderr.write(msg + "\n")
        raise TaskRefusalError(msg)

    return interp_path


def check_clean_tree(repo_root: Path) -> None:
    """Verify clean working tree via git status --porcelain (T-STG-04).
    
    Untracked files under .lintel/tasks/ are tolerated (do not count as dirty).
    """
    proc = run_git(["status", "--porcelain", "-uall"], repo_root, check=True)
    dirty_paths: List[str] = []

    for line in proc.stdout.splitlines():
        if not line:
            continue
        status_code = line[:2]
        path_str = line[3:].strip()
        if path_str.startswith('"') and path_str.endswith('"'):
            path_str = path_str[1:-1]
        if " -> " in path_str:
            path_str = path_str.split(" -> ")[-1]

        # Untracked files under .lintel/tasks/ or prior gate/baseline-<seq>.log are tolerated
        if status_code == "??" and (
            path_str == ".lintel/tasks"
            or path_str.startswith(".lintel/tasks/")
            or re.match(r"^gate/baseline-\d+\.log$", path_str)
        ):
            continue

        dirty_paths.append(path_str)

    if dirty_paths:
        listing = "\n".join(f"  {p}" for p in dirty_paths)
        msg = (
            f"Refused: working tree is not clean (T-STG-04):\n"
            f"{listing}\n"
            f"Commit or revert dirty paths and run again."
        )
        sys.stderr.write(msg + "\n")
        raise TaskRefusalError(msg)


def _collect_project_files_for_config(repo_root: Path) -> List[str]:
    """Collect relative paths of project files to inspect test group overlaps."""
    git_dir = repo_root / ".git"
    if git_dir.is_dir() or git_dir.is_file():
        try:
            proc = run_git(
                ["ls-files", "--cached", "--others", "--exclude-standard"],
                repo_root,
                check=False,
            )
            if proc.returncode == 0:
                files = [
                    line.strip()
                    for line in proc.stdout.splitlines()
                    if line.strip()
                ]
                if files:
                    return files
        except Exception:
            pass

    collected: List[str] = []
    skip_dirs = {
        ".git",
        ".lintel",
        ".agents",
        "target",
        "node_modules",
        ".pytest_cache",
        "__pycache__",
    }
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for f in files:
            if f == ".DS_Store" or f.endswith(".pyc"):
                continue
            full = Path(root) / f
            try:
                rel = full.relative_to(repo_root).as_posix()
                collected.append(rel)
            except ValueError:
                continue
    return collected


def validate_project_config(repo_root: Path) -> Dict[str, Any]:
    """Validate project config and verify no overlapping test groups (T-STG-05)."""
    cfg_file = repo_root / ".lintel" / "config.jsonc"
    if not cfg_file.is_file():
        try:
            cfg = load_config({}, workspace_root=repo_root)
        except Exception as exc:
            msg = f"Refused: invalid config (T-STG-05): {exc}"
            sys.stderr.write(msg + "\n")
            raise TaskRefusalError(msg)
        return cfg

    try:
        content = cfg_file.read_text(encoding="utf-8")
        cfg = load_config(content, workspace_root=repo_root)
    except ConfigError as exc:
        msg = f"Refused: invalid config in {cfg_file.name} (T-STG-05): {exc}"
        sys.stderr.write(msg + "\n")
        raise TaskRefusalError(msg)
    except Exception as exc:
        msg = f"Refused: failed to parse config {cfg_file.name} (T-STG-05): {exc}"
        sys.stderr.write(msg + "\n")
        raise TaskRefusalError(msg)

    # Check for test group overlaps
    project_files = _collect_project_files_for_config(repo_root)
    try:
        overlaps = detect_test_group_overlaps(project_files, cfg, repo_root=repo_root)
        if overlaps:
            first_path = next(iter(overlaps))
            groups = overlaps[first_path]
            msg = (
                f"Refused: invalid config (T-STG-05): test group overlap: "
                f"'{first_path}' matches multiple groups: {groups}"
            )
            sys.stderr.write(msg + "\n")
            raise TaskRefusalError(msg)
    except ConfigError as exc:
        msg = f"Refused: invalid config (T-STG-05): {exc}"
        sys.stderr.write(msg + "\n")
        raise TaskRefusalError(msg)

    return cfg


def check_test_scripts(repo_root: Path, config: Dict[str, Any]) -> None:
    """Check that test group scripts exist and are not unadjusted templates (T-STG-08)."""
    test_groups = config.get("paths", {}).get("tests", {})
    if not isinstance(test_groups, dict):
        return

    for group in test_groups:
        if group == "support" or test_groups[group] is None:
            continue
        script_file = repo_root / ".lintel" / "scripts" / f"test-{group}.sh"
        if script_file.is_file():
            try:
                content = script_file.read_text(encoding="utf-8")
                if is_unadjusted_template(content):
                    msg = (
                        f"Refused: test script for group '{group}' is an unadjusted template (T-STG-08).\n"
                        f"adjust .lintel/scripts/test-{group}.sh"
                    )
                    sys.stderr.write(msg + "\n")
                    raise TaskRefusalError(msg)
            except OSError:
                pass


def get_next_baseline_seq(repo_root: Path) -> int:
    """Determine next sequence number for baseline log in gate/baseline-<seq>.log."""
    gate_dir = repo_root / "gate"
    if not gate_dir.is_dir():
        return 1
    max_seq = 0
    for p in gate_dir.glob("baseline-*.log"):
        m = re.match(r"^baseline-(\d+)\.log$", p.name)
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return max_seq + 1 if max_seq > 0 else 1


def is_id_colliding(repo_root: Path, task_id: str) -> bool:
    """Return True if task_id conflicts with existing task folder, state, or git branch."""
    tasks_dir = repo_root / ".lintel" / "tasks" / task_id
    state_dir = repo_root / ".lintel" / "state" / task_id
    if tasks_dir.exists() or state_dir.exists():
        return True

    proc = run_git(
        ["show-ref", "--verify", "--quiet", f"refs/heads/lintel/{task_id}"],
        repo_root,
        check=False,
    )
    if proc.returncode == 0:
        return True
    return False


def generate_task_id(repo_root: Path, title: str) -> str:
    """Generate task ID <yyyymmdd>-<slug> with collision suffix (-2, -3) if conflicting."""
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    slug = slugify(title, max_length=40)
    base_id = f"{date_str}-{slug}"

    candidate_id = base_id
    suffix = 2
    while is_id_colliding(repo_root, candidate_id):
        candidate_id = f"{base_id}-{suffix}"
        suffix += 1

    return candidate_id


def check_unmerged_finished_branches(repo_root: Path, recorded_branch: str) -> None:
    """Warn if any finished task branch is not merged into recorded_branch."""
    finished_task_ids: Set[str] = set()

    # 1. Read .lintel/state/finished.json if present
    finished_file = repo_root / ".lintel" / "state" / "finished.json"
    if finished_file.is_file():
        try:
            data = json.loads(finished_file.read_text(encoding="utf-8"))
            for t in data.get("tasks", []):
                tid = t.get("id")
                if tid:
                    finished_task_ids.add(tid)
        except Exception:
            pass

    # 2. Check .lintel/state/*/task.json for status == 'finished'
    state_dir = repo_root / ".lintel" / "state"
    if state_dir.is_dir():
        try:
            for entry in state_dir.iterdir():
                if entry.is_dir():
                    t_json = entry / "task.json"
                    if t_json.is_file():
                        try:
                            data = json.loads(t_json.read_text(encoding="utf-8"))
                            if isinstance(data, dict) and data.get("status") == "finished":
                                finished_task_ids.add(entry.name)
                        except Exception:
                            pass
        except OSError:
            pass

    if not finished_task_ids:
        return

    proc = run_git(["branch", "--no-merged", recorded_branch], repo_root, check=False)
    if proc.returncode != 0:
        return

    for line in proc.stdout.splitlines():
        b_name = line.strip().lstrip("* ").strip()
        if b_name.startswith("lintel/"):
            tid = b_name[len("lintel/"):]
            if tid in finished_task_ids:
                print(
                    f"Warning: finished task branch '{b_name}' is not merged into '{recorded_branch}'",
                    flush=True,
                )


def task_start(
    repo_root: Optional[Union[str, Path]] = None,
    title: str = "",
    tmp_root: Optional[Union[str, Path]] = None,
) -> str:
    """Execute 'lintel task start "<title>"' workflow.
    
    Returns the newly created task ID.
    Raises TaskRefusalError (SystemExit 1) on any failure/guard violation.
    """
    root = Path(repo_root).resolve() if repo_root else Path.cwd().resolve()
    clean_title = title.strip()
    if not clean_title:
        msg = "Refused: task title cannot be empty."
        sys.stderr.write(msg + "\n")
        raise TaskRefusalError(msg)

    # 1. Check no active task (T-STG-02)
    active = find_active_task_in_state(root)
    if active is not None:
        active_id, _ = active
        msg = f"Refused: a task is already active: '{active_id}' (T-STG-02). Run 'lintel task status'."
        sys.stderr.write(msg + "\n")
        raise TaskRefusalError(msg)

    # 2. Check interpreter (T-STG-03)
    interp_path = check_recorded_interpreter(root)

    # 3. Check clean tree (T-STG-04)
    check_clean_tree(root)

    # 4. Validate config (T-STG-05)
    cfg = validate_project_config(root)

    # 5. Check test scripts (T-STG-08)
    check_test_scripts(root, cfg)

    # 6. Git state and branches
    proc_branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], root, check=True)
    recorded_branch = proc_branch.stdout.strip()
    proc_head = run_git(["rev-parse", "HEAD"], root, check=True)
    base_sha = proc_head.stdout.strip()

    # 7. Run baseline gate run in gate worktree (T-STG-01, T-STG-06, T-STG-07)
    resolved_tmp = resolve_tmp_root(tmp_root)
    seq = get_next_baseline_seq(root)
    baseline_log_file = root / "gate" / f"baseline-{seq}.log"
    baseline_run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        gate_res = run_gate(
            repo_root=root,
            command="baseline",
            mode=GateMode.RUN_ALL,
            parent_commit=base_sha,
            tmp_root=resolved_tmp,
            log_path=baseline_log_file,
            state_dir=root / ".lintel" / "state",
            config=cfg,
        )
    except Exception as exc:
        msg = f"Refused: baseline gate run execution failed: {exc}"
        sys.stderr.write(msg + "\n")
        raise TaskRefusalError(msg)

    # Verify baseline greenness (T-STG-06)
    groups_green = True
    for gname, gres in gate_res.groups.items():
        if gres.status not in ("green", "no-tests"):
            groups_green = False
            break

    setup_ok = (gate_res.setup_result is None or gate_res.setup_result.exit_code == 0)
    if not (setup_ok and groups_green):
        msg = (
            f"Refused: baseline test run is not green (T-STG-06):\n"
            f"A red existing group blocks Lintel until it is fixed or its script is narrowed.\n"
            f"Baseline log kept at gate/baseline-{seq}.log"
        )
        sys.stderr.write(msg + "\n")
        raise TaskRefusalError(msg)

    # Verify gate worktree was clean after the run (T-STG-07)
    if not gate_res.worktree_clean or not gate_res.tests_clean:
        violations = gate_res.worktree_violations or gate_res.tests_violations
        v_lines = "\n".join(f"  {v}" for v in violations)
        msg = (
            f"Refused: baseline run left modifications or untracked files in gate worktree (T-STG-07):\n"
            f"{v_lines}\n"
            f"the project must gitignore what its test run leaves."
        )
        sys.stderr.write(msg + "\n")
        raise TaskRefusalError(msg)

    # 8. Task ID generation
    task_id = generate_task_id(root, clean_title)

    # 9. Warning for unmerged finished task branches
    check_unmerged_finished_branches(root, recorded_branch)

    # 10. Create branch lintel/<id> from base SHA and switch to it
    run_git(["checkout", "-b", f"lintel/{task_id}", base_sha], root, check=True)

    # 11. State and directories setup
    task_state_dir = root / ".lintel" / "state" / task_id
    task_tasks_dir = root / ".lintel" / "tasks" / task_id
    task_shared_dir = task_tasks_dir / "shared"
    task_gate_dir = task_tasks_dir / "gate"

    task_state_dir.mkdir(parents=True, exist_ok=True)
    task_shared_dir.mkdir(parents=True, exist_ok=True)
    task_gate_dir.mkdir(parents=True, exist_ok=True)

    # 12. Write shared/request.md and compute hash
    request_content = clean_title + "\n"
    request_file = task_shared_dir / "request.md"
    request_file.write_text(request_content, encoding="utf-8")
    request_hash = hashlib.sha256(request_content.encode("utf-8")).hexdigest()

    # 13. Move baseline log(s) to task gate folder
    gate_dir = root / "gate"
    if gate_dir.is_dir():
        for b_log in sorted(gate_dir.glob("baseline-*.log")):
            if re.match(r"^baseline-\d+\.log$", b_log.name) and b_log.is_file():
                shutil.move(str(b_log), str(task_gate_dir / b_log.name))
        try:
            if not any(gate_dir.iterdir()):
                gate_dir.rmdir()
        except OSError:
            pass

    # 14. Record baseline metadata
    baseline_groups_dict: Dict[str, Dict[str, Any]] = {}
    for gname, gres in gate_res.groups.items():
        baseline_groups_dict[gname] = {
            "exit": gres.exit_code if gres.exit_code is not None else 0,
            "status": gres.status,
        }

    baseline_data: Dict[str, Any] = {
        "at": baseline_run_at,
        "log": f"gate/baseline-{seq}.log",
        "groups": baseline_groups_dict,
    }

    # 15. Construct task.json conforming to docs/state-machine.md §9.3
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    task_data: Dict[str, Any] = {
        "version": 1,
        "id": task_id,
        "title": clean_title,
        "status": "active",
        "created_at": created_at,
        "finished_at": None,
        "branch": f"lintel/{task_id}",
        "recorded_branch": recorded_branch,
        "base_sha": base_sha,
        "tmp": str(resolved_tmp),
        "interpreter": interp_path,
        "head": base_sha,
        "intended": None,
        "request_hash": request_hash,
        "answers_hash": None,
        "event_seq": 1,
        "orchestrator": {
            "conversation_id": os.environ.get("ANTIGRAVITY_CONVERSATION_ID"),
            "since": created_at,
        },
        "baseline": baseline_data,
        "known_requirement_ids": [],
        "position": {
            "stage": "r0",
            "code": "W.po",
            "phase": None,
            "fr": False,
            "run": None,
        },
        "route": None,
        "cycle": None,
        "revisit": None,
        "deferred": [],
        "memory": [],
        "escalation": None,
        "docs_update": None,
        "phases": {},
        "delivered_phases": [],
        "counters": {
            "run": {},
            "gate": {},
            "snapshot": 0,
            "agent": 0,
            "deferred": 0,
        },
        "snapshots": [],
        "agent_commits": [],
    }

    # Validate state document
    task_json_file = task_state_dir / "task.json"
    validate_state_doc("task", task_data, file_path=task_json_file)
    atomic_write_json(task_json_file, task_data)

    # 16. Append task_start event to events.jsonl
    event_rec = create_event(
        step=1,
        event="start",
        stage="r0",
        row="T-STG-01",
    )
    append_event(
        state_dir=root / ".lintel" / "state",
        task_id=task_id,
        event_data=event_rec,
        task_dir=task_tasks_dir,
    )

    # 17. Render initial progress.md
    update_progress_md(root, task_data, "lintel invoke po --phase r0")

    # 18. Print next action
    print("lintel invoke po --phase r0", flush=True)

    return task_id


def task_status(repo_root: Optional[Union[str, Path]] = None) -> int:
    """Execute 'lintel task status' command.
    
    If active task exists, prints progress.md.
    If no active task, prints 'No active task.' and exits 0.
    """
    root = Path(repo_root).resolve() if repo_root else Path.cwd().resolve()
    active = find_active_task_in_state(root)
    if not active:
        print("No active task.", flush=True)
        return 0

    task_id, task_data = active
    progress_file = root / ".lintel" / "tasks" / task_id / "progress.md"
    if not progress_file.is_file():
        next_act = format_next_action(task_data, repo_root=root)
        update_progress_md(root, task_data, next_act)

    if progress_file.is_file():
        content = progress_file.read_text(encoding="utf-8")
        print(content.rstrip(), flush=True)
    else:
        content = render_progress_md(root, task_id, task_data)
        print(content.rstrip(), flush=True)

    return 0
