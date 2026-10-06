// Lays the mod API's type declarations into plugins/yvoke/.claude-plugin/types/, so `npx tsc --noEmit`
// has something to check against. Claude Code writes them only when it loads the mod from a folder;
// `claude plugin validate` and `claude plugin test` do not. So this starts a print-mode session with the
// plugin, points the model at an address nothing answers on (no sign-in, no model call, no cost), waits
// for the declarations to appear, and stops the session.
// Usage: node scripts/lay-types.mjs   (exit code 1 if the declarations do not appear within 60 s)
import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const plugin = join(root, 'plugins', 'yvoke')
const types = join(plugin, '.claude-plugin', 'types')
const api = join(types, 'claude-code', 'index.d.ts')

// Start clean, so a stale file from an older Claude Code cannot pass for a fresh one.
rmSync(types, { recursive: true, force: true })

// A throwaway config folder, so the run leaves nothing in the developer's own Claude Code history.
const config = mkdtempSync(join(tmpdir(), 'yvoke-lay-types-'))

const child = spawn('claude', ['--plugin-dir', plugin, '-p', 'lay types'], {
  stdio: 'ignore',
  env: { ...process.env, CLAUDE_CONFIG_DIR: config, ANTHROPIC_BASE_URL: 'http://127.0.0.1:9', ANTHROPIC_API_KEY: 'unused' },
})
child.on('error', (err) => stop(1, `lay-types: could not start claude: ${err.message}`))
process.on('SIGINT', () => stop(130, 'lay-types: interrupted'))
process.on('SIGTERM', () => stop(143, 'lay-types: stopped'))

const started = Date.now()
const timer = setInterval(() => {
  // The engine writes the file in one go; wait a moment after it appears so it is complete.
  if (existsSync(api) && statSync(api).size > 0 && Date.now() - statSync(api).mtimeMs > 500) {
    stop(0, `lay-types: OK (${api.slice(root.length + 1)})`)
  } else if (Date.now() - started > 60_000) {
    stop(1, 'lay-types: the declarations did not appear within 60 s')
  }
}, 250)

function stop(code, message) {
  clearInterval(timer)
  child.kill('SIGKILL')
  rmSync(config, { recursive: true, force: true })
  ;(code === 0 ? console.log : console.error)(message)
  process.exit(code)
}
