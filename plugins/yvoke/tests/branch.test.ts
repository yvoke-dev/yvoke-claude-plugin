import { test, expect } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { McpToolResult, On, RenderElement } from 'claude-code'

// P1-08 machine check 3 (2026-10-21, Claude Code 2.1.306): /branch starts the new session with empty
// `$.state`, and its classic.SessionStart names no parent session. Claude Code ends the parent first
// (`session.end`, with its id). The test kit keeps `$.state` across a fork, so this engine empties it
// there, as Claude Code does, until the plugin writes a key again.

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

const ALREADY = 'This is already a Yvoke session.'
const STARTED = "Yvoke session started. Ask your question; Yvoke's setup and rules apply until /clear."
const LINE = 'OIM · Single agent · oim-full v3'

const text = (body: string): McpToolResult => ({ content: [{ type: 'text', text: body }], isError: false })
const AREAS = [
  {
    name: 'OIM',
    title: 'OIM',
    prototype: false,
    defaultPlaybook: 'oim-full',
    defaultProfile: 'OIM',
    playbooks: ['oim-full', 'oim-quick'],
    profiles: ['OIM'],
  },
]
const PLAYBOOKS = [
  { name: 'oim-quick', area: 'OIM', prototype: false, version: 2 },
  { name: 'oim-full', area: 'OIM', prototype: false, version: 3 },
]

type World = { sessionId: string; turns: number; store: Map<string, unknown>; emptied: Set<string> }

function engine(on: On): World {
  const w: World = { sessionId: 'session-a', turns: 0, store: new Map(), emptied: new Set() }
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('session.end', (_$, e) => ({ sessionId: e.sessionId }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('session.turns', () => ({ value: w.turns }))
  on('session.id', () => ({ value: w.sessionId }))
  on('store.get', (_$, e) => ({ value: w.store.get(e.key) }))
  on('store.set', (_$, e) => {
    w.store.set(e.key, e.value)
    return { value: undefined }
  })
  // Claude Code's fork: a key reads as never written until the plugin writes it in the new session.
  on('state.get', (_$, e, next) => (w.emptied.has(e.key) ? { value: { value: undefined, version: 0 } } : next(e)))
  on('state.set', (_$, e, next) => {
    w.emptied.delete(e.key)
    return next(e)
  })
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:yvoke:yvoke' } }))
  on('mcp.call', (_$, e) => {
    if (e.tool === 'list_areas') return { value: text(JSON.stringify(AREAS)) }
    if (e.tool === 'list_playbooks') return { value: text(JSON.stringify(PLAYBOOKS)) }
    // P1-07's calls, when its module is loaded too: answer them so a question can enter.
    if (e.tool === 'get_playbook') return { value: text(JSON.stringify({ name: e.args.name, text: 'PLAYBOOK' })) }
    if (e.tool === 'get_system_prompt') return { value: text('BASE') }
    return { value: text(`ERROR: no tool ${e.tool}`) }
  })
  on('prompt.submit', (_$, e) => {
    w.turns++
    return { text: e.text }
  })
  on('ui.status', () => ({ value: undefined }))
  on('ui.toast', () => ({ value: undefined }))
  on('ui.render', ($$, e) => {
    const { Box } = $$.ui.resolve(e)
    return h(Box, {}) as RenderElement
  })
  on('classic.SessionStart', () => ({}))
  return w
}

const begin = ($: Engine) => $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
const yvoke = ($: Engine) =>
  $.command.run({ command: 'yvoke', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 80 } })
const ask = ($: Engine, question: string) => $.prompt.submit({ text: question, wait: false, origin: { kind: 'composer' } })
const lineOf = async ($: Engine) => {
  const ui = await $.ui.mount({ plugin: 'yvoke', surface: 'terminal', ...BAND })
  const line = (await ui.find({ type: 'Text', text: / · (Single agent|Multi-agent) · / }))?.text
  await ui.unmount()
  return line
}

// What Claude Code does on /branch: end the parent, empty the state, start the fork under a new id.
async function branch($: Engine, w: World, to: string) {
  await $.session.end({ reason: 'resume', sessionId: w.sessionId, resume: { id: w.sessionId } } as never)
  for (const key of ['yvokeSession', 'setup', 'catalogue']) w.emptied.add(key)
  w.sessionId = to
  await $.classic.SessionStart({ source: 'fork', session_id: to })
}

test('/branch of a locked Yvoke session, with the state emptied: the branch keeps the session and the setup', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  await ask($, 'q')
  expect(await lineOf($)).toBe(LINE)

  await branch($, w, 'session-b')

  expect(await lineOf($)).toBe(LINE)
  expect(w.store.get('session:session-b')).toEqual({ yvoke: true })
  expect(w.store.get('setup:session-b')).toMatchObject({ playbook: 'oim-full', version: 3, lockedSessionId: 'session-b' })
  expect(await yvoke($)).toMatchObject({ text: ALREADY })
})

test('/branch of a plain session after a Yvoke one ended: the branch stays plain', async ($, on) => {
  const w = engine(on)
  await begin($)
  await yvoke($)
  await ask($, 'q')
  await branch($, w, 'session-b')

  // A plain session in the same process: /clear, then a question, then /branch.
  await $.session.end({ reason: 'clear', sessionId: 'session-b', resume: { id: 'session-b' } } as never)
  w.sessionId = 'session-c'
  w.turns = 0
  await $.classic.SessionStart({ source: 'clear', session_id: 'session-c' })
  await ask($, 'fix the build')
  await branch($, w, 'session-d')

  expect(await lineOf($)).toBeUndefined()
  expect(w.store.get('session:session-d')).toBeUndefined()
  w.turns = 0
  expect(await yvoke($)).toMatchObject({ text: STARTED })
})
