"""Drift detection, review, acknowledgment, and rejection (M1a-26).

Implements PLAN §3.6, §3.5, and docs/state-machine.md §3.6 (T-AMD-50 to T-AMD-53).

The allow-list rule:
Every changed file in the change set is drift unless it is plain source or docs.
Plain source means a file matching `paths.source` (from config or defaults:
**/*.java, **/*.kt, **/*.ts, **/*.js, **/*.py, **/*.go, **/*.rs, **/*.cs,
**/*.rb, **/*.php, **/*.swift, **/*.c, **/*.cpp, **/*.h, **/*.md),
EXCEPT:
- agent-config files (.agents/**, **/AGENTS.md, **/GEMINI.md, .gemini/**, etc.)
- dotfiles and dot-dirs (any path component beginning with '.')
- *.config.*, *.conf.*, *.workspace.* wildcard matches on path or basename
- paths.build files (matching config['paths']['build'] globs)
- paths.drift files (matching config['paths']['drift'] globs)
- repo files named in .lintel/scripts/*, build files, or support runner configs (token scan)
- suspect tests (code files matching suspect test heuristics)

If a file does not match paths.source, it is drift.
If a file matches paths.source but matches any exception, it is drift.
Only files matching paths.source with NO exceptions are plain source (not drift).
"""

import sys
sys.dont_write_bytecode = True

import argparse
from datetime import datetime, timezone
import difflib
import fnmatch
import json
import os
from pathlib import Path
import re
import shlex
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

try:
    from lintel import caller, events, flow, git, loops, paths, reviews
    from lintel.config import ConfigError, load_config
    from lintel.git import ChangeEntry, ChangeSet
    from lintel.state import (
        StateError,
        atomic_write_json,
        load_state_file,
        resolve_state_root,
        state_lock,
        validate_state_doc,
    )
except ImportError:
    from . import caller, events, flow, git, loops, paths, reviews
    from .config import ConfigError, load_config
    from .git import ChangeEntry, ChangeSet
    from .state import (
        StateError,
        atomic_write_json,
        load_state_file,
        resolve_state_root,
        state_lock,
        validate_state_doc,
    )


# Default fallback patterns if not present in config
DEFAULT_SOURCE_PATTERNS: List[str] = [
    "**/*.java",
    "**/*.kt",
    "**/*.ts",
    "**/*.js",
    "**/*.py",
    "**/*.go",
    "**/*.rs",
    "**/*.cs",
    "**/*.rb",
    "**/*.php",
    "**/*.swift",
    "**/*.c",
    "**/*.cpp",
    "**/*.h",
    "**/*.md",
]

DEFAULT_BUILD_PATTERNS: List[str] = [
    "**/pom.xml",
    "**/package.json",
    "**/build.gradle*",
    "**/settings.gradle*",
    "**/buildSrc/**",
    "**/setup.py",
    "**/pyproject.toml",
    "**/noxfile.py",
    "**/gulpfile.*",
    "**/Rakefile",
    "**/mix.exs",
    "**/Package.swift",
    "**/CMakeLists.txt",
    "**/Makefile",
]

DEFAULT_DRIFT_PATTERNS: List[str] = [
    "**/setupTests.*",
    "**/jest.setup.*",
    "**/vitest.setup.*",
]

DEFAULT_SUPPORT_PATTERNS: List[str] = [
    "jest.config.*",
    "vitest.config.*",
    "playwright.config.*",
    "pytest.ini",
    "**/conftest.py",
    "**/__snapshots__/**",
    "**/__mocks__/**",
    "**/fixtures/**",
    "**/testdata/**",
]


class DriftRefusalError(Exception):
    """Raised when a drift command is refused per docs/state-machine.md §3.6."""

    def __init__(self, message: str, row: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.row = row
        self.code = 1


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
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


def _matches_config_wildcard(norm_path: str) -> bool:
    """Check if path or its basename matches *.config.*, *.conf.*, or *.workspace.*."""
    filename = Path(norm_path).name
    wildcards = ("*.config.*", "*.conf.*", "*.workspace.*")
    for pat in wildcards:
        if fnmatch.fnmatch(filename, pat) or fnmatch.fnmatch(norm_path, pat):
            return True
        if fnmatch.fnmatch(norm_path, f"**/{pat}"):
            return True
    return False


def _is_agent_config(
    norm_path: str,
    repo_root: Path,
    config: Optional[dict] = None,
) -> bool:
    """Check if path is classified as agent-config."""
    # Fast path: known names
    base = Path(norm_path).name.lower()
    if base in ("agents.md", "gemini.md"):
        return True

    parts = Path(norm_path).parts
    if parts:
        first = parts[0].lower()
        if first in (".agents", ".gemini"):
            # Exclude lintel personas (.agents/agents/lintel-*.md) which are class lintel
            if len(parts) >= 3 and parts[0] == ".agents" and parts[1] == "agents":
                if parts[2].startswith("lintel-") and parts[2].endswith(".md"):
                    return False
            return True

    # Use classify_path
    try:
        p_class = paths.classify_path(norm_path, config=config, repo_root=repo_root)
        if str(p_class) == "agent-config":
            return True
    except Exception:
        pass

    return False


def _get_scanned_tokens(
    repo_root: Path,
    config: dict,
    head_sha: Optional[str] = None,
) -> Tuple[Set[str], List[str]]:
    """Scan .lintel/scripts/*, build files, and support runner configs for referenced paths.

    Returns:
        (tokens_set, full_texts)
    """
    files_to_read: List[Path] = []

    # 1. Files in .lintel/scripts/*
    scripts_dir = repo_root / ".lintel" / "scripts"
    if scripts_dir.is_dir():
        for f in scripts_dir.glob("*"):
            if f.is_file():
                files_to_read.append(f)

    # 2. Build files matching paths.build
    raw_build = config.get("paths", {}).get("build") or DEFAULT_BUILD_PATTERNS
    build_patterns = [raw_build] if isinstance(raw_build, str) else list(raw_build)
    for root_dir, _, filenames in os.walk(repo_root):
        rel_dir = os.path.relpath(root_dir, repo_root)
        if rel_dir == ".git" or rel_dir.startswith(".git/"):
            continue
        if rel_dir == ".lintel/tasks" or rel_dir.startswith(".lintel/tasks/"):
            continue
        for fn in filenames:
            rel_file = fn if rel_dir == "." else f"{rel_dir}/{fn}"
            norm_rel = rel_file.replace("\\", "/")
            if paths.match_globs(norm_rel, build_patterns):
                files_to_read.append(repo_root / norm_rel)

    # 3. Support test group runner configs
    raw_support = (
        config.get("paths", {})
        .get("tests", {})
        .get("support", {})
        .get("paths", DEFAULT_SUPPORT_PATTERNS)
    )
    support_patterns = [raw_support] if isinstance(raw_support, str) else list(raw_support)
    for root_dir, _, filenames in os.walk(repo_root):
        rel_dir = os.path.relpath(root_dir, repo_root)
        if rel_dir == ".git" or rel_dir.startswith(".git/"):
            continue
        if rel_dir == ".lintel/tasks" or rel_dir.startswith(".lintel/tasks/"):
            continue
        for fn in filenames:
            rel_file = fn if rel_dir == "." else f"{rel_dir}/{fn}"
            norm_rel = rel_file.replace("\\", "/")
            if paths.match_globs(norm_rel, support_patterns):
                files_to_read.append(repo_root / norm_rel)

    # Also check git head for .lintel/scripts if head_sha given
    git_texts: List[str] = []
    if head_sha:
        res = git.run_git(["ls-tree", "-r", "--name-only", head_sha, ".lintel/scripts"], repo_root, check=False)
        if res.returncode == 0 and res.stdout.strip():
            for line in res.stdout.splitlines():
                line = line.strip()
                if line:
                    cat_res = git.run_git(["show", f"{head_sha}:{line}"], repo_root, check=False)
                    if cat_res.returncode == 0:
                        git_texts.append(cat_res.stdout)

    tokens_set: Set[str] = set()
    full_texts: List[str] = list(git_texts)

    for fp in files_to_read:
        try:
            content = fp.read_text(encoding="utf-8", errors="ignore")
            full_texts.append(content)
        except Exception:
            continue

    for text in full_texts:
        # Split into tokens by common separators
        raw_tokens = re.split(r'[\s"\'`<>()[\]{},;=:]+', text)
        for tok in raw_tokens:
            tok = tok.strip()
            # Strip leading ./ or /
            while tok.startswith("./"):
                tok = tok[2:]
            tok = tok.lstrip("/")
            tok = tok.rstrip("./\\")
            if tok:
                tokens_set.add(tok)
                norm_tok = tok.replace("\\", "/")
                tokens_set.add(norm_tok)

    return tokens_set, full_texts


def _is_path_named_in_scanned_files(
    norm_path: str,
    tokens_set: Set[str],
    full_texts: Sequence[str],
) -> bool:
    """Check if norm_path is named in scanned scripts, build files, or runner configs."""
    if norm_path in tokens_set:
        return True

    # Check if any token ends with /norm_path (e.g. <rootDir>/scripts/test.py)
    suffix = "/" + norm_path
    for tok in tokens_set:
        if tok.endswith(suffix):
            return True

    # Best effort regex search in raw texts
    # Look for norm_path surrounded by non-path word characters or quotes/whitespace
    pattern = rf'(?:^|[\s"\'`<>(\[/]){re.escape(norm_path)}(?:$|[\s"\'`>)\]/])'
    for text in full_texts:
        if re.search(pattern, text):
            return True

    return False


def is_drift_file(
    path: str,
    repo_root: Path,
    config: dict,
    head_sha: Optional[str] = None,
) -> bool:
    """Implement the allow-list logic:

    Every changed file in the change set is drift unless it is plain source or docs.
    Plain source means a file matching `paths.source`,
    EXCEPT:
    - agent-config files (AGENTS.md, **/AGENTS.md, .agents/**, etc.)
    - dotfiles and dot-dirs (any path component beginning with '.')
    - *.config.*, *.conf.*, *.workspace.* wildcard matches on path or basename
    - paths.build files (matching config['paths']['build'] globs)
    - paths.drift files (matching config['paths']['drift'] globs)
    - repo files named in .lintel/scripts/*, build files, or support runner configs (token scan)
    - suspect tests (code files matching suspect test heuristics)
    - files classified as tests or other non-code classes

    If a file does not match paths.source, it is drift.
    If a file matches paths.source but matches any exception, it is drift.
    Only files matching paths.source with NO exceptions are plain source (not drift).
    """
    root = Path(repo_root).resolve()
    norm_path = paths.normalize_path(path, repo_root=root, resolve_symlinks=False)
    norm_path = norm_path.replace("\\", "/").strip("/")

    # 1. Dotfiles and dot-directories
    parts = Path(norm_path).parts
    if any(p.startswith(".") for p in parts):
        return True

    # 2. agent-config files
    if _is_agent_config(norm_path, repo_root=root, config=config):
        return True

    # 3. *.config.*, *.conf.*, *.workspace.* wildcard matches on path or basename
    if _matches_config_wildcard(norm_path):
        return True

    # 4. paths.build files
    raw_build = config.get("paths", {}).get("build") or DEFAULT_BUILD_PATTERNS
    build_patterns = [raw_build] if isinstance(raw_build, str) else list(raw_build)
    if paths.match_globs(norm_path, build_patterns):
        return True

    # 5. paths.drift files
    raw_drift = config.get("paths", {}).get("drift") or DEFAULT_DRIFT_PATTERNS
    drift_patterns = [raw_drift] if isinstance(raw_drift, str) else list(raw_drift)
    if paths.match_globs(norm_path, drift_patterns):
        return True

    # 6. Suspect tests
    try:
        if paths.is_suspect_test(norm_path, config=config, repo_root=root):
            return True
    except Exception:
        pass

    # 7. Check path classification
    # Only files in the 'code' class can ever be plain source.
    # Files in 'tests', 'lintel', 'living-docs', 'test-output', etc. are NOT plain source.
    try:
        p_class = paths.classify_path(norm_path, config=config, repo_root=root)
        if str(p_class) != "code":
            return True
    except Exception:
        return True

    # 8. Token scan: repo files named in .lintel/scripts/*, build files, or support runner configs
    tokens_set, full_texts = _get_scanned_tokens(root, config=config, head_sha=head_sha)
    if _is_path_named_in_scanned_files(norm_path, tokens_set, full_texts):
        return True

    # 9. Check paths.source
    raw_source = config.get("paths", {}).get("source") or DEFAULT_SOURCE_PATTERNS
    source_patterns = [raw_source] if isinstance(raw_source, str) else list(raw_source)
    if not paths.match_globs(norm_path, source_patterns):
        return True

    # File matches paths.source and matched NO exceptions -> plain source!
    return False


def get_drift_files(
    repo_root: Path,
    head_sha: str,
    config: dict,
) -> Dict[str, ChangeEntry]:
    """Obtain change set via git.get_change_set and return drift entries."""
    root = Path(repo_root).resolve()
    cs = git.get_change_set(root, head_sha, config=config)

    drift_entries: Dict[str, ChangeEntry] = {}
    for p, entry in cs.entries.items():
        if is_drift_file(p, root, config=config, head_sha=head_sha):
            drift_entries[p] = entry

    return drift_entries


def _compute_drift_diff(repo_root: Path, head_sha: str, rel_path: str) -> str:
    """Compute unified diff for a drift file against recorded head commit."""
    # Check head content
    in_head = False
    head_text = None
    proc = git.run_git(["show", f"{head_sha}:{rel_path}"], repo_root, check=False)
    if proc.returncode == 0:
        in_head = True
        head_text = proc.stdout

    # Check working tree content
    wt_file = repo_root / rel_path
    in_wt = wt_file.is_file()
    wt_text = None
    if in_wt:
        try:
            wt_text = wt_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            in_wt = False

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
            return "".join(diff)
    elif not in_head and in_wt:
        wt_lines = (wt_text or "").splitlines(keepends=True)
        diff = difflib.unified_diff(
            [],
            wt_lines,
            fromfile="/dev/null",
            tofile=f"b/{rel_path}",
        )
        return "".join(diff)
    elif in_head and not in_wt:
        head_lines = (head_text or "").splitlines(keepends=True)
        diff = difflib.unified_diff(
            head_lines,
            [],
            fromfile=f"a/{rel_path}",
            tofile="/dev/null",
        )
        return "".join(diff)
    return ""


def check_drift_acked(
    repo_root: Path,
    phase: Union[int, str],
    config: Optional[dict] = None,
) -> Tuple[bool, Dict[str, str], Dict[str, str]]:
    """Check if current change set's drift files match a drift --ack in acks.json.

    Returns:
        (matches, current_drift_map, acked_drift_map)
    """
    root = Path(repo_root).resolve()
    state_dir = root / ".lintel" / "state"

    task_info = caller.find_active_task(repo_root=root, state_dir=state_dir)
    if not task_info:
        return False, {}, {}
    task_id, task_data = task_info
    head_sha = task_data.get("head")
    if not head_sha:
        return False, {}, {}

    p_num = int(str(phase).lstrip("p")) if str(phase).lstrip("p").isdigit() else 1

    if config is None:
        head_cfg_res = git.run_git(["show", f"{head_sha}:.lintel/config.jsonc"], root, check=False)
        if head_cfg_res.returncode == 0:
            config = load_config(head_cfg_res.stdout, workspace_root=root)
        else:
            wt_cfg = root / ".lintel" / "config.jsonc"
            if wt_cfg.is_file():
                config = load_config(wt_cfg, workspace_root=root)
            else:
                config = load_config({}, workspace_root=root)

    drift_entries = get_drift_files(root, head_sha, config=config)
    current_map: Dict[str, str] = {
        p: (entry.blob if not entry.is_deleted() else "deleted")
        for p, entry in drift_entries.items()
    }

    acks_file = state_dir / task_id / "acks.json"
    acked_map: Dict[str, str] = {}
    if acks_file.is_file():
        try:
            acks_data = load_state_file(acks_file, doc_type="acks")
            acked_map = acks_data.get("drift", {}).get(str(p_num), {}).get("paths", {})
        except Exception:
            pass

    matches = (current_map == acked_map)
    return matches, current_map, acked_map


def drift(
    repo_root: Path,
    phase: Optional[Union[int, str]] = None,
    ack: bool = False,
    reject: Optional[str] = None,
    full: bool = False,
    skip_caller_check: bool = False,
    dialog_fn: Optional[Callable[..., str]] = None,
) -> Any:
    """Implement `lintel drift --phase N [--ack | --reject "<why>"] [--full]`.

    Operations:
    - Default `drift --phase N` (neither ack nor reject):
      - Anyone may run (no dialog check H0).
      - If `--full`: human-only (check dialog H0).
      - If no drift files: prints "No drift files."
      - If drift files exist:
        - If not `--full`: lists drift file paths, one per line (sorted).
        - If `--full`: shows unified diffs against task.head for all drift files.
    - `drift --phase N --ack`:
      - Human-only: requires dialog check H0.
      - NR check: refuse if run is pending or active.
      - Shows unified diffs for all drift files.
      - Records map path -> blob_id in acks.json under drift.<n>.paths.
      - Sets drift.<n>.at = _now_iso().
      - Updates acks.last_human.
      - Logs event drift to events.jsonl (T-AMD-51).
      - Next step: next step of current state.
    - `drift --phase N --reject "<why>"`:
      - Human-only: requires dialog check H0.
      - NR check: refuse if run is pending or active.
      - Adds blocker finding authored by human in loops.json under loops.dev.p<N>.findings.
      - Sets after_human_reject = True in loop (T-STL-10).
      - Transitions state to p<N>:W.dev (T-AMD-52).
      - Logs event reject to events.jsonl (T-AMD-52).
      - Next action: lintel invoke dev --phase p<N>.
    """
    root = Path(repo_root).resolve()
    state_dir = root / ".lintel" / "state"

    # Step 1: Active task check
    task_info = caller.find_active_task(repo_root=root, state_dir=state_dir)
    if not task_info:
        raise DriftRefusalError("Refused: no active task found", row="T-STG-09")
    task_id, task_data = task_info
    task_file = state_dir / task_id / "task.json"

    # Step 2: Phase resolution & validation
    pos = flow.get_position(task_data)
    stage = pos.stage
    if phase is not None:
        p_str = str(phase)
        if p_str.startswith("p") and p_str[1:].isdigit():
            p_num = int(p_str[1:])
        elif p_str.isdigit():
            p_num = int(p_str)
        else:
            raise DriftRefusalError(f"Invalid phase argument: {phase}", row="T-AMD-53")
    else:
        if pos.phase is not None:
            p_num = pos.phase
        elif stage.startswith("p") and stage[1:].isdigit():
            p_num = int(stage[1:])
        else:
            p_num = 1

    # Step 3: Guard check - outside DEV's turn after lock or inside amend cycle (T-AMD-53)
    # Check amend cycle
    if bool(task_data.get("cycle")):
        raise DriftRefusalError(
            "Refused: drift is not allowed inside an amend cycle (T-AMD-53)",
            row="T-AMD-53",
        )

    # Check phase locked
    phases_data = task_data.get("phases", {})
    p_info = phases_data.get(str(p_num), {})
    if not bool(p_info.get("lock")):
        raise DriftRefusalError(
            f"Refused: phase {p_num} is not locked; drift is only allowed during DEV's turn after lock (T-AMD-53)",
            row="T-AMD-53",
        )

    # Check phase delivered
    if bool(p_info.get("delivered")) or p_num in task_data.get("delivered_phases", []):
        raise DriftRefusalError(
            f"Refused: phase {p_num} has already been delivered (T-AMD-53)",
            row="T-AMD-53",
        )

    # Check DEV's turn
    if not flow.is_dev_turn(task_data):
        raise DriftRefusalError(
            "Refused: drift is only allowed during DEV's turn after lock (T-AMD-53)",
            row="T-AMD-53",
        )

    # Step 4: Caller dialog check for human-only operations
    is_human_op = ack or (reject is not None) or full
    if is_human_op and not skip_caller_check:
        cmd_tokens = ["drift", "--phase", str(p_num)]
        if ack:
            cmd_tokens.append("--ack")
        if reject is not None:
            cmd_tokens.extend(["--reject", reject])
        if full:
            cmd_tokens.append("--full")
        cmd_str = " ".join(cmd_tokens)
        dummy_args = argparse.Namespace(
            subcommand="drift",
            phase=str(p_num),
            ack=ack,
            reject=reject,
            full=full,
            task_id=task_id,
        )
        caller.check_caller(cmd_str, dummy_args, repo_root=root, dialog_fn=dialog_fn)

    # Step 5: NR check - refuse if run is pending or active (T-HUM-05) for --ack and --reject
    if ack or (reject is not None):
        if is_run_pending_or_active(task_data, state_dir, task_id):
            raise DriftRefusalError(
                "Refused: a run is pending or active; lintel task wait (T-HUM-05)",
                row="T-HUM-05",
            )

    # Step 6: Load configuration from recorded head
    head_sha = task_data.get("head")
    if not head_sha:
        raise DriftRefusalError("Refused: task has no recorded head commit", row="T-STG-09")

    head_cfg_res = git.run_git(["show", f"{head_sha}:.lintel/config.jsonc"], root, check=False)
    if head_cfg_res.returncode == 0:
        cfg = load_config(head_cfg_res.stdout, workspace_root=root)
    else:
        wt_cfg = root / ".lintel" / "config.jsonc"
        if wt_cfg.is_file():
            cfg = load_config(wt_cfg, workspace_root=root)
        else:
            cfg = load_config({}, workspace_root=root)

    # Compute drift files
    drift_entries = get_drift_files(root, head_sha, config=cfg)
    sorted_paths = sorted(drift_entries.keys())

    # Step 7: Operation dispatch

    # --- Operation A: drift --phase N --reject "<why>" (T-AMD-52) ---
    if reject is not None:
        why = reject
        # Write rejection file in DEV folder
        folder_rel = f".lintel/tasks/{task_id}/dev/p{p_num}-reject"
        run_folder = root / folder_rel
        run_folder.mkdir(parents=True, exist_ok=True)
        rejection_file = run_folder / "rejection.md"
        rejection_file.write_text(why + "\n", encoding="utf-8")
        rejection_rel_path = f"{folder_rel}/rejection.md"

        # Record blocker finding authored by human in loops.json under loops.dev.p<N>.findings
        # and set after_human_reject = True
        loops.record_human_reject(
            state_dir=state_dir,
            task_id=task_id,
            writer="dev",
            stage=f"p{p_num}",
            reason=why,
            file_path=rejection_rel_path,
            where="drift",
        )

        with state_lock(state_dir):
            task_data = load_state_file(task_file, doc_type="task")
            # Transition state to p<N>:W.dev (T-AMD-52)
            flow.set_position(task_data, stage=f"p{p_num}", code="W.dev", phase=p_num, fr=False, run=None)
            next_action = flow.format_next_action(task_data)
            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            validate_state_doc("task", task_data, file_path=task_file)
            atomic_write_json(task_file, task_data)

            # Log event reject to events.jsonl (row T-AMD-52)
            event_rec = events.create_event(
                step=event_seq,
                event="reject",
                stage=f"p{p_num}",
                role="dev",
                row="T-AMD-52",
            )
            events.append_event(
                state_dir=state_dir,
                task_id=task_id,
                event_data=event_rec,
                task_dir=root / ".lintel" / "tasks" / task_id,
            )

            # Update acks.last_human
            acks_file = state_dir / task_id / "acks.json"
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
                "command": f'drift --reject "{why}"',
            }
            validate_state_doc("acks", acks_data, file_path=acks_file)
            atomic_write_json(acks_file, acks_data)

        reviews.update_progress_md(root, task_data, next_action)
        sys.stdout.write(f"Rejected drift for phase {p_num}: {why}\n{next_action}\n")
        return next_action

    # --- Operation B: drift --phase N --ack (T-AMD-51) ---
    if ack:
        # Compute and print unified diffs for all drift files
        diffs_text = ""
        for p in sorted_paths:
            d = _compute_drift_diff(root, head_sha, p)
            if d:
                diffs_text += d

        if diffs_text:
            sys.stdout.write(diffs_text)
        elif not sorted_paths:
            sys.stdout.write("No drift files to acknowledge.\n")

        # Record map path -> blob_id in acks.json under drift.<n>.paths
        drift_blob_map: Dict[str, str] = {
            p: (entry.blob if not entry.is_deleted() else "deleted")
            for p, entry in drift_entries.items()
        }

        with state_lock(state_dir):
            task_data = load_state_file(task_file, doc_type="task")
            event_seq = task_data.get("event_seq", 0) + 1
            task_data["event_seq"] = event_seq
            validate_state_doc("task", task_data, file_path=task_file)
            atomic_write_json(task_file, task_data)

            acks_file = state_dir / task_id / "acks.json"
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

            acks_data.setdefault("drift", {})[str(p_num)] = {
                "paths": drift_blob_map,
                "at": _now_iso(),
            }
            acks_data["last_human"] = {
                "at": _now_iso(),
                "event_seq": event_seq,
                "command": "drift --ack",
            }
            validate_state_doc("acks", acks_data, file_path=acks_file)
            atomic_write_json(acks_file, acks_data)

            # Log event drift-ack to events.jsonl (row T-AMD-51)
            event_rec = events.create_event(
                step=event_seq,
                event="drift-ack",
                stage=f"p{p_num}",
                role="dev",
                row="T-AMD-51",
            )
            events.append_event(
                state_dir=state_dir,
                task_id=task_id,
                event_data=event_rec,
                task_dir=root / ".lintel" / "tasks" / task_id,
            )

        next_action = flow.format_next_action(task_data)
        reviews.update_progress_md(root, task_data, next_action)
        sys.stdout.write(f"Acknowledged drift files for phase {p_num}.\nNext action: {next_action}\n")
        return {
            "paths": drift_blob_map,
            "next_action": next_action,
        }

    # --- Operation C: Default drift --phase N [--full] (T-AMD-50) ---
    if not sorted_paths:
        sys.stdout.write("No drift files.\n")
        return {} if full else []

    if full:
        # Show unified diffs against task.head for all drift files
        diffs_by_path: Dict[str, str] = {}
        full_diff_text = ""
        for p in sorted_paths:
            d = _compute_drift_diff(root, head_sha, p)
            if d:
                diffs_by_path[p] = d
                full_diff_text += d

        sys.stdout.write(full_diff_text)
        return diffs_by_path
    else:
        # List drift file paths, one per line (sorted)
        for p in sorted_paths:
            sys.stdout.write(f"{p}\n")
        return sorted_paths
