import { test, expect, describe } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On, TurnStepChunk, TurnStepResult } from 'claude-code'

// P2-08: the turn ceilings through the engine. `$.state` is answered by `state.get` / `state.set` hooks
// beneath the plugin, so a test can start inside a Yvoke session, in either mode, without /yvoke or the
// setup band. The model is a `turn.step` hook that answers every request with some text and one tool call.

const WRAP_UP =
  'Yvoke: this question has reached its turn limit. Do not call any more tools. Answer now with what you ' +
  'have found, and say plainly that the answer may be incomplete.'
const CHECK_FAILED = 'Yvoke: the turn-limit check failed, so this tool was not run.'
const POLICY_FAILED = 'Yvoke: the policy check failed, so this tool was not run.'
const notAvailable = (tool: string) =>
  `The tool "${tool}" is not available in this application. Use the yvoke knowledge-base tools instead.`
const flag = (n: number) => `Yvoke: stopped at the turn limit (${n} model requests). The answer may be incomplete.`

type World = {
  state: Map<string, unknown>
  failGet: string | null
  failSet: string | null
  toolsRun: string[]
  failConnect: boolean
}

const address = (e: { plugin: string; key: string; id?: string }) =>
  e.id === undefined ? `${e.plugin}.${e.key}` : `${e.plugin}.${e.key}#${e.id}`

function engine(on: On, start: { yvoke?: boolean; mode?: string } = {}): World {
  const w: World = { state: new Map(), failGet: null, failSet: null, toolsRun: [], failConnect: false }
  if (start.yvoke !== false) w.state.set('yvoke.yvokeSession', true)
  // The tool gate (P2-02) grants the server tools of the session's playbook: oim-full declares none, so the
  // default set, search_corpus among it.
  w.state.set('yvoke.catalogue', {
    ok: true,
    catalogue: { areas: [{ name: 'OIM', title: 'OIM', playbooks: [{ name: 'oim-full', version: 1, tools: null }], defaultPlaybook: 'oim-full', profiles: [], defaultProfile: null }] },
  })
  w.state.set(
    'yvoke.setup',
    start.mode === undefined
      ? { area: 'OIM', mode: 'single', playbook: 'oim-full', version: 1, profile: null, lockedSessionId: null }
      : { area: 'OIM', mode: start.mode, playbook: null },
  )
  on('state.get', (_$, e) => {
    // A hook that throws is skipped and the kit answers in its place, so a failed read is staged as an
    // answer the plugin cannot use.
    if (w.failGet === e.key) return { value: null as never }
    return { value: { value: w.state.get(address(e)), version: 1 } }
  })
  on('state.set', (_$, e) => {
    if (w.failSet === e.key) return { value: null as never }
    w.state.set(address(e), e.value)
    return { value: { isSet: true, version: 1 } }
  })
  on('turn.step', async function* (_$, e) {
    const id = `call-${e.agentId ?? 'main'}-${e.index}`
    yield { kind: 'text', index: 0, text: `text ${e.index}` }
    yield { kind: 'tool', index: 1, id, name: 'mcp__plugin_yvoke_yvoke__search_corpus' }
    yield { kind: 'input', index: 1, json: '{"query":"oim"}' }
    yield { kind: 'stop', stopReason: 'tool_use', usage: null }
    return {
      turnId: e.turnId,
      index: e.index,
      answer: `text ${e.index}`,
      toolUses: [{ name: 'mcp__plugin_yvoke_yvoke__search_corpus', input: { query: 'oim' } }],
      stopReason: 'tool_use',
      usage: null,
    }
  })
  // The tool gate (P2-01) asks for the Yvoke server's name to know its tools.
  on('mcp.connect', () => {
    if (w.failConnect) throw new Error('connect failed')
    return { value: { isConnected: true, server: 'plugin:yvoke:yvoke' } }
  })
  on('tool.call', (_$, e) => {
    w.toolsRun.push(e.tool)
    return { result: 'from the engine' }
  })
  on('turn.complete', (_$, e) => ({ text: e.answer }))
  return w
}

type Step = { chunks: TurnStepChunk[]; result: TurnStepResult }

// One model request as the engine would make it, read to its end.
async function step($: Engine, index: number, agentId?: string, turnId = 'turn-1'): Promise<Step> {
  const stream = $.turn.step({ turnId, index, model: 'claude', messageCount: 1, ...(agentId ? { agentId } : {}) })
  const chunks: TurnStepChunk[] = []
  for (;;) {
    const next = await stream.next()
    if (next.done) return { chunks, result: next.value }
    chunks.push(next.value)
  }
}

// The tool call the model's response asked for, as the engine would run it.
const callTool = ($: Engine, agentId?: string) =>
  $.tool.call({ tool: 'mcp__plugin_yvoke_yvoke__search_corpus', query: 'oim', ...(agentId ? { agentId } : {}) })

const complete = ($: Engine, answer: string, agentId?: string, turnId = 'turn-1') =>
  $.turn.complete({ answer, durationMs: 1, isAborted: false, turnId, reason: 'answer', ...(agentId ? { agentId } : {}) })

const kinds = (s: Step) => s.chunks.map((c) => c.kind)

// Runs requests 0 to `upTo`, each followed by its tool call, and returns what the last tool call answered.
async function runTo($: Engine, upTo: number, agentId?: string) {
  let answer: unknown
  for (let i = 0; i <= upTo; i++) {
    await step($, i, agentId)
    answer = await callTool($, agentId)
  }
  return answer
}

describe('outside a Yvoke session', () => {
  test('a request past any ceiling keeps its tool call, which runs, and the answer has no line', async ($, on) => {
    const w = engine(on, { yvoke: false })

    const last = await step($, 30)
    const answer = await callTool($)

    expect(kinds(last)).toEqual(['text', 'tool', 'input', 'stop'])
    expect(answer).toEqual({ result: 'from the engine' })
    expect(w.toolsRun).toEqual(['mcp__plugin_yvoke_yvoke__search_corpus'])
    expect(await complete($, 'the answer')).toMatchObject({ text: 'the answer' })
  })
})

describe('single agent, ceiling 25', () => {
  test('under the limit: requests and their tool calls pass unchanged', async ($, on) => {
    const w = engine(on)

    const first = await step($, 0)
    expect(await callTool($)).toEqual({ result: 'from the engine' })
    const late = await step($, 22)
    expect(await callTool($)).toEqual({ result: 'from the engine' })

    expect(kinds(first)).toEqual(['text', 'tool', 'input', 'stop'])
    expect(kinds(late)).toEqual(['text', 'tool', 'input', 'stop'])
    expect(late.result.stopReason).toBe('tool_use')
    expect(w.toolsRun).toHaveLength(2)
    expect(await complete($, 'the answer')).toMatchObject({ text: 'the answer' })
  })

  test('request 24 of 25: its tool calls are denied with the wrap-up message', async ($, on) => {
    const w = engine(on)

    expect(await runTo($, 22)).toEqual({ result: 'from the engine' })
    const warned = await step($, 23)
    const denied = await callTool($)

    expect(kinds(warned)).toEqual(['text', 'tool', 'input', 'stop'])
    expect(denied).toMatchObject({ deny: WRAP_UP })
    expect(w.toolsRun).toHaveLength(23)
  })

  test('request 25 of 25 is the answer: its tool calls are dropped and the turn ends', async ($, on) => {
    engine(on)

    await runTo($, 23)
    const last = await step($, 24)

    expect(kinds(last)).toEqual(['text', 'stop'])
    expect(last.chunks[0]).toMatchObject({ kind: 'text', text: 'text 24' })
    expect(last.chunks[1]).toMatchObject({ kind: 'stop', stopReason: 'end_turn' })
    expect(last.result).toMatchObject({ answer: 'text 24', toolUses: [], stopReason: 'end_turn' })
  })

  test('the answer gets the flag line beneath it', async ($, on) => {
    engine(on)

    await runTo($, 23)
    await step($, 24)

    expect(await complete($, 'text 24')).toMatchObject({ text: flag(25) })
  })

  test('a turn that ended before the ceiling gets no line', async ($, on) => {
    engine(on)

    await runTo($, 23)
    expect(await complete($, 'done early')).toMatchObject({ text: 'done early' })
  })

  test("the next question starts counting again, and the previous question's line is not repeated", async ($, on) => {
    engine(on)

    await runTo($, 23)
    await step($, 24)
    await step($, 0, undefined, 'turn-2')

    expect(await callTool($)).toEqual({ result: 'from the engine' })
    expect(await complete($, 'second answer', undefined, 'turn-2')).toMatchObject({ text: 'second answer' })
  })
  test('a later turn that ends before its first request does not get the earlier turn\'s line', async ($, on) => {
    engine(on)

    await runTo($, 23)
    await step($, 24)

    expect(await complete($, '', undefined, 'turn-2')).toMatchObject({ text: '' })
  })
})

describe('each role', () => {
  test('the lead, in a multi-agent session: 60', async ($, on) => {
    engine(on, { mode: 'multi' })

    expect(await runTo($, 57)).toEqual({ result: 'from the engine' })
    await step($, 58)
    expect(await callTool($)).toMatchObject({ deny: WRAP_UP })
    expect(kinds(await step($, 59))).toEqual(['text', 'stop'])
    expect(await complete($, 'lead answer')).toMatchObject({ text: flag(60) })
  })

  test('the main loop in single-agent mode, named as such: 25', async ($, on) => {
    engine(on, { mode: 'single' })

    await runTo($, 22)
    await step($, 23)
    expect(await callTool($)).toMatchObject({ deny: WRAP_UP })
  })

  test('a specialist or the reviewer: 20, and its report gets no line', async ($, on) => {
    engine(on, { mode: 'multi' })

    expect(await runTo($, 17, 'specialist-1')).toEqual({ result: 'from the engine' })
    await step($, 18, 'specialist-1')
    expect(await callTool($, 'specialist-1')).toMatchObject({ deny: WRAP_UP })
    expect(kinds(await step($, 19, 'specialist-1'))).toEqual(['text', 'stop'])
    expect(await complete($, 'partial report', 'specialist-1')).toMatchObject({ text: 'partial report' })
  })

  test('two specialists running side by side are counted apart, and apart from the lead', async ($, on) => {
    engine(on, { mode: 'multi' })

    await step($, 0)
    await step($, 18, 'specialist-1')
    await step($, 3, 'specialist-2')

    expect(await callTool($, 'specialist-2')).toEqual({ result: 'from the engine' })
    expect(await callTool($)).toEqual({ result: 'from the engine' })
    expect(await callTool($, 'specialist-1')).toMatchObject({ deny: WRAP_UP })
  })
})

describe('settings', () => {
  test('a ceiling set to 5', { options: { turnLimitSingle: 5 } }, async ($, on) => {
    engine(on)

    expect(await runTo($, 2)).toEqual({ result: 'from the engine' })
    await step($, 3)
    expect(await callTool($)).toMatchObject({ deny: WRAP_UP })
    expect(kinds(await step($, 4))).toEqual(['text', 'stop'])
    expect(await complete($, 'short')).toMatchObject({ text: flag(5) })
  })

  test('a ceiling set to 0 is off', { options: { turnLimitSingle: 0 } }, async ($, on) => {
    engine(on)

    const late = await step($, 200)
    expect(kinds(late)).toEqual(['text', 'tool', 'input', 'stop'])
    expect(await callTool($)).toEqual({ result: 'from the engine' })
    expect(await complete($, 'long')).toMatchObject({ text: 'long' })
  })
})

// One `tool.call` gate holds every tool rule (the engine takes one unmatched `tool.call` hook per plugin).
// The turn limit is checked first, so at the limit the model is told to answer, never pointed to other tools.
describe('with the tool policy', () => {
  test('under the limit, a tool the policy refuses is refused by the policy', async ($, on) => {
    const w = engine(on)

    await step($, 0)
    expect(await $.tool.call({ tool: 'Bash', command: 'ls' })).toMatchObject({ deny: notAvailable('Bash') })
    expect(w.toolsRun).toEqual([])
  })

  test('at the limit, a tool the policy refuses gets the wrap-up message instead', async ($, on) => {
    engine(on)

    await runTo($, 22)
    await step($, 23)
    expect(await $.tool.call({ tool: 'Bash', command: 'ls' })).toMatchObject({ deny: WRAP_UP })
  })

  test('the turn-limit check fails: its own message, and the server is not asked', async ($, on) => {
    const w = engine(on)

    await step($, 0)
    w.failGet = 'loopStep'
    w.failConnect = true
    expect(await callTool($)).toMatchObject({ deny: CHECK_FAILED })
  })

  test('the turn-limit check passes and the policy check fails: the policy message', async ($, on) => {
    const w = engine(on)

    await step($, 0)
    w.failConnect = true
    expect(await callTool($)).toMatchObject({ deny: POLICY_FAILED })
    expect(w.toolsRun).toEqual([])
  })
})

describe('a hook failure fails closed', () => {
  test('the tool-call check cannot read its state: the tool is denied', async ($, on) => {
    const w = engine(on)

    await step($, 0)
    w.failGet = 'loopStep'
    expect(await callTool($)).toMatchObject({ deny: CHECK_FAILED })
    expect(w.toolsRun).toEqual([])
  })

  test('a request cannot record where its loop stands: its tool calls are dropped', async ($, on) => {
    const w = engine(on)

    w.failSet = 'loopStep'
    const first = await step($, 0)

    expect(kinds(first)).toEqual(['text', 'stop'])
    expect(first.result).toMatchObject({ toolUses: [], stopReason: 'end_turn' })
  })

  test('a request cannot read the setup: its tool calls are dropped', async ($, on) => {
    const w = engine(on, { mode: 'multi' })

    w.failGet = 'setup'
    expect(kinds(await step($, 0))).toEqual(['text', 'stop'])
  })

  test('the end of the turn cannot read its state: the answer stands as it was', async ($, on) => {
    const w = engine(on)

    await runTo($, 23)
    await step($, 24)
    w.failGet = 'loopStep'
    expect(await complete($, 'text 24')).toMatchObject({ text: 'text 24' })
  })
})
