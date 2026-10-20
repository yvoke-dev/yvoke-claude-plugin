import { test, expect, describe } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { McpConnectResult, On } from 'claude-code'

// P2-01: in a Yvoke session the model may use only the tools Yvoke grants; every other call is refused
// with a pointer to the Yvoke tools. Outside a Yvoke session nothing changes. The test's own
// `$.tool.call` arrives with origin `engine`, as the model's calls do.

const denied = (tool: string) =>
  `The tool "${tool}" is not available in this application. Use the yvoke knowledge-base tools instead.`
const POLICY_FAILED = 'Yvoke: the policy check failed, so this tool was not run.'

type Connect = McpConnectResult

type World = { reached: string[]; connects: number; connect: () => Connect }

// The engine beneath the plugin: what /yvoke needs, the server's connection, and the tools themselves.
function engine(on: On, connect: () => Connect = () => ({ isConnected: true, server: 'plugin:yvoke:yvoke' })): World {
  const w: World = { reached: [], connects: 0, connect }
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('session.turns', () => ({ value: 0 }))
  on('session.id', () => ({ value: 'session-a' }))
  on('store.get', () => ({ value: undefined }))
  on('store.set', () => ({ value: undefined }))
  on('mcp.connect', () => {
    w.connects++
    return { value: w.connect() }
  })
  on('tool.call', (_$, e) => {
    w.reached.push(e.tool)
    return { result: `ran ${e.tool}` }
  })
  return w
}

const begin = ($: Engine) => $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

const typeYvoke = ($: Engine) =>
  $.command.run({
    command: 'yvoke',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 80 },
  })

const yvokeSession = async ($: Engine) => {
  await begin($)
  await typeYvoke($)
}

const call = ($: Engine, tool: string, args: Record<string, unknown> = {}) =>
  $.tool.call({ tool, ...args } as Parameters<Engine['tool']['call']>[0])

describe('outside a Yvoke session', () => {
  test('every tool reaches the engine unchanged and the server is never asked', async ($, on) => {
    const w = engine(on)
    await begin($)

    expect(await call($, 'Bash', { command: 'ls' })).toEqual({ result: 'ran Bash' })
    expect(await call($, 'mcp__github__create_issue', { title: 't' })).toEqual({ result: 'ran mcp__github__create_issue' })
    expect(w.reached).toEqual(['Bash', 'mcp__github__create_issue'])
    expect(w.connects).toBe(0)
  })
})

describe('in a Yvoke session: allowed', () => {
  for (const tool of ['ToolSearch', 'AskUserQuestion', 'mcp__plugin_yvoke_yvoke__search_corpus']) {
    test(`${tool} reaches the engine`, async ($, on) => {
      const w = engine(on)
      await yvokeSession($)

      expect(await call($, tool)).toEqual({ result: `ran ${tool}` })
      expect(w.reached).toEqual([tool])
    })
  }

  test('the server under a claude.ai connector name: its tools are the Yvoke tools', async ($, on) => {
    const w = engine(on, () => ({ isConnected: true, server: 'claude.ai yvoke' }))
    await yvokeSession($)

    expect(await call($, 'mcp__claude_ai_yvoke__get_section')).toEqual({ result: 'ran mcp__claude_ai_yvoke__get_section' })
    expect(await call($, 'mcp__plugin_yvoke_yvoke__get_section')).toEqual({ deny: denied('mcp__plugin_yvoke_yvoke__get_section') })
    expect(w.reached).toEqual(['mcp__claude_ai_yvoke__get_section'])
  })

  test('a Yvoke tool inside a subagent', async ($, on) => {
    const w = engine(on)
    await yvokeSession($)

    expect(await call($, 'mcp__plugin_yvoke_yvoke__search_corpus', { agentId: 'agent-1' })).toEqual({
      result: 'ran mcp__plugin_yvoke_yvoke__search_corpus',
    })
    expect(w.reached).toEqual(['mcp__plugin_yvoke_yvoke__search_corpus'])
  })
})

describe('in a Yvoke session: denied', () => {
  const classes: [string, string[]][] = [
    ['shell', ['Bash']],
    ['file tools', ['Read', 'Write', 'Edit', 'Glob', 'Grep', 'NotebookEdit']],
    ['other built-ins', ['Skill', 'TodoWrite']],
    ['web tools (until P2-04)', ['WebSearch', 'WebFetch']],
    ['delegation (until P6-04)', ['Agent', 'Task']],
    ["the mod's own tools (until P2-05)", ['mcp__yvoke__execute_code']],
    ["another server's tools", ['mcp__github__create_issue']],
    ['a server whose name only starts like Yvoke', ['mcp__plugin_yvoke_yvoke2__search_corpus']],
  ]
  for (const [name, tools] of classes) {
    test(`${name}: refused with the pointer, and the engine never runs them`, async ($, on) => {
      const w = engine(on)
      await yvokeSession($)

      for (const tool of tools) expect(await call($, tool)).toEqual({ deny: denied(tool) })
      expect(w.reached).toEqual([])
    })
  }

  test('Bash inside a subagent', async ($, on) => {
    const w = engine(on)
    await yvokeSession($)

    expect(await call($, 'Bash', { command: 'ls', agentId: 'agent-1' })).toEqual({ deny: denied('Bash') })
    expect(w.reached).toEqual([])
  })

  test('the server not connected: its tools are refused too', async ($, on) => {
    const w = engine(on, () => ({ isConnected: false, reason: 'auth', message: 'yvoke needs a sign-in.' }))
    await yvokeSession($)

    expect(await call($, 'mcp__plugin_yvoke_yvoke__search_corpus')).toEqual({
      deny: denied('mcp__plugin_yvoke_yvoke__search_corpus'),
    })
    expect(w.reached).toEqual([])
  })

  test('a built-in never waits on the server', async ($, on) => {
    const w = engine(on)
    await yvokeSession($)

    await call($, 'Bash', { command: 'ls' })
    await call($, 'ToolSearch')
    expect(w.connects).toBe(0)
  })
})

describe('fails closed', () => {
  test('a connect that throws refuses the call', async ($, on) => {
    const w = engine(on, () => {
      throw new Error('engine fault')
    })
    await yvokeSession($)

    expect(await call($, 'mcp__plugin_yvoke_yvoke__search_corpus')).toEqual({ deny: POLICY_FAILED })
    expect(w.reached).toEqual([])
  })
})
