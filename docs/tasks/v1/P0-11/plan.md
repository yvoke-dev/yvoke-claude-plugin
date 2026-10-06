# P0-11 Dev environment

**Release:** v1 · **Size:** S · **Type:** feature · **Status:** done

A developer installs the working copy in their own Claude Code and points it at a yvoke-web running on
their machine. Task entry: [plan.md, P0-11](../plan.md#phase-0--foundation-and-spikes).

## What was checked first

Checked in a cloud session on **Claude Code 2.1.291** on 2026-10-06, with a throwaway copy of the plugin
installed from a local marketplace and a small mock MCP server that logs the headers it receives.

- `"url": "${user_config.serverUrl}"` in `.mcp.json` works. The value is set with
  `claude plugin install yvoke@yvoke --config serverUrl=…`, with `/plugin configure yvoke@yvoke` in a
  session, or by piping JSON to `claude plugin configure yvoke@yvoke --values-stdin`. It is stored under
  `pluginConfigs."yvoke@yvoke".options` in the user's `settings.json`, never in the repository.
  `claude mcp list` then shows `plugin:yvoke:yvoke: http://127.0.0.1:8099/mcp (HTTP) - √ Connected`.
- **A dev token cannot sit next to the Entra sign-in in `.mcp.json`.** As soon as `headers.Authorization`
  is present, Claude Code turns the OAuth sign-in off, even when the value is empty: *"OAuth fallback is
  disabled when headers.Authorization is set."* Header names are not substituted from `userConfig`, so the
  header cannot be switched on by a setting either. A static header would therefore break P1-02 for every
  user.
- A `headersHelper` script could add the header only when a dev value is set, and an empty answer leaves
  the OAuth sign-in working. But Claude Code never passes plugin settings to the helper (the loader refuses
  `${user_config.*}` in it, and the server's `env` block does not reach it), and it strips variables whose
  names look like credentials (`YVOKE_DEV_TOKEN` did not arrive; `YVOKE_DEV_USER` did). So the value would
  have to be a shell variable, not a setting. The helper would also run on every user's machine on every
  connection, Windows included, for a dev-only feature, and Claude Code has managed settings that let an
  organization turn off commands declared by a marketplace. On a Team or Enterprise plan (D-01) that would
  switch off the sign-in for everyone.
- yvoke-web's own tests pin today's mock-mode behaviour: `McpSecurityGatingIT` runs with
  `app.security.mock=true` and expects `/mcp` with no token to answer 401 with the `WWW-Authenticate`
  header that starts the OAuth sign-in, and expects a browser session not to reach `/mcp` (SEC-13). That
  behaviour has to stay as it is.
- **A header with another name works.** `"headers": { "X-Yvoke-Dev-Mode": "${user_config.devMode}" }`
  reaches the server as `"true"` or `"false"` (a boolean setting is substituted as text), and with no `Authorization` header Claude Code still
  starts the OAuth sign-in when the server answers 401. So a dev value can travel next to the Entra
  sign-in, as long as it is not called `Authorization`.

## Decided

Eduard, 2026-10-06: the plugin has a switch. Off, it connects to the server at `serverUrl` with the
normal sign-in, whatever environment that is. On, it sends a dev-mode header that only a local yvoke-web in
mock mode accepts in place of a sign-in.

- **Plugin:** two settings. `serverUrl` picks the environment. `devMode` (on/off, off by default) is the
  switch: the plugin always sends `X-Yvoke-Dev-Mode: ${user_config.devMode}`, so the header reads `true`
  only when the switch is on. Off means the normal sign-in, untouched.
- **yvoke-web:** with its existing `app.security.mock` (`APP_SECURITY_MOCK`, refused outside the `dev`,
  `local` and `test` profiles) on, an MCP request carrying `X-Yvoke-Dev-Mode: true` is the mock MCP user
  yvoke-web already builds for any bearer token. With mock off, the header is ignored and only an Entra
  token works. A request with no token still answers 401
  in both modes, so `McpSecurityGatingIT` stays as it is.

## Files that change

In **yvoke-claude-plugin** (branch `claude/p0-11-dev-setup-3i393i`):

| File | Change |
| --- | --- |
| `plugins/yvoke/.claude-plugin/plugin.json` | Add `userConfig.serverUrl`: `type: "string"`, title "Yvoke server URL", a description naming the local form `http://localhost:8080/mcp`, not `required` (see *Risks*). **No default yet**: the production URL is not in any repository, and nothing works in production before P1-02 (Entra sign-in) anyway. P1-02 adds the production default (D-14). Add `userConfig.devMode`: `type: "boolean"`, title "Dev mode (local yvoke-web)", default `false`, a description saying it works only against a local yvoke-web in mock mode and that off means the normal sign-in. |
| `plugins/yvoke/.mcp.json` | New. Server `yvoke` (D-05), `"type": "http"`, `"url": "${user_config.serverUrl}"`, `"headers": { "X-Yvoke-Dev-Mode": "${user_config.devMode}" }`. Never an `Authorization` header. |
| `README.md` | Replace the "Try it" line with a **Use it locally** section, step by step: start yvoke-web in mock mode (`./redeploy.sh`, `APP_SECURITY_MOCK=true`); add the clone as a marketplace (`claude plugin marketplace add <clone>/`; a bare `.` is refused); install with `--config serverUrl=http://localhost:8080/mcp --config devMode=true`; check with `claude mcp list` and `/mcp`; pick up edits with `/reload-plugins`; switch to another environment with `/plugin configure yvoke@yvoke` (new `serverUrl`, `devMode` off, normal sign-in); and the Claude Code version it was checked on. P0-03's contributor guide links to it. |
| `docs/specs/packaging.md` | At the end (finish-task): the plugin's two settings and its one MCP server. |
| `docs/tasks/v1/plan.md` | P0-11's sign-in bullet: the `devMode` switch, the `X-Yvoke-Dev-Mode` header and why not `Authorization`; P1-02's note that it adds the production default and must keep `Authorization` out of `.mcp.json`; tick P0-11. |
| `docs/tasks/v1/design.md` | D-14: record the decision above and the `headers.Authorization` finding. |

In **yvoke-web** (same branch name, its own PR, linked from this one):

| File | Change |
| --- | --- |
| `src/main/java/de/palsoftware/yvoke/shared/security/SecurityConfig.java` | On the `/mcp` chain, when `mockAuth` is on, a `BearerTokenResolver` that returns the request's bearer token, or else a fixed placeholder when `X-Yvoke-Dev-Mode` is exactly `true`, or else nothing (401, as today). The mock `JwtDecoder` turns the token into the mock MCP user, as today. With mock off, the default resolver is used and the header is never read. |
| `src/it/java/de/palsoftware/yvoke/shared/security/McpSecurityGatingIT.java` | New tests in the existing mock-mode class (no new Spring context): `X-Yvoke-Dev-Mode: true` is not 401; `false` is 401 with the `WWW-Authenticate` header. Existing tests unchanged. |
| `src/it/java/de/palsoftware/yvoke/shared/security/SecurityGatingIT.java` (mock off) | New test: `X-Yvoke-Dev-Mode: true` with mock off is 401. |
| `spec/07_using_the_assistant_from_other_tools.md` | One line under local development: in mock mode, MCP accepts the plugin's dev-mode header. |

## Order of work

1. **yvoke-web, tests first.** Add the three IT cases. Run them: the dev-token case is red (401).
2. **yvoke-web, make it pass.** Add the resolver. All green, existing tests unchanged. Prove the mock-off
   test pins the gate: make the resolver read the header with mock off, see it go red, restore from the
   saved copy and `diff -q`. Run the full `./mvnw verify -Pit-tests`, open the yvoke-web PR.
3. **Plugin.** Add the two settings and `.mcp.json`. Run `claude plugin validate --strict .` and
   `claude plugin validate --strict plugins/yvoke`. There is no mod behaviour, so no mod test; the
   existing `scaffold.test.ts` must stay green.
4. **Check it in the cloud.** Install the working copy from a local marketplace in a throwaway
   `CLAUDE_CONFIG_DIR` against a mock MCP server that answers 401 without the dev header. Show
   `claude mcp list` reporting `√ Connected` with `devMode` on, and the OAuth sign-in starting with it
   off. Paste the output in the PR.
5. **Documents.** The README section, plan.md and D-14 notes. Then follow the README section word for word in a fresh `CLAUDE_CONFIG_DIR` against the mock server, so the steps are known to work as written.
6. **Eduard's machine** (he runs these himself; the cloud cannot reach his yvoke-web):
   1. Start yvoke-web with `./redeploy.sh` (Compose sets the `local` profile; `.env` has
      `APP_SECURITY_MOCK=true`), from yvoke-web `main` (#6 merged).
   2. `claude plugin marketplace add <path to the clone>/`, then
      `claude plugin install yvoke@yvoke --config serverUrl=http://localhost:8080/mcp --config devMode=true`.
   3. `claude mcp list`: `plugin:yvoke:yvoke … √ Connected`.
   4. In a new session, `/mcp` lists the `yvoke` tools; ask *"Use search_corpus to find …"* and the answer
      cites a chunk from the local corpus.
7. **Close out** with `finish-task` once Eduard reports step 6.

## Risks

- **Claude Code changes how settings reach `.mcp.json`.** Checked on 2.1.291; Eduard's version may differ.
  Step 6 shows it on his build, and the README names the version it was checked on.
- **A setting with no default.** Until P1-02, a user who installs without `--config` sees the `yvoke`
  server fail with *"URL is unset or invalid — open /plugin manage and configure yvoke options"*. The mod
  still loads. Found while building: marking `serverUrl` `required: true` stops the whole hooks module
  loading while the value is unset (*"options do not fit plugin.json userConfig: Yvoke server URL is
  required but not provided"*), which also failed `claude plugin test`. So it is optional. Rejected: a
  fake default URL, which would fail later and less clearly.
- **A public dev switch.** The header is in a public repository, so it must only ever work in mock mode,
  which already trusts any bearer token and is refused outside a dev profile. The mock-off test pins
  that the header is ignored in production.
- **Rejected: a `headersHelper` script** that adds `Authorization` only in dev. Plugin settings never
  reach it, it would run on every user's machine on every connection, and an organization's managed
  settings can turn it off, which would also stop the Entra sign-in.
- **Rejected: accepting a request with no token in mock mode.** It breaks `McpSecurityGatingIT` and
  removes the only local way to see the OAuth start.
- **Rejected: a second server entry such as `yvoke-dev`.** It would change every tool name (D-05) and the
  name the mod calls.
- **Rejected: a static `Authorization` header now, sorted out in P1-02.** It turns the OAuth sign-in off,
  so P1-02 would have to undo it, and production would break if it shipped.

## Proof

- yvoke-web: the three new IT cases, the unchanged existing ones, and a green `./mvnw verify -Pit-tests`.
- Plugin: the checks in [AGENTS.md](../../../../AGENTS.md#verification), `npm run check`, all passing.
- Done when (Eduard's machine, step 6): `claude mcp list` shows the local server connected, and a session
  with the plugin lists its tools and answers from `search_corpus`.

**Result, 2026-10-06 (Eduard's machine, Claude Code 2.1.291):** with yvoke-web `main` in mock mode, `/mcp`
answered 401 with no header and 400 with `X-Yvoke-Dev-Mode: true`. The marketplace add and the install
with both settings succeeded. `claude mcp list` showed
`plugin:yvoke:yvoke: http://localhost:8080/mcp (HTTP) - ✔ Connected`. In a session, with the `claude.ai yvoke`
connector to production disabled, `search_corpus` answered with cited chunks, and the requests showed in
the local yvoke-web logs.
