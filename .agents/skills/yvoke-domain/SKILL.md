---
name: yvoke-domain
description: Yvoke domain model, yvoke-desktop and yvoke-web 1.3.0 parity rules, MCP server client contract, session/setup lifecycle, system prompt resolution, tool qualification, and multi-agent orchestration.
---

# Yvoke Domain Architecture & Parity Rules (`yvoke-domain`)

Authoritative domain rules for `yvoke-claude-plugin` and its parity contracts with `yvoke-desktop` 1.3.0 and `yvoke-web` 1.3.0.

---

## 1. Parity Mapping with `yvoke-desktop` 1.3.0 & `yvoke-web` 1.3.0

| Plugin Module / Feature | Status | `yvoke-desktop` Reference Path | Core Responsibility |
| --- | --- | --- | --- |
| `plugins/yvoke/src/server.ts` | Implemented | `src/main/agent/McpPrompts.ts` | MCP tool invocation (`callYvoke`), JSON string unwrapping, `ERROR:` body detection, `Yvoke Backend:` prefixing |
| `plugins/yvoke/src/session.ts` | Implemented | Session lifecycle | Opt-in `/yvoke` activation before first question, `/clear` reset, `/resume` and `/branch` persistence |
| `plugins/yvoke/src/setup.ts` | Implemented | Setup selector | Area -> Mode -> Playbook (`name vN`) or Profile selection, locking on first question, playbook version upgrade on resume/branch |
| `plugins/yvoke/src/system-prompt.ts` | Implemented | `src/main/agent/McpPrompts.ts` | Base instructions (`get_system_prompt`) + playbook (`get_playbook`) joined by `\n\n---\n\n` |
| `plugins/yvoke/src/tool-names.ts` | Implemented | `src/shared/types.ts`, `src/main/agent/computeTools.ts` | `serverToolPrefix`, `qualifyTool`, `builtinTool`, `DEFAULT_KB_TOOLS`, `isWebTool`, `isComputeTool` |
| `plugins/yvoke/src/policy.ts` | Implemented (core gate) | `src/main/agent/policy.ts` | Single `tool.call` gate: turn-limit check first, then deny-by-default and playbook tool scoping (catches `ask_clarifying_question` and asks the user with `$.ui.ask`; planned extensions: web allow-list and `execute_code`) |
| `plugins/yvoke/src/turn-limits.ts` | Implemented | `src/main/agent/AgentService.ts` | Per-question turn ceilings (single: 25, lead: 60, subagent/reviewer: 20), wrap-up tool denial at `n - 1`, tool drop at `n` |
| Playbook Preflight (`src/preflight.ts`) | Planned (`PLAN.md`) | `src/main/agent/playbookValidation.ts`, `PlaybookValidator.ts` | `$.model.complete` fit check on non-default playbooks when area has $\ge 2$ playbooks; fails open |
| Citations (`src/citations.ts`) | Planned (`PLAN.md`) | `src/renderer/src/components/citationRehype.ts`, `CitationModal.tsx`, `sectionView.ts` | Citation marker parsing (`[<uuid>]` and legacy markers), `get_section` chunk/document lookup, passage cleaning |
| Multi-Agent Orchestration | Planned (`PLAN.md`) | `src/main/agent/orchestration.ts`, `reviewTools.ts`, `evidenceDigest.ts`, `chunkBlocks.ts` | Live profile registration (`$.agent.register`), `mapSpecialistTools`, `submit_answer`, `submit_review`, enforced review rounds |

---

## 2. Server Client Contract (`plugins/yvoke/src/server.ts`)

1. **Single Entry Point (`callYvoke(io, tool, args, options)`):**
   - Connects to server `'yvoke'` (`$.mcp.connect('yvoke')`) and invokes `$.mcp.call(connected.server, tool, args)`.
   - Default timeout is `TIMEOUT_MS = 8_000` (below the 10-second hook budget).
   - Unwraps JSON-encoded string blocks (`unwrapJsonString`) and joins non-empty text blocks with `\n\n`.
2. **Error Detection & Prefixing (`Yvoke Backend:`):**
   - Treats `result.isError === true` **or** combined text starting with `/^\s*(ERROR|Error):/` as a failure (`yvoke-web` MCP tools report errors at the start of the body without setting `isError`).
   - Every failure returns `{ ok: false, error }` prefixed with `'Yvoke Backend: '` exactly once (never throws, never caches, never retries).
   - When `connected.isConnected` is false with `reason === 'auth'`, appends `' Run /mcp and sign in to yvoke.'`.

---

## 3. Session & Setup Lifecycle (`session.ts`, `setup.ts`)

1. **Opt-In Per Session (`/yvoke`):**
   - A session becomes a Yvoke session only when the user runs `/yvoke` before the first question (`$.session.turns() === 0`).
   - `/yvoke` writes `{ yvoke: true }` to `$.store` (`session:<sessionId>`) before setting `$.state` (`yvokeSession: true`).
   - Once both writes succeed, `/yvoke` deletes the `session:` and `setup:` entries of sessions beyond the newest `KEEP_SESSIONS` (5,000), by insertion order (`staleKeys`). A failed prune is ignored and the session still starts (D-28).
2. **Area-First Setup Band (`AbovePrompt`):**
   - Selects **Area** (default `OIM`, or first area) -> **Mode** (`Single agent`; `Multi-agent` is gated behind `MULTI_AGENT_READY = false` in `setup.ts` until multi-agent profiles are wired) -> **Playbook** (`<name> v<version>`, default `area.defaultPlaybook`) or **Profile** (`area.defaultProfile`).
   - Prototype areas and playbooks (`prototype: true`) are hidden unless `options.showPrototypes === true`.
3. **Locking & Persistence (`/clear`, `/resume`, `/branch`):**
   - The first non-dropped question locks the setup (`lockedSessionId = sessionId`) and displays the read-only line `Area · Mode · playbook vN` in the band and status line (`$.ui.status`).
   - **Lock with the Loaded Version:** `get_playbook` takes a name only, so `system-prompt.ts` records the version it got in `yvoke.instructions` (`loadedFor`). When that differs from the band's, the setup locks with the loaded version (a setup already locked, after `/resume` or `/branch`, moves to it on its first question) and shows a toast (`<playbook> is now v<to> on the server, so this session uses v<to> (the band showed v<from>).`). Cached instructions are reused only while `loadedFor` matches the setup.
   - `/clear` resets the session to plain Claude Code.
   - `/resume` restores `yvokeSession` and `setup` from `$.store` for `e.session_id`.
   - `/branch` (`source: 'fork'`) starts with empty `$.state` in Claude Code; the mod reads the parent session ID recorded in `$.store` under key `'ended'` (written on `session.end`) and carries over its Yvoke flag and locked setup.
   - **Playbook Version Upgrade on Resume/Branch:** On `/resume` or `/branch` of a locked single-agent session, if the server's catalogue has a newer `version` for the locked playbook, the setup updates to the new version and shows a toast (`<playbook> moved from v<from> to v<to> since the setup was locked; answers from now on use v<to>.`).

---

## 4. System Prompt Composition (`system-prompt.ts`)

1. **Base Instructions Resolution Order (1.3.0 Parity):**
   - Decided from `list_areas` and `get_playbook` metadata **before** calling `get_system_prompt`:
     1. The playbook's own `systemPrompt` if **any** area in `list_areas` lists that prompt name in its `systemPrompts` array (global lookup on the server).
     2. Otherwise, the session area's `defaultSystemPrompt`.
     3. Otherwise, the active default (`get_system_prompt` called with no `name`).
   - Makes exactly one `get_system_prompt` call; any failure (including `ERROR:`) drops the question.
2. **Section Formatting:**
   - Appends `{ id: 'yvoke:instructions', text: `${base}\n\n---\n\n${playbook}`, scope: 'session' }` in `prompt.compose` (base instructions first, playbook second so playbook rules take precedence).

---

## 5. Tool Naming, Policy Gate & Turn Ceilings (`tool-names.ts`, `policy.ts`, `turn-limits.ts`)

1. **Dynamic Server Tool Prefixing (`serverToolPrefix` & `qualifyTool`):**
   - `serverToolPrefix(server)` replaces characters outside `[a-zA-Z0-9_-]` with `_` (and for `'claude.ai ...'` connectors collapses `_+` and trims leading/trailing `_`), producing `mcp__<normalized>__` (e.g., `mcp__plugin_yvoke_yvoke__` or `mcp__claude_ai_yvoke__`).
   - `qualifyTool(tool, serverPrefix)` trims whitespace, leaves built-in tools (`WebSearch`, `WebFetch`, `ToolSearch`, `AskUserQuestion`, case-insensitive) unprefixed in their canonical casing, and strips any legacy `mcp__<alias>__` prefix (`mcp__yvoke__`, `mcp__oim__`) before prepending `serverPrefix`.
2. **Playbook Tool Scoping (`allowedServerTools`):**
   - When a playbook declares `tools: []` or `null`, it receives `DEFAULT_KB_TOOLS` (the 10 knowledge-base tools, `ask_clarifying_question` among them).
   - When a playbook declares a non-empty `tools` list, built-in web tools (`WebSearch`, `WebFetch`) and compute tools (`execute_code`, `calculate`, `statistics`, `date_diff`, `mcp__compute__*`) are filtered out of `allowedServerTools` so a playbook declaration alone never grants web or compute access without their dedicated gates.
   - If a Yvoke server tool is called when `setup` or `catalogue` is missing or `tools` is `'unreadable'`, deny with `PLAYBOOK_UNKNOWN` (never fall back to `DEFAULT_KB_TOOLS`).
   - A call to the Yvoke server's `ask_clarifying_question` never reaches the server. When the playbook grants it, the gate asks with `$.ui.ask(question, { options, header: 'Yvoke' })` (first 4 options) and answers `{ result: 'User answered: <answer>' }`, or `User skipped the question.` when `$.ui.ask` rejects. With no question or fewer than 2 options it answers `NOT_ASKED` without a dialog. A playbook that declares tools and leaves it out gets `USE_ASK_USER_QUESTION`. A tool of that name on another server gets the usual `denied(tool)`.
3. **Turn Ceilings (`turn-limits.ts`):**
   - Defaults: `turnLimitSingle = 25`, `turnLimitLead = 60`, `turnLimitAgent = 20` (shared by all specialists and the reviewer). `0` disables the ceiling; values `< 2` clamp to `2`.
   - Checked first in `policy.ts`: at request `n - 1` (`index >= n - 2`), tool calls are denied with `WRAP_UP`; at request `n` (`index >= n - 1`), `turn.step` strips tool uses from the model stream and `turn.complete` appends `flagLine(n)` beneath main-loop answers when `at.turnId === e.turnId`.
