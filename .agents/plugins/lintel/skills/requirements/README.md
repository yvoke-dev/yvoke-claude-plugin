# Requirements Method Skill (`lintel:requirements`)

The `requirements` method skill provides Lintel's authoritative methodology for authoring, refining, and reviewing behavioural requirements in `shared/requirements.md` and maintaining living specifications in `.lintel/specs/`.

## Who Uses This Skill

- **Product Owner (`po`)**: Uses this skill during requirements authoring, revisits/reroutes, and living specification updates to translate user requests (`shared/request.md`) into structured, testable, stack-neutral requirements.
- **PO Reviewer (`po-reviewer`)**: Uses this skill as the audit standard when validating `shared/requirements.md` and `.lintel/specs/` updates before issuing `approve` or `changes` verdicts.

## What This Skill Covers

1. **Document Structure & Lifecycle Markers**:
   - Standard `### R<n>: <title>` heading convention and permanent ID stability across review and reroute cycles.
   - Explicit withdrawal syntax (`Status: withdrawn — <reason>`) to preserve traceability when requirements are descoped.
   - Explicit unchanged-behaviour marker (`Behaviour: unchanged`) for refactorings and performance optimizations.
2. **INVEST Quality Standard**:
   - Concrete criteria for keeping every requirement Independent, Negotiable, Valuable, Estimable, Small, and Testable.
3. **EARS Syntax & Acceptance Criteria**:
   - Normative sentence templates for Ubiquitous, Event-Driven, State-Driven, Unwanted Behaviour (error handling), and Optional rules, paired with `Given / When / Then` verification scenarios.
4. **Boundary & Edge-Case Analysis**:
   - Systematic checklists for collection cardinality (`0`, `1`, `max`, `max + 1`), numeric/scalar bounds, string normalization, temporal boundaries, state/concurrency invariants, and atomic failure contracts.
5. **Scope Control & Clarification Discipline**:
   - Writing explicit **Not Supported / Out of Scope** sections.
   - Decision rubric for when to pause with `needs-input` (`questions.md`) versus when to document low-risk domain assumptions and proceed.
6. **Strict Stack Neutrality**:
   - Ensures requirements describe observable system behaviour and domain rules without leaking implementation details, programming languages, or phase planning.
