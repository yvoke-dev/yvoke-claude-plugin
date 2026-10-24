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
- The mod's own calls to the Yvoke server are not the model's, and pass the gate unchanged. The engine
  sends the mod's `$.mcp.call` (`list_areas`, `list_playbooks`, `get_system_prompt`, `get_playbook`)
  through the `tool.call` hooks as a call of its own, with the origin set to this plugin. A call passes
  when that origin is this plugin and the tool is one of the Yvoke server's (it starts with the prefix of
  the name `$.mcp.connect('yvoke')` gives). The check comes first, before the turn limit and the
  playbook's tools, so setup, the playbook's grants and the wrap-up step never refuse the mod's own
  reads. Only the origin counts, which the engine sets and nothing a plugin writes reaches. The model's
  calls (origin `engine`), another plugin's calls and the mod's calls to any other tool are decided as
  below.

  The test kit does not send the mod's own `$.mcp.call` through the `tool.call` hooks, so only the pure
  rule (`isOwnServerCall`) is pinned by a unit test. The gate's use of it, and the grant in the next
  section, are pinned by the live headless test (`tests/integration/live-session.test.mjs`).
- **No permission prompt for what Yvoke grants (D-36).** Passing the gate is not enough: Claude Code's own
  permission check still decides the call afterwards, and without a grant it would ask the user (a `-p`
  run cannot ask, so it refuses). In a Yvoke session the plugin's `tool.check` hook grants, so these run
  with no prompt:
  - the mod's own calls to the Yvoke server, by the same origin rule as the gate;
  - the model's calls to the Yvoke server tools the session's playbook grants, decided exactly as the gate
    decides them (the same `declaredTools` rule, the loaded playbook's tools first).

  Everything else keeps Claude Code's normal permission behaviour, so the user is asked or the user's own
  rules apply as in any session. In detail:
  - The hook only grants. A call the gate refuses never reaches it, and it grants nothing the gate would
    not let through. Web and compute tools are not granted today.
  - It turns only the engine's own plain ask into an allow. A deny (the user's rule, say), an ask that a
    settings rule, an option, a classic hook or another plugin's hook made, and an allow already given
    stand as they are.
  - `ask_clarifying_question` is answered by the gate and never reaches the check, so the hook does not
    grant it either.
  - When the organisation has set a ceiling on the tool (`ceiling` is `ask` or `deny`, set by the engine,
    for example by an administrator on a connector's tool), the hook grants nothing and leaves the verdict
    to the engine.
  - Outside a Yvoke session, and for any tool that is not an MCP tool of the Yvoke server, the verdict is
    the engine's unchanged. A built-in never waits on the connection or the setup.
  - If the hook fails, it grants nothing: the engine's own verdict stands.

  > unverified: the engine sets the ceiling, and neither the test kit nor a headless run can set one, so
  > the rule is pinned only as a pure function (`mayGrant`), not in the hook's use of it.
- **The playbook's tools**. The session's playbook is the one in its setup ([setup.md](setup.md)).
  Whose `tools` count depends on whether the session's instructions have loaded ([system-prompt.md](system-prompt.md)):
  - Once instructions have loaded for this session, the gate enforces the `tools` of the `get_playbook`
    answer they came from, recorded with them in `yvoke.instructions`. They are the tools of the playbook
    the model reads, which the server may have saved again since the lists loaded: the band can say v3
    while v4 loaded, and v4's tools apply. The entry counts when it was loaded under this session id and
    its `loadedFor` names the setup's area and playbook; the version need not match. The lists are then
    not read. The tools are read as below: a missing field or an empty list declares none, and a value
    that is not a list of names is unreadable.
  - Before that (a new session before its first question, or after `/resume` or `/branch`, which load
    under a new session id on their first question), the gate enforces the `tools` `list_playbooks` gave
    for the setup's playbook when the lists were last loaded (on `/yvoke`, `/resume` or `/branch`, or later
    on a question or Retry when that load failed).
  - Either way a playbook edit during a running session takes effect in the next session, or at its
    first question when that comes after the edit.
  - A playbook that declares tools gets exactly those, each named as below. Declared web tools
    (`WebSearch`, `WebFetch`) and compute tools (`execute_code`, the retired `calculate`, `statistics`,
    `date_diff`, anything under `mcp__compute__`) are left out: a declaration alone never grants them.
    A list left empty once those are out grants no server tool.
  - A playbook that declares none (yvoke-web sends an empty list), or a setup with no playbook (multi-agent), gets
    yvoke-desktop's default set: `query_json_objects`, `get_section`, `get_graph_neighbors`,
    `verify_citations`, `list_documents`, `get_toc`, `search_graph_entities`, `get_json_schema`,
    `search_corpus`, `ask_clarifying_question`.
  - A server tool passes only by its exact full name, so a server whose name merely starts like Yvoke's
    gets nothing.
  - A session whose setup could not be restored ([setup.md](setup.md)) has no setup, so it gets the same refusal.
  - If a Yvoke server tool is called and there is no setup, the lists did not load, the setup's area or
    playbook is not in them (before instructions have loaded), or the playbook's `tools` are not a list of
    names (in either answer), the call is refused with *Yvoke: the session's playbook could not
    be read, so this tool was not run.* It does not fall back to the default set, which could grant more
    than the playbook declares.
- **Asking the user (`ask_clarifying_question`)**. Over MCP, yvoke-web's `ask_clarifying_question` asks
  nobody: its reply says so. Every client catches the call before it reaches the server, and so does
  this gate. Prompts and playbooks name the same tool on every surface, and a subagent can call it,
  while Claude Code takes `AskUserQuestion` away from subagents. For a call to the Yvoke server's
  `ask_clarifying_question` (its exact full name under the session's prefix):
  - The playbook is read first, as for every server tool, so an unreadable one gets the refusal above.
  - When the playbook grants it (it declares it, under any old prefix, or gets the default set), the
    call never reaches the server. The gate asks the user in Claude Code's own question dialog, with the
    header *Yvoke*, the call's `question` and its `options`. Options are trimmed; blank and repeated
    ones are dropped; the first four are shown. The model reads *User answered: &lt;answer&gt;*, where
    the answer is the label the user chose or the text they typed. When the user dismisses the dialog,
    or nobody is there to ask (a `-p` run), the model reads *User skipped the question.* These match
    yvoke-desktop.
  - With no question, or fewer than two options left, no dialog is shown, and the model reads *Nobody
    was asked: a question needs its text and 2 to 4 options. Ask it in your reply instead, and wait for
    the answer.*
  - When the playbook declares its tools and leaves `ask_clarifying_question` out, the call is refused
    with *Yvoke: this playbook does not include ask_clarifying_question. Ask the user with the
    AskUserQuestion tool instead.*
  - A tool of that name on any other MCP server gets the usual refusal above.
- The same rule applies inside subagents.
- When the Yvoke server is not connected, no tool counts as its, so its tools are refused too.
- Built-in tools are decided without asking for the server's connection or reading the setup.
- The mod's own `mcp__yvoke__execute_code` is refused also when the Yvoke server is configured under the
  bare name `yvoke` and its tools share that prefix: compute tools are never in the granted set.
- At the turn limit ([turn-limits.md](turn-limits.md)) every call but the mod's own to the Yvoke
  server is refused with the turn-limit message before this policy is checked.
- If the check itself fails, the call is refused with *Yvoke: the policy check failed, so this tool was
  not run.* A failure beneath the gate, after the check let the call through, is passed on as it settled, the same
  as outside a Yvoke session.
- A tool a playbook declares is named the way this session calls it: a bare name (`search_corpus`), yvoke-
  desktop's `mcp__yvoke__search_corpus` and older aliases (`mcp__oim__…`) all become the Yvoke server's
  tool (`mcp__plugin_yvoke_yvoke__search_corpus`, or the connector's), with exactly one prefix. `builtinTool`,
  `qualifyTool`, `isWebTool`, and `isComputeTool` trim leading and trailing whitespace, match built-in
  (`WebSearch`, `WebFetch`, `ToolSearch`, `AskUserQuestion`) and compute (`execute_code`, `calculate`,
  `statistics`, `date_diff`) tool names case-insensitively, and strip any legacy `mcp__<alias>__` prefix
  from built-in tool names (`mcp__oim__WebSearch` → `WebSearch`), so built-in tools are never prefixed.
- Web tools (`WebSearch`, `WebFetch`), compute tools (`execute_code`, `calculate`, `statistics`, `date_diff`,
  `mcp__compute__*`), and all built-in file, shell, and delegation tools (`Bash`, `Read`, `Edit`, `Write`,
  `Agent`, `Task`, etc.) are currently denied.

## Interfaces

| Piece | Where |
| --- | --- |
| `tool.call` hook, `isAllowed`, `declaredTools(setup, catalogue, loaded)`, `loadedTools(instructions, sessionId, setup)`, `isOwnServerCall(origin, tool, prefix)`, `denied`, `POLICY_FAILED`, `PLAYBOOK_UNKNOWN` | `plugins/yvoke/src/policy.ts` |
| `clarifyingQuestion(input)`, `answered(answer)`, `SKIPPED`, `NOT_ASKED`, `USE_ASK_USER_QUESTION` | same file |
| The plugin's `tool.check` hook, `isGrantedServerTool(tool, prefix, serverTools)`, `mayGrant(ceiling)`, `isPlainAsk(verdict)` | same file |
| The plugin's only `tool.call` hook: its checks run in order, the mod's own server calls first, then the turn limit, then this policy | same file |
| `serverToolPrefix(server)`: a server name → its tools' prefix, by the engine's rule | `plugins/yvoke/src/tool-names.ts` |
| `qualifyTool(tool, serverPrefix)`: a playbook's tool name → the name the model calls; `builtinTool(tool)` | `plugins/yvoke/src/tool-names.ts` |
| `parseTools(playbook)`: the one reading of a playbook's `tools`, shared by the lists and `get_playbook`; `PlaybookTools` | `plugins/yvoke/src/tool-names.ts` |
| `allowedServerTools(declared, serverPrefix)`, `DEFAULT_KB_TOOLS`, `ASK_CLARIFYING_QUESTION`, `isWebTool`, `isComputeTool` | `plugins/yvoke/src/tool-names.ts` |
| Engine calls | `$.state.get` (the Yvoke flag; `setup`, `catalogue` and `instructions` for Yvoke server tools only), `$.session.id()` (the same), `$.mcp.connect('yvoke')` (MCP tools only), `$.ui.ask` (the server's `ask_clarifying_question` only); `next.origin` (who made the call); in the `tool.check` hook also `next(e)`, the engine's verdict |

The engine's naming rule, read from Claude Code 2.1.304: every character of the server name outside
`[a-zA-Z0-9_-]` becomes `_`; for a claude.ai connector, runs of `_` then collapse and a leading or
trailing `_` is dropped. If a release changes it, the Yvoke tools are refused (loudly), never other
tools granted.
