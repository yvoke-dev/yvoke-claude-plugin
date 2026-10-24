# Testing Guide (`claude-code/testing`)

## Test Runner & Verification Commands

- **Unit / Mod Tests** (the unit test script in `.lintel/scripts/`):
  - Runs `claude plugin validate --strict plugins/yvoke`, `./node_modules/.bin/tsc --noEmit`, and `claude plugin test plugins/yvoke` with an isolated temporary `CLAUDE_CONFIG_DIR`.
  - Executes all test suites under `plugins/yvoke/tests/` against the real Claude Code mod engine in headless mode (no network, no filesystem, no live session). The engine here is a kit: it stands in for the parts of Claude Code that a test does not stage (see below).
- **Integration Tests** (`.lintel/scripts/test-integration.sh`, `npm run test:integration`):
  - Run the plugin in a **real** headless Claude Code session (`claude -p`, stream-json) from `tests/integration/*.test.mjs`: plain Node 22 with `node:test` and no dependencies. They live outside `plugins/yvoke/tests/`, so `claude plugin test` never picks them up.
  - Install the plugin from the working tree into a throwaway `CLAUDE_CONFIG_DIR`, point it at a stub Yvoke MCP server and point Claude Code at a fake Anthropic Messages API, both on `127.0.0.1` ports the system picks. They check what each side was sent.
  - `CLAUDE_BIN` names another Claude Code binary (default `claude`), so one machine can check 2.1.287 and the latest. A run takes about ten seconds.
  - Exit codes follow the group contract: 0 passed, 1 a test failed, 2 the test could not run (no `node` or `claude`, or the plugin would not install), 3 no tests.
- **Build & Static Checks** (`.lintel/scripts/build.sh`):
  1. `node scripts/check-docs.mjs` — verifies Markdown links and `#anchor` targets across repository docs, `.lintel/specs/`, and `.lintel/steering/`.
  2. `claude plugin validate --strict .` — validates `.claude-plugin/marketplace.json`.
  3. `claude plugin validate --strict plugins/yvoke` — validates the plugin and mod, and verifies the `calls:` line contains no `$.fs`, `$.process`, or `$.http`.
  4. `./node_modules/.bin/tsc --noEmit` — type-checks `hooks`, `src`, `types`, and `tests` against `plugins/yvoke/.claude-plugin/types/claude-code/index.d.ts`.

## Selecting Test Groups in `design.md` (`## Test outline`)

Most scenarios belong in the **`unit`** group: pure rule tests (`-rules` suites) and engine-wired hook tests (hook pipelines, mocked MCP server calls, mounted `terminal`/`desktop` UI surfaces), all run by `claude plugin test plugins/yvoke`.

**The kit is not the engine.** A unit test runs the mod against `claude-code/testing`, which answers every `$` call the test does not stage. It differs from a live session where hooks meet each other. The known case: in a live session `$.mcp.call` is routed through the plugin's own `tool.call` hook (the call carries `next.origin.plugin`), and the kit does not do that. So a unit test can prove each hook right and still miss that one hook refuses another's call; the first live run found exactly that, every Yvoke question dropped.

Put a scenario in **`integration`** when it depends on how the real engine, the MCP connection or the model request behave, which the kit cannot show:
- a `$.mcp.call`, `$.tool.call` or other `$` call that crosses into another hook of the plugin (the gate in `policy.ts` against `/yvoke` and the first question);
- what reaches the Yvoke server (tool names, the `X-Yvoke-Dev-Mode` header) or the model (the tools offered, a refusal coming back as a `tool_result`);
- the mod staying out of a session that did not start with `/yvoke`.

Keep everything else in `unit`. An integration test costs a process start and has fewer ways to stage a failure; it is there for the boundary, not for rule coverage.

What a headless run cannot show: `claude -p` composes the system prompt once, at start-up, before the first question's `prompt.submit` loads the instructions (checked with a plain mod whose `prompt.compose` adds a section that depends on state set in `prompt.submit`: every request of the run carried the section from before the first prompt). So the instructions in the model's system prompt are not checkable there, and nothing that draws a UI is either. The test for the composed section is marked `todo` in `tests/integration/live-session.test.mjs` until a run can see it.

When authoring `shared/phase-<N>/design.md` (`## Test outline`):
- Assign each requirement scenario to `unit` (`- R<n> unit: <scenario>`) or, for the cases above, `integration` (`- R<n> integration: <scenario>`).
- `integration` is a required group, so the outline names it: either scenarios, or an exemption that says why the unit suite is enough:
  - `- integration: none: Pure rule change; no hook, MCP call or model request changes`
- `e2e` has no runner. Exempt it:
  - `- e2e: none: Integration covers the live session; there is no UI test harness`

## Writing Tests with `claude-code/testing`

1. **Imports**:
   - Import `test`, `expect`, `describe`, and `mock` from `'claude-code/testing'`.
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
