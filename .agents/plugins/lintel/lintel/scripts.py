"""Project script templates and contract parsing (M1a-13).

Provides:
- load_template(name: str) -> str: loads script template from plugin/templates/scripts/.
- parse_script_header(content: str) -> Optional[Dict[str, Any]]: extracts script name
  and integer contract version from '# lintel-script: <name>   contract: <version>'.
- is_unadjusted_template(content: str) -> bool: detects unadjusted script template
  containing 'not configured yet'.
- render_test_group_script(template_content: str, group_name: str) -> str: renders
  test-<group>.sh for a named group from the test-group.sh template.
"""

from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union

DEFAULT_PLUGIN_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = DEFAULT_PLUGIN_DIR / "templates" / "scripts"

CURRENT_CONTRACT_VERSION = 1
SCRIPT_TEMPLATES: Tuple[str, ...] = (
    "test-group.sh",
    "build.sh",
    "setup.sh",
    "teardown.sh",
)

# Header regex pattern: matches `# lintel-script: <name>\s+contract:\s+(\d+)`
_HEADER_PATTERN = re.compile(
    r"^\s*#\s*lintel-script:\s*([^\s]+)\s+contract:\s*(\d+)",
    re.MULTILINE,
)


def get_templates_dir() -> Path:
    """Return the absolute path to plugin/templates/scripts/."""
    return TEMPLATES_DIR


def load_template(
    name: str,
    templates_dir: Optional[Union[str, Path]] = None,
) -> str:
    """Load a script template by name from templates/scripts/.

    The name can be supplied with or without the '.sh' extension
    (e.g., 'test-group' or 'test-group.sh').
    """
    base_dir = Path(templates_dir) if templates_dir is not None else TEMPLATES_DIR
    target = base_dir / name
    if not target.is_file() and not name.endswith(".sh"):
        target = base_dir / f"{name}.sh"
    if not target.is_file():
        raise FileNotFoundError(f"Template script '{name}' not found in {base_dir}")
    return target.read_text(encoding="utf-8")


def parse_script_header(content: str) -> Optional[Dict[str, Any]]:
    r"""Extract script name and integer contract version from script header.

    Matches `# lintel-script: <name>\s+contract:\s+(\d+)`.
    Returns dict with keys 'name', 'contract', 'script', 'version', or None if not matched.
    """
    match = _HEADER_PATTERN.search(content)
    if not match:
        return None
    name = match.group(1)
    contract = int(match.group(2))
    return {
        "name": name,
        "contract": contract,
        "script": name,
        "version": contract,
    }


def is_unadjusted_template(content: str) -> bool:
    """Return True if script content is unadjusted (contains 'not configured yet')."""
    return "not configured yet" in content


def render_test_group_script(template_content: str, group_name: str) -> str:
    """Render a test group script for a specific group from test-group.sh template.

    Replaces 'test-group' in header with 'test-<group_name>' and '<group>' in text/body
    with '<group_name>'.
    """
    rendered = template_content.replace("test-group", f"test-{group_name}")
    rendered = rendered.replace("<group>", group_name)
    return rendered


def list_script_templates(
    templates_dir: Optional[Union[str, Path]] = None,
) -> List[str]:
    """List available script template filenames in templates/scripts/."""
    base_dir = Path(templates_dir) if templates_dir is not None else TEMPLATES_DIR
    if not base_dir.is_dir():
        return []
    return sorted(
        p.name for p in base_dir.glob("*.sh") if p.is_file()
    )
