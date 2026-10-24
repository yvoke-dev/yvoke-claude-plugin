# Lintel Doctor Skill (`lintel-doctor`)

The `lintel-doctor` skill guides agents and users through running `./.lintel/bin/lintel doctor`, interpreting diagnostic reports, and remediating errors and warnings across a Lintel-enabled repository.

## Overview

`lintel doctor` inspects the repository, Lintel configuration, runtime dependencies, hooks, and Antigravity (`agy`) platform settings to ensure the QA -> DEV workflow can execute safely and deterministically.

## Usage

```sh
# Standard diagnostic check
./.lintel/bin/lintel doctor

# Inspect resolved skills and steering sources for a specific role
./.lintel/bin/lintel doctor --role dev

# Inspect path classification and test group assignment for a path
./.lintel/bin/lintel doctor --path src/main
```

When invoked programmatically with `full=True` (`--full`), the report also expands `Path Classes` and `Test Groups` to list all matching files under each category.

## Diagnostic Categories Covered

| Category | Severity | What It Validates |
| :--- | :--- | :--- |
| `config` | Error | `.lintel/config.jsonc` existence, JSONC syntax, and schema validity. |
| `overlap` (`config`) | Error | Ensures no file matches multiple test groups in `paths.tests`. |
| `python` | Error / Warning | Validates `.lintel/state/python` (Python >= 3.9); auto-resolves and records a working interpreter if missing or broken. |
| `git` | Error | Verifies `git` >= 2.25.0 is available on `PATH`. |
| `scripts` | Error / Warning | Checks `.lintel/scripts/test-<group>.sh` existence, warns on unadjusted templates (`test-<group>.sh`, `build.sh`, `setup.sh`, `teardown.sh`), and checks script contract version (`>= 1`). |
| `shim` / `hook` | Error / Note | Executes a live PreToolUse shim self-test (`run_shim_selftest`) and verifies `.lintel/state/hook-errors.log` is clean. |
| `suspect_tests` | Warning | Detects files classified as `code` that match test-naming heuristics (`find_suspect_tests`). |
| `agent_files` | Error / Note | Verifies `.agents/agents/lintel-<role>.md` for all 10 roles match the canonical template, have no forbidden frontmatter keys, and only use accepted tools. |
| `sandbox` | Error / Note | Verifies `enableTerminalSandbox` is `false` so `.git` is writable. |
| `permissions` | Warning / Note | Checks required `command(...)` allow rules, `toolPermission` mode (`accept-edits`), and `trustedWorkspaces`. |
| `platform` / `macos` | Warning / Note | Checks tested `agy` version (`1.2.16`) and advises against granting macOS Automation/Accessibility permissions to the host terminal/IDE. |
| `gui` | Warning | Checks for an active macOS Aqua GUI session required by human confirmation dialogs. |
| `skills` | Warning | Verifies all role-assigned skills exist and contain no literal test file paths. |
| `steering` | Error / Warning | Verifies mapped `.lintel/steering/` files exist and contain no literal test file paths. |
| `specs` | Warning | Checks `.lintel/specs/` files to ensure no literal test file paths are referenced. |
| `personas` | Warning | Warns when `.lintel/personas/<role>.md` is older than the plugin's canonical persona file. |
| `agy_readers` | Error / Note | Runs self-tests for `agy` state readers when `evidence_m08` fixtures are present. |

See [`SKILL.md`](./SKILL.md) for detailed, step-by-step instructions to remediate every warning and error.
