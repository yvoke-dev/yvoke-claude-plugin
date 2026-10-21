# Turn ceilings

## Behaviour

- In a Yvoke session, each loop may make at most `n` model requests for one question: the main loop per
  question, and each specialist or reviewer run once per run. Outside a Yvoke session nothing is counted or
  changed.
- Requests 1 to `n − 1` run as usual. The tool calls asked for by request `n − 1` are denied with:
  *Yvoke: this question has reached its turn limit. Do not call any more tools. Answer now with what you
  have found, and say plainly that the answer may be incomplete.*
- Request `n` is the answer. Any tool calls it still asks for are dropped from the response (a `tool_use`
  stop becomes `end_turn`), so Claude Code runs none and the turn ends with the text written so far.
- A main-loop answer that reached request `n` gets one line beneath it: *Yvoke: stopped at the turn limit
  (n model requests). The answer may be incomplete.* The transcript is not changed. A subagent's report
  gets no line; its own text, told to say it may be incomplete, is what the lead receives.
- Ceilings: single agent 25, lead 60 (the main loop when the setup's mode is set and not `single`), every
  subagent 20, specialists and reviewer alike (D-24). `0` turns a ceiling off; any other value below 2
  counts as 2; a fraction is rounded down.
- At the limit every tool call is refused with the wrap-up message, also one the tool policy would refuse,
  so the model is told to answer rather than pointed to other tools.
- Fails closed: when the tool-call check fails, the tool is denied with *Yvoke: the turn-limit check
  failed, so this tool was not run.* When a request cannot read the setup or record where its loop stands,
  it is treated as the last one and its tool calls are dropped. When the end-of-turn hook fails, the answer
  stands without the line.

## Interfaces

| Piece | Where |
| --- | --- |
| Settings `turnLimitSingle` (25), `turnLimitLead` (60), `turnLimitAgent` (20), read when the mod loads | `plugins/yvoke/.claude-plugin/plugin.json` `userConfig` |
| `turn.step`: records the loop's place, drops tool calls on the last request | `plugins/yvoke/src/turn-limits.ts` |
| The denial at request `n − 1` and later: the first check of the one `tool.call` gate, before the tool policy | `plugins/yvoke/src/policy.ts` |
| `turn.complete`: the line under a main-loop answer | `plugins/yvoke/src/turn-limits.ts` |
| `$.state` `yvoke.loopStep`, a family keyed by loop (`id`: the subagent's id, or `main`) → `{ turnId, index, n }` or null | contract in `plugins/yvoke/types/index.d.ts` |
| `$.state` `yvoke.setup` (read for `mode`, written by the setup band) | same contract |

The request number comes from `turn.step`'s own `index`, so nothing is counted in module variables, and
parallel specialists each write their own family member.

## Tests

- `plugins/yvoke/tests/turn-limits-rules.test.ts`: the settings (defaults, 0, below 2, fractions, text),
  the ceiling per role, and where a request stands.
- `plugins/yvoke/tests/turn-limits.test.ts`, through the engine with `$.state` stubbed beneath the plugin:
  outside a Yvoke session; under the limit; the wrap-up denial; the last request; the flag line and when
  it is not shown; the lead, the main loop in single-agent mode, a subagent, two subagents side by side;
  settings 5 and 0; a failure of each hook; and the order against the tool policy (a refused tool at the
  limit, a failed turn-limit check, a failed policy check).
- Not checked in tests: a hook that overruns its budget (the test kit cannot stage it), and the line as
  drawn in a real session.

## History

- P2-08 ([#26](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/26)): turn ceilings, delivered
  rather than discarded at the limit; D-24 gives the reviewer the specialists' 20.
