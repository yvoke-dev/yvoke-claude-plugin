import json
import uuid
import sys
import argparse
from pathlib import Path
from typing import Dict, Optional, Any, List

def _get_comment_syntax(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    if ext in {".java", ".js", ".ts", ".go", ".rs", ".c", ".cpp"}:
        return "\n// {marker}\n"
    else:
        return "\n# {marker}\n"

def plant_markers(repo_root: Path, 
                 paths_tests: Optional[List[str]] = None, 
                 paths_impl: Optional[List[str]] = None,
                 tests: Optional[List[str]] = None,
                 impl: Optional[List[str]] = None) -> Dict[str, Dict[str, str]]:
    manifest: Dict[str, Dict[str, str]] = {}
    
    test_paths = paths_tests if paths_tests is not None else (tests if tests is not None else [])
    impl_paths = paths_impl if paths_impl is not None else (impl if impl is not None else [])
    
    for p in test_paths:
        full_path = repo_root / p
        if not full_path.exists():
            continue
        marker = f"CANARY_TEST_{uuid.uuid4().hex}"
        syntax = _get_comment_syntax(full_path)
        with open(full_path, "a") as f:
            f.write(syntax.format(marker=marker))
        manifest[marker] = {"path": str(p), "category": "test"}
        
    for p in impl_paths:
        full_path = repo_root / p
        if not full_path.exists():
            continue
        marker = f"CANARY_IMPL_{uuid.uuid4().hex}"
        syntax = _get_comment_syntax(full_path)
        with open(full_path, "a") as f:
            f.write(syntax.format(marker=marker))
        manifest[marker] = {"path": str(p), "category": "implementation"}
        
    return manifest

def clean_markers(repo_root: Path, manifest: Dict[str, Dict[str, str]]) -> None:
    for marker, info in manifest.items():
        p = repo_root / info["path"]
        if not p.exists():
            continue
        with open(p, "r") as f:
            content = f.read()
        
        # Remove both types of comments just to be safe
        content = content.replace(f"\n// {marker}\n", "")
        content = content.replace(f"\n# {marker}\n", "")
        
        with open(p, "w") as f:
            f.write(content)

def get_brain_dir(payload_path: Optional[Path], brain_dir: Optional[Path]) -> Optional[Path]:
    if brain_dir:
        return brain_dir
    if not payload_path or not payload_path.exists():
        return None
    try:
        with open(payload_path, "r") as f:
            data = json.load(f)
            artifact_dir = data.get("artifactDirectoryPath")
            if artifact_dir:
                return Path(artifact_dir).parent
    except Exception:
        pass
    return None

def verify_canaries(manifest: Dict[str, Dict[str, str]], 
                    brain_dir_or_payload: Path, 
                    audit_log: Path, 
                    is_payload: bool = False) -> Dict[str, Any]:
    brain_dir = get_brain_dir(brain_dir_or_payload if is_payload else None, 
                              brain_dir_or_payload if not is_payload else None)
    if not brain_dir:
        return {"leaks": [], "missing_audit": []}

    # Map conversation id to role from subagents
    role_by_conv = {}
    subagents_dir = brain_dir / ".system_generated" / "subagents"
    if subagents_dir.exists():
        for child in subagents_dir.glob("*.json"):
            try:
                with open(child, "r") as f:
                    data = json.load(f)
                    role = data.get("name") or data.get("role")
                    if role:
                        role_by_conv[child.stem] = role
            except Exception:
                pass

    # Read audit log entries
    audit_entries = []
    if audit_log.exists():
        with open(audit_log, "r") as f:
            for line in f:
                if not line.strip(): continue
                try:
                    entry = json.loads(line)
                    audit_entries.append(entry)
                except json.JSONDecodeError:
                    pass

    leaks = []
    missing_audit = []

    for conv_dir in brain_dir.iterdir():
        if not conv_dir.is_dir() or conv_dir.name == ".system_generated":
            continue
            
        logs_dir = conv_dir / ".system_generated" / "logs"
        transcript = logs_dir / "transcript_full.jsonl"
        if not transcript.exists():
            transcript = logs_dir / "transcript.jsonl"
            
        if not transcript.exists():
            continue
            
        role = role_by_conv.get(conv_dir.name, "")
        
        # Read transcript skipping partial last line
        transcript_text = ""
        with open(transcript, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                try:
                    json.loads(line) # validate
                    transcript_text += line
                except json.JSONDecodeError:
                    pass
                    
        for marker, info in manifest.items():
            if marker in transcript_text:
                file_cat = info["category"]
                file_path = info["path"]
                
                # Check for leaks
                # "DEV or dev-reviewer seeing a test marker"
                # "Or any role seeing forbidden markers."
                is_leak = False
                if role in ("DEV", "dev", "dev-reviewer") and file_cat == "test":
                    is_leak = True
                
                if is_leak:
                    leaks.append({"role": role, "marker": marker, "path": file_path, "conv": conv_dir.name})
                    
                    # Check if there is an audit entry for this role and path
                    audit_found = False
                    for entry in audit_entries:
                        # Extract role: first check if flat 'role' exists (fallback), else map conversationId
                        e_role = entry.get("role")
                        if not e_role:
                            conv_id = entry.get("conversationId")
                            if conv_id:
                                e_role = role_by_conv.get(conv_id, "")
                                
                        if e_role == role:
                            # Check if file_path is mentioned in the entry
                            # flat path fallback
                            e_path = entry.get("path")
                            if e_path and file_path in e_path:
                                audit_found = True
                                break
                            
                            # real audit.jsonl: check target and args
                            target = entry.get("target", "")
                            if target and file_path in target:
                                audit_found = True
                                break
                                
                            args = entry.get("args", {})
                            if any(isinstance(v, str) and file_path in v for v in args.values()):
                                audit_found = True
                                break
                                
                    if not audit_found:
                        missing_audit.append({"role": role, "marker": marker, "path": file_path})
                        
    return {"leaks": leaks, "missing_audit": missing_audit}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plant", action="store_true")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--repo-root", type=str, default=".")
    parser.add_argument("--manifest-in", type=str, default="manifest.json")
    parser.add_argument("--manifest-out", type=str, default="manifest.json")
    parser.add_argument("--tests", nargs="*", default=[])
    parser.add_argument("--impl", nargs="*", default=[])
    parser.add_argument("--brain-dir", type=str)
    parser.add_argument("--payload", type=str)
    parser.add_argument("--audit", type=str)
    
    args = parser.parse_args()
    root = Path(args.repo_root)
    
    if args.plant:
        man = plant_markers(root, args.tests, args.impl)
        with open(args.manifest_out, "w") as f:
            json.dump(man, f, indent=2)
            
    elif args.clean:
        with open(args.manifest_in, "r") as f:
            man = json.load(f)
        clean_markers(root, man)
        
    elif args.verify:
        with open(args.manifest_in, "r") as f:
            man = json.load(f)
        
        if args.payload:
            res = verify_canaries(man, Path(args.payload), Path(args.audit) if args.audit else Path("audit.jsonl"), is_payload=True)
        else:
            res = verify_canaries(man, Path(args.brain_dir), Path(args.audit) if args.audit else Path("audit.jsonl"), is_payload=False)
            
        print(json.dumps(res, indent=2))
        
        if res["missing_audit"]:
            print("Missing audit entries found!", file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    main()
