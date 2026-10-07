# P6-03 Reviewer agent

**Release:** v1 · **Size:** S · **Type:** feature · **Status:** planned

**Done when** (from [plan.md](../plan.md)): a regression test for "NOT APPROVED" fails before the fix.

Facts below were checked on 2026-10-07 against yvoke-desktop `main` (`src/main/agent/orchestration.ts`,
`src/main/agent/translate.ts`, `tests/orchestration.test.ts`, `tests/review.test.ts`), yvoke-web `main`
(`chat/orchestration/OrchestrationService.java`, `SubmitReviewTool.java`, `Verdict.java`) and the mod API
declarations that Claude Code 2.1.291 writes (`AgentSpec`, `$.agent.register`).

## What the module does

`plugins/yvoke/src/reviewer.ts` holds the two pure pieces of the reviewer. Nothing in it calls `$`, so it
needs no `io` closures and has no hook of its own.

1. **The reviewer's agent definition.** `reviewerAgent(input)` returns the object `$.agent.register` takes
   (`AgentSpec`), for the agent type `yvoke:reviewer`:
   - `prompt`: the profile's reviewer playbook text, then the runtime adapter (below). **No base
     instructions**: the `get_system_prompt` text is not prepended, as in yvoke-desktop, because the
     reviewer writes a verdict, not an answer, and the answer-format rules are noise to it.
   - `tools`: exactly one, the `verify_citations` tool. The caller passes its full name, which it gets
     from the server name `$.mcp.connect('yvoke')` answers (usually `mcp__plugin_yvoke_yvoke__verify_citations`,
     D-05). No `get_section`: the server's reviewer playbook says it does not have it, and yvoke-desktop
     removed it for that reason.
   - `omitClaudeMd: true`, so the user's and the project's CLAUDE.md do not reach it either.
   - `model`, `effort` and `maxTurns` are passed through from the caller (P6-01 reads them from the
     profile; the reviewer's turn ceiling is 20 by default, requirements.md).
   - `description`: "Validates the composed answer against the gathered evidence. Never searches anew."
     (yvoke-desktop's line).
2. **The runtime adapter text.** yvoke-desktop's `REVIEWER_ADAPTER`, reworded from "Desktop runtime" to
   "Runtime": the server's reviewer playbook ends with a `submit_review` tool call that only the web harness
   has, so the adapter tells the reviewer to ignore that call and instead begin its reply with `APPROVED`
   or `REJECTED` alone on the first line, with the feedback as prose beneath it.
3. **Strict verdict parsing.** `parseVerdict(text)` returns `{ approved: true | false, feedback? }` or
   `null` for "no clear verdict":
   - Leading blank lines and the spaces around the first line are ignored.
   - The first line must then be exactly `APPROVED` or `REJECTED`, in capitals. Anything else is `null`:
     `NOT APPROVED`, `Approved.`, `**APPROVED**`, `Verdict: APPROVED`, an empty reply, or a verdict that
     only appears further down.
   - The feedback is everything after the first line, trimmed; absent when empty.

   This is the fix the plan asks for. yvoke-desktop's `parseVerdict` falls back to finding `APPROVED`
   anywhere in the text when `REJECTED` is absent, so a reviewer that writes "NOT APPROVED" is read as an
   approval and an unreviewed answer ships. A stricter reading costs at most one revision round when the
   reviewer formats its verdict badly; a looser one can let a rejected answer through.

What happens with the verdict (re-prompt, revision rounds, the warning when rounds run out) is P6-05. Who
registers the agent, with which playbook and models, is P6-01. Making sure a delegation to the reviewer
runs in the foreground, so its verdict is in hand, is P6-04/P6-05's `agent.spawn` work.

## Files that change

| File | Change |
| --- | --- |
| `plugins/yvoke/src/reviewer.ts` (new) | `REVIEWER_AGENT = 'reviewer'`, `REVIEWER_ADAPTER`, `reviewerAgent`, `parseVerdict`. |
| `plugins/yvoke/tests/reviewer.test.ts` (new) | The tests below. |
| `docs/specs/multi-agent.md` (new) and `docs/specs/README.md` | The reviewer's definition and the verdict rule, once merged. |
| `docs/tasks/v1/plan.md` | Tick P6-03. Its entry says "Port `review.test.ts`"; the verdict tests actually live in yvoke-desktop's `orchestration.test.ts` (`parseVerdict`, the reviewer's tools and prompt), and most of `review.test.ts` tests the review loop. So P6-03 ports the former, and P6-05's entry gains "Port `review.test.ts`". |

`hooks/register.tsx` does not change: the module registers nothing. P6-01 calls `$.agent.register(reviewerAgent(…))`
once it has the profile. Other threads running now (P1-07, P0-03, P1-09, P3-01, P2-05) do not touch these
files, except that several add a row to `docs/specs/README.md` and tick `plan.md`; those are one-line merges.

## Order of work

Each step starts with a test that is seen failing, then the code that makes it pass.

1. **Port yvoke-desktop's parser as it is**, with its six `parseVerdict` tests, so the starting point is the
   behaviour being replaced.
2. **Regression test, own commit.** `parseVerdict('NOT APPROVED\nclaim 2 is unsupported')` is `null`. Run it
   and see it fail against the ported parser (it reads as approved). Record the commit hash here.
3. **Make the parser strict.** Add tests for `Approved.`, `**APPROVED**`, `Verdict: APPROVED`, a verdict
   below a paragraph of reasoning, `APPROVED with notes`, empty and whitespace-only replies (all `null`),
   and for leading blank lines and surrounding spaces (still read). Change the three desktop tests that
   assert the loose reading (trailing punctuation, "Verdict: APPROVED" further down, the deliberation
   fallback) to expect `null`, each with a comment saying why. Then replace the fallback with the strict rule.
4. **The agent definition.** Tests: the prompt starts with the playbook text and ends with the adapter and
   contains nothing else (in particular no base instructions); `tools` is exactly the one name passed in;
   `omitClaudeMd` is set; model, effort and maxTurns pass through and are left out when not given; the name
   is `reviewer`. Then the code.
5. **The adapter.** Tests that it names both verdict words, says they go alone on the first line, and tells
   the reviewer to ignore `submit_review`. Then the text.
6. Specs, plan tick, `finish-task`.

## Risks

- **A reviewer that bolds or punctuates its verdict loses a round.** Accepted: requirements.md asks for an
  exact first line, the adapter tells the model the exact form, and an unclear verdict is treated as a
  rejection, never as a pass. Tolerating `**APPROVED**` or `Approved.` was considered and rejected for
  now: each tolerance is a new way to misread, and the observed failure is in the other direction. If
  pilots show many lost rounds, widen it to a short list of exact spellings, still never a substring match.
- **The server's reviewer works differently.** yvoke-web's reviewer ends with a `submit_review` tool call
  carrying `approved`, `feedback`, `unsupported_claims` and `citation_fixes` (`Verdict.java`). The plugin
  could register a mod tool of the same name and get the same structure. Rejected for this task: the plan,
  requirements and yvoke-desktop all use a plain-text verdict, and a mod tool adds a `tool.call` hook and
  a way to end the turn without calling it. It is worth a decision later if P6-05 needs the two lists to
  tell "renumber a citation" from "go and search again", as the server does.
- **Whether `$.agent.register` with `tools` limits an MCP tool to one subagent** while the parent keeps
  others, and whether `omitClaudeMd` takes effect, is checked when P6-01 registers the agent (spike P0-07
  covers subagents). This task only builds the definition, so it is not blocked.
- **The tool's full name.** The caller passes it in, so a connector-provided server (another prefix) works
  without a change here.

## Proof

- `plugins/yvoke/tests/reviewer.test.ts`, in particular the "NOT APPROVED" test, with its red run shown
  against the ported parser at the commit recorded in step 2.
- The verification table in `AGENTS.md`: `node scripts/check-docs.mjs`, `claude plugin validate --strict .`,
  `claude plugin validate --strict plugins/yvoke` (the `calls:` line unchanged), `npm run typecheck`,
  `claude plugin test plugins/yvoke`.
