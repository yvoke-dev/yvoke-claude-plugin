# Specs Method Skill (`lintel:specs`)

The `specs` method skill provides the Product Owner (`po`) and documentation workflows with a structured, stack-neutral methodology for authoring and maintaining living specifications in `.lintel/specs/`.

## Purpose

While `shared/requirements.md` captures the requirement delta (`### R<n>: <title>`) for an individual task, `.lintel/specs/` maintains the cumulative, authoritative specification of system behaviour at rest. Every specification **describes behaviour, never test cases**.

## Key Capabilities Covered in `SKILL.md`

1. **Two-Tier Living Specification Architecture**
   - `.lintel/specs/index.md`: Master domain map, system overview, cross-cutting invariants, and glossary.
   - `.lintel/specs/<domain-area>.md`: Cohesive domain specifications organized by functional area rather than code layers or task chronology.
2. **Behavioural Specification Structure**
   - Domain concepts and scope boundaries.
   - Invariants and business rules.
   - Observable operations (preconditions, inputs/defaults, postconditions, idempotency).
   - Explicit state transition tables and guard conditions.
   - Deterministic error semantics, boundary conditions, and explicitly unsupported operations.
3. **End-to-End Lifecycle Integration**
   - **Initial Specification (`po` stage)**: Consulting `.lintel/specs/` to ground task requirements (`### R<n>: <title>`) in canonical domain concepts.
   - **Documentation Merge (`docs` stage)**: Merging delivered requirements into `.lintel/specs/` in place with `[TASK-ID]` traceability tags, or finishing with `no-change` when observable domain behaviour is unaltered.
4. **Clean Handling of Requirement Evolution**
   - Excluding withdrawn requirements (`Status: withdrawn — <reason>`) from active specifications.
   - Preserving existing specification sections when requirements specify `Behaviour: unchanged`.
   - Rewriting superseded behaviour in place and moving retired capabilities to **Unsupported Operations** without leaving contradictory historical text.

## Usage

Assign `lintel:specs` to the `po` role (or documentation roles) in `.lintel/config.jsonc` so agents follow consistent living-specification conventions across all tasks.
