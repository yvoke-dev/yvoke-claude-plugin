# Rules for agents working in this repository

You are building **Yvoke for Claude**, a Claude plugin with a Claude Code mod. Read these before any change.

## Where to start

- How work flows here: [docs/sdlc.md](docs/sdlc.md). Start a task with the `start-task` skill and close it
  with `finish-task`.
- The current release: [docs/tasks/v1/](docs/tasks/v1/plan.md). Read its `design.md` section 4 ("Notes for
  implementers") before you pick up a task.
- What is already built: [docs/specs/](docs/specs/README.md).
- Reference implementation: [`yvoke-dev/yvoke-desktop`](https://github.com/yvoke-dev/yvoke-desktop). Its
  `spec/` says what a feature is for; its `tests/` say exactly how it behaves. Server:
  [`yvoke-dev/yvoke-web`](https://github.com/yvoke-dev/yvoke-web).

## Rules

- **Tests first.** Red, green, refactor. A test does not count until you have seen it fail.
- **A bug gets a failing regression test before its fix.** Commit the test first; then the test freeze is
  on (docs/sdlc.md) and you fix the code, never the test. If the test is wrong, stop and ask.
- **Smallest change at the root cause.** Make the bug unrepresentable rather than documenting it.
- **Never weaken verification**: do not skip, disable or delete a failing test, and do not loosen a check
  to get green.
- **This repository is public.** Never commit playbook text, secrets, tokens, customer data or internal
  hostnames.
- **Never log tokens or full answers** (`$.ui.log` output ends up in support tickets).
- **Decisions are the product owner's.** When a choice changes what the user gets, raise it as a decision
  with a recommendation, one at a time, and record the answer in the release's `design.md`.
- **Keep documents true.** If the work differs from the task plan, update the task's `plan.md` in the same
  commit. If it shows `requirements.md` or `design.md` wrong, fix them in the same pull request. Update
  `docs/specs/` in the pull request that delivers the behaviour.
- Write plainly: short sentences, active voice, no jargon a consultant would not know.

## Verification

Run these before saying anything is done, and show the output. Each must pass.

| Check | Command | Healthy result |
| --- | --- | --- |
| Docs and links | `node scripts/check-docs.mjs` | `check-docs: OK (N Markdown files)` |
| Marketplace manifest | `claude plugin validate .` | exits 0, no errors (from P0-01) |
| Plugin and mod | `claude plugin validate plugins/yvoke` | exits 0; `calls:` lists no `$.fs`, `$.process` or `$.http` (from P0-01) |
| Types | `npx tsc --noEmit` | no output, exits 0 (from P0-01) |
| Tests | `claude plugin test plugins/yvoke` | every test passes, none skipped (from P0-01) |

Rows marked "from P0-01" apply once the plugin scaffold exists. P0-01 fills in any missing detail, and
CI (`.github/workflows/ci.yml`) runs the docs
check today; P0-02 adds the other rows.
