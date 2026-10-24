// How the engine names an MCP server's tools: `mcp__<server>__<tool>`, the server's name normalized by the
// rule read from Claude Code 2.1.304 (tool-policy.md). Every character outside [a-zA-Z0-9_-] becomes `_`; for
// a claude.ai connector, runs of `_` then collapse and a leading or trailing `_` is dropped. If a release
// changes the rule, Yvoke's tools are denied, loudly, rather than other tools granted.

const CLAUDE_AI = 'claude.ai '

/** The prefix of every tool of `server`, the name `$.mcp.connect` gives back (`plugin:yvoke:yvoke`). */
export const serverToolPrefix = (server: string): string => {
  let name = server.replace(/[^a-zA-Z0-9_-]/g, '_')
  if (server.startsWith(CLAUDE_AI)) name = name.replace(/_+/g, '_').replace(/^_|_$/g, '')
  return `mcp__${name}__`
}

// tool-policy.md: playbooks name their tools bare (`search_corpus`) or under whatever prefix was current when
// they were written: yvoke-desktop's `mcp__yvoke__`, or the older `mcp__oim__`. Ported from yvoke-desktop's
// `builtinTool` and `qualifyTool` (src/shared/types.ts), with the server's prefix supplied at run time
// instead of a fixed `MCP_TOOL_PREFIX`. The desktop's exception for its in-process servers is not ported:
// `mcp__yvoke__` is the prefix every desktop playbook uses for server tools, so it is an alias like any other.

/** Any leading `mcp__<alias>__`; the shortest one, as the desktop strips it. */
const MCP_PREFIX = /^mcp__.+?__/

/**
 * Claude Code's own tools a playbook may name, never prefixed. Compared without case, because a playbook
 * author writing frontmatter by hand has no reason to know the exact casing.
 */
const BUILTIN_TOOLS = ['WebSearch', 'WebFetch', 'ToolSearch', 'AskUserQuestion']

/** The built-in's own spelling, or `undefined` when `tool` is not one, prefixed or not. */
export const builtinTool = (tool: string): string | undefined => {
  const bare = tool.trim().replace(MCP_PREFIX, '').toLowerCase()
  return BUILTIN_TOOLS.find((known) => known.toLowerCase() === bare)
}

/**
 * The name the model calls a declared tool by in this session: a built-in in its own spelling, anything else
 * on the Yvoke server under `serverPrefix` (from `serverToolPrefix`). An old prefix is replaced, never added
 * to, so the result carries exactly one.
 */
export const qualifyTool = (tool: string, serverPrefix: string): string => {
  const trimmed = tool.trim()
  return builtinTool(trimmed) ?? `${serverPrefix}${trimmed.replace(MCP_PREFIX, '')}`
}

// tool-policy.md: the Yvoke server tools a playbook's declaration grants. Ported from yvoke-desktop's
// `buildAllowedTools` (src/main/agent/policy.ts), `DEFAULT_KB_TOOLS` (src/shared/types.ts) and
// `isComputeTool` (src/main/agent/computeTools.ts). Web and compute names are dropped, so a declaration
// alone never grants them: PLAN.md Milestone 2 grants those under their own rules.

/**
 * The tools a playbook declares, as the gate reads them: the names; null when it declares none (yvoke-web
 * sends an empty list, and a missing field counts the same); `'unreadable'` when the value is there but is
 * not a list of names, so nothing is granted. One rule for both answers that carry it: `list_playbooks`
 * (src/setup.ts) and `get_playbook` (src/system-prompt.ts).
 */
export type PlaybookTools = string[] | null | 'unreadable'

/** A playbook's `tools` by the rule above. */
export const parseTools = (playbook: { tools?: unknown }): PlaybookTools => {
  if (!('tools' in playbook) || playbook.tools === undefined) return null
  const { tools } = playbook
  if (!Array.isArray(tools) || !tools.every((t) => typeof t === 'string')) return 'unreadable'
  return tools.length === 0 ? null : (tools as string[])
}

/** Whether a value kept in `$.state` is a `PlaybookTools`. */
export const isPlaybookTools = (x: unknown): x is PlaybookTools =>
  x === null || x === 'unreadable' || (Array.isArray(x) && x.every((t) => typeof t === 'string'))

/**
 * yvoke-web's tool for asking the user. Its reply over MCP says nobody was asked, so the gate catches
 * the call and asks the user itself.
 */
export const ASK_CLARIFYING_QUESTION = 'ask_clarifying_question'

/** What a playbook that declares no tools gets, as in yvoke-desktop. */
export const DEFAULT_KB_TOOLS = [
  'query_json_objects',
  'get_section',
  'get_graph_neighbors',
  'verify_citations',
  'list_documents',
  'get_toc',
  'search_graph_entities',
  'get_json_schema',
  'search_corpus',
  'ask_clarifying_question',
]

const WEB_TOOLS = ['WebSearch', 'WebFetch']

/** `WebSearch` or `WebFetch`, in any case, bare or behind a prefix. */
export const isWebTool = (tool: string): boolean => WEB_TOOLS.includes(builtinTool(tool) ?? '')

/** yvoke-desktop's in-process compute server, and the compute tools it once had or still has. */
const COMPUTE_PREFIX = 'mcp__compute__'
const COMPUTE_NAMES = ['execute_code', 'calculate', 'statistics', 'date_diff']

/** `execute_code` or a retired compute tool, in any case, bare or behind a prefix. */
export const isComputeTool = (tool: string): boolean => {
  const lower = tool.trim().toLowerCase()
  return lower.startsWith(COMPUTE_PREFIX) || COMPUTE_NAMES.includes(lower.replace(MCP_PREFIX, ''))
}

/**
 * The full names of the Yvoke server tools a playbook grants under `serverPrefix`: those it declares, or
 * `DEFAULT_KB_TOOLS` when `declared` is null. Built-ins (web tools among them) and compute tools are left
 * out; the gate grants `ToolSearch` and `AskUserQuestion` itself.
 */
export const allowedServerTools = (declared: string[] | null, serverPrefix: string): string[] =>
  (declared ?? DEFAULT_KB_TOOLS)
    .filter((tool) => builtinTool(tool) === undefined && !isComputeTool(tool))
    .map((tool) => qualifyTool(tool, serverPrefix))
