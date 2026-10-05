# Yvoke for Claude — implementation plan

> **Status:** draft v3 · 2026-10-05 (revised after two external reviews)
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

- A consultant installs one plugin (or IT installs it for them), opens the Code tab in Claude Desktop, picks a
  playbook, asks a question, and gets an answer whose citations open the cited passage.
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
│   ├── skills/<playbook>/SKILL.md  ← generated from the server's playbooks (P1-04)
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
 ├─ skills/          playbooks; the mod fills each one's body from the server when invoked
 ├─ connector        Yvoke MCP server → search_corpus, get_section, verify_citations, …
 └─ mod (register.tsx), in-process hooks:
     ├─ tool.call        deny by default; per-playbook scoping; web allow-list; compute tools
     ├─ prompt.submit    playbook-required gate; playbook preflight
     ├─ skill.prompt     live playbook text from the server
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
- **The preflight check fails open; everything else that enforces policy fails closed.**

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
- [ ] **D-02** `PO` · `IT` — **How do users get access to the marketplace repository?** Options: users' own
  GitHub access to a private repo; a public repo containing no playbook text; or yvoke-web serving
  `marketplace.json` and a plugin `archive` behind an auth header.
  - Recommendation: if D-04 chooses live playbooks, the repo holds no playbook text and can be public.
  - Blocks: P7-04.
- [ ] **D-03** `server` · `plugin` — **How does Claude Code reach the Yvoke MCP server, and how does it sign
  in?** A claude.ai custom connector (already works in Claude sessions today), or a `.mcp.json` entry in the
  plugin using MCP OAuth against Entra ID.
  - The mod itself calls the server for live playbooks (P1-07), the citation pane (P3-03), feedback (P4) and
    sync (P5). `$.mcp.connect` only connects servers the plugin's own manifest lists, so unless P0-04 shows
    `$.mcp.call` reaching a claude.ai connector, the answer is `.mcp.json`.
  - If the server is also offered as a claude.ai or organization connector, use the same URL in `.mcp.json`
    so users who have both see one set of tools.
  - Depends on: P0-04 findings.
  - Blocks: P1-01, P1-02.
- [ ] **D-04** `PO` — **Playbooks: live or copied?** Live: skill stubs (name and description only) are
  generated, and the mod fetches each playbook's text when it is invoked (needs server task P1-06). Copied:
  full `SKILL.md` text generated into the repo by a scheduled job.
  - Recommendation: **live**, to keep yvoke-desktop's "no stale instructions" rule. Copied text also
    works in Chat and Cowork, where no mod runs, so Chat/Cowork may need copied text either way (see P8-01).
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
- [ ] **D-10** `PO` — **Is Chat/Cowork support in scope for v1**, or Claude Code only?
  - Blocks: Phase 8.

---

## 5. Feature parity map

Every Yvoke Desktop capability, where it lands, and which tasks deliver it. Chapters refer to
`yvoke-desktop/spec/`.

| Yvoke Desktop capability | Spec | Claude Code (mod) | Cowork | Chat | Tasks |
| --- | --- | --- | --- | --- | --- |
| Knowledge-base tools (search, sections, graph, records) | 2 | ✅ connector | ✅ | ✅ | P1-01, P1-02 |
| Base instructions from the server | 2 | ✅ | ✅ | 🔍 | P1-01 |
| Playbooks (pick, `/` autocomplete, sticky per conversation) | 1, 2 | ✅ | ✅ (skills) | ✅ (skills) | P1-04 – P1-08 |
| Playbook required for a single-agent question | 1 | ✅ | — | — | P1-08 |
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
  - From the mod: `$.mcp.connect` + `$.mcp.call(server, "get_section", …)` and `search_corpus`.
    `$.mcp.connect` only connects servers listed in the plugin's own manifest: can `$.mcp.call` reach a
    claude.ai connector at all? If not, D-03 must choose `.mcp.json`.
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
  - **Which agent made a tool call?** `tool.call` also fires for subagents' calls, but the docs show
    `agentId` only on `turn.step` and `turn.complete`. Without it on `tool.call`, deny-by-default (P2-01),
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

### Phase 1 — Knowledge base, base instructions and playbooks (MVP)

**Milestone M1:** in the Code tab, a user picks a playbook, asks a question, and gets a grounded answer.

- [ ] **P1-01** `server` · S — **Serve the base instructions as the MCP server's `instructions`.** Return the
  `default-chat` prompt in the MCP `initialize` result, so every Claude client that honours server
  instructions applies them, with no copy in this repo. ⛔ D-03
  - Fallback if P0-04 finds a surface that ignores them: in Claude Code the mod fetches the text from the
    server and adds it with a `prompt.section` hook; in Chat/Cowork a skill tells Claude to fetch it.
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
- [ ] **P1-04** `plugin` · M — **Skill generator** (`scripts/generate-skills.ts`). Reads the server's
  playbook catalogue (`prompts/list` with `_meta`) and writes one `skills/<name>/SKILL.md` per playbook. ⛔ D-04
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
  metadata. Needed because a mod can call MCP tools but not read MCP prompts. ⛔ D-04 (only if live)
- [ ] **P1-07** `plugin` · M — **Live playbook text.** A `skill.prompt` hook fetches the playbook's text via
  P1-06 when the skill is invoked. A fetch failure fails the question with a clear `Yvoke Backend:` message.
  There is deliberately no cached fallback. ⛔ D-04, P1-06
  - Done when: tests cover success, server down and unknown playbook.
- [ ] **P1-08** `plugin` · M — **Active playbook state.**
  - Invoking a playbook skill makes it the session's active playbook, kept across messages (`$.state`) until
    the user switches or clears it.
  - The status line shows it; `/yvoke-playbook` lists, switches and clears. Register commands last in the
    `session.start` hook, or inside `try`/`catch`: a name clash throws and skips the rest of the hook.
  - **Playbook required:** a single-agent question without an active playbook is refused with a message, and
    the draft is kept; not gated when the server offers no playbooks or a profile is active (spec chapter 1).
    Only inside the Yvoke folder. ⛔ P1-10
  - **Across `/clear`, `/resume` and `/branch`:** `/clear` starts a new conversation with no active
    playbook; `/resume` restores the resumed conversation's playbook (saved per session id); `/branch` keeps
    it. 🔍 P0-06
  - Done when: tests cover set, keep across turns, switch, clear, the required gate, `/clear` and `/resume`.
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
  - Path matching is on segment boundaries: `~/Yvoke-old` is not inside `~/Yvoke`.
  - 🔍 Confirm in P0-06 which of `cwd` and `root` is stable for the session.
  - Done when: tests cover the folder itself, a subfolder, an unrelated folder and a same-prefix sibling.

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
- [ ] **P2-02** `plugin` · M — **Per-playbook scoping, sticky.** A playbook that declares tools gets exactly
  those (plus the always-added three); one that declares none gets the default set (`DEFAULT_KB_TOOLS`).
  Scoping follows the active playbook from P1-08, so it holds across messages (unlike skill
  `disallowed-tools`, which clears on the next message). ⛔ P1-08
  - Done when: tests cover declared tools, no declaration, and a switch mid-conversation.
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
  - The domain list comes from deployment configuration (`userConfig` set through managed `pluginConfigs`),
    never from the user. 🔍 Confirm managed `pluginConfigs` override user values.
  - Done when: yvoke-desktop's web cases from `policy.test.ts` pass here.
- [ ] **P2-05** `plugin` · M — **Safe compute tools.** Register `calculate`, `statistics` and `date_diff` with
  `$.tool.register`, ported from yvoke-desktop's `computeTools.ts` with its `computeTools.test.ts`. Withheld when
  the active playbook declares `codeExecution: false`. ⛔ D-05
  - Fix while porting: make the `log` (base 10) vs `ln` difference explicit in the tool description.
- [ ] **P2-06** `plugin` · M — **Playbook preflight check.** On submit, when there are at least two
  playbooks and a playbook is active, ask the model (`$.model.complete`, no tools, 45 s timeout) whether the
  playbook fits; if not, show a band with **Switch to …** / **Send anyway**.
  - Fails open on every error, including a reply with `isAnswered: false`: `$.model.complete` reports a
    Claude API failure that way rather than by rejecting. Runs on the conversation's first message and after
    each playbook switch, not again for the same playbook. Port `playbookValidation.test.ts`.
  - Shows that the check is running (`$.ui.status` or the band above the prompt) while it waits, so a slow
    model does not look like a frozen app. Time spent inside `$.model.complete` does not count against a
    hook's 10-second limit.
  - Done when: tests cover fit, better match, timeout, unparseable reply and unknown suggestion.
- [ ] **P2-07** `IT` · `plugin` · S — **Managed-settings lockdown template** (`deploy/managed-settings.lockdown.json`):
  `permissions.deny` for `Bash`, `Write`, `Edit`, `NotebookEdit`, and the plugin enabled at managed scope so
  users cannot disable it. The deny applies to the whole machine (see D-09). ⛔ D-09
  - Done when: on a test machine, the denied tools stay denied with the plugin disabled.

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
  `list_profiles` / `get_profile`, or a generated `agents/` directory, per D-06). `/yvoke-profile` lists,
  switches and clears; prototypes are hidden unless enabled. The active profile follows the same `/clear`,
  `/resume` and `/branch` rules as the active playbook (P1-08). ⛔ D-06
- [ ] **P6-02** `plugin` · M — **Specialist agent types**, one per profile specialist, from the base
  instructions plus the specialist's playbook. Tools follow yvoke-desktop's `mapSpecialistTools` (declared or
  default knowledge-base tools, compute unless `codeExecution: false`, web only if declared). Model and effort
  per role from deployment configuration. Port `orchestration.test.ts`.
- [ ] **P6-03** `plugin` · S — **Reviewer agent.** Only `verify_citations`; no base instructions; strict
  verdict parsing: the first line must be exactly `APPROVED` or `REJECTED`; anything else is "no clear
  verdict". This fixes yvoke-desktop's defect where "NOT APPROVED" reads as approved. Done when: a regression
  test for "NOT APPROVED" fails before the fix. Port `review.test.ts`.
- [ ] **P6-04** `plugin` · M — **Lead mode.** While a profile is active, the lead may only delegate and ask
  the user; knowledge-base tools are denied to it, but not to the specialists (how the hook tells them
  apart comes from P0-07). The desktop runtime adapter text is appended to its
  instructions, with `REVIEW_FEEDBACK_HEADING` and `EVIDENCE_HEADING` byte-identical to the server's.
  Switching between single-agent and a profile, or between profiles, requires a new conversation (yvoke-desktop
  pitfall: sessions cannot resume across modes).
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

### Phase 8 — Chat and Cowork (lower fidelity; only if D-10 says yes)

- [ ] **P8-01** `plugin` · S — **Playbook skills usable in Chat and Cowork.** Without a mod, a stub cannot
  fetch its text, so Chat/Cowork need either copied playbook text or a stub that tells Claude to call
  `get_playbook` first. ⛔ D-04, D-10
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

## 7. Not planned and known gaps

Write things down here when they are decided against, so nobody "fixes" them by accident.

- **Hiding playbook text from the user.** Skills are readable in Claude's skill viewer; playbooks are also
  readable by any connected client of the server. Not a secret today either.
- **Isolation from the user's own Claude setup.** In Claude, the user's other plugins, skills, memory and
  connectors can affect answers; Yvoke Desktop excluded them. Managed settings can narrow this, not remove it.
  The other direction is handled: outside the Yvoke folder the mod changes nothing (P1-10), but the
  playbook skills and the Yvoke connector are still offered in every session.
- **Company cost reporting.** Model usage stays on each user's Claude subscription, as with Yvoke Desktop.
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
| The mod cannot reach a claude.ai connector (`$.mcp.connect` covers only the plugin's own servers) | Live playbooks, citation pane, feedback and sync have no server | Spike P0-04; D-03 then picks `.mcp.json` |
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
