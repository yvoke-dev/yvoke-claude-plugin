import { test, expect, describe } from 'claude-code/testing'
import type { McpToolResult } from 'claude-code'
import type { ServerIo } from '../src/server'
import { loadInstructions, DEFAULT_SETUP, MARGIN_MS } from '../src/system-prompt'

// P1-07: loading the base instructions and the playbook text. As in server.test.ts, the server is a set of
// plain functions in place of `$`; the hooks are tested through the engine in system-prompt.test.ts.

const text = (body: string, isError = false): McpToolResult => ({ content: [{ type: 'text', text: body }], isError })

const AREAS = JSON.stringify([
  { name: 'PingID', defaultPlaybook: 'ping-full', defaultSystemPrompt: null },
  { name: 'OIM', defaultPlaybook: 'oim-full', defaultSystemPrompt: 'oim-base' },
])
const playbook = (name: string, body: string) => JSON.stringify({ name, title: name, area: 'OIM', text: body })

type Answers = Record<string, (args: Record<string, unknown>) => Promise<McpToolResult>>

const server = (answers: Answers = {}) => {
  const calls: { tool: string; args: Record<string, unknown> }[] = []
  const sleeps: number[] = []
  const defaults: Answers = {
    list_areas: async () => text(AREAS),
    get_system_prompt: async () => text('BASE INSTRUCTIONS'),
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
  test('no setup: the default area, its default playbook, base instructions first', async () => {
    const s = server()

    const answer = await loadInstructions(s.io, null, plenty)

    expect(answer).toEqual({ ok: true, text: 'BASE INSTRUCTIONS\n\nPLAYBOOK oim-full' })
    expect(DEFAULT_SETUP).toEqual({ area: 'OIM', mode: 'single', playbook: null })
    expect(s.calls).toEqual([
      { tool: 'list_areas', args: {} },
      { tool: 'get_system_prompt', args: { area: 'OIM' } },
      { tool: 'get_playbook', args: { name: 'oim-full' } },
    ])
  })

  test('a setup naming its playbook skips list_areas and asks for both at once', async () => {
    const s = server()

    const answer = await loadInstructions(s.io, { area: 'PingID', mode: 'single', playbook: 'ping-lite' }, plenty)

    expect(answer).toEqual({ ok: true, text: 'BASE INSTRUCTIONS\n\nPLAYBOOK ping-lite' })
    expect(s.calls).toEqual([
      { tool: 'get_system_prompt', args: { area: 'PingID' } },
      { tool: 'get_playbook', args: { name: 'ping-lite' } },
    ])
  })

  test("a setup without a playbook takes that area's default", async () => {
    const s = server()

    const answer = await loadInstructions(s.io, { area: 'PingID', mode: 'single', playbook: null }, plenty)

    expect(answer).toEqual({ ok: true, text: 'BASE INSTRUCTIONS\n\nPLAYBOOK ping-full' })
  })

  test('multi-agent mode: only the base instructions, no playbook', async () => {
    const s = server()

    const answer = await loadInstructions(s.io, { area: 'OIM', mode: 'oim-team', playbook: 'oim-full' }, plenty)

    expect(answer).toEqual({ ok: true, text: 'BASE INSTRUCTIONS' })
    expect(s.calls.map((c) => c.tool)).toEqual(['get_system_prompt'])
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
  })

  test('get_system_prompt fails even when the playbook loads', async () => {
    const s = server({ get_system_prompt: async () => text("ERROR: area 'OIM' does not exist.") })

    const answer = await loadInstructions(s.io, null, plenty)

    expect(answer).toEqual({ ok: false, error: "Yvoke Backend: ERROR: area 'OIM' does not exist." })
  })

  test('list_areas fails', async () => {
    const s = server({ list_areas: async () => text('boom', true) })

    expect(await loadInstructions(s.io, null, plenty)).toEqual({ ok: false, error: 'Yvoke Backend: boom' })
  })

  test('the area is not in list_areas', async () => {
    const s = server()

    const answer = await loadInstructions(s.io, { area: 'SAP', mode: 'single', playbook: null }, plenty)

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
    const left = [10_000, 3_000]
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
