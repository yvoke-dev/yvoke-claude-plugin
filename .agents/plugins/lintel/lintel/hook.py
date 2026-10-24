"""Lintel audit hook payload processing and logging (M1b-02, PLAN §3.3, §11, docs/state-machine.md §9.11).

Pure Python standard library only, Python 3.9 compatible.
Prints NOTHING: redirects stdout and stderr to os.devnull and exits 0 on all branches.
Sets a 3s signal.alarm deadline where supported.
"""

import sys
sys.dont_write_bytecode = True

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import signal
from typing import Any, Dict, List, Optional, Set, Tuple, Union

_plugin_dir = str(Path(__file__).resolve().parent.parent)
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

# Tool argument allow-list per tests/fixtures/payloads/tool-names.json
TOOL_ALLOWLIST: Dict[str, Set[str]] = {
    "view_file": {"AbsolutePath", "StartLine", "EndLine"},
    "write_to_file": {"TargetFile", "Overwrite"},
    "replace_file_content": {"TargetFile", "StartLine", "EndLine", "AllowMultiple"},
    "run_command": {"CommandLine", "Cwd", "WaitMsBeforeAsync", "IsDaemon", "BypassSandbox"},
    "invoke_subagent": {"Subagents"},
    "send_message": {"Recipient", "Message"},
    "grep_search": {"SearchPath", "Query", "CaseInsensitive", "MatchPerLine"},
    "find_by_name": {"SearchDirectory", "Pattern"},
    "list_dir": {"DirectoryPath"},
    "manage_subagents": {"Action", "ConversationIds"},
    "manage_task": {"Action", "TaskId"},
    "schedule": {"DurationSeconds", "Prompt", "TimerCondition", "CronExpression", "MaxIterations", "IsDaemon"},
    "ask_question": {"questions"},
    "read_url_content": {"Url"},
    "search_web": {"query"},
    "call_mcp_tool": {"ServerName", "ToolName"},
    "read_resource": {"ServerName", "Uri"},
    "list_resources": {"ServerName"},
    "notebook_edit": {"NotebookPath", "Action", "CellIndex"},
    "define_subagent": {
        "name",
        "description",
        "system_prompt",
        "enable_write_tools",
        "enable_mcp_tools",
        "enable_subagent_tools",
    },
}

SUBAGENT_ALLOWLIST: Set[str] = {"TypeName", "Role", "Prompt", "Model", "Workspace"}

# Path arguments kept for 'other' conversations during a persona run
TOOL_PATH_ARGS: Dict[str, Set[str]] = {
    "view_file": {"AbsolutePath"},
    "write_to_file": {"TargetFile"},
    "replace_file_content": {"TargetFile"},
    "list_dir": {"DirectoryPath"},
    "grep_search": {"SearchPath"},
    "find_by_name": {"SearchDirectory"},
    "notebook_edit": {"NotebookPath"},
    "run_command": {"Cwd"},  # Plus CommandLine truncated to 200 chars
}

_RUNNERS: Set[str] = {
    "sh", "bash", "zsh", "ksh", "dash",
    "python", "python3", "python3.9", "python3.10", "python3.11", "python3.12", "python3.13",
    "env", "sudo", "nohup", "time", "exec",
}


def find_repo_root(payload: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """Locate the repository root directory containing .lintel."""
    # 1. Check workspacePaths in payload
    if payload and isinstance(payload, dict):
        workspace_paths = payload.get("workspacePaths") or []
        for wp in workspace_paths:
            try:
                p = Path(wp).resolve()
                if (p / ".lintel" / "config.jsonc").is_file() or (p / ".lintel" / "state").is_dir():
                    return p
                for parent in p.parents:
                    if (parent / ".lintel" / "config.jsonc").is_file() or (parent / ".lintel" / "state").is_dir():
                        return parent
            except Exception:
                continue

    # 2. Check parents of this file
    try:
        cur = Path(__file__).resolve()
        for parent in cur.parents:
            if (parent / ".lintel" / "config.jsonc").is_file() or (parent / ".lintel" / "state").is_dir():
                return parent
    except Exception:
        pass

    # 3. Check cwd and parents
    try:
        cur = Path.cwd().resolve()
        if (cur / ".lintel" / "config.jsonc").is_file() or (cur / ".lintel" / "state").is_dir():
            return cur
        for parent in cur.parents:
            if (parent / ".lintel" / "config.jsonc").is_file() or (parent / ".lintel" / "state").is_dir():
                return parent
    except Exception:
        pass

    return None


def is_task_active(repo_root: Path) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """Check if any task in .lintel/state has status == 'active'."""
    state_dir = repo_root / ".lintel" / "state"
    if not state_dir.is_dir():
        return False, None, None

    # Check top-level task.json if present
    task_json = state_dir / "task.json"
    if task_json.is_file():
        try:
            data = json.loads(task_json.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("status") == "active":
                return True, data.get("id"), data
        except Exception:
            pass

    # Check state/<task_id>/task.json
    try:
        entries = sorted(state_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        return False, None, None

    for entry in entries:
        if entry.is_dir():
            t_file = entry / "task.json"
            if t_file.is_file():
                try:
                    data = json.loads(t_file.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("status") == "active":
                        return True, entry.name, data
                except Exception:
                    pass

    return False, None, None


def is_lintel_command(cmd_line: str) -> bool:
    """Check if CommandLine runs the Lintel CLI."""
    if not cmd_line or not isinstance(cmd_line, str):
        return False
    segments = re.split(r"[;&|]+", cmd_line)
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        try:
            tokens = shlex.split(seg)
        except Exception:
            tokens = seg.split()
        if not tokens:
            continue
        i = 0
        while i < len(tokens) and ("=" in tokens[i] and not tokens[i].startswith("./")):
            i += 1
        while i < len(tokens):
            tok = tokens[i]
            base = os.path.basename(tok)
            if tok == "-c" and i + 1 < len(tokens):
                if is_lintel_command(tokens[i + 1]):
                    return True
                i += 2
                continue
            if base in _RUNNERS:
                i += 1
                while i < len(tokens) and tokens[i].startswith("-"):
                    if tokens[i] == "-c" and i + 1 < len(tokens):
                        if is_lintel_command(tokens[i + 1]):
                            return True
                        i += 2
                        break
                    i += 1
                continue
            if tok.startswith("-"):
                i += 1
                continue
            if (
                base in ("lintel", "lintel.sh", "lintel.py", "cli.py")
                or tok.endswith("/.lintel/bin/lintel")
                or tok in ("./.lintel/bin/lintel", ".lintel/bin/lintel")
            ):
                return True
            break
    return False


def is_other_conversation_during_persona_run(
    calling_conv_id: str,
    task_data: Dict[str, Any],
    repo_root: Path,
    task_id: Optional[str] = None,
) -> bool:
    """Determine if calling_conv_id is an 'other' conversation during an active persona run."""
    orchestrator = task_data.get("orchestrator")
    if calling_conv_id and calling_conv_id == orchestrator:
        return False

    pos = task_data.get("position")
    clean_code = pos.get("code", "") if isinstance(pos, dict) else ""

    is_persona_active = False
    active_persona_conv_id = None

    state_dir = repo_root / ".lintel" / "state"
    runs_dir = None
    if task_id:
        runs_dir = state_dir / task_id / "runs"
    else:
        tid = task_data.get("id")
        if tid:
            runs_dir = state_dir / tid / "runs"

    if runs_dir and runs_dir.is_dir():
        try:
            for rf in runs_dir.glob("*.json"):
                try:
                    rd = json.loads(rf.read_text(encoding="utf-8"))
                    if isinstance(rd, dict) and rd.get("state") in ("active", "pending"):
                        is_persona_active = True
                        if rd.get("conversation_id"):
                            active_persona_conv_id = rd.get("conversation_id")
                except Exception:
                    pass
        except Exception:
            pass

    if clean_code.startswith("A.") or clean_code.startswith("P."):
        is_persona_active = True

    if not is_persona_active:
        return False

    if active_persona_conv_id and calling_conv_id == active_persona_conv_id:
        return False

    return True


def sanitize_args(
    tool_name: str,
    raw_args: Dict[str, Any],
    is_other: bool = False,
) -> Dict[str, Any]:
    """Sanitize arguments according to tool allow-list and 'other' conversation rules."""
    if not isinstance(raw_args, dict):
        return {}

    clean_args: Dict[str, Any] = {}

    if is_other:
        # For 'other' conversations during a persona run:
        # only tool names and paths are kept, plus run_command lines cut to 200 characters
        if tool_name == "run_command":
            if "CommandLine" in raw_args:
                clean_args["CommandLine"] = str(raw_args["CommandLine"])[:200]
            if "Cwd" in raw_args:
                clean_args["Cwd"] = raw_args["Cwd"]
            return clean_args
        elif tool_name in TOOL_PATH_ARGS:
            for p_key in TOOL_PATH_ARGS[tool_name]:
                if p_key in raw_args:
                    clean_args[p_key] = raw_args[p_key]
            return clean_args
        else:
            return {}

    # Standard allow-list
    allowed = TOOL_ALLOWLIST.get(tool_name)
    if allowed is None:
        # Unknown tool: empty args
        return {}

    for k in allowed:
        if k not in raw_args:
            continue
        if k == "Subagents" and tool_name == "invoke_subagent":
            subagents = raw_args.get("Subagents")
            if isinstance(subagents, list):
                cleaned_subagents = []
                for sub in subagents:
                    if isinstance(sub, dict):
                        clean_sub = {}
                        for sub_k in SUBAGENT_ALLOWLIST:
                            if sub_k in sub:
                                clean_sub[sub_k] = sub[sub_k]
                        cleaned_subagents.append(clean_sub)
                clean_args["Subagents"] = cleaned_subagents
        else:
            clean_args[k] = raw_args[k]

    return clean_args


def process_payload(
    payload: Union[str, Dict[str, Any]],
    repo_root: Optional[Union[str, Path]] = None,
    event: Optional[str] = None,
    now: Optional[str] = None,
    write_log: bool = True,
    force_log: bool = False,
) -> Optional[Dict[str, Any]]:
    """Process hook payload, sanitize arguments, and append audit record to audit.jsonl.

    Returns the written record dictionary or None if discarded.
    """
    try:
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            return None

        # Check if event wrapper is present (e.g. from sequence file)
        event_name = event
        if "event" in payload and "payload" in payload and isinstance(payload["payload"], dict):
            event_name = payload["event"]
            payload = payload["payload"]

        # Discard non-tool events (PreInvocation, PostInvocation, Stop)
        if event_name in ("PreInvocation", "PostInvocation", "Stop"):
            return None

        tool_call = payload.get("toolCall")
        if not isinstance(tool_call, dict):
            return None

        tool_name = tool_call.get("name")
        if not tool_name or not isinstance(tool_name, str):
            return None

        raw_args = tool_call.get("args") or {}
        if not isinstance(raw_args, dict):
            raw_args = {}

        # Resolve repo root
        root_path: Optional[Path] = None
        if repo_root is not None:
            root_path = Path(repo_root).resolve()
        else:
            root_path = find_repo_root(payload)

        # Determine if task is active or Lintel CLI call
        task_active = False
        task_id = None
        task_data = None
        if root_path is not None:
            task_active, task_id, task_data = is_task_active(root_path)

        cmd_line = str(raw_args.get("CommandLine", "")) if tool_name == "run_command" else ""
        is_lintel = (tool_name == "run_command" and is_lintel_command(cmd_line))

        if not force_log and not task_active and not is_lintel:
            # Neither task active nor Lintel command: do not log
            return None

        # Check if 'other' conversation during persona run
        calling_conv_id = str(payload.get("conversationId", ""))
        is_other = False
        if task_active and task_data and root_path:
            is_other = is_other_conversation_during_persona_run(
                calling_conv_id, task_data, root_path, task_id=task_id
            )

        # Determine hook type: 'pre' or 'post'
        if event_name:
            hook_type = "post" if "post" in event_name.lower() else "pre"
        elif "error" in payload:
            hook_type = "post"
        else:
            hook_type = "pre"

        # Sanitize arguments
        clean_args = sanitize_args(tool_name, raw_args, is_other=is_other)

        # Narrow denies: DEV cannot read test files via direct read tools
        if hook_type == "pre" and task_active and task_data and root_path and not is_other:
            active_role = None
            state_dir = root_path / ".lintel" / "state"
            tid = task_id or task_data.get("id")
            if tid:
                runs_dir = state_dir / tid / "runs"
                if runs_dir.is_dir():
                    try:
                        for rf in runs_dir.glob("*.json"):
                            rd = json.loads(rf.read_text(encoding="utf-8"))
                            if isinstance(rd, dict) and rd.get("conversation_id") == calling_conv_id:
                                active_role = rd.get("role")
                                break
                    except Exception:
                        pass
            
            if active_role == "dev" and tool_name in ("view_file", "read_file", "grep_search"):
                target_paths = []
                if tool_name in ("view_file", "read_file"):
                    p = raw_args.get("AbsolutePath")
                    if p: target_paths.append(str(p))
                elif tool_name == "grep_search":
                    p1 = raw_args.get("SearchPath")
                    if p1: target_paths.append(str(p1))
                    
                if target_paths:
                    from lintel.config import load_config
                    from lintel.paths import classify_path, OverlapError
                    try:
                        cfg_path = root_path / ".lintel" / "config.jsonc"
                        plugin_dir = root_path / ".agents" / "plugins" / "lintel"
                        if not plugin_dir.is_dir():
                            import lintel.doctor
                            plugin_dir = lintel.doctor.DEFAULT_PLUGIN_DIR
                            
                        cfg = load_config(cfg_path, workspace_root=root_path, plugin_dir=plugin_dir)
                        for tp in target_paths:
                            is_tests = False
                            try:
                                p_class = classify_path(tp, config=cfg, repo_root=root_path)
                                if p_class.base_class == "tests":
                                    is_tests = True
                            except OverlapError:
                                is_tests = True

                            if is_tests:
                                out = sys.__stdout__ if sys.__stdout__ is not None else sys.stdout
                                print(
                                    json.dumps({"error": f"Narrow deny: DEV cannot read test paths directly ({tp} is classified as tests)."}),
                                    file=out,
                                    flush=True,
                                )
                                sys.exit(2)
                    except SystemExit:
                        raise
                    except Exception:
                        pass

        # Extract target, marker, prompt
        target = None
        marker = None
        prompt = None

        if not is_other:
            if tool_name == "invoke_subagent":
                subagents = raw_args.get("Subagents")
                if isinstance(subagents, list) and subagents and isinstance(subagents[0], dict):
                    first_sub = subagents[0]
                    target = first_sub.get("TypeName") or first_sub.get("Role")
                    prompt = first_sub.get("Prompt")
                    if prompt and isinstance(prompt, str):
                        m = re.search(r"\[lintel:([^:]+):([^\]]+)\]", prompt)
                        if m:
                            marker = {"role": m.group(1), "nonce": m.group(2)}
            elif tool_name == "send_message":
                target = raw_args.get("Recipient")
                prompt = raw_args.get("Message")
                if prompt and isinstance(prompt, str):
                    m = re.search(r"\[lintel:([^:]+):([^\]]+)\]", prompt)
                    if m:
                        marker = {"role": m.group(1), "nonce": m.group(2)}
            elif tool_name == "define_subagent":
                target = raw_args.get("name")
                prompt = raw_args.get("system_prompt")
                if prompt and isinstance(prompt, str):
                    m = re.search(r"\[lintel:([^:]+):([^\]]+)\]", prompt)
                    if m:
                        marker = {"role": m.group(1), "nonce": m.group(2)}

        # Timestamp
        at_ts = now if now is not None else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Step index
        try:
            step_idx = int(payload.get("stepIdx", 0))
        except (ValueError, TypeError):
            step_idx = 0

        # Construct audit record matching docs/state-machine.md §9.11
        record: Dict[str, Any] = {
            "version": 1,
            "at": at_ts,
            "hook": hook_type,
            "conversationId": calling_conv_id,
            "artifactDirectoryPath": payload.get("artifactDirectoryPath"),
            "transcriptPath": payload.get("transcriptPath"),
            "stepIdx": step_idx,
            "tool": tool_name,
            "target": target,
            "marker": marker,
            "prompt": prompt,
            "args": clean_args,
        }

        if hook_type == "post":
            record["error"] = str(payload.get("error") or "")

        # Write to audit.jsonl if requested and repo root known
        if write_log and root_path is not None:
            state_dir = root_path / ".lintel" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            audit_file = state_dir / "audit.jsonl"
            line = json.dumps(record) + "\n"
            with open(audit_file, "a", encoding="utf-8") as f:
                f.write(line)

        return record
    except Exception:
        return None


def main() -> None:
    """Entry point for hook.py CLI execution."""
    # 1. Suppress/redirect stdout and stderr immediately
    try:
        devnull = open(os.devnull, "w")
        sys.stdout = devnull
        sys.stderr = devnull
    except Exception:
        pass

    # 2. 3-second deadline
    try:
        if hasattr(signal, "SIGALRM") and hasattr(signal, "alarm"):
            def _alarm_handler(signum, frame):
                sys.exit(0)
            signal.signal(signal.SIGALRM, _alarm_handler)
            signal.alarm(3)
    except Exception:
        pass

    # 3. Read payload from sys.argv[1] (if file) or stdin
    try:
        raw_text = None
        if len(sys.argv) > 1 and sys.argv[1] and sys.argv[1] != "-":
            arg_path = Path(sys.argv[1])
            if arg_path.is_file():
                raw_text = arg_path.read_text(encoding="utf-8", errors="replace")
        if raw_text is None:
            raw_text = sys.stdin.read()

        if raw_text and raw_text.strip():
            process_payload(raw_text)
    except SystemExit:
        raise
    except Exception:
        pass
    finally:
        try:
            if hasattr(signal, "alarm"):
                signal.alarm(0)
        except Exception:
            pass
    sys.exit(0)


if __name__ == "__main__":
    main()
