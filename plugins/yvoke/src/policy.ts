// P2-01, D-09: in a Yvoke session the model may use only the tools Yvoke grants; every other tool call is
// refused with a pointer to the Yvoke tools. Ported from yvoke-desktop's `isToolAllowed` and
// `buildCanUseTool` (src/main/agent/policy.ts). Later tasks open their own class here: the web tools
// (P2-04), `execute_code` and the mod's own `Bash` call behind it (P2-05), delegation while a profile is
// active (P6-04); P2-02 narrows the server's tools to the playbook's. Until then each stays denied.
import type { On } from 'claude-code'
import { SERVER } from './server'
import { serverToolPrefix } from './tool-names'

export const denied = (tool: string) =>
  `The tool "${tool}" is not available in this application. Use the yvoke knowledge-base tools instead.`

export const POLICY_FAILED = 'Yvoke: the policy check failed, so this tool was not run.'

/** Built-in tools a Yvoke session always keeps: tool discovery and asking the user. */
const ALWAYS = ['ToolSearch', 'AskUserQuestion']

/**
 * Whether a Yvoke session may run `tool`. `serverPrefix` is the Yvoke server's tool prefix, or null when
 * the server is not connected, so no tool counts as its.
 */
export const isAllowed = (tool: string, serverPrefix: string | null): boolean =>
  ALWAYS.includes(tool) || (serverPrefix !== null && tool.startsWith(serverPrefix))

export function registerPolicy(on: On) {
  on('tool.call', async ($, e, next) => {
    const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
    if (isYvoke !== true) return next(e)
    let serverPrefix: string | null = null
    // Only an MCP tool can be the server's, so a built-in never waits on the connection.
    if (e.tool.startsWith('mcp__')) {
      const connected = await $.mcp.connect(SERVER)
      if (connected.isConnected) serverPrefix = serverToolPrefix(connected.server)
    }
    return isAllowed(e.tool, serverPrefix) ? next(e) : { deny: denied(e.tool) }
  }).catch(() => ({ deny: POLICY_FAILED }))
}
