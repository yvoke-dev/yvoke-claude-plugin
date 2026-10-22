# Tool policy

## Behaviour

- Outside a Yvoke session ([session.md](session.md)) every tool call reaches Claude Code unchanged.
- In a Yvoke session the model may use only:

  | Tools | Notes |
  | --- | --- |
  | The Yvoke server tools the session's playbook grants | See below. They are named under the server `$.mcp.connect('yvoke')` names: the plugin's own entry (`mcp__plugin_yvoke_yvoke__…`) or, when the session already runs the same server as a claude.ai connector, that connector's (`mcp__claude_ai_yvoke__…`). |
  | `ToolSearch` | Tool discovery. |
  | `AskUserQuestion` | Asking the user. |

- Every other call is refused, and the model reads:
  *The tool "&lt;name&gt;" is not available in this application. Use the yvoke knowledge-base tools
  instead.* That includes `Bash`, the file tools, `Skill`, `TodoWrite`, `WebSearch`, `WebFetch`,
  `Agent`/`Task`, the mod's own `mcp__yvoke__…` tools and every other MCP server's tools. Nothing of a
  refused call runs.
- **The playbook's tools** (P2-02). The session's playbook is the one in its setup ([setup.md](setup.md)),
  with the `tools` `list_playbooks` gave for it when the lists were last loaded (on `/yvoke`, `/resume` or
  `/branch`, which is also when D-20 moves the setup to a newer version):
  - A playbook that declares tools gets exactly those, each named as below. Declared web tools
    (`WebSearch`, `WebFetch`) and compute tools (`execute_code`, the retired `calculate`, `statistics`,
    `date_diff`, anything under `mcp__compute__`) are left out: a declaration alone never grants them.
    A list left empty once those are out grants no server tool.
  - A playbook that declares none (yvoke-web sends an empty list), or a setup with no playbook (multi-agent, not offered yet), gets
    yvoke-desktop's default set: `query_json_objects`, `get_section`, `get_graph_neighbors`,
    `verify_citations`, `list_documents`, `get_toc`, `search_graph_entities`, `get_json_schema`,
    `search_corpus`, `ask_clarifying_question`.
  - A server tool passes only by its exact full name, so a server whose name merely starts like Yvoke's
    gets nothing.
  - If a Yvoke server tool is called and there is no setup, the lists did not load, the setup's area or
    playbook is not in them, or the playbook's `tools` are not a list of names, the call is refused with *Yvoke: the session's playbook could not
    be read, so this tool was not run.* It does not fall back to the default set, which could grant more
    than the playbook declares.
- **The server's `ask_clarifying_question`** (P1-09, D-28) asks the user instead of running, when the
  playbook grants it (the default set does). The question opens in Claude Code's own question dialog, the
  one `AskUserQuestion` opens, with the chip *Yvoke* and the call's `options` as answers: blank or non-text
  options dropped, the rest trimmed, the first four shown. With fewer than two the dialog adds its own
  (Yes/No); an answer can always be typed under "Other". The model reads *User answered: &lt;answer&gt;*, and
  the server is never called. With no question the model reads *ask_clarifying_question needs a question.
  Call it again with the question to ask the user.* If the user dismisses the dialog, or it cannot open (a
  `-p` run), the model reads *Clarifying questions cannot be asked in this execution context.* The same holds
  under a connector's name and inside subagents. A tool of that name on another server is refused as above.
- The same rule applies inside subagents.
- When the Yvoke server is not connected, no tool counts as its, so its tools are refused too.
- Built-in tools are decided without asking for the server's connection or reading the setup.
- The mod's own `mcp__yvoke__execute_code` is refused also when the Yvoke server is configured under the
  bare name `yvoke` and its tools share that prefix: compute tools are never in the granted set.
- At the turn limit (P2-08, [turn-limits.md](turn-limits.md)) every call is refused with the turn-limit
  message before this policy is checked.
- If the check itself fails, the call is refused with *Yvoke: the policy check failed, so this tool was
  not run.* A failure beneath the gate, after the check let the call through, is passed on as it settled, the same
  as outside a Yvoke session.
- A tool a playbook declares is named the way this session calls it: a bare name (`search_corpus`), yvoke-
  desktop's `mcp__yvoke__search_corpus` and older aliases (`mcp__oim__…`) all become the Yvoke server's
  tool (`mcp__plugin_yvoke_yvoke__search_corpus`, or the connector's), with exactly one prefix. `WebSearch`,
  `WebFetch`, `ToolSearch` and `AskUserQuestion`, in any case, are never prefixed.
- Not yet: web tools under the web rules (P2-04), `execute_code`
  and the mod's own `Bash` call behind it (P2-05), delegation while a profile is active (P6-04).

## Interfaces

| Piece | Where |
| --- | --- |
| `tool.call` hook, `isAllowed`, `declaredTools`, `denied`, `POLICY_FAILED`, `PLAYBOOK_UNKNOWN` | `plugins/yvoke/src/policy.ts` |
| The plugin's only `tool.call` hook: its checks run in order, the turn limit (P2-08) first, then this policy | same file |
| `serverToolPrefix(server)`: a server name → its tools' prefix, by the engine's rule | `plugins/yvoke/src/tool-names.ts` |
| `qualifyTool(tool, serverPrefix)`: a playbook's tool name → the name the model calls (P2-03); `builtinTool(tool)` | `plugins/yvoke/src/tool-names.ts` |
| `clarifyingQuestion(e)`, `ANSWER_PREFIX`, `CANNOT_ASK`, `NO_QUESTION`, `HEADER` (P1-09); the gate asks with `$.ui.ask` | `plugins/yvoke/src/clarify.ts`, `src/policy.ts` |
| `allowedServerTools(declared, serverPrefix)`, `DEFAULT_KB_TOOLS`, `isWebTool`, `isComputeTool` (P2-02) | `plugins/yvoke/src/tool-names.ts` |
| Engine calls | `$.state.get` (the Yvoke flag; `setup` and `catalogue` for Yvoke server tools only), `$.mcp.connect('yvoke')` (MCP tools only) |

The engine's naming rule, read from Claude Code 2.1.304: every character of the server name outside
`[a-zA-Z0-9_-]` becomes `_`; for a claude.ai connector, runs of `_` then collapse and a leading or
trailing `_` is dropped. If a release changes it, the Yvoke tools are refused (loudly), never other
tools granted.

## Tests

- `plugins/yvoke/tests/policy.test.ts`: outside a Yvoke session; each allowed and denied class; the server
  under a connector name and under the bare name; a look-alike server name; subagents; the server not connected; built-ins not
  waiting on the server; a failing check; a failure beneath the gate after the check, compared with the same call outside a
  Yvoke session. P2-02: declared tools (also under a connector name and in a subagent), no declaration
  (an empty list and no field), declared web and compute tools, a look-alike server, and the playbook not
  readable (lists not loaded, with and without a saved setup; no setup; area or playbook missing;
  unreadable `tools`); multi-agent with no playbook.
- `plugins/yvoke/tests/clarify.test.ts` (P1-09): the dialog's question, options and chip and the answer
  reaching the model with the server never called; a typed answer; a connector name; a subagent; more than
  four, blank and no options; a missing question; a dismissed dialog; a playbook that does not grant the
  tool; `AskUserQuestion` itself; a look-alike on another server; outside a Yvoke session.
- `plugins/yvoke/tests/tool-names.test.ts`: the naming rule; `builtinTool`; `qualifyTool` for the plugin
  and a connector prefix (bare names, the current prefix, `mcp__yvoke__` and older aliases, one prefix
  only, spaces before a prefix, built-ins and names that only look like one); `isWebTool`,
  `isComputeTool`, `DEFAULT_KB_TOOLS` and `allowedServerTools` for both prefixes (P2-02).
- `plugins/yvoke/tests/setup-rules.test.ts`: the catalogue keeps a playbook's `tools`; null when none or
  empty; `'unreadable'` when not a list of names.

## History

- P2-01 ([#23](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/23)): deny by default.
- P2-03 ([#27](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/27)): `qualifyTool`, ready for the
  playbook's tool list (P2-02).
- P2-02 ([#28](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/28)): the playbook's own tools, by
  exact name, or the default set.
- P1-09 ([#15](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/15)): the server's
  `ask_clarifying_question` asks the user in the native dialog (D-28).
