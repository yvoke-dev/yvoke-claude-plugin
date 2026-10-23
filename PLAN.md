# Yvoke for Claude: Feature Roadmap & Implementation Plan

> **Scope:** v1 targets **Claude Code** (both the Claude Desktop **Code** tab and the terminal). Living specifications for the implemented baseline live in [`.lintel/specs/index.md`](.lintel/specs/index.md), and the current architecture and coding conventions live in [`.lintel/steering/architecture.md`](.lintel/steering/architecture.md) and [`.lintel/steering/conventions.md`](.lintel/steering/conventions.md).

This document defines the remaining feature milestones, codebase integration requirements, pre-implementation spikes, server/IT dependencies, and post-v1 roadmap. Each section is self-contained and ready to be executed as a Lintel task (`lintel start`).

---

## Contents

1. [Current Baseline (Already Implemented)](#1-current-baseline-already-implemented)
2. [Recommended Implementation Order](#2-recommended-implementation-order)
3. [Milestone 1: Session Setup, Auth & Playbook Preflight](#3-milestone-1-session-setup-auth--playbook-preflight)
4. [Milestone 2: Tool Policy Extensions (Web Access & Sandboxed Compute)](#4-milestone-2-tool-policy-extensions-web-access--sandboxed-compute)
5. [Milestone 3: Citations & Source Passage Pane](#5-milestone-3-citations--source-passage-pane)
6. [Milestone 4: Multi-Agent Investigations](#6-milestone-4-multi-agent-investigations)
7. [Milestone 5: Diagnostics, Compatibility, Packaging & Rollout](#7-milestone-5-diagnostics-compatibility-packaging--rollout)
8. [Post-v1 Roadmap (Deferred After v1)](#8-post-v1-roadmap-deferred-after-v1)
9. [Server-Side (`yvoke-web`) & IT Dependencies and Key Risks](#9-server-side-yvoke-web--it-dependencies-and-key-risks)

---

## 1. Current Baseline (Already Implemented)

The single-agent MVP is implemented and verified by headless mod tests in `plugins/yvoke/tests/` and living specs in [`.lintel/specs/index.md`](.lintel/specs/index.md):

| Area | Implemented Files | Summary | Living Spec |
| --- | --- | --- | --- |
| **Packaging & Manifests** | `.claude-plugin/marketplace.json`, `plugins/yvoke/.claude-plugin/plugin.json`, `plugins/yvoke/.mcp.json`, `plugins/yvoke/hooks/register.tsx` | Self-hosted `yvoke` marketplace, `userConfig` settings (`serverUrl`, `devMode`, `showPrototypes`, `turnLimit*`), HTTP MCP server entry with `X-Yvoke-Dev-Mode` header | [`packaging.md`](.lintel/specs/packaging.md) |
| **Server Client** | `plugins/yvoke/src/server.ts` | `callYvoke(io, tool, args, options)` with 8 s default timeout, JSON string unwrapping, `/^\s*(ERROR\|Error):/` body error detection, and `Yvoke Backend:` prefixing | [`server-client.md`](.lintel/specs/server-client.md) |
| **Session Activation** | `plugins/yvoke/src/session.ts` | Opt-in `/yvoke` slash command before first question, `/clear` reset, and `/resume` / `/branch` (`fork`) persistence via `$.state` and `$.store` | [`session.md`](.lintel/specs/session.md) |
| **Session Setup Band** | `plugins/yvoke/src/setup.ts` | `AbovePrompt` setup selector (Area -> Mode -> versioned Playbook `name vN`), locks on first non-dropped question, upgrades playbook version with a toast on `/resume` or `/branch` | [`setup.md`](.lintel/specs/setup.md) |
| **System Prompt Builder** | `plugins/yvoke/src/system-prompt.ts` | Resolves base instructions (`get_system_prompt` by playbook prompt, area default, or active default) and active playbook (`get_playbook`), joined by `\n\n---\n\n` in `yvoke:instructions` | [`system-prompt.md`](.lintel/specs/system-prompt.md) |
| **Tool Qualification & Gate** | `plugins/yvoke/src/tool-names.ts`, `plugins/yvoke/src/policy.ts` | Dynamic MCP tool prefix normalization (`serverToolPrefix`, `qualifyTool`), deny-by-default `tool.call` gate, per-playbook tool scoping (`allowedServerTools` or `DEFAULT_KB_TOOLS`). The gate catches `ask_clarifying_question` before it reaches the server and asks the user with `$.ui.ask` (`User answered: …` / `User skipped the question.`); a playbook that leaves it out is pointed to `AskUserQuestion` | [`tool-policy.md`](.lintel/specs/tool-policy.md) |
| **Turn Ceilings** | `plugins/yvoke/src/turn-limits.ts`, `plugins/yvoke/src/policy.ts` | Per-question model request caps (25 single, 60 lead, 20 subagent/reviewer), wrap-up denial at request `n - 1`, tool-use stripping at request `n`, and footer notice line | [`turn-limits.md`](.lintel/specs/turn-limits.md) |

On the server side (`yvoke-web`), the MCP tools `get_system_prompt`, `list_areas`, `list_playbooks`, and `get_playbook` are already implemented and restricted to MCP clients only (excluded from `yvoke-web`'s own web assistant and playbook editor).

---

## 2. Recommended Implementation Order

Each feature is designed to live in its own `plugins/yvoke/src/` module (with pure rule functions tested in `<feature>-rules` suites and hook wiring tested in `<feature>` suites) or add a clean step inside the single `tool.call` gate in `plugins/yvoke/src/policy.ts`.

1. **Immediate Self-Contained Plugin Features** (no external account or server blockers; testable immediately against `claude-code/testing` hook doubles):
   - **Domain-Restricted Web Access (`WebSearch` & `WebFetch`)** in `plugins/yvoke/src/policy.ts` and `plugin.json`.
   - **Playbook Preflight Check** in `plugins/yvoke/src/preflight.ts` (plus catalogue metadata fields in `setup.ts`).
   - **Citation Marker Parser** in `plugins/yvoke/src/citations.ts`.
   - **Minimum Version Check & `/yvoke-doctor` Diagnostics** in `plugins/yvoke/src/doctor.ts`.
2. **Interactive Spikes & Account Registration**:
   - **Entra ID Public Client Registration** (*Yvoke for Claude*) -> unblocks production `.mcp.json` connector defaults and token refresh verification.
   - **Desktop Surface Citation Rendering Spike** -> confirms `onLinkPress` vs. source button row on the Desktop Code tab and terminal -> unblocks **Citations in Assistant Replies** and **Citation Source Pane**.
   - **Claude Code `Bash` Sandbox Security Spike** -> verifies whether `execute_code` running `node -e` via `Bash` blocks all four escape vectors -> unblocks **Sandboxed Compute Tool (`execute_code`)**.
   - **Dynamic Subagent & Reviewer Spike** -> confirms `$.agent.register`, `$.agent.spawn`, lead text withholding, and reviewer hook placement -> unblocks **Milestone 4: Multi-Agent Investigations**.
3. **Milestone 3: Citations & Source Passage Pane** (`src/citations.ts` UI hooks + `Pane` + `get_section`).
4. **Milestone 4: Multi-Agent Investigations** (`list_profiles`/`get_profile` on `yvoke-web`, specialist/reviewer registration, lead mode, enforced review rounds, team UI).
5. **Milestone 5: Packaging, Managed Deployment & Consultant Pilot**.
6. **Post-v1 Roadmap** (conversation sync & traces, 👍/👎 answer ratings, Chat & Cowork surfaces).

---

## 3. Milestone 1: Session Setup, Auth & Playbook Preflight

### 3.1 Entra ID Public Client Registration & Production `.mcp.json` Connector

- **Entra Client Registration (`server` / `IT`):**
  - Register a public client application (*Yvoke for Claude*, or extend the desktop registration) with redirect URI `http://localhost:<port>/callback` (and claude.ai's callback URI if needed), consented for `yvoke-web`'s `APP_SECURITY_MCP_SCOPE` and `offline_access`.
- **Production `.mcp.json` Defaults (`plugins/yvoke/.mcp.json`):**
  - Ship the production `yvoke-web` MCP URL as the default of `${user_config.serverUrl}` along with `oauth.clientId` and `oauth.callbackPort`.
  - Do **not** add an `Authorization` header to `.mcp.json`, because Claude Code disables OAuth PKCE sign-in whenever an `Authorization` header is present.
- **Connection & Token Refresh Verification:**
  - Verify `$.mcp.connect('yvoke')` and `$.mcp.call` in both the Desktop Code tab and terminal after Entra ID sign-in, and confirm how token expiry/refresh errors surface through `callYvoke` (`src/server.ts`).

### 3.2 Playbook Preflight Check (`plugins/yvoke/src/preflight.ts`)

- **Behaviour (`yvoke-desktop` & `yvoke-web` 1.3.0 Parity):**
  - Runs at most once per session on the first question of a single-agent Yvoke session (`yvoke.preflightDone` in `PluginState.yvoke`), before the setup locks.
  - **When to skip:** Skip the check immediately when:
    - the session is in multi-agent mode;
    - the selected area offers fewer than 2 playbooks; or
    - the selected playbook is the area's default playbook (`area.defaultPlaybook`).
  - **Model Fit Check:** When a non-default playbook is selected in an area with $\ge 2$ playbooks, show status via `$.ui.status('Checking whether the playbook fits your question…')` and call `$.model.complete` (no tools, 45 s timeout via `$.clock.sleep`) with a prompt listing the user's question and the area's candidate non-default playbooks (`name`, `title`, `description`).
  - **Handling the Suggestion:**
    - If `$.model.complete` suggests the already-chosen playbook or the area's default playbook, treat it as a fit and proceed without prompting the user.
    - If `$.model.complete` suggests a different known non-default playbook in the area, prompt the user via `$.ui.ask` with `header: 'Playbook'` and two options:
      1. `Switch to <suggestedTitle> (<suggestedName>)` — updates `yvoke.setup` (`playbook` and `version`) in `$.state` and `$.store`.
      2. `Keep <currentTitle> (<currentName>)` — keeps the current playbook.
  - **Fails Open:** Any error, timeout, unparseable model reply, unknown suggested playbook name, or `isAnswered: false` from `$.ui.ask` proceeds with the user's currently selected playbook.
- **Required Codebase Integration Details:**
  1. **Hook Registration Order in `plugins/yvoke/hooks/register.tsx`:** Register `registerPreflight(on)` **before** `registerSetup(on, ...)` and `registerSystemPrompt(on)`, using matcher `{ text: /^/ }` (since `registerSystemPrompt` owns the unmatched `prompt.submit` hook). Because `registerSetup`'s `prompt.submit` hook reads `yvoke.setup` before calling `next(e)` and writes that copy back to lock the session, running `registerPreflight` outermost ensures that any playbook switch is written to `yvoke.setup` before `registerSetup` reads and locks it and before `registerSystemPrompt` loads instructions.
  2. **Catalogue Metadata in `plugins/yvoke/src/setup.ts`:** Extend `PlaybookEntry` and `toCatalogue` in `plugins/yvoke/src/setup.ts` (and `plugins/yvoke/types/index.d.ts`) to retain `title` (defaulting to `name`) and `description` (defaulting to `''`) from `list_playbooks` so `preflight.ts` can include candidate playbook descriptions in the classifier prompt.
  3. **Instruction Cache Guard in `plugins/yvoke/src/system-prompt.ts`:** Include `loadedFor: { area, mode, playbook }` in `yvoke.instructions` (`plugins/yvoke/types/index.d.ts`) so cached instructions are verified against the active setup as well as `sessionId`.

### 3.3 Area-Scoped Collection Search (`yvoke-web`)

- When a playbook belongs to an area, `yvoke-web`'s knowledge-base search tools (`search_corpus`, `list_documents`, etc.) restrict retrieval to that area's collections in both web chat and MCP calls, while playbooks without an area remain unscoped.

---

## 4. Milestone 2: Tool Policy Extensions (Web Access & Sandboxed Compute)

### 4.1 Domain-Restricted Web Access (`WebSearch` & `WebFetch`)

- **Settings (`plugins/yvoke/.claude-plugin/plugin.json` `userConfig`):**
  - Add `webEnabled` (boolean, default `false`) and `allowedWebDomains` (string, comma- or newline-separated host/path patterns, default `""`).
- **Policy Rules (`plugins/yvoke/src/policy.ts`, ported from `yvoke-desktop`'s `src/main/agent/policy.ts`):**
  - Uses `builtinTool` and `isWebTool` already implemented in `plugins/yvoke/src/tool-names.ts`.
  - Grant `WebSearch` and `WebFetch` **only** when `webEnabled` is `true` in configuration **and** the active playbook declares the tool in its `tools` list (requires retaining a boolean or list of declared web tools on `PlaybookEntry` in `src/setup.ts`, since `allowedServerTools` strips web tools from the MCP server tool list).
  - **`WebSearch`:** Parse the configured `allowedWebDomains` into hostnames (refusing the call if the allow-list is empty or unparseable) and rewrite the tool call's `allowed_domains` argument via `next({ ...e, allowed_domains: hosts })`.
  - **`WebFetch`:** Validate `e.url` against the configured allow-list:
    1. Refuse hosts in `WAF_CHALLENGED_HOSTS` first with an instruction to use `WebSearch` instead.
    2. Refuse everything when the allow-list is empty or unparseable.
    3. Match the URL's host (exact host or subdomain) and optional path prefix on segment boundaries.

### 4.2 Sandboxed Compute Tool (`execute_code`)

- **Pre-Implementation Security Spike:**
  - Because the Claude Code mod worker cannot run `node:vm`, `new Function`, `eval`, or `WebAssembly`, `execute_code` must wrap `$.tool.call({ tool: 'Bash', command: 'node -e ...' })` under Claude Code's OS sandbox.
  - Before shipping `execute_code`, verify across macOS and Windows (Desktop Code tab and terminal, minimum `2.1.287` and `latest`) whether Claude Code's sandbox blocks all four escape vectors when `dangerouslyDisableSandbox` is unset:
    1. Reading secrets from `process.env`.
    2. Reading or writing files outside a scratch folder.
    3. Spawning child processes via `child_process`.
    4. Making network requests (`fetch`, `net`).
  - **Pass/Fail Gate:** If any of the four escape vectors succeeds on any supported platform where the sandbox reports active, `execute_code` is dropped from v1.
- **Implementation (If Spike Passes):**
  - Register `execute_code` once per process when `/yvoke` activates or restores a Yvoke session (`JavaScript` code up to 10,000 characters, 1 s default and 2 s maximum timeout, 32,000-character output cap).
  - Preserve `codeExecution?: boolean` on `PlaybookEntry` in `plugins/yvoke/src/setup.ts`.
  - On every `execute_code` invocation, check in order and refuse (running nothing) when:
    1. `yvokeSession` is not `true`;
    2. Claude Code's sandbox is off or unsupported (such as on native Windows); or
    3. the active playbook sets `codeExecution: false` or explicitly declares a `tools` list that omits `execute_code` / compute tools.
  - In `plugins/yvoke/src/policy.ts`, permit `e.tool === 'Bash'` **only** when `next.origin?.plugin === 'yvoke'`; all model-originated `Bash` calls remain denied.

---

## 5. Milestone 3: Citations & Source Passage Pane

### 5.1 Citation Marker Parser (`plugins/yvoke/src/citations.ts`)

- Port from `yvoke-desktop`'s `src/renderer/src/components/citationRehype.ts` and its unit test suite:
  - **Bare UUID markers:** `[<uuid>]` (standard 36-character hex UUID `8-4-4-4-12`, case-insensitive) -> parsed as a citation with display label set to the **first 8 characters** of the UUID.
  - **Legacy keyed markers:** `[chunk_id=<id>]`, `[document_id=<id>]`, and `[file=<id>]` -> parsed as a citation with the full label.
  - **Ignored patterns:** Plain numbers (`[1]`, `[2]`), truncated IDs (`[a1b2c3d4]`), markers inside fenced code blocks (` ``` `) or inline code spans (`` ` ``), and standard Markdown links `[text](url)` or images `![alt](url)` are left untouched.

### 5.2 Citations in Assistant Replies (`AssistantMessage`)

- **Desktop & Terminal Surface Spike:** Confirm whether clicking a Markdown link inside an `AssistantMessage` triggers `ui.press` (`onLinkPress`) in both the Desktop Code tab and the terminal, or whether a row of source `Button`s beneath the reply is required on either surface.
- **Rendering Hook (`ui.render` on `AssistantMessage`):**
  - Active only in a Yvoke session.
  - Renders citations as interactive links or source buttons on both `terminal` and `desktop`.
  - Splits rendered text/Markdown into chunks of at most **10,000 characters** per `Markdown` or `Text` element so long replies never fail Claude Code's UI validation.

### 5.3 Citation Source Pane (`Pane` & `get_section`)

- **Chunk-First Lookup:** When a user activates a citation, call `callYvoke(io, 'get_section', ...)` with the cited ID as a **chunk ID** first; if not found as a chunk, fall back to calling `get_section` with the ID as a **document ID**.
- **Passage vs. Surrounding Section Display:**
  - Open a `Pane` showing the document title, version, and **only the exact cited passage** up front.
  - Place the rest of the section behind a collapsed **Show surrounding section** control explicitly labelled as **not part of the cited source** (omitted when the citation resolved at the document level).
- **Passage HTML Cleaning (`yvoke-desktop` 1.3.0 Parity, `sectionView.ts`):**
  - Balance split HTML tables so partial table fragments render cleanly.
  - Strip MadCap Flare copy toolbars, line-number gutters, HTML comments (`<!-- ... -->`), and empty bookmark anchors (`<a name="..."></a>`, `<a id="..."></a>`).
  - Render any remaining raw markup safely as plain text.
- **Deep Link (`yvoke-web`):** Once `yvoke-web` exposes a browser route to open a passage by ID, include an *Open in Yvoke* link in the pane header.

---

## 6. Milestone 4: Multi-Agent Investigations

### 6.1 Pre-Implementation Subagent Spike

- Verify on Claude Code (`>= 2.1.287` and `latest`, terminal and Desktop Code tab):
  1. Dynamic registration via `$.agent.register` (restricted tool list, explicit model ID, effort) and spawning via `$.agent.spawn`.
  2. Identifying callers via `e.agentId` on `tool.call` / `turn.complete` (unset on the lead/main loop) and `e.parentAgentId` on `agent.spawn`.
  3. Holding back unsubmitted lead text on `AssistantMessage` so only the final answer submitted via `submit_answer` is displayed.
  4. Whether the reviewer subagent can be spawned and awaited inside the lead's `submit_answer` `tool.call` hook within the 10-second hook budget, or whether review execution must run on `turn.complete` followed by `$.prompt.submit`.

### 6.2 Live Profile Catalogue & Setup (`list_profiles`, `get_profile`)

- **Server (`yvoke-web`):** Expose `list_profiles(area?)` and `get_profile(name)` over `/mcp` (returning lead, reviewer, and specialist playbook assignments, `area`, `prototype`, `maxReviewRounds`, and `maxSpecialistCalls`, while excluding them from `yvoke-web`'s own web assistant).
- **Plugin (`plugins/yvoke/src/setup.ts`):**
  - Load `list_profiles` alongside `list_areas` and `list_playbooks`.
  - Flip `MULTI_AGENT_READY = true` in `plugins/yvoke/src/setup.ts` so areas with profiles offer *Multi-agent* mode and the **Profile** selector (`yvoke-profile`), hiding `prototype: true` profiles unless `showPrototypes` is enabled.

### 6.3 Specialist Agent Registration

- When a multi-agent profile locks on the first question, fetch the profile (`get_profile`) and each specialist's playbook (`get_playbook`), and register one subagent per specialist via `$.agent.register`:
  - **Instructions:** Compose each specialist's system prompt using the same base-instruction resolution rule as `src/system-prompt.ts` (the specialist playbook's `systemPrompt` if listed in any area's `systemPrompts`, else the area's `defaultSystemPrompt`, else the active default) joined with `\n\n---\n\n` and the specialist's playbook text.
  - **Tools:** Map each specialist's allowed tools using `yvoke-desktop`'s `mapSpecialistTools` (`src/main/agent/orchestration.ts`): declared or default knowledge-base tools, `execute_code` unless `codeExecution: false`, and web tools only if enabled and declared.
  - **Model & Effort:** Map role models to explicit Claude 5.5 IDs (`claude-sonnet-5-5`, `claude-haiku-5-5`, matching `yvoke-desktop` 1.3.0) and effort from `userConfig`.

### 6.4 Reviewer Agent (`submit_review` & Cited Evidence Digest)

- Register the profile's reviewer agent via `$.agent.register`:
  - **Instructions:** Runs on its reviewer playbook text **alone** (no base instructions).
  - **Fixed Tool Set:** Receives **only** `verify_citations` (qualified for the Yvoke MCP server) and a mod-registered `submit_review` tool (`approved: boolean`, `feedback: string`, `unsupported_claims: string[]`, `citation_fixes: string[]`). Any `tools` declared on the reviewer playbook are ignored.
  - **Cited Evidence Digest (`evidenceDigest.ts`, `chunkBlocks.ts`):** Port from `yvoke-desktop`. The reviewer receives the user's question, the lead's draft, and **only** the source blocks cited by the draft. Across revision rounds, the reviewer maintains its session and receives only newly cited sources.
  - **Verdict Rule:** The structured `submit_review` tool call is the verdict; finishing a review turn without calling `submit_review` counts as "no clear verdict" (treated as a rejection).

### 6.5 Lead Mode, Turn-Ceiling Exemptions & Enforced Review Loop

- **Lead Mode (`plugins/yvoke/src/policy.ts`):**
  - While a multi-agent profile is active, the lead (`e.agentId` unset) may call **only** delegation (`Agent` / `Task`), `AskUserQuestion`, `verify_citations`, and mod-registered `submit_answer`. Direct knowledge-base retrieval tools (`search_corpus`, `get_section`, etc.) are denied to the lead (while permitted to specialists according to their playbooks).
  - Append the desktop runtime adapter instructions to the lead's prompt with `REVIEW_FEEDBACK_HEADING` and `EVIDENCE_HEADING` byte-identical to `yvoke-desktop` / `yvoke-web`.
  - Hold back unsubmitted lead text in `AssistantMessage`; if the lead finishes without calling `submit_answer`, a turn that consulted at least one specialist treats the lead's final text as the draft for review, while a turn that consulted no specialist delivers the text after any answer marker without review.
- **Turn-Ceiling Exemptions (`plugins/yvoke/src/policy.ts`):**
  - Exempt `submit_review` and `submit_answer` from the request `n - 1` (`WRAP_UP`) turn-limit denial in `plugins/yvoke/src/policy.ts` so the reviewer and lead can still submit their verdict or final answer when instructed to wrap up on their penultimate turn.
- **Specialist Spawn Budget (`agent.spawn`):**
  - Count specialist spawns per question in `agent.spawn` and deny spawns beyond the profile's `maxSpecialistCalls` (excluding the reviewer agent).
- **Specialist Clarifying Questions:**
  - Ensure any clarifying question asked by a specialist is surfaced to the user rather than leaving the composer locked.
- **Enforced Review Rounds:**
  - Every lead answer on a turn that consulted at least one specialist is reviewed by the reviewer agent:
    - **Approved:** Deliver the submitted answer.
    - **Rejected or No Verdict (within `maxReviewRounds`):** Send a structured revision prompt to the lead separating `citation_fixes` from `unsupported_claims` along with cited evidence. Place the reviewer feedback, `citation_fixes`, `unsupported_claims`, and revision directive **first**, and cap the total prompt at `MAX_REVISION_PROMPT_CHARS = 32_000` (narrowing attached evidence to cited blocks before truncating).
    - **Out of Rounds (`maxReviewRounds` reached):** Deliver the final draft with a prominent unapproved warning banner.
    - **Reviewer Failure:** If the reviewer call itself fails, deliver a non-blank draft with an *unreviewed* warning.
    - **Aborted or Failed Turn:** Never re-prompt a turn where `e.isAborted` is true or the turn failed.

### 6.6 Team UI

- Render a card per specialist delegation showing the specialist name, sub-question, status, and answer (truncating specialist answers over 10,000 characters on the card with a button to open the full answer in a `Pane`).
- Render the reviewer's Approved / Rejected badge with review notes, and display a warning banner on any final answer delivered without reviewer approval (*delivered without review*, *no clear verdict*, or *rejected*).
- Collapse rejected lead drafts and mod-originated revision prompts (`AssistantMessage` and plugin-originated `UserMessage`) into a single expandable *Draft rejected by reviewer* row.

---

## 7. Milestone 5: Diagnostics, Compatibility, Packaging & Rollout

- **Minimum Version Check (`session.start`):**
  - Read `$.session.version` at session start and show a clear notice when Claude Code is older than `2.1.287`.
- **`/yvoke-doctor` Diagnostics Command (`plugins/yvoke/src/doctor.ts`):**
  - Register `/yvoke-doctor` to report:
    1. Claude Code version vs. minimum supported (`2.1.287`).
    2. Whether the current session is an active Yvoke session (`/yvoke`).
    3. Yvoke MCP server connection and sign-in status, server version, and available area/playbook count (via `callYvoke`).
    4. Active setup (`area`, `mode`, `playbook` + `version`, or `profile`).
    5. Whether normal Claude subscription billing applies or `ANTHROPIC_API_KEY` is set in the environment (warn if an API key is overriding subscription billing, without ever printing any key, token, or secret).
- **Versioning, Release Automation & Managed Deployment:**
  - Maintain `version` solely in `plugins/yvoke/.claude-plugin/plugin.json` (not duplicated in `.claude-plugin/marketplace.json`), add `CHANGELOG.md` and `docs/releasing.md`, and gate release tagging on CI passing on `main`.
  - Provide `deploy/managed-settings.json` template (`extraKnownMarketplaces` with `autoUpdate: true`, `enabledPlugins`, and deployment `pluginConfigs` for `serverUrl`, web domains, role models, and turn limits).
  - Verify enterprise distribution routes end to end, especially managed settings (Route B) and MDM-copied directory marketplaces (Route D) for organizations where managed settings would otherwise skip `prompt.compose` and `classic.SessionStart` on user-installed local clones.
  - Add per-user rate limiting on `yvoke-web`'s `/mcp` endpoint.
- **Documentation & Consultant Pilot:**
  - Complete second-developer clean-machine verification of `docs/contributing.md` in both terminal and Desktop Code tab.
  - Author `docs/user-guide.md` (installation, Entra sign-in, folder trust onboarding, `/yvoke`, playbooks/profiles, citations, `/yvoke-doctor`, differences from Yvoke Desktop) and `docs/security.md` (`claude plugin validate` `hooks:` and `calls:` output, data flow, `$.store` footprint, managed settings guidance).
  - Run a two-week pilot with 3–5 consultants on macOS and Windows, measuring time from install to first cited answer.

---

## 8. Post-v1 Roadmap (Deferred After v1)

1. **Conversation Sync & Multi-Agent Trace Upload Over MCP:**
   - Add MCP tools on `yvoke-web` to create a conversation, append a completed turn (with idempotency key and playbook version), and upload a multi-agent orchestrator trace.
   - Implement a bounded persistent retry queue in `$.store` (respecting the 4 MiB machine-wide limit, evicting oldest entries with a visible *not synced* indicator, and removing synced payloads immediately after upload), plus per-conversation sync status UI.
2. **Answer Ratings (👍 / 👎 Feedback):**
   - Expose `submit_feedback` on `yvoke-web` (`/mcp`), keyed to the synced server message ID and stamped with the Claude plugin version and playbook version.
   - Render 👍 / 👎 controls beneath synced main-loop replies (`AssistantMessage`), with an optional comment on 👍 and a required comment on 👎 (via `Pane` or `/yvoke-feedback`), plus retry/recovery handling on failed or duplicate submissions.
3. **Claude Chat & Cowork Surfaces:**
   - Spike Cowork and Chat plugin capabilities (command hooks in `hooks.json` for `PreToolUse` and `Stop`, skill `disallowed-tools` behavior, MCP App widgets for passage viewing and feedback).
   - Restore and wire the playbook skill generator (preserved in git commit `cf7147a`, `yvoke-dev/yvoke-claude-plugin#24`) and scheduled catalogue sync workflow if Chat/Cowork surfaces are adopted.

---

## 9. Server-Side (`yvoke-web`) & IT Dependencies and Key Risks

### 9.1 Pending Server-Side & IT Items

| Item | Owner | Milestone | Description |
| --- | --- | --- | --- |
| Entra client *Yvoke for Claude* | `server` / `IT` | Milestone 1 | Public client registration with `http://localhost:<port>/callback`, consented for `APP_SECURITY_MCP_SCOPE` + `offline_access` |
| Area-scoped collection search | `server` | Milestone 1 | Restrict search tools to the active playbook area's collections |
| Passage deep link route | `server` | Milestone 3 | Browser route to open a cited passage by ID (*Open in Yvoke*) |
| `list_profiles(area?)` & `get_profile(name)` | `server` | Milestone 4 | MCP tools exposing multi-agent profiles (excluded from `yvoke-web`'s own web assistant) |
| `/mcp` per-user rate limit | `server` | Milestone 5 | Rate-limit knowledge-base searches arriving from AI clients on `/mcp` |
| Conversation sync, trace & `submit_feedback` MCP tools | `server` | Post-v1 | Authenticated MCP endpoints for turn sync, multi-agent traces, and 👍/👎 ratings |

### 9.2 Key Risks & Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Mod API changes between Claude Code releases | Mod fails to load or misbehaves after an update | CI runs against both pinned minimum (`2.1.287`) and `latest`; runtime minimum version check in Milestone 5 |
| Organization managed settings skip `prompt.compose` / `classic.SessionStart` on user-installed local clones | System prompt instructions or `/resume` restoration skipped in managed orgs | Deploy via organization managed settings or MDM directory marketplace (`deploy/managed-settings.json`, Milestone 5) |
| Enforcing hook throws or exceeds 10-second budget | Engine skips a throwing/timed-out hook | Every enforcing hook attaches `.catch` returning a fail-closed refusal (`{ deny }`, `{ drop }`, or refusal section); server calls time out at 8 s |
| `Bash` sandbox allows an escape vector during `execute_code` spike | Arbitrary code execution risk | Strict pass/fail spike in Milestone 2 across macOS/Windows; any escape drops `execute_code` from v1 |
| Desktop surface link click handling differs from terminal | Citation interaction differs across surfaces | Desktop surface spike in Milestone 3; source button row fallback works identically on both surfaces |
