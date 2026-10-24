import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
import sys

from lintel.events import read_events

REVIEWER_ROLES: Set[str] = {
    "po-reviewer",
    "architect-reviewer",
    "qa-reviewer",
    "dev-reviewer",
}

REVIEWER_TO_WRITER: Dict[str, str] = {
    "po-reviewer": "po",
    "architect-reviewer": "architect",
    "qa-reviewer": "qa",
    "dev-reviewer": "dev",
}

def get_tasks_dir(repo_root: Path) -> Path:
    return repo_root / ".lintel" / "tasks"

def run_stats(repo_root: Union[str, Path], task_id: Optional[str] = None, as_json: bool = False) -> None:
    root = Path(repo_root).resolve()
    stats_data = compute_stats(root, task_id)
    format_stats(stats_data, as_json)

def compute_stats(repo_root: Path, task_id: Optional[str] = None) -> Dict[str, Any]:
    tasks_dir = get_tasks_dir(repo_root)
    if not tasks_dir.is_dir():
        return _empty_stats()
        
    task_dirs = []
    if task_id:
        target = tasks_dir / task_id
        if target.is_dir():
            task_dirs.append(target)
    else:
        for d in tasks_dir.iterdir():
            if d.is_dir() and (d / "events.jsonl").is_file():
                task_dirs.append(d)
                
    if not task_dirs:
        return _empty_stats()
        
    stats = _empty_stats()
    stats["tasks"] = len(task_dirs)
    
    for t_dir in task_dirs:
        _process_task(t_dir, stats, repo_root)
        
    # Process insights/recommendations based on stats
    _generate_insights(stats)
    
    return stats

def _empty_stats() -> Dict[str, Any]:
    return {
        "tasks": 0,
        "rounds_per_pair_and_phase": defaultdict(lambda: defaultdict(int)),
        "stall_reasons": defaultdict(int),
        "arbiter_rulings": defaultdict(int),
        "human_decisions": defaultdict(int),
        "time_per_step": {
            "total_duration_sec": 0.0,
            "count": 0,
            "min_sec": float('inf'),
            "max_sec": 0.0,
            "mean_sec": 0.0,
        },
        "red_gate_runs": {
            "exit_codes": defaultdict(int),
            "failed_groups": defaultdict(int),
        },
        "recurring_findings": 0,
        "crossings_by_role": defaultdict(lambda: defaultdict(int)),
        "insights": []
    }

def _process_task(task_dir: Path, stats: Dict[str, Any], repo_root: Path) -> None:
    events_file = task_dir / "events.jsonl"
    if not events_file.is_file():
        return
        
    events = read_events(events_file)
    
    finding_occurrences: Dict[str, Set[str]] = defaultdict(set) # finding_id -> set of runs
    
    for ev in events:
        event_type = ev.get("event")
        role = ev.get("role")
        stage = ev.get("stage")
        run = ev.get("run")
        
        # 1. Rounds (PLAN §3.9)
        if event_type == "done" and role and stage:
            if role in REVIEWER_ROLES:
                verdict = ev.get("verdict")
                is_valid_verdict = verdict in ("approve", "changes") or verdict is None
                is_counted = ev.get("row") != "T-STL-10"
                if is_valid_verdict and is_counted:
                    pair = REVIEWER_TO_WRITER.get(role, role.replace("-reviewer", ""))
                    stats["rounds_per_pair_and_phase"][stage][pair] += 1
            
        # 2. Stall reasons
        if event_type == "stall" and ev.get("stall_reason"):
            stats["stall_reasons"][ev["stall_reason"]] += 1
            
        # 3. Arbiter rulings
        if event_type == "ruling" and ev.get("verdict"):
            stats["arbiter_rulings"][ev["verdict"]] += 1
            
        # 4. Human decisions
        if event_type == "decide" and ev.get("decision"):
            stats["human_decisions"][ev["decision"]] += 1
            
        # 5. Time per step
        dur = ev.get("duration_seconds")
        if dur is not None and dur > 0:
            ts = stats["time_per_step"]
            ts["total_duration_sec"] += dur
            ts["count"] += 1
            if dur < ts["min_sec"]: ts["min_sec"] = dur
            if dur > ts["max_sec"]: ts["max_sec"] = dur
            
        # 6. Red gate runs
        if event_type in ("check", "deliver"):
            gate = ev.get("gate")
            if gate:
                code = gate.get("exit_code")
                if code is not None and code != 0:
                    stats["red_gate_runs"]["exit_codes"][code] += 1
                if code != 0:
                    for g_name, g_res in gate.items():
                        if g_name == "exit_code": continue
                        if isinstance(g_res, dict) and g_res.get("exit_code", 0) != 0:
                            stats["red_gate_runs"]["failed_groups"][g_name] += 1
                            
    # Calculate mean time
    ts = stats["time_per_step"]
    if ts["count"] > 0:
        ts["mean_sec"] = ts["total_duration_sec"] / ts["count"]
    if ts["min_sec"] == float('inf'):
        ts["min_sec"] = 0.0

    # 7. Recurring findings
    for findings_file in task_dir.rglob("findings.json"):
        try:
            with open(findings_file, "r") as f:
                fdata = json.load(f)
                run_id = findings_file.parent.name
                if isinstance(fdata, dict) and "findings" in fdata:
                    for item in fdata["findings"]:
                        fid = item.get("id")
                        if fid:
                            finding_occurrences[fid].add(run_id)
        except Exception:
            pass
            
    for fid, runs in finding_occurrences.items():
        if len(runs) > 1:
            stats["recurring_findings"] += 1

    # 8. Audit crossings by role (PLAN §3.10, TASKS M4-04)
    audit_file = task_dir / "audit.jsonl"
    if not audit_file.is_file():
        state_task_audit = repo_root / ".lintel" / "state" / task_dir.name / "audit.jsonl"
        if state_task_audit.is_file():
            audit_file = state_task_audit
        else:
            state_audit = repo_root / ".lintel" / "state" / "audit.jsonl"
            if state_audit.is_file():
                audit_file = state_audit
            else:
                audit_file = None

    if audit_file and audit_file.is_file():
        try:
            with open(audit_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        rec = json.loads(line_str)
                    except Exception:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    if rec.get("kind") == "crossing":
                        role = rec.get("role") or "unknown"
                        sev = rec.get("severity") or "crossing"
                        stats["crossings_by_role"][role][sev] += 1
        except Exception:
            pass

def _generate_insights(stats: Dict[str, Any]) -> None:
    ins = stats["insights"]
    if stats["recurring_findings"] > 5:
        ins.append("High number of recurring findings detected. Consider reviewing reviewer personas (QA, Dev-Reviewer) for unclear guidance or writers (Dev, QA) for skill deficits.")
    
    for code, count in stats["red_gate_runs"]["exit_codes"].items():
        if count > 5:
            ins.append(f"Gate exit code {code} occurred {count} times. Look into DEV stubs or local test configuration.")
            
    for stall_reason, count in stats["stall_reasons"].items():
        if count > 2:
            ins.append(f"Frequent stalls due to '{stall_reason}'. Check loop configuration (maxRounds).")

def format_stats(stats: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        def _to_dict(d):
            if isinstance(d, defaultdict):
                return {k: _to_dict(v) for k, v in d.items()}
            return d
        
        out = _to_dict(stats)
        sys.stdout.write(json.dumps(out, indent=2) + "\n")
        return
        
    sys.stdout.write("=== Lintel Task Statistics ===\n\n")
    sys.stdout.write(f"Tasks processed: {stats['tasks']}\n\n")
    
    sys.stdout.write("1. Rounds per Pair and Phase:\n")
    for phase, pairs in stats["rounds_per_pair_and_phase"].items():
        sys.stdout.write(f"  Phase: {phase}\n")
        for pair, count in pairs.items():
            sys.stdout.write(f"    {pair}: {count}\n")
    if not stats["rounds_per_pair_and_phase"]:
        sys.stdout.write("  (none)\n")
        
    sys.stdout.write("\n2. Stall Reasons:\n")
    for reason, count in stats["stall_reasons"].items():
        sys.stdout.write(f"  {reason}: {count}\n")
    if not stats["stall_reasons"]:
        sys.stdout.write("  (none)\n")
        
    sys.stdout.write("\n3. Arbiter Rulings:\n")
    for ruling, count in stats["arbiter_rulings"].items():
        sys.stdout.write(f"  {ruling}: {count}\n")
    if not stats["arbiter_rulings"]:
        sys.stdout.write("  (none)\n")
        
    sys.stdout.write("\n4. Human Decisions:\n")
    for dec, count in stats["human_decisions"].items():
        sys.stdout.write(f"  {dec}: {count}\n")
    if not stats["human_decisions"]:
        sys.stdout.write("  (none)\n")
        
    ts = stats["time_per_step"]
    sys.stdout.write("\n5. Time per Step (seconds):\n")
    sys.stdout.write(f"  Mean: {ts['mean_sec']:.2f}\n")
    sys.stdout.write(f"  Min:  {ts['min_sec']:.2f}\n")
    sys.stdout.write(f"  Max:  {ts['max_sec']:.2f}\n")
    sys.stdout.write(f"  Total steps with duration: {ts['count']}\n")
    
    sys.stdout.write("\n6. Red Gate Runs:\n")
    sys.stdout.write("  Exit Codes:\n")
    for code, count in stats["red_gate_runs"]["exit_codes"].items():
        sys.stdout.write(f"    {code}: {count}\n")
    sys.stdout.write("  Failed Groups:\n")
    for grp, count in stats["red_gate_runs"]["failed_groups"].items():
        sys.stdout.write(f"    {grp}: {count}\n")
    if not stats["red_gate_runs"]["exit_codes"] and not stats["red_gate_runs"]["failed_groups"]:
        sys.stdout.write("  (none)\n")
        
    sys.stdout.write(f"\n7. Recurring Findings: {stats['recurring_findings']}\n")
    
    sys.stdout.write("\n8. Audit Crossings by Role:\n")
    for role, severities in stats["crossings_by_role"].items():
        sys.stdout.write(f"  Role: {role}\n")
        for sev, count in severities.items():
            sys.stdout.write(f"    {sev}: {count}\n")
    if not stats["crossings_by_role"]:
        sys.stdout.write("  (none)\n")
        
    sys.stdout.write("\n=== Persona & Skill Insights ===\n")
    for ins in stats["insights"]:
        sys.stdout.write(f"- {ins}\n")
    if not stats["insights"]:
        sys.stdout.write("- No specific insights derived from current stats.\n")
