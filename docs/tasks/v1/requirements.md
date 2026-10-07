# Yvoke for Claude v1: requirements

What the plugin must do. Why it exists and who uses it is in [intent.md](intent.md); how it is built is
in [design.md](design.md); the tasks that deliver each requirement, with their **Done when** tests, are in
[plan.md](plan.md). Where a task's **Done when** and this page disagree, fix whichever is wrong in the
same pull request.

## 1. Principles carried over from yvoke-desktop

- **The server is the source of truth** for base instructions, playbooks and profiles. No stale local copy is
  used as a fallback: an unreachable server fails the question and says so (yvoke-desktop decision #14).
- **Deny by default.** Only knowledge-base tools, tool discovery, the safe compute tools and (when declared and
  enabled) restricted web tools may run. No shell.
- **The tests are the contract.** TDD (red → green → refactor); a test only counts once it has been seen
  failing; a bug gets a failing regression test before its fix.
- **Make the bug unrepresentable** rather than documenting it.
- **The preflight check fails open; everything else that enforces policy fails closed.** Decided
  2026-10-05: every enforcing hook (`tool.call`, `prompt.submit`, `prompt.compose`, `agent.spawn`) is
  registered with a `.catch` that answers the safe result (`{ deny }`, `{ drop }`, or a refusal text),
  because the engine otherwise skips a hook that throws or times out and runs the call. Each such task's
  **Done when** includes a test for the throw and the timeout path ([design 4.4](design.md#44-rules-that-are-easy-to-get-wrong)).
- **One session, one setup.** A session's area, mode and playbook are chosen once and cannot change
  afterwards; a different setup is a new session (`/clear`). Decided 2026-10-05 (D-11).
- **Only in a Yvoke session.** Yvoke works in any folder. The mod's rules and UI apply only in a session the
  user started with `/yvoke`. Everywhere else Claude Code behaves as if the mod were not there (P1-10,
  D-13).

## 2. Behaviour in Claude Code

### 2.1 Session setup and system prompt

- `/yvoke`, typed before a session's first question, makes it a Yvoke session until `/clear`. (P1-10)
- A new Yvoke session shows a setup band with **area** (default OIM), **mode** (single agent or one of the
  area's multi-agent profiles, D-15) and, for single agent, **playbook** (default: the area's default
  playbook, `oim-full` for OIM). The lists and defaults come live from the server; prototypes are hidden unless enabled. (P1-08, P1-12)
- Sending the first question locks the setup. From then on the band and the status line show it read-only.
  `/resume` and `/branch` keep it. (P1-08)
- On a single-agent session's first question, when the area has at least two playbooks, a preflight check
  may offer **Switch to …** or **Send anyway**. Any error lets the question through. (P2-06)
- The system prompt is the server's base instructions followed by the playbook's text, fetched when the
  setup locks. A failed fetch drops the question with a `Yvoke Backend:` message and leaves the setup
  unlocked. (P1-01, P1-06, P1-07)
- Playbook skills are not offered in Claude Code; the model cannot start a playbook by itself. (P1-08)

### 2.2 Tool policy

- Allowed: the playbook's knowledge-base tools (or the default set when it declares none), `ToolSearch`,
  `AskUserQuestion`, the compute tools, web tools under the web rules, and delegation while a profile is
  active. Everything else is denied with a message pointing to the Yvoke tools. (P2-01, P2-02, P2-03)
- Web tools only when enabled in configuration **and** declared by the playbook; `WebSearch` limited to the
  configured hosts, `WebFetch` checked against the allow-list, WAF-challenged hosts refused. (P2-04)
- Compute tools `calculate`, `statistics` and `date_diff`, withheld when the playbook declares
  `codeExecution: false`. (P2-05)
- The server's `ask_clarifying_question` is denied in favour of the native `AskUserQuestion`. (P1-09)
- Turn ceilings per question (single agent 25, lead 60, specialist and reviewer 20, configurable). At the
  ceiling the answer is still delivered and marked *stopped at the turn limit*. (P2-08)

### 2.3 Citations

- Citation markers in a reply become links (or a row of source buttons) on `terminal` and `desktop`.
  (P3-01, P3-02)
- Pressing one opens a pane with the document title and version and only the cited passage; the
  surrounding section sits behind a collapsed control labelled as not part of the source. (P3-03)

### 2.4 Multi-agent investigations

- The profile's specialists and reviewer are registered live from the server; role models and budgets come
  from configuration. (D-06, P6-01, P6-02)
- The lead may only delegate, ask the user and call `verify_citations`. (P6-04)
- The reviewer's verdict is strict: the first line is exactly `APPROVED` or `REJECTED`. (P6-03)
- Review is enforced in code: no review means one re-prompt; a rejection or no verdict means a revision, up
  to `maxReviewRounds`; out of rounds means a warning in the answer. The specialist budget is enforced.
  (P6-05, P6-06)
- Each delegation shows as a card; rejected drafts collapse into one row; an unapproved answer carries a
  banner. A specialist's clarifying question reaches the user. (P6-07, P6-08)

### 2.5 Errors and diagnostics

- Server errors are prefixed by origin (`Yvoke Backend:`), as in yvoke-desktop. (P1-03)
- `/yvoke-doctor` reports the Claude Code version against the minimum, whether this is a Yvoke session and
  why, whether the server is connected and signed in, its version and playbook count, and the active
  playbook or profile. It never prints tokens. (P7-10)

## 3. Capability per surface

Every Yvoke Desktop capability, where it lands, and which tasks deliver it. Chapters refer to
`yvoke-desktop/spec/`. "After v1" follows D-07, D-08 and D-10.

| Yvoke Desktop capability | Spec | Claude Code (mod) | Cowork | Chat | Tasks |
| --- | --- | --- | --- | --- | --- |
| Knowledge-base tools (search, sections, graph, records) | 2 | ✅ connector | ✅ | ✅ | P0-09, P1-02 |
| Base instructions from the server | 2 | ✅ | ✅ stub fetches them | ✅ stub fetches them | P1-01, P1-07, P8-01 |
| Playbooks (pick, `/` autocomplete, sticky per conversation) | 1, 2 | ✅ setup band, fixed per session (D-11) | ✅ live stubs | ✅ live stubs | P1-04 – P1-08, P1-12, P8-01 |
| Playbook required for a single-agent question | 1 | ✅ (the default `oim-full` applies) | — | — | P1-08 |
| Playbook preflight check | 1, 2 | ✅ | — | — | P2-06 |
| Deny by default; no shell | 2 | ✅ | after v1 (hooks) | n/a | P2-01, P8-02 |
| Per-playbook tool scoping | 2 | ✅ | after v1 (hooks) | server only | P2-02, P2-03, P8-02 |
| Safe compute tools | 2 | ✅ | — | — | P2-05 |
| Domain-restricted web search/fetch, WAF hosts refused | 2, 6 | ✅ | after v1 (hooks) | — | P2-04, P8-02 |
| Clarifying questions | 1, 2 | ✅ native `AskUserQuestion` | ✅ native | ✅ in text | P1-09 |
| Clickable citations → cited passage panel | 1 | ✅ | after v1 | after v1, links only | P3-01 – P3-04, P8-04 |
| Rate an answer (👍/👎, comment required on 👎) | 1, 7 | after v1, with sync (D-08) | — | after v1 (MCP App) | P4-01 – P4-04, P8-03 |
| Conversations synced to the Yvoke account | 4, 7 | after v1 (D-07) | — | — | Phase 5 |
| Multi-agent profiles: lead, specialists, reviewer | 3 | ✅ | — | — | Phase 6 |
| Enforced review rounds, banner when unapproved | 3 | ✅ | — | — | P6-05, P6-07 |
| MAS trace uploaded to yvoke-web | 3, 7 | after v1 (D-07) | — | — | P5-05 |
| Model and thinking level per conversation | 1 | native | native | native | — |
| Image attachments, stop, copy | 1 | native | native | native | — |
| Local history and search | 4 | native (`/resume`) | native | native | — |
| Error origin prefixes (`Claude:` / `Entra:` / `Yvoke Backend:`) | 1 | ✅ for server errors | — | — | P1-03 |
| Installer, updates, diagnostics | 8 | plugin auto-update | org sync | org sync | Phase 7 |
