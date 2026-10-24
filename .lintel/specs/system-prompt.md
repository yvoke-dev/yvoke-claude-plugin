# System prompt

## Behaviour

- In a Yvoke session ([session.md](session.md)), the first question loads the instructions from the Yvoke
  server before it is sent: the base instructions and, in single-agent mode, the playbook's text
  (`get_playbook`).
- The base instructions are chosen from data before any `get_system_prompt` call, as in yvoke-web and
  yvoke-desktop 1.3.0: the system prompt the playbook names (its `systemPrompt`) when any area in
  `list_areas` lists it among its `systemPrompts` (the server looks prompt names up globally, not within the
  area); else the session area's `defaultSystemPrompt`; else the active default (`get_system_prompt`
  called with no name). A prompt no area lists falls back silently. Then the mod makes
  exactly one `get_system_prompt` call. If that call fails, an `ERROR:` body included, the question is
  dropped: the server client cannot tell a missing prompt from a broken server, so there is no second try.
- From then on every system prompt of that session ends with one section, `yvoke:instructions`
  (`scope: 'session'`): the base instructions, a `---` line between blank lines (`\n\n---\n\n`, as web
  chat joins them), then the playbook text. Claude Code's own
  sections stay before it. The playbook comes last, so it wins a conflict.
- The area, mode and playbook come from the setup band ([setup.md](setup.md)), which saves them in
  `yvoke.setup` before the question goes down. With no setup saved, the session runs on area **OIM**,
  single agent, and the playbook `list_areas` names as that area's default. In multi-agent mode only the
  base instructions are added.
- If loading fails, the question is not sent and the user sees why, as the server client words it, for
  example `Yvoke Backend: ERROR: playbook 'oim-ful' not found.`, or, when the server is not connected,
  `Yvoke Backend:` followed by Claude Code's own sentence (and *Run /mcp and sign in to yvoke.* when it
  needs a sign-in). Unparseable JSON or a wrong shape drops the question with
  `Yvoke Backend: <tool> gave an answer the plugin cannot read.`; a missing area drops it with
  `Yvoke Backend: area '<area>' does not exist.`; an area without a default playbook when none was picked
  drops it with `Yvoke Backend: area '<area>' has no default playbook.`; empty or whitespace-only
  `get_playbook` `text` drops it with `Yvoke Backend: playbook '<name>' has no text.`; and empty or
  whitespace-only `get_system_prompt` text drops it with
  `Yvoke Backend: get_system_prompt gave no instructions.` The next question tries again. Nothing is cached
  as a fallback.
- The loaded text is kept in `yvoke.instructions` with what it was loaded for, `loadedFor`: the area and
  mode, the playbook actually loaded (the setup's, or the area's default when the setup names none) and
  the `version` in `get_playbook`'s answer, null when it is missing or not a number. In multi-agent mode
  `playbook` and `version` are null. `get_playbook` takes no version, so the mod records the version it
  got rather than asking for one; the setup locks with it ([setup.md](setup.md)).
- A later question reuses the kept text only when it was loaded under the same session id and `loadedFor`
  matches the current setup: the same area and mode; the same playbook, unless the setup names none; and
  the same version, unless the setup's version or the loaded one is null. Otherwise it loads again. So a question dropped
  after the load (by another hook beneath, for example), followed by another pick in the still-open band,
  loads the new playbook. An entry without `loadedFor` (left by an older build before a hot reload) or one
  the plugin cannot read never matches. While the setup matches, later questions reuse the text even if
  the playbook changes on the server. A `get_playbook` answer without a version records null, which counts
  as unknown rather than different, so later questions reuse the text. `/clear`, `/resume` and `/branch`
  start a new session id, so that session loads again on its first question.
- Fails closed: if the hook that loads the instructions fails, the question is dropped with *Yvoke Backend:
  the instructions could not be loaded, so the question was not sent.* If the hook that adds the section
  fails, or, once the setup is locked for this session (`lockedSessionId` is the current session id),
  `yvoke.instructions` is missing or unreadable for the current session, the system prompt is replaced by
  one refusal section (`yvoke:instructions`):
  `The Yvoke instructions for this session could not be loaded. Do not answer the question and do not call any tool. Reply only: "Yvoke could not load its instructions. Please send your question again."`
  This covers a question that entered without its instructions, for example when another plugin's
  `prompt.submit` hook answers it without passing it on, so the loading hook never runs. Text loaded for
  the current session is served whether the setup is locked or not, and whatever version it was loaded
  for: `setup.ts` takes the loaded version only after the question has entered. Before the first question
  locks the setup, Claude Code's prompt passes unchanged, since Claude Code may compose prompts for its own
  requests then. `/resume` and `/branch` of a locked session lock the setup under the new session id at
  once, so there the refusal applies until that session's first question loads its instructions.
- Outside a Yvoke session neither hook changes anything, and the server is not called.
- Where an organization manages Claude Code, a plugin installed by the user from a local clone runs the
  loading hook (`prompt.submit`) but not `prompt.compose`, so the organization must install the plugin
  through managed settings or an MDM-managed marketplace rather than by the user from a local clone.

| Load step | Calls | Time |
| --- | --- | --- |
| Setup names its playbook | `list_areas` and `get_playbook` together, then `get_system_prompt` | two rounds |
| No setup saved | `list_areas`, then `get_playbook` (the area's default), then `get_system_prompt` | three rounds |
| Multi-agent mode | `list_areas`, then `get_system_prompt` | two rounds |

Each round waits at most 8 s (the server client's limit) or what the hook has left less 1,500 ms, whichever
is smaller. If the remaining hook budget minus the 1,500 ms reserve is under 1,000 ms
(`remainingMs() - 1500 < 1000`), the hook drops the question at once without calling the server:
`Yvoke Backend: there was not enough time left to load the instructions. Send the question again.`

> unverified: Live interactive Claude Code session check that the first answer already sees the composed section and whether subagent prompts also pass through prompt.compose.

## Interfaces

| Piece | Where |
| --- | --- |
| `prompt.submit` hook (loads, or drops the question) and `prompt.compose` hook (adds the section) | `plugins/yvoke/src/system-prompt.ts` |
| `$.state` `yvoke.instructions` = `{ sessionId, text, loadedFor: { area, mode, playbook, version } }`, written only by `system-prompt.ts`, read by `src/setup.ts` to lock with the loaded version | contract in `plugins/yvoke/types/index.d.ts` |
| `$.state` `yvoke.setup` = `{ area, mode, playbook, version, lockedSessionId }` (playbook null = the area's default; version checked against `loadedFor`; `lockedSessionId` decides the refusal in `prompt.compose`), read here, written by `src/setup.ts` | same |
| Server tools `list_areas` (`defaultPlaybook`, `defaultSystemPrompt`, `systemPrompts`), `get_playbook(name)` (`systemPrompt`, `text`, `version`), `get_system_prompt(name?)` | through `callYvoke` ([server-client.md](server-client.md)) |

Hooks nest in registration order: the first registered hook is on the outside and runs first, and a hook
that answers without calling `next(e)` hides the ones inside it. In `hooks/register.tsx`, `registerSetup`
runs before `registerSystemPrompt`: `registerSetup`'s `prompt.submit` hook calls `next(e)` first and locks
the setup only if `registerSystemPrompt`'s inner `prompt.submit` hook did not drop the question, with the
version that hook recorded in `loadedFor`.
`src/system-prompt.ts` owns the mod's unmatched `prompt.submit` and `prompt.compose` hooks; any other hook
on those events must register with a matcher ([architecture.md](../steering/architecture.md)).
