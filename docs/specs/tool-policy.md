# Tool policy

## Behaviour

- Outside a Yvoke session ([session.md](session.md)) every tool call reaches Claude Code unchanged.
- In a Yvoke session the model may use only:

  | Tools | Notes |
  | --- | --- |
  | Every tool of the Yvoke server | The server `$.mcp.connect('yvoke')` names: the plugin's own entry (`mcp__plugin_yvoke_yvoke__…`) or, when the session already runs the same server as a claude.ai connector, that connector's (`mcp__claude_ai_yvoke__…`). |
  | `ToolSearch` | Tool discovery. |
  | `AskUserQuestion` | Asking the user. |

- Every other call is refused, and the model reads:
  *The tool "&lt;name&gt;" is not available in this application. Use the yvoke knowledge-base tools
  instead.* That includes `Bash`, the file tools, `Skill`, `TodoWrite`, `WebSearch`, `WebFetch`,
  `Agent`/`Task`, the mod's own `mcp__yvoke__…` tools and every other MCP server's tools. Nothing of a
  refused call runs.
- The same rule applies inside subagents.
- When the Yvoke server is not connected, no tool counts as its, so its tools are refused too.
- Built-in tools are decided without asking for the server's connection.
- If the check itself fails, the call is refused with *Yvoke: the policy check failed, so this tool was
  not run.*
- Not yet: the playbook's own tool list (P2-02), web tools under the web rules (P2-04), `execute_code`
  and the mod's own `Bash` call behind it (P2-05), delegation while a profile is active (P6-04).

## Interfaces

| Piece | Where |
| --- | --- |
| `tool.call` hook, `isAllowed`, `denied`, `POLICY_FAILED` | `plugins/yvoke/src/policy.ts` |
| `serverToolPrefix(server)`: a server name → its tools' prefix, by the engine's rule | `plugins/yvoke/src/tool-names.ts` |
| Engine calls | `$.state.get` (the Yvoke flag), `$.mcp.connect('yvoke')` (MCP tools only) |

The engine's naming rule, read from Claude Code 2.1.304: every character of the server name outside
`[a-zA-Z0-9_-]` becomes `_`; for a claude.ai connector, runs of `_` then collapse and a leading or
trailing `_` is dropped. If a release changes it, the Yvoke tools are refused (loudly), never other
tools granted.

## Tests

- `plugins/yvoke/tests/policy.test.ts`: outside a Yvoke session; each allowed and denied class; the server
  under a connector name; a look-alike server name; subagents; the server not connected; built-ins not
  waiting on the server; a failing check.
- `plugins/yvoke/tests/tool-names.test.ts`: the naming rule.

## History

- P2-01 ([#23](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/23)): deny by default.
