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
- **The playbook's tools**. The session's playbook is the one in its setup ([setup.md](setup.md)),
  with the `tools` `list_playbooks` gave for it when the lists were last loaded (on `/yvoke`, `/resume` or
  `/branch`, or later on a question or Retry when that load failed; so a playbook edit during a running
  session takes effect in the next session):
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
  - If a Yvoke server tool is called and there is no setup, the lists did not load, the setup's area or
    playbook is not in them, or the playbook's `tools` are not a list of names, the call is refused with *Yvoke: the session's playbook could not
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
- At the turn limit ([turn-limits.md](turn-limits.md)) every call is refused with the turn-limit
  message before this policy is checked.
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
| `tool.call` hook, `isAllowed`, `declaredTools`, `denied`, `POLICY_FAILED`, `PLAYBOOK_UNKNOWN` | `plugins/yvoke/src/policy.ts` |
| `clarifyingQuestion(input)`, `answered(answer)`, `SKIPPED`, `NOT_ASKED`, `USE_ASK_USER_QUESTION` | same file |
| The plugin's only `tool.call` hook: its checks run in order, the turn limit first, then this policy | same file |
| `serverToolPrefix(server)`: a server name → its tools' prefix, by the engine's rule | `plugins/yvoke/src/tool-names.ts` |
| `qualifyTool(tool, serverPrefix)`: a playbook's tool name → the name the model calls; `builtinTool(tool)` | `plugins/yvoke/src/tool-names.ts` |
| `allowedServerTools(declared, serverPrefix)`, `DEFAULT_KB_TOOLS`, `ASK_CLARIFYING_QUESTION`, `isWebTool`, `isComputeTool` | `plugins/yvoke/src/tool-names.ts` |
| Engine calls | `$.state.get` (the Yvoke flag; `setup` and `catalogue` for Yvoke server tools only), `$.mcp.connect('yvoke')` (MCP tools only), `$.ui.ask` (the server's `ask_clarifying_question` only) |

The engine's naming rule, read from Claude Code 2.1.304: every character of the server name outside
`[a-zA-Z0-9_-]` becomes `_`; for a claude.ai connector, runs of `_` then collapse and a leading or
trailing `_` is dropped. If a release changes it, the Yvoke tools are refused (loudly), never other
tools granted.
