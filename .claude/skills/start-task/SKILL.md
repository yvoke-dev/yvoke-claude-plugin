---
name: start-task
description: Start work on a task from a release plan (an ID like P1-08, or a bug fix). Creates the task folder and its plan.md, and turns on the test freeze for bug fixes. Use before writing any code for a task.
---

# Start a task

Follow [docs/sdlc.md](../../../docs/sdlc.md), step 3. The argument is a task ID (`P1-08`), or `fix:` and a
short description for a bug outside the plan.

1. **Find the task.** Read its entry in `docs/tasks/<release>/plan.md` (the current release is the newest
   folder that still has unticked boxes). Check its `⛔` blockers are ticked; if not, stop and say which
   are open. For a bug, give it the next free ID in the phase it belongs to and add it to `plan.md`.
2. **Read the context**: the release's `requirements.md` lines that name the task, `design.md` (always
   section 4, "Notes for implementers", plus any section the task links), `AGENTS.md`, and the specs in
   `docs/specs/` the task touches. For a ported feature, read the yvoke-desktop source and tests the
   design names.
3. **Write `docs/tasks/<release>/<ID>/plan.md`** in plan mode, without editing code:

   ```markdown
   # <ID> <task title>

   **Release:** v1 · **Size:** S/M/L · **Type:** feature | bug fix | spike · **Status:** planned

   ## Files that change
   ## Order of work
   ## Risks
   ## Proof
   ```

   *Order of work* lists small steps with the test written first in each. *Risks* includes the
   alternatives considered and why they were rejected. *Proof* names the tests that show the task's
   **Done when** and the verification commands from `AGENTS.md`. A spike's plan says what will be tried,
   on which surface and Claude Code version, and its findings go in `findings.md` next to it.
4. **Open a draft pull request** with the plan as its first commit, one task per branch.
5. **Approval.** Ask the product owner to approve the plan in the pull request, and wait. This holds for
   every task, whatever its size.
6. **Bug fix only:** write the failing regression test, run it to see it fail, and commit it on its own.
   Then add the line `**Regression test:** <that commit's hash>` under the plan's **Release** line.
7. Set the plan's **Status** to `in progress`, commit the plan, and start the first step of *Order of work*.
   For a bug fix, the test freeze is now on: while a plan is in progress with a **Regression test** line,
   the hook refuses edits to existing test files, in every session and checkout of the branch.
