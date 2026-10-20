// Generates the Chat and Cowork playbook skills (P1-04): one live stub per playbook a user can pick, in
// plugins/yvoke/skills/<name>/SKILL.md, from the server's list_playbooks and list_areas tools. The stubs hold
// no playbook text (D-04); see docs/specs/skills.md.
//
// Usage: node scripts/generate-skills.mjs [--url <mcp endpoint>] [--dev-mode] [--from <file>] [--out <dir>]
//   --url       the Yvoke MCP endpoint (default http://localhost:8080/mcp)
//   --dev-mode  send X-Yvoke-Dev-Mode: true, for a local yvoke-web in mock mode (P0-11)
//   --from      read a saved { "playbooks": [...], "areas": [...] } instead of asking the server
//   --out       the skills folder (default plugins/yvoke/skills)
// A bearer token for any other server comes from the YVOKE_TOKEN environment variable. It is never printed.
import { readFileSync, writeFileSync, mkdirSync, readdirSync, rmSync, existsSync, statSync } from 'node:fs'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { planSkills } from './skills.mjs'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const USAGE = 'Usage: node scripts/generate-skills.mjs [--url <mcp endpoint>] [--dev-mode] [--from <file>] [--out <dir>]'

// Brings `dir` in line with `files` (folder name → SKILL.md text). The generator owns the folder, so a
// subfolder it did not write is removed; plain files are left alone.
export function writeSkills(dir, files) {
  mkdirSync(dir, { recursive: true })
  const report = { added: [], changed: [], removed: [] }
  for (const [name, text] of files) {
    const path = join(dir, name, 'SKILL.md')
    if (!existsSync(path)) report.added.push(name)
    else if (readFileSync(path, 'utf8') !== text) report.changed.push(name)
    else continue
    mkdirSync(join(dir, name), { recursive: true })
    writeFileSync(path, text)
  }
  for (const name of readdirSync(dir).sort()) {
    if (!files.has(name) && statSync(join(dir, name)).isDirectory()) {
      rmSync(join(dir, name), { recursive: true })
      report.removed.push(name)
    }
  }
  report.added.sort()
  report.changed.sort()
  return report
}

// Some tools answer with a JSON-encoded string; plugins/yvoke/src/server.ts unwraps it the same way.
function unwrapJsonString(text) {
  const trimmed = text.trim()
  if (!trimmed.startsWith('"')) return text
  try {
    const parsed = JSON.parse(trimmed)
    return typeof parsed === 'string' ? parsed : text
  } catch {
    return text
  }
}

// A JSON-RPC answer arrives as plain JSON or as a server-sent event stream; find the one with our id.
async function readReply(response, id) {
  const body = await response.text()
  if (!(response.headers.get('content-type') ?? '').includes('text/event-stream')) return JSON.parse(body)
  for (const event of body.split(/\r?\n\r?\n/)) {
    const data = event.split(/\r?\n/).filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trimStart()).join('\n')
    if (!data) continue
    const message = JSON.parse(data)
    if (message.id === id) return message
  }
  throw new Error(`the Yvoke server sent no answer to request ${id}`)
}

// Reads the catalogue over MCP's streamable HTTP transport: initialize, then one tools/call per tool.
export async function fetchCatalogue({ url, devMode = false, token, fetch = globalThis.fetch }) {
  const headers = { 'content-type': 'application/json', accept: 'application/json, text/event-stream' }
  if (devMode) headers['x-yvoke-dev-mode'] = 'true'
  else if (token) headers.authorization = `Bearer ${token}`
  let nextId = 1

  const post = async (method, params, notification = false) => {
    const message = { jsonrpc: '2.0', method, params, ...(notification ? {} : { id: nextId++ }) }
    let response
    try {
      response = await fetch(url, { method: 'POST', headers, body: JSON.stringify(message) })
    } catch (e) {
      throw new Error(`could not reach the Yvoke server at ${url}: ${e.cause?.code ?? e.message}`)
    }
    if (!response.ok) throw new Error(`the Yvoke server answered HTTP ${response.status} to ${method}`)
    const session = response.headers.get('mcp-session-id')
    if (session) headers['mcp-session-id'] = session
    if (notification) return null
    const reply = await readReply(response, message.id)
    if (reply.error) throw new Error(`the Yvoke server refused ${method}: ${reply.error.message}`)
    return reply.result
  }

  const callTool = async (name) => {
    const result = await post('tools/call', { name, arguments: {} })
    const text = (result?.content ?? []).filter((b) => b.type === 'text').map((b) => unwrapJsonString(b.text ?? '')).join('')
    if (result?.isError || /^(ERROR|Error):/.test(text.trim())) {
      throw new Error(`Yvoke Backend: ${text.trim() || `${name} failed`}`)
    }
    return JSON.parse(text)
  }

  await post('initialize', {
    protocolVersion: '2025-06-18',
    capabilities: {},
    clientInfo: { name: 'yvoke-generate-skills', version: '1' },
  })
  await post('notifications/initialized', undefined, true)
  return { playbooks: await callTool('list_playbooks'), areas: await callTool('list_areas') }
}

function parseArgs(argv) {
  const options = { url: 'http://localhost:8080/mcp', devMode: false, from: null, out: join(root, 'plugins/yvoke/skills') }
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]
    const value = () => {
      if (i + 1 >= argv.length) throw new Error(`${arg} needs a value\n${USAGE}`)
      return argv[++i]
    }
    if (arg === '--url') options.url = value()
    else if (arg === '--dev-mode') options.devMode = true
    else if (arg === '--from') options.from = value()
    else if (arg === '--out') options.out = resolve(value())
    else throw new Error(`unknown option ${arg}\n${USAGE}`)
  }
  return options
}

export async function main(argv, env) {
  const options = parseArgs(argv)
  const catalogue = options.from
    ? JSON.parse(readFileSync(options.from, 'utf8'))
    : await fetchCatalogue({ url: options.url, devMode: options.devMode, token: env.YVOKE_TOKEN })
  // planSkills throws on anything it cannot generate, before a single file is touched.
  const report = writeSkills(options.out, planSkills(catalogue))
  const lines = [
    ...report.added.map((n) => `added ${n}`),
    ...report.changed.map((n) => `changed ${n}`),
    ...report.removed.map((n) => `removed ${n}`),
  ]
  return lines.length ? lines.join('\n') : 'no changes'
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2), process.env).then(
    (out) => console.log(out),
    (e) => {
      console.error(`generate-skills: ${e.message}`)
      process.exitCode = 1
    },
  )
}
