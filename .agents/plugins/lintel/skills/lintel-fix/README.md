# Lintel Fix Skill (`lintel-fix`)

`lintel-fix` is the specialized Lintel skill for driving end-to-end **bug-fix tasks**. It adapts the standard Lintel multi-agent specification-driven workflow so that a user's bug report is systematically triaged, converted into testable behavioral requirements, locked behind a failing-then-passing regression test, and resolved at the root cause.

## When to Use

Invoke `/lintel-fix` (or activate the `lintel-fix` skill) whenever you need to fix a defect, regression, crash, or unexpected behavior in a Lintel-enabled repository:

- A user provides a bug report with error messages, stack traces, or unexpected output.
- A failing production scenario needs reproduction steps turned into a locked regression test before code changes are made.
- You want full traceability from the reported defect (`shared/requirements.md`) through root-cause analysis (`shared/plan.md`), locked regression tests (`shared/traceability.md`), and verified implementation (`deliver`).

## How `lintel-fix` Works

1. **Bug Report Triage**: Collects symptoms, stack traces, reproduction steps, and expected vs. actual behavior.
2. **Task Initialization**: Starts a tracked Lintel task via `./.lintel/bin/lintel task start "Fix: <bug summary>"`.
3. **Requirements (`po` ⇄ `po-reviewer`)**: Translates the bug report into formal, testable `### R<n>: <title>` requirements in `shared/requirements.md`, followed by human `./.lintel/bin/lintel approve requirements`.
4. **Root-Cause Plan (`architect` ⇄ `architect-reviewer`)**: Identifies the underlying defect and plans the fix in `shared/plan.md`, followed by human `./.lintel/bin/lintel approve plan`.
5. **Locked Regression Tests (`qa` ⇄ `qa-reviewer`)**: Authors automated regression tests that execute the reproduction steps (failing against the unpatched code at `./.lintel/bin/lintel lock` and mapped in `shared/traceability.md`).
6. **Fix & Verification (`dev` ⇄ `dev-reviewer`)**: Implements the production fix without altering locked tests, verifies all test tiers via `./.lintel/bin/lintel check`, and runs `./.lintel/bin/lintel deliver`.
7. **Documentation & Closure**: Updates specifications if needed (`docs` ⇄ `docs-reviewer`), passes human approvals (`approve final`, `approve docs`), and completes via `./.lintel/bin/lintel task finish`.
