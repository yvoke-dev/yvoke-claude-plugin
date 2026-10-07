import { test, expect, mock } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { McpToolResult, On, PromptComposeSection } from 'claude-code'

// P1-07: the hooks, run through the engine with the whole mod loaded. The test stands for the engine
// beneath the plugin (session, store, clock, the Yvoke server, the prompt and the system prompt), and the
// Yvoke session flag is set the way the user sets it: by typing /yvoke.

const DROP_FAILED = 'Yvoke Backend: the instructions could not be loaded, so the question was not sent.'
const CORE: PromptComposeSection[] = [
  { id: 'intro', text: 'You are Claude Code.', scope: 'shared' },
  { id: 'env', text: 'cwd: /work', scope: 'session' },
]
const AREAS = JSON.stringify([{ name: 'OIM', defaultPlaybook: 'oim-full' }])
const text = (body: string, isError = false): McpToolResult => ({ content: [{ type: 'text', text: body }], isError })

type Server = { calls: string[]; down: boolean; hang: boolean; playbook: string }
type World = { server: Server; sessionId: string; failId: boolean; entered: string[]; clock: ReturnType<typeof mock.clock> }

function engine(on: On): World {
  const w: World = {
    server: { calls: [], down: false, hang: false, playbook: 'PLAYBOOK' },
    sessionId: 'session-a',
    failId: false,
    entered: [],
    clock: mock.clock(on),
  }
  mock.store(on)
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('session.turns', () => ({ value: w.entered.length }))
  on('session.id', () => {
    if (w.failId) throw new Error('no id')
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
    if (e.tool === 'get_system_prompt') return { value: text('BASE') }
    if (e.tool === 'get_playbook') {
      const name = String(e.args.name)
      return { value: text(JSON.stringify({ name, text: w.server.playbook })) }
    }
    return { value: text(`ERROR: no tool ${e.tool}`) }
  })
  on('prompt.submit', (_$, e) => {
    w.entered.push(e.text)
    return { text: e.text }
  })
  on('prompt.compose', () => ({ sections: CORE }))
  on('classic.SessionStart', () => ({}))
  return w
}

const begin = async ($: Engine) => {
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
}
const yvoke = ($: Engine) =>
  $.command.run({
    command: 'yvoke',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 80 },
  })
const ask = ($: Engine, question: string) => $.prompt.submit({ text: question, wait: false, origin: { kind: 'composer' } })
const RENDER = { model: 'claude-x', promptModel: 'claude-x', surfaces: ['terminal'], tools: [], outputStyle: null, traits: [] } as const
const compose = async ($: Engine) => (await $.prompt.compose(RENDER)).sections

const OURS: PromptComposeSection = { id: 'yvoke:instructions', text: 'BASE\n\nPLAYBOOK', scope: 'session' }

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
  await yvoke($)

  expect(await compose($)).toEqual(CORE)
  expect(await ask($, 'how do I reset a password?')).toEqual({ text: 'how do I reset a password?' })

  expect(w.server.calls).toEqual(['list_areas', 'get_system_prompt', 'get_playbook'])
  expect(w.entered).toEqual(['how do I reset a password?'])
  expect(await compose($)).toEqual([...CORE, OURS])
})

test('later questions use what was loaded and do not call the server again', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  await ask($, 'one')
  w.server.playbook = 'CHANGED'

  await ask($, 'two')

  expect(w.server.calls).toHaveLength(3)
  expect(await compose($)).toEqual([...CORE, OURS])
})

test('server down: the question is dropped with the server message, and the next one tries again', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
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
  await yvoke($)
  w.server.playbook = ''

  expect(await ask($, 'one')).toEqual({ drop: "Yvoke Backend: playbook 'oim-full' has no text." })
  expect(w.entered).toEqual([])
})

test('a server that does not answer: the question is dropped once the server client gives up', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  w.server.hang = true

  const answer = ask($, 'one')
  await w.clock.advance(8_000)

  expect(await answer).toEqual({ drop: 'Yvoke Backend: the server did not answer list_areas within 8 seconds.' })
  expect(w.entered).toEqual([])
})

test('a new session id (/clear, /resume, /branch) loads its own instructions on its first question', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  await ask($, 'one')

  w.sessionId = 'session-b'
  await $.classic.SessionStart({ source: 'fork', session_id: 'session-b' })
  expect(await compose($)).toEqual(CORE)

  w.server.playbook = 'NEWER'
  await ask($, 'two')
  expect(w.server.calls).toHaveLength(6)
  expect(await compose($)).toEqual([...CORE, { ...OURS, text: 'BASE\n\nNEWER' }])
})

test('a throw in prompt.submit drops the question (fails closed)', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  w.failId = true

  expect(await ask($, 'one')).toEqual({ drop: DROP_FAILED })
  expect(w.entered).toEqual([])
})

// The kit charges a hook only for its own code, and it cuts a wait held beneath the plugin (the clock, the
// server) at ten seconds, so a plugin hook that overruns its own budget cannot be staged here; the .catch
// that answers it is the one the throw above reaches. What can be shown: a server that hangs past every
// budget never lets the question through.
test('a server that hangs past every budget never lets the question through', { timeoutMs: 30_000 }, async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  w.server.hang = true

  // The clock never moves, so nothing answers until the kit gives up on the waits beneath the plugin.
  expect(await ask($, 'one')).toEqual({ drop: expect.stringMatching(/^Yvoke Backend: /) })
  expect(w.entered).toEqual([])
})

test('a throw in prompt.compose serves the refusal section in place of the prompt (fails closed)', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  await ask($, 'one')
  w.failId = true

  const sections = await compose($)

  expect(sections).toHaveLength(1)
  expect(sections[0]).toMatchObject({ id: 'yvoke:instructions', scope: 'session' })
  expect(sections[0]?.text).toContain('could not load its instructions')
})
