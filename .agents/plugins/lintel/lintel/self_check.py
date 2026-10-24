"""Plugin self-check and input hashes (M1a-12).

Implements:
1. Ignored noise patterns: .DS_Store, *.swp, *~, __pycache__, *.pyc.
2. check_plugin_integrity(repo_root: Path, recorded_head: str) -> List[str]:
   - Compares vendored plugin directory (.agents/plugins/lintel/) against recorded head tree.
   - Untracked files included (scans working tree on disk for all non-noise files and
     compares with git ls-tree of recorded head).
   - Returns list of differences (modified, added, missing files).
   - Global install: if vendored plugin directory does not exist, skips comparison.
3. CLI start integration:
   - At CLI entry in cli.py, if active task has a recorded head:
     - If differences found: refuse every subcommand except doctor, task status,
       and task abort with clear error listing the differences (T-STG-10).
4. check_invoke_inputs(repo_root: Path, task_data: Dict[str, Any], docs_update: bool = False) -> Dict[str, List[str]]:
   - Helper for invoke (wired in M1a-17):
     1. Persona files: .lintel/personas/*.md compared against recorded head.
     2. Agent files: .agents/agents/lintel-*.md (all 10 roles) compared against recorded head.
     3. Launcher: .lintel/bin/lintel compared against recorded head.
     4. Living docs:
        - If docs_update is False: .lintel/specs/** and .lintel/steering/** compared against recorded head.
        - If docs_update is True: specs and steering edits are accepted (not reported as differences).
     5. Input hashes:
        - shared/request.md: sha256 of file content compared with task_data.get("request_hash").
        - shared/answers.md: sha256 of file content compared with task_data.get("answers_hash").
   - Returns dictionary of differences categorized by input type.
"""

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

try:
    from lintel.caller import find_active_task
    from lintel.git import GitError, run_git
except ImportError:
    from .caller import find_active_task
    from .git import GitError, run_git


# Ignored noise patterns per PLAN §3.6 and TASKS M1a-12
NOISE_PATTERNS = (".DS_Store", "*.swp", "*~", "__pycache__", "*.pyc")

# All 10 mandatory roles per PLAN §3.2
ALL_10_ROLES: Tuple[str, ...] = (
    "po",
    "po-reviewer",
    "architect",
    "architect-reviewer",
    "qa",
    "qa-reviewer",
    "dev",
    "dev-reviewer",
    "arbiter",
    "final-reviewer",
)


def is_noise(path: Union[str, Path]) -> bool:
    """Return True if path or any of its segments match noise patterns."""
    p = Path(path)
    for part in p.parts:
        if part in (".DS_Store", "__pycache__"):
            return True
        if part.endswith(".pyc") or part.endswith(".swp") or part.endswith("~"):
            return True
        if (
            fnmatch.fnmatch(part, "*.swp")
            or fnmatch.fnmatch(part, "*~")
            or fnmatch.fnmatch(part, "*.pyc")
        ):
            return True
    name = p.name
    if name in (".DS_Store", "__pycache__"):
        return True
    if (
        fnmatch.fnmatch(name, "*.swp")
        or fnmatch.fnmatch(name, "*~")
        or fnmatch.fnmatch(name, "*.pyc")
    ):
        return True
    return False


def git_blob_sha(data: bytes) -> str:
    """Compute git object SHA-1 for blob data."""
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def compute_disk_sha(path: Path) -> str:
    """Compute git blob sha for a file or symlink on disk."""
    if path.is_symlink():
        target = os.readlink(path)
        data = target.encode("utf-8")
    else:
        data = path.read_bytes()
    return git_blob_sha(data)


class Difference(str):
    """Represents a differing file, behaving as a string (the relative path)."""

    def __new__(
        cls,
        path: str,
        kind: str = "modified",
        message: Optional[str] = None,
    ):
        obj = str.__new__(cls, path)
        obj.path = path
        obj.kind = kind  # "modified", "added", "missing", "hash_mismatch"
        obj.message = message or f"{kind}: {path}"
        return obj

    def __repr__(self) -> str:
        return f"Difference({self.path!r}, kind={self.kind!r})"


class InvokeDifferences(dict):
    """Categorized differences dictionary returned by check_invoke_inputs.

    Primary categories:
      - 'personas': List[Difference]
      - 'agents': List[Difference]
      - 'launcher': List[Difference]
      - 'living_docs': List[Difference]
      - 'hashes': List[Difference]

    Aliases supported:
      - 'persona', 'persona_files' -> 'personas'
      - 'agent', 'agent_files' -> 'agents'
      - 'specs', 'steering', 'docs' -> 'living_docs'
      - 'input_hashes', 'hash' -> 'hashes'
      - 'request' -> filters hashes for request
      - 'answers' -> filters hashes for answers
    """

    _ALIASES = {
        "persona": "personas",
        "persona_files": "personas",
        "agent": "agents",
        "agent_files": "agents",
        "specs": "living_docs",
        "steering": "living_docs",
        "docs": "living_docs",
        "input_hashes": "hashes",
        "hash": "hashes",
    }

    def __getitem__(self, key: str) -> List[Any]:
        if key == "request":
            return [d for d in self.get("hashes", []) if "request" in str(d)]
        if key == "answers":
            return [d for d in self.get("hashes", []) if "answers" in str(d)]
        canonical = self._ALIASES.get(key, key)
        return super().__getitem__(canonical)

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            if key == "request":
                return any("request" in str(d) for d in self.get("hashes", []))
            if key == "answers":
                return any("answers" in str(d) for d in self.get("hashes", []))
            canonical = self._ALIASES.get(key, key)
            return super().__contains__(canonical)
        return super().__contains__(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "request":
            req_diffs = [d for d in super().get("hashes", []) if "request" in str(d)]
            return req_diffs if req_diffs else default
        if key == "answers":
            ans_diffs = [d for d in super().get("hashes", []) if "answers" in str(d)]
            return ans_diffs if ans_diffs else default
        canonical = self._ALIASES.get(key, key)
        return super().get(canonical, default)

    def all_differences(self) -> List[str]:
        """Flattened list of all differences across all categories."""
        all_diffs: List[str] = []
        for diffs in self.values():
            all_diffs.extend(diffs)
        return all_diffs


def read_tree_blobs(
    repo_root: Union[str, Path],
    commit_sha: str,
    path_prefix: Optional[str] = None,
) -> Dict[str, Tuple[str, str]]:
    """Return map of rel_path -> (mode, sha) for all blobs in commit_sha tree.

    If path_prefix is provided, only entries matching that path or subtree are returned.
    """
    repo = Path(repo_root).resolve()
    args = ["ls-tree", "-r", "-z", commit_sha]
    if path_prefix:
        args.extend(["--", path_prefix])

    proc = run_git(args, repo, check=False)
    if proc.returncode != 0:
        stderr_msg = (
            proc.stderr.strip()
            if isinstance(proc.stderr, str)
            else proc.stderr.decode("utf-8", "replace").strip()
        )
        raise GitError(f"git ls-tree failed for {commit_sha}: {stderr_msg}")

    entries: Dict[str, Tuple[str, str]] = {}
    out = proc.stdout
    if not out:
        return entries

    tokens = out.split("\0")
    for token in tokens:
        if not token:
            continue
        try:
            meta, path = token.split("\t", 1)
            parts = meta.split()
            if len(parts) >= 3:
                mode, _obj_type, sha = parts[0], parts[1], parts[2]
                entries[path] = (mode, sha)
        except ValueError:
            continue
    return entries


def check_plugin_integrity(
    repo_root: Union[str, Path],
    recorded_head: str,
) -> List[str]:
    """Compare vendored plugin directory (.agents/plugins/lintel/) against recorded head tree.

    Untracked files included (scans working tree on disk for all non-noise files and
    compares with git ls-tree of recorded head).
    Returns list of differences (modified, added, missing files).
    Global install: if vendored plugin directory does not exist, skips comparison.
    """
    repo = Path(repo_root).resolve()
    plugin_dir = repo / ".agents" / "plugins" / "lintel"

    # Global install check: if vendored plugin directory does not exist, skip comparison
    if not plugin_dir.is_dir():
        return []

    tree_entries = read_tree_blobs(repo, recorded_head, path_prefix=".agents/plugins/lintel")

    # Scan working tree on disk under plugin_dir
    disk_files: Dict[str, Path] = {}
    for root, dirs, files in os.walk(plugin_dir):
        # Skip noise directories and .git
        dirs[:] = [d for d in dirs if not is_noise(d) and d != ".git"]
        for f in files:
            if is_noise(f):
                continue
            full_path = Path(root) / f
            rel_path = full_path.relative_to(repo).as_posix()
            if is_noise(rel_path):
                continue
            disk_files[rel_path] = full_path

    differences: List[Difference] = []

    # Check files on disk
    for rel_path, full_path in disk_files.items():
        if rel_path not in tree_entries:
            # Untracked file in plugin directory
            differences.append(Difference(rel_path, "added"))
        else:
            # Compare content with recorded head blob
            disk_sha = compute_disk_sha(full_path)
            _tree_mode, tree_sha = tree_entries[rel_path]
            if disk_sha != tree_sha:
                differences.append(Difference(rel_path, "modified"))

    # Check for missing files (present in recorded head, missing on disk)
    for rel_path in tree_entries:
        if not rel_path.startswith(".agents/plugins/lintel/"):
            continue
        if is_noise(rel_path):
            continue
        if rel_path not in disk_files:
            differences.append(Difference(rel_path, "missing"))

    return sorted(differences, key=lambda d: str(d))


def check_invoke_inputs(
    repo_root: Union[str, Path],
    task_data: Dict[str, Any],
    docs_update: bool = False,
) -> Dict[str, List[str]]:
    """Helper for invoke checking personas, agents, launcher, living docs, and input hashes.

    1. Persona files: .lintel/personas/*.md compared against recorded head.
    2. Agent files: .agents/agents/lintel-*.md (all 10 roles) compared against recorded head.
    3. Launcher: .lintel/bin/lintel compared against recorded head.
    4. Living docs:
       - If docs_update is False: .lintel/specs/** and .lintel/steering/** compared against recorded head.
       - If docs_update is True: specs and steering edits are accepted (not reported as differences).
    5. Input hashes:
       - shared/request.md: sha256 of file content compared with task_data.get("request_hash").
       - shared/answers.md: sha256 of file content compared with task_data.get("answers_hash").

    Returns dictionary of differences categorized by input type.
    """
    repo = Path(repo_root).resolve()
    recorded_head = task_data.get("head") or task_data.get("recorded_head")
    if not recorded_head:
        raise ValueError("task_data must contain 'head' or 'recorded_head'")

    tree_blobs = read_tree_blobs(repo, recorded_head)
    diffs = InvokeDifferences()

    # 1. Persona files (.lintel/personas/*.md)
    personas_dir = repo / ".lintel" / "personas"
    disk_personas: Dict[str, Path] = {}
    if personas_dir.is_dir():
        for f in personas_dir.iterdir():
            if f.is_file() and f.name.endswith(".md") and not is_noise(f):
                rel = f.relative_to(repo).as_posix()
                disk_personas[rel] = f

    tree_personas = {
        p: blob
        for p, blob in tree_blobs.items()
        if p.startswith(".lintel/personas/") and p.endswith(".md") and not is_noise(p)
    }

    persona_diffs: List[Difference] = []
    for rel, fp in disk_personas.items():
        if rel not in tree_personas:
            persona_diffs.append(Difference(rel, "added"))
        else:
            if compute_disk_sha(fp) != tree_personas[rel][1]:
                persona_diffs.append(Difference(rel, "modified"))
    for rel in tree_personas:
        if rel not in disk_personas:
            persona_diffs.append(Difference(rel, "missing"))

    if persona_diffs:
        diffs["personas"] = sorted(persona_diffs, key=lambda d: str(d))

    # 2. Agent files (.agents/agents/lintel-*.md, all 10 roles)
    expected_agent_paths = {f".agents/agents/lintel-{role}.md" for role in ALL_10_ROLES}
    agents_dir = repo / ".agents" / "agents"
    disk_agents: Dict[str, Path] = {}
    if agents_dir.is_dir():
        for f in agents_dir.iterdir():
            if (
                f.is_file()
                and f.name.startswith("lintel-")
                and f.name.endswith(".md")
                and not is_noise(f)
            ):
                rel = f.relative_to(repo).as_posix()
                disk_agents[rel] = f

    tree_agents = {
        p: blob
        for p, blob in tree_blobs.items()
        if p.startswith(".agents/agents/lintel-") and p.endswith(".md") and not is_noise(p)
    }

    all_agent_paths = sorted(
        expected_agent_paths | set(disk_agents.keys()) | set(tree_agents.keys())
    )
    agent_diffs: List[Difference] = []
    for rel in all_agent_paths:
        in_disk = rel in disk_agents
        in_tree = rel in tree_agents
        if not in_disk and not in_tree:
            agent_diffs.append(Difference(rel, "missing"))
        elif in_disk and not in_tree:
            agent_diffs.append(Difference(rel, "added"))
        elif in_tree and not in_disk:
            agent_diffs.append(Difference(rel, "missing"))
        else:
            if compute_disk_sha(disk_agents[rel]) != tree_agents[rel][1]:
                agent_diffs.append(Difference(rel, "modified"))

    if agent_diffs:
        diffs["agents"] = agent_diffs

    # 3. Launcher (.lintel/bin/lintel)
    launcher_rel = ".lintel/bin/lintel"
    launcher_path = repo / ".lintel" / "bin" / "lintel"
    in_disk = launcher_path.is_file()
    in_tree = launcher_rel in tree_blobs
    launcher_diffs: List[Difference] = []
    if not in_disk and not in_tree:
        launcher_diffs.append(Difference(launcher_rel, "missing"))
    elif in_disk and not in_tree:
        launcher_diffs.append(Difference(launcher_rel, "added"))
    elif in_tree and not in_disk:
        launcher_diffs.append(Difference(launcher_rel, "missing"))
    else:
        if compute_disk_sha(launcher_path) != tree_blobs[launcher_rel][1]:
            launcher_diffs.append(Difference(launcher_rel, "modified"))

    if launcher_diffs:
        diffs["launcher"] = launcher_diffs

    # 4. Living docs (.lintel/specs/** and .lintel/steering/**)
    if not docs_update:
        disk_docs: Dict[str, Path] = {}
        for sub in (".lintel/specs", ".lintel/steering"):
            sdir = repo / sub
            if sdir.is_dir():
                for root, dirs, files in os.walk(sdir):
                    dirs[:] = [d for d in dirs if not is_noise(d)]
                    for f in files:
                        if is_noise(f):
                            continue
                        fp = Path(root) / f
                        rel = fp.relative_to(repo).as_posix()
                        if not is_noise(rel):
                            disk_docs[rel] = fp

        tree_docs = {
            p: blob
            for p, blob in tree_blobs.items()
            if (p.startswith(".lintel/specs/") or p.startswith(".lintel/steering/"))
            and not is_noise(p)
        }

        docs_diffs: List[Difference] = []
        for rel, fp in disk_docs.items():
            if rel not in tree_docs:
                docs_diffs.append(Difference(rel, "added"))
            else:
                if compute_disk_sha(fp) != tree_docs[rel][1]:
                    docs_diffs.append(Difference(rel, "modified"))
        for rel in tree_docs:
            if rel not in disk_docs:
                docs_diffs.append(Difference(rel, "missing"))

        if docs_diffs:
            diffs["living_docs"] = sorted(docs_diffs, key=lambda d: str(d))

    # 5. Input hashes (shared/request.md and shared/answers.md)
    task_id = task_data.get("id")

    def _find_shared_file(filename: str) -> Tuple[Optional[Path], str]:
        candidates: List[Path] = []
        if task_id:
            candidates.append(repo / ".lintel" / "tasks" / str(task_id) / "shared" / filename)
        candidates.append(repo / "shared" / filename)
        # Search task directories
        tasks_dir = repo / ".lintel" / "tasks"
        if tasks_dir.is_dir():
            try:
                for entry in tasks_dir.iterdir():
                    if entry.is_dir() and (entry / "shared" / filename).is_file():
                        candidates.append(entry / "shared" / filename)
            except OSError:
                pass

        for c in candidates:
            if c.is_file():
                try:
                    return c, c.relative_to(repo).as_posix()
                except ValueError:
                    return c, f"shared/{filename}"
        return None, f"shared/{filename}"

    hash_diffs: List[Difference] = []

    # request.md
    req_hash = task_data.get("request_hash")
    rf, rel_req = _find_shared_file("request.md")
    if req_hash is not None:
        if rf is None:
            hash_diffs.append(Difference(rel_req, "missing", f"missing: {rel_req}"))
        else:
            content_sha256 = hashlib.sha256(rf.read_bytes()).hexdigest()
            if content_sha256 != req_hash:
                hash_diffs.append(
                    Difference(
                        rel_req,
                        "hash_mismatch",
                        f"hash mismatch: {rel_req} (expected {req_hash[:8]}..., got {content_sha256[:8]}...)",
                    )
                )

    # answers.md
    ans_hash = task_data.get("answers_hash")
    af, rel_ans = _find_shared_file("answers.md")
    if ans_hash is not None:
        if af is None:
            hash_diffs.append(Difference(rel_ans, "missing", f"missing: {rel_ans}"))
        else:
            content_sha256 = hashlib.sha256(af.read_bytes()).hexdigest()
            if content_sha256 != ans_hash:
                hash_diffs.append(
                    Difference(
                        rel_ans,
                        "hash_mismatch",
                        f"hash mismatch: {rel_ans} (expected {ans_hash[:8]}..., got {content_sha256[:8]}...)",
                    )
                )
    else:
        # answers_hash is None (no answers recorded by CLI yet)
        if af is not None:
            hash_diffs.append(
                Difference(rel_ans, "modified", f"unrecorded answers file: {rel_ans}")
            )

    if hash_diffs:
        diffs["hashes"] = hash_diffs

    return diffs


def find_repo_root(start_path: Optional[Union[str, Path]] = None) -> Path:
    """Find repository root by locating .lintel or .git in start_path or parents."""
    cur = Path(start_path).resolve() if start_path else Path.cwd().resolve()
    for candidate in [cur, *cur.parents]:
        if (
            (candidate / ".lintel").is_dir()
            or (candidate / ".git").is_dir()
            or (candidate / ".git").is_file()
        ):
            return candidate
    return cur


def find_active_task_head(
    repo_root: Optional[Union[str, Path]] = None,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Find the active task recorded head, or any task state recorded head."""
    root = find_repo_root(repo_root)

    # 1. Try find_active_task
    try:
        active = find_active_task(repo_root=root)
        if active:
            _task_id, task_data = active
            head = task_data.get("head") or task_data.get("recorded_head")
            if head:
                return head, task_data
    except Exception:
        pass

    # 2. Search state directory for active task or any task with recorded head
    state_dir = root / ".lintel" / "state"
    if state_dir.is_dir():
        active_candidate: Optional[Tuple[str, Dict[str, Any]]] = None
        other_candidate: Optional[Tuple[str, Dict[str, Any]]] = None
        try:
            for entry in state_dir.iterdir():
                if entry.is_dir() and (entry / "task.json").is_file():
                    try:
                        data = json.loads((entry / "task.json").read_text(encoding="utf-8"))
                        head = data.get("head") or data.get("recorded_head")
                        if head:
                            if data.get("status") == "active":
                                active_candidate = (head, data)
                                break
                            elif other_candidate is None:
                                other_candidate = (head, data)
                    except Exception:
                        pass
        except OSError:
            pass

        if active_candidate:
            return active_candidate
        if other_candidate:
            return other_candidate

    return None


def is_exempt_from_integrity_check(args: argparse.Namespace) -> bool:
    """Check if command is exempt from plugin integrity check (doctor, task status, task abort)."""
    subcmd = getattr(args, "subcommand", None) or ""
    if subcmd == "doctor":
        return True
    if subcmd == "task":
        task_cmd = getattr(args, "task_command", None) or ""
        if task_cmd in ("status", "abort"):
            return True
    return False
