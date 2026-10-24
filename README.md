# Yvoke for Claude

A Claude plugin that brings Yvoke Desktop's capabilities into Claude: playbook-scoped, cited answers from the
Yvoke knowledge base, and multi-agent investigations with enforced review.

- **Claude Code** (the Claude Desktop app's **Code** tab and the terminal): full feature set, through a mod.
- **Cowork** and **Chat**: not in v1; to check after v1.

**Status:** single-agent MVP works against a local `yvoke-web` server, in a real Claude Code session. `/yvoke`
starts a Yvoke session with the setup band above the prompt (area and versioned playbook selection, locked on
the first question and preserved across `/resume` and `/branch`), composes base instructions and the active
playbook from the server, enforces a deny-by-default tool gate scoped to the playbook's knowledge-base tools,
and caps model requests per question. Production Entra ID sign-in, citations, web and compute tool rules,
playbook preflight checks, and multi-agent investigations are tracked in [PLAN.md](PLAN.md).

## Use it locally

Run the plugin from your clone against a [yvoke-web](https://github.com/yvoke-dev/yvoke-web) on your own
machine. Checked with Claude Code 2.1.287 and 2.1.310.

1. **Start yvoke-web in mock mode.** Use an up-to-date `main` (the dev switch needs
   [yvoke-web#6](https://github.com/yvoke-dev/yvoke-web/pull/6)). In your yvoke-web clone, make sure `.env` has
   `APP_SECURITY_MOCK=true` (`.env.example` already does), then run `./redeploy.sh`. The server listens
   on `http://localhost:8080`, and its MCP endpoint is `http://localhost:8080/mcp`.
2. **Add this clone as a marketplace**, from the clone's root folder:

   ```sh
   claude plugin marketplace add ./
   ```

   The `./` is needed: a bare `.` is refused. Claude Code then reads the plugin straight from your clone.
3. **Install the plugin** with its two settings:

   ```sh
   claude plugin install yvoke@yvoke --config serverUrl=http://localhost:8080/mcp --config devMode=true
   ```

   - `serverUrl` is the Yvoke server to use.
   - `devMode` on lets the plugin in without signing in. Only a yvoke-web in mock mode accepts it; any
     other server ignores it.
4. **Check the connection:**

   ```sh
   claude mcp list
   ```

   The line `plugin:yvoke:yvoke: http://localhost:8080/mcp (HTTP) - √ Connected` means it works. In a
   session, `/mcp` lists the `yvoke` tools, and you can ask Claude to use `search_corpus`.
5. **After you edit the plugin**, run `/reload-plugins` in the session, or start a new one.

**Use another server.** Run `/plugin configure yvoke@yvoke` in a session, set `serverUrl` to that server
and turn `devMode` off. In a terminal, pipe the values in instead:
`echo '{"serverUrl":"https://<server>/mcp","devMode":"false"}' | claude plugin configure yvoke@yvoke --values-stdin`. The plugin then uses the normal sign-in. Production Entra ID sign-in is tracked in [PLAN.md](PLAN.md); until then
only a local server in mock mode works.

**Turn limits.** Three more settings cap the model requests of one Yvoke question: `turnLimitSingle`
(25), `turnLimitLead` (60) and `turnLimitAgent` (20, each specialist and the reviewer). At the limit the
answer is still delivered, marked as stopped at the turn limit; `0` turns a limit off. Change them with
`/plugin configure yvoke@yvoke`.

**Quick look without installing:** `claude --plugin-dir plugins/yvoke` loads the plugin for one session.
Use the steps above when you need it connected to a server. To work on the mod with reload-on-save, see
[docs/contributing.md](docs/contributing.md).

**Remove it:** `claude plugin uninstall yvoke@yvoke`, then `claude plugin marketplace remove yvoke`.

## Documents

| Document | What it covers |
| --- | --- |
| [PLAN.md](PLAN.md) | Feature roadmap, upcoming milestones, codebase integration notes, and server/IT dependencies |
| [docs/contributing.md](docs/contributing.md) | Running the mod while you work on it: reload, types, debug log |
| [.lintel/specs/index.md](.lintel/specs/index.md) | Living specifications describing what is implemented today |
| [.lintel/steering/product.md](.lintel/steering/product.md) | Product mission, target users, and core principles |
| [.lintel/steering/architecture.md](.lintel/steering/architecture.md) | Current hook pipeline, question lifecycle, and module boundaries |
| [.lintel/steering/decisions.md](.lintel/steering/decisions.md) | Decisions with their reasons, and the known gaps |
| [.lintel/steering/tech.md](.lintel/steering/tech.md) | Technology stack and mod runtime environment |
| [.lintel/steering/conventions.md](.lintel/steering/conventions.md) | Coding and security conventions |
| [.lintel/steering/testing.md](.lintel/steering/testing.md) | Headless mod test runner, the live integration test, and testing patterns |

## Licence

Apache License 2.0. See [LICENSE](LICENSE).
