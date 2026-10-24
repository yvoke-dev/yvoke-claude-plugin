// session.md, D-13: a session becomes a Yvoke session only when the user types /yvoke before the first
// question. The flag lives in $.state (reset by /clear) and is saved per session id in $.store, so /resume
// and /branch keep it. This file is the only writer of the flag. /yvoke also deletes the entries of sessions
// beyond the newest 5,000 (D-28).
//
// Every enforcing hook in another module opens with these two lines, written in its own file (the engine
// follows $ and $.state references only within one file, so there can be no shared helper):
//
//   const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
//   if (isYvoke !== true) return next(e)
import type { On } from 'claude-code'

const FLAG = { plugin: 'yvoke', key: 'yvokeSession' } as const

export const STARTED = "Yvoke session started. Ask your question; Yvoke's setup and rules apply until /clear."
export const TOO_LATE = '/yvoke works only before the first question. Type /clear, then /yvoke.'
export const ALREADY = 'This is already a Yvoke session.'
export const FAILED = 'Yvoke: /yvoke did not work. Try it again.'

/** The `$.store` key holding one session's Yvoke entry. */
export const sessionKey = (sessionId: string) => `session:${sessionId}`

/** The `$.store` key holding one session's setup, written by src/setup.ts; here so the prune can name it. */
export const setupKey = (sessionId: string) => `setup:${sessionId}`

/**
 * The `$.store` key naming the session that ended last, `{ sessionId }`. Claude Code starts a /branch with
 * empty `$.state` and names no parent, but ends the parent just before, so the fork reads its parent here.
 */
export const ENDED = 'ended'

/** The id saved under ENDED, or undefined. */
export const endedId = (entry: unknown) =>
  typeof entry === 'object' && entry !== null && typeof (entry as { sessionId?: unknown }).sessionId === 'string'
    ? (entry as { sessionId: string }).sessionId
    : undefined

/** Whether a stored entry marks a Yvoke session. */
export const isSavedYvoke = (entry: unknown) =>
  typeof entry === 'object' && entry !== null && (entry as { yvoke?: unknown }).yvoke === true

/** How many sessions keep their `$.store` entries when /yvoke prunes the older ones (D-28). */
export const KEEP_SESSIONS = 5000

/**
 * The `session:<id>` and `setup:<id>` keys of every session but the newest `keep`, out of `$.store.keys()`.
 * A session is as old as its first key: the store lists keys in insertion order, and setting a key again
 * keeps its place. Any other key, `ended` among them, is never returned.
 */
export const staleKeys = (keys: readonly string[], keep: number): string[] => {
  const prefixes = [sessionKey(''), setupKey('')]
  const idOf = (key: string) => {
    const prefix = prefixes.find((p) => key.startsWith(p))
    return prefix === undefined ? undefined : key.slice(prefix.length)
  }
  // A Set iterates in the order its members were first added: oldest session first.
  const ids = new Set<string>()
  for (const key of keys) {
    const id = idOf(key)
    if (id !== undefined) ids.add(id)
  }
  const stale = new Set([...ids].slice(0, Math.max(0, ids.size - keep)))
  return keys.filter((key) => {
    const id = idOf(key)
    return id !== undefined && stale.has(id)
  })
}

export function registerSession(on: On) {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.command.register({
      name: 'yvoke',
      description: 'Make this session a Yvoke session (before the first question).',
    })
    return started
  })

  on('command.run', { command: 'yvoke' }, async ($) => {
    const { value: isYvoke } = await $.state.get(FLAG)
    if (isYvoke === true) return { text: ALREADY }
    if ((await $.session.turns()) > 0) return { text: TOO_LATE }
    // Store first: if it fails, the session stays plain rather than becoming Yvoke now and plain after
    // /resume. If the store write succeeds and the flag write then fails, the session is plain now and
    // Yvoke after /resume; the user sees the failure line and can type /yvoke again.
    await $.store.set(sessionKey(await $.session.id()), { yvoke: true })
    await $.state.set(FLAG, true)
    // D-28: nothing else empties the store, and a full one refuses every write.
    try {
      for (const key of staleKeys(await $.store.keys(), KEEP_SESSIONS)) await $.store.delete(key)
    } catch {
      // The session has started either way, so the user sees nothing; the next /yvoke tries again.
    }
    return { text: STARTED }
  }).catch(() => ({ text: FAILED }))

  on('classic.SessionStart', async ($, e, next) => {
    if (e.source === 'clear') {
      await $.state.set(FLAG, false)
    } else if (e.source === 'resume') {
      // Plain first, so a store read that fails leaves the resumed session plain, never the previous
      // session's value.
      await $.state.set(FLAG, false)
      if (isSavedYvoke(await $.store.get(sessionKey(e.session_id)))) await $.state.set(FLAG, true)
    } else if (e.source === 'fork') {
      let { value: isYvoke } = await $.state.get(FLAG)
      if (isYvoke !== true) {
        const parent = endedId(await $.store.get(ENDED))
        isYvoke = parent !== undefined && isSavedYvoke(await $.store.get(sessionKey(parent)))
        if (isYvoke) await $.state.set(FLAG, true)
      }
      if (isYvoke === true) await $.store.set(sessionKey(e.session_id), { yvoke: true })
    }
    return next(e)
  })

  // Every session, plain ones too, so a /branch never takes an older Yvoke session for its parent.
  on('session.end', async ($, e, next) => {
    await $.store.set(ENDED, { sessionId: e.sessionId })
    return next(e)
  }).catch((_$, e, next) => next(e))
}
