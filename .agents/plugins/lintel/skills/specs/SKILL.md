---
name: specs
description: Comprehensive guide for structuring, writing, and maintaining stack-neutral living specifications in .lintel/specs/ across initial specification and documentation updates.
---

# Living Specifications (`specs`)

A living specification in `.lintel/specs/` is the authoritative, cumulative record of how the system behaves from a domain and external-contract perspective. Unlike task-scoped requirement documents (`shared/requirements.md`), which capture the delta for a single feature or change, `.lintel/specs/` represents the current truth of the entire product at rest.

Every living specification strictly **describes behaviour, never test cases**, and remains 100% stack-neutral.

---

## 1. Core Principles

1. **Describes Behaviour, Never Test Cases**
   Specifications state *what* the system does, *when* state changes occur, and *what* invariants hold. They never prescribe test suites, assertion libraries, mock setups, or literal test file paths.
2. **Cumulative State, Not a Changelog**
   Read top-to-bottom, a domain specification file must describe how the system works *today*. Do not append chronological "change sections" at the bottom of a file; integrate modifications directly into the relevant domain rules and state definitions, preserving task traceability via inline tags.
3. **100% Stack-Neutral**
   Express contracts in terms of domain entities, inputs, outputs, state transitions, events, and error semantics. Avoid language-specific types, framework idioms, or runtime tooling names.
4. **Explicit Boundaries and Unsupported Operations**
   Ambiguity is a specification defect. Explicitly document unsupported operations, rejected inputs, boundary limits, and the exact error semantics returned when callers violate preconditions.

---

## 2. Directory Layout in `.lintel/specs/`

Living specifications live under `.lintel/specs/` and follow a two-tier structure: `index.md` plus cohesive domain area files.

```text
.lintel/specs/
├── index.md
├── <domain-area-1>.md
└── <domain-area-2>.md
```

### `index.md` — The Domain Map

`index.md` is the entry point for all readers and automated agents exploring the system specification. It must contain:

- **System Overview**: A concise summary of the product's purpose and primary domain boundaries.
- **Domain Area Directory**: A table or structured list linking to each `<domain-area>.md` file, stating the scope of responsibilities owned by that area and the key domain concepts defined inside.
- **Cross-Cutting Invariants**: System-wide rules that apply across all domain areas (for example, idempotency guarantees, standard error envelope semantics, or tenant isolation rules).
- **Glossary**: Canonical definitions of ubiquitous domain terms used across multiple area files.

### `<domain-area>.md` — Cohesive Domain Specifications

Partition specifications by **functional domain area** (such as `billing.md`, `authentication.md`, `order-lifecycle.md`, or `scheduling.md`), never by internal code layers, database tables, or task numbers.

When deciding whether a capability belongs in an existing area file or a new one:
- **Existing area file**: The capability extends an existing entity, state machine, or workflow already owned by that domain area.
- **New area file**: The capability introduces a distinct bounded context or domain concept with minimal coupling to existing area files. Whenever you create or remove an area file, update `index.md` in the same step.

---

## 3. Anatomy of a Domain Area Specification

Each `<domain-area>.md` file should follow a predictable structure so readers can locate rules quickly:

### 3.1 Scope and Domain Concepts
Define the entities, value objects, actors, and external interfaces governed by this domain area. State clearly what is in scope and what belongs to adjacent domain areas.

### 3.2 Invariants and Business Rules
List properties that must always hold true before and after any operation in this domain.
- Express invariants as universal truths: *"An invoice total is always equal to the sum of its line items minus applied discounts plus applicable taxes."*
- Specify precision, rounding, ordering, uniqueness, and concurrency guarantees.

### 3.3 Observable Domain Behaviour and Operations
For each capability or command in the domain, specify:
- **Preconditions**: Required system state, caller authorization, and input validity criteria.
- **Inputs & Defaults**: Required and optional parameters, default values when omitted, and normalization rules (such as trimming whitespace or case folding).
- **Postconditions & Side Effects**: Exact observable outcomes, persisted state changes, emitted domain events, and returned values.
- **Idempotency & Ordering**: Behaviour when the same operation is repeated or arrives out of sequence.

### 3.4 State Transitions
When a domain entity has a lifecycle, define its finite states and allowed transitions explicitly:

| Current State | Trigger / Operation | Guard Conditions | Next State | Observable Outcome |
| :--- | :--- | :--- | :--- | :--- |
| `draft` | `submit` | At least one line item; total > 0 | `submitted` | Emits submission timestamp; locks line items |
| `submitted` | `cancel` | No payment settled | `cancelled` | Releases reserved quota |
| `settled` | `cancel` | — | *(rejected)* | Fails with `invalid-state-transition` |

### 3.5 Error Semantics and Edge Cases
Document every failure mode as a first-class behavioural rule:
- **Condition**: The exact invalid input, violated invariant, or disallowed state transition.
- **Error Classification**: The semantic error category or code (for example, `validation-error`, `not-found`, `conflict`, `precondition-failed`, `quota-exceeded`).
- **Atomicity**: Confirm that failed operations leave prior domain state untouched (or specify any intentional audit logging).
- **Boundary Conditions**: Empty collections, zero or negative quantities, maximum payload limits, duplicate identifiers, and clock/expiration boundaries.

### 3.6 Unsupported Operations
Explicitly list operations or combinations of parameters that the domain intentionally does not support, along with how the system responds when they are attempted (e.g., deterministic rejection with `unsupported-operation` rather than silent ignoring).

---

## 4. Lifecycle Across Workflow Stages

### 4.1 Initial Specification (`po` Stage)
During initial specification (and revisits), the Product Owner reads `.lintel/specs/index.md` and relevant `<domain-area>.md` files to understand existing system behaviour before authoring `shared/requirements.md`.

- `shared/requirements.md` uses numbered requirement blocks (`### R1: <title>`, `### R2: <title>`, ...) to define the target behaviour for the current task.
- Each `### R<n>: <title>` section should align with the domain terminology in `.lintel/specs/` and clearly state whether it introduces net-new behaviour, modifies an existing rule, or deprecates prior behaviour.

### 4.2 Merging Changes During the `docs` Stage
In the `docs` stage, after implementation and verification complete, the Product Owner reconciles the delivered `shared/requirements.md` into `.lintel/specs/`.

Follow this step-by-step merge procedure:

1. **Inspect Delivered Requirements**
   Read `shared/requirements.md` and review the status of every requirement (`### R<n>: <title>`).
2. **Determine If Specification Updates Are Needed**
   - If every active requirement only refactored internal structure without altering observable domain behaviour, or if all requirements specify `Behaviour: unchanged` / `Status: withdrawn — <reason>` with no net change to domain rules, make no edits to `.lintel/specs/` and finish with verdict `no-change`.
3. **Locate Target Domain Files**
   Map each active requirement to the appropriate `.lintel/specs/<domain-area>.md` file(s). If a new domain area is required, create the file and register it in `.lintel/specs/index.md`.
4. **Merge In-Place with Task ID Traceability Tags**
   - Integrate new and modified rules directly into the appropriate sections (Invariants, Operations, State Transitions, Error Semantics, Unsupported Operations).
   - Tag every added or modified rule, table row, or section heading with the originating task identifier (and optional requirement ID) in brackets, such as `[TASK-042]` or `[TASK-042 / R2]`.
   - When modifying an existing rule that already carries an older task tag, replace the outdated rule text with the new current behaviour and update or append the new task tag (e.g., `[TASK-015, TASK-042]`) so traceability reflects the latest authoritative change.
5. **Update `index.md` When Scope Shifts**
   If domain boundaries, cross-cutting invariants, glossary terms, or area files changed, update `.lintel/specs/index.md` to match.

---

## 5. Handling Withdrawn, Unchanged, and Superseded Behaviour

Requirements evolve during a task (`revisit` or `reroute`) and across multiple tasks over time. Handle non-standard requirement states cleanly so `.lintel/specs/` never accumulates dead or contradictory rules.

### 5.1 Withdrawn Requirements (`Status: withdrawn — <reason>`)
In `shared/requirements.md`, a requirement withdrawn during the task lifecycle is marked:
```markdown
### R3: Bulk Export Archival
Status: withdrawn — deferred to a separate archival initiative after scope review.
```
**How to handle during `docs` merge:**
- **Never copy withdrawn requirements into `.lintel/specs/`.** A withdrawn requirement was not delivered and is not part of system behaviour.
- If the withdrawn requirement was meant to *add* a capability that never existed, simply omit it from `.lintel/specs/`.
- If the task *removed* an existing capability that was previously documented in `.lintel/specs/`, remove the obsolete positive behaviour from the specification and, if callers might still attempt the removed action, document its rejection under **Unsupported Operations** tagged with the current `[TASK-ID]`.

### 5.2 Unchanged Behaviour (`Behaviour: unchanged`)
In `shared/requirements.md`, a requirement that confirms existing domain rules remain intact is marked:
```markdown
### R4: Settlement Rounding Rules
Behaviour: unchanged
```
**How to handle during `docs` merge:**
- Leave the corresponding rules in `.lintel/specs/` intact. Do not add redundant duplicate paragraphs or unnecessary churn if the specification already accurately states the rule.
- If all requirements in the task result in no net behavioural change to `.lintel/specs/`, complete the stage with `no-change`.

### 5.3 Superseded Behaviour Across Tasks
When a new task changes or replaces behaviour established by an earlier task:
- **Rewrite in place, do not contradict**: Replace the superseded rule directly where it lives in `<domain-area>.md`. A reader should never see Rule A in section 2 and "Update: Rule A no longer applies" at the bottom of the file.
- **Update state transition tables and error semantics**: Ensure old states, triggers, or error codes that no longer exist are removed or moved to **Unsupported Operations** if backward-compatibility rejection applies.
- **Tag the replacement**: Attach the current `[TASK-ID]` to the updated rule so future readers and agents can trace when and why the behaviour changed.

---

## 6. Formatting Example

```markdown
# Order Lifecycle Specification

## Scope and Domain Concepts
Defines the lifecycle of customer orders from creation through settlement or cancellation. Currency conversion rates are governed by `billing.md`.

## Invariants
- **Non-Negative Totals**: An order's payable total must always be greater than or equal to zero `[TASK-001]`.
- **Immutable Settled Orders**: Once an order enters the `settled` state, its line items and quantities cannot be modified `[TASK-001, TASK-019]`.

## Operations

### Submit Order `[TASK-001, TASK-024]`
Transitions an order from `draft` to `submitted`.
- **Preconditions**: Order is in `draft` state and contains between 1 and 100 line items `[TASK-024]`.
- **Postconditions**: Order state becomes `submitted`; `submitted_at` timestamp is recorded; inventory reservation is requested.
- **Error Semantics**:
  - Empty line items or count > 100 -> rejected with `validation-error` `[TASK-024]`.
  - Order not in `draft` state -> rejected with `invalid-state-transition` `[TASK-001]`.

## Unsupported Operations
- **Partial Line-Item Cancellation**: Cancelling individual line items after submission is not supported; callers must cancel the entire order and submit a replacement `[TASK-019]`.
- **Legacy Batch Import**: The v1 batch import operation is retired and rejected with `unsupported-operation` `[TASK-031]`.
```

---

## 7. Quality Checklist Before Finishing

Before completing initial specification review or a `docs` stage update, verify:
- [ ] Every specification file describes observable domain behaviour, never test cases or test file paths.
- [ ] All terminology and examples are 100% stack-neutral.
- [ ] `.lintel/specs/index.md` accurately indexes all `<domain-area>.md` files and cross-cutting rules.
- [ ] Every operation specifies preconditions, postconditions, state transitions, edge cases, and error semantics.
- [ ] Unsupported operations and scope boundaries are explicitly documented.
- [ ] All merged changes from `shared/requirements.md` carry `[TASK-ID]` traceability tags.
- [ ] Withdrawn requirements (`Status: withdrawn — <reason>`) are excluded from active specification rules, and superseded rules are replaced in place rather than appended as contradictions.
