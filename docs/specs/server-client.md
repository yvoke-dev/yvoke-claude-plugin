# Server client

## Behaviour

- Every call the mod makes to the Yvoke server goes through one function, `callYvoke(io, tool, args)`.
  It answers `{ ok: true, text }` or `{ ok: false, error }`, and never throws.
- It finds the server with `$.mcp.connect('yvoke')`, the key in the plugin's own `.mcp.json`, and calls
  it under the name the engine gives back (usually `plugin:yvoke:yvoke`, or the name the session already
  runs the same server under).
- Every `error` starts with `Yvoke Backend: `, exactly once, and is ready to show to the user:
  - not connected: the engine's own sentence; when the server needs a sign-in, followed by
    "Run /mcp and sign in to yvoke.";
  - no answer within 8 seconds (or the caller's `timeoutMs`), counting from before the connect:
    "the server did not answer `<tool>` within `<n>` seconds.";
  - a result with the error flag, **or** whose text starts with `ERROR:` or `Error:` (yvoke-web's tools
    report their own failures that way, without the flag): the server's text as it is, for example
    `Yvoke Backend: ERROR: playbook 'x' not found.`;
  - a call that rejects: its message;
  - a failure with no text: "the `<tool>` call failed."
- `text` is the result's text blocks joined by a blank line, each unwrapped when it is a JSON string.
- There is no cache and no retry: a server that is down gives a clear error (U11).
- The client logs nothing.
- It registers no hook. A feature imports it and builds `io` in its own hook, because the engine does not
  let `$` cross an import:

  ```ts
  const io: ServerIo = {
    connect: (server) => $.mcp.connect(server),
    call: (server, tool, args) => $.mcp.call(server, tool, args),
    sleep: (ms, signal) => $.clock.sleep(ms, { signal }),
  }
  ```

## Interfaces

| Piece | File |
| --- | --- |
| `callYvoke`, `ServerIo`, `ServerAnswer`, `SERVER`, `TIMEOUT_MS` | `plugins/yvoke/src/server.ts` |
| Engine calls a caller makes for it | `$.mcp.connect`, `$.mcp.call`, `$.clock.sleep` |
| Server tools it is used with | yvoke-web's MCP tools, for example `get_system_prompt`, `list_playbooks`, `get_playbook` |

## Tests

- `plugins/yvoke/tests/server.test.ts`: success, the server name from `connect`, not connected (with and
  without sign-in), error flag, `ERROR:` body, timeout (call and connect, default and caller's own),
  rejected calls, and that the timer ends once the server answers. The tests hand in plain functions as
  `io`: a test's own `$` cannot call `$.mcp.call`. The first feature that calls the server tests the
  wiring through its own hook, with `on('mcp.call', …)` and `mock.clock`.

## History

- P1-03 ([#13](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/13)): the client.
