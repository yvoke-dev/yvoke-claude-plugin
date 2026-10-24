// Checks the repository's Markdown: every relative link points at a file that exists, and every
// `#anchor` on it matches a heading there. Checks the code and docs for IDs too: every decision they cite
// has its entry in .lintel/steering/decisions.md, and no old task ID is left.
// Usage: node scripts/check-docs.mjs   (exit code 1 on any problem)
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { join, dirname, resolve, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const skip = new Set(['.git', 'node_modules', '.agents'])
const skipLintel = new Set(['tasks', 'personas', 'bin', 'state', 'scripts'])
const problems = []

const files = []
function walk(dir, inLintel = false) {
  for (const name of readdirSync(dir)) {
    if (skip.has(name)) continue
    if (inLintel && skipLintel.has(name)) continue
    const path = join(dir, name)
    if (statSync(path).isDirectory()) walk(path, inLintel || name === '.lintel')
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

// Decision IDs (D-<nn>) must have a `### D-<nn>` entry in decisions.md. Task IDs (P<digit>-<nn>) belong to
// the old plan: cite the spec or PLAN.md instead. decisions.md may name the next free number before its
// entry exists.
const decisionsFile = join(root, '.lintel', 'steering', 'decisions.md')
const decided = new Set(existsSync(decisionsFile)
  ? [...readFileSync(decisionsFile, 'utf8').matchAll(/^### (D-\d\d)\b/gm)].map((m) => m[1])
  : [])
const nextFree = `D-${String(Math.max(0, ...[...decided].map((id) => Number(id.slice(2)))) + 1).padStart(2, '0')}`
const idRoots = ['plugins', '.github', 'scripts', 'docs', '.lintel/specs', '.lintel/steering', '.agents/skills', 'README.md', 'PLAN.md']
const idSkip = ['plugins/yvoke/.claude-plugin/types', '.agents/plugins', '.lintel/personas']
const idFiles = []
function walkIds(path) {
  const rel = relative(root, path).split(sep).join('/')
  if (rel.split('/').includes('node_modules') || idSkip.some((s) => rel === s || rel.startsWith(`${s}/`))) return
  if (!existsSync(path)) return
  if (statSync(path).isDirectory()) for (const name of readdirSync(path)) walkIds(join(path, name))
  else if (/\.(ts|tsx|mjs|json|yml|md)$/.test(path)) idFiles.push(path)
}
for (const r of idRoots) walkIds(join(root, r))

for (const file of idFiles) {
  const where = relative(root, file)
  readFileSync(file, 'utf8').split('\n').forEach((line, i) => {
    for (const [id] of line.matchAll(/\bD-\d\d\b/g)) {
      if (decided.has(id) || (file === decisionsFile && id === nextFree)) continue
      problems.push(`${where}:${i + 1}: ${id} has no entry in .lintel/steering/decisions.md`)
    }
    for (const [id] of line.matchAll(/\bP\d-\d\d\b/g)) {
      problems.push(`${where}:${i + 1}: task ID ${id}; cite the spec or PLAN.md instead`)
    }
  })
}

if (problems.length) {
  console.error(problems.join('\n'))
  console.error(`check-docs: ${problems.length} problem(s)`)
  process.exit(1)
}
console.log(`check-docs: OK (${files.length} Markdown files, ${idFiles.length} files checked for IDs)`)
