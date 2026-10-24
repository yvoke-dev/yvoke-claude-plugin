# Yvoke session

## Behaviour

- The mod loads in every Claude Code session that loads the plugin, in any folder. A session is a **Yvoke
  session** only after the user types `/yvoke` in it. In every other session the mod changes
  nothing: events reach Claude Code unchanged and nothing is drawn. The one thing every session gets is
  the `/yvoke` command itself.
- `/yvoke` answers with one line and adds nothing for the model to read:

  | Situation | Answer | Afterwards |
  | --- | --- | --- |
  | No question sent yet | *Yvoke session started. Ask your question; Yvoke's setup and rules apply until /clear.* | Yvoke session |
  | A question was already sent | */yvoke works only before the first question. Type /clear, then /yvoke.* | Unchanged |
  | Already a Yvoke session | *This is already a Yvoke session.* | Unchanged |
  | `/yvoke` failed | *Yvoke: /yvoke did not work. Try it again.* | Not a Yvoke session |

- Each `/yvoke` that starts a session then deletes the saved entries (`session:<id>` and `setup:<id>`)
  of every session beyond the newest 5,000 in the plugin's store (D-28). `ended` and any other key stay.
  If this fails, the session starts all the same and the user sees nothing; the next `/yvoke` tries
  again. A `/yvoke` that answers any other line deletes nothing.
- `/clear` ends a Yvoke session. `/resume` restores what the resumed session was: Yvoke if it was started
  with `/yvoke`, plain otherwise, whatever the session it was resumed from. If the saved entry cannot be
  read, the resumed session is plain. `/branch` keeps a Yvoke session. A resumed or branched Yvoke
  session whose setup cannot be restored is unusable until `/clear` ([setup.md](setup.md)).
- What a Yvoke session enforces applies only in a Yvoke session: the setup band is in [setup.md](setup.md),
  the tool rules in [tool-policy.md](tool-policy.md), [turn ceilings](turn-limits.md), and the system
  prompt carries the server's instructions ([system-prompt.md](system-prompt.md)).

> unverified: Live interactive Claude Code session check for how /yvoke autocompletes and whether /yvoke itself counts as a turn.

## Interfaces

| Piece | Where |
| --- | --- |
| Command `/yvoke` (registered at `session.start`, answered by `command.run`) | `plugins/yvoke/src/session.ts` |
| Flag `$.state` `yvoke.yvokeSession` (boolean), written only by `session.ts` | contract in `plugins/yvoke/types/index.d.ts` |
| Saved copy: `$.store` key `session:<session id>` → `{ yvoke: true }` | written by `/yvoke` and on `/branch` |
| `/clear`, `/resume`, `/branch` | `classic.SessionStart` with `source` `clear`, `resume`, `fork` |
| Last session that ended: `$.store` key `ended` → `{ sessionId }` | written on every `session.end`; read on `/branch` |
| Prune: `KEEP_SESSIONS` (5,000) and `staleKeys(keys, keep)`, the `session:` and `setup:` keys of every session beyond the newest `keep` | `plugins/yvoke/src/session.ts`; run by `/yvoke`'s `command.run` after the save and the flag, with `$.store.keys()` and `$.store.delete` |

`/yvoke` saves to `$.store` before it sets the flag, so a session is never Yvoke now and plain after
`/resume`. Claude Code starts a `/branch` with empty `$.state` and names no parent, but ends the parent
just before (`session.end`), so the branch reads the flag of the session saved under `ended`. Every
session writes `ended`, plain ones too, so a branch does not take an older Yvoke session for its parent.
This assumes no other Claude Code process ends a session between the parent ending and the branch
starting, and that the `ended` write succeeds; otherwise the branch takes the flag of the wrong session.
How later hooks check the flag: [architecture.md](../steering/architecture.md).

The prune counts sessions, not days: the entries carry no timestamps. `$.store.keys()` lists keys in
insertion order, and setting a key again keeps its place, so a session is as old as its first key,
whichever of the two it is. The session `/yvoke` has just saved is among the newest, so it stays.
