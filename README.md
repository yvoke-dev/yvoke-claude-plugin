# Yvoke for Claude

A Claude plugin that brings Yvoke Desktop's capabilities into Claude: playbook-scoped, cited answers from the
Yvoke knowledge base, and multi-agent investigations with enforced review.

- **Claude Code** (the Claude Desktop app's **Code** tab and the terminal): full feature set, through a mod.
- **Cowork** and **Chat**: not in v1; to check after v1.

**Status:** early. `/yvoke` starts a Yvoke session, and in it the mod lets through only the tools a Yvoke
answer may use. Picking a playbook and getting an answer from it come next (milestone M1).

## Use it locally

Run the plugin from your clone against a [yvoke-web](https://github.com/yvoke-dev/yvoke-web) on your own
machine. Checked with Claude Code 2.1.291.

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
`echo '{"serverUrl":"https://<server>/mcp","devMode":"false"}' | claude plugin configure yvoke@yvoke --values-stdin`. The plugin then uses the normal sign-in. That sign-in (Entra ID) arrives with task P1-02; until then
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
| [docs/contributing.md](docs/contributing.md) | Running the mod while you work on it: reload, types, debug log |
| [.lintel/specs/index.md](.lintel/specs/index.md) | Living specifications: what is built today |
| [.lintel/steering/product.md](.lintel/steering/product.md) | v1 intent, requirements, and roadmap |
| [.lintel/steering/architecture.md](.lintel/steering/architecture.md) | Architecture, decisions, notes for implementers, yvoke-web changes |
| [.lintel/steering/conventions.md](.lintel/steering/conventions.md) | Coding and security conventions |
