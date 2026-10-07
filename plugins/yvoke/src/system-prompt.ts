// P1-07: the system prompt of a Yvoke session comes from the server. On the session's first question the
// mod loads the base instructions (`get_system_prompt`, D-12, D-16) and, in single-agent mode, the
// playbook's text (`get_playbook`), and serves them from `prompt.compose` as one `session` section: base
// instructions first, the playbook after them, so the playbook wins a conflict. A failed load drops the
// question; nothing is cached as a fallback (requirements 1).
//
// The setup (area, mode, playbook) is P1-08's to choose. Until it writes `$.state` `yvoke.setup`, the
// session runs on DEFAULT_SETUP: area OIM, single agent, the area's default playbook from `list_areas`.
// Whether this session's instructions loaded is P1-08's "setup locked" signal (`yvoke.instructions`).
import type { On, PromptComposeSection } from 'claude-code'
import { callYvoke, TIMEOUT_MS, type ServerAnswer, type ServerIo } from './server'

/** What a session runs on. `mode` is `single` or a multi-agent profile's name; `playbook` null = default. */
export type Setup = { area: string; mode: string; playbook: string | null }

/** Instructions loaded for one session; text kept under another session's id is never served. */
export type Loaded = { sessionId: string; text: string }

export const SINGLE_AGENT = 'single'
export const DEFAULT_SETUP: Setup = { area: 'OIM', mode: SINGLE_AGENT, playbook: null }

/** What a round of server calls leaves of the hook's budget for the rest of the hook. */
export const MARGIN_MS = 1_500
/** Below this, a round would only time out; it fails at once instead. */
const MIN_ROUND_MS = 1_000

export const SECTION_ID = 'yvoke:instructions'

export const DROP_FAILED = 'Yvoke Backend: the instructions could not be loaded, so the question was not sent.'
const NO_TIME = 'there was not enough time left to load the instructions. Send the question again.'

/** Served in place of the prompt when the `prompt.compose` hook itself fails: a turn never runs bare. */
export const MISSING_SECTION: PromptComposeSection = {
  id: SECTION_ID,
  text:
    'The Yvoke instructions for this session could not be loaded. Do not answer the question and do not call any ' +
    'tool. Reply only: "Yvoke could not load its instructions. Please send your question again."',
  scope: 'session',
}

const ORIGIN = 'Yvoke Backend:'
const fail = (message: string): ServerAnswer => ({ ok: false, error: `${ORIGIN} ${message}` })
const unreadable = (tool: string) => fail(`${tool} gave an answer the plugin cannot read.`)

/**
 * Loads the base instructions and, in single-agent mode, the playbook text, joined by a blank line.
 * `remainingMs` reads what the calling hook has left; each round of calls waits at most that less
 * MARGIN_MS, and at most the server client's own 8 s.
 */
export const loadInstructions = async (
  io: ServerIo,
  setup: Setup | null,
  remainingMs: () => number,
): Promise<ServerAnswer> => {
  const { area, mode, playbook } = setup ?? DEFAULT_SETUP

  const round = <T>(calls: (timeoutMs: number) => Promise<T>): Promise<T | ServerAnswer> => {
    const timeoutMs = Math.min(TIMEOUT_MS, remainingMs() - MARGIN_MS)
    return timeoutMs < MIN_ROUND_MS ? Promise.resolve(fail(NO_TIME)) : calls(timeoutMs)
  }
  const call = (tool: string, args: Record<string, unknown>) => (timeoutMs: number) =>
    callYvoke(io, tool, args, { timeoutMs })

  if (mode !== SINGLE_AGENT) {
    const base = await round(call('get_system_prompt', { area }))
    return base.ok ? baseText(base.text) : base
  }

  if (playbook) {
    const both = await round((t) =>
      Promise.all([call('get_system_prompt', { area })(t), call('get_playbook', { name: playbook })(t)]),
    )
    return Array.isArray(both) ? join(both[0], both[1], playbook) : both
  }

  const first = await round((t) => Promise.all([call('list_areas', {})(t), call('get_system_prompt', { area })(t)]))
  if (!Array.isArray(first)) return first
  const [areas, base] = first
  if (!areas.ok) return areas
  if (!base.ok) return base
  const name = defaultPlaybook(areas.text, area)
  if (!name.ok) return name
  return join(base, await round(call('get_playbook', { name: name.text })), name.text)
}

const join = (base: ServerAnswer, pb: ServerAnswer, name: string): ServerAnswer => {
  if (!base.ok) return base
  if (!pb.ok) return pb
  const b = baseText(base.text)
  if (!b.ok) return b
  const p = playbookText(pb.text, name)
  if (!p.ok) return p
  return { ok: true, text: `${b.text}\n\n${p.text}` }
}

const baseText = (text: string): ServerAnswer =>
  text.trim() ? { ok: true, text: text.trim() } : fail('get_system_prompt gave no instructions.')

const playbookText = (json: string, name: string): ServerAnswer => {
  const parsed = parse(json)
  if (!isRecord(parsed) || typeof parsed.text !== 'string') return unreadable('get_playbook')
  const text = parsed.text.trim()
  return text ? { ok: true, text } : fail(`playbook '${name}' has no text.`)
}

const defaultPlaybook = (json: string, area: string): ServerAnswer => {
  const parsed = parse(json)
  if (!Array.isArray(parsed) || !parsed.every(isRecord)) return unreadable('list_areas')
  const entry = parsed.find((a) => a.name === area)
  if (!entry) return fail(`area '${area}' does not exist.`)
  const name = entry.defaultPlaybook
  return typeof name === 'string' && name.trim() ? { ok: true, text: name.trim() } : fail(`area '${area}' has no default playbook.`)
}

const parse = (json: string): unknown => {
  try {
    return JSON.parse(json)
  } catch {
    return undefined
  }
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

export function registerSystemPrompt(on: On) {
  on('prompt.submit', async ($, e, next) => {
    const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
    if (isYvoke !== true) return next(e)

    const sessionId = await $.session.id()
    const { value: loaded } = await $.state.get({ plugin: 'yvoke', key: 'instructions' })
    if (loaded?.sessionId === sessionId) return next(e)

    const { value: setup } = await $.state.get({ plugin: 'yvoke', key: 'setup' })
    const io: ServerIo = {
      connect: (server) => $.mcp.connect(server),
      call: (server, tool, args) => $.mcp.call(server, tool, args),
      sleep: (ms, signal) => $.clock.sleep(ms, { signal }),
    }
    const answer = await loadInstructions(io, setup ?? null, () => next.budget.remainingMs)
    if (!answer.ok) return { drop: answer.error }
    await $.state.set({ plugin: 'yvoke', key: 'instructions' }, { sessionId, text: answer.text })
    return next(e)
  }).catch(($, e, next) => (next.called ? next(e) : { drop: DROP_FAILED }))

  on('prompt.compose', async ($, e, next) => {
    const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
    if (isYvoke !== true) return next(e)

    const { value: loaded } = await $.state.get({ plugin: 'yvoke', key: 'instructions' })
    const sessionId = await $.session.id()
    const composed = await next(e)
    if (loaded?.sessionId !== sessionId) return composed
    return { sections: [...composed.sections, { id: SECTION_ID, text: loaded.text, scope: 'session' }] }
  }).catch(() => ({ sections: [MISSING_SECTION] }))
}
