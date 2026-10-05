// PreToolUse hook: once a bug fix's failing regression test is committed, existing tests are frozen
// until the fix is done, so the fix cannot pass by weakening a test. The freeze is on while
// .claude/state/fix-mode exists (written by the start-task skill after the regression test is committed,
// removed by finish-task). New test files may still be created. See docs/sdlc.md.
import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

const root = process.env.CLAUDE_PROJECT_DIR || process.cwd()
const marker = join(root, '.claude', 'state', 'fix-mode')

try {
  if (!existsSync(marker)) process.exit(0)
  const input = JSON.parse(readFileSync(0, 'utf8'))
  const file = input?.tool_input?.file_path || input?.tool_input?.notebook_path
  if (!file) process.exit(0)
  const path = resolve(root, file)
  const isTest = /\.test\.[cm]?[jt]sx?$/.test(path)
  if (isTest && existsSync(path)) {
    const task = readFileSync(marker, 'utf8').split('\n')[0].trim() || 'this fix'
    console.log(JSON.stringify({
      hookSpecificOutput: {
        hookEventName: 'PreToolUse',
        permissionDecision: 'deny',
        permissionDecisionReason:
          `Existing tests are frozen while the fix for ${task} is in progress (docs/sdlc.md). ` +
          'Fix the code, not the test. If the test itself is wrong, stop and ask the user; ' +
          'only they lift the freeze (delete .claude/state/fix-mode).'
      }
    }))
  }
} catch {
  // A broken guard must not block unrelated work; finish-task re-checks the diff.
}
process.exit(0)
