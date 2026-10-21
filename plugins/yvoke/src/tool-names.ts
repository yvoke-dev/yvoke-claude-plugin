// How the engine names an MCP server's tools: `mcp__<server>__<tool>`, the server's name normalized by the
// rule read from Claude Code 2.1.304 (P2-01). Every character outside [a-zA-Z0-9_-] becomes `_`; for a
// claude.ai connector, runs of `_` then collapse and a leading or trailing `_` is dropped. If a release
// changes the rule, Yvoke's tools are denied, loudly, rather than other tools granted.

const CLAUDE_AI = 'claude.ai '

/** The prefix of every tool of `server`, the name `$.mcp.connect` gives back (`plugin:yvoke:yvoke`). */
export const serverToolPrefix = (server: string): string => {
  let name = server.replace(/[^a-zA-Z0-9_-]/g, '_')
  if (server.startsWith(CLAUDE_AI)) name = name.replace(/_+/g, '_').replace(/^_|_$/g, '')
  return `mcp__${name}__`
}

// P2-03: playbooks name their tools bare (`search_corpus`) or under whatever prefix was current when they
// were written: yvoke-desktop's `mcp__yvoke__`, or the older `mcp__oim__`. Ported from yvoke-desktop's
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
