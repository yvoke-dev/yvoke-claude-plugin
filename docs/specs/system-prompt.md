# System prompt

## Behaviour

- In a Yvoke session ([session.md](session.md)), the first question loads the instructions from the Yvoke
  server before it is sent: the base instructions and, in single-agent mode, the playbook's text
  (`get_playbook`).
- The base instructions are chosen from data before any `get_system_prompt` call, as in yvoke-web and
  yvoke-desktop 1.3.0: the system prompt the playbook names (its `systemPrompt`) when any area in
  `list_areas` lists it among its `systemPrompts` (the server looks prompt names up globally, not within the
  area); else the session area's `defaultSystemPrompt`; else the active default (D-16, `get_system_prompt`
  with no name). A prompt no area lists falls back silently. Then the mod makes
  exactly one `get_system_prompt` call. If that call fails, an `ERROR:` body included, the question is
  dropped: the server client cannot tell a missing prompt from a broken server, so there is no second try.
- From then on every system prompt of that session ends with one section, `yvoke:instructions`
  (`scope: 'session'`): the base instructions, a `---` line between blank lines (`\n\n---\n\n`, as web
  chat joins them), then the playbook text. Claude Code's own
  sections stay before it. The playbook comes last, so it wins a conflict.
- Until the setup band exists (P1-08), the setup is area **OIM**, single agent, and the playbook `list_areas`
  names as that area's default (`oim-full` today). In multi-agent mode only the base instructions are added.
- If loading fails, the question is not sent and the user sees why, as the server client words it, for
  example `Yvoke Backend: ERROR: playbook 'oim-ful' not found.`, or, when the server is not connected,
  `Yvoke Backend:` followed by Claude Code's own sentence (and *Run /mcp and sign in to yvoke.* when it
  needs a sign-in). The next question tries again. Nothing is cached as a fallback.
- The text is loaded once per session id. Later questions reuse it, even if the playbook changes on the
  server. `/clear`, `/resume` and `/branch` start a new session id, so that session loads again on its first
  question.
- Fails closed: if the hook that loads the instructions fails, the question is dropped with *Yvoke Backend:
  the instructions could not be loaded, so the question was not sent.* If the hook that adds the section
  fails, the system prompt is replaced by one section telling the model to reply only that Yvoke could not
  load its instructions.
- Outside a Yvoke session neither hook changes anything, and the server is not called.

| Load step | Calls | Time |
| --- | --- | --- |
| Setup names its playbook | `list_areas` and `get_playbook` together, then `get_system_prompt` | two rounds |
| Setup has no playbook (today) | `list_areas`, then `get_playbook` (the area's default), then `get_system_prompt` | three rounds |
| Multi-agent mode | `list_areas`, then `get_system_prompt` | two rounds |

Each round waits at most 8 s (the server client's limit) or what the hook has left less 1.5 s, whichever is
smaller. With under 1 s left, it fails at once and asks to send the question again.

## Interfaces

| Piece | Where |
| --- | --- |
| `prompt.submit` hook (loads, or drops the question) and `prompt.compose` hook (adds the section) | `plugins/yvoke/src/system-prompt.ts` |
| `$.state` `yvoke.instructions` = `{ sessionId, text }`, written only by `system-prompt.ts` | contract in `plugins/yvoke/types/index.d.ts` |
| `$.state` `yvoke.setup` = `{ area, mode, playbook }` (playbook null = the area's default), read here, written by P1-08 | same |
| Server tools `list_areas` (`defaultPlaybook`, `defaultSystemPrompt`, `systemPrompts`), `get_playbook(name)` (`systemPrompt`, `text`), `get_system_prompt(name?)` | through `callYvoke` ([server-client.md](server-client.md)) |

For P1-08: the setup is locked exactly when `yvoke.instructions.sessionId` is the current session id.
A `prompt.submit` hook that must act before the lock (P2-06's preflight) registers before
`registerSystemPrompt(on)` in `hooks/register.tsx`.

## Tests

- `plugins/yvoke/tests/system-prompt-load.test.ts`: the calls and their order, base first, the `---` join,
  which base prompt (listed by its own area, only by another area, by none, no area default) and the
  dropped `ERROR:`, a setup with and without a playbook, multi-agent, server down, unknown playbook, a missing area or default playbook,
  unreadable answers, empty texts, and the time budget per round.
- `plugins/yvoke/tests/system-prompt.test.ts`, through the engine: a plain session, first question,
  later questions, server down then back, no playbook text, a server that does not answer, a new session
  id, and a throw in each hook (fails closed).
- Not checkable in the test kit: a plugin hook that overruns its own 10 s budget (the kit cuts the waits
  beneath it first). The `.catch` it would reach is the one the throw tests reach.
- Not covered through the engine: reading `yvoke.setup` (the kit's `$` cannot write `$.state`); P1-08's
  tests cover it once its band writes the key.
- Not yet checked on a real Claude Code (P0-06): that the first answer already sees the section, and
  whether subagent prompts also pass through `prompt.compose`.

## History

- P1-07 ([#17](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/17)): instructions from the server,
  including a playbook's own system prompt and the `---` join from yvoke-web 1.3.0.
