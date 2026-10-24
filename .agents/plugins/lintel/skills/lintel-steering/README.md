# Lintel Steering Skill (`lintel-steering`)

The `lintel-steering` skill bootstraps and refreshes repository steering documentation in `.lintel/steering/`. Steering files give Lintel workflows (`lintel-feature`, `lintel-specs`, and phase agents) durable, repository-grounded context on technology choices, directory layout, coding conventions, architecture, and testing practices.

## What It Manages

`lintel-steering` creates or updates five standard files in `.lintel/steering/`, each initialized from `plugin/templates/steering/`:

- **`tech.md`** — Languages, runtime/framework versions, primary libraries, and exact build/run commands (`Stack and Versions`, `Main Libraries`, `Build and Run`).
- **`structure.md`** — Repository directories and modules, placement rules for new code, and naming conventions (`Directories and Modules`, `Where New Code Goes`, `Naming Conventions`).
- **`conventions.md`** — Linting/formatting rules, error handling, logging standards, and API idioms (`Code Style`, `Error Handling`, `Logging`, `API Style`).
- **`architecture.md`** — System layers, recurring design patterns, and key architectural decisions with rationale (`Layers`, `Patterns`, `Key Decisions`).
- **`testing.md`** — Practical test groups, shared fixtures and test data patterns, and assertion/mocking helpers (`Test Groups`, `Fixtures and Test Data`, `Helpers`).

## How It Works

1. **Inspects Repository Evidence**: Reads build manifests, directory structure, `README.md`, `CONTRIBUTING.md`, CI workflows, `AGENTS.md`, and any existing `.lintel/steering/*.md` files.
2. **Proposes Before Writing**: Presents a per-file outline of findings and highlights any uncertain points before touching disk.
3. **Confirms with the User**: Waits for the user to confirm or refine the proposed outlines in chat.
4. **Writes Grounded Documentation**: Writes `.lintel/steering/<file>.md` using the canonical templates, marking any unconfirmed detail with `> unverified: <reason>`.
5. **Stays Clean for `lintel doctor`**: Avoids referencing specific test filenames or test file paths with extensions so `lintel doctor` emits zero content warnings.
6. **Rerunnable Anytime**: Can be re-run in a normal chat for the entire repository or targeted to a single steering file whenever the codebase evolves.
