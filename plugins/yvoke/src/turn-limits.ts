// turn-limits.md: turn ceilings per question. Each loop (the main loop, each subagent's run) may make at
// most `n` model requests in one turn. The tool calls asked for by request n − 1 are denied with a message
// telling the model to answer now; request n's tool calls are dropped from its response, so the engine runs
// none and the turn ends with the text written so far. A main-loop answer that reached request n gets a
// line beneath it. The work is delivered, never discarded (decided 2026-10-05; D-24 for the subagents).
//
// `turn.step` carries the request's position (`index`), so nothing is counted here: each request writes
// where its loop stands to `$.state` `yvoke.loopStep` (one member per loop). The denial itself is the first
// check of the one `tool.call` gate in src/policy.ts, which knows the call's loop but not its request and
// reads `loopStep` back: the engine takes one unmatched `tool.call` hook per plugin, so the gate is one hook.
import type { On, PluginOptions, TurnStepChunk, TurnStepResult } from 'claude-code'

export type Limits = { single: number | null; lead: number | null; agent: number | null }

export const DEFAULT_LIMITS: Limits = { single: 25, lead: 60, agent: 20 }

export const WRAP_UP =
  'Yvoke: this question has reached its turn limit. Do not call any more tools. Answer now with what you ' +
  'have found, and say plainly that the answer may be incomplete.'

export const CHECK_FAILED = 'Yvoke: the turn-limit check failed, so this tool was not run.'

export const flagLine = (n: number) =>
  `Yvoke: stopped at the turn limit (${n} model requests). The answer may be incomplete.`

// 0 is off, as in yvoke-desktop. Below 2 counts as 2: one request to be warned, one to answer.
function limitFrom(value: unknown, fallback: number | null): number | null {
  const n = typeof value === 'number' ? value : typeof value === 'string' && value.trim() !== '' ? Number(value) : NaN
  if (!Number.isFinite(n)) return fallback
  if (n === 0) return null
  return Math.max(2, Math.floor(n))
}

/** The three ceilings from the plugin's settings, defaults filled in. */
export function limitsFrom(options: PluginOptions): Limits {
  return {
    single: limitFrom(options.turnLimitSingle, DEFAULT_LIMITS.single),
    lead: limitFrom(options.turnLimitLead, DEFAULT_LIMITS.lead),
    agent: limitFrom(options.turnLimitAgent, DEFAULT_LIMITS.agent),
  }
}

/** The ceiling of one loop: every subagent shares one (D-24); the main loop is the lead outside single-agent mode. */
export function limitFor(limits: Limits, agentId: string | undefined, mode: string | undefined): number | null {
  if (agentId !== undefined) return limits.agent
  return mode === undefined || mode === 'single' ? limits.single : limits.lead
}

/** Whether the tool calls of the request at `index` are denied with the wrap-up message. */
export const isWrapUp = (index: number, n: number) => index >= n - 2

/** Whether the request at `index` is the loop's last: its tool calls are dropped. */
export const isLast = (index: number, n: number) => index >= n - 1

// A response with its tool calls taken out: the `tool` chunk and the arguments of each tool block are
// dropped (the engine then records and runs none), and a `tool_use` stop becomes `end_turn`.
async function* withoutTools(stream: AsyncGenerator<TurnStepChunk, TurnStepResult>) {
  const toolBlocks = new Set<number>()
  for (;;) {
    const next = await stream.next()
    if (next.done) {
      const result = next.value
      return {
        ...result,
        toolUses: [],
        stopReason: result.stopReason === 'tool_use' ? 'end_turn' : result.stopReason,
      } satisfies TurnStepResult
    }
    const chunk = next.value
    if (chunk.kind === 'tool') toolBlocks.add(chunk.index)
    else if (chunk.kind === 'input' && toolBlocks.has(chunk.index)) continue
    else if (chunk.kind === 'stop' && chunk.stopReason === 'tool_use') yield { ...chunk, stopReason: 'end_turn' as const }
    else yield chunk
  }
}

export function registerTurnLimits(on: On, options: PluginOptions) {
  const limits = limitsFrom(options)

  on('turn.step', async function* ($, e, next) {
    const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
    if (isYvoke !== true) return yield* next(e)
    // Fails closed: when this request's place cannot be read or recorded, it is treated as the last one.
    let last = true
    try {
      const mode = e.agentId === undefined ? (await $.state.get({ plugin: 'yvoke', key: 'setup' })).value?.mode : undefined
      const n = limitFor(limits, e.agentId, mode)
      const written = await $.state.set(
        { plugin: 'yvoke', key: 'loopStep', id: e.agentId ?? 'main' },
        n === null ? null : { turnId: e.turnId, index: e.index, n },
      )
      // Unrecorded, the next tool call could not be warned, so the loop ends here.
      last = !written.isSet || (n !== null && isLast(e.index, n))
    } catch {
      last = true
    }
    if (!last) return yield* next(e)
    return yield* withoutTools(next(e))
  }).catch(async function* ($, e, next) {
    return yield* withoutTools(next(e))
  })

  // Only adds a line; it enforces nothing, so a failure here leaves the answer as it was.
  on('turn.complete', async ($, e, next) => {
    const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
    if (isYvoke !== true || e.agentId !== undefined) return next(e)
    const done = await next(e)
    const { value: at } = await $.state.get({ plugin: 'yvoke', key: 'loopStep', id: 'main' })
    if (at && at.turnId === e.turnId && isLast(at.index, at.n)) return { ...done, text: flagLine(at.n) }
    return done
  })
}
