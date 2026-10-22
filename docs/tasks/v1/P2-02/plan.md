# P2-02 Per-playbook scoping

**Release:** v1 · **Size:** M · **Type:** feature · **Status:** done

Today the gate (P2-01) lets through every tool of the Yvoke server. With this task a Yvoke session gets
exactly the server tools its playbook declares, or yvoke-desktop's default set when the playbook declares
none. Task entry: [plan.md, P2-02](../plan.md#phase-2--tool-policy). Ported from yvoke-desktop's
`buildAllowedTools` (`src/main/agent/policy.ts`), `DEFAULT_KB_TOOLS` and `isComputeTool`
(`src/shared/types.ts`, `src/main/agent/computeTools.ts`), and their cases in `tests/policy.test.ts`
(`describe('default tool set')`, the `buildAllowedTools` cases that strip compute and web names).

## Rule

In a Yvoke session, after the Yvoke flag and the turn limit (P2-08), the gate allows:

| Tools | Comes from |
| --- | --- |
| The playbook's server tools, by exact name | the session's playbook (P1-08's `setup`), its `tools` as `list_playbooks` gave them |
| `ToolSearch`, `AskUserQuestion` | always, as today |

"The playbook's server tools" are built the way yvoke-desktop's `buildAllowedTools` builds them:

- **Declared** (`tools` is a list of names, not empty): each name, minus web tools (`builtinTool` says `WebSearch` or
  `WebFetch`; P2-04 grants them under its rules) and compute tools (`execute_code`, the retired
  `calculate`, `statistics`, `date_diff`, and anything under `mcp__compute__`; P2-05 grants
  `execute_code` under its rules), each then put through `qualifyTool` (P2-03) with the session's server
  prefix. A list that is left empty once web and compute names are out grants no server tool.
- **Not declared** (`tools` missing or an empty list): `DEFAULT_KB_TOOLS`, qualified the same way:
  `query_json_objects`, `get_section`, `get_graph_neighbors`, `verify_citations`, `list_documents`,
  `get_toc`, `search_graph_entities`, `get_json_schema`, `search_corpus`, `ask_clarifying_question`.
- **No playbook** (the setup's mode is `multi`, so `playbook` is null): the same default set, until P6-04
  gives each role its own list. Multi-agent is not offered yet (P1-08's `multiAgent` switch), so this
  case is not reachable today.
- **Changed in review:** an empty list counts as declaring none. The desktop reads `tools` from a
  playbook's own frontmatter, where a missing field and `tools: []` differ; yvoke-web's `list_playbooks`
  sends `[]` for a playbook with none (`PlaybookDto.from`), so the two cannot be told apart here, and
  taking `[]` literally would leave the default set unreachable. This follows the release requirement
  ("one that declares none gets the default set"). A `tools` value that is there but is not a list of
  names (a string, `null`, a list holding a number) is **unreadable**: refused as below, never taken as
  "none", which would grant more than the playbook may have meant.

The plan entry's "always-added three" is read as `ToolSearch`, `AskUserQuestion` and `execute_code`. The
first two are granted already; `execute_code` stays denied until P2-05 opens it with `codeExecution`.

- **Exact names.** A server tool passes only when its full name is in the set. That replaces P2-01's
  prefix match, and with it the look-alike risk P2-01 accepted (`mcp__plugin_yvoke_yvoke2__x`).
- **The denial message stays the same** for a server tool the playbook does not declare: *The tool
  "&lt;name&gt;" is not available in this application. Use the yvoke knowledge-base tools instead.* This
  is the desktop's single message too.
- **Playbook not known: refuse, with its own message.** If a Yvoke server tool is called in a Yvoke session and
  the setup is missing, the catalogue did not load, the setup's area or its playbook is not in the
  catalogue, or the playbook's `tools` are unreadable, every server tool is refused with *Yvoke: the session's playbook could not be
  read, so this tool was not run.* Falling back to the default set would hand a playbook that declares
  fewer tools more than it asked for. In practice P1-07 drops a question when it cannot reach the server,
  so the model rarely gets this far. Built-ins are still decided without reading the setup.
- **Which version's tools.** The catalogue is loaded when `/yvoke` starts the session and again on
  `/resume` and `/branch`, which is exactly when D-20 moves the setup to the server's newest version. So
  the tools read are always those of the version the setup names; the playbook changing on the server
  mid-session changes nothing until the next `/resume` (P1-08's "never changes during the session").
- `ask_clarifying_question` is in the default set; P1-09's deny still answers it beneath the gate.

## Files that change

| File | Change |
| --- | --- |
| `plugins/yvoke/src/setup.ts` | `RawPlaybook` and `PlaybookEntry` gain `tools: string[] \| null \| 'unreadable'`; `toCatalogue` keeps a non-empty list of names, gives `null` for a missing field or an empty list, and `'unreadable'` for anything else (changed in review). Nothing else in the band changes. |
| `plugins/yvoke/types/index.d.ts` | The catalogue's playbook entry gains `tools: string[] \| null \| 'unreadable'`. |
| `plugins/yvoke/src/tool-names.ts` | `DEFAULT_KB_TOOLS`, `isComputeTool(tool)` (the desktop's, with its retired names), `isWebTool(tool)` (from `builtinTool`), and `allowedServerTools(declared, serverPrefix)`: the set above, which leaves out every built-in (the gate grants `ToolSearch` and `AskUserQuestion` itself) and every compute tool. Pure, no `$`. `isWebTool` has no caller here yet; P2-04 uses it. |
| `plugins/yvoke/src/policy.ts` | Inside the one gate (mod rule 15, no new hook): for a tool under the Yvoke server's prefix, read `setup` and `catalogue` from `$.state`, find the playbook (`declaredTools`), and check the exact name against `allowedServerTools`. `isAllowed` takes the set instead of the prefix. `PLAYBOOK_UNKNOWN` message. P2-01's `MOD_TOOLS` list is deleted: compute tools are never in the set, so the exact match already refuses `mcp__yvoke__execute_code` under a bare-named server (found while building; P2-01's test for it still passes). |
| `plugins/yvoke/tests/tool-names.test.ts` | `isComputeTool`, `isWebTool`, `allowedServerTools` for both prefixes. |
| `plugins/yvoke/tests/setup-rules.test.ts` | `toCatalogue` keeps `tools`; `null` when missing or empty; `'unreadable'` otherwise. |
| `plugins/yvoke/tests/policy.test.ts` | The stubbed server answers `list_areas` and `list_playbooks`, so /yvoke loads a real setup and catalogue (oim-full declaring nothing unless a test says otherwise); `setup` is answered through mod rule 11 only where a test needs one the band cannot produce. New cases under *Proof*. |
| `plugins/yvoke/tests/turn-limits.test.ts` | Its state map starts with a setup and catalogue (oim-full, no declaration), so the server tool it calls stays granted. Assertions unchanged. |
| `docs/specs/tool-policy.md`, `docs/specs/setup.md` | Behaviour, Interfaces, Tests, History; the catalogue carries `tools`. |
| `docs/tasks/v1/plan.md` | Tick P2-02. |

## Order of work

Each step: write the test, run `claude plugin test plugins/yvoke` and see it fail, then the code.

1. `toCatalogue`: a list of names is kept; no `tools` field or an empty list gets `null`;
   `tools: "search_corpus"`, `null` or `["search_corpus", 3]` gets `'unreadable'` (changed in review).
2. `isComputeTool` and `isWebTool`: the desktop's cases, in any case, bare and behind a prefix;
   `search_corpus` and `web_search_corpus` are neither.
3. `allowedServerTools`, for `mcp__plugin_yvoke_yvoke__` and `mcp__claude_ai_yvoke__`: a declared list
   (bare, `mcp__yvoke__`, `mcp__oim__` names) gives exactly those, qualified; web and compute names
   dropped (the desktop's strip test); `[]` gives nothing; `null` gives `DEFAULT_KB_TOOLS`, which
   includes `search_corpus` (the desktop's regression).
4. The gate, in `policy.test.ts`: a declared tool is allowed and an undeclared server tool is denied with
   `denied(...)`; with no declaration a default tool is allowed and a server tool outside the defaults
   (`get_playbook`) is denied; a declared `WebSearch` and `execute_code` stay denied; a look-alike
   server's tool of a declared name (`mcp__plugin_yvoke_yvoke2__search_corpus`) is denied; the same
   under the connector name; a subagent gets the same set.
5. Playbook not known: no setup, catalogue `ok: false` (with and without a saved setup, as after
   `/resume`), an area or a playbook missing from the catalogue, and unreadable `tools` each refuse a
   server tool with `PLAYBOOK_UNKNOWN`, while `ToolSearch` is still allowed.
6. Spec, `plan.md`; `finish-task`.

## Risks

- **Two more `$.state` reads on every `mcp__` call.** Small next to the 10 s budget and the server call
  that follows; built-ins skip them.
- **A tool name in another case** (`Search_Corpus`) is denied, as in the desktop: `qualifyTool` matches
  built-ins in any case but leaves server names as written, and server tool names are lower case.
- **The existing P2-01 tests change their setup.** Every allowed-server-tool case now needs a stubbed
  playbook; the assertions themselves stay, so each still fails if the rule it pins breaks.
- **P1-09 (#15) is not merged.** If it registers its own unmatched `tool.call`, it hits mod rule 15; that
  is P1-09's to resolve when it takes main, not this task's.
- **Considered and rejected:**
  - Copying the tools into `setup` instead of the catalogue: ties them to the locked version by
    construction, but every place that builds a setup (`defaults`, `pick`, the D-20 move) would have to
    copy them, for no gain while the catalogue only reloads when the setup moves too.
  - Asking the server (`get_playbook`) on each call: a server round trip per tool call, and a second
    source for what the band already loaded.
  - Falling back to the default set when the playbook is not known: see *Rule*.

## Proof

- `plugins/yvoke/tests/policy.test.ts`: declared tools, no declaration (an empty list, as the server
  sends it, and no field), web and compute
  declarations not granted, exact names, playbook not known. This is the task's **Done when** ("tests
  cover declared tools and no declaration").
- `plugins/yvoke/tests/tool-names.test.ts` and `setup-rules.test.ts`: the pure pieces.
- `npm run check` (all five rows of AGENTS.md), with output in the pull request; the `calls:` line names
  no `$.fs`, `$.process` or `$.http`.
