# P0-13 Type declarations on Claude Code 2.1.304

**Release:** v1 · **Size:** S · **Type:** bug fix · **Status:** done

**Regression test:** none, by exception: `scripts/` has no test kit. The failing CI run is the proof:
[plugin (Claude Code latest) on the plan commit](https://github.com/yvoke-dev/yvoke-claude-plugin/actions/runs/39470560537/job/118793203056).

`npm run typecheck` fails on Claude Code 2.1.304: `scripts/lay-types.mjs` prints
`lay-types: the declarations did not appear within 60 s`. So the CI job "plugin (Claude Code latest)"
goes red on `main` at its next run (the Monday 05:00 UTC run included) and on every open pull request.
Found in [#20](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/20#issuecomment-6297350313). Task
entry: [plan.md, P0-13](../plan.md#phase-0--foundation-and-spikes).

## Cause

Checked in a cloud session on 2026-10-20, with a fresh `CLAUDE_CONFIG_DIR` and no sign-in, the way the
script runs.

- On 2.1.304 the mod still loads in the script's print-mode run (the debug log says
  `hooks module yvoke@inline loaded`), but Claude Code no longer writes `.claude-plugin/types/`.
- Its plugin-authoring reference says why: the engine lays the declarations only for a folder the session
  **hot-reloads**. "A folder the session loads from and does not hot-reload (a `--plugin-dir` under
  `claude -p` or an SDK host, an installed plugin's) is never written in." The change came in **2.1.295**:
  of the releases tested, 2.1.287, 2.1.293 and 2.1.294 still write them under `-p`, and every release
  from 2.1.295 to 2.1.304 does not (table below). CI's `latest` row first met it on 2.1.304.
- The same reference names the switch that makes a headless session watch its plugin folders:
  `CLAUDE_CODE_PLUGIN_DIR_WATCH=1` ("a long-lived headless session (SDK, desktop) watches too when
  `CLAUDE_CODE_PLUGIN_DIR_WATCH=1` is set"). It is the same variable P0-03 documents for the Desktop
  Code tab.

Tried by hand, same script environment plus that one variable, each release installed from npm
(2.1.297 and 2.1.298 were never published):

| Claude Code | Without the variable | With `CLAUDE_CODE_PLUGIN_DIR_WATCH=1` |
| --- | --- | --- |
| 2.1.287 | declarations written | declarations written |
| 2.1.293, 2.1.294 | declarations written | declarations written |
| 2.1.295, 2.1.296, 2.1.299 to 2.1.302 | nothing within 40 s | declarations written |
| 2.1.303 | nothing within 40 s | declarations written |
| 2.1.304 | nothing within 60 s | declarations written, `tsc --noEmit` clean |

## Files that change

| File | Change |
| --- | --- |
| `scripts/lay-types.mjs` | Add `CLAUDE_CODE_PLUGIN_DIR_WATCH: '1'` to the child's environment. Update the header comment: the engine writes the declarations only for a folder it watches, so the script asks print mode to watch. |
| `AGENTS.md` | Verification paragraph: say the script runs print mode with folder watching on, and why. |
| `docs/tasks/v1/design.md` | Section 4.2, the bullet that says `npm run types` "loads the mod once in print mode": same correction. |
| `docs/tasks/v1/plan.md` | Add P0-13 to phase 0 and to wave 1 of *Order of work* (it unblocks CI); tick it with the PR link at the end. |

No change to `.github/workflows/ci.yml`: the variable lives in the script, so a developer's
`npm run typecheck` gets the fix too.

## Order of work

1. **Regression proof first.** There is no test kit for `scripts/` (the mod's `claude plugin test` runs
   only the mod's own files, in a sandbox with no processes), so the failing check is the check itself.
   Done: on this branch's plan commit, before the fix, CI's `latest` row failed with
   `lay-types: the declarations did not appear within 60 s`, exit 1
   ([job log](https://github.com/yvoke-dev/yvoke-claude-plugin/actions/runs/39470560537/job/118793203056)); the same reproduces locally on 2.1.304.
2. Add the variable after `...process.env` in the child's environment, so a developer's own
   `CLAUDE_CODE_PLUGIN_DIR_WATCH=0` cannot turn it off, and the comments; run `npm run typecheck` on 2.1.304 (green) and on 2.1.287 (green).
3. Correct `AGENTS.md` and `design.md`; run `npm run check`; CI green on both matrix rows.

## Risks

- **The variable is documented for long-lived headless sessions, not for `-p`.** A later release could
  stop laying types under `-p` again. The script then fails the same loud way (60 s timeout) on the
  `latest` row of the next CI run: any push, pull request or the weekly run.
- **Alternatives considered.**
  - *Commit the declarations.* Rejected: they are written per Claude Code version and would be stale on
    one of the two CI versions; AGENTS.md says the folder is never committed.
  - *Type-check against the file the plugin-authoring skill writes.* Rejected: it appears only when a
    model loads that skill, which needs a sign-in and a model call.
  - *Pin CI's "latest" row to 2.1.293.* Rejected: it hides the next break, the reason the row exists.

## Proof

- `npm run typecheck` on Claude Code 2.1.304: before the fix
  `lay-types: the declarations did not appear within 60 s`, exit 1; after it `lay-types: OK (…)` and
  nothing from `tsc`, exit 0.
- The same on 2.1.287 after the fix, and the declarations written on every release from 2.1.293 to
  2.1.304 (table above).
- `npm run check` (all five rows of the AGENTS.md table), and both CI matrix rows green on the PR.
