"""Shell command scanning and analysis (M1b-03).

Pure Python standard library only, Python 3.9 compatible.
Scans agent command lines for lane crossings, disallowed git commands,
unauthorized test searches, dynamic constructs, and informational listings.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

sys.dont_write_bytecode = True

try:
    from lintel.paths import (
        PathClass,
        classify_path,
        is_test_side_task_path,
        normalize_path,
    )
except ImportError:
    from .paths import (
        PathClass,
        classify_path,
        is_test_side_task_path,
        normalize_path,
    )

# Finding kinds
KIND_CROSSING = "crossing"
KIND_INFORMATIONAL = "informational"

# Finding categories
CAT_READ_OUTSIDE_LANE = "read-outside-lane"
CAT_GIT_OUTSIDE_ALLOWLIST = "git-outside-allowlist"
CAT_GIT_FRONTEND = "git-frontend"
CAT_TEST_SEARCH = "test-search"
CAT_MIXED_SEARCH = "mixed-search"
CAT_LISTING = "listing"
CAT_DYNAMIC_CONSTRUCT = "dynamic-construct"
CAT_TEST_REPORT = "test-report"

# Roles that should not read tests per PLAN §3.4 & §3.7
ROLES_SHOULD_NOT_READ_TESTS: frozenset = frozenset({
    "dev",
    "dev-reviewer",
    "po",
    "po-reviewer",
    "architect",
    "architect-reviewer",
    "orchestrator",
})

# Unconditional allowed git subcommands per PLAN §3.7
UNCONDITIONAL_GIT_SUBCOMMANDS: frozenset = frozenset({
    "status",
    "add",
    "rm",
    "mv",
    "rev-parse",
    "branch",
    "ls-files",
})

# Conditional git subcommands per PLAN §3.7
CONDITIONAL_GIT_SUBCOMMANDS: frozenset = frozenset({
    "diff",
    "show",
    "log",
    "blame",
})

# Known git front-ends
GIT_FRONTENDS: frozenset = frozenset({
    "gh",
    "glab",
    "tig",
})

# Listing commands (informational)
LISTING_COMMANDS: frozenset = frozenset({
    "ls",
    "find",
    "list_dir",
    "find_by_name",
})

# Content search commands
SEARCH_COMMANDS: frozenset = frozenset({
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "ag",
    "ack",
})

# Legitimate project work / build / test runners (PLAN §3.7)
BUILD_TEST_COMMANDS: frozenset = frozenset({
    "mvn",
    "./mvnw",
    "mvnw",
    "gradle",
    "./gradlew",
    "gradlew",
    "npm",
    "pnpm",
    "yarn",
    "bun",
    "pytest",
    "cargo",
    "go",
    "make",
    "ninja",
    "cmake",
})

# Lintel CLI command prefixes / paths
LINTEL_COMMANDS: frozenset = frozenset({
    "lintel",
    "./.lintel/bin/lintel",
    ".lintel/bin/lintel",
})


@dataclass
class ShellFinding:
    """Audit finding resulting from scanning a command line."""

    kind: str  # "crossing" or "informational"
    category: str
    message: str
    command: str
    path: Optional[str] = None


@dataclass
class CommandSegment:
    """A single command within a pipeline or chain."""

    raw: str
    words: List[str]
    env_vars: Dict[str, str] = field(default_factory=dict)
    redirects: List[Tuple[str, str]] = field(default_factory=list)
    operator_before: Optional[str] = None
    operator_after: Optional[str] = None
    is_downstream_pipe: bool = False


def _find_dynamic_constructs(cmd_line: str) -> List[Tuple[str, str]]:
    """Find dynamic constructs in command line text.

    Returns list of (construct_type, snippet).
    Constructs flagged:
    - $(...) command substitution
    - `...` backtick command substitution
    - eval
    - sh -c, bash -c, zsh -c
    """
    found: List[Tuple[str, str]] = []
    i = 0
    n = len(cmd_line)
    in_single = False
    in_double = False

    while i < n:
        c = cmd_line[i]

        if not in_single and not in_double:
            if c == "\\":
                i += 2
                continue
            elif c == "'":
                in_single = True
                i += 1
                continue
            elif c == '"':
                in_double = True
                i += 1
                continue
            elif c == "$" and i + 1 < n and cmd_line[i + 1] == "(":
                # Scan matching )
                depth = 1
                j = i + 2
                while j < n and depth > 0:
                    if cmd_line[j] == "\\":
                        j += 2
                        continue
                    if cmd_line[j] == "(":
                        depth += 1
                    elif cmd_line[j] == ")":
                        depth -= 1
                    j += 1
                snippet = cmd_line[i:j]
                found.append(("$(...)", snippet))
                i = j
                continue
            elif c == "`":
                j = i + 1
                while j < n and cmd_line[j] != "`":
                    if cmd_line[j] == "\\":
                        j += 2
                        continue
                    j += 1
                if j < n:
                    j += 1
                snippet = cmd_line[i:j]
                found.append(("`...`", snippet))
                i = j
                continue
            else:
                i += 1
                continue
        elif in_single:
            if c == "'":
                in_single = False
            i += 1
        elif in_double:
            if c == "\\":
                i += 2
                continue
            elif c == '"':
                in_double = False
                i += 1
                continue
            elif c == "$" and i + 1 < n and cmd_line[i + 1] == "(":
                depth = 1
                j = i + 2
                while j < n and depth > 0:
                    if cmd_line[j] == "\\":
                        j += 2
                        continue
                    if cmd_line[j] == "(":
                        depth += 1
                    elif cmd_line[j] == ")":
                        depth -= 1
                    j += 1
                snippet = cmd_line[i:j]
                found.append(("$(...)", snippet))
                i = j
                continue
            elif c == "`":
                j = i + 1
                while j < n and cmd_line[j] != "`":
                    if cmd_line[j] == "\\":
                        j += 2
                        continue
                    j += 1
                if j < n:
                    j += 1
                snippet = cmd_line[i:j]
                found.append(("`...`", snippet))
                i = j
                continue
            else:
                i += 1

    return found


def split_command_line(cmd_line: str) -> List[CommandSegment]:
    """Parse a zsh command line into individual command segments.

    Handles quotes, backslash escapes, pipelines (|), operators (&&, ||, ;, &),
    environment variable prefixes, and redirections.
    """
    segments: List[CommandSegment] = []
    i = 0
    n = len(cmd_line)
    in_single = False
    in_double = False
    cur_token: List[str] = []
    tokens: List[str] = []
    current_op_before: Optional[str] = None

    def push_token():
        if cur_token:
            tokens.append("".join(cur_token))
            cur_token.clear()

    def push_segment(op_after: Optional[str] = None):
        push_token()
        if tokens or op_after is not None:
            # Separate leading env vars (FOO=bar)
            w_idx = 0
            env_vars: Dict[str, str] = {}
            while w_idx < len(tokens):
                t = tokens[w_idx]
                if "=" in t and not t.startswith("="):
                    prefix = t.split("=", 1)[0]
                    if prefix.isidentifier():
                        env_vars[prefix] = t.split("=", 1)[1]
                        w_idx += 1
                        continue
                break

            words: List[str] = []
            redirects: List[Tuple[str, str]] = []
            while w_idx < len(tokens):
                t = tokens[w_idx]
                # Redirection operators
                if t in (">", ">>", "<", ">|", "&>", "&>>", "2>", "1>") and w_idx + 1 < len(tokens):
                    redirects.append((t, tokens[w_idx + 1]))
                    w_idx += 2
                elif t in ("2>&1", "1>&2", ">&1", ">&2"):
                    redirects.append((t, ""))
                    w_idx += 1
                elif t.startswith("2>&") or t.startswith("1>&"):
                    redirects.append((t, ""))
                    w_idx += 1
                elif t.startswith("2>") or t.startswith("1>"):
                    redirects.append((t[:2], t[2:]))
                    w_idx += 1
                elif (t.startswith(">") or t.startswith("<")) and len(t) > 1 and not t.startswith(">="):
                    if (t.startswith("<") and ">" in t) or (t.startswith(">") and "<" in t):
                        words.append(t)
                        w_idx += 1
                    else:
                        redirects.append((t[0], t[1:]))
                        w_idx += 1
                else:
                    words.append(t)
                    w_idx += 1

            is_downstream = current_op_before in ("|", "|&")
            raw_text = " ".join(tokens)
            seg = CommandSegment(
                raw=raw_text,
                words=words,
                env_vars=env_vars,
                redirects=redirects,
                operator_before=current_op_before,
                operator_after=op_after,
                is_downstream_pipe=is_downstream,
            )
            segments.append(seg)
            tokens.clear()

    while i < n:
        c = cmd_line[i]

        if not in_single and not in_double:
            if c == "\\":
                if i + 1 < n:
                    cur_token.append(cmd_line[i + 1])
                    i += 2
                else:
                    i += 1
                continue
            elif c == "'":
                in_single = True
                i += 1
                continue
            elif c == '"':
                in_double = True
                i += 1
                continue
            elif c in " \t\r\n":
                push_token()
                i += 1
                continue
            # Command chaining operators
            elif cmd_line.startswith("&&", i):
                push_segment("&&")
                current_op_before = "&&"
                i += 2
                continue
            elif cmd_line.startswith("||", i):
                push_segment("||")
                current_op_before = "||"
                i += 2
                continue
            elif cmd_line.startswith("|&", i):
                push_segment("|&")
                current_op_before = "|&"
                i += 2
                continue
            elif c == "|":
                push_segment("|")
                current_op_before = "|"
                i += 1
                continue
            elif c == ";":
                push_segment(";")
                current_op_before = ";"
                i += 1
                continue
            elif c == "&":
                if (cur_token and cur_token[-1] in (">", "<")) or cmd_line.startswith("&>", i) or cmd_line.startswith("&>>", i):
                    cur_token.append(c)
                    i += 1
                    continue
                push_segment("&")
                current_op_before = "&"
                i += 1
                continue
            elif c in "()":
                # Subshell grouping boundary
                push_token()
                i += 1
                continue
            else:
                cur_token.append(c)
                i += 1
                continue
        elif in_single:
            if c == "'":
                in_single = False
                i += 1
            else:
                cur_token.append(c)
                i += 1
        elif in_double:
            if c == "\\":
                if i + 1 < n and cmd_line[i + 1] in ('"', "\\", "$", "`"):
                    cur_token.append(cmd_line[i + 1])
                    i += 2
                else:
                    cur_token.append(c)
                    i += 1
            elif c == '"':
                in_double = False
                i += 1
            else:
                cur_token.append(c)
                i += 1

    push_segment(None)
    return [s for s in segments if s.words or s.raw.strip()]


def resolve_effective_cwd(cwd: Optional[Union[str, Path]], repo_root: Path) -> Path:
    """Resolve initial working directory relative to repo_root."""
    if cwd is None or str(cwd).strip() == "" or str(cwd).strip() == ".":
        return repo_root.resolve()
    p = Path(cwd)
    if not p.is_absolute():
        return (repo_root / p).resolve()
    return p.resolve()


def _resolve_and_classify(
    token: str,
    effective_cwd: Path,
    repo_root: Path,
    config: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    run_records: Optional[Sequence[Dict[str, Any]]] = None,
    is_dir: bool = False,
) -> Tuple[str, PathClass]:
    """Resolve a token against effective cwd and classify it."""
    clean_tok = token.strip()
    if clean_tok.startswith("~/") or clean_tok == "~":
        expanded = os.path.expanduser(clean_tok)
        res_path = os.path.normpath(expanded)
    elif os.path.isabs(clean_tok):
        res_path = os.path.normpath(clean_tok)
    else:
        res_path = os.path.normpath(os.path.join(str(effective_cwd), clean_tok))

    rel_path = normalize_path(res_path, repo_root=repo_root, resolve_symlinks=False)
    p_class = classify_path(
        res_path,
        config=config,
        repo_root=repo_root,
        is_tracked=False,
        is_dir=is_dir,
        for_audit=True,
        task_id=task_id,
        runs=run_records,
    )
    return rel_path, p_class


def _is_target_inside_tests(
    token: str,
    effective_cwd: Path,
    repo_root: Path,
    config: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    run_records: Optional[Sequence[Dict[str, Any]]] = None,
    is_dir: bool = False,
) -> bool:
    """Return True if path token resolves to a tests path or test-side task path."""
    rel, p_class = _resolve_and_classify(
        token,
        effective_cwd,
        repo_root,
        config=config,
        task_id=task_id,
        run_records=run_records,
        is_dir=is_dir,
    )
    if p_class.base_class in ("tests", "test-output"):
        return True
    if is_test_side_task_path(rel, runs=run_records, task_id=task_id, repo_root=repo_root):
        return True
    return False


def is_git_command_allowed(
    command_or_words: Union[str, Sequence[str]],
    role: str = "dev",
    cwd: Optional[Union[str, Path]] = None,
    repo_root: Optional[Union[str, Path]] = None,
    config: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    run_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> bool:
    """Check if a git command is allowed for the given role per PLAN §3.7."""
    findings = _check_git_command(
        command_or_words,
        role=role,
        cwd=cwd,
        repo_root=repo_root,
        config=config,
        task_id=task_id,
        run_records=run_records,
    )
    return len(findings) == 0


def _check_git_command(
    command_or_words: Union[str, Sequence[str]],
    role: str = "dev",
    cwd: Optional[Union[str, Path]] = None,
    repo_root: Optional[Union[str, Path]] = None,
    config: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    run_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[ShellFinding]:
    """Scan a git or git-frontend command and return any crossings."""
    if role not in ROLES_SHOULD_NOT_READ_TESTS:
        return []

    if repo_root is None:
        repo_root = Path.cwd()
    else:
        repo_root = Path(repo_root)

    eff_cwd = resolve_effective_cwd(cwd, repo_root)

    if isinstance(command_or_words, str):
        segs = split_command_line(command_or_words)
        if not segs:
            return []
        words = segs[0].words
        raw_cmd = command_or_words
    else:
        words = list(command_or_words)
        raw_cmd = " ".join(words)

    if not words:
        return []

    prog = os.path.basename(words[0])

    # 1. Git front-ends: gh pr diff, glab mr diff, tig
    if prog == "tig":
        return [
            ShellFinding(
                kind=KIND_CROSSING,
                category=CAT_GIT_FRONTEND,
                message="Git front-end 'tig' is outside the allow-list",
                command=raw_cmd,
            )
        ]
    if prog == "gh" and len(words) >= 3 and words[1] == "pr" and words[2] == "diff":
        return [
            ShellFinding(
                kind=KIND_CROSSING,
                category=CAT_GIT_FRONTEND,
                message="Git front-end 'gh pr diff' is outside the allow-list",
                command=raw_cmd,
            )
        ]
    if prog == "glab" and len(words) >= 3 and words[1] == "mr" and words[2] == "diff":
        return [
            ShellFinding(
                kind=KIND_CROSSING,
                category=CAT_GIT_FRONTEND,
                message="Git front-end 'glab mr diff' is outside the allow-list",
                command=raw_cmd,
            )
        ]

    if prog != "git":
        return []

    # 2. Extract git global options and subcommand
    idx = 1
    options_with_args = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--exec-path"}
    while idx < len(words):
        arg = words[idx]
        if arg in options_with_args:
            idx += 2
        elif any(arg.startswith(opt + "=") for opt in options_with_args):
            idx += 1
        elif arg.startswith("-"):
            idx += 1
        else:
            break

    if idx >= len(words):
        return [
            ShellFinding(
                kind=KIND_CROSSING,
                category=CAT_GIT_OUTSIDE_ALLOWLIST,
                message="Bare 'git' command with no subcommand",
                command=raw_cmd,
            )
        ]

    subcmd = words[idx]
    sub_args = words[idx + 1 :]

    # 3. Unconditional allowed subcommands
    if subcmd in UNCONDITIONAL_GIT_SUBCOMMANDS:
        return []

    # 4. git diff --name-only is unconditional allowed
    if subcmd == "diff" and any(arg in ("--name-only", "--name-status") for arg in sub_args):
        return []

    # 5. git grep is always outside allow-list
    if subcmd == "grep":
        return [
            ShellFinding(
                kind=KIND_CROSSING,
                category=CAT_GIT_OUTSIDE_ALLOWLIST,
                message="'git grep' is outside the allow-list",
                command=raw_cmd,
            )
        ]

    # 6. Conditional subcommand: diff
    if subcmd == "diff":
        # Extract pathspecs
        pathspecs: List[str] = []
        if "--" in sub_args:
            sep_idx = sub_args.index("--")
            pathspecs = sub_args[sep_idx + 1 :]
            if not pathspecs:
                return [
                    ShellFinding(
                        kind=KIND_CROSSING,
                        category=CAT_GIT_OUTSIDE_ALLOWLIST,
                        message="'git diff' with trailing '--' and no pathspec",
                        command=raw_cmd,
                    )
                ]
        else:
            # Without '--', check positional arguments
            positionals = [a for a in sub_args if not a.startswith("-")]
            # Look for pathspecs among positionals
            for pos in positionals:
                if pos in (".", "./", "*", ":"):
                    pathspecs.append(pos)
                elif "/" in pos or pos.endswith(".py") or pos.endswith(".java") or pos in ("tests", "src", "docs"):
                    pathspecs.append(pos)

            if not pathspecs:
                return [
                    ShellFinding(
                        kind=KIND_CROSSING,
                        category=CAT_GIT_OUTSIDE_ALLOWLIST,
                        message="'git diff' without pathspec",
                        command=raw_cmd,
                    )
                ]

        # Check each pathspec
        for ps in pathspecs:
            if ps in (".", "./", "*", ":"):
                return [
                    ShellFinding(
                        kind=KIND_CROSSING,
                        category=CAT_GIT_OUTSIDE_ALLOWLIST,
                        message=f"'git diff' whole-tree pathspec '{ps}'",
                        command=raw_cmd,
                        path=ps,
                    )
                ]
            if _is_target_inside_tests(
                ps, eff_cwd, repo_root, config=config, task_id=task_id, run_records=run_records, is_dir=True
            ):
                return [
                    ShellFinding(
                        kind=KIND_CROSSING,
                        category=CAT_GIT_OUTSIDE_ALLOWLIST,
                        message=f"'git diff' targeting tests pathspec '{ps}'",
                        command=raw_cmd,
                        path=ps,
                    )
                ]

        return []

    # 7. Conditional subcommand: show
    if subcmd == "show":
        # Check for <rev>:<path>
        colon_args = [a for a in sub_args if ":" in a and not a.startswith("-")]
        if colon_args:
            for c_arg in colon_args:
                _, path_part = c_arg.split(":", 1)
                if not path_part or path_part in (".", "./"):
                    return [
                        ShellFinding(
                            kind=KIND_CROSSING,
                            category=CAT_GIT_OUTSIDE_ALLOWLIST,
                            message=f"'git show' bare whole-tree object '{c_arg}'",
                            command=raw_cmd,
                            path=path_part or ".",
                        )
                    ]
                if _is_target_inside_tests(
                    path_part,
                    eff_cwd,
                    repo_root,
                    config=config,
                    task_id=task_id,
                    run_records=run_records,
                    is_dir=False,
                ):
                    return [
                        ShellFinding(
                            kind=KIND_CROSSING,
                            category=CAT_GIT_OUTSIDE_ALLOWLIST,
                            message=f"'git show' targeting tests path '{path_part}'",
                            command=raw_cmd,
                            path=path_part,
                        )
                    ]
            return []

        # Check for pathspec after --
        if "--" in sub_args:
            sep_idx = sub_args.index("--")
            pathspecs = sub_args[sep_idx + 1 :]
            if not pathspecs:
                return [
                    ShellFinding(
                        kind=KIND_CROSSING,
                        category=CAT_GIT_OUTSIDE_ALLOWLIST,
                        message="Bare 'git show' with trailing '--'",
                        command=raw_cmd,
                    )
                ]
            for ps in pathspecs:
                if ps in (".", "./", "*"):
                    return [
                        ShellFinding(
                            kind=KIND_CROSSING,
                            category=CAT_GIT_OUTSIDE_ALLOWLIST,
                            message=f"'git show' whole-tree pathspec '{ps}'",
                            command=raw_cmd,
                            path=ps,
                        )
                    ]
                if _is_target_inside_tests(
                    ps, eff_cwd, repo_root, config=config, task_id=task_id, run_records=run_records, is_dir=True
                ):
                    return [
                        ShellFinding(
                            kind=KIND_CROSSING,
                            category=CAT_GIT_OUTSIDE_ALLOWLIST,
                            message=f"'git show' targeting tests pathspec '{ps}'",
                            command=raw_cmd,
                            path=ps,
                        )
                    ]
            return []

        # Bare git show without pathspec or object:path
        return [
            ShellFinding(
                kind=KIND_CROSSING,
                category=CAT_GIT_OUTSIDE_ALLOWLIST,
                message="Bare 'git show' without pathspec",
                command=raw_cmd,
            )
        ]

    # 8. Conditional subcommand: log
    if subcmd == "log":
        has_patch = any(a in ("-p", "-u", "--patch") for a in sub_args)
        pathspecs: List[str] = []
        if "--" in sub_args:
            pathspecs = sub_args[sub_args.index("--") + 1 :]
        else:
            for a in sub_args:
                if not a.startswith("-") and ("/" in a or a in ("tests", "src", "docs", ".")):
                    pathspecs.append(a)

        if has_patch:
            if not pathspecs or any(ps in (".", "./", "*") for ps in pathspecs):
                return [
                    ShellFinding(
                        kind=KIND_CROSSING,
                        category=CAT_GIT_OUTSIDE_ALLOWLIST,
                        message="'git log -p' without specific non-test pathspec",
                        command=raw_cmd,
                    )
                ]
            for ps in pathspecs:
                if _is_target_inside_tests(
                    ps, eff_cwd, repo_root, config=config, task_id=task_id, run_records=run_records, is_dir=True
                ):
                    return [
                        ShellFinding(
                            kind=KIND_CROSSING,
                            category=CAT_GIT_OUTSIDE_ALLOWLIST,
                            message=f"'git log -p' targeting tests pathspec '{ps}'",
                            command=raw_cmd,
                            path=ps,
                        )
                    ]
            return []
        else:
            # Without patch, commit log messages are generic and open to everyone,
            # unless a pathspec explicitly targets tests
            for ps in pathspecs:
                if _is_target_inside_tests(
                    ps, eff_cwd, repo_root, config=config, task_id=task_id, run_records=run_records, is_dir=True
                ):
                    return [
                        ShellFinding(
                            kind=KIND_CROSSING,
                            category=CAT_GIT_OUTSIDE_ALLOWLIST,
                            message=f"'git log' targeting tests pathspec '{ps}'",
                            command=raw_cmd,
                            path=ps,
                        )
                    ]
            return []

    # 9. Conditional subcommand: blame
    if subcmd == "blame":
        pathspecs: List[str] = []
        if "--" in sub_args:
            pathspecs = sub_args[sub_args.index("--") + 1 :]
        else:
            positionals = [a for a in sub_args if not a.startswith("-")]
            if positionals:
                pathspecs = [positionals[-1]]

        if not pathspecs:
            return [
                ShellFinding(
                    kind=KIND_CROSSING,
                    category=CAT_GIT_OUTSIDE_ALLOWLIST,
                    message="'git blame' without pathspec",
                    command=raw_cmd,
                )
            ]

        for ps in pathspecs:
            if _is_target_inside_tests(
                ps, eff_cwd, repo_root, config=config, task_id=task_id, run_records=run_records, is_dir=False
            ):
                return [
                    ShellFinding(
                        kind=KIND_CROSSING,
                        category=CAT_GIT_OUTSIDE_ALLOWLIST,
                        message=f"'git blame' targeting tests path '{ps}'",
                        command=raw_cmd,
                        path=ps,
                    )
                ]
        return []

    # 10. Any other git subcommand
    return [
        ShellFinding(
            kind=KIND_CROSSING,
            category=CAT_GIT_OUTSIDE_ALLOWLIST,
            message=f"Git subcommand '{subcmd}' is outside the allow-list",
            command=raw_cmd,
        )
    ]


def _scan_search_listing(
    seg: CommandSegment,
    effective_cwd: Path,
    repo_root: Path,
    role: str,
    config: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    run_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> Optional[ShellFinding]:
    """Scan listing (ls, find) and content search (grep, rg) commands."""
    if not seg.words:
        return None

    cmd_word = os.path.basename(seg.words[0])

    # Listing commands: ls, find, list_dir, find_by_name
    if cmd_word in LISTING_COMMANDS:
        return ShellFinding(
            kind=KIND_INFORMATIONAL,
            category=CAT_LISTING,
            message=f"Directory listing command '{cmd_word}'",
            command=seg.raw,
        )

    # Search commands: grep, rg, ag, ack
    if cmd_word in SEARCH_COMMANDS:
        # If grep is in a downstream pipe and has no file args and not -r/-R, it's filtering stdin
        is_recursive = any(a in ("-r", "-R", "--recursive") for a in seg.words)
        if cmd_word in ("rg", "ag", "ack"):
            is_recursive = True

        # Extract search paths from arguments
        flags_with_args = {
            "-e",
            "--regexp",
            "-f",
            "--file",
            "-m",
            "--max-count",
            "-A",
            "-B",
            "-C",
            "--context",
            "--exclude",
            "--include",
            "--exclude-dir",
            "--include-dir",
        }
        idx = 1
        has_e_flag = False
        non_flag_args: List[str] = []
        while idx < len(seg.words):
            arg = seg.words[idx]
            if arg in flags_with_args:
                has_e_flag = has_e_flag or arg in ("-e", "--regexp", "-f", "--file")
                idx += 2
            elif any(arg.startswith(f + "=") for f in flags_with_args):
                has_e_flag = has_e_flag or any(arg.startswith(f + "=") for f in ("-e", "--regexp", "-f", "--file"))
                idx += 1
            elif arg.startswith("-"):
                idx += 1
            else:
                non_flag_args.append(arg)
                idx += 1

        if has_e_flag:
            search_paths = non_flag_args
        else:
            # First non-flag argument is pattern
            search_paths = non_flag_args[1:] if len(non_flag_args) > 1 else []

        if not search_paths and seg.is_downstream_pipe and not is_recursive:
            # Piped filter over stdin, e.g. git status | grep modified
            return None

        if not search_paths:
            # Defaults to effective working directory
            search_roots = ["."]
        else:
            search_roots = search_paths

        # Check each search root
        is_test_root = False
        target_path_str: Optional[str] = None
        for root in search_roots:
            rel, p_class = _resolve_and_classify(
                root,
                effective_cwd,
                repo_root,
                config=config,
                task_id=task_id,
                run_records=run_records,
                is_dir=True,
            )
            if role in ROLES_SHOULD_NOT_READ_TESTS:
                if p_class.base_class in ("tests", "test-output") or is_test_side_task_path(
                    rel, runs=run_records, task_id=task_id, repo_root=repo_root
                ):
                    is_test_root = True
                    target_path_str = rel
                    break

        if is_test_root:
            return ShellFinding(
                kind=KIND_CROSSING,
                category=CAT_TEST_SEARCH,
                message=f"Content search '{cmd_word}' rooted at tests",
                command=seg.raw,
                path=target_path_str,
            )
        else:
            return ShellFinding(
                kind=KIND_INFORMATIONAL,
                category=CAT_MIXED_SEARCH,
                message=f"Content search '{cmd_word}' over mixed root",
                command=seg.raw,
                path=search_roots[0] if search_roots else None,
            )

    return None


def _is_build_or_lintel_command(words: List[str]) -> bool:
    """Return True if command is a legitimate build/test runner or Lintel CLI."""
    if not words:
        return False

    first = words[0]
    base = os.path.basename(first)

    if first in BUILD_TEST_COMMANDS or base in BUILD_TEST_COMMANDS:
        return True

    if first in LINTEL_COMMANDS or base == "lintel":
        return True

    # Scripts in .lintel/scripts/ or test-*.sh
    if ".lintel/scripts/" in first or base.startswith("test-") and base.endswith(".sh"):
        return True

    # python -m pytest
    if base in ("python", "python3") and len(words) >= 3 and words[1] == "-m" and words[2] == "pytest":
        return True

    # sh / bash running test-*.sh or .lintel/scripts/
    if base in ("sh", "bash", "zsh") and len(words) >= 2:
        target_script = os.path.basename(words[1])
        if ".lintel/scripts/" in words[1] or (target_script.startswith("test-") and target_script.endswith(".sh")):
            return True

    return False


def _scan_command_tokens(
    seg: CommandSegment,
    effective_cwd: Path,
    repo_root: Path,
    role: str,
    config: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    run_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[ShellFinding]:
    """Scan command arguments and redirect targets for lane violations and test reports."""
    findings: List[ShellFinding] = []
    if not seg.words:
        return findings

    # Collect candidate tokens
    candidate_tokens: List[str] = []

    # Command arguments (skip command word itself)
    for w in seg.words[1:]:
        if w.startswith("--") and "=" in w:
            candidate_tokens.append(w.split("=", 1)[1])
        elif w.startswith("-"):
            continue
        else:
            candidate_tokens.append(w)

    # Redirection targets
    for op, target in seg.redirects:
        if target:
            candidate_tokens.append(target)

    for tok in candidate_tokens:
        clean_tok = tok.strip().strip("'\"")
        if not clean_tok:
            continue

        rel, p_class = _resolve_and_classify(
            clean_tok,
            effective_cwd,
            repo_root,
            config=config,
            task_id=task_id,
            run_records=run_records,
            is_dir=False,
        )

        # 1. Untracked test report: informational
        if p_class.base_class == "test-report":
            findings.append(
                ShellFinding(
                    kind=KIND_INFORMATIONAL,
                    category=CAT_TEST_REPORT,
                    message=f"Reading test report '{clean_tok}'",
                    command=seg.raw,
                    path=rel or clean_tok,
                )
            )
            continue

        # 2. Roles that should not read tests
        if role in ROLES_SHOULD_NOT_READ_TESTS:
            if p_class.base_class in ("tests", "test-output") or is_test_side_task_path(
                rel, runs=run_records, task_id=task_id, repo_root=repo_root
            ):
                findings.append(
                    ShellFinding(
                        kind=KIND_CROSSING,
                        category=CAT_READ_OUTSIDE_LANE,
                        message=f"Read outside lane targeting tests '{clean_tok}'",
                        command=seg.raw,
                        path=rel or clean_tok,
                    )
                )
                continue

            # Touching .lintel/state/
            if rel.startswith(".lintel/state/") or rel == ".lintel/state":
                findings.append(
                    ShellFinding(
                        kind=KIND_CROSSING,
                        category=CAT_READ_OUTSIDE_LANE,
                        message=f"Access outside lane targeting .lintel/state '{clean_tok}'",
                        command=seg.raw,
                        path=rel or clean_tok,
                    )
                )
                continue

    return findings


def scan_command_line(
    command_line: str,
    cwd: Union[str, Path],
    role: str,
    repo_root: Path,
    config: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    run_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[ShellFinding]:
    """Scan a shell command line and return all audit findings.

    Args:
        command_line: The full shell command line to analyze.
        cwd: Initial working directory for the command (relative or absolute).
        role: Persona role (e.g. 'dev', 'qa', 'orchestrator').
        repo_root: Path to the repository / workspace root.
        config: Loaded project configuration dict.
        task_id: Active task ID.
        run_records: Optional sequence of run record dictionaries.

    Returns:
        List of ShellFinding instances (both crossings and informational).
    """
    if not command_line or not command_line.strip():
        return []

    findings: List[ShellFinding] = []
    repo_root = Path(repo_root).resolve()
    effective_cwd = resolve_effective_cwd(cwd, repo_root)

    # 1. Dynamic construct detection
    dynamic_items = _find_dynamic_constructs(command_line)
    for c_type, snippet in dynamic_items:
        findings.append(
            ShellFinding(
                kind=KIND_INFORMATIONAL,
                category=CAT_DYNAMIC_CONSTRUCT,
                message=f"Dynamic construct {c_type} detected: {snippet}",
                command=command_line,
            )
        )

    # 2. Parse command line into segments
    segments = split_command_line(command_line)

    for seg in segments:
        if not seg.words:
            continue

        cmd_word = seg.words[0]
        base_cmd = os.path.basename(cmd_word)

        # Check for dynamic command words: eval, sh -c, bash -c, zsh -c
        if base_cmd == "eval":
            findings.append(
                ShellFinding(
                    kind=KIND_INFORMATIONAL,
                    category=CAT_DYNAMIC_CONSTRUCT,
                    message="Dynamic construct 'eval' detected",
                    command=seg.raw,
                )
            )
        elif base_cmd in ("sh", "bash", "zsh", "ksh", "dash") and len(seg.words) >= 2 and "-c" in seg.words:
            findings.append(
                ShellFinding(
                    kind=KIND_INFORMATIONAL,
                    category=CAT_DYNAMIC_CONSTRUCT,
                    message=f"Dynamic construct '{base_cmd} -c' detected",
                    command=seg.raw,
                )
            )

        # Working directory change: cd <dir>
        if base_cmd == "cd":
            if len(seg.words) == 1:
                effective_cwd = Path.home()
            else:
                target_dir = seg.words[1]
                if target_dir.startswith("~/") or target_dir == "~":
                    effective_cwd = Path(os.path.expanduser(target_dir)).resolve()
                elif os.path.isabs(target_dir):
                    effective_cwd = Path(target_dir).resolve()
                else:
                    effective_cwd = (effective_cwd / target_dir).resolve()
            continue

        # Git front-ends or Git commands
        if base_cmd in ("git", "tig", "gh", "glab"):
            git_findings = _check_git_command(
                seg.words,
                role=role,
                cwd=effective_cwd,
                repo_root=repo_root,
                config=config,
                task_id=task_id,
                run_records=run_records,
            )
            if git_findings:
                findings.extend(git_findings)
                continue
            elif base_cmd in ("git", "tig", "gh", "glab"):
                # Clean allowed git command
                continue

        # Search and listing commands
        search_finding = _scan_search_listing(
            seg,
            effective_cwd,
            repo_root,
            role=role,
            config=config,
            task_id=task_id,
            run_records=run_records,
        )
        if search_finding is not None:
            findings.append(search_finding)
            continue

        # Legitimate build tools and Lintel CLI
        if _is_build_or_lintel_command(seg.words):
            continue

        # General file reading / token scanning
        token_findings = _scan_command_tokens(
            seg,
            effective_cwd,
            repo_root,
            role=role,
            config=config,
            task_id=task_id,
            run_records=run_records,
        )
        findings.extend(token_findings)

    return findings


def is_command_line_clean(
    command_line: str,
    cwd: Union[str, Path],
    role: str,
    repo_root: Path,
    config: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    run_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> bool:
    """Return True if command line contains no crossing findings."""
    findings = scan_command_line(
        command_line,
        cwd=cwd,
        role=role,
        repo_root=repo_root,
        config=config,
        task_id=task_id,
        run_records=run_records,
    )
    return not any(f.kind == KIND_CROSSING for f in findings)
