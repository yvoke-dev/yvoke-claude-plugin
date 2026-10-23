"""Doctor diagnostic checks (M1a-15, PLAN §3.5, §11).

Implements doctor(project_dir, role=None, path_arg=None) -> DoctorReport:
- Path argument inspection (--path): prints path class and test group.
- Config validation: validates .lintel/config.jsonc via load_config, checks for test group overlaps.
- Python interpreter check: checks .lintel/state/python, verifies executable, re-resolves and records if broken.
- Git version check: verifies git >= 2.25.0 via check_git_version.
- Scripts check:
  - Verifies each test group in config has .lintel/scripts/test-<group>.sh.
  - Warns about unadjusted templates (is_unadjusted_template) and unadjusted build.sh.
  - Warns about older contract versions (< 1).
- Suspect tests check: reports files matching suspect-test heuristics classified as code.
- Agent files check:
  - Verifies all 10 agent files exist in .agents/agents/lintel-<role>.md.
  - Checks they equal template copies.
  - Checks no forbidden frontmatter keys (model:, commandExecutionPolicy:, inheritCustomizations:, hooks:).
  - Checks allowed tools list.
- Platform settings check:
  - Inspects agy binary on PATH (/config and /permissions slash commands).
  - Terminal sandbox detection (enableTerminalSandbox: false required).
  - Allow rules check (checks for required rules for this project).
  - Advisories if agy not on PATH or slash commands unavailable.
  - macOS notes: no Automation/Accessibility grants to host app; checks Aqua GUI session.
  - Hint for invoke_subagent failures with "not found or not allowed".
- Role inspection (--role): shows skills and steering files resolved for the role.
"""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from lintel.caller import is_macos_gui_session
from lintel.config import (
    ConfigError,
    load_config,
    resolve_skills_for_role,
    resolve_steering_for_role,
)
from lintel.find_python import SNIPPET, find_and_record_python, run_bounded
from lintel.git import GitError, check_git_version
from lintel.paths import (
    classify_path,
    detect_test_group_overlaps,
    find_suspect_tests,
    test_group_for_path,
)
from lintel.personas import VALID_ROLES
from lintel.scripts import is_unadjusted_template, parse_script_header
from lintel.self_check import ALL_10_ROLES
from lintel.skills import resolve_role_steering_with_sources

DEFAULT_PLUGIN_DIR = Path(__file__).resolve().parent.parent

FORBIDDEN_FRONTMATTER_KEYS = (
    "model:",
    "commandExecutionPolicy:",
    "inheritCustomizations:",
    "hooks:",
)

ACCEPTED_TOOLS: Set[str] = {
    "view_file",
    "grep_search",
    "find_by_name",
    "list_dir",
    "run_command",
    "write_to_file",
    "replace_file_content",
    "send_message",
}


@dataclass
class DoctorIssue:
    kind: str  # "error", "warning", "note"
    category: str
    message: str
    details: Optional[str] = None

    def __str__(self) -> str:
        prefix = self.kind.upper()
        return f"[{prefix}] ({self.category}) {self.message}"


@dataclass
class PlatformSettings:
    agy_on_path: bool = False
    agy_version: Optional[str] = None
    terminal_sandbox: Optional[bool] = None
    tool_permission: Optional[str] = None
    trusted_workspaces: Optional[List[str]] = None
    allow_rules: Optional[List[str]] = None
    raw_config: Optional[Dict[str, str]] = None


@dataclass
class DoctorReport:
    project_dir: Path
    issues: List[DoctorIssue] = field(default_factory=list)
    path_info: Optional[Dict[str, Any]] = None
    role_info: Optional[Dict[str, Any]] = None
    group_counts: Optional[Dict[str, int]] = None
    group_files: Optional[Dict[str, List[str]]] = None
    class_counts: Optional[Dict[str, int]] = None
    class_files: Optional[Dict[str, List[str]]] = None
    full: bool = False

    @property
    def errors(self) -> List[str]:
        return [i.message for i in self.issues if i.kind == "error"]

    @property
    def warnings(self) -> List[str]:
        return [i.message for i in self.issues if i.kind == "warning"]

    @property
    def notes(self) -> List[str]:
        return [i.message for i in self.issues if i.kind == "note"]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def format(self) -> str:
        lines: List[str] = []
        lines.append(f"Lintel Doctor Report for: {self.project_dir}")
        lines.append("=" * 60)

        if self.path_info:
            lines.append("Path Classification:")
            lines.append(f"  Path:  {self.path_info.get('path')}")
            lines.append(f"  Class: {self.path_info.get('class')}")
            lines.append(f"  Group: {self.path_info.get('group') or '(none)'}")
            lines.append("-" * 60)

        if self.role_info:
            lines.append(f"Role: {self.role_info.get('role')}")
            lines.append("  Skills:")
            skills = self.role_info.get('skills', [])
            if not skills:
                lines.append("    (none)")
            else:
                for s, src in skills:
                    lines.append(f"    {s} ({src})")
            lines.append("  Steering:")
            steering = self.role_info.get('steering', [])
            if not steering:
                lines.append("    (none)")
            else:
                for s, src in steering:
                    lines.append(f"    {s} ({src})")
            lines.append("-" * 60)

        if self.class_counts is not None:
            lines.append("Path Classes:")
            for cls_name, cnt in sorted(self.class_counts.items()):
                if cnt > 0:
                    lines.append(f"  {cls_name}: {cnt} file(s)")
                    if self.full and self.class_files and self.class_files.get(cls_name):
                        for f in sorted(self.class_files[cls_name]):
                            lines.append(f"    {f}")
            lines.append("-" * 60)

        if self.group_counts is not None:
            lines.append("Test Groups:")
            for g, cnt in self.group_counts.items():
                lines.append(f"  {g}: {cnt} file(s)")
                if self.full and self.group_files and self.group_files.get(g):
                    for f in sorted(self.group_files[g]):
                        lines.append(f"    {f}")
            lines.append("-" * 60)


        if self.errors:
            lines.append(f"Errors ({len(self.errors)}):")
            for e in self.errors:
                lines.append(f"  [ERROR] {e}")
            lines.append("")

        if self.warnings:
            lines.append(f"Warnings ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"  [WARN]  {w}")
            lines.append("")

        if self.notes:
            lines.append("Advisory Notes:")
            for n in self.notes:
                lines.append(f"  [NOTE]  {n}")
            lines.append("")

        if self.ok:
            lines.append("Result: doctor OK (all critical checks passed)")
        else:
            lines.append(f"Result: doctor FAIL ({len(self.errors)} error(s) found)")

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.format()


def inspect_platform_settings(project_dir: Path) -> PlatformSettings:
    """Inspect platform settings via agy CLI, environment hooks or files."""
    settings = PlatformSettings()

    # 1. Environment hooks for testing
    if os.environ.get("LINTEL_TEST_TERMINAL_SANDBOX") == "1":
        settings.terminal_sandbox = True
    elif os.environ.get("LINTEL_TEST_TERMINAL_SANDBOX") == "0":
        settings.terminal_sandbox = False

    if os.environ.get("LINTEL_TEST_ALLOW_RULES") is not None:
        rules_env = os.environ["LINTEL_TEST_ALLOW_RULES"]
        settings.allow_rules = [r.strip() for r in rules_env.split(",") if r.strip()]

    if os.environ.get("LINTEL_TEST_AGY_VERSION") is not None:
        settings.agy_version = os.environ["LINTEL_TEST_AGY_VERSION"]

    if os.environ.get("LINTEL_TEST_TOOL_PERMISSION") is not None:
        settings.tool_permission = os.environ["LINTEL_TEST_TOOL_PERMISSION"]

    if os.environ.get("LINTEL_TEST_TRUSTED_WORKSPACES") is not None:
        raw_tw = os.environ["LINTEL_TEST_TRUSTED_WORKSPACES"]
        try:
            settings.trusted_workspaces = json.loads(raw_tw)
        except Exception:
            settings.trusted_workspaces = [w.strip() for w in raw_tw.split(",") if w.strip()]

    if os.environ.get("LINTEL_TEST_AGY_ON_PATH") == "0":
        settings.agy_on_path = False
        return settings

    # 2. Check agy binary on PATH
    if os.environ.get("LINTEL_TEST_AGY_ON_PATH") == "1":
        settings.agy_on_path = True
        return settings

    agy_bin = shutil.which("agy")
    if not agy_bin:
        settings.agy_on_path = False
        return settings

    settings.agy_on_path = True

    # agy --version
    try:
        proc_ver = subprocess.run(
            [agy_bin, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=3,
        )
        if proc_ver.returncode == 0 and settings.agy_version is None:
            settings.agy_version = proc_ver.stdout.strip()
    except Exception:
        pass

    # agy -p "/config"
    try:
        proc_cfg = subprocess.run(
            [agy_bin, "-p", "/config"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=5,
        )
        if proc_cfg.returncode == 0:
            cfg_dict: Dict[str, str] = {}
            for line in proc_cfg.stdout.splitlines():
                parts = line.strip().split("\t", 1)
                if len(parts) == 2:
                    cfg_dict[parts[0].strip()] = parts[1].strip()
                elif len(parts) == 1 and parts[0]:
                    cfg_dict[parts[0].strip()] = ""
            settings.raw_config = cfg_dict
            if "enableTerminalSandbox" in cfg_dict and settings.terminal_sandbox is None:
                settings.terminal_sandbox = (cfg_dict["enableTerminalSandbox"].lower() == "true")
            if "toolPermission" in cfg_dict and settings.tool_permission is None:
                settings.tool_permission = cfg_dict["toolPermission"]
            if "trustedWorkspaces" in cfg_dict and settings.trusted_workspaces is None:
                try:
                    settings.trusted_workspaces = json.loads(cfg_dict["trustedWorkspaces"])
                except Exception:
                    pass
    except Exception:
        pass

    # agy -p "/permissions"
    try:
        proc_perm = subprocess.run(
            [agy_bin, "-p", "/permissions"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=5,
        )
        if proc_perm.returncode == 0:
            rules: List[str] = []
            for line in proc_perm.stdout.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 3 and parts[1] == "allow":
                    rules.append(parts[2].strip())
                elif "command(" in line:
                    m = re.search(r"command\([^)]+\)", line)
                    if m:
                        rules.append(m.group(0))
            if settings.allow_rules is None:
                settings.allow_rules = rules
    except Exception:
        pass

    return settings


def _collect_project_files(project_dir: Path) -> List[str]:
    """Collect non-ignored project file relative paths for checking."""
    collected: List[str] = []
    # If git repo, use git ls-files if possible
    git_dir = project_dir / ".git"
    if git_dir.is_dir() or git_dir.is_file():
        try:
            proc = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=project_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    if line.strip():
                        collected.append(line.strip())
                if collected:
                    return collected
        except Exception:
            pass

    # Fallback disk scan excluding standard noise
    skip_dirs = {".git", ".lintel", ".agents", "target", "node_modules", ".pytest_cache", "__pycache__"}
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for f in files:
            if f in (".DS_Store",) or f.endswith(".pyc"):
                continue
            full = Path(root) / f
            try:
                rel = full.relative_to(project_dir).as_posix()
                collected.append(rel)
            except ValueError:
                continue
    return collected


def run_agy_readers_selftest(
    fixtures_dir: Optional[Union[str, Path]] = None,
    project_dir: Optional[Union[str, Path]] = None,
) -> List[DoctorIssue]:
    """Verify agy state readers against curated fixture copy of M0-08 evidence files.

    Returns a list of DoctorIssue instances:
    - note: when all cases pass, or when fixtures are not present in installed run
    - error: when a case fails to match expected status/stop_cause, or manifest is corrupted/missing
    """
    fdir: Optional[Path] = None
    if fixtures_dir is not None:
        fdir = Path(fixtures_dir)
        if not fdir.is_dir():
            return [
                DoctorIssue(
                    "error",
                    "agy_readers",
                    f"Agy readers fixture directory not found: {fdir}",
                )
            ]
    else:
        candidates: List[Path] = []
        if project_dir is not None:
            candidates.append(Path(project_dir) / "tests" / "fixtures" / "evidence_m08")
        candidates.append(DEFAULT_PLUGIN_DIR.parent / "tests" / "fixtures" / "evidence_m08")
        candidates.append(Path.cwd() / "tests" / "fixtures" / "evidence_m08")
        for c in candidates:
            if c.is_dir():
                fdir = c
                break

    if fdir is None or not fdir.is_dir():
        return [
            DoctorIssue(
                "note",
                "agy_readers",
                "Evidence fixtures (evidence_m08) not present; skipped agy readers self-test.",
            )
        ]

    manifest_file = fdir / "manifest.json"
    if not manifest_file.is_file():
        return [
            DoctorIssue(
                "error",
                "agy_readers",
                f"Missing manifest.json in evidence fixture directory: {fdir}",
            )
        ]

    try:
        manifest_data = json.loads(manifest_file.read_text(encoding="utf-8", errors="replace"))
        cases = manifest_data.get("cases") or []
        if not cases:
            return [
                DoctorIssue(
                    "error",
                    "agy_readers",
                    f"No cases found in evidence fixture manifest: {manifest_file}",
                )
            ]
    except Exception as exc:
        return [
            DoctorIssue(
                "error",
                "agy_readers",
                f"Corrupted manifest.json in evidence fixture directory: {exc}",
            )
        ]

    from lintel.agy_readers import inspect_agy_state

    issues: List[DoctorIssue] = []
    passed_cases = 0

    for c in cases:
        name = c.get("name") or "unnamed"
        case_subdir = c.get("dir") or name
        case_dir = fdir / case_subdir
        exp_status = c.get("expected_status")
        exp_stop_cause = c.get("expected_stop_cause")

        case_manifest_file = case_dir / "manifest.json"
        if not case_manifest_file.is_file():
            issues.append(
                DoctorIssue(
                    "error",
                    "agy_readers",
                    f"Missing case manifest in fixture: {case_manifest_file}",
                )
            )
            continue

        try:
            case_manifest = json.loads(case_manifest_file.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            issues.append(
                DoctorIssue(
                    "error",
                    "agy_readers",
                    f"Corrupted manifest in fixture {case_manifest_file}: {exc}",
                )
            )
            continue

        role = case_manifest.get("role")
        nonce = case_manifest.get("nonce")

        # 1. Audit log inspection
        obs_audit = inspect_agy_state(
            audit_log_path=case_dir / "audit.jsonl",
            role=role,
            nonce=nonce,
            base_dir=case_dir,
        )
        if obs_audit.status != exp_status or obs_audit.stop_cause != exp_stop_cause:
            issues.append(
                DoctorIssue(
                    "error",
                    "agy_readers",
                    f"Agy reader selftest failed for case '{name}' (via audit log): "
                    f"expected status='{exp_status}', stop_cause='{exp_stop_cause}'; "
                    f"got status='{obs_audit.status}', stop_cause='{obs_audit.stop_cause}'",
                    details=obs_audit.details,
                )
            )
            continue

        # 2. Hints inspection
        obs_hints = inspect_agy_state(
            hints=case_dir / "hints.json",
            role=role,
            nonce=nonce,
            base_dir=case_dir,
        )
        if obs_hints.status != exp_status or obs_hints.stop_cause != exp_stop_cause:
            issues.append(
                DoctorIssue(
                    "error",
                    "agy_readers",
                    f"Agy reader selftest failed for case '{name}' (via hints): "
                    f"expected status='{exp_status}', stop_cause='{exp_stop_cause}'; "
                    f"got status='{obs_hints.status}', stop_cause='{obs_hints.stop_cause}'",
                    details=obs_hints.details,
                )
            )
            continue

        passed_cases += 1

    if not issues:
        case_names = [c.get("name", "") for c in cases]
        issues.append(
            DoctorIssue(
                "note",
                "agy_readers",
                f"Agy state readers self-test passed ({passed_cases} evidence cases verified: "
                f"{', '.join(case_names)}).",
            )
        )

    return issues



def run_shim_selftest(project_dir: Path, plugin_source: Optional[Path] = None) -> List[DoctorIssue]:
    issues: List[DoctorIssue] = []

    src = Path(plugin_source).resolve() if plugin_source is not None else DEFAULT_PLUGIN_DIR
    plugin_dir = project_dir / ".agents" / "plugins" / "lintel"
    if not (plugin_dir / "lintel" / "hook.sh").is_file():
        plugin_dir = src

    hooks_json_path = plugin_dir / "hooks.json"

    cmd_string = ""
    if hooks_json_path.is_file():
        try:
            cfg = json.loads(hooks_json_path.read_text(encoding="utf-8"))
            pre_tool = cfg.get("lintel", {}).get("PreToolUse", [])
            if pre_tool and pre_tool[0].get("hooks"):
                cmd_string = pre_tool[0]["hooks"][0].get("command", "")
        except Exception:
            pass

    if not cmd_string:
        issues.append(DoctorIssue("error", "hook", f"Hook check failed: hook did not fire for doctor's own call (could not extract command from {hooks_json_path})"))
        return issues

    payload = {
        "conversationId": "doctor-shim-selftest",
        "stepIdx": 1,
        "workspacePaths": [str(project_dir)],
        "toolCall": {
            "name": "run_command",
            "args": {
                "CommandLine": "lintel doctor"
            }
        }
    }

    audit_file = project_dir / ".lintel" / "state" / "audit.jsonl"
    initial_size = audit_file.stat().st_size if audit_file.is_file() else 0

    env = os.environ.copy()
    env["LINTEL_PROJECT_ROOT"] = str(project_dir)

    try:
        proc = subprocess.run(
            ["sh", "-c", cmd_string],
            input=json.dumps(payload).encode("utf-8"),
            capture_output=True,
            cwd=str(plugin_dir),
            env=env
        )
    except Exception as exc:
        issues.append(DoctorIssue("error", "hook", f"Shim self-test execution failed: {exc}"))
        return issues

    err_msg = ""
    if proc.returncode != 0:
        err_msg = f"Shim self-test failed: exit code {proc.returncode}"
    elif proc.stdout:
        err_msg = f"Shim self-test failed: stdout not empty ({proc.stdout.decode('utf-8', errors='replace').strip()})"
    else:
        final_size = audit_file.stat().st_size if audit_file.is_file() else 0
        if final_size <= initial_size:
            err_msg = f"Shim self-test failed: no audit line logged (out={proc.stdout!r}, err={proc.stderr!r}, code={proc.returncode})"

    if err_msg:
        hook_errors_log = project_dir / ".lintel" / "state" / "hook-errors.log"
        if hook_errors_log.is_file():
            try:
                lines = [l for l in hook_errors_log.read_text(encoding="utf-8").splitlines() if l.strip()]
                if lines:
                    err_msg += f". Latest log: {lines[-1]}"
            except Exception:
                pass
        issues.append(DoctorIssue("error", "hook", err_msg))
    else:
        issues.append(DoctorIssue("note", "hook", "Hook check passed: hook fired for doctor's own call"))

    return issues

def doctor(
    project_dir: Union[str, Path],
    role: Optional[str] = None,
    path_arg: Optional[str] = None,
    plugin_source: Optional[Union[str, Path]] = None,
    full: bool = False,
) -> DoctorReport:
    """Run diagnostic checks against project_dir and return DoctorReport."""
    proj = Path(project_dir).resolve()
    src = Path(plugin_source).resolve() if plugin_source is not None else DEFAULT_PLUGIN_DIR
    report = DoctorReport(project_dir=proj, full=full)

    # 1. Config validation
    config_file = proj / ".lintel" / "config.jsonc"
    loaded_config: Optional[Dict[str, Any]] = None

    if not config_file.is_file():
        report.issues.append(
            DoctorIssue("error", "config", f"Missing config file: {config_file}")
        )
    else:
        try:
            loaded_config = load_config(config_file, workspace_root=proj, plugin_dir=src)
        except ConfigError as exc:
            report.issues.append(
                DoctorIssue("error", "config", f"Config validation error: {exc}")
            )
        except Exception as exc:
            report.issues.append(
                DoctorIssue("error", "config", f"Config parsing failed: {exc}")
            )

    active_config = loaded_config or {}

    # Check for test group overlaps across project files
    repo_files = _collect_project_files(proj)
    if loaded_config:
        try:
            overlaps = detect_test_group_overlaps(repo_files, loaded_config, repo_root=proj)
            if overlaps:
                for opath, groups in overlaps.items():
                    report.issues.append(
                        DoctorIssue(
                            "error",
                            "config",
                            f"Test group overlap: '{opath}' matches multiple groups: {groups}",
                        )
                    )
        except Exception as exc:
            report.issues.append(
                DoctorIssue("error", "config", f"Test group overlap check failed: {exc}")
            )

        # Steering files existence check
        seen_steering: Set[Tuple[str, str]] = set()
        for st_role in ALL_10_ROLES:
            try:
                resolved_steering = resolve_role_steering_with_sources(active_config, st_role, workspace_root=proj, plugin_dir=src)
                for s_name, _ in resolved_steering:
                    if (st_role, s_name) not in seen_steering:
                        seen_steering.add((st_role, s_name))
                        rel_sf = s_name if s_name.startswith(".lintel/steering/") else f".lintel/steering/{s_name}"
                        target_sf = proj / rel_sf
                        if not target_sf.is_file():
                            report.issues.append(
                                DoctorIssue(
                                    "warning",
                                    "steering",
                                    f"Mapped steering file does not exist: {rel_sf} (role {st_role})",
                                )
                            )
            except Exception as exc:
                report.issues.append(
                    DoctorIssue("error", "steering", f"Failed to resolve steering for role '{st_role}': {exc}")
                )

    # 2. Path inspection if path_arg provided
    if path_arg:
        try:
            p_class = classify_path(path_arg, config=active_config, repo_root=proj)
            report.path_info = {
                "path": path_arg,
                "class": str(p_class.base_class),
                "group": p_class.group,
            }
        except Exception as exc:
            report.issues.append(
                DoctorIssue("error", "path", f"Path classification error for '{path_arg}': {exc}")
            )

    # 3. Role inspection if role provided
    if role:
        if role not in VALID_ROLES:
            report.issues.append(
                DoctorIssue("error", "role", f"Unknown role: '{role}' (valid roles: {', '.join(VALID_ROLES)})")
            )
        elif loaded_config:
            try:
                from lintel.skills import resolve_role_skills_with_sources
                r_skills = resolve_role_skills_with_sources(loaded_config, role, workspace_root=proj, plugin_dir=src)
                
                raw_steering = resolve_role_steering_with_sources(loaded_config, role, workspace_root=proj, plugin_dir=src)
                valid_steering = []
                for s, src_layer in raw_steering:
                    if (proj / ".lintel" / "steering" / s).is_file():
                        valid_steering.append((s, src_layer))
                
                report.role_info = {
                    "role": role,
                    "skills": r_skills,
                    "steering": valid_steering,
                }
            except Exception as exc:
                report.issues.append(
                    DoctorIssue("error", "role", f"Failed resolving role '{role}': {exc}")
                )

    # 4. Count files per test group and path class
    test_groups_cfg = active_config.get("paths", {}).get("tests", {})
    group_counts: Dict[str, int] = {g: 0 for g in test_groups_cfg} if test_groups_cfg and isinstance(test_groups_cfg, dict) else {}
    group_files: Dict[str, List[str]] = {g: [] for g in test_groups_cfg} if test_groups_cfg and isinstance(test_groups_cfg, dict) else {}
    class_counts: Dict[str, int] = {}
    class_files: Dict[str, List[str]] = {}

    for rf in repo_files:
        try:
            p_class = classify_path(rf, config=active_config, repo_root=proj)
            cname = str(p_class.base_class)
            class_counts[cname] = class_counts.get(cname, 0) + 1
            if cname not in class_files:
                class_files[cname] = []
            class_files[cname].append(rf)

            g = p_class.group
            if g:
                if g not in group_counts:
                    group_counts[g] = 0
                    group_files[g] = []
                group_counts[g] += 1
                group_files[g].append(rf)
        except Exception:
            pass
    report.group_counts = group_counts
    report.group_files = group_files
    report.class_counts = class_counts
    report.class_files = class_files

    # 5. Python interpreter check
    python_record_file = proj / ".lintel" / "state" / "python"
    interpreter_valid = False
    python_rewritten = False
    if python_record_file.is_file():
        rec_py = python_record_file.read_text(encoding="utf-8").strip()
        if rec_py and os.path.exists(rec_py) and os.access(rec_py, os.X_OK):
            rc, _ = run_bounded([rec_py, "-B", "-c", SNIPPET], timeout=5)
            if rc == 0:
                interpreter_valid = True

    if not interpreter_valid:
        # Resolve and record
        found = find_and_record_python(proj / ".lintel" / "state")
        python_rewritten = bool(found)
        if found:
            report.issues.append(
                DoctorIssue(
                    "warning",
                    "python",
                    f"Python interpreter was missing or broken; re-resolved and recorded to .lintel/state/python: {found}",
                )
            )
        else:
            report.issues.append(
                DoctorIssue(
                    "error",
                    "python",
                    "No valid Python >= 3.9 interpreter found. Please install Python via 'xcode-select --install' or 'brew install python'.",
                )
            )

    # 6. Git version check
    try:
        check_git_version()
    except GitError as exc:
        report.issues.append(DoctorIssue("error", "git", f"Git version check failed: {exc}"))
    except Exception as exc:
        report.issues.append(DoctorIssue("error", "git", f"Git check failed: {exc}"))

    # 7. Scripts check
    if test_groups_cfg and isinstance(test_groups_cfg, dict):
        for group in test_groups_cfg:
            if group == "support":
                continue
            script_path = proj / ".lintel" / "scripts" / f"test-{group}.sh"
            if not script_path.is_file():
                report.issues.append(
                    DoctorIssue(
                        "error",
                        "scripts",
                        f"Missing test script for group '{group}': .lintel/scripts/test-{group}.sh",
                    )
                )
            else:
                try:
                    content = script_path.read_text(encoding="utf-8")
                    if is_unadjusted_template(content):
                        report.issues.append(
                            DoctorIssue(
                                "warning",
                                "scripts",
                                f"Unadjusted test script template: .lintel/scripts/test-{group}.sh (not configured yet)",
                            )
                        )
                    hdr = parse_script_header(content)
                    if hdr and hdr.get("contract", 0) < 1:
                        report.issues.append(
                            DoctorIssue(
                                "warning",
                                "scripts",
                                f"Script .lintel/scripts/test-{group}.sh written for older contract version {hdr['contract']}",
                            )
                        )
                except Exception as exc:
                    report.issues.append(
                        DoctorIssue("error", "scripts", f"Could not read .lintel/scripts/test-{group}.sh: {exc}")
                    )

    # Check build.sh
    build_path = proj / ".lintel" / "scripts" / "build.sh"
    if build_path.is_file():
        try:
            content = build_path.read_text(encoding="utf-8")
            if is_unadjusted_template(content):
                report.issues.append(
                    DoctorIssue(
                        "warning",
                        "scripts",
                        "Unadjusted script template: .lintel/scripts/build.sh (not configured yet)",
                    )
                )
            hdr = parse_script_header(content)
            if hdr and hdr.get("contract", 0) < 1:
                report.issues.append(
                    DoctorIssue(
                        "warning",
                        "scripts",
                        f"Script .lintel/scripts/build.sh written for older contract version {hdr['contract']}",
                    )
                )
        except Exception:
            pass


    # Run shim self-test
    shim_issues = run_shim_selftest(proj, src)
    report.issues.extend(shim_issues)

    # 8. Suspect tests check
    try:
        suspect_files = find_suspect_tests(repo_files, config=active_config, repo_root=proj)
        if suspect_files:
            report.issues.append(
                DoctorIssue(
                    "warning",
                    "suspect_tests",
                    f"Suspect test files classified as 'code': {sorted(suspect_files)}",
                )
            )
    except Exception:
        pass

    # 9. Agent files check
    agent_tmpl_path = src / "templates" / "agents" / "persona.md"
    agent_tmpl = agent_tmpl_path.read_text(encoding="utf-8") if agent_tmpl_path.is_file() else ""

    for r in ALL_10_ROLES:
        agent_file = proj / ".agents" / "agents" / f"lintel-{r}.md"
        if not agent_file.is_file():
            report.issues.append(
                DoctorIssue("error", "agent_files", f"Missing agent file: .agents/agents/lintel-{r}.md")
            )
            continue

        try:
            content = agent_file.read_text(encoding="utf-8")
        except Exception as exc:
            report.issues.append(
                DoctorIssue("error", "agent_files", f"Could not read .agents/agents/lintel-{r}.md: {exc}")
            )
            continue

        expected = agent_tmpl.replace("<role>", r) if agent_tmpl else ""
        if expected and content != expected:
            report.issues.append(
                DoctorIssue(
                    "error",
                    "agent_files",
                    f"Agent file .agents/agents/lintel-{r}.md has been edited or differs from template",
                )
            )

        # Frontmatter parse
        fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if fm_match:
            fm_text = fm_match.group(1)
            for fkey in FORBIDDEN_FRONTMATTER_KEYS:
                if re.search(r"^\s*" + re.escape(fkey), fm_text, re.MULTILINE):
                    report.issues.append(
                        DoctorIssue(
                            "error",
                            "agent_files",
                            f"Forbidden frontmatter key '{fkey}' in .agents/agents/lintel-{r}.md",
                        )
                    )

            # Check tools list
            tools_match = re.search(r"^\s*tools:\s*\[(.*?)\]", fm_text, re.MULTILINE)
            if tools_match:
                raw_tools = [t.strip() for t in tools_match.group(1).split(",") if t.strip()]
                forbidden_tools = [t for t in raw_tools if t not in ACCEPTED_TOOLS]
                if forbidden_tools:
                    report.issues.append(
                        DoctorIssue(
                            "error",
                            "agent_files",
                            f"Forbidden tools in .agents/agents/lintel-{r}.md: {forbidden_tools}",
                        )
                    )

    # Hint for invoke failures
    report.issues.append(
        DoctorIssue(
            "note",
            "agent_files",
            "Hint: If 'invoke_subagent' fails with 'not found or not allowed', check the agent file frontmatter under .agents/agents/ (ensure no 'model:', 'commandExecutionPolicy:', 'inheritCustomizations:', or 'hooks:' keys).",
        )
    )

    # 10. Platform settings & sandbox & allow rules
    settings = inspect_platform_settings(proj)

    # Determine required allow rules for this project
    required_allow_rules = [
        "command(./.lintel/bin/lintel)",
        "command(./.lintel/scripts/build.sh)",
        "command(git status)",
        "command(git diff)",
        "command(git log)",
        "command(git show)",
    ]
    if (proj / "pom.xml").exists() or (proj / "mvnw").exists():
        required_allow_rules.append("command(./mvnw)")

    # Terminal sandbox check
    if settings.terminal_sandbox is True:
        report.issues.append(
            DoctorIssue(
                "error",
                "sandbox",
                "Terminal sandbox is ON (enableTerminalSandbox: true). Inside it .git is read-only and git/build commands fail.",
            )
        )
    elif settings.terminal_sandbox is None:
        report.issues.append(
            DoctorIssue(
                "note",
                "sandbox",
                "Platform setting note (terminal sandbox): terminal sandbox must be off (enableTerminalSandbox: false). Inside it .git is read-only.",
            )
        )

    # Allow rules check
    if settings.allow_rules is not None:
        for r_rule in required_allow_rules:
            if not any(r_rule in active_r for active_r in settings.allow_rules):
                report.issues.append(
                    DoctorIssue("warning", "permissions", f"Missing allow rule: {r_rule}")
                )
    else:
        report.issues.append(
            DoctorIssue(
                "note",
                "permissions",
                "Platform setting note (allow rules): ensure allow rules are configured: "
                + ", ".join(required_allow_rules),
            )
        )

    # Tool permission check
    if settings.tool_permission in ("request-review", "plan"):
        report.issues.append(
            DoctorIssue(
                "warning",
                "permissions",
                f"Tool permission mode is '{settings.tool_permission}'; accept-edits with allow rules is recommended.",
            )
        )
    elif settings.tool_permission is None:
        report.issues.append(
            DoctorIssue(
                "note",
                "permissions",
                "Platform setting note (permission mode): 'accept-edits' together with allow rules is recommended (not request-review or plan).",
            )
        )

    # agy version check (PLAN §11: 1.2.16 tested; doctor warns when another is installed)
    if settings.agy_version is not None:
        clean_ver = settings.agy_version.strip()
        if clean_ver.startswith("agy "):
            clean_ver = clean_ver[4:].strip()
        if not clean_ver.startswith("1.2.16"):
            report.issues.append(
                DoctorIssue(
                    "warning",
                    "platform",
                    f"agy version {settings.agy_version} installed; version 1.2.16 was tested.",
                )
            )
    else:
        report.issues.append(
            DoctorIssue(
                "note",
                "platform",
                "Platform setting note (agy version): agy version 1.2.16 tested.",
            )
        )

    # Workspace trust check (PLAN §11: the project folder trusted (trustedWorkspaces))
    if settings.trusted_workspaces is not None:
        proj_str = str(proj)
        proj_resolved = str(proj.resolve())
        trusted_normalized = {str(Path(w).resolve()) for w in settings.trusted_workspaces}
        if proj_str not in settings.trusted_workspaces and proj_resolved not in trusted_normalized:
            report.issues.append(
                DoctorIssue(
                    "warning",
                    "permissions",
                    f"Project folder {proj} is not in trustedWorkspaces; add it to trusted workspaces.",
                )
            )
    else:
        report.issues.append(
            DoctorIssue(
                "note",
                "permissions",
                f"Platform setting note (workspace trust): project folder should be added to trustedWorkspaces: {proj}",
            )
        )

    # macOS Aqua check
    is_aqua = is_macos_gui_session()
    if not is_aqua:
        report.issues.append(
            DoctorIssue(
                "warning",
                "gui",
                "No Aqua GUI session detected (launchctl managername != 'Aqua' or running under SSH). Confirmation dialog for human-only commands will fail closed.",
            )
        )

    report.issues.append(
        DoctorIssue(
            "note",
            "macos",
            "macOS permissions note: Do not grant Automation or Accessibility permissions to the app hosting the agent (Ghostty, Terminal.app, IDE).",
        )
    )

    # Hook errors log check
    hook_errors_log = proj / ".lintel" / "state" / "hook-errors.log"
    if hook_errors_log.is_file():
        try:
            err_lines = [l for l in hook_errors_log.read_text(encoding="utf-8").splitlines() if l.strip()]
            if err_lines:
                msg = f"hook-errors.log contains {len(err_lines)} error line(s)"
                if python_rewritten:
                    msg += " (stale interpreter lines were recorded until doctor rewrote it)"
                report.issues.append(
                    DoctorIssue(
                        "error",
                        "hook",
                        msg,
                    )
                )
        except Exception:
            pass

    # agy readers self-test over M0-08 evidence fixtures
    selftest_issues = run_agy_readers_selftest(project_dir=proj)
    report.issues.extend(selftest_issues)

    # M1c-02 Content warnings
    if active_config:
        import lintel.skills

        def _check_for_specific_test_files(filepath: Path, category: str):
            if not filepath.is_file():
                return
            try:
                content = filepath.read_text(encoding="utf-8")
            except Exception:
                return
            # Find word-like things containing a dot (to ensure it looks like a file, not just a dir)
            words = set(re.findall(r'[\w\./\-]+', content))
            for w in words:
                suffix = Path(w).suffix
                if not suffix or not suffix[1:].islower():
                    continue
                if w.endswith("/") or w.endswith(".") or w.startswith(".lintel/"):
                    continue
                try:
                    g = test_group_for_path(w, config=active_config, repo_root=proj)
                    if g:
                        report.issues.append(
                            DoctorIssue("warning", category, f"File {filepath} references specific test file '{w}' (test directory name is fine)")
                        )
                except Exception:
                    pass

        seen_skills = set()
        for r in ALL_10_ROLES:
            try:
                r_skills = lintel.skills.resolve_role_skills_with_sources(active_config, r, workspace_root=proj, plugin_dir=src)
                for sk_name, _ in r_skills:
                    sp = lintel.skills.resolve_skill_path(sk_name, workspace_root=proj, plugin_source=src)
                    if not sp or not sp.is_file():
                        report.issues.append(
                            DoctorIssue("warning", "skills", f"Missing assigned skill: '{sk_name}' (assigned to role {r})")
                        )
                    else:
                        if sp not in seen_skills:
                            seen_skills.add(sp)
                            _check_for_specific_test_files(sp, "skills")
            except Exception:
                pass
                
        steering_dir = proj / ".lintel" / "steering"
        if steering_dir.is_dir():
            for sf in steering_dir.rglob("*"):
                if sf.is_file():
                    _check_for_specific_test_files(sf, "steering")
                    
        specs_dir = proj / ".lintel" / "specs"
        if specs_dir.is_dir():
            for sp_f in specs_dir.rglob("*"):
                if sp_f.is_file():
                    _check_for_specific_test_files(sp_f, "specs")

    for r in ALL_10_ROLES:
        proj_persona = proj / ".lintel" / "personas" / f"{r}.md"
        plug_persona = src / "personas" / f"{r}.md"
        if proj_persona.is_file() and plug_persona.is_file():
            try:
                if proj_persona.stat().st_mtime < plug_persona.stat().st_mtime:
                    report.issues.append(
                        DoctorIssue("warning", "personas", f"Persona file .lintel/personas/{r}.md is older than plugin copy")
                    )
            except Exception:
                pass

    return report
