# P2-06 Playbook preflight check

**Release:** v1 · **Size:** M · **Type:** feature · **Status:** done

On the first question of a single-agent Yvoke session, before the setup locks, the mod asks the model
whether the chosen playbook fits the question. If another playbook of the area clearly fits better, the user
is offered **Switch to …** or **Send anyway**; either answer sends the question and locks the setup. Every
failure lets the question through as it was. Task entry: [plan.md, P2-06](../plan.md#phase-2--tool-policy);
requirement [2.1](../requirements.md#21-session-setup-and-system-prompt); intent U4.

## What was checked first

Checked in a cloud session on 2026-10-21, against the declarations Claude Code 2.1.306 writes and a
throwaway mod run with `claude plugin test` on **2.1.306 and 2.1.287**:

- A `prompt.submit` hook can call `$.model.complete({ model, system, prompt, maxTokens, effort, timeoutMs })`.
  It never rejects for a provider failure: it resolves `{ isAnswered: false, reason }` (`api-error`,
  `empty-reply`, `aborted`, the last also for `timeoutMs`). It rejects only for a request the engine refuses
  to send (a blocked model, a bad cap). A test answers it with `on('model.complete', …)` returning
  `{ value: result }`.
- The same hook can wait for the user with `$.ui.ask(question, options)`, the engine's own question dialog.
  It resolves the chosen label (or text typed under *Other*) and **rejects when dismissed** and in a `-p`
  run. It is a `tool.call` of `AskUserQuestion`, which P2-01's gate always lets through (`ALWAYS` in
  `src/policy.ts`). A test answers it with `on('tool.call', { tool: 'AskUserQuestion' }, …)` returning
  `{ result: { questions: [], answers: { [question]: label } } }`.
- Neither wait counts against the hook's 10-second budget: the clock stops while a `$` call is in flight,
  except `$.clock` waits (`HookBudget`). So the hook can wait 45 s for the model and as long as the user
  likes for the dialog.
- `$.session.model()` gives the main loop's model as `/model` shows it, which `$.model.complete` accepts.
- yvoke-web 1.3.0 (`PlaybookValidationController`): skips the check for any area's default playbook, leaves
  default playbooks and prototypes out of the candidates, treats a suggestion of the selected playbook as a
  fit, drops a suggestion not on the list, and returns "fits" on every exception. yvoke-desktop 1.3.0
  (`playbookValidation.ts`, `PlaybookValidator.ts`, `AppCore.validatePlaybook`): the same rules with a
  domain-neutral prompt, a 45 s timeout, at least two candidates, and a check not repeated for the same
  playbook. The desktop skips by a configured exclusion list (`oim-full`); the area's default replaces that
  here, as the plan entry says.
- `list_playbooks` already returns `title` and `description` for each playbook (`PlaybookDto`); the
  catalogue P1-08 keeps drops them today.

## Design

### Where the hook sits: first, before the setup band (changes the plan entry)

The plan entry and design 4.4 say the hook registers *before `registerSystemPrompt(on)`*. That is not
enough. P1-08's `prompt.submit` hook (registered first, so outermost) reads the setup into `chosen` before
the question goes down and, once it is back, locks `{ ...chosen, lockedSessionId }` and shows it in the
status line. A preflight hook between it and P1-07 could make P1-07 load the new playbook, but P1-08 would
then lock and show the old one. So `registerPreflight(on)` is the **first line** of `register()`, above
`registerSetup`: it changes the setup before P1-08 reads it, and P1-08 then locks the playbook the user
switched to. It registers with the matcher `{ text: /^/ }` (design 4.4, one unmatched hook per event).

On a switch, `src/preflight.ts` writes `$.state` `yvoke.setup` (and its `$.store` copy `setup:<id>`), using
P1-08's own `pick` so the playbook and its version change together. `setup` then has two writers; the
contract in `types/index.d.ts` says so.

### When it runs

The hook passes the question straight on, with no model call, unless all of these hold:

1. The session is a Yvoke session, and the setup is not yet locked for this session.
2. The question is typed by the user: not a slash command (`e.command`) or a meta prompt (`e.isMeta`), and
   not blank.
3. Mode is single agent, the setup names a playbook, and the catalogue is loaded (`ok`) and lists the
   setup's area. (If it is not loaded, P1-08's hook below loads it or drops the question; the check is
   skipped, never the question.)
4. The playbook is not the area's default (yvoke-web and yvoke-desktop 1.3.0).
5. The candidates, the area's playbooks shown in the band minus the area's default, number at least two
   (the chosen one and one other). Prototypes are in the list only when the band shows them.
6. This session has not already checked this playbook (`$.state` `yvoke.preflight` =
   `{ sessionId, playbook }`). A first question that P1-07 then drops, sent again, is not checked twice;
   changing the playbook in the band makes it a new check, as in the desktop.

### The check

`$.ui.status('Yvoke: checking the playbook…')` while it waits, cleared before the question goes down
(P1-08 then sets the locked line). Then one `$.model.complete`:

- `model`: `$.session.model()`, the session's own model, as web and desktop use the conversation's model.
- `system`: the desktop's domain-neutral validator prompt (`name | title | description` per candidate, the
  selected name, the conservative rules, raw JSON `{ plausible, reason, suggestedPlaybookName }`).
- `prompt`: the question's text. `maxTokens` 500 (the web's cap), `effort: 'low'`, `timeoutMs` 45 000.

The reply is read by the desktop's `parseValidation`, ported: JSON dug out of fences or prose; anything but
`plausible: false` with a suggestion that names a candidate (by name or title, any case) and is not the
selected playbook counts as a fit.

### What the user sees on a better match (D-27)

**Decided 2026-10-22 (Eduard):** the engine's question dialog, `$.ui.ask`, with the header *Playbook* and:

> *&lt;reason&gt;* Switch to **&lt;title&gt;** (&lt;name&gt;) for this session?
> — **Switch to &lt;name&gt;** · **Send anyway**

- **Switch to …**: the setup's playbook becomes the suggestion, then the question goes down; P1-07 loads the
  new playbook and P1-08 locks it and shows it (*OIM · Single agent · oim-schema v2*).
- **Send anyway**, text typed under *Other*, or the dialog dismissed (Esc, or a `-p` run): the question goes
  down with the playbook as chosen. Dismissing counts as *Send anyway* because the check fails open.

The plan entry says *show a band*. A band cannot do this inside the hook: a `prompt.submit` hook can wait on
a `$` call, but a band button is pressed in a later `ui.render` handler, so the hook would have to drop the
question, keep its text, and resubmit it with `$.prompt.submit` after the press, which brings back
dropped-then-resent questions, a pending state in the band, and a question the user no longer sees in the
prompt. The dialog keeps the question in flight and needs no state beyond the hook. D-27 in `design.md`.

### Fails open

Every error sends the question unchanged with the playbook as chosen, and nothing is shown but the cleared
status: `isAnswered: false` (any reason, the 45 s timeout included), a `$.model.complete` that rejects, an
unreadable reply, an unknown or same-playbook suggestion, a failed `$.session.model()`, state or store
call, and the hook throwing anywhere before `next`: everything before `next(e)` is caught inside the hook,
which then calls `next(e)` itself. Its `.catch` drops with the setup band's message instead (see *Found
while building*). A switch whose setup write fails sends the question with the old playbook. Nothing is logged: the question and the model's reply never
reach `$.ui.log`.

## Files that change

| File | Change |
| --- | --- |
| `plugins/yvoke/src/preflight.ts` | new: the rules (`candidates`, `buildValidatorPrompt`, `parseValidation`, `shouldCheck`) with no `$`, and `registerPreflight(on)` |
| `plugins/yvoke/hooks/register.tsx` | `registerPreflight(on)` as the first line, with a comment why |
| `plugins/yvoke/src/setup.ts` | the catalogue keeps each playbook's `title` and `description` (`toCatalogue`) |
| `plugins/yvoke/types/index.d.ts` | catalogue playbook `title`, `description`; new key `preflight`; `setup` also written by `src/preflight.ts` on a switch |
| `plugins/yvoke/tests/preflight-rules.test.ts` | new: the rules, port of desktop's `playbookValidation.test.ts` (prompt and parse cases; the SDK and exclusion-list cases do not apply) |
| `plugins/yvoke/tests/preflight.test.ts` | new: the hook through the engine with the whole mod loaded |
| `plugins/yvoke/tests/setup-rules.test.ts` | the catalogue's expected playbooks gain `title` and `description` |
| `docs/specs/preflight.md`, `docs/specs/README.md` | new spec and its index line |
| `docs/specs/setup.md`, `docs/specs/system-prompt.md` | the hook order: preflight first |
| `docs/tasks/v1/design.md` | D-27 (recorded with the plan); 1.2 and 1.3 (dialog, hook order); 4.4 the hook order note; 4.3 row for `$.ui.ask` |
| `docs/tasks/v1/requirements.md` | 2.1 line: the suggestion comes in a dialog (D-27) |
| `docs/tasks/v1/plan.md` | the P2-06 entry (order, dialog), tick with the PR link |

## Order of work

Each step: the test first, seen failing, then the code, then `npm run check`.

1. **Catalogue keeps title and description.** `setup-rules.test.ts` expects them; `toCatalogue` keeps them
   (empty string when missing or not a string); the type follows.
2. **Rules.** `preflight-rules.test.ts`: the validator prompt (every candidate as `name | title |
   description`, the selected name, raw JSON asked for); `parseValidation` (fit; better match returns the
   canonical name and the reason; fenced or chatty JSON; suggestion by title and in another case; unknown
   suggestion; same playbook by name and by title; prose, empty, broken JSON, non-boolean verdict, a
   rejection with no suggestion); `candidates` (the area's default left out, prototypes as the band shows
   them, other areas' playbooks never); `shouldCheck` (each skip of *When it runs*).
3. **The hook, fit and skips.** `preflight.test.ts` with the whole mod: a fit sends the question unchanged,
   one model call with the session's model, `effort: 'low'`, `maxTokens: 500`, `timeoutMs: 45000`, the
   question as the prompt, no dialog. No model call for: a plain session, the area's default playbook, an
   area with one other playbook or none, multi-agent mode, a slash command, a locked setup, a second
   question, the same playbook checked before in this session after a dropped first question. A new check
   after the playbook was changed in the band.
4. **Better match.** Dialog shown with the reason, title and both options; **Switch** sends the question,
   P1-07 fetches the suggested playbook, the setup locks with it and its version, the status line and the
   store show it; **Send anyway**, *Other* text and a dismissed dialog send with the chosen playbook.
5. **Fails open.** Timeout (`aborted`), `api-error`, `empty-reply`, a rejecting `$.model.complete`, an
   unreadable reply, an unknown suggestion, a suggestion of the selected playbook, a failing
   `$.session.model()`: each sends the question with the playbook as chosen, and the status is cleared.
6. **Status.** `$.ui.status` shows the checking line before the model call and is cleared before the
   dialog and the question.
7. **Hook order.** The module loads with the new matcher; the switch test of step 4 fails if
   `registerPreflight` is moved below `registerSetup` (seen red once by moving it).
8. **Docs**: specs, design, requirements, plan; `finish-task`.

## Found while building

- **The outermost hook's `.catch` answers for the whole event.** The first version's `.catch` was
  `(_$, e, next) => next(e)`. It turned two existing tests red (`setup.test.ts`, *a failing hook drops the
  question*; `system-prompt.test.ts`, *a throw in prompt.submit drops the question*): a `$` call failing
  in the setup band's or the instructions' hook beneath was answered by the preflight's `.catch`, now the
  outermost, and its `next(e)` let the question through bare. So the hook catches its own failures inside
  its body and always calls `next(e)` itself, and the `.catch` is
  `next.called ? next(e) : { drop: DROP_FAILED }`, the setup band's message, which keeps both tests as
  they were. Recorded in design 4.4.
- **`$.ui.status` does not throw**: an engine that cannot show it drops it and the hook goes on (seen in
  the throwaway mod). The `try` around it and the test for a failing status line were taken out; the
  mutation that removed the `try` left every test green.
- **A failing `$.state.set` cannot be staged**: the kit has its own state, and a test hook on `state.set`
  that throws is skipped. The planned test for a switch whose setup write fails was taken out; the write
  sits inside the caught body like every other step.
- **Claude Code 2.1.287's `prompt.submit` has no `command` or `isMeta`** (its type lacks both; CI's
  typecheck on 2.1.287 failed). The hook reads them through a wider type, so on 2.1.287 neither skip
  applies there (not checked whether 2.1.287 raises `prompt.submit` for a slash command at all).
- **Ported cases**: the desktop's parser reads the first `{` to the last `}`, so a JSON array around an
  object is read as that object; no test claims otherwise.
- **Merge with P2-02** ([#28](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/28)): both add fields
  to the catalogue's playbook entries in `setup.ts`, `types/index.d.ts` and `setup-rules.test.ts`.
  Whichever merges second keeps both (`tools` and `title`, `description`).

## Risks

- **A switch locked under the wrong playbook.** The reason for registering first; pinned by step 4's test
  asserting the locked setup, the status line and P1-07's `get_playbook` name together, and by step 7.
- **The dialog on Claude Desktop.** `$.ui.ask` draws the AskUserQuestion dialog on both surfaces in the test
  kit; whether Claude Desktop's Code tab draws it from a `prompt.submit` hook is not checked here. If it does
  not, the call rejects and the question goes through (fails open), so the worst case is no suggestion. A
  check on Eduard's machine is listed under Proof; it uses a fresh `/yvoke` session only, never `/resume` or
  `/branch` (D-25).
- **The user's quota and time.** One small call per session at most (≤ 500 output tokens, low effort), only
  for a non-default playbook in an area with a choice. A slow model holds the question up to 45 s with the
  checking line shown, as in the desktop.
- **The question text goes to the model twice** (check and answer). It goes through the session's own client
  and credentials, as every turn does; nothing is logged.
- **Rejected:** registering just before `registerSystemPrompt` (the plan entry's wording; see *Where the hook
  sits*); putting the check inside P1-08's hook (keeps one writer of `setup`, but mixes a second feature into
  a 450-line module that other threads also touch); a band with drop-and-resend (D-27); the desktop's
  exclusion-list setting and on/off switch (the area's default replaces the list; no setting was asked for);
  `$.model.fork` (it sends the whole transcript, and there is none before the first question).

## Proof

- `plugins/yvoke/tests/preflight-rules.test.ts`, `plugins/yvoke/tests/preflight.test.ts`: the **Done when**
  cases: fit, better match, timeout, unparseable reply, unknown suggestion, the area's default playbook and a
  suggestion of the same playbook, plus the skips, the switch locking the new playbook, and the status.
- `npm run check` (docs, both validates, typecheck, tests) on Claude Code 2.1.306 and 2.1.287, output in the
  PR; CI runs 2.1.287 and latest.
- On Eduard's machine, in a fresh session (no `/resume` or `/branch`), local yvoke-web: `/yvoke`, pick a
  non-default playbook, ask a question that fits another one; the dialog appears in the terminal and in the
  Desktop Code tab, and **Switch** shows the new playbook in the locked band.
