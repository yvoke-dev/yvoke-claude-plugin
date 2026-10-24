---
name: lintel-specs
description: Bootstraps or updates living specifications in .lintel/specs/.
---

# Lintel Specs (`lintel-specs`)

Use this skill to bootstrap or incrementally update living functional specifications in `.lintel/specs/`. This skill runs interactively in a normal chat session (not as an automated Lintel task) and is designed to be **rerunnable** across the entire repository or for a single functional domain area.

---

## Core Principles

1. **Describes Behaviour, Never Test Cases**
   - Specifications document **what the system does** from a domain and contract perspective: inputs, outputs, state transitions, side effects, invariants, and error conditions.
   - Read source modules, documentation, and existing test suites to understand the system (test suites are a great source of truth for intended behaviour and edge cases), but **specs describe behaviour, NEVER test cases!**
   - Never list test function names, test suite file paths, assertion mechanics, or mock setups inside `.lintel/specs/`. Translate every test scenario you discover into a declarative statement of system behaviour.

2. **Interactive Three-Step Workflow (Propose → Confirm → Write)**
   - Never overwrite or generate specification files silently in bulk.
   - Always **propose** the domain areas and outline first, wait for the user to **confirm** (or adjust) the scope, and then **write** one area at a time.

3. **Explicit Uncertainty (`> unverified:`)**
   - Do not guess or hallucinate domain rules when the implementation is ambiguous, incomplete, or contradictory.
   - Mark any behaviour, invariant, or edge case that could not be verified from the codebase with a blockquote callout:
     `> unverified: <explanation of what is unclear and where it appears>`

4. **Idempotent and Rerunnable**
   - This workflow is **rerunnable** at any point—whether bootstrapping `.lintel/specs/` for the first time, adding a newly built domain area, or refreshing a single area (`<area>.md`) after code changes.
   - When rerunning on an existing `.lintel/specs/` directory, inspect existing specs first, preserve user-curated domain notes and resolved decisions, and only update sections that have drifted from the codebase or were requested by the user.

---

## Directory Structure of `.lintel/specs/`

Living specifications live under `.lintel/specs/` and consist of an index file plus one markdown file per functional domain area:

```text
.lintel/specs/
├── index.md          # High-level system overview, domain glossary, and links to area specs
├── auth.md           # Example area: authentication, session lifecycle, permissions
├── billing.md        # Example area: pricing rules, invoicing, payment state machine
└── notifications.md  # Example area: delivery channels, retry policies, rate limits
```

### `.lintel/specs/index.md`
Acts as the entry point for both humans and Lintel workflow agents:
- **System Purpose**: Concise summary of what the system or service does.
- **Domain Glossary**: Key ubiquitous language terms and core entities.
- **Specification Areas**: A linked table or list of all `<area>.md` files with a one-sentence description of each area's responsibility and boundaries.
- **Cross-Cutting Invariants**: System-wide guarantees (e.g., idempotency rules, tenancy isolation, audit logging requirements).

### `.lintel/specs/<area>.md`
Each domain area file should be cohesive and self-contained. Recommended sections:
1. **Overview & Scope**: What functional capability this area covers and where its boundary ends.
2. **Core Entities & State Model**: Key domain objects, lifecycle states, and valid state transitions.
3. **Observable Behaviour & Contracts**:
   - Commands / operations / endpoints and their preconditions.
   - Expected outcomes, return payloads, events emitted, and persistent state changes.
4. **Invariants & Business Rules**: Rules that must always hold true before, during, and after operations.
5. **Edge Cases & Error Conditions**: How the system handles invalid inputs, boundary values, concurrency conflicts, timeouts, or partial failures.
6. **Non-Goals & Out of Scope**: Capabilities intentionally excluded or delegated to another area.
7. **Open / Unverified Questions**: Any remaining `> unverified: <explanation>` items requiring human confirmation.

---

## Step-by-Step Workflow

### Step 1: Discover & Analyze
1. **Check Existing Specs**: Inspect `.lintel/specs/index.md` and any existing `.lintel/specs/<area>.md` files to understand current coverage and formatting conventions.
2. **Survey the Codebase**:
   - Read entry points, domain models, service layers, API handlers, CLI commands, and configuration schemas.
   - Read existing documentation and test suites to uncover subtle business rules, boundary conditions, and error paths. Remember: use test suites as evidence of behaviour, **never test cases** as spec content.
3. **Identify Gaps & Ambiguities**: Note any dead code, conflicting rules, or implicit assumptions that will need `> unverified:` markers.

### Step 2: Propose Areas and Outline
Before writing or modifying any files in `.lintel/specs/`:
1. **Propose** the specification plan to the user:
   - If bootstrapping from scratch: propose the list of domain areas (`index.md` plus specific `<area>.md` files such as `reports.md`, `auth.md`, etc.) with a brief summary of what each file will cover.
   - If updating a specific area or running incrementally: propose the specific additions, modifications, and newly discovered behaviours for the target `<area>.md` and `index.md`.
2. Highlight any major ambiguities discovered during analysis so the user can clarify them early if they wish.

### Step 3: Confirm with the User
1. Ask the user to **confirm** the proposed areas and structure before writing files.
2. Incorporate any adjustments (merging areas, splitting large domains, renaming files, or clarifying domain rules).

### Step 4: Write Specifications Area by Area
Once confirmed:
1. **Write** one area at a time to `.lintel/specs/<area>.md` and keep `.lintel/specs/index.md` synchronized.
2. For each area:
   - Express every rule as observable system behaviour, state invariants, edge cases, and non-goals.
   - Double-check that the text describes behaviour, **never test cases**, test file names, or testing framework constructs.
   - Mark any behaviour or edge case that could not be verified from the code or user input as:
     ```markdown
     > unverified: <explanation>
     ```
3. If multiple areas are being created or updated, present progress clearly as each `<area>.md` is written so the user can review incrementally.

### Step 5: Review & Commit Reminder
After writing `.lintel/specs/index.md` and the target `.lintel/specs/<area>.md` files:
1. Summarize the files created or updated and list any `> unverified:` markers that remain in the specs.
2. Remind the user to review the generated specifications, edit or resolve any `> unverified:` items, and commit `.lintel/specs/` to version control.
