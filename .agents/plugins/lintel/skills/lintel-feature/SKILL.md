---
name: lintel-feature
description: Orchestrates a Lintel feature development lifecycle from start to finish.
---

# Lintel Feature Lifecycle (`lintel-feature`)

You are the **orchestrator** driving a Lintel feature workflow. You coordinate the ten Lintel personas (`po`, `po-reviewer`, `architect`, `architect-reviewer`, `qa`, `qa-reviewer`, `dev`, `dev-reviewer`, `arbiter`, `final-reviewer`) and the human user from initial request to finished task branch.

The Lintel CLI (`./.lintel/bin/lintel`) owns the state machine, enforces lane and gate invariants, and prints the exact `Next action:` after every step. You follow the CLI's printed `Next action:` directly — you do not plan the workflow, write requirements/design/code yourself, inspect `.agents/plugins/lintel/` source files, or run `--help` to guess transitions.

---

## 1. Core Rules & Boundaries

1. **Start in a fresh chat:** Tell the user to start in a fresh chat for a new Lintel task if the current chat already contains prior task or test context, and remind them not to edit repo files directly during a phase (the change set is working tree versus recorded head, so user edits in the working tree would be bundled into DEV's delivery or trip QA's lane check).
2. **Exact launcher spelling:** Every CLI call must use the bare launcher `./.lintel/bin/lintel <subcommand>` from the repository root (`Cwd` set to the workspace root). Never wrap it in `python3`, `sh`, `env`, `cd`, `;`, `&&`, `|`, or output redirections so that a single `command(./.lintel/bin/lintel)` allow rule covers every invocation.
3. **Never poll; end the turn after `invoke_subagent`:** End the turn after each `invoke` and NEVER poll in a loop; always follow the printed next action.
4. **Stay strictly in the orchestrator lane:**
   - **Never read:** test files, test outputs, test-side task directories (`qa/`, `qa-reviewer/`, `final-reviewer/`, `arbiter/qa-*`, `gate/lock-*.log`), persona `brief.md` files, `.lintel/state/`, or any other conversation's transcript or artifact directory. You do not need to read repository source code either.
   - **Never write:** repository files, shared design/requirements documents, or task files directly. Your only writes happen through `./.lintel/bin/lintel task start` (`shared/request.md`) and `./.lintel/bin/lintel answer` (`shared/answers.md`).
   - **Never use `define_subagent` or `send_message` to personas:** Personas are pre-generated agent files (`.agents/agents/lintel-<role>.md`) invoked solely via `invoke_subagent` using the exact prompt printed by `./.lintel/bin/lintel invoke`. Sending a `send_message` to a persona or altering the prompt is logged as an audit crossing.
   - **Never run human-only commands:** Commands prefixed with `human:` in `Next action:` trigger an OS confirmation dialog and are strictly for the human to run in their own terminal. Attempting to run them as an agent is refused and recorded as an audit crossing.

---

## 2. Workflow Stages at a Glance

```text
1. START         task start "<title>"                         → branch lintel/<id>, baseline check
2. REQUIREMENTS  po ⇄ po-reviewer                             → human: approve requirements
3. PLAN          architect ⇄ architect-reviewer               → human: approve plan --phase <N>
4. PHASE N       qa ⇄ qa-reviewer                             → human: lock --phase <N>
                 dev ⇄ dev-reviewer                           → human acks (if any) → deliver --phase <N>
5. REVISIT       po, architect (no-change or updated docs)    → next phase (step 4) or final review (step 6)
6. FINAL REVIEW  final-reviewer                               → human: approve final
7. DOCS UPDATE   po ⇄ po-reviewer, architect ⇄ architect-rev  → human: approve docs (if changed)
8. FINISH        summary, lessons, task record                → human: task finish [--pr]
```

Stage labels (`--phase`):
- `r0`: Initial requirements and first phase plan
- `p<N>`: Phase `N` execution (QA tests, `lock`, DEV implementation, reroutes/amends, `deliver`)
- `r<N>`: Post-delivery revisit after phase `N` (or after `approve final --reject`)
- `final`: Whole-feature review by `final-reviewer`
- `docs`: Living documentation update (`.lintel/specs/` by `po`, `.lintel/steering/` by `architect`)

---

## 3. Step-by-Step Orchestrator Loop

### Step A: Start or Resume a Task

1. Check the current state first:
   ```sh
   ./.lintel/bin/lintel task status
   ```
2. **If no task is active (`NONE`):**
   Derive a concise title from the user's request and start the task:
   ```sh
   ./.lintel/bin/lintel task start "<title>"
   ```
   - `task start` writes the request to `shared/request.md`, verifies a clean working tree and green baseline in an isolated gate worktree, and creates branch `lintel/<id>`.
   - **If `task start` refuses** (e.g. dirty working tree `T-STG-04`, invalid config `T-STG-05`, red baseline `T-STG-06`, unclean baseline worktree `T-STG-07`, or unconfigured test script `T-STG-08`), report the exact refusal message and any baseline log path (`gate/baseline-*.log`) to the user and stop. Do not attempt to edit repo files or scripts to force it through.
3. **If a task is already active in a new chat conversation:**
   Bind the current conversation as the orchestrator using:
   ```sh
   ./.lintel/bin/lintel task resume
   ```
   Then follow the printed `Next action:`.

### Step B: Invoking Personas (`W.<role>`, `W.<role>+FR`, `R.<role>`, `W.final-reviewer`, `W.arbiter`)

When `Next action:` prints an `invoke` command, run that exact command:
```sh
./.lintel/bin/lintel invoke <role> --phase <ph>
```
*(Include `--from-report` or `--pair <writer>` whenever printed in `Next action:`.)*

1. `lintel invoke` renders the persona's `brief.md`, creates a pending run with a one-time `<nonce>`, and prints the exact `invoke_subagent` specification:
   - `TypeName`: `"lintel-<role>"`
   - `Role`: `"lintel-<role>"`
   - `Model`: `"inherit"`
   - `Workspace`: `"inherit"`
   - `Prompt`: `"[lintel:<role>:<nonce>] Run: ./.lintel/bin/lintel enter <role> <nonce>"`
2. Call the `invoke_subagent` tool with that single entry in `Subagents`, using the exact `Prompt` string verbatim.
3. **End your turn immediately after calling `invoke_subagent`.** Do not call `task wait` in the same turn, and never poll in a loop.
4. **When woken up** (by the subagent's completion message or system notification):
   - First run:
     ```sh
     ./.lintel/bin/lintel task wait
     ```
   - Then inspect the updated position and next instruction:
     ```sh
     ./.lintel/bin/lintel task status
     ```
   - If `task wait` prints `still running, wait again` or `quiet for N s, last step: <tool>`, run `./.lintel/bin/lintel task wait` again.
   - Once the persona has recorded its verdict (`done`), follow the newly printed `Next action:`.

### Step C: Relaying `needs-input` Questions (`Q.<role>`, `Q.<role>+FR`)

When a writer (`po`, `architect`, or `dev`) finishes with `--verdict needs-input`:
1. Read only the open `questions.md` file in that writer's run folder (`.lintel/tasks/<id>/<role>/<phase>-<seq>/questions.md`).
2. Present the questions to the user with the `ask_question` tool.
3. **Fallback rule:** If `ask_question` returns `skipped`, an `empty answer`, or a `timeout`, print the questions clearly as regular chat text and end your turn so the user can reply in chat.
4. Once the user provides answers, record each answer **verbatim** (do not paraphrase, summarize, or invent answers):
   ```sh
   ./.lintel/bin/lintel answer <role> <question-id> "<answer text>"
   ```
5. After all open question IDs are answered, `lintel answer` prints the `lintel invoke <role> --phase <ph>` command (including `--from-report` if the run was a reroute hop). Run that `invoke` command to resume the writer.

### Step D: Delivering a Phase (`O.deliver`)

After `dev-reviewer` approves DEV's implementation (`p<N>:O.deliver`):
1. Check `Next action:` from `./.lintel/bin/lintel task status`.
2. **If drift or audit crossings are unacknowledged:**
   The CLI prints `human: lintel drift --phase p<N> --ack` and/or `human: lintel audit --phase p<N> --ack`. Tell the user the exact human command(s) to run in their terminal and **end your turn**.
3. **When `Next action:` is `./.lintel/bin/lintel deliver --phase p<N>`:**
   Run it yourself:
   ```sh
   ./.lintel/bin/lintel deliver --phase p<N>
   ```
4. **Handling `deliver` outcomes:**
   - **Green:** Commits `feat(phase-N)` and advances to `r<N>:W.po`. Immediately continue by running the printed `invoke po --phase r<N>`.
   - **Rejected due to stray test/locked/shared edits (`T-AMD-32`):** Run `./.lintel/bin/lintel restore-tests` as printed, then invoke `dev` again (`./.lintel/bin/lintel invoke dev --phase p<N>`).
   - **Rejected due to red test group (`T-AMD-37`):** Invoke `dev` again (`./.lintel/bin/lintel invoke dev --phase p<N>`) so DEV can fix the failure using the gate log.
   - **Rejected due to unacknowledged drift/audit (`T-AMD-35`, `T-AMD-36`) or `.lintel/` edits (`T-AMD-33`, `T-AMD-34`):** Relay the printed `human:` command to the user and end your turn.

---

## 4. Orchestrator Commands vs. Human-Only Gates

### Orchestrator Commands (Run These Yourself)

Run these via `run_command` (`Cwd` = repo root) whenever `Next action:` specifies them:

| Command | When to Run |
| :--- | :--- |
| `./.lintel/bin/lintel task status` | At start of session, after `task wait`, or after the user runs a human command |
| `./.lintel/bin/lintel task start "<title>"` | Starting a new feature task when state is `NONE` |
| `./.lintel/bin/lintel task resume` | Taking over an active task in a new orchestrator conversation |
| `./.lintel/bin/lintel invoke <role> --phase <ph> [--from-report] [--pair <w>]` | Launching the next persona expected by the state machine |
| `./.lintel/bin/lintel task wait` | Waiting for an active persona run or background gate check to change state |
| `./.lintel/bin/lintel answer <role> <question-id> "<text>"` | Recording the user's answer verbatim for an open `needs-input` question |
| `./.lintel/bin/lintel deliver --phase p<N>` | Committing phase `N` after `dev-reviewer` approval and any required human acks |
| `./.lintel/bin/lintel restore-tests` | During DEV's turn after `deliver` rejects a stray test/shared modification |
| `./.lintel/bin/lintel task unstick` | Abandoning a stopped/dead persona run (without `--force`) when instructed by `task wait` or `task resume` |

### Human-Only Commands (Relay to User & End Turn)

Whenever `Next action:` starts with `human:`, **do not execute the command**. Present the exact `./.lintel/bin/lintel ...` command to the user to run in their terminal and **end your turn**:

| State / Situation | Human Command to Relay |
| :--- | :--- |
| `H.req` (Requirements approval) | `./.lintel/bin/lintel approve requirements` (or `--reject "<why>"`) |
| `H.plan` (Phase plan & contract approval) | `./.lintel/bin/lintel approve plan --phase <N>` (or without `--phase` when no undelivered phase remains, or `--reject "<why>"`) |
| `H.lock` (Locking phase acceptance tests) | `./.lintel/bin/lintel lock --phase <N>` (or `--allow-green <group>`, or `--reject "<why>"`) |
| `H.amend` (Amending tests after DEV/QA block) | `./.lintel/bin/lintel amend --phase <N>` (or `--reject "<why>"`) |
| `O.deliver` (Unacknowledged build/config drift) | `./.lintel/bin/lintel drift --phase <N> --ack` (or `--reject "<why>"`) |
| `O.deliver` (Unacknowledged audit crossings) | `./.lintel/bin/lintel audit --phase <N> --ack` (or `--reject "<why>"` or `reset --phase <N>`) |
| `D.*` (Stalls, post-lock requirement blocks, environment blocks, repeat blocks, final-reject no-change) | `./.lintel/bin/lintel decide --phase <ph> ...` (`--guidance "<text>"`, `--accept`, `--retry`, `--reset`, `--to final\|docs`, `--skip-docs`, `--abort`) |
| `H.final` (Final feature review approval) | `./.lintel/bin/lintel approve final` (or `--reject "<why>"`) |
| `H.docs` (Specs and steering update approval) | `./.lintel/bin/lintel approve docs` (or `--reject "<why>" [--pair po\|architect]`) |
| `H.finish` (Completing task & committing record) | `./.lintel/bin/lintel task finish` (or `./.lintel/bin/lintel task finish --pr`) |
| Config / script / glob fixes mid-task | `./.lintel/bin/lintel amend --config [--release <path>]` |
| Resetting DEV's phase attempt | `./.lintel/bin/lintel reset --phase <N>` |
| Force-abandoning a live-looking stuck run | `./.lintel/bin/lintel task unstick --force` |

When the user returns and says they ran the command, run `./.lintel/bin/lintel task status` and continue with the next action.

---

## 5. Recovery: `task resume`, `task unstick`, and Refusals

1. **Stopped or Dead Persona Run (`T-FIN-40`, `T-FIN-44`, `T-FIN-45`):**
   - If a persona was killed, denied a permission, stopped by a server restart, ended without recording a verdict, or never called `enter`, `task wait` or `task resume` reports `stopped, run lintel task unstick` (and suggests `./.lintel/bin/lintel doctor` if an agent file failed before `enter`).
   - Run `./.lintel/bin/lintel task unstick`. This marks the dead run `abandoned` (counting as no round) and prints a fresh `lintel invoke <role> --phase <ph>` command. Run that `invoke` command and launch the new subagent.
2. **Pending Run Never Entered in Previous Session (`T-FIN-47`):**
   - Running `./.lintel/bin/lintel task resume` in a new chat re-issues a fresh nonce for the pending run and prints the `invoke_subagent` call again. Call `invoke_subagent` with the newly printed prompt and end your turn.
3. **Run Looks Live When User Says It Is Stuck (`T-FIN-51`, `T-FIN-52`):**
   - `./.lintel/bin/lintel task unstick` without `--force` refuses while the run still looks live (`still running` or `quiet for N s`). Tell the user to either let `task wait` finish or run `./.lintel/bin/lintel task unstick --force` in their terminal.
4. **Aborting a Task (`T-FIN-30`):**
   - Only run `./.lintel/bin/lintel task abort` if the user explicitly asks to abort the entire task (and no persona run is currently active).
