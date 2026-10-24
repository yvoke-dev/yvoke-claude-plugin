"""Gate runner in git worktree (M1a-10).

Executes the candidate commit in an isolated git worktree under <tmp>,
running setup, test groups in config order, and teardown scripts.
Enforces process group isolation, output capping, test mutation checks,
leftover sweeping with owner proof, and signal trapping.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

try:
    from lintel.config import ConfigError, load_config
    from lintel.paths import PathClass, assign_test_group, classify_path, normalize_path
    from lintel.state import (
        StateError,
        atomic_write_json,
        get_gate_owner_path,
        get_process_start_time,
        is_pid_alive,
        is_process_group_ours,
        load_state_file,
        read_gate_owner,
        remove_gate_owner,
        resolve_state_root,
        running_gate,
        write_gate_owner,
    )
    from lintel.git import GitError, create_commit, get_change_set, get_ref, run_git
except ImportError:
    from .config import ConfigError, load_config
    from .paths import PathClass, assign_test_group, classify_path, normalize_path
    from .state import (
        StateError,
        atomic_write_json,
        get_gate_owner_path,
        get_process_start_time,
        is_pid_alive,
        is_process_group_ours,
        load_state_file,
        read_gate_owner,
        remove_gate_owner,
        resolve_state_root,
        running_gate,
        write_gate_owner,
    )
    from .git import GitError, create_commit, get_change_set, get_ref, run_git


DEFAULT_TIMEOUT_SECONDS: float = 600.0
DEFAULT_MAX_OUTPUT_BYTES: int = 1_048_576  # 1 MB


class GateError(Exception):
    """Raised when gate initialization or execution fails."""
    pass


class GateMode:
    STOP_AT_FIRST_RED = "stop-at-first-red"
    RUN_ALL = "run-all"


@dataclass
class GateGroupResult:
    """Execution result for a single script (group, setup, or teardown)."""
    name: str
    status: str  # "green", "red", "not-run", "no-tests"
    exit_code: Optional[int]
    duration_seconds: float
    output: str
    timed_out: bool = False
    test_files_count: int = 0

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


@dataclass
class GateResult:
    """Overall outcome of a gate run."""
    command: str
    mode: str
    candidate_commit: str
    candidate_tree: str
    overall_green: bool
    tests_clean: bool
    tests_violations: List[str]
    groups: Dict[str, GateGroupResult]
    setup_result: Optional[GateGroupResult] = None
    teardown_result: Optional[GateGroupResult] = None
    log_path: Optional[str] = None
    log_content: str = ""
    worktree_clean: bool = True
    worktree_violations: List[str] = field(default_factory=list)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# 1. Candidate Commit & Worktree Helpers
# ---------------------------------------------------------------------------

def resolve_tmp_root(tmp_dir: Optional[Union[str, Path]] = None) -> Path:
    """Resolve <tmp> directory with symlinks fully resolved."""
    if tmp_dir:
        return Path(tmp_dir).resolve()
    env_tmp = os.environ.get("TMPDIR")
    if env_tmp:
        return Path(env_tmp).resolve()
    return Path(tempfile.gettempdir()).resolve()


def create_candidate_commit(
    repo_root: Union[str, Path],
    parent_commit: Optional[str] = None,
    tmp_dir: Optional[Union[str, Path]] = None,
    config: Optional[dict] = None,
    message: str = "lintel: candidate commit",
) -> Tuple[str, str]:
    """Build candidate commit snapshotting working tree into temporary index.
    
    Parent: recorded head or HEAD (for baseline).
    Tree: working tree snapshot via get_change_set.
    Fixed identity: Lintel <lintel@noreply.local>, --no-gpg-sign.
    Returns (candidate_commit_sha, tree_id).
    """
    repo = Path(repo_root).resolve()

    if parent_commit is None:
        parent_sha = get_ref(repo, "HEAD")
        if not parent_sha:
            proc = run_git(["rev-parse", "HEAD"], repo, check=True)
            parent_sha = proc.stdout.strip()
    else:
        parent_sha = parent_commit

    change_set = get_change_set(
        repo_root=repo,
        head_commit=parent_sha,
        tmp_dir=tmp_dir,
        config=config,
    )
    tree_id = change_set.tree_id

    commit_sha = create_commit(
        repo_root=repo,
        tree_id=tree_id,
        parent=parent_sha,
        message=message,
        author_role="lintel",
    )

    return commit_sha, tree_id


def create_gate_worktree(
    repo_root: Union[str, Path],
    commit_sha: str,
    tmp_root: Optional[Union[str, Path]] = None,
) -> Path:
    """Create detached worktree under <tmp> for the candidate commit.
    
    Uses tempfile.mkdtemp(prefix='lintel-gate-', dir=tmp_root).
    Bypasses project post-checkout hooks with core.hooksPath=/dev/null.
    Initializes submodules if .gitmodules exists in candidate.
    """
    repo = Path(repo_root).resolve()
    tmp_dir = resolve_tmp_root(tmp_root)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    gate_dir_raw = tempfile.mkdtemp(prefix="lintel-gate-", dir=str(tmp_dir))
    gate_dir = Path(gate_dir_raw).resolve()

    try:
        run_git(
            [
                "-c",
                "core.hooksPath=/dev/null",
                "worktree",
                "add",
                "--detach",
                str(gate_dir),
                commit_sha,
            ],
            repo,
            check=True,
        )

        gitmodules_file = gate_dir / ".gitmodules"
        if gitmodules_file.is_file():
            run_git(
                [
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "protocol.file.allow=always",
                    "submodule",
                    "update",
                    "--init",
                ],
                gate_dir,
                check=True,
            )

        return gate_dir
    except Exception as e:
        if gate_dir.exists():
            shutil.rmtree(gate_dir, ignore_errors=True)
        raise GateError(f"Failed to create gate worktree at {gate_dir}: {e}") from e


def remove_gate_worktree(
    gate_dir: Union[str, Path],
    repo_root: Union[str, Path],
) -> None:
    """Safely remove gate worktree and owner record."""
    gdir = Path(gate_dir).resolve()
    repo = Path(repo_root).resolve()

    try:
        run_git(
            ["-c", "core.hooksPath=/dev/null", "worktree", "remove", "--force", str(gdir)],
            repo,
            check=False,
        )
    except Exception:
        pass

    if gdir.exists():
        shutil.rmtree(gdir, ignore_errors=True)

    try:
        run_git(["worktree", "prune"], repo, check=False)
    except Exception:
        pass

    remove_gate_owner(gdir)


# ---------------------------------------------------------------------------
# 2. Leftover Sweep
# ---------------------------------------------------------------------------

def sweep_leftover_gate_worktrees(
    tmp_root: Optional[Union[str, Path]] = None,
    repo_root: Optional[Union[str, Path]] = None,
) -> Dict[str, List[Any]]:
    """Scan <tmp> for leftover gate worktrees and remove proven-own leftovers.
    
    Safety rules:
    - Scans direct children of <tmp> named 'lintel-gate-*'.
    - Only removes if there is a corresponding '.owner.json' matching this repo.
    - Owner PID must be dead (not is_pid_alive(opid)).
    - If recorded process group is still ours per M1a-07, signal it (SIGTERM, 3s grace, SIGKILL).
      If recycled (not ours), do NOT signal!
    - Removes worktree (git worktree remove --force and rmtree), unlinks .owner.json, and runs git worktree prune.
    - Crucial safety: A directory with no owner record, or whose owner is alive, or for a different
      repo, is NEVER deleted (reported/logged only). A bare directory name match alone is never deleted!
    """
    tmp_dir = resolve_tmp_root(tmp_root)
    repo = Path(repo_root).resolve() if repo_root else None

    result: Dict[str, List[Any]] = {
        "swept": [],
        "skipped_owner_alive": [],
        "skipped_different_repo": [],
        "skipped_no_record": [],
        "skipped_recycled_pgid": [],
        "signalled_pgids": [],
    }

    if repo:
        try:
            run_git(["worktree", "prune"], repo, check=False)
        except Exception:
            pass

    if not tmp_dir.is_dir():
        return result

    # 1. Scan for owner records: <tmp>/lintel-gate-*.owner.json
    for owner_file in tmp_dir.glob("lintel-gate-*.owner.json"):
        gate_name = owner_file.name[:-len(".owner.json")]
        gate_dir = tmp_dir / gate_name

        try:
            rec = read_gate_owner(owner_file)
        except Exception:
            continue

        rec_repo_str = rec.get("repo")
        if not rec_repo_str:
            continue
        rec_repo = Path(rec_repo_str).resolve()
        if repo and rec_repo != repo:
            result["skipped_different_repo"].append(str(gate_dir))
            continue

        opid = rec.get("pid")
        if opid is not None and is_pid_alive(opid):
            result["skipped_owner_alive"].append(str(gate_dir))
            continue

        # Owner process is dead: proven-own leftover!
        opg = rec.get("pgid")
        if opg is not None and opg > 0:
            expected_start = rec.get("proc_start") or rec.get("lstart") or ""
            if is_process_group_ours(opg, leader_pid=opg, expected_start=expected_start, gate_dir=gate_dir):
                # Process group is still ours: terminate it
                try:
                    os.killpg(opg, signal.SIGTERM)
                    deadline = time.time() + 3.0
                    while time.time() < deadline:
                        try:
                            os.killpg(opg, 0)
                            time.sleep(0.05)
                        except OSError:
                            break
                    else:
                        try:
                            os.killpg(opg, signal.SIGKILL)
                        except OSError:
                            pass
                    result["signalled_pgids"].append(opg)
                except OSError:
                    pass
            else:
                # Recycled process group: do NOT signal!
                try:
                    os.killpg(opg, 0)
                    result["skipped_recycled_pgid"].append(opg)
                except OSError:
                    pass

        # Remove worktree directory
        target_repo = repo or rec_repo
        try:
            run_git(
                ["-c", "core.hooksPath=/dev/null", "worktree", "remove", "--force", str(gate_dir)],
                target_repo,
                check=False,
            )
        except Exception:
            pass

        if gate_dir.exists():
            shutil.rmtree(gate_dir, ignore_errors=True)

        owner_file.unlink(missing_ok=True)

        try:
            run_git(["worktree", "prune"], target_repo, check=False)
        except Exception:
            pass

        result["swept"].append(str(gate_dir))

    # 2. Check direct children matching lintel-gate-* without owner record
    for child in tmp_dir.glob("lintel-gate-*"):
        if child.name.endswith(".owner.json"):
            continue
        owner_file = tmp_dir / f"{child.name}.owner.json"
        if not owner_file.is_file():
            result["skipped_no_record"].append(str(child))

    return result


# ---------------------------------------------------------------------------
# 3. Script Execution with Process Group Isolation & Output Capping
# ---------------------------------------------------------------------------

def run_script_with_timeout(
    cmd: Sequence[str],
    cwd: Union[str, Path],
    env: Dict[str, str],
    timeout_seconds: float,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    on_proc_start: Optional[Callable[[subprocess.Popen], None]] = None,
) -> Tuple[int, str, bool, float]:
    """Execute script in its own process group with a timeout.
    
    Enforces:
    - start_new_session=True (own process group)
    - on timeout: kill process group (SIGTERM, wait 3s, SIGKILL)
    - output capped at max_output_bytes (tail kept)
    - exit codes outside 0..3 and timeouts recorded as 2
    
    Returns (recorded_exit_code, captured_output, timed_out, duration_seconds).
    """
    start_time = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    pgid = proc.pid

    if on_proc_start is not None:
        try:
            on_proc_start(proc)
        except Exception:
            pass

    timed_out = False
    raw_output = b""

    try:
        stdout_data, _ = proc.communicate(timeout=timeout_seconds)
        raw_output = stdout_data or b""
    except subprocess.TimeoutExpired:
        timed_out = True
        # Terminate process group
        try:
            os.killpg(pgid, signal.SIGTERM)
            deadline = time.time() + 3.0
            while time.time() < deadline:
                try:
                    os.killpg(pgid, 0)
                    time.sleep(0.05)
                except OSError:
                    break
            else:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except OSError:
                    pass
        except OSError:
            pass

        try:
            stdout_data, _ = proc.communicate(timeout=2.0)
            raw_output = stdout_data or b""
        except Exception:
            try:
                proc.kill()
            except OSError:
                pass
            raw_output = b""

    duration = time.time() - start_time

    # Output cap (keep tail)
    if len(raw_output) > max_output_bytes:
        raw_output = b"[... output truncated ...]\n" + raw_output[-max_output_bytes:]

    output_str = raw_output.decode("utf-8", errors="replace")

    # Exit code normalization: outside 0..3 and timeout -> 2
    if timed_out:
        recorded_code = 2
    else:
        raw_code = proc.returncode
        if raw_code in (0, 1, 2, 3):
            recorded_code = raw_code
        else:
            recorded_code = 2

    return recorded_code, output_str, timed_out, duration


# ---------------------------------------------------------------------------
# 4. Green Checks & Tests Alone Verification
# ---------------------------------------------------------------------------

def count_group_test_files(
    gate_dir: Union[str, Path],
    group: str,
    config: Optional[dict] = None,
) -> int:
    """Count test files belonging to a specific test group in the candidate tree."""
    gdir = Path(gate_dir).resolve()
    proc = run_git(["ls-files", "-z"], gdir, check=True)
    tracked_files = proc.stdout.split("\0")

    cfg = config
    if cfg is None:
        cfg_path = gdir / ".lintel" / "config.jsonc"
        if cfg_path.is_file():
            try:
                cfg = load_config(cfg_path.read_text(encoding="utf-8"), workspace_root=gdir)
            except Exception:
                cfg = load_config({}, workspace_root=gdir)
        else:
            cfg = load_config({}, workspace_root=gdir)

    count = 0
    for f in tracked_files:
        if not f:
            continue
        cls = classify_path(f, config=cfg, repo_root=gdir, is_tracked=True)
        if str(cls) == "tests":
            grp = assign_test_group(f, config=cfg, repo_root=gdir)
            if grp == group:
                count += 1
    return count


def check_tests_left_alone(
    gate_dir: Union[str, Path],
    config: Optional[dict] = None,
) -> Tuple[bool, List[str]]:
    """Verify that git status in gate worktree shows no modified or deleted tracked file
    and no new untracked file in a tests path.
    
    Returns (is_clean, violations_list).
    """
    gdir = Path(gate_dir).resolve()
    proc = run_git(["status", "--porcelain", "-uall"], gdir, check=True)

    cfg = config
    if cfg is None:
        cfg_path = gdir / ".lintel" / "config.jsonc"
        if cfg_path.is_file():
            try:
                cfg = load_config(cfg_path.read_text(encoding="utf-8"), workspace_root=gdir)
            except Exception:
                cfg = load_config({}, workspace_root=gdir)
        else:
            cfg = load_config({}, workspace_root=gdir)

    violations: List[str] = []

    for line in proc.stdout.splitlines():
        if not line:
            continue
        code = line[:2]
        path_str = line[3:].strip()
        if path_str.startswith('"') and path_str.endswith('"'):
            path_str = path_str[1:-1]

        if code == "??":
            # Untracked file: check if it belongs to tests
            cls = classify_path(path_str, config=cfg, repo_root=gdir, is_tracked=False)
            if str(cls) == "tests" or cls.base_class == "tests":
                violations.append(f"new test file created: {path_str}")
        else:
            # Tracked file modified or deleted
            violations.append(f"tracked file modified or deleted ({code.strip()}): {path_str}")

    return len(violations) == 0, violations


def check_worktree_clean(gate_dir: Union[str, Path]) -> Tuple[bool, List[str]]:
    """Verify that git status in gate worktree shows no modified tracked files
    and no untracked non-ignored files (PLAN §3.4, docs/state-machine.md T-STG-07).
    
    Returns (is_clean, violations_list).
    """
    gdir = Path(gate_dir).resolve()
    proc = run_git(["status", "--porcelain", "-uall"], gdir, check=True)
    violations: List[str] = []

    for line in proc.stdout.splitlines():
        if not line:
            continue
        code = line[:2]
        path_str = line[3:].strip()
        if path_str.startswith('"') and path_str.endswith('"'):
            path_str = path_str[1:-1]
        violations.append(f"{code.strip()}: {path_str}")

    return len(violations) == 0, violations


# ---------------------------------------------------------------------------
# 5. Gate Runner Main Entrypoint
# ---------------------------------------------------------------------------

def run_gate(
    repo_root: Union[str, Path],
    command: str = "check",
    mode: Optional[str] = None,
    parent_commit: Optional[str] = None,
    candidate_commit: Optional[str] = None,
    tmp_root: Optional[Union[str, Path]] = None,
    log_path: Optional[Union[str, Path]] = None,
    reports_dir: Optional[Union[str, Path]] = None,
    state_dir: Optional[Union[str, Path]] = None,
    task_id: Optional[str] = None,
    phase: Optional[int] = None,
    run_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    config: Optional[dict] = None,
) -> GateResult:
    """Execute gate run in an isolated worktree under <tmp>.
    
    Steps:
    1. Sweep leftover gate worktrees under <tmp>.
    2. Determine candidate commit (create from working tree snapshot if not given).
    3. Create detached worktree under <tmp> bypassing hooks.
    4. Write owner record outside worktree.
    5. Trap SIGTERM/SIGINT/SIGHUP to kill test process group and clean worktree.
    6. Run setup.sh if present.
    7. Run each test group script (.lintel/scripts/test-<group>.sh) in config order.
       Stop-at-first-red mode (check, deliver) stops after first non-green group.
       Run-all mode (lock, baseline) executes all groups.
    8. Run teardown.sh always if present.
    9. Run "run left the tests alone" verification.
    10. Write log, copy test reports, remove worktree and owner record.
    """
    repo = Path(repo_root).resolve()
    resolved_tmp = resolve_tmp_root(tmp_root)

    # 1. Leftover sweep
    sweep_leftover_gate_worktrees(tmp_root=resolved_tmp, repo_root=repo)

    # Infer mode if not specified
    if mode is None:
        if command in ("check", "deliver"):
            exec_mode = GateMode.STOP_AT_FIRST_RED
        else:
            exec_mode = GateMode.RUN_ALL
    else:
        exec_mode = mode

    # Load configuration
    cfg = config
    if cfg is None:
        cfg_file = repo / ".lintel" / "config.jsonc"
        if cfg_file.is_file():
            try:
                cfg = load_config(cfg_file.read_text(encoding="utf-8"), workspace_root=repo)
            except Exception:
                cfg = load_config({}, workspace_root=repo)
        else:
            cfg = load_config({}, workspace_root=repo)

    # Resolve timeouts
    workflow_cfg = cfg.get("workflow", {})
    default_timeout = float(workflow_cfg.get("testTimeoutSeconds", DEFAULT_TIMEOUT_SECONDS))

    # 2. Candidate commit
    if candidate_commit is None:
        cand_commit, cand_tree = create_candidate_commit(
            repo_root=repo,
            parent_commit=parent_commit,
            tmp_dir=resolved_tmp,
            config=cfg,
            message=f"lintel: candidate commit for {command}",
        )
    else:
        cand_commit = candidate_commit
        proc_tree = run_git(["rev-parse", f"{cand_commit}^{{tree}}"], repo, check=True)
        cand_tree = proc_tree.stdout.strip()

    # Resolve log path
    if log_path is None:
        if task_id:
            phase_suffix = f"-p{phase}" if phase is not None else ""
            log_file = repo / ".lintel" / "tasks" / task_id / "gate" / f"{command}{phase_suffix}-1.log"
        else:
            log_file = repo / "gate" / f"{command}.log"
    else:
        lp = Path(log_path)
        log_file = lp if lp.is_absolute() else (repo / lp)

    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Resolve state directory
    st_dir = None
    if state_dir:
        st_dir = resolve_state_root(state_dir)

    # 3. Create gate worktree
    gate_dir = create_gate_worktree(
        repo_root=repo,
        commit_sha=cand_commit,
        tmp_root=resolved_tmp,
    )

    # 4. Write initial owner record
    my_pid = os.getpid()
    write_gate_owner(
        gate_dir=gate_dir,
        repo=repo,
        pid=my_pid,
        pgid=None,
        proc_start=None,
    )

    # 5. Trap management
    active_pgid: Optional[int] = None
    old_handlers: Dict[int, Any] = {}

    def _trap_handler(signum: int, frame: Any) -> None:
        if active_pgid is not None:
            try:
                os.killpg(active_pgid, signal.SIGTERM)
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    try:
                        os.killpg(active_pgid, 0)
                        time.sleep(0.05)
                    except OSError:
                        break
                else:
                    try:
                        os.killpg(active_pgid, signal.SIGKILL)
                    except OSError:
                        pass
            except OSError:
                pass

        try:
            remove_gate_worktree(gate_dir, repo)
        except Exception:
            pass

        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                old_handlers[sig] = signal.signal(sig, _trap_handler)
            except (ValueError, OSError):
                pass

    log_lines: List[str] = [
        "=" * 70,
        f"LINTEL GATE RUN: {command.upper()}",
        f"Mode:             {exec_mode}",
        f"Candidate commit: {cand_commit}",
        f"Candidate tree:   {cand_tree}",
        f"Worktree:         {gate_dir}",
        f"Started at:       {datetime.now(timezone.utc).isoformat()}",
        "=" * 70,
        "",
    ]

    setup_res: Optional[GateGroupResult] = None
    teardown_res: Optional[GateGroupResult] = None
    group_results: Dict[str, GateGroupResult] = {}
    tests_clean = True
    tests_violations: List[str] = []
    worktree_clean = True
    worktree_violations: List[str] = []

    def on_proc_start_cb(proc: subprocess.Popen) -> None:
        nonlocal active_pgid
        active_pgid = proc.pid
        lstart = get_process_start_time(proc.pid) or ""
        write_gate_owner(
            gate_dir=gate_dir,
            repo=repo,
            pid=my_pid,
            pgid=proc.pid,
            proc_start=lstart,
        )

    def on_proc_end() -> None:
        nonlocal active_pgid
        active_pgid = None
        write_gate_owner(
            gate_dir=gate_dir,
            repo=repo,
            pid=my_pid,
            pgid=None,
            proc_start=None,
        )

    # Inner execution logic
    def _execute() -> None:
        nonlocal setup_res, teardown_res, tests_clean, tests_violations, worktree_clean, worktree_violations

        # 6. setup.sh if present
        setup_script = gate_dir / ".lintel" / "scripts" / "setup.sh"
        if setup_script.is_file():
            log_lines.append("--- SETUP (.lintel/scripts/setup.sh) ---")
            setup_env = {**os.environ, "LINTEL_GATE": "1"}
            setup_env.pop("LINTEL_TEST_GROUP", None)
            code, out, tout, dur = run_script_with_timeout(
                cmd=["/bin/sh", str(setup_script)],
                cwd=gate_dir,
                env=setup_env,
                timeout_seconds=default_timeout,
                max_output_bytes=DEFAULT_MAX_OUTPUT_BYTES,
                on_proc_start=on_proc_start_cb,
            )
            on_proc_end()
            setup_status = "green" if code == 0 else "red"
            setup_res = GateGroupResult(
                name="setup",
                status=setup_status,
                exit_code=code,
                duration_seconds=dur,
                output=out,
                timed_out=tout,
            )
            log_lines.append(f"Exit code: {code} ({setup_status}), Duration: {dur:.2f}s")
            if out:
                log_lines.append(out.rstrip())
            log_lines.append("")

            if code != 0:
                # Setup failure halts test groups
                log_lines.append("Setup failed: test groups skipped.")
                _run_teardown()
                return

        # 7. Test groups in config order
        configured_groups = cfg.get("paths", {}).get("tests", {})
        stop_further = False

        for grp_name, grp_cfg in configured_groups.items():
            if grp_name == "support" or grp_cfg is None:
                continue

            if stop_further:
                group_results[grp_name] = GateGroupResult(
                    name=grp_name,
                    status="not-run",
                    exit_code=None,
                    duration_seconds=0.0,
                    output="",
                )
                log_lines.append(f"--- TEST GROUP: {grp_name} (skipped: not-run) ---")
                log_lines.append("")
                continue

            grp_timeout = float(grp_cfg.get("timeoutSeconds", default_timeout)) if isinstance(grp_cfg, dict) else default_timeout
            script_path = gate_dir / ".lintel" / "scripts" / f"test-{grp_name}.sh"

            log_lines.append(f"--- TEST GROUP: {grp_name} (.lintel/scripts/test-{grp_name}.sh) ---")

            if not script_path.is_file():
                # Missing test script counts as absent -> exit 2
                group_results[grp_name] = GateGroupResult(
                    name=grp_name,
                    status="red",
                    exit_code=2,
                    duration_seconds=0.0,
                    output=f"Script {script_path} not found\n",
                )
                log_lines.append("Script not found: recorded exit 2 (red)")
                log_lines.append("")
                if exec_mode == GateMode.STOP_AT_FIRST_RED:
                    stop_further = True
                continue

            env = {
                **os.environ,
                "LINTEL_GATE": "1",
                "LINTEL_TEST_GROUP": grp_name,
            }

            code, out, tout, dur = run_script_with_timeout(
                cmd=["/bin/sh", str(script_path)],
                cwd=gate_dir,
                env=env,
                timeout_seconds=grp_timeout,
                max_output_bytes=DEFAULT_MAX_OUTPUT_BYTES,
                on_proc_start=on_proc_start_cb,
            )
            on_proc_end()

            # Determine group greenness
            test_files_count = count_group_test_files(gate_dir, grp_name, cfg)
            if code == 0:
                grp_status = "green"
            elif code == 3:
                if test_files_count == 0:
                    grp_status = "no-tests"
                else:
                    grp_status = "red"
            else:
                grp_status = "red"

            group_results[grp_name] = GateGroupResult(
                name=grp_name,
                status=grp_status,
                exit_code=code,
                duration_seconds=dur,
                output=out,
                timed_out=tout,
                test_files_count=test_files_count,
            )

            log_lines.append(
                f"Exit code: {code} (status: {grp_status}, test_files: {test_files_count}), Duration: {dur:.2f}s"
            )
            if out:
                log_lines.append(out.rstrip())
            log_lines.append("")

            if exec_mode == GateMode.STOP_AT_FIRST_RED and grp_status not in ("green", "no-tests"):
                stop_further = True

        # 8. Teardown script
        _run_teardown()

        # 9. "Run left the tests alone" verification & worktree clean check
        tests_clean, tests_violations = check_tests_left_alone(gate_dir, cfg)
        worktree_clean, worktree_violations = check_worktree_clean(gate_dir)
        log_lines.append("--- TESTS ALONE CHECK ---")
        if tests_clean:
            log_lines.append("Status: PASS (all tracked files untouched, no new test files)")
        else:
            log_lines.append("Status: FAILED")
            for v in tests_violations:
                log_lines.append(f"  Violation: {v}")
        log_lines.append("")
        log_lines.append("--- WORKTREE CLEAN CHECK ---")
        if worktree_clean:
            log_lines.append("Status: PASS (no modified tracked files or untracked non-ignored files)")
        else:
            log_lines.append("Status: FAILED")
            for v in worktree_violations:
                log_lines.append(f"  Violation: {v}")
        log_lines.append("")

    def _run_teardown() -> None:
        nonlocal teardown_res
        teardown_script = gate_dir / ".lintel" / "scripts" / "teardown.sh"
        if teardown_script.is_file():
            log_lines.append("--- TEARDOWN (.lintel/scripts/teardown.sh) ---")
            teardown_env = {**os.environ, "LINTEL_GATE": "1"}
            teardown_env.pop("LINTEL_TEST_GROUP", None)
            code, out, tout, dur = run_script_with_timeout(
                cmd=["/bin/sh", str(teardown_script)],
                cwd=gate_dir,
                env=teardown_env,
                timeout_seconds=default_timeout,
                max_output_bytes=DEFAULT_MAX_OUTPUT_BYTES,
                on_proc_start=on_proc_start_cb,
            )
            on_proc_end()
            # Teardown exit code is logged only
            teardown_res = GateGroupResult(
                name="teardown",
                status="logged",
                exit_code=code,
                duration_seconds=dur,
                output=out,
                timed_out=tout,
            )
            log_lines.append(f"Exit code: {code}, Duration: {dur:.2f}s")
            if out:
                log_lines.append(out.rstrip())
            log_lines.append("")

    # Run execution under running_gate if state_dir is available
    try:
        if st_dir:
            with running_gate(
                state_dir=st_dir,
                command=command,
                tree_id=cand_tree,
                log_path=str(log_file),
                bound_seconds=int(default_timeout),
                task_id=task_id,
                phase=phase,
                run_id=run_id,
                conversation_id=conversation_id,
                gate_dir=gate_dir,
            ):
                _execute()
        else:
            _execute()
    finally:
        # Restore trap handlers
        for sig, h in old_handlers.items():
            try:
                signal.signal(sig, h)
            except (ValueError, OSError):
                pass

        # 10. Copy reports if reports_dir is specified or configured
        if reports_dir:
            rep_dest = Path(reports_dir).resolve()
            rep_dest.mkdir(parents=True, exist_ok=True)
            report_globs = cfg.get("paths", {}).get("testReport")
            if not report_globs:
                report_globs = ["**/surefire-reports/*.xml", "**/failsafe-reports/*.xml", "**/test-results/**/*.xml"]
            elif isinstance(report_globs, str):
                report_globs = [report_globs]

            for g in report_globs:
                for match in gate_dir.glob(g):
                    if match.is_file():
                        rel = match.relative_to(gate_dir)
                        target = rep_dest / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(match, target)

        # Remove worktree and owner record
        try:
            remove_gate_worktree(gate_dir, repo)
        except Exception:
            pass

    # Determine overall greenness
    all_groups_green = True
    for grp_res in group_results.values():
        if grp_res.status not in ("green", "no-tests"):
            all_groups_green = False
            break

    setup_ok = (setup_res is None or setup_res.exit_code == 0)
    if command == "baseline":
        overall_green = bool(setup_ok and all_groups_green and tests_clean and worktree_clean)
    else:
        overall_green = bool(setup_ok and all_groups_green and tests_clean)

    # Summary section in log
    log_lines.append("--- SUMMARY ---")
    if setup_res:
        log_lines.append(f"setup:          exit {setup_res.exit_code} ({setup_res.status})")
    for gname, gres in group_results.items():
        log_lines.append(f"{gname:12}: {gres.status} (exit {gres.exit_code})")
    if teardown_res:
        log_lines.append(f"teardown:       exit {teardown_res.exit_code} (logged)")
    log_lines.append(f"tests_clean:    {'PASS' if tests_clean else 'FAILED'}")
    log_lines.append(f"worktree_clean: {'PASS' if worktree_clean else 'FAILED'}")
    log_lines.append(f"OVERALL:        {'GREEN' if overall_green else 'RED'}")
    log_lines.append("=" * 70)

    full_log_text = "\n".join(log_lines) + "\n"

    try:
        log_file.write_text(full_log_text, encoding="utf-8")
    except Exception:
        pass

    return GateResult(
        command=command,
        mode=exec_mode,
        candidate_commit=cand_commit,
        candidate_tree=cand_tree,
        overall_green=overall_green,
        tests_clean=tests_clean,
        tests_violations=tests_violations,
        groups=group_results,
        setup_result=setup_res,
        teardown_result=teardown_res,
        log_path=str(log_file),
        log_content=full_log_text,
        worktree_clean=worktree_clean,
        worktree_violations=worktree_violations,
    )
