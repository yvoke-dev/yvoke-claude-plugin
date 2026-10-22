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

/** Between the base instructions and the playbook text, as yvoke-web's chat joins them. */
export const SEPARATOR = '\n\n---\n\n'

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
const fail = (message: string): { ok: false; error: string } => ({ ok: false, error: `${ORIGIN} ${message}` })
const unreadable = (tool: string) => fail(`${tool} gave an answer the plugin cannot read.`)

/**
 * Loads the base instructions and, in single-agent mode, the playbook text, joined by SEPARATOR.
 *
 * Which base instructions is decided from data before any `get_system_prompt` call, as yvoke-web and
 * yvoke-desktop 1.3.0 do: the playbook's own `systemPrompt` when any area lists it among its
 * `systemPrompts` (the server looks a prompt name up globally), else the session area's
 * `defaultSystemPrompt`, else the active default (no name, D-16). Then
 * exactly one `get_system_prompt` call; if it fails, an `ERROR:` body included, the question is dropped,
 * since the server client cannot tell a missing prompt from a broken server.
 *
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
  const basePrompt = async (name: string | null) => {
    const base = await round(call('get_system_prompt', name ? { name } : {}))
    return base.ok ? baseText(base.text) : base
  }

  if (mode !== SINGLE_AGENT) {
    const areas = await round(call('list_areas', {}))
    if (!areas.ok) return areas
    const entry = findArea(areas.text, area)
    return entry.ok ? basePrompt(entry.area.defaultSystemPrompt) : entry
  }

  let entry: AreaFound
  let pb: ServerAnswer
  let name: string
  if (playbook) {
    const both = await round((t) => Promise.all([call('list_areas', {})(t), call('get_playbook', { name: playbook })(t)]))
    if (!Array.isArray(both)) return both
    const [areas, answer] = both
    if (!areas.ok) return areas
    entry = findArea(areas.text, area)
    pb = answer
    name = playbook
  } else {
    const areas = await round(call('list_areas', {}))
    if (!areas.ok) return areas
    entry = findArea(areas.text, area)
    if (!entry.ok) return entry
    if (!entry.area.defaultPlaybook) return fail(`area '${area}' has no default playbook.`)
    name = entry.area.defaultPlaybook
    pb = await round(call('get_playbook', { name }))
  }
  if (!entry.ok) return entry
  if (!pb.ok) return pb
  const parsed = playbookText(pb.text, name)
  if (!parsed.ok) return parsed

  const own = parsed.systemPrompt
  const base = await basePrompt(own && entry.allPrompts.includes(own) ? own : entry.area.defaultSystemPrompt)
  if (!base.ok) return base
  return { ok: true, text: `${base.text}${SEPARATOR}${parsed.text}` }
}

type Area = { defaultPlaybook: string | null; defaultSystemPrompt: string | null }
/** The session's area, and every system prompt name any area lists. */
type AreaFound = { ok: true; area: Area; allPrompts: string[] } | { ok: false; error: string }

const findArea = (json: string, area: string): AreaFound => {
  const parsed = parse(json)
  if (!Array.isArray(parsed) || !parsed.every(isRecord)) return unreadable('list_areas')
  const entry = parsed.find((a) => a.name === area)
  if (!entry) return fail(`area '${area}' does not exist.`)
  return {
    ok: true,
    area: { defaultPlaybook: nameOf(entry.defaultPlaybook), defaultSystemPrompt: nameOf(entry.defaultSystemPrompt) },
    allPrompts: parsed.flatMap((a) =>
      Array.isArray(a.systemPrompts) ? a.systemPrompts.filter((n): n is string => typeof n === 'string') : [],
    ),
  }
}

const nameOf = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value.trim() : null)

const baseText = (text: string): ServerAnswer =>
  text.trim() ? { ok: true, text: text.trim() } : fail('get_system_prompt gave no instructions.')

type PlaybookText = { ok: true; text: string; systemPrompt: string | null } | { ok: false; error: string }

const playbookText = (json: string, name: string): PlaybookText => {
  const parsed = parse(json)
  if (!isRecord(parsed) || typeof parsed.text !== 'string') return unreadable('get_playbook')
  const text = parsed.text.trim()
  if (!text) return fail(`playbook '${name}' has no text.`)
  return { ok: true, text, systemPrompt: nameOf(parsed.systemPrompt) }
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
