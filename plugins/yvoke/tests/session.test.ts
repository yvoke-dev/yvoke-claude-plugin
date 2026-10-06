import { test, expect } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

// P1-10: a session becomes a Yvoke session only when the user types /yvoke before the first question
// (D-13). A test's `$` cannot read `$.state`, so the flag is observed the way the user sees it: a second
// /yvoke answers "already" only while the session is a Yvoke session.

const STARTED = "Yvoke session started. Ask your question; Yvoke's setup and rules apply until /clear."
const TOO_LATE = '/yvoke works only before the first question. Type /clear, then /yvoke.'
const ALREADY = 'This is already a Yvoke session.'
const FAILED = 'Yvoke: /yvoke did not work. Try it again.'

type Session = {
  id: string
  turns: number
  store: Map<string, unknown>
  registered: string[]
  writes: string[]
  failStore: boolean
  failStoreRead: boolean
}

// The engine beneath the plugin: a session id, a turn count, an in-memory store, and the command list.
function engine(on: On, start: Partial<Session> = {}): Session {
  const s: Session = {
    id: 'session-a',
    turns: 0,
    store: new Map(),
    registered: [],
    writes: [],
    failStore: false,
    failStoreRead: false,
    ...start,
  }
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', (_$, e) => {
    s.registered.push(e.name)
    return { value: { command: e.name } }
  })
  on('session.turns', () => ({ value: s.turns }))
  on('session.id', () => ({ value: s.id }))
  on('store.get', (_$, e) => {
    if (s.failStoreRead) throw new Error('store unreadable')
    return { value: s.store.get(e.key) }
  })
  on('store.set', (_$, e) => {
    if (s.failStore) throw new Error('disk full')
    s.writes.push(e.key)
    s.store.set(e.key, e.value)
    return { value: undefined }
  })
  on('classic.SessionStart', () => ({}))
  return s
}

// The user typing /yvoke at the prompt.
const typeYvoke = ($: Engine) =>
  $.command.run({
    command: 'yvoke',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 80 },
  })

const begin = ($: Engine) =>
  $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

test('a session without /yvoke: only the command is added, and events reach the engine unchanged', async ($, on) => {
  const s = engine(on)
  const seen: unknown[] = []
  on('tool.call', (_$, e) => {
    seen.push(e)
    return { result: 'from the engine' }
  })

  await begin($)
  const answer = await $.tool.call({ tool: 'Bash', command: 'ls' })

  expect(s.registered).toEqual(['yvoke'])
  expect(answer).toEqual({ result: 'from the engine' })
  expect(seen).toEqual([expect.objectContaining({ tool: 'Bash', command: 'ls' })])
  expect(s.writes).toEqual([])
})

test('a session without /yvoke: a prompt reaches the engine unchanged', async ($, on) => {
  engine(on)
  const seen: unknown[] = []
  on('prompt.submit', (_$, e) => {
    seen.push(e)
    return { text: e.text }
  })

  await begin($)
  await $.prompt.submit({ text: 'fix the build', wait: false, origin: { kind: 'composer' } })

  expect(seen).toEqual([expect.objectContaining({ text: 'fix the build' })])
  expect(seen[0]).not.toHaveProperty('context')
})

test('/yvoke before the first question starts a Yvoke session and saves it under the session id', async ($, on) => {
  const s = engine(on)
  await begin($)

  expect(await typeYvoke($)).toMatchObject({ text: STARTED })
  expect(s.store.get('session:session-a')).toEqual({ yvoke: true })
  expect(await typeYvoke($)).toMatchObject({ text: ALREADY })
})

test('/yvoke after the first question says to /clear first and changes nothing', async ($, on) => {
  const s = engine(on, { turns: 1 })
  await begin($)

  expect(await typeYvoke($)).toMatchObject({ text: TOO_LATE })
  expect(s.writes).toEqual([])
  expect(await typeYvoke($)).toMatchObject({ text: TOO_LATE })
})

test('/yvoke twice: the second answers "already" and writes nothing', async ($, on) => {
  const s = engine(on)
  await begin($)

  await typeYvoke($)
  s.turns = 2
  expect(await typeYvoke($)).toMatchObject({ text: ALREADY })
  expect(s.writes).toEqual(['session:session-a'])
})

test('/yvoke adds nothing for the model to read', async ($, on) => {
  engine(on)
  await begin($)

  const answer = await typeYvoke($)
  expect(answer.context).toBeUndefined()
})

test('/clear ends the Yvoke session', async ($, on) => {
  const s = engine(on)
  await begin($)
  await typeYvoke($)

  s.id = 'session-b'
  await $.classic.SessionStart({ source: 'clear', session_id: 'session-b' })

  expect(await typeYvoke($)).toMatchObject({ text: STARTED })
  expect(s.store.get('session:session-b')).toEqual({ yvoke: true })
})

test('/resume of a Yvoke session restores it', async ($, on) => {
  const s = engine(on, { store: new Map([['session:old', { yvoke: true }]]) })
  await begin($)

  s.id = 'old'
  s.turns = 3
  await $.classic.SessionStart({ source: 'resume', session_id: 'old' })

  expect(await typeYvoke($)).toMatchObject({ text: ALREADY })
})

test('/resume of a plain session from inside a Yvoke session turns Yvoke off', async ($, on) => {
  const s = engine(on)
  await begin($)
  await typeYvoke($)

  s.id = 'plain'
  s.turns = 3
  await $.classic.SessionStart({ source: 'resume', session_id: 'plain' })

  expect(await typeYvoke($)).toMatchObject({ text: TOO_LATE })
})

test('/branch keeps the Yvoke session and saves it under the new id', async ($, on) => {
  const s = engine(on)
  await begin($)
  await typeYvoke($)

  s.id = 'branch'
  s.turns = 3
  await $.classic.SessionStart({ source: 'fork', session_id: 'branch' })

  expect(s.store.get('session:branch')).toEqual({ yvoke: true })
  expect(await typeYvoke($)).toMatchObject({ text: ALREADY })
})

test('/branch of a plain session saves nothing', async ($, on) => {
  const s = engine(on)
  await begin($)

  s.id = 'branch'
  await $.classic.SessionStart({ source: 'fork', session_id: 'branch' })

  expect(s.writes).toEqual([])
})

test('a store write that fails leaves the session plain and says so', async ($, on) => {
  const s = engine(on, { failStore: true })
  await begin($)

  expect(await typeYvoke($)).toMatchObject({ text: FAILED })

  s.failStore = false
  expect(await typeYvoke($)).toMatchObject({ text: STARTED })
})

test('/resume when the saved entry cannot be read leaves the session plain', async ($, on) => {
  const s = engine(on)
  await begin($)
  await typeYvoke($)

  s.id = 'unknown'
  s.turns = 3
  s.failStoreRead = true
  await $.classic.SessionStart({ source: 'resume', session_id: 'unknown' })

  expect(await typeYvoke($)).toMatchObject({ text: TOO_LATE })
})
