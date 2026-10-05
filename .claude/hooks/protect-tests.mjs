// PreToolUse hook: once a bug fix's failing regression test is committed, existing tests are frozen
// until the fix is done, so the fix cannot pass by weakening a test. The freeze is on while a task plan
// (docs/tasks/<release>/<ID>/plan.md) has **Status:** in progress and a **Regression test:** commit.
// start-task writes that line, finish-task sets the status to done. The plan is committed, so the
// freeze holds in every session and checkout of the branch. Frozen are existing *.test.* files and every
// existing file under a tests/ folder (helpers, stubs, fixtures). New files may still be created.
// See docs/sdlc.md.
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join, relative, resolve, sep } from 'node:path'

const root = process.env.CLAUDE_PROJECT_DIR || process.cwd()

// The task IDs whose plan is in progress with a regression-test commit.
function frozenTasks() {
  const tasks = join(root, 'docs', 'tasks')
  const found = []
  if (!existsSync(tasks)) return found
  for (const release of readdirSync(tasks, { withFileTypes: true })) {
    if (!release.isDirectory()) continue
    for (const task of readdirSync(join(tasks, release.name), { withFileTypes: true })) {
      const plan = join(tasks, release.name, task.name, 'plan.md')
      if (!task.isDirectory() || !existsSync(plan)) continue
      const text = readFileSync(plan, 'utf8')
      if (/\*\*Status:\*\*\s*in progress/i.test(text) && /\*\*Regression test:\*\*\s*`?[0-9a-f]{7,40}/i.test(text)) {
        found.push(task.name)
      }
    }
  }
  return found
}

try {
  const input = JSON.parse(readFileSync(0, 'utf8'))
  const file = input?.tool_input?.file_path || input?.tool_input?.notebook_path
  if (!file) process.exit(0)
  const path = resolve(root, file)
  const isTest = /\.test\.[cm]?[jt]sx?$/.test(path) || relative(root, path).split(sep).includes('tests')
  if (!isTest || !existsSync(path)) process.exit(0)
  const frozen = frozenTasks()
  if (frozen.length) {
    console.log(JSON.stringify({
      hookSpecificOutput: {
        hookEventName: 'PreToolUse',
        permissionDecision: 'deny',
        permissionDecisionReason:
          `Existing tests are frozen while the fix for ${frozen.join(', ')} is in progress (docs/sdlc.md). ` +
          'Fix the code, not the test. If the test itself is wrong, stop and ask the user; ' +
          'only they lift the freeze.'
      }
    }))
  }
} catch {
  // A broken guard must not block unrelated work; finish-task re-checks the diff.
}
process.exit(0)
