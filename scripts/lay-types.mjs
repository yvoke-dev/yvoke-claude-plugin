// Lays the mod API's type declarations into plugins/yvoke/.claude-plugin/types/, so `npx tsc --noEmit`
// has something to check against. Claude Code writes them only when it loads the mod from a folder;
// `claude plugin validate` and `claude plugin test` do not. From 2.1.295 on it writes them only for a folder
// the session watches, and print mode watches only with CLAUDE_CODE_PLUGIN_DIR_WATCH=1. So this starts a
// print-mode session with the plugin and watching on, points the model at an address nothing answers on
// (no sign-in, no model call, no cost), waits for the declarations to appear, and stops the session.
//
// The old types move to a temporary folder outside the repo while the session runs, so a stale file from
// an older Claude Code cannot pass for a fresh one, and come back if the run fails: a failed run never
// leaves the developer with no types. The session's debug log is read while it runs. When managed settings
// lock the plugin, Claude Code ignores --plugin-dir and never writes the declarations, so this stops at once
// and says so. Claude Code knows --debug-file from 2.1.293 at the latest; one that does not (2.1.287 may
// not) exits at once on it, and the session starts again with --debug, then with neither.
// Usage: node scripts/lay-types.mjs   (exit code 1, with the old types kept, if the declarations do not
// appear within 60 s or managed settings lock the plugin)
import { spawn } from 'node:child_process'
import {
  cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, renameSync, rmSync, statSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const plugin = join(root, 'plugins', 'yvoke')
const types = join(plugin, '.claude-plugin', 'types')
const api = join(types, 'claude-code', 'index.d.ts')
const mcpTypes = join(types, 'claude-code-mcp', 'index.d.ts')

// A throwaway folder outside the repo, for the session's config folder (so the run leaves nothing in the
// developer's own Claude Code history) and debug log, and for the old types while the session runs.
const scratch = mkdtempSync(join(tmpdir(), 'yvoke-lay-types-'))
const config = join(scratch, 'config')
const debugDir = join(config, 'debug') // where --debug writes too
const kept = join(scratch, 'types')
mkdirSync(debugDir, { recursive: true })

// Claude Code's words when it ignores the --plugin-dir copy, matched loosely to survive small rewordings.
const lockLine = /\b(?:blocked|locked)\b.{0,40}\bmanaged[ -]?settings\b/i
const managedSettings = {
  darwin: '/Library/Application Support/ClaudeCode/managed-settings.json',
  win32: 'C:\\ProgramData\\ClaudeCode\\managed-settings.json',
}[process.platform] ?? '/etc/claude-code/managed-settings.json'
// "yvoke" as a name, not as part of a path such as the repo's own.
const named = /(?<![\w/\\.-])yvoke(?![\w/\\-])/i

// The ways to ask for the debug log, tried in turn while a session exits before writing anything.
const attempts = [['--debug-file', join(debugDir, 'lay-types.txt')], ['--debug'], []]
const env = {
  ...process.env,
  CLAUDE_CONFIG_DIR: config,
  CLAUDE_CODE_PLUGIN_DIR_WATCH: '1',
  ANTHROPIC_BASE_URL: 'http://127.0.0.1:9',
  ANTHROPIC_API_KEY: 'unused',
}
// The developer's own plugin folders could load a second copy of the plugin.
delete env.CLAUDE_CODE_PLUGIN_DIRS

let child, timer, stderr = '', stopping = false
const old = existsSync(types)
process.on('SIGINT', () => stop(130, 'lay-types: interrupted'))
process.on('SIGTERM', () => stop(143, 'lay-types: stopped'))

// Aside, not deleted: stop() drops them on success and puts them back on any failure.
try {
  if (old) move(types, kept)
} catch (err) {
  stop(1, `lay-types: could not move the old types aside: ${err.message}`)
}

const started = Date.now()
launch()
timer = setInterval(check, 250)

function launch() {
  rmSync(types, { recursive: true, force: true }) // whatever an earlier attempt left
  stderr = ''
  child = spawn('claude', ['--plugin-dir', plugin, ...attempts.shift(), '-p', 'lay types'], {
    stdio: ['ignore', 'ignore', 'pipe'],
    env,
  })
  child.stderr.on('data', (chunk) => { stderr = (stderr + chunk).slice(-1_000_000) })
  child.on('error', (err) => stop(1, `lay-types: could not start claude: ${err.message}`))
  child.on('exit', () => {
    if (stopping) return
    check()
    // It quit before writing anything, most likely over a flag it does not know: try the next way.
    if (!stopping && !existsSync(api) && attempts.length > 0) launch()
  })
}

function check() {
  const file = statSync(api, { throwIfNoEntry: false })
  // The engine writes the file in one go; wait a moment after it appears so it is complete.
  if (file && file.size > 0 && Date.now() - file.mtimeMs > 500) {
    stop(0, `lay-types: OK (${api.slice(root.length + 1)})`)
  } else if (debugLines().some((line) => lockLine.test(line) && /yvoke/i.test(line))) {
    stop(1, [
      "lay-types: Claude Code's managed settings lock the yvoke plugin, so --plugin-dir is ignored and the " +
        'types cannot be laid from this folder',
      `  managed settings: ${managedSettings}`,
      '  what to do: docs/contributing.md, section 8 "When the mod does not show up"',
    ].join('\n'))
  } else if (Date.now() - started > 60_000) {
    const hint = [...new Set(debugLines().filter((line) => named.test(line))
      .map((line) => line.replace(/^\S+Z\s+/, '').trim().slice(0, 200)))].slice(0, 5)
    stop(1, [
      'lay-types: the declarations did not appear within 60 s',
      ...(hint.length ? ['  debug lines that mention yvoke:', ...hint.map((line) => `    ${line}`)] : []),
    ].join('\n'))
  }
}

// The session's debug output so far: the log files in the config folder, and stderr.
function debugLines() {
  let text = stderr
  try {
    for (const name of readdirSync(debugDir)) {
      if (name.endsWith('.txt')) text += '\n' + readFileSync(join(debugDir, name), 'utf8')
    }
  } catch {}
  return text.split('\n')
}

// Moves a folder, copying when a rename cannot (another volume). A failed copy leaves the source as it was.
function move(from, to) {
  try {
    renameSync(from, to)
    return
  } catch {}
  try {
    cpSync(from, to, { recursive: true, preserveTimestamps: true })
  } catch (err) {
    rmSync(to, { recursive: true, force: true })
    throw err
  }
  rmSync(from, { recursive: true, force: true })
}

function stop(code, message) {
  if (stopping) return
  stopping = true
  clearInterval(timer)
  child?.kill('SIGKILL')
  let note = ''
  if (code === 0) {
    if (existsSync(mcpTypes) && readFileSync(mcpTypes, 'utf8').includes('mcp__')) {
      writeFileSync(
        mcpTypes,
        "export {}\ndeclare module 'claude-code' {\n  interface McpToolInputs {}\n}\n",
      )
    }
  } else {
    // Drop whatever the session wrote and put the old types back. With old types but no copy aside, they
    // never moved and are untouched.
    try {
      if (!old || existsSync(kept)) rmSync(types, { recursive: true, force: true })
      if (existsSync(kept)) move(kept, types)
      if (old) note = '; the old types were kept'
    } catch (err) {
      note = `; putting the old types back failed (${err.message}), they are in ${kept}`
    }
  }
  try {
    rmSync(code === 0 || !existsSync(kept) ? scratch : config, { recursive: true, force: true, maxRetries: 3 })
  } catch {}
  const [head, ...rest] = message.split('\n')
  ;(code === 0 ? console.log : console.error)([head + note, ...rest].join('\n'))
  process.exit(code)
}
