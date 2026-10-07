# P1-09 Clarifying questions

**Release:** v1 · **Size:** S · **Type:** feature · **Status:** planned

The Yvoke server's `ask_clarifying_question` tool answers *"Clarifying question asked successfully"*
without asking anyone (design 5.5). yvoke-desktop intercepts it and shows the question; Claude Code does
not. So in a Yvoke session the mod refuses the tool and tells the model to ask with Claude Code's own
`AskUserQuestion`, which shows the user a question prompt and waits for the answer. Task entry:
[plan.md, P1-09](../plan.md#phase-1--knowledge-base-base-instructions-and-playbooks-mvp). Requirement:
[requirements.md](../requirements.md) ("The server's `ask_clarifying_question` is denied in favour of the
native `AskUserQuestion`"). No yvoke-web change (design 5.5: leave the server tool as it is).

## What the user sees

In a Yvoke session, when the model tries the server's question tool, it gets this error instead of a fake
success, and asks again through the native prompt:

> *ask_clarifying_question is not available in Claude Code. To ask the user a clarifying question, use the
> AskUserQuestion tool instead.*

The user sees the refused call as one failed tool line, then Claude Code's question prompt. Outside a
Yvoke session nothing changes.

## Design

- One `tool.call` hook in a new `src/clarify.ts`, registered from `register.tsx` with
  `registerClarify(on)`.
- It opens with the two-line Yvoke check (design 4.4). Outside a Yvoke session it calls `next(e)` and
  nothing else.
- **Which calls it refuses:** any MCP tool whose name ends in `__ask_clarifying_question`, so the
  plugin's own `mcp__plugin_yvoke_yvoke__ask_clarifying_question` and the same tool reached through a
  claude.ai connector (`mcp__<connector>__ask_clarifying_question`, names still to be confirmed by
  P0-04). This is how yvoke-desktop's `isClarificationTool` matches it: by the bare name, whatever the
  server prefix. My default; say if you want only the plugin's own server matched.
- Main loop and subagents alike (`e.agentId` is not checked): no agent in a Yvoke session should get a
  fake "asked".
- **Fails closed:** `.catch(() => ({ deny: <the same message> }))`. If the check itself fails, refusing a
  tool that never asks anyone loses nothing.
- Every other tool goes to `next(e)` untouched. Allowing `AskUserQuestion` itself is P2-01's job (it is
  on P2-01's allow list); nothing denies it today.

## Files that change

| File | Change |
| --- | --- |
| `plugins/yvoke/src/clarify.ts` | New. `registerClarify(on)`: the `tool.call` hook above, and the message as an exported constant. |
| `plugins/yvoke/hooks/register.tsx` | One line: `registerClarify(on)`. |
| `plugins/yvoke/tests/clarify.test.ts` | New. The cases under *Proof*. |
| `docs/specs/clarifying-questions.md` | New spec: behaviour, interfaces, tests, history. Row in `docs/specs/README.md`. |
| `docs/tasks/v1/plan.md` | Tick P1-09 with the PR link. |

**Shared with other threads:** `register.tsx` and `docs/specs/README.md` each get one line, as every
feature task does (P1-07, P3-01, P6-03 and P2-05 are running at the same time). A merge conflict there is
one line to keep from each side. No other file is shared.

## Order of work

Each step: write the test, run it and see it fail, then write the code.

1. In a Yvoke session, `mcp__plugin_yvoke_yvoke__ask_clarifying_question` is denied with the message, and
   the engine beneath never sees the call.
2. The same through a connector name (`mcp__claude_ai_yvoke__ask_clarifying_question`), and from a
   subagent (`agentId` set).
3. Outside a Yvoke session the call reaches the engine unchanged.
4. In a Yvoke session, other tools (`AskUserQuestion`, `mcp__plugin_yvoke_yvoke__search_corpus`, and a
   look-alike such as `mcp__plugin_yvoke_yvoke__ask_clarifying_question_v2`) reach the engine unchanged.
5. A failing `$.state` read denies with the same message (the `.catch`).
6. Spec, plan tick, `npm run check`.

A test's `$` has no `$.state`, so a Yvoke session is made the way the user makes one: `/yvoke` through
`registerSession`, with a small stubbed engine like the one in `session.test.ts`.

## Risks

- **Ordering with P2-01 and P2-02.** yvoke-desktop puts `ask_clarifying_question` in `DEFAULT_KB_TOOLS`.
  If P2-02 ports that list as is, its allow must not reach the tool before this deny. This hook answers
  without calling `next`, so it wins as long as P2-01/P2-02 call `next(e)` for tools they allow, which is
  the chain rule anyway. I will add a line to P2-02's entry: drop `ask_clarifying_question` from the
  ported default set.
- **A wasted turn.** The model still sees the server tool and may try it once per session before it
  switches. Accepted: P2-01 and P2-02 decide which tools are listed. Hiding or rewording it with a
  `tool.describe` hook was considered and left out, because the listing belongs to those tasks.
- **Subagents and `AskUserQuestion`.** If Claude Code does not offer `AskUserQuestion` to subagents, a
  specialist told to use it cannot. Then it answers without asking, which is still better than a fake
  "asked". P6-04 decides who may ask the user in a multi-agent run.
- **Considered and rejected:** answering the tool call with a result instead of a deny (for example by
  calling `AskUserQuestion` from the mod). The mod API has no way to put a question to the user from a
  `tool.call` hook, and a deny with a pointer is what the task asks for.

## Proof

- `plugins/yvoke/tests/clarify.test.ts`: the cases in *Order of work* 1 to 5. Case 1 is P1-09's **Done
  when** ("a test shows the deny and its message").
- `npm run check`: docs, both `validate --strict` runs (the `calls:` line names no `$.fs`, `$.process` or
  `$.http`), typecheck, tests.
