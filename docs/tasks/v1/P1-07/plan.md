# P1-07 System prompt from the server

**Release:** v1 · **Size:** M · **Type:** feature · **Status:** planned

**Done when** (from [plan.md](../plan.md#phase-1--knowledge-base-base-instructions-and-playbooks-mvp)):
tests cover success, server down, unknown playbook, and a hook failure (fails closed).

Facts below were read from the declarations Claude Code **2.1.293** lays for the mod, and from
yvoke-web `main` on 2026-10-07. No yvoke-web change is needed: `list_areas`, `get_system_prompt(area)` and
`get_playbook(name)` already exist (P1-01, P1-06, P1-12).

## What the user gets

In a Yvoke session (P1-10), the first question loads the instructions from the server before it is sent:

1. The mod reads the session's setup: area, mode and playbook.
2. It calls `get_system_prompt({ area })` (the area's default prompt, else the active default, D-16) and
   `get_playbook({ name })`, and keeps the playbook's `text`.
3. It adds one section to the system prompt, `{ id: 'yvoke:instructions', scope: 'session' }`, holding the
   base instructions, a blank line, then the playbook text. Base first, playbook after, so the playbook
   wins a conflict (decided 2026-10-05).
4. If any call fails, the question is not sent. The user sees the server client's message as is, for
   example `Yvoke Backend: ERROR: playbook 'oim-ful' not found.`, and can send again. Nothing is cached;
   the next question tries again.

Claude Code's own sections stay; the Yvoke section is added after them, as design 4.3 says.

## Until P1-08: the setup comes from the area's defaults

P1-08 (the setup band) is not built yet. Until it is, the setup is fixed:

- **area** `OIM` (D-11's default),
- **mode** single agent,
- **playbook** the `defaultPlaybook` that `list_areas` gives for that area (`oim-full` today).

So the first question costs three server calls: `list_areas` and `get_system_prompt` together, then
`get_playbook`. An area missing from `list_areas`, or one with no default playbook, is an error like any
other: `Yvoke Backend: area 'OIM' has no default playbook.`

**How P1-08 hands over its choice.** P1-08's band will write what the user picked to a `$.state` key
`yvoke.setup` (`{ area, mode, playbook }`). P1-07 reads that key when it is set and uses the defaults
above only when it is not, so P1-08 adds no code here. P1-08 also needs to know when the setup is
locked; it reads that off P1-07's key below ("instructions loaded for this session"). The setup is then
locked exactly when the fetch worked, which is what requirements 2.1 asks ("a failed fetch … leaves the
setup unlocked"). In multi-agent mode P1-07 adds no playbook text; the lead's prompt comes with the
profiles (P6-01).

## Design

- **`src/system-prompt.ts`**, a new module with two parts:
  - `loadInstructions(io, setup, timeouts)`: the server calls above, built on `callYvoke` (P1-03). Returns
    `{ ok: true, text } | { ok: false, error }`. No `$` in it; the hook hands in the `io` closures
    (mod rule 1).
  - `registerSystemPrompt(on)`: the hooks, one line in `hooks/register.tsx`.
- **`prompt.submit`** (enforcing, so it opens with the two-line Yvoke check, rule 5):
  - If this session's instructions are already loaded, `next(e)`.
  - Else load them. On success write `$.state` `yvoke.instructions` = `{ sessionId, text }`, call
    `$.ui.invalidate('prompt.compose')`, then `next(e)`. On failure answer `{ drop: error }`.
  - `.catch` → `{ drop: 'Yvoke Backend: the instructions could not be loaded, so the question was not sent.' }`
    (requirements 1: fails closed).
- **`prompt.compose`**: `next(e)`, then, in a Yvoke session whose `yvoke.instructions.sessionId` is the
  current `$.session.id()`, append the section. Anything else returns `next(e)` unchanged.
  - `.catch` answers one section only, telling the model the Yvoke instructions are missing and to reply
    only that the user should ask again. A missing section would otherwise let a turn run on plain Claude
    Code instructions.
- **Why the session id is in the state value.** `/clear`, `/resume` and `/branch` each start a new session
  id. Text saved under the old id is then never served, so the new session loads its own on its first
  question, without a `SessionStart` hook having to clear anything. On `/resume` that means one fresh
  fetch (live, as requirements 1 wants), not a copy in `$.store`: playbooks can be large and the store is
  4 MiB shared by all sessions.
- **`classic.SessionStart`**: only `$.ui.invalidate('prompt.compose')`, so a cached prompt from the
  previous session is never reused after `/clear` or `/resume`. It returns `next(e)` and gates nothing.
- **Time budget.** A hook has 10 s and a `$.clock` wait counts (design 4.4). Two rounds of 8 s could
  outrun it, so the second call gets what is left: `next.budget.remainingMs` minus a 1.5 s margin, at most
  8 s. Below 1 s left, it fails at once with a timeout message instead of being skipped.
- **`types/index.d.ts`**: add `instructions: { sessionId: string; text: string } | null` (and `setup`,
  so P1-08 only writes it) to `PluginState.yvoke`.
- Nothing is logged with `$.ui.log`: the text is playbook content.

## Files that change

| File | Change |
| --- | --- |
| `plugins/yvoke/src/system-prompt.ts` | new: `loadInstructions` and the three hooks |
| `plugins/yvoke/hooks/register.tsx` | one line: `registerSystemPrompt(on)` |
| `plugins/yvoke/types/index.d.ts` | `instructions` and `setup` keys |
| `plugins/yvoke/tests/system-prompt.test.ts` | new |
| `docs/specs/system-prompt.md`, `docs/specs/README.md` | new spec and its index line |
| `docs/specs/session.md` | one line: the system prompt now applies in a Yvoke session |
| `docs/tasks/v1/plan.md` | tick P1-07, link this plan |
| `docs/tasks/v1/design.md` | 4.3 row: the session-id key and the second-call budget, if building shows more |

Shared with threads running now: `hooks/register.tsx` and `types/index.d.ts` each get one line from
several tasks (P1-09, P3-01, P6-03, P2-05). Those merge without real conflicts; whoever merges second
rebases. `docs/tasks/v1/plan.md` gets one tick per task, same.

## Order of work

Each step starts with a test, seen failing first.

1. `loadInstructions` with a fake `io` (as `server.test.ts` does): success joins base then playbook with
   a blank line; the three calls and their arguments; the `setup` key wins over the defaults.
2. Failures: server down, `ERROR:` from `get_system_prompt`, unknown playbook, area missing, area with no
   default playbook, a `get_playbook` answer that is not JSON or has no `text`; each returns its
   `Yvoke Backend:` error.
3. Budget: the second call's timeout comes from what is left; too little left fails at once.
4. `prompt.submit` hook through the engine (stub `mcp.connect`/`mcp.call`, `session.id`, `state`):
   a plain session passes untouched and calls nothing; first question loads and enters; a failed load
   drops with the message and a second question tries again; a loaded session does not call the server
   again; a new session id loads again.
5. `prompt.compose` hook: section appended last as `session` in a loaded Yvoke session; nothing added in a
   plain session or for another session id.
6. Fail closed: a throw and a timeout in `prompt.submit` drop the question; a throw in `prompt.compose`
   answers the refusal section.
7. Register the line, write the spec, run the checks.

## Risks

- **`prompt.compose` caching.** The declarations say it is cached until invalidated. The plan
  invalidates after a load and on every `SessionStart`. Whether the engine re-renders before the very
  turn the question starts is not checkable in the cloud; P0-06 checks it on Eduard's machine. If it does
  not, the first answer runs without the section, so the test plan for P0-06 gets that case.
- **Subagents.** If `prompt.compose` also renders subagent prompts, they would get the playbook too. The
  input has no agent id to tell them apart. In single-agent mode P2-01 denies delegation, so this is left
  to P6.
- **Claude Code's coding sections stay** in the prompt beside the playbook. Replacing them changes what
  the user gets and is not in this task's entry; if answers suffer, it becomes a decision for Eduard.
- **The refusal `.catch` on `prompt.compose`** also fires in a plain session if `$.state` itself fails,
  breaking that coding turn. Rejected alternative: letting a failure pass through, which runs a Yvoke
  turn without its instructions. The hook does one state read before anything else, so this is unlikely,
  and the failure is loud rather than silent.
- **Rejected: fetch in `prompt.compose`.** It fires on every request and cannot drop a question, so a
  failure could only be a refusal text, not the "question not sent" the task asks for.
- **Rejected: save the text in `$.store` for `/resume`.** That is a cached copy (requirements 1 forbids
  a fallback) and costs store space; one fetch on resume is cheap.

## Proof

- `plugins/yvoke/tests/system-prompt.test.ts`: success, server down, unknown playbook, hook throw and
  timeout (fails closed), plain sessions untouched, new session reloads.
- `npm run check`: docs, both `validate --strict` runs (the `calls:` line names no `$.fs`, `$.process`
  or `$.http`), types, and `claude plugin test plugins/yvoke` (N pass, 0 fail).
