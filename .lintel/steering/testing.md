# Testing Guide (`claude-code/testing`)

## Test Runner & Verification Commands

- **Unit / Mod Tests** (`.lintel/scripts/test-unit.sh`):
  - Runs `claude plugin test plugins/yvoke` with an isolated temporary `CLAUDE_CONFIG_DIR`.
  - Executes all test suites under `plugins/yvoke/tests/` against the real Claude Code mod engine in headless mode (no network, no filesystem, no live session).
- **Build & Static Checks** (`.lintel/scripts/build.sh`):
  1. `node scripts/check-docs.mjs` — verifies Markdown links, heading anchors.
  2. `claude plugin validate --strict .` — validates `.claude-plugin/marketplace.json`.
  3. `claude plugin validate --strict plugins/yvoke` — validates the plugin and mod, and verifies the `calls:` line contains no `$.fs`, `$.process`, or `$.http`.
  4. `./node_modules/.bin/tsc --noEmit` — type-checks `hooks`, `src`, `types`, and `tests` against `plugins/yvoke/.claude-plugin/types/claude-code/index.d.ts`.

## Writing Tests with `claude-code/testing`

1. **Imports**:
   - Import `test`, `expect`, and `mock` from `'claude-code/testing'`.
2. **How Test Hooks Sit Beneath the Plugin**:
   - Hooks registered inside a test sit *beneath* the plugin and stand in for the Claude Code engine.
   - An answer to a `$` call that returns a value must be wrapped in `{ value: ... }`:
     - `on('session.turns', () => ({ value: 0 }))`
     - `on('state.get', () => ({ value: { value: true, version: 1 } }))`
     - `on('mcp.call', () => ({ value: { content: [{ type: 'text', text: '...' }], isError: false } }))`
3. **Observing & Stubbing `$.state` and `$.store`**:
   - A test's own `$` does not expose `$.state` or `$.store` directly. Observe them through behaviour or stub `state.get` / `state.set` and `store.get` / `store.set` beneath the plugin.
   - A stubbed hook that throws in the test kit is skipped by the kit (which then answers instead). To stage a failed state/store read, return an invalid shape that the plugin cannot use so the plugin's fail-closed path triggers.
4. **Testing Fail-Closed `.catch` Paths**:
   - Every enforcing hook must have a test verifying that an unexpected failure fails closed (denies the tool call, drops the prompt, or replaces the composed system prompt with the refusal section).
5. **UI Surface Coverage**:
   - Loop UI rendering and interaction tests over both surfaces: `for (const surface of ['terminal', 'desktop'] as const)`.
6. **Pure Rule Modules vs Engine Wiring**:
   - Pure helper/rule functions can be tested directly in `-rules` test suites alongside full engine-wired hook tests in `plugins/yvoke/tests/`.
