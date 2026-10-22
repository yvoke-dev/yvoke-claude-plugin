# P1-09 Clarifying questions

**Release:** v1 · **Size:** S · **Type:** feature · **Status:** planned

The Yvoke server's `ask_clarifying_question` tool answers *"Clarifying question asked successfully"*
without asking anyone (design 5.5). yvoke-desktop treats it as an alias of the native question tool: it
catches the call, shows the question, and hands the model *"User answered: …"* (`buildCanUseTool` and
`isClarificationTool` in its `src/main/agent/policy.ts` and `src/shared/types.ts`). The plugin does the
same in Claude Code: in a Yvoke session the server tool puts its question to the user in Claude Code's own
question dialog, and the model gets the answer. Task entry:
[plan.md, P1-09](../plan.md#phase-1--knowledge-base-base-instructions-and-playbooks-mvp). Decision: D-28
(Eduard, 2026-10-22: an alias, not a deny). No yvoke-web change (design 5.5: leave the server tool as it is).

## What the user sees

In a Yvoke session, when the model calls the server's `ask_clarifying_question`, Claude Code's question
dialog opens with the model's question and its suggested answers, the same dialog `AskUserQuestion`
opens, with "Other" for a typed answer. The user picks or types; the model reads
*User answered: <answer>* and carries on. `AskUserQuestion` itself keeps working as before. Outside a
Yvoke session nothing changes.

## What was checked first

On **Claude Code 2.1.308** (the generated declarations):

- `$.ui.ask(question, { options })` opens the engine's own AskUserQuestion dialog and resolves to the
  label chosen, or the text typed under "Other". It takes 2 to 4 option labels (fewer are padded with
  Yes/No). It rejects when the user dismisses the dialog, and in a `-p` run. It runs as a `tool.call` of
  `AskUserQuestion` through every hook but the calling one, so the policy gate does not stop it.
- Waiting on a `$` call is free of the hook's 10 s budget (`HookBudget`), so the gate may wait for the
  user as long as they take.
- The plugin may have only one unmatched `tool.call` hook (`src/policy.ts` says so), so the alias is a
  step in that hook, not a hook of its own.
- yvoke-web's tool takes `question` (required) and `options` (an optional list of strings).

## Design

- **Where:** in the policy gate (`src/policy.ts`), after the call has passed the checks. If the tool is
  the Yvoke server's `ask_clarifying_question` (its full name under the prefix the gate already works
  out, so the plugin's entry or the claude.ai connector's), the gate answers the call itself and does
  not call `next(e)`:
  - `question` and `options` are read from the call by a small pure function in a new
    `src/clarify.ts` (no `$`), and passed to `$.ui.ask`.
  - The answer goes back as the tool's result: `{ result: 'User answered: <answer>' }`, yvoke-desktop's
    wording (`CLARIFICATION_ANSWER_PREFIX`).
- **Granting stays the playbook's.** The gate decides first, as for every server tool: the default set
  includes `ask_clarifying_question`; a playbook that declares tools gets it only if it declares it.
  When it is refused, the model still has `AskUserQuestion`.
- **Options.** More than 4: the first 4 are shown, and the others can still be typed under "Other".
  Fewer than 2: `$.ui.ask` adds Yes/No; for an open question with no options the user types under
  "Other". Empty or blank labels are dropped. A missing or blank `question` is answered with an error
  result telling the model to ask with a question, without opening a dialog.
- **Dismissed, interrupted or `-p`:** `$.ui.ask` rejects, and the model reads yvoke-desktop's
  *Clarifying questions cannot be asked in this execution context.* The call is not refused as a policy
  failure, because the check had passed.
- **Subagents:** the same alias. If a specialist asks, the user sees the question.
- No other tool changes, and `AskUserQuestion` stays allowed as P2-01 has it.

## Files that change

| File | Change |
| --- | --- |
| `plugins/yvoke/src/clarify.ts` | New. `clarifyingQuestion(e)`: the call's arguments → `{ question, options }` or a reason it cannot be asked; `ANSWER_PREFIX`, `CANNOT_ASK`. No `$`. |
| `plugins/yvoke/src/policy.ts` | The alias step after the grant check, as above. |
| `plugins/yvoke/tests/clarify.test.ts` | New. The cases under *Proof*. |
| `docs/specs/tool-policy.md` | `ask_clarifying_question` asks the user; tests line. |
| `docs/tasks/v1/requirements.md` | The P1-09 line: the server's tool asks with the native dialog, instead of "is denied" (with this plan). |
| `docs/tasks/v1/design.md` | D-28; section 5.5's line on the tool (with this plan). |
| `docs/tasks/v1/plan.md` | P1-09's entry and its **Done when** (with this plan); the tick with the PR link at the end. |

**Shared with other threads:** `src/policy.ts` and `docs/specs/tool-policy.md` are the policy gate's
(P2-01/P2-02, merged). Any thread changing them at the same time (P2-04 web tools, P2-05 compute,
P6-04 delegation) adds its own class there, so a conflict is a few lines to keep from each side.

## Order of work

Each step: write the test, run it and see it fail, then write the code.

1. In a Yvoke session, the plugin's `ask_clarifying_question` with a question and three options opens
   the dialog with that question and those options; the model gets `User answered: <label>`, and the
   server is never called.
2. An answer typed under "Other" comes back as typed.
3. The same through the connector's name, and from a subagent.
4. Options: more than 4 (first 4), none, blank labels dropped; a blank question gets the error result
   and no dialog.
5. Dismissed (the dialog rejects): the *cannot be asked* result.
6. A playbook that does not declare the tool: refused as today, no dialog.
7. Outside a Yvoke session the call reaches the engine unchanged; `AskUserQuestion` still passes.
8. Docs and spec, plan tick, `npm run check`.

## Risks

- **Yes/No padding.** With fewer than two suggested answers the dialog offers Yes/No, which can read
  oddly for an open question ("Which version?"). The user can still type under "Other". If it bothers
  in use, the mod can instead ask the model to re-ask with `AskUserQuestion` for such questions.
- **What the dialog returns in a test.** `$.ui.ask` dispatches a `tool.call` of `AskUserQuestion`
  beneath the plugin; the test answers it. The exact result shape the engine reads the answer from is
  checked in step 1 and noted in the spec.
- **Considered and rejected:** refusing the tool with a pointer to `AskUserQuestion` (the first version
  of this plan): it costs the model a failed call and a retry every time, and yvoke-desktop's alias is
  the behaviour users already know. Removing the tool from the playbook's grant: playbooks name it and
  the server's prompts tell the model to call it, so it must work.

## Proof

- `plugins/yvoke/tests/clarify.test.ts`: the cases in *Order of work* 1 to 7. Step 1 is P1-09's **Done
  when**.
- `npm run check`: docs, both `validate --strict` runs (the `calls:` line names no `$.fs`, `$.process` or
  `$.http`), typecheck, tests.
