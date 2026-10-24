import json
"""Persona file parsing and handling."""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Optional, Union

HEADER_PATTERN = re.compile(
    r"^(?:<!--\s*)?lintel-persona:\s*([a-z0-9_-]+)\s+version:\s*(\d+)(?:\s*-->)?$",
    re.IGNORECASE,
)

VALID_ROLES = (
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


@dataclass
class Persona:
    role: str
    version: int
    text: str
    path: Optional[Path] = None


def parse_persona(source: Union[str, Path]) -> Persona:
    """Parse a persona markdown file.

    The file begins with a header:
        <!-- lintel-persona: <role>  version: <N> -->
    followed by the role description text.
    """
    path = None
    if isinstance(source, Path) and source.is_file():
        path = source
        content = path.read_text(encoding="utf-8")
    elif isinstance(source, str) and "\n" not in source and source.strip() and Path(source).is_file():
        path = Path(source)
        content = path.read_text(encoding="utf-8")
    else:
        content = str(source)

    lines = content.splitlines()
    if not lines:
        raise ValueError("Empty persona file")

    first_line = lines[0].strip()
    match = HEADER_PATTERN.match(first_line)
    if not match:
        raise ValueError(f"Invalid persona header: {first_line!r}")

    role = match.group(1).lower()
    version = int(match.group(2))

    if role not in VALID_ROLES:
        raise ValueError(f"Unknown persona role: {role!r}")

    body = "\n".join(lines[1:]).strip()
    if not body:
        raise ValueError(f"Missing role text for persona: {role}")

    return Persona(role=role, version=version, text=body, path=path)
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lintel.personas import parse_persona

from lintel.state import load_state_file

def render_brief(
    role: str,
    task_id: str,
    stage: str,
    run_id: str,
    repo_root: Path,
    plugin_dir: Path,
    config: Dict[str, Any],
    copied_report_content: Optional[str] = None,
    amends_list: Optional[List[Dict[str, Any]]] = None,
    current_phase_num: Optional[int] = None,
    task_data: Optional[Dict[str, Any]] = None,
    open_findings: Optional[List[Dict[str, Any]]] = None,
    is_final_reject_revisit: bool = False,
    is_test_side: bool = False,
    from_report_record: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[str], bool]:
    """Render brief.md text and return (markdown_text, assigned_skill_names, is_test_side)."""

    # 1. Persona text
    persona_file = repo_root / ".lintel" / "personas" / f"{role}.md"
    if not persona_file.is_file():
        persona_file = plugin_dir / "personas" / f"{role}.md"
    
    persona = parse_persona(persona_file)

    lines = [
        f"# Brief for {role}",
        "",
        persona.text,
        "",
    ]
    
    if role in ("qa", "qa-reviewer"):
        is_test_side = True
    if from_report_record:
        fr_run = str(from_report_record.get("run", "")).lower()
        if fr_run.startswith("qa-") or fr_run == "qa":
            is_test_side = True

    def _check_leakage(val: str) -> None:
        if is_test_side or not val:
            return
        v_low = val.lower()
        if "tests/" in v_low or "/tests" in v_low or "qa-section" in v_low or "qa section" in v_low:
            raise ValueError(f"Attempted to leak test path or QA section into non-test-side brief for role '{role}'")

    # Resolve temp dir for scratch
    tmp_dir = "/tmp"
    if task_data:
        tmp_dir = task_data.get("tmp", "/tmp")
    scratch_dir = f"{tmp_dir}/lintel/{task_id}/{role}/"

    # 2. The lane
    lines.append("## Your Lane")
    lines.append(f"- **Scratch space:** `{scratch_dir}` (outside the repo). Use this for any temporary files.")
    lines.append("- **Do not commit:** Never run git commit or modify git history.")
    lines.append("- **Notice:** Every tool call you make is logged, and any access outside your lane will be reported as a crossing for human review.")
    
    if role in ("po", "po-reviewer"):
        lines.append("- **Do not read:** `tests`, `test-output`, test-side task dirs, or other conversations' transcripts (they contain tests and your role must not see them).")
        if stage == "docs":
            lines.append("- **Where to write:** `.lintel/specs/**` only, plus your run folder.")
        else:
            lines.append("- **Where to write:** `shared/requirements.md`, plus your run folder.")
    elif role in ("architect", "architect-reviewer"):
        lines.append("- **Do not read:** `tests`, `test-output`, test-side task dirs, or other conversations' transcripts.")
        if stage == "docs":
            lines.append("- **Where to write:** `.lintel/steering/**` (except `testing.md`), plus your run folder.")
        else:
            lines.append("- **Where to write:** `shared/plan.md`, `shared/phase-<N>/` (including stubs), plus your run folder.")
    elif role in ("qa", "qa-reviewer"):
        lines.append("- **Do not read:** Any code changed since the phase lock (until delivery or reset) or other conversations' transcripts.")
        lines.append("- **Where to write:** `tests` (and build files for test dependencies before the lock), plus your run folder.")
    elif role in ("dev", "dev-reviewer"):
        lines.append("- **Do not read:** `tests`, `test-output`, test-side task dirs, or other conversations' transcripts (they contain tests and your role must not see them).")
        lines.append("- **Where to write:** `code`, plus your run folder.")
    elif role == "arbiter":
        lines.append("- **Do not read:** Whatever the pair you are ruling on should not read. Never read tests and code together. Do not read other conversations' transcripts.")
        lines.append("- **Where to write:** Only your run folder.")
    elif role == "final-reviewer":
        lines.append("- **Do not read:** Other conversations' transcripts.")
        lines.append("- **Where to write:** Only your run folder.")

    if role in ("architect", "architect-reviewer", "qa", "qa-reviewer"):
        lines.append("")
        lines.append("### Test Groups")
        paths = config.get("paths", {})
        tests = paths.get("tests", {})
        for gname, gval in tests.items():
            if isinstance(gval, dict):
                req = gval.get("required", False)
                pats = gval.get("paths", [])
                lines.append(f"- **{gname}** (required: {req}): patterns {pats}")
                
    if role in ("qa", "qa-reviewer") and current_phase_num is not None:
        design_file = repo_root / f"shared/p{current_phase_num}/design.md"
        if not design_file.is_file():
            design_file = repo_root / f"shared/phase-{current_phase_num}/design.md"
        if not design_file.is_file() and task_id:
            design_file = repo_root / f".lintel/tasks/{task_id}/shared/phase-{current_phase_num}/design.md"
        if design_file.is_file():
            design_text = design_file.read_text(encoding="utf-8")
            outline_match = re.search(r"^##\s+(?:\d+(?:\.\d+)*\.?\s+)?Test\s+outline(.*?)(?=^#|\Z)", design_text, flags=re.MULTILINE | re.DOTALL | re.IGNORECASE)
            if outline_match:
                lines.append("")
                lines.append("### Test Outline")
                lines.append(outline_match.group(1).strip())

    # 3. How to finish and commands
    lines.append("")
    lines.append("## How to Run Commands")
    lines.append("- **Cwd:** The `run_command` tool must ALWAYS be called with `Cwd` = the repo root (no default).")
    lines.append("- **Launcher:** Call the lintel CLI exactly as `./.lintel/bin/lintel` (bare, not with sh or python). Run other project scripts bare too.")
    lines.append("- **Read files:** Use `view_file` and `grep_search` instead of `cat` and `grep`.")
    lines.append("- **Background tasks:** Commands running longer than about 10s are moved to the background; you will be woken when they end.")
    lines.append("- **Denials:** A refused command or denied tool must be recorded in your report; never work around it.")
    lines.append("- **Tools:** Ignore global skills and MCP tools unless explicitly assigned below.")

    lines.append("")
    lines.append("## How to Finish")
    lines.append("- Run `./.lintel/bin/lintel done --verdict <verdict>` (e.g. complete, blocked, needs-input).")
    lines.append("- Then, send ONE `send_message` to your caller.")

    # 4. Inputs
    lines.append("")
    lines.append("## Allowed Inputs")
    lines.append("- Request: shared/request.md")
    lines.append("- Requirements: shared/requirements.md")
    lines.append("- Plan: shared/plan.md")
    if stage.startswith("p") or stage.startswith("r"):
        lines.append(f"- Design: shared/{stage}/design.md")
        lines.append(f"- Contract: shared/{stage}/contract.md")
    
    ans_file = repo_root / "shared/answers.md"
    if ans_file.is_file():
        lines.append("- Answers: shared/answers.md")
        
    if role == "dev" and task_data:
        lines.append(f"- Base SHA: {task_data.get('head')} (do not look at the lock commit, which holds tests)")
        
    if is_final_reject_revisit and role == "po":
        lines.append("- Final findings: shared/final-findings.md")
        
    if copied_report_content:
        _check_leakage(copied_report_content)
        lines.append("")
        lines.append("### Copied Report Section")
        lines.append(copied_report_content)

    if is_final_reject_revisit and role == "po":
        ff_path = repo_root / "shared/final-findings.md"
        if ff_path.is_file():
            ff_content = ff_path.read_text(encoding="utf-8").strip()
            _check_leakage(ff_content)
            lines.append("")
            lines.append("### Final Findings")
            lines.append(ff_content)
            
    if amends_list and role == "dev":
        last_amend = amends_list[-1]
        if last_amend.get("kind") == "tests-stand":
            lines.append("")
            lines.append("### Amend Notice")
            lines.append("The tests stand.")
        if last_amend.get("cause") == "contract":
            arch_run = last_amend.get("architect_run")
            if arch_run:
                try:
                    ard = json.loads((repo_root / ".lintel" / "state" / task_id / "runs" / f"{arch_run}.json").read_text(encoding="utf-8"))
                    arch_rep_file = repo_root / ard.get("folder", "") / "report.md"
                    if arch_rep_file.is_file():
                        arch_rep_text = arch_rep_file.read_text(encoding="utf-8").strip()
                        if arch_rep_text:
                            _check_leakage(arch_rep_text)
                            lines.append("")
                            lines.append("### Architect Report")
                            lines.append(arch_rep_text)
                except Exception as e:
                    print(f"EXCEPTION: {e}")
                    pass

    if open_findings:
        lines.append("")
        lines.append("### Open Findings")
        for f in open_findings:
            _check_leakage(f.get('summary', ''))
            _check_leakage(f.get('file', ''))
            author_tag = f" [{f.get('author')}]" if f.get("author") == "human" else ""
            lines.append(f"- **{f.get('id')}{author_tag}** ({f.get('severity')}): {f.get('summary')}")
            if f.get("file"):
                lines.append(f"  File: {f.get('file')}")
                
    if role == "arbiter" and task_data:
        pair_writer = (task_data.get("escalation") or {}).get("writer")
        if pair_writer:
            lines.append("")
            lines.append(f"### Lane Details for pair ({pair_writer} & {pair_writer}-reviewer)")
            if pair_writer == "po":
                lines.append("Where they write: `shared/requirements.md` (or `.lintel/specs/`), no tests.")
            elif pair_writer == "architect":
                lines.append("Where they write: `shared/plan.md`, `shared/<phase>/`, no tests.")
            elif pair_writer == "qa":
                lines.append("Where they write: `tests`.")
            elif pair_writer == "dev":
                lines.append("Where they write: `code`, no tests.")
                
            try:
                state_dir = repo_root / ".lintel" / "state"
                loops_file = state_dir / task_id / "loops.json"
                if loops_file.is_file():
                    ld = json.loads(loops_file.read_text(encoding="utf-8"))
                    loop_key = f"{pair_writer}.{stage}"
                    lp = ld.get("loops", {}).get(loop_key, {})
                    lines.append("")
                    lines.append("### Findings History")
                    lines.append(json.dumps(lp.get("findings", []), indent=2))
                
                counters = task_data.get("counters", {}).get("run", {})
                w_seq = counters.get(pair_writer, {}).get(stage)
                r_seq = counters.get(f"{pair_writer}-reviewer", {}).get(stage)
                
                if w_seq:
                    w_run_id = f"{pair_writer}-{stage}-{w_seq}"
                    w_run_file = state_dir / "runs" / f"{w_run_id}.json"
                    if w_run_file.is_file():
                        w_rd = json.loads(w_run_file.read_text(encoding="utf-8"))
                        w_rep = repo_root / w_rd.get("folder", "") / "report.md"
                        if w_rep.is_file():
                            lines.append("")
                            lines.append(f"### Writer Report ({pair_writer})")
                            lines.append(w_rep.read_text(encoding="utf-8").strip())
                            
                if r_seq:
                    r_run_id = f"{pair_writer}-reviewer-{stage}-{r_seq}"
                    r_run_file = state_dir / "runs" / f"{r_run_id}.json"
                    if r_run_file.is_file():
                        r_rd = json.loads(r_run_file.read_text(encoding="utf-8"))
                        r_rep = repo_root / r_rd.get("folder", "") / "report.md"
                        if r_rep.is_file():
                            lines.append("")
                            lines.append(f"### Reviewer Report ({pair_writer}-reviewer)")
                            lines.append(r_rep.read_text(encoding="utf-8").strip())
            except Exception as e:
                pass


    # 5. Assigned skills and steering
    from lintel.skills import resolve_role_skills_with_sources, resolve_role_steering_with_sources, resolve_skill_path
    assigned_skills_with_sources = resolve_role_skills_with_sources(config, role, None, repo_root, plugin_dir)
    assigned_steering_with_sources = resolve_role_steering_with_sources(config, role, None, repo_root, plugin_dir)
    
    assigned_skill_names = []
    
    lines.append("")
    lines.append("## Assigned Skills and Steering Files")
    lines.append("READ THEM ALL BEFORE STARTING.")
    lines.append("Conflict rule: On any conflict, steering and project skills win over lintel: skills, EXCEPT over the lane. The lane ALWAYS wins.")
    
    if assigned_skills_with_sources:
        lines.append("")
        lines.append("### Skills")
        for sname, src in assigned_skills_with_sources:
            spath = resolve_skill_path(sname, repo_root, plugin_dir)
            if spath:
                desc = ""
                if Path(spath).is_file():
                    sk_text = Path(spath).read_text(encoding="utf-8")
                    # Try frontmatter
                    m_desc = re.search(r"^description:\s*(.*?)$", sk_text, flags=re.MULTILINE | re.IGNORECASE)
                    if m_desc:
                        desc = m_desc.group(1).strip()
                    else:
                        # Try first paragraph after # <name>
                        # Try first paragraph after # <name>
                        m_para = re.search(r"^#\s+.*?\n\n+([^#].*?)(?:\n\n|\Z)", sk_text, flags=re.MULTILINE | re.DOTALL)
                        if m_para:
                            desc = m_para.group(1).strip().replace("\n", " ")
                
                if desc:
                    lines.append(f"- {sname} ({spath}): {desc}")
                else:
                    lines.append(f"- **{sname}**: `{spath}`")
                assigned_skill_names.append(sname)
                
    if assigned_steering_with_sources:
        lines.append("")
        lines.append("### Steering")
        for sname, src in assigned_steering_with_sources:
            if sname.startswith(".lintel/steering/"):
                lines.append(f"- `{repo_root / sname}`")
                
    return "\n".join(lines) + "\n", assigned_skill_names, is_test_side
