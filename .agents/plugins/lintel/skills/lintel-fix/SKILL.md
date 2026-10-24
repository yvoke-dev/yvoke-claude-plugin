---
name: lintel-fix
description: Orchestrates a Lintel bug-fix lifecycle, triaging a bug report into testable requirements and regression tests through delivery.
---

# Lintel Fix Lifecycle (`lintel-fix`)

You are the main-agent orchestrator driving a structured **Lintel bug-fix task** from an initial user bug report to a verified, regression-tested delivery.

## 1. Triage the Bug Report

Before starting the task, gather or synthesize a clear **bug report** from the user's input and codebase context:

- **Symptoms & Stack Traces**: Capture error messages, exception types, logs, or failing command outputs.
- **Reproduction Steps**: Identify the exact sequence of inputs, flags, state, or calls that trigger the defect. If critical reproduction steps are missing and cannot be inferred from the codebase, ask the user before or during the PO phase.
- **Expected vs. Actual Behavior**: Contrast what the system currently does (actual defective behavior) against what the specification or user contract requires (expected behavior).
- **Affected Scope**: Note the subsystem, API boundary, or component involved without pre-committing to an implementation hack before the PO and Architect stages run.

## 2. Start the Bug-Fix Task

Initialize the Lintel task workspace with a concise `"Fix: <bug summary>"` goal that embeds the triaged bug report context:

```text
./.lintel/bin/lintel task start "Fix: <bug summary>"
```

- `task start` creates the isolated task workspace, initializes `state.json`, and prints a `Next action:` line.
- From this point onward, **follow the CLI's printed `Next action:` line verbatim** at every step.

## 3. How Each Phase Handles a Bug Fix

### Phase 1: Product Owner (`po` ⇄ `po-reviewer`) -> `approve requirements`
- **Role**: The PO reads the bug report (symptoms, reproduction steps, logs, expected vs. actual behavior), existing domain specifications (`specs/`), and project steering (`steering/`).
- **Artifact (`shared/requirements.md`)**: The PO translates the defect into clear, positive, testable behavioral requirements in `shared/requirements.md`, formatted with one `### R<n>: <title>` heading per requirement:
  - **Root-Cause Behavioral Contract**: State the exact invariant or behavior that must hold when the reproduction steps are executed.
  - **Edge Cases & Boundary Conditions**: Cover adjacent inputs or states exposed by the bug report so the fix is comprehensive rather than a narrow band-aid.
  - **Non-Regression Guarantees**: Specify existing behaviors that must remain intact.
- **Human Gate**: Once `po-reviewer` approves, tell the user to run:
  ```text
  ./.lintel/bin/lintel approve requirements
  ```

### Phase 2: Architect (`architect` ⇄ `architect-reviewer`) -> `approve plan`
- **Role**: The Architect locates the root cause in the production codebase, designs the minimal sound fix in `shared/plan.md`, and prepares any minimal structural stubs if new symbols are introduced.
- **Root-Cause Analysis**: `shared/plan.md` documents why the defect occurs, which modules/functions are responsible, and how each `### R<n>: <title>` requirement will be satisfied and verified.
- **Human Gate**: Once `architect-reviewer` approves, tell the user to run:
  ```text
  ./.lintel/bin/lintel approve plan
  ```

### Phase 3: QA (`qa` ⇄ `qa-reviewer`) -> `lock`
- **Role**: QA authors automated regression tests inside the repository's configured test directories (as declared in `.lintel/config.yaml`) and maps every requirement in `shared/requirements.md` (`### R<n>: <title>`) to test cases in `shared/traceability.md`.
- **Regression Test Discipline**:
  - The new regression tests execute the **reproduction steps** from the bug report and assert the **expected behavior**.
  - Because DEV has not applied the production fix yet, the new regression tests **must fail at `lock` time** against the unpatched production code (confirming the test genuinely reproduces the bug), while the project still compiles/collects cleanly.
- **Human Gate**: Once `qa-reviewer` approves the regression suite and traceability matrix, tell the user to run:
  ```text
  ./.lintel/bin/lintel lock
  ```
  `lock` records SHA-256 hashes of all QA test files and `shared/traceability.md` so DEV cannot weaken or delete the regression test.

### Phase 4: Developer (`dev` ⇄ `dev-reviewer`) -> `deliver`
- **Role**: DEV implements the root-cause fix in production code (without modifying locked QA tests) and runs `./.lintel/bin/lintel check` until all unit, integration, and e2e test suites—including the locked regression tests—pass and traceability/drift/audit checks are clean.
- **Drift & Audit Gates**:
  - If DEV genuinely needs a locked test adjusted, DEV must request `revisit` back to QA/Architect/PO rather than editing locked tests directly, or the user must explicitly acknowledge drift via `./.lintel/bin/lintel drift --ack "<reason>"`.
  - If static test-integrity audit flags require human sign-off, ask the user to run `./.lintel/bin/lintel audit --ack "<reason>"`.
- **Delivery**: Once `dev-reviewer` approves, run:
  ```text
  ./.lintel/bin/lintel deliver
  ```

### Phase 5: Final Review, Docs, and Finish (`approve final` -> `approve docs` -> `task finish`)
- If `deliver` enters `final_review`, ask the user to run `./.lintel/bin/lintel approve final`.
- Invoke `docs` ⇄ `docs-reviewer` to update `specs/` or user documentation if the bug fix clarified or corrected documented behavior, then ask the user to run `./.lintel/bin/lintel approve docs`.
- Finally, tell the user to close out the task by running:
  ```text
  ./.lintel/bin/lintel task finish
  ```

## 4. Orchestrator Rules & Invariants

1. **Follow `Next action:` Strictly**: Never inspect `.agents/plugins/lintel/` source files or run `--help` to guess state transitions. Always execute or relay the exact command printed after `Next action:`.
2. **Every CLI Call is Bare `./.lintel/bin/lintel`**: Do not wrap calls in `python`, `poetry`, `uv`, or `cd`.
3. **Subagent Lifecycle (`invoke` -> `task wait` -> `task status`)**:
   - After `./.lintel/bin/lintel invoke <role>`, launch the subagent with its generated prompt file, then run `./.lintel/bin/lintel task wait` followed by `./.lintel/bin/lintel task status` (or end your turn when waiting asynchronously—never poll in a tight loop).
4. **Relaying `needs-input` Questions**:
   - When `task status` reports open `needs-input` questions, ask the user with `ask_question`.
   - Fall back to printing questions as plain chat text if `ask_question` is unavailable, returns `skipped`, returns an empty answer, or times out.
   - Record every user answer verbatim via:
     ```text
     ./.lintel/bin/lintel answer <role> <question-id> "<answer text>"
     ```
5. **Respect Human Gates**: Never run human-only commands (`approve requirements`, `approve plan`, `lock`, `drift --ack`, `audit --ack`, `approve final`, `approve docs`, `task finish`, `revisit`) on the user's behalf. Print the exact command and wait for the user.
