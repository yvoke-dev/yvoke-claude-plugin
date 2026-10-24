import { test, expect, mock } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { McpToolResult, On, PromptComposeSection } from 'claude-code'

// system-prompt.md: the hooks, run through the engine with the whole mod loaded. The test stands for the
// engine beneath the plugin (session, store, clock, the Yvoke server, the prompt and the system prompt), and
// the Yvoke session flag is set the way the user sets it: by typing /yvoke.

const DROP_FAILED = 'Yvoke Backend: the instructions could not be loaded, so the question was not sent.'
const CORE: PromptComposeSection[] = [
  { id: 'intro', text: 'You are Claude Code.', scope: 'shared' },
  { id: 'env', text: 'cwd: /work', scope: 'session' },
]
const AREAS = JSON.stringify([
  { name: 'OIM', title: 'OIM', prototype: false, defaultPlaybook: 'oim-full', playbooks: ['oim-full'], profiles: [], defaultProfile: null },
])
// The setup band (setup.md) loads the playbook list on /yvoke; it picks the area's default, oim-full.
const PLAYBOOKS = JSON.stringify([{ name: 'oim-full', area: 'OIM', prototype: false, version: 1 }])
const text = (body: string, isError = false): McpToolResult => ({ content: [{ type: 'text', text: body }], isError })

// playbook, version: the text and the version get_playbook gives; PLAYBOOKS lists oim-full at v1.
type Server = { calls: string[]; down: boolean; hang: boolean; playbook: string; version: unknown }
// idCallsLeft: how many more session.id calls answer before one throws; null never throws.
// dropBeneath: a hook beneath the plugin that drops the question after the instructions loaded.
type World = {
  server: Server
  sessionId: string
  idCallsLeft: number | null
  entered: string[]
  dropBeneath: string | null
  clock: ReturnType<typeof mock.clock>
}

function engine(on: On): World {
  const w: World = {
    server: { calls: [], down: false, hang: false, playbook: 'PLAYBOOK', version: 1 },
    sessionId: 'session-a',
    idCallsLeft: null,
    entered: [],
    dropBeneath: null,
    clock: mock.clock(on),
  }
  mock.store(on)
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('session.turns', () => ({ value: w.entered.length }))
  on('session.id', () => {
    if (w.idCallsLeft !== null && w.idCallsLeft-- <= 0) throw new Error('no id')
    return { value: w.sessionId }
  })
  on('mcp.connect', () => ({
    value: w.server.down
      ? { isConnected: false, reason: 'failed', message: 'yvoke is not connected.' }
      : { isConnected: true, server: 'plugin:yvoke:yvoke' },
  }))
  on('mcp.call', async (_$, e) => {
    w.server.calls.push(e.tool)
    if (w.server.hang) await new Promise(() => {})
    if (e.tool === 'list_areas') return { value: text(AREAS) }
    if (e.tool === 'list_playbooks') return { value: text(PLAYBOOKS) }
    if (e.tool === 'get_system_prompt') return { value: text('BASE') }
    if (e.tool === 'get_playbook') {
      const name = String(e.args.name)
      return { value: text(JSON.stringify({ name, text: w.server.playbook, version: w.server.version })) }
    }
    return { value: text(`ERROR: no tool ${e.tool}`) }
  })
  on('prompt.submit', (_$, e) => {
    if (w.dropBeneath !== null) return { drop: w.dropBeneath }
    w.entered.push(e.text)
    return { text: e.text }
  })
  on('prompt.compose', () => ({ sections: CORE }))
  on('classic.SessionStart', () => ({}))
  on('ui.status', () => ({ value: undefined }))
  on('ui.toast', () => ({ value: undefined }))
  return w
}

const begin = async ($: Engine) => {
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
}
// /yvoke also loads the setup band's lists (list_areas, list_playbooks); the tests count only later calls.
const yvoke = async ($: Engine, w: World) => {
  await $.command.run({
    command: 'yvoke',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 80 },
  })
  w.server.calls = []
}
const ask = ($: Engine, question: string) => $.prompt.submit({ text: question, wait: false, origin: { kind: 'composer' } })
const RENDER = { model: 'claude-x', promptModel: 'claude-x', surfaces: ['terminal'], tools: [], outputStyle: null, traits: [] } as const
const compose = async ($: Engine) => (await $.prompt.compose(RENDER)).sections

const OURS: PromptComposeSection = { id: 'yvoke:instructions', text: 'BASE\n\n---\n\nPLAYBOOK', scope: 'session' }
const LOAD = ['list_areas', 'get_playbook', 'get_system_prompt']
// The refusal that replaces the whole prompt when a turn would otherwise run without the instructions.
const MISSING_SECTION: PromptComposeSection = {
  id: 'yvoke:instructions',
  text:
    'The Yvoke instructions for this session could not be loaded. Do not answer the question and do not call any ' +
    'tool. Reply only: "Yvoke could not load its instructions. Please send your question again."',
  scope: 'session',
}

// What the plugin writes to `yvoke.instructions`, seen on its way to the kit's store.
const keptInstructions = (on: On) => {
  const kept: unknown[] = []
  on('state.set', { plugin: 'yvoke', key: 'instructions' }, (_$, e, next) => {
    kept.push(e.value)
    return next(e)
  })
  return kept
}
// The setup as a test sets it (setup.md): a hook here answers the plugin's reads of `yvoke.setup`.
const setupIs = (on: On, start: Record<string, unknown>) => {
  const s = { setup: start }
  on('state.get', { plugin: 'yvoke', key: 'setup' }, () => ({ value: { value: s.setup as never, version: 1 } }))
  return s
}
const FULL = { area: 'OIM', mode: 'single', playbook: 'oim-full', version: 1, profile: null, lockedSessionId: null }
const LOCKED = { ...FULL, lockedSessionId: 'session-a' }
// What `yvoke.instructions` holds, as a test sets it: a hook here answers the plugin's reads of the key.
const instructionsAre = (on: On, kept: unknown) =>
  on('state.get', { plugin: 'yvoke', key: 'instructions' }, () => ({ value: { value: kept as never, version: 1 } }))
const LOADED_FOR = { area: 'OIM', mode: 'single', playbook: 'oim-full', version: 1 }

test('a plain session: questions and the system prompt pass through, and the server is never called', async ($, on) => {
  const w = engine(on)
  await begin($)

  expect(await ask($, 'fix the build')).toEqual({ text: 'fix the build' })
  expect(await compose($)).toEqual(CORE)
  expect(w.server.calls).toEqual([])
})

test('the first question loads the instructions, then the question is sent with them in the prompt', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($, w)

  expect(await compose($)).toEqual(CORE)
  expect(await ask($, 'how do I reset a password?')).toEqual({ text: 'how do I reset a password?' })

  expect(w.server.calls).toEqual(['list_areas', 'get_playbook', 'get_system_prompt'])
  expect(w.entered).toEqual(['how do I reset a password?'])
  expect(await compose($)).toEqual([...CORE, OURS])
})

test("the setup band's choice in yvoke.setup decides what loads: multi-agent asks for no playbook", async ($, on) => {
  const w = engine(on)
  // The test's `$` cannot write `$.state`, but a hook here answers the plugin's read of the key.
  on('state.get', { plugin: 'yvoke', key: 'setup' }, () => ({
    value: { value: { area: 'OIM', mode: 'oim-team', playbook: null }, version: 1 },
  }))
  await begin($)
  await yvoke($, w)

  expect(await ask($, 'one')).toEqual({ text: 'one' })

  expect(w.server.calls).toEqual(['list_areas', 'get_system_prompt'])
  expect(await compose($)).toEqual([...CORE, { ...OURS, text: 'BASE' }])
})

test('later questions use what was loaded and do not call the server again', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($, w)
  await ask($, 'one')
  w.server.playbook = 'CHANGED'

  await ask($, 'two')

  expect(w.server.calls).toHaveLength(3)
  expect(await compose($)).toEqual([...CORE, OURS])
})

test('what loaded is kept with the text, and a later question on the same setup reuses it', async ($, on) => {
  const w = engine(on)
  const kept = keptInstructions(on)
  await begin($)
  await yvoke($, w)

  await ask($, 'one')
  await ask($, 'two')

  expect(kept).toEqual([
    {
      sessionId: 'session-a',
      text: 'BASE\n\n---\n\nPLAYBOOK',
      loadedFor: { area: 'OIM', mode: 'single', playbook: 'oim-full', version: 1 },
      tools: null,
    },
  ])
  expect(w.server.calls).toEqual(LOAD)
  expect(await compose($)).toEqual([...CORE, OURS])
})

test('a question dropped beneath after the load, then another playbook picked: the next question loads that one', async ($, on) => {
  const w = engine(on)
  const s = setupIs(on, FULL)
  await begin($)
  await yvoke($, w)
  w.dropBeneath = 'Yvoke: dropped beneath.'
  expect(await ask($, 'one')).toEqual({ drop: 'Yvoke: dropped beneath.' })
  expect(w.server.calls).toEqual(LOAD)

  // The setup is still open after a drop.
  s.setup = { ...FULL, playbook: 'oim-quick' }
  w.server.playbook = 'QUICK'
  w.dropBeneath = null
  expect(await ask($, 'two')).toEqual({ text: 'two' })

  expect(w.server.calls).toEqual([...LOAD, ...LOAD])
  expect(await compose($)).toEqual([...CORE, { ...OURS, text: 'BASE\n\n---\n\nQUICK' }])
})

test('a setup whose version is not the one that loaded: the next question loads again', async ($, on) => {
  const w = engine(on)
  const s = setupIs(on, FULL)
  await begin($)
  await yvoke($, w)
  await ask($, 'one')

  s.setup = { ...FULL, version: 2 }
  w.server.version = 2
  w.server.playbook = 'NEWER'
  await ask($, 'two')
  await ask($, 'three')

  expect(w.server.calls).toEqual([...LOAD, ...LOAD])
  expect(await compose($)).toEqual([...CORE, { ...OURS, text: 'BASE\n\n---\n\nNEWER' }])
})

test('a get_playbook answer with no version: later questions reuse what loaded', async ($, on) => {
  const w = engine(on)
  w.server.version = undefined
  await begin($)
  await yvoke($, w)

  await ask($, 'one')
  await ask($, 'two')

  expect(w.server.calls).toEqual(LOAD)
  expect(await compose($)).toEqual([...CORE, OURS])
})

test('an entry kept without loadedFor (from before a hot reload) is loaded again', async ($, on) => {
  const w = engine(on)
  // What an older build left in `yvoke.instructions` for this session, until the plugin writes it again.
  const kept = keptInstructions(on)
  on('state.get', { plugin: 'yvoke', key: 'instructions' }, (_$, e, next) =>
    kept.length === 0 ? { value: { value: { sessionId: 'session-a', text: 'OLD' } as never, version: 1 } } : next(e),
  )
  await begin($)
  await yvoke($, w)

  expect(await ask($, 'one')).toEqual({ text: 'one' })

  expect(w.server.calls).toEqual(LOAD)
  expect(await compose($)).toEqual([...CORE, OURS])
})

test('server down: the question is dropped with the server message, and the next one tries again', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($, w)
  w.server.down = true

  expect(await ask($, 'one')).toEqual({ drop: 'Yvoke Backend: yvoke is not connected.' })
  expect(w.entered).toEqual([])
  expect(await compose($)).toEqual(CORE)

  w.server.down = false
  expect(await ask($, 'again')).toEqual({ text: 'again' })
  expect(await compose($)).toEqual([...CORE, OURS])
})

test('a playbook with no text drops the question', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($, w)
  w.server.playbook = ''

  expect(await ask($, 'one')).toEqual({ drop: "Yvoke Backend: playbook 'oim-full' has no text." })
  expect(w.entered).toEqual([])
})

test('a server that does not answer: the question is dropped once the server client gives up', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($, w)
  w.server.hang = true

  const answer = ask($, 'one')
  await w.clock.advance(8_000)

  expect(await answer).toEqual({ drop: 'Yvoke Backend: the server did not answer list_areas within 8 seconds.' })
  expect(w.entered).toEqual([])
})

test('a new session id (/clear, /resume, /branch) loads its own instructions on its first question', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($, w)
  await ask($, 'one')

  w.sessionId = 'session-b'
  await $.classic.SessionStart({ source: 'fork', session_id: 'session-b' })
  // The setup is locked in session-b now, and what loaded is session-a's: the refusal, not session-a's text.
  expect(await compose($)).toEqual([MISSING_SECTION])
  w.server.calls = [] // the setup band reloads its lists for the new session

  w.server.playbook = 'NEWER'
  await ask($, 'two')
  expect(w.server.calls).toEqual(['list_areas', 'get_playbook', 'get_system_prompt'])
  expect(await compose($)).toEqual([...CORE, { ...OURS, text: 'BASE\n\n---\n\nNEWER' }])
})

test('a throw in prompt.submit drops the question (fails closed)', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($, w)
  // The setup band's hook, the outer one, asks for the session id first; the system prompt hook's ask is
  // the one that throws. The drop that reaches the user is the setup band's wording, since the failure
  // passes back through its hook.
  w.idCallsLeft = 1

  expect(await ask($, 'one')).toEqual({ drop: expect.stringMatching(/^Yvoke Backend: .*so the question was not sent\.$/) })
  expect(w.entered).toEqual([])
})

// The kit charges a hook only for its own code, and it cuts a wait held beneath the plugin (the clock, the
// server) at ten seconds, so a plugin hook that overruns its own budget cannot be staged here; the .catch
// that answers it is the one the throw above reaches. What can be shown: a server that hangs past every
// budget never lets the question through.
test('a server that hangs past every budget never lets the question through', { timeoutMs: 30_000 }, async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($, w)
  w.server.hang = true

  // The clock never moves, so nothing answers until the kit gives up on the waits beneath the plugin.
  expect(await ask($, 'one')).toEqual({ drop: expect.stringMatching(/^Yvoke Backend: /) })
  expect(w.entered).toEqual([])
})

test('a throw in prompt.compose serves the refusal section in place of the prompt (fails closed)', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($, w)
  await ask($, 'one')
  w.idCallsLeft = 0

  const sections = await compose($)

  expect(sections).toHaveLength(1)
  expect(sections[0]).toMatchObject({ id: 'yvoke:instructions', scope: 'session' })
  expect(sections[0]?.text).toContain('could not load its instructions')
})

// Once the setup is locked in this session, a turn never runs on Claude Code's prompt alone: a question that
// entered without its instructions (another plugin's prompt.submit above this one answered it, say) gets the
// refusal in place of the whole prompt.
test('a setup locked in this session and no instructions: the refusal section replaces the prompt', async ($, on) => {
  const w = engine(on)
  setupIs(on, LOCKED)
  await begin($)
  await yvoke($, w)

  expect(await compose($)).toEqual([MISSING_SECTION])
  expect(w.server.calls).toEqual([])
})

test('a setup locked in this session and instructions kept for another session id: the refusal section', async ($, on) => {
  const w = engine(on)
  setupIs(on, LOCKED)
  instructionsAre(on, { sessionId: 'session-z', text: 'BASE\n\n---\n\nOTHER', loadedFor: LOADED_FOR })
  await begin($)
  await yvoke($, w)

  expect(await compose($)).toEqual([MISSING_SECTION])
})

for (const [what, kept] of [
  ['text that is not a string', { sessionId: 'session-a', text: 42, loadedFor: LOADED_FOR }],
  ['the text alone, not a record', 'BASE\n\n---\n\nPLAYBOOK'],
] as const) {
  test(`a setup locked in this session and an entry with ${what}: the refusal section`, async ($, on) => {
    const w = engine(on)
    setupIs(on, LOCKED)
    instructionsAre(on, kept)
    await begin($)
    await yvoke($, w)

    expect(await compose($)).toEqual([MISSING_SECTION])
  })
}

// Claude Code may compose prompts for its own side requests before the first question locks the setup.
for (const [what, setup] of [
  ['not locked', FULL],
  ['locked in another session', { ...FULL, lockedSessionId: 'session-old' }],
] as const) {
  test(`a setup ${what} and no instructions: Claude Code's prompt passes unchanged`, async ($, on) => {
    const w = engine(on)
    setupIs(on, setup)
    await begin($)
    await yvoke($, w)

    expect(await compose($)).toEqual(CORE)
  })
}

test('a setup locked in this session and instructions loaded for it: the prompt ends with the section', async ($, on) => {
  const w = engine(on)
  setupIs(on, LOCKED)
  await begin($)
  await yvoke($, w)

  expect(await ask($, 'one')).toEqual({ text: 'one' })

  expect(w.server.calls).toEqual(LOAD)
  expect(await compose($)).toEqual([...CORE, OURS])
})

test('loaded for another version than the locked setup shows: the prompt still ends with the section', async ($, on) => {
  const w = engine(on)
  // The server saved the playbook again since the lists loaded, so v2 loads. The hook here keeps answering
  // the setup at v1, as a prompt composed before setup.ts takes the loaded version would see it.
  setupIs(on, LOCKED)
  w.server.version = 2
  await begin($)
  await yvoke($, w)
  await ask($, 'one')

  expect(await compose($)).toEqual([...CORE, OURS])
})

test('outside a Yvoke session a locked setup changes nothing: the prompt passes unchanged', async ($, on) => {
  const w = engine(on)
  setupIs(on, LOCKED)
  await begin($)

  expect(await compose($)).toEqual(CORE)
  expect(w.server.calls).toEqual([])
})

// D-11, setup.md: a session whose setup was lost takes no question. If one enters anyway (another plugin's
// prompt.submit above answered it), the prompt is the refusal, with the instructions or without.
// The flag as a test sets it: a hook here answers the plugin's reads of `yvoke.setupLost`.
const setupLost = (on: On, start = true) => {
  const flag = { lost: start }
  on('state.get', { plugin: 'yvoke', key: 'setupLost' }, () => ({ value: { value: flag.lost as never, version: 1 } }))
  return flag
}

test('a session whose setup was lost and no instructions: the refusal section replaces the prompt', async ($, on) => {
  const w = engine(on)
  setupLost(on)
  await begin($)
  await yvoke($, w)

  expect(await compose($)).toEqual([MISSING_SECTION])
  expect(w.server.calls).toEqual([])
})

test('a session whose setup was lost, with instructions loaded for it: the refusal section, not the instructions', async ($, on) => {
  const w = engine(on)
  const flag = setupLost(on, false)
  await begin($)
  await yvoke($, w)
  await ask($, 'one')
  expect(await compose($)).toEqual([...CORE, OURS])

  flag.lost = true
  expect(await compose($)).toEqual([MISSING_SECTION])
})
