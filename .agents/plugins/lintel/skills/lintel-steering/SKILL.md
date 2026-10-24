---
name: lintel-steering
description: Bootstraps or updates project steering documentation in .lintel/steering/.
---

# Lintel Steering (`lintel-steering`)

Use this skill to bootstrap or refresh repository-wide steering documentation in `.lintel/steering/`. Steering documents capture durable, evidence-backed context about how the project is built, organized, styled, architected, and tested so every Lintel phase starts aligned with the codebase.

## Execution Context & Rerunnability

- **Normal chat session**: Run this skill directly in a normal interactive chat (not inside an automated Lintel task phase).
- **Rerunnable design**: This workflow is completely rerunnable—either across the entire `.lintel/steering/` suite when bootstrapping a repository, or scoped to a single steering file or subsystem when architecture, tooling, or conventions evolve.
- **Non-destructive updates**: When refreshing existing `.lintel/steering/*.md` files, read the current steering files first, preserve valid human-curated notes and resolved decisions, and only update sections that are outdated, incomplete, or newly evidenced in the repository.

---

## Target Steering Files & Templates

All steering files live in `.lintel/steering/<file>.md` and are structured after the canonical templates in `plugin/templates/steering/` (or `.lintel/templates/steering/` when vendored):

| Target File | Template Source | Required Sections & Focus |
| :--- | :--- | :--- |
| `.lintel/steering/tech.md` | `templates/steering/tech.md` | **Tech Stack**: `## Stack and Versions` (languages, runtimes, frameworks), `## Main Libraries` (core dependencies and their purpose), `## Build and Run` (exact build, lint, test, and run commands). |
| `.lintel/steering/structure.md` | `templates/steering/structure.md` | **Project Structure**: `## Directories and Modules` (top-level layout and module responsibilities), `## Where New Code Goes` (placement rules for new features, adapters, and shared utilities), `## Naming Conventions` (variables, classes/types, files, and modules). |
| `.lintel/steering/conventions.md` | `templates/steering/conventions.md` | **Conventions**: `## Code Style` (linters, formatters, typing rules), `## Error Handling` (exception hierarchy, error returns, recovery patterns), `## Logging` (levels, structured format, sinks), `## API Style` (CLI/REST/RPC/internal interface idioms and response shapes). |
| `.lintel/steering/architecture.md` | `templates/steering/architecture.md` | **Architecture**: `## Layers` (presentation/CLI, domain, data/storage boundaries and dependency direction), `## Patterns` (recurring design patterns and state flows), `## Key Decisions` (architectural choices and the rationale behind them). |
| `.lintel/steering/testing.md` | `templates/steering/testing.md` | **Testing**: `## Test Groups` (unit, integration, end-to-end categories in practice and how they are invoked), `## Fixtures and Test Data` (where shared fixtures live and how synthetic data is constructed), `## Helpers` (common assertion helpers, harnesses, and mocking patterns). |

---

## Step-by-Step Workflow: Propose -> Confirm -> Write

Never write or overwrite `.lintel/steering/*.md` files without walking through the **Propose -> Confirm -> Write** cycle with the user.

### Step 1: Inspect the Repository for Concrete Evidence

Before drafting anything, gather verifiable facts from the repository:

1. **Existing steering & agent guides**: Read `.lintel/steering/*.md` (if present), `AGENTS.md`, `CLAUDE.md`, `README.md`, and `CONTRIBUTING.md`.
2. **Build, package, and tool manifests**: Inspect language and build manifests (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `Makefile`, `justfile`, linter/formatter configs, and lockfiles) to verify exact language versions, libraries, and commands.
3. **CI and automation workflows**: Inspect `.github/workflows/`, CI pipelines, and pre-commit configurations to confirm the authoritative build, lint, typecheck, and test invocations.
4. **Directory layout and representative modules**: List top-level directories and sample representative source modules to verify layering, module boundaries, error handling, logging, and naming conventions in practice.
5. **Test suite organization**: Inspect the test directory layout, runner configuration, and shared fixture modules to understand test groups, fixture mechanisms, and helper utilities.

### Step 2: Propose Outlines and Key Findings

Present a concise proposal in chat covering the target `.lintel/steering/<file>.md` files (all five when bootstrapping, or the requested subset when refreshing):

- List each file you plan to create or update.
- Provide a structured outline per file summarizing the concrete findings you discovered and how they map to the template headings.
- Explicitly highlight any gaps, ambiguities, or inferred behaviors that could not be verified from repository artifacts and will be marked with `> unverified: <explanation>`.
- Ask the user to **confirm** or adjust the proposed outlines and resolve any open questions before writing files.

### Step 3: Confirm with the User

Wait for the user to confirm the proposed outlines (or incorporate their corrections and clarifications). Do not write to `.lintel/steering/` until confirmation is received.

### Step 4: Write `.lintel/steering/<file>.md`

Once confirmed, write the approved files to `.lintel/steering/<file>.md` following the structure in `templates/steering/`:

- Keep descriptions concise, factual, and grounded in repository evidence.
- Preserve all required section headings from the corresponding template in `templates/steering/`.
- Apply the uncertainty marker and `lintel doctor` content hygiene rules below.
- After writing the files, summarize what was written, list any remaining `> unverified:` markers, and remind the user to review, edit, and commit `.lintel/steering/`.

---

## Evidence & Uncertainty Rules (`> unverified:`)

Every statement in `.lintel/steering/` must be backed by concrete repository evidence or explicit user confirmation:

- **Never guess or fabricate**: If a build command, runtime version, architectural boundary, logging convention, or rationale cannot be confirmed from the repository or the user, do not state it as fact.
- **Use the exact marker format**: Prefix any unconfirmed claim, inferred rationale, or missing command with a blockquote on its own line:
  ```markdown
  > unverified: <clear explanation of what could not be confirmed and what evidence was checked>
  ```
- **Rerunnable resolution**: When re-running `lintel-steering` on an existing file, re-check existing `> unverified:` blocks against current repository evidence and user input—converting confirmed items into normal prose and keeping still-unconfirmed items marked.

---

## `lintel doctor` Content Hygiene (Zero Warnings)

`lintel doctor` scans `.lintel/steering/*.md` (and `.lintel/specs/*.md`) for brittle references that decay when test suites change. To ensure `lintel doctor` emits **zero content warnings**:

1. **Never reference specific test filenames or test file paths with extensions**:
   - Do **not** include literal paths or filenames pointing to individual test files in `testing.md`, `structure.md`, or any other steering file.
   - Instead, refer to **test directories** (such as `/tests` or unit/integration subdirectories), **test groups** (unit, integration, end-to-end), **runner flags or markers**, and **general naming conventions** described in prose.
2. **Describe durable patterns over brittle line numbers**:
   - Focus on module responsibilities, directory boundaries, shared fixture modules, and architectural invariants rather than ephemeral test case names or line numbers.
3. **Verify after writing**:
   - If `./.lintel/bin/lintel doctor` is available in the workspace, run it (or recommend running it) after writing steering files to verify that all `.lintel/steering/*.md` checks pass with zero warnings.
