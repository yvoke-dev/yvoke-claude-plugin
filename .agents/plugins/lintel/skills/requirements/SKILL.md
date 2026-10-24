---
name: requirements
description: Authoritative methodology for writing, structuring, and reviewing behavioural requirements in Lintel using INVEST criteria, EARS syntax, boundary analysis, and strict lifecycle markers.
---

# Requirements Engineering Methodology

This skill defines the standard methodology for authoring, maintaining, and reviewing requirements in `shared/requirements.md` and `.lintel/specs/`. It applies to both the **Product Owner (PO)** when specifying system behaviour and the **PO Reviewer** when auditing requirements for completeness, testability, and conformance.

---

## 1. Core Principles

1. **Behavioural Focus (What, Not How)**: Specify externally observable system behaviour, domain rules, state transitions, input/output contracts, and failure semantics. Do not prescribe internal class hierarchies, database schemas, algorithm choices, or implementation mechanics.
2. **No Phase Decomposition**: The PO defines *what* the system must do in its target state. Breaking delivery into implementation phases is strictly the Architect's responsibility.
3. **Strict Stack Neutrality**: Express requirements in domain-level, technology-agnostic language. Never embed language-specific keywords, build tools, test runner names, or framework internals unless mandated by `shared/request.md`.
4. **Traceability to Living Specs**: Every requirement set must align with or explicitly evolve the project's living specifications in `.lintel/specs/` and respect `.lintel/steering/`.

---

## 2. Standard Structure of `shared/requirements.md`

Organize `shared/requirements.md` into clear, predictable sections:

1. **Context & Intent**: A concise summary of the problem being solved and the actors or callers affected, grounded in `shared/request.md`.
2. **Assumptions & Decisions** *(if applicable)*: Explicit, low-risk domain assumptions made where the request was silent but existing conventions or specs provided clear guidance.
3. **Requirements (`### R<n>: <title>`)**: The numbered behavioural requirements, each with EARS rules, acceptance criteria, and boundary conditions.
4. **Not Supported / Out of Scope**: Explicit negative boundaries stating what this change does *not* address.

---

## 3. Requirement Headings & Lifecycle Markers

### 3.1 Heading Format (`### R<n>: <title>`)
Every distinct requirement must use a level-3 Markdown heading with a sequential integer identifier:

```markdown
### R1: Reject Expired Discount Codes at Checkout
```

- **Level-3 Headings Reserved for Requirements**: The CLI validator (`validate_requirements_doc`) requires *every* `### ` heading in `shared/requirements.md` to match `### R<n>: <title>`. Never use `### ` headings for non-requirement sections or subsections (such as under `## Out of Scope`); use `## `, `#### `, or bold text (`**...**`) instead.
- **ID Stability**: Once assigned, a requirement ID (`R1`, `R2`, `R3`, ...) is permanent for the lifetime of the work item. Never renumber existing requirements when adding or removing items during review cycles, revisits, or reroutes. Append new requirements using the next unused integer.

### 3.2 Withdrawn Requirements (`Status: withdrawn — <reason>`)
If a requirement becomes obsolete, is descoped during a revisit, or is invalidated by user feedback or reviewer findings, **do not delete the heading or reuse the ID**. Keep the `### R<n>: <title>` heading intact and place the exact withdrawal marker immediately below it:

```markdown
### R4: Automatic Currency Conversion for International Orders
Status: withdrawn — Deferred to a separate billing initiative per user clarification.
```

### 3.3 Refactoring & Unchanged Behaviour (`Behaviour: unchanged`)
When a request involves internal restructuring, code cleanup, or performance optimization where externally observable behaviour must remain identical, explicitly declare `Behaviour: unchanged`:

```markdown
### R1: Preserve Order Total Calculation Semantics During Pricing Engine Refactor
Behaviour: unchanged

- All existing rounding rules, tax application order, and discount stacking priorities shall produce identical outputs for all valid and invalid inputs.
- Error responses and rejection conditions for malformed line items shall remain identical.
```

- **PO Reviewer Check**: Whenever `Behaviour: unchanged` is claimed, verify against `shared/request.md` and `.lintel/specs/` that the request genuinely requires zero behavioural changes and that observable invariants to protect are clearly identified.

---

## 4. INVEST Quality Criteria

Every active requirement block (`### R<n>: <title>`) must satisfy the **INVEST** criteria:

| Criterion | Requirement Standard | Common Anti-Pattern to Reject |
| :--- | :--- | :--- |
| **I — Independent** | Self-contained behavioural rule that can be understood and evaluated without circular dependencies on other requirement blocks. | Overlapping `R<n>` blocks that contradict or partially redefine the same rule. |
| **N — Negotiable** | States the required outcome, domain constraints, and observable contract while leaving internal design choices open to the Architect. | Dictating internal data structures, private helper methods, or execution phases. |
| **V — Valuable** | Delivers clear, observable value to a user, external caller, or system invariant directly traceable to `shared/request.md`. | Speculative "nice-to-have" features not grounded in the request. |
| **E — Estimable** | Concrete and bounded enough that the Architect and Developer can assess scope without guessing hidden business rules. | Vague qualifiers such as "fast", "user-friendly", "robust", or "handle errors gracefully". |
| **S — Small** | Covers a single cohesive behavioural concern. Compound rules must be split into separate `### R<n>` items. | Monolithic requirements combining happy path, unrelated administrative workflows, and reporting in one block. |
| **T — Testable** | Falsifiable. Defines deterministic inputs, preconditions, and observable outputs so QA can write automated checks that definitively pass or fail. | Unverifiable statements lacking concrete pass/fail criteria. |

---

## 5. Writing Unambiguous Rules with EARS & Acceptance Criteria

Combine **EARS (Easy Approach to Requirements Syntax)** for normative rules with **Given / When / Then** scenarios for concrete boundary verification.

### 5.1 EARS Rule Patterns

Use these five structured sentence templates to eliminate ambiguity:

1. **Ubiquitous (Always Active Invariant)**
   - *Template*: `The <system/component> shall <response>.`
   - *Example*: `The billing service shall record all monetary amounts in integer minor units (cents).`
2. **Event-Driven (Triggered by a Discrete Input or Action)**
   - *Template*: `When <trigger event>, the <system/component> shall <response>.`
   - *Example*: `When a caller submits a reservation request with a valid seat identifier, the booking service shall lock the seat for 15 minutes and return a reservation token.`
3. **State-Driven (Active While a Condition Holds)**
   - *Template*: `While <in specific state>, the <system/component> shall <response>.`
   - *Example*: `While an account is in the suspended state, the authentication service shall reject new session creation requests.`
4. **Unwanted Behaviour (Error Conditions, Invalid Inputs, Failures)**
   - *Template*: `If <unwanted condition or invalid input>, then the <system/component> shall <error response and state guarantee>.`
   - *Example*: `If the requested withdrawal amount exceeds the available balance, then the ledger service shall reject the transaction with an insufficient-funds error and leave the account balance unmodified.`
5. **Optional / Configuration-Scoped**
   - *Template*: `Where <optional capability or configuration is enabled>, the <system/component> shall <response>.`
   - *Example*: `Where strict audit logging is enabled, the access controller shall emit an audit record for every read operation.`

### 5.2 Acceptance Scenarios (Given / When / Then)
Under each `### R<n>: <title>` block, accompany normative rules with concrete scenarios covering happy paths, boundaries, and error cases:

```markdown
### R2: Enforce Maximum Batch Size on Bulk Import

When a bulk import payload is submitted, the import processor shall accept batches containing between 1 and 500 records inclusive.
If a batch contains 0 records or more than 500 records, then the import processor shall reject the entire batch before persisting any records.

**Acceptance Criteria:**
- **Given** a valid batch of 1 record or 500 records, **When** submitted for import, **Then** all records are persisted and a success summary is returned.
- **Given** an empty batch (0 records), **When** submitted for import, **Then** the request is rejected with an empty-batch validation error and no state changes occur.
- **Given** a batch of 501 records, **When** submitted for import, **Then** the entire batch is rejected with a batch-limit-exceeded error and 0 records are persisted.
```

---

## 6. Boundary & Edge-Case Analysis Checklist

Before finalizing `shared/requirements.md`, systematically evaluate every requirement against these boundary dimensions:

1. **Cardinality & Collection Bounds**:
   - Empty input (`0` items), single item (`1`), typical volume, exact maximum limit (`max`), and one past the maximum (`max + 1`).
2. **Scalar & Numeric Limits**:
   - Zero (`0`), negative values, minimum allowed value (`min`), `min - 1`, `max`, `max + 1`, precision/rounding rules, and overflow/underflow limits.
3. **Strings & Text Payloads**:
   - Missing/null vs. empty string (`""`) vs. whitespace-only (`"   "`), maximum length bounds, leading/trailing whitespace normalization, case sensitivity, and special/Unicode characters.
4. **Temporal & Ordering Edge Cases**:
   - Expired vs. exact expiration boundary (`now == expires_at`), future-dated timestamps, out-of-order delivery, and duplicate/idempotent retries of the same request.
5. **State & Concurrency**:
   - Operations attempted in an invalid lifecycle state, concurrent conflicting mutations, and partial failure atomicity (whether an operation is all-or-nothing or supports partial success).
6. **Failure & Rejection Semantics**:
   - Every invalid input or failure condition must specify:
     1. The exact trigger condition.
     2. The observable error category, status, or message contract returned to the caller.
     3. The state invariant (e.g., no partial writes, existing state preserved).

---

## 7. Explicit "Not Supported / Out of Scope" Boundaries

Unbounded requirements invite scope creep during architecture and ambiguity during QA verification. Every `shared/requirements.md` must include an explicit **Not Supported / Out of Scope** section that distinguishes between:

1. **Rejected Inputs (Active Enforcement)**: Inputs or operations that the system must actively detect and reject with a defined error (these should also be cross-referenced in the relevant `R<n>` error rules).
2. **Deferred Capabilities (Non-Goals)**: Adjacent features or extensions that are intentionally excluded from this work item so the Architect, Developer, and QA do not build or test for them.
3. **Untouched Subsystems**: Existing workflows or contracts that must remain unaffected by the change.

---

## 8. Raising `questions.md` (`needs-input`) vs. Documenting Assumptions

When `shared/request.md` contains gaps, the PO must decide whether to pause for human input or proceed with a documented assumption.

### 8.1 When to Finish as `needs-input` (`questions.md`)
Pause and write `questions.md` in your run folder (then finish with `needs-input`) **only** when:
- Core business intent is ambiguous and competing interpretations lead to incompatible product behaviour.
- `shared/request.md` directly contradicts existing living specs in `.lintel/specs/` or project steering without stating an intent to override them.
- A high-impact policy decision is missing (e.g., destructive data deletion rules, security/authorization boundaries, or breaking external contract changes).

**Format for `questions.md`**:
- Keep questions brief, numbered, and decision-oriented.
- For each question, state the ambiguity, present 2–3 concrete options with their behavioural trade-offs, and indicate a recommended option if one is clearest.

### 8.2 When to Make a Documented Assumption
Do **not** block on `needs-input` for minor details that can be inferred from:
- Existing domain conventions in `.lintel/specs/`, `.lintel/steering/`, or established codebase behaviour.
- Standard, low-risk defaults (e.g., standard validation rejection on empty required identifiers).
- Record these choices under an **Assumptions** section in `shared/requirements.md` so the PO Reviewer and user have full visibility.

---

## 9. Revisits, Reroutes, and Living Specs Updates

1. **During Revisits and Reroutes**:
   - Inspect delivered changes (`lintel diff --delivered N`) and any build-side reports (`report.md`, `findings.json`).
   - Resolve deferred requirement defects or newly uncovered edge cases by updating the relevant `### R<n>: <title>` blocks, adding new `### R<n>` items, or marking invalidated requirements with `Status: withdrawn — <reason>`.
2. **During the Living Specs Update Step (`.lintel/specs/`)**:
   - Merge finalized, approved requirements into the permanent domain specification files under `.lintel/specs/` so the repository's living specs accurately reflect current system behaviour.
   - Omit withdrawn requirements and transient task commentary when merging into `.lintel/specs/`.
   - If the work item was a pure refactor (`Behaviour: unchanged`) and `.lintel/specs/` already accurately captures the behaviour, finish as `no-change`.

---

## 10. PO & PO-Reviewer Verification Checklist

Before approving or submitting `shared/requirements.md`, verify every item below:

- [ ] **Format & IDs**: Every requirement uses `### R<n>: <title>` with unique, stable sequential IDs.
- [ ] **Withdrawn Marker**: Any descoped requirement preserves its heading and states `Status: withdrawn — <reason>`.
- [ ] **Unchanged Behaviour Marker**: Refactors or non-behavioural optimizations explicitly state `Behaviour: unchanged` and list preserved invariants.
- [ ] **No Phase Planning**: `shared/requirements.md` contains zero implementation phases or delivery milestones.
- [ ] **Stack Neutrality**: No language, build tool, test framework, or internal implementation jargon appears in the requirements.
- [ ] **INVEST & Testability**: Every active requirement is atomic, unambiguous, and accompanied by falsifiable acceptance criteria.
- [ ] **Boundaries & Errors Covered**: Empty, zero, minimum, maximum, `max + 1`, malformed, duplicate, and invalid-state inputs have explicit behavioural and error outcomes.
- [ ] **Explicit Scope Boundaries**: A "Not Supported / Out of Scope" section clearly bounds what is excluded.
- [ ] **Living Specs Alignment**: Requirements are consistent with `.lintel/specs/` and `.lintel/steering/` (or explicitly specify how existing specs are updated).
