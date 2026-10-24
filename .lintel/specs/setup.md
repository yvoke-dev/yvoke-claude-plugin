# Session setup

## Behaviour

- In a Yvoke session (see [session.md](session.md)) a band above the prompt shows the session's setup.
  Outside a Yvoke session nothing is drawn and no list is loaded. When a survey is active
  (`e.props.hasSurvey` is `true` on `AbovePrompt`), `ui.render` yields (`next(e)`) and does not draw the
  setup band.
- `/yvoke` loads the areas and playbooks from the server (`list_areas`, `list_playbooks`). Until they
  arrive the band says *Loading the Yvoke setup…*.
- Before the first question the band has three selects, area first:
  - **Area**: each area by its title (an area without a non-empty `title` defaults its title to `name`).
    Default: OIM when the server has it, else the first area.
  - **Mode**: *Single agent*. *Multi-agent* is gated behind `MULTI_AGENT_READY = false` in
    `plugins/yvoke/src/setup.ts`, so the setup band currently offers only *Single agent* mode (while
    `setup.ts` already supports rendering and locking profile selections when multi-agent mode is enabled),
    and never for an area with no profiles.
  - **Playbook**: the area's playbooks as *name vN* (`oim-full v3`; a playbook without a numeric `version`
    defaults to `1`), the area's default playbook selected. In multi-agent mode a **Profile** select takes
    its place, the area's default profile selected.
  A surface whose `$.ui.resolve(e)` provides no `Select` component renders the read-only text line even
  while unlocked.
- Changing the area resets mode, playbook and profile to that area's defaults. Switching to *Multi-agent*
  selects the area's default profile; switching back selects the default playbook. If saving an unlocked
  select change to `$.store` fails, the error is ignored so the pick still holds in `$.state`.
- Prototype areas and playbooks are hidden unless the `showPrototypes` setting is on. A default the band
  does not show falls back to the area's first playbook or profile.
- The first question that enters the session **locks** the setup. Just typing accepts the defaults. From
  then on the band shows one read-only line, *OIM · Single agent · oim-full v3* (when `lineFor` formats the
  read-only line without a loaded catalogue, it falls back to `setup.area` (`name`)), and the status line
  shows the same. Nothing can change it; a different setup is `/clear`, then `/yvoke`.
- Any submission that reaches the model counts as that first question, not only a typed one: a skill or
  prompt command, the turn of a built-in command that asks the model, and a prompt the engine queued that
  the person does not see as typed (`isMeta`). It locks the setup and loads the instructions like a typed
  question: neither `prompt.submit` hook, here or in `src/system-prompt.ts`, reads `e.command` or `e.isMeta`.
- The lock takes the playbook version that loaded, which the answers use. `src/system-prompt.ts` fetches
  the playbook by name only, so if the server saved it between `/yvoke` and the first question, the band
  shows v3 while v4 loaded. Once the question has entered, `setup.ts` reads `yvoke.instructions`: when it
  is for this session and its `loadedFor` names the chosen playbook with a version other than the band's,
  the setup locks with that version, the band and the status line show it, and a toast says once
  *oim-full is now v4 on the server, so this session uses v4 (the band showed v3).* With the same version,
  no version in `get_playbook`'s answer, or multi-agent mode, the setup locks as the band showed it. If
  the read fails, the question still enters and the setup locks with the band's version.
- A setup already locked gets the same correction once its question has entered: the first question after
  `/resume` or `/branch` loads the instructions again, and if the server saved the playbook after the lists
  loaded, the setup, the line, the status line and `setup:<session id>` move to the loaded version with the
  same toast, once. This holds on the fast path (the lists already loaded; it reads only
  `yvoke.instructions`, with no server call) and after a late load of the lists. A failure there never
  drops the question; it only skips the correction.
- A question that is dropped does not lock the setup. When the server call that loads the instructions
  fails in `src/system-prompt.ts`, its `prompt.submit` hook drops the question before `setup.ts` locks the
  session, so the setup stays open.
- The locked line keeps the version it locked with while the session runs, even when the server's
  playbook moves on.
- Server down, or an answer the plugin cannot read (`Yvoke Backend: list_areas gave an answer the plugin cannot read.`
  / `Yvoke Backend: list_playbooks gave an answer the plugin cannot read.`): the band shows the `Yvoke Backend:`
  error and a **Retry** button, and a question is dropped with the same error. A question sent before the
  lists arrived loads them first. A server with no areas is refused the same way (`Yvoke Backend: the server has no areas.`
  with a **Retry** button). When an area has no playbooks, the catalogue loads and the band still renders
  the **Area** and **Mode** selects so the user can pick another area, replacing only the third slot with
  red text `The area <title> has no playbooks.`; if a question is submitted while that area is selected in
  single-agent mode, the question is dropped with `Yvoke Backend: the area <name> has no playbooks, so the question was not sent.`
  There is no cached list.
- `/clear` ends the setup with the session. `/resume` brings back the setup the resumed session had,
  locked or not; if it cannot be read, the session has none. `/branch` keeps the setup, locked in the new
  session; Claude Code starts a branch with empty `$.state`, so it is read from the session that just
  ended ([session.md](session.md)). The status line is cleared on each of the three and set again only for a locked setup, so it
  never shows a session's setup under another session.
- Whenever the lists load, an unlocked setup keeps its picks only while they are still offered: its area,
  and in that area its playbook (single agent) or its profile (multi-agent), by name. A playbook deleted on
  the server, or a prototype now hidden because `showPrototypes` is off, gives way to that area's
  defaults, and an area that is gone to the default area's; the band and the first question use them. A
  locked setup is kept as it is, even when its playbook is gone.
- After `/resume` or `/branch` of a locked session whose playbook the server has saved again, the setup
  carries on with the server's version: the line and the status line show it, and a toast says once *oim-full moved
  from v3 to v4 since the setup was locked; answers from now on use v4.*
- If the lists fail to load on `/resume` or `/branch` of a locked session (the server is down, still
  connecting or needs a sign-in), the setup stays locked, and the tool gate refuses every Yvoke tool until
  they load ([tool-policy.md](tool-policy.md)). The session recovers without `/clear`:
  - The band shows the locked line, the `Yvoke Backend:` error and a **Retry** button.
  - The next question loads the lists again before it goes on. If they still fail, the question is
    dropped with the same `Yvoke Backend:` error and the setup stays as it was. If they load, the question
    goes on, the band shows the locked line alone, and later questions load no list.
  - **Retry** loads them the same way, without a question.
  - A late load applies the same version upgrade as `/resume`: if the server has a newer version of the
    locked playbook, the setup moves to it, the toast is shown once and the status line shows the new
    version. The question hook saves the moved setup before the question goes down, so
    `src/system-prompt.ts` loads the instructions for it. Only the version can change; the area, mode,
    playbook and lock stay as they were.
- A question that entered the session is never reported as dropped, even when saving the lock fails;
  the lock then holds for this session, and `/resume` finds the setup unlocked.
- The saved setups (`setup:<session id>`) are a few hundred bytes each, in the plugin's 4 MiB store.
  `/yvoke` keeps the entries of the newest 5,000 sessions, about 1.25 MiB, and deletes the rest (D-28,
  [session.md](session.md)).
- A failure inside the question hook, before the question has gone down, drops the question:
  *Yvoke Backend: the session could not be set up, so the question was not sent.*

## Interfaces

| Piece | Where |
| --- | --- |
| Rules (catalogue, defaults, picks, read-only line, the loaded version at the lock, version upgrade on resume/branch or a late load) and hooks | `plugins/yvoke/src/setup.ts` |
| Band | `ui.render` on `AbovePrompt` (terminal and desktop); `Select` keys `yvoke-area`, `yvoke-mode`, `yvoke-playbook`, `yvoke-profile`; Button `yvoke-retry` |
| Loading | `command.run` on `yvoke`, `classic.SessionStart` (`resume`, `fork`), Retry, or a question with no lists or failed ones, locked or not |
| Lock | `prompt.submit` (matcher `{ text: /^/ }`, so it can sit beside `src/system-prompt.ts`'s unmatched hook) |
| `$.state` `yvoke.setup` | `{ area, mode: 'single' \| 'multi', playbook, version, profile, lockedSessionId }` (read by `src/system-prompt.ts` and `src/policy.ts`) |
| `$.state` `yvoke.instructions` | read at the lock for `loadedFor` (written only by `src/system-prompt.ts`, [system-prompt.md](system-prompt.md)) |
| `$.state` `yvoke.catalogue` | the lists as the band shows them, or the error; each playbook carries the `tools` it declares, null when it declares none (an empty list or no field), `'unreadable'` when the server sent something else, which the tool gate reads ([tool-policy.md](tool-policy.md)) |
| `$.store` `setup:<session id>` | the setup, saved on every pick, before a question goes down, and at the lock |
| Setting `showPrototypes` | `plugin.json` `userConfig`, boolean, default off |
| Server tools | `list_areas`, `list_playbooks` (through [server-client.md](server-client.md)) |
