# P0-11 Dev environment

**Release:** v1 · **Size:** S · **Type:** feature · **Status:** planned

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

- **A header with another name works.** `"headers": { "X-Yvoke-Dev-Token": "${user_config.devToken}" }`
  reaches the server with the setting's value, and with no `Authorization` header Claude Code still
  starts the OAuth sign-in when the server answers 401. So a dev token can travel next to the Entra
  sign-in, as long as it is not called `Authorization`.

## Decided

Eduard, 2026-10-06: the dev token is a hard-coded string, and a switch turns dev (mock) mode on and off.

- **Plugin:** a setting `devToken`, empty by default. A developer sets it to the hard-coded value
  `yvoke-dev`, and the plugin sends it as `X-Yvoke-Dev-Token`. Empty means off: the server ignores the
  header.
- **yvoke-web:** the switch is the existing `app.security.mock` (`APP_SECURITY_MOCK`), which yvoke-web
  already refuses outside the `dev`, `local` and `test` profiles. With it on, an MCP request carrying
  `X-Yvoke-Dev-Token: yvoke-dev` is the mock MCP user yvoke-web already builds for any bearer token. With
  it off, the header is ignored and only an Entra token works. A request with no token still answers 401
  in both modes, so `McpSecurityGatingIT` stays as it is.

## Files that change

In **yvoke-claude-plugin** (branch `claude/p0-11-dev-setup-3i393i`):

| File | Change |
| --- | --- |
| `plugins/yvoke/.claude-plugin/plugin.json` | Add `userConfig.serverUrl`: `type: "string"`, title "Yvoke server URL", a description naming the local form `http://localhost:8080/mcp`, `required: true`. **No default yet**: the production URL is not in any repository, and nothing works in production before P1-02 (Entra sign-in) anyway. P1-02 adds the production default (D-14). Add `userConfig.devToken`: `type: "string"`, title "Dev token (local server only)", default `""`, a description saying to set `yvoke-dev` only against a local yvoke-web in mock mode. |
| `plugins/yvoke/.mcp.json` | New. Server `yvoke` (D-05), `"type": "http"`, `"url": "${user_config.serverUrl}"`, `"headers": { "X-Yvoke-Dev-Token": "${user_config.devToken}" }`. Never an `Authorization` header. |
| `docs/dev-setup.md` | New, short: start yvoke-web in mock mode, add the working copy as a marketplace (`claude plugin marketplace add <clone>/`; a bare `.` is refused), install with `--config serverUrl=http://localhost:8080/mcp --config devToken=yvoke-dev`, check with `claude mcp list`, and how to change or clear the settings later. P0-03's contributor guide links to it. |
| `README.md` | One "Local development" line linking `docs/dev-setup.md`. |
| `docs/specs/packaging.md` | At the end (finish-task): the plugin's two settings and its one MCP server. |
| `docs/tasks/v1/plan.md` | P0-11's sign-in bullet: the `X-Yvoke-Dev-Token` header and why not `Authorization`; P1-02's note that it adds the production default and must keep `Authorization` out of `.mcp.json`; tick P0-11. |
| `docs/tasks/v1/design.md` | D-14: record the decision above and the `headers.Authorization` finding. |

In **yvoke-web** (same branch name, its own PR, linked from this one):

| File | Change |
| --- | --- |
| `src/main/java/de/palsoftware/yvoke/shared/security/SecurityConfig.java` | On the `/mcp` chain, when `mockAuth` is on, a `BearerTokenResolver` that returns the request's bearer token, or else the `X-Yvoke-Dev-Token` value when it equals the hard-coded `yvoke-dev`, or else nothing (401, as today). The mock `JwtDecoder` turns the token into the mock MCP user, as today. With mock off, the default resolver is used and the header is never read. |
| `src/it/java/de/palsoftware/yvoke/shared/security/McpSecurityGatingIT.java` | New tests in the existing mock-mode class (no new Spring context): `X-Yvoke-Dev-Token: yvoke-dev` is not 401; a wrong value is 401 with the `WWW-Authenticate` header. Existing tests unchanged. |
| `src/it/java/de/palsoftware/yvoke/shared/security/SecurityGatingIT.java` (mock off) | New test: `X-Yvoke-Dev-Token: yvoke-dev` with mock off is 401. |
| `spec/07_using_the_assistant_from_other_tools.md` | One line under local development: in mock mode, MCP accepts the dev token header. |

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
   `claude mcp list` reporting `√ Connected` with `devToken=yvoke-dev`, and the OAuth sign-in starting
   with `devToken` empty. Paste the output in the PR.
5. **Documents.** `docs/dev-setup.md`, README line, plan.md and D-14 notes.
6. **Eduard's machine** (he runs these himself; the cloud cannot reach his yvoke-web):
   1. Start yvoke-web with `./redeploy.sh` (Compose sets the `local` profile; `.env` has
      `APP_SECURITY_MOCK=true`), from the yvoke-web branch until its PR merges.
   2. `claude plugin marketplace add <path to the clone>/`, then
      `claude plugin install yvoke@yvoke --config serverUrl=http://localhost:8080/mcp --config devToken=yvoke-dev`.
   3. `claude mcp list`: `plugin:yvoke:yvoke … √ Connected`.
   4. In a new session, `/mcp` lists the `yvoke` tools; ask *"Use search_corpus to find …"* and the answer
      cites a chunk from the local corpus.
7. **Close out** with `finish-task` once Eduard reports step 6.

## Risks

- **Claude Code changes how settings reach `.mcp.json`.** Checked on 2.1.291; Eduard's version may differ.
  Step 6 shows it on his build, and `docs/dev-setup.md` names the version it was checked on.
- **A required setting with no default.** Until P1-02, a user who installs without `--config` sees the
  `yvoke` server fail with *"Plugin option "serverUrl" isn't set"*. The mod still loads. Acceptable: only
  developers install the plugin before P1-02. Rejected: a fake default URL, which would fail later and
  less clearly.
- **A public dev token.** The value is in a public repository, so it must only ever work in mock mode,
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
