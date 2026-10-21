# Session setup

## Behaviour

- In a Yvoke session (see [session.md](session.md)) a band above the prompt shows the session's setup.
  Outside a Yvoke session nothing is drawn and no list is loaded.
- `/yvoke` loads the areas and playbooks from the server (`list_areas`, `list_playbooks`). Until they
  arrive the band says *Loading the Yvoke setup…*.
- Before the first question the band has three selects, area first (D-11, D-18):
  - **Area**: each area by its title. Default: OIM when the server has it, else the first area.
  - **Mode**: *Single agent*. *Multi-agent* is not offered until multi-agent mode is built (Phase 6), and
    never for an area with no profiles.
  - **Playbook**: the area's playbooks as *name vN* (`oim-full v3`), the area's default playbook
    selected. In multi-agent mode a **Profile** select takes its place, the area's default profile
    selected.
- Changing the area resets mode, playbook and profile to that area's defaults. Switching to *Multi-agent*
  selects the area's default profile; switching back selects the default playbook.
- Prototype areas and playbooks are hidden unless the `showPrototypes` setting is on. A default the band
  does not show falls back to the area's first playbook or profile.
- The first question that enters the session **locks** the setup. Just typing accepts the defaults. From
  then on the band shows one read-only line, *OIM · Single agent · oim-full v3*, and the status line
  shows the same. Nothing can change it; a different setup is `/clear`, then `/yvoke`.
- A question that is dropped (by the server's instructions failing to load, P1-07, or anything else)
  does not lock the setup.
- The locked line keeps the version it locked with while the session runs, even when the server's
  playbook moves on.
- Server down, or an answer the mod cannot read: the band shows the `Yvoke Backend:` error and a **Retry**
  button, and a question is dropped with the same error. A question sent before the lists arrived loads
  them first. A server with no areas, or an area with no playbooks, is refused the same way. There is no
  cached list.
- `/clear` ends the setup with the session. `/resume` brings back the setup the resumed session had,
  locked or not; if it cannot be read, the session has none. `/branch` keeps the setup, locked in the new
  session; Claude Code starts a branch with empty `$.state`, so it is read from the session that just
  ended ([session.md](session.md)). The status line is cleared on each of the three and set again only for a locked setup, so it
  never shows a session's setup under another session.
- After `/resume` or `/branch` of a locked session whose playbook the server has saved again, the setup
  carries on with the server's version: the line and the status line show it, and a toast says once *oim-full moved
  from v3 to v4 since the setup was locked; answers from now on use v4.* (D-20).
- A question that entered the session is never reported as dropped, even when saving the lock fails;
  the lock then holds for this session, and `/resume` finds the setup unlocked.
- The saved setups (`setup:<session id>`) are a few hundred bytes each, in the plugin's 4 MiB store. They
  are not pruned yet; at that size thousands of sessions fit.
- A failure inside the question hook, before the question has gone down, drops the question:
  *Yvoke Backend: the session could not be set up, so the question was not sent.*

## Interfaces

| Piece | Where |
| --- | --- |
| Rules (catalogue, defaults, picks, read-only line, D-20) and hooks | `plugins/yvoke/src/setup.ts` |
| Band | `ui.render` on `AbovePrompt` (terminal and desktop); `Select` keys `yvoke-area`, `yvoke-mode`, `yvoke-playbook`, `yvoke-profile`; Button `yvoke-retry` |
| Loading | `command.run` on `yvoke`, `classic.SessionStart` (`resume`, `fork`), Retry, or a question with no lists |
| Lock | `prompt.submit` (matcher `{ text: /^/ }`, so it can sit beside another module's unmatched hook) |
| `$.state` `yvoke.setup` | `{ area, mode: 'single' \| 'multi', playbook, version, profile, lockedSessionId }`; P1-07 reads area, mode and playbook |
| `$.state` `yvoke.catalogue` | the lists as the band shows them, or the error |
| `$.store` `setup:<session id>` | the setup, saved on every pick, before a question goes down, and at the lock |
| Setting `showPrototypes` | `plugin.json` `userConfig`, boolean, default off |
| Server tools | `list_areas`, `list_playbooks` (through [server-client.md](server-client.md)) |

## Tests

- `plugins/yvoke/tests/setup-rules.test.ts`: reading the lists (versions, prototypes, fallbacks, empty
  and unreadable answers), defaults, each pick, the area reset, the switch to multi-agent, the lock
  guard, the modes offered, the read-only line with its version, and D-20.
- `plugins/yvoke/tests/setup.test.ts`, on `terminal` and `desktop`: a plain session, the defaults,
  changing playbook and area, the lock and its line and status line, just typing, the version kept after
  the lock, a dropped question not locking, the setup saved before the question goes down, server down
  with Retry, no areas, an area with no playbooks, lists loaded by the first question, a survey, `/clear`,
  `/resume` locked, moved on (D-20), unlocked, plain or unreadable, `/branch` (and moved on), the status
  line cleared on `/resume` of a plain or unlocked session, a hook failure, a lock that
  cannot be saved, and prototypes on. Run on Claude Code 2.1.287 and latest.
- `plugins/yvoke/tests/branch.test.ts`: the setup after `/branch` with `$.state` emptied.
- Checked outside the test kit on Claude Code 2.1.306 (P1-08's plan, *Results*): the typed text stays
  after a dropped question, the session id is the same after `/resume`, and the setup survives `/branch`.

## History

- P1-08 ([#22](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/22)): the setup band.
- P1-08 ([#29](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/29)): the setup survives `/branch` with empty `$.state`.
