# Server client

## Behaviour

- Every call the mod makes to the Yvoke server goes through one function, `callYvoke(io, tool, args, options)`.
  It answers `{ ok: true, text }` or `{ ok: false, error }`, and never throws.
- It finds the server with `$.mcp.connect('yvoke')`, the key in the plugin's own `.mcp.json`, and calls
  it under the name the engine gives back (usually `plugin:yvoke:yvoke`, or the name the session already
  runs the same server under).
- Every `error` starts with `Yvoke Backend: `, exactly once, and is ready to show to the user:
  - not connected: the engine's own sentence; when the server needs a sign-in, followed by
    "Run /mcp and sign in to yvoke.";
  - no answer within 8 seconds (or the caller's `timeoutMs`), counting from before the connect:
    "the server did not answer `<tool>` within `<n>` seconds.";
  - a result with the error flag, **or** whose combined text starts with `ERROR:` or `Error:`
    (`/^\s*(ERROR|Error):/`, checking only the start of the combined text, allowing leading whitespace,
    and after unwrapping JSON-encoded strings, so an `ERROR:` on a later line is returned as normal answer
    text; yvoke-web's tools report their own failures that way, without the flag): the server's text as it
    is, for example `Yvoke Backend: ERROR: playbook 'x' not found.`;
  - a call that rejects: its message;
  - a failure with no text: "the `<tool>` call failed."
- `text` (`readText`) skips non-`text` content blocks (for example `image`) and empty text blocks when
  joining the result's text blocks with `\n\n`, each unwrapped when it is a JSON string.
- There is no cache and no retry: a server that is down gives a clear error.
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

The client-only tools (`get_system_prompt`, `list_areas`, `list_playbooks`, `get_playbook`) are served
to MCP clients only. yvoke-web's own assistant never gets them, and its playbook editor does not offer them.
