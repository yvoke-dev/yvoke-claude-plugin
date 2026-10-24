# Yvoke for Claude v1: intent

**Author:** Eduard · **Status:** accepted (decisions D-01 through D-26, taken 2026-10-05 to 2026-10-21) · **Updated:** 2026-10-21 (D-26: v1 is Claude Code only)

Why we are building the plugin, who it is for, and what they will do with it. What the plugin must do is in
requirements.md, how it is built is in design.md, and the work and its
progress are in [plan.md](plan.md).

**What we are building:** one Claude plugin that brings Yvoke Desktop's capabilities into Claude. It
works fully in **Claude Code** (the Desktop app's **Code** tab and the terminal) through a **mod**. v1 is
Claude Code only (D-26, 2026-10-21); a reduced form for **Chat** and **Cowork** is listed to check after v1.

**Reference implementation:** [`yvoke-dev/yvoke-desktop`](https://github.com/yvoke-dev/yvoke-desktop). Its
`spec/` chapters say *what* each feature is for; its `tests/` say *exactly* how it behaves.

## 1. Problem

Consultants and support engineers get grounded, cited answers from the Yvoke knowledge base through Yvoke
Desktop, a separate Electron app that runs its own agent loop and has to be installed, signed in and kept
up to date on its own. Many of them already work in Claude, on their organization's Claude plan. Today they
switch apps to ask Yvoke, and in Claude itself they have no playbooks, no enforced tool rules and no
verified citations.

## 2. Goal

Consultants and support engineers should get the same grounded, cited, playbook-scoped answers from the
Yvoke knowledge base inside Claude that they get in Yvoke Desktop, on their own Claude subscription, without
installing a separate app.

**Success looks like:**

- A consultant installs one plugin (or IT installs it for them), opens the Code tab in Claude Desktop, accepts
  or changes the session's area, mode and playbook (defaults: OIM, single agent, `oim-full`), asks a
  question, and gets an answer whose citations open the cited passage.
- The playbook decides which Yvoke tools the assistant may use, and that rule is enforced in code, not only
  requested in a prompt.
- A multi-agent profile runs lead → specialists → reviewer, with review enforced in code.
- Behaviour is pinned by automated tests in this repo, as it is in yvoke-desktop.
- After v1, once conversations sync: 👍/👎 feedback on an answer reaches yvoke-web (D-07, D-08).

## 3. Users

- **Consultants and support engineers** ask questions about OIM and get cited answers. Some of them also use
  Claude Code for coding on the same machine.
- **An organization admin** adds the Yvoke connector and pushes the plugin. Users are on Claude Team or
  Enterprise (D-01).
- **The yvoke-web team** owns the server, its playbooks, profiles and base instructions.

## 4. Use cases

Each use case names the tasks in [plan.md](plan.md) that deliver it.

| # | Who | Use case | What they see | Tasks |
| --- | --- | --- | --- | --- |
| U1 | Admin, once | **Roll it out** | Adds the Yvoke connector for the organization and pushes the plugin; the pilot installs it from the Git marketplace instead. Nothing else on the machine is locked down. | P7-03, P7-04 |
| U2 | Consultant, first use | **Install and sign in** | The first time the plugin talks to Yvoke, a browser window opens for the usual Microsoft sign-in. After that the token refreshes on its own. | P0-09, P1-02 |
| U3 | Consultant, daily | **Ask with the defaults** | Opens the Code tab in any folder, types `/yvoke`, then just types the question. OIM, single agent and `oim-full` apply, and the answer comes back with citations. | P1-07, P1-08, P1-10 |
| U4 | Consultant | **Pick a specific playbook** | Changes the playbook in the setup band before the first question. The preflight may suggest a better fit; the user decides. | P1-08, P2-06 |
| U5 | Consultant | **Check a source** | Clicks a citation to read the exact passage, with the surrounding section behind a collapsed control. | P3-01 – P3-03 |
| U6 | Consultant | **Run an investigation** | Chooses a multi-agent profile as the mode. Cards show each specialist's sub-question and answer, the reviewer's verdict, and a banner if the final answer was not approved. | Phase 6 |
| U7 | Consultant | **Answer a clarifying question** | When the question is ambiguous, Claude asks with its native question prompt, then continues. | P1-09, P6-08 |
| U8 | Consultant | **Start over with another setup** | Area, mode and playbook are fixed once the first question is sent. `/clear` ends the Yvoke session, and `/yvoke` starts a new one with a fresh setup band; `/resume` brings back an old session with its setup. | P1-08 |
| U9 | Developer on the same laptop | **Code as usual** | In any session where they did not type `/yvoke`, Claude Code behaves exactly as before. | P1-10 |
| U10 | Chat or Cowork user (after v1, D-26) | **Use a playbook outside Claude Code** | Invokes a Yvoke playbook skill. Claude fetches the playbook from the server and answers from the knowledge base, without the setup band, the tool rules or the source pane. | P1-01, P1-04, P1-06, P8-01 |
| U11 | Anyone | **Server unreachable** | The question fails with a `Yvoke Backend:` message. There is no stale cached answer. | P1-03, P1-07 |
| U12 | Consultant | **Very long question** | A loop that hits the turn ceiling still delivers what it has, marked *stopped at the turn limit*. | P2-08 |

After v1: rate an answer with 👍/👎 (Phase 4) and find Claude conversations in the Yvoke account (Phase 5).

## 5. Scope and target surfaces

One plugin, three surfaces. v1 targets Claude Code only (D-26); Chat and Cowork are listed for after v1.

| Surface | Loads from this plugin | v1 (D-26) | Later |
| --- | --- | --- | --- |
| **Claude Code**: Desktop **Code** tab, terminal, JetBrains | connector, mod | **Full parity**, the primary target | Rating and sync |
| **Cowork** (Desktop) | connector (hooks and agents possible, no mod) | Not in v1 | Playbook stubs over the organization connector (P1-04, P8-01), tool scoping through hooks (P8-02), MCP App widgets (P8-03) |
| **Chat** (Desktop, web, mobile) | connector (no agents, hooks or mod) | Not in v1 | Playbook stubs (P1-04, P8-01), widgets and citation links (P8-03, P8-04); anything enforced must live on the server |

Not targeted: VS Code extension UI (mod hooks run there, but nothing it draws is shown), WSL sessions in the
Desktop app (no plugins), cloud sessions (hooks run, nothing is drawn).

## 6. Not in v1

- Conversation sync into the Yvoke account (D-07) and rating answers, which builds on sync (D-08).
- Chat and Cowork (D-26): playbook stubs, Cowork hooks, MCP App widgets and citation URLs (P0-08, P1-04,
  P1-05, P8-01 to P8-04).
- An IT lockdown of shell and file tools (D-09). Claude Code's own permission prompts still apply.

## 7. Constraints

- Users are on Claude Team or Enterprise; an admin adds the connector and can push the plugin (D-01).
- The marketplace repository is public, so it never holds playbook text, secrets or customer data (D-02).
- yvoke-web stays the source of truth; there is no cached fallback when it is unreachable.
- yvoke-desktop's Entra registration and REST API do not change.
- The mod API is early access and needs Claude Code ≥ v2.1.287; it changes between releases.
- Using Yvoke is opt-in; there is no IT lockdown (D-09).

## 8. Open questions

- The production API scope (the desktop's ends in `/desktop`) and the claude.ai connector callback URL for
  the new Entra client (P0-09).
- Mod API behaviour the spikes settle: tool names per surface, drawing in the Desktop Code tab, `/branch`,
  dynamic subagents (P0-04 to P0-07).

## 9. Not planned and known gaps

Write things down here when they are decided against, so nobody "fixes" them by accident.

- **Hiding playbook text from the user.** Playbooks are readable by any connected client of the server. Not a secret today either.
- **Isolation from the user's own Claude setup.** In Claude, the user's other plugins, skills, memory and
  connectors can affect answers; Yvoke Desktop excluded them. Managed settings can narrow this, not remove it.
  The other direction is handled: outside a Yvoke session the mod changes nothing (P1-10), but the
  Yvoke connector is still offered in every session.
- **Company cost reporting.** Model usage stays on each user's Claude subscription, as with Yvoke Desktop.
- **Removing a rejected draft from the record.** Claude Code keeps every turn; the mod only collapses
  rejected drafts on screen (P6-07). The transcript, `/resume` and copy still contain them.
- **Behaviour in VS Code, WSL and cloud sessions.** Hooks may run there, but no UI is drawn (or no plugin
  loads at all); not supported.

---

# Yvoke for Claude v1: requirements

What the plugin must do. Why it exists and who uses it is in intent.md; how it is built is
in design.md; the tasks that deliver each requirement, with their **Done when** tests, are in
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
  **Done when** includes a test for the throw and the timeout path (design 4.4).
- **One session, one setup.** A session's area, mode and playbook are chosen once and cannot change
  afterwards; a different setup is a new session (`/clear`). Decided 2026-10-05 (D-11).
- **Only in a Yvoke session.** Yvoke works in any folder. The mod's rules and UI apply only in a session the
  user started with `/yvoke`. Everywhere else Claude Code behaves as if the mod were not there (P1-10,
  D-13).

## 2. Behaviour in Claude Code

### 2.1 Session setup and system prompt

- `/yvoke`, typed before a session's first question, makes it a Yvoke session until `/clear`. (P1-10)
- A new Yvoke session shows a setup band with **area** (default OIM), **mode** (single agent, the default,
  or multi-agent) and then **playbook** (default: the area's default playbook, `oim-full` for OIM) or
  **profile** (default: the area's default profile) (D-18). The area comes first and the other two follow it. The lists and defaults come live from the server; prototypes are hidden unless enabled. Playbooks show with their version (*oim-full v3*). (P1-08, P1-12)
- Sending the first question locks the setup. From then on the band and the status line show it read-only.
  `/resume` and `/branch` keep it. (P1-08)
- On a single-agent session's first question, when the area has at least two playbooks, a preflight check
  may offer **Switch to …** or **Send anyway**. The area's default playbook skips the check. Any error lets the question through. (P2-06)
- The system prompt is the server's base instructions followed by the playbook's text, separated by a `---`
  line, fetched when the setup locks. The base instructions are the playbook's own system prompt when any
  area lists it, else the area's default, else the active default; which one is decided from `list_areas`
  before any call, so a missing prompt never reaches the server as a name. Any failed fetch, an `ERROR:`
  answer included, drops the question with a `Yvoke Backend:` message and leaves the setup unlocked.
  (P1-01, P1-06, P1-07)
- The plugin ships no playbook skills, so the model cannot start a playbook by itself; only the setup band
  starts one. (D-26, P1-08)

### 2.2 Tool policy

- Allowed: the playbook's knowledge-base tools (or the default set when it declares none), `ToolSearch`,
  `AskUserQuestion`, the compute tools, web tools under the web rules, and delegation while a profile is
  active. Everything else is denied with a message pointing to the Yvoke tools. (P2-01, P2-02, P2-03)
- Web tools only when enabled in configuration **and** declared by the playbook; `WebSearch` limited to the
  configured hosts, `WebFetch` checked against the allow-list, WAF-challenged hosts refused. (P2-04)
- A compute tool `execute_code`, refused on every call while Claude Code's sandbox is off, run through `Bash` behind the
  mod's own tool, so the model never gets `Bash` (D-19). Also refused on every call while the playbook
  declares `codeExecution: false`. (P0-12, P2-05)
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
- The lead may only delegate, ask the user, call `verify_citations` and submit its answer
  (`submit_answer`). (P6-04)
- The reviewer gives its verdict through a `submit_review` tool (approved or not, feedback, unsupported
  claims, citation fixes) and sees only the sources the draft cites. No verdict counts as a rejection. (P6-03)
- Review is enforced in code: every answer that consulted a specialist is reviewed; a rejection or no
  verdict means a revision, up to `maxReviewRounds`; out of rounds means a warning in the answer; a
  reviewer that fails lets the draft through marked unreviewed. The specialist budget is enforced.
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
`yvoke-desktop/spec/`. "After v1" follows D-07, D-08 and D-26. v1 is Claude Code only (D-26, 2026-10-21):
the Cowork and Chat columns say what could come after v1, not what v1 delivers.

| Yvoke Desktop capability | Spec | Claude Code (mod) | Cowork | Chat | Tasks |
| --- | --- | --- | --- | --- | --- |
| Knowledge-base tools (search, sections, graph, records) | 2 | ✅ connector | after v1 | after v1 | P0-09, P1-02 |
| Base instructions from the server | 2 | ✅ | after v1, stub fetches them | after v1, stub fetches them | P1-01, P1-07, P8-01 |
| Playbooks (pick, `/` autocomplete, sticky per conversation) | 1, 2 | ✅ setup band, fixed per session (D-11) | after v1, live stubs | after v1, live stubs | P1-06 – P1-08, P1-12; after v1 P1-04, P1-05, P8-01 |
| Playbook required for a single-agent question | 1 | ✅ (the default `oim-full` applies) | — | — | P1-08 |
| Playbook preflight check | 1, 2 | ✅ | — | — | P2-06 |
| Deny by default; no shell | 2 | ✅ | after v1 (hooks) | n/a | P2-01, P8-02 |
| Per-playbook tool scoping | 2 | ✅ | after v1 (hooks) | server only | P2-02, P2-03, P8-02 |
| Safe compute tools | 2 | ✅ | — | — | P2-05 |
| Domain-restricted web search/fetch, WAF hosts refused | 2, 6 | ✅ | after v1 (hooks) | — | P2-04, P8-02 |
| Clarifying questions | 1, 2 | ✅ native `AskUserQuestion` | after v1, native | after v1, in text | P1-09 |
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

---

# Yvoke for Claude v1: roadmap & feature backlog

> **Status:** v1 is Claude Code only (D-26). Living specifications for delivered features live in `.lintel/specs/`, and architectural decisions live in `.lintel/steering/architecture.md`.

### Changes in yvoke-web and yvoke-desktop since 2026-10-06

Both apps moved to 1.3.0. What that changes here:

| Change (yvoke-web / yvoke-desktop) | Effect on this plan |
| --- | --- |
| A playbook can name its own system prompt; it replaces the default base instructions for that playbook. Base and playbook are joined with `\n\n---\n\n` ([web#11](https://github.com/yvoke-dev/yvoke-web/pull/11), [web#12](https://github.com/yvoke-dev/yvoke-web/pull/12), desktop #20, #21) | P1-07 resolves the base instructions from the playbook first, and uses the same separator |
| Mode and playbook lock on the first question ([web#12](https://github.com/yvoke-dev/yvoke-web/pull/12), desktop #21) | Already D-11; nothing to change |
| Playbooks carry a version, shown next to the name everywhere and stamped on messages and trace steps ([web#15](https://github.com/yvoke-dev/yvoke-web/pull/15), [web#17](https://github.com/yvoke-dev/yvoke-web/pull/17), desktop #25, #26) | P1-08 shows *name vN*; P4-02 and P5-05 record the version |
| The area's default playbook skips the preflight check; a suggestion of the same playbook counts as a fit ([web#18](https://github.com/yvoke-dev/yvoke-web/pull/18), desktop #17) | P2-06 |
| `calculate`, `statistics` and `date_diff` replaced by one sandboxed `execute_code` (JavaScript in an in-memory VM) in the desktop; the web added and then removed the three as MCP tools ([web#8](https://github.com/yvoke-dev/yvoke-web/pull/8), [web#13](https://github.com/yvoke-dev/yvoke-web/pull/13), desktop #23) | A mod cannot run generated code: checked on Claude Code 2.1.304, `new Function` and `eval` throw *Code generation from strings disallowed* and `WebAssembly` is undefined. D-19: `execute_code` wraps Claude Code's `Bash`; spike P0-12, then P2-05 |
| The reviewer submits a structured verdict through a `submit_review` tool (`approved`, `feedback`, `unsupported_claims`, `citation_fixes`), has only `verify_citations` besides, sees only the cited evidence, and keeps its own session across rounds. The lead delivers through `submit_answer`, which runs the review in code ([web](https://github.com/yvoke-dev/yvoke-web/blob/main/src/main/java/de/palsoftware/yvoke/chat/orchestration/OrchestrationService.java), desktop #24, #27) | P6-03, P6-04 and P6-05 follow this design. D-17 is reversed: the reviewer's tools are fixed, not taken from its playbook |
| Domain-scoped `WebSearch` in the web's own chat ([web#14](https://github.com/yvoke-dev/yvoke-web/pull/14)) | None: it is not an MCP tool. P2-04 keeps Claude Code's `WebSearch` |
| Models mapped to the 5.5 family by explicit id (desktop #19) | P6-02: role models are explicit ids such as `claude-sonnet-5-5`; a bare alias like `haiku` resolves to an older model |
| Split HTML tables balanced, MadCap toolbars and line gutters, HTML comments and empty anchors dropped (web #19–#21, desktop #28) | P3-03 cleans the passage it shows the same way |
| Single-agent tool calls recorded in the web's trace ([web#10](https://github.com/yvoke-dev/yvoke-web/pull/10)) | After v1 (Phase 5); nothing in v1 |

To keep parallel threads from colliding, each feature lives in its own `src/` module and adds one line to
`hooks/register.tsx`.

## 1. Work plan

### Phase 0 — Foundation and spikes

The spikes de-risk everything later in the plan. Record each spike's findings in
 the spike task findings (what was tried, Claude Code version, surface, result) before ticking it.
Spikes need the Desktop Code tab and a signed-in Claude Code, so they run on a developer's machine, not in
a cloud session.

- [x] **P0-01** `plugin` · S — **Repository scaffold.**
  - `README.md`; `.gitignore`; `.claude-plugin/marketplace.json`; `plugins/yvoke/.claude-plugin/plugin.json`;
    `hooks/hooks.json`; a no-op `hooks/register.tsx`; `types/index.d.ts`; `package.json` and `tsconfig.json`
    for type-checking.
  - Fill in the plugin commands in the repository verification scripts.
  - Done when: `claude plugin validate .` and `claude plugin validate plugins/yvoke` pass, and the plugin
    loads with `claude --plugin-dir plugins/yvoke`.
  ([#7](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/7))
- [x] **P0-02** `plugin` · M — **CI.** ([#9](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/9)) Extend `.github/workflows/ci.yml` (added 2026-10-05 with only the docs
  check) so it runs on every push and PR:
  `claude plugin validate` (marketplace and plugin), `tsc --noEmit`, `claude plugin test plugins/yvoke`.
  Run on the pinned minimum Claude Code version and on the latest release, plus a weekly scheduled run
  against the latest release so mod API changes are caught early.
  - `claude plugin test` needs no session, sign-in or network (per the mods test docs). Each test has a
    5-second default timeout; tests that stub slow model calls set `timeoutMs`.
  - `node scripts/check-docs.mjs` (links, anchors, and relative Markdown links/anchors).
  - Done when: a deliberately broken test turns the workflow red, and restoring it turns it green.
- [x] **P0-13** `plugin` · S — **Type declarations on Claude Code 2.1.304** (bug fix, found 2026-10-20). ([#21](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/21))
  `npm run typecheck` times out on 2.1.295 and later (first seen on 2.1.304), which write
  `.claude-plugin/types/` only for a plugin folder the session watches; `scripts/lay-types.mjs` turns watching on in its print-mode run
  (P0-13 plan).
  - Done when: `npm run typecheck` passes on 2.1.287 and on 2.1.304, and both CI rows are green.
- [ ] **P0-03** `plugin` · S — **Contributor guide** (`docs/contributing.md`):
  - terminal: `claude --plugin-dir plugins/yvoke`;
  - Desktop Code tab: `CLAUDE_CODE_PLUGIN_DIRS` and `CLAUDE_CODE_PLUGIN_DIR_WATCH=1` in the `env` block
    of `~/.claude/settings.json`, or add the working copy as a local marketplace and use `/reload-plugins`;
  - type-checking with the generated `.claude-plugin/types/`; reading `claude --debug` output.
  - Done when: a second developer follows it from a clean machine and gets a hot-reloading mod.
- [x] **P0-10** `plugin` · S — **Development process and Lintel agent harness.** `.lintel/steering/`, `.lintel/specs/`, `.lintel/scripts/`, `.lintel/tasks/`, and `.agents/` subagents.
- [x] **P0-11** `plugin` · S — **Dev environment** (proposed 2026-10-05). A developer installs the
  working copy in their own Claude Code and points it at a yvoke-web running on their machine.
  - Install: the repo as a local marketplace (`/plugin marketplace add <path>`), or `--plugin-dir` (P0-03).
  - Server URL: `${user_config.serverUrl}` in `.mcp.json`, with no default until P1-02 adds the
    production URL (D-14); a developer sets `http://localhost:<port>/mcp`.
  - Sign-in: a `devMode` switch in `userConfig` (off by default). The plugin always sends
    `X-Yvoke-Dev-Mode: true|false`; a local yvoke-web in mock mode (`APP_SECURITY_MOCK=true`, dev profiles
    only) accepts `true` in place of a sign-in, and any other server ignores it. Not an `Authorization`
    header: Claude Code turns the OAuth sign-in off whenever `.mcp.json` sets one, even an empty one
    (P0-11 plan). Off, the plugin uses the normal sign-in. So no Entra client is needed
    for dev.
  - Done when: on the developer's machine, a session with the plugin lists the local server's tools and
    `search_corpus` answers from the local server. ⛔ P0-01
  ([#10](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/10), with
  [yvoke-web#6](https://github.com/yvoke-dev/yvoke-web/pull/6); checked on Eduard's machine 2026-10-06)
- [ ] **P0-04** `plugin` · `server` · S — **Spike: reaching the Yvoke server from Claude Code.**
  - Can the Yvoke MCP server be used from the Code tab as a claude.ai connector, from a plugin `.mcp.json`,
    or both? How does each sign in with Entra ID? What server and tool names does each produce
    (expected: `mcp__plugin_yvoke_<server>__…` from `.mcp.json`, `mcp__<uuid>__…` for a connector in the
    Desktop Code tab)?
  - From the mod: `$.mcp.call(server, "get_section", …)` and `search_corpus`, once against a `.mcp.json`
    server (after `$.mcp.connect`) and once against the claude.ai connector by the name `/mcp` lists. The
    API documents both as working; confirm it on the Desktop Code tab.
  - What happens when the Entra token expires during a session: silent refresh, a sign-in prompt, or a
    failed call? What error does `$.mcp.call` return then, so P1-03 can tell the user to sign in again?
  - Done when: a throwaway `/yvoke-ping` command prints the server version and one search hit, and the
    findings confirm D-03 and D-05 (tool names) or reopen them.
- [ ] **P0-05** `plugin` · S — **Spike: drawing on the `desktop` surface.**
  - Redraw an `AssistantMessage` as `Markdown` with links and `Button`s; check whether a link click raises
    `ui.press` (`onLinkPress`) in the Desktop Code tab or only in the fullscreen terminal; open a `Pane`
    with an `Input` and submit it, as the 👎 comment form (P4-03) will.
  - Done when: findings say which citation UI P3-02 uses (clickable links vs. a row of source buttons), and
    whether the feedback form works in a pane on both surfaces or needs a `/yvoke-feedback` command instead.
  - Mod commands cannot contain `:` (names are letters, digits, `_` and `-`), so every command the mod
    registers is `/yvoke-<name>`.
- [ ] **P0-06** `plugin` · S — **Spike: playbook mechanics.**
  - `tool.call` denying `mcp__…` tools; `$.state` surviving across turns.
  - Hiding tools: per the mods reference, `tool.describe` can only reword a tool or defer it behind tool
    search, and `agent.offer` can withhold subagent types. Tools are therefore denied at call time, not
    hidden; confirm, and check that a deferred tool stays out of the model's context until searched.
  - A mod command named `yvoke`: does `/yvoke` register and
    autocomplete cleanly on both surfaces (P1-10)?
  - Moved to P1-08 on 2026-10-20: the typed text after `{ drop }`, and the setup surviving `/resume` and
    `/branch`. Hiding playbook skills is no longer needed: the plugin ships none (D-26).
  - Done when: findings confirm (or rule out) denying tools at call time and the `/yvoke` start (P1-10).
- [ ] **P0-07** `plugin` · S — **Spike: dynamic subagents.**
  - `$.agent.register` with a restricted tool list, model and effort; `$.agent.spawn`; whether delegation
    shows up in `tool.call` as `Agent`/`Task` so it can be counted; `turn.complete` + `$.prompt.submit` for
    re-prompting.
  - **Which agent made a tool call?** Answered in Claude Code 2.1.289: `tool.call`'s input carries `agentId`
    (absent on the main loop) and `agent.spawn` carries `parentAgentId`. Confirm it; Phase 6 design need not
    wait (review M2). The original concern: `tool.call` also fires for subagents' calls; without `agentId`
    on it, deny-by-default (P2-01),
    the lead's knowledge-base ban (P6-04) and the specialist budget (P6-06) cannot tell the lead from a
    specialist. Restricting specialists' tools in `agents/*.md` does not help, because the mod's hook still
    sees their calls. The specialist budget does not need it: `agent.spawn` fires before each subagent
    starts and can deny it (P6-06).
  - `turn.complete` also fires for each subagent's turn (`e.agentId` set): confirm P6-05 can act on the
    lead's turn only.
  - Can a mod change the final answer text? `turn.complete` can only add a line under the answer; P6-05's
    out-of-rounds warning may need a `session.append` rewrite, or a `ui.render` hook on `AssistantMessage`
    (which changes only what is shown, not what is stored).
  - **Holding back the lead's text** (added 2026-10-20): yvoke-desktop withholds the lead's live and plain
    text in its own translate layer and shows only the submitted answer. Find the mod's way to do the same
    (a `ui.render` hook on the lead's `AssistantMessage`, or another), and what is stored versus shown (P6-04).
  - **Review from a tool call** (added 2026-10-20): yvoke-desktop now runs the review inside the lead's
    `submit_answer` call and hands the verdict back as that call's result (P6-05). Check whether a
    `tool.call` hook can spawn the reviewer and wait for it, given the hook's 10-second budget; if not, find
    the fallback (`turn.complete` plus `$.prompt.submit`, as planned before).
  - Done when: findings confirm D-06 (or reopen it), say how P2-01 and P6-04 tell the lead from a specialist,
    how the lead's text is held back, and where the review runs (in `submit_answer` or on `turn.complete`).
- [ ] **P0-12** `plugin` · S — **Spike: `execute_code` through a wrapped `Bash`** (D-19). A mod-registered
  `execute_code` whose `tool.call` hook runs the code with `$.tool.call({ tool: 'Bash', command: 'node -e …' })`.
  Check, on macOS and on Windows, with the Desktop Code tab and the terminal, on Claude Code 2.1.287 (the
  oldest with mods) and on the latest release, since D-19's premise was checked only on 2.1.304:
  - whether `node` is there for users of Claude Code's native installer, and what to do when it is not;
  - Claude Code's sandbox: on by default or only by setting, how the mod tells it is on
    (`$.settings.read`, managed settings) so it can refuse to register or run without it, and what happens
    on native Windows, where it is not supported;
  - escape cases, each tried from the code itself: reading `process.env` (tokens, keys), reading and
    writing files outside a scratch folder, `child_process` (spawning a shell), network (`fetch`,
    `net`), and `dangerouslyDisableSandbox` in the wrapped call, which the mod must never set;
  - whether the call raises a permission prompt, and whether P2-01's deny lets the mod's own `Bash` call
    through (`next.origin`) while still denying the model's;
  - time: yvoke-desktop's limits are a 1 s default and a 2 s maximum per run; check a runaway script is
    killed at that limit and that the whole call fits the hook's 10-second budget;
  - output: the 32,000-character cap and how stdout, the final value and errors come back.
  - **Pass or fail, not a report.** The route passes only if, on every system and version in the matrix
    where the tool can run, the sandbox blocks **all four** escapes (`process.env` secrets, files
    outside the scratch folder, `child_process`, network) and the wrapped call runs with
    `dangerouslyDisableSandbox` unset. Any one escape that gets through fails the spike, and P2-05 is
    struck through (D-19: v1 then has no compute tool).
  - Done when: findings record the result of every escape case on every row of the matrix, and say pass
    or fail.

- [ ] **P0-09** `server` · `IT` · S — **Entra client registration for Claude clients** (design 5.2).
  A public-client app registration (or the desktop's, extended) with the redirect URIs Claude Code and
  claude.ai use, consented for the API scope yvoke-web checks. Feeds D-03 and P1-02.
  - Done when: `claude mcp add --transport http --client-id <id> --callback-port <port> yvoke <url>` signs
    in from a clean machine and lists the tools, and the claude.ai connector does the same.

### Phase 1 — Knowledge base, base instructions and playbooks (MVP)

**Milestone M1:** in the Code tab, a user picks a playbook, asks a question, and gets a grounded answer.

- [x] **P1-01** `server` · S — **`get_system_prompt(name = "default-chat")` MCP tool** returning the base
  instructions: the admin's Active Default Chat System Prompt, as yvoke-web's single-agent mode uses it
  (D-16). The server does **not** send them as MCP
  `instructions` (D-12): those would reach every session that connects, including users' coding sessions,
  and Yvoke sessions would get them twice. In Claude Code the mod adds the text (P1-07).
  - Done when: the tool returns the active default chat prompt's text, and the server's `initialize` result carries no
    base instructions.
  - Delivered in [yvoke-web#5](https://github.com/yvoke-dev/yvoke-web/pull/5); plan in
    P1-01/plan.md ([#6](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/6)).
- [ ] **P1-02** `plugin` · S — **Connector configuration.** Ship the `.mcp.json` entry D-03 chose: server `yvoke`,
  the server URL setting, and the Entra client and callback port from P0-09. ⛔ P0-09
  - The URL comes from `${user_config.serverUrl}` with the production URL as its default (D-14).
  - P0-11 ships `serverUrl` with no default; this task adds the production default. Keep any
    `Authorization` header out of `.mcp.json`: it turns the OAuth sign-in off (P0-11).
  - Done when: a fresh machine with the plugin installed can call `search_corpus` after one sign-in.
- [x] **P1-03** `plugin` · S — **One server client module** (`src/server.ts`) used by every feature:
  ([#13](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/13))
  - finds the plugin's own server `yvoke` (D-03, D-05) with `$.mcp.connect`, which returns the exact name
    `$.mcp.call` takes (such as `plugin:yvoke:yvoke`), so it does not wait for P0-04;
  - wraps `$.mcp.call` with a timeout;
  - treats a body starting with `ERROR:` as a failure even without the error flag (as yvoke-desktop's
    `McpPrompts.callGetSection` does);
  - prefixes server failures with `Yvoke Backend:`.
  - Done when: tests cover not connected, timeout, error flag, `ERROR:` body and success.
- [x] **P1-06** `server` · S — **`list_playbooks()` and `get_playbook(name)` MCP tools** returning the
  playbook list with its metadata, and one playbook's full text and metadata (design 5.3).
  Needed because a mod can call MCP tools but not read MCP prompts. Required by D-11. The `area` parameter
  and field come with P1-12, which adds the attribute.
  - Done when: server tests cover the list (with `tools`, `codeExecution`, `targetAgent`, `prototype`),
    a known playbook, and an unknown name answered with an `ERROR:` body.
  - Delivered in [yvoke-web#4](https://github.com/yvoke-dev/yvoke-web/pull/4); plan in
    P1-06/plan.md ([#8](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/8)).
- [x] **P1-07** `plugin` · M — **System prompt from the server.** When the session's setup locks (P1-08),
  the mod fetches the base instructions and, in single-agent mode, the playbook's text (P1-06), and serves
  them from a `prompt.compose` hook as one `scope: 'session'` section: base instructions plus the playbook
  text, **base instructions first and the playbook after them** (decided 2026-10-05, as in yvoke-desktop:
  the playbook's rules win any conflict), joined by `\n\n---\n\n` as yvoke-web and yvoke-desktop do.
  A fetch failure drops the question with a clear
  `Yvoke Backend:` message and keeps the setup unlocked. There is deliberately no cached fallback.
  ⛔ P1-01, P1-06
  - **Which base instructions** (added 2026-10-20): the playbook's own `systemPrompt` (from `get_playbook`)
    when it exists as a chat prompt, i.e. **any** area in `list_areas` lists it among its `systemPrompts`
    (yvoke-web's `findChatPrompt` looks a name up globally, not within the area); else the area's
    `defaultSystemPrompt`; else the active default (D-16). The choice is made from that data **before**
    any call, so the mod then makes one `get_system_prompt` call (by name, or with no name for the active
    default). A playbook prompt that no area lists falls back to the area's default, then the active
    default (the web and the desktop also log a warning and fall back, but straight to the active default). That one call failing, an
    `ERROR:` body included (P1-03 cannot tell a missing prompt from a broken server), drops the question:
    no fallback after a failed call.
  - Done when: tests cover success, server down, unknown playbook, a hook failure (fails closed), a
    playbook prompt its own area lists, one only another area lists (still used), one no area lists
    (falls back to the area default), no area default (active default), and an `ERROR:` from
    `get_system_prompt` (dropped).
  - Delivered in [#17](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/17); plan in
    P1-07/plan.md. The setup comes from P1-08's band; with none saved, OIM, single agent,
    the area's default playbook.
- [x] **P1-08** `plugin` · M — **Session setup: area, mode, playbook** (D-11) ([#22](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/22), [#29](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/29)).
  - In a new Yvoke session a band above the prompt (`ui.render` on `AbovePrompt`) shows three `Select`s
    (D-18): **area** (default OIM), **mode** (*Single agent* or *Multi-agent*; default *Single agent*) and
    then **playbook** for single agent (default the area's default playbook; `oim-full` for OIM) or
    **profile** for multi-agent (default the area's default profile). Lists and defaults come live from
    `list_areas` (P1-12); prototypes are hidden unless enabled. Until Phase 6 is built, the mode list offers
    only *Single agent*, and an area with no profiles never offers *Multi-agent*.
  - **Area first** (re-stated 2026-10-20): the area is chosen first, and the other selects follow it.
    Changing the area resets mode, playbook and profile to that area's defaults; switching the mode to
    *Multi-agent* selects the area's default profile.
  - Playbooks show by name with their version, *oim-full v3*, in the select and in the read-only line
    (yvoke-web and yvoke-desktop 1.3.0). The session keeps the version it locked with.
  - The selection **locks when the first question is sent**: the user can accept the defaults by just
    typing. From then on the band shows a read-only line (*OIM · Single agent · oim-full v3*), and the status
    line shows the same. Nothing can change it; a different setup is a new session (`/clear`).
  - Kept in `$.state`, and in `$.store` under the session id so `/resume` restores it and `/branch` keeps it
    (`classic.SessionStart` `source`), as P1-10 does for the Yvoke flag.
  - A server with no areas or playbooks: the band says so and questions are refused (no fallback).
  - Only in a Yvoke session (P1-10). ⛔ P1-10, P1-12 (both done)
  - Done when: tests cover the defaults (single agent, the area's default playbook), changing each select
    before the first question, the area reset, the switch to *Multi-agent* picking the area's default
    profile, the version shown, the version kept after the lock even when the server's playbook moves on
    to a newer one, the lock, the read-only line on `terminal` and `desktop`, `/clear`,
    `/resume`, and server down. Before it is ticked, a real Claude Code install (a non-managed one for `/resume` and `/branch`) confirms what P0-06 used to
    (moved 2026-10-20): the typed
    text stays in the prompt box after `{ drop }` (else restore it with `$.prompt.fill`); `$.session.id`
    is the same after `/resume`; and the setup survives `/branch`.
- [ ] **P1-09** `plugin` · S — **Clarifying questions.** Deny the server's `ask_clarifying_question` with a
  message telling the model to use Claude Code's native `AskUserQuestion`. Yvoke Desktop intercepts that tool;
  nothing else does.
  - Done when: a test shows the deny and its message.
- [x] **P1-10** `plugin` · S — **Yvoke session start** (D-13) ([#12](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/12)). The mod's hooks run in every session that loads
  the plugin, in any folder. A session becomes a Yvoke session only when the user types `/yvoke`.
  - In a Yvoke session: policy, playbook gate, compute tools, preflight and UI all apply.
  - In any other session: every hook passes the event through unchanged and nothing is registered or drawn,
    so the user's other Claude Code work is untouched.
  - `/yvoke` works only before the session's first question; afterwards it says to `/clear` first. Typing it
    again in a Yvoke session changes nothing. `/clear` ends the Yvoke session. The flag is kept in `$.state`
    and in `$.store` under the session id, so `/resume` and `/branch` keep it (`classic.SessionStart`
    `source`, as P1-08).
  - Done when: tests cover a session without `/yvoke`, `/yvoke` before the first question, `/yvoke` after it,
    `/yvoke` twice, `/clear`, `/resume`, and `/branch`.
- P1-11 was never assigned.
- [x] **P1-13** `server` · S — **Keep client-only tools away from models that should not call them**
  (design 5.4). `get_system_prompt`, `list_areas`, `list_playbooks`, `get_playbook`, `submit_feedback` and the sync tools are for
  Claude clients, not for the web's in-app assistant, which before P1-13 shared one tool set with every MCP client.
  - Delivered in [yvoke-web#23](https://github.com/yvoke-dev/yvoke-web/pull/23); plan in
    P1-13/plan.md ([#25](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/25)).
- [x] **P1-12** `server` · L — **Areas** (D-15). An area is a collection of system prompts, collections,
  playbooks and orchestrator profiles, with a default for each of the first, third and fourth. yvoke-web
  gets an `areas` table, an area list on each of the four kinds of item, and an admin page. A
  `list_areas` MCP tool lists each area with its members and defaults (OIM: `oim-full`);
  `list_playbooks(area)` and `get_system_prompt(area)` filter by it. Plan: P1-12/plan.md.
  - Done when: server tests cover one area, two areas, and a playbook listed under its area.
  - Delivered in [yvoke-web#7](https://github.com/yvoke-dev/yvoke-web/pull/7); plan in
    P1-12/plan.md ([#11](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/11)).

- [ ] **P1-14** `server` · M — **Playbooks search only their area's collections** (decided 2026-10-06).
  When a playbook belongs to an area, the search tools it calls reach only that area's collections, in the
  web chat and over MCP. Not needed for M1; can follow P1-12 later. ⛔ P1-12
  - Done when: server tests show a search from an area's playbook cannot return another area's chunks,
    and a playbook with no area is unchanged.

### Phase 2 — Tool policy

Port yvoke-desktop's `src/main/agent/policy.ts` semantics into a `tool.call` hook. Port the matching cases
from `tests/ (policy suite)` first, watch them fail, then implement.

- [x] **P2-01** `plugin` · M — **Deny by default** ([#23](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/23)). Allowed: the knowledge-base tools granted (P2-02),
  `ToolSearch`, `AskUserQuestion` (P1-09, P6-04), the compute tools (P2-05), the web tools under P2-04's
  rules, and delegation while a profile is active (P6-04). Everything else is denied
  with: *"The tool … is not available in this application. Use the yvoke knowledge-base tools instead."*
  - Includes `Bash`, file tools, other MCP servers and `Agent`/`Task` outside a profile.
  - The mod's own `Bash` call that serves `execute_code` (D-19) is let through by P2-05, which is where
    that call is made and can be tested; the gate sees it with `next.origin.plugin` `yvoke`, the model's
    calls with `engine` (checked on 2.1.304).
  - Until P2-02, every tool of the Yvoke server is granted; until P2-04, P2-05 and P6-04, the web tools,
    `execute_code` and delegation stay denied.
  - Applies only in a Yvoke session. ⛔ P1-10
  - Done when: tests cover each class of tool.
- [x] **P2-02** `plugin` · M — **Per-playbook scoping** ([#28](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/28)). A playbook that declares tools gets exactly
  those (plus the always-added three); one that declares none gets the default set (`DEFAULT_KB_TOOLS`).
  Scoping follows the session's playbook from P1-08, which never changes during the session. ⛔ P1-08
  - Done when: tests cover declared tools and no declaration.
- [x] **P2-03** `plugin` · S — **Re-namespacing** ([#27](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/27)). Playbooks name tools by their base name (`search_corpus`).
  The mod maps each to the Yvoke server's real tool name, using the server P1-03 found (D-05 lists the
  forms). Do not port yvoke-desktop's fixed `MCP_TOOL_PREFIX`; port `qualifyTool`'s tests with the prefix
  supplied at run time. The built-ins `WebSearch`, `WebFetch` and `ToolSearch` are never prefixed. ⛔ P1-03
- [ ] **P2-04** `plugin` · M — **Web access rules.**
  - Granted only when enabled in deployment configuration **and** declared by the active playbook.
  - `WebSearch`: `allowed_domains` replaced with the configured hosts before the call runs.
  - `WebFetch`: URL checked against the allow-list (host or subdomain, path on segment boundaries); hosts in
    `WAF_CHALLENGED_HOSTS` refused first, with an instruction to use `WebSearch`; an empty or unparseable list
    refuses everything.
  - The domain list comes from the plugin's `userConfig`, like every other deployment setting (role
    models, budgets). **Decided 2026-10-05:** all settings stay in `userConfig` and are editable by the
    user, as in yvoke-desktop; where IT deploys managed `pluginConfigs`, those values apply. 🔍 Confirm in
    P0-06 that managed `pluginConfigs` override a user's own values; if they do not, record it in [intent 9](intent.md#9-not-planned-and-known-gaps).
  - Done when: yvoke-desktop's web cases from `policy test suite` pass here.
- [ ] **P2-05** `plugin` · M — **Compute tool `execute_code`** (re-planned 2026-10-20, D-19). The mod
  registers `execute_code` with yvoke-desktop's name and limits (JavaScript up to 10,000 characters, a
  1 s default and 2 s maximum run time, 32,000 characters of output) and serves it by running the code
  through Claude Code's `Bash` under its sandbox, as spike P0-12 settles. The model never gets `Bash`
  itself (D-09, P2-01), and the mod never sets `dangerouslyDisableSandbox`.
  - **Its own contract, not the desktop's.** yvoke-desktop's tool is a zero-privilege `node:vm` and says
    "no filesystem, network, shell, or timers"; this one is a `node` process started by a shell, so its
    description promises only what P0-12 showed the sandbox blocks. Do not copy the desktop's
    description or its isolation tests.
  - **Registered once, checked on every call.** The mod registers the tool when `/yvoke` starts the Yvoke
    session, or when `/resume` or `/branch` restores one (P1-10), if this process has not registered it
    yet. That happens before the playbook is known, and a registered tool cannot be withdrawn (P2-05's
    earlier plan, #19): after `/clear` it stays listed in what is now a plain session. So every call
    checks, in this order, and refuses with a message saying why, running nothing, when:
    - the Yvoke flag (`$.state` `yvokeSession`) is not `true`, i.e. this is not, or is no longer, a Yvoke
      session;
    - Claude Code's sandbox is off, or unsupported as on native Windows;
    - the active playbook declares `codeExecution: false`. (A specialist agent type in P6-02 simply
      leaves the tool out of its list.)
  - **The mod's own `Bash` call passes P2-01's gate** only when it comes from this plugin
    (`next.origin.plugin === 'yvoke'`) and is `Bash`; the model's `Bash` stays denied. A test drives it
    through the real `execute_code` hook (moved here from P2-01, 2026-10-20).
  - Tests cover each refusal: a call after `/clear`, a call in a session never started with `/yvoke`,
    sandbox off, and `codeExecution: false`; each runs nothing.
  - Port only the result and limit
    cases of yvoke-desktop's `computeTools test suite`; the escape cases come from P0-12's findings. If P0-12 shows the route cannot be made safe, v1 has no compute
  tool and this task is struck through. The earlier plan for `calculate`, `statistics` and `date_diff`
  ([#19](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/19)) is not used. ⛔ P0-12
- [ ] **P2-06** `plugin` · M — **Playbook preflight check.** On the first question of a single-agent
  session, before the setup locks (P1-08), when the area offers at least two playbooks, ask the model
  (`$.model.complete`, no tools, 45 s timeout) whether the playbook fits; if not, show a band with
  **Switch to …** / **Send anyway**. Either answer locks the setup.
  - Fails open on every error, including a reply with `isAnswered: false`: `$.model.complete` reports a
    Claude API failure that way rather than by rejecting. Runs once per session, on the first question.
    Port `playbookValidation test suite`.
  - Its `prompt.submit` hook registers before `registerSystemPrompt(on)` (P1-07), whose load locks the
    setup: registered after it, the old playbook is already loaded and a switch changes nothing. It
    registers with a matcher (even `{}`), since P1-07's `prompt.submit` hook is the mod's unmatched one
    and a second unmatched hook stops the module loading (design 4.4).
  - Shows that the check is running (`$.ui.status` or the band above the prompt) while it waits, so a slow
    model does not look like a frozen app. Time spent inside `$.model.complete` does not count against a
    hook's 10-second limit.
  - **The area's default playbook skips the check** (yvoke-web and yvoke-desktop 1.3.0): the question goes
    straight through, and default playbooks are never offered as a suggestion. A suggestion naming the
    playbook already chosen counts as a fit.
  - Done when: tests cover fit, better match, timeout, unparseable reply, unknown suggestion, the area's
    default playbook and a suggestion of the same playbook.
- ~~**P2-07** `IT` · `plugin` · S — **Managed-settings lockdown template** (`deploy/managed-settings.lockdown.json`):
  `permissions.deny` for `Bash`, `Write`, `Edit`, `NotebookEdit`, and the plugin enabled at managed scope so
  users cannot disable it. The deny applies to the whole machine (see D-09). ⛔ D-09~~
  Dropped: no IT lockdown (D-09).

- [x] **P2-08** `plugin` · M — **Turn ceilings per question** (decided 2026-10-05). The mod counts the model
  requests of each question per loop (`turn.step`, keyed by `turnId` and `agentId`) and enforces
  configurable ceilings from `userConfig`: single agent 25, lead 60, each specialist and the reviewer 20, one
  setting for all subagents (D-24; yvoke-desktop ships 25, 60 and 20, but hard-codes its reviewer at 6). At the ceiling it **delivers rather than discards**: further tool calls
  in that loop are denied with a message telling the model to answer now with what it has, and the answer
  is flagged (*stopped at the turn limit*) by a line under it. A specialist at its ceiling returns its
  partial answer to the lead. Fails closed ([requirements 1](requirements.md#1-principles-carried-over-from-yvoke-desktop)). Port the ceiling cases from yvoke-desktop's tests where
  they exist.
  - Done when: tests cover under the limit, at the limit for each role, the flag line, and a hook failure.
  - Done: [#26](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/26); spec.

### Phase 3 — Citations

**Milestone M2:** every citation in an answer opens the passage it names.

- [ ] **P3-01** `plugin` · M — **Citation marker parser** (`src/citations.ts`). Port from yvoke-desktop's
  `citationRehype.ts` and its tests:
  - bare `[<uuid>]` → clickable, labelled with the first 8 characters;
  - legacy `[chunk_id=…]`, `[document_id=…]`, `[file=…]` → clickable, full label;
  - `[1]`-style numbers → not clickable; a truncated id → not a marker;
  - markers inside code blocks and inside real links `[2](https://…)` → left alone.
- [ ] **P3-02** `plugin` · M — **Citations in replies.** A `ui.render` hook on `AssistantMessage` turns
  markers into links (or a row of source buttons under the reply, depending on P0-05), on both `terminal`
  and `desktop`. ⛔ P0-05, P3-01
  - A `Markdown` or `Text` string holds at most 10,000 characters: split a longer reply into several
    elements, or leave Claude Code's own rendering and add only the row of source buttons.
  - Done when: UI tests mount the reply on both surfaces and press a citation, including a reply over
    10,000 characters.
- [ ] **P3-03** `plugin` · M — **Citation source pane.** On press, `get_section` is called with the id as a
  chunk first, then as a document. The pane shows the document title and version and **only the cited
  passage**, with the surrounding section behind a collapsed *Show surrounding section* control labelled
  as not part of the source. A document-level citation shows the section without that control. ⛔ P1-03
  - The passage is cleaned as yvoke-desktop's panel does since 1.3.0: split HTML tables balanced, MadCap
    copy toolbars and line-number gutters, HTML comments and empty bookmark anchors dropped; other markup
    shown as text.
  - Port `citationLookup test suite`. Done when: tests cover chunk hit, document fallback, both missing and
    `ERROR:` body.
- [ ] **P3-04** `server` · S — **Passage deep link** in yvoke-web (open a passage by id in the browser), used
  by the pane's *Open in Yvoke* action and by Chat/Cowork links (P8-04). 🔍 check whether a route exists.

### Phase 4 — Feedback (after v1; built after Phase 5, D-08)

**Milestone M3:** 👍/👎 on an answer arrives in yvoke-web's feedback screens.

Rating needs the server's message id, so it starts once turns sync (P5-01, P5-02).

- [ ] **P4-01** `server` · M — **Feedback endpoint for Claude clients**, as an MCP tool (`submit_feedback`), so
  the connector's sign-in authenticates it. Keyed to the synced message id (D-08), as yvoke-desktop's
  `PUT /messages/{id}/feedback`; marked as coming from the Claude plugin with its version. ⛔ P5-01
- [ ] **P4-02** `plugin` · M — **Turn capture.** For each completed turn, keep the question
  (`prompt.submit`), the final answer text, the active playbook and its version, the model, the cited ids and the tool
  names (`session.append` / `turn.complete`), keyed by turn, in session state. Only the main conversation's
  turns: `turn.complete` also fires for subagents (`e.agentId` set), and those are not rated.
- [ ] **P4-03** `plugin` · M — **👍/👎 under each final reply.** 👍 may carry a comment; 👎 requires one
  (comment form in a pane). A new rating replaces the previous one; the form opens pre-filled with the last
  comment. Only on turns that have synced; until then the buttons show "syncing". ⛔ P4-01, P4-02, P5-02
- [ ] **P4-04** `plugin` · S — **Every submit outcome has a recovery path:** success, error from the server
  (buttons re-enabled, message shown), and a stale or duplicate submit. This is yvoke-desktop's "async action
  state machine" pitfall. Done when: a test covers each of the three.
- ~~**P4-05** `plugin` · S — **Feedback without a synced message.** When conversations are not synced
  (D-07), feedback is self-contained; when they are, it attaches to the server's message id. ⛔ D-07~~
  Dropped: rating always uses the synced message id (D-08).

### Phase 5 — Conversation sync and traces (after v1; D-07: no sync in v1)

- [ ] **P5-01** `server` · M — **Sync over MCP.** Tools to create a conversation (marked as from the Claude
  plugin), append a finished turn, and upload a multi-agent trace, all authenticated by the connector.
  Today's desktop sync API needs an Entra bearer token the mod does not have. ⛔ D-07
- [ ] **P5-02** `plugin` · M — **Turn sync** after each completed turn, reusing P4-02's capture, with a
  persistent retry queue (`$.store`) and an idempotency key per turn, so a retry never duplicates a turn
  (fixes yvoke-desktop's "nothing de-duplicates"). ⛔ P5-01
  - `$.store` is 4 MiB in total, shared by every session on the machine: cap the queue, evict the oldest
    entries with a visible "not synced" state, and remove entries once sent (privacy, D-07).
- [ ] **P5-03** `plugin` · S — **Sync state** shown per conversation (pending / failed / synced), updated on
  every change (fixes yvoke-desktop's stale sync dot).
- ~~**P5-04** `plugin` · S — **Feedback attaches to the synced message id**~~ Folded into P4-01/P4-03:
  rating is keyed to the message id from the start (D-08).
- [ ] **P5-05** `plugin` · M — **Multi-agent trace upload**: role, round, playbook and its version, model, instructions,
  output, verdict and token counts per step, with a size cap per step. Name the reviewer's real playbook
  (yvoke-desktop records it as "reviewer"). ⛔ P5-01, Phase 6

### Phase 6 — Multi-agent investigations

**Milestone M4:** a profile runs lead → specialists → reviewer with review enforced in code.

- [ ] **P6-01** `plugin` · `server` · M — **Profiles.** Read profiles from the server (new MCP tools
  `list_profiles` / `get_profile`), registered live by the mod (D-06). A profile is chosen as the
  session's mode in the setup band (P1-08) and is fixed for the session; prototypes are hidden unless
  enabled. ⛔ P1-08
- [ ] **P6-02** `plugin` · M — **Specialist agent types**, one per profile specialist, from the base
  instructions plus the specialist's playbook. Tools follow yvoke-desktop's `mapSpecialistTools` (declared or
  default knowledge-base tools, compute unless `codeExecution: false`, web only if declared). Model and effort
  per role from deployment configuration. Port `orchestration test suite`.
  - Role models are explicit ids (`claude-sonnet-5-5`, `claude-haiku-5-5`), as yvoke-desktop maps them since
    1.3.0: a bare alias such as `haiku` resolves to an older model.
  - A specialist whose playbook names a system prompt runs on that prompt instead of the default (P1-07's rule).
- [ ] **P6-03** `plugin` · M — **Reviewer agent** (re-planned 2026-10-20 after yvoke-desktop #24 aligned it
  with yvoke-web). Runs on its playbook alone, with no base instructions, and with exactly
  `verify_citations` and a mod-registered `submit_review` tool: `approved` (boolean), `feedback`,
  `unsupported_claims` (claims nothing supplied backs) and `citation_fixes` (repairable from the sources in
  hand). The verdict is the tool call, not text: no call means "no clear verdict", which is how the old
  "NOT APPROVED reads as approved" defect becomes impossible. It sees the question, the draft and only the
  sources the draft cites (port `evidenceDigest.ts` and `chunkBlocks.ts`), and keeps its own session across
  rounds, receiving only newly cited sources. Port `reviewTools test suite` (the `submit_review` half),
  `evidenceDigest test suite`, `chunkBlocks test suite`, and from `review test suite` only the
  `renderReviewTask` / `renderReviewFollowUp` cases. The tool list is fixed and tools on the reviewer's
  playbook are ignored (D-17, reversed 2026-10-20).
- [ ] **P6-04** `plugin` · M — **Lead mode.** While a profile is active, the lead may only delegate, ask
  the user, call `verify_citations` and deliver through `submit_answer` (yvoke-desktop's `orchestration.ts`
  grants exactly those, and the server's orchestrator playbook relies on `verify_citations`); only the
  submitted answer is shown, its other text being held back by the mechanism P0-07 finds; other
  knowledge-base tools are denied to it, but not to the specialists (how the hook tells them apart comes
  from P0-07).
  - A lead that never calls `submit_answer`: as in yvoke-desktop, if it consulted a specialist its final
    text is reviewed as the draft (P6-05); if it consulted none, its text after any answer marker is the
    answer, unreviewed. The desktop runtime adapter text is appended to its
  instructions, with `REVIEW_FEEDBACK_HEADING` and `EVIDENCE_HEADING` byte-identical to the server's.
  The mode is fixed per session (D-11), which also avoids yvoke-desktop's pitfall of resuming across modes.
- [ ] **P6-05** `plugin` · L — **Review enforced in code.** As yvoke-desktop 1.3.0 does it: when the lead
  calls `submit_answer` on a turn that consulted a specialist, the mod runs the reviewer (P6-03) and answers
  the call with the outcome. Whether a `tool.call` hook can wait that long is P0-07's question; the fallback
  is the lead's `turn.complete` (subagents' turns, which carry `e.agentId`, are ignored):
  - a turn that consulted no specialist is not reviewed;
  - rejected or no verdict → the revision prompt, separating `citation_fixes` (repair from the sources in
    hand, no specialist) from `unsupported_claims` (may need new research), with the cited evidence, up to
    `maxReviewRounds`. The revision prompt puts the feedback, the two lists and the directive first and is
    capped at 32,000 characters (`MAX_REVISION_PROMPT_CHARS`, yvoke-desktop #27), narrowing the evidence to
    what is cited before cutting it, because Claude Code moves a tool result over about 50,000 characters
    to a file and hands the lead only a short preview, which a lead without file tools cannot read;
  - the reviewer itself fails → a non-blank draft ships with an *unreviewed* warning;
  - out of rounds → a warning written into the answer text itself (mechanism from P0-07);
  - a stopped (`e.isAborted`) or failed turn is never re-prompted.
  - `$.prompt.submit` waits until the session is idle; do not `await` it inside the `turn.complete` hook.
  - Port from `review test suite` the `buildRevisionPrompt`, `reviewStatusOf`, `reviewAttempts`, `addUsage`
    (token totals carried across review rounds), `reviewFlagNote`, `unreviewedNote`, `afterAnswerMarker`
    and `idsUnderReview` cases (the
    `buildRunTrace` ones belong to P5-05), and the `submit_answer` half of `reviewTools test suite`.
  - Done when: those tests pass here, including a revision prompt over 32,000 characters that keeps its
    feedback and directive.
- [ ] **P6-06** `plugin` · S — **Specialist budget actually enforced.** Count specialist starts in
  `agent.spawn` and deny beyond `maxSpecialistCalls` with a message (yvoke-desktop only advises the lead;
  decision #6 there). The reviewer does not count against the budget.
- [ ] **P6-07** `plugin` · M — **Team UI.** A card per delegation (specialist, sub-question, status, answer),
  the reviewer's Approved/Rejected badge with notes, and the unapproved-answer banner (*delivered without
  review*, *no clear verdict*, *rejected*). A specialist's answer can exceed the 10,000-character element
  limit: show the start on the card and the rest in a pane.
  - **Rejected drafts (decided 2026-10-05):** a `ui.render` hook on `AssistantMessage` and on the
    mod-originated revision prompt (`UserMessage` with a plugin origin) draws each rejected draft and its
    revision prompt as one collapsed *Draft rejected by reviewer* row that opens on click, so the final
    answer reads clean. Drawing only: the stored transcript, `/resume` and copy keep the draft, which is
    recorded in [intent 9](intent.md#9-not-planned-and-known-gaps).
- [ ] **P6-08** `plugin` · S — **A specialist's clarifying question** is shown to the user, not left as a
  locked composer with nothing to answer (yvoke-desktop limit).

### Phase 7 — Packaging, distribution and rollout

**Milestone M5:** a pilot group uses the plugin in their daily work.

- [ ] **P7-01** `plugin` · S — **Versioning and changelog.** `version` in `plugin.json` only (not also in
  the marketplace entry), bumped on every release; `CHANGELOG.md`; a release checklist in `docs/releasing.md`.
- [ ] **P7-02** `plugin` · S — **Release workflow.** A release is cut only after CI passes on `main`
  (validate, type-check, tests); no hand-made tags.
- [ ] **P7-03** `IT` · `plugin` · S — **Managed-settings template** (`deploy/managed-settings.json`):
  `extraKnownMarketplaces` with `autoUpdate: true` and `enabledPlugins`, for Intune/MDM (route B), plus the
  deployment `pluginConfigs` (web domains, role models and budgets; the server URL is fixed in
  `.mcp.json`, see P1-02).
- [ ] **P7-04** `IT` · `plugin` · M — **Distribution route set up and tested end to end:** route A (users add
  the repo), B (managed settings via MDM) or C (claude.ai organization sync), including how users get read
  access (D-02). If IT requires `allowManagedModsOnly`, use an MDM-copied directory marketplace instead.
  - Decided 2026-10-05: route A/B for the pilot; **route D** (a directory marketplace that MDM copies to the
    same admin-only path on every machine, enabled in managed settings) for rollout once IT enforces policy.
    Only route D makes the mod the organization's, so it can run first (`prependPlugins`) and survives
    `allowManagedModsOnly`.
  - D-25 (2026-10-21): where an organization manages Claude Code, a user's own install (route A) loses
    the hooks that add the instructions, so pilot users there need route B or D. Check which route the
    pilot machines are on, and whether route C counts as the organization's install.
- [ ] **P7-11** `server` · S — **Per-user rate limit on `/mcp`** (design 5.5). Searches from AI clients are
  not rate-limited today; the plugin moves every consultant onto that route.
- [ ] **P7-05** `plugin` · S — **User guide** (`docs/user-guide.md`): install, sign in, starting with `/yvoke`,
  picking playbooks and profiles, citations, feedback, what is different from Yvoke Desktop.
- [ ] **P7-06** `plugin` · S — **Security review pack** (`docs/security.md`): the `claude plugin validate`
  `hooks:` and `calls:` output for each release, what data leaves the machine and to where, what the mod
  keeps on disk (`$.store`, e.g. P5-02's queue), and the managed settings options IT has. Notes for IT:
  - keep `allowModsToOverrideDenyRules` off; otherwise any mod a user installs can approve calls a `deny`
    rule refuses. The Yvoke mod only denies and never needs it;
  - managed `PreToolUse` hooks run before every mod, and a block from one is final.
- [ ] **P7-07** `plugin` · S — **Compatibility.** Minimum supported Claude Code version documented and
  tested in CI; the mod reads `$.session.version` at session start and shows a clear message if it is too old.
- [ ] **P7-08** `plugin` · S — **Folder trust.** No mod loads in the Code tab until the user trusts the
  session's folder. Yvoke works in any folder (D-13), so document that users trust the folder they work in
  once; for users who have none, suggest (and if possible script) a folder to keep for Yvoke sessions.
- [ ] **P7-09** `PO` · M — **Pilot** with 3–5 consultants on macOS and Windows for two weeks; collect issues;
  go/no-go for wider rollout. Include users who are not developers, and record how long each takes from
  install to a first cited answer, and where they get stuck (Code tab, folder trust, slash commands).
- [ ] **P7-10** `plugin` · S — **`/yvoke-doctor` diagnostics** (spec chapter 8). One command that reports
  the Claude Code version against the minimum, whether this is a Yvoke session (P1-10), whether the
  Yvoke server is connected and signed in, the server's version, how many playbooks it offers, and the
  active playbook or profile. Uses P1-03; never prints tokens or other secrets.
  - Done when: tests cover a healthy session, a session without `/yvoke`, server not connected and signed out.

### Phase 8 — Chat and Cowork: after v1, to check later (D-26)

Moved here on 2026-10-21 (D-26): v1 is Claude Code only. Nothing in this phase is scheduled; it is a list to
check after v1. In Claude Code the mod reads playbooks live from the server, so the repository stores
nothing about them. In Chat and Cowork no live listing is possible without a mod, which is what would make
stubs, a generator and a sync job necessary. The generator built for P1-04 is in the history of
[#24](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/24) (commit `cf7147a`) and its plan in
P1-04/plan.md.

- [ ] **P0-08** `plugin` · S — **Spike: Cowork and Chat.** Install a test plugin on claude.ai: confirm what
  Chat loads, whether Cowork runs plugin `hooks.json` command hooks (`PreToolUse` deny, `Stop` block), and
  whether skill `disallowed-tools` has any effect outside Claude Code.
  - Command hooks keep no state between calls (no mod, no `$.state`). Check that a hook can work out the
    active playbook and whether the reviewer ran from the transcript at `transcript_path`, as P8-02 needs.
  - Done when: findings feed this phase (and may reopen D-26).
- [ ] **P1-04** `plugin` · M — **Skill generator for Chat and Cowork.** Reads `list_playbooks` and
  `list_areas` and writes one live stub `skills/<name>/SKILL.md` per playbook a user can pick (orchestrator,
  reviewer and prototype playbooks left out). Built and then removed in
  [#24](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/24) (commit `cf7147a`); how it worked and what
  was left open is in P1-04/plan.md. ⛔ P0-08
  - Done when: running it against the server produces a stable, reviewable diff.
- [ ] **P1-05** `plugin` · S — **Catalogue sync job.** A scheduled GitHub Action runs P1-04 and opens a PR
  when playbooks change on the server.
  - Done when: adding a test playbook on the server produces a PR within a day.

- [ ] **P8-01** `plugin` · S — **Playbook skills usable in Chat and Cowork.** Without a mod, a stub cannot
  fetch its text itself, so each stub tells Claude to call `get_system_prompt` and then `get_playbook` first
  (D-04 decided: live stubs; D-12: base instructions by tool only). ⛔ P1-01, P1-06, P0-08, P1-04
  - Done when: in Chat and Cowork, invoking a stub makes Claude call `get_system_prompt` and `get_playbook`
    before answering, in the P0-08 spike's test conversations.
  - Skills for these surfaces use only the portable frontmatter fields (`name`, `description`, `license`,
    `compatibility`, `metadata`, `allowed-tools`); Claude Code extras such as `disallowed-tools`,
    `context: fork` and `!` command injection do not apply there.
- [ ] **P8-02** `plugin` · M — **Cowork hooks** (`hooks.json` command hooks): `PreToolUse` scoping of
  `mcp__…` tools by active playbook; `Stop` blocking a lead from finishing without review. Command hooks keep
  no state, so each call works out the active playbook and whether review ran from the transcript at
  `transcript_path`. ⛔ P0-08
- [ ] **P8-03** `server` · M — **MCP App widgets** rendered in Chat/Cowork: a sources viewer and a feedback
  form that posts to `submit_feedback`.
- [ ] **P8-04** `server` · S — **Citation URLs in tool results**, so answers in Chat/Cowork can link each
  citation to the passage page from P3-04.

---

## 2. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Mod API changes between Claude Code releases | Mod stops loading or misbehaves after an update | Weekly CI against the latest release (P0-02); pinned minimum version (P7-07) |
| IT sets `allowManagedModsOnly` | Mod does not load from Git or claude.ai sync | MDM-copied directory marketplace (P7-04) |
| The organization manages Claude Code and a user installs the mod themselves | Instructions load but never reach the model (D-25) | Unsupported; install through managed settings (P7-04) |
| Desktop surface lacks a UI feature the terminal has (e.g. link clicks) | Citation UX differs | Decided by spike P0-05; button-row fallback |
| Connector sign-in does not work in Claude Code | No MVP | Spike P0-04 first; server-side auth work early |
| Users cannot reach a private repo | Install and updates fail | Settled by D-02: the repo is public and holds no playbook text |
| Users disable the plugin | Policy not enforced | Accepted: Yvoke is opt-in (D-09) |
| The mod blocks users' other Claude Code work (it runs in every session) | Coding sessions lose shell and file tools; every prompt needs a playbook | Enforce only in sessions started with `/yvoke` (P1-10, D-13); no IT lockdown (D-09) |
| A `tool.call` hook cannot tell which agent made the call | Lead-only rules (P6-04) cannot be enforced | Spike P0-07 before Phase 6 is designed; the budget (P6-06) uses `agent.spawn` instead |
| The mod cannot reach a claude.ai connector in practice (documented as working through `$.mcp.call`) | Live playbooks, citation pane, feedback and sync have no server | Settled by D-03: Claude Code uses the plugin's own `.mcp.json` entry; P0-04 confirms `$.mcp.call` on it |
| A policy hook throws or times out, or mods are off (`disableAllHooks`, `--safe-mode`, hooks worker crashed) | The tool runs or the prompt goes through: policy fails open | `.catch` on every enforcing hook (design 4.4); Claude Code's permission prompts still apply |
| Server tasks are on the critical path (P1-01, P1-06 for M1; P4-01 for M3; P5-01, P6-01 later) | Plugin work waits on yvoke-web | Agree dates for the `server` tasks with the yvoke-web team before Phase 1 starts |
| Non-developer users find the Code tab, folder trust and slash commands unfamiliar | Slow adoption; support load | Scripted folder setup (P7-08), user guide (P7-05), pilot measures onboarding (P7-09) |

## 3. References

- Yvoke Desktop functional spec: `yvoke-desktop/spec/` (chapters 1–8); tests: `yvoke-desktop/tests/`
- Claude Code mods: <https://code.claude.com/docs/en/plugins/mods/overview>
- Manage mods for an organization: <https://code.claude.com/docs/en/plugins/mods/admin>
- Create / host a marketplace: <https://code.claude.com/docs/en/plugins/create-marketplace>,
  <https://code.claude.com/docs/en/plugins/host-marketplace>
- Manage plugins for an organization (managed settings): <https://code.claude.com/docs/en/plugins/org>
- Skills (frontmatter): <https://code.claude.com/docs/en/skills>
- Plugin support per surface: <https://claude.com/docs/plugins/platform-support>
- MCP Apps: <https://claude.com/docs/connectors/building/mcp-apps/getting-started>
- Review of draft v3, with the findings behind design section 4: [2026-10-05-plan-v3-review.md](https://github.com/yvoke-dev/yvoke-claude-plugin/blob/8f1bbb16c093f829e09675b0a865dfcc8425520a/docs/reviews/2026-10-05-plan-v3-review.md) (in git history)
- yvoke-desktop coverage check (gaps C1–C13; D-11 decided): [2026-10-05-yvoke-desktop-coverage.md](https://github.com/yvoke-dev/yvoke-claude-plugin/blob/8f1bbb16c093f829e09675b0a865dfcc8425520a/docs/reviews/2026-10-05-yvoke-desktop-coverage.md) (in git history)
