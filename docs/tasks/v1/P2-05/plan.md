# P2-05 Safe compute tools

**Release:** v1 · **Size:** M · **Type:** feature · **Status:** planned

The mod gives the model three small tools, `calculate`, `statistics` and `date_diff`, so it can do
arithmetic, summary statistics and date maths without `Bash`. They are ported from yvoke-desktop's
`src/main/agent/computeTools.ts` (the maths) and `computeServer.ts` (names, descriptions, schemas, result
shapes), with its `tests/computeTools.test.ts`. Task entry:
[plan.md, P2-05](../plan.md#phase-2--tool-policy).

## What was checked first

Checked in a cloud session on **Claude Code 2.1.293** on 2026-10-07, against the generated declarations
and a throwaway copy of the plugin run with `claude plugin test` and `claude plugin validate --strict`:

- `$.tool.register({ name, description, inputSchema })` inside `session.start` (awaited, after `next(e)`)
  declares the tool; it answers `{ tool: 'mcp__yvoke__calculate' }`. The name cannot collide with the
  server's tools, which are `mcp__plugin_yvoke_yvoke__<tool>` (D-05).
- A `tool.call` hook on `{ tool: 'mcp__yvoke__calculate' }` serves the call: the tool's arguments sit flat
  on `e` (`e.expression`), and the hook returns `{ result }` (with `isError: true` for a failure). A call
  no hook answers fails in the engine.
- A registered tool cannot be withdrawn or hidden: there is no unregister, and `tool.describe` changes only
  the description and whether the tool waits behind ToolSearch. `session.start` fires once per process, so
  the three tools are listed in **every** session of the user, not only Yvoke sessions. Left to the engine,
  they wait behind ToolSearch like any MCP tool, so a plain session carries only their names.
- `validate` flags a `tool.call` hook without `.catch` as a gating hook; each one gets a catch.

## What the user sees

In a Yvoke session the model can call:

| Tool | Input | Answer |
| --- | --- | --- |
| `calculate` | `expression` | `{ expression, result }` |
| `statistics` | `values` (numbers) | `count`, `sum`, `mean`, `median`, `min`, `max`, `range`, sample `variance` and `stdev` |
| `date_diff` | `from`, `to`, optional `unit` (default `days`) | `{ from, to, unit, difference }`, signed (`to − from`) |

A bad input answers as an error the model reads (`calculate error: unknown function: foo`), as in
yvoke-desktop. The `calculate` description now says plainly that `log` is base 10 and `ln` the natural
logarithm (fix named in the task entry); `log10` and `log2` stay. Nothing is drawn.

**Outside a Yvoke session** (decision pending, see below; default A): a call to one of the three tools is
refused with *"Yvoke: this tool works only in a Yvoke session. Type /clear, then /yvoke, to start one."*

## Two choices in this plan

1. **For Eduard: what the tools do outside a Yvoke session.** Registration cannot be limited to Yvoke
   sessions (see above), so the model in a plain session can find them through ToolSearch.
   - **A (recommended): refuse the call with the line above.** Closest to D-13 ("every other session
     behaves as if the mod were not there"): the tools are listed but do nothing.
   - B: serve them everywhere. They are pure maths with no shell, files or network, so they are harmless,
     but a plain session then gets new behaviour from the mod.
2. **Default I picked: the `codeExecution: false` gate moves to P2-02.** Withholding the tools needs the
   session's locked playbook, which only P1-08 provides, and P2-02 (per-playbook tool scoping) already
   waits on P1-08 and owns exactly this decision in yvoke-desktop (`policy.ts`, `isToolAllowed` and
   `buildAllowedTools`). P2-05 exports the three full tool names (`COMPUTE_TOOLS`) for P2-01 and P2-02
   to use. The plan's P2-05 and P2-02 entries and requirements 2.2 are updated to say so. The rule itself
   is unchanged: an undeclared `codeExecution` allows the tools, only `false` withholds them.

## Files that change

| File | Change |
| --- | --- |
| `plugins/yvoke/src/compute.ts` | New. The maths, ported unchanged: `safeCalculate` (hand-written parser, no `eval`, whitelisted functions and constants, 1,000-character limit), `computeStatistics`, `dateDiff`. No `$`, no hooks. |
| `plugins/yvoke/src/computeTools.ts` | New. `registerComputeTools(on)`: registers the three tools in `session.start`, serves them in `tool.call`, refuses outside a Yvoke session, and exports `COMPUTE_TOOLS`. |
| `plugins/yvoke/hooks/register.tsx` | One line: `registerComputeTools(on)`. |
| `plugins/yvoke/tests/compute.test.ts` | New. `computeTools.test.ts` ported case for case (vitest `describe`/`it` become `test`). |
| `plugins/yvoke/tests/computeTools.test.ts` | New. Registration, serving, errors, plain session, failure paths. |
| `docs/specs/compute-tools.md`, `docs/specs/README.md` | New spec and its row. |
| `docs/tasks/v1/plan.md`, `requirements.md` | P2-05 ticked; the `codeExecution` gate moved to P2-02 (choice 2). |

Other threads running now (P1-07, P0-03, P1-09, P3-01, P6-03) also add one line to `register.tsx` and
may touch `plan.md`, `requirements.md` and `docs/specs/README.md`; those are one-line merges. No yvoke-web
change is needed: `list_playbooks` already returns `codeExecution` (P1-06).

## Order of work

Each step starts with its test, run and seen failing first.

1. **Maths.** Port `computeTools.test.ts` into `tests/compute.test.ts` (precedence, right-associative
   `^`, `**`, functions and constants, the twelve hostile expressions, malformed input, `1 / 0`,
   statistics on 8, 1 and 0 values, non-finite values, signed date differences and invalid dates). Add a
   case pinning `log(100) = 2` and `ln(e) = 1`, and one for the 1,000-character limit. Then port
   `src/compute.ts`.
2. **Registration.** Test: after `session.start`, exactly the three tools are registered with their
   names, descriptions and input schemas, the `calculate` description names `log` as base 10 and `ln` as
   natural, and `session.start`'s own answer comes back unchanged. Then implement.
3. **Serving in a Yvoke session.** Tests (session made Yvoke by typing `/yvoke`, as in
   `session.test.ts`): each tool answers its shape; `date_diff` defaults to days; a bad expression, a
   non-number in `values` and an unknown unit answer `isError: true` with the tool's error line; other
   tools' calls reach the engine unchanged.
4. **Plain session.** Test: before `/yvoke`, and after `/clear`, each call is refused with the line
   above (or served, if Eduard picks B).
5. **Failure paths.** Test: a hook that throws (forced through a stubbed `$.state` read that fails)
   answers the catch's refusal, `Yvoke: the calculation could not be run.` A hook running past its budget
   takes the same catch.
6. **Docs.** Spec `compute-tools.md`, plan and requirements updates, then `finish-task`.

## Risks

- **The tools are visible in every session** (no unregister). Alternatives considered: registering on
  `/yvoke` instead of `session.start` keeps a plain session clean only until a `/clear` after `/yvoke`,
  and needs a second registration on `/resume`, which `src/session.ts` owns; rejected as more moving parts
  for a partial gain, and design 4.3 already says `session.start`.
- **Hook order with P2-01.** P2-01's deny-by-default `tool.call` hook must let `COMPUTE_TOOLS` through,
  and P2-02 will deny them for `codeExecution: false`. Both import `COMPUTE_TOOLS` from this module, so the
  names are written once.
- **Deferred or listed.** The tools wait behind ToolSearch (the engine's default for MCP-style tools).
  P2-01 allows `ToolSearch`, so the model can load them. If testing on Eduard's machine shows the model
  never finds them, `isDeferred: false` in a Yvoke session is a one-line change; not done now because it
  would put three schemas into every plain session's prompt.
- **Results the engine maps.** A hook's `{ result }` object reaches the model through the engine's own
  mapping (checked in the spike for the object shape, not for the text the model reads). Checked in
  P0-06 on a real session; if the model sees something unreadable, return the JSON text as
  yvoke-desktop did.

## Proof

- `tests/compute.test.ts`: every yvoke-desktop case, plus `log` vs `ln` and the length limit.
- `tests/computeTools.test.ts`: registration, the three answers, errors, the plain-session refusal, the
  catch.
- `npm run check` (docs, both `validate --strict` runs with no `$.fs`, `$.process` or `$.http` on the
  `calls:` line, types, tests), output in the pull request.
