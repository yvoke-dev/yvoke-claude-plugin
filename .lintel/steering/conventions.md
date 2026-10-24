# Coding & Product Conventions

## Core Rules

1. **Public Repository Security**:
   - This repository is public. Never commit playbook text, secrets, tokens, customer data, or internal hostnames.
   - Never log tokens or full answers (`$.ui.log` output ends up in support tickets).
2. **Forbidden Mod APIs**:
   - The mod must **never** call `$.fs`, `$.process`, or `$.http`.
   - `claude plugin validate --strict plugins/yvoke` prints a `calls:` line; `.lintel/scripts/build.sh` and CI fail if `calls:` names `$.fs`, `$.process`, or `$.http`.
   - Reach `yvoke-web` only through `$.mcp.call` (via `callYvoke` in `plugins/yvoke/src/server.ts`).
3. **Fail Closed Explicitly**:
   - A hook that throws, returns an invalid shape, or exceeds its 10-second budget is skipped by the Claude Code engine.
   - Every enforcing hook (`tool.call`, `prompt.submit`, `prompt.compose`, `agent.spawn`) **must** attach a `.catch(($, e, next) => ...)` returning the safe refusal (`{ deny }`, `{ drop }`, or refusal section). Once `next.called` is true, pass on `next(e)`.
4. **One Unmatched Hook Per Event**:
   - A second `on(event, hook)` without a matcher stops the mod from loading (`registered twice without a matcher`).
   - All `tool.call` rules live inside the single `tool.call` gate in `plugins/yvoke/src/policy.ts` in explicit order (turn limits first, then tool policy).
   - For other events where another module already registers an unmatched hook, register with a catch-all matcher (e.g. `{ source: /^(clear|resume|fork)$/ }` or `{ text: /^/ }`).
5. **Yvoke Session Guard (`yvokeSession`)**:
   - Outside a session started with `/yvoke`, the mod must pass every event through unchanged (`return next(e)`).
   - Every enforcing hook must open with these two lines written directly in its own file:
     ```ts
     const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
     if (isYvoke !== true) return next(e)
     ```
   - Never extract this into an imported helper that takes `$`: the Claude Code engine refuses a module that passes `$` to a function imported from another file, builds a hook via a wrapper, or imports a `$.state` reference from another file.
6. **Immutability of Event Payloads**:
   - Hook event objects `e` are frozen. Rewrite inputs with `next({ ...e, updatedField })`.
7. **Server Error Prefixing**:
   - Every error from `yvoke-web` surfaced to the user must start with `Yvoke Backend: ` (handled by `callYvoke` in `plugins/yvoke/src/server.ts`).
8. **Documentation & Link Hygiene**:
   - Every relative Markdown link and `#anchor` in repository docs is verified by `node scripts/check-docs.mjs` during `build.sh`.
   - Cite a decision by its ID (`D-13`), which must have an entry in [decisions.md](decisions.md). Cite a spec or PLAN.md, never an old task ID; `check-docs.mjs` fails on both.
   - Write plainly: short sentences, active voice, no unnecessary jargon.
