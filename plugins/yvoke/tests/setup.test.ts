import { test, expect, mock } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { McpToolResult, On, RenderElement } from 'claude-code'

// setup.md: the setup band through the engine, with the whole mod loaded. The test stands for the engine
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

// savedAfterLists: the playbooks the server has once list_playbooks has answered, a save in between.
type Server = { calls: string[]; down: boolean; areas: unknown[]; playbooks: unknown[]; savedAfterLists: unknown[] | null }
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
  failLockedSave: boolean
  savedAtSubmit: unknown[]
  /** The saved setup each time src/system-prompt.ts fetched a playbook's text. */
  savedAtFetch: unknown[]
}

function engine(on: On, start: Partial<World> = {}): World {
  const w: World = {
    server: {
      calls: [],
      down: false,
      areas: [area(), area({ name: 'PING', title: 'PingID', defaultPlaybook: 'ping-main', profiles: [], defaultProfile: null })],
      playbooks: [playbook('oim-quick', { version: 2 }), playbook('oim-full', { version: 3 }), playbook('ping-main', { area: 'PING', version: 5 })],
      savedAfterLists: null,
    },
    sessionId: 'session-a',
    store: new Map(),
    entered: [],
    dropBeneath: null,
    status: [],
    toasts: [],
    failStoreRead: false,
    failId: false,
    failLockedSave: false,
    savedAtSubmit: [],
    savedAtFetch: [],
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
    if (w.failLockedSave && (e.value as { lockedSessionId?: unknown } | undefined)?.lockedSessionId) throw new Error('disk full')
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
    if (e.tool === 'list_playbooks') {
      const listed = text(JSON.stringify(w.server.playbooks))
      if (w.server.savedAfterLists) [w.server.playbooks, w.server.savedAfterLists] = [w.server.savedAfterLists, null]
      return { value: listed }
    }
    // src/system-prompt.ts's calls, when its module is loaded too: answer them so a question can enter.
    if (e.tool === 'get_playbook') {
      w.savedAtFetch.push(w.store.get(`setup:${w.sessionId}`))
      // The playbook as the server has it now, which may be newer than the lists the band loaded.
      const now = (w.server.playbooks as { name?: unknown; version?: unknown }[]).find((p) => p.name === e.args.name)
      return { value: text(JSON.stringify({ name: e.args.name, text: 'PLAYBOOK', version: now?.version })) }
    }
    if (e.tool === 'get_system_prompt') return { value: text('BASE') }
    return { value: text(`ERROR: no tool ${e.tool}`) }
  })
  // The tools themselves, behind the tool gate (tool-policy.md).
  on('tool.call', (_$, e) => ({ result: `ran ${e.tool}` }))
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
const useTool = ($: Engine, tool: string) => $.tool.call({ tool } as Parameters<Engine['tool']['call']>[0])
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

// What the plugin writes to `yvoke.instructions` (system-prompt.md), seen on its way to the kit's store.
const keptInstructions = (on: On) => {
  const kept: { loadedFor?: unknown }[] = []
  on('state.set', { plugin: 'yvoke', key: 'instructions' }, (_$, e, next) => {
    kept.push(e.value as { loadedFor?: unknown })
    return next(e)
  })
  return kept
}
const SAVED_SINCE = 'oim-full is now v4 on the server, so this session uses v4 (the band showed v3).'

test('the server saved the playbook after /yvoke: the lock takes the version that loaded and says so once', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  // The band still shows the lists /yvoke loaded.
  w.server.playbooks = [playbook('oim-quick', { version: 2 }), playbook('oim-full', { version: 4 })]
  const before = await band($, 'terminal')
  expect(await optionsOf(before, 'yvoke-playbook')).toEqual(['oim-quick v2', 'oim-full v3'])
  await before.unmount()

  expect(await ask($, 'q')).toEqual({ text: 'q' })

  for (const surface of SURFACES) {
    const ui = await band($, surface)
    expect(await lineOf(ui)).toBe('OIM · Single agent · oim-full v4')
    await ui.unmount()
  }
  expect(w.status.at(-1)).toBe('OIM · Single agent · oim-full v4')
  expect(w.store.get('setup:session-a')).toMatchObject({ playbook: 'oim-full', version: 4, lockedSessionId: 'session-a' })
  expect(w.toasts).toEqual([SAVED_SINCE])

  // The next question reuses what loaded: no second load and no second toast.
  w.server.calls = []
  expect(await ask($, 'q2')).toEqual({ text: 'q2' })
  expect(w.server.calls).toEqual([])
  expect(w.toasts).toEqual([SAVED_SINCE])
})

test('the version that loaded is the one the band showed: the lock keeps it, with no toast', async ($, on) => {
  const w = engine(on)
  const kept = keptInstructions(on)
  await begin($)
  await yvoke($)

  expect(await ask($, 'q')).toEqual({ text: 'q' })

  expect(kept.map((k) => k.loadedFor)).toEqual([{ area: 'OIM', mode: 'single', playbook: 'oim-full', version: 3 }])
  expect(w.status.at(-1)).toBe(LINE)
  expect(w.store.get('setup:session-a')).toMatchObject({ version: 3, lockedSessionId: 'session-a' })
  expect(w.toasts).toEqual([])
})

test('get_playbook gives no version: the lock keeps the version the band showed, with no toast', async ($, on) => {
  const w = engine(on)
  const kept = keptInstructions(on)
  await begin($)
  await yvoke($)
  w.server.playbooks = [playbook('oim-quick', { version: 2 }), playbook('oim-full', { version: undefined })]

  expect(await ask($, 'q')).toEqual({ text: 'q' })

  expect(kept.map((k) => k.loadedFor)).toEqual([{ area: 'OIM', mode: 'single', playbook: 'oim-full', version: null }])
  const ui = await band($, 'terminal')
  expect(await lineOf(ui)).toBe(LINE)
  expect(w.status.at(-1)).toBe(LINE)
  expect(w.store.get('setup:session-a')).toMatchObject({ version: 3, lockedSessionId: 'session-a' })
  expect(w.toasts).toEqual([])
})

test('reading the instructions fails after the question entered: it still enters, locked with the version the band showed', async ($, on) => {
  const w = engine(on)
  const kept = keptInstructions(on)
  // Once src/system-prompt.ts has written them, a read of the instructions gets an answer the plugin
  // cannot use.
  on('state.get', { plugin: 'yvoke', key: 'instructions' }, (_$, e, next) => (kept.length > 0 ? { value: null as never } : next(e)))
  await begin($)
  await yvoke($)
  w.server.playbooks = [playbook('oim-quick', { version: 2 }), playbook('oim-full', { version: 4 })]

  expect(await ask($, 'q')).toEqual({ text: 'q' })

  // v4 loaded, but the lock could not read it.
  expect(kept.map((k) => k.loadedFor)).toEqual([{ area: 'OIM', mode: 'single', playbook: 'oim-full', version: 4 }])
  expect(w.entered).toEqual(['q'])
  expect(w.status.at(-1)).toBe(LINE)
  expect(w.store.get('setup:session-a')).toMatchObject({ version: 3, lockedSessionId: 'session-a' })
  expect(w.toasts).toEqual([])
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

test('the setup is saved before the question goes down, for src/system-prompt.ts to read', async ($, on) => {
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
  expect(w.toasts).toEqual(['oim-quick moved from v2 to v4 since the setup was locked; answers from now on use v4.'])
  expect(w.store.get('setup:old')).toMatchObject({ version: 4 })

  await $.classic.SessionStart({ source: 'resume', session_id: 'old' })
  expect(w.toasts).toHaveLength(1)
})

const V4 = 'OIM · Single agent · oim-full v4'
const SAVED_V4 = [playbook('oim-quick', { version: 2 }), playbook('oim-full', { version: 4 })]

test('/resume of a locked session, then a save on the server: its first question moves it to the version that loaded, once', async ($, on) => {
  const w = engine(on, {
    sessionId: 'old',
    store: new Map<string, unknown>([
      ['session:old', { yvoke: true }],
      ['setup:old', { ...LOCKED, playbook: 'oim-full', version: 3 }],
    ]),
  })
  await begin($)
  await $.classic.SessionStart({ source: 'resume', session_id: 'old' })
  expect(w.status.at(-1)).toBe(LINE)
  // Saved after /resume loaded the lists.
  w.server.playbooks = SAVED_V4

  expect(await ask($, 'q')).toEqual({ text: 'q' })

  for (const surface of SURFACES) {
    const ui = await band($, surface)
    expect(await lineOf(ui)).toBe(V4)
    await ui.unmount()
  }
  expect(w.status.at(-1)).toBe(V4)
  expect(w.store.get('setup:old')).toEqual({ ...LOCKED, playbook: 'oim-full', version: 4 })
  expect(w.toasts).toEqual([SAVED_SINCE])

  w.server.calls = []
  expect(await ask($, 'q2')).toEqual({ text: 'q2' })
  expect(w.server.calls).toEqual([])
  expect(w.toasts).toEqual([SAVED_SINCE])
})

test('/branch of a locked session, then a save on the server: its first question moves it to the version that loaded, once', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  await ask($, 'q')
  w.sessionId = 'session-b'
  await $.classic.SessionStart({ source: 'fork', session_id: 'session-b' })
  expect(w.status.at(-1)).toBe(LINE)
  // Saved after /branch loaded the lists.
  w.server.playbooks = SAVED_V4

  expect(await ask($, 'q2')).toEqual({ text: 'q2' })

  for (const surface of SURFACES) {
    const ui = await band($, surface)
    expect(await lineOf(ui)).toBe(V4)
    await ui.unmount()
  }
  expect(w.status.at(-1)).toBe(V4)
  expect(w.store.get('setup:session-b')).toMatchObject({ playbook: 'oim-full', version: 4, lockedSessionId: 'session-b' })
  expect(w.toasts).toEqual([SAVED_SINCE])

  w.server.calls = []
  expect(await ask($, 'q3')).toEqual({ text: 'q3' })
  expect(w.server.calls).toEqual([])
  expect(w.toasts).toEqual([SAVED_SINCE])
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

// A resumed session whose saved setup, still unlocked, names `name` in `area`.
const resumedUnlocked = (on: On, name: string, area = 'OIM') =>
  engine(on, {
    sessionId: 'old',
    store: new Map<string, unknown>([
      ['session:old', { yvoke: true }],
      ['setup:old', { ...LOCKED, area, playbook: name, version: 1, lockedSessionId: null }],
    ]),
  })

test("/resume of an unlocked setup whose playbook is gone: the band and the question take its area's defaults", async ($, on) => {
  const w = resumedUnlocked(on, 'ping-old', 'PING')
  await begin($)
  await $.classic.SessionStart({ source: 'resume', session_id: 'old' })
  for (const surface of SURFACES) {
    const ui = await band($, surface)
    expect(await selected(ui, 'yvoke-area')).toBe('PING')
    expect(await selected(ui, 'yvoke-playbook')).toBe('ping-main')
    await ui.unmount()
  }

  expect(await ask($, 'q')).toEqual({ text: 'q' })
  const pingMain = { area: 'PING', mode: 'single', playbook: 'ping-main', version: 5, profile: null }
  expect(w.savedAtFetch).toEqual([{ ...pingMain, lockedSessionId: null }])
  expect(w.store.get('setup:old')).toEqual({ ...pingMain, lockedSessionId: 'old' })
  expect(w.status.at(-1)).toBe('PingID · Single agent · ping-main v5')
})

test("/resume of an unlocked setup whose playbook is now a hidden prototype: the area's default", async ($, on) => {
  const w = resumedUnlocked(on, 'oim-new')
  w.server.playbooks = [...w.server.playbooks, playbook('oim-new', { prototype: true })]
  await begin($)
  await $.classic.SessionStart({ source: 'resume', session_id: 'old' })
  const ui = await band($, 'terminal')
  expect(await selected(ui, 'yvoke-playbook')).toBe('oim-full')
  expect(await optionsOf(ui, 'yvoke-playbook')).toEqual(['oim-quick v2', 'oim-full v3'])
  await ui.unmount()

  expect(await ask($, 'q')).toEqual({ text: 'q' })
  expect(w.store.get('setup:old')).toMatchObject({ playbook: 'oim-full', version: 3, lockedSessionId: 'old' })
  expect(w.status.at(-1)).toBe(LINE)
})

test('/resume of an unlocked setup whose prototype playbook is shown: kept', { options: { showPrototypes: true } }, async ($, on) => {
  const w = resumedUnlocked(on, 'oim-new')
  w.server.playbooks = [...w.server.playbooks, playbook('oim-new', { prototype: true })]
  await begin($)
  await $.classic.SessionStart({ source: 'resume', session_id: 'old' })
  const ui = await band($, 'terminal')
  expect(await selected(ui, 'yvoke-playbook')).toBe('oim-new')
})

test('/resume of a locked setup whose playbook is gone: kept as it was', async ($, on) => {
  const w = engine(on, {
    sessionId: 'old',
    store: new Map<string, unknown>([
      ['session:old', { yvoke: true }],
      ['setup:old', LOCKED],
    ]),
  })
  w.server.playbooks = [playbook('oim-full', { version: 3 })]
  await begin($)
  await $.classic.SessionStart({ source: 'resume', session_id: 'old' })
  const ui = await band($, 'terminal')
  expect(await lineOf(ui)).toBe('OIM · Single agent · oim-quick v2')
  expect(await ui.find({ key: 'yvoke-playbook' })).toBeUndefined()
  expect(w.store.get('setup:old')).toEqual(LOCKED)
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

test('the status line does not outlive its session: /resume of a plain or unlocked session clears it', async ($, on) => {
  const w = engine(on, {
    store: new Map<string, unknown>([
      ['session:plain', {}],
      ['session:unlocked', { yvoke: true }],
      ['setup:unlocked', { ...LOCKED, lockedSessionId: null }],
    ]),
  })
  await begin($)
  await yvoke($)
  await ask($, 'q')
  expect(w.status.at(-1)).toBe(LINE)

  w.sessionId = 'plain'
  await $.classic.SessionStart({ source: 'resume', session_id: 'plain' })
  expect(w.status.at(-1)).toBeUndefined()

  w.sessionId = 'session-a'
  await $.classic.SessionStart({ source: 'resume', session_id: 'session-a' })
  expect(w.status.at(-1)).toBe(LINE)

  w.sessionId = 'unlocked'
  await $.classic.SessionStart({ source: 'resume', session_id: 'unlocked' })
  expect(w.status.at(-1)).toBeUndefined()
})

test('/branch with a newer playbook on the server: the branch moves to it and says so once (D-20)', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  await ask($, 'q')
  w.server.playbooks = [playbook('oim-full', { version: 4 })]

  w.sessionId = 'session-b'
  await $.classic.SessionStart({ source: 'fork', session_id: 'session-b' })
  const ui = await band($, 'terminal')
  expect(await lineOf(ui)).toBe('OIM · Single agent · oim-full v4')
  expect(w.status.at(-1)).toBe('OIM · Single agent · oim-full v4')
  expect(w.toasts).toEqual(['oim-full moved from v3 to v4 since the setup was locked; answers from now on use v4.'])
  expect(w.store.get('setup:session-b')).toMatchObject({ version: 4, lockedSessionId: 'session-b' })
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

// A locked session whose lists fail on /resume or /branch: the server is down, still connecting or needs a
// sign-in. Until the lists load, the tool gate cannot read the playbook, so it refuses every Yvoke tool.
const DOWN = 'Yvoke Backend: yvoke is not connected.'
const SEARCH = 'mcp__plugin_yvoke_yvoke__search_corpus'
const PLAYBOOK_UNKNOWN = "Yvoke: the session's playbook could not be read, so this tool was not run."
const QUICK = 'OIM · Single agent · oim-quick v2'
const MOVED = 'oim-quick moved from v2 to v4 since the setup was locked; answers from now on use v4.'

const resumedDown = async ($: Engine, on: On) => {
  const w = engine(on, {
    sessionId: 'old',
    store: new Map<string, unknown>([
      ['session:old', { yvoke: true }],
      ['setup:old', LOCKED],
    ]),
  })
  w.server.down = true
  await begin($)
  await $.classic.SessionStart({ source: 'resume', session_id: 'old' })
  return w
}

test('/resume of a locked session whose lists fail: the locked line, the error and Retry', async ($, on) => {
  const w = await resumedDown($, on)
  for (const surface of SURFACES) {
    const ui = await band($, surface)
    expect(await lineOf(ui)).toBe(QUICK)
    expect(await errorOf(ui)).toBe(DOWN)
    expect(await ui.find({ key: 'yvoke-retry' })).toBeDefined()
    expect(await ui.find({ key: 'yvoke-area' })).toBeUndefined()
    await ui.unmount()
  }
  expect(w.status.at(-1)).toBe(QUICK)
})

test('a locked session whose lists failed: a question with the server still down is dropped, the setup unchanged', async ($, on) => {
  const w = await resumedDown($, on)
  expect(await ask($, 'q')).toEqual({ drop: DOWN })
  expect(w.entered).toEqual([])
  expect(w.store.get('setup:old')).toEqual(LOCKED)
  expect(w.toasts).toEqual([])

  const ui = await band($, 'terminal')
  expect(await lineOf(ui)).toBe(QUICK)
  expect(await errorOf(ui)).toBe(DOWN)
  expect(await ui.find({ key: 'yvoke-retry' })).toBeDefined()
  expect(await ui.find({ key: 'yvoke-playbook' })).toBeUndefined()
})

test('a locked session whose lists failed: a question with the server back loads them, and the Yvoke tools come back', async ($, on) => {
  const w = await resumedDown($, on)
  w.server.down = false
  expect(await useTool($, SEARCH)).toEqual({ deny: PLAYBOOK_UNKNOWN })

  expect(await ask($, 'q')).toEqual({ text: 'q' })
  expect(w.server.calls).toContain('list_playbooks')
  expect(await useTool($, SEARCH)).toEqual({ result: `ran ${SEARCH}` })
  expect(w.store.get('setup:old')).toEqual(LOCKED)
  expect(w.status.at(-1)).toBe(QUICK)
  expect(w.toasts).toEqual([])
  const ui = await band($, 'terminal')
  expect(await lineOf(ui)).toBe(QUICK)
  expect(await ui.find({ key: 'yvoke-retry' })).toBeUndefined()
  await ui.unmount()

  // Loaded now: the next question takes the fast path again.
  w.server.calls = []
  expect(await ask($, 'q2')).toEqual({ text: 'q2' })
  expect(w.server.calls).toEqual([])
})

test('a late load that finds a newer playbook: the setup moves to it before the question goes down (D-20)', async ($, on) => {
  const w = await resumedDown($, on)
  w.server.down = false
  w.server.playbooks = [playbook('oim-quick', { version: 4 })]

  expect(await ask($, 'q')).toEqual({ text: 'q' })
  // src/system-prompt.ts fetched the instructions with the moved setup already saved.
  expect(w.savedAtFetch).toEqual([{ ...LOCKED, version: 4 }])
  expect(w.savedAtSubmit).toEqual([{ ...LOCKED, version: 4 }])
  expect(w.store.get('setup:old')).toEqual({ ...LOCKED, version: 4 })
  expect(w.toasts).toEqual([MOVED])
  expect(w.status.at(-1)).toBe('OIM · Single agent · oim-quick v4')
  const ui = await band($, 'terminal')
  expect(await lineOf(ui)).toBe('OIM · Single agent · oim-quick v4')
  await ui.unmount()

  await ask($, 'q2')
  expect(w.toasts).toEqual([MOVED])
})

test('a late load, then a save on the server before the instructions load: the setup moves to the version that loaded', async ($, on) => {
  const w = await resumedDown($, on)
  w.server.down = false
  w.server.savedAfterLists = [playbook('oim-quick', { version: 4 }), playbook('oim-full', { version: 3 })]

  expect(await ask($, 'q')).toEqual({ text: 'q' })

  expect(w.toasts).toEqual(['oim-quick is now v4 on the server, so this session uses v4 (the band showed v2).'])
  expect(w.store.get('setup:old')).toEqual({ ...LOCKED, version: 4 })
  expect(w.status.at(-1)).toBe('OIM · Single agent · oim-quick v4')
  const ui = await band($, 'terminal')
  expect(await lineOf(ui)).toBe('OIM · Single agent · oim-quick v4')
  await ui.unmount()

  w.server.calls = []
  expect(await ask($, 'q2')).toEqual({ text: 'q2' })
  expect(w.server.calls).toEqual([])
  expect(w.toasts).toHaveLength(1)
})

test('Retry in the locked band loads the lists and moves the setup the same way (D-20)', async ($, on) => {
  const w = await resumedDown($, on)
  w.server.playbooks = [playbook('oim-quick', { version: 4 })]
  for (const surface of SURFACES) {
    // Each surface from the same failed /resume.
    w.store.set('setup:old', LOCKED)
    w.toasts = []
    w.server.down = true
    await $.classic.SessionStart({ source: 'resume', session_id: 'old' })
    w.server.down = false

    const ui = await band($, surface)
    await ui.press({ key: 'yvoke-retry' })
    expect(await lineOf(ui)).toBe('OIM · Single agent · oim-quick v4')
    expect(await errorOf(ui)).toBeUndefined()
    expect(await ui.find({ key: 'yvoke-retry' })).toBeUndefined()
    expect(await ui.find({ key: 'yvoke-playbook' })).toBeUndefined()
    await ui.unmount()
    expect(w.store.get('setup:old')).toEqual({ ...LOCKED, version: 4 })
    expect(w.toasts).toEqual([MOVED])
    expect(w.status.at(-1)).toBe('OIM · Single agent · oim-quick v4')
    expect(await useTool($, SEARCH)).toEqual({ result: `ran ${SEARCH}` })
  }
})

test('/branch of a locked session whose lists fail: the same as /resume', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  await ask($, 'q')
  w.server.down = true
  w.sessionId = 'session-b'
  await $.classic.SessionStart({ source: 'fork', session_id: 'session-b' })
  const branched = { area: 'OIM', mode: 'single', playbook: 'oim-full', version: 3, profile: null, lockedSessionId: 'session-b' }

  for (const surface of SURFACES) {
    const ui = await band($, surface)
    expect(await lineOf(ui)).toBe(LINE)
    expect(await errorOf(ui)).toBe(DOWN)
    expect(await ui.find({ key: 'yvoke-retry' })).toBeDefined()
    await ui.unmount()
  }
  expect(await ask($, 'q2')).toEqual({ drop: DOWN })
  expect(w.store.get('setup:session-b')).toEqual(branched)

  w.server.down = false
  w.server.playbooks = [playbook('oim-full', { version: 4 })]
  expect(await useTool($, SEARCH)).toEqual({ deny: PLAYBOOK_UNKNOWN })
  expect(await ask($, 'q3')).toEqual({ text: 'q3' })
  expect(await useTool($, SEARCH)).toEqual({ result: `ran ${SEARCH}` })
  expect(w.store.get('setup:session-b')).toEqual({ ...branched, version: 4 })
  expect(w.toasts).toEqual(['oim-full moved from v3 to v4 since the setup was locked; answers from now on use v4.'])
  expect(w.status.at(-1)).toBe('OIM · Single agent · oim-full v4')
})

test('a locked session with its lists loaded: a question asks the server for no list (the fast path)', async ($, on) => {
  const w = engine(on, {
    sessionId: 'old',
    store: new Map<string, unknown>([
      ['session:old', { yvoke: true }],
      ['setup:old', LOCKED],
    ]),
  })
  await begin($)
  await $.classic.SessionStart({ source: 'resume', session_id: 'old' })
  w.server.calls = []

  // Only src/system-prompt.ts's first load of the instructions.
  expect(await ask($, 'q')).toEqual({ text: 'q' })
  expect(w.server.calls.sort()).toEqual(['get_playbook', 'get_system_prompt', 'list_areas'])
  w.server.calls = []
  expect(await ask($, 'q2')).toEqual({ text: 'q2' })
  expect(w.server.calls).toEqual([])
})

test('a failing hook drops the question (fails closed)', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  w.failId = true
  expect(await ask($, 'q')).toMatchObject({ drop: expect.stringContaining('Yvoke Backend:') })
  expect(w.entered).toEqual([])
})

test('a question that entered is never reported as dropped, even when saving the lock fails', async ($, on) => {
  const w = engine(on, { failLockedSave: true })
  await begin($)
  await yvoke($)
  expect(await ask($, 'q')).toEqual({ text: 'q' })
  expect(w.entered).toEqual(['q'])
  expect(w.store.get('setup:session-a')).toMatchObject({ lockedSessionId: null })
  const ui = await band($, 'terminal')
  expect(await lineOf(ui)).toBe(LINE)
})

test('prototypes show only when the setting is on', { options: { showPrototypes: true } }, async ($, on) => {
  const w = engine(on)
  w.server.areas = [area(), area({ name: 'LAB', title: 'Lab', prototype: true })]
  await begin($)
  await yvoke($)
  const ui = await band($, 'terminal')
  expect(await optionsOf(ui, 'yvoke-area')).toEqual(['OIM', 'Lab'])
})
