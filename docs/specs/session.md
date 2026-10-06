# Yvoke session

## Behaviour

- The mod loads in every Claude Code session that loads the plugin, in any folder. A session is a **Yvoke
  session** only after the user types `/yvoke` in it (D-13). In every other session the mod changes
  nothing: events reach Claude Code unchanged and nothing is drawn. The one thing every session gets is
  the `/yvoke` command itself.
- `/yvoke` answers with one line and adds nothing for the model to read:

  | Situation | Answer | Afterwards |
  | --- | --- | --- |
  | No question sent yet | *Yvoke session started. Ask your question; Yvoke's setup and rules apply until /clear.* | Yvoke session |
  | A question was already sent | */yvoke works only before the first question. Type /clear, then /yvoke.* | Unchanged |
  | Already a Yvoke session | *This is already a Yvoke session.* | Unchanged |
  | `/yvoke` failed | *Yvoke: /yvoke did not work. Try it again.* | Not a Yvoke session |

- `/clear` ends a Yvoke session. `/resume` restores what the resumed session was: Yvoke if it was started
  with `/yvoke`, plain otherwise, whatever the session it was resumed from. If the saved entry cannot be
  read, the resumed session is plain. `/branch` keeps a Yvoke session.
- What a Yvoke session enforces (setup band, system prompt, tool rules) arrives with later tasks; each
  applies only in a Yvoke session.

## Interfaces

| Piece | Where |
| --- | --- |
| Command `/yvoke` (registered at `session.start`, answered by `command.run`) | `plugins/yvoke/src/session.ts` |
| Flag `$.state` `yvoke.yvokeSession` (boolean), written only by `session.ts` | contract in `plugins/yvoke/types/index.d.ts` |
| Saved copy: `$.store` key `session:<session id>` → `{ yvoke: true }` | written by `/yvoke` and on `/branch` |
| `/clear`, `/resume`, `/branch` | `classic.SessionStart` with `source` `clear`, `resume`, `fork` |

`/yvoke` saves to `$.store` before it sets the flag, so a session is never Yvoke now and plain after
`/resume`. How later hooks check the flag: [design 4.4](../tasks/v1/design.md#44-rules-that-are-easy-to-get-wrong).

## Tests

- `plugins/yvoke/tests/session.test.ts`: a session without `/yvoke` (a tool call and a prompt pass
  through), `/yvoke` before the first question, after it and twice, `/clear`, `/resume` of a Yvoke and of
  a plain session, `/branch` of both, a failed save, and a failed read on `/resume`.
- Not yet checked on a real Claude Code: autocomplete next to the plugin's `/yvoke:<skill>` skills, whether
  `/yvoke` itself counts as a turn, and `$.state` on `/branch` (P0-06).

## History

- P1-10 ([#12](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/12)): the `/yvoke` session start.
