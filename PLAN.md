# Yvoke for Claude: Feature Roadmap & Implementation Plan

> **Scope:** v1 targets **Claude Code** (both the Claude Desktop **Code** tab and the terminal). Living specifications for the implemented baseline live in [`.lintel/specs/index.md`](.lintel/specs/index.md), and the current architecture and coding conventions live in [`.lintel/steering/architecture.md`](.lintel/steering/architecture.md) and [`.lintel/steering/conventions.md`](.lintel/steering/conventions.md).

This document defines the remaining feature milestones, codebase integration requirements, pre-implementation spikes, server/IT dependencies, and post-v1 roadmap. Each section is self-contained and ready to be executed as a Lintel task (`lintel task start "<title>"`), in the order [§2](#2-recommended-implementation-order) sets.

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
| **System Prompt Builder** | `plugins/yvoke/src/system-prompt.ts` | Resolves base instructions (`get_system_prompt` by playbook prompt, area default, or active default) and active playbook (`get_playbook`), joined by `\n\n---\n\n` in `yvoke:instructions`. Keeps what loaded (`loadedFor`: area, mode, playbook, `get_playbook`'s version) and reuses it only while the setup matches; the setup locks with that version | [`system-prompt.md`](.lintel/specs/system-prompt.md) |
| **Tool Qualification & Gate** | `plugins/yvoke/src/tool-names.ts`, `plugins/yvoke/src/policy.ts` | Dynamic MCP tool prefix normalization (`serverToolPrefix`, `qualifyTool`), deny-by-default `tool.call` gate, per-playbook tool scoping (`allowedServerTools` or `DEFAULT_KB_TOOLS`). The gate catches `ask_clarifying_question` before it reaches the server and asks the user with `$.ui.ask` (`User answered: …` / `User skipped the question.`); a playbook that leaves it out is pointed to `AskUserQuestion` | [`tool-policy.md`](.lintel/specs/tool-policy.md) |
| **Turn Ceilings** | `plugins/yvoke/src/turn-limits.ts`, `plugins/yvoke/src/policy.ts` | Per-question model request caps (25 single, 60 lead, 20 subagent/reviewer), wrap-up denial at request `n - 1`, tool-use stripping at request `n`, and footer notice line | [`turn-limits.md`](.lintel/specs/turn-limits.md) |

Four behaviours of the baseline are easy to miss:

- **The gate lets the mod's own server calls through.** In a real Claude Code session the engine routes the mod's own `$.mcp.call` through the plugin's own `tool.call` hook, so the gate recognises those calls by `next.origin.plugin === 'yvoke'` and lets them pass, before the turn-limit check ([`tool-policy.md`](.lintel/specs/tool-policy.md), D-30). The model's calls are judged as before. Without this, the gate refused the mod's `list_areas`, `list_playbooks`, `get_playbook` and `get_system_prompt` calls and dropped every Yvoke question.
- **Resume fails closed.** On `/resume` or `/branch` of a locked Yvoke session whose saved setup cannot be restored, the session stays a Yvoke session but drops every question with a message to type `/clear`, then `/yvoke`. It never falls back to the area's defaults (D-29).
- **Tools follow the loaded playbook.** Once the setup locks, the gate enforces the tools of the playbook text that actually loaded (from `get_playbook`), not the `list_playbooks` entry from when the band loaded (D-31).
- **A live integration test covers the single-agent path.** A real headless Claude Code session runs with the plugin installed from the working tree, against a stub Yvoke MCP server and a fake Anthropic API, in CI on the minimum Claude Code version and on `latest`. The test kit does not route `$.mcp.call` through `tool.call`, so it could not catch the gate bug above. See [`testing.md`](.lintel/steering/testing.md).

On the server side (`yvoke-web`), the MCP tools `get_system_prompt`, `list_areas`, `list_playbooks`, and `get_playbook` are already implemented and restricted to MCP clients only (excluded from `yvoke-web`'s own web assistant and playbook editor).

---

## 2. Recommended Implementation Order

**This is the only order.** The milestone numbers group the work; they don't set its order.

Each feature is designed to live in its own `plugins/yvoke/src/` module (with pure rule functions tested in `<feature>-rules` suites and hook wiring tested in `<feature>` suites) or add a clean step inside the single `tool.call` gate in `plugins/yvoke/src/policy.ts`.

**Request now, in parallel: Entra ID Public Client Registration** (*Yvoke for Claude*, [§3.1](#31-entra-id-public-client-registration--production-mcpjson-connector)). Without it the plugin cannot sign anyone in to the real `yvoke-web` server. It is an IT request with a wait the team doesn't control, so file it before step 1 and carry on with the steps below. Waiting on it: the production `.mcp.json` defaults and the production token-refresh check (both in §3.1), and the consultant pilot (Milestone 5). The Entra sign-in spike in step 1 does not wait for it.

**Also now, in parallel, on `yvoke-web`: the server half of Area-Scoped Collection Search** ([§3.3](#33-area-scoped-collection-search-pluginsyvokesrcpolicyts--yvoke-web)). The plugin half, the gate passing each session's area, follows once the server takes the `area` argument.

1. **Spikes That Can Change the Plan, First** (run them before or alongside step 2; each can take a design decision away, so learn the answer early):
   - **Dynamic Subagent & Reviewer Spike** ([§6.1](#61-pre-implementation-subagent-spike)) -> confirms `$.agent.register`, `$.agent.spawn`, lead text withholding, reviewer hook placement, `prompt.compose` for registered agents, and `$.ui.ask` from a specialist -> unblocks **Milestone 4: Multi-Agent Investigations**. It is the biggest open risk in the plan.
   - **Entra Sign-In Spike** ([§3.1](#31-entra-id-public-client-registration--production-mcpjson-connector)) -> confirms Claude Code's MCP OAuth sign-in works against Entra, with a test tenant and a test app registration, without waiting for IT.
2. **Immediate Self-Contained Plugin Features** (no external account or server blockers; testable immediately against `claude-code/testing` hook doubles; they can run in parallel with step 1):
   - **Domain-Restricted Web Access (`WebSearch` & `WebFetch`)** in `plugins/yvoke/src/policy.ts` and `plugin.json`.
   - **Playbook Preflight Check** in `plugins/yvoke/src/preflight.ts` (plus catalogue metadata fields in `setup.ts`).
   - **Citation Marker Parser** in `plugins/yvoke/src/citations.ts`.
   - **Minimum Version Check & `/yvoke-doctor` Diagnostics** in `plugins/yvoke/src/doctor.ts`.
3. **Spikes Right Before the Features They Unblock**:
   - **Desktop Surface Citation Rendering Spike** -> confirms `onLinkPress` vs. source button row on the Desktop Code tab and terminal -> unblocks **Citations in Assistant Replies** and **Citation Source Pane**.
   - **Claude Code `Bash` Sandbox Security Spike** -> verifies whether `execute_code` running `node -e` via `Bash` blocks all four escape vectors, and answers §4.2's Node, permission-prompt and sandbox-state questions -> unblocks **Sandboxed Compute Tool (`execute_code`)**.
4. **Milestone 3: Citations & Source Passage Pane** (`src/citations.ts` UI hooks + `Pane` + `get_section`).
5. **Milestone 4: Multi-Agent Investigations** (`list_profiles`/`get_profile` on `yvoke-web`, specialist/reviewer registration, lead mode, enforced review rounds, team UI).
6. **Milestone 5: Packaging, Managed Deployment & Consultant Pilot**.
7. **Post-v1 Roadmap** (conversation sync & traces, 👍/👎 answer ratings, Chat & Cowork surfaces).

---

## 3. Milestone 1: Session Setup, Auth & Playbook Preflight

### 3.1 Entra ID Public Client Registration & Production `.mcp.json` Connector

- **Entra Client Registration (`server` / `IT`):**
  - Register a public client application (*Yvoke for Claude*, or extend the desktop registration) with redirect URI `http://localhost:<port>/callback` (and claude.ai's callback URI if needed), consented for `yvoke-web`'s `APP_SECURITY_MCP_SCOPE` and `offline_access`.
- **Production `.mcp.json` Defaults (`plugins/yvoke/.mcp.json`):**
  - Ship the production `yvoke-web` MCP URL as the default of `${user_config.serverUrl}` along with `oauth.clientId` and `oauth.callbackPort`.
  - Do **not** add an `Authorization` header to `.mcp.json`, because Claude Code disables OAuth PKCE sign-in whenever an `Authorization` header is present.
- **Entra Sign-In Spike (now, with a test tenant; do not wait for IT):**
  - Register a test app (public client, redirect `http://localhost:<port>/callback`, the same scope and `offline_access`) in a test tenant, point a `yvoke-web` at that tenant, and check that Claude Code's own MCP OAuth sign-in works against Entra:
    1. **Discovery:** `yvoke-web` serves `/.well-known/oauth-protected-resource` (the resource URL, the authorization server and `scopes_supported`) and `/.well-known/oauth-authorization-server` (Entra's authorize and token endpoints, `S256`, and `scopes_supported` with `offline_access`), both from `ProtectedResourceMetadataController` (`src/main/java/de/palsoftware/yvoke/shared/security/` in `yvoke-web`). Confirm Claude Code finds and follows them from the `/mcp` URL.
    2. **No registration endpoint:** Neither document advertises a `registration_endpoint`, so Claude Code cannot register a client itself and `oauth.clientId` must be preset in `.mcp.json`. Confirm it uses the preset id and does not try to register.
    3. **The `resource` parameter:** MCP clients send the RFC 8707 `resource` parameter together with the `api://…` scope. Entra may reject a `resource` that does not match the scope's audience. Check this against the test tenant; do not assume it works. If Entra rejects it, the fix is a server, registration or `.mcp.json` change, so find that out now.
    4. **Token against the server's checks:** The access token's issuer, audience and scope must pass what `yvoke-web` requires (`app.security.mcp.allowed-audience` and `app.security.mcp.required-scope`, checked in `SecurityConfig`). Call `/mcp` with the token the sign-in produced.
    5. **Refresh:** With `offline_access`, an expired access token is refreshed without a new sign-in.
  - A failure here is a server or IT change with a wait, which is why the spike comes first.
- **Connection & Token Refresh Verification (production):**
  - Once the registration exists, verify `$.mcp.connect('yvoke')` and `$.mcp.call` in both the Desktop Code tab and terminal after Entra ID sign-in, and confirm how token expiry/refresh errors surface through `callYvoke` (`src/server.ts`).

- **First-Answer Instructions Verification (Desktop Code tab, must do before rollout):**
  - `claude -p` composes the system prompt once at start-up, before the first question's `prompt.submit` loads the instructions, so a headless run never applies the `yvoke:instructions` section (nor the refusal section) on 2.1.287 or 2.1.310; an interactive terminal session does ([`system-prompt.md`](.lintel/specs/system-prompt.md)). Verify in the Claude Desktop Code tab, on the minimum version and on `latest`, that the section reaches the **first** answer of a `/yvoke` session. If that tab drives Claude Code headlessly, the same gap hits real users: their answers would run without the playbook.
  - If it does not reach the first answer, the owner decides whether a Yvoke session should refuse the question wherever the section cannot be applied. Nothing refuses it today, and this check does not build a refusal.

### 3.2 Playbook Preflight Check (`plugins/yvoke/src/preflight.ts`)

- **Settings (`plugins/yvoke/.claude-plugin/plugin.json` `userConfig`, as `yvoke-desktop`'s `playbookValidationEnabled` and `playbookValidationExcludedPlaybooks`, D-32):**
  - `preflightEnabled` (boolean, default `true`): the on/off switch.
  - `preflightExcludedPlaybooks` (string, comma- or newline-separated, default `oim-full`): playbooks that are never checked, each entry a playbook `name` or `title`, matched case-insensitively and trimmed (`isPlaybookValidationExcluded`, `src/shared/types.ts` in `yvoke-desktop`).
- **Behaviour (`yvoke-desktop` 1.3.0 Parity, except the timeout and the candidate scoping):**
  - Runs at most once per session on the first question of a single-agent Yvoke session (`yvoke.preflightDone` in `PluginState.yvoke`), before the setup locks.
  - **When to skip:** As `AppCore.validatePlaybook` (`src/main/AppCore.ts` in `yvoke-desktop`), skip the check immediately when:
    - `preflightEnabled` is `false`;
    - the session is in multi-agent mode;
    - the question is empty;
    - the selected playbook is on the exclusion list (by `name` or `title`);
    - the selected playbook is not among the candidates (for example the lists did not load); or
    - there are fewer than 2 candidates.
  - **Candidates:** The playbooks the setup band offers for the selected area, **the area's default playbook included**. This differs from `yvoke-desktop` on purpose: the desktop lists every user-selectable playbook, while the plugin's setup is area-first (D-15, D-18), so the check only suggests playbooks the user could pick in this area.
  - **Model Fit Check:** Show status via `$.ui.status('Checking whether the playbook fits your question…')` (cleared with `$.ui.status(undefined)` when the check ends) and call `$.model.complete` (no tools) with a prompt listing the user's question, the selected playbook's name, and the candidates (`name`, `title`, `description`). As with `yvoke-desktop`'s `buildValidatorSystemPrompt` (`src/main/agent/playbookValidation.ts`), the model answers with one JSON object:
    - `plausible`: `true` when the selected playbook fits;
    - `suggestedPlaybookName`: the name of a different listed playbook that fits better, else `null`; and
    - `reason`: one or two sentences on why the suggestion fits better, else `''`.
  - **Which model:** `ModelCompleteRequest.model` is required and has no default (an alias or a full model ID). Pass the session's own main-loop model, read with `$.session.model()`. This is what `yvoke-desktop` does (it validates with the conversation's own model), and it needs no model setting of the plugin's own. Pass a low `effort` (`ModelCompleteRequest.effort`, default is the model's own) to keep the call cheap.
  - **8 s Timeout:** Pass `timeoutMs: 8_000` on the `$.model.complete` request (`ModelCompleteRequest.timeoutMs`). When the time is up, the call resolves `isAnswered: false` with `reason: 'aborted'`, and the question goes on with the current playbook.
    - **No budget cost:** a `$` call's time never counts against the hook's 10 s budget (`HookBudget`), and `timeoutMs` bounds the call by the clock, so no timer is needed. Don't time it with `$.clock.sleep`: the sleep is the one `$` call whose wait does count, and a 45 s one would make the `prompt.submit` hook overrun at 10 s before it fired.
    - **Why 8 s rather than `yvoke-desktop`'s 45 s (`VALIDATION_TIMEOUT_MS`):** the user's question waits while the check runs, and the check is only advice, so a hung call gives up quickly and the question goes with the playbook the user picked. 8 s also matches the server-call timeout (`TIMEOUT_MS` in `src/server.ts`).
  - **Handling the Suggestion:** Read the reply as `yvoke-desktop`'s `parseValidation` does:
    - Take the JSON from the first `{` to the last `}`, so a fenced block or a line of preamble still parses. A `plausible` that isn't `false` is a fit: proceed without prompting the user.
    - Match `suggestedPlaybookName` against the candidates case-insensitively, by `name` first, then by `title`.
    - If it matches the already-chosen playbook, treat it as a fit and proceed without prompting the user. This is the only suggestion that counts as a fit: a suggestion of any other candidate, **the area's default playbook included**, is a valid suggestion.
    - If it matches a different candidate, prompt the user via `$.ui.ask` with `header: 'Playbook'`, the model's trimmed `reason` in the question (`<reason> Switch playbooks?`, or `<suggestedTitle> may fit this question better than <currentTitle>. Switch playbooks?` when the reason is empty), and the two options of `yvoke-desktop`'s card (`src/renderer/src/components/ChatView.tsx`):
      1. `Switch to <suggestedTitle> (<suggestedName>)` — updates `yvoke.setup` (`playbook` and `version`) in `$.state` and `$.store`.
      2. `Send anyway` — keeps the current playbook.
    - `$.ui.ask` resolves to the chosen label or to free text typed under "Other", and rejects when the user dismisses the dialog and in a `-p` run. Only an answer exactly equal to the Switch label switches; anything else means `Send anyway`.
  - **Fails Open:** The question goes on with the currently selected playbook on any error; on the 8 s timeout; on a `$.model.complete` result with `isAnswered: false` (`reason` `api-error`, `empty-reply` or `aborted`); on an unparseable reply; on a missing or unknown suggested playbook name; and when `$.ui.ask` rejects (dismissed, or a `-p` run) or resolves to anything but the Switch label (`Send anyway`, or free text typed under "Other").
- **Required Codebase Integration Details:**
  1. **Hook Registration Order in `plugins/yvoke/hooks/register.tsx`:** Register `registerPreflight(on)` **before** `registerSetup(on, ...)` and `registerSystemPrompt(on)`, using matcher `{ text: /^/ }` (since `registerSystemPrompt` owns the unmatched `prompt.submit` hook). Because `registerSetup`'s `prompt.submit` hook reads `yvoke.setup` before calling `next(e)` and writes that copy back to lock the session, running `registerPreflight` outermost ensures that any playbook switch is written to `yvoke.setup` before `registerSetup` reads and locks it and before `registerSystemPrompt` loads instructions.
  2. **Catalogue Metadata in `plugins/yvoke/src/setup.ts`:** Extend `PlaybookEntry` and `toCatalogue` in `plugins/yvoke/src/setup.ts` (and `plugins/yvoke/types/index.d.ts`) to retain `title` (defaulting to `name`) and `description` (defaulting to `''`) from `list_playbooks` so `preflight.ts` can include candidate playbook descriptions in the classifier prompt.

### 3.3 Area-Scoped Collection Search (`plugins/yvoke/src/policy.ts` & `yvoke-web`)

This is both a plugin and a server task. An MCP call carries no session context, and `yvoke-web`'s `search_corpus` takes a required `collection` argument that the model chooses (`src/main/java/de/palsoftware/yvoke/mcp/tools/SearchCorpusTool.java` in `yvoke-web`), so the server alone cannot know a session's area.

- **Plugin (`plugins/yvoke/src/policy.ts`):** The `tool.call` gate adds the session's area (from the locked setup) to each call of a Yvoke knowledge-base tool that reads collections, by passing changed arguments on with `next({ ...e, area })`, the same way [§4.1](#41-domain-restricted-web-access-websearch--webfetch) rewrites `WebSearch`'s `allowed_domains`. Ship it after the server side below. Add the rule to [`tool-policy.md`](.lintel/specs/tool-policy.md) when it is built.
- **Server (`yvoke-web`):** The knowledge-base tools that read collections (`search_corpus`, `list_documents`, etc.) take an optional `area` argument and restrict retrieval to that area's collections. Without it they stay unscoped, as today. Web chat does its scoping in-process and doesn't need the argument.
- **Why not a header:** `.mcp.json` headers are fixed per install, so they can't follow each session's area.

---

## 4. Milestone 2: Tool Policy Extensions (Web Access & Sandboxed Compute)

### 4.1 Domain-Restricted Web Access (`WebSearch` & `WebFetch`)

- **Allow-List Is Deployment Configuration, the Switch Is a User Setting (D-34):**
  - On `yvoke-desktop`, `webSearch.allowedDomains` is deployment configuration: it is read only from the bundled `settings.json`, never from the user's profile, and `Settings.set()` refuses a list from the renderer (`src/main/settings/Settings.ts`). The plugin follows that for the allow-list. `webSearch.enabled` is a user preference on the desktop (a checkbox on the settings screen), and the plugin's switch stays one too.
  - In the plugin, `webEnabled` (boolean, default `false`, so web access is off until the user turns it on) stays a per-user setting in `userConfig`, changed with `/plugin configure`.
  - `allowedWebDomains` (string, comma- or newline-separated host/path patterns, default `""`) is set by the organisation through managed settings (`pluginConfigs`, see `deploy/managed-settings.json` in [§7](#7-milestone-5-diagnostics-compatibility-packaging--rollout)), not by the user with `/plugin configure`.
  - **Check first:** Confirm whether a user can override a value that managed `pluginConfigs` sets (for example with `/plugin configure` or their own settings). If they can, the allow-list is not deployment configuration after all: serve it from `yvoke-web` instead, as the server is the source of truth (product principle 1 in [`product.md`](.lintel/steering/product.md)). That needs a server item ([§9.1](#91-pending-server-side--it-items)).
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
  - The same spike also answers three questions about what `execute_code` costs:
    - **Node on every machine:** `node -e` needs Node installed on each consultant's machine, and the native Claude Code install neither needs nor ships it. Check what the `Bash` call does when `node` is missing. Without `node`, `execute_code` is refused and runs nothing (check 4 below).
    - **Permission prompts:** Check whether a plugin-originated `Bash` call through `$.tool.call` asks the user for permission each time. If it does, find out whether it can be pre-allowed for that one command without also allowing the model's own `Bash`. The plugin's `tool.check` hook (D-36) is where such a grant would go.
    - **Sandbox state:** The mod API (checked in Claude Code 2.1.309's declarations, `plugins/yvoke/.claude-plugin/types/claude-code/index.d.ts`) has no way to read whether Claude Code's sandbox is on, so check 2 below has nothing to call. The spike must find a way.
  - **Pass/Fail Gate:** If any of the four escape vectors succeeds on any supported platform where the sandbox reports active, or the spike finds no way to read the sandbox state, `execute_code` is dropped from v1.
- **Implementation (If Spike Passes):**
  - Register `execute_code` once per process when `/yvoke` activates or restores a Yvoke session (`JavaScript` code up to 10,000 characters, 1 s default and 2 s maximum timeout, 32,000-character output cap).
  - Preserve `codeExecution?: boolean` on `PlaybookEntry` in `plugins/yvoke/src/setup.ts`.
  - On every `execute_code` invocation, check in order and refuse (running nothing) when:
    1. `yvokeSession` is not `true`;
    2. Claude Code's sandbox is off or unsupported (such as on native Windows), read the way the spike found;
    3. the active playbook sets `codeExecution: false`. Nothing else decides this: `yvoke-desktop` grants compute exactly when `codeExecution !== false`, for the single agent (`isToolAllowed`, `src/main/agent/policy.ts`) and for specialists (`mapSpecialistTools`, `src/main/agent/orchestration.ts`), so a declared `tools` list neither removes nor adds it, and the plugin's `tool-names.ts` already ignores compute names in declarations (D-33); or
    4. `node` is not installed.
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
  5. Whether `prompt.compose` runs for the requests of agents registered with `$.agent.register`. Their `prompt` is "the agent's system prompt, whole: it replaces the session's" (`AgentSpec` in `plugins/yvoke/.claude-plugin/types/claude-code/index.d.ts`). If the compose hook in `plugins/yvoke/src/system-prompt.ts` still runs for them, it adds the lead's instructions, or the refusal section, to every specialist. The spike decides whether that hook must skip agents.
  6. Whether `$.ui.ask` shows the question dialog when a specialist subagent calls the server's `ask_clarifying_question`, which the tool gate catches (D-27, [`tool-policy.md`](.lintel/specs/tool-policy.md)).

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
- **Specialist Call Budget (guidance, not a deny; D-33):**
  - As on `yvoke-desktop`, `maxSpecialistCalls` is prompt guidance: the lead's adapter text says "Aim for at most N specialist calls in total" (`orchestratorAdapter`, `src/main/agent/orchestration.ts`). It denies nothing. The hard bound on a lead's work is its turn ceiling (`turnLimitLead`, [`turn-limits.md`](.lintel/specs/turn-limits.md)).
  - The plugin may count specialist spawns per question in `agent.spawn` (excluding the reviewer agent) to show the count on the team UI ([§6.6](#66-team-ui)), but it must not deny a spawn on that count.
- **Specialist Clarifying Questions:**
  - The tool gate already asks the user for every caller of `ask_clarifying_question` (D-27); spike item 6 ([§6.1](#61-pre-implementation-subagent-spike)) confirms it works from a specialist. Only if it doesn't does Milestone 4 need another way to surface the question.
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
  - The supported minimum is the newest Claude Code release that has every mod API the plugin uses. Today that is `2.1.287`, which CI verifies, and it is also the oldest release with mods at all.
  - Read `$.session.version` at session start and show a clear notice only when the running version is below that minimum. Mods did not exist before `2.1.287`, so there the mod never loads and no notice could show; the notice matters once the minimum moves above the first mod release.
  - The minimum is one value, kept in the doctor and the check, and the CI pin moves with it (so a plugin change that needs a newer API raises the minimum and the pin together).
- **`/yvoke-doctor` Diagnostics Command (`plugins/yvoke/src/doctor.ts`):**
  - Register `/yvoke-doctor` to report:
    1. Claude Code version vs. the supported minimum.
    2. Whether the current session is an active Yvoke session (`/yvoke`).
    3. Yvoke MCP server connection and sign-in status, server version, and available area/playbook count (via `callYvoke`).
    4. Active setup (`area`, `mode`, `playbook` + `version`, or `profile`).
    5. Whether normal Claude subscription billing applies or `ANTHROPIC_API_KEY` is set in the environment (warn if an API key is overriding subscription billing, without ever printing any key, token, or secret).
- **Versioning, Release Automation & Managed Deployment:**
  - Maintain `version` solely in `plugins/yvoke/.claude-plugin/plugin.json` (not duplicated in `.claude-plugin/marketplace.json`), add `CHANGELOG.md` and `docs/releasing.md`, and gate release tagging on CI passing on `main`.
  - **Other directory listing items:** The plugin-directory section of `claude plugin validate --strict plugins/yvoke` reports two warnings, listed apart from the problems the directory refuses a submission for (the licence is done: Apache-2.0, D-35):
    - `ICON_MISSING`: add a square PNG at `.claude-plugin/icon.png`, or set `icon` in `plugin.json` to a square PNG or JPEG (512 to 2048 px on each side, under 2 MB). It can become the listing icon only once: the first time the plugin is saved or submitted in the developer portal.
    - `PRIVACY_URL_MISSING`: the plugin connects to a remote MCP server, so set `privacyPolicyUrl` in `plugin.json` (or add a *Privacy* link to the README).
    - Passing these checks doesn't mean the directory accepts the plugin: it applies its own rules to hooks modules, and runs a safety scan and a policy review the validator doesn't.
  - Provide `deploy/managed-settings.json` template (`extraKnownMarketplaces` with `autoUpdate: true`, `enabledPlugins`, and deployment `pluginConfigs` for `serverUrl`, the web domain allow-list ([§4.1](#41-domain-restricted-web-access-websearch--webfetch)), role models, and turn limits).
  - Verify enterprise distribution routes end to end, especially managed settings (Route B) and MDM-copied directory marketplaces (Route D) for organizations where managed settings would otherwise skip `prompt.compose` and `classic.SessionStart` on user-installed local clones.
  - Add per-user rate limiting on `yvoke-web`'s `/mcp` endpoint.
- **Documentation & Consultant Pilot:**
  - Complete second-developer clean-machine verification of `docs/contributing.md` in both terminal and Desktop Code tab.
  - Author `docs/user-guide.md` (installation, Entra sign-in, folder trust onboarding, `/yvoke`, playbooks/profiles, citations, `/yvoke-doctor`, differences from Yvoke Desktop) and `docs/security.md` (`claude plugin validate` `hooks:` and `calls:` output, data flow, `$.store` footprint, managed settings guidance).
  - Run a two-week pilot with 3–5 consultants on macOS and Windows, measuring time from install to first cited answer.
    - The plugin sends no telemetry, so measure it from `yvoke-web`'s `/mcp` logs per signed-in user, with the consultants' own notes as a check.

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
| Entra client *Yvoke for Claude* | `server` / `IT` | Now (needed by Milestone 1) | Public client registration with `http://localhost:<port>/callback`, consented for `APP_SECURITY_MCP_SCOPE` + `offline_access` |
| Area-scoped collection search | `server` | Milestone 1 | Optional `area` argument on the collection-reading MCP tools (`search_corpus`, `list_documents`, etc.), restricting retrieval to that area's collections; without it they stay unscoped. The plugin's gate passes each session's area ([§3.3](#33-area-scoped-collection-search-pluginsyvokesrcpolicyts--yvoke-web)) |
| Web allow-list tool (only if needed) | `server` | Milestone 2 | Only if [§4.1](#41-domain-restricted-web-access-websearch--webfetch)'s check finds a user can override managed `pluginConfigs`: an MCP tool serving `webSearch.allowedDomains`, so the server stays the source of truth |
| Passage deep link route | `server` | Milestone 3 | Browser route to open a cited passage by ID (*Open in Yvoke*) |
| `list_profiles(area?)` & `get_profile(name)` | `server` | Milestone 4 | MCP tools exposing multi-agent profiles (excluded from `yvoke-web`'s own web assistant) |
| `/mcp` per-user rate limit | `server` | Milestone 5 | Rate-limit knowledge-base searches arriving from AI clients on `/mcp` |
| Conversation sync, trace & `submit_feedback` MCP tools | `server` | Post-v1 | Authenticated MCP endpoints for turn sync, multi-agent traces, and 👍/👎 ratings |

### 9.2 Key Risks & Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Mod API changes between Claude Code releases | Mod fails to load or misbehaves after an update | CI runs against both pinned minimum (`2.1.287`) and `latest`; runtime minimum version check in Milestone 5 |
| Claude Code composes the system prompt before the first question in a headless run (`claude -p`), so the Yvoke instructions are not applied there; an interactive terminal session applies them | If the Desktop Code tab runs Claude Code headlessly, consultants get answers without the playbook and nothing tells them | Check in the Desktop Code tab that the section reaches the first answer ([§3.1](#31-entra-id-public-client-registration--production-mcpjson-connector)); the owner then decides whether a Yvoke session refuses questions where the section cannot be applied. No refusal is built now |
| Organization managed settings skip `prompt.compose` / `classic.SessionStart` on user-installed local clones | System prompt instructions or `/resume` restoration skipped in managed orgs | Deploy via organization managed settings or MDM directory marketplace (`deploy/managed-settings.json`, Milestone 5) |
| The test kit differs from the real engine (it does not route `$.mcp.call` through `tool.call`) | A gate bug the unit tests cannot see: the gate once refused the mod's own server calls and dropped every Yvoke question | A live headless integration test runs a real Claude Code session on the minimum version and on `latest` in CI ([`testing.md`](.lintel/steering/testing.md)); spikes check each new engine behaviour live before relying on the kit |
| Claude Code's MCP OAuth sign-in may not work against Entra (no `registration_endpoint`, the `resource` parameter, audience or scope, refresh) | The plugin cannot sign anyone in to the real server, found late and with an IT wait | The Entra sign-in spike against a test tenant comes first ([§3.1](#31-entra-id-public-client-registration--production-mcpjson-connector), [§2](#2-recommended-implementation-order)) |
| Enforcing hook throws or exceeds 10-second budget | Engine skips a throwing/timed-out hook | Every enforcing hook attaches `.catch` returning a fail-closed refusal (`{ deny }`, `{ drop }`, or refusal section); server calls time out at 8 s |
| `Bash` sandbox allows an escape vector during `execute_code` spike, or the mod cannot read whether it is on | Arbitrary code execution risk | Strict pass/fail spike in Milestone 2 across macOS/Windows; any escape, or no way to read the sandbox state, drops `execute_code` from v1; refused when `node` is missing |
| Desktop surface link click handling differs from terminal | Citation interaction differs across surfaces | Desktop surface spike in Milestone 3; source button row fallback works identically on both surfaces |
