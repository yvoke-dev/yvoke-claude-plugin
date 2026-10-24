"""Caller verification dialog and human-only command enforcement (M1a-11)."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

try:
    from lintel.state import StateError, resolve_state_root, validate_state_doc
except ImportError:
    from .state import StateError, resolve_state_root, validate_state_doc


class CallerRefusalError(SystemExit):
    """Raised when a caller check refuses a human-only command."""

    def __init__(
        self,
        message: str,
        outcome: str,
        record: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(1)
        self.message = message
        self.outcome = outcome
        self.record = record

    def __str__(self) -> str:
        return self.message


# Table of subcommands that are strictly human-only per PLAN §3.5
HUMAN_ONLY_COMMANDS: Set[str] = {
    "install",
    "init",
    "upgrade",
    "uninstall",
    "approve",
    "decide",
    "lock",
    "amend",
    "amend --config",
    "reset",
    "task finish",
    "task unstick --force",
}


def is_macos_gui_session() -> bool:
    """Check if running in a macOS Aqua GUI session and not in SSH."""
    if sys.platform != "darwin":
        return False
    if (
        os.environ.get("SSH_CLIENT")
        or os.environ.get("SSH_TTY")
        or os.environ.get("SSH_CONNECTION")
    ):
        return False
    try:
        proc = subprocess.run(
            ["launchctl", "managername"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=5,
        )
        return proc.returncode == 0 and proc.stdout.strip() == "Aqua"
    except Exception:
        return False


def format_command_for_audit(command_str: str) -> str:
    """Normalize command string to subcommand and options without free text."""
    s = command_str.strip()
    if s.startswith("./.lintel/bin/lintel "):
        s = s[len("./.lintel/bin/lintel ") :].strip()
    elif s.startswith(".lintel/bin/lintel "):
        s = s[len(".lintel/bin/lintel ") :].strip()
    elif s.startswith("lintel "):
        s = s[7:].strip()

    # Strip project argument after install
    s = re.sub(r"^(install)(\s+.*)$", r"\1", s)
    # Strip free text after --reject
    s = re.sub(r'--reject(\s+["\'][^"\']*["\']|(\s+(?!--)\S+)+)', "--reject", s)
    # Strip free text after --guidance
    s = re.sub(r'--guidance(\s+["\'][^"\']*["\']|(\s+(?!--)\S+)+)', "--guidance", s)
    # Strip free text after task start "<title>"
    s = re.sub(r'^(task\s+start)(\s+["\'][^"\']*["\']|\s+.*)$', r"\1", s)
    # Strip free text after answer <role> <qid> "<text>"
    s = re.sub(r'^(answer\s+\S+\s+\S+)(\s+["\'][^"\']*["\']|\s+.*)$', r"\1", s)
    return s.strip()


def show_caller_dialog(
    command_str: str,
    task_id: Optional[str] = None,
    timeout_seconds: int = 60,
) -> str:
    """Display confirmation dialog and return outcome.

    Outcomes:
      - 'ok': user clicked OK
      - 'not-me': user clicked Not me
      - 'gave-up': dialog timed out after timeout_seconds
      - 'canceled': user canceled (AppleScript error -128)
      - 'error': execution error
      - 'unavailable': no GUI session or missing tool
    """
    clean_cmd = command_str.strip()
    if clean_cmd.startswith("./.lintel/bin/lintel "):
        clean_cmd = clean_cmd[len("./.lintel/bin/lintel ") :].strip()
    elif clean_cmd.startswith(".lintel/bin/lintel "):
        clean_cmd = clean_cmd[len(".lintel/bin/lintel ") :].strip()

    if not clean_cmd.startswith("lintel ") and clean_cmd != "lintel":
        display_cmd = f"lintel {clean_cmd}"
    else:
        display_cmd = clean_cmd

    if task_id:
        if task_id in display_cmd:
            dialog_text = (
                f"Did you just run `{display_cmd}` in your own terminal? Click OK only if you did."
            )
        else:
            dialog_text = (
                f"Did you just run `{display_cmd}` for task {task_id} in your own terminal? Click OK only if you did."
            )
    else:
        dialog_text = (
            f"Did you just run `{display_cmd}` in your own terminal? Click OK only if you did."
        )

    if sys.platform == "darwin":
        if not is_macos_gui_session():
            return "unavailable"
        osascript_bin = shutil.which("osascript")
        if not osascript_bin:
            return "unavailable"

        escaped_text = (
            dialog_text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
        )
        script = (
            f'display dialog "{escaped_text}" '
            f'buttons {{"Not me", "OK"}} default button "Not me" '
            f"giving up after {int(timeout_seconds)}"
        )

        try:
            proc = subprocess.run(
                [osascript_bin, "-e", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout_seconds + 5,
            )
        except subprocess.TimeoutExpired:
            return "gave-up"
        except Exception:
            return "error"

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        if "gave up:true" in stdout:
            return "gave-up"
        if "button returned:OK" in stdout:
            return "ok"
        if "button returned:Not me" in stdout:
            return "not-me"
        if proc.returncode != 0:
            if "-128" in stderr or "User canceled" in stderr:
                return "canceled"
            return "error"
        return "error"

    elif sys.platform.startswith("linux"):
        if (
            os.environ.get("SSH_CLIENT")
            or os.environ.get("SSH_TTY")
            or os.environ.get("SSH_CONNECTION")
        ):
            return "unavailable"
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return "unavailable"
        zenity_bin = shutil.which("zenity")
        if not zenity_bin:
            return "unavailable"

        try:
            proc = subprocess.run(
                [
                    zenity_bin,
                    "--question",
                    f"--text={dialog_text}",
                    "--ok-label=OK",
                    "--cancel-label=Not me",
                    f"--timeout={int(timeout_seconds)}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout_seconds + 5,
            )
            if proc.returncode == 0:
                return "ok"
            elif proc.returncode == 5:
                return "gave-up"
            elif proc.returncode == 1:
                return "not-me"
            else:
                return "error"
        except subprocess.TimeoutExpired:
            return "gave-up"
        except Exception:
            return "error"

    return "unavailable"


def record_refusal(
    state_dir: Union[str, Path],
    command_str: str,
    outcome: str,
    task_id: Optional[str] = None,
    at: Optional[str] = None,
) -> Dict[str, Any]:
    """Append one refusal record to <state_dir>/refusals.jsonl atomically."""
    clean_cmd = format_command_for_audit(command_str)
    at_str = at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    id_payload = f"refusal:{at_str}:{clean_cmd}".encode("utf-8")
    refusal_id = hashlib.sha256(id_payload).hexdigest()

    marker = "agent" if os.environ.get("ANTIGRAVITY_AGENT") else "none"

    record: Dict[str, Any] = {
        "version": 1,
        "id": refusal_id,
        "at": at_str,
        "task": task_id,
        "command": clean_cmd,
        "outcome": outcome,
        "marker": marker,
    }

    validate_state_doc("refusals", record)

    # Determine refusals.jsonl path
    p = Path(state_dir).resolve()
    if p.is_file() and p.name == "refusals.jsonl":
        refusals_file = p
    elif p.name == "state":
        refusals_file = p / "refusals.jsonl"
    elif (p / ".lintel" / "state").exists() or (p / ".lintel").is_dir():
        refusals_file = p / ".lintel" / "state" / "refusals.jsonl"
    else:
        refusals_file = p / "refusals.jsonl"

    refusals_file.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(record, sort_keys=True) + "\n"
    encoded = line.encode("utf-8")

    fd = os.open(refusals_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)

    return record


def read_refusals(state_dir_or_file: Union[str, Path]) -> List[Dict[str, Any]]:
    """Read and validate all refusal records from refusals.jsonl."""
    p = Path(state_dir_or_file).resolve()
    if p.is_dir():
        if (p / ".lintel" / "state" / "refusals.jsonl").is_file():
            p = p / ".lintel" / "state" / "refusals.jsonl"
        elif (p / "refusals.jsonl").is_file():
            p = p / "refusals.jsonl"
        else:
            return []
    if not p.is_file():
        return []

    records = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            rec = json.loads(line_str)
            validate_state_doc("refusals", rec, file_path=p)
            records.append(rec)
    return records


def find_active_task(
    repo_root: Optional[Union[str, Path]] = None,
    state_dir: Optional[Union[str, Path]] = None,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Find active task ID and state data, if any."""
    s_dirs: List[Path] = []
    if state_dir is not None:
        s_dirs.append(Path(state_dir).resolve())
    if repo_root is not None:
        s_dirs.append(Path(repo_root).resolve() / ".lintel" / "state")

    cur = Path.cwd().resolve()
    s_dirs.append(cur / ".lintel" / "state")
    for parent in cur.parents:
        s_dirs.append(parent / ".lintel" / "state")

    for sdir in s_dirs:
        if not sdir.is_dir():
            continue

        # Check subdirectories <task_id>/task.json
        try:
            for entry in sdir.iterdir():
                if entry.is_dir() and (entry / "task.json").is_file():
                    try:
                        data = json.loads((entry / "task.json").read_text(encoding="utf-8"))
                        if data.get("status") == "active":
                            return entry.name, data
                    except Exception:
                        pass
        except OSError:
            pass

        # Check sdir/task.json
        if (sdir / "task.json").is_file():
            try:
                data = json.loads((sdir / "task.json").read_text(encoding="utf-8"))
                if data.get("status") == "active":
                    return data.get("id", "current"), data
            except Exception:
                pass

    # Check git current branch
    try:
        rroot = str(repo_root or Path.cwd())
        proc = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            cwd=rroot,
            timeout=5,
        )
        if proc.returncode == 0:
            branch = proc.stdout.strip()
            if branch.startswith("lintel/"):
                t_id = branch[7:]
                for sdir in s_dirs:
                    t_file = sdir / t_id / "task.json"
                    if t_file.is_file():
                        try:
                            data = json.loads(t_file.read_text(encoding="utf-8"))
                            return t_id, data
                        except Exception:
                            pass
                return t_id, {}
    except Exception:
        pass

    return None


def _is_devs_turn_for_restore_tests(task_data: Dict[str, Any]) -> bool:
    """Return True if state corresponds to DEV's turn: phase locked, not delivered, outside amend cycle."""
    if not task_data or not isinstance(task_data, dict):
        return False
    if task_data.get("status") != "active":
        return False
    # Outside amend cycle
    if task_data.get("cycle") is not None:
        return False

    pos = task_data.get("position", {})
    phase = pos.get("phase")
    stage = str(pos.get("stage", ""))
    if phase is None and stage.startswith("p") and stage[1:].isdigit():
        phase = int(stage[1:])
    if phase is None:
        return False

    phases = task_data.get("phases", {})
    phase_info = phases.get(str(phase)) or phases.get(phase)
    if not phase_info or not isinstance(phase_info, dict):
        return False

    # Must be locked
    lock = phase_info.get("lock")
    if not lock:
        return False

    # Must NOT be delivered
    delivered = phase_info.get("delivered")
    if delivered:
        return False
    delivered_phases = task_data.get("delivered_phases", [])
    if phase in delivered_phases or str(phase) in delivered_phases:
        return False

    # Position code check: if explicit non-DEV role
    code = pos.get("code")
    if code and str(code).startswith(
        ("W.po", "R.po", "W.architect", "R.architect", "W.qa", "R.qa", "H.req", "H.plan", "H.lock", "H.amend")
    ):
        return False

    return True


def is_human_only_command(
    command: str,
    args: Optional[argparse.Namespace] = None,
    repo_root: Optional[Path] = None,
) -> bool:
    """Evaluate whether a command is human-only per PLAN §3.5 and M1a-11."""
    # 1. Any command with --full is human-only (T-HUM-07)
    if args is not None and getattr(args, "full", False):
        return True
    if "--full" in command.split():
        return True

    clean_cmd = format_command_for_audit(command)
    cmd_tokens = clean_cmd.split()
    primary_cmd = cmd_tokens[0] if cmd_tokens else ""

    subcmd = getattr(args, "subcommand", None) if args is not None else None
    effective_subcmd = subcmd or primary_cmd

    # 2. Check restore-tests state-dependent rule
    if effective_subcmd == "restore-tests":
        task_info = find_active_task(repo_root=repo_root)
        if task_info:
            _, task_data = task_info
            if _is_devs_turn_for_restore_tests(task_data):
                return False
        return True

    # 3. Check task subcommands
    if effective_subcmd == "task":
        task_cmd = getattr(args, "task_command", None) if args is not None else None
        if task_cmd is None and len(cmd_tokens) > 1:
            task_cmd = cmd_tokens[1]

        if task_cmd == "finish":
            return True
        if task_cmd == "unstick":
            force = getattr(args, "force", False) if args is not None else False
            if "--force" in cmd_tokens or force:
                return True
            return False
        return False

    # 4. Check drift and audit: human-only with --ack or --reject
    if effective_subcmd in {"drift", "audit"}:
        if args is not None:
            if getattr(args, "ack", False) or getattr(args, "reject", None) is not None:
                return True
        if "--ack" in cmd_tokens or "--reject" in cmd_tokens:
            return True
        return False

    # 5. Check if effective subcommand or clean command in HUMAN_ONLY_COMMANDS
    if effective_subcmd in HUMAN_ONLY_COMMANDS:
        return True
    if clean_cmd in HUMAN_ONLY_COMMANDS:
        return True
    if primary_cmd in HUMAN_ONLY_COMMANDS:
        return True

    return False


def check_caller(
    command_str: str,
    args: argparse.Namespace,
    repo_root: Optional[Path] = None,
    dialog_fn: Optional[Callable[..., str]] = None,
    state_dir: Optional[Union[str, Path]] = None,
) -> None:
    """Enforce caller check for human-only commands.

    Evaluates if command is human-only. If so, invokes dialog_fn (default: show_caller_dialog).
    If outcome == 'ok': passes.
    If outcome == 'gave-up': records refusal, prints 're-run the command when ready', and raises CallerRefusalError.
    If outcome in {'not-me', 'canceled', 'error', 'unavailable'}: records refusal, prints refusal message, and raises CallerRefusalError.
    Crucial: NO environment variable or flag switches this check off!
    """
    if not is_human_only_command(command_str, args=args, repo_root=repo_root):
        return

    active_task_id: Optional[str] = getattr(args, "task_id", None)
    task_info = find_active_task(repo_root=repo_root, state_dir=state_dir)
    if active_task_id is None and task_info:
        active_task_id = task_info[0]

    dfn = dialog_fn if dialog_fn is not None else show_caller_dialog
    outcome = dfn(command_str, task_id=active_task_id)

    if outcome == "ok":
        return

    # Determine state directory for recording refusal
    if state_dir is not None:
        target_state_dir = Path(state_dir)
    elif repo_root is not None:
        target_state_dir = Path(repo_root) / ".lintel" / "state"
    else:
        cur = Path.cwd().resolve()
        target_state_dir = cur / ".lintel" / "state"
        for parent in cur.parents:
            candidate = parent / ".lintel" / "state"
            if candidate.is_dir():
                target_state_dir = candidate
                break

    rec = record_refusal(
        state_dir=target_state_dir,
        command_str=command_str,
        outcome=outcome,
        task_id=active_task_id,
    )

    if outcome == "gave-up":
        msg = "re-run the command when ready"
        print(msg)
        raise CallerRefusalError(msg, outcome=outcome, record=rec)
    else:
        msg = f"refused: caller check failed ({outcome})"
        print(msg)
        raise CallerRefusalError(msg, outcome=outcome, record=rec)
