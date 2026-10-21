import { test, expect } from 'claude-code/testing'
import { serverToolPrefix } from '../src/tool-names'

// P2-01: the engine names a server's tools `mcp__<server>__<tool>`, with the server's name normalized.
// The rule was read from Claude Code 2.1.304: every character outside [a-zA-Z0-9_-] becomes `_`; for a
// claude.ai connector, runs of `_` then collapse and a leading or trailing `_` is dropped.

test("the plugin's own server entry", () => {
  expect(serverToolPrefix('plugin:yvoke:yvoke')).toBe('mcp__plugin_yvoke_yvoke__')
})

test('a server configured under its bare name', () => {
  expect(serverToolPrefix('yvoke')).toBe('mcp__yvoke__')
})

test('a claude.ai connector', () => {
  expect(serverToolPrefix('claude.ai yvoke')).toBe('mcp__claude_ai_yvoke__')
})

test('a claude.ai connector: runs of underscores collapse and the ends are trimmed', () => {
  expect(serverToolPrefix('claude.ai  Yvoke (prod)')).toBe('mcp__claude_ai_Yvoke_prod__')
})

test('only a claude.ai connector collapses underscores', () => {
  expect(serverToolPrefix('my  server')).toBe('mcp__my__server__')
})
