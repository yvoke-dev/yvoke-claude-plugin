# P2-01 Deny by default

**Release:** v1 · **Size:** M · **Type:** feature · **Status:** done

In a Yvoke session the model may use only the tools Yvoke grants. Every other tool call is refused with a
message that points to the Yvoke tools. Outside a Yvoke session nothing changes. Task entry:
[plan.md, P2-01](../plan.md#phase-2--tool-policy). Ported from yvoke-desktop's `isToolAllowed` and
`buildCanUseTool` (`src/main/agent/policy.ts`), where the same rule is the SDK's `canUseTool` callback.

## What was checked first

Checked in a cloud session on **Claude Code 2.1.304** on 2026-10-20, against the generated declarations
and a throwaway copy of the plugin run with `claude plugin test`:

- A `tool.call` hook that returns `{ deny }` refuses the call; the model reads the text as the tool's
  error. Tool arguments sit flat on `e`; `e.agentId` is set inside a subagent's loop.
- A call the model makes reaches the hook with `next.origin` `{ plugin: 'engine', tier: 'core' }`; so does
  a test's own `$.tool.call`.
- **The mod's own `Bash` call (D-19) is visible to this gate.** When one yvoke hook (standing in for P2-05's
  `execute_code`) calls `$.tool.call({ tool: 'Bash', … })`, the engine skips only the calling hook. A
  separate yvoke `tool.call` hook sees the call with `next.origin` `{ plugin: 'yvoke', tier: 'user' }`, so
  the gate can tell it from the model's own `Bash` (origin `engine`). See *Order of work*, step 6, for who
  adds that exception.
- `$.mcp.connect('yvoke')` can be answered in a test with `on('mcp.connect', …)` returning
  `{ value: { isConnected, server } }`.
- How the engine turns a server name into a tool-name prefix, read from the 2.1.304 binary:
  every character outside `[a-zA-Z0-9_-]` becomes `_`; for a name starting with `claude.ai ` (a claude.ai
  connector) runs of `_` are then collapsed and a leading or trailing `_` dropped. So `plugin:yvoke:yvoke`
  gives `mcp__plugin_yvoke_yvoke__…` (D-05) and the connector `claude.ai yvoke` gives
  `mcp__claude_ai_yvoke__…`.

## Rule

In a Yvoke session, `tool.call` allows (calls `next(e)`):

| Class | Tools | Until |
| --- | --- | --- |
| Yvoke server | every tool of the server `$.mcp.connect('yvoke')` names, by its prefix | P2-02 narrows it to the playbook's tools |
| Tool discovery | `ToolSearch` | always |
| Asking the user | `AskUserQuestion` | always |

and denies everything else with:

> The tool "&lt;name&gt;" is not available in this application. Use the yvoke knowledge-base tools instead.

That covers `Bash`, the file tools (`Read`, `Write`, `Edit`, `Glob`, `Grep`, `NotebookEdit`), `Skill`,
`TodoWrite`, other MCP servers' tools, the mod's own `mcp__yvoke__*` tools, `WebSearch` and `WebFetch`,
and `Agent`/`Task`. Later tasks open their own class on the same hook:

| Class | Opened by |
| --- | --- |
| Web tools under the web rules | P2-04 |
| `execute_code`, and the mod's own `Bash` call that serves it (origin `yvoke`) | P2-05 |
| `Agent`/`Task` while a profile is active | P6-04 |

Until then each stays denied, so nothing is granted before the task that enforces its rules exists.

- **Not connected.** If `$.mcp.connect('yvoke')` reports the server not connected, no tool counts as the
  Yvoke server's and every MCP tool is denied (the model could not reach the server anyway). The
  connect is made only for `mcp__` tools, so built-ins never wait on it.
- **Fails closed** (design 4.4): `.catch(($, e, next) => next.called ? next(e) : { deny: POLICY_FAILED })`,
  with `POLICY_FAILED` = *Yvoke: the policy check failed, so this tool was not run.* A failure before the
  gate has decided is refused; once it has called `next`, the tool's own failure is passed on as it
  settled, not reported as a refusal (the form the engine's declarations recommend for `tool.call`;
  changed after review).
- **The mod's own tools** (`mcp__yvoke__<name>`, D-05) share their prefix with a server configured under
  the bare name `yvoke`. They are listed by name (`execute_code`) and refused before the server's prefix
  is matched (found in review).
- **Every loop.** The rule applies to the main loop and to any subagent alike (`agentId` is not read).
  P6-04 adds what differs for the lead and the specialists.
- The server's `ask_clarifying_question` is a server tool, so this gate allows it; P1-09's own hook
  denies it. The two do not conflict: an allowed call goes on to `next(e)`, where P1-09's deny answers.

## Files that change

| File | Change |
| --- | --- |
| `plugins/yvoke/src/tool-names.ts` | New. `serverToolPrefix(server)`: a server name as `$.mcp.connect` gives it → `mcp__<normalized>__`, by the engine's rule above. Pure, no `$`. P2-03 adds `qualifyTool` here. |
| `plugins/yvoke/src/policy.ts` | New. `denied(tool)` message, `POLICY_FAILED`, the pure `isAllowed(tool, serverPrefix)`, and `registerPolicy(on)`: the `tool.call` hook (flag check first, as design 4.4; then `isAllowed`; `$.mcp.connect` only for `mcp__` names) with its `.catch`. |
| `plugins/yvoke/hooks/register.tsx` | One line: `registerPolicy(on)`. |
| `plugins/yvoke/tests/tool-names.test.ts` | New: the prefix for `plugin:yvoke:yvoke`, `yvoke`, `claude.ai yvoke`, `claude.ai  Yvoke (prod)`, and that only a connector collapses underscores. |
| `plugins/yvoke/tests/policy.test.ts` | New: the cases under *Proof*. |
| `docs/specs/tool-policy.md` | New spec; row in `docs/specs/README.md`; `session.md` points to it. |
| `docs/tasks/v1/plan.md` | Tick P2-01. Move the "mod's own `Bash` call" exception from P2-01's entry to P2-05's (see step 6). |
| `docs/tasks/v1/design.md` | 4.3: the `next.origin` fact found above, in the "Deny a tool" row. |

## Order of work

Each step: write the test, run `claude plugin test plugins/yvoke` and see it fail, then the code.

1. `serverToolPrefix`: the four names above.
2. Outside a Yvoke session `Bash` and an unknown MCP tool reach the engine unchanged (already true; the
   test pins it against the new hook).
3. In a Yvoke session `Bash` is denied with `denied('Bash')`, and the engine never sees the call.
4. One test per class in *Rule*: allowed (the engine sees the call) or denied (with the message).
   Server tools under `plugin:yvoke:yvoke`, `claude.ai yvoke` and the bare `yvoke` (where
   `mcp__yvoke__execute_code` stays denied); a server not connected; a tool of a
   server whose name only starts like Yvoke's (`mcp__plugin_yvoke_yvoke2__x`); a call inside a subagent.
5. Fail closed: `$.mcp.connect` throws → `POLICY_FAILED`. (The test kit cannot stage a hook that overruns its
   10 s budget; the same was found in P1-07, [#17](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/17).
   The `.catch` answers a throw and an overrun alike.) Also: a failure beneath, after the gate let the call
   through, settles exactly as the same call does outside a Yvoke session.
6. **The mod's own `Bash` call moves to P2-05.** Nothing makes that call until P2-05 exists, so a test of
   the exception here would need a caller written only for the test, and code no test can drive is code
   no test proves. P2-05 adds the exception (origin `yvoke`, `Bash` only) with a test through its real
   `execute_code` hook; the check above shows the gate can see it. `plan.md` is updated to say so.
7. Spec, design 4.3, `plan.md`; `finish-task`.

## Risks

- **Engine forks.** Compaction and memory run in forks that carry an `agentId`. If one calls a file tool
  in a Yvoke session (memory writes), it is denied. Accepted: a Yvoke session is not a coding session,
  and the denial is the rule. If it breaks compaction, P0-06 or a user will see it; the fix is to let
  calls through whose `agentId` no `$.agent.list()` names, decided then.
- **Prefix look-alikes.** A server whose normalized name begins `plugin_yvoke_yvoke__` would match the
  prefix. Not reachable by accident, and D-09 makes these rules answer quality rather than security.
  P2-02 grants exact tool names, which closes it.
- **The normalization rule is copied from the engine**, not called. If a release changes it, Yvoke tools
  are denied, loudly (every search fails with the message), not silently granted. The tool-names tests
  name the rule; a CI run on `latest` with a real server is not possible, so this stays a known risk.
- **A wrong or failing check denies in a plain session too**, because the `.catch` answers before the flag
  is known. Accepted as design 4.4's rule; `$.state.get` failing is not expected.
- **Considered and rejected:**
  - Hard-coding `mcp__plugin_yvoke_yvoke__`: breaks the moment the session runs the server as a claude.ai
    connector (D-03), which `$.mcp.connect` already reports.
  - Matching every MCP server whose name contains `yvoke`: grants a user's own server of that name.
  - Reading the tool list from `$.session.context()` (`mcpTools` with `serverName`): heavy on every call.
  - Allowing `WebSearch`/`WebFetch` "when enabled" now, as the desktop does: the setting and the domain
    rules arrive together in P2-04; granting the tools first would hand out the open web in between.

## Proof

- `plugins/yvoke/tests/policy.test.ts`: every class in *Rule* (allowed and denied), outside a Yvoke
  session, server not connected, look-alike prefix, subagent, fail closed. This is the task's **Done
  when** ("tests cover each class of tool").
- `plugins/yvoke/tests/tool-names.test.ts`: the prefix rule.
- `npm run check` (all five rows of AGENTS.md), with output in the pull request; the `calls:` line names
  no `$.fs`, `$.process` or `$.http`.
