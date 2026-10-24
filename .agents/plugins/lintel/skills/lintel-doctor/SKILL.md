---
name: lintel-doctor
description: Runs the Lintel doctor diagnostic tool, interprets every check category, and walks the user through step-by-step instructions to remediate all warnings and errors.
---

# Lintel Doctor (`lintel-doctor`)

Use this skill to run the Lintel doctor diagnostic check, interpret its report sections and issue categories, and remediate any reported errors, warnings, or environment misconfigurations.

---

## 1. Running Lintel Doctor

Run the doctor command from the repository root:

```sh
./.lintel/bin/lintel doctor
```

### Command Options & Modes

- **Standard diagnostic run (`./.lintel/bin/lintel doctor`)**:
  Validates project configuration, test group overlaps, steering mappings, path class counts, test group counts, Python interpreter status, Git version, script templates and contract headers, hook/shim execution (`run_shim_selftest`), suspect test files, `.agents/agents/lintel-<role>.md` integrity, platform settings (`agy`, sandbox, permissions, workspace trust, GUI session), `hook-errors.log`, `agy_readers` self-test, and content checks on skills, steering, specs, and personas.
- **Full file listing (`--full`)**:
  When invoked with `--full` (or `doctor(..., full=True)`), expands the `Path Classes` and `Test Groups` summary counts to list every individual repository file classified under each path class and test group.
- **Role inspection (`./.lintel/bin/lintel doctor --role <role>`)**:
  Inspects a specific Lintel persona role (`po`, `po-reviewer`, `architect`, `architect-reviewer`, `qa`, `qa-reviewer`, `dev`, `dev-reviewer`, `arbiter`, `orchestrator`). Prints the resolved `Skills` and `Steering` files for that role along with the configuration layer source where each entry was defined.
- **Path inspection (`./.lintel/bin/lintel doctor --path <path>`)**:
  Inspects a single relative path and prints its `Path Classification` (`Path`, `Class` such as `code`, `test`, `docs`, `build`, `lintel`, or `other`, and `Group` if it belongs to a configured test group).

---

## 2. Understanding the Doctor Report Structure

1. **Path Classification** (when `--path <path>` is passed): Shows the resolved base class and test group for the target path.
2. **Role Inspection** (when `--role <role>` is passed): Lists resolved skills and steering documents with their origin layers.
3. **Path Classes**: Counts (and with `--full`, file paths) for each path class across non-ignored repository files.
4. **Test Groups**: Counts (and with `--full`, file paths) for each configured test group in `.lintel/config.jsonc`.
5. **Errors (`[ERROR]`)**: Critical failures that cause `lintel doctor` to exit non-zero (`Result: doctor FAIL`). Must be remediated before starting or continuing Lintel tasks.
6. **Warnings (`[WARN]`)**: Non-fatal configuration, environment, or template issues that should be reviewed and fixed so workflows run reliably.
7. **Advisory Notes (`[NOTE]`)**: Informational confirmations (such as passing hook self-tests) or reminders when `agy` CLI introspection is unavailable.

---

## 3. Check Categories & Step-by-Step Remediation Guide

Walk the user through each reported issue by matching its category below and following the steps to remediate it.

### `config` — Configuration Validation
- **What it checks**: Verifies `.lintel/config.jsonc` exists, parses as valid JSONC, conforms to the Lintel configuration schema, and has valid path globs and role settings.
- **How to remediate**:
  1. If `.lintel/config.jsonc` is missing, initialize the project configuration by running `./.lintel/bin/lintel init` (requires human confirmation) or restore `.lintel/config.jsonc` from git (`git checkout -- .lintel/config.jsonc`).
  2. If a syntax or schema validation error is reported, open `.lintel/config.jsonc`, fix any malformed JSONC syntax, unknown keys, or invalid types, and re-run `./.lintel/bin/lintel doctor`.
  3. If a task is currently active and configuration changes are needed, use `./.lintel/bin/lintel amend --config` after editing.

### `overlap` (reported under `config`) — Test Group Overlaps
- **What it checks**: Uses `detect_test_group_overlaps` to ensure no repository file matches more than one test group glob in `paths.tests` within `.lintel/config.jsonc`. Every test file must belong to at most one test group (excluding `support`).
- **How to remediate**:
  1. Inspect the conflicting file paths and the list of matching test groups reported in the `Test group overlap` error message.
  2. Open `.lintel/config.jsonc` and locate `paths.tests`.
  3. Refine the glob patterns or add negative exclusion patterns (`!pattern`) so that each test group covers a mutually exclusive set of files.
  4. Verify the fix with `./.lintel/bin/lintel doctor --path <conflicting-path>` and `./.lintel/bin/lintel doctor`.

### `python` — Python Interpreter Check
- **What it checks**: Validates the recorded Python interpreter in `.lintel/state/python`, ensuring it exists, is executable, and passes the Python >= 3.9 feature check snippet. If broken or missing, `doctor` automatically searches for a valid interpreter (`find_and_record_python`) and updates `.lintel/state/python`.
- **How to remediate**:
  1. **Warning (`Python interpreter was missing or broken; re-resolved and recorded...`)**: Doctor has already self-healed `.lintel/state/python`. If `.lintel/state/hook-errors.log` also recorded stale interpreter errors prior to this rewrite, clear `.lintel/state/hook-errors.log` and re-run `./.lintel/bin/lintel doctor`.
  2. **Error (`No valid Python >= 3.9 interpreter found`)**: Install Python 3.9 or newer on the system (for example via `xcode-select --install` or `brew install python` on macOS), verify `python3 --version` is >= 3.9, and re-run `./.lintel/bin/lintel doctor` so it records the new binary path in `.lintel/state/python`.

### `git` — Git Version Check
- **What it checks**: Verifies `git` is installed on `PATH` and its version is at least `2.25.0`.
- **How to remediate**:
  1. Run `git --version` to check the active Git binary.
  2. Upgrade Git to `>= 2.25.0` (e.g., `brew install git` on macOS or via your OS package manager) and ensure the updated binary precedes older system binaries on `PATH`.

### `scripts` — Test & Build Script Checks
- **What it checks**:
  - Every test group configured in `paths.tests` (except `support`) has a corresponding `.lintel/scripts/test-<group>.sh` file.
  - Checks whether `.lintel/scripts/test-<group>.sh` and `.lintel/scripts/build.sh` are still unadjusted starter templates (`is_unadjusted_template`) or use an outdated contract header version (`contract < 1`).
- **How to remediate**:
  1. **Missing `.lintel/scripts/test-<group>.sh`**: Run `./.lintel/bin/lintel init --scripts` to generate missing script templates without overwriting existing scripts, or create `.lintel/scripts/test-<group>.sh` with `chmod +x`.
  2. **Unadjusted template warning (`not configured yet`)**: Edit `.lintel/scripts/test-<group>.sh`, `.lintel/scripts/build.sh`, `.lintel/scripts/setup.sh`, or `.lintel/scripts/teardown.sh` to invoke the project's actual build and test runner commands, remove the unconfigured template marker, and ensure the script is executable (`chmod +x`).
  3. **Older contract version warning**: Update the `# lintel-script-contract: 1` header comment at the top of the script and verify the script adheres to contract version 1 exit code and output semantics.

### `shim` & `hook` — PreToolUse Hook & Shim Self-Test
- **What it checks**:
  - `run_shim_selftest` extracts the `PreToolUse` hook command from `hooks.json`, sends a synthetic `run_command` payload (`lintel doctor`) with `conversationId: "doctor-shim-selftest"`, and verifies that the shim exits `0`, produces empty `stdout` (allowing the tool call), and appends an audit entry to `.lintel/state/audit.jsonl`.
  - Also checks `.lintel/state/hook-errors.log` for any previously logged hook failures.
- **How to remediate**:
  1. **Missing or unreadable `hooks.json` / `hook.sh`**: Ensure `.agents/plugins/lintel/hooks.json` and `.agents/plugins/lintel/lintel/hook.sh` exist, are intact, and have executable permissions (`chmod +x .agents/plugins/lintel/lintel/hook.sh`). If corrupted, run `./.lintel/bin/lintel upgrade` or restore from git.
  2. **Shim self-test failed (non-zero exit, unexpected stdout, or no audit line logged)**: Inspect the error message and read `.lintel/state/hook-errors.log` to see the underlying failure (e.g., broken `.lintel/state/python`, permission error on `.lintel/state/audit.jsonl`, or modified plugin files).
  3. **`hook-errors.log contains N error line(s)`**: After fixing the root cause (such as doctor rewriting `.lintel/state/python`), inspect `.lintel/state/hook-errors.log` to confirm no new errors are occurring, then remove or truncate `.lintel/state/hook-errors.log` (`rm .lintel/state/hook-errors.log` or `: > .lintel/state/hook-errors.log`) and re-run `./.lintel/bin/lintel doctor`.

### `suspect_tests` — Suspect Test Files Classified as Code
- **What it checks**: Scans repository files classified as `code` against test-naming heuristics (`find_suspect_tests`). Warns when files that look like tests are classified as `code` instead of `test` (which would allow DEV to edit them or bypass test locking).
- **How to remediate**:
  1. Review the list of suspect files reported by the warning.
  2. If the files are indeed tests or test helpers, update `paths.tests` in `.lintel/config.jsonc` so their directory or naming pattern is mapped to the appropriate test group (or `support` group).
  3. Confirm the updated classification by running `./.lintel/bin/lintel doctor --path <suspect-path>`.

### `agent_files` — Agent Persona Wrapper Files
- **What it checks**: Verifies that all 10 role files exist at `.agents/agents/lintel-<role>.md`, match the canonical `templates/agents/persona.md` template exactly, contain no forbidden frontmatter keys (`model:`, `commandExecutionPolicy:`, `inheritCustomizations:`, `hooks:`), and only declare allowed tools (`view_file`, `grep_search`, `find_by_name`, `list_dir`, `run_command`, `write_to_file`, `replace_file_content`, `send_message`).
- **How to remediate**:
  1. If any `.agents/agents/lintel-<role>.md` file is missing, edited, or contains forbidden frontmatter keys or tools, restore it from git (`git checkout -- .agents/agents/`) or re-run `./.lintel/bin/lintel init` / `./.lintel/bin/lintel upgrade` to regenerate canonical agent files from the template.
  2. Never add `model:`, `commandExecutionPolicy:`, `inheritCustomizations:`, or `hooks:` to `.agents/agents/lintel-<role>.md`—those keys cause `invoke_subagent` to fail with `"not found or not allowed"`. Customize persona behavior via `.lintel/personas/<role>.md` or `.lintel/steering/` instead.

### `sandbox` — Terminal Sandbox Configuration
- **What it checks**: Inspects platform settings to ensure `enableTerminalSandbox` is `false`. When the terminal sandbox is enabled (`enableTerminalSandbox: true`), `.git` is mounted read-only and Lintel git/build operations fail.
- **How to remediate**:
  1. Disable the terminal sandbox in your Antigravity (`agy`) configuration so `enableTerminalSandbox` is set to `false` (run `/config` in `agy` or update user settings).
  2. Re-run `./.lintel/bin/lintel doctor` to confirm the sandbox error is resolved.

### `permissions` — Allow Rules, Tool Permission Mode & Workspace Trust
- **What it checks**:
  - Required command allow rules are configured: `command(./.lintel/bin/lintel)`, `command(./.lintel/scripts/build.sh)`, `command(git status)`, `command(git diff)`, `command(git log)`, `command(git show)` (and `command(./mvnw)` when Maven is detected).
  - `toolPermission` mode is not set to `request-review` or `plan` (`accept-edits` with allow rules is recommended).
  - The project directory is included in `trustedWorkspaces`.
- **How to remediate**:
  1. **Missing allow rule**: Add each missing `command(...)` allow rule via `/permissions` in `agy` or in your Antigravity permissions configuration so subagents can run Lintel and read-only Git commands without blocking.
  2. **Tool permission mode warning**: Switch `toolPermission` from `request-review` or `plan` to `accept-edits`.
  3. **Untrusted workspace warning**: Add the repository root path to `trustedWorkspaces` in your Antigravity settings.

### `platform` & `macos` — Platform Version & macOS Advisories
- **What it checks**: Checks installed `agy` CLI version against tested version `1.2.16` and emits the macOS security advisory regarding Automation/Accessibility permissions.
- **How to remediate**:
  1. If `agy` is not on `PATH`, add the `agy` CLI binary directory to `PATH` so `lintel doctor` can automatically verify `/config` and `/permissions`.
  2. If a different `agy` version is reported, verify compatibility or switch to `1.2.16` if you encounter platform-specific subagent issues.
  3. On macOS, ensure your terminal or IDE host application (Ghostty, Terminal, VS Code / Antigravity IDE) is **not** granted macOS System Settings -> Privacy & Security -> Automation or Accessibility permissions, preventing agents from scripting confirmation dialogs.

### `gui` — macOS Aqua GUI Session Check
- **What it checks**: Checks whether the current process is running inside a macOS Aqua GUI session (`launchctl managername == "Aqua"` and not over headless SSH). Human-only Lintel commands require a native GUI confirmation dialog and fail closed without Aqua.
- **How to remediate**:
  1. Run human-only commands (`install`, `init`, `upgrade`, `uninstall`, `approve`, `lock`, `amend`, `decide`, `reset`, `task finish`) from a local terminal or IDE session running directly in the macOS desktop (Aqua) environment rather than an SSH or headless background session.

### `skills` — Assigned Role Skills & Skill Content Checks
- **What it checks**:
  - Verifies every skill assigned to any of the 10 roles in `.lintel/config.jsonc` resolves to an existing `SKILL.md` file in `.agents/skills/<skill>/SKILL.md` or the plugin skills directory.
  - Scans skill files to ensure they do not reference specific repository test file paths (which causes test-path leakage warnings).
- **How to remediate**:
  1. **Missing assigned skill warning**: Either create the missing skill at `.agents/skills/<skill-name>/SKILL.md` or remove/fix the typo in the `skills` mapping in `.lintel/config.jsonc`. Inspect role mappings with `./.lintel/bin/lintel doctor --role <role>`.
  2. **Specific test file reference warning**: Edit the flagged skill file to replace concrete test file names (anything with a file extension matching a test group) with general test directory names or placeholders without test file extensions.

### `steering` — Role Steering Files & Content Checks
- **What it checks**:
  - Verifies every steering file mapped to a role in `.lintel/config.jsonc` exists under `.lintel/steering/<file>`.
  - Scans `.lintel/steering/` files to ensure no concrete test file paths are referenced.
- **How to remediate**:
  1. **Mapped steering file does not exist**: Create the missing markdown file under `.lintel/steering/<name>` or remove the stale entry from `steering` in `.lintel/config.jsonc`.
  2. **Specific test file reference warning**: Edit the flagged file in `.lintel/steering/` and replace specific test file names with directory names or abstract descriptions so DEV agents do not receive test file hints.

### `specs` — Specification Content Checks
- **What it checks**: Scans all files in `.lintel/specs/` to ensure they do not reference specific test file paths matching configured test groups.
- **How to remediate**:
  1. Open the flagged file under `.lintel/specs/` and replace any literal test file path with a general directory reference or requirement description.

### `personas` — Persona Override Freshness
- **What it checks**: Compares modification timestamps of project-level persona overrides in `.lintel/personas/<role>.md` against the plugin's canonical `personas/<role>.md`. Warns if the project copy is older than the plugin copy.
- **How to remediate**:
  1. Diff `.lintel/personas/<role>.md` against `.agents/plugins/lintel/personas/<role>.md`.
  2. Merge any upstream persona updates into `.lintel/personas/<role>.md` (or touch/update the file after reviewing), or remove the project override if custom modifications are no longer needed.

### `agy_readers` — Agy State Readers Self-Test
- **What it checks**: Runs `run_agy_readers_selftest` against `evidence_m08` fixtures when present in the repository to verify `inspect_agy_state` audit log and hints parsing.
- **How to remediate**:
  1. If an `agy_readers` error occurs in the Lintel repository, inspect the corrupted/missing fixture manifest under `tests/fixtures/evidence_m08` or fix `lintel/agy_readers.py` so observed status and stop cause match expected fixture values.

---

## 4. Verification Workflow

1. Run `./.lintel/bin/lintel doctor` (and `--role <role>` / `--path <path>` as needed).
2. Group reported issues into **Errors** (blocking) and **Warnings** (important hygiene).
3. Explain each finding clearly to the user and remediate the issues step by step.
4. Re-run `./.lintel/bin/lintel doctor` after applying fixes and confirm with the user that `Result: doctor OK (all critical checks passed)` is achieved.
