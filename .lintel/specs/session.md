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
- What a Yvoke session enforces applies only in a Yvoke session: the setup band is in [setup.md](setup.md),
  the tool rules in [tool-policy.md](tool-policy.md), and the system prompt carries the server's
  instructions ([system-prompt.md](system-prompt.md)).

## Interfaces

| Piece | Where |
| --- | --- |
| Command `/yvoke` (registered at `session.start`, answered by `command.run`) | `plugins/yvoke/src/session.ts` |
| Flag `$.state` `yvoke.yvokeSession` (boolean), written only by `session.ts` | contract in `plugins/yvoke/types/index.d.ts` |
| Saved copy: `$.store` key `session:<session id>` → `{ yvoke: true }` | written by `/yvoke` and on `/branch` |
| `/clear`, `/resume`, `/branch` | `classic.SessionStart` with `source` `clear`, `resume`, `fork` |
| Last session that ended: `$.store` key `ended` → `{ sessionId }` | written on every `session.end`; read on `/branch` |

`/yvoke` saves to `$.store` before it sets the flag, so a session is never Yvoke now and plain after
`/resume`. Claude Code starts a `/branch` with empty `$.state` and names no parent, but ends the parent
just before (`session.end`), so the branch reads the flag of the session saved under `ended`. Every
session writes `ended`, plain ones too, so a branch does not take an older Yvoke session for its parent.
This assumes no other Claude Code process ends a session between the parent ending and the branch
starting, and that the `ended` write succeeds; otherwise the branch takes the flag of the wrong session.
How later hooks check the flag: [design 4.4](../tasks/v1/design.md#44-rules-that-are-easy-to-get-wrong).


## History

- P1-10 ([#12](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/12)): the `/yvoke` session start.
- P1-08 ([#29](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/29)): `/branch` keeps a Yvoke session although Claude Code empties `$.state`.
