"""Audit identity tracking, persona binding, and hook evidence validation (M1b-04).

Pure Python standard library only, Python 3.9 compatible.
Implements PLAN §3.3 (Identity & Hook evidence), §3.7 (Crossings), docs/state-machine.md §9.11.
"""

import sys
sys.dont_write_bytecode = True

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

try:
    from lintel import agy_readers, caller, paths, shell
except ImportError:
    from . import agy_readers, caller, paths, shell

# -----------------------------------------------------------------------------
# Constants & Enums
# -----------------------------------------------------------------------------

KIND_CROSSING = "crossing"
KIND_INFORMATIONAL = "informational"

CAT_ROLE_REUSE = "role-reuse"
CAT_NO_HOOK_EVIDENCE = "no-hook-evidence"
CAT_UNKNOWN_ROLE = "unknown-role"
CAT_STOPPED_PERSONA = "stopped-persona"
CAT_INFO_LISTING = "listing"
CAT_INFO_MIXED_SEARCH = "mixed-search"
CAT_INFO_DYNAMIC_CONSTRUCT = "dynamic-construct"
CAT_INFO_UNMAPPED_TOOL = "unmapped-tool"
CAT_INFO_AGENT_COMMIT = "agent-commit"
CAT_INFO_TEST_REPORT_READ = "test-report-read"

CAT_READ_OUTSIDE_LANE = "read-outside-lane"
CAT_GIT_OUTSIDE_ALLOWLIST = "git-outside-allowlist"
CAT_TEST_SEARCH = "test-search"
CAT_WRITE_OUTSIDE_LANE = "write-outside-lane"
CAT_TRANSCRIPT_READ = "transcript-read"
CAT_REFUSED_COMMAND = "refused-command"
CAT_HOOK_FAILED = "hook-failed"
CAT_PROMPT_MISMATCH = "prompt-mismatch"
CAT_ORCHESTRATOR_MESSAGE = "orchestrator-message"

ALL_CROSSING_CATEGORIES: Set[str] = {
    CAT_READ_OUTSIDE_LANE,
    CAT_GIT_OUTSIDE_ALLOWLIST,
    CAT_TEST_SEARCH,
    CAT_WRITE_OUTSIDE_LANE,
    CAT_TRANSCRIPT_READ,
    CAT_REFUSED_COMMAND,
    CAT_NO_HOOK_EVIDENCE,
    CAT_HOOK_FAILED,
    CAT_ROLE_REUSE,
    CAT_PROMPT_MISMATCH,
    CAT_ORCHESTRATOR_MESSAGE,
    CAT_UNKNOWN_ROLE,
}

ROLES_SHOULD_NOT_READ_TESTS: Set[str] = {
    "dev",
    "dev-reviewer",
    "po",
    "po-reviewer",
    "architect",
    "architect-reviewer",
    "orchestrator",
}

# Tool kinds per tool-names.json
TOOL_KIND_READ = "read"
TOOL_KIND_WRITE = "write"
TOOL_KIND_EDIT = "edit"
TOOL_KIND_SEARCH = "search"
TOOL_KIND_LIST = "list"
TOOL_KIND_FIND = "find"
TOOL_KIND_COMMAND = "command"
TOOL_KIND_SUBAGENT_INVOKE = "subagent-invoke"
TOOL_KIND_SUBAGENT_DEFINE = "subagent-define"
TOOL_KIND_MESSAGE = "message"
TOOL_KIND_SUBAGENT_MANAGE = "subagent-manage"
TOOL_KIND_QUESTION = "question"
TOOL_KIND_BROWSER = "browser"
TOOL_KIND_MCP = "mcp"
TOOL_KIND_OTHER = "other"

_RUNNERS: Set[str] = {
    "sh", "bash", "zsh", "ksh", "dash",
    "python", "python3", "python3.9", "python3.10", "python3.11", "python3.12", "python3.13",
    "env", "sudo", "nohup", "time", "exec",
}

FALLBACK_TOOL_NAMES: Dict[str, Dict[str, Any]] = {
    "view_file": {"kind": "read", "path_args": ["AbsolutePath"]},
    "write_to_file": {"kind": "write", "path_args": ["TargetFile"], "never_log_args": ["CodeContent"]},
    "replace_file_content": {"kind": "edit", "path_args": ["TargetFile"], "never_log_args": ["TargetContent", "ReplacementContent"]},
    "run_command": {"kind": "command", "command_arg": "CommandLine", "cwd_arg": "Cwd"},
    "invoke_subagent": {"kind": "subagent-invoke"},
    "send_message": {"kind": "message"},
    "grep_search": {"kind": "search", "root_arg": "SearchPath", "query_arg": "Query"},
    "find_by_name": {"kind": "find", "root_arg": "SearchDirectory", "query_arg": "Pattern"},
    "list_dir": {"kind": "list", "path_args": ["DirectoryPath"]},
    "manage_subagents": {"kind": "subagent-manage"},
    "manage_task": {"kind": "command"},
    "schedule": {"kind": "other"},
    "ask_question": {"kind": "question"},
    "read_url_content": {"kind": "other"},
    "search_web": {"kind": "other"},
    "call_mcp_tool": {"kind": "mcp"},
    "read_resource": {"kind": "mcp"},
    "list_resources": {"kind": "mcp"},
    "notebook_edit": {"kind": "edit", "path_args": ["NotebookPath"]},
    "define_subagent": {"kind": "subagent-define"},
}


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class ToolDefinition:
    """Definition of a platform tool loaded from tool-names.json."""
    name: str
    kind: str = "other"
    path_args: List[str] = field(default_factory=list)
    root_arg: Optional[str] = None
    query_arg: Optional[str] = None
    command_arg: Optional[str] = None
    cwd_arg: Optional[str] = None
    never_log_args: List[str] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    captured: bool = False
    notes: Optional[str] = None


@dataclass
class ParsedAuditEvent:
    """Normalized audit log event matching docs/state-machine.md §9.11."""
    version: int
    at: str
    hook: str  # "pre" or "post"
    conversation_id: str
    artifact_directory_path: Optional[str]
    transcript_path: Optional[str]
    step_idx: int
    tool: str
    target: Optional[str] = None
    marker: Optional[Dict[str, str]] = None
    prompt: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    role: Optional[str] = None  # Inferred identity (e.g. "orchestrator", "dev", "unknown", "other")
    run_id: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation matching audit.jsonl format."""
        d: Dict[str, Any] = {
            "version": self.version,
            "at": self.at,
            "hook": self.hook,
            "conversationId": self.conversation_id,
            "artifactDirectoryPath": self.artifact_directory_path,
            "transcriptPath": self.transcript_path,
            "stepIdx": self.step_idx,
            "tool": self.tool,
            "target": self.target,
            "marker": self.marker,
            "prompt": self.prompt,
            "args": self.args,
        }
        if self.error is not None:
            d["error"] = self.error
        if self.role is not None:
            d["role"] = self.role
        if self.run_id is not None:
            d["run_id"] = self.run_id
        return d





def generate_finding_id(
    conversation_id: Optional[str],
    step_idx: Optional[int],
    kind: str,
    path: Optional[str] = None,
) -> str:
    """Generate stable finding ID as SHA-256 hash of conversationId, stepIdx, kind, and path."""
    raw_val = f"{conversation_id or ''}:{step_idx if step_idx is not None else 0}:{kind}:{path or ''}"
    return hashlib.sha256(raw_val.encode("utf-8")).hexdigest()[:16]


@dataclass
class AuditFinding:
    """An audit crossing or informational finding per PLAN §3.7."""
    id: str = ""
    kind: str = KIND_CROSSING  # "crossing" or "informational"
    category: str = ""         # One of CAT_* (e.g. "read-outside-lane")
    role: Optional[str] = None
    conversation_id: Optional[str] = None
    run_id: Optional[str] = None
    step_idx: Optional[int] = None
    at: Optional[str] = None
    path: Optional[str] = None
    tool: Optional[str] = None
    command: Optional[str] = None
    message: str = ""
    severity: str = KIND_CROSSING
    stop_cause: Optional[str] = None
    details: str = ""
    finding_id: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.category and self.kind in ALL_CROSSING_CATEGORIES:
            self.category = self.kind
            self.kind = KIND_CROSSING
        if not self.id and self.finding_id:
            self.id = self.finding_id
        elif not self.finding_id and self.id:
            self.finding_id = self.id
        elif not self.id and not self.finding_id:
            self.id = generate_finding_id(
                self.conversation_id,
                self.step_idx,
                self.category or self.kind,
                self.path,
            )
            self.finding_id = self.id
        if not self.severity:
            self.severity = self.kind
        if not self.message and self.details:
            self.message = self.details
        elif not self.details and self.message:
            self.details = self.message

    @property
    def timestamp(self) -> Optional[str]:
        return self.at

    @property
    def crossing_kind(self) -> str:
        return self.category or self.kind

    def to_dict(self) -> Dict[str, Any]:
        """Convert finding to dictionary."""
        return {
            "id": self.id,
            "kind": self.kind,
            "category": self.category,
            "role": self.role,
            "conversation_id": self.conversation_id,
            "run_id": self.run_id,
            "step_idx": self.step_idx,
            "at": self.at,
            "path": self.path,
            "tool": self.tool,
            "command": self.command,
            "message": self.message,
            "severity": self.severity,
            "stop_cause": self.stop_cause,
            "details": self.details,
            "finding_id": self.finding_id,
        }

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


IdentityFinding = AuditFinding


@dataclass
class AuditIdentityResult:
    """Result of identity tracking and hook evidence verification."""
    orchestrator_id: Optional[str]
    orchestrator_history: List[Tuple[str, str]]
    bound_personas: Dict[str, str]  # conversation_id -> role
    events: List[ParsedAuditEvent]
    findings: List[IdentityFinding]
    other_counts: Dict[str, int]  # conversation_id -> event count
    unknown_events: List[ParsedAuditEvent]
    stopped_personas: List[IdentityFinding]

    @property
    def crossings(self) -> List[IdentityFinding]:
        """Return all crossing findings."""
        return [f for f in self.findings if f.kind == KIND_CROSSING]

    @property
    def informationals(self) -> List[IdentityFinding]:
        """Return all informational findings."""
        return [f for f in self.findings if f.kind == KIND_INFORMATIONAL]

    @property
    def has_crossings(self) -> bool:
        """True if any crossing was recorded."""
        return bool(self.crossings)

    def findings_by_category(self, category: str) -> List[IdentityFinding]:
        """Return findings matching a specific category."""
        return [f for f in self.findings if f.category == category]


# -----------------------------------------------------------------------------
# Tool Names Loader (M0-04)
# -----------------------------------------------------------------------------

_LOADED_TOOL_NAMES: Optional[Dict[str, Any]] = None


def load_tool_names(path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Load tool definitions from tests/fixtures/payloads/tool-names.json or fallback.

    Data, not code, because other platforms (IDE, etc.) may name tools differently.
    """
    global _LOADED_TOOL_NAMES
    if path is None and _LOADED_TOOL_NAMES is not None:
        return _LOADED_TOOL_NAMES

    candidates: List[Path] = []
    if path is not None:
        candidates.append(Path(path).resolve())

    # Path relative to plugin/lintel/audit.py -> repo root -> tests/fixtures/payloads/
    cur_file = Path(__file__).resolve()
    repo_root = cur_file.parent.parent.parent
    candidates.append(repo_root / "tests" / "fixtures" / "payloads" / "tool-names.json")
    candidates.append(repo_root / ".lintel" / "tool-names.json")
    candidates.append(cur_file.parent / "tool-names.json")

    # Path relative to current working directory
    cwd = Path.cwd().resolve()
    candidates.append(cwd / "tests" / "fixtures" / "payloads" / "tool-names.json")
    candidates.append(cwd / ".lintel" / "tool-names.json")

    for cand in candidates:
        if cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "tools" in data:
                    if path is None:
                        _LOADED_TOOL_NAMES = data
                    return data
            except Exception:
                continue

    fallback = {
        "agy_version": "1.2.16",
        "tools": FALLBACK_TOOL_NAMES,
    }
    if path is None:
        _LOADED_TOOL_NAMES = fallback
    return fallback


def get_tool_info(tool_name: str, tool_names_data: Optional[Dict[str, Any]] = None) -> ToolDefinition:
    """Retrieve ToolDefinition for tool_name, defaulting to 'other' (tool with no path mapping)."""
    data = tool_names_data if tool_names_data is not None else load_tool_names()
    tools_dict = data.get("tools", {}) if isinstance(data, dict) else {}
    t_info = tools_dict.get(tool_name)
    if isinstance(t_info, dict):
        return ToolDefinition(
            name=tool_name,
            kind=str(t_info.get("kind", "other")),
            path_args=list(t_info.get("path_args", [])),
            root_arg=t_info.get("root_arg"),
            query_arg=t_info.get("query_arg"),
            command_arg=t_info.get("command_arg"),
            cwd_arg=t_info.get("cwd_arg"),
            never_log_args=list(t_info.get("never_log_args", [])),
            actions=list(t_info.get("actions", [])),
            captured=bool(t_info.get("captured", False)),
            notes=t_info.get("notes"),
        )
    return ToolDefinition(name=tool_name, kind="other")


def get_tool_kind(tool_name: str, tool_names_data: Optional[Dict[str, Any]] = None) -> str:
    """Return tool classification kind (read, edit, write, command, search, etc.)."""
    return get_tool_info(tool_name, tool_names_data).kind


def get_tool_path_args(tool_name: str, tool_names_data: Optional[Dict[str, Any]] = None) -> List[str]:
    """Return list of argument names that carry file or directory paths."""
    return get_tool_info(tool_name, tool_names_data).path_args


# -----------------------------------------------------------------------------
# Event Parsing & CLI Detection
# -----------------------------------------------------------------------------

def parse_audit_event(item: Union[str, Dict[str, Any]]) -> Optional[ParsedAuditEvent]:
    """Parse a single audit log line or payload dictionary into ParsedAuditEvent.

    Skips non-tool lifecycle events (PreInvocation, PostInvocation, Stop).
    Returns None if item is malformed or not a tool event.
    """
    if isinstance(item, str):
        line = item.strip()
        if not line:
            return None
        try:
            item = json.loads(line)
        except Exception:
            return None
    if not isinstance(item, dict):
        return None

    # Handle sequence envelope {"event": "...", "payload": {...}}
    event_type = None
    payload = item
    if "event" in item and "payload" in item and isinstance(item["payload"], dict):
        event_type = item.get("event")
        payload = item["payload"]

    if event_type in ("PreInvocation", "PostInvocation", "Stop"):
        return None

    # 1. State audit.jsonl record format
    if "tool" in payload and "conversationId" in payload:
        try:
            step_idx = int(payload.get("stepIdx", 0))
        except (ValueError, TypeError):
            step_idx = 0
        target = payload.get("target")
        marker = payload.get("marker") if isinstance(payload.get("marker"), dict) else None
        prompt = payload.get("prompt")
        args = dict(payload.get("args", {})) if isinstance(payload.get("args"), dict) else {}
        tool_name = str(payload.get("tool", ""))

        if tool_name == "invoke_subagent":
            subs = args.get("Subagents")
            if isinstance(subs, list) and subs and isinstance(subs[0], dict):
                first_sub = subs[0]
                if not target:
                    target = first_sub.get("TypeName") or first_sub.get("Role")
                if not prompt:
                    prompt = first_sub.get("Prompt")
                if not marker and prompt and isinstance(prompt, str):
                    m = re.search(r"\[lintel:([^:]+):([^\]]+)\]", prompt)
                    if m:
                        marker = {"role": m.group(1), "nonce": m.group(2)}
        elif tool_name == "send_message":
            if not target:
                target = args.get("Recipient")
            if not prompt:
                prompt = args.get("Message")
            if not marker and prompt and isinstance(prompt, str):
                m = re.search(r"\[lintel:([^:]+):([^\]]+)\]", prompt)
                if m:
                    marker = {"role": m.group(1), "nonce": m.group(2)}
        elif tool_name == "define_subagent":
            if not target:
                target = args.get("name")
            if not prompt:
                prompt = args.get("system_prompt")
            if not marker and prompt and isinstance(prompt, str):
                m = re.search(r"\[lintel:([^:]+):([^\]]+)\]", prompt)
                if m:
                    marker = {"role": m.group(1), "nonce": m.group(2)}

        return ParsedAuditEvent(
            version=int(payload.get("version", 1)),
            at=str(payload.get("at", "")),
            hook=str(payload.get("hook", "pre")),
            conversation_id=str(payload.get("conversationId", "")),
            artifact_directory_path=payload.get("artifactDirectoryPath"),
            transcript_path=payload.get("transcriptPath"),
            step_idx=step_idx,
            tool=tool_name,
            target=target,
            marker=marker,
            prompt=prompt,
            args=args,
            error=str(payload.get("error", "")) if "error" in payload else None,
            raw=dict(item),
        )

    # 2. Raw hook payload format {"toolCall": {"name": ..., "args": ...}, ...}
    tool_call = payload.get("toolCall")
    if isinstance(tool_call, dict) and "name" in tool_call:
        tool_name = str(tool_call.get("name", ""))
        raw_args = dict(tool_call.get("args", {})) if isinstance(tool_call.get("args"), dict) else {}
        try:
            step_idx = int(payload.get("stepIdx", 0))
        except (ValueError, TypeError):
            step_idx = 0

        hook_type = "pre"
        if event_type:
            hook_type = "post" if "post" in event_type.lower() else "pre"
        elif "error" in payload:
            hook_type = "post"

        target = None
        marker = None
        prompt = None
        if tool_name == "invoke_subagent":
            subs = raw_args.get("Subagents")
            if isinstance(subs, list) and subs and isinstance(subs[0], dict):
                first_sub = subs[0]
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

        return ParsedAuditEvent(
            version=1,
            at=str(payload.get("at", "")),
            hook=hook_type,
            conversation_id=str(payload.get("conversationId", "")),
            artifact_directory_path=payload.get("artifactDirectoryPath"),
            transcript_path=payload.get("transcriptPath"),
            step_idx=step_idx,
            tool=tool_name,
            target=target,
            marker=marker,
            prompt=prompt,
            args=raw_args,
            error=str(payload.get("error", "")) if "error" in payload else None,
            raw=dict(item),
        )

    return None


def parse_audit_log(source: Union[str, Path, Iterable[Union[str, Dict[str, Any], ParsedAuditEvent]]]) -> List[ParsedAuditEvent]:
    """Parse audit log events from file path, JSON string, or iterable of events."""
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.is_file():
            events: List[ParsedAuditEvent] = []
            try:
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    ev = parse_audit_event(line)
                    if ev is not None:
                        events.append(ev)
            except Exception:
                pass
            return events
        elif isinstance(source, str) and ("\n" in source or source.strip().startswith("{")):
            events = []
            for line in source.splitlines():
                ev = parse_audit_event(line)
                if ev is not None:
                    events.append(ev)
            return events

    events = []
    if isinstance(source, Iterable):
        for item in source:
            if isinstance(item, ParsedAuditEvent):
                events.append(item)
            else:
                ev = parse_audit_event(item)
                if ev is not None:
                    events.append(ev)
    return events


def extract_lintel_command(event: ParsedAuditEvent) -> Optional[Tuple[str, List[str]]]:
    """Extract Lintel subcommand and arguments from an audit event if it invokes the Lintel CLI.

    Returns:
        (subcommand, args_list) e.g. ("task", ["start", "title"]) or ("enter", ["po", "nonce"])
        or None if not a Lintel command.
    """
    # Direct tool call mock support (e.g. enter, task)
    if event.tool in ("enter", "task", "done", "invoke", "answer"):
        if event.tool == "enter":
            r_arg = event.args.get("role") or (event.args.get("args") or [None])[0]
            n_arg = event.args.get("nonce") or (event.args.get("args") or [None, None])[1]
            return "enter", [str(r_arg or ""), str(n_arg or "")]
        elif event.tool == "task":
            action = event.args.get("action") or event.args.get("subcommand") or ""
            return "task", [str(action)]
        return event.tool, []

    if event.tool != "run_command":
        return None

    cmd_line = str(event.args.get("CommandLine", ""))
    if not cmd_line or not cmd_line.strip():
        return None

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

        # Skip leading environment variables VAR=val
        i = 0
        while i < len(tokens) and ("=" in tokens[i] and not tokens[i].startswith("./")):
            i += 1

        while i < len(tokens):
            tok = tokens[i]
            base = os.path.basename(tok)
            if tok == "-c" and i + 1 < len(tokens):
                sub_ev = ParsedAuditEvent(
                    version=event.version,
                    at=event.at,
                    hook=event.hook,
                    conversation_id=event.conversation_id,
                    artifact_directory_path=event.artifact_directory_path,
                    transcript_path=event.transcript_path,
                    step_idx=event.step_idx,
                    tool="run_command",
                    args={"CommandLine": tokens[i + 1]},
                )
                res = extract_lintel_command(sub_ev)
                if res:
                    return res
                i += 2
                continue

            if base in _RUNNERS:
                i += 1
                while i < len(tokens) and tokens[i].startswith("-"):
                    if tokens[i] == "-c" and i + 1 < len(tokens):
                        sub_ev = ParsedAuditEvent(
                            version=event.version,
                            at=event.at,
                            hook=event.hook,
                            conversation_id=event.conversation_id,
                            artifact_directory_path=event.artifact_directory_path,
                            transcript_path=event.transcript_path,
                            step_idx=event.step_idx,
                            tool="run_command",
                            args={"CommandLine": tokens[i + 1]},
                        )
                        res = extract_lintel_command(sub_ev)
                        if res:
                            return res
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
                remaining = tokens[i + 1 :]
                if not remaining:
                    return "lintel", []
                subcmd = remaining[0]
                subargs = remaining[1:]
                return subcmd, subargs

            break

    return None


# -----------------------------------------------------------------------------
# Path & Workspace Inspection
# -----------------------------------------------------------------------------

def is_touching_tests_or_output(
    event: ParsedAuditEvent,
    repo_root: Optional[Union[str, Path]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Optional[str]]:
    """Determine if an audit event touches files or directories in 'tests' or 'test-output'.

    Returns:
        (is_touching, touched_path_str)
    """
    root = Path(repo_root).resolve() if repo_root else None
    cfg = config or {}

    # 1. File and search tools
    path_keys = [
        "AbsolutePath", "TargetFile", "NotebookPath", "DirectoryPath", "SearchPath", "SearchDirectory"
    ]
    for pk in path_keys:
        p_val = event.args.get(pk)
        if p_val and isinstance(p_val, (str, Path)):
            p_str = str(p_val)
            try:
                p_cls = paths.classify_path(p_str, config=cfg, repo_root=root, for_audit=True)
                if p_cls.base_class in ("tests", "test-output"):
                    return True, p_str
            except Exception:
                pass
            norm_lower = p_str.replace("\\", "/").lower()
            if any(seg in norm_lower for seg in ("/test/", "/tests/", "test_", "_test.", "/__pycache__/", "test-classes", "surefire", "failsafe", "coverage")):
                return True, p_str

    # 2. Command lines (run_command)
    if event.tool == "run_command":
        cmd_line = str(event.args.get("CommandLine", ""))
        cwd = event.args.get("Cwd")
        eff_root = root or (Path(cwd).resolve() if cwd else None)
        try:
            tokens = shlex.split(cmd_line)
        except Exception:
            tokens = cmd_line.split()

        for tok in tokens:
            if tok.startswith("-") or "=" in tok or tok in _RUNNERS:
                continue
            try:
                eff_path = tok
                if cwd and not os.path.isabs(tok):
                    eff_path = os.path.join(str(cwd), tok)
                p_cls = paths.classify_path(eff_path, config=cfg, repo_root=eff_root, for_audit=True)
                if p_cls.base_class in ("tests", "test-output"):
                    return True, tok
            except Exception:
                pass
            norm_lower = tok.replace("\\", "/").lower()
            if any(seg in norm_lower for seg in ("/test/", "/tests/", "test_", "_test.", "/__pycache__/", "test-classes", "surefire", "failsafe", "coverage")):
                return True, tok

    return False, None


def is_workspace_operation(
    event: ParsedAuditEvent,
    repo_root: Optional[Union[str, Path]] = None,
) -> bool:
    """Determine if an audit event represents an operation on workspace files or commands."""
    root = Path(repo_root).resolve() if repo_root else None

    if event.tool in ("view_file", "write_to_file", "replace_file_content", "notebook_edit", "grep_search", "find_by_name", "list_dir"):
        path_keys = ["AbsolutePath", "TargetFile", "NotebookPath", "DirectoryPath", "SearchPath", "SearchDirectory"]
        for pk in path_keys:
            val = event.args.get(pk)
            if val and root:
                try:
                    p = Path(val).resolve()
                    if str(p).startswith(str(root)):
                        return True
                except Exception:
                    pass
        return True

    if event.tool == "run_command":
        cwd = event.args.get("Cwd")
        if cwd and root:
            try:
                p = Path(cwd).resolve()
                if str(p).startswith(str(root)):
                    return True
            except Exception:
                pass
        return True

    return False


# -----------------------------------------------------------------------------
# Run Normalization
# -----------------------------------------------------------------------------

def normalize_runs(
    runs: Optional[Union[Dict[str, Any], Sequence[Dict[str, Any]], str, Path]],
    repo_root: Optional[Union[str, Path]] = None,
    task_id: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Normalize runs input into a dictionary keyed by run_id."""
    result: Dict[str, Dict[str, Any]] = {}
    if isinstance(runs, (str, Path)):
        p = Path(runs)
        if p.is_dir():
            for rf in p.glob("*.json"):
                try:
                    rd = json.loads(rf.read_text(encoding="utf-8"))
                    if isinstance(rd, dict) and "id" in rd:
                        result[rd["id"]] = rd
                except Exception:
                    pass
            return result
        elif p.is_file():
            try:
                rd = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(rd, dict):
                    if "id" in rd:
                        result[rd["id"]] = rd
                    else:
                        for k, v in rd.items():
                            if isinstance(v, dict):
                                result[v.get("id", k)] = v
                elif isinstance(rd, list):
                    for item in rd:
                        if isinstance(item, dict) and "id" in item:
                            result[item["id"]] = item
            except Exception:
                pass
            return result
    elif isinstance(runs, dict):
        for k, v in runs.items():
            if isinstance(v, dict):
                r_id = v.get("id", k)
                result[r_id] = v
        return result
    elif isinstance(runs, (list, tuple)):
        for item in runs:
            if isinstance(item, dict) and "id" in item:
                result[item["id"]] = item
        return result

    # Discover from .lintel/state/<task_id>/runs if repo_root is available
    if repo_root:
        root = Path(repo_root).resolve()
        state_dir = root / ".lintel" / "state"
        if task_id:
            r_dir = state_dir / task_id / "runs"
            if r_dir.is_dir():
                for rf in r_dir.glob("*.json"):
                    try:
                        rd = json.loads(rf.read_text(encoding="utf-8"))
                        if isinstance(rd, dict) and "id" in rd:
                            result[rd["id"]] = rd
                    except Exception:
                        pass
        elif state_dir.is_dir():
            for t_dir in state_dir.iterdir():
                if t_dir.is_dir():
                    r_dir = t_dir / "runs"
                    if r_dir.is_dir():
                        for rf in r_dir.glob("*.json"):
                            try:
                                rd = json.loads(rf.read_text(encoding="utf-8"))
                                if isinstance(rd, dict) and "id" in rd:
                                    result[rd["id"]] = rd
                            except Exception:
                                pass
    return result


def find_matching_run_for_enter(
    role: str,
    nonce: str,
    runs_map: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Locate a run matching role and nonce."""
    for r in runs_map.values():
        if r.get("role") == role and r.get("nonce") == nonce:
            return r
    return None


# -----------------------------------------------------------------------------
# Identity Tracking (M1b-04)
# -----------------------------------------------------------------------------

def track_identities(
    events: Union[str, Path, Iterable[Union[str, Dict[str, Any], ParsedAuditEvent]]],
    initial_orchestrator: Optional[str] = None,
    runs: Optional[Union[Dict[str, Any], Sequence[Dict[str, Any]], str, Path]] = None,
    repo_root: Optional[Union[str, Path]] = None,
    task_data: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> AuditIdentityResult:
    """Track orchestrator, bound personas, role reuse, unknown roles, and other conversations.

    Key behaviors:
    1. Identifies orchestrator from task start, task.orchestrator, or initial_orchestrator.
    2. Moves orchestrator to conv-B when conv-B executes 'task resume'.
    3. Binds persona when a conversation executes 'enter <role> <nonce>' with a valid nonce.
    4. Detects role reuse (crossing) if the conversation was holding another role (orchestrator or different persona).
    5. During an active persona run, unrecognized conversations touching workspace files land as 'unknown' role.
    6. Touches of tests or test-output by an unknown role are flagged as 'unknown-role' crossings.
    7. Other conversations are classified as 'other' and event counts are tracked.
    """
    parsed_events = parse_audit_log(events)
    runs_map = normalize_runs(runs, repo_root=repo_root)

    # 1. Resolve initial orchestrator
    current_orchestrator = initial_orchestrator
    if current_orchestrator is None and task_data:
        orch = task_data.get("orchestrator")
        if isinstance(orch, dict):
            current_orchestrator = orch.get("conversation_id")
        elif isinstance(orch, str):
            current_orchestrator = orch

    if current_orchestrator is None:
        for ev in parsed_events:
            cmd_info = extract_lintel_command(ev)
            if cmd_info:
                subcmd, subargs = cmd_info
                if subcmd == "task" and subargs and subargs[0] == "start":
                    current_orchestrator = ev.conversation_id
                    break

    orchestrator_history: List[Tuple[str, str]] = []
    conversation_roles: Dict[str, str] = {}

    def _record_orchestrator(cid: str, ts: str) -> None:
        nonlocal current_orchestrator
        current_orchestrator = cid
        conversation_roles[cid] = "orchestrator"
        if not orchestrator_history or orchestrator_history[-1][0] != cid:
            orchestrator_history.append((cid, ts))

    if current_orchestrator:
        first_ts = parsed_events[0].at if parsed_events else ""
        _record_orchestrator(current_orchestrator, first_ts)

    active_persona_conv: Optional[str] = None
    active_persona_role: Optional[str] = None
    active_persona_run_id: Optional[str] = None

    bound_personas: Dict[str, str] = {}
    other_counts: Dict[str, int] = {}
    findings: List[IdentityFinding] = []
    unknown_events: List[ParsedAuditEvent] = []
    annotated_events: List[ParsedAuditEvent] = []
    seen_finding_ids: Set[str] = set()

    for ev in parsed_events:
        calling_conv = ev.conversation_id
        cmd_info = extract_lintel_command(ev)

        # ---------------------------------------------------------------------
        # Check CLI commands that alter identity state
        # ---------------------------------------------------------------------
        if cmd_info:
            subcmd, subargs = cmd_info

            # Case: task start
            if subcmd == "task" and subargs and subargs[0] == "start":
                _record_orchestrator(calling_conv, ev.at)
                ev.role = "orchestrator"

            # Case: task resume
            elif subcmd == "task" and subargs and subargs[0] == "resume":
                _record_orchestrator(calling_conv, ev.at)
                ev.role = "orchestrator"

            # Case: enter <role> <nonce>
            elif subcmd == "enter" and len(subargs) >= 2:
                role = subargs[0]
                nonce = subargs[1]

                matching_run: Optional[Dict[str, Any]] = None
                if runs_map:
                    matching_run = find_matching_run_for_enter(role, nonce, runs_map)
                else:
                    matching_run = {"id": f"{role}-run", "role": role, "nonce": nonce}

                if matching_run is not None:
                    # Valid nonce
                    prev_role = conversation_roles.get(calling_conv)
                    is_role_reuse = False

                    if calling_conv == current_orchestrator:
                        is_role_reuse = True
                        prev_role = "orchestrator"
                    elif prev_role is not None and prev_role != role:
                        is_role_reuse = True

                    if is_role_reuse:
                        f_id = generate_finding_id(calling_conv, ev.step_idx, CAT_ROLE_REUSE)
                        if f_id not in seen_finding_ids:
                            seen_finding_ids.add(f_id)
                            finding = IdentityFinding(
                                kind=KIND_CROSSING,
                                category=CAT_ROLE_REUSE,
                                role=role,
                                conversation_id=calling_conv,
                                run_id=matching_run.get("id"),
                                step_idx=ev.step_idx,
                                at=ev.at,
                                details=f"Conversation '{calling_conv}' already holding role '{prev_role}' reused for role '{role}'",
                                finding_id=f_id,
                            )
                            findings.append(finding)

                    # Binding still happens
                    active_persona_conv = calling_conv
                    active_persona_role = role
                    active_persona_run_id = matching_run.get("id")
                    bound_personas[calling_conv] = role
                    conversation_roles[calling_conv] = role
                    ev.role = role
                    ev.run_id = active_persona_run_id

            # Case: done
            elif subcmd == "done":
                if calling_conv == active_persona_conv:
                    ev.role = active_persona_role
                    ev.run_id = active_persona_run_id
                    active_persona_conv = None
                    active_persona_role = None
                    active_persona_run_id = None

        # ---------------------------------------------------------------------
        # Role classification for the event
        # ---------------------------------------------------------------------
        if ev.role is None:
            if calling_conv == active_persona_conv:
                ev.role = active_persona_role
                ev.run_id = active_persona_run_id
            elif calling_conv == current_orchestrator:
                ev.role = "orchestrator"
            elif active_persona_conv is not None:
                # Persona run is active, but calling_conv is neither orchestrator nor active persona
                touches_test, path_touched = is_touching_tests_or_output(ev, repo_root=repo_root, config=config)
                is_ws = is_workspace_operation(ev, repo_root=repo_root)

                if touches_test:
                    ev.role = "unknown"
                    ev.run_id = active_persona_run_id
                    unknown_events.append(ev)
                    f_id = generate_finding_id(calling_conv, ev.step_idx, CAT_UNKNOWN_ROLE, path_touched)
                    if f_id not in seen_finding_ids:
                        seen_finding_ids.add(f_id)
                        finding = IdentityFinding(
                            kind=KIND_CROSSING,
                            category=CAT_UNKNOWN_ROLE,
                            role="unknown",
                            conversation_id=calling_conv,
                            run_id=active_persona_run_id,
                            step_idx=ev.step_idx,
                            at=ev.at,
                            tool=ev.tool,
                            path=path_touched,
                            details=f"Unknown role conversation '{calling_conv}' touched {path_touched} during active {active_persona_role} run '{active_persona_run_id}'",
                            finding_id=f_id,
                        )
                        findings.append(finding)
                elif is_ws:
                    # Persona conversationId changed mid-run: subsequent reads land as unknown role
                    ev.role = "unknown"
                    ev.run_id = active_persona_run_id
                    unknown_events.append(ev)
                else:
                    ev.role = "other"
                    other_counts[calling_conv] = other_counts.get(calling_conv, 0) + 1
            else:
                # No active persona run, not orchestrator
                ev.role = "other"
                other_counts[calling_conv] = other_counts.get(calling_conv, 0) + 1

        annotated_events.append(ev)

    return AuditIdentityResult(
        orchestrator_id=current_orchestrator,
        orchestrator_history=orchestrator_history,
        bound_personas=bound_personas,
        events=annotated_events,
        findings=findings,
        other_counts=other_counts,
        unknown_events=unknown_events,
        stopped_personas=[],
    )


# -----------------------------------------------------------------------------
# Hook Evidence Verification (M1b-04)
# -----------------------------------------------------------------------------

def check_hook_evidence(
    runs: Union[Dict[str, Any], Sequence[Dict[str, Any]], str, Path],
    audit_events: Union[str, Path, Iterable[Union[str, Dict[str, Any], ParsedAuditEvent]]],
    repo_root: Optional[Union[str, Path]] = None,
    task_data: Optional[Dict[str, Any]] = None,
    base_dir: Optional[Union[str, Path]] = None,
) -> List[IdentityFinding]:
    """Evaluate runs for hook evidence vs persona stopped before enter.

    For each run:
    - If audit log has an 'enter' event for this run -> hook evidence is present.
    - If audit log has NO 'enter' event for this run:
      - Inspect invoking conversation's child record and mailbox/transcript (via agy_readers).
      - If persona stopped before enter (child record KILLED, stop notice, denial, restart) ->
        Reported as a STOPPED PERSONA (informational finding, NOT a crossing).
        Role is also read from child record (typeName, role).
      - If persona has no hook evidence and NO stop notice / denial ->
        Flagged as a CROSSING ('no-hook-evidence').
    """
    parsed_events = parse_audit_log(audit_events)
    runs_map = normalize_runs(runs, repo_root=repo_root)
    evidence_findings: List[IdentityFinding] = []

    for run_id, run in runs_map.items():
        role = run.get("role") or ""
        nonce = run.get("nonce") or ""

        # 1. Check if audit log contains an enter event for this run
        has_enter_event = False
        for ev in parsed_events:
            cmd_info = extract_lintel_command(ev)
            if cmd_info:
                subcmd, subargs = cmd_info
                if subcmd == "enter" and len(subargs) >= 2:
                    if subargs[0] == role and subargs[1] == nonce:
                        has_enter_event = True
                        break
            elif ev.tool == "enter":
                r_arg = ev.args.get("role") or (ev.args.get("args") or [None])[0]
                n_arg = ev.args.get("nonce") or (ev.args.get("args") or [None, None])[1]
                if r_arg == role and n_arg == nonce:
                    has_enter_event = True
                    break

        if has_enter_event:
            # Hook evidence is confirmed
            continue

        # 2. No enter event found: locate invoking conversation context
        inv_conv_id: Optional[str] = None
        inv_art_dir: Optional[Path] = None

        # Search audit events for invoke_subagent or send_message matching this run
        for ev in parsed_events:
            if ev.tool in ("invoke_subagent", "send_message"):
                m = ev.marker or {}
                prompt = ev.prompt or ""
                target = ev.target or ""
                marker_match = (m.get("nonce") == nonce) if nonce else False
                prompt_match = (f":{nonce}]" in prompt or f"enter {role} {nonce}" in prompt) if nonce else False
                target_match = (target in (role, f"lintel-{role}"))

                if marker_match or prompt_match or target_match:
                    inv_conv_id = ev.conversation_id
                    if ev.artifact_directory_path:
                        inv_art_dir = Path(ev.artifact_directory_path)
                        if not inv_art_dir.is_absolute():
                            root_cand = Path(base_dir) if base_dir else (Path(repo_root) if repo_root else Path.cwd())
                            inv_art_dir = (root_cand / inv_art_dir).resolve()
                    break

        # Fallback to run hints
        if not inv_art_dir or not inv_art_dir.is_dir():
            hints = run.get("hints") or {}
            if isinstance(hints, dict):
                h_conv = hints.get("conversation_id")
                h_app = hints.get("app_data_dir")
                if h_conv and not inv_conv_id:
                    inv_conv_id = h_conv
                if h_conv and h_app:
                    cand = Path(h_app) / "brain" / h_conv
                    if cand.is_dir():
                        inv_art_dir = cand

        # Fallback to task orchestrator
        if (not inv_art_dir or not inv_art_dir.is_dir()) and task_data:
            orch = task_data.get("orchestrator")
            orch_id = orch.get("conversation_id") if isinstance(orch, dict) else orch
            for ev in parsed_events:
                if ev.artifact_directory_path:
                    p = Path(ev.artifact_directory_path)
                    if p.parent.name == "brain" and orch_id:
                        cand = p.parent / orch_id
                        if cand.is_dir():
                            inv_art_dir = cand
                            if not inv_conv_id:
                                inv_conv_id = orch_id
                            break

        # 3. Inspect whether persona stopped before enter
        is_stopped = False
        stop_cause: Optional[str] = None
        stop_details: str = ""
        child_role: Optional[str] = None

        # Check recorded stop on run
        if run.get("stop"):
            s_obj = run["stop"]
            cause = s_obj.get("cause") if isinstance(s_obj, dict) else s_obj
            if cause in ("killed", "permission-denied", "restart", "forced", "ended-without-verdict", "never-entered"):
                is_stopped = True
                stop_cause = str(cause)
                stop_details = f"Recorded stop cause: {cause}"

        # Inspect using agy_readers if invoking artifact directory is available
        if inv_art_dir and inv_art_dir.is_dir():
            # Check child record
            child_id, child_rec = agy_readers.find_child_record(
                invoking_artifact_dir=inv_art_dir,
                role=role,
                nonce=nonce,
            )
            if child_rec:
                desc = child_rec.get("subagentDescriptor") or {}
                child_role = desc.get("role") or desc.get("typeName")
                rec_state = str(child_rec.get("state") or "")
                if "KILLED" in rec_state.upper() or rec_state.lower() == "killed":
                    is_stopped = True
                    stop_cause = "killed"
                    stop_details = "Child subagent record state is KILLED"

            obs = agy_readers.inspect_agy_state(
                invoking_artifact_dir=inv_art_dir,
                invoking_conversation_id=inv_conv_id,
                role=role,
                nonce=nonce,
                hints=run.get("hints"),
                base_dir=base_dir or repo_root,
            )
            if obs.status in ("killed", "permission-denied", "stopped-by-restart") or obs.stop_cause in ("killed", "permission-denied", "restart"):
                is_stopped = True
                stop_cause = obs.stop_cause or ("killed" if obs.status == "killed" else ("permission-denied" if obs.status == "permission-denied" else "restart"))
                stop_details = obs.details or obs.status

        effective_role = child_role or role

        if is_stopped:
            # Persona stopped before enter: Informational, NOT a crossing
            f_id = generate_finding_id(inv_conv_id, 0, CAT_STOPPED_PERSONA, run_id)
            finding = IdentityFinding(
                kind=KIND_INFORMATIONAL,
                category=CAT_STOPPED_PERSONA,
                role=effective_role,
                run_id=run_id,
                conversation_id=inv_conv_id,
                stop_cause=stop_cause or "killed",
                details=f"Persona for run '{run_id}' ({effective_role}) stopped before enter ({stop_cause}): {stop_details}",
                finding_id=f_id,
            )
            evidence_findings.append(finding)
        else:
            # No hook evidence and no stop notice: CROSSING
            f_id = generate_finding_id(inv_conv_id, 0, CAT_NO_HOOK_EVIDENCE, run_id)
            finding = IdentityFinding(
                kind=KIND_CROSSING,
                category=CAT_NO_HOOK_EVIDENCE,
                role=effective_role,
                run_id=run_id,
                conversation_id=inv_conv_id,
                details=f"No hook evidence for run '{run_id}' ({effective_role}): persona was not stopped before enter",
                finding_id=f_id,
            )
            evidence_findings.append(finding)

    return evidence_findings


# -----------------------------------------------------------------------------
# High-Level Interface: resolve_identities
# -----------------------------------------------------------------------------

def resolve_identities(
    audit_events: Union[str, Path, Iterable[Union[str, Dict[str, Any], ParsedAuditEvent]]],
    repo_root: Optional[Union[str, Path]] = None,
    task_data: Optional[Dict[str, Any]] = None,
    runs_data: Optional[Union[Dict[str, Any], Sequence[Dict[str, Any]], str, Path]] = None,
    config: Optional[Dict[str, Any]] = None,
    initial_orchestrator: Optional[str] = None,
    base_dir: Optional[Union[str, Path]] = None,
) -> AuditIdentityResult:
    """Resolve all identities, bound personas, role-reuse crossings, unknown roles, and hook evidence."""
    parsed_events = parse_audit_log(audit_events)
    runs_map = normalize_runs(runs_data, repo_root=repo_root)

    # 1. Track identities through event stream
    result = track_identities(
        events=parsed_events,
        initial_orchestrator=initial_orchestrator,
        runs=runs_map,
        repo_root=repo_root,
        task_data=task_data,
        config=config,
    )

    # 2. Check hook evidence across runs
    if runs_map:
        evidence_findings = check_hook_evidence(
            runs=runs_map,
            audit_events=result.events,
            repo_root=repo_root,
            task_data=task_data,
            base_dir=base_dir,
        )
        for ef in evidence_findings:
            if ef.category == CAT_STOPPED_PERSONA:
                result.stopped_personas.append(ef)
            result.findings.append(ef)

    return result


# -----------------------------------------------------------------------------
# Crossing Detection Helpers (M1b-05)
# -----------------------------------------------------------------------------

_SIBLING_STORE_NAMES = frozenset({
    "conversations",
    "history.jsonl",
    "conversation_summaries.db",
    "annotations",
    "log",
    "cli.log",
    "implicit",
    "jetbox_summaries_proto.pb",
    "worktrees",
})


def _normalize_candidate_path(path_str: str) -> str:
    """Normalize path string, stripping file://, expanding ~, and normalizing slashes."""
    s = str(path_str).strip()
    if s.startswith("file://"):
        s = s[7:]
    if s.startswith("~/") or s == "~":
        s = os.path.expanduser(s)
    elif s.startswith("$HOME/") or s == "$HOME":
        home = os.environ.get("HOME", "")
        s = home + s[5:] if s != "$HOME" else home
    s = s.replace("\\", "/")
    return s


def check_transcript_read(
    event: ParsedAuditEvent,
    repo_root: Optional[Union[str, Path]] = None,
) -> Optional[AuditFinding]:
    """Check if event reads another conversation's transcript, artifact dir, or app data store.

    PLAN §3.7 rules:
    - Path resolving under <root>/<other_id>/ for any conversation other than the event's own.
    - Sibling stores in app data dir (conversations/*.db, history.jsonl, etc.).
    - Recursive search rooted at brain root, app data dir, or ~/.gemini.
    - Glob over brain/*.
    - Relative file path as a failed attempt.
    - Own-directory reads are permitted.
    - Plain list_dir of brain root is informational (returns None here).
    """
    event_conv_id = event.conversation_id
    art_path = event.artifact_directory_path
    brain_root = None
    app_data_dir = None
    if art_path:
        norm_art = _normalize_candidate_path(art_path)
        art_p = Path(norm_art)
        if art_p.name == event_conv_id:
            brain_root = art_p.parent
            app_data_dir = brain_root.parent

    # Candidate path tokens
    candidate_tokens: List[Tuple[str, str]] = []  # (token, source_type: 'path', 'search_root', 'command')

    # 1. File tools
    for pk in ("AbsolutePath", "TargetFile", "NotebookPath", "DirectoryPath", "Url", "Uri"):
        val = event.args.get(pk)
        if val and isinstance(val, (str, Path)):
            candidate_tokens.append((str(val), "path" if pk != "DirectoryPath" else "dir"))

    # 2. Search tools
    for rk in ("SearchPath", "SearchDirectory"):
        val = event.args.get(rk)
        if val and isinstance(val, (str, Path)):
            candidate_tokens.append((str(val), "search_root"))

    # 3. Command line
    if event.tool == "run_command":
        cmd_line = str(event.args.get("CommandLine", ""))
        # Check glob over brain/* directly on cmd_line
        if re.search(r"(?:^|[\s\"'/])brain/\*", cmd_line) or "brain/*" in cmd_line:
            f_id = generate_finding_id(event_conv_id, event.step_idx, CAT_TRANSCRIPT_READ, "brain/*")
            return AuditFinding(
                id=f_id,
                kind=KIND_CROSSING,
                category=CAT_TRANSCRIPT_READ,
                role=event.role,
                conversation_id=event_conv_id,
                step_idx=event.step_idx,
                at=event.at,
                path="brain/*",
                tool=event.tool,
                command=cmd_line[:200],
                message=f"Glob over brain/* in command line: {cmd_line[:200]}",
                details="Glob over brain/*",
            )

        try:
            tokens = shlex.split(cmd_line)
        except Exception:
            tokens = cmd_line.split()

        cmd_word = os.path.basename(tokens[0]) if tokens else ""
        is_search = cmd_word in ("grep", "egrep", "fgrep", "rg", "ag", "ack", "find")

        for idx, tok in enumerate(tokens):
            if tok.startswith("-") or "=" in tok:
                continue
            if is_search and idx > 0:
                candidate_tokens.append((tok, "search_root"))
            else:
                candidate_tokens.append((tok, "command"))

    # Now evaluate each candidate token
    for raw_tok, src_type in candidate_tokens:
        tok_clean = raw_tok.strip().strip("'\"")
        if not tok_clean:
            continue

        # Check glob over brain/* in token
        if "brain/*" in tok_clean or tok_clean.endswith("brain/*"):
            f_id = generate_finding_id(event_conv_id, event.step_idx, CAT_TRANSCRIPT_READ, tok_clean)
            return AuditFinding(
                id=f_id,
                kind=KIND_CROSSING,
                category=CAT_TRANSCRIPT_READ,
                role=event.role,
                conversation_id=event_conv_id,
                step_idx=event.step_idx,
                at=event.at,
                path=tok_clean,
                tool=event.tool,
                command=event.args.get("CommandLine"),
                message=f"Glob over brain/*: {tok_clean}",
                details="Glob over brain/*",
            )

        # Check relative file path targeting transcript / brain
        if "brain" in tok_clean and (tok_clean.startswith("../") or tok_clean.startswith("./")):
            f_id = generate_finding_id(event_conv_id, event.step_idx, CAT_TRANSCRIPT_READ, tok_clean)
            return AuditFinding(
                id=f_id,
                kind=KIND_CROSSING,
                category=CAT_TRANSCRIPT_READ,
                role=event.role,
                conversation_id=event_conv_id,
                step_idx=event.step_idx,
                at=event.at,
                path=tok_clean,
                tool=event.tool,
                command=event.args.get("CommandLine"),
                message=f"Relative path access to transcript / brain: {tok_clean}",
                details="Relative file path targeting brain",
            )

        norm_p = _normalize_candidate_path(tok_clean)
        norm_lower = norm_p.lower()

        # Check if search root is brain root, app data dir, or ~/.gemini
        if src_type in ("search_root", "dir") and event.tool != "list_dir":
            if (
                norm_lower.endswith("/.gemini")
                or norm_lower.endswith("/.gemini/")
                or "/.gemini/antigravity" in norm_lower
                or (brain_root and norm_p == str(brain_root).replace("\\", "/"))
                or (app_data_dir and norm_p == str(app_data_dir).replace("\\", "/"))
                or re.search(r"/\.gemini/[^/]+/brain/?$", norm_lower)
                or re.search(r"/\.gemini/[^/]+/?$", norm_lower)
            ):
                f_id = generate_finding_id(event_conv_id, event.step_idx, CAT_TRANSCRIPT_READ, norm_p)
                return AuditFinding(
                    id=f_id,
                    kind=KIND_CROSSING,
                    category=CAT_TRANSCRIPT_READ,
                    role=event.role,
                    conversation_id=event_conv_id,
                    step_idx=event.step_idx,
                    at=event.at,
                    path=norm_p,
                    tool=event.tool,
                    command=event.args.get("CommandLine"),
                    message=f"Search rooted at brain root, app data dir, or ~/.gemini: {norm_p}",
                    details="Search rooted at brain root or app data dir",
                )

        # Check plain list_dir of brain root: informational, not a crossing!
        if event.tool == "list_dir" and (
            (brain_root and norm_p == str(brain_root).replace("\\", "/"))
            or re.search(r"/\.gemini/[^/]+/brain/?$", norm_lower)
        ):
            continue

        # Check access to another conversation's artifact dir or transcript
        m_conv = re.search(r"/\.gemini/[^/]+/brain/([^/]+)(?:/.*)?$", norm_lower)
        if m_conv:
            target_cid = m_conv.group(1)
            if target_cid != event_conv_id.lower():
                f_id = generate_finding_id(event_conv_id, event.step_idx, CAT_TRANSCRIPT_READ, norm_p)
                return AuditFinding(
                    id=f_id,
                    kind=KIND_CROSSING,
                    category=CAT_TRANSCRIPT_READ,
                    role=event.role,
                    conversation_id=event_conv_id,
                    step_idx=event.step_idx,
                    at=event.at,
                    path=norm_p,
                    tool=event.tool,
                    command=event.args.get("CommandLine"),
                    message=f"Read of another conversation's transcript/artifact dir ({target_cid}): {norm_p}",
                    details=f"Target conversation {target_cid} != own {event_conv_id}",
                )
            else:
                # Own directory read is permitted
                continue

        # If brain_root is known, check if target resolves under brain_root/<other_cid>
        if brain_root:
            br_str = str(brain_root).replace("\\", "/").lower()
            if norm_lower.startswith(br_str + "/"):
                rel_under = norm_p[len(br_str) + 1 :]
                target_cid = rel_under.split("/")[0]
                if target_cid.lower() != event_conv_id.lower():
                    f_id = generate_finding_id(event_conv_id, event.step_idx, CAT_TRANSCRIPT_READ, norm_p)
                    return AuditFinding(
                        id=f_id,
                        kind=KIND_CROSSING,
                        category=CAT_TRANSCRIPT_READ,
                        role=event.role,
                        conversation_id=event_conv_id,
                        step_idx=event.step_idx,
                        at=event.at,
                        path=norm_p,
                        tool=event.tool,
                        command=event.args.get("CommandLine"),
                        message=f"Read of another conversation's artifact dir ({target_cid}): {norm_p}",
                        details=f"Target conversation {target_cid} != own {event_conv_id}",
                    )
                else:
                    continue

        # Check sibling stores under app data dir
        is_sibling_store = False
        for sname in _SIBLING_STORE_NAMES:
            if f"/{sname}" in norm_lower or norm_lower.endswith(f"/{sname}"):
                if "/.gemini" in norm_lower or (app_data_dir and str(app_data_dir).replace("\\", "/").lower() in norm_lower):
                    is_sibling_store = True
                    break
        if is_sibling_store:
            f_id = generate_finding_id(event_conv_id, event.step_idx, CAT_TRANSCRIPT_READ, norm_p)
            return AuditFinding(
                id=f_id,
                kind=KIND_CROSSING,
                category=CAT_TRANSCRIPT_READ,
                role=event.role,
                conversation_id=event_conv_id,
                step_idx=event.step_idx,
                at=event.at,
                path=norm_p,
                tool=event.tool,
                command=event.args.get("CommandLine"),
                message=f"Read of sibling store in app data dir: {norm_p}",
                details="Sibling store read in app data dir",
            )

    return None


def check_read_outside_lane(
    event: ParsedAuditEvent,
    repo_root: Path,
    config: Optional[Dict[str, Any]] = None,
    task_data: Optional[Dict[str, Any]] = None,
    locked_phase: Optional[int] = None,
    code_changed_since_lock: Optional[Set[str]] = None,
    task_id: Optional[str] = None,
    run_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[AuditFinding]:
    """Check if event reads outside the role's allowed lane."""
    findings: List[AuditFinding] = []
    role = event.role or "unknown"
    cfg = config or {}

    # 1. File read tools: view_file, etc.
    tool_info = get_tool_info(event.tool)
    if tool_info.kind == "read" or event.tool in ("view_file", "read_file", "read_resource"):
        path_args = tool_info.path_args or ["AbsolutePath", "Uri"]
        for pa in path_args:
            val = event.args.get(pa)
            if not val:
                continue
            p_str = _normalize_candidate_path(str(val))
            if not os.path.isabs(p_str):
                p_str = os.path.normpath(os.path.join(str(repo_root), p_str))

            try:
                p_cls = paths.classify_path(p_str, config=cfg, repo_root=repo_root, for_audit=True)
            except Exception:
                p_cls = paths.PathClass("code", base_class="code")

            rel = paths.normalize_path(p_str, repo_root=repo_root, resolve_symlinks=False)

            # Check .lintel/state/ read
            if rel.startswith(".lintel/state/") or rel == ".lintel/state":
                f_id = generate_finding_id(event.conversation_id, event.step_idx, CAT_READ_OUTSIDE_LANE, rel)
                findings.append(
                    AuditFinding(
                        id=f_id,
                        kind=KIND_CROSSING,
                        category=CAT_READ_OUTSIDE_LANE,
                        role=role,
                        conversation_id=event.conversation_id,
                        step_idx=event.step_idx,
                        at=event.at,
                        path=rel,
                        tool=event.tool,
                        message=f"Role '{role}' read outside lane targeting .lintel/state: {rel}",
                        details="Read of .lintel/state/ path",
                    )
                )
                continue

            # Roles that should not read tests
            if role in ROLES_SHOULD_NOT_READ_TESTS:
                if p_cls.base_class in ("tests", "test-output") or paths.is_test_side_task_path(
                    rel, runs=run_records, task_id=task_id, repo_root=repo_root
                ):
                    f_id = generate_finding_id(event.conversation_id, event.step_idx, CAT_READ_OUTSIDE_LANE, rel)
                    findings.append(
                        AuditFinding(
                            id=f_id,
                            kind=KIND_CROSSING,
                            category=CAT_READ_OUTSIDE_LANE,
                            role=role,
                            conversation_id=event.conversation_id,
                            step_idx=event.step_idx,
                            at=event.at,
                            path=rel,
                            tool=event.tool,
                            message=f"Role '{role}' read outside lane targeting tests: {rel}",
                            details="Read of tests path",
                        )
                    )
                    continue

            # QA reading code changed since lock during locked phase
            if role in ("qa", "qa-reviewer") and locked_phase is not None:
                if p_cls.base_class == "code":
                    if code_changed_since_lock and rel in code_changed_since_lock:
                        f_id = generate_finding_id(event.conversation_id, event.step_idx, CAT_READ_OUTSIDE_LANE, rel)
                        findings.append(
                            AuditFinding(
                                id=f_id,
                                kind=KIND_CROSSING,
                                category=CAT_READ_OUTSIDE_LANE,
                                role=role,
                                conversation_id=event.conversation_id,
                                step_idx=event.step_idx,
                                at=event.at,
                                path=rel,
                                tool=event.tool,
                                message=f"QA read code changed since lock: {rel}",
                                details="QA read code during locked phase",
                            )
                        )

    # 2. Command lines: run_command
    if event.tool == "run_command":
        cmd_line = str(event.args.get("CommandLine", ""))
        cwd = event.args.get("Cwd") or repo_root
        sh_findings = shell.scan_command_line(
            cmd_line,
            cwd=cwd,
            role=role,
            repo_root=repo_root,
            config=cfg,
            task_id=task_id,
            run_records=run_records,
        )
        for sf in sh_findings:
            if sf.kind == KIND_CROSSING and sf.category == shell.CAT_READ_OUTSIDE_LANE:
                f_id = generate_finding_id(event.conversation_id, event.step_idx, CAT_READ_OUTSIDE_LANE, sf.path)
                findings.append(
                    AuditFinding(
                        id=f_id,
                        kind=KIND_CROSSING,
                        category=CAT_READ_OUTSIDE_LANE,
                        role=role,
                        conversation_id=event.conversation_id,
                        step_idx=event.step_idx,
                        at=event.at,
                        path=sf.path,
                        tool=event.tool,
                        command=cmd_line[:200],
                        message=sf.message,
                        details=sf.message,
                    )
                )

    return findings


def check_git_outside_allowlist(
    event: ParsedAuditEvent,
    repo_root: Path,
    config: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    run_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[AuditFinding]:
    """Check if command line executes a git command outside the allow-list."""
    findings: List[AuditFinding] = []
    if event.tool != "run_command":
        return findings

    role = event.role or "unknown"
    if role not in ROLES_SHOULD_NOT_READ_TESTS:
        return findings

    cmd_line = str(event.args.get("CommandLine", ""))
    cwd = event.args.get("Cwd") or repo_root
    sh_findings = shell.scan_command_line(
        cmd_line,
        cwd=cwd,
        role=role,
        repo_root=repo_root,
        config=config,
        task_id=task_id,
        run_records=run_records,
    )
    for sf in sh_findings:
        if sf.kind == KIND_CROSSING and sf.category in (shell.CAT_GIT_OUTSIDE_ALLOWLIST, shell.CAT_GIT_FRONTEND):
            f_id = generate_finding_id(event.conversation_id, event.step_idx, CAT_GIT_OUTSIDE_ALLOWLIST, sf.path or sf.command)
            findings.append(
                AuditFinding(
                    id=f_id,
                    kind=KIND_CROSSING,
                    category=CAT_GIT_OUTSIDE_ALLOWLIST,
                    role=role,
                    conversation_id=event.conversation_id,
                    step_idx=event.step_idx,
                    at=event.at,
                    path=sf.path,
                    tool=event.tool,
                    command=cmd_line[:200],
                    message=sf.message,
                    details=sf.message,
                )
            )

    return findings


def check_test_search(
    event: ParsedAuditEvent,
    repo_root: Path,
    config: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    run_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[AuditFinding]:
    """Check if search tool or command has its search root in tests."""
    findings: List[AuditFinding] = []
    role = event.role or "unknown"
    if role not in ROLES_SHOULD_NOT_READ_TESTS:
        return findings

    cfg = config or {}

    # 1. grep_search tool
    if event.tool == "grep_search":
        s_path = event.args.get("SearchPath")
        if s_path:
            norm_s = _normalize_candidate_path(str(s_path))
            if not os.path.isabs(norm_s):
                norm_s = os.path.normpath(os.path.join(str(repo_root), norm_s))
            try:
                p_cls = paths.classify_path(norm_s, config=cfg, repo_root=repo_root, is_dir=True, for_audit=True)
            except Exception:
                p_cls = paths.PathClass("code", base_class="code")
            rel = paths.normalize_path(norm_s, repo_root=repo_root, resolve_symlinks=False)

            if p_cls.base_class in ("tests", "test-output") or paths.is_test_side_task_path(
                rel, runs=run_records, task_id=task_id, repo_root=repo_root
            ):
                f_id = generate_finding_id(event.conversation_id, event.step_idx, CAT_TEST_SEARCH, rel)
                findings.append(
                    AuditFinding(
                        id=f_id,
                        kind=KIND_CROSSING,
                        category=CAT_TEST_SEARCH,
                        role=role,
                        conversation_id=event.conversation_id,
                        step_idx=event.step_idx,
                        at=event.at,
                        path=rel,
                        tool=event.tool,
                        message=f"Search tool '{event.tool}' rooted at tests: {rel}",
                        details="Content search rooted at tests",
                    )
                )

    # 2. Command lines (grep -r, rg, etc.)
    if event.tool == "run_command":
        cmd_line = str(event.args.get("CommandLine", ""))
        cwd = event.args.get("Cwd") or repo_root
        sh_findings = shell.scan_command_line(
            cmd_line,
            cwd=cwd,
            role=role,
            repo_root=repo_root,
            config=cfg,
            task_id=task_id,
            run_records=run_records,
        )
        for sf in sh_findings:
            if sf.kind == KIND_CROSSING and sf.category == shell.CAT_TEST_SEARCH:
                f_id = generate_finding_id(event.conversation_id, event.step_idx, CAT_TEST_SEARCH, sf.path)
                findings.append(
                    AuditFinding(
                        id=f_id,
                        kind=KIND_CROSSING,
                        category=CAT_TEST_SEARCH,
                        role=role,
                        conversation_id=event.conversation_id,
                        step_idx=event.step_idx,
                        at=event.at,
                        path=sf.path,
                        tool=event.tool,
                        command=cmd_line[:200],
                        message=sf.message,
                        details=sf.message,
                    )
                )

    return findings


def check_write_outside_lane(
    event: ParsedAuditEvent,
    repo_root: Path,
    config: Optional[Dict[str, Any]] = None,
    task_data: Optional[Dict[str, Any]] = None,
    locked_phase: Optional[int] = None,
    is_docs_update: bool = False,
    task_id: Optional[str] = None,
    run_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[AuditFinding]:
    """Check if event writes outside the role's allowed lane."""
    findings: List[AuditFinding] = []
    role = event.role or "unknown"
    cfg = config or {}

    def _evaluate_target_write(target_path_str: str, source_desc: str) -> Optional[str]:
        """Return violation reason if writing to target_path_str is not allowed, else None."""
        p_str = _normalize_candidate_path(target_path_str)
        if not os.path.isabs(p_str):
            p_str = os.path.normpath(os.path.join(str(repo_root), p_str))

        rel = paths.normalize_path(p_str, repo_root=repo_root, resolve_symlinks=False)

        # 1. State directory write
        if rel.startswith(".lintel/state/") or rel == ".lintel/state":
            return f"Writing into .lintel/state/ is forbidden: {rel}"

        # 2. Plugin directory write
        if rel.startswith("plugin/") or rel == "plugin":
            return f"Writing into plugin directory is forbidden: {rel}"

        # 3. Lintel configuration / scripts write
        try:
            p_cls = paths.classify_path(p_str, config=cfg, repo_root=repo_root, for_audit=True)
        except Exception:
            p_cls = paths.PathClass("code", base_class="code")

        if p_cls.base_class == "lintel":
            return f"Writing into lintel files is forbidden: {rel}"

        if p_cls.base_class == "agent-config":
            return f"Writing into agent configuration is forbidden: {rel}"

        # 4. Living-docs write
        if p_cls.base_class == "living-docs":
            if not is_docs_update:
                return f"Writing into living-docs outside docs update is forbidden: {rel}"
            else:
                if role in ("po", "po-reviewer"):
                    if not rel.startswith(".lintel/specs/"):
                        return f"PO may only write .lintel/specs/ during docs update: {rel}"
                elif role in ("architect", "architect-reviewer"):
                    if rel.endswith("testing.md") or not rel.startswith(".lintel/steering/"):
                        return f"Architect may only write .lintel/steering/ (except testing.md) during docs update: {rel}"
                else:
                    return f"Role '{role}' is not allowed to write living-docs: {rel}"

        # 5. Role write lanes
        if role in ("dev", "dev-reviewer"):
            if p_cls.base_class in ("tests", "test-output") or paths.is_test_side_task_path(
                rel, runs=run_records, task_id=task_id, repo_root=repo_root
            ):
                return f"DEV is not allowed to write tests: {rel}"
        elif role in ("qa", "qa-reviewer"):
            if locked_phase is not None and p_cls.base_class == "code":
                return f"QA is not allowed to write code after lock: {rel}"
        elif role in ("po", "po-reviewer"):
            if not is_docs_update and rel != "shared/requirements.md" and not rel.startswith(f".lintel/tasks/{task_id or ''}/po"):
                return f"PO is not allowed to write outside requirements.md: {rel}"
        elif role in ("architect", "architect-reviewer"):
            if not is_docs_update:
                allowed_arch = rel in ("shared/plan.md") or rel.startswith("shared/phase-") or rel.startswith(f".lintel/tasks/{task_id or ''}/architect")
                if not allowed_arch:
                    return f"Architect is not allowed to write outside plan/design: {rel}"

        return None

    # A. File write tools
    tool_info = get_tool_info(event.tool)
    if tool_info.kind in ("write", "edit") or event.tool in ("write_to_file", "replace_file_content", "notebook_edit"):
        path_args = tool_info.path_args or ["TargetFile", "NotebookPath"]
        for pa in path_args:
            val = event.args.get(pa)
            if not val:
                continue
            reason = _evaluate_target_write(str(val), event.tool)
            if reason:
                rel = paths.normalize_path(str(val), repo_root=repo_root, resolve_symlinks=False)
                f_id = generate_finding_id(event.conversation_id, event.step_idx, CAT_WRITE_OUTSIDE_LANE, rel)
                findings.append(
                    AuditFinding(
                        id=f_id,
                        kind=KIND_CROSSING,
                        category=CAT_WRITE_OUTSIDE_LANE,
                        role=role,
                        conversation_id=event.conversation_id,
                        step_idx=event.step_idx,
                        at=event.at,
                        path=rel,
                        tool=event.tool,
                        message=reason,
                        details=reason,
                    )
                )

    # B. Command lines: check redirections
    if event.tool == "run_command":
        cmd_line = str(event.args.get("CommandLine", ""))
        segs = shell.split_command_line(cmd_line)
        for seg in segs:
            for op, target in seg.redirects:
                if op in (">", ">>", ">|", "&>", "&>>") and target:
                    reason = _evaluate_target_write(target, f"redirect {op}")
                    if reason:
                        rel = paths.normalize_path(target, repo_root=repo_root, resolve_symlinks=False)
                        f_id = generate_finding_id(event.conversation_id, event.step_idx, CAT_WRITE_OUTSIDE_LANE, rel)
                        findings.append(
                            AuditFinding(
                                id=f_id,
                                kind=KIND_CROSSING,
                                category=CAT_WRITE_OUTSIDE_LANE,
                                role=role,
                                conversation_id=event.conversation_id,
                                step_idx=event.step_idx,
                                at=event.at,
                                path=rel,
                                tool=event.tool,
                                command=cmd_line[:200],
                                message=reason,
                                details=reason,
                            )
                        )

    return findings


def check_prompt_mismatch(
    event: ParsedAuditEvent,
    runs_map: Dict[str, Dict[str, Any]],
) -> Optional[AuditFinding]:
    """Check if orchestrator invoke_subagent prompt differs from recorded run prompt."""
    if event.tool != "invoke_subagent":
        return None

    actual_prompt = event.prompt
    if not actual_prompt and event.args:
        subs = event.args.get("Subagents")
        if isinstance(subs, list) and subs and isinstance(subs[0], dict):
            actual_prompt = subs[0].get("Prompt")

    # Check prompt vs runs_map
    matching_run: Optional[Dict[str, Any]] = None
    if event.marker:
        r_role = event.marker.get("role")
        r_nonce = event.marker.get("nonce")
        for r in runs_map.values():
            if r.get("role") == r_role and r.get("nonce") == r_nonce:
                matching_run = r
                break

    if not matching_run and actual_prompt:
        for r in runs_map.values():
            n = r.get("nonce")
            if n and f":{n}]" in actual_prompt:
                matching_run = r
                break

    if matching_run and matching_run.get("prompt") and actual_prompt:
        expected = matching_run["prompt"].strip()
        actual = actual_prompt.strip()
        if expected != actual:
            run_id = matching_run.get("id") or "unknown-run"
            target_path = matching_run.get("folder") or run_id
            f_id = generate_finding_id(event.conversation_id, event.step_idx, CAT_PROMPT_MISMATCH, target_path)
            return AuditFinding(
                id=f_id,
                kind=KIND_CROSSING,
                category=CAT_PROMPT_MISMATCH,
                role="orchestrator",
                conversation_id=event.conversation_id,
                run_id=run_id,
                step_idx=event.step_idx,
                at=event.at,
                path=target_path,
                tool=event.tool,
                message=f"Orchestrator invoke_subagent prompt differs from recorded run prompt for run '{run_id}'",
                details=f"Expected: {expected[:100]}... Actual: {actual[:100]}...",
            )

    return None


def check_orchestrator_message(
    event: ParsedAuditEvent,
    bound_personas: Dict[str, str],
    orchestrator_id: Optional[str] = None,
    active_persona_conv: Optional[str] = None,
) -> Optional[AuditFinding]:
    """Check if orchestrator sends a direct message to a persona."""
    if event.tool != "send_message":
        return None

    is_orch = (event.role == "orchestrator") or (orchestrator_id and event.conversation_id == orchestrator_id)
    if not is_orch:
        return None

    recipient = str(event.args.get("Recipient") or event.target or "")
    if not recipient:
        return None

    is_persona_target = False
    if recipient in bound_personas:
        is_persona_target = True
    elif recipient.startswith("lintel-") or recipient in ("dev", "qa", "po", "architect", "dev-reviewer", "qa-reviewer", "po-reviewer", "architect-reviewer", "final-reviewer"):
        is_persona_target = True
    elif active_persona_conv and recipient == active_persona_conv:
        is_persona_target = True

    if is_persona_target:
        f_id = generate_finding_id(event.conversation_id, event.step_idx, CAT_ORCHESTRATOR_MESSAGE, recipient)
        return AuditFinding(
            id=f_id,
            kind=KIND_CROSSING,
            category=CAT_ORCHESTRATOR_MESSAGE,
            role="orchestrator",
            conversation_id=event.conversation_id,
            step_idx=event.step_idx,
            at=event.at,
            path=None,
            tool=event.tool,
            message=f"Orchestrator sent direct message to persona '{recipient}'",
            details=f"Recipient: {recipient}, Message: {event.prompt or event.args.get('Message')}",
        )

    return None


def check_refused_commands(
    state_dir: Union[str, Path],
    task_id: Optional[str] = None,
    refusals_data: Optional[Union[str, Path, List[Dict[str, Any]]]] = None,
) -> List[AuditFinding]:
    """Read refusals.jsonl and convert non-ok outcomes to crossings."""
    findings: List[AuditFinding] = []
    records: List[Dict[str, Any]] = []

    if refusals_data is not None:
        if isinstance(refusals_data, list):
            records = refusals_data
        elif isinstance(refusals_data, (str, Path)):
            records = caller.read_refusals(refusals_data)
    else:
        records = caller.read_refusals(state_dir)

    for rec in records:
        outcome = rec.get("outcome")
        if outcome and outcome != "ok":
            cmd = rec.get("command", "")
            rec_id = rec.get("id")
            rec_task = rec.get("task") or task_id or ""
            f_id = rec_id or generate_finding_id(rec_task, 0, CAT_REFUSED_COMMAND, cmd)
            findings.append(
                AuditFinding(
                    id=f_id,
                    kind=KIND_CROSSING,
                    category=CAT_REFUSED_COMMAND,
                    role="human",
                    conversation_id=rec_task or None,
                    step_idx=0,
                    at=rec.get("at"),
                    command=cmd,
                    message=f"Refused human-only command '{cmd}' ({outcome})",
                    details=str(outcome),
                    raw=rec,
                )
            )

    return findings


def check_hook_failed(
    state_dir: Union[str, Path],
    hook_errors_data: Optional[Union[str, Path, List[str]]] = None,
) -> List[AuditFinding]:
    """Read hook-errors.log and convert failure lines to crossings."""
    findings: List[AuditFinding] = []
    lines: List[str] = []

    if hook_errors_data is not None:
        if isinstance(hook_errors_data, list):
            lines = hook_errors_data
        elif isinstance(hook_errors_data, (str, Path)):
            p = Path(hook_errors_data)
            if p.is_file():
                try:
                    lines = p.read_text(encoding="utf-8").splitlines()
                except Exception:
                    pass
    else:
        p = Path(state_dir)
        err_file = p / "hook-errors.log" if p.is_dir() else (p.parent / "hook-errors.log")
        if err_file.is_file():
            try:
                lines = err_file.read_text(encoding="utf-8").splitlines()
            except Exception:
                pass

    for line in lines:
        s_line = line.strip()
        if not s_line:
            continue
        m = re.search(r"\b(conv-[a-zA-Z0-9_-]+)\b", s_line)
        conv_id = m.group(1) if m else None
        f_id = generate_finding_id(conv_id, 0, CAT_HOOK_FAILED, s_line)
        findings.append(
            AuditFinding(
                id=f_id,
                kind=KIND_CROSSING,
                category=CAT_HOOK_FAILED,
                role="hook",
                conversation_id=conv_id,
                step_idx=0,
                path=".lintel/state/hook-errors.log",
                message=f"Hook failure recorded: {s_line}",
                details=s_line,
            )
        )

    return findings



def check_informational(
    event: ParsedAuditEvent,
    repo_root: Path,
    config: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    run_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[AuditFinding]:
    """Check for informational findings per PLAN §3.7."""
    findings: List[AuditFinding] = []
    role = event.role or "unknown"
    cfg = config or {}

    # 1. unmapped-tool
    tool_info = get_tool_info(event.tool)
    if event.tool not in ("enter", "task", "done", "invoke", "answer", "invoke_subagent", "send_message", "define_subagent", "manage_task", "schedule") and (tool_info.kind == "other" or event.tool in ("call_mcp_tool", "list_resources", "read_resource", "read_browser_page")):
        f_id = generate_finding_id(event.conversation_id, event.step_idx, CAT_INFO_UNMAPPED_TOOL, event.tool)
        findings.append(AuditFinding(
            id=f_id,
            kind=KIND_INFORMATIONAL,
            category=CAT_INFO_UNMAPPED_TOOL,
            role=role,
            conversation_id=event.conversation_id,
            step_idx=event.step_idx,
            at=event.at,
            tool=event.tool,
            message=f"Unmapped or opaque tool used: {event.tool}"
        ))

    # 2. command line checks
    if event.tool == "run_command":
        cmd_line = str(event.args.get("CommandLine", ""))
        cwd = event.args.get("Cwd") or repo_root
        
        # Parse command
        segs = shell.split_command_line(cmd_line)
        for seg in segs:
            prog = seg.words[0] if seg.words else ""
            
            # agent-commit
            if prog == "git" and len(seg.words) > 1 and seg.words[1] == "commit":
                f_id = generate_finding_id(event.conversation_id, event.step_idx, CAT_INFO_AGENT_COMMIT, cmd_line[:50])
                findings.append(AuditFinding(
                    id=f_id,
                    kind=KIND_INFORMATIONAL,
                    category=CAT_INFO_AGENT_COMMIT,
                    role=role,
                    conversation_id=event.conversation_id,
                    step_idx=event.step_idx,
                    at=event.at,
                    tool=event.tool,
                    command=cmd_line[:200],
                    message="Agent executed git commit"
                ))

        # Shell findings (listing, mixed-search, dynamic-construct, test-report-read)
        sh_findings = shell.scan_command_line(
            cmd_line,
            cwd=cwd,
            role=role,
            repo_root=repo_root,
            config=cfg,
            task_id=task_id,
            run_records=run_records,
        )
        for sf in sh_findings:
            if sf.category in (shell.CAT_LISTING, shell.CAT_MIXED_SEARCH, shell.CAT_DYNAMIC_CONSTRUCT, shell.CAT_TEST_REPORT):
                cat = sf.category
                # map shell categories to audit categories
                if cat == shell.CAT_LISTING:
                    cat = CAT_INFO_LISTING
                elif cat == shell.CAT_MIXED_SEARCH:
                    cat = CAT_INFO_MIXED_SEARCH
                elif cat == shell.CAT_DYNAMIC_CONSTRUCT:
                    cat = CAT_INFO_DYNAMIC_CONSTRUCT
                elif cat == shell.CAT_TEST_REPORT:
                    cat = CAT_INFO_TEST_REPORT_READ
                
                f_id = generate_finding_id(event.conversation_id, event.step_idx, cat, sf.path or sf.command[:50])
                findings.append(AuditFinding(
                    id=f_id,
                    kind=KIND_INFORMATIONAL,
                    category=cat,
                    role=role,
                    conversation_id=event.conversation_id,
                    step_idx=event.step_idx,
                    at=event.at,
                    tool=event.tool,
                    command=cmd_line[:200],
                    path=sf.path,
                    message=sf.message
                ))

    # 3. specific tools
    if event.tool in ("list_dir", "find_by_name"):
        f_id = generate_finding_id(event.conversation_id, event.step_idx, CAT_INFO_LISTING, event.tool)
        findings.append(AuditFinding(
            id=f_id,
            kind=KIND_INFORMATIONAL,
            category=CAT_INFO_LISTING,
            role=role,
            conversation_id=event.conversation_id,
            step_idx=event.step_idx,
            at=event.at,
            tool=event.tool,
            message=f"Directory listing tool used: {event.tool}"
        ))
    elif event.tool == "grep_search":
        # grep_search mixed search
        target = event.args.get("Directory", "")
        p_cls = paths.classify_path(str(target), config=cfg, repo_root=repo_root, for_audit=True)
        if p_cls.base_class in ("code", "docs", "lintel"):
            f_id = generate_finding_id(event.conversation_id, event.step_idx, CAT_INFO_MIXED_SEARCH, event.tool)
            findings.append(AuditFinding(
                id=f_id,
                kind=KIND_INFORMATIONAL,
                category=CAT_INFO_MIXED_SEARCH,
                role=role,
                conversation_id=event.conversation_id,
                step_idx=event.step_idx,
                at=event.at,
                tool=event.tool,
                message=f"Mixed search tool used: {event.tool} on {target}"
            ))
            
    return findings


def detect_crossings(
    repo_root: Union[str, Path],
    state_dir: Optional[Union[str, Path]] = None,
    audit_events: Optional[Union[str, Path, Iterable[Union[str, Dict[str, Any], ParsedAuditEvent]]]] = None,
    task_data: Optional[Dict[str, Any]] = None,
    runs_data: Optional[Union[Dict[str, Any], Sequence[Dict[str, Any]], str, Path]] = None,
    config: Optional[Dict[str, Any]] = None,
    initial_orchestrator: Optional[str] = None,
    base_dir: Optional[Union[str, Path]] = None,
    refusals_data: Optional[Union[str, Path, List[Dict[str, Any]]]] = None,
    hook_errors_data: Optional[Union[str, Path, List[str]]] = None,
    locked_phase: Optional[int] = None,
    code_changed_since_lock: Optional[Set[str]] = None,
    include_informational: bool = False,
) -> List[AuditFinding]:
    """Comprehensive crossing detection across events, runs, and state logs (PLAN §3.7).

    Detects:
    1. read-outside-lane (file tool or command line reading outside lane)
    2. git-outside-allowlist (disallowed git command for roles that shouldn't read tests)
    3. test-search (search tool or command rooted at tests)
    4. write-outside-lane (file tool or redirect writing outside allowed lane)
    5. transcript-read (access to another conversation's transcript, artifact dir, or sibling stores)
    6. refused-command (non-ok dialog outcomes from refusals.jsonl)
    7. no-hook-evidence (persona run with no enter event and no stop notice)
    8. hook-failed (failure lines in hook-errors.log)
    9. role-reuse (conversation reused across distinct roles)
    10. prompt-mismatch (invoke_subagent prompt differs from recorded run.prompt)
    11. orchestrator-message (orchestrator sending message directly to persona)
    12. unknown-role (unknown conversation touching tests/test-output during active run)

    Returns:
        List of AuditFinding instances with stable, deterministic finding IDs.
    """
    root = Path(repo_root).resolve()
    s_dir = Path(state_dir).resolve() if state_dir else (root / ".lintel" / "state")

    # 1. Load config if not provided
    cfg = config
    if cfg is None:
        try:
            from lintel.config import load_config
            cfg = load_config(root)
        except Exception:
            cfg = {}

    # 2. Load audit events
    if audit_events is not None:
        parsed_events = parse_audit_log(audit_events)
    else:
        audit_file = s_dir / "audit.jsonl"
        parsed_events = parse_audit_log(audit_file) if audit_file.is_file() else []

    # 3. Load task data and task id
    t_data = task_data
    task_id = None
    if t_data is None:
        active_info = caller.find_active_task(repo_root=root, state_dir=s_dir)
        if active_info:
            task_id, t_data = active_info
    elif isinstance(t_data, dict):
        task_id = t_data.get("id")

    # 4. Resolve runs map
    runs_map = normalize_runs(runs_data, repo_root=root, task_id=task_id)

    # 5. Determine locked phase and docs update state if not passed
    eff_locked_phase = locked_phase
    is_docs_update = False
    if t_data and isinstance(t_data, dict):
        pos = t_data.get("position", {})
        stage = str(pos.get("stage", ""))
        is_docs_update = (stage == "docs" or pos.get("code") in ("W.po", "R.po", "W.architect", "R.architect") and stage == "docs")
        if eff_locked_phase is None:
            phase = pos.get("phase")
            if phase is not None:
                phases = t_data.get("phases", {})
                ph_info = phases.get(str(phase)) or phases.get(phase) or {}
                if ph_info.get("lock") and not ph_info.get("delivered"):
                    eff_locked_phase = int(phase)

    # 6. Resolve identities & hook evidence
    identity_res = resolve_identities(
        audit_events=parsed_events,
        repo_root=root,
        task_data=t_data,
        runs_data=runs_map,
        config=cfg,
        initial_orchestrator=initial_orchestrator,
        base_dir=base_dir,
    )

    all_findings: List[AuditFinding] = []

    # Add identity & hook evidence findings
    all_findings.extend(identity_res.findings)

    # 7. Scan events for crossings
    run_records_list = list(runs_map.values()) if runs_map else None

    # Determine active persona conversation at event points
    active_persona_conv = None
    for ev in identity_res.events:
        if ev.role not in ("orchestrator", "other", "unknown", None):
            active_persona_conv = ev.conversation_id
            break

    # Find events with post hooks to skip pre-only failed attempts
    has_post_hook = set()
    for ev in identity_res.events:
        if ev.hook == "post":
            has_post_hook.add((ev.conversation_id, ev.step_idx))

    for ev in identity_res.events:
        # Skip PreToolUse without PostToolUse (failed attempts)
        if ev.hook == "pre" and (ev.conversation_id, ev.step_idx) not in has_post_hook:
            continue

        # Check informational findings
        info_findings = check_informational(
            ev,
            repo_root=root,
            config=cfg,
            task_id=task_id,
            run_records=run_records_list,
        )
        if info_findings:
            all_findings.extend(info_findings)

        # A. Transcript read
        tr_finding = check_transcript_read(ev, repo_root=root)
        if tr_finding:
            all_findings.append(tr_finding)
            continue

        # B. Prompt mismatch (invoke_subagent)
        pm_finding = check_prompt_mismatch(ev, runs_map=runs_map)
        if pm_finding:
            all_findings.append(pm_finding)

        # C. Orchestrator message (send_message)
        om_finding = check_orchestrator_message(
            ev,
            bound_personas=identity_res.bound_personas,
            orchestrator_id=identity_res.orchestrator_id,
            active_persona_conv=active_persona_conv,
        )
        if om_finding:
            all_findings.append(om_finding)

        # D. Test search
        ts_findings = check_test_search(
            ev,
            repo_root=root,
            config=cfg,
            task_id=task_id,
            run_records=run_records_list,
        )
        if ts_findings:
            all_findings.extend(ts_findings)

        # E. Git outside allow-list
        git_findings = check_git_outside_allowlist(
            ev,
            repo_root=root,
            config=cfg,
            task_id=task_id,
            run_records=run_records_list,
        )
        if git_findings:
            all_findings.extend(git_findings)

        # F. Write outside lane
        write_findings = check_write_outside_lane(
            ev,
            repo_root=root,
            config=cfg,
            task_data=t_data,
            locked_phase=eff_locked_phase,
            is_docs_update=is_docs_update,
            task_id=task_id,
            run_records=run_records_list,
        )
        if write_findings:
            all_findings.extend(write_findings)

        # G. Read outside lane
        read_findings = check_read_outside_lane(
            ev,
            repo_root=root,
            config=cfg,
            task_data=t_data,
            locked_phase=eff_locked_phase,
            code_changed_since_lock=code_changed_since_lock,
            task_id=task_id,
            run_records=run_records_list,
        )
        if read_findings:
            all_findings.extend(read_findings)

    # 8. Refused human-only commands
    ref_findings = check_refused_commands(
        state_dir=s_dir,
        task_id=task_id,
        refusals_data=refusals_data,
    )
    all_findings.extend(ref_findings)

    # 9. Hook failures
    hf_findings = check_hook_failed(
        state_dir=s_dir,
        hook_errors_data=hook_errors_data,
    )
    all_findings.extend(hf_findings)

    # 9.5 Never-opened skills
    never_opened_findings = check_never_opened_skills(run_records_list or [], identity_res.events, root)
    if never_opened_findings:
        all_findings.extend(never_opened_findings)

    # 10. Filter and deduplicate
    seen_ids: Set[str] = set()
    deduped: List[AuditFinding] = []
    for f in all_findings:
        if not include_informational and f.kind != KIND_CROSSING:
            continue
        if f.id not in seen_ids:
            seen_ids.add(f.id)
            deduped.append(f)

    return deduped


def is_blocking_finding(finding: AuditFinding) -> bool:
    """Return False for all informational findings."""
    return finding.kind != KIND_INFORMATIONAL and finding.severity != "info"



def get_window_boundary(
    state_dir: Union[str, Path],
    task_id: str,
    role: Optional[str] = None,
    finding_at: Optional[str] = None,
) -> str:
    """Calculate the audit time window start for a given pair or no-pair context.
    
    If role is None, returns the no-pair boundary (last_human.at or dev_start if in DEV window).
    """
    from lintel.state import load_state_file, resolve_state_root
    from lintel.events import read_events
    root = resolve_state_root(state_dir)
    
    try:
        task_data = load_state_file(root / task_id / "task.json", doc_type="task")
        task_start = task_data.get("created_at", "1970-01-01T00:00:00Z")
    except Exception:
        task_start = "1970-01-01T00:00:00Z"

    pair = role.lower() if role else None
    if pair in ("po-reviewer", "po"): pair = "po"
    elif pair in ("architect-reviewer", "architect"): pair = "architect"
    elif pair in ("qa-reviewer", "qa"): pair = "qa"
    elif pair in ("dev-reviewer", "dev"): pair = "dev"
    elif pair == "dev-arbiter": pair = None
    
    if pair == "po":
        try:
            reviews_data = load_state_file(root / task_id / "reviews.json", doc_type="reviews")
            approvals = reviews_data.get("approvals", [])
        except Exception:
            approvals = []
        last_app = task_start
        for app in approvals:
            if app.get("pair") == "po" and app.get("step") in ("requirements", "docs"):
                app_at = app.get("at", "")
                if app_at > last_app: last_app = app_at
        return last_app
        
    elif pair == "architect":
        try:
            reviews_data = load_state_file(root / task_id / "reviews.json", doc_type="reviews")
            approvals = reviews_data.get("approvals", [])
        except Exception:
            approvals = []
        last_app = task_start
        for app in approvals:
            if app.get("pair") == "architect" and app.get("step") == "plan":
                app_at = app.get("at", "")
                if app_at > last_app: last_app = app_at
        return last_app
        
    elif pair == "qa":
        try:
            reviews_data = load_state_file(root / task_id / "reviews.json", doc_type="reviews")
            approvals = reviews_data.get("approvals", [])
        except Exception:
            approvals = []
        last_app = task_start
        for app in approvals:
            if app.get("pair") == "qa" and app.get("step") in ("lock", "docs"):
                app_at = app.get("at", "")
                if app_at > last_app: last_app = app_at
        return last_app
        
    if pair == "dev":
        last_dev_start = task_start
        try:
            events = read_events(root / task_id / "events.jsonl")
            for ev in events:
                if ev.get("event") in ("lock", "reset"):
                    ev_at = ev.get("at", "")
                    if ev_at > last_dev_start:
                        last_dev_start = ev_at
        except Exception:
            pass
        return last_dev_start

    # No-pair findings
    dev_start_for_finding = task_start
    in_dev_window_at_finding = False
    
    if finding_at is not None:
        try:
            events = read_events(root / task_id / "events.jsonl")
            for ev in events:
                ev_at = ev.get("at", "")
                if ev_at > finding_at:
                    break
                if ev.get("event") in ("lock", "reset"):
                    dev_start_for_finding = ev_at
                    in_dev_window_at_finding = True
                elif ev.get("event") == "deliver":
                    in_dev_window_at_finding = False
        except Exception:
            pass
            
        if in_dev_window_at_finding:
            return dev_start_for_finding

    try:
        acks_data = load_state_file(root / task_id / "acks.json", doc_type="acks")
        return acks_data.get("last_human", {}).get("at", task_start)
    except Exception:
        return task_start

def get_pair_window(
    state_dir: Union[str, Path],
    task_id: str,
    pair: Optional[str] = None,
) -> str:
    """Alias for get_window_boundary."""
    return get_window_boundary(state_dir, task_id, pair)

def filter_findings_for_window(
    findings: Iterable[AuditFinding],
    window_start: str,
    window_end: Optional[str] = None,
    acked_ids: Optional[Set[str]] = None,
) -> Tuple[List[AuditFinding], List[AuditFinding]]:
    """Return (unacked_in_window, acked_in_window)."""
    unacked = []
    acked = []
    acked_set = acked_ids or set()
    
    for f in findings:
        f_at = f.at or "1970-01-01T00:00:00Z"
        if f_at < window_start:
            continue
        if window_end and f_at > window_end:
            continue
            
        if f.id in acked_set:
            acked.append(f)
        else:
            unacked.append(f)
            
    return unacked, acked

def record_audit_ack(
    state_dir: Union[str, Path],
    task_id: str,
    finding_ids: Iterable[str],
    step: str,
    phase: Optional[Union[int, str]] = None,
    timestamp: Optional[str] = None,
) -> None:
    """Record acceptance of audit findings in acks.json."""
    from lintel.state import load_state_file, atomic_write_json, state_lock, resolve_state_root
    from datetime import datetime, timezone

    root = resolve_state_root(state_dir)
    acks_file = root / task_id / "acks.json"
    
    with state_lock(root):
        try:
            acks_data = load_state_file(acks_file, doc_type="acks")
        except Exception:
            acks_data = {
                "version": 1,
                "drift": {},
                "audit": {"acks": [], "acked_ids": []},
                "last_human": {}
            }
        
        if "audit" not in acks_data:
            acks_data["audit"] = {"acks": [], "acked_ids": []}
            
        now_str = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ids_list = sorted(list(set(finding_ids)))
        if not ids_list:
            return
            
        ack_record = {
            "at": now_str,
            "phase": phase,
            "step": step,
            "ids": ids_list
        }
        acks_data["audit"]["acks"].append(ack_record)
        
        current_acked = set(acks_data["audit"].get("acked_ids", []))
        current_acked.update(ids_list)
        acks_data["audit"]["acked_ids"] = sorted(list(current_acked))
        
        atomic_write_json(acks_file, acks_data)



def get_ackable_findings(
    repo_root: Union[str, Path],
    pair: str,
) -> List[AuditFinding]:
    """Return the unacked findings for the given pair, and applicable no-pair findings."""
    from lintel.state import resolve_state_root, load_state_file
    from lintel.task_start import find_active_task_in_state
    root = Path(repo_root).resolve()
    active_info = find_active_task_in_state(root)
    if not active_info:
        return []
    task_id, _ = active_info
    state_dir = root / ".lintel" / "state"
    
    findings = detect_crossings(root)
    
    try:
        acks_data = load_state_file(state_dir / task_id / "acks.json", doc_type="acks")
        acked_ids = set(acks_data.get("audit", {}).get("acked_ids", []))
    except Exception:
        acked_ids = set()
        
    pair_start = get_pair_window(state_dir, task_id, pair)
    
    ackable = []
    
    for f in findings:
        f_role = f.role
        if f_role == pair:
            unacked, _ = filter_findings_for_window([f], pair_start, acked_ids=acked_ids)
            ackable.extend(unacked)
        elif f_role in (None, "orchestrator", "dev-arbiter"):
            # check if finding is in DEV window
            f_at = f.at or "1970-01-01T00:00:00Z"
            f_win = get_window_boundary(state_dir, task_id, role=None, finding_at=f_at)
            # If we are accepting for DEV, we only accept if it's bound to DEV window!
            # Wait, "In DEV's window ... ONLY audit --ack accepts them."
            # So no human command accepts them!
            # Wait, no-pair findings outside DEV's window: "shown and accepted at the next human command of any kind".
            # So ANY pair command (approve, lock, amend) accepts no-pair findings that are OUTSIDE DEV's window!
            
            # Check if this finding is bound to DEV's window
            # get_window_boundary with finding_at returns DEV start if it's in DEV window, else last_human
            dev_start = get_pair_window(state_dir, task_id, "dev")
            in_dev = False
            if f_win == dev_start and dev_start != "1970-01-01T00:00:00Z":
                in_dev = True
                
            if in_dev:
                # ONLY audit --ack accepts these
                continue
            else:
                # Outside DEV window -> accepted by next human command
                unacked, _ = filter_findings_for_window([f], f_win, acked_ids=acked_ids)
                ackable.extend(unacked)
                
    return ackable

def accept_findings_for_command(
    repo_root: Union[str, Path],
    pair: str,
    step: str,
    phase: Optional[Union[int, str]] = None,
) -> None:
    """Convenience: find ackable findings for pair and accept them."""
    from lintel.task_start import find_active_task_in_state
    root = Path(repo_root).resolve()
    active_info = find_active_task_in_state(root)
    if not active_info:
        return
    task_id, _ = active_info
    state_dir = root / ".lintel" / "state"
    
    ackable = get_ackable_findings(repo_root, pair)
    if ackable:
        ids = [f.id for f in ackable]
        record_audit_ack(state_dir, task_id, ids, step, phase)

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def audit(
    repo_root, phase=None, ack=False, reject=None, pair=None, full=False,
    skip_caller_check=False, dialog_fn=None
) -> None:
    from lintel.task_start import find_active_task_in_state
    from lintel.state import resolve_state_root, load_state_file, atomic_write_json, state_lock
    from lintel.caller import check_caller, is_human_only_command
    import argparse
    from lintel.flow import FlowRefusalError, get_position, on_audit_reject
    from lintel.loops import record_human_reject
    from lintel.reviews import update_progress_md
    from lintel.events import create_event, append_event
    from pathlib import Path
    import json
    
    root = Path(repo_root).resolve()
    active_info = find_active_task_in_state(root)
    if not active_info:
        raise FlowRefusalError("No active task found.")
    task_id, _ = active_info
    state_dir = root / ".lintel" / "state"
    task_data = load_state_file(state_dir / task_id / "task.json")
    
    pos = get_position(task_data)
    run_state = pos.run
    is_human = ack or reject or full
    
    if (ack or reject) and run_state in ("pending", "active"):
        raise FlowRefusalError("Refused: a run is pending or active (T-HUM-05)", row="T-HUM-05")
        
    if is_human and not skip_caller_check:
        args = argparse.Namespace(subcommand="audit", phase=phase, ack=ack, reject=reject, pair=pair, full=full)
        command_str = "audit"
        if ack: command_str += " --ack"
        if reject: command_str += " --reject"
        if full: command_str += " --full"
        check_caller(command_str=command_str, args=args, repo_root=root, dialog_fn=dialog_fn)

    if reject:
        _handle_audit_reject(root, task_id, task_data, reject, pair, phase)
        return

    if ack:
        _handle_audit_ack(root, task_id, task_data, pair, phase)
        return
        
    _handle_audit_default(root, task_id, task_data, full)

def _handle_audit_reject(root, task_id, task_data, reject_reason, pair_arg, phase):
    from lintel.flow import get_position, FlowRefusalError, on_audit_reject
    from lintel.state import atomic_write_json, load_state_file, state_lock
    from lintel.loops import record_human_reject
    from lintel.reviews import update_progress_md
    from lintel.events import create_event, append_event
    
    pos = get_position(task_data)
    stage = pos.code
    state_dir = root / ".lintel" / "state"
    
    if task_data.get("cycle"):
        raise FlowRefusalError("Refused: inside an amend cycle (T-HUM-33)", row="T-HUM-33")
        
    valid_states = {"H.req", "H.plan", "H.lock", "O.deliver", "H.docs"}
    if task_data.get("escalation") or stage.startswith("D.") or stage not in valid_states:
        if task_data.get("escalation") or stage.startswith("D."):
            raise FlowRefusalError("Refused: pending step is approve final, a decide, or none (T-HUM-34); next action: decide", row="T-HUM-34")
        elif stage == "H.final":
            raise FlowRefusalError("Refused: pending step is approve final, a decide, or none (T-HUM-34); next action: approve final --reject", row="T-HUM-34")
        else:
            raise FlowRefusalError("Refused: pending step is approve final, a decide, or none (T-HUM-34); next action: none", row="T-HUM-34")
    if stage == "H.docs":
        docs_up = task_data.get("docs_update", {})
        po_ch = docs_up.get("po_changed", False)
        arch_ch = docs_up.get("architect_changed", False)
        if po_ch and arch_ch:
            if not pair_arg:
                raise FlowRefusalError("Refused: both pairs changed something and --pair is omitted (T-HUM-35); next action: --pair po or --pair architect", row="T-HUM-35")
            target = pair_arg
        else:
            target = pair_arg or ("po" if po_ch else "architect")
    else:
        if stage == "O.deliver": target = "dev"
        elif stage == "H.req": target = "po"
        elif stage == "H.plan": target = "architect"
        elif stage == "H.lock": target = "qa"
        else: target = "dev"
        
        if pair_arg and pair_arg != target:
            raise FlowRefusalError(f"Refused: expected pair is {target} (T-HUM-17)", row="T-HUM-17")

    record_human_reject(state_dir, task_id, target, stage, reject_reason)
    
    with state_lock(state_dir):
        try:
            acks_data = load_state_file(state_dir / task_id / "acks.json", doc_type="acks")
        except:
            acks_data = {"version": 1, "drift": {}, "audit": {"acks": [], "acked_ids": []}, "last_human": {}}
        acks_data["last_human"] = {"at": _now_iso()}
        atomic_write_json(state_dir / task_id / "acks.json", acks_data)
        
        task_data_new = load_state_file(state_dir / task_id / "task.json")
        task_data_new, next_action, rule = on_audit_reject(task_data_new, target)
        atomic_write_json(state_dir / task_id / "task.json", task_data_new)
        
    append_event(state_dir, task_id, create_event(step=task_data.get("event_seq", 1), event="reject", stage=target))
    update_progress_md(root, task_data_new, next_action)
    print(next_action)

def _handle_audit_ack(root, task_id, task_data, pair_arg, phase):
    from lintel.flow import get_position
    from lintel.state import load_state_file, atomic_write_json, state_lock
    from lintel.reviews import update_progress_md
    from lintel.events import create_event, append_event
    
    pos = get_position(task_data)
    stage = pos.code
    state_dir = root / ".lintel" / "state"
    
    if stage == "O.deliver": target = "dev"
    elif stage == "H.req": target = "po"
    elif stage == "H.plan": target = "architect"
    elif stage == "H.lock": target = "qa"
    elif stage == "H.docs": target = pair_arg or "po"
    else: target = "dev" # fallback
    
    ackable = get_ackable_findings(root, target)
    if not ackable:
        print("No unacknowledged crossings in the window.")
        return
        
    ids = [f.id for f in ackable]
    record_audit_ack(state_dir, task_id, ids, step="audit", phase=phase)
    
    with state_lock(state_dir):
        try:
            acks_data = load_state_file(state_dir / task_id / "acks.json", doc_type="acks")
        except:
            acks_data = {"version": 1, "drift": {}, "audit": {"acks": [], "acked_ids": []}, "last_human": {}}
        acks_data["last_human"] = {"at": _now_iso()}
        atomic_write_json(state_dir / task_id / "acks.json", acks_data)
        
    append_event(state_dir, task_id, create_event(step=task_data.get("event_seq", 1), event="audit-ack", stage="audit"))
    
    from lintel.flow import format_next_action
    next_action = format_next_action(task_data)
    update_progress_md(root, task_data, next_action)
    print("Acknowledged.")

def _handle_audit_default(root, task_id, task_data, full):
    from lintel.flow import get_position
    pos = get_position(task_data)
    stage = pos.code
    
    if stage == "O.deliver": target = "dev"
    elif stage == "H.req": target = "po"
    elif stage == "H.plan": target = "architect"
    elif stage == "H.lock": target = "qa"
    elif stage == "H.docs": target = "po"
    elif stage.endswith("dev"): target = "dev"
    elif stage.endswith("po"): target = "po"
    elif stage.endswith("architect"): target = "architect"
    elif stage.endswith("qa"): target = "qa"
    else: target = "dev"

    ackable = get_ackable_findings(root, target)
    if not ackable:
        print("No unacknowledged crossings in the window.")
        return
        
    if not full:
        counts = {}
        for f in ackable:
            counts[f.kind] = counts.get(f.kind, 0) + 1
            
        print("Audit findings (restricted):")
        for k, v in counts.items():
            print(f"- {k}: {v}")
            
        build_side = [f for f in ackable if getattr(f, 'is_build_side', False)]
        if build_side:
            print("\
Build-side findings:")
            for f in build_side:
                print(f"- {f.id} [{f.kind}] {f.role}: {getattr(f, 'path', '')}")
    else:
        print("Audit findings (full):")
        for f in ackable:
            print(f"- {f.id} [{f.kind}] {f.role} at {f.at}: {getattr(f, 'path', '')}")


def check_never_opened_skills(
    run_records: Sequence[Dict[str, Any]],
    events: Iterable[ParsedAuditEvent],
    repo_root: Path
) -> List[AuditFinding]:
    """Check if any assigned skill was never opened during a run."""
    findings = []
    
    # Map conversation to run
    conv_to_run = {}
    for r in run_records:
        if r.get("conversation_id"):
            conv_to_run[r["conversation_id"]] = r

    opened_skills = {r["id"]: set() for r in run_records}
    
    # Pre-resolve all required skills
    skill_paths_map = {}
    from lintel.skills import resolve_skill_path
    import json
    for r in run_records:
        for s in r.get("skills", []):
            if s not in skill_paths_map:
                p = resolve_skill_path(s, repo_root)
                if p:
                    skill_paths_map[s] = p.as_posix()
                    
    for ev in events:
        run = conv_to_run.get(ev.conversation_id)
        if not run:
            continue
        run_id = run["id"]
        
        args_str = json.dumps(ev.args)
        for s in run.get("skills", []):
            sp = skill_paths_map.get(s)
            if sp and sp in args_str:
                opened_skills[run_id].add(s)

    for r in run_records:
        run_id = r["id"]
        for s in r.get("skills", []):
            if s not in opened_skills.get(run_id, set()):
                f_id = f"info-{run_id}-{s}-never-opened"
                findings.append(AuditFinding(
                    id=f_id,
                    kind=KIND_INFORMATIONAL,
                    category="never-opened-skill",
                    role=r.get("role", "unknown"),
                    conversation_id=r.get("conversation_id", "unknown"),
                    step_idx=0,
                    at=r.get("done_at") or r.get("created_at") or "1970-01-01T00:00:00Z",
                    tool="",
                    message=f"Assigned skill '{s}' was never opened."
                ))

    return findings
