# `lintel-feature` Workflow Skill

`lintel-feature` is the primary orchestrator workflow skill for Lintel 2. It guides the default main agent through a complete feature lifecycle (`START → REQUIREMENTS → PLAN → PHASE N (QA lock → DEV deliver) → REVISIT → FINAL REVIEW → DOCS UPDATE → FINISH`) while keeping all state transitions deterministic inside `./.lintel/bin/lintel`.

## Responsibilities of the Orchestrator

- **State-machine follower:** Executes `./.lintel/bin/lintel` commands and follows the printed `Next action:` line verbatim.
- **Persona dispatcher:** Runs `./.lintel/bin/lintel invoke <role> --phase <ph> [--from-report] [--pair <writer>]`, dispatches the corresponding `.agents/agents/lintel-<role>.md` agent via `invoke_subagent` (`Workspace: "inherit"`, `Model: "inherit"`), ends its turn immediately, and synchronizes on wake-up with `./.lintel/bin/lintel task wait` followed by `./.lintel/bin/lintel task status`.
- **Question relay:** Reads `questions.md` when a writer finishes `needs-input`, asks the user via `ask_question` (falling back to chat text on `skipped`, `empty answer`, or `timeout`), and records answers verbatim via `./.lintel/bin/lintel answer <role> <question-id> "<text>"`.
- **Human gate relay:** Prompts the user to run human-only commands (`approve requirements`, `approve plan`, `lock`, `amend`, `drift --ack`, `audit --ack`, `decide`, `approve final`, `approve docs`, `task finish`, `reset`, `amend --config`, `task unstick --force`) in their own terminal so the OS confirmation dialog (`H0`) can verify the caller.
- **Recovery handler:** Uses `./.lintel/bin/lintel task resume` when continuing in a new chat and `./.lintel/bin/lintel task unstick` when `task wait` detects a stopped or unentered persona run.

## Files

- `SKILL.md` — Full operational instructions loaded by the orchestrator agent.
- `README.md` — Overview of the `lintel-feature` skill and its role in the plugin.
