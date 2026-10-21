// Tests for the skill generator's command line, file writing and MCP fetch (P1-04): node --test scripts/
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { writeSkills, fetchCatalogue } from './generate-skills.mjs'
import { GENERATED_MARKER } from './skills.mjs'

const script = fileURLToPath(new URL('./generate-skills.mjs', import.meta.url))
const fixture = fileURLToPath(new URL('./fixtures/catalogue.json', import.meta.url))
const stub = (body) => `---\nname: x\n---\n\n${GENERATED_MARKER}\n\n${body}\n`
const tempDir = () => mkdtempSync(join(tmpdir(), 'yvoke-skills-'))
const run = (args) => promisify(execFile)(process.execPath, [script, ...args]).then(
  (r) => ({ code: 0, ...r }),
  (e) => ({ code: e.code, stdout: e.stdout, stderr: e.stderr }),
)

// --- Writing the folder ---------------------------------------------------------------------------

test('adds, changes and removes stub folders, and leaves an unchanged one alone', () => {
  const dir = tempDir()
  mkdirSync(join(dir, 'same'))
  writeFileSync(join(dir, 'same', 'SKILL.md'), stub('same'))
  mkdirSync(join(dir, 'edited'))
  writeFileSync(join(dir, 'edited', 'SKILL.md'), stub('old'))
  mkdirSync(join(dir, 'gone'))
  writeFileSync(join(dir, 'gone', 'SKILL.md'), stub('gone'))
  writeFileSync(join(dir, 'notes.txt'), 'not a skill folder')

  const report = writeSkills(dir, new Map([['edited', stub('new')], ['fresh', stub('fresh')], ['same', stub('same')]]))

  assert.deepEqual(report, { added: ['fresh'], changed: ['edited'], removed: ['gone'] })
  assert.deepEqual(readdirSync(dir).sort(), ['edited', 'fresh', 'notes.txt', 'same'])
  assert.equal(readFileSync(join(dir, 'edited', 'SKILL.md'), 'utf8'), stub('new'))
  assert.equal(readFileSync(join(dir, 'fresh', 'SKILL.md'), 'utf8'), stub('fresh'))
})

// A wrong --out (such as the repository root) must not cost anyone their files.
test('never removes a folder it did not generate', () => {
  const dir = tempDir()
  mkdirSync(join(dir, 'src'))
  writeFileSync(join(dir, 'src', 'main.ts'), 'code')
  mkdirSync(join(dir, 'hand-made'))
  writeFileSync(join(dir, 'hand-made', 'SKILL.md'), '---\nname: hand-made\n---\nWritten by a person.\n')

  const report = writeSkills(dir, new Map([['a', stub('a')]]))

  assert.deepEqual(report.removed, [])
  assert.equal(readFileSync(join(dir, 'src', 'main.ts'), 'utf8'), 'code')
  assert.ok(existsSync(join(dir, 'hand-made', 'SKILL.md')))
})

test('refuses to overwrite a SKILL.md it did not generate, and writes nothing', () => {
  const dir = tempDir()
  mkdirSync(join(dir, 'oim-full'))
  writeFileSync(join(dir, 'oim-full', 'SKILL.md'), 'Written by a person.\n')
  assert.throws(() => writeSkills(dir, new Map([['aaa', stub('a')], ['oim-full', stub('b')]])), /oim-full.*not generated/)
  assert.equal(readFileSync(join(dir, 'oim-full', 'SKILL.md'), 'utf8'), 'Written by a person.\n')
  assert.equal(existsSync(join(dir, 'aaa')), false)
})

test('creates the folder when it does not exist yet', () => {
  const dir = join(tempDir(), 'skills')
  assert.deepEqual(writeSkills(dir, new Map([['a', stub('a')]])), { added: ['a'], changed: [], removed: [] })
})

// --- The command line, from a saved catalogue -----------------------------------------------------

test('a second run with the same catalogue changes nothing', async () => {
  const out = join(tempDir(), 'skills')
  const first = await run(['--from', fixture, '--out', out])
  assert.equal(first.code, 0, first.stderr)
  assert.equal(first.stdout, 'added oim-db-history\nadded oim-full\n')
  assert.deepEqual(readdirSync(out).sort(), ['oim-db-history', 'oim-full'])

  const second = await run(['--from', fixture, '--out', out])
  assert.equal(second.code, 0, second.stderr)
  assert.equal(second.stdout, 'no changes\n')
})

test('a catalogue with nothing to generate stops the run and leaves the folder as it was', async () => {
  const out = tempDir()
  mkdirSync(join(out, 'oim-full'))
  writeFileSync(join(out, 'oim-full', 'SKILL.md'), 'kept\n')
  const empty = join(tempDir(), 'empty.json')
  writeFileSync(empty, JSON.stringify({ playbooks: [], areas: [] }))

  const r = await run(['--from', empty, '--out', out])
  assert.equal(r.code, 1)
  assert.match(r.stderr, /^generate-skills: the server listed no playbooks/)
  assert.equal(readFileSync(join(out, 'oim-full', 'SKILL.md'), 'utf8'), 'kept\n')
})

test('an unknown option is refused with the usage', async () => {
  const r = await run(['--frm', fixture])
  assert.equal(r.code, 1)
  assert.match(r.stderr, /unknown option --frm/)
  assert.match(r.stderr, /Usage: node scripts\/generate-skills\.mjs/)
})

// --- Fetching over MCP ----------------------------------------------------------------------------

const catalogue = JSON.parse(readFileSync(fixture, 'utf8'))

// A minimal streamable-HTTP MCP server: answers initialize, accepts the initialized notification, and
// answers tools/call for list_playbooks and list_areas. `answer` can override one tool's text.
async function mcpServer({ sse = false, answer = {}, status = 200, stall = false } = {}) {
  const seen = []
  const server = createServer((req, res) => {
    let body = ''
    req.on('data', (c) => { body += c })
    req.on('end', () => {
      const msg = JSON.parse(body)
      seen.push({ method: msg.method, tool: msg.params?.name, headers: req.headers })
      if (stall) return // accepts the request and never answers
      if (status !== 200) { res.writeHead(status).end('denied'); return }
      if (msg.id === undefined) { res.writeHead(202).end(); return }
      let result
      if (msg.method === 'initialize') {
        result = { protocolVersion: '2025-06-18', capabilities: { tools: {} }, serverInfo: { name: 'Yvoke', version: 'test' } }
      } else {
        const tool = msg.params.name
        const text = answer[tool] ?? JSON.stringify(tool === 'list_playbooks' ? catalogue.playbooks : catalogue.areas)
        result = { content: [{ type: 'text', text }], isError: false }
      }
      const reply = JSON.stringify({ jsonrpc: '2.0', id: msg.id, result })
      const headers = msg.method === 'initialize' ? { 'mcp-session-id': 'session-1' } : {}
      // A server may send other messages on the stream first, such as a log notification.
      const log = JSON.stringify({ jsonrpc: '2.0', method: 'notifications/message', params: { level: 'info', data: 'hi' } })
      if (sse) res.writeHead(200, { ...headers, 'content-type': 'text/event-stream' }).end(`event: message\ndata: ${log}\n\nevent: message\ndata: ${reply}\n\n`)
      else res.writeHead(200, { ...headers, 'content-type': 'application/json' }).end(reply)
    })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const url = `http://127.0.0.1:${server.address().port}/mcp`
  return { url, seen, close: () => new Promise((resolve) => { server.closeAllConnections(); server.close(resolve) }) }
}

for (const sse of [false, true]) {
  test(`reads both tools over MCP (${sse ? 'SSE' : 'JSON'} answers), carrying the session`, async () => {
    const s = await mcpServer({ sse })
    try {
      const got = await fetchCatalogue({ url: s.url })
      assert.deepEqual(got, { playbooks: catalogue.playbooks, areas: catalogue.areas })
      assert.deepEqual(s.seen.map((r) => r.tool ?? r.method), ['initialize', 'notifications/initialized', 'list_playbooks', 'list_areas'])
      assert.equal(s.seen[0].headers['mcp-session-id'], undefined)
      for (const r of s.seen.slice(1)) assert.equal(r.headers['mcp-session-id'], 'session-1')
      assert.match(s.seen[0].headers.accept, /application\/json/)
      assert.match(s.seen[0].headers.accept, /text\/event-stream/)
    } finally { await s.close() }
  })
}

test('sends the dev-mode header when asked, and no authorization', async () => {
  const s = await mcpServer()
  try {
    await fetchCatalogue({ url: s.url, devMode: true })
    for (const r of s.seen) {
      assert.equal(r.headers['x-yvoke-dev-mode'], 'true')
      assert.equal(r.headers.authorization, undefined)
    }
  } finally { await s.close() }
})

test('sends a bearer token when given one, and no dev-mode header', async () => {
  const s = await mcpServer()
  try {
    await fetchCatalogue({ url: s.url, token: 'secret-token' })
    for (const r of s.seen) {
      assert.equal(r.headers.authorization, 'Bearer secret-token')
      assert.equal(r.headers['x-yvoke-dev-mode'], undefined)
    }
  } finally { await s.close() }
})

test('an HTTP error is reported without the token', async () => {
  const s = await mcpServer({ status: 401 })
  try {
    await assert.rejects(fetchCatalogue({ url: s.url, token: 'secret-token' }), (e) => {
      assert.match(e.message, /HTTP 401/)
      assert.ok(!e.message.includes('secret-token'))
      return true
    })
  } finally { await s.close() }
})

test('a server that accepts the request and never answers times out', async () => {
  const s = await mcpServer({ stall: true })
  try {
    const started = Date.now()
    await assert.rejects(fetchCatalogue({ url: s.url, timeoutMs: 200 }), /did not answer initialize within 0\.2 s/)
    assert.ok(Date.now() - started < 5000)
  } finally { await s.close() }
})

test('an ERROR: answer from a tool fails the fetch with the server\'s text', async () => {
  const s = await mcpServer({ answer: { list_areas: 'ERROR: something broke.' } })
  try {
    await assert.rejects(fetchCatalogue({ url: s.url }), /Yvoke Backend: ERROR: something broke\./)
  } finally { await s.close() }
})

test('an answer wrapped in a JSON string is unwrapped, as the server client does', async () => {
  const s = await mcpServer({ answer: { list_areas: JSON.stringify(JSON.stringify(catalogue.areas)) } })
  try {
    assert.deepEqual((await fetchCatalogue({ url: s.url })).areas, catalogue.areas)
  } finally { await s.close() }
})

test('a server that is down stops the run, and the folder stays as it was', async () => {
  const out = tempDir()
  const s = await mcpServer()
  await s.close()
  const r = await run(['--url', s.url, '--out', out])
  assert.equal(r.code, 1)
  assert.match(r.stderr, /^generate-skills: /)
  assert.equal(existsSync(join(out, 'oim-full')), false)
})
