---
name: finish-task
description: Close out a task before review. Runs every verification command, updates the specs, ticks the release plan, and lifts the test freeze. Use when a task's code is written and its tests pass.
---

# Finish a task

Follow [docs/sdlc.md](../../../docs/sdlc.md), step 5. The argument is the task ID.

1. **Verify.** Run every command in `AGENTS.md`'s verification table that applies today. Each must pass.
   Paste the output into the pull request. If one fails, fix it and run all of them again.
2. **Check the task plan.** `docs/tasks/<release>/<ID>/plan.md` must match what was done; update it if not.
   Set its **Status** to `done`.
3. **Bug fix only:** take the regression-test commit from the second line of `.claude/state/fix-mode` and
   run `git diff --stat --diff-filter=a <commit> -- '*.test.*'` (lower-case `a`: everything except added
   files, compared with the working tree). It must print nothing: no existing test was changed, deleted
   or renamed after that commit, committed or not, including through shell commands the hook cannot see.
   Then delete `.claude/state/fix-mode`.
4. **Update the specs.** Add or change the `docs/specs/` files for the behaviour this task delivered
   (Behaviour, Interfaces, Tests, History). A spike changes no spec; its `findings.md` is enough.
5. **Update the release documents.** Tick the task in the release `plan.md` with the pull request link.
   If the work showed `requirements.md` or `design.md` wrong, fix them, and say so in the pull request.
6. **Run `node scripts/check-docs.mjs` again**, commit, and push.
7. When CI is green, mark the pull request ready for review and tell the product owner in one line what
   to look at. Never merge.
