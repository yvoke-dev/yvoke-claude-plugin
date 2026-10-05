# P0-01 Repository scaffold

**Release:** v1 · **Size:** S · **Type:** feature · **Status:** done

The empty plugin that every later task builds on: a marketplace at the repository root, one plugin
`yvoke` with a mod that does nothing yet, type-checking, one test, and the plugin rows of the verification
table in `AGENTS.md` filled in. Task entry: [plan.md, P0-01](../plan.md#phase-0--foundation-and-spikes).

Facts below were checked in a cloud session on **Claude Code 2.1.289** on 2026-10-05 with a throwaway
mod: `claude plugin validate` and `claude plugin test` both run with no session or sign-in; neither writes
the type declarations; loading the plugin with `claude --plugin-dir <dir> -p …` writes them to
`<dir>/.claude-plugin/types/` (with its own `.gitignore` of `*`) even when no model is reachable.

## Files that change

| File | Change |
| --- | --- |
| `.claude-plugin/marketplace.json` | New. Marketplace `yvoke`, one entry: plugin `yvoke`, `"source": "./plugins/yvoke"`. A relative source means a local-marketplace install reads the working copy itself, which P0-11 relies on. |
| `plugins/yvoke/.claude-plugin/plugin.json` | New. `name: "yvoke"` (D-05), `version: "0.1.0"`, description, author, homepage, repository, `"types": "./types/index.d.ts"`. No `userConfig` yet: the first setting, `serverUrl`, comes with `.mcp.json` in P0-11 (D-14). |
| `plugins/yvoke/hooks/hooks.json` | New. `{ "modules": ["./register.tsx"] }`. |
| `plugins/yvoke/hooks/register.tsx` | New. `export const register: Register = (on) => {}`, with one comment saying each feature adds one line here from its own `src/` module (plan.md, "Order of work"). Registers no hook, so every event reaches the engine unchanged. |
| `plugins/yvoke/types/index.d.ts` | New. The `$.state` contract: `PluginState.yvoke`, typed by an exported `YvokeState`, empty for now. A contract may export only types (`export {}` is refused). P1-10 adds the session flag. |
| `plugins/yvoke/tests/scaffold.test.ts` | New. The one test (see *Proof*). |
| `plugins/yvoke/tsconfig.json` | New. Extends the engine's `./.claude-plugin/types/tsconfig.json` and adds `src`, so editors type-check the mod. |
| `tsconfig.json` (root) | New. Extends `plugins/yvoke/tsconfig.json`, so `npx tsc --noEmit` from the root checks `hooks`, `src`, `types` and `tests`. |
| `package.json` (root) | New. `private`, `"type": "module"`, `typescript` (pinned) as the only dev dependency, and scripts `types`, `typecheck`, `test`, `validate`, `check`. No runtime dependencies: the mod cannot import npm packages. |
| `package-lock.json` | New, from `npm install`. |
| `scripts/lay-types.mjs` | New. Starts `claude --plugin-dir plugins/yvoke -p` with the model pointed at an unreachable address, waits until `plugins/yvoke/.claude-plugin/types/claude-code/index.d.ts` exists, then stops it. Exits 1 if the file does not appear within 60 s. Uses a throwaway `CLAUDE_CONFIG_DIR`, so it leaves nothing in the developer's history. Needs no sign-in, so CI (P0-02) can run it too. |
| `.gitignore` | Add `plugins/yvoke/.claude-plugin/types/` (design 4.2: never commit it). |
| `AGENTS.md` | Fill in the "from P0-01" rows: the exact commands, `npm run types` before `npx tsc --noEmit`, and what a healthy run prints, taken from real runs. |
| `README.md` | Status line: scaffold in place, nothing a user can see yet. Add a "Try it" line: `claude --plugin-dir plugins/yvoke`. |
| `docs/specs/packaging.md` (new) and `docs/specs/README.md` | At the end (finish-task): what is built, which is how the plugin is packaged and installed, and that the mod does nothing yet. |
| `docs/tasks/v1/plan.md` | At the end: tick P0-01 with the PR link. |

Not in this task: `.mcp.json` and `userConfig` (P0-11 adds `serverUrl`, P1-02 the Entra sign-in), CI
jobs for the plugin checks (P0-02), the contributor guide (P0-03), `src/` modules (later tasks).

## Order of work

1. **Test first.** Write `tests/scaffold.test.ts` and the two manifests, without `register.tsx`. Run
   `claude plugin test plugins/yvoke` and see it fail because the module is missing.
2. **Make it pass.** Add `hooks/hooks.json` and the empty `register.tsx`. Run the test: green. Then prove
   the test can fail: make `register.tsx` deny every tool call, see the test go red, restore the file from
   the saved copy, and check with `diff -q` that it is back.
3. **Marketplace.** Add `.claude-plugin/marketplace.json`. Run `claude plugin validate .` and
   `claude plugin validate plugins/yvoke`; both exit 0, and the plugin's `calls:` line names no `$.fs`,
   `$.process` or `$.http`. Fix any warning. Both pass `--strict`, so the verification table uses it.
4. **Types.** Add `types/index.d.ts`, `scripts/lay-types.mjs`, both `tsconfig.json` files, `package.json`
   and the `.gitignore` line. Run `npm install`, `npm run types`, `npx tsc --noEmit`: no output, exit 0.
   Then put a deliberate type error in `register.tsx`, see `tsc` fail, and restore.
5. **It loads.** Run `claude --plugin-dir plugins/yvoke --debug -p "hi"` and find the line in the debug
   log that says the `yvoke` module loaded with no error. Run `claude plugin marketplace add .` and
   `claude plugin install yvoke@yvoke` in a throwaway `HOME` to check the local-marketplace install P0-11
   will use.
6. **Documents.** Fill in `AGENTS.md`'s rows and the README from the real output of steps 2 to 5.
7. **Close out** with the `finish-task` skill: all checks, `docs/specs/packaging.md`, tick P0-01.

## Risks

- **Getting the type declarations without a session.** The engine writes them only when it loads the mod
  from a folder; `validate` and `test` do not. `scripts/lay-types.mjs` relies on a `-p` load writing them
  before any model call, which held on 2.1.289 with an unreachable model. If a later build stops doing
  that, `npm run types` fails loudly rather than type-checking against nothing. Rejected: committing a copy
  of the declarations (design 4.2 says never; it would go stale on the next release); copying them from the
  `plugin-authoring` skill's folder (its path changes with every process).
- **Marketplace name.** I picked `yvoke`, so the install name is `yvoke@yvoke`. The other choice,
  `yvoke-claude-plugin` (the repository name), reads longer in `/plugin` and settings. It is cheap to change
  now and costly after the pilot installs it, so say so in review if you prefer the other.
- **The test proves little on its own.** A no-op mod has no behaviour, so the test only shows that the
  plugin loads in the test engine and leaves a tool call untouched. That is the right floor: it is the
  rule every later hook must keep outside a Yvoke session (design 4.4), and it gives P0-02 a test that can
  be broken on purpose.
- **Early-access API.** Manifest fields or the testing kit may differ from what this plan assumes. The
  declarations win (design 4.2); any difference goes into this plan in the same commit.

## Proof

- **Done when** (plan.md): `claude plugin validate .` and `claude plugin validate plugins/yvoke` pass, and
  the plugin loads with `claude --plugin-dir plugins/yvoke`. Shown by steps 3 and 5, with their output in
  the pull request.
- `tests/scaffold.test.ts`: "the plugin loads and leaves a tool call unchanged": a test hook beneath the
  plugin answers `tool.call`, the test makes a call through `$.tool.call`, and asserts that the answer arrives
  as given and the hook saw the tool name and arguments as sent (they sit flat on `e`, beside `tool`).
  Seen failing in step 1 (no module) and in step 2 against two mutants: one denying every call, one
  rewriting the arguments.
- Every row of `AGENTS.md`'s verification table, run and shown in the pull request:
  `node scripts/check-docs.mjs`, `claude plugin validate .`, `claude plugin validate plugins/yvoke`,
  `npm run typecheck`, `claude plugin test plugins/yvoke`. `npm run check` runs all of them.
