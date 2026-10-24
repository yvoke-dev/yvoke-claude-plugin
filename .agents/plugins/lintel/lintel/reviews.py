"""Reviews and approvals management (M1a-20).

Implements:
1. State document reviews.json schema (docs/state-machine.md §9.6):
   - version: 1
   - reviews: list of {id, pair, stage, phase, run, verdict, lane_hash, commit, kind, at, voided_at}
   - approvals: list of {id, step, kind, pair, stage, phase, review, lane_hash, commit, head, at, event_seq}
2. Review recording on reviewer done approve (or decide --accept).
3. Derived staleness check (D1, PLAN §3.6, is_review_valid).
4. Approval pager formatting and user confirmation.
5. approve requirements (T-HUM-08, 09, 10, 11, 12, 13).
6. approve plan [--phase N] (T-ARC-01, 02, 03, 04, 05, 06, 07, 10..20).
"""

import sys
sys.dont_write_bytecode = True

from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

try:
    from lintel import config as config_mod, documents, flow, git, loops, paths as paths_mod, state
    from lintel.events import append_event, create_event
    from lintel.state import atomic_write_json, load_state_file, state_lock, validate_state_doc
    from lintel.task_start import find_active_task_in_state
except ImportError:
    from . import config as config_mod, documents, flow, git, loops, paths as paths_mod, state
    from .events import append_event, create_event
    from .state import atomic_write_json, load_state_file, state_lock, validate_state_doc
    from .task_start import find_active_task_in_state


class ReviewRefusalError(flow.FlowRefusalError):
    """Raised when an approval command is refused per state machine rules."""

    def __init__(self, message: str, code: int = 1, row: Optional[str] = None):
        super().__init__(message=message, code=code, row=row)


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 1. reviews.json schema loading and saving
# ---------------------------------------------------------------------------

def load_reviews(state_dir: Union[str, Path], task_id: str) -> Dict[str, Any]:
    """Load reviews.json document for task, or return empty version 1 structure."""
    sdir = Path(state_dir).resolve()
    rev_file = sdir / task_id / "reviews.json"
    if rev_file.is_file():
        return load_state_file(rev_file, doc_type="reviews")
    return {
        "version": 1,
        "reviews": [],
        "approvals": [],
    }


def save_reviews(state_dir: Union[str, Path], task_id: str, data: Dict[str, Any]) -> None:
    """Save reviews.json document atomically with schema validation."""
    sdir = Path(state_dir).resolve()
    rev_file = sdir / task_id / "reviews.json"
    validate_state_doc("reviews", data, file_path=rev_file)
    atomic_write_json(rev_file, data)


# ---------------------------------------------------------------------------
# 2. Latest CLI commit excluding chore(lintel): config
# ---------------------------------------------------------------------------

def get_latest_cli_commit_excluding_config(
    repo_root: Union[str, Path],
    head_sha: Optional[str] = None,
) -> Optional[str]:
    """Find the latest CLI commit on the task branch excluding chore(lintel): config.

    Walks backward from head_sha using git log.
    """
    root = Path(repo_root).resolve()
    if not head_sha:
        return None
    res = git.run_git(["log", "--format=%H\t%s", head_sha], root, check=False)
    if res.returncode != 0 or not res.stdout.strip():
        return head_sha
    for line in res.stdout.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        parts = line_clean.split("\t", 1)
        sha = parts[0].strip()
        subj = parts[1].strip() if len(parts) > 1 else ""
        if subj.startswith("chore(lintel): config"):
            continue
        return sha
    return head_sha


# ---------------------------------------------------------------------------
# 3. Writer lane paths and lane hash calculation
# ---------------------------------------------------------------------------

def get_writer_lane_paths(
    repo_root: Union[str, Path],
    pair: str,
    stage: str,
    task_id: str,
    phase: Optional[int] = None,
    task_data: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Return sorted list of relative file paths in the writer's lane for role/pair."""
    root = Path(repo_root).resolve()
    p_num = phase
    if p_num is None:
        if stage.startswith("p") and stage[1:].isdigit():
            p_num = int(stage[1:])
        elif task_data and task_data.get("position", {}).get("phase") is not None:
            p_num = task_data["position"]["phase"]

    if pair == "po":
        if stage == "docs":
            paths_set: Set[str] = set()
            specs_dir = root / ".lintel" / "specs"
            if specs_dir.is_dir():
                for p in specs_dir.rglob("*"):
                    if p.is_file() and not p.name.startswith("."):
                        paths_set.add(p.relative_to(root).as_posix())
            head_sha = task_data.get("head") if task_data else None
            if head_sha:
                res = git.run_git(["ls-tree", "-r", "--name-only", head_sha, "--", ".lintel/specs"], root, check=False)
                if res.returncode == 0 and res.stdout.strip():
                    for line in res.stdout.splitlines():
                        line = line.strip()
                        if line and not Path(line).name.startswith("."):
                            paths_set.add(line)
            return sorted(paths_set) if paths_set else [".lintel/specs/"]
        return ["shared/requirements.md"]

    if pair == "docs":
        po_paths = get_writer_lane_paths(root, "po", "docs", task_id, phase=phase, task_data=task_data)
        arch_paths = get_writer_lane_paths(root, "architect", "docs", task_id, phase=phase, task_data=task_data)
        return sorted(set(po_paths + arch_paths))

    if pair == "architect":
        if stage == "docs":
            paths_set = set()
            steering_dir = root / ".lintel" / "steering"
            if steering_dir.is_dir():
                for p in steering_dir.rglob("*"):
                    if p.is_file() and not p.name.startswith(".") and p.name != "testing.md":
                        paths_set.add(p.relative_to(root).as_posix())
            head_sha = task_data.get("head") if task_data else None
            if head_sha:
                res = git.run_git(["ls-tree", "-r", "--name-only", head_sha, "--", ".lintel/steering"], root, check=False)
                if res.returncode == 0 and res.stdout.strip():
                    for line in res.stdout.splitlines():
                        line = line.strip()
                        if line and not Path(line).name.startswith(".") and Path(line).name != "testing.md":
                            paths_set.add(line)
            return sorted(paths_set) if paths_set else [".lintel/steering/"]

        is_locked = False
        if task_data and p_num is not None:
            is_locked = flow.is_phase_locked(task_data, p_num)

        if is_locked:
            # After lock: contract.md and design.md (test outline only)
            paths: List[str] = []
            for dname in (f"phase-{p_num}", f"p{p_num}"):
                paths.append(f"shared/{dname}/contract.md")
                paths.append(f"shared/{dname}/design.md")
            return sorted(set(paths))

        # Before lock: plan.md, phase-N/ files, and stub files
        paths = ["shared/plan.md"]
        if p_num is not None:
            found_phase_dir = False
            for dname in (f"phase-{p_num}", f"p{p_num}"):
                c_path = root / "shared" / dname / "contract.md"
                if not c_path.is_file():
                    c_path = root / ".lintel" / "tasks" / task_id / "shared" / dname / "contract.md"
                if c_path.is_file():
                    paths.append(f"shared/{dname}/contract.md")
                    paths.append(f"shared/{dname}/design.md")
                    found_phase_dir = True
                    # Extract stub files from contract.md
                    try:
                        content = c_path.read_text(encoding="utf-8")
                        m = re.search(
                            r"##\s+(?:\d+(?:\.\d+)*\.?\s+)?Stub\s+files\s*\n(.*?)(?=\n##|\Z)",
                            content,
                            re.DOTALL | re.IGNORECASE,
                        )
                        if m:
                            for sline in m.group(1).splitlines():
                                sline = sline.strip()
                                if sline.startswith("-"):
                                    sp = sline[1:].strip().strip("`'\" \t")
                                    if sp:
                                        paths.append(sp)
                    except Exception:
                        pass
                    break
            if not found_phase_dir:
                paths.append(f"shared/phase-{p_num}/contract.md")
                paths.append(f"shared/phase-{p_num}/design.md")
        return sorted(set(paths))

    if pair == "qa":
        cfg = None
        try:
            cfg_file = root / ".lintel" / "config.jsonc"
            if cfg_file.is_file():
                cfg = config_mod.load_config(cfg_file, workspace_root=root)
        except Exception:
            cfg = None

        tests_paths: Set[str] = set()

        res = git.run_git(["ls-files"], root, check=False)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                line = line.strip()
                if not line or line.startswith("."):
                    continue
                if cfg is not None:
                    cls = paths_mod.classify_path(line, config=cfg, repo_root=root, is_tracked=True)
                    if str(cls.base_class if hasattr(cls, "base_class") else cls) == "tests":
                        tests_paths.add(line)
                elif line.startswith("tests/"):
                    tests_paths.add(line)

        tests_dir = root / "tests"
        if tests_dir.is_dir():
            for p in tests_dir.rglob("*"):
                if p.is_file() and not p.name.startswith("."):
                    rel = p.relative_to(root).as_posix()
                    if cfg is not None:
                        cls = paths_mod.classify_path(rel, config=cfg, repo_root=root, is_tracked=False)
                        if str(cls.base_class if hasattr(cls, "base_class") else cls) == "tests":
                            tests_paths.add(rel)
                    else:
                        tests_paths.add(rel)

        is_locked_phase = False
        if task_data and p_num is not None:
            is_locked_phase = flow.is_phase_locked(task_data, p_num)

        if not is_locked_phase and cfg is not None:
            build_patterns = cfg.get("paths", {}).get("build", [])
            if build_patterns:
                res = git.run_git(["ls-files"], root, check=False)
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        line = line.strip()
                        if line and paths_mod.match_globs(line, build_patterns):
                            tests_paths.add(line)

        return sorted(tests_paths) if tests_paths else ["tests"]
    if pair == "dev":
        return ["*"]
    return []


def compute_writer_lane_hash(
    repo_root: Union[str, Path],
    pair: str,
    stage: str,
    task_id: str,
    phase: Optional[int] = None,
    task_data: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Compute sha256 lane hash over sorted tuples of (path, content_hash) for the writer's lane."""
    if pair == "final-reviewer":
        return None

    root = Path(repo_root).resolve()
    if pair == "dev":
        hasher = hashlib.sha256()
        res = git.run_git(["status", "--porcelain"], root, check=False)
        if res.returncode == 0:
            for line in sorted(res.stdout.splitlines()):
                line = line.strip()
                if not line:
                    continue
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    p = parts[1]
                    if not p.startswith(".lintel/") and not p.startswith("tests/") and not p.startswith("shared/"):
                        hasher.update(f"{p}:{parts[0]}\n".encode("utf-8"))
                        fp = root / p
                        if fp.is_file():
                            hasher.update(hashlib.sha256(fp.read_bytes()).hexdigest().encode("utf-8"))
        return hasher.hexdigest()

    lane_paths = get_writer_lane_paths(
        root, pair, stage, task_id, phase=phase, task_data=task_data
    )
    if not lane_paths:
        return None

    p_num = phase
    if p_num is None and task_data:
        p_num = task_data.get("position", {}).get("phase")

    is_locked = False
    if task_data and p_num is not None:
        is_locked = flow.is_phase_locked(task_data, p_num)

    hasher = hashlib.sha256()
    for rel_path in sorted(lane_paths):
        full_path = root / rel_path
        if not full_path.is_file() and rel_path.startswith("shared/"):
            alt_path = root / ".lintel" / "tasks" / task_id / rel_path
            if alt_path.is_file():
                full_path = alt_path

        if full_path.is_file():
            # If after lock and path is design.md: hash only ## Test outline section
            if is_locked and pair == "architect" and rel_path.endswith("design.md"):
                try:
                    text = full_path.read_text(encoding="utf-8")
                    m = re.search(
                        r"##\s+Test\s+outline\s*\n(.*?)(?=\n##|\Z)",
                        text,
                        re.DOTALL | re.IGNORECASE,
                    )
                    outline_text = m.group(1).strip() if m else ""
                    blob_hash = hashlib.sha256(outline_text.encode("utf-8")).hexdigest()
                    hasher.update(f"{rel_path}:{blob_hash}\n".encode("utf-8"))
                    continue
                except Exception:
                    pass

            content = full_path.read_bytes()
            blob_hash = hashlib.sha256(content).hexdigest()
            hasher.update(f"{rel_path}:{blob_hash}\n".encode("utf-8"))
        else:
            hasher.update(f"{rel_path}:deleted\n".encode("utf-8"))

    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# 4. Review recording and validity checking (D1)
# ---------------------------------------------------------------------------

def record_review(
    repo_root: Union[str, Path],
    task_id: str,
    pair: str,
    stage: str,
    phase: Optional[int] = None,
    run_id: Optional[str] = None,
    verdict: str = "approve",
    kind: str = "reviewer",
    task_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Record a review in reviews.json with the writer's current lane hash and latest CLI commit."""
    root = Path(repo_root).resolve()
    state_dir = root / ".lintel" / "state"

    if task_data is None:
        task_data = load_state_file(state_dir / task_id / "task.json", doc_type="task")

    lane_hash = None
    if pair != "final-reviewer":
        lane_hash = compute_writer_lane_hash(
            root, pair, stage, task_id, phase=phase, task_data=task_data
        )

    head_sha = task_data.get("head")
    latest_commit = get_latest_cli_commit_excluding_config(root, head_sha) or head_sha or ""

    with state_lock(state_dir):
        reviews_data = load_reviews(state_dir, task_id)
        reviews_list = reviews_data.setdefault("reviews", [])
        review_id = f"V{len(reviews_list) + 1}"

        review_entry: Dict[str, Any] = {
            "id": review_id,
            "pair": pair,
            "stage": stage,
            "phase": phase,
            "run": run_id,
            "verdict": verdict,
            "lane_hash": lane_hash,
            "commit": latest_commit,
            "kind": kind,
            "at": _now_iso(),
            "voided_at": None,
        }
        reviews_list.append(review_entry)
        save_reviews(state_dir, task_id, reviews_data)

    return review_entry


def get_valid_review(
    repo_root: Union[str, Path],
    task_id: str,
    pair: str,
    task_data: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Return the valid review entry for pair if valid per D1 and PLAN §3.6, else None."""
    root = Path(repo_root).resolve()
    state_dir = root / ".lintel" / "state"

    reviews_data = load_reviews(state_dir, task_id)
    reviews_list = reviews_data.get("reviews", [])

    # Find latest review for pair
    matching = [r for r in reviews_list if r.get("pair") == pair]
    if not matching:
        return None
    latest_review = matching[-1]

    # 1. Verdict check: approve or kind accept (or issues for final-reviewer)
    if pair == "final-reviewer":
        if latest_review.get("verdict") not in ("approve", "issues"):
            return None
    else:
        if latest_review.get("verdict") != "approve" and latest_review.get("kind") != "accept":
            return None

    # 2. voided_at check
    if latest_review.get("voided_at") is not None:
        return None

    if task_data is None:
        task_json = state_dir / task_id / "task.json"
        if not task_json.is_file():
            return None
        task_data = load_state_file(task_json, doc_type="task")

    # 3. Commit check: latest CLI commit excluding config
    current_commit = get_latest_cli_commit_excluding_config(root, task_data.get("head"))
    if current_commit != latest_review.get("commit"):
        return None

    # 4. Lane hash check
    if pair != "final-reviewer":
        rev_stage = latest_review.get("stage") or task_data.get("position", {}).get("stage", "r0")
        rev_phase = latest_review.get("phase")
        cur_lane_hash = compute_writer_lane_hash(
            root, pair, rev_stage, task_id, phase=rev_phase, task_data=task_data
        )
        if cur_lane_hash != latest_review.get("lane_hash"):
            return None

    return latest_review


def is_review_valid(
    repo_root: Union[str, Path],
    task_id: str,
    pair: str,
    task_data: Optional[Dict[str, Any]] = None,
) -> bool:
    """Check if a review for pair is currently valid per D1 and PLAN §3.6."""
    return get_valid_review(repo_root, task_id, pair, task_data=task_data) is not None


def void_reviews(
    state_dir: Union[str, Path],
    task_id: str,
    pair: str,
) -> int:
    """Void all active reviews for pair (e.g. when restore-tests alters pair lane)."""
    sdir = Path(state_dir).resolve()
    with state_lock(sdir):
        reviews_data = load_reviews(sdir, task_id)
        reviews_list = reviews_data.get("reviews", [])
        now_str = _now_iso()
        count = 0
        for r in reviews_list:
            if r.get("pair") == pair and r.get("voided_at") is None:
                r["voided_at"] = now_str
                count += 1
        if count > 0:
            save_reviews(sdir, task_id, reviews_data)
    return count


def record_approval(
    state_dir: Union[str, Path],
    task_id: str,
    step: str,
    kind: Optional[str],
    pair: str,
    stage: str,
    phase: Optional[int],
    review_id: str,
    lane_hash: Optional[str],
    commit_sha: Optional[str],
    head_sha: str,
    event_seq: int,
) -> Dict[str, Any]:
    """Record an approval in reviews.json."""
    sdir = Path(state_dir).resolve()
    with state_lock(sdir):
        reviews_data = load_reviews(sdir, task_id)
        approvals_list = reviews_data.setdefault("approvals", [])
        approval_id = f"A{len(approvals_list) + 1}"

        entry: Dict[str, Any] = {
            "id": approval_id,
            "step": step,
            "kind": kind,
            "pair": pair,
            "stage": stage,
            "phase": phase,
            "review": review_id,
            "lane_hash": lane_hash,
            "commit": commit_sha,
            "head": head_sha,
            "at": _now_iso(),
            "event_seq": event_seq,
        }
        approvals_list.append(entry)
        save_reviews(sdir, task_id, reviews_data)
    return entry


# ---------------------------------------------------------------------------
# 5. Git lane commit creation
# ---------------------------------------------------------------------------

def create_lane_commit(
    repo_root: Union[str, Path],
    task_id: str,
    author_role: str,
    message: str,
    lane_files: Sequence[str],
    event_seq: int,
    parent_sha: Optional[str] = None,
) -> str:
    """Create a commit modifying ONLY lane_files against parent_sha.

    Leaves any other dirty files in the worktree completely untouched.
    """
    root = Path(repo_root).resolve()
    head = parent_sha
    if not head:
        task_data = load_state_file(
            root / ".lintel" / "state" / task_id / "task.json", doc_type="task"
        )
        head = task_data["head"]

    with tempfile.NamedTemporaryFile(prefix="lintel-lane-index-", delete=False) as tf:
        temp_index_path = tf.name

    try:
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = temp_index_path

        # 1. Populate temp index with recorded head tree
        git.run_git(["read-tree", head], root, env=env, check=True)

        # 2. Stage only files in lane_files
        for rel_path in lane_files:
            if rel_path.endswith("/") or (root / rel_path).is_dir():
                rel_clean = rel_path.rstrip("/")
                full_dir = root / rel_clean
                if full_dir.is_dir():
                    git.run_git(["add", "-A", "--", rel_clean], root, env=env, check=True)
                else:
                    git.run_git(["rm", "-r", "--cached", "--ignore-unmatch", "--", rel_clean], root, env=env, check=True)
                continue

            full_path = root / rel_path
            # Check fallback in .lintel/tasks/<task_id>/shared/
            if not full_path.is_file() and rel_path.startswith("shared/"):
                alt_path = root / ".lintel" / "tasks" / task_id / rel_path
                if alt_path.is_file():
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_bytes(alt_path.read_bytes())

            if full_path.is_file():
                git.run_git(["add", "--", rel_path], root, env=env, check=True)
            else:
                git.run_git(
                    ["rm", "--cached", "--ignore-unmatch", "--", rel_path],
                    root,
                    env=env,
                    check=True,
                )

        # 3. Write tree from temp index
        write_tree_proc = git.run_git(["write-tree"], root, env=env, check=True)
        new_tree = write_tree_proc.stdout.strip()

        # 4. Create commit with fixed author and trailer
        commit_sha = git.create_commit(
            repo_root=root,
            tree_id=new_tree,
            parent=head,
            message=message,
            author_role=author_role,
            task_id=task_id,
            event_seq=event_seq,
        )

        # 5. Advance branch using compare-and-swap update-ref
        git.advance_branch(
            repo_root=root,
            task_id=task_id,
            new_commit=commit_sha,
        )

        return commit_sha

    finally:
        if os.path.exists(temp_index_path):
            try:
                os.unlink(temp_index_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 6. Approval pager & confirmation
# ---------------------------------------------------------------------------

def format_approval_pager(
    repo_root: Path,
    task_id: str,
    step: str,
    pair: str,
    phase: Optional[int] = None,
    task_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Format review details for the approval pager per PLAN §3.5 and §3.6."""
    lines: List[str] = [f"=== Approval: {step.capitalize()}" + (f" (Phase {phase})" if phase else "") + " ==="]

    if task_data is None:
        try:
            task_data = load_state_file(repo_root / ".lintel" / "state" / task_id / "task.json", doc_type="task")
        except Exception:
            task_data = {}

    head = task_data.get("head") if task_data else None

    if step == "requirements":
        req_file = repo_root / "shared" / "requirements.md"
        if not req_file.is_file():
            req_file = repo_root / ".lintel" / "tasks" / task_id / "shared" / "requirements.md"
        if req_file.is_file():
            lines.append("\n--- Document: shared/requirements.md ---")
            lines.append(req_file.read_text(encoding="utf-8").strip())
        else:
            lines.append("\n(shared/requirements.md missing)")

    elif step == "plan":
        plan_file = repo_root / "shared" / "plan.md"
        if not plan_file.is_file():
            plan_file = repo_root / ".lintel" / "tasks" / task_id / "shared" / "plan.md"
        cur_plan_text = plan_file.read_text(encoding="utf-8") if plan_file.is_file() else ""

        head_plan_text = ""
        if head:
            head_plan_proc = git.run_git(["show", f"{head}:shared/plan.md"], repo_root, check=False)
            if head_plan_proc.returncode == 0:
                head_plan_text = head_plan_proc.stdout

        plan_diff = list(
            difflib.unified_diff(
                head_plan_text.splitlines(),
                cur_plan_text.splitlines(),
                fromfile="a/shared/plan.md",
                tofile="b/shared/plan.md",
                lineterm="",
            )
        )
        lines.append("\n--- Diff: shared/plan.md ---")
        if plan_diff:
            lines.extend(plan_diff)
        else:
            lines.append("(No changes in shared/plan.md)")

        if phase is not None:
            # Show outline with none reasons from design.md
            for dname in (f"phase-{phase}", f"p{phase}"):
                des_file = repo_root / "shared" / dname / "design.md"
                if not des_file.is_file():
                    des_file = repo_root / ".lintel" / "tasks" / task_id / "shared" / dname / "design.md"
                if des_file.is_file():
                    text = des_file.read_text(encoding="utf-8")
                    m = re.search(
                        r"##\s+(?:\d+(?:\.\d+)*\.?\s+)?Test\s+outline\s*\n(.*?)(?=\n##|\Z)",
                        text,
                        re.DOTALL | re.IGNORECASE,
                    )
                    if m:
                        lines.append(f"\n--- Test Outline ({dname}/design.md) ---")
                        lines.append(m.group(1).strip())
                    break

            # Show stub files from contract.md with unified diff against recorded head
            stub_paths: List[str] = []
            matched_dname = f"phase-{phase}"
            for dname in (f"phase-{phase}", f"p{phase}"):
                con_file = repo_root / "shared" / dname / "contract.md"
                if not con_file.is_file():
                    con_file = repo_root / ".lintel" / "tasks" / task_id / "shared" / dname / "contract.md"
                if con_file.is_file():
                    matched_dname = dname
                    text = con_file.read_text(encoding="utf-8")
                    m = re.search(
                        r"##\s+(?:\d+(?:\.\d+)*\.?\s+)?Stub\s+files\s*\n(.*?)(?=\n##|\Z)",
                        text,
                        re.DOTALL | re.IGNORECASE,
                    )
                    if m:
                        for sline in m.group(1).splitlines():
                            sline = sline.strip()
                            if sline.startswith("-"):
                                sp = sline[1:].strip().strip("`'\" \t")
                                if sp:
                                    stub_paths.append(sp)
                    break

            lines.append(f"\n--- Stub Files ({matched_dname}/contract.md) ---")
            if not stub_paths:
                lines.append("(No stub files listed in contract.md)")
            else:
                for sp in stub_paths:
                    head_stub_text = ""
                    if head:
                        res = git.run_git(["show", f"{head}:{sp}"], repo_root, check=False)
                        if res.returncode == 0:
                            head_stub_text = res.stdout
                    cur_file = repo_root / sp
                    cur_stub_text = cur_file.read_text(encoding="utf-8") if cur_file.is_file() else ""
                    stub_diff = list(
                        difflib.unified_diff(
                            head_stub_text.splitlines(),
                            cur_stub_text.splitlines(),
                            fromfile=f"a/{sp}",
                            tofile=f"b/{sp}",
                            lineterm="",
                        )
                    )
                    lines.append(f"\n--- Diff: {sp} ---")
                    if stub_diff:
                        lines.extend(stub_diff)
                    else:
                        lines.append(f"(No changes in {sp})")

    elif step == "final":
        lines.append("\n--- Final Review Summary ---")
        rev_doc = load_reviews(repo_root / ".lintel" / "state", task_id)
        matching = [r for r in rev_doc.get("reviews", []) if r.get("pair") == "final-reviewer"]
        latest_rev = matching[-1] if matching else None
        run_folder = None
        if latest_rev and latest_rev.get("run"):
            run_file = repo_root / ".lintel" / "state" / task_id / "runs" / f"{latest_rev['run']}.json"
            if run_file.is_file():
                try:
                    rd = json.loads(run_file.read_text(encoding="utf-8"))
                    if rd.get("folder"):
                        run_folder = repo_root / rd["folder"]
                except Exception:
                    pass

        review_shown = False
        if run_folder:
            rev_file = run_folder / "review.md"
            if rev_file.is_file():
                lines.append(rev_file.read_text(encoding="utf-8").strip())
                review_shown = True
        if not review_shown:
            lines.append("(No review.md in run folder)")

        lines.append("\n--- Final Findings (final-findings.md) ---")
        findings_shown = False
        if run_folder:
            ff_file = run_folder / "final-findings.md"
            if ff_file.is_file():
                lines.append(ff_file.read_text(encoding="utf-8").strip())
                findings_shown = True
        if not findings_shown:
            shared_ff = repo_root / "shared" / "final-findings.md"
            if not shared_ff.is_file():
                shared_ff = repo_root / ".lintel" / "tasks" / task_id / "shared" / "final-findings.md"
            if shared_ff.is_file():
                lines.append(shared_ff.read_text(encoding="utf-8").strip())
                findings_shown = True
        if not findings_shown:
            lines.append("(No final findings recorded)")

    elif step == "docs":
        # Diffs of .lintel/specs/
        lines.append("\n--- Diffs: .lintel/specs/ ---")
        specs_paths: Set[str] = set()
        specs_dir = repo_root / ".lintel" / "specs"
        if specs_dir.is_dir():
            for p in specs_dir.rglob("*"):
                if p.is_file() and not p.name.startswith("."):
                    specs_paths.add(p.relative_to(repo_root).as_posix())
        if head:
            res = git.run_git(["ls-tree", "-r", "--name-only", head, "--", ".lintel/specs"], repo_root, check=False)
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.splitlines():
                    line = line.strip()
                    if line and not Path(line).name.startswith("."):
                        specs_paths.add(line)

        specs_diff_found = False
        for sp in sorted(specs_paths):
            head_text = ""
            if head:
                res = git.run_git(["show", f"{head}:{sp}"], repo_root, check=False)
                if res.returncode == 0:
                    head_text = res.stdout
            cur_file = repo_root / sp
            cur_text = cur_file.read_text(encoding="utf-8") if cur_file.is_file() else ""
            if head_text != cur_text:
                specs_diff_found = True
                diff = list(
                    difflib.unified_diff(
                        head_text.splitlines(),
                        cur_text.splitlines(),
                        fromfile=f"a/{sp}",
                        tofile=f"b/{sp}",
                        lineterm="",
                    )
                )
                lines.append(f"\n--- Diff: {sp} ---")
                lines.extend(diff)
        if not specs_diff_found:
            lines.append("(No changes in .lintel/specs/)")

        # Diffs of .lintel/steering/
        lines.append("\n--- Diffs: .lintel/steering/ ---")
        steering_paths: Set[str] = set()
        steering_dir = repo_root / ".lintel" / "steering"
        if steering_dir.is_dir():
            for p in steering_dir.rglob("*"):
                if p.is_file() and not p.name.startswith(".") and p.name != "testing.md":
                    steering_paths.add(p.relative_to(repo_root).as_posix())
        if head:
            res = git.run_git(["ls-tree", "-r", "--name-only", head, "--", ".lintel/steering"], repo_root, check=False)
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.splitlines():
                    line = line.strip()
                    if line and not Path(line).name.startswith(".") and Path(line).name != "testing.md":
                        steering_paths.add(line)

        steering_diff_found = False
        for sp in sorted(steering_paths):
            head_text = ""
            if head:
                res = git.run_git(["show", f"{head}:{sp}"], repo_root, check=False)
                if res.returncode == 0:
                    head_text = res.stdout
            cur_file = repo_root / sp
            cur_text = cur_file.read_text(encoding="utf-8") if cur_file.is_file() else ""
            if head_text != cur_text:
                steering_diff_found = True
                diff = list(
                    difflib.unified_diff(
                        head_text.splitlines(),
                        cur_text.splitlines(),
                        fromfile=f"a/{sp}",
                        tofile=f"b/{sp}",
                        lineterm="",
                    )
                )
                lines.append(f"\n--- Diff: {sp} ---")
                lines.extend(diff)
        if not steering_diff_found:
            lines.append("(No changes in .lintel/steering/)")

    # Copied report sections for the pair since its last approval (§3.2, §3.5)
    copied_sections: List[Tuple[str, str]] = []
    last_approval_at = None
    try:
        rev_doc = load_reviews(repo_root / ".lintel" / "state", task_id)
        if pair == "docs":
            approvals = [a for a in rev_doc.get("approvals", []) if a.get("step") == "docs"]
        else:
            approvals = [a for a in rev_doc.get("approvals", []) if a.get("pair") == pair]
        if approvals:
            last_approval_at = approvals[-1].get("at")
    except Exception:
        pass

    runs_dir = repo_root / ".lintel" / "state" / task_id / "runs"
    if runs_dir.is_dir():
        for rf in sorted(runs_dir.glob("*.json")):
            try:
                rdata = json.loads(rf.read_text(encoding="utf-8"))
            except Exception:
                continue
            r_role = rdata.get("role", "")
            if pair == "docs":
                if r_role not in ("po", "po-reviewer", "architect", "architect-reviewer"):
                    continue
            elif r_role != pair and r_role != f"{pair}-reviewer":
                continue
            r_created = rdata.get("created_at")
            if last_approval_at and r_created and r_created <= last_approval_at:
                continue

            r_folder = rdata.get("folder")
            if r_folder:
                brief_path = repo_root / r_folder / "brief.md"
                if brief_path.is_file():
                    b_text = brief_path.read_text(encoding="utf-8")
                    m = re.search(r"##\s+Copied Report Section\s*\n(.*?)(?=\n##|\Z)", b_text, re.DOTALL | re.IGNORECASE)
                    if m:
                        sec_text = m.group(1).strip()
                        if sec_text:
                            run_id = rdata.get("id", rf.stem)
                            copied_sections.append((run_id, sec_text))

    if copied_sections:
        lines.append("\n--- Copied Report Sections ---")
        for r_id, sec_content in copied_sections:
            lines.append(f"### From run {r_id}:")
            lines.append(sec_content)

    # Pair findings from loop (PLAN §3.5, §3.9, T-HUM-16)
    try:
        loops_doc = loops.load_loops(repo_root / ".lintel" / "state", task_id)
        pos = flow.get_position(task_data)
        stage = pos.stage
        pairs_to_check = ["po", "architect"] if pair == "docs" else [pair]
        loop_findings = []
        for p in pairs_to_check:
            l_key = f"{p}.{stage}"
            loop_entry = loops_doc.get("loops", {}).get(l_key, {})
            for f in loop_entry.get("findings", []):
                loop_findings.append((p, f))

        if loop_findings:
            lines.append("\n--- Findings ---")
            for p, f in loop_findings:
                fid = f.get("id", "")
                author = f.get("author", "reviewer")
                sev = f.get("severity", "")
                st = f.get("status", "")
                summ = f.get("summary", "")
                author_tag = f" [{author}]" if author == "human" else ""
                lines.append(f"- [{st.upper()}] {fid}{author_tag} ({sev}): {summ}")
    except Exception:
        pass

    lines.append("\n--- Audit Findings ---")
    try:
        from lintel.audit import get_ackable_findings
        acks = get_ackable_findings(repo_root, pair)
        if acks:
            for f in acks:
                lines.append(f"- [{f.kind.upper()}] {f.id} ({f.category}): {f.message}")
        else:
            lines.append("(No unacknowledged audit findings)")
    except Exception:
        lines.append("(No unacknowledged audit findings)")
    return "\n".join(lines)


def confirm_approval(prompt_text: str = "Approve this step? [y/N]: ") -> bool:
    """Prompt user for confirmation (T-HUM-08).

    In non-interactive testing or when LINTEL_AUTO_APPROVE=1, returns True.
    """
    if os.environ.get("LINTEL_AUTO_APPROVE") == "1":
        return True
    if not sys.stdin.isatty():
        return True
    try:
        ans = input(prompt_text).strip().lower()
        return ans in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def update_progress_md(repo_root: Path, task_data: Dict[str, Any], next_action: str) -> None:
    """Update progress.md in the task directory."""
    try:
        from lintel.progress import update_progress_md as _impl
    except ImportError:
        from .progress import update_progress_md as _impl
    _impl(repo_root, task_data, next_action)


# ---------------------------------------------------------------------------
# 7. approve requirements
# ---------------------------------------------------------------------------

def approve_requirements(
    repo_root: Union[str, Path],
    reject: Optional[str] = None,
    confirm: Optional[bool] = None,
    pair: Optional[str] = None,
) -> str:
    """Execute 'lintel approve requirements' (T-HUM-08, 09, 10, 11, 12, 13, 15, 17)."""
    root = Path(repo_root).resolve()
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise ReviewRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active_info
    state_dir = root / ".lintel" / "state"
    task_state_file = state_dir / task_id / "task.json"

    with state_lock(state_dir):
        task_data = load_state_file(task_state_file, doc_type="task")
        pos = flow.get_position(task_data)
        stage = pos.stage

        # NR check: no run pending or active (T-HUM-05)
        if pos.run is not None:
            run_file = state_dir / task_id / "runs" / f"{pos.run}.json"
            if run_file.is_file():
                try:
                    rd = json.loads(run_file.read_text(encoding="utf-8"))
                    if rd.get("state") in ("pending", "active"):
                        raise ReviewRefusalError(
                            "Refused: a run is pending or active; lintel task wait (T-HUM-05)",
                            row="T-HUM-05",
                        )
                except ReviewRefusalError:
                    raise
                except Exception:
                    pass

        # Check expected state (T-HUM-14)
        if pos.code != "H.req":
            exp = flow.format_next_action(task_data, repo_root=root)
            raise ReviewRefusalError(
                f"Refused: approve requirements not expected in state '{pos.code}' (T-HUM-14);\n"
                f"expected action: {exp}",
                row="T-HUM-14",
            )

        # T-HUM-17: --pair must be po outside docs update
        if pair and pair != "po":
            raise ReviewRefusalError(
                "Refused: --pair must be po outside the docs update (T-HUM-17)",
                row="T-HUM-17",
            )

        # VR(po) check: valid PO review (T-HUM-09)
        valid_rev = get_valid_review(root, task_id, "po", task_data=task_data)
        if not valid_rev:
            next_act = f"lintel invoke po-reviewer --phase {stage}"
            raise ReviewRefusalError(
                f"Refused: no valid PO review (T-HUM-09);\nnext action: {next_act}",
                row="T-HUM-09",
            )

        if reject is not None:
            # T-HUM-15: rejection transition
            why = reject.strip() or "Requirements rejected"

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
                folder_rel = f".lintel/tasks/{task_id}/po/{stage}-reject"

            run_folder = root / folder_rel
            run_folder.mkdir(parents=True, exist_ok=True)
            rejection_file = run_folder / "rejection.md"
            rejection_file.write_text(why + "\n", encoding="utf-8")
            rejection_rel_path = f"{folder_rel}/rejection.md"

            loops.record_human_reject(
                state_dir=state_dir,
                task_id=task_id,
                writer="po",
                stage=stage,
                reason=why,
                file_path=rejection_rel_path,
                where="shared/requirements.md",
            )

            task_data, next_action, row_id = flow.on_approve_requirements(task_data, reject_reason=why)
            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

            event_rec = create_event(
                step=event_seq,
                event="reject",
                stage=stage,
                role="po",
                row=row_id,
            )
            append_event(
                state_dir=state_dir,
                task_id=task_id,
                event_data=event_rec,
                task_dir=root / ".lintel" / "tasks" / task_id,
            )
            update_progress_md(root, task_data, next_action)
            return next_action

        # Requirements document check (T-HUM-13)
        req_file = root / "shared" / "requirements.md"
        if not req_file.is_file():
            req_file = root / ".lintel" / "tasks" / task_id / "shared" / "requirements.md"
        if not req_file.is_file():
            raise ReviewRefusalError(
                f"Refused: shared/requirements.md missing (T-HUM-13);\n"
                f"run 'lintel invoke po --phase {stage}'",
                row="T-HUM-13",
            )

        req_content = req_file.read_text(encoding="utf-8")
        try:
            known_ids = set(task_data.get("known_requirement_ids", []))
            req_doc = documents.validate_requirements_doc(
                req_content, known_ids=known_ids if known_ids else None
            )
        except documents.DocumentValidationError as exc:
            raise ReviewRefusalError(
                f"Refused: requirements.md validation failed (T-HUM-13): {exc};\n"
                f"run 'lintel invoke po --phase {stage}'",
                row="T-HUM-13",
            )

        # Pager and confirmation (T-HUM-08)
        pager_text = format_approval_pager(
            root, task_id, step="requirements", pair="po", task_data=task_data
        )

        sys.stdout.write(pager_text + "\n")
        do_confirm = confirm if confirm is not None else confirm_approval()
        if not do_confirm:
            act = flow.format_next_action(task_data, repo_root=root)
            sys.stdout.write(f"Approval canceled (T-HUM-08).\n{act}\n")
            return act
        from lintel.audit import accept_findings_for_command
        accept_findings_for_command(repo_root, "po", "requirements")

        # Ensure root has shared/requirements.md and task folder has it
        target_shared_req = root / "shared" / "requirements.md"
        target_shared_req.parent.mkdir(parents=True, exist_ok=True)
        if not target_shared_req.is_file() or target_shared_req.read_text(encoding="utf-8") != req_content:
            target_shared_req.write_text(req_content, encoding="utf-8")

        task_shared_req = root / ".lintel" / "tasks" / task_id / "shared" / "requirements.md"
        task_shared_req.parent.mkdir(parents=True, exist_ok=True)
        if not task_shared_req.is_file() or task_shared_req.read_text(encoding="utf-8") != req_content:
            task_shared_req.write_text(req_content, encoding="utf-8")

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq

        # Commit PO lane ONLY (shared/requirements.md)
        commit_sha = create_lane_commit(
            repo_root=root,
            task_id=task_id,
            author_role="po",
            message="docs(lintel): requirements",
            lane_files=["shared/requirements.md"],
            event_seq=event_seq,
            parent_sha=task_data["head"],
        )
        task_data["head"] = commit_sha

        # Update known requirement IDs
        known_set = set(task_data.get("known_requirement_ids", []))
        known_set.update(req_doc.ids)
        sorted_known = sorted(
            known_set,
            key=lambda r: int(r[1:]) if r.startswith("R") and r[1:].isdigit() else 9999,
        )
        task_data["known_requirement_ids"] = sorted_known

        # Record approval in reviews.json
        record_approval(
            state_dir=state_dir,
            task_id=task_id,
            step="requirements",
            kind=None,
            pair="po",
            stage=stage,
            phase=None,
            review_id=valid_rev["id"],
            lane_hash=valid_rev.get("lane_hash"),


            commit_sha=commit_sha,
            head_sha=commit_sha,
            event_seq=event_seq,
        )

        # Transition state via flow
        task_data, next_action, row_id = flow.on_approve_requirements(task_data)

        # Save task.json
        validate_state_doc("task", task_data, file_path=task_state_file)
        atomic_write_json(task_state_file, task_data)

        # Append approval event
        event_rec = create_event(
            step=event_seq,
            event="approve",
            stage=stage,
            role="po",
            row=row_id,
        )
        append_event(
            state_dir=state_dir,
            task_id=task_id,
            event_data=event_rec,
            task_dir=root / ".lintel" / "tasks" / task_id,
        )

        update_progress_md(root, task_data, next_action)

    return next_action


# ---------------------------------------------------------------------------
# 8. approve plan [--phase N]
# ---------------------------------------------------------------------------

def _extract_non_test_outline(text: str) -> str:
    """Extract design.md text excluding the ## Test outline section."""
    lines = text.splitlines()
    res: List[str] = []
    in_outline = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            sec_name = stripped[3:].strip()
            if re.match(r"^(?:\d+(?:\.\d+)*\.?\s+)?Test\s+outline$", sec_name, re.IGNORECASE):
                in_outline = True
            else:
                in_outline = False
                res.append(line)
        elif not in_outline:
            res.append(line)
    return "\n".join(res).strip()


def approve_plan(
    repo_root: Union[str, Path],
    phase: Optional[Union[str, int]] = None,
    reject: Optional[str] = None,
    confirm: Optional[bool] = None,
    pair: Optional[str] = None,
) -> str:
    """Execute 'lintel approve plan [--phase N]' (T-ARC-01..08, 10..20, T-HUM-17)."""
    root = Path(repo_root).resolve()
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise ReviewRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active_info
    state_dir = root / ".lintel" / "state"
    task_state_file = state_dir / task_id / "task.json"

    with state_lock(state_dir):
        task_data = load_state_file(task_state_file, doc_type="task")
        pos = flow.get_position(task_data)
        stage = pos.stage

        # NR check (T-HUM-05)
        if pos.run is not None:
            run_file = state_dir / task_id / "runs" / f"{pos.run}.json"
            if run_file.is_file():
                try:
                    rd = json.loads(run_file.read_text(encoding="utf-8"))
                    if rd.get("state") in ("pending", "active"):
                        raise ReviewRefusalError(
                            "Refused: a run is pending or active; lintel task wait (T-HUM-05)",
                            row="T-HUM-05",
                        )
                except ReviewRefusalError:
                    raise
                except Exception:
                    pass

        # Expected state check (T-HUM-14)
        if pos.code != "H.plan":
            exp = flow.format_next_action(task_data, repo_root=root)
            raise ReviewRefusalError(
                f"Refused: approve plan not expected in state '{pos.code}' (T-HUM-14);\n"
                f"expected action: {exp}",
                row="T-HUM-14",
            )

        # T-HUM-17: --pair must be architect outside docs update
        if pair and pair != "architect":
            raise ReviewRefusalError(
                "Refused: --pair must be architect outside the docs update (T-HUM-17)",
                row="T-HUM-17",
            )

        # VR(architect) check: valid architect review (T-ARC-05)
        valid_rev = get_valid_review(root, task_id, "architect", task_data=task_data)
        if not valid_rev:
            next_act = f"lintel invoke architect-reviewer --phase {stage}"
            raise ReviewRefusalError(
                f"Refused: no valid architect review (T-ARC-05);\nnext action: {next_act}",
                row="T-ARC-05",
            )

        if reject is not None:
            # T-ARC-08: rejection transition
            why = reject.strip() or "Plan rejected"

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
                folder_rel = f".lintel/tasks/{task_id}/architect/{stage}-reject"

            run_folder = root / folder_rel
            run_folder.mkdir(parents=True, exist_ok=True)
            rejection_file = run_folder / "rejection.md"
            rejection_file.write_text(why + "\n", encoding="utf-8")
            rejection_rel_path = f"{folder_rel}/rejection.md"

            loops.record_human_reject(
                state_dir=state_dir,
                task_id=task_id,
                writer="architect",
                stage=stage,
                reason=why,
                file_path=rejection_rel_path,
                where="shared/plan.md",
            )

            target_phase_num = None
            if phase is not None:
                norm_p = flow.normalize_phase_arg(phase)
                try:
                    target_phase_num = int(norm_p[1:]) if norm_p.startswith("p") else int(norm_p)
                except ValueError:
                    target_phase_num = None
            task_data, next_action, row_id = flow.on_approve_plan(
                task_data, phase=target_phase_num, reject_reason=why
            )
            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

            event_rec = create_event(
                step=event_seq,
                event="reject",
                stage=stage,
                role="architect",
                row=row_id,
            )
            append_event(
                state_dir=state_dir,
                task_id=task_id,
                event_data=event_rec,
                task_dir=root / ".lintel" / "tasks" / task_id,
            )
            update_progress_md(root, task_data, next_action)
            return next_action

        # Load plan.md
        plan_file = root / "shared" / "plan.md"
        if not plan_file.is_file():
            plan_file = root / ".lintel" / "tasks" / task_id / "shared" / "plan.md"
        if not plan_file.is_file():
            raise ReviewRefusalError(
                f"Refused: shared/plan.md missing;\nrun 'lintel invoke architect --phase {stage}'",
                row="T-ARC-10",
            )
        plan_content = plan_file.read_text(encoding="utf-8")
        try:
            plan_doc = documents.parse_plan_doc(plan_content)
        except documents.DocumentValidationError as exc:
            raise ReviewRefusalError(
                f"Refused: plan.md format invalid (T-ARC-10): {exc}",
                row="T-ARC-10",
            )

        delivered_phases_list = task_data.get("delivered_phases", [])
        delivered_phases: Set[int] = set(delivered_phases_list)
        undelivered_phases = [
            p_num for p_num in sorted(plan_doc.phases.keys()) if p_num not in delivered_phases
        ]

        target_phase: Optional[int] = None
        if undelivered_phases:
            first_undelivered = undelivered_phases[0]
            if phase is not None:
                norm_p = flow.normalize_phase_arg(phase)
                try:
                    num_val = int(norm_p[1:]) if norm_p.startswith("p") else int(norm_p)
                except ValueError:
                    num_val = -1
                if num_val != first_undelivered:
                    raise ReviewRefusalError(
                        f"Refused: phase '{phase}' is not the first undelivered phase ({first_undelivered}) in plan.md (T-ARC-04)",
                        row="T-ARC-04",
                    )
                target_phase = first_undelivered
            else:
                target_phase = first_undelivered
        else:
            # No undelivered phase remaining
            if phase is not None:
                raise ReviewRefusalError(
                    "Refused: approve plan takes no --phase when no undelivered phase remains (T-ARC-04)",
                    row="T-ARC-04",
                )
            target_phase = None

        # Load requirements.md for document checks
        req_file = root / "shared" / "requirements.md"
        if not req_file.is_file():
            req_file = root / ".lintel" / "tasks" / task_id / "shared" / "requirements.md"
        if not req_file.is_file():
            raise ReviewRefusalError(
                "Refused: shared/requirements.md missing for approve plan document checks (T-ARC-10)",
                row="T-ARC-10",
            )
        req_content = req_file.read_text(encoding="utf-8")
        try:
            req_doc = documents.validate_requirements_doc(req_content)
        except documents.DocumentValidationError as exc:
            raise ReviewRefusalError(
                f"Refused: requirements.md invalid (T-ARC-10): {exc}",
                row="T-ARC-10",
            )

        # Plan doc validation against requirements (§3.13, T-ARC-10..13)
        try:
            documents.validate_plan_doc(
                plan_content,
                requirements_doc=req_doc,
                state_data=task_data,
            )
        except documents.DocumentValidationError as exc:
            err_str = str(exc)
            if "is not in any phase in plan.md" in err_str:
                raise ReviewRefusalError(f"Refused: requirement is in no phase (T-ARC-10): {exc}", row="T-ARC-10")
            if "does not exist in requirements.md" in err_str:
                raise ReviewRefusalError(f"Refused: requirement does not exist (T-ARC-11): {exc}", row="T-ARC-11")
            if "whose text changed" in err_str:
                raise ReviewRefusalError(f"Refused: changed requirement not in undelivered phase (T-ARC-12): {exc}", row="T-ARC-12")
            if "withdrawn" in err_str.lower() and "undelivered phase" in err_str.lower():
                raise ReviewRefusalError(f"Refused: withdrawn delivered requirement not in undelivered phase (T-ARC-13): {exc}", row="T-ARC-13")
            raise ReviewRefusalError(f"Refused: plan.md validation failed: {exc}", row="T-ARC-10")

        # Delivered phase sections unchanged check (T-ARC-07)
        if delivered_phases:
            head_commit = task_data.get("head")
            if head_commit:
                head_plan_proc = git.run_git(["show", f"{head_commit}:shared/plan.md"], root, check=False)
                if head_plan_proc.returncode == 0:
                    try:
                        head_plan_doc = documents.parse_plan_doc(head_plan_proc.stdout)
                        for dp in delivered_phases:
                            if dp in head_plan_doc.phases and dp in plan_doc.phases:
                                if head_plan_doc.phases[dp].raw_section.strip() != plan_doc.phases[dp].raw_section.strip():
                                    raise ReviewRefusalError(
                                        f"Refused: delivered phase {dp} section changed in plan.md; "
                                        "changes to delivered behaviour become new phases (T-ARC-07)",
                                        row="T-ARC-07",
                                    )
                    except ReviewRefusalError:
                        raise
                    except Exception:
                        pass

        # Phase design & contract checks if target_phase is present
        contract_doc = None
        if target_phase is not None:
            # 1. design.md & test outline (T-ARC-14..19)
            des_file = None
            for dname in (f"phase-{target_phase}", f"p{target_phase}"):
                cand = root / "shared" / dname / "design.md"
                if not cand.is_file():
                    cand = root / ".lintel" / "tasks" / task_id / "shared" / dname / "design.md"
                if cand.is_file():
                    des_file = cand
                    break
            if not des_file or not des_file.is_file():
                raise ReviewRefusalError(
                    f"Refused: design.md missing for phase {target_phase} (T-ARC-14)",
                    row="T-ARC-14",
                )
            des_content = des_file.read_text(encoding="utf-8")

            # Load config to get test groups
            cfg_file = root / ".lintel" / "config.jsonc"
            cfg = config_mod.load_config(cfg_file if cfg_file.is_file() else {}, workspace_root=root)
            test_groups = [g for g in cfg.get("paths", {}).get("tests", {}).keys() if g != "support"]
            phase_reqs = set(plan_doc.phases[target_phase].requirements)

            try:
                documents.validate_test_outline(des_content, phase_reqs, test_groups)
            except documents.DocumentValidationError as exc:
                err_str = str(exc)
                if "missing from test outline" in err_str or "has no line in test outline" in err_str or "not in this phase's requirements line" in err_str.lower():
                    raise ReviewRefusalError(f"Refused: requirement has no line in test outline (T-ARC-14): {exc}", row="T-ARC-14")
                if "has neither" in err_str:
                    raise ReviewRefusalError(f"Refused: required test group missing from outline (T-ARC-15): {exc}", row="T-ARC-15")
                if "cannot have both test lines and a 'none' line" in err_str or "has both an outline line and a none" in err_str:
                    raise ReviewRefusalError(f"Refused: group has both outline line and none (T-ARC-16): {exc}", row="T-ARC-16")
                if "unknown" in err_str.lower() or "support" in err_str.lower():
                    raise ReviewRefusalError(f"Refused: outline line names unknown group or support (T-ARC-17): {exc}", row="T-ARC-17")
                if "requires a non-empty reason" in err_str.lower() or "none has no reason" in err_str.lower() or "missing reason" in err_str.lower():
                    raise ReviewRefusalError(f"Refused: none has no reason in test outline (T-ARC-18): {exc}", row="T-ARC-18")
                if "empty" in err_str.lower() or "no group" in err_str.lower():
                    raise ReviewRefusalError(f"Refused: no group listed in test outline (T-ARC-19): {exc}", row="T-ARC-19")
                raise ReviewRefusalError(f"Refused: test outline validation failed: {exc}", row="T-ARC-14")

            # 2. contract.md & stubs (T-ARC-20)
            con_file = None
            for dname in (f"phase-{target_phase}", f"p{target_phase}"):
                cand = root / "shared" / dname / "contract.md"
                if not cand.is_file():
                    cand = root / ".lintel" / "tasks" / task_id / "shared" / dname / "contract.md"
                if cand.is_file():
                    con_file = cand
                    break
            if not con_file or not con_file.is_file():
                raise ReviewRefusalError(
                    f"Refused: contract.md missing for phase {target_phase} (T-ARC-20)",
                    row="T-ARC-20",
                )
            con_content = con_file.read_text(encoding="utf-8")
            try:
                contract_doc = documents.validate_contract_doc(con_content, repo_root=root, config=cfg)
            except documents.DocumentValidationError as exc:
                raise ReviewRefusalError(
                    f"Refused: stub file check failed (T-ARC-20): {exc}",
                    row="T-ARC-20",
                )

        # Post-lock lane check (T-ARC-06)
        if target_phase is not None and flow.is_phase_locked(task_data, target_phase):
            p_lock_info = task_data.get("phases", {}).get(str(target_phase), {}).get("lock", {})
            lock_commit = p_lock_info.get("commit") or task_data.get("head")
            if lock_commit:
                # 1. plan.md unchanged
                lock_plan_res = git.run_git(["show", f"{lock_commit}:shared/plan.md"], root, check=False)
                if lock_plan_res.returncode == 0:
                    if lock_plan_res.stdout.strip() != plan_content.strip():
                        raise ReviewRefusalError(
                            "Refused: after the lock the architect's lane is contract.md and "
                            "the test outline only (plan.md was modified) (T-ARC-06)",
                            row="T-ARC-06",
                        )
                # 2. design.md outside test outline unchanged
                for dname in (f"phase-{target_phase}", f"p{target_phase}"):
                    lock_des_res = git.run_git(["show", f"{lock_commit}:shared/{dname}/design.md"], root, check=False)
                    if lock_des_res.returncode == 0:
                        lock_outside = _extract_non_test_outline(lock_des_res.stdout)
                        cur_outside = _extract_non_test_outline(des_content)
                        if lock_outside != cur_outside:
                            raise ReviewRefusalError(
                                "Refused: after the lock the architect's lane is contract.md and "
                                "the test outline only (design.md was modified outside test outline) (T-ARC-06)",
                                row="T-ARC-06",
                            )
                        break

        # Pager and confirmation (T-HUM-08)
        pager_text = format_approval_pager(
            root, task_id, step="plan", pair="architect", phase=target_phase, task_data=task_data
        )

        sys.stdout.write(pager_text + "\n")
        do_confirm = confirm if confirm is not None else confirm_approval()
        if not do_confirm:
            act = flow.format_next_action(task_data, repo_root=root)
            sys.stdout.write(f"Approval canceled (T-HUM-08).\n{act}\n")
            return act
        from lintel.audit import accept_findings_for_command
        accept_findings_for_command(repo_root, "architect", "plan", target_phase)

        # Ensure root shared/ files exist
        target_shared_plan = root / "shared" / "plan.md"
        target_shared_plan.parent.mkdir(parents=True, exist_ok=True)
        if not target_shared_plan.is_file() or target_shared_plan.read_text(encoding="utf-8") != plan_content:
            target_shared_plan.write_text(plan_content, encoding="utf-8")

        task_shared_plan = root / ".lintel" / "tasks" / task_id / "shared" / "plan.md"
        task_shared_plan.parent.mkdir(parents=True, exist_ok=True)
        if not task_shared_plan.is_file() or task_shared_plan.read_text(encoding="utf-8") != plan_content:
            task_shared_plan.write_text(plan_content, encoding="utf-8")

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq

        if target_phase is None:
            # T-ARC-03: no undelivered phase left -> design: plan
            commit_sha = create_lane_commit(
                repo_root=root,
                task_id=task_id,
                author_role="architect",
                message="design: plan",
                lane_files=["shared/plan.md"],
                event_seq=event_seq,
                parent_sha=task_data["head"],
            )
            task_data["head"] = commit_sha
            kind = "plan"
            task_data, next_action, row_id = flow.on_approve_plan(
                task_data, no_undelivered_left=True, commit_sha=commit_sha
            )
        else:
            # Phase design approval: T-ARC-01 or T-ARC-02
            pref_dir = f"shared/phase-{target_phase}"
            if not (root / pref_dir).is_dir() and (root / f"shared/p{target_phase}").is_dir():
                pref_dir = f"shared/p{target_phase}"

            lane_files = [
                "shared/plan.md",
                f"{pref_dir}/design.md",
                f"{pref_dir}/contract.md",
            ]
            if not flow.is_phase_locked(task_data, target_phase) and contract_doc:
                for stub in contract_doc.stub_files:
                    lane_files.append(stub)

            phases_info = task_data.setdefault("phases", {}).setdefault(str(target_phase), {})
            des_commits = phases_info.get("design_commits", [])
            already_approved = bool(
                des_commits
                or flow.is_phase_locked(task_data, target_phase)
                or flow.is_phase_delivered(task_data, target_phase)
            )

            if already_approved:
                # T-ARC-02: revise
                msg = f"design(phase-{target_phase}): revise"
                kind = "revise"
                commit_sha = create_lane_commit(
                    repo_root=root,
                    task_id=task_id,
                    author_role="architect",
                    message=msg,
                    lane_files=lane_files,
                    event_seq=event_seq,
                    parent_sha=task_data["head"],
                )
                task_data["head"] = commit_sha
                task_data, next_action, row_id = flow.on_approve_plan(
                    task_data, phase=target_phase, design_already_approved=True, commit_sha=commit_sha
                )
            else:
                # T-ARC-01: first approval
                msg = f"design(phase-{target_phase})"
                kind = "design"
                commit_sha = create_lane_commit(
                    repo_root=root,
                    task_id=task_id,
                    author_role="architect",
                    message=msg,
                    lane_files=lane_files,
                    event_seq=event_seq,
                    parent_sha=task_data["head"],
                )
                task_data["head"] = commit_sha
                task_data, next_action, row_id = flow.on_approve_plan(
                    task_data, phase=target_phase, design_already_approved=False, commit_sha=commit_sha
                )

        # Record approval in reviews.json
        record_approval(
            state_dir=state_dir,
            task_id=task_id,
            step="plan",
            kind=kind,
            pair="architect",
            stage=stage,
            phase=target_phase,
            review_id=valid_rev["id"],
            lane_hash=valid_rev.get("lane_hash"),


            commit_sha=commit_sha,
            head_sha=commit_sha,
            event_seq=event_seq,
        )

        # Save task.json
        validate_state_doc("task", task_data, file_path=task_state_file)
        atomic_write_json(task_state_file, task_data)

        # Append approval event
        event_rec = create_event(
            step=event_seq,
            event="approve",
            stage=stage,
            role="architect",
            row=row_id,
        )
        append_event(
            state_dir=state_dir,
            task_id=task_id,
            event_data=event_rec,
            task_dir=root / ".lintel" / "tasks" / task_id,
        )

        update_progress_md(root, task_data, next_action)

    return next_action


# ---------------------------------------------------------------------------
# 9. approve final
# ---------------------------------------------------------------------------

def approve_final(
    repo_root: Union[str, Path],
    reject: Optional[str] = None,
    confirm: Optional[bool] = None,
    phase: Optional[str] = None,
    pair: Optional[str] = None,
) -> str:
    """Execute 'lintel approve final' (T-FIN-01, 03, 04, 05, T-HUM-05, 08, 14, 17)."""
    root = Path(repo_root).resolve()
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise ReviewRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active_info
    state_dir = root / ".lintel" / "state"
    task_state_file = state_dir / task_id / "task.json"

    with state_lock(state_dir):
        task_data = load_state_file(task_state_file, doc_type="task")
        pos = flow.get_position(task_data)
        stage = pos.stage

        # NR check: no run pending or active (T-HUM-05)
        if pos.run is not None:
            run_file = state_dir / task_id / "runs" / f"{pos.run}.json"
            if run_file.is_file():
                try:
                    rd = json.loads(run_file.read_text(encoding="utf-8"))
                    if rd.get("state") in ("pending", "active"):
                        raise ReviewRefusalError(
                            "Refused: a run is pending or active; lintel task wait (T-HUM-05)",
                            row="T-HUM-05",
                        )
                except ReviewRefusalError:
                    raise
                except Exception:
                    pass

        # Check expected state (T-HUM-14)
        if pos.code != "H.final":
            exp = flow.format_next_action(task_data, repo_root=root)
            raise ReviewRefusalError(
                f"Refused: approve final not expected in state '{pos.code}' (T-HUM-14);\n"
                f"expected action: {exp}",
                row="T-HUM-14",
            )

        # T-HUM-17: approve final does not accept --pair
        if pair:
            raise ReviewRefusalError(
                "Refused: approve final does not accept --pair (T-HUM-17)",
                row="T-HUM-17",
            )

        # Requires final reviewer's done, either verdict (approve or issues), bound to recorded head (T-FIN-01, T-FIN-04)
        valid_rev = get_valid_review(root, task_id, "final-reviewer", task_data=task_data)
        if not valid_rev:
            raise ReviewRefusalError(
                "Refused: final review is stale or missing (T-FIN-04);\n"
                "run 'lintel invoke final-reviewer --phase final'",
                row="T-FIN-04",
            )

        if reject is not None:
            # T-FIN-05: rejection transition to PO revisit
            # Locate final reviewer's run folder and final-findings.md on test side
            orig_content = ""
            if valid_rev and valid_rev.get("run"):
                run_file = state_dir / task_id / "runs" / f"{valid_rev['run']}.json"
                if run_file.is_file():
                    try:
                        rd = json.loads(run_file.read_text(encoding="utf-8"))
                        if rd.get("folder"):
                            orig_path = root / rd["folder"] / "final-findings.md"
                            if orig_path.is_file():
                                orig_content = orig_path.read_text(encoding="utf-8")
                    except Exception:
                        pass

            why = reject.strip()
            if why:
                if f"## Rejection Reason\n\n{why}" not in orig_content:
                    if orig_content.strip():
                        initial_editor_content = f"{orig_content.rstrip()}\n\n## Rejection Reason\n\n{why}\n"
                    else:
                        initial_editor_content = f"# Final Findings\n\n## Rejection Reason\n\n{why}\n"
                else:
                    initial_editor_content = orig_content
            else:
                initial_editor_content = orig_content or "# Final Findings\n"

            # Open findings in human's $EDITOR (T-FIN-05)
            editor_cmd = os.environ.get("EDITOR", "vi")
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as tf:
                tf.write(initial_editor_content)
                temp_path = tf.name

            try:
                cmd_parts = shlex.split(editor_cmd) + [temp_path]
                subprocess.run(cmd_parts, check=True)
                edited_content = Path(temp_path).read_text(encoding="utf-8")
            finally:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

            # Copy result to shared/final-findings.md (the original on test side is kept intact)
            shared_dir = root / "shared"
            shared_dir.mkdir(parents=True, exist_ok=True)
            shared_ff = shared_dir / "final-findings.md"
            shared_ff.write_text(edited_content, encoding="utf-8")

            # Commits nothing. Records revisit cause final-reject in task.revisit
            task_data, next_action, row_id = flow.on_approve_final(task_data, reject_reason=reject)
            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

            event_rec = create_event(
                step=event_seq,
                event="reject",
                stage=stage,
                role="final-reviewer",
                row=row_id,
            )
            append_event(
                state_dir=state_dir,
                task_id=task_id,
                event_data=event_rec,
                task_dir=root / ".lintel" / "tasks" / task_id,
            )
            update_progress_md(root, task_data, next_action)
            return next_action

        # Pager and confirmation (T-HUM-08)
        pager_text = format_approval_pager(
            root, task_id, step="final", pair="final-reviewer", task_data=task_data
        )

        sys.stdout.write(pager_text + "\n")
        do_confirm = confirm if confirm is not None else confirm_approval()
        if not do_confirm:
            act = flow.format_next_action(task_data, repo_root=root)
            sys.stdout.write(f"Approval canceled (T-HUM-08).\n{act}\n")
            return act
        from lintel.audit import accept_findings_for_command
        accept_findings_for_command(repo_root, "final-reviewer", "final")

        # Commits NOTHING. Advances event sequence
        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq

        # Record approval in reviews.json
        record_approval(
            state_dir=state_dir,
            task_id=task_id,
            step="final",
            kind=None,
            pair="final-reviewer",
            stage=stage,
            phase=None,
            review_id=valid_rev["id"],
            lane_hash=None,
            commit_sha=None,
            head_sha=task_data["head"],
            event_seq=event_seq,
        )


        # Transition state to docs:W.po (T-FIN-03)
        task_data, next_action, row_id = flow.on_approve_final(task_data)

        # Save task.json
        validate_state_doc("task", task_data, file_path=task_state_file)
        atomic_write_json(task_state_file, task_data)

        # Append approval event
        event_rec = create_event(
            step=event_seq,
            event="approve",
            stage=stage,
            role="final-reviewer",
            row=row_id,
        )
        append_event(
            state_dir=state_dir,
            task_id=task_id,
            event_data=event_rec,
            task_dir=root / ".lintel" / "tasks" / task_id,
        )

        update_progress_md(root, task_data, next_action)

    return next_action


# ---------------------------------------------------------------------------
# 10. approve docs
# ---------------------------------------------------------------------------

def _check_dir_changed_against_head(
    repo_root: Path,
    head_sha: Optional[str],
    rel_dir: str,
    exclude_files: Optional[Sequence[str]] = None,
) -> bool:
    """Check if any file in rel_dir differs from head_sha or is untracked."""
    root = Path(repo_root).resolve()
    target_dir = root / rel_dir
    excl = set(exclude_files or [])

    if not head_sha:
        if target_dir.is_dir():
            for p in target_dir.rglob("*"):
                if p.is_file() and not p.name.startswith(".") and p.name not in excl:
                    return True
        return False

    res = git.run_git(["diff", "--name-only", head_sha, "--", rel_dir], root, check=False)
    if res.returncode == 0 and res.stdout.strip():
        for line in res.stdout.splitlines():
            line = line.strip()
            if line and Path(line).name not in excl and not Path(line).name.startswith("."):
                return True

    res_status = git.run_git(["status", "--porcelain", "--", rel_dir], root, check=False)
    if res_status.returncode == 0 and res_status.stdout.strip():
        for line in res_status.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                p = parts[1]
                if Path(p).name not in excl and not Path(p).name.startswith("."):
                    return True

    return False


def approve_docs(
    repo_root: Union[str, Path],
    reject: Optional[str] = None,
    pair: Optional[str] = None,
    confirm: Optional[bool] = None,
    phase: Optional[str] = None,
) -> str:
    """Execute 'lintel approve docs' (T-FIN-10, 11, 12, 13, T-HUM-05, 08, 14)."""
    root = Path(repo_root).resolve()
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise ReviewRefusalError(
            "Refused: no active task; run 'lintel task start \"<title>\"' (T-STG-09)",
            row="T-STG-09",
        )
    task_id, task_data = active_info
    state_dir = root / ".lintel" / "state"
    task_state_file = state_dir / task_id / "task.json"

    with state_lock(state_dir):
        task_data = load_state_file(task_state_file, doc_type="task")
        pos = flow.get_position(task_data)
        stage = pos.stage

        # NR check: no run pending or active (T-HUM-05)
        if pos.run is not None:
            run_file = state_dir / task_id / "runs" / f"{pos.run}.json"
            if run_file.is_file():
                try:
                    rd = json.loads(run_file.read_text(encoding="utf-8"))
                    if rd.get("state") in ("pending", "active"):
                        raise ReviewRefusalError(
                            "Refused: a run is pending or active; lintel task wait (T-HUM-05)",
                            row="T-HUM-05",
                        )
                except ReviewRefusalError:
                    raise
                except Exception:
                    pass

        # Check expected state (T-HUM-14)
        if pos.code != "H.docs":
            exp = flow.format_next_action(task_data, repo_root=root)
            raise ReviewRefusalError(
                f"Refused: approve docs not expected in state '{pos.code}' (T-HUM-14);\n"
                f"expected action: {exp}",
                row="T-HUM-14",
            )

        # Requires both docs-update writers to have finished (T-FIN-11)
        docs_up = flow._ensure_docs_update(task_data)
        if not docs_up.get("po_finished"):
            raise ReviewRefusalError(
                "Refused: PO writer has not finished in docs update (T-FIN-11);\n"
                "run 'lintel invoke po --phase docs'",
                row="T-FIN-11",
            )
        if not docs_up.get("architect_finished"):
            raise ReviewRefusalError(
                "Refused: architect writer has not finished in docs update (T-FIN-11);\n"
                "run 'lintel invoke architect --phase docs'",
                row="T-FIN-11",
            )

        # Check if PO or Architect changed anything
        po_changed = docs_up.get("po_changed")
        if po_changed is None:
            po_changed = _check_dir_changed_against_head(root, task_data.get("head"), ".lintel/specs")
        arch_changed = docs_up.get("architect_changed")
        if arch_changed is None:
            arch_changed = _check_dir_changed_against_head(
                root, task_data.get("head"), ".lintel/steering", exclude_files=["testing.md"]
            )

        # If neither pair changed anything, approve docs is skipped!
        if not po_changed and not arch_changed:
            docs_up["outcome"] = "skipped"
            pos.code = "H.finish"
            task_data["position"] = pos.to_dict()
            next_act = flow.format_next_action(task_data, repo_root=root)
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)
            update_progress_md(root, task_data, next_act)
            return next_act

        # Valid review (VR) check for each pair that changed something (T-FIN-11)
        po_valid_rev = None
        arch_valid_rev = None
        if po_changed:
            po_valid_rev = get_valid_review(root, task_id, "po", task_data=task_data)
            if not po_valid_rev:
                raise ReviewRefusalError(
                    "Refused: PO specs review is missing or stale (T-FIN-11);\n"
                    "run 'lintel invoke po-reviewer --phase docs'",
                    row="T-FIN-11",
                )
        if arch_changed:
            arch_valid_rev = get_valid_review(root, task_id, "architect", task_data=task_data)
            if not arch_valid_rev:
                raise ReviewRefusalError(
                    "Refused: architect steering review is missing or stale (T-FIN-11);\n"
                    "run 'lintel invoke architect-reviewer --phase docs'",
                    row="T-FIN-11",
                )

        if reject is not None:
            why = reject.strip() or "Docs rejected"

            # T-FIN-13: both changed and --pair is missing
            if po_changed and arch_changed and not pair:
                raise ReviewRefusalError(
                    "Refused: --pair po or --pair architect required for docs rejection when both pairs changed (T-FIN-13)",
                    row="T-FIN-13",
                )
            if pair:
                if pair not in ("po", "architect"):
                    raise ReviewRefusalError(
                        "Refused: --pair must be po or architect (T-FIN-13)",
                        row="T-FIN-13",
                    )
                if pair == "po" and not po_changed:
                    raise ReviewRefusalError(
                        "Refused: PO pair did not change anything in docs update (T-FIN-12)",
                        row="T-FIN-12",
                    )
                if pair == "architect" and not arch_changed:
                    raise ReviewRefusalError(
                        "Refused: architect pair did not change anything in docs update (T-FIN-12)",
                        row="T-FIN-12",
                    )
                target_pair = pair
            else:
                target_pair = "po" if po_changed else "architect"

            target_valid_rev = po_valid_rev if target_pair == "po" else arch_valid_rev
            folder_rel = None
            if target_valid_rev and target_valid_rev.get("run"):
                run_file = state_dir / task_id / "runs" / f"{target_valid_rev['run']}.json"
                if run_file.is_file():
                    try:
                        rd = json.loads(run_file.read_text(encoding="utf-8"))
                        folder_rel = rd.get("folder")
                    except Exception:
                        pass
            if not folder_rel:
                folder_rel = f".lintel/tasks/{task_id}/{target_pair}/docs-reject"

            run_folder = root / folder_rel
            run_folder.mkdir(parents=True, exist_ok=True)
            rejection_file = run_folder / "rejection.md"
            rejection_file.write_text(why + "\n", encoding="utf-8")
            rejection_rel_path = f"{folder_rel}/rejection.md"

            loops.record_human_reject(
                state_dir=state_dir,
                task_id=task_id,
                writer=target_pair,
                stage="docs",
                reason=why,
                file_path=rejection_rel_path,
                where=".lintel/specs/" if target_pair == "po" else ".lintel/steering/",
            )

            task_data, next_action, row_id = flow.on_approve_docs(task_data, reject_pair=target_pair)
            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            validate_state_doc("task", task_data, file_path=task_state_file)
            atomic_write_json(task_state_file, task_data)

            event_rec = create_event(
                step=event_seq,
                event="reject",
                stage=stage,
                role=target_pair,
                row=row_id,
            )
            append_event(
                state_dir=state_dir,
                task_id=task_id,
                event_data=event_rec,
                task_dir=root / ".lintel" / "tasks" / task_id,
            )
            update_progress_md(root, task_data, next_action)
            return next_action

        # Pager and confirmation (T-HUM-08)
        pager_text = format_approval_pager(
            root, task_id, step="docs", pair="docs", task_data=task_data
        )
    
        sys.stdout.write(pager_text + "\n")
        do_confirm = confirm if confirm is not None else confirm_approval()
        if not do_confirm:
            act = flow.format_next_action(task_data, repo_root=root)
            sys.stdout.write(f"Approval canceled (T-HUM-08).\n{act}\n")
            return act
        from lintel.audit import accept_findings_for_command
        if po_changed:
            accept_findings_for_command(repo_root, "po", "docs")
        if arch_changed:
            accept_findings_for_command(repo_root, "architect", "docs")

        # Lane files to commit: ONLY .lintel/specs/ and/or .lintel/steering/
        lane_files: List[str] = []
        if po_changed:
            lane_files.extend(get_writer_lane_paths(root, "po", "docs", task_id, task_data=task_data))
        if arch_changed:
            lane_files.extend(get_writer_lane_paths(root, "architect", "docs", task_id, task_data=task_data))
        lane_files = sorted(set(lane_files))

        event_seq = task_data.get("event_seq", 0) + 1
        task_data["event_seq"] = event_seq

        # Create commit docs(lintel): specs and steering as Lintel
        commit_sha = create_lane_commit(
            repo_root=root,
            task_id=task_id,
            author_role="lintel",
            message="docs(lintel): specs and steering",
            lane_files=lane_files,
            event_seq=event_seq,
            parent_sha=task_data["head"],
        )
        task_data["head"] = commit_sha

        # Record approval in reviews.json for each pair approved
        if po_changed and po_valid_rev:
            record_approval(
                state_dir=state_dir,
                task_id=task_id,
                step="docs",
                kind=None,
                pair="po",
                stage=stage,
                phase=None,
                review_id=po_valid_rev["id"],
                lane_hash=po_valid_rev.get("lane_hash"),
    

                commit_sha=commit_sha,
                head_sha=commit_sha,
                event_seq=event_seq,
            )
        if arch_changed and arch_valid_rev:
            record_approval(
                state_dir=state_dir,
                task_id=task_id,
                step="docs",
                kind=None,
                pair="architect",
                stage=stage,
                phase=None,
                review_id=arch_valid_rev["id"],
                lane_hash=arch_valid_rev.get("lane_hash"),
    

                commit_sha=commit_sha,
                head_sha=commit_sha,
                event_seq=event_seq,
            )

        # Transition state to H.finish (T-FIN-10)
        task_data, next_action, row_id = flow.on_approve_docs(task_data)

        # Save task.json
        validate_state_doc("task", task_data, file_path=task_state_file)
        atomic_write_json(task_state_file, task_data)

        # Append approval event
        event_rec = create_event(
            step=event_seq,
            event="approve",
            stage=stage,
            role="lintel",
            row=row_id,
        )
        append_event(
            state_dir=state_dir,
            task_id=task_id,
            event_data=event_rec,
            task_dir=root / ".lintel" / "tasks" / task_id,
        )

        update_progress_md(root, task_data, next_action)

    return next_action
