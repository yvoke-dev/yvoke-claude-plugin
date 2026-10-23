---
name: claude-code-testing
description: Headless mod testing with claude-code/testing, engine hook doubles beneath the plugin, state/store failure staging, deterministic clock mocking, and terminal/desktop surface mounting.
---

# Testing Claude Code Mods (`claude-code-testing`)

Authoritative patterns for authoring and reviewing automated mod and unit tests in `plugins/yvoke/tests/` executed by `claude plugin test plugins/yvoke`.

---

## 1. Test Runner & Imports

1. **Headless Mod Engine Runner (the unit test runner in `.lintel/scripts/`):**
   - Tests run via `claude plugin test plugins/yvoke` against the real Claude Code mod engine in an isolated headless environment (no live session, no network, no filesystem, no subprocesses).
   - Never import `vitest`, `@jest/globals`, or `@playwright/test`.
2. **Standard Imports:**
   - Import `{ test, expect, describe, mock }` and `type { Engine }` from `'claude-code/testing'`.
   - Import mod event and element types (`type { On, McpConnectResult, McpToolResult, RenderElement }`) from `'claude-code'`.

---

## 2. Engine Hook Doubles Beneath the Plugin

1. **Test Hooks Stand in for the Engine:**
   - Hooks registered on `on` inside a test (`test('...', async ($, on) => { ... })`) sit **beneath** the plugin's hooks and act as the Claude Code engine answering the plugin's `$` and `next(e)` calls.
2. **Wrapping `$` Return Values in `{ value: ... }`:**
   - Every test hook answering a `$` call that returns a value must wrap its payload in `{ value: ... }`:
     - `on('session.turns', () => ({ value: 0 }))`
     - `on('session.id', () => ({ value: 'session-a' }))`
     - `on('command.register', (_$, e) => ({ value: { command: e.name } }))`
     - `on('store.get', (_$, e) => ({ value: store.get(e.key) }))`
     - `on('store.set', (_$, e) => { store.set(e.key, e.value); return { value: undefined } })`
     - `on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:yvoke:yvoke' } }))`
     - `on('mcp.call', (_$, e) => ({ value: { content: [{ type: 'text', text: '...' }], isError: false } }))`
3. **Event Pass-Through Hooks (`prompt.submit`, `tool.call`, `session.start`):**
   - Engine event handlers beneath the plugin return the event's settled result directly (not wrapped in `{ value }`):
     - `on('session.start', (_$, e) => ({ cwd: e.cwd }))`
     - `on('prompt.submit', (_$, e) => ({ text: e.text }))`
     - `on('tool.call', (_$, e) => ({ result: `ran ${e.tool}` }))`

---

## 3. Observing & Staging `$.state`, `$.store`, and Fail-Closed Paths

1. **Observing `$.state` and `$.store`:**
   - The test's own `$` (`Engine`) does not expose `$.state` or `$.store` directly.
   - Observe state transitions through observable plugin behaviour (UI rendering, tool allow/deny results, composed prompt sections) or by intercepting `state.get` / `state.set` and `store.get` / `store.set` on `on`.
2. **Staging Failed State Reads (`state.get` / `state.set`):**
   - If a test hook on `state.get` or `state.set` throws an exception, the `claude-code/testing` kit skips the throwing test hook and answers from its built-in memory store instead!
   - Therefore, to stage a failed `$.state.get` or `$.state.set` inside a plugin hook (to test fail-closed `.catch` or `try / catch` handling), return a malformed answer shape that the plugin cannot destructure, such as:
     ```ts
     on('state.get', (_$, e) =>
       e.key === 'loopStep' ? ({ value: null as never }) : next(e),
     )
     ```
3. **Testing Enforcing Hook `.catch` Paths:**
   - Every enforcing hook (`tool.call`, `prompt.submit`, `prompt.compose`, `agent.spawn`, `turn.step`) must have a test verifying both:
     - **Failure before `next(e)`**: e.g., throwing from `mcp.connect` or returning an unusable `state.get` answer triggers the hook's safe refusal (`{ deny }`, `{ drop }`, or refusal section).
     - **Failure after `next(e)` (`next.called === true`)**: throwing from the downstream engine `tool.call` or `prompt.submit` hook propagates the downstream error rather than falsely reporting a policy/setup refusal.

---

## 4. Deterministic Time & UI Surface Testing

1. **Deterministic Clock (`mock.clock(on)`):**
   - Always register `mock.clock(on)` in tests that exercise `$.clock.sleep` or `callYvoke` timeouts so 8-second server timeouts advance instantly without wall-clock sleeps.
2. **Mounting UI Surfaces (`terminal` and `desktop`):**
   - Loop every UI rendering and interaction test over `for (const surface of ['terminal', 'desktop'] as const)`.
   - Mount components via `await $.ui.mount({ plugin: 'yvoke', surface, component: 'AbovePrompt', props: ... })`.
   - Note that mounted `Text` elements do **not** retain `key` in the mounted tree; locate `Text` nodes by inspecting their `text` property, and locate interactive controls (`Select`, `Button`) by their `key` (`yvoke-area`, `yvoke-mode`, `yvoke-playbook`, `yvoke-profile`, `yvoke-retry`).

---

## 5. Suite Organization in `plugins/yvoke/tests/`

1. **Split Pure Rules from Engine Wiring:**
   - Place fast, pure decision and parsing tests in a `<feature>-rules` suite importing directly from `../src/<feature>`, and place full mod hook integration tests in a `<feature>` suite in `plugins/yvoke/tests/`.
2. **Starting a Yvoke Session in Engine Tests:**
   - Start a session the same way a user does: call `await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })` followed by `await $.command.run({ command: 'yvoke', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 80 } })`.
