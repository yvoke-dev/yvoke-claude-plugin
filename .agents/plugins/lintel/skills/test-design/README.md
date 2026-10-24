# Test Design Method Skill (`lintel:test-design`)

The `test-design` skill provides Lintel's stack-neutral methodology for planning, specifying, implementing, and reviewing acceptance tests across phases.

## Purpose

In Lintel's gated workflow, tests are designed in two stages before production logic is written:
1. **Architecture Phase (`architect` / `architect-reviewer`)**: Requirements assigned to a phase are decomposed into a structured `## Test outline` at the end of `shared/phase-<N>/design.md`, paired with exact interface contracts in `shared/phase-<N>/contract.md` and executable code stubs that throw unimplemented errors.
2. **QA Phase (`qa` / `qa-reviewer`)**: Each outline item is translated into concrete, deterministic acceptance tests at its designated test group (`unit`, `integration`, or `e2e`) and verified prior to the `lock` gate.

## What `SKILL.md` Covers

- **`## Test outline` Syntax & Traceability**: Strict formatting rules for `- R<n> <group>: <what>` and `- <group>: none: <reason>`, including criteria for valid vs. invalid `none` exemptions.
- **Test Group Partitioning**: Clear boundaries between `unit` (isolated domain logic and combinatorial edge cases), `integration` (real persistence, I/O, and multi-component seams), and `e2e` (full system entrypoints and user workflows).
- **Systematic Test Case Derivation**:
  - **Equivalence Partitioning**: Selecting representative valid and invalid inputs across domain and state partitions without redundant bloat.
  - **Boundary-Value Analysis (BVA)**: Testing off-by-one limits (`N-1`, `N`, `N+1`), zero/empty/absent values, minimum/maximum extremes, and temporal/clock boundaries.
  - **Negative & Fault-Injection Testing**: Verifying fast failure on invalid inputs, downstream dependency faults, and out-of-order state transitions while guaranteeing state atomicity.
  - **Property & Invariant Checks**: Asserting round-trip symmetry, idempotency, conservation laws, and deterministic ordering.
- **Designing for the `lock` Gate**: Ensuring tests fail cleanly against unimplemented stubs (`exit 1`) and pass once the contract is implemented (`exit 0`), never asserting on stub internals or catching generic exceptions that mask unimplemented stubs.
- **Role Checklists**: Actionable verification criteria for `architect`, `qa`, `architect-reviewer`, and `qa-reviewer`.
