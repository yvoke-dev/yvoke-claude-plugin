// P1-07: the system prompt of a Yvoke session comes from the server. On the session's first question the
// mod loads the base instructions (`get_system_prompt`, D-12, D-16) and, in single-agent mode, the
// playbook's text (`get_playbook`), and serves them from `prompt.compose` as one `session` section: base
// instructions first, the playbook after them, so the playbook wins a conflict. A failed load drops the
// question; nothing is cached as a fallback (requirements 1). Once the setup is locked in this session, a
// prompt composed without them is replaced by MISSING_SECTION alone.
//
// The setup (area, mode, playbook) is P1-08's to choose. Until it writes `$.state` `yvoke.setup`, the
// session runs on DEFAULT_SETUP: area OIM, single agent, the area's default playbook from `list_areas`.
// `yvoke.instructions` keeps the text with what it was loaded for (`loadedFor`): P1-08 locks the setup with
// the playbook version that loaded, and a later question reuses the text only while the setup still matches.
import type { On, PromptComposeSection } from 'claude-code'
import { callYvoke, TIMEOUT_MS, type ServerAnswer, type ServerIo } from './server'

/**
 * What a session runs on. `mode` is `single` or a multi-agent profile's name; `playbook` null = default.
 * `version` is the one the band showed, null or left out when there is none to check.
 */
export type Setup = { area: string; mode: string; playbook: string | null; version?: number | null }

/**
 * What a load was for: the setup's area and mode, the playbook it loaded (the setup's, or the area's
 * default) and the `version` `get_playbook` gave, null when it gave none. Both null in multi-agent mode.
 */
export type LoadedFor = { area: string; mode: string; playbook: string | null; version: number | null }

/** Instructions loaded for one session; text kept under another session's id is never served. */
export type Loaded = { sessionId: string; text: string; loadedFor: LoadedFor }

type Failed = { ok: false; error: string }
export type Instructions = { ok: true; text: string; loadedFor: LoadedFor } | Failed

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

/**
 * Served in place of the prompt when the `prompt.compose` hook itself fails, or when the setup is locked in
 * this session and its instructions are not there: a turn never runs bare.
 */
export const MISSING_SECTION: PromptComposeSection = {
  id: SECTION_ID,
  text:
    'The Yvoke instructions for this session could not be loaded. Do not answer the question and do not call any ' +
    'tool. Reply only: "Yvoke could not load its instructions. Please send your question again."',
  scope: 'session',
}

const ORIGIN = 'Yvoke Backend:'
const fail = (message: string): Failed => ({ ok: false, error: `${ORIGIN} ${message}` })
const unreadable = (tool: string) => fail(`${tool} gave an answer the plugin cannot read.`)

/**
 * Loads the base instructions and, in single-agent mode, the playbook text, joined by SEPARATOR, with what
 * they were loaded for. `get_playbook` takes a name only, so the version is the one the server sent.
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
): Promise<Instructions> => {
  const { area, mode, playbook } = setup ?? DEFAULT_SETUP

  const round = <T>(calls: (timeoutMs: number) => Promise<T>): Promise<T | Failed> => {
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
    if (!entry.ok) return entry
    const base = await basePrompt(entry.area.defaultSystemPrompt)
    return base.ok ? { ...base, loadedFor: { area, mode, playbook: null, version: null } } : base
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
  return {
    ok: true,
    text: `${base.text}${SEPARATOR}${parsed.text}`,
    loadedFor: { area, mode, playbook: name, version: parsed.version },
  }
}

/**
 * Whether instructions kept in `$.state` may serve this question: loaded under this session id, for the same
 * area and mode, the same playbook unless the setup names none, and the same version unless either is null
 * (a version the server did not send is unknown, not another one). An entry with no `loadedFor` (kept from
 * before it existed) or one the plugin cannot read never does.
 */
export const reusable = (loaded: unknown, sessionId: string, setup: Setup | null): boolean => {
  if (!isRecord(loaded) || loaded.sessionId !== sessionId || typeof loaded.text !== 'string') return false
  const was = loaded.loadedFor
  if (!isLoadedFor(was)) return false
  const { area, mode, playbook, version = null } = setup ?? DEFAULT_SETUP
  return (
    was.area === area &&
    was.mode === mode &&
    (playbook === null || was.playbook === playbook) &&
    (version === null || was.version === null || was.version === version)
  )
}

const isLoadedFor = (x: unknown): x is LoadedFor =>
  isRecord(x) &&
  typeof x.area === 'string' &&
  typeof x.mode === 'string' &&
  (x.playbook === null || typeof x.playbook === 'string') &&
  (x.version === null || typeof x.version === 'number')

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

type PlaybookText =
  | { ok: true; text: string; systemPrompt: string | null; version: number | null }
  | { ok: false; error: string }

const playbookText = (json: string, name: string): PlaybookText => {
  const parsed = parse(json)
  if (!isRecord(parsed) || typeof parsed.text !== 'string') return unreadable('get_playbook')
  const text = parsed.text.trim()
  if (!text) return fail(`playbook '${name}' has no text.`)
  const version = typeof parsed.version === 'number' ? parsed.version : null
  return { ok: true, text, systemPrompt: nameOf(parsed.systemPrompt), version }
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
    const { value: setup } = await $.state.get({ plugin: 'yvoke', key: 'setup' })
    // Only while the setup still matches: a question dropped after the load leaves the band open to another
    // playbook.
    if (reusable(loaded, sessionId, setup ?? null)) return next(e)

    const io: ServerIo = {
      connect: (server) => $.mcp.connect(server),
      call: (server, tool, args) => $.mcp.call(server, tool, args),
      sleep: (ms, signal) => $.clock.sleep(ms, { signal }),
    }
    const answer = await loadInstructions(io, setup ?? null, () => next.budget.remainingMs)
    if (!answer.ok) return { drop: answer.error }
    const kept: Loaded = { sessionId, text: answer.text, loadedFor: answer.loadedFor }
    await $.state.set({ plugin: 'yvoke', key: 'instructions' }, kept)
    return next(e)
  }).catch(($, e, next) => (next.called ? next(e) : { drop: DROP_FAILED }))

  on('prompt.compose', async ($, e, next) => {
    const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
    if (isYvoke !== true) return next(e)

    const { value: loaded } = await $.state.get({ plugin: 'yvoke', key: 'instructions' })
    const sessionId = await $.session.id()
    const composed = await next(e)
    // What loaded for this session serves, locked or not: setup.ts takes the loaded version only after the
    // question has entered, so the setup is not checked here.
    if (loaded?.sessionId === sessionId && typeof loaded.text === 'string') {
      return { sections: [...composed.sections, { id: SECTION_ID, text: loaded.text, scope: 'session' }] }
    }
    // Locked in this session: a question entered without its instructions, so the refusal replaces the
    // prompt. Before the first question, Claude Code's own prompts pass unchanged.
    const { value: setup } = await $.state.get({ plugin: 'yvoke', key: 'setup' })
    if (setup?.lockedSessionId === sessionId) return { sections: [MISSING_SECTION] }
    return composed
  }).catch(() => ({ sections: [MISSING_SECTION] }))
}
