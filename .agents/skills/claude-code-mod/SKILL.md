---
name: claude-code-mod
description: Claude Code mod runtime environment, static validation rules, hook registration and ordering, fail-closed safety invariants, UI surface rendering, and Architect contract/stub conventions.
---

# Claude Code Mod Architecture & Rules (`claude-code-mod`)

Authoritative reference for designing, writing, and wiring in-process Claude Code mod modules under `plugins/yvoke/hooks/register.tsx` and `plugins/yvoke/src/`.

---

## 1. Mod Runtime Environment & Forbidden APIs

1. **Isolated Worker Runtime (Not Node.js):**
   - Mod modules execute inside Claude Code's isolated mod worker, **not** a standard Node.js runtime.
   - No `require`, no dynamic `import()` (a module containing dynamic `import()` fails to load), no DOM globals (`window`, `document`), and no `process` or `Buffer`.
   - Static ES module `import` of the plugin's own `.ts` and `.tsx` files is supported using **extensionless** relative specifiers (e.g., `import { callYvoke } from './server'`, never `'./server.js'`).
   - Use `$.clock.sleep(ms, { signal })` for timers and timeouts (do not use `setTimeout` or `setInterval`).
2. **Forbidden Engine Capabilities (`$.fs`, `$.process`, `$.http`):**
   - Never call `$.fs`, `$.process`, or `$.http`.
   - `claude plugin validate --strict plugins/yvoke` prints a `calls:` line; `.lintel/scripts/build.sh` and CI fail if `calls:` contains `$.fs`, `$.process`, or `$.http`.
   - All backend communication with `yvoke-web` goes exclusively through `$.mcp.connect` and `$.mcp.call` via `callYvoke` (`plugins/yvoke/src/server.ts`).
3. **Type Declarations Source of Truth:**
   - Claude Code generates its own mod API type declarations into `plugins/yvoke/.claude-plugin/types/claude-code/index.d.ts` when loaded via `scripts/lay-types.mjs`.
   - Whenever documentation and `plugins/yvoke/.claude-plugin/types/claude-code/index.d.ts` disagree, the generated type declarations win.

---

## 2. Static Analysis Rules (`claude plugin validate --strict`)

1. **Never Pass `$` Across File Imports:**
   - The Claude Code validator rejects any module that passes `$` (or `$.state` / `$.mcp` references) into a function imported from another file.
   - Keep pure rule functions in `plugins/yvoke/src/<feature>.ts` free of `$`, and construct thin inline closure adapters (such as `ServerIo = { connect, call, sleep }`) directly inside the hook callback where `$` is in scope.
2. **Never Wrap `on(...)` or Import `$.state` Keys:**
   - Do not build hooks through higher-order wrapper functions (e.g., `on('tool.call', yvokeOnly(handler))` is rejected).
   - Write `$.state.get({ plugin: 'yvoke', key: '...' })` and `$.state.set({ plugin: 'yvoke', key: '...' }, value)` with literal keys inline inside the module that registers the hook.
3. **Declare All `$.state` Keys in `plugins/yvoke/types/index.d.ts`:**
   - `claude plugin validate` checks every `$.state` key against `PluginState.yvoke` in `plugins/yvoke/types/index.d.ts`.
   - Declare keys inline inside `interface PluginState { yvoke: { ... } }` (and mirror them in `YvokeState`), without top-level imports in `plugins/yvoke/types/index.d.ts`.
4. **One Unmatched Hook Per Event & Strict Registration Order:**
   - Registering two unmatched `on(event, handler)` hooks for the same `event` across the plugin causes module load failure (`registered twice without a matcher`).
   - **Current Unmatched Hook Owners:**
     - `tool.call`: owned by `plugins/yvoke/src/policy.ts` (`registerPolicy`). Every tool rule (`turn-limits`, playbook scoping, and planned web/compute/lead-mode rules) is evaluated inside this single `tool.call` gate in explicit order so failure messages and precedence are deterministic.
     - `prompt.submit` and `prompt.compose`: unmatched hooks owned by `plugins/yvoke/src/system-prompt.ts` (`registerSystemPrompt`). Other `prompt.submit` hooks (such as `registerSetup` in `plugins/yvoke/src/setup.ts` or a playbook preflight check) must specify a matcher (e.g., `{ text: /^/ }`).
     - `classic.SessionStart`: unmatched hook owned by `plugins/yvoke/src/session.ts` (`registerSession`). `registerSetup` uses matcher `{ source: /^(clear|resume|fork)$/ }`.
   - **Registration Order in `plugins/yvoke/hooks/register.tsx`:**
     1. `registerSetup(on, options)`
     2. `registerSession(on)`
     3. `registerPolicy(on)`
     4. `registerTurnLimits(on, options)`
     5. `registerSystemPrompt(on)`
     Hooks nest in registration order (first registered = outermost). A hook that returns a final answer without calling `next(e)` hides hooks registered after it—for example, `registerSetup` must register before `registerSession` so `setup.ts` wraps `session.ts`'s `command.run` hook on `/yvoke`.

---

## 3. Hook Safety Invariants

1. **Yvoke Session Guard (`yvokeSession`):**
   - Outside a session started with `/yvoke`, the mod must be completely invisible and pass every event through unchanged.
   - Every enforcing or UI hook must open with these exact two lines written inline in its own file:
     ```ts
     const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
     if (isYvoke !== true) return next(e)
     ```
2. **Fail Closed Explicitly on Enforcing Hooks:**
   - A hook that throws, returns an invalid shape, or exceeds its 10-second execution budget (`$.clock.sleep` counts against the budget; `$` and `next` calls do not) is skipped by the engine.
   - Attach `.catch(($, e, next) => ...)` to every enforcing hook (`tool.call`, `prompt.submit`, `prompt.compose`, `agent.spawn`, `turn.step`) returning the safe refusal (`{ deny }`, `{ drop }`, refusal section, or for `turn.step`'s `async function*` generator, yielding `withoutTools(next(e))`):
     ```ts
     on('tool.call', enforcePolicy).catch((_$, e, next) =>
       next.called ? next(e) : { deny: POLICY_FAILED },
     )
     ```
   - Once `next.called` is `true`, the downstream action has already run; pass on `next(e)` rather than claiming the action was blocked.
   - Exception: the planned playbook preflight check explicitly fails open on errors or timeouts.
3. **Frozen Event Payloads (`e`):**
   - Event objects `e` are frozen. Rewrite tool arguments or event properties by passing a new object to `next({ ...e, allowed_domains: [...] })`. Note that tool arguments sit flat on `e` beside `e.tool` (there is no `e.input`).
4. **Log Hygiene:**
   - Never log tokens, credentials, or full answers with `$.ui.log` (debug logs are attached to support tickets).

---

## 4. UI Rendering & JSX Rules

1. **JSX Factory & Surface Resolution:**
   - JSX compiles against global `h` and `Fragment` declared by Claude Code's mod types. Never import React.
   - Resolve UI components per render event via `const { Box, Text, Button, Select, Markdown } = $.ui.resolve(e)`.
2. **Element & Surface Constraints:**
   - `Text` and `Markdown` elements accept at most **10,000 characters** per element; split longer strings across multiple elements or open a `Pane`.
   - `AbovePrompt` and `AssistantMessage` hooks run on `terminal` and `desktop` surfaces.

---

## 5. TypeScript Contracts & Architect Stub Rule (`lintel lock`)

1. **Type-First Contracts & Discriminated Unions:**
   - Place pure rule functions (no `$` parameter) at the top of `plugins/yvoke/src/<feature>.ts` with explicit parameter and return types, using discriminated unions for fallible results (e.g., `{ ok: true, ... } | { ok: false, error: string }` or `{ allow: true } | { allow: false, reason: string }`).
   - Specify exact exported symbol names, types, error strings, `PluginState.yvoke` keys, and hook registrations in `shared/phase-<N>/contract.md`.
2. **Never Throw Synchronously Inside `register<Feature>(on)` in Stubs:**
   - `plugins/yvoke/hooks/register.tsx` invokes `register<Feature>(on)` at plugin load time before any test case executes.
   - If a stub throws `new Error('not implemented')` synchronously inside `register<Feature>(on)`, `claude plugin test` aborts at module load with exit code `2` instead of running tests and failing on assertions with exit code `1` (which blocks `lintel lock`).
   - In Architect code stubs:
     - Pure helper functions (`src/<feature>.ts`) should throw `new Error('not implemented')`.
     - `register<Feature>(on)` should either be a no-op `{}` in the stub or throw inside the async hook callback registered on `on(...)`, so `build.sh` exits `0` and the unit test runner fails cleanly on test assertions with exit code `1`.
