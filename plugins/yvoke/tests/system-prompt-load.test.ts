import { test, expect, describe } from 'claude-code/testing'
import type { McpToolResult } from 'claude-code'
import type { ServerIo } from '../src/server'
import { loadInstructions, reusable, DEFAULT_SETUP, MARGIN_MS, SEPARATOR } from '../src/system-prompt'

// P1-07: loading the base instructions and the playbook text. As in server.test.ts, the server is a set of
// plain functions in place of `$`; the hooks are tested through the engine in system-prompt.test.ts.

const text = (body: string, isError = false): McpToolResult => ({ content: [{ type: 'text', text: body }], isError })

const AREAS = JSON.stringify([
  { name: 'PingID', defaultPlaybook: 'ping-full', defaultSystemPrompt: null, systemPrompts: ['ping-strict'] },
  { name: 'OIM', defaultPlaybook: 'oim-full', defaultSystemPrompt: 'oim-base', systemPrompts: ['oim-base', 'oim-strict'] },
])
const playbook = (name: string, body: string, systemPrompt: string | null = null) =>
  JSON.stringify({ name, title: name, area: 'OIM', systemPrompt, version: 1, text: body })

type Answers = Record<string, (args: Record<string, unknown>) => Promise<McpToolResult>>

const server = (answers: Answers = {}) => {
  const calls: { tool: string; args: Record<string, unknown> }[] = []
  const sleeps: number[] = []
  const defaults: Answers = {
    list_areas: async () => text(AREAS),
    get_system_prompt: async (args) => text(`BASE ${args.name === undefined ? 'active' : String(args.name)}`),
    get_playbook: async (args) => text(playbook(String(args.name), `PLAYBOOK ${String(args.name)}`)),
  }
  const io: ServerIo = {
    connect: async () => ({ isConnected: true, server: 'plugin:yvoke:yvoke' }),
    call: (_server, tool, args) => {
      calls.push({ tool, args })
      const answer = answers[tool] ?? defaults[tool]
      return answer ? answer(args) : Promise.resolve(text(`ERROR: no tool ${tool}`))
    },
    sleep: (ms) => {
      sleeps.push(ms)
      return new Promise<void>(() => {})
    },
  }
  return { io, calls, sleeps }
}

const plenty = () => 10_000

describe('success', () => {
  test("no setup: the default area, its default playbook, the area's default prompt first", async () => {
    const s = server()

    const answer = await loadInstructions(s.io, null, plenty)

    expect(answer).toMatchObject({ ok: true, text: `BASE oim-base${SEPARATOR}PLAYBOOK oim-full` })
    expect(DEFAULT_SETUP).toEqual({ area: 'OIM', mode: 'single', playbook: null })
    expect(SEPARATOR).toBe('\n\n---\n\n')
    expect(s.calls).toEqual([
      { tool: 'list_areas', args: {} },
      { tool: 'get_playbook', args: { name: 'oim-full' } },
      { tool: 'get_system_prompt', args: { name: 'oim-base' } },
    ])
  })

  test('a setup naming its playbook asks for the areas and the playbook at once', async () => {
    const s = server()

    const answer = await loadInstructions(s.io, { area: 'OIM', mode: 'single', playbook: 'oim-lite' }, plenty)

    expect(answer).toMatchObject({ ok: true, text: `BASE oim-base${SEPARATOR}PLAYBOOK oim-lite` })
    expect(s.calls).toEqual([
      { tool: 'list_areas', args: {} },
      { tool: 'get_playbook', args: { name: 'oim-lite' } },
      { tool: 'get_system_prompt', args: { name: 'oim-base' } },
    ])
  })

  test("a setup without a playbook takes that area's default", async () => {
    const s = server()

    const answer = await loadInstructions(s.io, { area: 'PingID', mode: 'single', playbook: null }, plenty)

    expect(answer).toMatchObject({ ok: true, text: `BASE active${SEPARATOR}PLAYBOOK ping-full` })
  })

  test("multi-agent mode: only the area's base instructions, no playbook", async () => {
    const s = server()

    const answer = await loadInstructions(s.io, { area: 'OIM', mode: 'oim-team', playbook: 'oim-full' }, plenty)

    expect(answer).toMatchObject({ ok: true, text: 'BASE oim-base' })
    expect(s.calls.map((c) => c.tool)).toEqual(['list_areas', 'get_system_prompt'])
  })
})

describe('what loaded (loadedFor)', () => {
  test('a setup naming its playbook: that playbook, at the version get_playbook gave', async () => {
    const s = server({ get_playbook: async () => text(JSON.stringify({ name: 'oim-lite', version: 4, text: 'LITE' })) })

    const answer = await loadInstructions(s.io, { area: 'OIM', mode: 'single', playbook: 'oim-lite', version: 3 }, plenty)

    expect(answer).toEqual({
      ok: true,
      text: `BASE oim-base${SEPARATOR}LITE`,
      loadedFor: { area: 'OIM', mode: 'single', playbook: 'oim-lite', version: 4 },
    })
  })

  test("no playbook in the setup: the area's default, at the version get_playbook gave", async () => {
    const s = server()

    expect(await loadInstructions(s.io, null, plenty)).toMatchObject({
      loadedFor: { area: 'OIM', mode: 'single', playbook: 'oim-full', version: 1 },
    })
    expect(await loadInstructions(s.io, { area: 'PingID', mode: 'single', playbook: null }, plenty)).toMatchObject({
      loadedFor: { area: 'PingID', mode: 'single', playbook: 'ping-full', version: 1 },
    })
  })

  test('a get_playbook answer with no version, or one that is not a number: null', async () => {
    for (const version of [undefined, null, '4', { major: 4 }]) {
      const s = server({ get_playbook: async () => text(JSON.stringify({ name: 'oim-full', version, text: 'FULL' })) })

      expect(await loadInstructions(s.io, null, plenty)).toEqual({
        ok: true,
        text: `BASE oim-base${SEPARATOR}FULL`,
        loadedFor: { area: 'OIM', mode: 'single', playbook: 'oim-full', version: null },
      })
    }
  })

  test('multi-agent mode: no playbook and no version', async () => {
    const s = server()

    const answer = await loadInstructions(s.io, { area: 'OIM', mode: 'oim-team', playbook: 'oim-full' }, plenty)

    expect(answer).toEqual({
      ok: true,
      text: 'BASE oim-base',
      loadedFor: { area: 'OIM', mode: 'oim-team', playbook: null, version: null },
    })
  })
})

describe('reusing what loaded (reusable)', () => {
  const kept = (loadedFor: unknown) => ({ sessionId: 'session-a', text: 'TEXT', loadedFor })
  const FULL_V3 = { area: 'OIM', mode: 'single', playbook: 'oim-full', version: 3 }

  test('the same session and setup; no playbook or version in the setup, or none that loaded, checks none', () => {
    expect(reusable(kept(FULL_V3), 'session-a', { area: 'OIM', mode: 'single', playbook: 'oim-full', version: 3 })).toBe(true)
    // The area's default loaded for a setup that names none, and a setup with no version to check.
    expect(reusable(kept(FULL_V3), 'session-a', null)).toBe(true)
    expect(reusable(kept(FULL_V3), 'session-a', { area: 'OIM', mode: 'single', playbook: 'oim-full', version: null })).toBe(true)
    expect(reusable(kept(FULL_V3), 'session-a', { area: 'OIM', mode: 'single', playbook: 'oim-full' })).toBe(true)
    const multi = { area: 'OIM', mode: 'multi', playbook: null, version: null }
    expect(reusable(kept(multi), 'session-a', multi)).toBe(true)
    // A version the server did not send is unknown, not another one.
    const setup = { area: 'OIM', mode: 'single', playbook: 'oim-full', version: 3 }
    expect(reusable(kept({ ...FULL_V3, version: null }), 'session-a', setup)).toBe(true)
  })

  test('another session, area, mode, playbook or version loads again', () => {
    const setup = { area: 'OIM', mode: 'single', playbook: 'oim-full', version: 3 }
    expect(reusable(kept(FULL_V3), 'session-b', setup)).toBe(false)
    expect(reusable(kept(FULL_V3), 'session-a', { ...setup, area: 'PingID' })).toBe(false)
    expect(reusable(kept(FULL_V3), 'session-a', { ...setup, mode: 'multi', playbook: null, version: null })).toBe(false)
    expect(reusable(kept(FULL_V3), 'session-a', { ...setup, playbook: 'oim-quick' })).toBe(false)
    expect(reusable(kept(FULL_V3), 'session-a', { ...setup, version: 4 })).toBe(false)
  })

  test('an entry without loadedFor, or one the plugin cannot read, never serves', () => {
    const setup = { area: 'OIM', mode: 'single', playbook: 'oim-full', version: 3 }
    for (const entry of [
      { sessionId: 'session-a', text: 'TEXT' },
      kept(null),
      kept('oim-full'),
      kept({ ...FULL_V3, version: '3' }),
      kept({ ...FULL_V3, playbook: 7 }),
      kept({ mode: 'single', playbook: 'oim-full', version: 3 }),
      { sessionId: 'session-a', loadedFor: FULL_V3 },
      null,
      undefined,
      'TEXT',
    ]) {
      expect(reusable(entry, 'session-a', setup)).toBe(false)
      expect(reusable(entry, 'session-a', null)).toBe(false)
    }
  })
})

describe('which base instructions (decided from list_areas before any get_system_prompt call)', () => {
  const owning = (systemPrompt: string | null) =>
    server({ get_playbook: async (args) => text(playbook(String(args.name), 'PLAYBOOK', systemPrompt)) })
  const prompts = (s: ReturnType<typeof server>) =>
    s.calls.filter((c) => c.tool === 'get_system_prompt').map((c) => c.args)

  test("the playbook's own prompt, when its own area lists it", async () => {
    const s = owning('oim-strict')

    expect(await loadInstructions(s.io, null, plenty)).toMatchObject({ ok: true, text: `BASE oim-strict${SEPARATOR}PLAYBOOK` })
    expect(prompts(s)).toEqual([{ name: 'oim-strict' }])
  })

  test('a prompt only another area lists is still used (the server looks prompts up globally)', async () => {
    const s = owning('ping-strict')

    expect(await loadInstructions(s.io, null, plenty)).toMatchObject({ ok: true, text: `BASE ping-strict${SEPARATOR}PLAYBOOK` })
    expect(prompts(s)).toEqual([{ name: 'ping-strict' }])
  })

  test("a prompt no area lists falls back to the area's default, without asking for it", async () => {
    const s = owning('nowhere')

    expect(await loadInstructions(s.io, null, plenty)).toMatchObject({ ok: true, text: `BASE oim-base${SEPARATOR}PLAYBOOK` })
    expect(prompts(s)).toEqual([{ name: 'oim-base' }])
  })

  test('no own prompt and no area default: the active default, asked for with no name', async () => {
    const s = owning(null)

    const answer = await loadInstructions(s.io, { area: 'PingID', mode: 'single', playbook: 'ping-lite' }, plenty)

    expect(answer).toMatchObject({ ok: true, text: `BASE active${SEPARATOR}PLAYBOOK` })
    expect(prompts(s)).toEqual([{}])
  })

  test('an ERROR: from get_system_prompt drops the question; there is no second call', async () => {
    const s = server({
      get_playbook: async (args) => text(playbook(String(args.name), 'PLAYBOOK', 'oim-strict')),
      get_system_prompt: async () => text("ERROR: system prompt 'oim-strict' does not exist."),
    })

    const answer = await loadInstructions(s.io, null, plenty)

    expect(answer).toEqual({ ok: false, error: "Yvoke Backend: ERROR: system prompt 'oim-strict' does not exist." })
    expect(prompts(s)).toEqual([{ name: 'oim-strict' }])
  })
})

describe('failures', () => {
  test('server down: the server client error, as is', async () => {
    const s = server()
    s.io.connect = async () => ({ isConnected: false, reason: 'failed', message: 'yvoke is not connected.' })

    const answer = await loadInstructions(s.io, null, plenty)

    expect(answer).toEqual({ ok: false, error: 'Yvoke Backend: yvoke is not connected.' })
  })

  test('unknown playbook', async () => {
    const s = server({ get_playbook: async () => text("ERROR: playbook 'oim-ful' not found.") })

    const answer = await loadInstructions(s.io, { area: 'OIM', mode: 'single', playbook: 'oim-ful' }, plenty)

    expect(answer).toEqual({ ok: false, error: "Yvoke Backend: ERROR: playbook 'oim-ful' not found." })
    expect(s.calls.map((c) => c.tool)).not.toContain('get_system_prompt')
  })

  test('list_areas fails', async () => {
    const s = server({ list_areas: async () => text('boom', true) })

    expect(await loadInstructions(s.io, null, plenty)).toEqual({ ok: false, error: 'Yvoke Backend: boom' })
  })

  test('the area is not in list_areas', async () => {
    const s = server()

    const answer = await loadInstructions(s.io, { area: 'SAP', mode: 'single', playbook: 'sap-full' }, plenty)

    expect(answer).toEqual({ ok: false, error: "Yvoke Backend: area 'SAP' does not exist." })
  })

  test('the area has no default playbook', async () => {
    const s = server({ list_areas: async () => text(JSON.stringify([{ name: 'OIM', defaultPlaybook: null }])) })

    const answer = await loadInstructions(s.io, null, plenty)

    expect(answer).toEqual({ ok: false, error: "Yvoke Backend: area 'OIM' has no default playbook." })
  })

  test('list_areas answers something that is not a list of areas', async () => {
    const s = server({ list_areas: async () => text('not json') })

    const answer = await loadInstructions(s.io, null, plenty)

    expect(answer).toEqual({ ok: false, error: 'Yvoke Backend: list_areas gave an answer the plugin cannot read.' })
  })

  test('get_playbook answers without a text', async () => {
    const s = server({ get_playbook: async () => text(JSON.stringify({ name: 'oim-full' })) })

    const answer = await loadInstructions(s.io, null, plenty)

    expect(answer).toEqual({ ok: false, error: 'Yvoke Backend: get_playbook gave an answer the plugin cannot read.' })
  })

  test('an empty base instruction or playbook text is a failure, not an empty prompt', async () => {
    const empty = server({ get_playbook: async () => text(playbook('oim-full', '  ')) })
    expect(await loadInstructions(empty.io, null, plenty)).toEqual({
      ok: false,
      error: "Yvoke Backend: playbook 'oim-full' has no text.",
    })

    const noBase = server({ get_system_prompt: async () => text('') })
    expect(await loadInstructions(noBase.io, null, plenty)).toEqual({
      ok: false,
      error: 'Yvoke Backend: get_system_prompt gave no instructions.',
    })
  })
})

describe('time budget', () => {
  test('each round waits at most 8 s, or what the hook has left less the margin', async () => {
    const left = [10_000, 10_000, 3_000]
    const s = server()

    await loadInstructions(s.io, null, () => left.shift() ?? 0)

    expect(s.sleeps).toEqual([8_000, 8_000, 3_000 - MARGIN_MS])
  })

  test('too little left: fails at once without calling the server', async () => {
    const s = server()

    const answer = await loadInstructions(s.io, null, () => MARGIN_MS + 500)

    expect(answer).toEqual({
      ok: false,
      error: 'Yvoke Backend: there was not enough time left to load the instructions. Send the question again.',
    })
    expect(s.calls).toEqual([])
  })
})
