# P1-08 Session setup: area, mode, playbook

**Release:** v1 · **Size:** M · **Type:** feature · **Status:** in progress
**Regression test:** f1bc1b8

**Done when** (from [plan.md](../plan.md#phase-1--knowledge-base-base-instructions-and-playbooks-mvp)):
tests cover the defaults (single agent, the area's default playbook), changing each select before the first
question, the area reset, the switch to *Multi-agent* picking the area's default profile, the version shown,
the version kept after the lock even when the server's playbook moves on to a newer one, the lock, the
read-only line on `terminal` and `desktop`, `/clear`, `/resume`, and server down. Before it is ticked, a
developer's machine confirms: the typed
text stays in the prompt box after `{ drop }` (else restore it with `$.prompt.fill`); `$.session.id` is the
same after `/resume`; and the setup survives `/branch`.

Facts below were read from the declarations Claude Code **2.1.304** lays for the mod, and from yvoke-web
`main` (1.3.0) on 2026-10-20. No yvoke-web change is needed: `list_areas` and `list_playbooks` exist
(P1-06, P1-12).

## What the user gets

1. The user types `/yvoke`. The band above the prompt shows *Loading the Yvoke setup…*, then three selects:
   - **Area**: every non-prototype area by title (`OIM`). Default: OIM if the server has it, else the first.
   - **Mode**: *Single agent*. *Multi-agent* is added only once Phase 6 is built, and never for an area
     with no profiles.
   - **Playbook** (single agent): the area's playbooks as *title vN* (`oim-full v3`), the area's
     `defaultPlaybook` selected. **Profile** (multi-agent) takes its place: the area's profiles, its
     `defaultProfile` selected.
2. Changing the area resets mode to *Single agent* and the playbook and profile to that area's defaults.
   Switching the mode to *Multi-agent* selects the area's default profile.
3. The user types a question and sends it. If it gets through (P1-07 loaded the instructions, nothing
   dropped it), the setup **locks**: the band becomes one read-only line, *OIM · Single agent · oim-full
   v3*, and the status line shows the same. Nothing can change it; a different setup is `/clear`, then
   `/yvoke`.
4. `/resume` brings the setup back as it was locked; `/branch` keeps it; `/clear` ends it with the session.
5. Server down, or a server with no areas or no playbook in the chosen area: the band says so with a
   **Retry** button, and a question is dropped with a `Yvoke Backend:` message rather than sent without a
   setup. There is no cached list.

Prototype areas and playbooks are hidden unless a new `userConfig` switch `showPrototypes` (default off) is
on, as in yvoke-web's picker.

## Design

- **`src/setup.ts`** (new), two parts, so the rules are testable without the engine:
  - Pure functions, no `$` (mod rule 1): `toCatalogue(areasJson, playbooksJson, showPrototypes)` →
    areas with their playbooks and profiles, or an error; `defaults(catalogue, area)`; `pick(setup, catalogue,
    select, value)` doing the area reset and the profile on switch; `lineFor(setup)` for the read-only line.
  - `registerSetup(on)`: the hooks, one line in `hooks/register.tsx`.
- **Loading.** Two calls through `callYvoke` (P1-03): `list_areas` and `list_playbooks` (no area: one call
  returns every playbook with its `area` and `version`). They run when `/yvoke` starts a session (a
  `command.run` hook on `yvoke` that calls `next(e)` first and loads only if the flag is now true), on
  `/resume` or `/branch` of a Yvoke session that is not yet locked, and on **Retry**. The result goes to
  `$.state` `yvoke.catalogue`, then `$.ui.invalidate` redraws the band.
- **The choice** lives in `$.state` `yvoke.setup` = `{ area, mode, playbook, version, profile, lockedSessionId }`
  (`playbook` null in multi-agent mode, `profile` null in single agent). This is the key P1-07 already reads
  (PR #17: `{ area, mode, playbook }`); the added fields are ignored by it. Written on every pick and copied to
  `$.store` under its own key `setup:<id>` (changed while building: sharing P1-10's `session:<id>` entry
  would let P1-10's `{ yvoke: true }` writes on `/yvoke` and `/branch` wipe it), so `/resume` and `/branch`
  bring it back.
- **The lock.** A `prompt.submit` hook (enforcing: opens with the Yvoke check, rule 5) calls `next(e)` and,
  when the question entered (no `drop`), sets `lockedSessionId` to the current session id, saves the entry,
  sets the status line and redraws the band. So the setup locks exactly when the question was sent, P1-07's
  fetch included, whichever hook order the two modules end up in; P1-08 does not read P1-07's
  `yvoke.instructions` key. Before calling `next`, it drops the question when there is no catalogue or no
  setup (server down, empty server) with the load's own `Yvoke Backend:` error. `.catch` → `{ drop }`
  (fails closed). Locked means `lockedSessionId === $.session.id()`; `/clear` empties `$.state`, so it
  cannot carry over.
- **The band.** `ui.render` on `{ component: 'AbovePrompt' }`, only in a Yvoke session and only on
  `terminal` and `desktop` (`AbovePrompt` is raised on those two only, design 4.3). Not locked: the three
  `Select`s, each `onSelect` writing through `pick`. Locked: one `Text` line. It passes while
  `hasSurvey` is true. It draws nothing outside a Yvoke session.
- **`types/index.d.ts`**: `setup` and `catalogue` keys in `PluginState.yvoke`. If P1-07 merges first it adds
  `setup` already; whoever merges second rebases.
- **`plugin.json`**: `userConfig.showPrototypes` (boolean, default false).
- **Store size.** The entry stays a few hundred bytes per session. Pruning old `session:` entries (left to
  this task by P1-10) is not needed yet at that size; `setup.md` says so.
- Nothing is logged with `$.ui.log`.

## Decided: the version after `/resume` and `/branch` (D-20, Eduard 2026-10-20 and 2026-10-21)

The server keeps no old playbook text (yvoke-web spec 02 "Not supported": saving overwrites in place).
Within one session nothing moves: P1-07 fetches the text once, and the line keeps the locked version even
when the server's list moves on. After `/resume` or `/branch`, P1-07 fetches again and gets whatever the server has now.
**Eduard chose to carry on with the new version**: on `/resume` (and, since his 2026-10-21 review, `/branch`) of a locked session the mod reloads the
lists, and when the playbook's version differs from the locked one it updates `setup.version`, the line
and the status line to the new version, and tells the user once (a toast: *oim-full moved from v3 to v4
since the setup was locked; answers from now on use v4.*). A playbook the server no longer has is not a
version change: P1-07's fetch fails and drops the question as it does today. Recorded in `design.md` as
D-20.

## Files that change

| File | Change |
| --- | --- |
| `plugins/yvoke/src/setup.ts` | new: pure rules and the hooks |
| `plugins/yvoke/hooks/register.tsx` | one line: `registerSetup(on)` |
| `plugins/yvoke/types/index.d.ts` | `setup` and `catalogue` keys |
| `plugins/yvoke/.claude-plugin/plugin.json` | `showPrototypes` setting |
| `plugins/yvoke/tests/setup-rules.test.ts` | new: the pure functions |
| `plugins/yvoke/tests/setup.test.ts` | new: the hooks through the engine, both surfaces |
| `docs/specs/setup.md`, `docs/specs/README.md`, `docs/specs/session.md`, `docs/specs/packaging.md` | new spec, index line, one line in session, the `showPrototypes` setting |
| `docs/tasks/v1/plan.md`, `design.md` | tick P1-08 (after the machine checks); D-20 and D-26 in this PR, with the release plan changes D-26 needs (also in #24); 4.3 rows if the API differs |
| `README.md`, `intent.md`, `requirements.md`, `docs/tasks/v1/P1-04/plan.md` | D-26's changes, copied from #24 so either PR can merge first |

## Order of work

Each step starts with a test, seen failing first.

1. `toCatalogue`: areas with members and defaults, playbooks joined by area with their version, prototypes
   hidden and shown, `ERROR:` and bad JSON, no areas, an area whose default playbook is missing.
2. `defaults` and `pick`: the defaults (OIM, single agent, the area's default playbook); changing each
   select; the area reset; *Multi-agent* picking the default profile; *Multi-agent* never offered while
   Phase 6 is off or for an area without profiles; `lineFor` with the version.
3. Loading hooks with a stubbed server (`on('mcp.call', …)`): `/yvoke` loads once; a plain session calls
   nothing; server down shows the error and Retry; Retry loads again.
4. The band on `terminal` and `desktop`: selects before the lock, the line after, nothing in a plain
   session.
5. `prompt.submit`: the lock after an entered question; no lock after a drop beneath; dropped with no
   catalogue; a throw drops (fails closed); a plain session passes untouched; the version kept after the
   lock when the server's list moves on to a newer one.
6. `/clear`, `/resume` (locked setup restored, store read failing leaves it unlocked and reloads), `/branch`
   (copied to the new id).
7. D-20: `/resume` or `/branch` of a locked session whose playbook moved on updates the version and the line and
   toasts once; same version, no toast; a toast is not repeated on a second resume.
8. Register the line, write the spec, run the checks, then the machine checks below.

## Changed while building

- **One hook per event, unless matched.** Claude Code 2.1.304 refuses a module that registers an event
  twice without a matcher. `src/session.ts` already hooks `classic.SessionStart` and `command.run`, and
  P1-07 hooks `prompt.submit`, so this module's `classic.SessionStart` matches `{ source: /^(clear|resume|fork)$/ }`
  and its `prompt.submit` matches every prompt (`{ text: /^/ }`). Checked by merging P1-07's branch
  locally: both modules load and P1-08's tests pass beside it; P1-07's own tests then need a stub for
  `list_playbooks` (told to its thread). Recorded in design 4.4.
- **Register order.** Hooks on one event run in registration order, the first outermost, and the
  session's `/yvoke` hook answers without `next`, so `registerSetup` comes first in `hooks/register.tsx`.
  On `classic.SessionStart` the setup hook calls `next(e)` first, so P1-10 has decided the flag before it
  reads it.
- **A `Text` keeps no `key`.** Tests find the read-only line and the notices by their text.
- **A lock that cannot be saved never drops the question.** After `next(e)` the question has entered;
  the status line and store writes are caught (found red on 2.1.287 only, whose test kit passes a test
  hook's throw on to the caller).
- **The handover to P1-07 is tested when both are on one branch.** P1-07's thread found it cannot stage a
  test of reading `yvoke.setup` from its side. Whichever of #17 and #22 merges second adds an engine test
  that picks a non-default playbook in the band and asks a question, and checks that the stubbed
  `get_playbook` call names that playbook. A local merge of the two branches already shows both
  modules loading together.
- **P2-01's `catch` form.** The question hook's `.catch` uses `next.called ? next(e) : { drop }` (design 4.4,
  P2-01): once the question has gone down, the hook passes on what it settled to. P2-01's test that a
  built-in never asks the server now counts from after `/yvoke`, since `/yvoke` loads the lists.
- **Review of #22 (Eduard, 2026-10-21).** The status line is now cleared on every `/clear`, `/resume` and
  `/branch` and set again only for a locked setup (it outlived its session on `/resume` of a plain or
  unlocked one). D-20 also applies to `/branch`, which fetches the playbook afresh under its new id.
- **No playbook skills (D-26, Eduard 2026-10-21).** Chat and Cowork are dropped from v1, so the plugin
  ships no skills, and the `skill.prompt` hook, its test and its machine check are removed (they were
  built and then taken out in this PR).
- **Slash commands that reach the model lock too.** `prompt.submit` fires for a command only when it asks
  the model, which is a question like any other, as for P1-07.

## Machine checks (Eduard's machine, before the tick)

The test kit cannot show these; one short session with `claude --plugin-dir plugins/yvoke`, Claude Code
2.1.287 and latest, against his local yvoke-web:

1. Stop the server, type a question: it is dropped; is the typed text still in the prompt box? If not, the
   hook restores it with `$.prompt.fill` (one line plus a test).
2. Lock a setup, `/resume` the session: same `$.session.id`, setup line back.
3. `/branch`: the new session shows the same locked line.

Findings go in this plan's *Results* section; a check that fails reopens the step it belongs to.

## Results

Run 2026-10-21 on Claude Code 2.1.306.

1. **Typed text after `{ drop }`: passes** (Eduard's Mac, terminal). The question is dropped with the
   server's message and `test question` stays in the prompt box, so no `$.prompt.fill` is needed.
2. **`/resume`: passes** (cloud sandbox, interactive terminal under tmux, a stub Yvoke server). The
   session id is the same before and after (`0e2c40a3…`), the locked line *OIM · Single agent ·
   oim-quick v2* comes back, and with the stub's playbook bumped D-20 moves it to v3 and toasts once.
   Not run on Eduard's Mac: his Claude Code belongs to a Team organization, so the built-in
   `cc-plugin-sec-default` passes over a self-installed plugin's `classic.SessionStart` (D-25). A local
   `managed-settings.json` does not help there, because the organization's remote managed settings
   take its place once they arrive.
3. **`/branch`: fails** (cloud sandbox). The branch gets a new session id, but Claude Code starts it
   with **empty `$.state`**, and its `classic.SessionStart` (`source: 'fork'`) names no parent session.
   So the Yvoke flag (P1-10) and the setup are both lost and the branch is a plain session. The test kit
   keeps `$.state` across a fork, which is why the engine tests passed. `session.end` fires for the
   parent just before the fork, with its id.

Also seen: in manual permission mode, Claude Code asks the user before each of the mod's own
`list_areas` and `list_playbooks` calls; in auto mode it does not.

## Fix for check 3 (bug, agreed with Eduard 2026-10-21)

- **Regression test first** (`tests/branch.test.ts`): the test's engine empties `$.state` at the fork, as
  Claude Code does, and the branch must still be a Yvoke session with the locked setup.
- `src/session.ts`: on `session.end`, save `{ sessionId }` of the ending session under the store key
  `ended` (every session, so a plain one overwrites a Yvoke one). On a fork, when `$.state` has no flag,
  read the flag from `session:<ended id>`.
- `src/setup.ts`: on a fork, when `$.state` has no setup, read it from `setup:<ended id>`.
- Risk: two Claude Code processes ending sessions in the same moment could swap `ended`; the branch
  then takes the other session's flag. Rejected alternatives: a time window (needs `$.clock.now` for a
  race of milliseconds), and reading the transcript (needs `$.fs`).

## Risks

- **The lock depends on hook order only in its timing.** If P1-07's hook sits beneath this one, `next(e)`
  includes its fetch and a failed fetch is a `drop`: no lock. If it sits above, a drop never reaches this
  hook: no lock either. Only the first order is tested (a test's hooks sit beneath the plugin, so a
  stand-in for P1-07 is always beneath); the second follows from the engine not calling a hook beneath a
  drop.
- **A select picked while the question is being sent.** `pick` returns a locked setup unchanged, so a pick
  after the lock is ignored.
- **Rejected: lock on P1-07's `yvoke.instructions` key**, as the coordinator's note suggested. It couples
  P1-08 to an unmerged module and gives no lock until P1-07 lands; the `next(e)` result gives the same
  answer with no coupling.
- **Rejected: load the lists inside `ui.render`.** It redraws often and a slow server would hold the band;
  loading on `/yvoke`, resume and Retry keeps rendering to reads of `$.state`.
- **Rejected: one Select listing *Single agent* plus each profile** (the old plan); D-18 replaced it.

## Proof

- `plugins/yvoke/tests/setup-rules.test.ts` and `setup.test.ts` cover every item of *Done when* that a test
  can show; each hook test seen failing against a mutation of what it pins (Yvoke check removed, lock
  before `next`, catch removed, reset removed).
- The three machine checks above, recorded under *Results*.
- `npm run check`: docs, both `validate --strict` runs (the `calls:` line names no `$.fs`, `$.process` or
  `$.http`), types, and `claude plugin test plugins/yvoke` (N pass, 0 fail).
