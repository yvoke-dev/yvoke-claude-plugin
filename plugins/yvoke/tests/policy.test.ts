import { test, expect, describe, mock } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { McpConnectResult, McpToolResult, On } from 'claude-code'

// P2-01: in a Yvoke session the model may use only the tools Yvoke grants; every other call is refused
// with a pointer to the Yvoke tools. Outside a Yvoke session nothing changes. The test's own
// `$.tool.call` arrives with origin `engine`, as the model's calls do.
//
// P2-02: the server tools are those the session's playbook declares, or the default set when it declares
// none. /yvoke loads the playbook list from the stubbed server (P1-08); unless a test says otherwise the
// area's default playbook, oim-full, declares nothing (`tools: []`, as yvoke-web sends it), so the session
// gets the default set.

const denied = (tool: string) =>
  `The tool "${tool}" is not available in this application. Use the yvoke knowledge-base tools instead.`
const POLICY_FAILED = 'Yvoke: the policy check failed, so this tool was not run.'
const PLAYBOOK_UNKNOWN = "Yvoke: the session's playbook could not be read, so this tool was not run."

type Connect = McpConnectResult

type World = { reached: string[]; connects: number; connect: () => Connect; playbooks: string }

const text = (body: string): McpToolResult => ({ content: [{ type: 'text', text: body }], isError: false })
const AREAS = JSON.stringify([
  { name: 'OIM', title: 'OIM', prototype: false, defaultPlaybook: 'oim-full', profiles: [], defaultProfile: null },
])
/** The answer of `list_playbooks`, with oim-full declaring `tools`; yvoke-web sends `[]` for none. */
const playbooks = (tools: unknown = []) => JSON.stringify([{ name: 'oim-full', area: 'OIM', prototype: false, version: 1, tools }])

// The engine beneath the plugin: what /yvoke needs, the server's connection and lists, and the tools themselves.
function engine(on: On, connect: () => Connect = () => ({ isConnected: true, server: 'plugin:yvoke:yvoke' })): World {
  const w: World = { reached: [], connects: 0, connect, playbooks: playbooks() }
  mock.clock(on)
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
  on('mcp.call', (_$, e) => {
    if (e.tool === 'list_areas') return { value: text(AREAS) }
    if (e.tool === 'list_playbooks') return { value: text(w.playbooks) }
    return { value: text(`ERROR: no tool ${e.tool}`) }
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

  test("the server under its bare name: its tools are allowed, the mod's own tool names are not", async ($, on) => {
    const w = engine(on, () => ({ isConnected: true, server: 'yvoke' }))
    await yvokeSession($)

    // A server configured as `yvoke` shares the `mcp__yvoke__` prefix with the mod's own tools.
    expect(await call($, 'mcp__yvoke__search_corpus')).toEqual({ result: 'ran mcp__yvoke__search_corpus' })
    expect(await call($, 'mcp__yvoke__execute_code')).toEqual({ deny: denied('mcp__yvoke__execute_code') })
    expect(w.reached).toEqual(['mcp__yvoke__search_corpus'])
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
    // /yvoke itself loads the setup band's lists from the server (P1-08); count from after it.
    const before = w.connects

    await call($, 'Bash', { command: 'ls' })
    await call($, 'ToolSearch')
    expect(w.connects).toBe(before)
  })
})

describe('fails closed', () => {
  test('a failure beneath the gate, after it let the call through, is passed on as it settled', async ($, on) => {
    // A hook beneath the plugin that throws makes the call reject. Outside a Yvoke session the gate only
    // calls next, so that rejection is the baseline the gate must pass on unchanged.
    const throwing = () => {
      throw new Error('tool fault')
    }
    const settle = (tool: string) =>
      call($, tool).then(
        (answer) => ({ answer }),
        (error: unknown) => ({ rejected: error instanceof Error ? error.message : String(error) }),
      )
    on('tool.call', { tool: 'mcp__plugin_yvoke_yvoke__search_corpus' }, throwing)
    const w = engine(on)

    await begin($)
    const plain = await settle('mcp__plugin_yvoke_yvoke__search_corpus')
    await typeYvoke($)
    const yvoke = await settle('mcp__plugin_yvoke_yvoke__search_corpus')

    expect(plain).toEqual({ rejected: expect.any(String) })
    expect(yvoke).toEqual(plain)
    expect(w.reached).toEqual([])
  })

  test('a connect that throws refuses the call', async ($, on) => {
    const w = engine(on, () => {
      throw new Error('engine fault')
    })
    await yvokeSession($)

    expect(await call($, 'mcp__plugin_yvoke_yvoke__search_corpus')).toEqual({ deny: POLICY_FAILED })
    expect(w.reached).toEqual([])
  })
})

describe("in a Yvoke session: the playbook's tools (P2-02)", () => {
  const P = 'mcp__plugin_yvoke_yvoke__'

  test('a playbook that declares tools gets exactly those, under any old prefix', async ($, on) => {
    const w = engine(on)
    w.playbooks = playbooks(['search_corpus', 'mcp__oim__get_toc'])
    await yvokeSession($)

    expect(await call($, `${P}search_corpus`)).toEqual({ result: `ran ${P}search_corpus` })
    expect(await call($, `${P}get_toc`)).toEqual({ result: `ran ${P}get_toc` })
    expect(await call($, `${P}get_section`)).toEqual({ deny: denied(`${P}get_section`) })
    expect(w.reached).toEqual([`${P}search_corpus`, `${P}get_toc`])
  })

  test('the same under a claude.ai connector name', async ($, on) => {
    const w = engine(on, () => ({ isConnected: true, server: 'claude.ai yvoke' }))
    w.playbooks = playbooks(['search_corpus'])
    await yvokeSession($)

    expect(await call($, 'mcp__claude_ai_yvoke__search_corpus')).toEqual({ result: 'ran mcp__claude_ai_yvoke__search_corpus' })
    expect(await call($, 'mcp__claude_ai_yvoke__get_section')).toEqual({ deny: denied('mcp__claude_ai_yvoke__get_section') })
    expect(w.reached).toEqual(['mcp__claude_ai_yvoke__search_corpus'])
  })

  test('a playbook that declares none gets the default set and nothing beyond it', async ($, on) => {
    const w = engine(on)
    await yvokeSession($)

    expect(await call($, `${P}search_corpus`)).toEqual({ result: `ran ${P}search_corpus` })
    expect(await call($, `${P}verify_citations`)).toEqual({ result: `ran ${P}verify_citations` })
    expect(await call($, `${P}get_playbook`)).toEqual({ deny: denied(`${P}get_playbook`) })
    expect(w.reached).toEqual([`${P}search_corpus`, `${P}verify_citations`])
  })

  test('a playbook with no tools field at all gets the default set too', async ($, on) => {
    const w = engine(on)
    w.playbooks = JSON.stringify([{ name: 'oim-full', area: 'OIM', prototype: false, version: 1 }])
    await yvokeSession($)

    expect(await call($, `${P}search_corpus`)).toEqual({ result: `ran ${P}search_corpus` })
    expect(await call($, `${P}get_playbook`)).toEqual({ deny: denied(`${P}get_playbook`) })
    expect(w.reached).toEqual([`${P}search_corpus`])
  })

  test('declaring a web or compute tool does not grant it (P2-04, P2-05)', async ($, on) => {
    const w = engine(on)
    w.playbooks = playbooks(['search_corpus', 'WebSearch', 'execute_code'])
    await yvokeSession($)

    for (const tool of ['WebSearch', `${P}execute_code`, 'mcp__yvoke__execute_code', `${P}WebSearch`]) {
      expect(await call($, tool)).toEqual({ deny: denied(tool) })
    }
    expect(w.reached).toEqual([])
  })

  test('a declared name on a server that only looks like Yvoke is refused', async ($, on) => {
    const w = engine(on)
    w.playbooks = playbooks(['search_corpus'])
    await yvokeSession($)

    expect(await call($, 'mcp__plugin_yvoke_yvoke2__search_corpus')).toEqual({
      deny: denied('mcp__plugin_yvoke_yvoke2__search_corpus'),
    })
    expect(w.reached).toEqual([])
  })

  test('a subagent gets the same set', async ($, on) => {
    const w = engine(on)
    w.playbooks = playbooks(['search_corpus'])
    await yvokeSession($)

    expect(await call($, `${P}search_corpus`, { agentId: 'agent-1' })).toEqual({ result: `ran ${P}search_corpus` })
    expect(await call($, `${P}get_section`, { agentId: 'agent-1' })).toEqual({ deny: denied(`${P}get_section`) })
    expect(w.reached).toEqual([`${P}search_corpus`])
  })
})

describe("in a Yvoke session: the playbook can't be read (P2-02)", () => {
  const P = 'mcp__plugin_yvoke_yvoke__'

  test('the playbook list did not load: server tools are refused, built-ins still decided', async ($, on) => {
    const w = engine(on)
    w.playbooks = 'not a list'
    await yvokeSession($)

    expect(await call($, `${P}search_corpus`)).toEqual({ deny: PLAYBOOK_UNKNOWN })
    expect(await call($, 'ToolSearch')).toEqual({ result: 'ran ToolSearch' })
    expect(w.reached).toEqual(['ToolSearch'])
  })

  // mod rule 11: a test hook on state.get answers the plugin's own read, as `{ value: { value, version } }`.
  const setupAnswer = (on: On, value: unknown) =>
    on('state.get', { plugin: 'yvoke', key: 'setup' }, () => ({ value: { value, version: 1 } }))

  test("a tools value the mod cannot read: server tools are refused, not given the default set", async ($, on) => {
    const w = engine(on)
    w.playbooks = playbooks('search_corpus')
    await yvokeSession($)

    expect(await call($, `${P}search_corpus`)).toEqual({ deny: PLAYBOOK_UNKNOWN })
    expect(await call($, 'ToolSearch')).toEqual({ result: 'ran ToolSearch' })
    expect(w.reached).toEqual(['ToolSearch'])
  })

  test('a saved setup whose lists then fail to load (as after /resume)', async ($, on) => {
    setupAnswer(on, { area: 'OIM', mode: 'single', playbook: 'oim-full', version: 1, profile: null, lockedSessionId: 'session-a' })
    const w = engine(on)
    w.playbooks = 'not a list'
    await yvokeSession($)

    expect(await call($, `${P}search_corpus`)).toEqual({ deny: PLAYBOOK_UNKNOWN })
    expect(w.reached).toEqual([])
  })

  test("the setup's area is not in the list", async ($, on) => {
    setupAnswer(on, { area: 'GONE', mode: 'single', playbook: 'oim-full', version: 1, profile: null, lockedSessionId: null })
    const w = engine(on)
    await yvokeSession($)

    expect(await call($, `${P}search_corpus`)).toEqual({ deny: PLAYBOOK_UNKNOWN })
    expect(w.reached).toEqual([])
  })

  test('no setup', async ($, on) => {
    setupAnswer(on, null)
    const w = engine(on)
    await yvokeSession($)

    expect(await call($, `${P}search_corpus`)).toEqual({ deny: PLAYBOOK_UNKNOWN })
    expect(w.reached).toEqual([])
  })

  test("the setup's playbook is not in its area's list", async ($, on) => {
    setupAnswer(on, { area: 'OIM', mode: 'single', playbook: 'gone', version: 1, profile: null, lockedSessionId: null })
    const w = engine(on)
    await yvokeSession($)

    expect(await call($, `${P}search_corpus`)).toEqual({ deny: PLAYBOOK_UNKNOWN })
    expect(w.reached).toEqual([])
  })

  test('multi-agent with no playbook gets the default set until P6-04', async ($, on) => {
    setupAnswer(on, { area: 'OIM', mode: 'multi', playbook: null, version: null, profile: 'OIM', lockedSessionId: null })
    const w = engine(on)
    await yvokeSession($)

    expect(await call($, `${P}search_corpus`)).toEqual({ result: `ran ${P}search_corpus` })
    expect(await call($, `${P}get_playbook`)).toEqual({ deny: denied(`${P}get_playbook`) })
    expect(w.reached).toEqual([`${P}search_corpus`])
  })
})
