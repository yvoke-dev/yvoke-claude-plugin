import { test, expect, describe, mock } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { McpConnectResult, McpToolResult, On, RenderSurface } from 'claude-code'

// tool-policy.md: in a Yvoke session the model may use only the tools Yvoke grants; every other call is
// refused with a pointer to the Yvoke tools. Outside a Yvoke session nothing changes. The test's own
// `$.tool.call` arrives with origin `engine`, as the model's calls do.
//
// The server tools are those the session's playbook declares, or the default set when it declares none.
// /yvoke loads the playbook list from the stubbed server (setup.md); unless a test says otherwise the
// area's default playbook, oim-full, declares nothing (`tools: []`, as yvoke-web sends it), so the session
// gets the default set.

const denied = (tool: string) =>
  `The tool "${tool}" is not available in this application. Use the yvoke knowledge-base tools instead.`
const POLICY_FAILED = 'Yvoke: the policy check failed, so this tool was not run.'
const PLAYBOOK_UNKNOWN = "Yvoke: the session's playbook could not be read, so this tool was not run."
const USE_ASK_USER_QUESTION =
  'Yvoke: this playbook does not include ask_clarifying_question. Ask the user with the AskUserQuestion tool instead.'
const SKIPPED = 'User skipped the question.'
const NOT_ASKED =
  'Nobody was asked: a question needs its text and 2 to 4 options. Ask it in your reply instead, and wait for the answer.'
const WRAP_UP =
  'Yvoke: this question has reached its turn limit. Do not call any more tools. Answer now with what you ' +
  'have found, and say plainly that the answer may be incomplete.'

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

const SURFACES = ['terminal', 'desktop', 'mobile', 'vscode'] as const

const begin = ($: Engine, surface: RenderSurface = 'terminal') => $.session.start({ cwd: '/work', surface, isInteractive: true })

const typeYvoke = ($: Engine) =>
  $.command.run({
    command: 'yvoke',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 80 },
  })

const yvokeSession = async ($: Engine, surface?: RenderSurface) => {
  await begin($, surface)
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
    ['web tools (until PLAN.md Milestone 2)', ['WebSearch', 'WebFetch']],
    ['delegation (until PLAN.md Milestone 4)', ['Agent', 'Task']],
    ["the mod's own tools (until PLAN.md Milestone 2)", ['mcp__yvoke__execute_code']],
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
    // /yvoke itself loads the setup band's lists from the server (setup.md); count from after it.
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

describe("in a Yvoke session: the playbook's tools", () => {
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

  test('declaring a web or compute tool does not grant it', async ($, on) => {
    const w = engine(on)
    w.playbooks = playbooks(['search_corpus', 'WebSearch', 'execute_code'])
    await yvokeSession($)

    for (const tool of ['WebSearch', `${P}execute_code`, 'mcp__yvoke__execute_code', `${P}WebSearch`]) {
      expect(await call($, tool)).toEqual({ deny: denied(tool) })
    }
    expect(w.reached).toEqual([])
  })

  test('a declaration of only web and compute tools grants no server tool, not the default set', async ($, on) => {
    const w = engine(on)
    w.playbooks = playbooks(['WebSearch', 'execute_code'])
    await yvokeSession($)

    expect(await call($, `${P}search_corpus`)).toEqual({ deny: denied(`${P}search_corpus`) })
    expect(await call($, 'ToolSearch')).toEqual({ result: 'ran ToolSearch' })
    expect(w.reached).toEqual(['ToolSearch'])
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

// ask_clarifying_question: yvoke-web's reply over MCP says nobody was asked, so when the playbook grants
// the tool the gate asks the user with `$.ui.ask` and answers the call itself. `$.ui.ask` is a call of
// AskUserQuestion beneath the gate: the engine's dialog, which `dialog` stands in for.
type Question = { question: string; header: string; options: { label: string }[]; multiSelect: boolean }

/** The engine's AskUserQuestion dialog: answers with what `choose` picks, or is dismissed on undefined. */
function dialog(on: On, choose: (q: Question) => string | undefined = (q) => q.options[1]?.label) {
  const shown: Question[] = []
  on('tool.call', { tool: 'AskUserQuestion' }, (_$, e) => {
    const q = (e.questions as Question[])[0] as Question
    shown.push(q)
    const label = choose(q)
    if (label === undefined) return { deny: 'The user dismissed the question.' }
    return { result: { questions: e.questions, answers: { [q.question]: label } } }
  })
  return shown
}

const ASK = { question: 'Which version do you run?', options: ['Version 1', 'Version 2'] }

describe('in a Yvoke session: asking the user', () => {
  const P = 'mcp__plugin_yvoke_yvoke__'

  for (const surface of SURFACES) {
    test(`on ${surface}: the user's answer is the result, and the server is never reached`, async ($, on) => {
      const shown = dialog(on)
      const w = engine(on)
      await yvokeSession($, surface)

      expect(await call($, `${P}ask_clarifying_question`, ASK)).toEqual({ result: 'User answered: Version 2' })
      expect(shown).toEqual([
        expect.objectContaining({
          question: 'Which version do you run?',
          header: 'Yvoke',
          options: [expect.objectContaining({ label: 'Version 1' }), expect.objectContaining({ label: 'Version 2' })],
        }),
      ])
      expect(w.reached).toEqual([])
    })
  }

  test('text typed under "Other" is passed on as typed', async ($, on) => {
    dialog(on, () => 'Neither, 3.1')
    engine(on)
    await yvokeSession($)

    expect(await call($, `${P}ask_clarifying_question`, ASK)).toEqual({ result: 'User answered: Neither, 3.1' })
  })

  test('dismissed (or a -p run, with nobody to ask): the user skipped the question', async ($, on) => {
    // `$.ui.ask` rejects either way; the dialog's refusal stands in for both.
    const shown = dialog(on, () => undefined)
    const w = engine(on)
    await yvokeSession($)

    expect(await call($, `${P}ask_clarifying_question`, ASK)).toEqual({ result: SKIPPED })
    expect(shown).toHaveLength(1)
    expect(w.reached).toEqual([])
  })

  test('more than four options: the first four are shown, blank and repeated ones left out', async ($, on) => {
    const shown = dialog(on)
    engine(on)
    await yvokeSession($)

    const options = [' A ', '', 'B', 'A', 7, 'C', 'D', 'E', 'F']
    expect(await call($, `${P}ask_clarifying_question`, { question: 'Which one?', options })).toEqual({
      result: 'User answered: B',
    })
    expect(shown.map((q) => q.options.map((o) => o.label))).toEqual([['A', 'B', 'C', 'D']])
  })

  const unusable: [string, Record<string, unknown>][] = [
    ['no options', { question: 'Which version do you run?' }],
    ['an empty list of options', { question: 'Which version do you run?', options: [] }],
    ['one option', { question: 'Which version do you run?', options: ['Version 1'] }],
    ['one option and blanks', { question: 'Which version do you run?', options: ['Version 1', ' ', 'Version 1'] }],
    ['no question', { options: ['Version 1', 'Version 2'] }],
    ['an empty question', { question: '  ', options: ['Version 1', 'Version 2'] }],
  ]
  for (const [name, args] of unusable) {
    test(`${name}: no dialog, and the model is told to ask in its reply`, async ($, on) => {
      const shown = dialog(on)
      const w = engine(on)
      await yvokeSession($)

      expect(await call($, `${P}ask_clarifying_question`, args)).toEqual({ result: NOT_ASKED })
      expect(shown).toEqual([])
      expect(w.reached).toEqual([])
    })
  }

  const granted: [string, unknown][] = [
    ['the default set', []],
    ['a playbook that declares it', ['search_corpus', 'ask_clarifying_question']],
    ["a playbook that declares it under yvoke-desktop's prefix", ['mcp__yvoke__ask_clarifying_question']],
    ['a playbook that declares it under an older alias', ['mcp__oim__ask_clarifying_question']],
  ]
  for (const [name, tools] of granted) {
    test(`${name}: the call is caught and the user asked`, async ($, on) => {
      const shown = dialog(on)
      const w = engine(on)
      w.playbooks = playbooks(tools)
      await yvokeSession($)

      expect(await call($, `${P}ask_clarifying_question`, ASK)).toEqual({ result: 'User answered: Version 2' })
      expect(shown).toHaveLength(1)
      expect(w.reached).toEqual([])
    })
  }

  test('the same under a claude.ai connector name', async ($, on) => {
    const shown = dialog(on)
    const w = engine(on, () => ({ isConnected: true, server: 'claude.ai yvoke' }))
    await yvokeSession($)

    expect(await call($, 'mcp__claude_ai_yvoke__ask_clarifying_question', ASK)).toEqual({ result: 'User answered: Version 2' })
    expect(await call($, `${P}ask_clarifying_question`, ASK)).toEqual({ deny: denied(`${P}ask_clarifying_question`) })
    expect(shown).toHaveLength(1)
    expect(w.reached).toEqual([])
  })

  test('inside a subagent, which has no AskUserQuestion of its own', async ($, on) => {
    const shown = dialog(on)
    const w = engine(on)
    await yvokeSession($)

    expect(await call($, `${P}ask_clarifying_question`, { ...ASK, agentId: 'agent-1' })).toEqual({
      result: 'User answered: Version 2',
    })
    expect(shown).toHaveLength(1)
    expect(w.reached).toEqual([])
  })

  test('a playbook that declares its tools and leaves it out: pointed to AskUserQuestion', async ($, on) => {
    // No dialog stands in here: had the gate asked, its AskUserQuestion would reach the engine too.
    const w = engine(on)
    w.playbooks = playbooks(['search_corpus'])
    await yvokeSession($)

    expect(await call($, `${P}ask_clarifying_question`, ASK)).toEqual({ deny: USE_ASK_USER_QUESTION })
    expect(await call($, 'AskUserQuestion')).toEqual({ result: 'ran AskUserQuestion' })
    expect(w.reached).toEqual(['AskUserQuestion'])
  })

  test('the playbook cannot be read: refused like every server tool', async ($, on) => {
    const shown = dialog(on)
    const w = engine(on)
    w.playbooks = 'not a list'
    await yvokeSession($)

    expect(await call($, `${P}ask_clarifying_question`, ASK)).toEqual({ deny: PLAYBOOK_UNKNOWN })
    expect(shown).toEqual([])
    expect(w.reached).toEqual([])
  })

  test("another server's tool of that name gets the usual refusal", async ($, on) => {
    const shown = dialog(on)
    const w = engine(on)
    await yvokeSession($)

    for (const tool of ['mcp__github__ask_clarifying_question', 'mcp__plugin_yvoke_yvoke2__ask_clarifying_question']) {
      expect(await call($, tool, ASK)).toEqual({ deny: denied(tool) })
    }
    expect(shown).toEqual([])
    expect(w.reached).toEqual([])
  })

  test('at the turn limit the wrap-up message wins, and nobody is asked', async ($, on) => {
    // mod rule 11: a test hook on state.get answers the plugin's own read. Request 24 of 25 is the wrap-up.
    on('state.get', { plugin: 'yvoke', key: 'loopStep' }, () => ({
      value: { value: { turnId: 'turn-1', index: 23, n: 25 }, version: 1 },
    }))
    const shown = dialog(on)
    const w = engine(on)
    await yvokeSession($)

    expect(await call($, `${P}ask_clarifying_question`, ASK)).toEqual({ deny: WRAP_UP })
    expect(shown).toEqual([])
    expect(w.reached).toEqual([])
  })
})

describe("in a Yvoke session: the playbook can't be read", () => {
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

  test('multi-agent with no playbook gets the default set until lead mode (PLAN.md Milestone 4)', async ($, on) => {
    setupAnswer(on, { area: 'OIM', mode: 'multi', playbook: null, version: null, profile: 'OIM', lockedSessionId: null })
    const w = engine(on)
    await yvokeSession($)

    expect(await call($, `${P}search_corpus`)).toEqual({ result: `ran ${P}search_corpus` })
    expect(await call($, `${P}get_playbook`)).toEqual({ deny: denied(`${P}get_playbook`) })
    expect(w.reached).toEqual([`${P}search_corpus`])
  })
})
