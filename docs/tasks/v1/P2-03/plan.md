# P2-03 Re-namespacing

**Release:** v1 · **Size:** S · **Type:** feature · **Status:** done

Playbooks name their tools by base name (`search_corpus`), or under whatever prefix was current when they
were written: yvoke-desktop's `mcp__yvoke__search_corpus`, or the older `mcp__oim__search_corpus`. The mod
turns each declared name into the name the model actually calls in this session, using the server prefix
P2-01 already finds. Task entry: [plan.md, P2-03](../plan.md#phase-2--tool-policy). Ported from
yvoke-desktop's `qualifyTool` and `builtinTool` (`src/shared/types.ts`) and their tests in
`tests/policy.test.ts` (`describe('qualifyTool')`) and `tests/types.test.ts`.

## What it does

`qualifyTool(tool, serverPrefix)` in `src/tool-names.ts`, where `serverPrefix` is what P2-01's
`serverToolPrefix` gives for the server `$.mcp.connect('yvoke')` names (`mcp__plugin_yvoke_yvoke__`, or a
connector's):

| Declared | Result (plugin entry) |
| --- | --- |
| `search_corpus` | `mcp__plugin_yvoke_yvoke__search_corpus` |
| `mcp__yvoke__search_corpus` (yvoke-desktop's prefix) | `mcp__plugin_yvoke_yvoke__search_corpus` |
| `mcp__oim__search_corpus` (an older alias) | `mcp__plugin_yvoke_yvoke__search_corpus` |
| `mcp__plugin_yvoke_yvoke__search_corpus` (already current) | unchanged |
| `  search_corpus  ` | trimmed, then as above |
| `WebSearch`, `webfetch`, `ToolSearch`, `AskUserQuestion`, also behind a prefix (`mcp__oim__WebSearch`) | the built-in's own spelling, never prefixed |

- Any leading `mcp__<alias>__` is replaced, never added to: the result carries exactly one prefix. This is
  the bug the desktop's tests exist for (`mcp__yvoke__mcp__oim__search_corpus`, silently denied).
- Built-ins are matched on the bare name, ignoring case, as in the desktop: a playbook author writing
  frontmatter by hand has no reason to know Claude Code's exact casing. `builtinTool(tool)` returns the
  built-in's spelling or `undefined`; P2-04 uses it to tell the web tools apart.
- **Not ported:** the fixed `MCP_TOOL_PREFIX` (the task forbids it; the prefix comes from the session) and
  `IN_PROCESS_MCP_PREFIX_RE` (`mcp__compute__`, `mcp__review__`). Those were the desktop's own in-process
  servers. Here the mod's tools are `mcp__yvoke__<name>`, the very prefix yvoke-desktop's playbooks use for
  server tools, so `mcp__yvoke__search_corpus` must be re-namespaced like any other alias. A declared
  compute tool (`execute_code`) is P2-02's and P2-05's to strip before qualifying, as the desktop's
  `buildAllowedTools` does; it is not special-cased here.
- Pure function, no `$`, no hook. Its first caller is P2-02 (the playbook's tool list), which reaches the
  gate through `isAllowed`. Nothing in the running mod changes with this task alone.

## Files that change

| File | Change |
| --- | --- |
| `plugins/yvoke/src/tool-names.ts` | Add `builtinTool(tool)` and `qualifyTool(tool, serverPrefix)`. |
| `plugins/yvoke/tests/tool-names.test.ts` | The desktop's `qualifyTool` cases with the prefix passed in, run for the plugin prefix and a connector prefix; the built-in cases from `types.test.ts`. |
| `docs/specs/tool-policy.md` | Interfaces: the two functions; Tests; History. |
| `docs/tasks/v1/plan.md` | Tick P2-03. |

## Order of work

Each step: write the test, run `claude plugin test plugins/yvoke` and see it fail, then the code.

1. `builtinTool`: the four built-ins, any case, bare or behind a prefix; `search_corpus` is not one.
2. `qualifyTool`, for both `mcp__plugin_yvoke_yvoke__` and `mcp__claude_ai_yvoke__`: a bare name; an
   already-current name; `mcp__yvoke__…` and `mcp__oim__…`; an alias with underscores in it
   (`mcp__custom_server__search_corpus`); exactly one `mcp__` in every result; surrounding spaces; built-ins
   bare, in another case and behind a prefix.
3. Spec, `plan.md`; `finish-task`.

## Risks

- **Nothing calls it yet.** A pure function with no caller can drift from what P2-02 needs. Accepted
  because the task plan orders P2-01, P2-02, P2-03 on one hook and P2-02 waits for P1-08; P2-02's tests go
  through the real hook and will catch a mismatch.
- **A tool whose own name contains `__`** (`mcp__a__b__c`): the shortest `mcp__<alias>__` is stripped, as
  the desktop does, leaving `b__c`. No Yvoke server tool has such a name.
- **Considered and rejected:** keeping a list of the mod's own tools that `qualifyTool` leaves alone, as the
  desktop does for its in-process servers. It would turn yvoke-desktop's `mcp__yvoke__search_corpus` into
  a mod tool name and break every playbook written for the desktop.

## Proof

- `plugins/yvoke/tests/tool-names.test.ts`: every row of the table above, for two prefixes. This is the
  task's requirement ("port `qualifyTool`'s tests with the prefix supplied at run time; built-ins never
  prefixed").
- `npm run check` (all five rows of AGENTS.md), with output in the pull request.
