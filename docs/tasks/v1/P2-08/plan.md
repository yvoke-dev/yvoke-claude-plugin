# P2-08 Turn ceilings per question

**Release:** v1 · **Size:** M · **Type:** feature · **Status:** planned

A Yvoke question may make only so many model requests per loop. At the ceiling the mod does not throw the
work away, as yvoke-desktop does (`error_max_turns` ends its turn as a failure): it tells the model to
answer now with what it has, and marks the answer *stopped at the turn limit*. Task entry:
[plan.md, P2-08](../plan.md#phase-2--tool-policy); requirement
[2.2](../requirements.md#22-tool-policy); intent U12.

## What was checked first

Checked in a cloud session on **Claude Code 2.1.304** on 2026-10-20, against the generated declarations
and a throwaway test run with `claude plugin test`:

- `turn.step` fires once per model request, with `turnId` (one per question on the main loop, one per run
  of a subagent) and `index`, the request's position in the turn from 0. So the count needs no counter of
  its own: request number = `index + 1`. `agentId` is absent on the main loop and set in a subagent.
- `turn.step` is a streaming hook (`async function*`). A hook may drop chunks, and *"a block whose `tool`
  chunk never comes out of the chain is no tool call: the engine records none and runs none."* That is the
  hard stop. It cannot add a message or change the tool list (mod rule 7), so the "answer now" message has
  to travel as a denied tool call: `tool.call` → `{ deny }`.
- `tool.call` carries `agentId` but no `turnId` or step index. The step therefore records where its loop
  stands in `$.state`, in a **state family** keyed by loop (`id`: the `agentId`, or `main`), so parallel
  specialists never write over each other.
- `register(on, options)` receives the plugin's `userConfig` values with defaults filled in, and a test sets
  them with `test(name, { options }, body)`.
- A test can drive `$.turn.step(...)` and read the chunks it yields, and can answer the plugin's
  `$.state.get` and `$.state.set` with hooks beneath it, so every role can be staged without Phase 6.
- yvoke-desktop 1.3.0 ships single agent 25 (its `settings.json`; the code default is 0, "no ceiling"),
  lead 60, specialist 20 (`DEFAULT_ORCHESTRATOR_SETTINGS`), and the reviewer **6**, hard-coded in
  `AgentService.ts`. The plan entry's "reviewer 20" is not the desktop's value; that is the open decision
  below. Its tests only check that `error_max_turns` becomes a `Claude:` error, which is the behaviour this
  task replaces, so there are no ceiling cases to port as they are; the cases below are their replacement.

## What the user sees

The ceiling `N` is the most model requests one loop may make for one question (a specialist or the
reviewer: for one run). Requests 1 to `N − 1` run as usual.

1. The tool calls asked for by request `N − 1` are **denied** with: *Yvoke: this question has reached its
   turn limit. Do not call any more tools. Answer now with what you have found, and say plainly that the
   answer may be incomplete.* The model reads this as the tool results.
2. Request `N` is the answer. If it still asks for tools, those calls are dropped from the response, so
   the engine runs none and the turn ends with the text written so far.
3. On the main loop, a turn that reached request `N` gets one line under the answer: *Yvoke: stopped at the
   turn limit (N model requests). The answer may be incomplete.* The transcript itself is not changed.
4. A specialist at its ceiling ends its run the same way, so its text, marked incomplete by itself, is the
   report the lead receives. No line is added under a subagent's report.

A question that finishes before request `N − 1` asks for tools sees nothing of this. Outside a Yvoke
session nothing changes.

**Ceilings** (plugin settings, read once when the mod loads, like `serverUrl`):

| Setting | Default | Applies to |
| --- | --- | --- |
| `turnLimitSingle` | 25 | the main loop in a single-agent session |
| `turnLimitLead` | 60 | the main loop in a multi-agent session (`yvoke.setup.mode` set and not `single`) |
| `turnLimitAgent` | 20 | every subagent: each specialist and the reviewer (*if the decision below says 20*) |

`0` turns a ceiling off, as in yvoke-desktop. Any other value below 2 counts as 2, because the mechanism
needs one request to warn and one to answer.

## Open decision (one card in the thread)

**Reviewer ceiling.** Recommended: **20, one setting for all subagents** (as the plan entry says). It needs
no way to tell the reviewer from a specialist, and a reviewer stopped at its ceiling cannot call
`submit_review`, which counts as a rejection (P6-03), so a low ceiling causes extra revision rounds. The
alternative is the desktop's 6 with a separate `turnLimitReviewer` setting, matched on the reviewer's agent
name, which P6-02 would then have to use. The answer is recorded in `design.md` as D-24, and the plan
entry's "(yvoke-desktop's shipped values)" is corrected either way.

## Design

- **`src/turn-limits.ts`** (new), two parts, so the rules are testable without the engine:
  - pure functions: `ceilings(options)` reads the three settings (missing, 0, below 2, not a number);
    `ceilingFor(ceilings, agentId, mode)` picks the role's ceiling or `null` (off); `isWrapUp(index, n)`
    (`index ≥ n − 2`: this request's tool calls are denied) and `isLast(index, n)` (`index ≥ n − 1`: its
    tool calls are dropped); the two message texts.
  - `registerTurnLimits(on, options)`, three hooks, each opening with the `yvokeSession` check:
    - `turn.step`: reads `yvoke.setup` (main loop only), writes `yvoke.loopStep` (`id`: `agentId ?? 'main'`)
      = `{ turnId, index, n }` **before** `next(e)`, then streams. On the last request it drops `tool` and
      `input` chunks of tool blocks and turns a `tool_use` stop reason into `end_turn`, in the stop chunk
      and in the result.
    - `tool.call`: reads `yvoke.loopStep` for `e.agentId ?? 'main'`; when the recorded index is at or past
      `n − 2`, answers `{ deny: WRAP_UP }`. `.catch` → `{ deny: 'Yvoke: the turn-limit check failed, so this
      tool was not run.' }`.
    - `turn.complete` (main loop only): `next(e)` first; when `loopStep` for `main` names this `turnId` and
      reached `n − 1`, returns the answer's text with the flag line beneath.
- **`hooks/register.tsx`**: one line, `registerTurnLimits(on, options)`; `register` takes `options`.
- **`types/index.d.ts`**: `loopStep: StateFamily<{ turnId: string; index: number; n: number } | null>`, and
  `setup` with the shape PR #17 and PR #22 declare (`{ area, mode, playbook } | null`, plus their added
  fields). Whichever PR merges later rebases; the read here only uses `mode`.
- **`.claude-plugin/plugin.json`**: the three `userConfig` number fields with titles, descriptions and
  defaults.

**Fails closed** (requirements 1):

- `tool.call` throws or overruns: the call is denied (`.catch`).
- `turn.step`'s own work (reading the setup, writing `loopStep`) throws: the step is treated as the last
  one, so its tool calls are dropped and the loop ends with the text so far. The hook catches this itself
  before calling `next`, because a streaming hook that fails mid-stream is simply left by the engine (fail
  open). A `.catch` on the registration covers a throw before the first chunk the same way.
- `turn.complete` throws: the answer stands without the flag line. It enforces nothing, so failing open
  here only loses the line, never the answer.

## Files that change

| File | Change |
| --- | --- |
| `plugins/yvoke/src/turn-limits.ts` | new: pure rules and the three hooks |
| `plugins/yvoke/hooks/register.tsx` | `register(on, options)`; one line `registerTurnLimits(on, options)` |
| `plugins/yvoke/types/index.d.ts` | `loopStep` family and `setup` keys |
| `plugins/yvoke/.claude-plugin/plugin.json` | `turnLimitSingle`, `turnLimitLead`, `turnLimitAgent` |
| `plugins/yvoke/tests/turn-limits-rules.test.ts` | new: the pure functions |
| `plugins/yvoke/tests/turn-limits.test.ts` | new: the hooks through the engine |
| `docs/specs/turn-limits.md`, `docs/specs/README.md` | new spec, listed |
| `README.md` | the three settings next to `serverUrl` and `devMode` |
| `docs/tasks/v1/plan.md`, `design.md`, `requirements.md` | tick P2-08; D-24; correct "desktop's shipped values" |

## Order of work

Each step: write the test, run it and see it fail, then the code, then `npm run check`.

1. **Rules.** `ceilings` (defaults, 0 = off, 1 → 2, non-numbers → default) and `ceilingFor` for main
   single, main multi-agent, subagent; `isWrapUp` / `isLast` at `n − 3`, `n − 2`, `n − 1`, `n`.
2. **Outside a Yvoke session** all three hooks pass `next(e)` untouched: a step past any ceiling keeps its
   tool chunks, a tool call reaches the engine, `turn.complete` adds nothing.
3. **Under the limit** (single agent, step index 0 and 22 of 25): chunks unchanged, tool call runs.
4. **At the limit, single agent 25**: after step index 23, the tool call is denied with the wrap-up text;
   step index 24 yields its text, no `tool`/`input` chunks, stop `end_turn`, result `toolUses` empty.
5. **Each role**: lead 60 (setup mode `multi`), a subagent 20 (`agentId` set), and two subagents
   interleaved, each counted on its own (state family). A custom setting (`turnLimitSingle: 5`) and `0`.
6. **The flag line**: `turn.complete` of a main-loop turn that reached request `N` returns the answer plus
   the line; a turn that did not, or another turn id, returns it unchanged; a subagent's gets nothing.
7. **Hook failure**: `tool.call` with `$.state.get` throwing → denied with the failure text; `turn.step`
   with `$.state.set` throwing → tool chunks dropped; `turn.complete` throwing → answer unchanged. Plus a
   `tool.call` that overruns its budget, if the test kit can stage it (P1-07 found it cannot; then say so
   in the PR as for P1-07).
8. Spec, README, plan tick, D-24; `finish-task`.

## Risks

- **The model ignores the denial and keeps asking for tools at request `N`.** Covered: those calls are
  dropped, so the loop ends regardless. The cost is that request `N`'s text may be only a preamble ("Let
  me search…"); the flag line says the answer may be incomplete.
- **Interplay with the tool gate (P2-01/P2-03).** Both are `tool.call` hooks in their own files; either
  one's deny ends the call, so the order does not matter. No shared file other than `register.tsx` (one
  line each).
- **Phase 6 tools at the ceiling.** All tool calls are denied at the ceiling, `submit_answer` and
  `submit_review` included. A lead out of requests then has no `submit_answer`, which P6-04 handles as a
  missing submission; a reviewer has no verdict, which counts as a rejection. Phase 6 may exempt its own
  submit tools from the wrap-up denial; noted for P6-04 and P6-03, not done here.
- **`$.state` families and `register(on, options)` on Claude Code 2.1.287.** Checked only on 2.1.304 so
  far; CI runs 2.1.287. If either is missing there, the fallback is one `$.state` key per role (`main`,
  and a map for subagents written with `ifVersion` to avoid lost updates), and settings read with
  `$.config.list()`.
- **A question that continues in a new turn** (a background task waking the session) starts a new
  `turnId`, so its count starts again. Accepted: the ceiling is per model turn, as yvoke-desktop's
  `maxTurns` is per query.
- **Rejected alternatives.** Counting in a module variable: lost on hot reload and shared by every session
  in the process. Counting `tool.call`s: the ceiling is on model requests, and parallel tool calls in one
  request would count several times. Ending the turn with `$.turn.abort`: it discards the answer instead of
  delivering it, and works on the main loop only. Dropping tool calls with no warning first: the model
  would end on a preamble almost every time.

## Proof

- `tests/turn-limits-rules.test.ts` and `tests/turn-limits.test.ts`: under the limit, at the limit for each
  role (single, lead, specialist/reviewer), the flag line, and a hook failure (the task's **Done when**).
- `npm run check` (docs, both validates, typecheck, tests) with its output in the PR; the `calls:` line
  names no `$.fs`, `$.process` or `$.http`. CI on Claude Code 2.1.287 and latest.
- Not checkable in the cloud, left for a run on a developer's machine before the box is ticked: a real
  question with `turnLimitSingle` set to 3, to see the denial, the answer and the flag line drawn.
