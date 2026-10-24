"""Configuration loading, layering, and validation (M1a-04)."""

from collections import OrderedDict
import copy
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Union

try:
    from lintel.personas import VALID_ROLES
except ImportError:
    from .personas import VALID_ROLES

DEFAULT_PLUGIN_DIR = Path(__file__).resolve().parent.parent

# Group name syntax per PLAN §3.8: [a-z][a-z0-9-]*
GROUP_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

# Preset name syntax: safe identifier to prevent path traversal
PRESET_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

# Allowed keys for validation and schema synchronization
ALLOWED_TOP_LEVEL_KEYS: Set[str] = {
    "$schema",
    "presets",
    "paths",
    "skills",
    "steering",
    "personas",
    "workflow",
    "permissions",
}

# Presets may additionally include detection markers (PLAN §8.1)
ALLOWED_PRESET_KEYS: Set[str] = ALLOWED_TOP_LEVEL_KEYS | {"markers"}

ALLOWED_PATHS_KEYS: Set[str] = {
    "tests",
    "testOutput",
    "testReport",
    "build",
    "source",
    "drift",
}

ALLOWED_GROUP_KEYS: Set[str] = {
    "paths",
    "required",
    "timeoutSeconds",
}

ALLOWED_WORKFLOW_KEYS: Set[str] = {
    "maxRounds",
    "stallRounds",
    "testTimeoutSeconds",
}

ALLOWED_LIST_MODIFIER_KEYS: Set[str] = {
    "add",
    "remove",
}

ALLOWED_SKILLS_ROLES: Set[str] = {"all", *VALID_ROLES}
ALLOWED_STEERING_ROLES: Set[str] = {"all", *VALID_ROLES}
ALLOWED_PERSONA_ROLES: Set[str] = set(VALID_ROLES)

DEFAULT_WORKFLOW: Dict[str, Any] = {
    "maxRounds": 25,
    "stallRounds": 3,
    "testTimeoutSeconds": 900,
}


class ConfigError(ValueError):
    """Raised when configuration parsing or validation fails."""
    pass


def strip_jsonc(text: str) -> str:
    """String-aware stripping of comments (// and /* ... */) and trailing commas.

    Characters like //, /*, */, and commas inside JSON strings are not stripped or corrupted.
    """
    out: List[str] = []
    i = 0
    n = len(text)
    in_string = False
    escape = False

    # Pass 1: Strip comments string-aware
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            i += 1
        else:
            if c == '"':
                in_string = True
                out.append(c)
                i += 1
            elif c == "/" and i + 1 < n and text[i + 1] == "/":
                # Line comment: skip until newline or EOF
                i += 2
                while i < n and text[i] not in ("\r", "\n"):
                    i += 1
            elif c == "/" and i + 1 < n and text[i + 1] == "*":
                # Block comment: skip until */
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    if text[i] == "\n":
                        out.append("\n")  # Preserve newline count for error reporting
                    i += 1
                if i + 1 >= n:
                    raise ConfigError("Unterminated block comment in JSONC")
                i += 2
            else:
                out.append(c)
                i += 1

    if in_string:
        raise ConfigError("Unterminated string in JSONC")

    no_comments = "".join(out)

    # Pass 2: Strip trailing commas string-aware
    out2: List[str] = []
    i = 0
    n = len(no_comments)
    in_string = False
    escape = False

    while i < n:
        c = no_comments[i]
        if in_string:
            out2.append(c)
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            i += 1
        else:
            if c == '"':
                in_string = True
                out2.append(c)
                i += 1
            elif c == ",":
                # Lookahead to see if next non-whitespace char is } or ]
                j = i + 1
                while j < n and no_comments[j] in (" ", "\t", "\r", "\n"):
                    j += 1
                if j < n and no_comments[j] in ("}", "]"):
                    # Trailing comma: omit it
                    i += 1
                else:
                    out2.append(c)
                    i += 1
            else:
                out2.append(c)
                i += 1

    return "".join(out2)


def parse_jsonc(content: str) -> Any:
    """Parse JSONC text after string-aware stripping of comments and trailing commas."""
    stripped = strip_jsonc(content)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON: {exc.msg} (line {exc.lineno} col {exc.colno})") from exc


def is_safe_steering_path(path_str: str) -> bool:
    """Check that a steering path stays inside .lintel/steering/."""
    if not isinstance(path_str, str) or not path_str.strip():
        return False
    if path_str.startswith("/") or path_str.startswith("\\"):
        return False
    norm = os.path.normpath(path_str).replace("\\", "/")
    if norm.startswith(".lintel/"):
        if not norm.startswith(".lintel/steering/"):
            return False
        rel = norm[len(".lintel/steering/") :]
        return not rel.startswith("..") and not rel.startswith("/") and len(rel) > 0
    combined = os.path.normpath(os.path.join(".lintel", "steering", norm)).replace("\\", "/")
    return combined.startswith(".lintel/steering/")


def is_safe_persona_path(path_str: str) -> bool:
    """Check that a persona path stays inside .lintel/personas/."""
    if not isinstance(path_str, str) or not path_str.strip():
        return False
    if path_str.startswith("/") or path_str.startswith("\\"):
        return False
    norm = os.path.normpath(path_str).replace("\\", "/")
    if norm.startswith(".lintel/"):
        if not norm.startswith(".lintel/personas/"):
            return False
        rel = norm[len(".lintel/personas/") :]
        return not rel.startswith("..") and not rel.startswith("/") and len(rel) > 0
    combined = os.path.normpath(os.path.join(".lintel", "personas", norm)).replace("\\", "/")
    return combined.startswith(".lintel/personas/")


def merge_list_semantics(base: list, override: Union[list, dict]) -> list:
    """Apply list semantics: plain array adds, object {"add": [...], "remove": [...]} (adds first, then removes)."""
    if isinstance(override, list):
        return list(base) + list(override)
    elif isinstance(override, dict):
        add_items = override.get("add", [])
        remove_items = set(override.get("remove", []))
        combined = list(base) + list(add_items)
        return [x for x in combined if x not in remove_items]
    else:
        raise ConfigError(f"Expected list or add/remove object, got {type(override).__name__}")


def merge_role_specs(base_val: Any, override_val: Any) -> Any:
    """Merge role specifications for skills or steering across layers.

    - If base is a list, evaluate list semantics directly (returns a list).
    - If base is a dict (modifier):
      - If override is a list, treat override as additions.
      - If override is a dict, combine add and remove lists.
    - If base is neither (or override is invalid), raise ConfigError.
    """
    if isinstance(base_val, list):
        return merge_list_semantics(base_val, override_val)

    if isinstance(base_val, dict):
        base_add = list(base_val.get("add", []))
        base_remove = list(base_val.get("remove", []))

        if isinstance(override_val, list):
            over_add = list(override_val)
            over_remove: List[str] = []
        elif isinstance(override_val, dict):
            over_add = list(override_val.get("add", []))
            over_remove = list(override_val.get("remove", []))
        else:
            raise ConfigError(f"Expected list or add/remove object, got {type(override_val).__name__}")

        over_remove_set = set(over_remove)
        over_add_set = set(over_add)

        # Combined additions:
        # Items added by base that are not removed by override, plus items added by override
        combined_add = [x for x in base_add if x not in over_remove_set] + [
            x for x in over_add if x not in over_remove_set
        ]
        combined_add = list(dict.fromkeys(combined_add))

        # Combined removals:
        # Items removed by base stay removed unless re-added by override (and not removed again by override)
        # Items removed by override are always removed
        combined_remove_set = (set(base_remove) - over_add_set) | over_remove_set
        combined_remove = [x for x in base_remove if x in combined_remove_set] + [
            x for x in over_remove if x not in base_remove
        ]
        combined_remove = list(dict.fromkeys(combined_remove))

        result: Dict[str, List[str]] = {}
        if combined_add:
            result["add"] = combined_add
        if combined_remove:
            result["remove"] = combined_remove
        return result

    if isinstance(override_val, (list, dict)):
        return copy.deepcopy(override_val)

    return override_val


def merge_test_groups(base_groups: dict, override_groups: dict) -> dict:
    """Merge test groups by name across layers:

    - paths lists merge with list semantics
    - required and timeoutSeconds override
    - null removes group
    - new groups placed after lower layers' ones
    - reserved group 'support' is placed after all named groups
    """
    result = OrderedDict()

    # Lower layer groups
    for name, spec in base_groups.items():
        if isinstance(spec, dict):
            result[name] = copy.deepcopy(spec)
        elif isinstance(spec, list):
            result[name] = {"paths": list(spec), "required": False if name == "support" else True}
        else:
            result[name] = copy.deepcopy(spec)

    # Upper layer overrides
    for name, spec in override_groups.items():
        if spec is None:
            result.pop(name, None)
            continue

        if isinstance(spec, list):
            spec_dict = {"paths": spec}
        elif isinstance(spec, dict):
            spec_dict = spec
        else:
            raise ConfigError(f"Test group {name!r} must be an object, list, or null")

        if name in result:
            current = result[name]
            if "paths" in spec_dict:
                current_paths = current.get("paths", [])
                current["paths"] = merge_list_semantics(current_paths, spec_dict["paths"])
            if "required" in spec_dict:
                current["required"] = spec_dict["required"]
            if "timeoutSeconds" in spec_dict:
                current["timeoutSeconds"] = spec_dict["timeoutSeconds"]
        else:
            new_group: Dict[str, Any] = {}
            if "paths" in spec_dict:
                new_group["paths"] = merge_list_semantics([], spec_dict["paths"])
            else:
                new_group["paths"] = []
            if "required" in spec_dict:
                new_group["required"] = spec_dict["required"]
            else:
                # Default required to True for named groups, False for support
                new_group["required"] = False if name == "support" else True
            if "timeoutSeconds" in spec_dict:
                new_group["timeoutSeconds"] = spec_dict["timeoutSeconds"]
            result[name] = new_group

    # Ensure support group, if present, comes after all named groups
    if "support" in result:
        support_val = result.pop("support")
        result["support"] = support_val

    return result


def merge_configs(base: dict, override: dict) -> dict:
    """Merge two configuration layers: base (lower) and override (higher)."""
    result = copy.deepcopy(base)
    result.pop("markers", None)

    if "$schema" in override:
        result["$schema"] = override["$schema"]

    if "presets" in override:
        result["presets"] = override["presets"]

    # Paths
    if "paths" in override:
        base_paths = result.get("paths", {})
        override_paths = override["paths"]
        merged_paths: Dict[str, Any] = {}

        for key in ("testOutput", "testReport", "build", "source", "drift"):
            if key in override_paths:
                merged_paths[key] = merge_list_semantics(base_paths.get(key, []), override_paths[key])
            elif key in base_paths:
                merged_paths[key] = list(base_paths[key])

        # Test groups
        base_tests = base_paths.get("tests", {})
        override_tests = override_paths.get("tests", {})
        merged_paths["tests"] = merge_test_groups(base_tests, override_tests)
        result["paths"] = merged_paths

    # Skills
    if "skills" in override:
        base_skills = result.get("skills", {})
        override_skills = override["skills"]
        merged_skills: Dict[str, Any] = {}
        for role, val in base_skills.items():
            merged_skills[role] = copy.deepcopy(val)
        for role, val in override_skills.items():
            if role in merged_skills:
                merged_skills[role] = merge_role_specs(merged_skills[role], val)
            else:
                merged_skills[role] = copy.deepcopy(val)
        result["skills"] = merged_skills

    # Steering
    if "steering" in override:
        base_steering = result.get("steering", {})
        override_steering = override["steering"]
        merged_steering: Dict[str, Any] = {}
        for role, val in base_steering.items():
            merged_steering[role] = copy.deepcopy(val)
        for role, val in override_steering.items():
            if role in merged_steering:
                merged_steering[role] = merge_role_specs(merged_steering[role], val)
            else:
                merged_steering[role] = copy.deepcopy(val)
        result["steering"] = merged_steering

    # Personas
    if "personas" in override:
        base_personas = result.get("personas", {})
        override_personas = override["personas"]
        merged_personas = dict(base_personas)
        merged_personas.update(override_personas)
        result["personas"] = merged_personas

    # Workflow
    if "workflow" in override:
        base_wf = result.get("workflow", {})
        override_wf = override["workflow"]
        merged_wf = dict(base_wf)
        merged_wf.update(override_wf)
        result["workflow"] = merged_wf

    return result


def _validate_list_modifier(data: Any, context: str) -> None:
    """Validate that data is either a list of strings or an add/remove object."""
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, str):
                raise ConfigError(f"{context} list items must be strings, got {type(item).__name__}")
    elif isinstance(data, dict):
        unknown_keys = set(data.keys()) - ALLOWED_LIST_MODIFIER_KEYS
        if unknown_keys:
            raise ConfigError(f"Unknown keys in {context} add/remove modifier: {sorted(unknown_keys)}")
        for key in ("add", "remove"):
            if key in data:
                items = data[key]
                if not isinstance(items, list):
                    raise ConfigError(f"{context} '{key}' must be a list of strings, got {type(items).__name__}")
                for item in items:
                    if not isinstance(item, str):
                        raise ConfigError(f"{context} '{key}' items must be strings, got {type(item).__name__}")
    else:
        raise ConfigError(f"{context} must be a list or add/remove object, got {type(data).__name__}")


def validate_config(
    config: dict,
    workspace_root: Optional[Union[str, Path]] = None,
    is_layer: bool = False,
    is_preset: bool = False,
) -> None:
    """Validate a configuration dictionary against MVP keys and rules.

    Raises ConfigError on any unknown keys, invalid roles, invalid syntax or illegal paths.
    """
    if not isinstance(config, dict):
        raise ConfigError(f"Configuration must be an object/dict, got {type(config).__name__}")

    # Top-level keys
    allowed_top_keys = ALLOWED_PRESET_KEYS if is_preset else ALLOWED_TOP_LEVEL_KEYS
    unknown_top_keys = set(config.keys()) - allowed_top_keys
    if unknown_top_keys:
        raise ConfigError(f"Unknown top-level config keys: {sorted(unknown_top_keys)}")

    # markers (presets only per PLAN §8.1)
    if "markers" in config:
        if not is_preset:
            raise ConfigError("Unknown top-level config keys: ['markers']")
        markers = config["markers"]
        if not isinstance(markers, list):
            raise ConfigError("markers must be a list of strings")
        for m in markers:
            if not isinstance(m, str) or not m.strip():
                raise ConfigError(f"Marker must be a non-empty string, got {m!r}")

    # $schema
    if "$schema" in config and not isinstance(config["$schema"], str):
        raise ConfigError("$schema must be a string")

    # presets
    if "presets" in config:
        presets = config["presets"]
        if not isinstance(presets, list):
            raise ConfigError("presets must be a list of preset names")
        for p in presets:
            if not isinstance(p, str) or not p.strip():
                raise ConfigError(f"Preset name must be a non-empty string, got {p!r}")
            if not PRESET_NAME_PATTERN.match(p):
                raise ConfigError(f"Invalid preset name {p!r}: must match ^[a-zA-Z0-9_-]+$")

    # paths
    if "paths" in config:
        paths = config["paths"]
        if not isinstance(paths, dict):
            raise ConfigError("paths must be an object")

        unknown_paths_keys = set(paths.keys()) - ALLOWED_PATHS_KEYS
        if unknown_paths_keys:
            raise ConfigError(f"Unknown keys in paths: {sorted(unknown_paths_keys)}")

        # Path globs
        for key in ("testOutput", "testReport", "build", "source", "drift"):
            if key in paths:
                _validate_list_modifier(paths[key], f"paths.{key}")

        # Test groups
        if "tests" in paths:
            tests = paths["tests"]
            if not isinstance(tests, dict):
                raise ConfigError("paths.tests must be an object mapping group names to configurations")

            for group_name, group_spec in tests.items():
                if not isinstance(group_name, str) or not GROUP_NAME_PATTERN.match(group_name):
                    raise ConfigError(f"Invalid test group name {group_name!r}: must match ^[a-z][a-z0-9-]*$")

                if group_spec is None:
                    if not is_layer:
                        raise ConfigError(f"Resolved test group {group_name!r} cannot be null")
                    continue

                if isinstance(group_spec, list):
                    for p in group_spec:
                        if not isinstance(p, str):
                            raise ConfigError(f"Test group {group_name!r} path items must be strings")
                elif isinstance(group_spec, dict):
                    unknown_group_keys = set(group_spec.keys()) - ALLOWED_GROUP_KEYS
                    if unknown_group_keys:
                        raise ConfigError(f"Unknown keys in test group {group_name!r}: {sorted(unknown_group_keys)}")

                    if "paths" in group_spec:
                        _validate_list_modifier(group_spec["paths"], f"paths.tests.{group_name}.paths")

                    if "required" in group_spec:
                        req = group_spec["required"]
                        if not isinstance(req, bool):
                            raise ConfigError(f"Test group {group_name!r} 'required' must be a boolean, got {req!r}")
                        if group_name == "support" and req is True:
                            raise ConfigError("Reserved group 'support' cannot be required")

                    if "timeoutSeconds" in group_spec:
                        timeout = group_spec["timeoutSeconds"]
                        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
                            raise ConfigError(f"Test group {group_name!r} 'timeoutSeconds' must be a positive number, got {timeout!r}")
                        if group_name == "support":
                            raise ConfigError("Reserved group 'support' has no script and cannot have a timeout")
                else:
                    raise ConfigError(f"Test group {group_name!r} must be an object, list, or null")

    # skills
    if "skills" in config:
        skills = config["skills"]
        if not isinstance(skills, dict):
            raise ConfigError("skills must be an object")

        unknown_roles = set(skills.keys()) - ALLOWED_SKILLS_ROLES
        if unknown_roles:
            raise ConfigError(f"Unknown roles in skills: {sorted(unknown_roles)}")

        for role, val in skills.items():
            _validate_list_modifier(val, f"skills.{role}")

    # steering
    if "steering" in config:
        steering = config["steering"]
        if not isinstance(steering, dict):
            raise ConfigError("steering must be an object")

        unknown_roles = set(steering.keys()) - ALLOWED_STEERING_ROLES
        if unknown_roles:
            raise ConfigError(f"Unknown roles in steering: {sorted(unknown_roles)}")

        for role, val in steering.items():
            _validate_list_modifier(val, f"steering.{role}")
            # Check path containment inside .lintel/steering/
            if isinstance(val, list):
                paths_to_check = val
            elif isinstance(val, dict):
                paths_to_check = val.get("add", [])
            else:
                paths_to_check = []

            for p in paths_to_check:
                if not is_safe_steering_path(p):
                    raise ConfigError(f"Steering path {p!r} for role {role!r} must be inside .lintel/steering/")

    # personas
    if "personas" in config:
        personas = config["personas"]
        if not isinstance(personas, dict):
            raise ConfigError("personas must be an object")

        unknown_roles = set(personas.keys()) - ALLOWED_PERSONA_ROLES
        if unknown_roles:
            raise ConfigError(f"Unknown roles in personas: {sorted(unknown_roles)}")

        for role, p in personas.items():
            if not isinstance(p, str):
                raise ConfigError(f"Persona path for role {role!r} must be a string, got {type(p).__name__}")
            if not is_safe_persona_path(p):
                raise ConfigError(f"Persona path {p!r} for role {role!r} must be inside .lintel/personas/")

            if workspace_root is not None:
                ws = Path(workspace_root)
                norm = os.path.normpath(p).replace("\\", "/")
                if norm.startswith(".lintel/personas/"):
                    target_file = ws / norm
                else:
                    target_file = ws / ".lintel" / "personas" / norm
                if not target_file.is_file():
                    raise ConfigError(f"Persona file for role {role!r} does not exist: {target_file}")

    # workflow
    if "workflow" in config:
        wf = config["workflow"]
        if not isinstance(wf, dict):
            raise ConfigError("workflow must be an object")

        unknown_wf_keys = set(wf.keys()) - ALLOWED_WORKFLOW_KEYS
        if unknown_wf_keys:
            raise ConfigError(f"Unknown keys in workflow: {sorted(unknown_wf_keys)}")

        if "maxRounds" in wf:
            val = wf["maxRounds"]
            if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
                raise ConfigError(f"workflow.maxRounds must be a positive integer, got {val!r}")

        if "stallRounds" in wf:
            val = wf["stallRounds"]
            if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
                raise ConfigError(f"workflow.stallRounds must be a positive integer, got {val!r}")

        if "testTimeoutSeconds" in wf:
            val = wf["testTimeoutSeconds"]
            if isinstance(val, bool) or not isinstance(val, (int, float)) or val <= 0:
                raise ConfigError(f"workflow.testTimeoutSeconds must be a positive number, got {val!r}")


def load_preset(preset_name: str, plugin_dir: Optional[Union[str, Path]] = None) -> dict:
    """Load and validate a single preset configuration file from presets/<preset_name>.json."""
    if not PRESET_NAME_PATTERN.match(preset_name):
        raise ConfigError(f"Invalid preset name {preset_name!r}: must match ^[a-zA-Z0-9_-]+$")
    pdir = DEFAULT_PLUGIN_DIR if plugin_dir is None else Path(plugin_dir)
    preset_file = pdir / "presets" / f"{preset_name}.json"
    if not preset_file.is_file():
        raise ConfigError(f"Preset file not found: {preset_file}")
    preset_data = parse_jsonc(preset_file.read_text(encoding="utf-8"))
    if not isinstance(preset_data, dict):
        raise ConfigError(f"Preset {preset_name!r} must be a JSON object")
    validate_config(preset_data, workspace_root=None, is_layer=True, is_preset=True)
    return preset_data


def load_config(
    source: Union[str, Path, bytes, dict],
    workspace_root: Optional[Union[str, Path]] = None,
    plugin_dir: Optional[Union[str, Path]] = None,
    load_layers: bool = True,
) -> dict:
    """Load, merge, and validate configuration from file path, string/bytes content, or dict."""
    if plugin_dir is None:
        pdir = DEFAULT_PLUGIN_DIR
    else:
        pdir = Path(plugin_dir)

    # 1. Parse source
    if isinstance(source, dict):
        project_config = copy.deepcopy(source)
    else:
        if isinstance(source, bytes):
            content = source.decode("utf-8")
        elif isinstance(source, Path):
            content = source.read_text(encoding="utf-8")
        elif isinstance(source, str):
            if "\n" not in source and Path(source).is_file():
                content = Path(source).read_text(encoding="utf-8")
            else:
                content = source
        else:
            raise TypeError(f"Invalid config source type: {type(source)}")

        project_config = parse_jsonc(content)
        if not isinstance(project_config, dict):
            raise ConfigError("Configuration must be a JSON object")

    # Validate raw input as a layer
    validate_config(project_config, workspace_root=None, is_layer=True)

    if not load_layers:
        validate_config(project_config, workspace_root=workspace_root, is_layer=False)
        return project_config

    # 2. Defaults layer
    layers: List[dict] = []
    defaults_file = pdir / "presets" / "_defaults.json"
    if defaults_file.is_file():
        defaults_text = defaults_file.read_text(encoding="utf-8").strip()
        if defaults_text:
            defaults_data = parse_jsonc(defaults_text)
            if defaults_data and isinstance(defaults_data, dict):
                validate_config(defaults_data, workspace_root=None, is_layer=True)
                layers.append(defaults_data)

    # 3. Presets layers in order
    presets_list = project_config.get("presets", [])
    for preset_name in presets_list:
        preset_data = load_preset(preset_name, plugin_dir=pdir)
        layers.append(preset_data)

    # 4. Project config layer
    layers.append(project_config)

    # 5. Merge all layers
    merged: Dict[str, Any] = {}
    for layer in layers:
        merged = merge_configs(merged, layer)

    # 6. Apply default workflow settings if missing
    wf = merged.setdefault("workflow", {})
    for k, v in DEFAULT_WORKFLOW.items():
        wf.setdefault(k, v)

    # 7. Normalize empty paths/skills/steering/personas if omitted
    paths = merged.setdefault("paths", {})
    paths.setdefault("tests", OrderedDict())
    for k in ("testOutput", "testReport", "build", "source", "drift"):
        paths.setdefault(k, [])
    merged.setdefault("skills", {})
    merged.setdefault("steering", {})
    merged.setdefault("personas", {})
    merged.setdefault("presets", presets_list)

    # 8. Validate final merged config
    validate_config(merged, workspace_root=workspace_root, is_layer=False)

    return merged


def resolve_skills_for_role(config: dict, role: str, pair: Optional[str] = None) -> List[str]:
    """Resolve skill list for a given role according to PLAN §6 and §8.3:

    - Writers (po, architect, qa, dev) take 'all' + role.
    - Reviewers take writer's resolved list + reviewer's list.
    - Arbiter takes the stalled pair's reviewer list.
    - Final reviewer takes union of four writers' lists.
    """
    if role not in VALID_ROLES:
        raise ConfigError(f"Unknown role: {role!r}")

    skills_map = config.get("skills", {})

    def _get_list(key: str) -> List[str]:
        val = skills_map.get(key, [])
        if isinstance(val, list):
            return list(val)
        if isinstance(val, dict):
            return merge_list_semantics([], val)
        return []

    all_list = _get_list("all")

    if role in ("po", "architect", "qa", "dev"):
        combined = all_list + _get_list(role)
        return list(dict.fromkeys(combined))

    if role == "po-reviewer":
        writer_skills = resolve_skills_for_role(config, "po")
        spec = skills_map.get("po-reviewer", [])
        return list(dict.fromkeys(merge_list_semantics(writer_skills, spec)))

    if role == "architect-reviewer":
        writer_skills = resolve_skills_for_role(config, "architect")
        spec = skills_map.get("architect-reviewer", [])
        return list(dict.fromkeys(merge_list_semantics(writer_skills, spec)))

    if role == "qa-reviewer":
        writer_skills = resolve_skills_for_role(config, "qa")
        spec = skills_map.get("qa-reviewer", [])
        return list(dict.fromkeys(merge_list_semantics(writer_skills, spec)))

    if role == "dev-reviewer":
        writer_skills = resolve_skills_for_role(config, "dev")
        spec = skills_map.get("dev-reviewer", [])
        return list(dict.fromkeys(merge_list_semantics(writer_skills, spec)))

    if role == "arbiter":
        stalled_pair = pair or "dev"
        rev_role = f"{stalled_pair}-reviewer" if not stalled_pair.endswith("-reviewer") else stalled_pair
        return resolve_skills_for_role(config, rev_role)

    if role == "final-reviewer":
        combined: List[str] = []
        for w in ("po", "architect", "qa", "dev"):
            combined.extend(resolve_skills_for_role(config, w))
        return list(dict.fromkeys(combined))

    return []


def resolve_steering_for_role(config: dict, role: str, pair: Optional[str] = None) -> List[str]:
    """Resolve steering files for a given role according to PLAN §6:

    - Writers take 'all' + role.
    - Reviewers take writer's resolved list + reviewer's list.
    - Arbiter takes the stalled pair's reviewer list.
    - Final reviewer takes union of four writers' lists.
    """
    if role not in VALID_ROLES:
        raise ConfigError(f"Unknown role: {role!r}")

    steering_map = config.get("steering", {})

    def _get_list(key: str) -> List[str]:
        val = steering_map.get(key, [])
        if isinstance(val, list):
            return list(val)
        if isinstance(val, dict):
            return merge_list_semantics([], val)
        return []

    all_list = _get_list("all")

    if role in ("po", "architect", "qa", "dev"):
        combined = all_list + _get_list(role)
        return list(dict.fromkeys(combined))

    if role == "po-reviewer":
        writer_steering = resolve_steering_for_role(config, "po")
        spec = steering_map.get("po-reviewer", [])
        return list(dict.fromkeys(merge_list_semantics(writer_steering, spec)))

    if role == "architect-reviewer":
        writer_steering = resolve_steering_for_role(config, "architect")
        spec = steering_map.get("architect-reviewer", [])
        return list(dict.fromkeys(merge_list_semantics(writer_steering, spec)))

    if role == "qa-reviewer":
        writer_steering = resolve_steering_for_role(config, "qa")
        spec = steering_map.get("qa-reviewer", [])
        return list(dict.fromkeys(merge_list_semantics(writer_steering, spec)))

    if role == "dev-reviewer":
        writer_steering = resolve_steering_for_role(config, "dev")
        spec = steering_map.get("dev-reviewer", [])
        return list(dict.fromkeys(merge_list_semantics(writer_steering, spec)))

    if role == "arbiter":
        stalled_pair = pair or "dev"
        rev_role = f"{stalled_pair}-reviewer" if not stalled_pair.endswith("-reviewer") else stalled_pair
        return resolve_steering_for_role(config, rev_role)

    if role == "final-reviewer":
        combined: List[str] = []
        for w in ("po", "architect", "qa", "dev"):
            combined.extend(resolve_steering_for_role(config, w))
        return list(dict.fromkeys(combined))

    return []
