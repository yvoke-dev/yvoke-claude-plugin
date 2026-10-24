"""Git plumbing and index manipulation (M1a-08).

Implements:
1. Git check:
   - Check `git --version` >= 2.25.0 (MIN_GIT_VERSION).
2. Temporary index change set (`get_change_set`):
   - Use dedicated temporary index file (`GIT_INDEX_FILE` under `<tmp>` or pytest `tmp_path`).
   - `git read-tree <head>`.
   - `git add -A -- . ':!.lintel/tasks'`.
   - `tree_id = git write-tree`.
   - Diff working tree snapshot against recorded head:
     `git diff-tree -r --name-status <head_tree> <working_tree>`.
   - Compute path classes using the recorded head's `config.jsonc` (loaded via
     `git show <head>:.lintel/config.jsonc` using `load_config`), NOT from working tree.
   - Refuse any symlink pointing outside the repository (raise `GitError`).
3. Lane hashes (`compute_lane_hash`):
   - Computes sha256 over sorted tuples of `(path, mode, blob)` for paths in the lane.
   - A deleted path is entered as `(path, "deleted")`.
4. Commits (`create_commit`):
   - Fixed identities:
     - `po`: Lintel PO <po@noreply.local>
     - `architect`: Lintel Architect <architect@noreply.local>
     - `qa`: Lintel QA <qa@noreply.local>
     - `dev`: Lintel DEV <dev@noreply.local>
     - `lintel`: Lintel <lintel@noreply.local>
   - Build commit through `git commit-tree <tree>` with `--no-gpg-sign`.
   - Trailer: `Lintel-Step: <task_id>/<event_seq>`.
5. Ref moves & state sync (`advance_branch`):
   - Refuse if `HEAD` is not on the task branch (`refs/heads/lintel/<id>`).
   - Two-phase write:
     1. Write intended commit id to state (`task.intended = new_commit`).
     2. Move ref via `git update-ref refs/heads/lintel/<id> <new_commit> <expected_tip>`.
     3. Update state (`task.head = new_commit`, `task.intended = None`).
     4. Reset index (`git read-tree <new_commit>`).
   - Roll-forward (`check_and_roll_forward`): On startup, if branch tip equals
     `intended_commit` while `head` was not yet updated, roll the state forward.
6. Agent commits & snapshots:
   - Agent commit detection (`detect_agent_commits`): If the actual branch tip is neither
     `recorded_head` nor `intended_commit`, save it under `refs/lintel/<id>/agent-<n>`.
   - Snapshot refs (`create_snapshot_ref`): Save current working state to
     `refs/lintel/<id>/snapshot-<n>`.
"""

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

try:
    from lintel.config import ConfigError, load_config
    from lintel.paths import PathClass, assign_test_group, classify_path, normalize_path
    from lintel.state import StateError, atomic_write_json, load_state_file, state_lock
except ImportError:
    from .config import ConfigError, load_config
    from .paths import PathClass, assign_test_group, classify_path, normalize_path
    from .state import StateError, atomic_write_json, load_state_file, state_lock


MIN_GIT_VERSION: Tuple[int, int, int] = (2, 25, 0)

# Fixed commit author / committer identities (PLAN §3.5)
COMMIT_IDENTITIES: Dict[str, Tuple[str, str]] = {
    "po": ("Lintel PO", "po@noreply.local"),
    "architect": ("Lintel Architect", "architect@noreply.local"),
    "qa": ("Lintel QA", "qa@noreply.local"),
    "dev": ("Lintel DEV", "dev@noreply.local"),
    "lintel": ("Lintel", "lintel@noreply.local"),
}


class GitError(Exception):
    """Raised when a git operation fails or an invariant is violated."""
    pass


# ---------------------------------------------------------------------------
# 1. Git Version Checking & Subprocess Execution
# ---------------------------------------------------------------------------

def parse_git_version(version_output: str) -> Tuple[int, int, int]:
    """Parse git version string into a (major, minor, patch) integer tuple."""
    match = re.search(r"git version (\d+)\.(\d+)(?:\.(\d+))?", version_output)
    if not match:
        raise GitError(f"Could not parse git version output: {version_output!r}")
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3)) if match.group(3) else 0
    return major, minor, patch


def check_git_version(
    min_version: Tuple[int, int, int] = MIN_GIT_VERSION,
    git_binary: str = "git",
) -> Tuple[int, int, int]:
    """Verify that git binary is present and >= min_version (default 2.25.0)."""
    try:
        proc = subprocess.run(
            [git_binary, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise GitError(f"git binary '{git_binary}' not found on PATH: {e}") from e

    if proc.returncode != 0:
        raise GitError(f"git --version failed (exit {proc.returncode}): {proc.stderr.strip()}")

    ver = parse_git_version(proc.stdout)
    if ver < min_version:
        min_str = ".".join(str(x) for x in min_version)
        cur_str = ".".join(str(x) for x in ver)
        raise GitError(
            f"Git version {cur_str} is below minimum required {min_str}"
        )
    return ver


def run_git(
    args: Sequence[str],
    repo_root: Union[str, Path],
    env: Optional[Dict[str, str]] = None,
    input: Optional[Union[str, bytes]] = None,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    """Execute git command within repo_root."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)

    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            env=full_env,
            input=input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            check=False,
        )
    except FileNotFoundError as e:
        raise GitError(f"git binary not found: {e}") from e

    if check and proc.returncode != 0:
        stderr_msg = proc.stderr.strip() if isinstance(proc.stderr, str) else proc.stderr.decode("utf-8", "replace").strip()
        cmd_str = "git " + " ".join(args)
        raise GitError(f"{cmd_str} failed (exit {proc.returncode}): {stderr_msg}")

    return proc


def get_ref(repo_root: Union[str, Path], ref_name: str) -> Optional[str]:
    """Return the commit SHA for a ref, or None if it does not exist."""
    proc = run_git(["rev-parse", "--verify", "-q", ref_name], repo_root, check=False)
    if proc.returncode == 0:
        sha = proc.stdout.strip()
        return sha if sha else None
    return None


def get_symbolic_ref(repo_root: Union[str, Path], ref_name: str = "HEAD") -> Optional[str]:
    """Return the symbolic ref target (e.g. 'refs/heads/lintel/task-1'), or None if detached."""
    proc = run_git(["symbolic-ref", "-q", ref_name], repo_root, check=False)
    if proc.returncode == 0:
        target = proc.stdout.strip()
        return target if target else None
    return None


def is_head_on_branch(repo_root: Union[str, Path], branch_name: str) -> bool:
    """Check if HEAD is currently attached to refs/heads/<branch_name>."""
    head_ref = get_symbolic_ref(repo_root, "HEAD")
    if not head_ref:
        return False
    target = f"refs/heads/{branch_name}" if not branch_name.startswith("refs/") else branch_name
    return head_ref == target


def update_ref(
    repo_root: Union[str, Path],
    ref_name: str,
    new_sha: str,
    expected_sha: Optional[str] = None,
) -> None:
    """Move a ref using git update-ref, optionally with compare-and-swap expected_sha."""
    args = ["update-ref", ref_name, new_sha]
    if expected_sha:
        args.append(expected_sha)
    run_git(args, repo_root, check=True)


# ---------------------------------------------------------------------------
# 2. Change Set & Path Classification
# ---------------------------------------------------------------------------

class ChangeEntry:
    """Single file modification, addition, or deletion in a change set."""

    def __init__(
        self,
        path: str,
        status: str,
        mode: str,
        blob: str,
        old_mode: Optional[str] = None,
        old_blob: Optional[str] = None,
        path_class: Optional[PathClass] = None,
        test_group: Optional[str] = None,
    ):
        self.path = path
        self.status = status
        self.mode = mode
        self.blob = blob
        self.old_mode = old_mode
        self.old_blob = old_blob
        self.path_class = path_class
        self.test_group = test_group

    def is_deleted(self) -> bool:
        return self.status == "D"

    def is_added(self) -> bool:
        return self.status == "A"

    def is_modified(self) -> bool:
        return self.status == "M"

    def __repr__(self) -> str:
        return (
            f"ChangeEntry(path={self.path!r}, status={self.status!r}, "
            f"mode={self.mode!r}, blob={self.blob[:8]!r}, class={self.path_class!r})"
        )


class ChangeSet:
    """Snapshot of working tree changes diffed against recorded head commit."""

    def __init__(
        self,
        head_commit: str,
        head_tree: str,
        tree_id: str,
        entries: Dict[str, ChangeEntry],
        config: dict,
    ):
        self.head_commit = head_commit
        self.head_tree = head_tree
        self.tree_id = tree_id
        self.entries = entries
        self.config = config

    @property
    def paths(self) -> List[str]:
        return sorted(self.entries.keys())

    def get(self, path: str) -> Optional[ChangeEntry]:
        return self.entries.get(path)

    def __contains__(self, path: str) -> bool:
        return path in self.entries

    def filter_by_class(self, path_class: str) -> List[ChangeEntry]:
        return [e for e in self.entries.values() if e.path_class == path_class]

    def filter_by_group(self, group: str) -> List[ChangeEntry]:
        return [e for e in self.entries.values() if e.test_group == group]

    def has_changes(self) -> bool:
        return len(self.entries) > 0


def _is_symlink_outside(repo_root: Path, link_rel_path: str, target_str: str) -> bool:
    """Check if symlink target points outside the repository."""
    repo_real = repo_root.resolve()
    link_abs = repo_root / link_rel_path
    if os.path.isabs(target_str):
        target_abs = Path(target_str).resolve()
    else:
        target_abs = (link_abs.parent / target_str).resolve()

    try:
        target_abs.relative_to(repo_real)
        return False
    except ValueError:
        return True


def get_change_set(
    repo_root: Union[str, Path],
    head_commit: str,
    tmp_dir: Optional[Union[str, Path]] = None,
    config: Optional[dict] = None,
) -> ChangeSet:
    """Build a temporary index change set comparing working tree against recorded head.

    Steps:
    1. Dedicated temporary index file under tmp_dir or system temp.
    2. git read-tree <head_commit> into temporary index.
    3. git add -A -- . ':!.lintel/tasks' into temporary index.
    4. tree_id = git write-tree.
    5. Diff working tree snapshot against recorded head:
       git diff-tree -r --name-status <head_tree> <working_tree>.
    6. Compute path classes using the recorded head's config.jsonc (loaded via
       git show <head_commit>:.lintel/config.jsonc), NOT from the working tree.
    7. Refuse any symlink pointing outside the repository (raise GitError).
    """
    repo = Path(repo_root).resolve()

    # 1. Dedicated temporary index file
    if tmp_dir:
        tmp_parent = Path(tmp_dir).resolve()
        tmp_parent.mkdir(parents=True, exist_ok=True)
    else:
        tmp_parent = Path(tempfile.gettempdir()).resolve()

    idx_filename = f"lintel_idx_{os.getpid()}_{time.time_ns()}.tmp"
    index_file = tmp_parent / idx_filename
    env = {"GIT_INDEX_FILE": str(index_file)}

    try:
        # 2. git read-tree <head_commit>
        run_git(["read-tree", head_commit], repo, env=env, check=True)

        # 3. git add -A -- . ':!.lintel/tasks' ':!gate/baseline-*.log'
        run_git(
            ["add", "-A", "--", ".", ":!.lintel/tasks", ":!gate/baseline-*.log"],
            repo,
            env=env,
            check=True,
        )

        # 4. tree_id = git write-tree
        proc_wt = run_git(["write-tree"], repo, env=env, check=True)
        working_tree = proc_wt.stdout.strip()

        # 5. Diff working tree snapshot against recorded head
        proc_ht = run_git(["rev-parse", f"{head_commit}^{{tree}}"], repo, check=True)
        head_tree = proc_ht.stdout.strip()

        # Run diff-tree with -z --raw to get unambiguous paths, modes, and blob SHAs
        proc_raw = run_git(
            ["diff-tree", "-r", "-z", "--raw", head_tree, working_tree],
            repo,
            check=True,
        )

        # Parse raw diff-tree records
        tokens = proc_raw.stdout.split("\0")
        i = 0
        raw_entries: Dict[str, Dict[str, Any]] = {}
        while i < len(tokens):
            tok = tokens[i]
            if not tok:
                i += 1
                continue
            if not tok.startswith(":"):
                i += 1
                continue

            parts = tok[1:].split()
            if len(parts) < 5:
                i += 1
                continue

            old_mode, new_mode, old_sha, new_sha, status = parts[0], parts[1], parts[2], parts[3], parts[4]
            i += 1
            if i >= len(tokens):
                break
            path = tokens[i]
            i += 1

            if status.startswith("R") or status.startswith("C"):
                if i < len(tokens):
                    new_path = tokens[i]
                    i += 1
                    path = new_path

            mode = new_mode if status != "D" else old_mode
            blob = new_sha if status != "D" else old_sha

            raw_entries[path] = {
                "path": path,
                "status": status,
                "mode": mode,
                "blob": blob,
                "old_mode": old_mode if status != "A" else None,
                "old_blob": old_sha if status != "A" else None,
            }

        # 6. Load config from recorded head's tree (NOT working tree)
        head_cfg = config
        if head_cfg is None:
            show_proc = run_git(
                ["show", f"{head_commit}:.lintel/config.jsonc"],
                repo,
                check=False,
            )
            if show_proc.returncode == 0 and show_proc.stdout.strip():
                try:
                    head_cfg = load_config(show_proc.stdout, workspace_root=repo)
                except Exception:
                    head_cfg = load_config({}, workspace_root=repo)
            else:
                head_cfg = load_config({}, workspace_root=repo)

        # 7. Build ChangeEntries, check symlinks, and classify
        entries: Dict[str, ChangeEntry] = {}
        for path, info in raw_entries.items():
            entry_mode = info["mode"]
            entry_status = info["status"]
            entry_blob = info["blob"]

            # Symlink check (mode 120000)
            if entry_status != "D":
                full_path = repo / path
                is_link = (entry_mode == "120000") or os.path.islink(full_path)
                if is_link:
                    target_str: Optional[str] = None
                    if os.path.islink(full_path):
                        try:
                            target_str = os.readlink(full_path)
                        except OSError:
                            pass
                    if target_str is None and entry_blob and entry_blob != "0" * 40:
                        cat_proc = run_git(["cat-file", "-p", entry_blob], repo, check=False)
                        if cat_proc.returncode == 0:
                            target_str = cat_proc.stdout.strip()

                    if target_str is not None and _is_symlink_outside(repo, path, target_str):
                        raise GitError(
                            f"Refused: symlink '{path}' points outside repository to '{target_str}'"
                        )

            # Classify path using head's config
            is_tracked = (entry_status != "A")
            p_class = classify_path(
                path,
                config=head_cfg,
                repo_root=repo,
                is_tracked=is_tracked,
            )

            test_grp: Optional[str] = None
            if str(p_class) == "tests":
                test_grp = assign_test_group(path, config=head_cfg, repo_root=repo)

            entries[path] = ChangeEntry(
                path=path,
                status=entry_status,
                mode=entry_mode,
                blob=entry_blob,
                old_mode=info["old_mode"],
                old_blob=info["old_blob"],
                path_class=p_class,
                test_group=test_grp,
            )

        return ChangeSet(
            head_commit=head_commit,
            head_tree=head_tree,
            tree_id=working_tree,
            entries=entries,
            config=head_cfg,
        )

    finally:
        if index_file.exists():
            try:
                index_file.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 3. Lane Hashes
# ---------------------------------------------------------------------------

def compute_lane_hash(
    entries: Union[Iterable[Union[Tuple[str, ...], ChangeEntry]], ChangeSet],
    lane_paths: Optional[Iterable[str]] = None,
) -> str:
    """Compute sha256 over sorted tuples of (path, mode, blob) for paths in the lane.

    A deleted path is entered as (path, 'deleted').
    Accepts:
    - ChangeSet instance (optionally filtered by lane_paths)
    - Iterable of ChangeEntry objects
    - Iterable of (path, mode, blob) or (path, 'deleted') tuples
    """
    filter_set = set(lane_paths) if lane_paths is not None else None
    tuples: List[Tuple[str, ...]] = []

    if isinstance(entries, ChangeSet):
        for path, entry in entries.entries.items():
            if filter_set is not None and path not in filter_set:
                continue
            if entry.is_deleted():
                tuples.append((path, "deleted"))
            else:
                tuples.append((path, entry.mode, entry.blob))
    else:
        for item in entries:
            if isinstance(item, ChangeEntry):
                if filter_set is not None and item.path not in filter_set:
                    continue
                if item.is_deleted():
                    tuples.append((item.path, "deleted"))
                else:
                    tuples.append((item.path, item.mode, item.blob))
            elif isinstance(item, (tuple, list)):
                if not item:
                    continue
                path = item[0]
                if filter_set is not None and path not in filter_set:
                    continue
                if len(item) == 2 or (len(item) >= 2 and item[1] == "deleted"):
                    tuples.append((path, "deleted"))
                elif len(item) >= 3:
                    if item[2] == "deleted":
                        tuples.append((path, "deleted"))
                    else:
                        tuples.append((path, str(item[1]), str(item[2])))

    # Sort tuples deterministically by path
    sorted_tuples = sorted(tuples, key=lambda t: t[0])

    lines: List[str] = []
    for t in sorted_tuples:
        if len(t) == 2 or t[1] == "deleted":
            lines.append(f"{t[0]}\tdeleted\n")
        else:
            lines.append(f"{t[0]}\t{t[1]}\t{t[2]}\n")

    payload = "".join(lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# 4. Commits (`create_commit`)
# ---------------------------------------------------------------------------

def create_commit(
    repo_root: Union[str, Path],
    tree_id: str,
    parent: Optional[Union[str, Sequence[str]]] = None,
    message: str = "",
    author_role: str = "lintel",
    task_id: Optional[str] = None,
    event_seq: Optional[int] = None,
) -> str:
    """Create a commit using git commit-tree with --no-gpg-sign and fixed identities.

    Runs no hooks, and passes even if repo has commit.gpgsign = true.
    Appends trailer: 'Lintel-Step: <task_id>/<event_seq>' if provided.
    """
    repo = Path(repo_root).resolve()

    role_key = author_role.lower()
    if role_key not in COMMIT_IDENTITIES:
        raise GitError(
            f"Unknown author role {author_role!r}; must be one of {sorted(COMMIT_IDENTITIES.keys())}"
        )
    author_name, author_email = COMMIT_IDENTITIES[role_key]

    # Format trailer
    trailer = None
    if task_id is not None and event_seq is not None:
        trailer = f"Lintel-Step: {task_id}/{event_seq}"

    msg_body = message.rstrip()
    if trailer and trailer not in msg_body:
        if msg_body:
            full_message = f"{msg_body}\n\n{trailer}\n"
        else:
            full_message = f"{trailer}\n"
    else:
        full_message = f"{msg_body}\n" if msg_body else "\n"

    cmd = ["commit-tree", tree_id, "--no-gpg-sign"]
    if parent:
        if isinstance(parent, str):
            cmd.extend(["-p", parent])
        else:
            for p in parent:
                if p:
                    cmd.extend(["-p", p])

    env = {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
    }

    proc = run_git(cmd, repo, env=env, input=full_message, check=True)
    commit_sha = proc.stdout.strip()
    if not commit_sha:
        raise GitError("git commit-tree produced empty output")
    return commit_sha


# ---------------------------------------------------------------------------
# 5. Ref Moves & State Synchronization
# ---------------------------------------------------------------------------

def _resolve_task_json_path(
    repo_root: Union[str, Path],
    task_id: str,
    state_dir: Optional[Union[str, Path]] = None,
    task_path: Optional[Union[str, Path]] = None,
) -> Path:
    """Resolve absolute path to task.json."""
    if task_path:
        return Path(task_path).resolve()
    if state_dir:
        sdir = Path(state_dir).resolve()
        if (sdir / "task.json").is_file():
            return sdir / "task.json"
        if (sdir / task_id / "task.json").is_file():
            return sdir / task_id / "task.json"
        return sdir / task_id / "task.json"
    return Path(repo_root).resolve() / ".lintel" / "state" / task_id / "task.json"


def advance_branch(
    repo_root: Union[str, Path],
    task_id: str,
    new_commit: str,
    expected_tip: Optional[str] = None,
    state_dir: Optional[Union[str, Path]] = None,
    task_path: Optional[Union[str, Path]] = None,
) -> None:
    """Advance the task branch to new_commit with two-phase state write and index reset.

    1. Refuse if HEAD is not on the task branch (refs/heads/lintel/<id>).
    2. Write intended commit id to state (task.intended = new_commit).
    3. Move ref via git update-ref refs/heads/lintel/<id> <new_commit> <expected_tip>.
    4. Update state (task.head = new_commit, task.intended = None).
    5. Reset index (git read-tree <new_commit>), leaving working tree untouched.
    """
    repo = Path(repo_root).resolve()
    target_branch = f"refs/heads/lintel/{task_id}"

    # 1. Refuse if HEAD is not on task branch
    current_head = get_symbolic_ref(repo, "HEAD")
    if current_head != target_branch:
        raise GitError(
            f"Refused: HEAD is not on task branch '{target_branch}' (currently {current_head!r})"
        )

    task_file = _resolve_task_json_path(repo, task_id, state_dir=state_dir, task_path=task_path)
    if not task_file.is_file():
        raise GitError(f"Task state file not found: {task_file}")

    lock_dir = task_file.parent
    with state_lock(lock_dir):
        # 2. Phase 1: Write intended commit id to state
        task_data = load_state_file(task_file, doc_type="task")
        task_data["intended"] = new_commit
        task_data["intended_commit"] = new_commit
        atomic_write_json(task_file, task_data)

        # 3. Move ref via git update-ref
        tip = expected_tip
        if tip is None:
            tip = get_ref(repo, target_branch)

        update_ref(repo, target_branch, new_commit, expected_sha=tip)

        # 4. Phase 2: Update state proper
        task_data["head"] = new_commit
        task_data["recorded_head"] = new_commit
        task_data["intended"] = None
        task_data["intended_commit"] = None
        atomic_write_json(task_file, task_data)

        # 5. Reset index to new_commit, leaving working tree untouched
        run_git(["read-tree", new_commit], repo, check=True)


def check_and_roll_forward(
    repo_root: Union[str, Path],
    task_id: str,
    state_dir: Optional[Union[str, Path]] = None,
    task_path: Optional[Union[str, Path]] = None,
) -> bool:
    """Roll state forward if CLI died between ref move and state write (T-STG-11).

    If branch tip equals intended commit while head was not yet updated,
    set task.head = intended, task.intended = None, and reset index.
    Returns True if rolled forward, False otherwise.
    """
    repo = Path(repo_root).resolve()
    task_file = _resolve_task_json_path(repo, task_id, state_dir=state_dir, task_path=task_path)
    if not task_file.is_file():
        return False

    lock_dir = task_file.parent
    with state_lock(lock_dir):
        task_data = load_state_file(task_file, doc_type="task")
        intended = task_data.get("intended") or task_data.get("intended_commit")
        if not intended:
            return False

        target_branch = f"refs/heads/lintel/{task_id}"
        actual_tip = get_ref(repo, target_branch)
        if actual_tip == intended:
            task_data["head"] = intended
            task_data["recorded_head"] = intended
            task_data["intended"] = None
            task_data["intended_commit"] = None
            atomic_write_json(task_file, task_data)
            run_git(["read-tree", intended], repo, check=True)
            return True

    return False


# ---------------------------------------------------------------------------
# 6. Agent Commits & Snapshot Refs
# ---------------------------------------------------------------------------

def detect_agent_commits(
    repo_root: Union[str, Path],
    task_id: str,
    state_dir: Optional[Union[str, Path]] = None,
    task_path: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Detect agent commits on the task branch and save them under refs/lintel/<id>/agent-<n>.

    If actual branch tip is neither recorded_head nor intended_commit,
    saves it to refs/lintel/<id>/agent-<n> and records it in task.agent_commits.
    Returns list of newly recorded agent commit dictionaries.
    """
    repo = Path(repo_root).resolve()
    task_file = _resolve_task_json_path(repo, task_id, state_dir=state_dir, task_path=task_path)
    if not task_file.is_file():
        return []

    lock_dir = task_file.parent
    with state_lock(lock_dir):
        task_data = load_state_file(task_file, doc_type="task")
        recorded_head = task_data.get("head")
        intended = task_data.get("intended") or task_data.get("intended_commit")

        target_branch = f"refs/heads/lintel/{task_id}"
        actual_tip = get_ref(repo, target_branch)
        if not actual_tip:
            return []

        if actual_tip != recorded_head and actual_tip != intended:
            existing_commits = task_data.get("agent_commits", [])
            if any(c.get("sha") == actual_tip for c in existing_commits):
                return []

            counters = task_data.setdefault("counters", {})
            agent_n = counters.get("agent", 0) + 1
            counters["agent"] = agent_n

            ref_name = f"refs/lintel/{task_id}/agent-{agent_n}"
            update_ref(repo, ref_name, actual_tip)

            entry: Dict[str, Any] = {
                "ref": ref_name,
                "sha": actual_tip,
                "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            existing_commits.append(entry)
            task_data["agent_commits"] = existing_commits
            atomic_write_json(task_file, task_data)
            return [entry]

    return []


def create_snapshot_ref(
    repo_root: Union[str, Path],
    task_id: str,
    kind: str = "reset",
    parent: Optional[str] = None,
    state_dir: Optional[Union[str, Path]] = None,
    task_path: Optional[Union[str, Path]] = None,
    tmp_dir: Optional[Union[str, Path]] = None,
    message: Optional[str] = None,
) -> str:
    """Save current working tree state to refs/lintel/<id>/snapshot-<n>.

    Snapshots the working tree (excluding .lintel/tasks) into a tree object,
    creates a commit with author 'Lintel', and updates refs/lintel/<id>/snapshot-<n>.
    Returns the created ref name.
    """
    repo = Path(repo_root).resolve()

    # 1. Snapshot working tree into tree object via dedicated temporary index
    if tmp_dir:
        tmp_parent = Path(tmp_dir).resolve()
        tmp_parent.mkdir(parents=True, exist_ok=True)
    else:
        tmp_parent = Path(tempfile.gettempdir()).resolve()

    idx_filename = f"lintel_snap_{os.getpid()}_{time.time_ns()}.tmp"
    index_file = tmp_parent / idx_filename
    env = {"GIT_INDEX_FILE": str(index_file)}

    try:
        # Determine parent commit if not explicitly provided
        task_file = _resolve_task_json_path(repo, task_id, state_dir=state_dir, task_path=task_path)
        actual_parent = parent
        if not actual_parent and task_file.is_file():
            try:
                t_data = load_state_file(task_file, doc_type="task")
                actual_parent = t_data.get("head")
            except Exception:
                pass

        if not actual_parent:
            actual_parent = get_ref(repo, "HEAD")

        if actual_parent:
            run_git(["read-tree", actual_parent], repo, env=env, check=True)

        run_git(["add", "-A", "--", ".", ":!.lintel/tasks"], repo, env=env, check=True)
        proc_wt = run_git(["write-tree"], repo, env=env, check=True)
        tree_id = proc_wt.stdout.strip()
    finally:
        if index_file.exists():
            try:
                index_file.unlink()
            except OSError:
                pass

    # 2. Build snapshot commit
    commit_msg = message or f"snapshot({kind}): {task_id}"
    commit_sha = create_commit(
        repo_root=repo,
        tree_id=tree_id,
        parent=actual_parent,
        message=commit_msg,
        author_role="lintel",
    )

    # 3. Save under refs/lintel/<id>/snapshot-<n> and update task state
    if task_file.is_file():
        lock_dir = task_file.parent
        with state_lock(lock_dir):
            task_data = load_state_file(task_file, doc_type="task")
            counters = task_data.setdefault("counters", {})
            snap_n = counters.get("snapshot", 0) + 1
            counters["snapshot"] = snap_n

            ref_name = f"refs/lintel/{task_id}/snapshot-{snap_n}"
            update_ref(repo, ref_name, commit_sha)

            entry = {
                "ref": ref_name,
                "kind": kind,
                "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            task_data.setdefault("snapshots", []).append(entry)
            atomic_write_json(task_file, task_data)
            return ref_name
    else:
        # Fallback: inspect existing git refs
        refs = run_git(
            ["for-each-ref", "--format=%(refname)", f"refs/lintel/{task_id}/snapshot-*"],
            repo,
            check=False,
        ).stdout.splitlines()
        nums = [
            int(r.rsplit("-", 1)[-1])
            for r in refs
            if r.rsplit("-", 1)[-1].isdigit()
        ]
        snap_n = (max(nums) + 1) if nums else 1
        ref_name = f"refs/lintel/{task_id}/snapshot-{snap_n}"
        update_ref(repo, ref_name, commit_sha)
        return ref_name
