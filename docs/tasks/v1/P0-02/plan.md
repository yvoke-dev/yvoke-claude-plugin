# P0-02 CI

**Release:** v1 · **Size:** M · **Type:** feature · **Status:** planned

Run every check in the [AGENTS.md verification table](../../../../AGENTS.md#verification) on every push and
pull request, on the oldest Claude Code the mod supports and on the newest release, plus once a week
against the newest release, so a mod API change shows up before a user hits it. Task entry:
[plan.md, P0-02](../plan.md#phase-0--foundation-and-spikes).

Facts below were checked in a cloud session on 2026-10-06. Claude Code is on npm as
`@anthropic-ai/claude-code`, every version from 2.1.287 to 2.1.291 included. Installed from npm into a
clean folder, with a fresh `CLAUDE_CONFIG_DIR` and no sign-in, both **2.1.287** and **2.1.289** pass all
four plugin checks on `main` (both validates, `npm run typecheck`, `claude plugin test`: 1 pass, 0 fail).

## Files that change

| File | Change |
| --- | --- |
| `.github/workflows/ci.yml` | Keep the `docs` job. Add a `plugin` job with a matrix over two Claude Code versions, `2.1.287` (the minimum, design section 2) and `latest`, `fail-fast: false` so one cannot hide the other. Steps: checkout, Node 22 with the npm cache, `npm ci`, `npm install -g @anthropic-ai/claude-code@<version>`, print `claude --version`, then the four checks one step each so the failing one is named in the run. `CLAUDE_CONFIG_DIR` points at a fresh folder under `$RUNNER_TEMP`. Triggers: `push` to `main`, `pull_request`, a weekly `schedule` (Monday 05:00 UTC) and `workflow_dispatch` for a manual run. `timeout-minutes: 10` per job. Update the header comment. |
| `AGENTS.md` | Replace "CI runs the docs check today; P0-02 adds the other rows" with what CI now runs and on which versions. One paragraph only, because P0-11 is editing the same file in parallel. |
| `docs/specs/packaging.md` | One line under the verification bullet: CI runs the same checks on the minimum and latest Claude Code, and weekly. |
| `docs/tasks/v1/plan.md` | At the end: tick P0-02 with the PR link. |

Not in this task: the user-facing version check and documenting the minimum for users (P7-07), the
contributor guide (P0-03), the local dev setup (P0-11).

## Order of work

1. **Red first.** Write the `plugin` job. On this branch, push one commit that breaks
   `plugins/yvoke/tests/scaffold.test.ts` on purpose (flip its expectation). See the `plugin` job go red on
   both versions, with the test step named as the failure.
2. **Green.** Push a `git revert` of that commit, check with `diff -q` against `main` that the test file is
   back, and see the whole workflow go green. Both commits stay in the branch history as the proof; the
   PR is squash-merged.
3. **The `calls:` rule.** The verification table says the plugin's `calls:` line must name no `$.fs`,
   `$.process` or `$.http`, but only a reader checks it. Add it to the validate step as a `grep` that fails
   the step if one of the three appears. Prove it the same way: a throwaway commit that calls `$.fs` in
   `register.tsx` turns the step red, its revert turns it green.
4. **Schedule.** Run the workflow once by hand with `workflow_dispatch` to show the non-PR triggers work.
5. **Documents** (`AGENTS.md`, `packaging.md`), then close out with `finish-task`.

## Risks

- **Which minimum version.** Design section 2 says mods need 2.1.287, and it passes every check today, so
  CI pins `2.1.287`. P7-07 later decides the minimum we promise users and moves this pin to match. The
  alternative, 2.1.289 (what the docs' facts were checked on), would let a break on 2.1.287 through.
- **A new release breaks the mod on every PR.** With `latest` blocking, every open PR goes red until the
  mod is fixed. That is the point of the task (catch it early), so I keep it blocking rather than
  `continue-on-error`, which would make a red `latest` easy to ignore. If it gets in the way, the
  fallback is to make `latest` advisory on pull requests and blocking only on the weekly run.
- **`npm install -g` versus the native installer.** The native installer (`curl … | bash`) installs only
  the newest build. npm can install any exact version, which the minimum leg needs, and it is one line.
- **Stale config ("hooks modules are turned off").** Only a developer's old `~/.claude` hits it. CI sets a
  fresh `CLAUDE_CONFIG_DIR` anyway, so a runner image that ever ships one cannot break the run.
- **`lay-types` start-up time.** It waits up to 60 s for the declarations. Locally it takes a few seconds;
  the 10-minute job timeout leaves room without letting a hang burn an hour.
- **No secrets.** None of the checks needs a sign-in or a model, so the workflow keeps
  `permissions: contents: read` and uses no secrets, which matters for a public repository.

## Proof

- **Done when** (plan.md): the run for the deliberately broken test is red and the run after its revert is
  green; both runs are linked in the PR description.
- The `calls:` grep: the red and green runs from step 3, linked the same way.
- The verification commands from `AGENTS.md`, run locally with the output in the PR: `npm run check`.
