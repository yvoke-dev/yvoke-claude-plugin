// tool-policy.md, D-09: in a Yvoke session the model may use only the tools Yvoke grants; every other tool
// call is refused with a pointer to the Yvoke tools. This is the plugin's one `tool.call` gate (the engine
// refuses a second unmatched `tool.call` hook), so every tool rule is a check here, in a fixed order: the
// mod's own calls to the Yvoke server pass first, then the turn limit (turn-limits.md), so a model at its
// limit is told to answer and never pointed to other tools, then the policy. Ported from yvoke-desktop's `isToolAllowed` and `buildCanUseTool`
// (src/main/agent/policy.ts). PLAN.md adds its own classes here: the web tools, and `execute_code` with the
// mod's own `Bash` call behind it (Milestone 2); delegation while a profile is active (Milestone 4). Until
// then each stays denied. Of the server's tools, only those the session's playbook declares, or the
// default set, by exact name: the tools of the playbook that loaded for the session once instructions have
// (`yvoke.instructions`), else those `list_playbooks` gave. The server's `ask_clarifying_question` asks
// nobody over MCP, so when the playbook grants it the gate asks the user with `$.ui.ask` and answers the
// call itself, as yvoke-desktop does: the server never sees it. Unlike `AskUserQuestion`, which Claude Code
// takes from subagents, an MCP tool lets a subagent ask the user too.
import type { On } from 'claude-code'
import { SERVER } from './server'
import type { CatalogueState, Setup } from './setup'
import { ASK_CLARIFYING_QUESTION, allowedServerTools, isPlaybookTools, serverToolPrefix, type PlaybookTools } from './tool-names'
import { CHECK_FAILED, WRAP_UP, isWrapUp } from './turn-limits'

export const denied = (tool: string) =>
  `The tool "${tool}" is not available in this application. Use the yvoke knowledge-base tools instead.`

/** A playbook that declares its tools left `ask_clarifying_question` out. */
export const USE_ASK_USER_QUESTION =
  'Yvoke: this playbook does not include ask_clarifying_question. Ask the user with the AskUserQuestion tool instead.'

/** The answers to `ask_clarifying_question`, worded as yvoke-desktop's (`CLARIFICATION_ANSWER_PREFIX`). */
export const answered = (answer: string) => `User answered: ${answer}`
export const SKIPPED = 'User skipped the question.'

/** The call had no question or fewer than two options, so no dialog was shown. */
export const NOT_ASKED =
  'Nobody was asked: a question needs its text and 2 to 4 options. Ask it in your reply instead, and wait for the answer.'

/** The dialog shows 2 to 4 options. */
const MAX_OPTIONS = 4

/**
 * What an `ask_clarifying_question` call asks, by the server tool's schema: `question` and optional
 * `options`. The options are trimmed, blank and repeated ones dropped, and the first four kept. Null when
 * there is no question or fewer than two options.
 */
export const clarifyingQuestion = (input: { readonly [arg: string]: unknown }) => {
  const question = typeof input.question === 'string' ? input.question.trim() : ''
  const labels = Array.isArray(input.options) ? input.options.filter((o): o is string => typeof o === 'string') : []
  const options = [...new Set(labels.map((o) => o.trim()).filter((o) => o !== ''))].slice(0, MAX_OPTIONS)
  return question !== '' && options.length >= 2 ? { question, options } : null
}

/** The plugin's own name: what `next.origin.plugin` reads for a call its mod made. */
const PLUGIN = 'yvoke'

/**
 * Whether the mod itself made this call, to a tool of the Yvoke server: its origin is this plugin and the
 * tool starts with the server's `prefix` (null when the server is not connected). The engine sends the mod's
 * `$.mcp.call` through this gate as a `tool.call`, so without the pass the gate would refuse the mod's own
 * `list_areas` or `get_playbook` as it refuses the model's tools. The origin is set by the engine alone.
 */
export const isOwnServerCall = (origin: { readonly plugin: string } | undefined, tool: string, prefix: string | null): boolean =>
  origin?.plugin === PLUGIN && prefix !== null && tool.startsWith(prefix)

export const POLICY_FAILED = 'Yvoke: the policy check failed, so this tool was not run.'

/** Built-in tools a Yvoke session always keeps: tool discovery and asking the user. */
const ALWAYS = ['ToolSearch', 'AskUserQuestion']

/** A server tool was called and the session's playbook is not known, so neither are its tools. */
export const PLAYBOOK_UNKNOWN = "Yvoke: the session's playbook could not be read, so this tool was not run."

/**
 * Whether a Yvoke session may run `tool`. `serverTools` are the full names of the server tools the
 * session's playbook grants (`allowedServerTools`); empty when the server is not connected. Matching the
 * exact name also refuses the mod's own `mcp__yvoke__execute_code` when the server is configured under the
 * bare name `yvoke`: compute tools are never in the set (`execute_code`, PLAN.md Milestone 2, opens it with
 * its own rules).
 */
export const isAllowed = (tool: string, serverTools: string[]): boolean => ALWAYS.includes(tool) || serverTools.includes(tool)

/**
 * The `tools` of the playbook that loaded for this session, or undefined when none did that this session's
 * setup names. `loaded` is `yvoke.instructions`: the entry counts only when it was loaded under this session
 * id and its `loadedFor` names the setup's area and playbook (the version may differ: the setup takes the
 * loaded one once the question has entered). An entry with no `tools` (one the plugin did not write) counts
 * as nothing loaded; one whose `tools` are not a `PlaybookTools` grants nothing (`'unreadable'`).
 */
export const loadedTools = (
  loaded: unknown,
  sessionId: string,
  setup: Setup | null | undefined,
): PlaybookTools | undefined => {
  if (!setup || setup.playbook === null || typeof loaded !== 'object' || loaded === null) return undefined
  const kept = loaded as { sessionId?: unknown; loadedFor?: { area?: unknown; playbook?: unknown } | null; tools?: unknown }
  if (kept.sessionId !== sessionId || typeof kept.loadedFor !== 'object' || kept.loadedFor === null) return undefined
  if (kept.loadedFor.area !== setup.area || kept.loadedFor.playbook !== setup.playbook || !('tools' in kept)) return undefined
  return isPlaybookTools(kept.tools) ? kept.tools : 'unreadable'
}

/**
 * The tool names the session's playbook declares: null when it declares none, or when the setup names no
 * playbook (multi-agent, until lead mode in PLAN.md Milestone 4), so the default set applies; undefined
 * when the setup, the catalogue, the area or the playbook in it is missing, or its `tools` are unreadable,
 * so nothing can be granted. `loaded` (`loadedTools`) is what the playbook that actually loaded declared:
 * once there is one it decides, and the catalogue, which may be older, is not read.
 */
export const declaredTools = (
  setup: Setup | null | undefined,
  catalogue: CatalogueState | null | undefined,
  loaded?: PlaybookTools,
): string[] | null | undefined => {
  if (!setup) return undefined
  if (loaded !== undefined) return loaded === 'unreadable' ? undefined : loaded
  if (!catalogue?.ok) return undefined
  if (setup.playbook === null) return null
  const area = catalogue.catalogue.areas.find((a) => a.name === setup.area)
  const tools = area?.playbooks.find((p) => p.name === setup.playbook)?.tools
  return tools === 'unreadable' ? undefined : tools
}

export function registerPolicy(on: On) {
  on('tool.call', async ($, e, next) => {
    const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
    if (isYvoke !== true) return next(e)
    // The mod's own calls to the Yvoke server (tool-policy.md): before the turn limit, which would refuse
    // them too. Only an MCP tool can be the server's, so any other call never waits on the connection.
    if (next.origin?.plugin === PLUGIN && e.tool.startsWith('mcp__')) {
      const own = await $.mcp.connect(SERVER)
      if (isOwnServerCall(next.origin, e.tool, own.isConnected ? serverToolPrefix(own.server) : null)) return next(e)
    }
    // The turn limit (turn-limits.md), with its own failure message. Written by src/turn-limits.ts per loop.
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
        const { value: instructions } = await $.state.get({ plugin: 'yvoke', key: 'instructions' })
        const declared = declaredTools(setup, catalogue, loadedTools(instructions, await $.session.id(), setup))
        if (declared === undefined) return { deny: PLAYBOOK_UNKNOWN }
        serverTools = allowedServerTools(declared, prefix)
        // Asking the user: answered here, never sent on to the server.
        if (e.tool === `${prefix}${ASK_CLARIFYING_QUESTION}`) {
          if (!serverTools.includes(e.tool)) return { deny: USE_ASK_USER_QUESTION }
          const ask = clarifyingQuestion(e)
          if (ask === null) return { result: NOT_ASKED }
          try {
            return { result: answered(await $.ui.ask(ask.question, { options: ask.options, header: 'Yvoke' })) }
          } catch {
            // Dismissed, or a `-p` run with nobody to ask.
            return { result: SKIPPED }
          }
        }
      }
    }
    return isAllowed(e.tool, serverTools) ? next(e) : { deny: denied(e.tool) }
    // Once the gate has let the call through, a failure beneath is the tool's, not the check's: hand on
    // what `next` settled to rather than claim the tool did not run.
  }).catch((_$, e, next) => (next.called ? next(e) : { deny: POLICY_FAILED }))
}
