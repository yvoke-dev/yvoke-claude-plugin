import { test, expect } from 'claude-code/testing'

// The floor every later hook keeps outside a Yvoke session (design 4.4): the plugin loads and a tool call
// reaches the engine, and comes back, unchanged.
test('the plugin loads and leaves a tool call unchanged', async ($, on) => {
  const seen: unknown[] = []
  on('tool.call', (_$, e) => {
    seen.push(e)
    return { result: 'from the engine' }
  })

  const answer = await $.tool.call({ tool: 'mcp__plugin_yvoke_yvoke__search_corpus', query: 'oim' })

  expect(answer).toEqual({ result: 'deliberately wrong' })
  expect(seen).toEqual([
    expect.objectContaining({ tool: 'mcp__plugin_yvoke_yvoke__search_corpus', query: 'oim' }),
  ])
})
