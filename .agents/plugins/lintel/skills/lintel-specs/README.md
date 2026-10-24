# Lintel Specs Skill (`lintel-specs`)

`lintel-specs` is an interactive Lintel skill for bootstrapping and maintaining living functional specifications in `.lintel/specs/`.

## Purpose

Living specifications in `.lintel/specs/` give both developers and Lintel workflow phases (`frame`, `define`, `plan`, `implement`, `review`) a reliable map of **what the system does** and **what invariants must hold** across functional domains.

Unlike static design documents that go stale after a single feature launch, `.lintel/specs/` is organized by domain area (`index.md` + `<area>.md`) and is **rerunnable** whenever new features land or existing domains evolve.

## Key Characteristics

- **Interactive Chat Skill**: Runs directly in a normal chat session with the user rather than inside a background Lintel task.
- **Propose → Confirm → Write**: The agent first inspects the codebase and existing specs, proposes the domain area breakdown (`index.md` and `<area>.md` files) for the user to confirm, and then writes one area at a time.
- **Describes Behaviour, Never Test Cases**: While the agent reads source code, docs, and test suites to extract edge cases and guarantees, specifications strictly describe observable domain behaviour, invariants, state transitions, error conditions, and non-goals—never test cases or test suite implementation details.
- **Honest Uncertainty (`> unverified:`)**: Any behaviour or invariant that cannot be confirmed from the code is explicitly flagged with `> unverified: <explanation>` so humans can review and resolve it before or after committing.
- **Rerunnable per Area**: Can be invoked to bootstrap `.lintel/specs/` for an entire repository or to refresh a single `<area>.md` file after changes.

## Usage

Invoke the `lintel-specs` skill in chat:

- **Bootstrap all specs**: *"Run `lintel-specs` to bootstrap living specifications for this repository."*
- **Update a specific area**: *"Use `lintel-specs` to update `.lintel/specs/auth.md` with the new token refresh rules."*
- **Add a new domain area**: *"Run `lintel-specs` for the billing module and link it in `.lintel/specs/index.md`."*
