# P1-10 Yvoke session start

**Release:** v1 · **Size:** S · **Type:** feature · **Status:** done

The mod loads in every Claude Code session of the user, in any folder. A session becomes a Yvoke session
only when the user types `/yvoke` before the first question (D-13). Every other session behaves as if the
mod were not there. Task entry: [plan.md, P1-10](../plan.md#phase-1--knowledge-base-base-instructions-and-playbooks-mvp).

## What was checked first

Checked in a cloud session on **Claude Code 2.1.291** on 2026-10-06, against the generated declarations
and a throwaway copy of the plugin run with `claude plugin test`:

- A mod command is declared with `$.command.register({ name, description })` inside `session.start`, and
  answered by a `command.run` hook on `{ command: 'yvoke' }` that returns `{ text }`. The text is the
  command's output line; with no `context`, nothing is added for the model.
- `$.session.turns()` returns how many prompts the user has sent in the session. That is the "first
  question" test.
- `/clear`, `/resume` and `/branch` reach a mod as `classic.SessionStart` with `source` `clear`, `resume`
  or `fork` and the new `session_id`. `session.start` fires once per process, not after any of them.
- **The engine only follows `$` inside one file.** Passing `$` to a function imported from another file,
  or registering a hook built by a wrapper function (`on('tool.call', yvokeOnly(h))`), stops the whole
  module from loading. A `$.state` reference must be written as a literal, or as a `const` in the same
  file; an imported constant is refused. Hooks defined in a `src/` module and registered from
  `register.tsx` through `registerX(on)` are fine. (Same finding as P1-03.)
- In tests, each `$` call the mod makes (`command.register`, `session.turns`, `session.id`, `store.get`,
  `store.set`) is answered by a hook the test registers, so every case runs with no real session.
- Not checkable in the cloud, left to P0-06 on Eduard's machine: whether `/yvoke` autocompletes cleanly
  next to the plugin's `/yvoke:<skill>` skills, whether running `/yvoke` itself counts as a turn, and
  whether `$.state` is kept on `/branch` and cleared on `/clear` as the mods reference says. The design
  below does not depend on `/clear` clearing `$.state`, because it sets the flag off explicitly. It does
  depend on `$.state` being kept on `/branch` (see *Risks*).

Found while building:

- `validate` reads the `$.state` contract only where a key is written inline in `PluginState`
  (`yvoke: { yvokeSession: boolean }`); a key reached through a type alias is "not declared", and the
  contract file may not import. `YvokeState` therefore repeats the key.
- `tsc` refuses an import ending in `.ts`; `register.tsx` imports `../src/session` and the engine resolves it.
- `validate` notes "gating hook without .catch: classic.SessionStart". Left without a catch on purpose: a
  catch answer would stop the user's own `SessionStart` settings hooks from running. The hook does decide
  whether a resumed session is Yvoke, so it fails closed another way: on `resume` it sets the flag off
  first and on only when the saved entry reads back as Yvoke, so a store read that fails leaves the
  session plain (found in review).

## What the user sees

| Situation | `/yvoke` answers | Afterwards |
| --- | --- | --- |
| New session, nothing asked yet | *Yvoke session started. Ask your question; Yvoke's setup and rules apply until /clear.* | Yvoke session |
| A question was already sent | */yvoke works only before the first question. Type /clear, then /yvoke.* | Unchanged |
| Already a Yvoke session | *This is already a Yvoke session.* | Unchanged |
| `/yvoke` failed (store or state) | *Yvoke: /yvoke did not work. Try it again.* | Unchanged, not Yvoke |

`/clear` ends the Yvoke session silently. `/resume` restores whatever the resumed session was: Yvoke or
not, whichever session it was resumed from. `/branch` keeps it. Outside a Yvoke session the mod draws
nothing and changes nothing; the only thing it adds to every session is the `/yvoke` command itself,
which has to exist to be typed.

## Design

- **Flag:** `$.state` key `yvokeSession: boolean` (contract in `types/index.d.ts`). Survives hot reload,
  reset by `/clear`.
- **Saved copy:** `$.store` key `session:<session id>` → `{ yvoke: true }`, written when `/yvoke`
  succeeds. One key per session, as design 4.3 requires for a store shared by all open sessions. P1-08
  will add the locked setup to the same entry.
- **Order on `/yvoke`:** store first, then `$.state`. If the store write fails, the flag stays off and the
  user sees the failure line, so a session is never Yvoke now but plain after `/resume`.
- **`classic.SessionStart`:**
  - `clear` → flag off.
  - `resume` → flag off, then on only if `session:<session_id>` reads back `{ yvoke: true }`. This turns
    the flag *off* when a plain session is resumed from inside a Yvoke one, and when the read fails.
  - `fork` → if the flag is on, save it under the new session id, so a later `/resume` of the branch
    restores it.
  - `startup`, `compact` → nothing.
- **For later tasks:** because of the `$` rule above, there can be no shared `isYvokeSession($)` or
  `yvokeOnly(handler)` helper. Instead every enforcing hook in a later task (P2-01, P1-08, …) opens with
  the same two lines, written in its own file:

  ```ts
  const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
  if (isYvoke !== true) return next(e)
  ```

  The key is checked against the `types/index.d.ts` contract, so a typo fails `tsc` and `validate`. A
  flag that was never set reads `undefined`, which counts as "not a Yvoke session". Only `src/session.ts`
  writes the flag. Design 4.4 gets this pattern next to "Outside a Yvoke session, call `next(e)` and
  nothing else", in this PR.

## Files that change

| File | Change |
| --- | --- |
| `plugins/yvoke/src/session.ts` | New. `registerSession(on)`: the `session.start`, `command.run` and `classic.SessionStart` hooks above. |
| `plugins/yvoke/hooks/register.tsx` | One line: `registerSession(on)`. |
| `plugins/yvoke/types/index.d.ts` | `YvokeState` gets `yvokeSession: boolean`. |
| `plugins/yvoke/tests/session.test.ts` | New. The cases under *Proof*. |
| `docs/specs/session.md` | New spec: what a Yvoke session is, the command, the lines above. Row in `docs/specs/README.md`. |
| `docs/specs/packaging.md` | "registers no hooks yet" becomes a pointer to `session.md`. |
| `docs/tasks/v1/plan.md` | Tick P1-10 with the PR link. |
| `docs/tasks/v1/design.md` | Section 4.4: the two-line Yvoke check every enforcing hook opens with. Section 4.3 too if the build shows a row wrong (for example: a test answers `$` calls with `{ value }`). |

## Order of work

Each step: write the test, run it and see it fail, then write the code.

1. `/yvoke` before the first question starts a Yvoke session: answer text and store entry. A test's `$`
   cannot read `$.state`, so the flag is observed through behaviour: a second `/yvoke` answers "already".
2. A session without `/yvoke`: the commands registered are `/yvoke` alone, and a tool call and a prompt
   reach the engine unchanged (the scaffold test, extended to a prompt).
3. `/yvoke` after the first question: the `/clear` line, flag stays off, nothing stored.
4. `/yvoke` twice: the second answers "already", and writes nothing.
5. `/clear` ends it; `/resume` restores Yvoke and restores plain; `/branch` keeps it and saves it under
   the new id.
6. Failure paths: a store write that fails leaves the flag off with the failure line; a store read that
   fails on `/resume` leaves the session plain.
7. Spec, plan tick, `npm run check`.

## Risks

- **`/yvoke` next to the skills' `/yvoke:<skill>`.** No skills ship yet (P1-04), so nothing collides
  today. P0-06 checks autocomplete on both surfaces; if it collides, the command is renamed (for example
  `/yvoke-start`) in a follow-up, which changes one constant.
- **Does running `/yvoke` count as a turn?** If it did, `/yvoke` twice would still answer "already"
  (checked first), but nothing else breaks. P0-06 confirms; the turn count could be replaced by
  `$.session.messages()` then.
- **`/branch` and `$.state`.** If P0-06 finds `$.state` reset on `fork`, the branch loses the flag,
  because the fork event does not carry the parent's id. The fix would then key on the parent session
  from `session.end`. Tests here pin the intended behaviour.
- **Store growth.** One small key per Yvoke session in a 4 MiB store shared by all sessions: tens of
  thousands of sessions before it matters. Pruning old entries is left to P1-08, which adds more per
  session.
- **Considered and rejected:** a folder setting (replaced by D-13); asking the model to call a tool to
  start Yvoke (the user must decide, not the model); registering `/yvoke` only in fresh sessions (a
  command that disappears is harder to explain than one that says "type /clear first").

## Proof

- `plugins/yvoke/tests/session.test.ts`: without `/yvoke`, `/yvoke` before the first question, after it,
  twice, `/clear`, `/resume` (both ways), `/branch` (both ways), a prompt and a tool call passing through, and the two failure paths. These are P1-10's
  **Done when** cases.
- `npm run check`: docs, both `validate --strict` runs (the `calls:` line names no `$.fs`, `$.process` or
  `$.http`), typecheck, tests.
