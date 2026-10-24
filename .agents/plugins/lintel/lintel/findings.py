"""Findings and responses parsing and validation (M1a-14).

Parses and validates:
1. findings.json:
   - List of findings objects:
     - 'id': str
     - 'severity': 'blocker' | 'major' | 'minor'
     - 'status': 'open' | 'fixed' | 'withdrawn'
     - 'where': str
     - 'summary': str
     - optional 'author': str
   - Stable IDs carried forward from previous reviewer round.
   - Human findings rule (PLAN §3.9, §3.5, T-VER-22):
     A finding authored by human ('author == "human"') can NEVER be marked
     'withdrawn' by a reviewer (may be 'fixed' or 'open', never 'withdrawn').

2. responses.json:
   - Maps finding 'id' to response object:
     - 'status': 'fixed' | 'disputed'
     - 'reason': str (required for 'disputed', optional for 'fixed')
   - Every open finding must have a response.
   - Disputed finding requires a non-empty reason.
   - Arbiter ruling check (PLAN §3.9, T-VER-23):
     If arbiter ruled 'fix' on a finding, 'disputed' response is refused.
"""

import sys
sys.dont_write_bytecode = True

import json
from typing import Any, Dict, List, Optional, Set, Union


class FindingsValidationError(ValueError):
    """Raised when findings.json or responses.json validation fails."""
    pass


VALID_SEVERITIES: Set[str] = {"blocker", "major", "minor"}
VALID_STATUSES: Set[str] = {"open", "fixed", "withdrawn"}
VALID_RESPONSE_STATUSES: Set[str] = {"fixed", "disputed"}


def validate_findings(
    findings_data: Any,
    previous_findings: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Validate findings data (from findings.json).

    Checks:
    - Valid JSON list of finding objects.
    - Required fields: 'id', 'severity', 'status', 'where', 'summary'.
    - Valid enum values for 'severity' and 'status'.
    - Unique finding IDs in current document.
    - Stable IDs carried forward: every finding from previous_findings must be present.
    - Human findings rule: a human finding (author == 'human') can NEVER be marked
      'withdrawn' by a reviewer (may be 'fixed' or 'open').
    """
    if isinstance(findings_data, str):
        try:
            findings_list = json.loads(findings_data)
        except Exception as e:
            raise FindingsValidationError(f"findings.json is not valid JSON: {e}")
    elif isinstance(findings_data, list):
        findings_list = findings_data
    else:
        raise FindingsValidationError("findings data must be a JSON list")

    if not isinstance(findings_list, list):
        raise FindingsValidationError("findings data must be a list of finding objects")

    seen_ids: Set[str] = set()
    prev_map: Dict[str, Dict[str, Any]] = {}
    if previous_findings:
        for pf in previous_findings:
            if isinstance(pf, dict) and "id" in pf:
                prev_map[pf["id"]] = pf

    required_fields = ["id", "severity", "status", "where", "summary"]

    for idx, item in enumerate(findings_list):
        if not isinstance(item, dict):
            raise FindingsValidationError(f"Finding at index {idx} must be a JSON object")

        for f_name in required_fields:
            if f_name not in item:
                raise FindingsValidationError(
                    f"Finding at index {idx} missing required field '{f_name}'"
                )
            val = item[f_name]
            if not isinstance(val, str) or not val.strip():
                raise FindingsValidationError(
                    f"Finding at index {idx} field '{f_name}' must be a non-empty string"
                )

        fid = item["id"]
        if fid in seen_ids:
            raise FindingsValidationError(f"Duplicate finding id: '{fid}'")
        seen_ids.add(fid)

        sev = item["severity"]
        if sev not in VALID_SEVERITIES:
            raise FindingsValidationError(
                f"Finding '{fid}' has invalid severity '{sev}'; "
                f"must be one of: {', '.join(sorted(VALID_SEVERITIES))}"
            )

        status = item["status"]
        if status not in VALID_STATUSES:
            raise FindingsValidationError(
                f"Finding '{fid}' has invalid status '{status}'; "
                f"must be one of: {', '.join(sorted(VALID_STATUSES))}"
            )

        # Human finding rule: human findings can never be marked withdrawn
        is_human = item.get("author") == "human"
        if not is_human and fid in prev_map:
            if prev_map[fid].get("author") == "human":
                is_human = True

        if is_human and status == "withdrawn":
            raise FindingsValidationError(
                f"Human finding '{fid}' cannot be marked withdrawn by a reviewer; "
                "reviewer may mark it fixed or keep it open, but never withdrawn"
            )

        if fid in prev_map:
            arb_rule = prev_map[fid].get("arbiter_ruled")
            if arb_rule == "withdraw" and status != "withdrawn":
                raise FindingsValidationError(f"Finding '{fid}' was ruled 'withdraw' by the arbiter and cannot be reopened (T-VER-23)")
            if arb_rule == "fix" and status == "withdrawn":
                raise FindingsValidationError(f"Finding '{fid}' was ruled 'fix' by the arbiter and cannot be withdrawn (T-VER-23)")

    # Stable IDs check: all previous findings must be carried forward
    if prev_map:
        missing_ids = set(prev_map.keys()) - seen_ids
        for mid in missing_ids:
            if prev_map[mid].get("arbiter_ruled") == "fix":
                raise FindingsValidationError(f"Finding '{mid}' was ruled 'fix' by the arbiter and cannot be dropped (T-VER-23)")
                
        if missing_ids:
            sorted_missing = sorted(missing_ids)
            raise FindingsValidationError(
                f"Earlier finding(s) not carried forward: {', '.join(sorted_missing)}; "
                "reviewer must carry forward all earlier findings with stable ids"
            )

    return findings_list


def validate_responses(
    responses_data: Any,
    open_findings: List[Dict[str, Any]],
    arbiter_rulings: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Validate responses data (from responses.json).

    Answers open findings: maps finding 'id' to response {status: fixed|disputed, reason: ...}.
    Supports both a dict of id -> response and a list of response objects with 'id'.

    Checks:
    - Valid JSON object or list.
    - Every open finding must have a response.
    - Response status must be 'fixed' or 'disputed'.
    - 'disputed' response must include a non-empty 'reason'.
    - Arbiter ruling check: if arbiter ruled 'fix' on a finding, 'disputed' is refused.
    """
    if isinstance(responses_data, str):
        try:
            raw = json.loads(responses_data)
        except Exception as e:
            raise FindingsValidationError(f"responses.json is not valid JSON: {e}")
    else:
        raw = responses_data

    normalized_responses: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw, dict):
        normalized_responses = raw
    elif isinstance(raw, list):
        for idx, item in enumerate(raw):
            if not isinstance(item, dict):
                raise FindingsValidationError(f"Response item at index {idx} must be an object")
            if "id" not in item:
                raise FindingsValidationError(f"Response item at index {idx} missing 'id' field")
            normalized_responses[item["id"]] = item
    else:
        raise FindingsValidationError("responses data must be a JSON object or list")

    # Determine which findings require responses (status == 'open' or unspecified)
    open_ids: Set[str] = set()
    for f in open_findings:
        if isinstance(f, dict):
            status = f.get("status")
            if status is None or status == "open":
                if "id" in f:
                    open_ids.add(f["id"])

    # Every open finding must have a response
    missing_responses = open_ids - set(normalized_responses.keys())
    if missing_responses:
        sorted_missing = sorted(missing_responses)
        raise FindingsValidationError(
            f"Missing response for open finding(s): {', '.join(sorted_missing)}"
        )

    # Validate each response
    for fid, resp in normalized_responses.items():
        if not isinstance(resp, dict):
            raise FindingsValidationError(f"Response for finding '{fid}' must be an object")

        status = resp.get("status")
        if status not in VALID_RESPONSE_STATUSES:
            raise FindingsValidationError(
                f"Response for finding '{fid}' has invalid status '{status}'; "
                f"must be one of: {', '.join(sorted(VALID_RESPONSE_STATUSES))}"
            )

        if status == "disputed":
            reason = resp.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise FindingsValidationError(
                    f"Disputed response for finding '{fid}' must include a non-empty 'reason'"
                )

        # Check arbiter rulings
        if arbiter_rulings and arbiter_rulings.get(fid) == "fix":
            if status == "disputed":
                raise FindingsValidationError(
                    f"Finding '{fid}' was ruled 'fix' by the arbiter; 'disputed' is refused"
                )

    return normalized_responses

def validate_ruling(
    ruling_data: Any,
    open_findings: List[Dict[str, Any]],
) -> Dict[str, Dict[str, str]]:
    """Validate ruling.json from arbiter (T-STL-07, T-STL-09)."""
    if isinstance(ruling_data, str):
        try:
            ruling_dict = json.loads(ruling_data)
        except Exception as e:
            raise FindingsValidationError(f"ruling.json is not valid JSON: {e}")
    elif isinstance(ruling_data, dict):
        ruling_dict = ruling_data
    else:
        raise FindingsValidationError("ruling data must be a JSON object")

    to_rule = [
        f for f in open_findings 
        if f.get("status") in ("open", "disputed") and f.get("author") != "human"
    ]
    to_rule_ids = {f["id"] for f in to_rule}

    validated: Dict[str, Dict[str, str]] = {}
    for fid, val in ruling_dict.items():
        if not isinstance(val, dict):
            raise FindingsValidationError(f"Ruling for '{fid}' must be an object")
        status = val.get("status")
        if status not in ("fix", "withdraw", "human"):
            raise FindingsValidationError(f"Ruling for '{fid}' has invalid status '{status}'")
        reason = val.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise FindingsValidationError(f"Ruling for '{fid}' must have a non-empty reason")
        validated[fid] = {"status": status, "reason": reason}

    for required_fid in to_rule_ids:
        if required_fid not in validated:
            raise FindingsValidationError(f"ruling.json leaves finding '{required_fid}' unruled")

    return validated
