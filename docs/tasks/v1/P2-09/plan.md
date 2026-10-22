# P2-09 The gate refuses the mod's own server calls

**Release:** v1 · **Size:** S · **Type:** bug fix · **Status:** planned

After #28 (P2-02), `/yvoke` cannot load its lists: the band shows *Yvoke Backend: yvoke:
$.mcp.call(plugin:yvoke:yvoke, list_areas) refused: Yvoke: the session's playbook could not be read, so
this tool was not run.* Found by Eduard in the Claude Desktop Code tab on Claude Code 2.1.308.

## What was checked

- **Reproduced** in a cloud session on Claude Code 2.1.308, with a copy of `main` loaded with
  `--plugin-dir`, its `.mcp.json` pointed at a stub MCP server answering `list_areas` and
  `list_playbooks`. `claude -p "/yvoke"` logs:
  `$.mcp.call (yvoke): mcp__plugin_yvoke_yvoke__list_areas` → `$.tool.call (yvoke): …list_areas` →
  `tool.call … resolved by a hooks module (deny: Yvoke: the session's playbook could not be read…)`.
  The stub never received a `tools/call`.
- **Cause.** In the engine, `$.mcp.call` is a `$.tool.call` with the plugin as origin (2.1.308's
  `$.mcp.call` handler calls the `$.tool.call` path, and the declarations say "the plugin's call, seen by
  the hooks above it, is the grant"). Only the calling hook is skipped, so the setup's call reaches the
  plugin's own `tool.call` gate with `next.origin.plugin === 'yvoke'`. Before #28 the gate allowed every
  tool under the server's prefix, which happened to cover these calls. Since #28 it allows only the
  playbook's tools, and at `/yvoke` there is no setup yet, so it refuses with `PLAYBOOK_UNKNOWN`.
- **Wider than `/yvoke`.** With a setup in place the same calls are still refused: `list_areas`,
  `list_playbooks` (P1-08), `get_system_prompt` and `get_playbook` (P1-07) are not in any playbook's set.
  So setup Retry, `/resume`, `/branch` and P1-07's first-question load all fail the same way. A main loop
  that ended at its turn limit (P2-08) would also refuse them with the wrap-up message until the next
  request.
- **Why the tests missed it.** The test kit answers the mod's `$.mcp.call` through the `mcp.call` event
  and never through `tool.call`, and a test hook cannot call `$.tool.call` (the kit refuses it). So no
  test can drive a call with origin `yvoke` through the gate.

## Fix

The gate rules the **model's** calls (D-09). A call the mod itself makes to the Yvoke server passes
first, right after the Yvoke-session check and before the turn limit and the playbook:

- `next.origin.plugin === 'yvoke'` **and** the tool is under the Yvoke server's prefix → `next(e)`.
- Anything else the mod might call itself (its own `Bash` for `execute_code`) stays as today: P2-05 adds
  its own exception with its own test.
- The model cannot set the origin: the host sets it from where the call came from.

The decision is a pure function, `isOwnServerCall(caller, tool, serverPrefix)`, so it can be tested
without the engine.

## Files that change

| File | Change |
| --- | --- |
| `plugins/yvoke/tests/policy-rules.test.ts` | New. The regression test: `isOwnServerCall` for the mod's calls to `list_areas`, `list_playbooks`, `get_system_prompt`, `get_playbook` under the plugin and connector prefixes; the engine's (model's) calls; another plugin's calls; the mod calling another server; not connected. |
| `plugins/yvoke/src/policy.ts` | `isOwnServerCall`; the gate calls it first for an `mcp__` tool, before the turn limit. |
| `docs/specs/tool-policy.md` | Behaviour: the mod's own calls to the server pass; Interfaces; Tests; History. A note that the kit cannot drive this path. |
| `docs/tasks/v1/plan.md` | Add and tick P2-09 in Phase 2. |
| `docs/tasks/v1/design.md` | 4.3 or 4.4: `$.mcp.call` reaches the plugin's own `tool.call` hooks with origin = the plugin; the test kit does not show it. |

## Order of work

1. Write `policy-rules.test.ts`, run it and see it fail (`isOwnServerCall` does not exist), commit it on
   its own; put its hash on the **Regression test** line. The test freeze is then on.
2. Add `isOwnServerCall` and call it in the gate. Run the tests.
3. Re-run the stub-server repro on 2.1.308 with the fixed copy: the stub receives `tools/call` for
   `list_areas` and `list_playbooks`, and nothing is refused.
4. Spec, design note, `plan.md`; `finish-task`.

## Risks

- **The wiring line is not covered by a test**, only the decision is: the kit cannot raise a `tool.call`
  with origin `yvoke` (see above). Step 3's real-session run is the proof for the wiring, and the PR shows
  its log. Mutation-checking the predicate keeps the unit test honest.
- **A mod call skips the turn limit and the playbook.** Intended: neither rule is about the mod's own
  loading; the model's calls are unchanged.
- **Considered and rejected:**
  - Adding the mod's tools (`list_areas`, …) to every playbook's set: the model could then call them.
  - Passing every call with origin `yvoke`: would also pass the mod's future `Bash` before P2-05's own
    checks exist. Kept to the Yvoke server.
  - Calling the server some other way than `$.mcp.call`: there is none for a mod.

## Proof

- `plugins/yvoke/tests/policy-rules.test.ts` red before the fix, green after.
- The repro log from step 3 in the pull request.
- `npm run check` (all five rows of AGENTS.md), with output in the pull request.
