"""Lintel project installer and initialization (M1a-15, PLAN §3.1, §11).

Implements:
1. install(project_dir, plugin_source=None) -> None:
   - Copies plugin/ to <project>/.agents/plugins/lintel/ (excluding noise: __pycache__, *.pyc, etc.).
   - Writes VERSION file into .agents/plugins/lintel/VERSION.
   - Ensures fixed launcher spelling ./.lintel/bin/lintel in workflow skills.
   - Runs init(project_dir).
   - Prints PLAN §11 settings, allow rules, and macOS notes for the project.
2. init(project_dir, only_missing_scripts=False, plugin_source=None) -> None:
   - Preset detection: detects stack markers (e.g. pom.xml -> maven).
   - Writes .lintel/config.jsonc (if missing or only updating scripts).
   - Copies script templates from plugin/templates/scripts/:
     - Renders test-<group>.sh for each configured test group.
     - Copies setup.sh, teardown.sh, build.sh.
     - If only_missing_scripts=True (init --scripts): copies ONLY missing scripts, never overwriting.
   - Copies all 10 persona files from plugin/personas/*.md to .lintel/personas/.
   - Writes 10 agent files .agents/agents/lintel-<role>.md from template.
   - Writes launcher .lintel/bin/lintel and makes it executable (chmod 0755).
   - Adds .lintel/state/ to .gitignore.
   - Resolves and records Python interpreter in .lintel/state/python.
   - Prints PLAN §3.1 step 4 next steps.
"""

import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Dict, List, Optional, Set, Union

from lintel.config import load_config
from lintel.find_python import find_and_record_python
from lintel.personas import VALID_ROLES
from lintel.scripts import load_template, render_test_group_script
from lintel.self_check import ALL_10_ROLES


class InstallerRefusalError(Exception):
    pass


DEFAULT_PLUGIN_DIR = Path(__file__).resolve().parent.parent

DEFAULT_STEERING_MAPPING: Dict[str, List[str]] = {
    "all": ["tech.md", "structure.md", "conventions.md"],
    "architect": ["architecture.md", "testing.md"],
    "dev": ["architecture.md"],
    "qa": ["testing.md"],
}

DEFAULT_PERSONAS_MAPPING: Dict[str, str] = {
    role: f".lintel/personas/{role}.md" for role in ALL_10_ROLES
}


def detect_presets(
    project_dir: Union[str, Path],
    plugin_source: Optional[Union[str, Path]] = None,
) -> List[str]:
    """Detect applicable preset names from stack markers in project_dir."""
    proj = Path(project_dir).resolve()
    src = Path(plugin_source).resolve() if plugin_source is not None else DEFAULT_PLUGIN_DIR
    presets_dir = src / "presets"
    detected: List[str] = []

    if presets_dir.is_dir():
        for pfile in sorted(presets_dir.glob("*.json")):
            if pfile.name.startswith("_"):
                continue
            preset_name = pfile.stem
            try:
                data = json.loads(pfile.read_text(encoding="utf-8"))
                markers = data.get("markers", [])
                for marker in markers:
                    if (proj / marker).exists():
                        if preset_name not in detected:
                            detected.append(preset_name)
                        break
                    elif any(proj.glob(marker)):
                        if preset_name not in detected:
                            detected.append(preset_name)
                        break
            except Exception:
                continue

    # Explicit maven fallback check if pom.xml present
    if (proj / "pom.xml").exists() and "maven" not in detected:
        detected.append("maven")

    return detected


def ensure_launcher_spelling_in_skills(skills_dir: Union[str, Path]) -> None:
    """Ensure fixed launcher spelling ./.lintel/bin/lintel across workflow skills."""
    sdir = Path(skills_dir).resolve()
    if not sdir.is_dir():
        return
    for path in sorted(sdir.rglob("*")):
        if path.is_file() and path.suffix in (".md", ".txt", ".json", ".sh"):
            try:
                content = path.read_text(encoding="utf-8")
            except Exception:
                continue
            updated = content
            for placeholder in ("{{launcher}}", "<launcher>", "__LAUNCHER__"):
                if placeholder in updated:
                    updated = updated.replace(placeholder, "./.lintel/bin/lintel")
            if updated != content:
                path.write_text(updated, encoding="utf-8")


def print_install_notes(project_dir: Path, detected_presets: List[str]) -> None:
    """Print settings, allow rules, and macOS notes of PLAN §11."""
    allow_rules = [
        "command(./.lintel/bin/lintel)",
        "command(./.lintel/scripts/build.sh)",
    ]
    if "maven" in detected_presets or (project_dir / "mvnw").exists() or (project_dir / "pom.xml").exists():
        allow_rules.append("command(./mvnw)")
    allow_rules.extend([
        "command(git status)",
        "command(git diff)",
        "command(git log)",
        "command(git show)",
    ])

    rules_text = "\n".join(f"  - {r}" for r in allow_rules)
    notes = (
        "\nLintel installed successfully.\n\n"
        "Platform settings and allow rules (PLAN §11):\n"
        "- Tool-permission mode: accept-edits with allow rules (recommended).\n"
        "- Terminal sandbox: MUST BE OFF (enableTerminalSandbox: false).\n"
        "- Workspace trust: add this project folder to trustedWorkspaces.\n"
        "- Allow rules required for this project:\n"
        f"{rules_text}\n\n"
        "- macOS permissions: Do NOT grant Automation or Accessibility permissions to the host app (Ghostty, Terminal, IDE).\n"
    )
    print(notes)


def print_init_next_steps() -> None:
    """Print next steps per PLAN §3.1 step 4."""
    steps = (
        "Next steps (PLAN §3.1 step 4):\n"
        "1. Adjust test scripts (.lintel/scripts/test-*.sh) and build.sh.\n"
        "2. Populate steering files (.lintel/steering/) and specs (.lintel/specs/) with lintel-steering and lintel-specs skills.\n"
        "3. Commit agent files (.agents/agents/), plugin, launcher, config, scripts, personas, steering and specs.\n"
        "4. Restart the agent session (agy reads agent files when its process starts).\n"
        "5. Trust the project folder in agy/IDE trusted workspaces.\n"
    )
    print(steps)


def install(
    project_dir: Union[str, Path],
    plugin_source: Optional[Union[str, Path]] = None,
    global_install: bool = False,
    dest: Optional[Union[str, Path]] = None,
) -> None:
    """Vendor plugin into project directory (or globally) and initialize project per PLAN §3.1."""
    proj = Path(project_dir).resolve()
    src = Path(plugin_source).resolve() if plugin_source is not None else DEFAULT_PLUGIN_DIR

    if global_install:
        if dest:
            dest_plugin = Path(dest).resolve()
        else:
            dest_plugin = Path.home() / ".gemini" / "antigravity" / "plugins" / "lintel"
    else:
        dest_plugin = proj / ".agents" / "plugins" / "lintel"

    dest_plugin.parent.mkdir(parents=True, exist_ok=True)

    # 1. Copy plugin to <project>/.agents/plugins/lintel/, excluding noise
    if dest_plugin.exists():
        shutil.rmtree(dest_plugin)

    def _ignore_noise(path: str, names: List[str]) -> Set[str]:
        ignored: Set[str] = set()
        for name in names:
            if name in (".DS_Store", "__pycache__"):
                ignored.add(name)
            elif name.endswith(".pyc") or name.endswith(".swp") or name.endswith("~"):
                ignored.add(name)
        return ignored

    shutil.copytree(src, dest_plugin, ignore=_ignore_noise)

    # 2. Write VERSION file
    version_src = src / "VERSION"
    if version_src.is_file():
        version_str = version_src.read_text(encoding="utf-8").strip()
    else:
        version_str = "0.1.0"
    (dest_plugin / "VERSION").write_text(f"{version_str}\n", encoding="utf-8")

    # 3. Ensure launcher spelling in workflow skills
    ensure_launcher_spelling_in_skills(dest_plugin / "skills")

    # 4. Run init
    if global_install:
        found_py = find_and_record_python(dest_plugin)
        if not found_py:
            sys.stderr.write(
                "lintel: no suitable Python >= 3.9 interpreter found.\n"
                "Please install Python via 'xcode-select --install' or 'brew install python'.\n"
            )
            raise RuntimeError("No suitable Python >= 3.9 interpreter found")

    init(project_dir=proj, only_missing_scripts=False, plugin_source=src, global_install=global_install, dest_plugin=dest_plugin)

    # 5. Print settings, allow rules, and macOS notes of PLAN §11
    detected = detect_presets(proj, src)
    print_install_notes(proj, detected)


def init(
    project_dir: Union[str, Path],
    only_missing_scripts: bool = False,
    plugin_source: Optional[Union[str, Path]] = None,
    global_install: bool = False,
    dest_plugin: Optional[Union[str, Path]] = None,
) -> None:
    """Initialize Lintel project configuration, templates, launcher and python interpreter."""
    proj = Path(project_dir).resolve()
    src = Path(plugin_source).resolve() if plugin_source is not None else DEFAULT_PLUGIN_DIR

    scripts_dir = proj / ".lintel" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    scripts_template_dir = src / "templates" / "scripts"

    if only_missing_scripts:
        # init --scripts: copy only missing templates, never overwrite
        config_file = proj / ".lintel" / "config.jsonc"
        if config_file.is_file():
            try:
                cfg = load_config(config_file, workspace_root=proj, plugin_dir=src)
                tests_cfg = cfg.get("paths", {}).get("tests", {})
            except Exception:
                tests_cfg = {"unit": {}, "integration": {}, "e2e": {}}
        else:
            tests_cfg = {"unit": {}, "integration": {}, "e2e": {}}

        # Copy missing test group scripts
        for group in tests_cfg:
            if group == "support":
                continue
            script_path = scripts_dir / f"test-{group}.sh"
            if not script_path.exists():
                tmpl = load_template("test-group", templates_dir=scripts_template_dir)
                rendered = render_test_group_script(tmpl, group)
                script_path.write_text(rendered, encoding="utf-8")
                os.chmod(script_path, 0o755)

        # Copy missing standard scripts
        for sname in ("setup.sh", "teardown.sh", "build.sh"):
            script_path = scripts_dir / sname
            if not script_path.exists():
                tmpl = load_template(sname, templates_dir=scripts_template_dir)
                script_path.write_text(tmpl, encoding="utf-8")
                os.chmod(script_path, 0o755)

        return

    # 1. Preset detection
    detected = detect_presets(proj, src)

    # 2. Write .lintel/config.jsonc if missing
    lintel_dir = proj / ".lintel"
    lintel_dir.mkdir(parents=True, exist_ok=True)
    config_file = lintel_dir / "config.jsonc"
    if not config_file.is_file():
        cfg_dict: Dict[str, Any] = {
            "$schema": "../.agents/plugins/lintel/schema/config.schema.json",
            "presets": detected,
            "steering": DEFAULT_STEERING_MAPPING,
            "personas": DEFAULT_PERSONAS_MAPPING,
        }
        config_file.write_text(json.dumps(cfg_dict, indent=2) + "\n", encoding="utf-8")

    # 3. Copy persona files to .lintel/personas/
    personas_dest = lintel_dir / "personas"
    personas_dest.mkdir(parents=True, exist_ok=True)
    personas_src = src / "personas"
    if personas_src.is_dir():
        for pfile in personas_src.glob("*.md"):
            shutil.copy2(pfile, personas_dest / pfile.name)

    # 4. Write 10 agent files in .agents/agents/lintel-<role>.md
    agents_dest = proj / ".agents" / "agents"
    agents_dest.mkdir(parents=True, exist_ok=True)
    agent_tmpl_path = src / "templates" / "agents" / "persona.md"
    agent_tmpl = agent_tmpl_path.read_text(encoding="utf-8")
    for role in ALL_10_ROLES:
        agent_file = agents_dest / f"lintel-{role}.md"
        agent_file.write_text(agent_tmpl.replace("<role>", role), encoding="utf-8")

    # 5. Write launcher .lintel/bin/lintel (executable)
    bin_dest = lintel_dir / "bin"
    bin_dest.mkdir(parents=True, exist_ok=True)
    launcher_dest = bin_dest / "lintel"
    launcher_src = src / "templates" / "bin" / "lintel"
    
    if global_install and dest_plugin is not None:
        launcher_text = launcher_src.read_text(encoding="utf-8")
        # Ensure the global cli.py is checked first
        global_cli = Path(dest_plugin) / "lintel" / "cli.py"
        new_cli_check = f'if [ -f "{global_cli}" ]; then\n    CLI_PY="{global_cli}"\nelif'
        launcher_text = launcher_text.replace("if [ -f \"$ROOT/.agents/plugins/lintel/lintel/cli.py\" ]; then\n    CLI_PY=\"$ROOT/.agents/plugins/lintel/lintel/cli.py\"\nelif", new_cli_check)
        
        # Patch STATE_FILE to use the global python
        global_python = Path(dest_plugin) / "python"
        launcher_text = launcher_text.replace('STATE_FILE="$STATE_DIR/python"', f'STATE_FILE="{global_python}"')
        
        launcher_dest.write_text(launcher_text, encoding="utf-8")
    else:
        shutil.copy2(launcher_src, launcher_dest)
        
    os.chmod(launcher_dest, 0o755)

    # 6. Copy script templates
    try:
        cfg = load_config(config_file, workspace_root=proj, plugin_dir=src)
        tests_cfg = cfg.get("paths", {}).get("tests", {})
    except Exception:
        tests_cfg = {"unit": {}, "integration": {}, "e2e": {}}

    for group in tests_cfg:
        if group == "support":
            continue
        script_path = scripts_dir / f"test-{group}.sh"
        if not script_path.exists():
            tmpl = load_template("test-group", templates_dir=scripts_template_dir)
            rendered = render_test_group_script(tmpl, group)
            script_path.write_text(rendered, encoding="utf-8")
            os.chmod(script_path, 0o755)

    for sname in ("setup.sh", "teardown.sh", "build.sh"):
        script_path = scripts_dir / sname
        if not script_path.exists():
            tmpl = load_template(sname, templates_dir=scripts_template_dir)
            script_path.write_text(tmpl, encoding="utf-8")
            os.chmod(script_path, 0o755)

    # 7. Add .lintel/state/ to .gitignore
    gi_path = proj / ".gitignore"
    state_entry = ".lintel/state/"
    if not gi_path.is_file():
        gi_path.write_text(f"{state_entry}\n", encoding="utf-8")
    else:
        existing = gi_path.read_text(encoding="utf-8")
        lines = [line.strip() for line in existing.splitlines()]
        if state_entry not in lines and ".lintel/state" not in lines:
            if existing and not existing.endswith("\n"):
                existing += "\n"
            gi_path.write_text(f"{existing}{state_entry}\n", encoding="utf-8")

    # 8. Resolve and record Python interpreter with find_and_record_python
    if not global_install:
        state_dir = lintel_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        found_py = find_and_record_python(state_dir)
        if not found_py:
            sys.stderr.write(
                "lintel: no suitable Python >= 3.9 interpreter found.\n"
                "Please install Python via 'xcode-select --install' or 'brew install python'.\n"
            )
            raise RuntimeError("No suitable Python >= 3.9 interpreter found")

    # 9. Print next steps of PLAN §3.1 step 4
    print_init_next_steps()


def uninstall(repo_root: Union[str, Path]) -> None:
    """Uninstall the Lintel plugin from the project.
    Removes the vendored copy, agent files, and launcher. Leaves .lintel/ otherwise intact.
    """
    proj = Path(repo_root).resolve()
    
    # 1. Remove vendored copy
    dest_plugin = proj / ".agents" / "plugins" / "lintel"
    if dest_plugin.exists():
        shutil.rmtree(dest_plugin)

    # 2. Remove 10 agent files
    from lintel.self_check import ALL_10_ROLES
    agents_dir = proj / ".agents" / "agents"
    if agents_dir.exists():
        for role in ALL_10_ROLES:
            agent_file = agents_dir / f"lintel-{role}.md"
            if agent_file.exists():
                agent_file.unlink()

    # 3. Remove launcher
    launcher = proj / ".lintel" / "bin" / "lintel"
    if launcher.exists():
        launcher.unlink()


def upgrade(repo_root: Union[str, Path], plugin_source: Optional[Union[str, Path]] = None) -> None:
    """Upgrade the Lintel plugin to the current version.
    Replaces vendored copy, regenerates agent files and launcher.
    Lists script and persona changes.
    """
    proj = Path(repo_root).resolve()
    src = Path(plugin_source).resolve() if plugin_source is not None else DEFAULT_PLUGIN_DIR

    dest_plugin = proj / ".agents" / "plugins" / "lintel"
    if src == dest_plugin:
        raise InstallerRefusalError("Refused: run 'lintel upgrade' from the global installation, not the project launcher")

    from lintel.self_check import find_active_task_head, ALL_10_ROLES
    active = find_active_task_head(proj)
    if active:
        raise InstallerRefusalError("Refused: cannot upgrade while a task is active")

    if dest_plugin.exists():
        shutil.rmtree(dest_plugin)

    def _ignore_noise(path: str, names: List[str]) -> Set[str]:
        ignored: Set[str] = set()
        for name in names:
            if name in (".DS_Store", "__pycache__"):
                ignored.add(name)
            elif name.endswith(".pyc") or name.endswith(".swp") or name.endswith("~"):
                ignored.add(name)
        return ignored

    shutil.copytree(src, dest_plugin, ignore=_ignore_noise)

    version_src = src / "VERSION"
    if version_src.is_file():
        version_str = version_src.read_text(encoding="utf-8").strip()
    else:
        version_str = "0.1.0"
    (dest_plugin / "VERSION").write_text(f"{version_str}\n", encoding="utf-8")

    ensure_launcher_spelling_in_skills(dest_plugin / "skills")

    lintel_dir = proj / ".lintel"
    bin_dest = lintel_dir / "bin"
    bin_dest.mkdir(parents=True, exist_ok=True)
    launcher_dest = bin_dest / "lintel"
    launcher_src = src / "templates" / "bin" / "lintel"
    shutil.copy2(launcher_src, launcher_dest)
    os.chmod(launcher_dest, 0o755)

    import difflib
    import re
    agents_dest = proj / ".agents" / "agents"
    agents_dest.mkdir(parents=True, exist_ok=True)
    agent_tmpl_path = src / "templates" / "agents" / "persona.md"
    agent_tmpl = agent_tmpl_path.read_text(encoding="utf-8")

    for role in ALL_10_ROLES:
        agent_file = agents_dest / f"lintel-{role}.md"
        new_content = agent_tmpl.replace("<role>", role)
        if agent_file.exists():
            old_content = agent_file.read_text(encoding="utf-8")
            if old_content != new_content:
                diff = list(difflib.unified_diff(
                    old_content.splitlines(keepends=True),
                    new_content.splitlines(keepends=True),
                    fromfile=f"a/{agent_file.name}",
                    tofile=f"b/{agent_file.name}",
                ))
                print(f"agent file changed: {agent_file.name}")
                sys.stdout.writelines(diff)
        agent_file.write_text(new_content, encoding="utf-8")

    scripts_dir = proj / ".lintel" / "scripts"
    scripts_template_dir = src / "templates" / "scripts"
    if scripts_dir.exists() and scripts_template_dir.exists():
        for tfile in scripts_template_dir.glob("*"):
            if not tfile.is_file():
                continue
            if tfile.name == "test-group":
                for sfile in scripts_dir.glob("test-*.sh"):
                    if sfile.exists():
                        old_text = sfile.read_text(encoding="utf-8")
                        new_text = tfile.read_text(encoding="utf-8")
                        old_m = re.search(r"contract:\s*(\d+)", old_text)
                        new_m = re.search(r"contract:\s*(\d+)", new_text)
                        if old_m and new_m:
                            old_v = int(old_m.group(1))
                            new_v = int(new_m.group(1))
                            if new_v > old_v:
                                print(f"script-contract changed for {sfile.name}: {old_v} -> {new_v}")
            else:
                sfile = scripts_dir / tfile.name
                if sfile.exists():
                    old_text = sfile.read_text(encoding="utf-8")
                    new_text = tfile.read_text(encoding="utf-8")
                    old_m = re.search(r"contract:\s*(\d+)", old_text)
                    new_m = re.search(r"contract:\s*(\d+)", new_text)
                    if old_m and new_m:
                        old_v = int(old_m.group(1))
                        new_v = int(new_m.group(1))
                        if new_v > old_v:
                            print(f"script-contract changed for {tfile.name}: {old_v} -> {new_v}")

    personas_dir = proj / ".lintel" / "personas"
    personas_src = src / "personas"
    if personas_dir.exists() and personas_src.exists():
        for pfile in personas_src.glob("*.md"):
            sfile = personas_dir / pfile.name
            if sfile.exists():
                old_text = sfile.read_text(encoding="utf-8")
                new_text = pfile.read_text(encoding="utf-8")
                old_m = re.search(r"version:\s*(\d+)", old_text)
                new_m = re.search(r"version:\s*(\d+)", new_text)
                if old_m and new_m:
                    old_v = int(old_m.group(1))
                    new_v = int(new_m.group(1))
                    if new_v > old_v:
                        print(f"persona changed for {pfile.name}: {old_v} -> {new_v}")

    print("restart the agent session")

