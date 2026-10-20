import { test, expect, mock } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { McpToolResult, On, RenderElement } from 'claude-code'

// P1-08: the setup band through the engine, with the whole mod loaded. The test stands for the engine
// beneath the plugin (session, store, the Yvoke server and the prompt), and the Yvoke session is started
// the way the user starts it: by typing /yvoke.

const SURFACES = ['terminal', 'desktop'] as const
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

const text = (body: string, isError = false): McpToolResult => ({ content: [{ type: 'text', text: body }], isError })

const area = (over: Record<string, unknown> = {}) => ({
  name: 'OIM',
  title: 'OIM',
  prototype: false,
  defaultPlaybook: 'oim-full',
  defaultProfile: 'OIM',
  playbooks: ['oim-full', 'oim-quick'],
  profiles: ['OIM'],
  ...over,
})
const playbook = (name: string, over: Record<string, unknown> = {}) => ({ name, area: 'OIM', prototype: false, version: 1, ...over })

type Server = { calls: string[]; down: boolean; areas: unknown[]; playbooks: unknown[] }
type World = {
  server: Server
  sessionId: string
  store: Map<string, unknown>
  entered: string[]
  dropBeneath: string | null
  status: (string | undefined)[]
  toasts: string[]
  failStoreRead: boolean
  failId: boolean
  savedAtSubmit: unknown[]
}

function engine(on: On, start: Partial<World> = {}): World {
  const w: World = {
    server: {
      calls: [],
      down: false,
      areas: [area(), area({ name: 'PING', title: 'PingID', defaultPlaybook: 'ping-main', profiles: [], defaultProfile: null })],
      playbooks: [playbook('oim-quick', { version: 2 }), playbook('oim-full', { version: 3 }), playbook('ping-main', { area: 'PING', version: 5 })],
    },
    sessionId: 'session-a',
    store: new Map(),
    entered: [],
    dropBeneath: null,
    status: [],
    toasts: [],
    failStoreRead: false,
    failId: false,
    savedAtSubmit: [],
    ...start,
  }
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('session.turns', () => ({ value: w.entered.length }))
  on('session.id', () => {
    if (w.failId) throw new Error('no id')
    return { value: w.sessionId }
  })
  on('store.get', (_$, e) => {
    if (w.failStoreRead) throw new Error('store unreadable')
    return { value: w.store.get(e.key) }
  })
  on('store.set', (_$, e) => {
    w.store.set(e.key, e.value)
    return { value: undefined }
  })
  on('mcp.connect', () => ({
    value: w.server.down
      ? { isConnected: false, reason: 'failed', message: 'yvoke is not connected.' }
      : { isConnected: true, server: 'plugin:yvoke:yvoke' },
  }))
  on('mcp.call', (_$, e) => {
    w.server.calls.push(e.tool)
    if (e.tool === 'list_areas') return { value: text(JSON.stringify(w.server.areas)) }
    if (e.tool === 'list_playbooks') return { value: text(JSON.stringify(w.server.playbooks)) }
    // P1-07's calls, when its module is loaded too: answer them so a question can enter.
    if (e.tool === 'get_playbook') return { value: text(JSON.stringify({ name: e.args.name, text: 'PLAYBOOK' })) }
    if (e.tool === 'get_system_prompt') return { value: text('BASE') }
    return { value: text(`ERROR: no tool ${e.tool}`) }
  })
  on('prompt.submit', (_$, e) => {
    w.savedAtSubmit.push(w.store.get(`setup:${w.sessionId}`))
    if (w.dropBeneath !== null) return { drop: w.dropBeneath }
    w.entered.push(e.text)
    return { text: e.text }
  })
  on('ui.status', (_$, e) => {
    w.status.push(e.text)
    return { value: undefined }
  })
  on('ui.toast', (_$, e) => {
    w.toasts.push(e.text)
    return { value: undefined }
  })
  on('skill.prompt', (_$, e) => ({ text: e.text }))
  // The engine's own drawing of the band: nothing.
  on('ui.render', ($$, e) => {
    const { Box } = $$.ui.resolve(e)
    return h(Box, {}) as RenderElement
  })
  on('classic.SessionStart', () => ({}))
  return w
}

const begin = ($: Engine) => $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
const yvoke = ($: Engine) =>
  $.command.run({
    command: 'yvoke',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 80 },
  })
const ask = ($: Engine, question: string) => $.prompt.submit({ text: question, wait: false, origin: { kind: 'composer' } })
const band = <S extends (typeof SURFACES)[number]>($: Engine, surface: S) =>
  $.ui.mount({ plugin: 'yvoke', surface, ...BAND })
const selected = async (ui: { find: (q: { key: string }) => Promise<{ props: Record<string, unknown> } | undefined> }, key: string) =>
  (await ui.find({ key }))?.props.value
const optionsOf = async (ui: { find: (q: { key: string }) => Promise<{ props: Record<string, unknown> } | undefined> }, key: string) =>
  ((await ui.find({ key }))?.props.options as { value: string; label: string }[] | undefined)?.map((o) => o.label)

const LINE = 'OIM · Single agent · oim-full v3'

type Ui = { find: (q: { type?: string; key?: string; text?: string | RegExp }) => Promise<{ text: string } | undefined> }
// A Text has no key, so the read-only line and the notices are found by what they say.
const lineOf = async (ui: Ui) => (await ui.find({ type: 'Text', text: / · (Single agent|Multi-agent) · / }))?.text
const errorOf = async (ui: Ui) => (await ui.find({ type: 'Text', text: /^Yvoke Backend:/ }))?.text

test('a plain session: no band, no server call, questions pass', async ($, on) => {
  const w = engine(on)
  await begin($)
  for (const surface of SURFACES) {
    const ui = await band($, surface)
    expect(await ui.find({ key: 'yvoke-area' })).toBeUndefined()
    expect(await lineOf(ui)).toBeUndefined()
    await ui.unmount()
  }
  expect(await ask($, 'fix the build')).toEqual({ text: 'fix the build' })
  expect(w.server.calls).toEqual([])
  expect(w.status).toEqual([])
})

test('/yvoke loads the lists once and the band shows the defaults on every surface', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  expect(w.server.calls.sort()).toEqual(['list_areas', 'list_playbooks'])

  for (const surface of SURFACES) {
    const ui = await band($, surface)
    expect(await selected(ui, 'yvoke-area')).toBe('OIM')
    expect(await optionsOf(ui, 'yvoke-area')).toEqual(['OIM', 'PingID'])
    expect(await selected(ui, 'yvoke-mode')).toBe('single')
    expect(await optionsOf(ui, 'yvoke-mode')).toEqual(['Single agent'])
    expect(await selected(ui, 'yvoke-playbook')).toBe('oim-full')
    expect(await optionsOf(ui, 'yvoke-playbook')).toEqual(['oim-quick v2', 'oim-full v3'])
    expect(await ui.find({ key: 'yvoke-profile' })).toBeUndefined()
    await ui.unmount()
  }
  expect(w.server.calls).toHaveLength(2)
})

test('changing the playbook, then the area, before the first question', async ($, on) => {
  engine(on)
  await begin($)
  await yvoke($)
  for (const surface of SURFACES) {
    const ui = await band($, surface)
    await ui.select({ key: 'yvoke-playbook', value: 'oim-quick' })
    expect(await selected(ui, 'yvoke-playbook')).toBe('oim-quick')

    await ui.select({ key: 'yvoke-area', value: 'PING' })
    expect(await selected(ui, 'yvoke-area')).toBe('PING')
    expect(await selected(ui, 'yvoke-playbook')).toBe('ping-main')
    expect(await optionsOf(ui, 'yvoke-playbook')).toEqual(['ping-main v5'])

    await ui.select({ key: 'yvoke-area', value: 'OIM' })
    expect(await selected(ui, 'yvoke-playbook')).toBe('oim-full')
    await ui.unmount()
  }
})

test('the first question locks the setup: a read-only line in the band and the status line', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  const first = await band($, 'terminal')
  await first.select({ key: 'yvoke-playbook', value: 'oim-quick' })
  await first.unmount()

  expect(await ask($, 'how do I reset a password?')).toEqual({ text: 'how do I reset a password?' })

  for (const surface of SURFACES) {
    const ui = await band($, surface)
    expect(await lineOf(ui)).toBe('OIM · Single agent · oim-quick v2')
    expect(await ui.find({ key: 'yvoke-area' })).toBeUndefined()
    expect(await ui.find({ key: 'yvoke-playbook' })).toBeUndefined()
    await ui.unmount()
  }
  expect(w.status.at(-1)).toBe('OIM · Single agent · oim-quick v2')
  expect(w.store.get('setup:session-a')).toMatchObject({ playbook: 'oim-quick', version: 2, lockedSessionId: 'session-a' })
})

test('just typing accepts the defaults', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  await ask($, 'q')
  expect(w.status.at(-1)).toBe(LINE)
})

test('the locked line keeps its version when the server moves on', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  await ask($, 'q')
  w.server.playbooks = [playbook('oim-full', { version: 4 })]

  await ask($, 'q2')
  const ui = await band($, 'terminal')
  expect(await lineOf(ui)).toBe(LINE)
})

test('a question dropped beneath does not lock the setup', async ($, on) => {
  const w = engine(on, { dropBeneath: 'Yvoke Backend: ERROR: playbook not found.' })
  await begin($)
  await yvoke($)

  expect(await ask($, 'q')).toEqual({ drop: 'Yvoke Backend: ERROR: playbook not found.' })
  const ui = await band($, 'terminal')
  expect(await selected(ui, 'yvoke-playbook')).toBe('oim-full')
  expect(await lineOf(ui)).toBeUndefined()
  expect(w.status).toEqual([])
})

test('the setup is saved before the question goes down, for P1-07 to read', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  await ask($, 'q')
  expect(w.savedAtSubmit[0]).toMatchObject({ area: 'OIM', mode: 'single', playbook: 'oim-full', lockedSessionId: null })
})

test('server down: the band shows the error and Retry, a question is dropped, Retry loads again', async ($, on) => {
  const w = engine(on)
  w.server.down = true
  await begin($)
  await yvoke($)

  for (const surface of SURFACES) {
    const ui = await band($, surface)
    expect(await errorOf(ui)).toContain('Yvoke Backend: yvoke is not connected.')
    expect(await ui.find({ key: 'yvoke-retry' })).toBeDefined()
    await ui.unmount()
  }
  expect(await ask($, 'q')).toMatchObject({ drop: expect.stringContaining('Yvoke Backend:') })
  expect(w.entered).toEqual([])

  w.server.down = false
  const ui = await band($, 'terminal')
  await ui.press({ key: 'yvoke-retry' })
  expect(await selected(ui, 'yvoke-area')).toBe('OIM')
})

test('a server with no areas: the band says so and questions are refused', async ($, on) => {
  const w = engine(on)
  w.server.areas = []
  await begin($)
  await yvoke($)
  const ui = await band($, 'terminal')
  expect(await errorOf(ui)).toContain('the server has no areas')
  expect(await ask($, 'q')).toEqual({ drop: 'Yvoke Backend: the server has no areas.' })
})

test('an area with no playbooks: the band says so and questions are refused', async ($, on) => {
  const w = engine(on)
  w.server.playbooks = [playbook('ping-main', { area: 'PING' })]
  await begin($)
  await yvoke($)
  const ui = await band($, 'terminal')
  expect((await ui.find({ type: 'Text', text: /no playbooks/ }))?.text).toContain('no playbooks')
  expect(await ask($, 'q')).toEqual({ drop: 'Yvoke Backend: the area OIM has no playbooks, so the question was not sent.' })
})

test('a question with no lists loaded yet loads them first', async ($, on) => {
  const w = engine(on)
  w.server.down = true
  await begin($)
  await yvoke($)
  w.server.down = false
  expect(await ask($, 'q')).toEqual({ text: 'q' })
  expect(w.status.at(-1)).toBe(LINE)
})

test('a survey holds the band: the hook yields', async ($, on) => {
  engine(on)
  await begin($)
  await yvoke($)
  const ui = await $.ui.mount({ plugin: 'yvoke', surface: 'terminal', ...BAND, props: { ...BAND.props, hasSurvey: true } })
  expect(await ui.find({ key: 'yvoke-area' })).toBeUndefined()
})

test('/clear ends the setup with the session', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  await ask($, 'q')

  w.sessionId = 'session-b'
  w.entered = []
  await $.classic.SessionStart({ source: 'clear', session_id: 'session-b' })
  expect(w.status.at(-1)).toBeUndefined()
  const ui = await band($, 'terminal')
  expect(await lineOf(ui)).toBeUndefined()
  expect(await ui.find({ key: 'yvoke-area' })).toBeUndefined()
  await ui.unmount()

  await yvoke($)
  const fresh = await band($, 'terminal')
  expect(await selected(fresh, 'yvoke-playbook')).toBe('oim-full')
})

const LOCKED = { area: 'OIM', mode: 'single', playbook: 'oim-quick', version: 2, profile: null, lockedSessionId: 'old' }

test('/resume brings back a locked setup', async ($, on) => {
  const w = engine(on, {
    sessionId: 'old',
    store: new Map<string, unknown>([
      ['session:old', { yvoke: true }],
      ['setup:old', LOCKED],
    ]),
  })
  await begin($)
  await $.classic.SessionStart({ source: 'resume', session_id: 'old' })

  for (const surface of SURFACES) {
    const ui = await band($, surface)
    expect(await lineOf(ui)).toBe('OIM · Single agent · oim-quick v2')
    await ui.unmount()
  }
  expect(w.status.at(-1)).toBe('OIM · Single agent · oim-quick v2')
  expect(w.toasts).toEqual([])
  expect(await ask($, 'q')).toEqual({ text: 'q' })
})

test('/resume with a newer playbook on the server: the setup moves to it and says so once (D-20)', async ($, on) => {
  const w = engine(on, {
    sessionId: 'old',
    store: new Map<string, unknown>([
      ['session:old', { yvoke: true }],
      ['setup:old', LOCKED],
    ]),
  })
  w.server.playbooks = [playbook('oim-quick', { version: 4 })]
  await begin($)
  await $.classic.SessionStart({ source: 'resume', session_id: 'old' })

  const ui = await band($, 'terminal')
  expect(await lineOf(ui)).toBe('OIM · Single agent · oim-quick v4')
  expect(w.toasts).toEqual(['oim-quick moved from v2 to v4 since this session started; answers from now on use v4.'])
  expect(w.store.get('setup:old')).toMatchObject({ version: 4 })

  await $.classic.SessionStart({ source: 'resume', session_id: 'old' })
  expect(w.toasts).toHaveLength(1)
})

test('/resume of an unlocked setup keeps the picks, unlocked', async ($, on) => {
  engine(on, {
    sessionId: 'old',
    store: new Map<string, unknown>([
      ['session:old', { yvoke: true }],
      ['setup:old', { ...LOCKED, lockedSessionId: null }],
    ]),
  })
  await begin($)
  await $.classic.SessionStart({ source: 'resume', session_id: 'old' })
  const ui = await band($, 'terminal')
  expect(await selected(ui, 'yvoke-playbook')).toBe('oim-quick')
})

test('/resume of a plain session, or a store that cannot be read: no setup', async ($, on) => {
  const w = engine(on, { sessionId: 'old', store: new Map<string, unknown>([['setup:old', LOCKED]]) })
  await begin($)
  await $.classic.SessionStart({ source: 'resume', session_id: 'old' })
  const plain = await band($, 'terminal')
  expect(await lineOf(plain)).toBeUndefined()
  await plain.unmount()
  expect(w.server.calls).toEqual([])

  w.store.set('session:old', { yvoke: true })
  w.failStoreRead = true
  await $.classic.SessionStart({ source: 'resume', session_id: 'old' })
  const ui = await band($, 'terminal')
  expect(await lineOf(ui)).toBeUndefined()
})

test('/branch keeps a locked setup in the new session', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  await ask($, 'q')

  w.sessionId = 'session-b'
  await $.classic.SessionStart({ source: 'fork', session_id: 'session-b' })
  expect(w.store.get('setup:session-b')).toMatchObject({ playbook: 'oim-full', version: 3, lockedSessionId: 'session-b' })
  const ui = await band($, 'terminal')
  expect(await lineOf(ui)).toBe(LINE)
})

test('a failing hook drops the question (fails closed)', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  w.failId = true
  expect(await ask($, 'q')).toMatchObject({ drop: expect.stringContaining('Yvoke Backend:') })
  expect(w.entered).toEqual([])
})

test('a playbook skill in a Yvoke session is replaced by a pointer to the band', async ($, on) => {
  engine(on)
  await begin($)
  const skill = (pluginName?: string) =>
    $.skill.prompt({ skill: 'oim-full', text: 'PLAYBOOK STUB', frontmatter: {}, source: 'plugin', pluginName })

  expect(await skill('yvoke')).toEqual({ text: 'PLAYBOOK STUB' })
  await yvoke($)
  expect((await skill('yvoke')).text).toContain('setup band')
  expect(await skill('other')).toEqual({ text: 'PLAYBOOK STUB' })
})

test('prototypes show only when the setting is on', { options: { showPrototypes: true } }, async ($, on) => {
  const w = engine(on)
  w.server.areas = [area(), area({ name: 'LAB', title: 'Lab', prototype: true })]
  await begin($)
  await yvoke($)
  const ui = await band($, 'terminal')
  expect(await optionsOf(ui, 'yvoke-area')).toEqual(['OIM', 'Lab'])
})
