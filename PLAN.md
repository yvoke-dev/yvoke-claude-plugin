# Yvoke for Claude — implementation plan

> **Status:** draft v4 · 2026-10-05 (split into intent, requirements, design and this plan; all decisions
> D-01 to D-11 taken)
> **What we are building:** one Claude plugin that brings Yvoke Desktop's capabilities into Claude, fully
> in **Claude Code** through a **mod**, and as live playbook stubs in **Chat** and **Cowork** (D-10).

| Document | Answers | Read it when |
| --- | --- | --- |
| [docs/intent.md](docs/intent.md) | Why, for whom, use cases, scope, what is not planned | You want to know what the plugin is for |
| [docs/requirements.md](docs/requirements.md) | What the plugin must do, per surface | You decide whether something is a bug or a change |
| [docs/design.md](docs/design.md) | Architecture, constraints, decisions D-01 to D-11, notes for implementers, yvoke-web changes | Before you pick up a task |
| **PLAN.md** (this file) | The tasks, their order and their progress | You pick up, finish or re-plan a task |

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
3. **Decisions are tasks too** ([design section 3](docs/design.md#3-decisions)). A decision is ticked when its outcome is written under it.
4. Each task names its **owner area**: `plugin` (this repo), `server` (yvoke-web / its MCP server),
   `IT` (deployment, managed settings), `PO` (product-owner decision).
5. Size is a rough guess: **S** ≤ 1 day, **M** ≤ 3 days, **L** ≤ 2 weeks.

Count progress from the repository root:

```bash
echo "done $(grep -cE '^\s*- \[x\]' PLAN.md) / total $(grep -cE '^\s*- \[[ x]\]' PLAN.md)"
```

---

## 1. Work plan

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

- [ ] **P0-09** `server` · `IT` · S — **Entra client registration for Claude clients** ([design 5.2](docs/design.md#52-sign-in-for-claude-clients-p0-09-decides-d-03)).
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
  ([design 5.4](docs/design.md#54-which-model-sees-which-tool-p1-13)). `get_playbook`, `submit_feedback` and the sync tools are for the mod, not for the web's
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
    P0-06 that managed `pluginConfigs` override a user's own values; if they do not, record it in [intent 6](docs/intent.md#6-not-planned-and-known-gaps).
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
- ~~**P2-07** `IT` · `plugin` · S — **Managed-settings lockdown template** (`deploy/managed-settings.lockdown.json`):
  `permissions.deny` for `Bash`, `Write`, `Edit`, `NotebookEdit`, and the plugin enabled at managed scope so
  users cannot disable it. The deny applies to the whole machine (see D-09). ⛔ D-09~~
  Dropped: no IT lockdown (D-09).

- [ ] **P2-08** `plugin` · M — **Turn ceilings per question** (decided 2026-10-05). The mod counts the model
  requests of each question per loop (`turn.step`, keyed by `turnId` and `agentId`) and enforces
  configurable ceilings from `userConfig`: single agent 25, lead 60, each specialist and the reviewer 20
  (yvoke-desktop's shipped values). At the ceiling it **delivers rather than discards**: further tool calls
  in that loop are denied with a message telling the model to answer now with what it has, and the answer
  is flagged (*stopped at the turn limit*) by a line under it. A specialist at its ceiling returns its
  partial answer to the lead. Fails closed ([requirements 1](docs/requirements.md#1-principles-carried-over-from-yvoke-desktop)). Port the ceiling cases from yvoke-desktop's tests where
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

### Phase 4 — Feedback (after v1; built after Phase 5, D-08)

**Milestone M3:** 👍/👎 on an answer arrives in yvoke-web's feedback screens.

Rating needs the server's message id, so it starts once turns sync (P5-01, P5-02).

- [ ] **P4-01** `server` · M — **Feedback endpoint for Claude clients**, as an MCP tool (`submit_feedback`), so
  the connector's sign-in authenticates it. Keyed to the synced message id (D-08), as yvoke-desktop's
  `PUT /messages/{id}/feedback`; marked as coming from the Claude plugin with its version. ⛔ P5-01
- [ ] **P4-02** `plugin` · M — **Turn capture.** For each completed turn, keep the question
  (`prompt.submit`), the final answer text, the active playbook, the model, the cited ids and the tool
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
    recorded in [intent 6](docs/intent.md#6-not-planned-and-known-gaps).
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
- [ ] **P7-11** `server` · S — **Per-user rate limit on `/mcp`** ([design 5.5](docs/design.md#55-behaviour-to-fix-on-the-server)). Searches from AI clients are
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

## 2. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Mod API changes between Claude Code releases | Mod stops loading or misbehaves after an update | Weekly CI against the latest release (P0-02); pinned minimum version (P7-07) |
| IT sets `allowManagedModsOnly` | Mod does not load from Git or claude.ai sync | MDM-copied directory marketplace (P7-04) |
| Desktop surface lacks a UI feature the terminal has (e.g. link clicks) | Citation UX differs | Decided by spike P0-05; button-row fallback |
| Connector sign-in does not work in Claude Code | No MVP | Spike P0-04 first; server-side auth work early |
| Users cannot reach a private repo | Install and updates fail | D-02: public repo without playbook text, or archive served by yvoke-web |
| Users disable the plugin | Policy not enforced | Accepted: Yvoke is opt-in (D-09) |
| The mod blocks users' other Claude Code work (it runs in every session) | Coding sessions lose shell and file tools; every prompt needs a playbook | Enforce only in the Yvoke folder (P1-10); no IT lockdown (D-09) |
| A `tool.call` hook cannot tell which agent made the call | Lead-only rules (P6-04) cannot be enforced | Spike P0-07 before Phase 6 is designed; the budget (P6-06) uses `agent.spawn` instead |
| The mod cannot reach a claude.ai connector in practice (documented as working through `$.mcp.call`) | Live playbooks, citation pane, feedback and sync have no server | Spike P0-04; fall back to `.mcp.json` |
| A policy hook throws or times out, or mods are off (`disableAllHooks`, `--safe-mode`, hooks worker crashed) | The tool runs or the prompt goes through: policy fails open | `.catch` on every enforcing hook ([design 4.4](docs/design.md#44-rules-that-are-easy-to-get-wrong)); Claude Code's permission prompts still apply |
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
- Review of draft v3, with the findings behind design section 4: [docs/reviews/2026-10-05-plan-v3-review.md](docs/reviews/2026-10-05-plan-v3-review.md)
- yvoke-desktop coverage check (gaps C1–C13; D-11 decided): [docs/reviews/2026-10-05-yvoke-desktop-coverage.md](docs/reviews/2026-10-05-yvoke-desktop-coverage.md)
