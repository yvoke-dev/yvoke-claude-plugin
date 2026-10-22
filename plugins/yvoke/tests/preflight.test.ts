import { test, expect } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { McpToolResult, On, RenderElement } from 'claude-code'
import { CHECKING, MAX_TOKENS, SEND_ANYWAY, VALIDATION_TIMEOUT_MS } from '../src/preflight'

// P2-06: the preflight check through the engine, with the whole mod loaded: the setup band (P1-08) around
// it and the instructions load (P1-07) beneath it. The test stands for the engine beneath the plugin:
// the session, store, the Yvoke server, the model and the user's answer to the dialog.

const BAND = {
  component: 'AbovePrompt',
  props: {
    hasSurvey: false,
    isWorking: false,
    maxRows: 10,
    bodyColumns: 80,
    scroll: { offset: 0, bodyRows: 9, contentRows: 0 },
    view: {},
  },
} as const

const text = (body: string): McpToolResult => ({ content: [{ type: 'text', text: body }], isError: false })

const area = (over: Record<string, unknown> = {}) => ({
  name: 'OIM',
  title: 'OIM',
  prototype: false,
  defaultPlaybook: 'oim-full',
  defaultSystemPrompt: null,
  systemPrompts: [],
  ...over,
})
const playbook = (name: string, over: Record<string, unknown> = {}) => ({
  name,
  title: name,
  description: '',
  area: 'OIM',
  prototype: false,
  version: 1,
  ...over,
})

const USAGE = { input_tokens: 10, output_tokens: 5, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }
const answered = (reply: string) => ({ isAnswered: true, text: reply, usage: USAGE })
const verdict = (plausible: boolean, suggestedPlaybookName: string | null = null, reason = '') =>
  answered(JSON.stringify({ plausible, reason, suggestedPlaybookName }))
const BETTER = verdict(false, 'oim-schema', 'The question is about table columns.')
const QUESTION = 'Which columns does the Person table have?'
const DIALOG = 'The question is about table columns. Switch to Schema (oim-schema) for this session?'

type World = {
  sessionId: string
  store: Map<string, unknown>
  areas: unknown[]
  playbooks: unknown[]
  calls: { tool: string; args: Record<string, unknown> }[]
  entered: string[]
  status: (string | undefined)[]
  /** What the model answers; a function may throw, as the engine does for a request it refuses. */
  model: unknown | (() => unknown)
  models: Record<string, unknown>[]
  /** The label the user picks; null dismisses the dialog. */
  answer: string | null
  asks: Record<string, unknown>[]
  failModelName: boolean
  dropBeneath: string | null
  order: string[]
}

function engine(on: On, start: Partial<World> = {}): World {
  const w: World = {
    sessionId: 'session-a',
    store: new Map(),
    areas: [area(), area({ name: 'PING', title: 'PingID', defaultPlaybook: 'ping-main' })],
    playbooks: [
      playbook('oim-full', { title: 'Full', version: 3 }),
      playbook('oim-start', { title: 'Getting started', description: 'Onboarding and end-user questions.' }),
      playbook('oim-schema', { title: 'Schema', description: 'Table and column definitions.', version: 2 }),
      playbook('ping-main', { area: 'PING' }),
      playbook('ping-other', { area: 'PING' }),
    ],
    calls: [],
    entered: [],
    status: [],
    model: verdict(true),
    models: [],
    answer: null,
    asks: [],
    failModelName: false,
    dropBeneath: null,
    order: [],
    ...start,
  }
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('session.turns', () => ({ value: w.entered.length }))
  on('session.id', () => ({ value: w.sessionId }))
  on('session.model', () => {
    if (w.failModelName) throw new Error('no model')
    return { value: 'claude-sonnet-5-5' }
  })
  on('store.get', (_$, e) => ({ value: w.store.get(e.key) }))
  on('store.set', (_$, e) => {
    w.store.set(e.key, e.value)
    return { value: undefined }
  })
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:yvoke:yvoke' } }))
  on('mcp.call', (_$, e) => {
    w.calls.push({ tool: e.tool, args: e.args })
    if (e.tool === 'list_areas') return { value: text(JSON.stringify(w.areas)) }
    if (e.tool === 'list_playbooks') return { value: text(JSON.stringify(w.playbooks)) }
    if (e.tool === 'get_playbook') return { value: text(JSON.stringify({ name: e.args.name, text: `PLAYBOOK ${e.args.name}` })) }
    if (e.tool === 'get_system_prompt') return { value: text('BASE') }
    return { value: text(`ERROR: no tool ${e.tool}`) }
  })
  on('model.complete', (_$, e) => {
    w.order.push('model')
    w.models.push(e as unknown as Record<string, unknown>)
    return { value: typeof w.model === 'function' ? (w.model as () => unknown)() : w.model } as never
  })
  on('tool.call', { tool: 'AskUserQuestion' }, (_$, e) => {
    w.order.push('ask')
    w.asks.push(e as unknown as Record<string, unknown>)
    const question = (e as unknown as { questions: { question: string }[] }).questions[0]!.question
    return { result: { questions: [], answers: w.answer === null ? {} : { [question]: w.answer } } } as never
  })
  on('prompt.submit', (_$, e) => {
    w.order.push('entered')
    if (w.dropBeneath !== null) return { drop: w.dropBeneath }
    w.entered.push(e.text)
    return { text: e.text }
  })
  on('ui.status', (_$, e) => {
    w.order.push(`status:${e.text ?? ''}`)
    w.status.push(e.text)
    return { value: undefined }
  })
  on('ui.toast', () => ({ value: undefined }))
  on('ui.render', ($$, e) => {
    const { Box } = $$.ui.resolve(e)
    return h(Box, {}) as RenderElement
  })
  on('classic.SessionStart', () => ({}))
  on('prompt.compose', () => ({ sections: [] }))
  return w
}

const begin = ($: Engine) => $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
const yvoke = ($: Engine) =>
  $.command.run({ command: 'yvoke', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 80 } })
const ask = ($: Engine, question: string) => $.prompt.submit({ text: question, wait: false, origin: { kind: 'composer' } })
const choose = async ($: Engine, value: string) => {
  const ui = await $.ui.mount({ plugin: 'yvoke', surface: 'terminal', ...BAND })
  await ui.select({ key: 'yvoke-playbook', value })
  await ui.unmount()
}
/** A Yvoke session on `playbook`, ready for its first question. */
const ready = async ($: Engine, playbook = 'oim-start') => {
  await begin($)
  await yvoke($)
  await choose($, playbook)
}
const RENDER = { model: 'claude-x', promptModel: 'claude-x', surfaces: ['terminal'], tools: [], outputStyle: null, traits: [] } as const
const instructions = async ($: Engine) => (await $.prompt.compose(RENDER)).sections.map((s) => s.text)
const loaded = (w: World) => w.calls.filter((c) => c.tool === 'get_playbook').map((c) => c.args.name)
const locked = (w: World) => w.store.get(`setup:${w.sessionId}`) as Record<string, unknown> | undefined

test('a fit: one tool-less check with the session model, then the question goes through as chosen', async ($, on) => {
  const w = engine(on)
  await ready($)

  expect(await ask($, QUESTION)).toEqual({ text: QUESTION })
  expect(w.models).toHaveLength(1)
  expect(w.models[0]).toMatchObject({
    model: 'claude-sonnet-5-5',
    prompt: QUESTION,
    maxTokens: MAX_TOKENS,
    effort: 'low',
    timeoutMs: VALIDATION_TIMEOUT_MS,
  })
  const system = String(w.models[0]!.system)
  expect(system).toContain('oim-start | Getting started | Onboarding and end-user questions.')
  expect(system).toContain('oim-schema | Schema | Table and column definitions.')
  expect(system).not.toContain('oim-full')
  expect(system).toContain('Selected Playbook Name:\noim-start')
  expect(w.asks).toEqual([])
  expect(loaded(w)).toEqual(['oim-start'])
  expect(locked(w)).toMatchObject({ playbook: 'oim-start', lockedSessionId: 'session-a' })
})

test('a better match, Switch: the suggested playbook loads and the setup locks with it', async ($, on) => {
  const w = engine(on, { model: BETTER, answer: 'Switch to oim-schema' })
  await ready($)

  expect(await ask($, QUESTION)).toEqual({ text: QUESTION })
  expect(w.asks).toHaveLength(1)
  expect(w.asks[0]).toMatchObject({
    questions: [
      {
        question: DIALOG,
        header: 'Playbook',
        options: [{ label: 'Switch to oim-schema' }, { label: SEND_ANYWAY }],
      },
    ],
  })
  expect(loaded(w)).toEqual(['oim-schema'])
  expect(locked(w)).toMatchObject({ playbook: 'oim-schema', version: 2, lockedSessionId: 'session-a' })
  expect(w.status.at(-1)).toBe('OIM · Single agent · oim-schema v2')
})

for (const [label, answer] of [
  ['Send anyway', SEND_ANYWAY],
  ['text typed under Other', 'just answer it'],
  ['the dialog dismissed', null],
] as const) {
  test(`a better match, ${label}: the question goes through with the playbook as chosen`, async ($, on) => {
    const w = engine(on, { model: BETTER, answer })
    await ready($)

    expect(await ask($, QUESTION)).toEqual({ text: QUESTION })
    expect(w.asks).toHaveLength(1)
    expect(loaded(w)).toEqual(['oim-start'])
    expect(locked(w)).toMatchObject({ playbook: 'oim-start', lockedSessionId: 'session-a' })
  })
}

const NO_REPLY: [string, World['model']][] = [
  ['the timeout', { isAnswered: false, reason: 'aborted', usage: USAGE }],
  ['an API error', { isAnswered: false, reason: 'api-error', status: 529, error: 'overloaded', usage: USAGE }],
  ['an empty reply', { isAnswered: false, reason: 'empty-reply', usage: USAGE }],
  [
    'a request the engine refuses',
    () => {
      throw new Error('model not allowed')
    },
  ],
  ['an unparseable reply', answered('I would rather not say.')],
  ['an unknown suggestion', verdict(false, 'oim-invented', 'Try this.')],
  ['a suggestion of the chosen playbook', verdict(false, 'oim-start', 'Fine as it is.')],
  ["a suggestion of the area's default playbook", verdict(false, 'oim-full', 'Use everything.')],
]
for (const [label, model] of NO_REPLY) {
  test(`fails open on ${label}: no dialog, the question goes through as chosen`, async ($, on) => {
    const w = engine(on, { model })
    await ready($)

    expect(await ask($, QUESTION)).toEqual({ text: QUESTION })
    expect(w.models).toHaveLength(1)
    expect(w.asks).toEqual([])
    expect(loaded(w)).toEqual(['oim-start'])
    expect(w.status[0]).toBe(CHECKING)
    expect(w.status[1]).toBeUndefined()
  })
}

test('fails open when the session model cannot be read', async ($, on) => {
  const w = engine(on, { failModelName: true, model: BETTER })
  await ready($)
  expect(await ask($, QUESTION)).toEqual({ text: QUESTION })
  expect(w.models).toEqual([])
  expect(w.asks).toEqual([])
  expect(loaded(w)).toEqual(['oim-start'])
})

test('the checking line shows while the model works and is gone before the dialog and the question', async ($, on) => {
  const w = engine(on, { model: BETTER, answer: SEND_ANYWAY })
  await ready($)
  await ask($, QUESTION)
  const from = w.order.indexOf(`status:${CHECKING}`)
  expect(from).toBeGreaterThan(-1)
  expect(w.order.slice(from, from + 5)).toEqual([`status:${CHECKING}`, 'model', 'status:', 'ask', 'entered'])
})

test("no check for the area's default playbook", async ($, on) => {
  const w = engine(on, { model: BETTER })
  await begin($)
  await yvoke($)
  expect(await ask($, QUESTION)).toEqual({ text: QUESTION })
  expect(w.models).toEqual([])
  expect(w.status.includes(CHECKING)).toBe(false)
})

test('no check in an area with no other playbook to suggest', async ($, on) => {
  const w = engine(on, { model: BETTER })
  await begin($)
  await yvoke($)
  const ui = await $.ui.mount({ plugin: 'yvoke', surface: 'terminal', ...BAND })
  await ui.select({ key: 'yvoke-area', value: 'PING' })
  await ui.select({ key: 'yvoke-playbook', value: 'ping-other' })
  await ui.unmount()

  expect(await ask($, QUESTION)).toEqual({ text: QUESTION })
  expect(w.models).toEqual([])
  expect(loaded(w)).toEqual(['ping-other'])
})

test('no check in a plain session', async ($, on) => {
  const w = engine(on, { model: BETTER })
  await begin($)
  expect(await ask($, QUESTION)).toEqual({ text: QUESTION })
  expect(w.models).toEqual([])
})

test('once per session: the second question is not checked', async ($, on) => {
  const w = engine(on)
  await ready($)
  await ask($, QUESTION)
  await ask($, 'and the Department table?')
  expect(w.models).toHaveLength(1)
})

test('a first question dropped beneath and sent again is not checked twice', async ($, on) => {
  const w = engine(on, { dropBeneath: 'Yvoke Backend: ERROR: down.' })
  await ready($)
  expect(await ask($, QUESTION)).toEqual({ drop: 'Yvoke Backend: ERROR: down.' })
  w.dropBeneath = null
  expect(await ask($, QUESTION)).toEqual({ text: QUESTION })
  expect(w.models).toHaveLength(1)
})

test('changing the playbook after a dropped first question makes it a new check', async ($, on) => {
  const w = engine(on, { dropBeneath: 'Yvoke Backend: ERROR: down.' })
  await ready($)
  await ask($, QUESTION)
  w.dropBeneath = null
  await choose($, 'oim-schema')
  await ask($, QUESTION)
  expect(w.models).toHaveLength(2)
  expect(String(w.models[1]!.system)).toContain('Selected Playbook Name:\noim-schema')
})

// Found in review of #30 (P1-07 and P1-08 together): instructions loaded for a first question that a hook
// beneath then dropped must not serve the playbook chosen afterwards.
test('a playbook changed after a dropped first question loads its own instructions', async ($, on) => {
  const w = engine(on, { dropBeneath: 'Yvoke Backend: ERROR: down.' })
  await ready($)
  await ask($, QUESTION)
  w.dropBeneath = null
  await choose($, 'oim-schema')
  expect(await ask($, QUESTION)).toEqual({ text: QUESTION })
  expect(loaded(w)).toEqual(['oim-start', 'oim-schema'])
  expect(await instructions($)).toEqual(['BASE\n\n---\n\nPLAYBOOK oim-schema'])
})

// Claude Code 2.1.306 marked a slash command with `command`; the kit passes the field on.
for (const [label, command, checks] of [
  ['a slash command is not checked', '/review', 0],
  ['an empty command field is not a command', '', 1],
] as const) {
  test(label, async ($, on) => {
    const w = engine(on)
    await ready($)
    await $.prompt.submit({ text: QUESTION, wait: false, origin: { kind: 'composer' }, command } as never)
    expect(w.models).toHaveLength(checks)
  })
}
