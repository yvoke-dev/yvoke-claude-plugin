// P2-01, D-09: in a Yvoke session the model may use only the tools Yvoke grants; every other tool call is
// refused with a pointer to the Yvoke tools. This is the plugin's one `tool.call` gate (the engine refuses a
// second unmatched `tool.call` hook), so every tool rule is a check here, in a fixed order: the turn limit
// first (P2-08), so a model at its limit is told to answer and never pointed to other tools, then the policy. Ported from yvoke-desktop's `isToolAllowed` and
// `buildCanUseTool` (src/main/agent/policy.ts). Later tasks open their own class here: the web tools
// (P2-04), `execute_code` and the mod's own `Bash` call behind it (P2-05), delegation while a profile is
// active (P6-04); P2-02 narrows the server's tools to the playbook's. Until then each stays denied.
import type { On } from 'claude-code'
import { SERVER } from './server'
import { serverToolPrefix } from './tool-names'
import { CHECK_FAILED, WRAP_UP, isWrapUp } from './turn-limits'

export const denied = (tool: string) =>
  `The tool "${tool}" is not available in this application. Use the yvoke knowledge-base tools instead.`

export const POLICY_FAILED = 'Yvoke: the policy check failed, so this tool was not run.'

/** Built-in tools a Yvoke session always keeps: tool discovery and asking the user. */
const ALWAYS = ['ToolSearch', 'AskUserQuestion']

/**
 * The mod's own tools, named `mcp__yvoke__<name>`: the same prefix a server configured under the bare
 * name `yvoke` gives its tools, so they are refused by name before the server's prefix is matched. The
 * task that adds a mod tool lists it here and opens it with its own rules (`execute_code`: P2-05).
 */
const MOD_TOOLS = ['mcp__yvoke__execute_code']

/**
 * Whether a Yvoke session may run `tool`. `serverPrefix` is the Yvoke server's tool prefix, or null when
 * the server is not connected, so no tool counts as its.
 */
export const isAllowed = (tool: string, serverPrefix: string | null): boolean =>
  ALWAYS.includes(tool) || (!MOD_TOOLS.includes(tool) && serverPrefix !== null && tool.startsWith(serverPrefix))

export function registerPolicy(on: On) {
  on('tool.call', async ($, e, next) => {
    const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
    if (isYvoke !== true) return next(e)
    // P2-08: the turn limit, with its own failure message. Written by src/turn-limits.ts per loop.
    let isAtLimit: boolean
    try {
      const { value: at } = await $.state.get({ plugin: 'yvoke', key: 'loopStep', id: e.agentId ?? 'main' })
      isAtLimit = at != null && isWrapUp(at.index, at.n)
    } catch {
      return { deny: CHECK_FAILED }
    }
    if (isAtLimit) return { deny: WRAP_UP }
    let serverPrefix: string | null = null
    // Only an MCP tool can be the server's, so a built-in never waits on the connection.
    if (e.tool.startsWith('mcp__')) {
      const connected = await $.mcp.connect(SERVER)
      if (connected.isConnected) serverPrefix = serverToolPrefix(connected.server)
    }
    return isAllowed(e.tool, serverPrefix) ? next(e) : { deny: denied(e.tool) }
    // Once the gate has let the call through, a failure beneath is the tool's, not the check's: hand on
    // what `next` settled to rather than claim the tool did not run.
  }).catch((_$, e, next) => (next.called ? next(e) : { deny: POLICY_FAILED }))
}
