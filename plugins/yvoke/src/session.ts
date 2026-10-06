// P1-10, D-13: a session becomes a Yvoke session only when the user types /yvoke before the first
// question. The flag lives in $.state (reset by /clear) and is saved per session id in $.store, so /resume
// and /branch keep it. This file is the only writer of the flag.
//
// Every enforcing hook in a later task opens with these two lines, written in its own file (the engine
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

/** Whether a stored entry marks a Yvoke session. */
export const isSavedYvoke = (entry: unknown) =>
  typeof entry === 'object' && entry !== null && (entry as { yvoke?: unknown }).yvoke === true

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
      const { value: isYvoke } = await $.state.get(FLAG)
      if (isYvoke === true) await $.store.set(sessionKey(e.session_id), { yvoke: true })
    }
    return next(e)
  })
}
