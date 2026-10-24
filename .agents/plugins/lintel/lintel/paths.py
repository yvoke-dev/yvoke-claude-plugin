"""Path classification and pattern matching (M1a-05).

Implements:
1. Gitignore-style glob pattern matching, compilation, and normalisation:
   - Patterns without '/' match at any depth.
   - Patterns with '/' (except trailing) are anchored at repo root. Leading '/' stripped/anchored.
   - '**' matches zero or more directories.
   - '!' negates/excludes earlier matches; later positive pattern re-includes (last match wins).
   - Trailing slash on a glob matches only directories (or files inside them).
   - Symlinks followed / resolved.
   - Case-insensitive comparisons and glob matching on case-insensitive filesystems (macOS APFS).

2. Path Class hierarchy (PLAN §3.4):
   Evaluated in strict row order, first match wins:
   - Row 1: 'living-docs' (.lintel/specs/**, .lintel/steering/**).
   - Row 2: 'lintel' (.lintel/** except tasks/, plugin directory, .agents/agents/lintel-*.md, .git/**).
   - Row 3: 'agent-config' (.agents/**, **/AGENTS.md, **/GEMINI.md, .gemini/**, ~/.gemini/**, skills).
   - Row 4: 'task' (.lintel/tasks/<id>/** with 'task:<subdir>'; scratch dirs <tmp>/lintel/<task>/<role>/ as 'task:<role>').
   - Row 5: 'outside' (anything outside repo, except gate worktrees <tmp>/lintel-gate-*/** which count as 'tests').
   - Row 6: 'test-report' (UNTRACKED test reports: **/surefire-reports/**, TEST-*.xml, etc.).
   - Row 7: 'test-output' (UNTRACKED test output/cache/coverage: **/target/test-classes/**, **/__pycache__/**, coverage/**, etc.).
   - Row 8: 'tests' (test sources in test groups: unit, integration, e2e, etc. + support).
   - Row 9: 'code' (everything else).

3. Group assignment & Overlap detection (PLAN §3.8):
   - A file matching two named test groups is a configuration error (ConfigError / OverlapError).
   - Group 'support' is evaluated after named groups.

4. Suspect-test detection (PLAN §3.6):
   - 'code' files with a whole path segment from suspect words or filename containing both 'setup' and 'test'.

5. Test-side task dirs helper (PLAN §3.4):
   - Runs for qa, qa-reviewer, final-reviewer, arbiter on QA pair, PO and architect run folders with briefs
     holding copied QA sections, and gate/lock-*.log.
"""

from functools import lru_cache
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Union

try:
    from lintel.config import ConfigError
except ImportError:
    from .config import ConfigError

DEFAULT_PLUGIN_DIR = Path(__file__).resolve().parent.parent

# Suspect whole-path segments per PLAN §3.6
SUSPECT_SEGMENTS = frozenset({
    "test",
    "tests",
    "spec",
    "specs",
    "__tests__",
    "__mocks__",
    "mocks",
    "testdata",
    "golden",
    "e2e",
    "cypress",
    "fixtures",
})

# Roles that are inherently test-side per PLAN §3.4
TEST_SIDE_ROLES = frozenset({"qa", "qa-reviewer", "final-reviewer"})

# Default patterns for untracked reports and outputs (Rows 6 & 7)
DEFAULT_TEST_REPORT_PATTERNS = [
    "**/surefire-reports/**",
    "**/failsafe-reports/**",
    "**/TEST-*.xml",
    "TEST-*.xml",
]

DEFAULT_TEST_OUTPUT_PATTERNS = [
    "**/target/test-classes/**",
    "**/__pycache__/**",
    "**/.pytest_cache/**",
    "coverage/**",
    "htmlcov/**",
    "**/lcov-report/**",
]


class OverlapError(ConfigError):
    """Raised when a test file matches multiple named test groups."""
    pass


class PathClass(str):
    """Path classification result, acting as a string with metadata attributes."""

    base_class: str
    sub_class: Optional[str]
    group: Optional[str]

    def __new__(
        cls,
        value: str,
        base_class: Optional[str] = None,
        sub_class: Optional[str] = None,
        group: Optional[str] = None,
    ) -> "PathClass":
        instance = super().__new__(cls, value)
        if base_class is not None:
            instance.base_class = base_class
        elif ":" in value:
            instance.base_class = value.split(":", 1)[0]
        else:
            instance.base_class = value

        if sub_class is not None:
            instance.sub_class = sub_class
        elif ":" in value:
            instance.sub_class = value.split(":", 1)[1]
        else:
            instance.sub_class = None

        instance.group = group
        return instance

    def __repr__(self) -> str:
        extras = []
        if self.sub_class:
            extras.append(f"sub_class={self.sub_class!r}")
        if self.group:
            extras.append(f"group={self.group!r}")
        if extras:
            return f"PathClass({super().__repr__()}, {', '.join(extras)})"
        return f"PathClass({super().__repr__()})"


def is_case_insensitive_fs(path: Optional[Union[str, Path]] = None) -> bool:
    """Return True if the filesystem for path (or platform) is case-insensitive.

    On macOS (Darwin), APFS/HFS+ default is case-insensitive.
    """
    if sys.platform == "darwin":
        return True
    if sys.platform == "win32":
        return True
    if path is not None:
        try:
            p = Path(path).resolve()
            if p.exists():
                # Probe case-insensitivity on directory if possible
                test_upper = p / ".LINTEL_FS_PROBE"
                test_lower = p / ".lintel_fs_probe"
                if test_upper.exists() and test_lower.exists():
                    return True
        except Exception:
            pass
    return False


def resolve_temp_root(tmp_root: Optional[Union[str, Path]] = None) -> str:
    """Resolve the temporary root directory, following symlinks and ignoring trailing slashes.

    macOS spells one directory two ways (/var/folders/.../T/ and /private/var/...),
    so resolving symlinks canonicalizes them to the same path.
    """
    if tmp_root is not None:
        raw = str(tmp_root)
    else:
        raw = os.environ.get("TMPDIR") or tempfile.gettempdir()

    # Expand ~ and resolve realpath
    expanded = os.path.expanduser(raw)
    resolved = os.path.realpath(expanded).replace("\\", "/").rstrip("/")
    # On macOS /var is a symlink to /private/var
    if sys.platform == "darwin" and resolved.startswith("/var/"):
        resolved = "/private" + resolved
    return resolved


def _clean_path_str(path: Union[str, Path]) -> str:
    """Convert a path to normalized forward-slash string without trailing slash."""
    s = str(path).replace("\\", "/").strip()
    if s.endswith("/") and len(s) > 1:
        s = s.rstrip("/")
    return s


def normalize_path(
    path: Union[str, Path],
    repo_root: Optional[Union[str, Path]] = None,
    resolve_symlinks: bool = True,
) -> str:
    """Normalise a path string, resolving symlinks and making relative to repo root if inside.

    Returns:
    - Relative forward-slash path if path is inside repo_root.
    - Absolute resolved forward-slash path if outside repo_root.
    """
    path_str = _clean_path_str(path)
    if not path_str:
        return ""

    if repo_root is not None:
        res_repo = os.path.realpath(os.path.expanduser(str(repo_root))).replace("\\", "/").rstrip("/")
        if sys.platform == "darwin" and res_repo.startswith("/var/"):
            res_repo = "/private" + res_repo
    else:
        res_repo = None

    # Handle absolute vs relative path
    if os.path.isabs(path_str):
        if resolve_symlinks:
            target = os.path.realpath(os.path.expanduser(path_str)).replace("\\", "/")
            if sys.platform == "darwin" and target.startswith("/var/"):
                target = "/private" + target
        else:
            target = os.path.normpath(path_str).replace("\\", "/")
    elif res_repo is not None:
        candidate = os.path.join(res_repo, path_str)
        if resolve_symlinks and (os.path.exists(candidate) or os.path.islink(candidate)):
            target = os.path.realpath(candidate).replace("\\", "/")
            if sys.platform == "darwin" and target.startswith("/var/"):
                target = "/private" + target
        else:
            target = os.path.normpath(candidate).replace("\\", "/")
    else:
        # Relative path without repo_root
        return os.path.normpath(path_str).replace("\\", "/").lstrip("/")

    if res_repo is not None:
        # Check if inside repo
        case_fold = is_case_insensitive_fs(res_repo)
        target_cmp = target.lower() if case_fold else target
        repo_cmp = res_repo.lower() if case_fold else res_repo

        if target_cmp == repo_cmp:
            return ""
        if target_cmp.startswith(repo_cmp + "/"):
            rel = target[len(res_repo) + 1 :]
            return rel.replace("\\", "/")
        # Outside repo root
        return target

    return target


def _glob_to_regex(pat: str, anchored: bool, only_dir: bool) -> str:
    """Translate a gitignore glob pattern to a Python regular expression string."""
    if not pat:
        return r"^$" if anchored else r".*"

    res: List[str] = []
    i = 0
    n = len(pat)

    # Prefix anchoring
    if pat.startswith("**/"):
        res.append(r"(?:^|.*/)")
        i = 3
    elif anchored:
        res.append(r"^")
    else:
        res.append(r"(?:^|.*/)")

    while i < n:
        c = pat[i]
        if c == "/":
            if pat.startswith("/**/", i):
                res.append(r"(?:/|/.*/)")
                i += 4
                continue
            elif pat.startswith("/**", i) and i + 3 == n:
                res.append(r"(?:/.*)?")
                i += 3
                continue
            else:
                res.append(r"/")
                i += 1
                continue
        elif c == "*":
            if pat.startswith("**/", i):
                res.append(r"(?:^|.*/)")
                i += 3
                continue
            elif pat.startswith("**", i):
                res.append(r".*")
                i += 2
                continue
            else:
                res.append(r"[^/]*")
                i += 1
                continue
        elif c == "?":
            res.append(r"[^/]")
            i += 1
            continue
        elif c == "[":
            j = i + 1
            if j < n and pat[j] == "!":
                j += 1
            if j < n and pat[j] == "]":
                j += 1
            while j < n and pat[j] != "]":
                j += 1
            if j < n:
                char_class = pat[i + 1 : j]
                if char_class.startswith("!"):
                    char_class = "^" + char_class[1:]
                res.append(f"[{char_class}]")
                i = j + 1
                continue
            else:
                res.append(re.escape(c))
                i += 1
                continue
        else:
            res.append(re.escape(c))
            i += 1
            continue

    if only_dir:
        res.append(r"(?:/.*)?$")
    else:
        res.append(r"$")

    return "".join(res)


class GlobPattern:
    """Compiled gitignore-style glob pattern."""

    __slots__ = ("raw", "regex", "is_negated", "only_dir", "anchored", "pat")

    def __init__(
        self,
        raw: str,
        regex: re.Pattern,
        is_negated: bool,
        only_dir: bool,
        anchored: bool,
        pat: str,
    ):
        self.raw = raw
        self.regex = regex
        self.is_negated = is_negated
        self.only_dir = only_dir
        self.anchored = anchored
        self.pat = pat

    def matches(self, path: str, is_dir: bool = False) -> bool:
        norm = path.replace("\\", "/").strip("/")
        if not norm:
            return False

        m = self.regex.search(norm)
        if not m:
            return False

        if self.only_dir and not is_dir:
            # Trailing slash pattern requires either is_dir=True or matching inside the dir
            if self.anchored:
                if norm == self.pat:
                    return False
            else:
                if norm == self.pat or norm.endswith("/" + self.pat):
                    return False

        return True

    def __repr__(self) -> str:
        return f"GlobPattern({self.raw!r})"


@lru_cache(maxsize=1024)
def compile_glob(pattern: str, case_sensitive: bool = True) -> GlobPattern:
    """Compile a gitignore-style pattern into a GlobPattern object."""
    raw = pattern.strip()
    is_negated = False
    if raw.startswith("!"):
        is_negated = True
        pat = raw[1:].strip()
    else:
        pat = raw

    only_dir = False
    if pat.endswith("/"):
        only_dir = True
        pat = pat[:-1]

    anchored = False
    if pat.startswith("/"):
        anchored = True
        pat = pat[1:]
    elif "/" in pat:
        anchored = True

    regex_str = _glob_to_regex(pat, anchored=anchored, only_dir=only_dir)
    flags = 0 if case_sensitive else re.IGNORECASE
    compiled = re.compile(regex_str, flags)
    return GlobPattern(raw, compiled, is_negated, only_dir, anchored, pat)


def match_globs(
    path: Union[str, Path],
    patterns: Sequence[str],
    is_dir: bool = False,
    case_sensitive: Optional[bool] = None,
) -> bool:
    """Evaluate gitignore-style glob patterns in order against path.

    - Empty/comment patterns ignored.
    - Last match wins across merged layers.
    - '!' negates an earlier match; a later positive pattern can re-include.
    """
    if not patterns:
        return False

    norm_path = str(path).replace("\\", "/").strip("/")
    if not norm_path:
        return False

    if case_sensitive is None:
        case_sensitive = not is_case_insensitive_fs()

    matched = False
    for pat in patterns:
        if not pat or pat.startswith("#"):
            continue
        compiled = compile_glob(pat, case_sensitive=case_sensitive)
        if compiled.matches(norm_path, is_dir=is_dir):
            matched = not compiled.is_negated

    return matched


def assign_test_group(
    path: Union[str, Path],
    config: dict,
    repo_root: Optional[Union[str, Path]] = None,
    is_dir: bool = False,
    case_sensitive: Optional[bool] = None,
) -> Optional[str]:
    """Assign a path to its test group according to config['paths']['tests'].

    - Named groups are checked in order.
    - If a path matches two named groups, raises OverlapError (ConfigError).
    - Group 'support' is evaluated after named groups (only if no named group matched).
    """
    norm_path = normalize_path(path, repo_root=repo_root, resolve_symlinks=True)
    if not norm_path:
        return None

    paths_cfg = config.get("paths", {})
    test_groups = paths_cfg.get("tests", {})
    if not test_groups or not isinstance(test_groups, dict):
        return None

    if case_sensitive is None:
        case_sensitive = not is_case_insensitive_fs(repo_root)

    named_groups = [g for g in test_groups.keys() if g != "support"]
    matching_named_groups: List[str] = []

    for g_name in named_groups:
        spec = test_groups[g_name]
        if spec is None:
            continue
        if isinstance(spec, list):
            patterns = spec
        elif isinstance(spec, dict):
            patterns = spec.get("paths", [])
        else:
            continue

        if match_globs(norm_path, patterns, is_dir=is_dir, case_sensitive=case_sensitive):
            matching_named_groups.append(g_name)

    if len(matching_named_groups) > 1:
        raise OverlapError(
            f"Test path {norm_path!r} matches multiple named test groups: {matching_named_groups}"
        )

    if len(matching_named_groups) == 1:
        return matching_named_groups[0]

    # Evaluate 'support' group if no named group matched
    if "support" in test_groups:
        spec = test_groups["support"]
        if spec is not None:
            if isinstance(spec, list):
                patterns = spec
            elif isinstance(spec, dict):
                patterns = spec.get("paths", [])
            else:
                patterns = []

            if match_globs(norm_path, patterns, is_dir=is_dir, case_sensitive=case_sensitive):
                return "support"

    return None


test_group_for_path = assign_test_group


def detect_test_group_overlaps(
    paths: Iterable[Union[str, Path]],
    config: dict,
    repo_root: Optional[Union[str, Path]] = None,
    case_sensitive: Optional[bool] = None,
) -> Dict[str, List[str]]:
    """Check a collection of paths for test group overlaps.

    Returns a dict mapping path to the list of matching named groups for any overlapping paths.
    """
    overlaps: Dict[str, List[str]] = {}
    paths_cfg = config.get("paths", {})
    test_groups = paths_cfg.get("tests", {})
    if not test_groups or not isinstance(test_groups, dict):
        return overlaps

    named_groups = [g for g in test_groups.keys() if g != "support"]
    if case_sensitive is None:
        case_sensitive = not is_case_insensitive_fs(repo_root)

    for p in paths:
        norm = normalize_path(p, repo_root=repo_root, resolve_symlinks=True)
        if not norm:
            continue
        matched: List[str] = []
        for g_name in named_groups:
            spec = test_groups[g_name]
            if spec is None:
                continue
            patterns = spec.get("paths", []) if isinstance(spec, dict) else spec
            if isinstance(patterns, list) and match_globs(norm, patterns, case_sensitive=case_sensitive):
                matched.append(g_name)
        if len(matched) > 1:
            overlaps[norm] = matched

    return overlaps


def is_test_side_task_path(
    path: Union[str, Path],
    runs: Optional[Sequence[Dict[str, Any]]] = None,
    task_id: Optional[str] = None,
    task_dir: Optional[Union[str, Path]] = None,
    repo_root: Optional[Union[str, Path]] = None,
) -> bool:
    """Return True if path is in a test-side task directory per PLAN §3.4.

    Test-side task paths are:
    - Files or directories for roles 'qa', 'qa-reviewer', 'final-reviewer' in any task.
    - Run folders for arbiter ruling on the QA pair.
    - Run folders for PO and architect whose brief holds a copied QA section.
    - Gate lock logs 'gate/lock-*.log'.
    """
    raw_str = str(path).replace("\\", "/")

    # 1. Gate lock log: gate/lock-*.log
    if re.search(r"(?:^|[/\\])gate[/\\]lock-[^/\\]*\.log$", raw_str):
        return True

    # 2. Check task roles: .lintel/tasks/<task_id>/<role>/...
    if ".lintel/tasks/" in raw_str:
        rel = raw_str[raw_str.index(".lintel/tasks/") + len(".lintel/tasks/") :]
        parts = rel.split("/")
        if len(parts) >= 2 and parts[1] in TEST_SIDE_ROLES:
            return True

    # Check scratch dir roles: <tmp>/lintel/<task>/<role>/...
    if "/lintel/" in raw_str or raw_str.startswith("lintel/"):
        idx = raw_str.index("lintel/")
        rel = raw_str[idx + len("lintel/") :]
        parts = rel.split("/")
        if len(parts) >= 2 and parts[1] in TEST_SIDE_ROLES:
            return True

    # 3. Check run records if supplied
    if runs:
        norm_target = normalize_path(path, repo_root=repo_root, resolve_symlinks=True)
        for run in runs:
            role = run.get("role")
            writer = run.get("writer") or run.get("pair")
            folder = run.get("folder")
            if not folder:
                continue

            is_test_side_run = False
            if role in TEST_SIDE_ROLES:
                is_test_side_run = True
            elif role == "arbiter" and writer == "qa":
                is_test_side_run = True
            elif role in ("po", "po-reviewer", "architect", "architect-reviewer"):
                if run.get("brief_holds_qa_section") or run.get("holds_copied_qa"):
                    is_test_side_run = True
                elif run.get("from_report"):
                    fr = run["from_report"]
                    if isinstance(fr, dict):
                        fr_run = str(fr.get("run", "")).lower()
                        fr_hd = str(fr.get("heading", "")).lower()
                        if "qa" in fr_run or "qa" in fr_hd:
                            is_test_side_run = True
                    elif isinstance(fr, str) and "qa" in fr.lower():
                        is_test_side_run = True

            if is_test_side_run:
                norm_folder = normalize_path(folder, repo_root=repo_root, resolve_symlinks=True)
                if norm_target == norm_folder or norm_target.startswith(norm_folder + "/"):
                    return True

    return False


def get_test_side_task_dirs(
    runs: Optional[Sequence[Dict[str, Any]]] = None,
    task_id: Optional[str] = None,
    task_dir: Optional[Union[str, Path]] = None,
    repo_root: Optional[Union[str, Path]] = None,
) -> Set[str]:
    """Return set of test-side task directories given runs and task info."""
    result: Set[str] = set()

    prefix = f".lintel/tasks/{task_id}" if task_id else ".lintel/tasks/*"
    for role in TEST_SIDE_ROLES:
        result.add(f"{prefix}/{role}")

    result.add(f"{prefix}/gate/lock-*.log")

    if runs:
        for run in runs:
            role = run.get("role")
            writer = run.get("writer") or run.get("pair")
            folder = run.get("folder")
            if not folder:
                continue

            is_test_side_run = False
            if role in TEST_SIDE_ROLES:
                is_test_side_run = True
            elif role == "arbiter" and writer == "qa":
                is_test_side_run = True
            elif role in ("po", "po-reviewer", "architect", "architect-reviewer"):
                if run.get("brief_holds_qa_section") or run.get("holds_copied_qa"):
                    is_test_side_run = True
                elif run.get("from_report"):
                    fr = run["from_report"]
                    if isinstance(fr, dict):
                        fr_run = str(fr.get("run", "")).lower()
                        fr_hd = str(fr.get("heading", "")).lower()
                        if "qa" in fr_run or "qa" in fr_hd:
                            is_test_side_run = True
                    elif isinstance(fr, str) and "qa" in fr.lower():
                        is_test_side_run = True

            if is_test_side_run:
                norm = normalize_path(folder, repo_root=repo_root, resolve_symlinks=True)
                result.add(norm)

    return result


def is_suspect_test(
    path: Union[str, Path],
    config: Optional[dict] = None,
    repo_root: Optional[Union[str, Path]] = None,
    is_tracked: bool = True,
    path_class: Optional[str] = None,
    case_sensitive: Optional[bool] = None,
) -> bool:
    """Return True if path is classified as 'code' and matches suspect-test heuristics (PLAN §3.6).

    Suspect tests: 'code' files with a whole path segment from:
    test, tests, spec, specs, __tests__, __mocks__, mocks, testdata, golden, e2e, cypress, fixtures,
    or a filename containing both 'setup' and 'test'.
    """
    if path_class is None:
        p_class = classify_path(
            path,
            config=config,
            repo_root=repo_root,
            is_tracked=is_tracked,
            case_sensitive=case_sensitive,
        )
        path_class = str(p_class)

    # Suspect tests apply to 'code' files only
    if path_class != "code":
        return False

    norm = normalize_path(path, repo_root=repo_root, resolve_symlinks=True)
    parts = Path(norm).parts
    filename = Path(norm).name

    for part in parts:
        clean_part = part.strip()
        part_lower = clean_part.lower()
        if part_lower in SUSPECT_SEGMENTS:
            return True
        stem_lower = Path(clean_part).stem.lower()
        if stem_lower in SUSPECT_SEGMENTS:
            return True

    fn_lower = filename.lower()
    if "setup" in fn_lower and "test" in fn_lower:
        return True

    return False


def find_suspect_tests(
    paths: Iterable[Union[str, Path]],
    config: Optional[dict] = None,
    repo_root: Optional[Union[str, Path]] = None,
    is_tracked: bool = True,
    case_sensitive: Optional[bool] = None,
) -> List[str]:
    """Find and return all suspect test files from an iterable of paths."""
    suspects: List[str] = []
    for p in paths:
        if is_suspect_test(
            p,
            config=config,
            repo_root=repo_root,
            is_tracked=is_tracked,
            case_sensitive=case_sensitive,
        ):
            norm = normalize_path(p, repo_root=repo_root, resolve_symlinks=True)
            suspects.append(norm)
    return suspects


def classify_path(
    path: Union[str, Path],
    config: Optional[dict] = None,
    repo_root: Optional[Union[str, Path]] = None,
    is_tracked: bool = False,
    tmp_root: Optional[Union[str, Path]] = None,
    plugin_dir: Optional[Union[str, Path]] = None,
    task_id: Optional[str] = None,
    for_audit: bool = False,
    runs: Optional[Sequence[Dict[str, Any]]] = None,
    is_dir: bool = False,
    case_sensitive: Optional[bool] = None,
) -> PathClass:
    """Classify a path according to the PLAN §3.4 hierarchy.

    Evaluated in strict row order, first match wins:
    1. living-docs: .lintel/specs/** and .lintel/steering/**
    2. lintel: .lintel/** except tasks/, plugin dir, .agents/agents/lintel-*.md, .git/**
    3. agent-config: skills, .agents/**, **/AGENTS.md, **/GEMINI.md, .gemini/**, ~/.gemini/**
    4. task: .lintel/tasks/<id>/** with 'task:<subdir>'; <tmp>/lintel/<task>/<role>/ as 'task:<role>'
    5. outside: anything outside repo workspace, except gate worktrees <tmp>/lintel-gate-*/** which count as 'tests'
    6. test-report: untracked test reports (**/surefire-reports/**, TEST-*.xml) - untracked only
    7. test-output: untracked test output/classes/caches/coverage (**/__pycache__/**, coverage/**) - untracked only
    8. tests: test sources mapped to test groups + support
    9. code: everything else
    """
    if config is None:
        config = {}

    if case_sensitive is None:
        case_sensitive = not is_case_insensitive_fs(repo_root)

    res_tmp = resolve_temp_root(tmp_root)

    if plugin_dir is not None:
        res_plugin = os.path.realpath(os.path.expanduser(str(plugin_dir))).replace("\\", "/").rstrip("/")
        if sys.platform == "darwin" and res_plugin.startswith("/var/"):
            res_plugin = "/private" + res_plugin
    else:
        res_plugin = os.path.realpath(str(DEFAULT_PLUGIN_DIR)).replace("\\", "/").rstrip("/")
        if sys.platform == "darwin" and res_plugin.startswith("/var/"):
            res_plugin = "/private" + res_plugin

    if repo_root is not None:
        res_repo = os.path.realpath(os.path.expanduser(str(repo_root))).replace("\\", "/").rstrip("/")
        if sys.platform == "darwin" and res_repo.startswith("/var/"):
            res_repo = "/private" + res_repo
    else:
        res_repo = None

    # Resolve target path
    raw_path_str = _clean_path_str(path)
    if os.path.isabs(raw_path_str):
        res_path = os.path.realpath(os.path.expanduser(raw_path_str)).replace("\\", "/")
        if sys.platform == "darwin" and res_path.startswith("/var/"):
            res_path = "/private" + res_path
    elif res_repo is not None:
        candidate = os.path.join(res_repo, raw_path_str)
        res_path = os.path.realpath(candidate).replace("\\", "/")
        if sys.platform == "darwin" and res_path.startswith("/var/"):
            res_path = "/private" + res_path
    else:
        res_path = os.path.normpath(raw_path_str).replace("\\", "/")

    # Check if target is inside repo
    if res_repo is not None:
        cmp_path = res_path.lower() if not case_sensitive else res_path
        cmp_repo = res_repo.lower() if not case_sensitive else res_repo
        if cmp_path == cmp_repo:
            is_inside_repo = True
            rel_path = ""
        elif cmp_path.startswith(cmp_repo + "/"):
            is_inside_repo = True
            rel_path = res_path[len(res_repo) + 1 :]
        else:
            is_inside_repo = False
            rel_path = None
    else:
        if os.path.isabs(raw_path_str):
            is_inside_repo = False
            rel_path = None
        else:
            is_inside_repo = True
            rel_path = raw_path_str.lstrip("/")

    # Audit override: test-side task dirs count as 'tests'
    if for_audit and is_test_side_task_path(res_path, runs=runs, task_id=task_id, repo_root=res_repo):
        return PathClass("tests", base_class="tests", group="support")

    # -------------------------------------------------------------------------
    # Row 1: living-docs
    # .lintel/specs/** and .lintel/steering/**
    # -------------------------------------------------------------------------
    if is_inside_repo and rel_path is not None:
        rel_cmp = rel_path.lower() if not case_sensitive else rel_path
        if (
            rel_cmp == ".lintel/specs"
            or rel_cmp.startswith(".lintel/specs/")
            or rel_cmp == ".lintel/steering"
            or rel_cmp.startswith(".lintel/steering/")
        ):
            return PathClass("living-docs", base_class="living-docs")

    # -------------------------------------------------------------------------
    # Row 2: lintel
    # .lintel/** except tasks/, plugin directory, .agents/agents/lintel-*.md, .git/**
    # -------------------------------------------------------------------------
    # Check plugin dir (even if global / outside repo)
    cmp_path = res_path.lower() if not case_sensitive else res_path
    cmp_plugin = res_plugin.lower() if not case_sensitive else res_plugin
    if cmp_path == cmp_plugin or cmp_path.startswith(cmp_plugin + "/"):
        return PathClass("lintel", base_class="lintel")

    if is_inside_repo and rel_path is not None:
        rel_cmp = rel_path.lower() if not case_sensitive else rel_path

        # .agents/agents/lintel-*.md
        if re.search(r"^\.agents/agents/lintel-[^/]*\.md$", rel_cmp):
            return PathClass("lintel", base_class="lintel")

        # .lintel/** except tasks/
        if rel_cmp == ".lintel" or rel_cmp.startswith(".lintel/"):
            if rel_cmp == ".lintel/tasks" or rel_cmp.startswith(".lintel/tasks/"):
                pass  # Falls through to Row 4 (task)
            else:
                return PathClass("lintel", base_class="lintel")

        # .git/**
        if rel_cmp == ".git" or rel_cmp.startswith(".git/"):
            return PathClass("lintel", base_class="lintel")

    # -------------------------------------------------------------------------
    # Row 3: agent-config
    # skills, .agents/**, **/AGENTS.md, **/GEMINI.md, .gemini/**, ~/.gemini/**
    # -------------------------------------------------------------------------
    # User home agent dir ~/.gemini/**
    home_gemini = os.path.realpath(os.path.expanduser("~/.gemini")).replace("\\", "/").rstrip("/")
    if sys.platform == "darwin" and home_gemini.startswith("/var/"):
        home_gemini = "/private" + home_gemini
    cmp_home_gemini = home_gemini.lower() if not case_sensitive else home_gemini

    if cmp_path == cmp_home_gemini or cmp_path.startswith(cmp_home_gemini + "/"):
        return PathClass("agent-config", base_class="agent-config")

    if is_inside_repo and rel_path is not None:
        rel_cmp = rel_path.lower() if not case_sensitive else rel_path

        # .agents/** (note: lintel-*.md was matched by Row 2)
        if rel_cmp == ".agents" or rel_cmp.startswith(".agents/"):
            return PathClass("agent-config", base_class="agent-config")

        # .gemini/**
        if rel_cmp == ".gemini" or rel_cmp.startswith(".gemini/"):
            return PathClass("agent-config", base_class="agent-config")

        # **/AGENTS.md, **/GEMINI.md
        base_name = os.path.basename(rel_cmp)
        if base_name in ("agents.md", "gemini.md"):
            return PathClass("agent-config", base_class="agent-config")

    # -------------------------------------------------------------------------
    # Row 4: task
    # .lintel/tasks/<id>/** with 'task:<subdir>'
    # Scratch dirs: <tmp>/lintel/<task>/<role>/ as 'task:<role>'
    # -------------------------------------------------------------------------
    # Scratch dir under temp root
    cmp_tmp = res_tmp.lower() if not case_sensitive else res_tmp
    if cmp_path == cmp_tmp + "/lintel" or cmp_path.startswith(cmp_tmp + "/lintel/"):
        rel_tmp = res_path[len(res_tmp) + 1 :]  # e.g. lintel/<task>/<role>/...
        parts = rel_tmp.split("/")
        # parts[0] is 'lintel', parts[1] is <task>, parts[2] is <role>
        if len(parts) >= 3 and parts[2]:
            role = parts[2]
            return PathClass(f"task:{role}", base_class="task", sub_class=role)
        return PathClass("task", base_class="task")

    # Task directory in repo
    if is_inside_repo and rel_path is not None:
        rel_cmp = rel_path.lower() if not case_sensitive else rel_path
        if rel_cmp.startswith(".lintel/tasks/"):
            rel_after = rel_path[len(".lintel/tasks/") :]
            parts = rel_after.split("/")
            # parts[0] is <id>
            if len(parts) >= 3 and parts[1]:
                subdir = parts[1]
                return PathClass(f"task:{subdir}", base_class="task", sub_class=subdir)
            elif len(parts) == 2 and parts[1]:
                # If len(parts) == 2, check if parts[1] is a directory or top-level file
                if is_dir or ("." not in parts[1]):
                    subdir = parts[1]
                    return PathClass(f"task:{subdir}", base_class="task", sub_class=subdir)
                return PathClass("task", base_class="task")
            return PathClass("task", base_class="task")

    # -------------------------------------------------------------------------
    # Row 5: outside
    # Anything outside repo workspace, EXCEPT gate worktrees <tmp>/lintel-gate-*/** which count as 'tests'
    # -------------------------------------------------------------------------
    if not is_inside_repo:
        # Check gate worktrees: <tmp>/lintel-gate-*/**
        if cmp_path == cmp_tmp or cmp_path.startswith(cmp_tmp + "/"):
            rel_tmp = res_path[len(res_tmp) + 1 :]
            parts = rel_tmp.split("/")
            if parts and parts[0].startswith("lintel-gate-"):
                return PathClass("tests", base_class="tests", group="gate")

        return PathClass("outside", base_class="outside")

    # Path is inside repo. From here on, rel_path is not None.
    assert rel_path is not None

    # -------------------------------------------------------------------------
    # Row 6: test-report (UNTRACKED files only)
    # **/surefire-reports/**, **/failsafe-reports/**, TEST-*.xml, etc.
    # -------------------------------------------------------------------------
    if not is_tracked:
        report_patterns = config.get("paths", {}).get("testReport", [])
        if not report_patterns:
            report_patterns = DEFAULT_TEST_REPORT_PATTERNS
        if match_globs(rel_path, report_patterns, is_dir=is_dir, case_sensitive=case_sensitive):
            return PathClass("test-report", base_class="test-report")

    # -------------------------------------------------------------------------
    # Row 7: test-output (UNTRACKED files only)
    # **/target/test-classes/**, **/pycache/**, **/.pytest_cache/**, coverage/**, etc.
    # -------------------------------------------------------------------------
    if not is_tracked:
        output_patterns = config.get("paths", {}).get("testOutput", [])
        if not output_patterns:
            output_patterns = DEFAULT_TEST_OUTPUT_PATTERNS
        if match_globs(rel_path, output_patterns, is_dir=is_dir, case_sensitive=case_sensitive):
            return PathClass("test-output", base_class="test-output")

    # -------------------------------------------------------------------------
    # Row 8: tests
    # Test sources in test groups (unit, integration, e2e, etc. + support)
    # -------------------------------------------------------------------------
    group = assign_test_group(
        rel_path,
        config=config,
        repo_root=res_repo,
        is_dir=is_dir,
        case_sensitive=case_sensitive,
    )
    if group is not None:
        return PathClass("tests", base_class="tests", group=group)

    # -------------------------------------------------------------------------
    # Row 9: code
    # Everything else
    # -------------------------------------------------------------------------
    return PathClass("code", base_class="code")
