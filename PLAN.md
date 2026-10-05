# Yvoke for Claude — implementation plan

> **Status:** draft v3 · 2026-10-05 (revised after two external reviews; implementer notes in section 10)
> **What we are building:** one Claude plugin that brings Yvoke Desktop's capabilities into Claude —
> fully in **Claude Code** (the Desktop app's **Code** tab and the terminal) through a **mod**, and in a
> reduced form in **Chat** and **Cowork** through the same plugin's skills, connector, agents and hooks.
> **Reference implementation:** [`yvoke-dev/yvoke-desktop`](https://github.com/yvoke-dev/yvoke-desktop) —
> its `spec/` chapters say *what* each feature is for; its `tests/` say *exactly* how it behaves.

---

## How to use this plan

**Progress is tracked with the checkboxes in this file.**

| Mark | Meaning |
| --- | --- |
| `- [ ]` | Not done |
| `- [x]` | Done: its **Done when** holds, and the change is merged to `main` |
| `⛔ D-xx` / `⛔ P1-03` | Blocked until that decision or task is done |
| `🔍` | Needs verification in a spike before it can be designed in detail |

Rules:

1. **Tick a box in the same pull request that completes the task**, and append the PR link:
   `- [x] **P0-01** … ([#12](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/12))`.
2. **Never renumber.** New work gets the next free ID in its phase; dropped work is struck through
   (`~~P3-05~~`) with one line saying why, not deleted.
3. **Decisions are tasks too** (section 4). A decision is ticked when its outcome is written under it.
4. Each task names its **owner area**: `plugin` (this repo), `server` (yvoke-web / its MCP server),
   `IT` (deployment, managed settings), `PO` (product-owner decision).
5. Size is a rough guess: **S** ≤ 1 day, **M** ≤ 3 days, **L** ≤ 2 weeks.

Count progress from the repository root:

```bash
echo "done $(grep -cE '^\s*- \[x\]' PLAN.md) / total $(grep -cE '^\s*- \[[ x]\]' PLAN.md)"
```

---

## 1. Goal

Consultants and support engineers should get the same grounded, cited, playbook-scoped answers from the
Yvoke knowledge base inside Claude that they get in Yvoke Desktop, on their own Claude subscription, without
installing a separate app.

**Success looks like:**

- A consultant installs one plugin (or IT installs it for them), opens the Code tab in Claude Desktop, accepts
  or changes the session's area, mode and playbook (defaults: OIM, single agent, `oim-full`), asks a
  question, and gets an answer whose citations open the cited passage.
- The playbook decides which Yvoke tools the assistant may use, and that rule is enforced in code, not only
  requested in a prompt.
- 👍/👎 feedback on an answer reaches yvoke-web.
- A multi-agent profile runs lead → specialists → reviewer, with review enforced in code.
- Behaviour is pinned by automated tests in this repo, as it is in yvoke-desktop.

## 2. Scope and target surfaces

One plugin, three surfaces. Each surface loads only the parts it supports.

| Surface | Loads from this plugin | Fidelity target |
| --- | --- | --- |
| **Claude Code** — Desktop **Code** tab, terminal, JetBrains | skills, connector, agents, hooks, **mod** | **Full parity** (primary target) |
| **Cowork** (Desktop) | skills, connector, agents, hooks (no mod) | Playbooks, MAS, tool scoping via hooks; no custom UI |
| **Chat** (Desktop / web / mobile) | skills, connector (no agents, hooks or mod) | Playbooks and knowledge-base tools; anything enforced must live on the server |

Not targeted: VS Code extension UI (mod hooks run there, but nothing it draws is shown), WSL sessions in the
Desktop app (no plugins), cloud sessions (hooks run, nothing is drawn).

## 3. Architecture

### 3.1 Planned repository layout

```text
yvoke-claude-plugin/
├── PLAN.md                         ← this file
├── README.md
├── AGENTS.md                       ← rules for agents working in this repo (P0-01)
├── CLAUDE.md                       ← one line, `@AGENTS.md`, so the two cannot drift
├── .claude-plugin/marketplace.json ← the repo is its own marketplace
├── plugins/yvoke/
│   ├── .claude-plugin/plugin.json  ← name, version, userConfig, "types"
│   ├── .mcp.json                   ← Yvoke MCP server, if not provided as a claude.ai connector (D-03)
│   ├── skills/<playbook>/SKILL.md  ← generated from the server's playbooks, Chat/Cowork only (P1-04)
│   ├── agents/*.md                 ← only if profiles are generated statically (D-06)
│   ├── hooks/hooks.json            ← { "modules": ["./register.tsx"] }
│   ├── hooks/register.tsx          ← the mod entry point; wires the modules below
│   ├── src/                        ← policy, citations, feedback, playbooks, orchestration, …
│   ├── types/index.d.ts            ← contract for $.state values
│   └── tests/*.test.ts             ← `claude plugin test plugins/yvoke`
├── scripts/                        ← generators (skills, agents), release helpers
├── deploy/                         ← managed-settings templates for IT (P7-03)
├── docs/                           ← spikes, contributing, security review, user guide
└── .github/workflows/              ← CI (P0-02), catalog sync (P1-05)
```

### 3.2 Runtime picture

```text
Claude Code engine (Desktop Code tab / terminal)
 ├─ skills/          playbooks for Chat and Cowork only (D-11); not offered in Claude Code
 ├─ connector        Yvoke MCP server → search_corpus, get_section, verify_citations, …
 └─ mod (register.tsx), in-process hooks:
     ├─ AbovePrompt      session setup band: area, mode, playbook; read-only once locked (P1-08)
     ├─ tool.call        deny by default; per-playbook scoping; web allow-list; compute tools
     ├─ prompt.submit    locks the session's selection on the first question; playbook preflight
     ├─ prompt.compose   system prompt = base instructions + the session's playbook, from the server
     ├─ ui.render        citation links in replies; 👍/👎 under replies; delegation cards
     ├─ Pane             citation source panel; feedback comment form
     ├─ turn.complete    enforced review rounds; feedback/sync capture
     └─ $.mcp.call       every server call goes through the connector's own sign-in
```

### 3.3 Principles carried over from yvoke-desktop

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
  **Done when** includes a test for the throw and the timeout path (section 10.4).
- **One session, one setup.** A session's area, mode and playbook are chosen once and cannot change
  afterwards; a different setup is a new session (`/clear`). Decided 2026-10-05 (D-11).

### 3.4 Platform constraints that shape the design

- The **mod API is early access** and changes between Claude Code releases. CI must run against the newest
  release (P0-02), and the minimum supported version is pinned (P7-07). Mods need **Claude Code ≥ v2.1.287**.
- **Mods are not sandboxed** and run with the user's permissions. `claude plugin validate` lists every API a
  mod calls; keep that list minimal (no `$.fs`, no `$.process`) so security review is easy (P7-06).
- A user can **disable the plugin**. Hard guarantees (no shell, no file writes) need managed settings from IT
  (P2-07). The mod's own enforcement is the default, not the last line of defence.
- If IT sets **`allowManagedModsOnly`**, mods installed from Git or claude.ai sync stop loading. Only a mod
  copied by MDM into an admin-only directory marketplace counts as the organization's (P7-04).
- In the Code tab, **no mod loads until the user trusts the session's folder**. Users will keep a dedicated
  "Yvoke" folder (P7-08).
- **A mod's hooks run in every session that loads the plugin**, not only in the Yvoke folder. Without
  scoping, deny-by-default and the playbook gate would block the user's other Claude Code work, such as
  coding. The mod enforces only inside the Yvoke folder (P1-10).

---

## 4. Decisions

Each decision blocks the tasks listed under it. Record the outcome and date under the box when ticking.

- [ ] **D-01** `PO` — **Which Claude plans do users have?** Individual Pro/Max (as yvoke-desktop's spec
  assumes) or a Team/Enterprise organization?
  - Decides the distribution route: Pro/Max → Git marketplace, ideally pushed by MDM managed settings
    (route B); Team/Enterprise → claude.ai organization sync (route C) is also available.
  - Blocks: P7-03, P7-04.
  - **Install route decided 2026-10-05** (together with D-09 and P7-04): the pilot installs from the Git
    marketplace (route A or B); rollout moves to an MDM-copied directory marketplace (route D) once IT
    enforces policy. Plugins from Git, a URL or claude.ai sync always run as *user* mods: they cannot be
    listed in `prependPlugins` and do not load under `allowManagedModsOnly`. Still open: which plans users have.
- [ ] **D-02** `PO` · `IT` — **How do users get access to the marketplace repository?** Options: users' own
  GitHub access to a private repo; a public repo containing no playbook text; or yvoke-web serving
  `marketplace.json` and a plugin `archive` behind an auth header.
  - Recommendation: if D-04 chooses live playbooks, the repo holds no playbook text and can be public.
  - Blocks: P7-04.
- [ ] **D-03** `server` · `plugin` — **How does Claude Code reach the Yvoke MCP server, and how does it sign
  in?** A claude.ai custom connector (already works in Claude sessions today), or a `.mcp.json` entry in the
  plugin using MCP OAuth against Entra ID.
  - The mod itself calls the server for live playbooks (P1-07), the citation pane (P3-03), feedback (P4) and
    sync (P5). Either route works for that: `$.mcp.call(server, …)` calls any server the session has
    connected, claude.ai connectors included, with the engine's own credentials; only `$.mcp.connect` is
    limited to servers the plugin's own manifest lists. Decide on sign-in, naming (a connector's name is not
    known at build time) and Chat/Cowork reach instead (review H3).
  - If the server is also offered as a claude.ai or organization connector, use the same URL in `.mcp.json`
    so users who have both see one set of tools.
  - Depends on: P0-04 findings.
  - Blocks: P1-01, P1-02.
- [x] **D-04** `PO` — **Playbooks: live or copied?** Live: skill stubs (name and description only) are
  generated, and the mod fetches each playbook's text when it is invoked (needs server task P1-06). Copied:
  full `SKILL.md` text generated into the repo by a scheduled job.
  - Recommendation: **live**, to keep yvoke-desktop's "no stale instructions" rule. Copied text also
    works in Chat and Cowork, where no mod runs, so Chat/Cowork may need copied text either way (see P8-01).
  - **Claude Code is settled by D-11** (live, through the mod, no skills). This decision now covers only
    Chat and Cowork.
  - **Decided 2026-10-05: live stubs everywhere.** Each generated Chat/Cowork skill holds only the
    playbook's name and description and tells Claude to call `get_playbook` with that name first and follow
    what it returns. No playbook text is ever committed, so the public repo stays public (D-02). Risk: in
    Chat and Cowork nothing enforces the call; if P0-08 shows the model skipping it, fall back to copied text
    in a separate private marketplace.
  - Blocks: P1-04, P1-05.
- [ ] **D-05** `plugin` — **Naming.** The plugin name is user-facing (`/yvoke:<skill>`) and prefixes the
  mod's own tools (`mcp__<plugin>__<tool>`). The server's tool names differ by how it is reached: a server
  in the plugin's `.mcp.json` is `plugin:<plugin>:<server>` with tools `mcp__plugin_<plugin>_<server>__<tool>`;
  a claude.ai connector gets a connector-specific name (in the Desktop Code tab, `mcp__<uuid>__<tool>`).
  Neither collides with the mod's tools, so the plugin can simply be named `yvoke`.
  🔍 Confirm the names on each surface (P0-04).
  - Blocks: P2-04, P2-05.
- [ ] **D-06** `PO` · `plugin` — **Multi-agent profiles: registered live by the mod (`$.agent.register`)
  or generated into `agents/*.md`?** Live keeps the server as source of truth and works only in Claude Code;
  generated files also work in Cowork.
  - Generated files fix `model`, `effort` and `tools` at build time. Role models and budgets from deployment
    configuration (P6-02) need live registration. 🔍 Unless agent files accept `${user_config.*}`.
  - Depends on: P0-07.
  - Blocks: P6-01.
- [ ] **D-07** `PO` — **Should conversations be synced into the user's Yvoke account** (as Yvoke Desktop
  does), or stay only in Claude? This affects privacy review, server work, and whether feedback can attach to
  a server message id.
  - Privacy: sync's retry queue (P5-02) keeps questions and answers unencrypted in `$.store` on disk until
    they are sent, the same class of issue as yvoke-desktop decision #10.
  - Blocks: all of Phase 5.
- [ ] **D-08** `PO` · `server` — **Feedback shape.** Yvoke Desktop's feedback is keyed to a server message
  id that only exists because it syncs conversations. Without sync (D-07), feedback must be self-contained:
  rating, comment, question, answer, playbook, cited ids, model, client and plugin version.
  - Recommendation: **always self-contained**, whatever D-07 decides. If sync is added later, P5-04 adds
    the server's message id to the same payload. Phase 4 then does not wait on D-07.
  - Blocks: P4-01.
- [ ] **D-09** `IT` — **Lockdown level.** Mod-only enforcement (user can disable the plugin), or managed
  settings that deny `Bash`, `Write`, `Edit`, etc. on consultant machines.
  - Managed `permissions.deny` applies to every Claude Code session on the machine, not only the Yvoke
    folder. It suits consultant-only machines. On machines where users also code with Claude Code, the
    options are mod-only enforcement, or an organization-managed policy mod (`prependPlugins`) that applies
    the deny only inside the Yvoke folder. 🔍 Confirm whether users can disable a managed policy mod.
  - Blocks: P2-07.
- [x] **D-10** `PO` — **Is Chat/Cowork support in scope for v1**, or Claude Code only?
  - **Decided 2026-10-05 (Eduard): Claude Code plus playbook stubs.** v1 is built for Claude Code. Chat and
    Cowork get only the live playbook stubs (P8-01) over the Yvoke connector: knowledge-base answers with a
    playbook, but no setup band, scoping, rating or multi-agent mode there. P8-02 to P8-04 move after v1.
  - Blocks: Phase 8.
- [x] **D-11** `PO` · `plugin` — **In Claude Code, are playbooks skills or mod state?**
  - **Decided 2026-10-05 (Eduard): mod state, chosen once per session.** A small Yvoke UI in the session
    selects the **area** (default OIM), the **mode** (single agent or multi-agent; OIM offers both) and, for
    single agent, the **playbook** (default `oim-full`). The agent's system prompt is the base instructions
    loaded from the server, with the playbook's text added to it. Once chosen, area, mode and playbook cannot
    change for that session, and all three stay visible in it.
  - Consequences: no playbook skills in Claude Code (they stay for Chat/Cowork, P1-04, P8-01); the list of
    areas and playbooks is read live from the server; nothing to restore or switch mid-conversation; the
    model cannot start a playbook by itself.
  - Blocks: P1-08, P1-07, P1-12.

---

## 5. Feature parity map

Every Yvoke Desktop capability, where it lands, and which tasks deliver it. Chapters refer to
`yvoke-desktop/spec/`.

| Yvoke Desktop capability | Spec | Claude Code (mod) | Cowork | Chat | Tasks |
| --- | --- | --- | --- | --- | --- |
| Knowledge-base tools (search, sections, graph, records) | 2 | ✅ connector | ✅ | ✅ | P1-01, P1-02 |
| Base instructions from the server | 2 | ✅ | ✅ | 🔍 | P1-01 |
| Playbooks (pick, `/` autocomplete, sticky per conversation) | 1, 2 | ✅ setup band, fixed per session (D-11) | ✅ (skills) | ✅ (skills) | P1-04 – P1-08, P1-12 |
| Playbook required for a single-agent question | 1 | ✅ (the default `oim-full` applies) | — | — | P1-08 |
| Playbook preflight check | 1, 2 | ✅ | — | — | P2-06 |
| Deny by default; no shell | 2 | ✅ + managed settings | hooks | n/a | P2-01, P2-07 |
| Per-playbook tool scoping | 2 | ✅ | hooks | server only | P2-02, P2-03, P8-02 |
| Safe compute tools | 2 | ✅ | — | — | P2-05 |
| Domain-restricted web search/fetch, WAF hosts refused | 2, 6 | ✅ | hooks | — | P2-04 |
| Clarifying questions | 1, 2 | ✅ native `AskUserQuestion` | ✅ native | ✅ in text | P1-09 |
| Clickable citations → cited passage panel | 1 | ✅ | — | links only | P3-01 – P3-04, P8-04 |
| Rate an answer (👍/👎, comment required on 👎) | 1, 7 | ✅ | — | MCP App | P4-01 – P4-04, P8-03 |
| Conversations synced to the Yvoke account | 4, 7 | optional (D-07) | — | — | Phase 5 |
| Multi-agent profiles: lead, specialists, reviewer | 3 | ✅ | agents + hooks | — | Phase 6 |
| Enforced review rounds, banner when unapproved | 3 | ✅ | hooks | — | P6-05, P6-07 |
| MAS trace uploaded to yvoke-web | 3, 7 | optional (D-07) | — | — | P5-05 |
| Model and thinking level per conversation | 1 | native | native | native | — |
| Image attachments, stop, copy | 1 | native | native | native | — |
| Local history and search | 4 | native (`/resume`) | native | native | — |
| Error origin prefixes (`Claude:` / `Entra:` / `Yvoke Backend:`) | 1 | ✅ for server errors | — | — | P1-03 |
| Installer, updates, diagnostics | 8 | plugin auto-update | org sync | org sync | Phase 7 |

---

## 6. Work plan

### Phase 0 — Foundation and spikes

The spikes de-risk everything later in the plan. Record each spike's findings in `docs/spikes/<id>.md`
(what was tried, Claude Code version, surface, result) before ticking it.

- [ ] **P0-01** `plugin` · S — **Repository scaffold.**
  - `README.md`; `.gitignore`; `.claude-plugin/marketplace.json`; `plugins/yvoke/.claude-plugin/plugin.json`;
    `hooks/hooks.json`; a no-op `hooks/register.tsx`; `types/index.d.ts`; `package.json` and `tsconfig.json`
    for type-checking.
  - `AGENTS.md` for this repo, carrying over yvoke-desktop's rules that apply here: TDD,
    "a test does not count until you have seen it fail", regression test first, no secrets in logs,
    smallest change at the root cause. `CLAUDE.md` is the single line `@AGENTS.md`, so there is one copy
    of the rules and nothing to keep in sync.
  - Done when: `claude plugin validate .` and `claude plugin validate plugins/yvoke` pass, and the plugin
    loads with `claude --plugin-dir plugins/yvoke`.
- [ ] **P0-02** `plugin` · M — **CI.** GitHub Actions on every push and PR:
  `claude plugin validate` (marketplace and plugin), `tsc --noEmit`, `claude plugin test plugins/yvoke`.
  Run on the pinned minimum Claude Code version and on the latest release, plus a weekly scheduled run
  against the latest release so mod API changes are caught early.
  - `claude plugin test` needs no session, sign-in or network (per the mods test docs). Each test has a
    5-second default timeout; tests that stub slow model calls set `timeoutMs`.
  - A check that `CLAUDE.md` is still exactly `@AGENTS.md`, in place of yvoke-desktop's
    `AgentRuleFilesParity` test.
  - Done when: a deliberately broken test turns the workflow red, and restoring it turns it green.
- [ ] **P0-03** `plugin` · S — **Contributor guide** (`docs/contributing.md`):
  - terminal: `claude --plugin-dir plugins/yvoke`;
  - Desktop Code tab: `CLAUDE_CODE_PLUGIN_DIRS` and `CLAUDE_CODE_PLUGIN_DIR_WATCH=1` in the `env` block
    of `~/.claude/settings.json`, or add the working copy as a local marketplace and use `/reload-plugins`;
  - type-checking with the generated `.claude-plugin/types/`; reading `claude --debug` output.
  - Done when: a second developer follows it from a clean machine and gets a hot-reloading mod.
- [ ] **P0-04** `plugin` · `server` · S — **Spike: reaching the Yvoke server from Claude Code.**
  - Can the Yvoke MCP server be used from the Code tab as a claude.ai connector, from a plugin `.mcp.json`,
    or both? How does each sign in with Entra ID? What server and tool names does each produce
    (expected: `mcp__plugin_yvoke_<server>__…` from `.mcp.json`, `mcp__<uuid>__…` for a connector in the
    Desktop Code tab)?
  - From the mod: `$.mcp.call(server, "get_section", …)` and `search_corpus`, once against a `.mcp.json`
    server (after `$.mcp.connect`) and once against the claude.ai connector by the name `/mcp` lists. The
    API documents both as working; confirm it on the Desktop Code tab.
  - Does Claude Code apply the server's MCP `instructions` to the system prompt, for a `.mcp.json` server
    and for a connector?
  - What happens when the Entra token expires during a session: silent refresh, a sign-in prompt, or a
    failed call? What error does `$.mcp.call` return then, so P1-03 can tell the user to sign in again?
  - Done when: a throwaway `/yvoke-ping` command prints the server version and one search hit, and the
    findings close D-03 and inform D-05.
- [ ] **P0-05** `plugin` · S — **Spike: drawing on the `desktop` surface.**
  - Redraw an `AssistantMessage` as `Markdown` with links and `Button`s; check whether a link click raises
    `ui.press` (`onLinkPress`) in the Desktop Code tab or only in the fullscreen terminal; open a `Pane`
    with an `Input` and submit it, as the 👎 comment form (P4-03) will.
  - Done when: findings say which citation UI P3-02 uses (clickable links vs. a row of source buttons), and
    whether the feedback form works in a pane on both surfaces or needs a `/yvoke-feedback` command instead.
  - Mod commands cannot contain `:` (names are letters, digits, `_` and `-`), so every command the mod
    registers is `/yvoke-<name>`. Skills keep `/yvoke:<skill>`.
- [ ] **P0-06** `plugin` · S — **Spike: playbook mechanics.**
  - `skill.prompt` replacing a skill's text at invocation; `tool.call` denying `mcp__…` tools;
    `$.state` surviving across turns.
  - Hiding tools: per the mods reference, `tool.describe` can only reword a tool or defer it behind tool
    search, and `agent.offer` can withhold subagent types. Tools are therefore denied at call time, not
    hidden; confirm, and check that a deferred tool stays out of the model's context until searched.
  - `$.session.cwd` and `$.session.root` in the Code tab and the terminal: which one names the folder the
    session was opened in, and does it change during the session (P1-10)?
  - `prompt.submit` returning `{ drop }`: does the typed text stay in the prompt box? If not, restore it
    with `$.prompt.fill` (P1-08 promises the draft is kept).
  - `/clear`, `/resume` and `/branch`: `$.state` resets on `/clear` and `session.start` does not fire again
    after any of them. Is `$.session.id` the same after `/resume`, so state saved per session can be
    restored (P1-08, P6-01)?
  - Done when: findings confirm (or rule out) live playbooks (D-04), sticky scoping (P2-02) and the folder
    check (P1-10).
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
  - Done when: findings close D-06 and say how P2-01 and P6-04 tell the lead from a specialist.
- [ ] **P0-08** `plugin` · S — **Spike: Cowork and Chat.** Install a test plugin on claude.ai: confirm what
  Chat loads, whether Cowork runs plugin `hooks.json` command hooks (`PreToolUse` deny, `Stop` block), and
  whether skill `disallowed-tools` has any effect outside Claude Code.
  - Command hooks keep no state between calls (no mod, no `$.state`). Check that a hook can work out the
    active playbook and whether the reviewer ran from the transcript at `transcript_path`, as P8-02 needs.
  - Done when: findings feed D-10 and Phase 8.

- [ ] **P0-09** `server` · `IT` · S — **Entra client registration for Claude clients** (section 11.2).
  A public-client app registration (or the desktop's, extended) with the redirect URIs Claude Code and
  claude.ai use, consented for the API scope yvoke-web checks. Feeds D-03 and P1-02.
  - Done when: `claude mcp add --transport http --client-id <id> --callback-port <port> yvoke <url>` signs
    in from a clean machine and lists the tools, and the claude.ai connector does the same.

### Phase 1 — Knowledge base, base instructions and playbooks (MVP)

**Milestone M1:** in the Code tab, a user picks a playbook, asks a question, and gets a grounded answer.

- [ ] **P1-01** `server` · S — **Serve the base instructions as the MCP server's `instructions`.** Return the
  `default-chat` prompt in the MCP `initialize` result, so every Claude client that honours server
  instructions applies them, with no copy in this repo. ⛔ D-03
  - Fallback if P0-04 finds a surface that ignores them: in Claude Code the mod fetches the text from the
    server and appends it as a section (`scope: 'session'`) from a `prompt.compose` hook (`prompt.section` can
    only rewrite or drop an existing section); in Chat/Cowork a skill tells Claude to fetch it.
  - Done when: a Claude Code session shows the instructions under the server's entry and an answer follows
    the citation contract (bare `[uuid]` markers).
- [ ] **P1-02** `plugin` · S — **Connector configuration.** Ship the chosen setup from D-03 (`.mcp.json` with
  the server URL, or documented claude.ai connector steps), with Entra sign-in. ⛔ D-03
  - The URL is written into `.mcp.json` as a fixed value, not `${user_config.*}`: Chat ignores a server whose
    URL references one, and Cowork ignores it when the option has no default.
  - Done when: a fresh machine with the plugin installed can call `search_corpus` after one sign-in.
- [ ] **P1-03** `plugin` · S — **One server client module** (`src/server.ts`) used by every feature:
  - finds the connected Yvoke server: a fixed name if D-03 picks `.mcp.json` (`plugin:yvoke:<server>`),
    otherwise discovered at session start, since a connector's name is not known at build time;
  - wraps `$.mcp.call` with a timeout;
  - treats a body starting with `ERROR:` as a failure even without the error flag (as yvoke-desktop's
    `McpPrompts.callGetSection` does);
  - prefixes server failures with `Yvoke Backend:`.
  - Done when: tests cover not connected, timeout, error flag, `ERROR:` body and success.
- [ ] **P1-04** `plugin` · M — **Skill generator** (`scripts/generate-skills.ts`), **for Chat and Cowork
  only** (D-11). Reads the server's playbook catalogue (`prompts/list` with `_meta`) and writes one
  `skills/<name>/SKILL.md` per playbook. In Claude Code the mod hides them (P1-08). ⛔ D-04, D-10
  - Leaves out playbooks whose `targetAgent` is `orchestrator` or `reviewer`, and keeps `prototype` playbooks
    out of the default set.
  - Writes `tools` and `codeExecution` from `_meta` into the skill's metadata so the mod can read them.
  - Port the relevant cases from yvoke-desktop's `promptMapping.test.ts` and `playbooks.test.ts`, including
    the `_meta` vs `meta` regression.
  - Done when: running it against the server produces a stable, reviewable diff.
- [ ] **P1-05** `plugin` · S — **Catalogue sync job.** A scheduled GitHub Action runs P1-04 and opens a PR
  when playbooks change on the server.
  - Done when: adding a test playbook on the server produces a PR within a day.
- [ ] **P1-06** `server` · S — **`get_playbook(name)` MCP tool** returning a playbook's full text and
  metadata. Needed because a mod can call MCP tools but not read MCP prompts. Required by D-11.
- [ ] **P1-07** `plugin` · M — **System prompt from the server.** When the session's setup locks (P1-08),
  the mod fetches the base instructions and, in single-agent mode, the playbook's text (P1-06), and serves
  them from a `prompt.compose` hook as one `scope: 'session'` section: base instructions plus the playbook
  text, **base instructions first and the playbook after them** (decided 2026-10-05, as in yvoke-desktop:
  the playbook's rules win any conflict). A fetch failure drops the question with a clear
  `Yvoke Backend:` message and keeps the setup unlocked. There is deliberately no cached fallback.
  ⛔ D-11, P1-06
  - Done when: tests cover success, server down, unknown playbook, and a hook failure (fails closed).
- [ ] **P1-08** `plugin` · M — **Session setup: area, mode, playbook** (D-11).
  - In a new Yvoke session a band above the prompt (`ui.render` on `AbovePrompt`) shows three `Select`s:
    **area** (default OIM), **mode** (*Single agent* plus the area's multi-agent profiles; OIM offers both)
    and, for single agent, **playbook** (default `oim-full`). Lists come live from the server (P1-12);
    prototypes are hidden unless enabled.
  - The selection **locks when the first question is sent**: the user can accept the defaults by just
    typing. From then on the band shows a read-only line (*OIM · Single agent · oim-full*), and the status
    line shows the same. Nothing can change it; a different setup is a new session (`/clear`).
  - Kept in `$.state`, and in `$.store` under the session id so `/resume` restores it and `/branch` keeps it
    (`classic.SessionStart` `source`). 🔍 P0-06 for `/branch`.
  - Playbook skills are not offered in Claude Code: generated skills carry `disable-model-invocation`, and
    a `skill.prompt` hook inside the Yvoke folder answers with a pointer to the setup band.
  - A server with no areas or playbooks: the band says so and questions are refused (no fallback).
  - Only inside the Yvoke folder. ⛔ P1-10, P1-12
  - Done when: tests cover the defaults, changing each select before the first question, the lock, the
    read-only line on `terminal` and `desktop`, `/clear`, `/resume`, and server down.
- [ ] **P1-09** `plugin` · S — **Clarifying questions.** Deny the server's `ask_clarifying_question` with a
  message telling the model to use Claude Code's native `AskUserQuestion`. Yvoke Desktop intercepts that tool;
  nothing else does.
  - Done when: a test shows the deny and its message.
- [ ] **P1-10** `plugin` · S — **Yvoke session scope.** The mod's hooks run in every session that loads the
  plugin, so the mod decides at session start whether this is a Yvoke session: the session's folder
  (`$.session.cwd` / `$.session.root`) is the configured Yvoke folder or inside it. The folder comes from
  deployment configuration (`userConfig`, default `~/Yvoke`).
  - Inside: policy, playbook gate, compute tools, preflight and UI all apply.
  - Outside: every hook passes the event through unchanged and nothing is registered or drawn, so the user's
    other Claude Code work is untouched.
  - Path matching is on segment boundaries: `~/Yvoke-old` is not inside `~/Yvoke`. **Decided 2026-10-05:**
    plain string comparison of normalised paths, with no file access: case-insensitive on macOS and
    Windows, `\` and `/` treated alike, `~` expanded. A folder reached through a symlink or alias counts as
    outside. The scope is re-checked on every event, because `$.session.root()` moves on `/cd`, a directory
    change by the Desktop app, or a worktree move.
  - 🔍 Confirm in P0-06 which of `cwd` and `root` names the session's folder in the Code tab.
  - Done when: tests cover the folder itself, a subfolder, an unrelated folder, a same-prefix sibling, a
    case difference, Windows separators, and a folder change mid-session.
- [ ] **P1-13** `server` · S — **Keep plugin-control tools away from models that should not call them**
  (section 11.4). `get_playbook`, `submit_feedback` and the sync tools are for the mod, not for the web's
  in-app assistant, which today shares one tool set with every MCP client.
- [ ] **P1-12** `server` · S — **Areas.** An MCP tool (`list_areas`, or `_meta.area` on playbooks and
  profiles) that says which areas exist, which modes and profiles each offers, and each area's default
  playbook (OIM: `oim-full`). 🔍 "Area" here groups playbooks and profiles; it is not yvoke-web's *knowledge
  area* (a content collection such as *OIM Docs*), which the playbook still decides. Agree the name with
  the yvoke-web team.

### Phase 2 — Tool policy

Port yvoke-desktop's `src/main/agent/policy.ts` semantics into a `tool.call` hook. Port the matching cases
from `tests/policy.test.ts` first, watch them fail, then implement.

- [ ] **P2-01** `plugin` · M — **Deny by default.** Allowed: the knowledge-base tools granted (P2-02),
  `ToolSearch`, `AskUserQuestion` (P1-09, P6-04), the compute tools (P2-05), the web tools under P2-04's
  rules, and delegation while a profile is active (P6-04). Everything else is denied
  with: *"The tool … is not available in this application. Use the yvoke knowledge-base tools instead."*
  - Includes `Bash`, file tools, other MCP servers and `Agent`/`Task` outside a profile.
  - Applies only in a Yvoke session. ⛔ P1-10
  - Done when: tests cover each class of tool.
- [ ] **P2-02** `plugin` · M — **Per-playbook scoping.** A playbook that declares tools gets exactly
  those (plus the always-added three); one that declares none gets the default set (`DEFAULT_KB_TOOLS`).
  Scoping follows the session's playbook from P1-08, which never changes during the session. ⛔ P1-08
  - Done when: tests cover declared tools and no declaration.
- [ ] **P2-03** `plugin` · S — **Re-namespacing.** Playbooks name tools by their base name (`search_corpus`).
  The mod maps each to the Yvoke server's real tool name, using the server P1-03 found (D-05 lists the
  forms). Do not port yvoke-desktop's fixed `MCP_TOOL_PREFIX`; port `qualifyTool`'s tests with the prefix
  supplied at run time. The built-ins `WebSearch`, `WebFetch` and `ToolSearch` are never prefixed. ⛔ D-03, D-05
- [ ] **P2-04** `plugin` · M — **Web access rules.** ⛔ D-05
  - Granted only when enabled in deployment configuration **and** declared by the active playbook.
  - `WebSearch`: `allowed_domains` replaced with the configured hosts before the call runs.
  - `WebFetch`: URL checked against the allow-list (host or subdomain, path on segment boundaries); hosts in
    `WAF_CHALLENGED_HOSTS` refused first, with an instruction to use `WebSearch`; an empty or unparseable list
    refuses everything.
  - The domain list comes from the plugin's `userConfig`, like every other deployment setting (Yvoke folder,
    role models, budgets). **Decided 2026-10-05:** all settings stay in `userConfig` and are editable by the
    user, as in yvoke-desktop; where IT deploys managed `pluginConfigs`, those values apply. 🔍 Confirm in
    P0-06 that managed `pluginConfigs` override a user's own values; if they do not, record it in section 7.
  - Done when: yvoke-desktop's web cases from `policy.test.ts` pass here.
- [ ] **P2-05** `plugin` · M — **Safe compute tools.** Register `calculate`, `statistics` and `date_diff` with
  `$.tool.register`, ported from yvoke-desktop's `computeTools.ts` with its `computeTools.test.ts`. Withheld when
  the active playbook declares `codeExecution: false`. ⛔ D-05
  - Fix while porting: make the `log` (base 10) vs `ln` difference explicit in the tool description.
- [ ] **P2-06** `plugin` · M — **Playbook preflight check.** On the first question of a single-agent
  session, before the setup locks (P1-08), when the area offers at least two playbooks, ask the model
  (`$.model.complete`, no tools, 45 s timeout) whether the playbook fits; if not, show a band with
  **Switch to …** / **Send anyway**. Either answer locks the setup.
  - Fails open on every error, including a reply with `isAnswered: false`: `$.model.complete` reports a
    Claude API failure that way rather than by rejecting. Runs once per session, on the first question.
    Port `playbookValidation.test.ts`.
  - Shows that the check is running (`$.ui.status` or the band above the prompt) while it waits, so a slow
    model does not look like a frozen app. Time spent inside `$.model.complete` does not count against a
    hook's 10-second limit.
  - Done when: tests cover fit, better match, timeout, unparseable reply and unknown suggestion.
- [ ] **P2-07** `IT` · `plugin` · S — **Managed-settings lockdown template** (`deploy/managed-settings.lockdown.json`):
  `permissions.deny` for `Bash`, `Write`, `Edit`, `NotebookEdit`, and the plugin enabled at managed scope so
  users cannot disable it. The deny applies to the whole machine (see D-09). ⛔ D-09
  - Done when: on a test machine, the denied tools stay denied with the plugin disabled.

- [ ] **P2-08** `plugin` · M — **Turn ceilings per question** (decided 2026-10-05). The mod counts the model
  requests of each question per loop (`turn.step`, keyed by `turnId` and `agentId`) and enforces
  configurable ceilings from `userConfig`: single agent 25, lead 60, each specialist and the reviewer 20
  (yvoke-desktop's shipped values). At the ceiling it **delivers rather than discards**: further tool calls
  in that loop are denied with a message telling the model to answer now with what it has, and the answer
  is flagged (*stopped at the turn limit*) by a line under it. A specialist at its ceiling returns its
  partial answer to the lead. Fails closed (3.3). Port the ceiling cases from yvoke-desktop's tests where
  they exist.
  - Done when: tests cover under the limit, at the limit for each role, the flag line, and a hook failure.

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
  - Port `citationLookup.test.ts`. Done when: tests cover chunk hit, document fallback, both missing and
    `ERROR:` body.
- [ ] **P3-04** `server` · S — **Passage deep link** in yvoke-web (open a passage by id in the browser), used
  by the pane's *Open in Yvoke* action and by Chat/Cowork links (P8-04). 🔍 check whether a route exists.

### Phase 4 — Feedback

**Milestone M3:** 👍/👎 on an answer arrives in yvoke-web's feedback screens.

- [ ] **P4-01** `server` · M — **Feedback endpoint for Claude clients**, as an MCP tool (`submit_feedback`), so
  the connector's sign-in authenticates it. Fields per D-08; marked as coming from the Claude plugin with its
  version. ⛔ D-08
- [ ] **P4-02** `plugin` · M — **Turn capture.** For each completed turn, keep the question
  (`prompt.submit`), the final answer text, the active playbook, the model, the cited ids and the tool
  names (`session.append` / `turn.complete`), keyed by turn, in session state. Only the main conversation's
  turns: `turn.complete` also fires for subagents (`e.agentId` set), and those are not rated.
- [ ] **P4-03** `plugin` · M — **👍/👎 under each final reply.** 👍 may carry a comment; 👎 requires one
  (comment form in a pane). A new rating replaces the previous one; the form opens pre-filled with the last
  comment. ⛔ P4-01, P4-02
- [ ] **P4-04** `plugin` · S — **Every submit outcome has a recovery path:** success, error from the server
  (buttons re-enabled, message shown), and a stale or duplicate submit. This is yvoke-desktop's "async action
  state machine" pitfall. Done when: a test covers each of the three.
- ~~**P4-05** `plugin` · S — **Feedback without a synced message.** When conversations are not synced
  (D-07), feedback is self-contained; when they are, it attaches to the server's message id. ⛔ D-07~~
  Dropped: feedback is always self-contained (D-08 recommendation), and attaching the message id is P5-04.

### Phase 5 — Conversation sync and traces (only if D-07 says yes)

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
- [ ] **P5-04** `plugin` · S — **Feedback attaches to the synced message id** once the turn has synced, as an
  extra field on the self-contained payload (D-08).
- [ ] **P5-05** `plugin` · M — **Multi-agent trace upload**: role, round, playbook, model, instructions,
  output, verdict and token counts per step, with a size cap per step. Name the reviewer's real playbook
  (yvoke-desktop records it as "reviewer"). ⛔ P5-01, Phase 6

### Phase 6 — Multi-agent investigations

**Milestone M4:** a profile runs lead → specialists → reviewer with review enforced in code.

- [ ] **P6-01** `plugin` · `server` · M — **Profiles.** Read profiles from the server (new MCP tool
  `list_profiles` / `get_profile`, or a generated `agents/` directory, per D-06). A profile is chosen as the
  session's mode in the setup band (P1-08) and is fixed for the session; prototypes are hidden unless
  enabled. ⛔ D-06, P1-08
- [ ] **P6-02** `plugin` · M — **Specialist agent types**, one per profile specialist, from the base
  instructions plus the specialist's playbook. Tools follow yvoke-desktop's `mapSpecialistTools` (declared or
  default knowledge-base tools, compute unless `codeExecution: false`, web only if declared). Model and effort
  per role from deployment configuration. Port `orchestration.test.ts`.
- [ ] **P6-03** `plugin` · S — **Reviewer agent.** Only `verify_citations`; no base instructions; strict
  verdict parsing: the first line must be exactly `APPROVED` or `REJECTED`; anything else is "no clear
  verdict". This fixes yvoke-desktop's defect where "NOT APPROVED" reads as approved. Done when: a regression
  test for "NOT APPROVED" fails before the fix. Port `review.test.ts`.
- [ ] **P6-04** `plugin` · M — **Lead mode.** While a profile is active, the lead may only delegate, ask
  the user and call `verify_citations` (yvoke-desktop's `orchestration.ts` grants exactly those, and the
  server's orchestrator playbook relies on the last); other knowledge-base tools are denied to it, but not to the specialists (how the hook tells them
  apart comes from P0-07). The desktop runtime adapter text is appended to its
  instructions, with `REVIEW_FEEDBACK_HEADING` and `EVIDENCE_HEADING` byte-identical to the server's.
  The mode is fixed per session (D-11), which also avoids yvoke-desktop's pitfall of resuming across modes.
- [ ] **P6-05** `plugin` · L — **Review enforced in code** (on the lead's `turn.complete`; subagents' turns,
  which carry `e.agentId`, are ignored):
  - delegations happened but no review → re-prompt once to run the reviewer;
  - `REJECTED` or no verdict → revision prompt with the feedback and the specialists' evidence (each capped at
    12,000 characters), up to `maxReviewRounds`;
  - out of rounds → a warning written into the answer text itself (mechanism from P0-07);
  - a stopped (`e.isAborted`) or failed turn is never re-prompted.
  - `$.prompt.submit` waits until the session is idle; do not `await` it inside the `turn.complete` hook.
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
    recorded in section 7.
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
  deployment `pluginConfigs` (Yvoke folder, web domains, role models and budgets; the server URL is fixed in
  `.mcp.json`, see P1-02). ⛔ D-01
- [ ] **P7-04** `IT` · `plugin` · M — **Distribution route set up and tested end to end:** route A (users add
  the repo), B (managed settings via MDM) or C (claude.ai organization sync), including how users get read
  access (D-02). If IT requires `allowManagedModsOnly`, use an MDM-copied directory marketplace instead.
  ⛔ D-01, D-02
  - Decided 2026-10-05: route A/B for the pilot; **route D** (a directory marketplace that MDM copies to the
    same admin-only path on every machine, enabled in managed settings) for rollout once IT enforces policy.
    Only route D makes the mod the organization's, so it can run first (`prependPlugins`) and survives
    `allowManagedModsOnly`.
- [ ] **P7-11** `server` · S — **Per-user rate limit on `/mcp`** (section 11.5). Searches from AI clients are
  not rate-limited today; the plugin moves every consultant onto that route.
- [ ] **P7-05** `plugin` · S — **User guide** (`docs/user-guide.md`): install, sign in, the Yvoke folder,
  picking playbooks and profiles, citations, feedback, what is different from Yvoke Desktop.
- [ ] **P7-06** `plugin` · S — **Security review pack** (`docs/security.md`): the `claude plugin validate`
  `hooks:` and `calls:` output for each release, what data leaves the machine and to where, what the mod
  keeps on disk (`$.store`, e.g. P5-02's queue), and the managed settings options IT has. Notes for IT:
  - keep `allowModsToOverrideDenyRules` off; otherwise any mod a user installs can approve calls a `deny`
    rule refuses. The Yvoke mod only denies and never needs it;
  - managed `PreToolUse` hooks run before every mod, and a block from one is final.
- [ ] **P7-07** `plugin` · S — **Compatibility.** Minimum supported Claude Code version documented and
  tested in CI; the mod reads `$.session.version` at session start and shows a clear message if it is too old.
- [ ] **P7-08** `plugin` · S — **Folder trust.** Document (and if possible script) the dedicated "Yvoke" folder
  users open in the Code tab, so the trust prompt appears once. It is the same folder P1-10 scopes the mod
  to; the script creates it at the configured path.
- [ ] **P7-09** `PO` · M — **Pilot** with 3–5 consultants on macOS and Windows for two weeks; collect issues;
  go/no-go for wider rollout. Include users who are not developers, and record how long each takes from
  install to a first cited answer, and where they get stuck (Code tab, folder trust, slash commands).
- [ ] **P7-10** `plugin` · S — **`/yvoke-doctor` diagnostics** (spec chapter 8). One command that reports
  the Claude Code version against the minimum, whether this is a Yvoke session (P1-10) and why, whether the
  Yvoke server is connected and signed in, the server's version, how many playbooks it offers, and the
  active playbook or profile. Uses P1-03; never prints tokens or other secrets.
  - Done when: tests cover a healthy session, outside the folder, server not connected and signed out.

### Phase 8 — Chat and Cowork (lower fidelity; D-10: only P8-01 in v1)

- [ ] **P8-01** `plugin` · S — **Playbook skills usable in Chat and Cowork.** Without a mod, a stub cannot
  fetch its text itself, so each stub tells Claude to call `get_playbook` first (D-04 decided: live stubs).
  In v1 (D-10). ⛔ P1-06
  - Done when: in Chat and Cowork, invoking a stub makes Claude call `get_playbook` before answering, in the
    P0-08 spike's test conversations.
  - Skills for these surfaces use only the portable frontmatter fields (`name`, `description`, `license`,
    `compatibility`, `metadata`, `allowed-tools`); Claude Code extras such as `disallowed-tools`,
    `context: fork` and `!` command injection do not apply there.
- [ ] **P8-02** `plugin` · M — *After v1 (D-10).* **Cowork hooks** (`hooks.json` command hooks): `PreToolUse` scoping of
  `mcp__…` tools by active playbook; `Stop` blocking a lead from finishing without review. Command hooks keep
  no state, so each call works out the active playbook and whether review ran from the transcript at
  `transcript_path`. ⛔ P0-08
- [ ] **P8-03** `server` · M — *After v1 (D-10).* **MCP App widgets** rendered in Chat/Cowork: a sources viewer and a feedback
  form that posts to `submit_feedback`.
- [ ] **P8-04** `server` · S — *After v1 (D-10).* **Citation URLs in tool results**, so answers in Chat/Cowork can link each
  citation to the passage page from P3-04.

---

## 7. Not planned and known gaps

Write things down here when they are decided against, so nobody "fixes" them by accident.

- **Hiding playbook text from the user.** Skills are readable in Claude's skill viewer; playbooks are also
  readable by any connected client of the server. Not a secret today either.
- **Isolation from the user's own Claude setup.** In Claude, the user's other plugins, skills, memory and
  connectors can affect answers; Yvoke Desktop excluded them. Managed settings can narrow this, not remove it.
  The other direction is handled: outside the Yvoke folder the mod changes nothing (P1-10), but the
  playbook skills and the Yvoke connector are still offered in every session.
- **Company cost reporting.** Model usage stays on each user's Claude subscription, as with Yvoke Desktop.
- **Removing a rejected draft from the record.** Claude Code keeps every turn; the mod only collapses
  rejected drafts on screen (P6-07). The transcript, `/resume` and copy still contain them.
- **Behaviour in VS Code, WSL and cloud sessions.** Hooks may run there, but no UI is drawn (or no plugin
  loads at all); not supported.

## 8. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Mod API changes between Claude Code releases | Mod stops loading or misbehaves after an update | Weekly CI against the latest release (P0-02); pinned minimum version (P7-07) |
| IT sets `allowManagedModsOnly` | Mod does not load from Git or claude.ai sync | MDM-copied directory marketplace (P7-04) |
| Desktop surface lacks a UI feature the terminal has (e.g. link clicks) | Citation UX differs | Decided by spike P0-05; button-row fallback |
| Connector sign-in does not work in Claude Code | No MVP | Spike P0-04 first; server-side auth work early |
| Users cannot reach a private repo | Install and updates fail | D-02: public repo without playbook text, or archive served by yvoke-web |
| Users disable the plugin | Policy not enforced | Managed-settings lockdown (P2-07) |
| The mod blocks users' other Claude Code work (it runs in every session) | Coding sessions lose shell and file tools; every prompt needs a playbook | Enforce only in the Yvoke folder (P1-10); machine-wide lockdown only on consultant-only machines (D-09) |
| A `tool.call` hook cannot tell which agent made the call | Lead-only rules (P6-04) cannot be enforced | Spike P0-07 before Phase 6 is designed; the budget (P6-06) uses `agent.spawn` instead |
| The mod cannot reach a claude.ai connector in practice (documented as working through `$.mcp.call`) | Live playbooks, citation pane, feedback and sync have no server | Spike P0-04; fall back to `.mcp.json` |
| A policy hook throws or times out, or mods are off (`disableAllHooks`, `--safe-mode`, hooks worker crashed) | The tool runs or the prompt goes through: policy fails open | `.catch` on every enforcing hook (section 10.4); managed `permissions.deny` as the floor (D-09) |
| Server tasks are on the critical path (P1-01, P1-06 for M1; P4-01 for M3; P5-01, P6-01 later) | Plugin work waits on yvoke-web | Agree dates for the `server` tasks with the yvoke-web team before Phase 1 starts |
| Non-developer users find the Code tab, folder trust and slash commands unfamiliar | Slow adoption; support load | Scripted folder setup (P7-08), user guide (P7-05), pilot measures onboarding (P7-09) |

## 9. References

- Yvoke Desktop functional spec: `yvoke-desktop/spec/` (chapters 1–8); tests: `yvoke-desktop/tests/`
- Claude Code mods: <https://code.claude.com/docs/en/plugins/mods/overview>
- Manage mods for an organization: <https://code.claude.com/docs/en/plugins/mods/admin>
- Create / host a marketplace: <https://code.claude.com/docs/en/plugins/create-marketplace>,
  <https://code.claude.com/docs/en/plugins/host-marketplace>
- Manage plugins for an organization (managed settings): <https://code.claude.com/docs/en/plugins/org>
- Skills (frontmatter): <https://code.claude.com/docs/en/skills>
- Plugin support per surface: <https://claude.com/docs/plugins/platform-support>
- MCP Apps: <https://claude.com/docs/connectors/building/mcp-apps/getting-started>
- Review of draft v3, with the findings behind section 10: [docs/reviews/2026-10-05-plan-v3-review.md](docs/reviews/2026-10-05-plan-v3-review.md)
- yvoke-desktop coverage check (gaps C1–C13; D-11 decided): [docs/reviews/2026-10-05-yvoke-desktop-coverage.md](docs/reviews/2026-10-05-yvoke-desktop-coverage.md)

---

## 10. Notes for implementers

Read this before you pick up a task, whether you are a person or an agent. It holds what you would otherwise
rediscover. Facts were checked against **Claude Code 2.1.289** on 2026-10-05. The mod API is early access,
so where this section and the generated declarations disagree, the declarations win. Fix this section in
the same PR.

### 10.1 Where things are

- **yvoke-desktop** is public: `git clone https://github.com/yvoke-dev/yvoke-desktop`. It is an Electron app
  built on the **Claude Agent SDK** (`@anthropic-ai/claude-agent-sdk`), so its policy is a `canUseTool`
  callback plus `allowedTools`. Here the same rules become a `tool.call` hook.

  | Plan says | Path in yvoke-desktop |
  | --- | --- |
  | `policy.ts` (`isToolAllowed`, `buildAllowedTools`, `isUrlDomainAllowed`, `WAF_CHALLENGED_HOSTS`) | `src/main/agent/policy.ts` |
  | `DEFAULT_KB_TOOLS`, `qualifyTool`, `MCP_TOOL_PREFIX`, `builtinTool` | `src/shared/types.ts` |
  | `McpPrompts.callGetSection`, `_meta` playbook mapping | `src/main/agent/McpPrompts.ts` |
  | `computeTools.ts` | `src/main/agent/computeTools.ts` (served by `computeServer.ts`) |
  | `citationRehype.ts`, citation pane | `src/renderer/src/components/citationRehype.ts`, `CitationModal.tsx`, `sectionView.ts` |
  | `mapSpecialistTools`, `REVIEW_FEEDBACK_HEADING`, `EVIDENCE_HEADING`, verdict parsing | `src/main/agent/orchestration.ts` |
  | Preflight check | `src/main/agent/playbookValidation.ts`, `PlaybookValidator.ts` |
  | Tests named in this plan | `tests/<name>.test.ts` (vitest) |
  | Functional spec chapters | `spec/01_…` – `spec/08_…` |
  | Agent rules to carry over (TDD, known pitfalls) | `CLAUDE.md` and `.agents/AGENTS.md` |

- **The Yvoke MCP server** today exposes `search_corpus`, `get_section`, `get_toc`, `list_documents`,
  `get_graph_neighbors`, `search_graph_entities`, `query_json_objects`, `get_json_schema`,
  `verify_citations` and `ask_clarifying_question`. `get_playbook`, `submit_feedback`, `list_profiles` and
  the sync tools do not exist yet (P1-06, P4-01, P6-01, P5-01). Server code lives in `yvoke-dev/yvoke-web`.
- **What yvoke-desktop reads over REST, and the plugin's route for it.** The desktop calls yvoke-web's REST
  API (`/api/chat/v1`, `SyncClient.ts`) with its own Entra bearer token, and reads playbooks as MCP
  *prompts*. The mod has neither: Claude Code keeps the connector's token to itself, the only other key
  yvoke-web accepts is the shared ingest machine key (`ApiKeyAuthenticationFilter`, `ROLE_INGEST`), which
  must never ship to users, and the mod API cannot read MCP prompts. So every one of these becomes an MCP
  tool on yvoke-web, authenticated by the connector's sign-in. Each is a thin wrapper: yvoke-web's MCP tools
  are Spring AI `@Tool` classes in `src/main/java/de/palsoftware/yvoke/mcp/tools/`, and
  `chat/api/DesktopSyncController.java` already holds the logic.

  | yvoke-desktop call | Plugin route | Task |
  | --- | --- | --- |
  | `GET /prompts/system/default-chat` (base instructions) | MCP `instructions`, or a `get_system_prompt` tool | P1-01 |
  | MCP `prompts/list` + `prompts/get` (playbooks) | `list_playbooks` / `get_playbook` tools (with areas) | P1-06, P1-12 |
  | `GET /orchestrator/profiles` | `list_profiles` / `get_profile` tools | P6-01 |
  | `PUT /messages/{id}/feedback` | `submit_feedback` tool, self-contained (D-08) | P4-01 |
  | `/conversations…`, `/messages`, `POST /orchestrator/runs` | sync and trace tools, only if D-07 says yes | P5-01 |

  Worst case, if a server tool cannot be added in time: the read-only configuration (base instructions,
  playbooks, profiles) can be generated into the repository by a scheduled job (P1-05's mechanism). The repo
  is public, it goes stale between runs, and it breaks yvoke-desktop decision #14. Feedback and sync have no
  such fallback. A sign-in of the mod's own (Entra device-code flow over `$.http.fetch`) is possible but
  rejected: it would keep a refresh token in plain JSON in `$.store`.

### 10.2 The mod API: where the truth is

- Run `claude --version` and note it in your spike or PR. The declarations for **your** build are written by
  the engine: once a mod has loaded from a folder you own (`--plugin-dir`, `CLAUDE_CODE_PLUGIN_DIRS`), see
  `plugins/yvoke/.claude-plugin/types/claude-code/index.d.ts` (about 20,000 lines; grep for `'tool.call'`,
  `HookBudget` and so on). Do not commit that `types/` folder; it is regenerated on every load.
- `claude plugin validate plugins/yvoke` prints the `hooks:` and `calls:` lines the security review (P7-06)
  needs, and refuses source the engine could not read. Run it before every push.
- `claude plugin test plugins/yvoke` runs `*.test.ts` against the real engine with no session, network, fs
  or process. Import `test`, `expect` and `mock` from `claude-code/testing`. Hooks a test registers sit
  *beneath* the plugin and stand for the engine, so stub the Yvoke server by hooking `mcp.call` in the test:
  `on('mcp.call', () => ({ content: [{ type: 'text', text: '…' }], isError: false }))`. UI tests mount on a
  surface the test names. Loop every UI test over `['terminal', 'desktop'] as const`.

### 10.3 Plan concept → mod API

| Plan uses | API (2.1.289) | Notes |
| --- | --- | --- |
| Deny a tool | `on('tool.call', h)` → `{ deny: reason }` | `e.tool` is the full name; `e.agentId` set for subagents. Managed `PreToolUse` hooks run first. |
| Rewrite a tool's input (WebSearch domains) | `next({ ...e, input: { … } })` | Managed hooks run again on the rewritten call. |
| Gate a prompt | `on('prompt.submit', h)` → `{ drop: reason }` | The reason is shown to the user. Whether the draft stays is 🔍 P0-06; `$.prompt.fill` restores it. |
| Base instructions + playbook (D-11) | `on('prompt.compose', h)`: append `{ id, text, scope: 'session' }` | `prompt.section` cannot add a section. Cached until `$.ui.invalidate('prompt.compose')`; the text is fixed per session, so one fetch at lock. |
| Hide playbook skills in Claude Code | `on('skill.prompt', { skill }, h)` → `{ text }` | Input is only `{ skill, text }`: no caller, no metadata. |
| Session setup band | `ui.render` on `{ component: 'AbovePrompt' }` with `Select`s from `$.ui.resolve(e)` | `mobile` has no `Select`: show the read-only line there. |
| Mod commands | `$.command.register({ name, description })` in `session.start`, answered by `command.run` | No `:` in names. Register last or in `try`/`catch`. |
| Compute tools | `$.tool.register(…)` + `tool.call` on `mcp__yvoke__<name>` | Register in `session.start` (awaited) so they are listed on turn one. |
| Subagents | `$.agent.register` (`yvoke:<name>`), `$.agent.spawn`, `agent.spawn` (`{ deny }`, `parentAgentId`), `agent.offer` (`{ isOffered: false }`) | |
| End of turn | `turn.complete`: `answer`, `reason`, `isAborted`, `agentId`, `usage` | Returning `{ text }` shows a line *under* the answer; the record never changes. |
| Re-prompt | `$.prompt.submit` | Queues a new turn once idle; never `await` it inside `turn.complete`. |
| `/clear`, `/resume`, `/branch` | `classic.SessionStart` (`source`: `startup`, `resume`, `clear`, `compact`, `fork`); `session.end` (`reason`, `sessionId`) | `session.start` fires once per process, not after `/clear`. |
| Yvoke-folder scope | `$.session.root()`, `$.session.cwd()` | `root` moves on `/cd`, host directory changes and worktree moves: re-check per event. |
| Server calls | `$.mcp.call(server, tool, args)` | Any connected server, connectors included; `$.mcp.connect` only for the plugin's own `.mcp.json` entries. |
| Preflight model call | `$.model.complete({ model, prompt, timeoutMs })` | Never rejects for provider errors; check `isAnswered`. Uses the user's quota. |
| Session values | `$.state` (declared in `types/index.d.ts` under `PluginState.yvoke`) | Survives hot reload, not `/clear`. Module variables do not survive a reload. |
| Persistent values | `$.store` | One JSON file per plugin, 4 MiB total, shared by all open sessions: one key per session or turn. |
| Status line / toast | `$.ui.status(text)`, `$.ui.toast(text)` | |
| Pane / band | `$.ui.open({ id, title })` + `ui.render` on `{ component: 'Pane', requestId }`; `{ component: 'AbovePrompt' }` | Elements come from `$.ui.resolve(e)`, not globals. |
| Redraw a reply | `ui.render` on `{ component: 'AssistantMessage' }` | Strings ≤ 10,000 characters per element. `onLinkPress` only where the surface reports clicks. |

### 10.4 Rules that are easy to get wrong

- **Fail closed explicitly.** A hook that throws, returns a wrong shape or outruns its budget (10 s of its
  own time; `$` and `next` calls are free) is skipped, and the engine goes on as if it were not there. Every
  enforcing hook therefore gets a `.catch` with the safe answer (1 s grace):

  ```ts
  on('tool.call', enforcePolicy).catch(() => ({ deny: 'Yvoke: the policy check failed, so this tool was not run.' }))
  on('skill.prompt', livePlaybook).catch(() => ({ text: 'Tell the user: "Yvoke Backend: the playbook could not be loaded." Do not answer the question.' }))
  ```

  Tests cover the throw and the timeout path for each one.
- **Outside the Yvoke folder, call `next(e)` and nothing else.** The mod loads in every Claude Code session
  of the user (P1-10).
- **The module environment is not Node.** It has no `require`, no dynamic `import()` (a module holding one
  does not load), no DOM and no `process`. Static `import` of the plugin's own `.ts` files works, so
  generated data such as a playbook map can be a `.ts` module. JSX compiles against the global `h`.
- **No `$.fs`, `$.process` or `$.http`** unless a decision says so (3.4). Reach yvoke-web only through
  `$.mcp.call`, so the connector's sign-in is the only credential.
- **Never log tokens or full answers** with `$.ui.log`; the debug log is something users attach to tickets.
- **`e` is frozen.** Rewrite with `next({ ...e, x })`.

### 10.5 Development loop

- Terminal: `claude --plugin-dir plugins/yvoke --debug`. Saving a file hot-reloads the module (`register`
  runs again; `$.state` and `$.store` stay).
- Desktop Code tab: set `CLAUDE_CODE_PLUGIN_DIRS` (absolute path) and `CLAUDE_CODE_PLUGIN_DIR_WATCH=1` in the
  `env` block of `~/.claude/settings.json` (not a project's settings), then start a new session.
- A hook that failed or a tree that did not validate shows as one dim line in the transcript while
  hot-reloading (`yvoke: ui.render (<Component>) refused: …`), and always in the `--debug` log.
- Mods do not load in a folder the user has not trusted, in WSL sessions in the Desktop app, or under
  `--safe-mode`. Nothing is drawn in VS Code, `-p` or cloud sessions.

### 10.6 Porting from yvoke-desktop: what does not carry over by itself

The desktop app ran its own agent loop through the Agent SDK. Claude Code runs the loop, and the mod only
steers it, so some desktop behaviours need deliberate work. The full list, with proposals, is in
[the coverage check](docs/reviews/2026-10-05-yvoke-desktop-coverage.md). The ones that affect most tasks:

- **The `yvoke-web` server has its own orchestrator** (`OrchestrationService.java`), which the desktop's
  `orchestration.ts` mirrors grant for grant. When the two disagree, ask before choosing; do not pick the
  desktop's version silently.
- **The delegation tool has two names**: `Task` in allow lists and `Agent` in the tool call the model emits
  (`orchestration.ts` lines 10–15). Match both in `tool.call` and `agent.spawn` hooks.
- **The lead is the main loop.** Its model, effort and instructions are set with `turn.step` and
  `prompt.compose` hooks while a profile is active, not with an agent definition (coverage C5, C6).
- **Nothing is discarded from the transcript.** The desktop dropped failed turns and rejected drafts; Claude
  Code keeps both. Design what the user sees (coverage C8) instead of trying to delete rows.
- **Playbooks never switch mid-session** (D-11): the desktop restarted its session on a switch, and here a
  different setup is simply a new session.
- **Turn ceilings are not native.** If cost has to be bounded, count `turn.step` (coverage C4).
- **Billing.** The desktop removed `ANTHROPIC_API_KEY` to keep billing on the subscription. A plugin cannot,
  so `/yvoke-doctor` reports which applies (coverage C11).

---

## 11. Changes on the web side (yvoke-web)

Everything the plugin needs from yvoke-web, in one place. Facts about the current server were read from
`yvoke-dev/yvoke-web` at `main` on 2026-10-05. Task IDs refer to section 6; `server` tasks belong to the
yvoke-web team and should be dated with them before Phase 1 starts (section 8).

### 11.1 What yvoke-web already has

- **An MCP server at `/mcp`** (Spring AI, streamable HTTP, tools and prompts enabled, name `Yvoke`, version
  from the build). Tools are `@Tool` classes in `src/main/java/de/palsoftware/yvoke/mcp/tools/`; playbooks
  are MCP prompts from `mcp/prompts/PromptsService.java`.
- **MCP OAuth discovery already in place** (`shared/security/ProtectedResourceMetadataController.java`):
  `/.well-known/oauth-protected-resource` names Entra as the authorization server, a 401 from `/mcp`
  carries `WWW-Authenticate: Bearer resource_metadata=…`, and `/.well-known/oauth-authorization-server`
  republishes Entra's endpoints for clients that look for RFC 8414 metadata. This is why Claude clients
  can already connect today.
- **One token check for `/mcp` and `/api/chat/**`** (`SecurityConfig.java`): an Entra JWT from the tenant,
  with the configured audience (`APP_SECURITY_MCP_AUDIENCE`), and either the configured scope
  (`APP_SECURITY_MCP_SCOPE`) or the `User`/`Admin` app role. Users are created on first sign-in. The check
  does not care which client app obtained the token.
- **yvoke-desktop's own Entra sign-in**: a public-client registration (`settings.json`: client
  `a1824d0a-…`, scope `api://a1824d0a-…/desktop`), MSAL with PKCE, whose token the server accepts on both
  `/mcp` and `/api/chat/v1`.
- **The desktop's REST API** (`chat/api/DesktopSyncController.java`, `/api/chat/v1`): `GET /playbooks`,
  `GET /orchestrator/profiles`, `POST /orchestrator/runs`, `GET /prompts/system/{name}`, conversations,
  messages and `PUT /messages/{id}/feedback`.

**Nothing here changes for yvoke-desktop.** Its registration, scope and REST API stay as they are; the
plugin adds a second client and a few MCP tools beside them.

### 11.2 Sign-in for Claude clients (P0-09, decides D-03)

The plugin cannot borrow the desktop's sign-in. Claude Code and claude.ai are OAuth clients of their own,
and Entra offers no dynamic client registration, so each needs a client ID registered in advance.

| Client | How it signs in | Redirect URI to register |
| --- | --- | --- |
| Claude Code, from the plugin's `.mcp.json` | `"oauth": { "clientId": "<id>", "callbackPort": <port>, "scopes": "<api scope> offline_access" }`; no secret (public client, PKCE) | `http://localhost:<port>/callback` (exactly `localhost`, not `127.0.0.1`) |
| claude.ai custom connector (Chat, Cowork, and Claude Code through sync) | Connector's advanced settings: OAuth client ID (and secret, if the registration is confidential) | claude.ai's connector callback 🔍 confirm the exact URL in P0-04 |

Changes:

- **Register the client.** Recommended: a new registration *Yvoke for Claude* that requests the existing
  API scope, so sign-ins show up per client in Entra's logs and can be revoked on their own. The quicker
  option is to add the redirect URIs above to the desktop's registration. Either way the token's audience
  must stay the one `APP_SECURITY_MCP_AUDIENCE` names, or the server must accept both audiences.
- **Make the advertised scope match.** `scopes_supported` in the protected-resource metadata is
  `APP_SECURITY_MCP_SCOPE`; the plugin's `oauth.scopes` and the registration's consent must use the same
  value, plus `offline_access` so Claude Code can refresh without a new browser sign-in. 🔍 The desktop's
  scope ends in `/desktop`; check what production sets.
- **Pick a fixed callback port** (one unlikely to be in use) and document it. If the port is busy on a
  machine, sign-in fails there.
- 🔍 MCP clients send the RFC 8707 `resource` parameter; it works for today's connector, but confirm it for
  the new registration in P0-04.

### 11.3 New MCP tools

Each wraps logic the server already has. Inputs and outputs are JSON; errors start with `ERROR:` as the
existing tools' do (P1-03 relies on it).

| Tool | Wraps | Returns | Task |
| --- | --- | --- | --- |
| `get_system_prompt(name = "default-chat")` | `SystemPromptService` (as `GET /prompts/system/{name}`) | the base instructions. Optionally also served as MCP `instructions` for Chat/Cowork. | P1-01 |
| `list_areas()` | new | each area, its modes (*single agent*, its profiles), its default playbook (OIM: `oim-full`) | P1-12 |
| `list_playbooks(area?)` | `PlaybookService.listSpecializedPlaybooks` (as `GET /playbooks`) | name, title, description, `tools`, `codeExecution`, `targetAgent`, `prototype`, area | P1-06, P1-12 |
| `get_playbook(name)` | `PromptsService` | the playbook's full text and the same metadata | P1-06 |
| `list_profiles(area?)` / `get_profile(name)` | as `GET /orchestrator/profiles` | lead, reviewer and specialist playbooks, `prototype`, area | P6-01 |
| `submit_feedback(…)` | the feedback store behind `PUT /messages/{id}/feedback` | an id. Self-contained payload (D-08): rating, comment, question, answer, area, mode, playbook or profile, cited ids, model, client `claude-plugin`, plugin version. Needs storage that does not require a server message id. | P4-01 |
| sync tools: create conversation, append turn, record run | `DesktopSyncService`, `DesktopOrchestratorRunService` | ids; an idempotency key per turn, which the REST API lacks today | P5-01 (only if D-07) |

**Areas are new.** yvoke-web's *knowledge area* is a content collection (*OIM Docs*, *OIM Database*) that a
playbook decides; the plugin's *area* (D-11) groups playbooks and profiles, so a playbook and a profile each
need an area attribute, and an area a default playbook. 🔍 `DesktopSyncController` describes profiles as
"knowledge bases"; check whether that is already the grouping meant. Agree the name before building it.

### 11.4 Which model sees which tool (P1-13)

yvoke-web's spec says AI clients and the in-app assistant share one tool set, so a new tool is offered to
the web's own assistant and to every connected client, including the model in Claude Code and Chat.
`get_playbook`, `submit_feedback` and the sync tools are meant for the mod, not for any model.

- Recommended: keep them on the same `/mcp` server (one sign-in) and leave them out of the in-app
  assistant's tool set. In Claude Code the mod's deny-by-default already stops the model from calling them,
  and the mod reaches them through `$.mcp.call`, which is not a model tool call. In Chat and Cowork the model
  sees them; tool descriptions should say they are for the client, not for answering.
- Alternative: a second MCP endpoint (for example `/mcp/client`) with only these tools, listed as a second
  server in the plugin's `.mcp.json`. Cleaner separation, but a second connection to sign in.

### 11.5 Behaviour to fix on the server

- **Rate limiting (P7-11).** Searches from AI clients are not rate-limited (yvoke-web spec ch. 7). Every
  plugin user arrives through that route.
- **Deleted playbooks stay listed until a restart** (spec ch. 7). With a live list (D-11) that becomes
  visible to every plugin user. Refresh the prompt and tool listing when a playbook changes.
- **The client's version.** The MCP `clientInfo` a server sees is Claude Code's, not the plugin's. Feedback
  and sync payloads therefore carry the plugin version themselves.
- **`ask_clarifying_question` reports success without asking anyone** (spec ch. 7). The plugin denies it in
  Claude Code (P1-09); leave it as is unless Chat needs a change.
- **Conversations from the plugin** (only if D-07): mark them as coming from the Claude plugin, as desktop
  conversations are marked *Desktop*, so the web sidebar and the admin register can tell them apart.

