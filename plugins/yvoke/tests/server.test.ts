import { test, expect, describe } from 'claude-code/testing'
import type { McpConnectResult, McpToolResult } from 'claude-code'
import { callYvoke, SERVER, TIMEOUT_MS, type ServerIo } from '../src/server'

// A test's own `$` has no `mcp` noun, and a test's hook may not call `$.mcp.call`, so these tests hand
// the client plain functions in place of `$` (P1-03 plan, "Tests run without the engine's `$`").

const text = (body: string, isError = false): McpToolResult => ({ content: [{ type: 'text', text: body }], isError })
const never = <T,>() => new Promise<T>(() => {})

type Fake = {
  io: ServerIo
  calls: { server: string; tool: string; args: Record<string, unknown> }[]
  connects: string[]
  sleeps: { ms: number; signal: AbortSignal }[]
  fireTimer: () => void
}

const fake = (answer: {
  connect?: () => Promise<McpConnectResult>
  call?: () => Promise<McpToolResult>
}): Fake => {
  const calls: Fake['calls'] = []
  const connects: string[] = []
  const sleeps: Fake['sleeps'] = []
  let fire = () => {}
  const timer = new Promise<void>((resolve) => {
    fire = resolve
  })
  const io: ServerIo = {
    connect: (server) => {
      connects.push(server)
      return answer.connect ? answer.connect() : Promise.resolve({ isConnected: true, server: 'plugin:yvoke:yvoke' })
    },
    call: (server, tool, args) => {
      calls.push({ server, tool, args })
      return answer.call ? answer.call() : Promise.resolve(text('the answer'))
    },
    sleep: (ms, signal) => {
      sleeps.push({ ms, signal })
      return timer
    },
  }
  return { io, calls, connects, sleeps, fireTimer: () => fire() }
}

describe('success', () => {
  test('returns the text and calls the server connect named', async () => {
    const f = fake({})

    const answer = await callYvoke(f.io, 'get_playbook', { name: 'oim-full' })

    expect(answer).toEqual({ ok: true, text: 'the answer' })
    expect(f.connects).toEqual([SERVER])
    expect(SERVER).toBe('yvoke')
    expect(f.calls).toEqual([{ server: 'plugin:yvoke:yvoke', tool: 'get_playbook', args: { name: 'oim-full' } }])
  })

  test('joins the text blocks, skips other blocks and unwraps a JSON string body', async () => {
    const f = fake({
      call: async () => ({
        content: [
          { type: 'text', text: '"first\\nline"' },
          { type: 'image' },
          { type: 'text', text: 'second' },
        ],
        isError: false,
      }),
    })

    expect(await callYvoke(f.io, 'get_toc')).toEqual({ ok: true, text: 'first\nline\n\nsecond' })
  })

  test('a body that mentions ERROR: after its start is still an answer', async () => {
    const f = fake({ call: async () => text('Section 4\nERROR: codes are listed below.') })

    expect(await callYvoke(f.io, 'get_section')).toEqual({ ok: true, text: 'Section 4\nERROR: codes are listed below.' })
  })

  test('ends its timer once the server has answered', async () => {
    const f = fake({})

    await callYvoke(f.io, 'get_toc')

    expect(f.sleeps).toHaveLength(1)
    expect(f.sleeps[0]!.signal.aborted).toBe(true)
  })
})

describe('server name', () => {
  test('calls the server under the name the engine gives it', async () => {
    const f = fake({ connect: async () => ({ isConnected: true, server: 'yvoke-prod' }) })

    await callYvoke(f.io, 'get_toc')

    expect(f.calls.map((c) => c.server)).toEqual(['yvoke-prod'])
  })
})

describe('not connected', () => {
  test('reports the engine sentence and never calls', async () => {
    const f = fake({
      connect: async () => ({ isConnected: false, reason: 'failed', message: 'The server yvoke did not connect.' }),
    })

    expect(await callYvoke(f.io, 'get_toc')).toEqual({
      ok: false,
      error: 'Yvoke Backend: The server yvoke did not connect.',
    })
    expect(f.calls).toEqual([])
  })

  test('adds the sign-in hint only when the server needs a sign-in', async () => {
    const f = fake({
      connect: async () => ({ isConnected: false, reason: 'auth', message: 'The server yvoke needs a sign-in.' }),
    })

    expect(await callYvoke(f.io, 'get_toc')).toEqual({
      ok: false,
      error: 'Yvoke Backend: The server yvoke needs a sign-in. Run /mcp and sign in to yvoke.',
    })
    expect(f.calls).toEqual([])
  })
})

describe('error flag', () => {
  test('a flagged result is a failure carrying its message', async () => {
    const f = fake({ call: async () => text('Tool get_toc failed: bad input', true) })

    expect(await callYvoke(f.io, 'get_toc')).toEqual({
      ok: false,
      error: 'Yvoke Backend: Tool get_toc failed: bad input',
    })
  })

  test('a flagged result with no text names the tool', async () => {
    const f = fake({ call: async () => ({ content: [], isError: true }) })

    expect(await callYvoke(f.io, 'get_toc')).toEqual({ ok: false, error: 'Yvoke Backend: the get_toc call failed.' })
  })
})

describe('ERROR: body', () => {
  for (const body of [
    "ERROR: playbook 'x' not found.",
    "  Error: playbook 'x' not found.",
    JSON.stringify("ERROR: playbook 'x' not found."),
  ]) {
    test(`an unflagged body ${JSON.stringify(body)} is a failure`, async () => {
      const f = fake({ call: async () => text(body) })

      const answer = await callYvoke(f.io, 'get_playbook', { name: 'x' })

      expect(answer.ok).toBe(false)
      expect(answer).toEqual({ ok: false, error: expect.stringMatching(/^Yvoke Backend: (ERROR|Error): playbook 'x' not found\.$/) })
    })
  }
})

describe('timeout', () => {
  test('a server that does not answer fails once the timer fires', async () => {
    const f = fake({ call: never })

    const pending = callYvoke(f.io, 'get_toc')
    await Promise.resolve()
    f.fireTimer()

    expect(await pending).toEqual({
      ok: false,
      error: 'Yvoke Backend: the server did not answer get_toc within 8 seconds.',
    })
    expect(f.sleeps.map((s) => s.ms)).toEqual([TIMEOUT_MS])
    expect(TIMEOUT_MS).toBe(8_000)
  })

  test('the timeout also covers a connect that never answers', async () => {
    const f = fake({ connect: never })

    const pending = callYvoke(f.io, 'get_toc')
    await Promise.resolve()
    f.fireTimer()

    expect(await pending).toEqual({
      ok: false,
      error: 'Yvoke Backend: the server did not answer get_toc within 8 seconds.',
    })
    expect(f.calls).toEqual([])
  })

  test("a caller's own timeout reaches the timer and the message", async () => {
    const f = fake({ call: never })

    const pending = callYvoke(f.io, 'get_section', {}, { timeoutMs: 2_500 })
    await Promise.resolve()
    f.fireTimer()

    expect(await pending).toEqual({
      ok: false,
      error: 'Yvoke Backend: the server did not answer get_section within 2.5 seconds.',
    })
    expect(f.sleeps.map((s) => s.ms)).toEqual([2_500])
  })
})

describe('a call that rejects', () => {
  test('becomes a failure with its message', async () => {
    const f = fake({ call: () => Promise.reject(new Error('connection reset')) })

    expect(await callYvoke(f.io, 'get_toc')).toEqual({ ok: false, error: 'Yvoke Backend: connection reset' })
  })

  test('is not prefixed twice', async () => {
    const f = fake({ connect: () => Promise.reject(new Error('Yvoke Backend: already said')) })

    expect(await callYvoke(f.io, 'get_toc')).toEqual({ ok: false, error: 'Yvoke Backend: already said' })
  })

  test('drops a prefix that is already there more than once', async () => {
    const f = fake({ call: () => Promise.reject(new Error('Yvoke Backend: Yvoke Backend: said twice')) })

    expect(await callYvoke(f.io, 'get_toc')).toEqual({ ok: false, error: 'Yvoke Backend: said twice' })
  })

  test('with no message still says something', async () => {
    const f = fake({ call: () => Promise.reject(undefined) })

    expect(await callYvoke(f.io, 'get_toc')).toEqual({ ok: false, error: 'Yvoke Backend: the get_toc call failed.' })
  })
})
