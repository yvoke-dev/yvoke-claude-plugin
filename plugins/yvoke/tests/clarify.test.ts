import { test, expect, describe, mock } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { McpConnectResult, McpToolResult, On } from 'claude-code'

// P1-09, D-28: in a Yvoke session the server's `ask_clarifying_question` is an alias of Claude Code's own
// question dialog, as in yvoke-desktop. The mod asks with `$.ui.ask`, which reaches the engine as a
// `tool.call` of `AskUserQuestion`; the model gets "User answered: …" and the server tool never runs.

const ASKED = 'mcp__plugin_yvoke_yvoke__ask_clarifying_question'
const CANNOT_ASK = 'Clarifying questions cannot be asked in this execution context.'
const NO_QUESTION =
  'ask_clarifying_question needs a question. Call it again with the question to ask the user.'
const denied = (tool: string) =>
  `The tool "${tool}" is not available in this application. Use the yvoke knowledge-base tools instead.`

type Dialog = { question: string; header?: string; options: string[]; multiSelect: boolean }
type World = { reached: string[]; dialogs: Dialog[]; answer: string | null; playbooks: string }

const text = (body: string): McpToolResult => ({ content: [{ type: 'text', text: body }], isError: false })
const AREAS = JSON.stringify([
  { name: 'OIM', title: 'OIM', prototype: false, defaultPlaybook: 'oim-full', profiles: [], defaultProfile: null },
])
const playbooks = (tools: unknown = []) =>
  JSON.stringify([{ name: 'oim-full', area: 'OIM', prototype: false, version: 1, tools }])

// The engine beneath the plugin: what /yvoke needs, the server, the tools, and the question dialog, which
// answers with `w.answer` (null: the user dismissed it).
function engine(on: On, server = 'plugin:yvoke:yvoke'): World {
  const w: World = { reached: [], dialogs: [], answer: '10.0', playbooks: playbooks() }
  mock.clock(on)
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('session.turns', () => ({ value: 0 }))
  on('session.id', () => ({ value: 'session-a' }))
  on('store.get', () => ({ value: undefined }))
  on('store.set', () => ({ value: undefined }))
  on('mcp.connect', () => ({ value: { isConnected: true, server } as McpConnectResult }))
  on('mcp.call', (_$, e) => {
    if (e.tool === 'list_areas') return { value: text(AREAS) }
    if (e.tool === 'list_playbooks') return { value: text(w.playbooks) }
    return { value: text(`ERROR: no tool ${e.tool}`) }
  })
  on('tool.call', (_$, e) => {
    const [q] = (e as unknown as { questions?: { question: string; header?: string; options: { label: string }[]; multiSelect: boolean }[] }).questions ?? []
    if (e.tool === 'AskUserQuestion' && q) {
      w.dialogs.push({ question: q.question, header: q.header, options: q.options.map((o) => o.label), multiSelect: q.multiSelect })
      if (w.answer === null) return { deny: 'dismissed' }
      return { result: { questions: [], answers: { [q.question]: w.answer } } } as never
    }
    w.reached.push(e.tool)
    return { result: `ran ${e.tool}` }
  })
  return w
}

const yvokeSession = async ($: Engine) => {
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await $.command.run({
    command: 'yvoke',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 80 },
  })
}

const call = ($: Engine, tool: string, args: Record<string, unknown> = {}) =>
  $.tool.call({ tool, ...args } as Parameters<Engine['tool']['call']>[0])

const QUESTION = { question: 'Which OIM version do you mean?', options: ['9.3.1', '10.0', '12c'] }

describe('in a Yvoke session', () => {
  test("the server's question opens the dialog, and the model gets the answer; the server is never called", async ($, on) => {
    const w = engine(on)
    await yvokeSession($)

    expect(await call($, ASKED, QUESTION)).toEqual({ result: 'User answered: 10.0' })
    expect(w.dialogs).toEqual([
      { question: 'Which OIM version do you mean?', header: 'Yvoke', options: ['9.3.1', '10.0', '12c'], multiSelect: false },
    ])
    expect(w.reached).toEqual([])
  })

  test('an answer typed under "Other" comes back as typed', async ($, on) => {
    const w = engine(on)
    await yvokeSession($)
    w.answer = 'the 11g one, I think'

    expect(await call($, ASKED, QUESTION)).toEqual({ result: 'User answered: the 11g one, I think' })
  })

  test("the same through the claude.ai connector's name", async ($, on) => {
    const w = engine(on, 'claude.ai yvoke')
    await yvokeSession($)

    expect(await call($, 'mcp__claude_ai_yvoke__ask_clarifying_question', QUESTION)).toEqual({
      result: 'User answered: 10.0',
    })
    expect(w.dialogs).toHaveLength(1)
    expect(w.reached).toEqual([])
  })

  test('the same from a subagent', async ($, on) => {
    const w = engine(on)
    await yvokeSession($)

    expect(await call($, ASKED, { ...QUESTION, agentId: 'agent-1' })).toEqual({ result: 'User answered: 10.0' })
    expect(w.dialogs).toHaveLength(1)
  })

  test('more than four options: the first four are shown', async ($, on) => {
    const w = engine(on)
    await yvokeSession($)

    await call($, ASKED, { question: 'Which one?', options: ['a', 'b', 'c', 'd', 'e', 'f'] })
    expect(w.dialogs).toEqual([expect.objectContaining({ options: ['a', 'b', 'c', 'd'] })])
  })

  test('blank and non-text options are dropped, and labels are trimmed', async ($, on) => {
    const w = engine(on)
    await yvokeSession($)

    await call($, ASKED, { question: 'Which one?', options: [' a ', '', '   ', 3, null, 'b'] })
    expect(w.dialogs).toEqual([expect.objectContaining({ options: ['a', 'b'] })])
  })

  test('no options: the dialog still opens, and the user can type an answer', async ($, on) => {
    const w = engine(on)
    await yvokeSession($)
    w.answer = 'version 10'

    expect(await call($, ASKED, { question: 'Which version?' })).toEqual({ result: 'User answered: version 10' })
    expect(w.dialogs).toEqual([expect.objectContaining({ question: 'Which version?' })])
  })

  for (const question of [undefined, '', '   ', 42]) {
    test(`a missing or blank question (${JSON.stringify(question)}): an error result, and no dialog`, async ($, on) => {
      const w = engine(on)
      await yvokeSession($)

      expect(await call($, ASKED, { question, options: ['a', 'b'] })).toEqual({ deny: NO_QUESTION })
      expect(w.dialogs).toEqual([])
      expect(w.reached).toEqual([])
    })
  }

  test('the user dismisses the dialog: the model is told the question cannot be asked', async ($, on) => {
    const w = engine(on)
    await yvokeSession($)
    w.answer = null

    expect(await call($, ASKED, QUESTION)).toEqual({ deny: CANNOT_ASK })
    expect(w.dialogs).toHaveLength(1)
    expect(w.reached).toEqual([])
  })

  test('a playbook that does not declare the tool: refused as before, no dialog', async ($, on) => {
    const w = engine(on)
    w.playbooks = playbooks(['search_corpus'])
    await yvokeSession($)

    expect(await call($, ASKED, QUESTION)).toEqual({ deny: denied(ASKED) })
    expect(w.dialogs).toEqual([])
  })

  test('AskUserQuestion itself still reaches the engine', async ($, on) => {
    const w = engine(on)
    await yvokeSession($)

    expect(await call($, 'AskUserQuestion', { questions: [] })).toEqual({ result: 'ran AskUserQuestion' })
    expect(w.reached).toEqual(['AskUserQuestion'])
  })

  test('a tool whose name only ends like it is not asked', async ($, on) => {
    const w = engine(on)
    await yvokeSession($)

    expect(await call($, 'mcp__github__ask_clarifying_question', QUESTION)).toEqual({
      deny: denied('mcp__github__ask_clarifying_question'),
    })
    expect(w.dialogs).toEqual([])
  })
})

test('outside a Yvoke session the call reaches the engine unchanged', async ($, on) => {
  const w = engine(on)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  expect(await call($, ASKED, QUESTION)).toEqual({ result: `ran ${ASKED}` })
  expect(w.dialogs).toEqual([])
})
