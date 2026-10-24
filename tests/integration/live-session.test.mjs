// The integration group: the plugin in a real headless Claude Code session.
//
// `claude plugin test` runs the mod against a kit that stands in for the engine, and the kit differs from
// the real engine: in a real session `$.mcp.call` goes through the plugin's own `tool.call` hook, and the
// kit does not do that. A bug in how the hooks meet each other shows up only here. This test installs the
// plugin from the working tree into a throwaway Claude Code config, runs `claude -p` against a stub Yvoke
// MCP server and a fake Anthropic Messages API, and checks what each of them was sent.
//
// Everything is on 127.0.0.1 with ports the system picks, and nothing leaves the machine.
// Run it with `npm run test:integration`. CLAUDE_BIN names another Claude Code binary (default `claude`).
import { test, describe, before, after, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const CLAUDE = process.env.CLAUDE_BIN || 'claude'
/** Longest a single `claude` run may take. */
const RUN_TIMEOUT_MS = 100_000

const QUESTION = 'What is an OIM attestation policy?'
const STUB_BASE = 'STUB BASE INSTRUCTIONS'
const STUB_PLAYBOOK = 'STUB PLAYBOOK TEXT'
const SEPARATOR = '\n\n---\n\n' // the mod's SEPARATOR: base instructions, then the playbook
const SECTION = `${STUB_BASE}${SEPARATOR}${STUB_PLAYBOOK}`
const ANSWER = 'Stub answer.'
const GRANTED_TOOL = 'mcp__plugin_yvoke_yvoke__search_corpus'
/** A tool of the Yvoke server that the stub playbook does not grant. */
const UNGRANTED_TOOL = 'mcp__plugin_yvoke_yvoke__get_section'

// ---------------------------------------------------------------------------------------------------
// Servers

const listen = (handler) =>
  new Promise((ok) => {
    const server = createServer(handler)
    server.listen(0, '127.0.0.1', () => ok({ server, port: server.address().port }))
  })

const readBody = (req) =>
  new Promise((ok) => {
    let body = ''
    req.on('data', (c) => (body += c))
    req.on('end', () => ok(body))
  })

/** The Yvoke tools as yvoke-web answers them (streamable HTTP, JSON replies). */
const YVOKE_REPLIES = {
  list_areas: JSON.stringify([
    {
      name: 'OIM',
      title: 'OIM',
      defaultPlaybook: 'oim-full',
      defaultSystemPrompt: 'default-chat',
      systemPrompts: ['default-chat'],
      profiles: [],
    },
  ]),
  list_playbooks: JSON.stringify([{ name: 'oim-full', area: 'OIM', version: 3, tools: ['search_corpus'] }]),
  get_playbook: JSON.stringify({ name: 'oim-full', text: STUB_PLAYBOOK, version: 3, systemPrompt: null, tools: ['search_corpus'] }),
  get_system_prompt: STUB_BASE,
  search_corpus: 'STUB SEARCH RESULT',
  get_section: 'STUB SECTION', // listed so the model can call it; the stub playbook does not grant it
}

/** The stub Yvoke MCP server. `calls` logs every `tools/call` with the dev-mode header it came with. */
const startYvoke = async () => {
  const calls = []
  const { server, port } = await listen(async (req, res) => {
    const body = await readBody(req)
    if (req.method !== 'POST') return void res.writeHead(405).end()
    let msg
    try {
      msg = JSON.parse(body)
    } catch {
      return void res.writeHead(400).end()
    }
    if (msg.id === undefined) return void res.writeHead(202).end() // a notification
    let result = {}
    if (msg.method === 'initialize') {
      result = {
        protocolVersion: msg.params?.protocolVersion ?? '2025-03-26',
        capabilities: { tools: {} },
        serverInfo: { name: 'stub-yvoke', version: '0' },
      }
    } else if (msg.method === 'tools/list') {
      result = {
        tools: Object.keys(YVOKE_REPLIES).map((name) => ({
          name,
          description: name,
          inputSchema: { type: 'object', properties: {} },
        })),
      }
    } else if (msg.method === 'tools/call') {
      const name = msg.params?.name
      calls.push({ tool: name, devMode: req.headers['x-yvoke-dev-mode'] })
      result = { content: [{ type: 'text', text: YVOKE_REPLIES[name] ?? `ERROR: unknown tool ${name}` }], isError: false }
    }
    res.writeHead(200, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ jsonrpc: '2.0', id: msg.id, result }))
  })
  return { server, port, calls }
}

const sse = (events) => events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join('')

/** A streamed Messages API reply made of the given content blocks (text or tool_use). */
const streamReply = (blocks, stopReason) => {
  const events = [
    [
      'message_start',
      {
        type: 'message_start',
        message: {
          id: 'msg_stub',
          type: 'message',
          role: 'assistant',
          model: 'stub-model',
          content: [],
          stop_reason: null,
          stop_sequence: null,
          usage: { input_tokens: 1, output_tokens: 1 },
        },
      },
    ],
  ]
  blocks.forEach((block, index) => {
    if (block.type === 'text') {
      events.push(['content_block_start', { type: 'content_block_start', index, content_block: { type: 'text', text: '' } }])
      events.push([
        'content_block_delta',
        { type: 'content_block_delta', index, delta: { type: 'text_delta', text: block.text } },
      ])
    } else {
      events.push([
        'content_block_start',
        { type: 'content_block_start', index, content_block: { type: 'tool_use', id: block.id, name: block.name, input: {} } },
      ])
      events.push([
        'content_block_delta',
        { type: 'content_block_delta', index, delta: { type: 'input_json_delta', partial_json: JSON.stringify(block.input) } },
      ])
    }
    events.push(['content_block_stop', { type: 'content_block_stop', index }])
  })
  events.push([
    'message_delta',
    { type: 'message_delta', delta: { stop_reason: stopReason, stop_sequence: null }, usage: { output_tokens: 1 } },
  ])
  events.push(['message_stop', { type: 'message_stop' }])
  return sse(events)
}

const textOf = (content) =>
  typeof content === 'string'
    ? content
    : (content ?? []).map((b) => (b.type === 'text' ? b.text : b.type === 'tool_result' ? textOf(b.content) : '')).join('\n')

/** The system prompt of a request as one string. */
const systemText = (body) => textOf(body.system)

/**
 * The main loop's requests: the ones that carry the tools and the user's question. Claude Code also sends
 * side requests (a title, a quota probe) with no tools, so the first request is not assumed to be the one.
 */
const isMainLoop = (body) =>
  Array.isArray(body.tools) && body.tools.length > 0 && JSON.stringify(body.messages ?? []).includes(QUESTION)

const answer = () => ({ blocks: [{ type: 'text', text: ANSWER }], stopReason: 'end_turn' })

/**
 * The fake Anthropic API. `state.script` decides what a main-loop request gets: content blocks and a stop
 * reason. Every request is recorded. Side requests get a plain reply, streamed or not as they ask.
 */
const startAnthropic = async () => {
  const requests = []
  const state = { script: answer }
  const { server, port } = await listen(async (req, res) => {
    const raw = await readBody(req)
    if (req.method !== 'POST' || !req.url.startsWith('/v1/messages')) return void res.writeHead(404).end()
    let body
    try {
      body = JSON.parse(raw)
    } catch {
      return void res.writeHead(400).end()
    }
    requests.push(body)
    const { blocks, stopReason } = isMainLoop(body) ? state.script(body) : answer()
    if (body.stream) {
      res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' })
      return void res.end(streamReply(blocks, stopReason))
    }
    res.writeHead(200, { 'content-type': 'application/json' })
    res.end(
      JSON.stringify({
        id: 'msg_stub',
        type: 'message',
        role: 'assistant',
        model: 'stub-model',
        content: blocks,
        stop_reason: stopReason,
        stop_sequence: null,
        usage: { input_tokens: 1, output_tokens: 1 },
      }),
    )
  })
  return { server, port, requests, state }
}

// ---------------------------------------------------------------------------------------------------
// Claude Code

/** Marks a failure of the test's own setup, so the runner script can tell it from a test failure. */
class HarnessError extends Error {}

/** The `result` messages in Claude Code's stream-json output: one per prompt that ran to its end. */
const resultLines = (stdout) =>
  stdout
    .split('\n')
    .filter((line) => line.startsWith('{'))
    .flatMap((line) => {
      try {
        const message = JSON.parse(line)
        return message.type === 'result' ? [message] : []
      } catch {
        return [] // a line still being written
      }
    })

/**
 * Runs `claude` and collects what it printed. `lines` are written to its stdin one at a time, the next
 * after the previous one's `result` line has come out: sent all at once, Claude Code may fold queued
 * prompts into one turn. A run past `timeoutMs` is killed with its children and rejected.
 */
const claude = (args, { cwd, env, lines = [], timeoutMs = RUN_TIMEOUT_MS }) =>
  new Promise((ok, fail) => {
    const child = spawn(CLAUDE, args, { cwd, env, detached: true, stdio: ['pipe', 'pipe', 'pipe'] })
    let stdout = ''
    let stderr = ''
    let timedOut = false
    const killTree = () => {
      try {
        process.kill(-child.pid, 'SIGKILL')
      } catch {
        // already gone
      }
    }
    const timer = setTimeout(() => {
      timedOut = true
      killTree()
    }, timeoutMs)
    let sent = 0
    const sendNext = () => {
      if (sent < lines.length) child.stdin.write(`${lines[sent++]}\n`)
      else child.stdin.end()
    }
    let seen = 0
    child.stdout.on('data', (c) => {
      stdout += c
      const results = resultLines(stdout).length
      for (; seen < results; seen++) sendNext()
    })
    child.stderr.on('data', (c) => (stderr += c))
    child.on('error', (err) => {
      clearTimeout(timer)
      fail(new HarnessError(`could not start ${CLAUDE}: ${err.message}`))
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      killTree() // anything the session left behind
      if (timedOut) fail(new Error(`claude ${args.join(' ')} timed out after ${timeoutMs} ms\n${stdout}\n${stderr}`))
      else ok({ code, stdout, stderr })
    })
    child.stdin.on('error', () => {})
    if (lines.length === 0) child.stdin.end()
    else sendNext()
  })

/**
 * The environment the session starts from: the caller's, without anything that steers Claude Code. A test
 * run from inside a Claude Code session, or on a machine with a proxy or an API key set, would otherwise
 * change which endpoint, entry point and prompt the session under test uses.
 */
const cleanEnv = () =>
  Object.fromEntries(
    Object.entries(process.env).filter(
      ([name]) => !/^(CLAUDE|ANTHROPIC|CCR_|OTEL_)|^(HTTPS?|ALL)_PROXY$/i.test(name) && name !== 'DISABLE_AUTOUPDATER',
    ),
  )

const tail = (text, lines = 30) => text.split('\n').slice(-lines).join('\n')

describe('a headless Claude Code session with the plugin installed', () => {
  let root, yvoke, anthropic, env

  before(async () => {
    try {
      root = mkdtempSync(join(tmpdir(), 'yvoke-integration-'))
      yvoke = await startYvoke()
      anthropic = await startAnthropic()
      // Never the developer's own config. A fresh one also has no stale rollout state ("hooks modules are
      // turned off"), the same reason CI uses one.
      env = {
        ...cleanEnv(),
        CLAUDE_CONFIG_DIR: join(root, 'claude-config'),
        ANTHROPIC_BASE_URL: `http://127.0.0.1:${anthropic.port}`,
        ANTHROPIC_API_KEY: 'unused',
        NO_PROXY: '127.0.0.1,localhost',
        no_proxy: '127.0.0.1,localhost',
        DISABLE_AUTOUPDATER: '1',
        DISABLE_TELEMETRY: '1',
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1',
      }
      const version = await claude(['--version'], { cwd: root, env, timeoutMs: 30_000 })
      console.log(`# claude ${version.stdout.trim()}`)
      for (const args of [
        ['plugin', 'marketplace', 'add', './'], // `./`: a bare `.` is refused
        [
          'plugin',
          'install',
          'yvoke@yvoke',
          '--config',
          `serverUrl=http://127.0.0.1:${yvoke.port}/mcp`,
          '--config',
          'devMode=true',
        ],
      ]) {
        const out = await claude(args, { cwd: REPO, env, timeoutMs: 60_000 })
        if (out.code !== 0) throw new HarnessError(`claude ${args.join(' ')} exited ${out.code}\n${out.stdout}\n${out.stderr}`)
      }
    } catch (err) {
      console.error(`yvoke-integration: harness error: ${err.message}`)
      cleanUp() // a failed `before` may not be followed by `after`
      throw err
    }
  })

  const cleanUp = () => {
    for (const s of [yvoke, anthropic]) {
      s?.server.close()
      s?.server.closeAllConnections()
    }
    if (root) rmSync(root, { recursive: true, force: true })
  }
  after(cleanUp)

  beforeEach(() => {
    yvoke.calls.length = 0
    anthropic.requests.length = 0
    anthropic.state.script = answer
  })

  /**
   * One session in a fresh, empty working folder: each prompt is a line of stream-json input. Returns the
   * `result` lines, and `context()`, which prints what to look at when an assertion fails.
   */
  const session = async (prompts) => {
    const cwd = mkdtempSync(join(root, 'work-'))
    const debugFile = join(root, `debug-${Date.now()}.log`)
    const lines = prompts.map((content) => JSON.stringify({ type: 'user', message: { role: 'user', content } }))
    const out = await claude(
      [
        '-p',
        '--input-format',
        'stream-json',
        '--output-format',
        'stream-json',
        '--verbose',
        '--debug-file',
        debugFile,
      ],
      { cwd, env, lines },
    )
    const results = resultLines(out.stdout)
    const debug = existsSync(debugFile) ? readFileSync(debugFile, 'utf8') : '(no debug log)'
    const context = () =>
      [
        `claude exited ${out.code}`,
        'result lines:',
        ...results.map((r) => `  ${r.is_error ? '[error] ' : ''}${String(r.result).slice(0, 400)}`),
        `stub Yvoke tools/call: ${yvoke.calls.map((c) => c.tool).join(', ') || '(none)'}`,
        `fake API requests: ${anthropic.requests.length} (${anthropic.requests.filter(isMainLoop).length} main loop)`,
        'debug log tail:',
        tail(debug),
        'stderr tail:',
        tail(out.stderr, 10),
      ].join('\n')
    return { results, context }
  }

  test('a /yvoke session starts, and its question reaches the model with the Yvoke instructions', async () => {
    const { results, context } = await session(['/yvoke', QUESTION])
    const why = (message) => `${message}\n${context()}`

    assert.equal(results.length, 2, why('expected one result per prompt'))
    assert.match(String(results[0].result), /Yvoke session started/, why('/yvoke did not start a session'))

    // The bug this guards: one of the mod's hooks refuses a `$.mcp.call` of another, and the question is
    // dropped before it is sent.
    assert.doesNotMatch(String(results[1].result), /dropped by a hook/i, why('the question was dropped'))
    assert.equal(results[1].is_error, false, why('the question ended in an error'))
    assert.equal(results[1].result, ANSWER, why("the question did not get the model's answer"))

    // The setup loads the two lists at once, so their order is not fixed; both come before the question's
    // `get_playbook`, which comes before `get_system_prompt`.
    const names = yvoke.calls.map((c) => c.tool)
    const first = Object.fromEntries(
      ['list_areas', 'list_playbooks', 'get_playbook', 'get_system_prompt'].map((tool) => [tool, names.indexOf(tool)]),
    )
    for (const [tool, at] of Object.entries(first)) {
      assert.notEqual(at, -1, why(`the stub never received ${tool}; got: ${names.join(', ')}`))
    }
    assert.ok(
      Math.max(first.list_areas, first.list_playbooks) < first.get_playbook && first.get_playbook < first.get_system_prompt,
      why(`the stub's calls came in the wrong order: ${names.join(', ')}`),
    )
    for (const call of yvoke.calls) {
      assert.equal(call.devMode, 'true', why(`${call.tool} came without X-Yvoke-Dev-Mode: true`))
    }

    assert.ok(anthropic.requests.some(isMainLoop), why('no main-loop request reached the model'))
  })

  // What the model is told. `claude -p` composes the system prompt once, at start-up, so the instructions
  // that the first question loads in `prompt.submit` cannot reach it: even a plain `prompt.compose` mod
  // sees its state from before the first prompt in every later turn. The interactive REPL composes after
  // the submit hooks, so this is a limit of the headless run, not of the mod. The test stays as a marker
  // for the day a headless run can show it, and `todo` keeps its failure from failing the group.
  test(
    'the main-loop request carries the composed instructions',
    { todo: '`claude -p` composes the system prompt before the first question loads the instructions' },
    async () => {
      const { context } = await session(['/yvoke', QUESTION])
      const main = anthropic.requests.filter(isMainLoop)
      assert.ok(main.length >= 1, `no main-loop request reached the model\n${context()}`)
      assert.ok(
        systemText(main[0]).includes(SECTION),
        `the system prompt lacks the composed section ${JSON.stringify(SECTION)}\n${context()}`,
      )
    },
  )

  test('a plain session stays untouched: no Yvoke calls, no Yvoke prompt', async () => {
    const { results, context } = await session([QUESTION])
    const why = (message) => `${message}\n${context()}`

    assert.equal(results.length, 1, why('expected one result'))
    assert.equal(results[0].result, ANSWER, why("the question did not get the model's answer"))
    assert.deepEqual(yvoke.calls, [], why('the mod called the Yvoke server outside a /yvoke session'))

    const main = anthropic.requests.filter(isMainLoop)
    assert.ok(main.length >= 1, why('no main-loop request reached the model'))
    for (const body of main) {
      const system = systemText(body)
      assert.ok(!system.includes(STUB_BASE) && !system.includes(STUB_PLAYBOOK), why('a Yvoke section is in the system prompt'))
    }
  })

  // No `--allowedTools` anywhere in these runs: a `-p` session cannot show a permission dialog and refuses
  // what is not allowed, so a call that passes only because the plugin's `tool.check` hook grants it (the
  // mod's own `list_areas`, `get_playbook`, and the playbook's `search_corpus`) proves the plugin needs no
  // grant from the user (D-36).
  test('the granted Yvoke tool runs with no user grant and every other tool is refused', async () => {
    anthropic.state.script = (body) => {
      if (JSON.stringify(body.messages).includes('"tool_result"')) return answer()
      return {
        blocks: [
          { type: 'tool_use', id: 'toolu_search', name: GRANTED_TOOL, input: { query: 'attestation policy' } },
          { type: 'tool_use', id: 'toolu_bash', name: 'Bash', input: { command: 'echo hi', description: 'say hi' } },
          { type: 'tool_use', id: 'toolu_section', name: UNGRANTED_TOOL, input: { chunk_id: 'x' } },
        ],
        stopReason: 'tool_use',
      }
    }
    const { results, context } = await session(['/yvoke', QUESTION])
    const why = (message) => `${message}\n${context()}`

    assert.equal(results.length, 2, why('expected one result per prompt'))
    assert.doesNotMatch(String(results[1].result), /dropped by a hook/i, why('the question was dropped'))
    const followUp = anthropic.requests.filter(isMainLoop).find((b) => JSON.stringify(b.messages).includes('"tool_result"'))
    assert.ok(followUp, why('no request carried the tool results back to the model'))
    // Not just the last message: Claude Code may append system turns after the user turn with the results.
    const byId = Object.fromEntries(
      followUp.messages
        .flatMap((m) => (Array.isArray(m.content) ? m.content : []))
        .filter((b) => b.type === 'tool_result')
        .map((b) => [b.tool_use_id, textOf(b.content)]),
    )

    assert.ok(yvoke.calls.some((c) => c.tool === 'search_corpus'), why('the granted tool never reached the stub'))
    assert.match(byId.toolu_search ?? '', /STUB SEARCH RESULT/, why("the granted tool did not return the stub's result"))
    assert.match(
      byId.toolu_bash ?? '',
      /The tool "Bash" is not available in this application\. Use the yvoke knowledge-base tools instead\./,
      why("Bash was not refused with the plugin's text"),
    )
    // The hook grants only what the playbook names: another tool of the same server is still refused by the gate.
    assert.match(byId.toolu_section ?? '', new RegExp(`The tool "${UNGRANTED_TOOL}" is not available`), why('an ungranted server tool was not refused'))
    assert.ok(!yvoke.calls.some((c) => c.tool === 'get_section'), why('an ungranted server tool reached the stub'))
  })
})
