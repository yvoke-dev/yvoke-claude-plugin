"""Diff command implementation (M1a-29).

Implements:
1. diff (PLAN §3.5, docs/state-machine.md T-HUM-40, T-STG-23):
   - Read-only: open to anyone (no caller dialog H0).
   - Check active task (T-STG-09).
   - Refuse if both --base and --delivered are provided.
   - Three modes:
     Mode 1: diff --delivered N:
       - N must be a delivered phase (T-STG-23).
       - Lock commit to delivery commit diff.
       - Prints only 'code' class files. No test paths count.
     Mode 2: diff --base:
       - Base commit (task.base_sha) to working tree diff.
       - Prints only 'code' class files. No test paths count.
     Mode 3: diff (default, against recorded head):
       - Recorded head (task.head) to working tree diff.
       - Prints only 'code' class files.
       - At the end, checks working tree against task.head for modified/added/deleted 'tests' files:
         - If N > 0: prints "N test paths modified".
         - If N == 0: prints nothing extra.
   - CRITICAL: Never prints 'tests' or 'task' paths in unified diff!
"""

import sys
sys.dont_write_bytecode = True

import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Union

try:
    from lintel import config as config_mod, flow, git, paths, state
    from lintel.state import load_state_file, resolve_state_root
    from lintel.task_start import find_active_task_in_state
except ImportError:
    from . import config as config_mod, flow, git, paths, state
    from .state import load_state_file, resolve_state_root
    from .task_start import find_active_task_in_state


class DiffRefusalError(flow.FlowRefusalError):
    """Raised when diff is refused per state machine rules."""

    def __init__(self, message: str, code: int = 1, row: Optional[str] = None):
        super().__init__(message=message, code=code, row=row)


def _load_head_config(repo_root: Path, head_sha: Optional[str]) -> Dict[str, Any]:
    """Load project config from recorded head tree per PLAN §3.6."""
    if head_sha:
        res = git.run_git(["show", f"{head_sha}:.lintel/config.jsonc"], repo_root, check=False)
        if res.returncode == 0 and res.stdout.strip():
            try:
                return config_mod.load_config(res.stdout, workspace_root=repo_root)
            except Exception:
                pass
    cfg_file = repo_root / ".lintel" / "config.jsonc"
    if cfg_file.is_file():
        try:
            return config_mod.load_config(cfg_file, workspace_root=repo_root)
        except Exception:
            pass
    return config_mod.load_config({}, workspace_root=repo_root)


def diff(
    repo_root: Optional[Union[str, Path]] = None,
    base: bool = False,
    delivered: Optional[Union[int, str]] = None,
    full: bool = False,
) -> str:
    """Show code diffs excluding tests and task paths per PLAN §3.5 and T-HUM-40."""
    if repo_root is None:
        root = Path.cwd()
    else:
        root = Path(repo_root).resolve()

    state_dir = root / ".lintel" / "state"

    # Refuse if both --base and --delivered are provided
    if base and delivered is not None:
        raise DiffRefusalError(
            "Refused: cannot combine --base and --delivered (T-HUM-40)",
            row="T-HUM-40",
        )

    # Check active task (T-STG-09)
    active = find_active_task_in_state(root)
    if not active:
        raise DiffRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active
    task_state_file = state_dir / task_id / "task.json"
    task_data = load_state_file(task_state_file, doc_type="task")

    head_sha = task_data.get("head") or task_data.get("recorded_head")
    cfg = _load_head_config(root, head_sha)

    # Mode 1: diff --delivered N (T-STG-23)
    if delivered is not None:
        raw_s = str(delivered).strip()
        m = re.match(r"^p?(\d+)$", raw_s, re.IGNORECASE)
        if not m:
            raise DiffRefusalError(
                f"Refused: phase '{delivered}' is not a delivered phase (T-STG-23)",
                row="T-STG-23",
            )
        p_num = int(m.group(1))

        if not flow.is_phase_delivered(task_data, p_num):
            raise DiffRefusalError(
                f"Refused: phase '{delivered}' is not a delivered phase (T-STG-23)",
                row="T-STG-23",
            )

        p_info = task_data.get("phases", {}).get(str(p_num), {})
        lock_commit = p_info.get("lock", {}).get("commit")
        deliv_commit = p_info.get("delivered", {}).get("commit")
        if not lock_commit or not deliv_commit:
            raise DiffRefusalError(
                f"Refused: missing lock or delivery commit for phase {p_num} (T-STG-23)",
                row="T-STG-23",
            )

        # Get changed paths between lock_commit and deliv_commit
        proc_changed = git.run_git(
            ["diff-tree", "-r", "--name-only", lock_commit, deliv_commit],
            root,
            check=True,
        )
        changed_paths = [p.strip() for p in proc_changed.stdout.splitlines() if p.strip()]

        code_paths: List[str] = []
        for p in changed_paths:
            cls = paths.classify_path(p, config=cfg, repo_root=root, is_tracked=True)
            if str(cls) == "code":
                code_paths.append(p)

        if code_paths:
            proc_diff = git.run_git(
                ["diff", "-u", lock_commit, deliv_commit, "--", *sorted(code_paths)],
                root,
                check=True,
            )
            out = proc_diff.stdout
        else:
            out = ""

        if out:
            sys.stdout.write(out if out.endswith("\n") else out + "\n")
        return out

    # Mode 2: diff --base (T-HUM-40)
    if base:
        base_sha = task_data.get("base_sha") or task_data.get("base")
        if not base_sha:
            raise DiffRefusalError("Refused: no base SHA recorded for active task (T-HUM-40)", row="T-HUM-40")

        cs = git.get_change_set(root, base_sha, config=cfg)
        code_paths = [e.path for e in cs.entries.values() if str(e.path_class) == "code"]

        if code_paths:
            proc_diff = git.run_git(
                ["diff", "-u", base_sha, cs.tree_id, "--", *sorted(code_paths)],
                root,
                check=True,
            )
            out = proc_diff.stdout
        else:
            out = ""

        if out:
            sys.stdout.write(out if out.endswith("\n") else out + "\n")
        return out

    # Mode 3: diff default against recorded head (T-HUM-40)
    if not head_sha:
        raise DiffRefusalError("Refused: no recorded head commit for active task (T-HUM-40)", row="T-HUM-40")

    cs = git.get_change_set(root, head_sha, config=cfg)
    code_paths = [e.path for e in cs.entries.values() if str(e.path_class) == "code"]
    test_paths = [e.path for e in cs.entries.values() if str(e.path_class) == "tests"]

    if code_paths:
        proc_diff = git.run_git(
            ["diff", "-u", head_sha, cs.tree_id, "--", *sorted(code_paths)],
            root,
            check=True,
        )
        diff_text = proc_diff.stdout
    else:
        diff_text = ""

    n_tests = len(test_paths)
    if n_tests > 0:
        test_line = f"{n_tests} test paths modified\n"
        if diff_text:
            out = diff_text.rstrip("\n") + "\n" + test_line
        else:
            out = test_line
    else:
        out = diff_text

    if out:
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
    return out
