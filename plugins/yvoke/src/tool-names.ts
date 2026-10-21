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
