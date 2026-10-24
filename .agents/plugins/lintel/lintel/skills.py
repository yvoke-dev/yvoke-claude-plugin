"""Skill and steering resolution (M1c-01)."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lintel.personas import VALID_ROLES
from lintel.config import parse_jsonc, DEFAULT_PLUGIN_DIR

def resolve_skill_path(
    skill_name: str,
    workspace_root: Path,
    plugin_source: Optional[Path] = None
) -> Optional[Path]:
    """Resolve skill name to absolute SKILL.md path."""
    pdir = Path(plugin_source) if plugin_source is not None else DEFAULT_PLUGIN_DIR
    
    if skill_name.startswith("lintel:"):
        name = skill_name[len("lintel:"):]
        target = pdir / "skills" / name / "SKILL.md"
        return target if target.is_file() else None

    if ":" in skill_name:
        plugin, name = skill_name.split(":", 1)
        # Check workspace first
        ws_target = workspace_root / ".agents" / "plugins" / plugin / "skills" / name / "SKILL.md"
        if ws_target.is_file():
            return ws_target
        # Check global
        global_target = Path("~/.gemini/config/plugins").expanduser() / plugin / "skills" / name / "SKILL.md"
        if global_target.is_file():
            return global_target
        return None

    # Plain name
    target = workspace_root / ".agents" / "skills" / skill_name / "SKILL.md"
    return target if target.is_file() else None


def _get_layers(merged_config: dict, workspace_root: Optional[Path], plugin_dir: Optional[Path]) -> List[Tuple[str, dict]]:
    layers = []
    pdir = Path(plugin_dir) if plugin_dir is not None else DEFAULT_PLUGIN_DIR
    
    defaults_file = pdir / "presets" / "_defaults.json"
    if defaults_file.is_file():
        text = defaults_file.read_text(encoding="utf-8").strip()
        if text:
            layers.append(("defaults", parse_jsonc(text)))
            
    for p in merged_config.get("presets", []):
        pfile = pdir / "presets" / f"{p}.json"
        if pfile.is_file():
            layers.append((f"preset: {p}", parse_jsonc(pfile.read_text(encoding="utf-8"))))
            
    if "__raw_project_config" in merged_config:
        layers.append(("config", merged_config["__raw_project_config"]))
    elif workspace_root:
        cfile = workspace_root / ".lintel" / "config.jsonc"
        if cfile.is_file():
            layers.append(("config", parse_jsonc(cfile.read_text(encoding="utf-8"))))
    else:
        layers.append(("config", merged_config))
            
    return layers


def _apply_modifier(current: List[str], sources: Dict[str, str], spec: Any, layer_name: str) -> None:
    if isinstance(spec, list):
        for item in spec:
            if item not in current:
                current.append(item)
            sources[item] = layer_name
    elif isinstance(spec, dict):
        for item in spec.get("add", []):
            if item not in current:
                current.append(item)
            sources[item] = layer_name
        for item in spec.get("remove", []):
            if item in current:
                current.remove(item)
            sources.pop(item, None)


def _resolve_with_sources(
    item_type: str,
    merged_config: dict,
    role: str,
    pair: Optional[str],
    workspace_root: Optional[Path],
    plugin_dir: Optional[Path]
) -> List[Tuple[str, str]]:
    if role not in VALID_ROLES:
        return []

    layers = _get_layers(merged_config, workspace_root, plugin_dir)
    
    def _resolve_writer(w_role: str) -> Tuple[List[str], Dict[str, str]]:
        current: List[str] = []
        sources: Dict[str, str] = {}
        for layer_name, layer_data in layers:
            lmap = layer_data.get(item_type, {})
            _apply_modifier(current, sources, lmap.get("all", []), layer_name)
            _apply_modifier(current, sources, lmap.get(w_role, []), layer_name)
        return current, sources

    if role in ("po", "architect", "qa", "dev"):
        curr, src = _resolve_writer(role)
        return [(item, src[item]) for item in curr]

    if role in ("po-reviewer", "architect-reviewer", "qa-reviewer", "dev-reviewer"):
        writer_role = role.split("-")[0]
        curr, src = _resolve_writer(writer_role)
        for layer_name, layer_data in layers:
            lmap = layer_data.get(item_type, {})
            _apply_modifier(curr, src, lmap.get(role, []), layer_name)
        return [(item, src[item]) for item in curr]

    if role == "arbiter":
        stalled_pair = pair or "dev"
        rev_role = f"{stalled_pair}-reviewer" if not stalled_pair.endswith("-reviewer") else stalled_pair
        return _resolve_with_sources(item_type, merged_config, rev_role, None, workspace_root, plugin_dir)

    if role == "final-reviewer":
        curr: List[str] = []
        src: Dict[str, str] = {}
        for w in ("po", "architect", "qa", "dev"):
            w_curr, w_src = _resolve_writer(w)
            for item in w_curr:
                if item not in curr:
                    curr.append(item)
                src[item] = w_src[item]
        return [(item, src[item]) for item in curr]

    return []


def resolve_role_skills_with_sources(
    config: dict,
    role: str,
    pair: Optional[str] = None,
    workspace_root: Optional[Path] = None,
    plugin_dir: Optional[Path] = None
) -> List[Tuple[str, str]]:
    return _resolve_with_sources("skills", config, role, pair, workspace_root, plugin_dir)


def resolve_role_steering_with_sources(
    config: dict,
    role: str,
    pair: Optional[str] = None,
    workspace_root: Optional[Path] = None,
    plugin_dir: Optional[Path] = None
) -> List[Tuple[str, str]]:
    return _resolve_with_sources("steering", config, role, pair, workspace_root, plugin_dir)

