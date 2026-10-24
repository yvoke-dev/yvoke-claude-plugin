"""Document parsing and validation (M1a-14).

Parses and validates:
1. requirements.md:
   - Requirement sections: '### R<n>: <title>' (n is an integer >= 1)
   - Unique requirement IDs ('R1', 'R2', ...)
   - Marker lines right under heading:
     - 'Status: withdrawn — <reason>' (accepting em-dash '—', en-dash '–', or '-')
     - 'Behaviour: unchanged' (accepting 'Behavior: unchanged')
   - Text body follows marker lines
   - Functions:
     - validate_requirements_doc(content, known_ids=None) -> RequirementsDoc
     - all_non_withdrawn_unchanged(doc) -> bool
     - check_requirement_text_changes(old_doc, new_doc) -> Set[str]

2. plan.md:
   - Phase sections: '## Phase N: <title>' (N is an integer >= 1)
   - 'Requirements: R1, R3' line per phase
   - Functions:
     - validate_plan_doc(content, requirements_doc, state_data=None, previous_plan_doc=None, ...) -> PlanDoc
     - approve plan checks (PLAN §3.13, T-ARC-07, T-ARC-10 to 13)

3. phase-N/design.md:
   - Test outline section: '## Test outline'
   - Lines format:
     - '- R<n> <group>: <what>'
     - '- <group>: none: <reason>'
   - Functions:
     - validate_test_outline(content, phase_requirements, config_test_groups) -> TestOutline

4. phase-N/contract.md:
   - Sections: ## Signatures, ## External interfaces, ## UI hooks, ## Data, ## Stub files
   - Functions:
     - validate_contract_doc(content, repo_root, config) -> ContractDoc
"""

import sys
sys.dont_write_bytecode = True

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    from lintel.paths import classify_path
except ImportError:
    from .paths import classify_path


class DocumentValidationError(ValueError):
    """Raised when document parsing or validation fails."""
    pass


# ---------------------------------------------------------------------------
# 1. requirements.md
# ---------------------------------------------------------------------------

REQ_HEADING_RE = re.compile(r"^###\s+R(\d+)\s*:\s*(.*)$")
LEVEL_3_HEADING_RE = re.compile(r"^###(?:\s+(.*))?$")
WITHDRAWN_RE = re.compile(r"^Status:\s*withdrawn\s*(?:[—–-]|--)\s*(.+)$", re.IGNORECASE)
BEHAVIOUR_UNCHANGED_RE = re.compile(r"^Behaviou?r:\s*unchanged\s*$", re.IGNORECASE)


@dataclass
class Requirement:
    id: str
    number: int
    title: str
    is_withdrawn: bool = False
    withdrawn_reason: Optional[str] = None
    behaviour_unchanged: bool = False
    body: str = ""
    raw_section: str = ""


@dataclass
class RequirementsDoc:
    requirements: Dict[str, Requirement] = field(default_factory=dict)
    raw_content: str = ""

    @property
    def ids(self) -> Set[str]:
        return set(self.requirements.keys())

    def get(self, req_id: str) -> Optional[Requirement]:
        return self.requirements.get(req_id)

    def __contains__(self, req_id: str) -> bool:
        return req_id in self.requirements

    def __getitem__(self, req_id: str) -> Requirement:
        return self.requirements[req_id]

    def __iter__(self):
        return iter(self.requirements.values())

    def __len__(self) -> int:
        return len(self.requirements)


def validate_requirements_doc(
    content: str,
    known_ids: Optional[Set[str]] = None,
) -> RequirementsDoc:
    """Validate and parse a requirements.md document.

    Checks:
    - Requirement sections match '### R<n>: <title>' where <n> is an integer >= 1.
    - Unique requirement IDs ('R1', 'R2', ...).
    - Marker lines right under heading in any order:
      - 'Status: withdrawn — <reason>' (accepting em-dash or standard -)
      - 'Behaviour: unchanged'
      - Body follows marker lines.
    - Refuse missing known IDs (dropped requirements must keep their section with Status: withdrawn).
    - Refuse duplicate requirement IDs.
    - Refuse missing title or invalid section format.
    - Refuse 'Status: withdrawn' with no reason.
    """
    lines = content.splitlines()
    requirements: Dict[str, Requirement] = {}

    # Locate section headers and split content into sections
    section_starts: List[Tuple[int, int, str, str]] = []  # (line_idx, number, req_id, title)
    for idx, line in enumerate(lines):
        line_stripped = line.strip()
        if line_stripped.startswith("###"):
            lvl3_match = LEVEL_3_HEADING_RE.match(line_stripped)
            if not lvl3_match:
                continue
            m = REQ_HEADING_RE.match(line_stripped)
            if not m:
                raise DocumentValidationError(
                    f"Invalid requirement heading format: '{line_stripped}'; "
                    "expected '### R<n>: <title>'"
                )
            num_str = m.group(1)
            title = m.group(2).strip()
            num = int(num_str)
            if num < 1:
                raise DocumentValidationError(
                    f"Requirement number must be an integer >= 1, got R{num}"
                )
            if not title:
                raise DocumentValidationError(
                    f"Requirement R{num} missing title; expected '### R{num}: <title>'"
                )
            req_id = f"R{num}"
            section_starts.append((idx, num, req_id, title))

    if not section_starts:
        raise DocumentValidationError(
            "No requirement sections found in requirements document; "
            "expected '### R<n>: <title>'"
        )

    for i, (start_idx, num, req_id, title) in enumerate(section_starts):
        if req_id in requirements:
            raise DocumentValidationError(f"Duplicate requirement id: {req_id}")

        end_idx = section_starts[i + 1][0] if i + 1 < len(section_starts) else len(lines)
        section_lines = lines[start_idx:end_idx]
        raw_section = "\n".join(section_lines)

        # Parse markers directly below the heading
        is_withdrawn = False
        withdrawn_reason: Optional[str] = None
        behaviour_unchanged = False
        body_lines: List[str] = []

        # Lines following the heading
        content_lines = section_lines[1:]
        line_cursor = 0

        # Skip leading empty lines directly under heading
        while line_cursor < len(content_lines) and not content_lines[line_cursor].strip():
            line_cursor += 1

        # Read marker lines in any order until a non-marker non-empty line
        while line_cursor < len(content_lines):
            curr_line = content_lines[line_cursor].strip()
            if not curr_line:
                # Blank line within/after markers
                line_cursor += 1
                continue

            if curr_line.lower().startswith("status:"):
                wm = WITHDRAWN_RE.match(curr_line)
                if wm:
                    reason = wm.group(1).strip()
                    if not reason:
                        raise DocumentValidationError(
                            f"Requirement {req_id}: 'Status: withdrawn' requires a non-empty reason"
                        )
                    is_withdrawn = True
                    withdrawn_reason = reason
                    line_cursor += 1
                    continue
                else:
                    # Could be "Status: withdrawn" without dash or reason, or invalid status
                    if "withdrawn" in curr_line.lower():
                        raise DocumentValidationError(
                            f"Requirement {req_id}: 'Status: withdrawn' requires a non-empty reason"
                        )
                    raise DocumentValidationError(
                        f"Requirement {req_id}: invalid status marker '{curr_line}'; "
                        "expected 'Status: withdrawn — <reason>'"
                    )

            if BEHAVIOUR_UNCHANGED_RE.match(curr_line):
                behaviour_unchanged = True
                line_cursor += 1
                continue

            if curr_line.lower().startswith("behaviour:") or curr_line.lower().startswith("behavior:"):
                raise DocumentValidationError(
                    f"Requirement {req_id}: invalid behaviour marker '{curr_line}'; "
                    "expected 'Behaviour: unchanged'"
                )

            # Not a marker line: marker parsing ends, rest is body
            break

        body_lines = content_lines[line_cursor:]
        body_text = "\n".join(body_lines).strip()

        requirements[req_id] = Requirement(
            id=req_id,
            number=num,
            title=title,
            is_withdrawn=is_withdrawn,
            withdrawn_reason=withdrawn_reason,
            behaviour_unchanged=behaviour_unchanged,
            body=body_text,
            raw_section=raw_section,
        )

    # Check known IDs
    if known_ids:
        missing_ids = set(known_ids) - set(requirements.keys())
        if missing_ids:
            sorted_missing = sorted(missing_ids, key=lambda x: int(x[1:]) if x[1:].isdigit() else x)
            raise DocumentValidationError(
                f"Missing known requirement id(s): {', '.join(sorted_missing)}"
            )

    return RequirementsDoc(requirements=requirements, raw_content=content)


def all_non_withdrawn_unchanged(doc: RequirementsDoc) -> bool:
    """Return True if every requirement that isn't withdrawn has Behaviour: unchanged."""
    for req in doc.requirements.values():
        if not req.is_withdrawn:
            if not req.behaviour_unchanged:
                return False
    return True


def check_requirement_text_changes(
    old_doc: RequirementsDoc,
    new_doc: RequirementsDoc,
) -> Set[str]:
    """Return set of requirement IDs whose text (excluding marker lines) changed between revisions.

    Compares normalized title and body text for requirements present in both revisions.
    """
    changed: Set[str] = set()
    for req_id, new_req in new_doc.requirements.items():
        if req_id in old_doc.requirements:
            old_req = old_doc.requirements[req_id]
            old_text = (old_req.title.strip(), old_req.body.strip())
            new_text = (new_req.title.strip(), new_req.body.strip())
            if old_text != new_text:
                changed.add(req_id)
    return changed


# ---------------------------------------------------------------------------
# 2. plan.md
# ---------------------------------------------------------------------------

PHASE_HEADING_RE = re.compile(r"^##\s+Phase\s+(\d+)\s*:\s*(.*)$")
LEVEL_2_HEADING_RE = re.compile(r"^##\s+(?:Phase\s+.*)$", re.IGNORECASE)
REQUIREMENTS_LINE_RE = re.compile(r"^Requirements:\s*(.*)$", re.IGNORECASE)


@dataclass
class Phase:
    number: int
    title: str
    requirements: List[str]
    requirements_set: Set[str]
    body: str
    raw_section: str


@dataclass
class PlanDoc:
    phases: Dict[int, Phase] = field(default_factory=dict)
    raw_content: str = ""

    @property
    def phase_numbers(self) -> List[int]:
        return list(self.phases.keys())

    def get_phase(self, number: int) -> Optional[Phase]:
        return self.phases.get(number)

    def __contains__(self, number: int) -> bool:
        return number in self.phases

    def __getitem__(self, number: int) -> Phase:
        return self.phases[number]

    def __iter__(self):
        return iter(self.phases.values())

    def __len__(self) -> int:
        return len(self.phases)


def parse_plan_doc(content: str) -> PlanDoc:
    """Parse plan.md content into a PlanDoc object, checking syntax."""
    lines = content.splitlines()
    phases: Dict[int, Phase] = {}

    section_starts: List[Tuple[int, int, str]] = []  # (line_idx, phase_num, title)
    for idx, line in enumerate(lines):
        line_stripped = line.strip()
        if line_stripped.startswith("##"):
            # Check if this is a phase heading
            if re.match(r"^##\s+Phase\b", line_stripped, re.IGNORECASE):
                m = PHASE_HEADING_RE.match(line_stripped)
                if not m:
                    raise DocumentValidationError(
                        f"Invalid phase heading format: '{line_stripped}'; "
                        "expected '## Phase N: <title>'"
                    )
                num = int(m.group(1))
                title = m.group(2).strip()
                if num < 1:
                    raise DocumentValidationError(
                        f"Phase number must be an integer >= 1, got Phase {num}"
                    )
                if not title:
                    raise DocumentValidationError(
                        f"Phase {num} missing title; expected '## Phase {num}: <title>'"
                    )
                section_starts.append((idx, num, title))

    for i, (start_idx, num, title) in enumerate(section_starts):
        if num in phases:
            raise DocumentValidationError(f"Duplicate phase number: Phase {num}")

        end_idx = section_starts[i + 1][0] if i + 1 < len(section_starts) else len(lines)
        section_lines = lines[start_idx:end_idx]
        raw_section = "\n".join(section_lines)

        # Look for Requirements: line
        req_line_match = None
        for s_line in section_lines[1:]:
            rm = REQUIREMENTS_LINE_RE.match(s_line.strip())
            if rm:
                req_line_match = rm
                break

        if not req_line_match:
            raise DocumentValidationError(
                f"Phase {num} must contain a 'Requirements: R1, R3' line"
            )

        raw_reqs = req_line_match.group(1).strip()
        req_ids = [r.strip() for r in re.split(r"[\s,]+", raw_reqs) if r.strip()]
        if not req_ids:
            raise DocumentValidationError(
                f"Phase {num} has empty 'Requirements:' line"
            )

        content_lines = section_lines[1:]
        body_text = "\n".join(content_lines).strip()

        phases[num] = Phase(
            number=num,
            title=title,
            requirements=req_ids,
            requirements_set=set(req_ids),
            body=body_text,
            raw_section=raw_section,
        )

    return PlanDoc(phases=phases, raw_content=content)


def validate_plan_doc(
    content: str,
    requirements_doc: RequirementsDoc,
    state_data: Optional[Dict[str, Any]] = None,
    previous_plan_doc: Optional[PlanDoc] = None,
    changed_requirement_ids: Optional[Set[str]] = None,
    previous_requirements_doc: Optional[RequirementsDoc] = None,
) -> PlanDoc:
    """Validate and parse a plan.md document against requirements and state.

    Enforces the 'approve plan' checks (PLAN §3.13, T-ARC-07, T-ARC-10 to 13):
    1. Every requirement in requirements_doc must be in at least one phase,
       EXCEPT a withdrawn requirement that was never delivered.
    2. A withdrawn requirement that was delivered (in state_data["delivered_phases"]
       or previously delivered) MUST be in an undelivered phase (that removes the behaviour).
    3. Every requirement ID listed in Requirements: must exist in requirements_doc.
    4. Requirement whose text changed since last approved plan.md must be in an
       undelivered phase (changes to delivered behaviour become new phases, PLAN §3.6),
       except one withdrawn before it was delivered.
    5. Phase numbers are never reused: delivered phases keep theirs, removed undelivered
       phases leave a gap, new phases take next free number.
    6. Delivered phase sections must remain unchanged from previously approved plan.
    """
    plan_doc = parse_plan_doc(content)

    # 1. Resolve delivered phases
    delivered_phase_numbers: Set[int] = set()
    if state_data:
        dp_raw = state_data.get("delivered_phases", [])
        if isinstance(dp_raw, list):
            for x in dp_raw:
                if isinstance(x, int) and not isinstance(x, bool):
                    delivered_phase_numbers.add(x)

    # 2. Determine which requirements were delivered in any delivered phase
    delivered_req_ids: Set[str] = set()
    if previous_plan_doc:
        for p_num in delivered_phase_numbers:
            if p_num in previous_plan_doc.phases:
                delivered_req_ids.update(previous_plan_doc.phases[p_num].requirements)

    for p_num in delivered_phase_numbers:
        if p_num in plan_doc.phases:
            delivered_req_ids.update(plan_doc.phases[p_num].requirements)

    if state_data and isinstance(state_data.get("phases"), dict):
        for p_key, p_val in state_data["phases"].items():
            if str(p_key).isdigit() and int(p_key) in delivered_phase_numbers:
                if isinstance(p_val, dict) and "requirements" in p_val:
                    delivered_req_ids.update(p_val["requirements"])

    # 3. Categorize current plan phases into delivered vs undelivered
    undelivered_phases = [
        p for p_num, p in plan_doc.phases.items() if p_num not in delivered_phase_numbers
    ]
    undelivered_phase_req_ids: Set[str] = set()
    for p in undelivered_phases:
        undelivered_phase_req_ids.update(p.requirements)

    all_plan_req_ids: Set[str] = set()
    for p in plan_doc.phases.values():
        all_plan_req_ids.update(p.requirements)

    # Check 3: Every requirement ID listed in Requirements: must exist in requirements_doc
    for p_num, phase in plan_doc.phases.items():
        for req_id in phase.requirements:
            if req_id not in requirements_doc.requirements:
                raise DocumentValidationError(
                    f"Phase {p_num} lists requirement '{req_id}' which does not exist in requirements.md"
                )

    # Check 1: Every requirement in requirements_doc must be in at least one phase,
    # EXCEPT a withdrawn requirement that was never delivered.
    for req_id, req in requirements_doc.requirements.items():
        if req.is_withdrawn:
            if req_id in delivered_req_ids:
                # Withdrawn delivered requirement: must be in at least one phase (undelivered, checked in Check 2)
                if req_id not in all_plan_req_ids:
                    raise DocumentValidationError(
                        f"Delivered requirement '{req_id}' was withdrawn and must be in an "
                        "undelivered phase that removes its behaviour"
                    )
            else:
                # Withdrawn before delivered: exempt
                pass
        else:
            if req_id not in all_plan_req_ids:
                raise DocumentValidationError(
                    f"Requirement '{req_id}' is not in any phase in plan.md"
                )

    # Check 2: A withdrawn requirement that was delivered MUST be in an undelivered phase
    for req_id, req in requirements_doc.requirements.items():
        if req.is_withdrawn and req_id in delivered_req_ids:
            if req_id not in undelivered_phase_req_ids:
                raise DocumentValidationError(
                    f"Withdrawn delivered requirement '{req_id}' is not in an "
                    "undelivered phase that removes its behaviour"
                )

    # Check 4: Requirement whose text changed since last approved plan.md must be in an
    # undelivered phase, except one withdrawn before it was delivered.
    changed_ids = set()
    if changed_requirement_ids is not None:
        changed_ids = set(changed_requirement_ids)
    elif previous_requirements_doc is not None:
        changed_ids = check_requirement_text_changes(previous_requirements_doc, requirements_doc)
    elif state_data and "changed_requirement_ids" in state_data:
        changed_ids = set(state_data["changed_requirement_ids"])

    for req_id in changed_ids:
        if req_id in requirements_doc.requirements:
            req = requirements_doc.requirements[req_id]
            if req.is_withdrawn and req_id not in delivered_req_ids:
                # Withdrawn before delivered: exempt
                continue
            if req_id not in undelivered_phase_req_ids:
                raise DocumentValidationError(
                    f"Requirement '{req_id}' whose text changed must be in an undelivered phase"
                )

    # Check 5: Phase numbers are never reused: delivered phases keep theirs,
    # removed undelivered phases leave a gap, new phases take next free number.
    all_prev_numbers: Set[int] = set()
    prev_plan_numbers: Set[int] = set()
    if previous_plan_doc:
        prev_plan_numbers = set(previous_plan_doc.phases.keys())
        all_prev_numbers.update(prev_plan_numbers)
    if state_data and isinstance(state_data.get("phases"), dict):
        for k in state_data["phases"]:
            if str(k).isdigit():
                all_prev_numbers.add(int(k))

    # Delivered phases keep theirs
    for dp_num in delivered_phase_numbers:
        if dp_num not in plan_doc.phases:
            raise DocumentValidationError(
                f"Delivered Phase {dp_num} is missing from plan.md; delivered phases keep theirs"
            )

    if all_prev_numbers:
        max_prev = max(all_prev_numbers)
        # Any new phase not in previous_plan_doc must take next free number (> max_prev)
        for p_num in plan_doc.phases:
            is_new = (previous_plan_doc is not None and p_num not in prev_plan_numbers) or (
                p_num not in all_prev_numbers
            )
            if is_new:
                if p_num <= max_prev:
                    raise DocumentValidationError(
                        f"Phase number {p_num} reuses an older phase number; "
                        f"removed phases leave a gap and new phases take the next free number (> {max_prev})"
                    )

    # Check 6: Delivered phase sections must remain unchanged from previously approved plan
    if previous_plan_doc:
        for dp_num in delivered_phase_numbers:
            if dp_num in previous_plan_doc.phases and dp_num in plan_doc.phases:
                old_p = previous_plan_doc.phases[dp_num]
                new_p = plan_doc.phases[dp_num]
                if (
                    old_p.title.strip() != new_p.title.strip()
                    or old_p.requirements != new_p.requirements
                    or old_p.body.strip() != new_p.body.strip()
                ):
                    raise DocumentValidationError(
                        f"Delivered Phase {dp_num} section changed; "
                        "delivered phase sections must remain unchanged from previously approved plan"
                    )

    return plan_doc


# ---------------------------------------------------------------------------
# 3. phase-N/design.md (Test Outline)
# ---------------------------------------------------------------------------

TEST_OUTLINE_HEADING_RE = re.compile(
    r"^##\s+(?:\d+(?:\.\d+)*\.?\s+)?Test\s+outline\s*$", re.IGNORECASE
)
LEVEL_2_GENERIC_RE = re.compile(r"^##\s+.*$")
TEST_OUTLINE_TEST_LINE_RE = re.compile(r"^-\s+(R\d+)\s+([a-zA-Z0-9_-]+)\s*:\s*(.*)$")
TEST_OUTLINE_NONE_LINE_RE = re.compile(r"^-\s+([a-zA-Z0-9_-]+)\s*:\s*none\s*(?::\s*(.*))?$", re.IGNORECASE)


@dataclass
class TestOutlineEntry:
    group: str
    is_none: bool = False
    requirement_id: Optional[str] = None
    what: str = ""
    reason: Optional[str] = None
    raw_line: str = ""


@dataclass
class TestOutline:
    entries: List[TestOutlineEntry] = field(default_factory=list)
    listed_groups: Set[str] = field(default_factory=set)
    none_groups: Dict[str, str] = field(default_factory=dict)
    requirements_covered: Set[str] = field(default_factory=set)
    raw_content: str = ""


def validate_test_outline(
    content: str,
    phase_requirements: Set[str],
    config_test_groups: List[str],
) -> TestOutline:
    """Validate and parse ## Test outline section of phase-N/design.md.

    Checks (PLAN §3.13, T-ARC-14 to 19):
    - Every requirement in phase_requirements must have at least one test outline line.
    - Every required group (from config_test_groups) must have either at least one line
      or a '- <group>: none: <reason>'.
    - A group cannot have both test lines and a none line.
    - Groups must be known test groups from config; unknown group or 'support' is refused.
    - A 'none' line without a reason is refused ('- <group>: none' or empty reason).
    - An empty outline or no groups listed is refused.
    """
    lines = content.splitlines()

    outline_start = -1
    for idx, line in enumerate(lines):
        if TEST_OUTLINE_HEADING_RE.match(line.strip()):
            outline_start = idx
            break

    if outline_start == -1:
        raise DocumentValidationError("Missing '## Test outline' section in design document")

    # Extract lines until next ## heading or EOF
    outline_lines: List[str] = []
    for line in lines[outline_start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## ") and LEVEL_2_GENERIC_RE.match(stripped):
            break
        outline_lines.append(line)

    raw_outline = "\n".join(outline_lines)
    entries: List[TestOutlineEntry] = []

    for line in outline_lines:
        s_line = line.strip()
        if not s_line:
            continue
        if s_line.startswith("-"):
            # Check for '- <group>: none[: reason]'
            none_match = TEST_OUTLINE_NONE_LINE_RE.match(s_line)
            if none_match:
                group = none_match.group(1).strip()
                reason_raw = none_match.group(2)
                reason = reason_raw.strip() if reason_raw is not None else ""
                if not reason:
                    raise DocumentValidationError(
                        f"Test outline line '- {group}: none' requires a non-empty reason"
                    )
                entries.append(
                    TestOutlineEntry(
                        group=group,
                        is_none=True,
                        reason=reason,
                        raw_line=s_line,
                    )
                )
                continue

            # Check for '- R<n> <group>: <what>'
            test_match = TEST_OUTLINE_TEST_LINE_RE.match(s_line)
            if test_match:
                req_id = test_match.group(1).strip()
                group = test_match.group(2).strip()
                what = test_match.group(3).strip()
                if not what:
                    raise DocumentValidationError(
                        f"Test outline line for {req_id} {group} missing description: '{s_line}'"
                    )
                entries.append(
                    TestOutlineEntry(
                        group=group,
                        is_none=False,
                        requirement_id=req_id,
                        what=what,
                        raw_line=s_line,
                    )
                )
                continue

            # Malformed bullet line
            raise DocumentValidationError(
                f"Invalid test outline line format: '{s_line}'; "
                "expected '- R<n> <group>: <what>' or '- <group>: none: <reason>'"
            )

    if not entries:
        raise DocumentValidationError(
            "Test outline is empty; at least one test group must be listed"
        )

    config_groups_set = set(config_test_groups)
    groups_with_tests: Set[str] = set()
    groups_with_none: Dict[str, str] = {}
    requirements_covered: Set[str] = set()

    for e in entries:
        # Validate group name
        if e.group == "support":
            raise DocumentValidationError(
                "Test outline line names 'support' group, which is forbidden"
            )
        if e.group not in config_groups_set:
            raise DocumentValidationError(
                f"Test outline names unknown test group '{e.group}'; "
                f"known groups: {', '.join(sorted(config_test_groups))}"
            )

        if e.is_none:
            groups_with_none[e.group] = e.reason or ""
        else:
            groups_with_tests.add(e.group)
            if e.requirement_id:
                requirements_covered.add(e.requirement_id)
                # Requirement in outline must belong to phase
                if e.requirement_id not in phase_requirements:
                    raise DocumentValidationError(
                        f"Test outline names requirement '{e.requirement_id}' "
                        "which is not in this phase's Requirements line"
                    )

    # A group cannot have both test lines and a none line
    overlap = groups_with_tests & set(groups_with_none.keys())
    if overlap:
        sorted_overlap = sorted(overlap)
        raise DocumentValidationError(
            f"Test group(s) cannot have both test lines and a 'none' line: {', '.join(sorted_overlap)}"
        )

    # Every requirement in the phase's Requirements line must have at least one test outline line
    missing_reqs = phase_requirements - requirements_covered
    if missing_reqs:
        sorted_missing = sorted(missing_reqs, key=lambda x: int(x[1:]) if x[1:].isdigit() else x)
        raise DocumentValidationError(
            f"Requirement(s) in phase missing from test outline: {', '.join(sorted_missing)}"
        )

    # Every required group (from config_test_groups) must have either a line or a 'none'
    for g in config_test_groups:
        if g not in groups_with_tests and g not in groups_with_none:
            raise DocumentValidationError(
                f"Required test group '{g}' has neither a test outline line nor a 'none' line"
            )

    return TestOutline(
        entries=entries,
        listed_groups=groups_with_tests,
        none_groups=groups_with_none,
        requirements_covered=requirements_covered,
        raw_content=raw_outline,
    )


# ---------------------------------------------------------------------------
# 4. phase-N/contract.md
# ---------------------------------------------------------------------------

STUB_FILES_HEADING_RE = re.compile(
    r"^##\s+(?:\d+(?:\.\d+)*\.?\s+)?Stub\s+files\s*$", re.IGNORECASE
)


@dataclass
class ContractDoc:
    stub_files: List[str] = field(default_factory=list)
    sections: Dict[str, str] = field(default_factory=dict)
    raw_content: str = ""


def validate_contract_doc(
    content: str,
    repo_root: Union[str, Path],
    config: Dict[str, Any],
) -> ContractDoc:
    """Validate and parse a phase-N/contract.md document.

    Checks (PLAN §3.13, T-ARC-20):
    - Parses bullets under '## Stub files': '- <path>'.
    - Each stub file must exist on disk in the repo.
    - Each stub file must classify as class 'code' (using classify_path).
    - Refuse if file does not exist or if its class is not 'code'.
    """
    root_path = Path(repo_root).resolve()
    lines = content.splitlines()

    # Parse sections
    sections: Dict[str, str] = {}
    current_section: Optional[str] = None
    current_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_section is not None:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = stripped[3:].strip()
            current_lines = []
        else:
            if current_section is not None:
                current_lines.append(line)

    if current_section is not None:
        sections[current_section] = "\n".join(current_lines).strip()

    # Locate ## Stub files section
    stub_section_text: Optional[str] = None
    for s_name, s_content in sections.items():
        if re.match(r"^(?:\d+(?:\.\d+)*\.?\s+)?Stub\s+files$", s_name, re.IGNORECASE):
            stub_section_text = s_content
            break

    if stub_section_text is None:
        raise DocumentValidationError("Missing '## Stub files' section in contract document")

    stub_files: List[str] = []
    for line in stub_section_text.splitlines():
        s_line = line.strip()
        if s_line.startswith("-"):
            raw_path = s_line[1:].strip()
            # Remove any wrapping markdown backticks or quotes
            clean_path = raw_path.strip("`'\" \t")
            if clean_path:
                stub_files.append(clean_path)

    for stub_path in stub_files:
        full_path = (root_path / stub_path).resolve()
        if not full_path.exists():
            raise DocumentValidationError(
                f"Stub file does not exist on disk: '{stub_path}'"
            )

        p_class = classify_path(stub_path, config=config, repo_root=root_path)
        base_class = getattr(p_class, "base_class", str(p_class))
        if base_class != "code":
            raise DocumentValidationError(
                f"Stub file '{stub_path}' must be in class 'code', but classified as '{p_class}'"
            )

    return ContractDoc(
        stub_files=stub_files,
        sections=sections,
        raw_content=content,
    )
