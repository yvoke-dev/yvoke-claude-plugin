---
name: test-design
description: Stack-neutral methodology for designing test outlines, partitioning coverage across test groups, applying equivalence and boundary analysis, and writing lock-gate acceptance tests against unimplemented stubs.
---

# Test Design Methodology

This skill defines the rigorous, stack-neutral methodology for designing test outlines (`shared/phase-<N>/design.md`) and implementing executable acceptance tests (`QA` phase) that enforce phase contracts (`shared/phase-<N>/contract.md`). It governs how the **Architect** plans verification, how **QA** translates outlines into executable assertions, and how **Architect Reviewer** and **QA Reviewer** audit test coverage and lock-gate integrity.

---

## 1. The Lintel `## Test outline` Syntax & Contract

Every `shared/phase-<N>/design.md` document must conclude with a `## Test outline` section. The outline acts as the binding verification specification between the Architect and QA.

### Required Line Formats

Every bullet in `## Test outline` must strictly follow one of two formats:

1. **Requirement Coverage Item**:
   ```markdown
   - R<n> <group>: <what>
   ```
   - `R<n>`: The exact requirement identifier from `shared/requirements.md` and `shared/plan.md` assigned to the current phase (e.g., `R1`, `R4`). Each line maps to a single primary requirement so traceability remains unambiguous.
   - `<group>`: The configured test group name (typically `unit`, `integration`, or `e2e`, matching the project's `.lintel/scripts/test-<group>.sh` runners).
   - `<what>`: A concrete, behavioral specification of the scenario, input partition, and expected outcome or state transition—never a vague label like `"test happy path"` or `"verify service works"`.

2. **Explicit Group Exemption (`none`)**:
   ```markdown
   - <group>: none: <reason>
   ```
   - Used when an active test group configured in the repository requires zero tests for the current phase.
   - Every configured test group must appear in `## Test outline` either through one or more `- R<n> <group>: <what>` items or a single `- <group>: none: <reason>` declaration. Omitting a configured group is a blocker finding.

### Rules for Valid `none` Justifications

Reviewers must scrutinize every `- <group>: none: <reason>` line with skepticism:
- **Valid reasons** demonstrate structural impossibility or strict architectural irrelevance for the phase's scope:
  - `- e2e: none: Phase 1 introduces pure in-memory domain value objects with no external entrypoints, CLI commands, network endpoints, or user-facing workflows.`
  - `- integration: none: Phase 2 modifies only stateless string-parsing utilities with zero persistence, I/O, or cross-component wiring.`
- **Invalid reasons** (must be rejected by Reviewers):
  - `"Covered by unit tests"` when the phase introduces database queries, file I/O, message brokers, or multi-component wiring.
  - `"Too slow to set up"` or `"Will be tested in a later phase"` when the contract already exposes the boundary in the current phase.
  - `"Trivial code"` or `"Low risk"`.

---

## 2. Partitioning Coverage Across Test Groups (`unit`, `integration`, `e2e`)

Effective test design assigns each behavioral risk to the lowest test group capable of proving the contract with fidelity, while ensuring higher groups verify real component seams and end-to-end workflows.

| Test Group | Primary Scope & Seam | What Belongs Here | What Does NOT Belong Here |
| :--- | :--- | :--- | :--- |
| **`unit`** | Single module, class, or function in isolation; pure domain logic and deterministic state machines. | Combinatorial branching, equivalence classes, boundary values, parsing/validation rules, error mapping, algorithmic invariants, injected clock/randomness edge cases. | Real network sockets, live database engines, filesystem mutations outside ephemeral memory abstractions, or tautological mock-to-mock assertions. |
| **`integration`** | Real interaction across two or more internal components or infrastructure boundaries. | Persistence queries and transactions, schema migrations, serialization/deserialization across boundaries, filesystem/OS adapters, internal service-to-service wiring, configuration loading. | Exhaustive combinatorial input permutations already proven at the `unit` level, or full user journeys across the entire system deployment. |
| **`e2e`** | Complete system exercised through its public entrypoint (`CLI`, `HTTP` API, UI harness, or batch runner) as an external consumer experiences it. | Critical end-to-end user journeys, authentication/authorization enforcement at the edge, cross-cutting error formatting, process exit codes, and end-to-end state persistence. | Fine-grained edge-case permutations of internal helper functions. |

### The Anti-Tautology Rule for Doubles and Fakes
- Do not write `unit` tests that merely verify a mock was called with the exact arguments hardcoded in the test setup unless that outgoing call *is* the documented side-effect contract.
- When a requirement specifies persistence semantics (e.g., uniqueness constraints, atomic rollback, query filtering), verify it in `integration` against a real local store or ephemeral instance rather than faking the storage layer in `unit` tests.

---

## 3. Systematic Test Case Derivation

Architects (when drafting `## Test outline` items) and QA (when expanding each outline item into concrete test cases) must systematically apply four analytical techniques.

### 3.1 Equivalence Partitioning

Divide the input domain and system state space into partitions where all values within a partition should be treated identically by the contract:
- **Valid Partitions**: Distinct categories of well-formed inputs that trigger different behavioral paths (e.g., single-element vs. multi-element collections, authenticated admin vs. standard user, cached vs. uncached state, idempotent repeat request vs. initial request).
- **Invalid Partitions**: Distinct categories of malformed, unauthorized, or out-of-state inputs (e.g., missing required fields, structurally malformed tokens, type mismatches, operations invoked in a terminal lifecycle state).
- **Rule**: Select at least one representative value from every valid and invalid equivalence partition. Never bloat a suite with ten inputs from the same partition while leaving another partition untested.

### 3.2 Boundary-Value Analysis (BVA)

Defects cluster densely at the edges of equivalence partitions. For every ordered constraint, numeric range, length limit, quota, or temporal window `[min, max]`, test the three-point boundary (`boundary - 1`, `boundary`, `boundary + 1`):

1. **Off-by-One (`N - 1`, `N`, `N + 1`)**:
   - Pagination limits, batch sizes, retry counters, rate limits, and string/payload length caps.
   - Verify whether bounds are inclusive (`<=`) or exclusive (`<`) as specified in `contract.md`.
2. **Zero and Empty States**:
   - `0`, `-0`, empty string `""`, whitespace-only string, empty collection `[]`, empty map `{}`, and `null` / `nil` / `none` / absent optional fields.
   - Distinguish explicitly between an absent parameter, an empty container, and a container holding a single empty element.
3. **Minimum and Maximum Extremes**:
   - Minimum allowed domain value, negative numbers (`-1`), maximum representable integer/decimal precision, deeply nested structures, and maximum payload capacity.
4. **Clock, Timezone, and Temporal Boundaries**:
   - Exact expiration instant (`now == expires_at`: verify whether exact equality is treated as valid or expired), `1ms` before expiration, `1ms` after expiration.
   - Day/month/year rollovers, leap-year dates (`Feb 29`), daylight-saving transitions, non-UTC timezone offsets, and non-monotonic clock skew.

### 3.3 Negative & Fault-Injection Testing

Acceptance suites must actively attempt to break the contract under adverse conditions:
- **Contract Rejection**: Verify that invalid inputs fail fast with the exact exception type, error code, or structured error payload defined in `shared/phase-<N>/contract.md`, and **prove that no partial state mutation occurred** (atomicity).
- **Dependency & I/O Faults**: In `unit` and `integration` tests, simulate downstream failures—unavailable storage, timeout expiry, corrupted payload reads, permission denial, disk full, or mid-transaction crash—and verify graceful degradation, clean resource release, or deterministic rollback.
- **State-Transition Violations**: Invoke operations out of sequence (e.g., committing an already aborted transaction, cancelling a completed job, consuming a single-use token twice) to confirm state machine invariants hold.

### 3.4 Property and Invariant Checks

Beyond fixed input-output examples, assert structural invariants that must hold across all executions:
- **Round-Trip / Symmetry**: `deserialize(serialize(x)) == x`, `decode(encode(x)) == x`.
- **Idempotency**: Applying an idempotent operation $k \ge 1$ times yields the exact same system state and observable result as applying it once: $f(f(x)) = f(x)$.
- **Conservation & Accounting Invariants**: Sum of partition counts equals total count; account debit plus credit balances remain conserved; collection length after filtering never exceeds initial length.
- **Ordering & Determinism**: Repeated queries or sorting operations with ties produce a stable, deterministic order specified by the contract.

---

## 4. Designing Tests for Lintel's `lock` Gate

Lintel enforces a strict **Red-to-Green Lock Gate** between the Architect, QA, and Developer phases:
1. **Architect** creates production code stubs whose bodies raise an unimplemented error/exception, matching the signatures in `shared/phase-<N>/contract.md`.
2. **QA** writes acceptance tests against the contract and runs `./.lintel/scripts/test-<group>.sh`. Against the stubs, the test runner **must fail (exit code `1`)** prior to the `lock` gate.
3. **Developer** implements the production code without altering locked tests. When `./.lintel/scripts/test-<group>.sh` runs against the completed implementation, the exact same unmodified test suite **must pass (exit code `0`)**.

### Critical Rules for Lock-Gate Compatibility

Every test written by QA must satisfy two simultaneous conditions: **Red on Stubs (`exit 1`)** and **Green on Valid Implementation (`exit 0`)**.

#### Rule 1: Never Assert on Stub Internals (`NotImplemented` Exceptions)
- **FORBIDDEN**: Writing a test that expects or catches the stub's `NotImplemented` error/exception and treats it as a passing assertion. Such a test will pass against the stub (breaking the Red gate) and fail once the Developer implements the method!
- **FORBIDDEN**: In negative tests (where the contract specifies that invalid input raises a domain error like `ValidationError` or `InvalidArgument`), using an overly broad catch-all assertion (such as asserting that *any* base `Exception` or `Error` is thrown). Because the unimplemented stub *also* throws an exception, a broad exception assertion will **falsely pass (`exit 0`) against the stub**!
- **REQUIRED**: Negative tests must assert on the **specific domain error type, error code, or structured error state** required by `contract.md`, ensuring that an unimplemented stub exception causes the test to fail (`exit 1`).

#### Rule 2: Fail via Behavioral Assertions, Not Compilation or Import Errors
- Tests must compile, load, and execute cleanly against the Architect's stubs.
- A test runner exiting `1` because of a syntax error, missing symbol import, type-checker failure, or misconfigured runner script is a **broken test or contract defect**, not a valid Red lock-gate test.
- If a symbol required by `contract.md` is missing from the Architect's stubs or has a mismatched signature, QA must **not** work around it or edit production stubs; QA must document `## Contract defect` in `report.md` and finish with `lintel done --verdict blocked --cause contract`.

#### Rule 3: Assert Only on Contracted Seams, Never Unspecified Implementation Details
- Tests are locked before the Developer writes production code. If a test asserts on private fields, internal helper function names, exact log message wording not mandated by `contract.md`, or hash-map iteration order, the Developer will be trapped by an over-constrained test.
- Anchor every setup call, action, and assertion strictly to the public signatures, data structures, UI hooks, and observable side effects documented in `shared/phase-<N>/contract.md` and `shared/requirements.md`.

#### Rule 4: Deterministic, Isolated Execution
- Tests must be hermetic and repeatable: no reliance on wall-clock timing sleeps, unseeded random generators, shared mutable global state across test cases, or external network availability.
- Every test must clean up or isolate its temporary directories, in-memory stores, or database transactions so execution order never alters pass/fail outcomes.

---

## 5. Role-Specific Checklists

### For the Architect (Drafting `## Test outline`)
- [ ] Every requirement `R<n>` assigned to the phase in `shared/plan.md` has at least one `- R<n> <group>: <what>` entry in `## Test outline`.
- [ ] Every test group configured in the project is represented—either by `- R<n> <group>: <what>` entries or by an explicit, rigorously justified `- <group>: none: <reason>` entry.
- [ ] Outline items cover happy paths, boundary conditions, negative/error paths, and state invariants across appropriate test groups (`unit`, `integration`, `e2e`).
- [ ] Code stubs exist for every signature needed by QA to import, invoke, and assert against the contract.

### For QA (Implementing Acceptance Tests)
- [ ] Every `- R<n> <group>: <what>` line in `## Test outline` is implemented in the corresponding `<group>` test suite.
- [ ] Equivalence partitions, boundary values (`N-1`, `N`, `N+1`, zero/empty, extremes, time boundaries), and negative cases are thoroughly exercised.
- [ ] Negative tests assert specific domain errors/outcomes so they do **not** falsely pass when a stub raises an unimplemented exception.
- [ ] Running `./.lintel/scripts/test-<group>.sh` executes the tests cleanly against the stubs and exits `1` due to unmet behavioral assertions (never syntax/import errors).
- [ ] Zero production code or stub files were modified; if requirements are ambiguous, report `## Requirements problem` (`blocked --cause requirements`); if stubs or contracts are broken, report `## Contract defect` (`blocked --cause contract`).

### For Reviewers (`architect-reviewer` & `qa-reviewer`)
- [ ] **Architect Reviewer**: Reject vague outline descriptions, missing requirement mappings, improperly placed test groups, or weak `- <group>: none: <reason>` excuses. Verify stubs match `contract.md` and throw unimplemented errors.
- [ ] **QA Reviewer**: Trace every outline bullet to concrete test assertions at the specified `<group>`. Verify tests fail against stubs with exit code `1` for the right reason (assertion/stub failure, not broken imports or broad exception catches that pass on stubs), and confirm tests will pass (`exit 0`) once the contract is faithfully implemented.
