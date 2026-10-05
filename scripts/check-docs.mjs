// Checks the repository's Markdown: every relative link points at a file that exists, and every
// `#anchor` on it matches a heading there. Also checks that CLAUDE.md is exactly `@AGENTS.md`.
// Usage: node scripts/check-docs.mjs   (exit code 1 on any problem)
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { join, dirname, resolve, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const skip = new Set(['.git', 'node_modules'])
const problems = []

const files = []
function walk(dir) {
  for (const name of readdirSync(dir)) {
    if (skip.has(name)) continue
    const path = join(dir, name)
    if (statSync(path).isDirectory()) walk(path)
    else if (name.endsWith('.md')) files.push(path)
  }
}
walk(root)

// GitHub's heading slug: lower case, drop punctuation except - and _, spaces to -.
function slug(heading) {
  return heading.trim().toLowerCase()
    .replace(/[`*_~]/g, (c) => (c === '_' ? '_' : ''))
    .replace(/[^\p{L}\p{N}\s_-]/gu, '')
    .replace(/\s/g, '-')
}
const anchorCache = new Map()
function anchors(file) {
  if (!anchorCache.has(file)) {
    const text = readFileSync(file, 'utf8').replace(/```[\s\S]*?```/g, '')
    anchorCache.set(file, new Set([...text.matchAll(/^#{1,6}\s+(.+)$/gm)].map((m) => slug(m[1]))))
  }
  return anchorCache.get(file)
}

for (const file of files) {
  const text = readFileSync(file, 'utf8').replace(/```[\s\S]*?```/g, '').replace(/`[^`\n]*`/g, '')
  for (const m of text.matchAll(/\]\(([^)\s]+)\)/g)) {
    const target = m[1]
    if (/^[a-z]+:/i.test(target)) continue
    const [path, anchor] = target.split('#')
    const dest = path ? resolve(dirname(file), path) : file
    const where = relative(root, file)
    if (!existsSync(dest)) { problems.push(`${where}: missing file ${target}`); continue }
    if (anchor && dest.endsWith('.md') && !anchors(dest).has(anchor)) {
      problems.push(`${where}: missing anchor ${target}`)
    }
  }
}

const claude = join(root, 'CLAUDE.md')
if (!existsSync(claude) || readFileSync(claude, 'utf8').trim() !== '@AGENTS.md') {
  problems.push('CLAUDE.md: must be exactly the line @AGENTS.md')
}

if (problems.length) {
  console.error(problems.join('\n'))
  console.error(`check-docs: ${problems.length} problem(s)`)
  process.exit(1)
}
console.log(`check-docs: OK (${files.length} Markdown files)`)
