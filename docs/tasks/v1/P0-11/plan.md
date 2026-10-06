# P0-11 Dev environment

**Release:** v1 · **Size:** S · **Type:** feature · **Status:** planned

A developer installs the working copy in their own Claude Code and points it at a yvoke-web running on
their machine. Task entry: [plan.md, P0-11](../plan.md#phase-0--foundation-and-spikes).

## What was checked first

Checked in a cloud session on **Claude Code 2.1.291** on 2026-10-06, with a throwaway copy of the plugin
installed from a local marketplace and a small mock MCP server that logs the `Authorization` header it
receives.

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

So this plan **drops the dev token** and asks yvoke-web for one small, opt-in change instead (see
*Decision*).

## Decision for Eduard

**How does the plugin sign in to a local yvoke-web?**

1. **A dev switch on yvoke-web (recommended).** A new setting, `app.security.mock-mcp-without-token`
   (`APP_SECURITY_MOCK_MCP_WITHOUT_TOKEN`, default `false`). When it is on, an MCP request with no bearer
   token is treated as the same mock user yvoke-web already builds for any token in mock mode. yvoke-web
   refuses to start with it on unless `app.security.mock=true`, which itself is refused outside the
   `dev`, `local` and `test` profiles. `.env.example` turns it on for local Compose. Mock mode already
   trusts any token, so accepting none trusts nothing new, and the existing mock-mode tests keep their
   401. The plugin then ships only `serverUrl`, with no dev token anywhere.
2. **A shell helper in the plugin.** `.mcp.json` gets a `headersHelper` script that adds
   `Authorization: Bearer <x>` when a shell variable such as `YVOKE_DEV_USER` is set. No yvoke-web change,
   but the script ships to every user, runs on every connection, can be switched off by an organization's
   managed settings (which would also stop the Entra sign-in), and the dev value lives outside
   `userConfig`.

If Eduard picks 2, the yvoke-web part below is replaced by `plugins/yvoke/scripts/auth-headers.sh`, a
check that it prints `{}` with the variable unset, and a note in `docs/dev-setup.md` on setting the
variable (for the Desktop app, in the `env` block of `~/.claude/settings.json`).

## Files that change

In **yvoke-claude-plugin** (branch `claude/p0-11-dev-setup-3i393i`):

| File | Change |
| --- | --- |
| `plugins/yvoke/.claude-plugin/plugin.json` | Add `userConfig.serverUrl`: `type: "string"`, title "Yvoke server URL", a description naming the local form `http://localhost:8080/mcp`, `required: true`. **No default yet**: the production URL is not in any repository, and nothing works in production before P1-02 (Entra sign-in) anyway. P1-02 adds the production default (D-14). |
| `plugins/yvoke/.mcp.json` | New. `{ "mcpServers": { "yvoke": { "type": "http", "url": "${user_config.serverUrl}" } } }`. No headers. The server name stays `yvoke` (D-05), so tools are `mcp__plugin_yvoke_yvoke__…`. |
| `docs/dev-setup.md` | New, short: start yvoke-web locally, add the working copy as a marketplace (`claude plugin marketplace add <clone>/`; a bare `.` is refused), install with `--config serverUrl=http://localhost:8080/mcp`, check with `claude mcp list`, and how to change the URL later. P0-03's contributor guide links to it. |
| `README.md` | One "Local development" line linking `docs/dev-setup.md`. |
| `docs/specs/packaging.md` | At the end (finish-task): the plugin now has one setting, `serverUrl`, and one MCP server, `yvoke`. |
| `docs/tasks/v1/plan.md` | P0-11's sign-in bullet: no dev token (decision above); P1-02's note that it adds the production default; tick P0-11. |
| `docs/tasks/v1/design.md` | D-14: record that the dev token was dropped and why (the `headers.Authorization` finding). |

In **yvoke-web** (same branch name, its own PR, linked from this one), only with option 1:

| File | Change |
| --- | --- |
| `src/main/resources/application.yml` | `app.security.mock-mcp-without-token: ${APP_SECURITY_MOCK_MCP_WITHOUT_TOKEN:false}`, with a comment. |
| `.env.example` | `APP_SECURITY_MOCK_MCP_WITHOUT_TOKEN=true` next to `APP_SECURITY_MOCK=true`, with one line on what it is for. `EnvExampleContractTest` checks the pair. |
| `src/main/java/de/palsoftware/yvoke/shared/security/SecurityConfig.java` | Read the setting. Refuse to start when it is on and `app.security.mock` is off. On the `/mcp` chain, when it is on, use a `BearerTokenResolver` that returns the request's bearer token, or a fixed placeholder when there is none; the mock `JwtDecoder` turns either into the mock MCP user, as today. When it is off, nothing changes. |
| `src/test/java/…/SecurityConfigMockAuthGuardTest.java` | New case: the switch on with mock off fails startup, naming the setting. |
| `src/it/java/de/palsoftware/yvoke/shared/security/McpSecurityGatingIT.java` | Unchanged; its 401 tests keep pinning the default. |
| A new nested class or IT with the switch on (reusing one existing mock context's annotations plus the one property, so only one context is added) | `POST /mcp` `initialize` with no `Authorization` header is not 401. |
| `spec/07_using_the_assistant_from_other_tools.md` | One line under local development: with the switch on, MCP needs no token. |

## Order of work

1. **yvoke-web, tests first.** Add the switch-on IT (no token, not 401) and the startup-guard unit test.
   Run them: red. Then add the setting to `application.yml` and `.env.example`.
2. **yvoke-web, make it pass.** Add the guard and the resolver. Both new tests green, and
   `McpSecurityGatingIT` still green. Prove the guard test pins the guard: remove the check, see it go
   red, restore from the saved copy and `diff -q`. Run the full `./mvnw verify -Pit-tests` (the IT
   context cache is near its limit, see yvoke-web's pitfalls), open the yvoke-web PR.
3. **Plugin.** Add `userConfig.serverUrl` and `.mcp.json`. Run `claude plugin validate --strict .` and
   `claude plugin validate --strict plugins/yvoke`. There is no mod behaviour, so no mod test; the
   existing `scaffold.test.ts` must stay green.
4. **Check it in the cloud.** Install the working copy from a local marketplace in a throwaway
   `CLAUDE_CONFIG_DIR`, set `serverUrl` to a mock MCP server, and show `claude mcp list` reporting
   `√ Connected` and the server seeing a request with no `Authorization` header. Paste the output in the PR.
5. **Documents.** `docs/dev-setup.md`, README line, plan.md and D-14 notes.
6. **Eduard's machine** (he runs these himself; the cloud cannot reach his yvoke-web):
   1. Add `APP_SECURITY_MOCK_MCP_WITHOUT_TOKEN=true` to yvoke-web's `.env` (next to
      `APP_SECURITY_MOCK=true`) and start it with `./redeploy.sh` (Compose sets the `local` profile),
      from the yvoke-web branch until its PR merges.
   2. `claude plugin marketplace add <path to the clone>/`, then
      `claude plugin install yvoke@yvoke --config serverUrl=http://localhost:8080/mcp`.
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
- **Accepting no token.** It needs the new switch and mock mode together, and yvoke-web refuses mock mode
  outside a dev profile, so production cannot reach it. The default stays as today, pinned by
  `McpSecurityGatingIT`. Rejected: changing mock mode itself to accept no token, which would break those
  tests and remove the only local way to see the OAuth start.
- **Rejected: a second server entry such as `yvoke-dev`.** It would change every tool name (D-05) and the
  name the mod calls.
- **Rejected: a static `Authorization` header now, sorted out in P1-02.** It turns the OAuth sign-in off,
  so P1-02 would have to undo it, and production would break if it shipped.

## Proof

- yvoke-web: the switch-on IT, the startup-guard test, the unchanged `McpSecurityGatingIT`, and a green
  `./mvnw verify -Pit-tests`.
- Plugin: the checks in [AGENTS.md](../../../../AGENTS.md#verification), `npm run check`, all passing.
- Done when (Eduard's machine, step 6): `claude mcp list` shows the local server connected, and a session
  with the plugin lists its tools and answers from `search_corpus`.
