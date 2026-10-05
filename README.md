# Yvoke for Claude

A Claude plugin that brings Yvoke Desktop's capabilities into Claude: playbook-scoped, cited answers from the
Yvoke knowledge base, and multi-agent investigations with enforced review.

- **Claude Code** (the Claude Desktop app's **Code** tab and the terminal): full feature set, through a mod.
- **Cowork** and **Chat**: live playbook stubs over the Yvoke connector, with fewer guarantees.

**Status:** scaffold. The plugin installs and loads, but does nothing yet.

Try it from a clone: `claude --plugin-dir plugins/yvoke`, or add the clone as a local marketplace with
`claude plugin marketplace add .` and install `yvoke@yvoke`.

| Document | What it covers |
| --- | --- |
| [docs/sdlc.md](docs/sdlc.md) | How we build this: intent, spec, task plan, tests first, pull request |
| [docs/tasks/v1/intent.md](docs/tasks/v1/intent.md) | v1: why, for whom, use cases, constraints, open questions |
| [docs/tasks/v1/requirements.md](docs/tasks/v1/requirements.md) | v1: what the plugin must do, per surface |
| [docs/tasks/v1/design.md](docs/tasks/v1/design.md) | v1: architecture, decisions, notes for implementers, yvoke-web changes |
| [docs/tasks/v1/plan.md](docs/tasks/v1/plan.md) | v1: the tasks and their progress |
| [docs/specs/](docs/specs/README.md) | What is built today |
| [AGENTS.md](AGENTS.md) | Rules and verification commands for agents working here |
