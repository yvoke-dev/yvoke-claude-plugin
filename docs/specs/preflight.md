# Playbook preflight check

## Behaviour

- On the first question of a single-agent Yvoke session (see [session.md](session.md)), before the setup
  locks ([setup.md](setup.md)), the mod asks the model whether the chosen playbook fits the question.
- The check runs only when all of these hold; otherwise the question goes straight through with no model
  call:
  - the question was typed: not a slash command, not a prompt the user does not see, not blank;
  - the setup is single agent, unlocked, and names a playbook the catalogue lists for its area;
  - the playbook is **not the area's default** (yvoke-web and yvoke-desktop 1.3.0);
  - the area offers at least one non-default playbook besides the chosen one. Default playbooks are
    never candidates, so never suggested; prototypes are candidates only when the band shows them;
  - this session has not checked this playbook already. A first question dropped further down (for
    example by the instructions failing to load) and sent again is not checked twice; choosing another
    playbook in the band makes it a new check.
- While the model works, the status line says *Yvoke: checking the playbook…*; it is cleared before the
  dialog or the question.
- The check is one `$.model.complete` call: the session's own model (`$.session.model()`), no tools,
  `effort: 'low'`, at most 500 output tokens, 45 seconds. The system prompt is yvoke-desktop's
  domain-neutral validator prompt listing each candidate as `name | title | description` and the chosen
  playbook; the question is the prompt. The model is asked for raw JSON
  `{ plausible, reason, suggestedPlaybookName }`.
- Only a reply with `plausible: false` whose suggestion names a candidate (by name or title, any case)
  other than the chosen playbook is a better match. Fenced JSON or JSON inside prose is read.
- On a better match, Claude Code's question dialog (D-27) asks, under the header *Playbook*:
  *The question is about table columns. Switch to Schema (oim-schema) for this session?* with
  **Switch to oim-schema** and **Send anyway**.
  - **Switch to …**: the setup's playbook (and its version) becomes the suggestion, then the question
    goes on. The instructions load for the new playbook (they are loaded per setup, see
    [system-prompt.md](system-prompt.md)) and the setup locks with it (*OIM · Single agent · oim-schema v2*).
  - **Send anyway**, text typed under *Other*, or the dialog dismissed: the question goes on with the
    playbook as chosen.
- **The check fails open**: a timeout, an API error, an empty or unreadable reply, a suggestion that is
  unknown, the chosen playbook or the area's default, a model the engine refuses, a session model that
  cannot be read, or a failed state write: the question goes on as it would without the check. One
  exception: when the switch was written to `$.state` but its copy in `$.store` fails, the switch stands
  for this session and only `/resume` misses it.
  Nothing is logged; the question and the reply never reach `$.ui.log`.
- The hooks beneath still fail closed. The preflight hook is the mod's outermost on `prompt.submit`, so a
  failure in the setup band's or the instructions' hook is answered by its `.catch` with the setup band's
  drop: *Yvoke Backend: the session could not be set up, so the question was not sent.*

## Interfaces

| Piece | Where |
| --- | --- |
| Rules (`shouldCheck`, `buildValidatorPrompt`, `parseValidation`, `dialogFor`) and the hook | `plugins/yvoke/src/preflight.ts` |
| Hook | `prompt.submit` with the matcher `{ text: /^/ }`, registered **first** in `hooks/register.tsx`, above `registerSetup`: the setup band's hook reads the setup before the question goes down and locks that copy, so a switch has to be written before it reads |
| Model call | `$.model.complete({ model, system, prompt, maxTokens: 500, effort: 'low', timeoutMs: 45000 })` |
| Dialog | `$.ui.ask(question, { header: 'Playbook', options })`, an `AskUserQuestion` tool call that the tool gate always allows ([tool-policy.md](tool-policy.md)) |
| `$.state` `yvoke.preflight` | `{ sessionId, playbook }` the check last ran for; written only here |
| `$.state` `yvoke.setup`, `$.store` `setup:<session id>` | written here on **Switch**, with the setup band's own `pick` |
| `$.state` `yvoke.catalogue` | read for the candidates; each playbook carries `title` and `description` |

## Tests

- `plugins/yvoke/tests/preflight-rules.test.ts`: the validator prompt; reading the reply (fit, better
  match, fences and prose, suggestion by title and in another case, unknown suggestion, the chosen
  playbook by name and title, prose, empty, broken JSON, non-boolean verdict, a rejection with nothing
  to offer); which questions are checked and against which candidates; the dialog text. Ported from
  yvoke-desktop's `playbookValidation.test.ts`.
- `plugins/yvoke/tests/preflight.test.ts`, with the whole mod: a fit and the call it makes; a better
  match with Switch (the new playbook loads and locks), Send anyway, *Other* and a dismissed dialog; failing
  open on a timeout, an API error, an empty reply, a refused request, an unreadable reply, an unknown
  suggestion, the chosen playbook, the area's default and an unreadable session model; the checking line
  and its order; no check for the area's default playbook, an area with no other playbook, a plain
  session, a second question, a resent first question; a new check after the playbook changed, and that
  playbook's instructions loading for the resent question. Moving the hook below `registerSetup` turns
  the Switch test red. Run on Claude Code 2.1.287 and latest.
- `plugins/yvoke/tests/setup.test.ts` and `system-prompt.test.ts`: a failing hook beneath still drops
  the question.

## History

- P2-06 ([#30](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/30)): the preflight check; D-27
  (a dialog, not a band).
