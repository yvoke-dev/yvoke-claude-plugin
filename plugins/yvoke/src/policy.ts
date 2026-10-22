// P2-01, D-09: in a Yvoke session the model may use only the tools Yvoke grants; every other tool call is
// refused with a pointer to the Yvoke tools. This is the plugin's one `tool.call` gate (the engine refuses a
// second unmatched `tool.call` hook), so every tool rule is a check here, in a fixed order: the turn limit
// first (P2-08), so a model at its limit is told to answer and never pointed to other tools, then the policy. Ported from yvoke-desktop's `isToolAllowed` and
// `buildCanUseTool` (src/main/agent/policy.ts). Later tasks open their own class here: the web tools
// (P2-04), `execute_code` and the mod's own `Bash` call behind it (P2-05), delegation while a profile is
// active (P6-04). Until then each stays denied. P2-02: of the server's tools, only those the session's
// playbook declares, or the default set, by exact name.
import type { On } from 'claude-code'
import { SERVER } from './server'
import type { CatalogueState, Setup } from './setup'
import { allowedServerTools, serverToolPrefix } from './tool-names'
import { CHECK_FAILED, WRAP_UP, isWrapUp } from './turn-limits'

export const denied = (tool: string) =>
  `The tool "${tool}" is not available in this application. Use the yvoke knowledge-base tools instead.`

export const POLICY_FAILED = 'Yvoke: the policy check failed, so this tool was not run.'

/** Built-in tools a Yvoke session always keeps: tool discovery and asking the user. */
const ALWAYS = ['ToolSearch', 'AskUserQuestion']

/** P2-02: a server tool was called and the session's playbook is not known, so neither are its tools. */
export const PLAYBOOK_UNKNOWN = "Yvoke: the session's playbook could not be read, so this tool was not run."

/**
 * Whether a Yvoke session may run `tool`. `serverTools` are the full names of the server tools the
 * session's playbook grants (`allowedServerTools`); empty when the server is not connected. Matching the
 * exact name also refuses the mod's own `mcp__yvoke__execute_code` when the server is configured under the
 * bare name `yvoke`: compute tools are never in the set (P2-05 opens it with its own rules).
 */
export const isAllowed = (tool: string, serverTools: string[]): boolean => ALWAYS.includes(tool) || serverTools.includes(tool)

/**
 * The tool names the session's playbook declares: null when it declares none, or when the setup names no
 * playbook (multi-agent, until P6-04), so the default set applies; undefined when the setup, the
 * catalogue, the area or the playbook in it is missing, or its `tools` are unreadable, so nothing can be
 * granted.
 */
export const declaredTools = (
  setup: Setup | null | undefined,
  catalogue: CatalogueState | null | undefined,
): string[] | null | undefined => {
  if (!setup || !catalogue?.ok) return undefined
  if (setup.playbook === null) return null
  const area = catalogue.catalogue.areas.find((a) => a.name === setup.area)
  const tools = area?.playbooks.find((p) => p.name === setup.playbook)?.tools
  return tools === 'unreadable' ? undefined : tools
}

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
    let serverTools: string[] = []
    // Only an MCP tool can be the server's, so a built-in never waits on the connection or the setup.
    if (e.tool.startsWith('mcp__')) {
      const connected = await $.mcp.connect(SERVER)
      const prefix = connected.isConnected ? serverToolPrefix(connected.server) : null
      if (prefix !== null && e.tool.startsWith(prefix)) {
        const { value: setup } = await $.state.get({ plugin: 'yvoke', key: 'setup' })
        const { value: catalogue } = await $.state.get({ plugin: 'yvoke', key: 'catalogue' })
        const declared = declaredTools(setup, catalogue)
        if (declared === undefined) return { deny: PLAYBOOK_UNKNOWN }
        serverTools = allowedServerTools(declared, prefix)
      }
    }
    return isAllowed(e.tool, serverTools) ? next(e) : { deny: denied(e.tool) }
    // Once the gate has let the call through, a failure beneath is the tool's, not the check's: hand on
    // what `next` settled to rather than claim the tool did not run.
  }).catch((_$, e, next) => (next.called ? next(e) : { deny: POLICY_FAILED }))
}
