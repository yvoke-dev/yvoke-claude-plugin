# yvoke-desktop coverage check for PLAN.md draft v3

**Date:** 2026-10-05 · **Compared:** every capability, rule and limit in `yvoke-desktop/spec/` chapters 1–8
(and `src/main/agent/orchestration.ts` where the spec was ambiguous) against PLAN.md section 5 (parity map)
and the tasks in section 6.

**Result.** The plan carries over everything that makes an answer *grounded and governed*: knowledge-base
tools, base instructions, playbooks, the required-playbook gate, preflight, deny by default, scoping,
compute, web rules, clarifying questions, citations, rating, sync, multi-agent review and the error prefix.
Most of what it leaves out is either native to Claude Code or tied to the desktop app's own shell. The
gaps below are behaviours the desktop app has, or relies on, that **no task delivers yet**, plus places where
Claude Code changes the behaviour in a way the plan does not mention.

---

## Gaps: behaviour no task delivers yet

| # | yvoke-desktop behaviour (spec) | Why it matters here | Proposed |
| --- | --- | --- | --- |
| C1 | **The lead verifies its own citations** (ch. 3; `orchestration.ts` grants the lead exactly `Agent`/`Task`, `ask_clarifying_question` and `verify_citations`) | P6-04 says the lead "may only delegate and ask the user" and denies knowledge-base tools. Ported literally, that removes `verify_citations` from the lead, and the server's orchestrator playbook names it. | P6-04: the lead's allow list is delegation, `AskUserQuestion` and `verify_citations`. Port the grant from `orchestration.ts`, not from the prose. |
| C2 | **Changing the playbook restarts the session**, so the old playbook's instructions and tool list are gone (ch. 2) | In Claude Code a skill's text is injected into the conversation. After a switch, the previous playbook's instructions are still in context, and they contradict the new one. Scoping (P2-02) follows the switch, but the instructions do not. | A decision (proposed **D-11**, below). |
| C3 | **New playbooks appear within about a minute** (ch. 7: list cached for a minute) | With generated skills, a new playbook reaches users only after the catalogue-sync PR (P1-05, "within a day"), a release and the user's plugin auto-update. Live text (D-04) does not make the *list* live. | Part of D-11. |
| C4 | **Turn ceilings**: 25 per single-agent question, 60 for the lead, 20 per specialist and the reviewer (ch. 2, 3, 6) | They are the only real cost bound in multi-agent mode (the specialist budget was advisory). An interactive Claude Code session has no per-question ceiling. | New task **P6-09**: count `turn.step` per turn and agent, and stop with `$.turn.abort` or a `{ deny }` on further tool calls at configured ceilings. Unlike the desktop, deliver what exists with a warning rather than discarding the turn. |
| C5 | **Model and thinking level per role, including the lead** (ch. 3, 6) | Specialists and the reviewer get theirs from their agent definitions (P6-02). The lead is the main loop, so it runs on whatever `/model` says. | P6-02: set the lead's model and effort with a `turn.step` hook on the main loop (`next({ ...e, model, effort })`) while a profile is active. |
| C6 | **The lead's instructions** are base instructions + the lead's playbook + the runtime adapter text (ch. 3) | P6-04 says the adapter text is "appended to its instructions" without a mechanism. The lead is the main loop, so this is a system-prompt change. | P6-04: a `prompt.compose` section (`scope: 'session'`), invalidated when the profile changes. |
| C7 | **No nested delegation** (ch. 3: a specialist that delegates vanishes from the record) | With `agentId` on `tool.call`, the mod can enforce this rather than just tolerate it. | P6-04: deny `Agent`/`Task` when `e.agentId` is set. |
| C8 | **A held-back draft is dropped from the answer; the out-of-rounds warning is written into the answer text** (ch. 3) | Claude Code keeps every turn in the transcript. A rejected draft stays visible above the revision, and the revision prompt queued by `$.prompt.submit` shows as its own message. `turn.complete` can only add a line under the answer, and copy copies the record. | P6-05/P6-07: decide the visible shape. Suggested: `ui.render` on `AssistantMessage` collapses superseded drafts into a "Draft, rejected by reviewer" row; the warning row is drawn under the final answer. Record that the stored transcript keeps the draft. |
| C9 | **Only the text after the verdict line is fed back; failed specialists are left out of the evidence** (ch. 3) | Porting details that change what the lead is told. The second is a desktop weakness worth fixing. | P6-05: port "after the verdict line" with `review.test.ts`; list failed delegations in the evidence as "failed" rather than omitting them. |
| C10 | **Prototype playbooks and profiles hidden unless *Show prototypes*** (ch. 1) | P1-04 keeps prototypes out of the generated set and P6-01 hides them, but nothing lets a knowledge-team member turn them on. | A `showPrototypes` `userConfig` option read by `/yvoke-playbook` and `/yvoke-profile`; with generated skills, a separate `yvoke-prototypes` plugin in the same marketplace. |
| C11 | **The pay-per-token API key is removed so billing stays on the subscription** (ch. 2, 5) | Claude Code can bill an `ANTHROPIC_API_KEY` from the environment instead of the subscription, and a plugin cannot remove it from the engine's environment. Desktop users who moved over would silently start paying per token. | P7-10 `/yvoke-doctor`: report the billing mode (subscription or API key); P7-05 user guide: say so. |
| C12 | **A development token against a server with mock security** (ch. 5, 6) | P1-02 fixes the server URL in `.mcp.json`. Contributors and CI need a way to point the plugin at a local or dev yvoke-web without editing the shipped file. | P0-03: a `plugins/yvoke-dev/` overlay or a `--mcp-config` file for a local server, documented; CI tests stub `mcp.call` instead (section 10.2). |
| C13 | **Delete a conversation everywhere** (ch. 1, 4) | Only matters if D-07 syncs conversations: a conversation deleted in Claude Code would stay in the Yvoke account. | Phase 5 (if D-07 says yes): a `/yvoke-delete` command, or a documented "delete in yvoke-web". |

## Proposed decision D-11

> **Decided 2026-10-05:** mod state, chosen once per session in a setup band (area, mode, playbook; defaults OIM, single agent, `oim-full`). See PLAN.md D-11. C2 and C3 are resolved by it.

**D-11** `PO` · `plugin` — **In Claude Code, are playbooks skills or mod state?**

- *Skills* (the plan today): one generated skill per playbook, invoked as `/yvoke:<name>`; its text enters the
  conversation; the list changes only with a plugin release (C3); a switch leaves the old text in context (C2).
- *Mod state*: `/yvoke-playbook <name>` (P1-08) is the only picker in Claude Code. It reads the live list
  from the server, and the active playbook's text is a `prompt.compose` section that is replaced on every
  switch. That gives the desktop's behaviour for C2 and C3 and makes D-04 irrelevant for Claude Code. Skills
  are still generated, but for Chat and Cowork only (P8-01), and hidden in Claude Code (`agent.offer` does
  not apply to skills, so: `disable-model-invocation` plus a `skill.prompt` hook that redirects to
  `/yvoke-playbook` inside the Yvoke folder).
- 🔍 Needs from P0-06: whether `prompt.compose` sections can change mid-conversation without breaking the
  prompt cache too often (one invalidation per switch is the desktop's cost too), and how `/` autocomplete
  presents a mod command's arguments.
- Recommendation: **mod state** for Claude Code, because it is the only option that keeps the desktop's
  "switch replaces the instructions" and "new playbooks within a minute" rules.

## Carried over by Claude Code itself (no task needed)

| yvoke-desktop | In Claude Code |
| --- | --- |
| Trace under each answer (reasoning, tool calls, results) | Tool rows in the transcript, ctrl+o to expand. A `ui.render` summary line is possible but not needed for parity. |
| Streaming, Markdown, code, tables | Native. Mermaid diagrams, which the desktop draws, show as source in the terminal (🔍 check the Desktop Code tab). |
| Stop | Native (Esc). Better than the desktop: the question and the partial answer are kept (desktop decision #1). |
| Copy an answer | Native. |
| Image attachments | Native. |
| Model and thinking level per conversation | `/model`, effort. |
| Clarifying questions, including several questions and multi-select | `AskUserQuestion` (better than the desktop's single-answer card). |
| Local history and search | `/resume` with search. |
| Follow-ups remember the conversation | The session transcript. `/resume` works on the same machine only, as in the desktop (decision #2). |
| Claude sign-in and checks | Claude Code's own `/login` and `/status`. |
| Logs | `claude --debug` and the debug log; the mod writes with `$.ui.log`. |
| Updates | Plugin auto-update (P7-03), which fixes desktop decisions #11 and #12 if P7-02 holds. |

## Deliberately left behind (worth listing in PLAN.md section 7)

- **The desktop's local conversation cache**, its 200-conversation sidebar and server-side titles: Claude
  Code's own transcripts replace them. If D-07 syncs, conversations still appear in yvoke-web, but Claude Code
  never lists conversations from the server.
- **Restoring a conversation from the server onto a second machine.** Not possible from Claude Code.
- **The settings panel.** `/config` rows for `userConfig` plus managed `pluginConfigs` replace it; that is
  also the managed channel the desktop never had (its decision #8).
- **Rename a conversation.** Claude Code's `/rename` names the local session only.
- **Installer and signing** (ch. 8, `spec/signing.md`): not needed for a plugin.

## Desktop defects the plan already fixes (keep them as tests)

Reviewer "NOT APPROVED" read as approved (P6-03), advisory specialist budget (P6-06), `log`/`ln` description
(P2-05), duplicate turns on resend (P5-02), stale sync dot (P5-03), reviewer recorded as "reviewer" (P5-05),
a specialist's clarifying question with nothing on screen (P6-08), feedback blocked until sync (D-08), no
managed configuration (P7-03), and the release workflow not running tests (P7-02).
