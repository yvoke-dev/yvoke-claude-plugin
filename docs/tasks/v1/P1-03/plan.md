# P1-03 Server client

**Release:** v1 · **Size:** S · **Type:** feature · **Status:** in progress

**Done when** (from [plan.md](../plan.md)): tests cover not connected, timeout, error flag, `ERROR:` body
and success.

Facts below were checked on Claude Code 2.1.291 on 2026-10-06, against the generated declarations and with
throwaway tests that are not committed.

## What the module does

`plugins/yvoke/src/server.ts` is the one place where the mod talks to the Yvoke server. A feature asks it
for one tool call and gets back either the answer's text or an error message ready to show:

```ts
callYvoke(io, 'get_playbook', { name }) // → { ok: true, text } | { ok: false, error: 'Yvoke Backend: …' }
```

1. **Find the server.** It calls `$.mcp.connect('yvoke')`. `yvoke` is the key in our own `.mcp.json`
   (D-03, D-05), so it is known at build time. The engine answers with the name `$.mcp.call` takes
   (usually `plugin:yvoke:yvoke`), or with the name the session already runs the same server under. So the
   module never hard-codes the call name, and nothing waits for spike P0-04.
2. **Not connected.** If `connect` says the server is not connected, the call fails with the engine's own
   sentence. When the reason is `auth`, it adds: "Run /mcp and sign in to yvoke."
3. **Call with a timeout.** It races `$.mcp.call` against `$.clock.sleep`. The default is **8 seconds**,
   and a caller can pass its own. yvoke-desktop used 12 seconds, but here a `$.clock` wait counts against a
   hook's 10-second budget, and a hook that outruns it is skipped silently. 8 seconds leaves room for the
   rest of the hook.
4. **Read the answer.** It joins the `text` blocks (unwrapping a body that is a JSON string, as the desktop
   does). The call failed if `isError` is set **or** the text starts with `ERROR:` (or `Error:`), because
   yvoke-web's tools report their own failures in the body without the flag (`McpToolUtils.toolError`).
   This is the desktop's `McpPrompts.callGetSection` rule.
5. **Attribute failures.** Every failure starts with `Yvoke Backend: `, never twice, as the desktop's
   `tagAttributedError` does. An empty failure gets a sentence naming the tool. The server's text is kept as
   it is, so an unknown playbook reads `Yvoke Backend: ERROR: playbook 'x' not found.`, as in the desktop.
6. **No fallback.** There is no cache and no retry. A server that is down gives a clear error (U11).

The module never logs anything, so neither tokens nor answers can reach `$.ui.log`.

### Why `io` and not `$`

The engine's scan refuses a hooks module that passes `$` into a function from another file: "`$` is followed
only into a function declared in this same file, never across an import". So `server.ts` cannot take `$`.
It takes three small functions instead, and each caller writes them where `$` lives:

```ts
const io = {
  connect: (server: string) => $.mcp.connect(server),
  call: (server: string, tool: string, args: Record<string, unknown>) => $.mcp.call(server, tool, args),
  sleep: (ms: number) => $.clock.sleep(ms),
}
```

The scan accepts this and lists `$.mcp.call`, `$.mcp.connect` and `$.clock.sleep` under `calls:` (tried with
a throwaway hook). No `$.fs`, `$.process` or `$.http`.

The closures are plain arrow functions passed as arguments inside a hook that is itself a function
literal, so the two other scan refusals P1-10 found (a hook built by a wrapper, an imported `$.state`
reference) do not apply: `server.ts` builds no hook and reads no state.

## Files that change

| File | Change |
| --- | --- |
| `plugins/yvoke/src/server.ts` (new) | `SERVER = 'yvoke'`, `TIMEOUT_MS = 8000`, the `ServerIo` type, `callYvoke`, and the small helpers it needs (reading the text, attributing an error). |
| `plugins/yvoke/tests/server.test.ts` (new) | The tests below. |
| `docs/specs/server-client.md` (new) and `docs/specs/README.md` | What the client does, once merged. |
| `docs/tasks/v1/plan.md` | Tick P1-03. Reword its first bullet: the call name comes from `$.mcp.connect`, not from P0-04. P2-03 can take the tool prefix from the same answer. |
| `docs/tasks/v1/design.md` section 4.3 | The "Server calls" row points at `callYvoke` and the `io` pattern. The scan rules themselves (`$` never crosses an import, no wrapper-built hooks, `$.state` references written in the same file) go into section 4.4 with P1-10 ([#12](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/12)), which found the same rule; this task does not edit 4.4, so the two pull requests do not collide. |

`hooks/register.tsx` does not change: the module registers no hook. The first feature that calls the server
(P1-07, P1-10 or P1-12's band) imports it.

## Order of work

Each step starts with a test that is seen failing, then the code that makes it pass.

1. **Success.** `connect` answers `plugin:yvoke:yvoke`; `call` returns one text block. The result is
   `{ ok: true, text }`, and `call` was asked for that server name, tool and arguments.
2. **Another name.** `connect` answers a different server name (the same server under another name); the
   call goes to that name.
3. **Not connected.** `connect` answers `isConnected: false` with reason `failed`, then with `auth`. The
   error starts with `Yvoke Backend: `, carries the engine's sentence, adds the sign-in hint only for `auth`,
   and `call` is never made.
4. **Error flag.** `isError: true` with a message: the error is `Yvoke Backend: <message>`. With no text:
   a sentence naming the tool.
5. **`ERROR:` body.** No flag, body `ERROR: playbook 'x' not found.`: a failure. Also `Error:` and a body
   that is a JSON string holding `ERROR: …`. A body that only *contains* "ERROR:" later on is a success.
6. **Timeout.** `call` never answers and `sleep` resolves: a timeout error naming the tool and the
   seconds. A caller's own `timeoutMs` reaches `sleep`.
7. **A call that rejects** (the connection drops): `Yvoke Backend: <message>`; a message already starting
   with `Yvoke Backend:` is not prefixed twice.
8. Spec, design notes and plan.md entry; run `finish-task`.

## Tests run without the engine's `$`, deliberately

A test's own `$` has no `mcp` noun, and a test's hook may not call `$.mcp.call` ("its hooks module does not
call it"). So the only way to drive `on('mcp.call', …)` is through a hook of the plugin, and P1-03 has none.
Because `server.ts` takes `io`, the tests hand it plain functions instead: a fake `call` that answers, fails
or never answers, and a fake `sleep` the test resolves. That covers every case in **Done when**.

The first feature that calls the server tests the wiring end to end: its hook builds `io` from `$`, and its
test stubs the server with `on('mcp.call', …)` and the clock with `mock.clock`. I tried that path with a
throwaway hook: the stub received `{ server, tool, args }`, and `mock.clock` drove the timeout.

## Risks

- **What `$.mcp.call` does when the connection drops between `connect` and `call`** is not documented. The
  module treats a rejection as a failure (step 7), so either way the user gets a `Yvoke Backend:` message.
  P0-04 checks the real wording on a machine.
- **Whether `connect` alone can trigger a sign-in.** It is called only when a feature needs the server,
  which happens only in a Yvoke session, so a normal Claude Code session is never affected.
- **8 seconds may be short** for a slow server. It is one constant; callers that need longer pass
  `timeoutMs`, within the hook budget.
- **Alternatives rejected.**
  - Hard-coding `plugin:yvoke:yvoke`: depends on P0-04 and breaks if the user also runs the same server
    under another name. `connect` answers both.
  - An `on('mcp.call')` hook in the plugin that wraps every call to the server: tests cannot raise
    `mcp.call`, and it would also change calls made by other plugins.
  - Throwing on failure instead of returning `{ ok: false, error }`: a throw inside a hook is caught by its
    fail-closed `.catch`, which shows a generic reason and loses the server's message.
  - Retrying or caching: ruled out by "no cached fallback" (U11).

## Proof

- `plugins/yvoke/tests/server.test.ts`: one test per case in **Order of work**, each seen failing first.
- The verification table in `AGENTS.md`: `npm run check` (docs, both validates with a `calls:` line free of
  `$.fs`, `$.process` and `$.http`, types, tests).
