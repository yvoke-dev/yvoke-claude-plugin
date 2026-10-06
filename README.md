# Yvoke for Claude

A Claude plugin that brings Yvoke Desktop's capabilities into Claude: playbook-scoped, cited answers from the
Yvoke knowledge base, and multi-agent investigations with enforced review.

- **Claude Code** (the Claude Desktop app's **Code** tab and the terminal): full feature set, through a mod.
- **Cowork** and **Chat**: live playbook stubs over the Yvoke connector, with fewer guarantees.

**Status:** scaffold. The plugin installs, loads and connects to a Yvoke server, but adds no behaviour yet.

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

**Quick look without installing:** `claude --plugin-dir plugins/yvoke` loads the plugin for one session.
Use the steps above when you need it connected to a server.

**Remove it:** `claude plugin uninstall yvoke@yvoke`, then `claude plugin marketplace remove yvoke`.

## Documents

| Document | What it covers |
| --- | --- |
| [docs/sdlc.md](docs/sdlc.md) | How we build this: intent, spec, task plan, tests first, pull request |
| [docs/tasks/v1/intent.md](docs/tasks/v1/intent.md) | v1: why, for whom, use cases, constraints, open questions |
| [docs/tasks/v1/requirements.md](docs/tasks/v1/requirements.md) | v1: what the plugin must do, per surface |
| [docs/tasks/v1/design.md](docs/tasks/v1/design.md) | v1: architecture, decisions, notes for implementers, yvoke-web changes |
| [docs/tasks/v1/plan.md](docs/tasks/v1/plan.md) | v1: the tasks and their progress |
| [docs/specs/](docs/specs/README.md) | What is built today |
| [AGENTS.md](AGENTS.md) | Rules and verification commands for agents working here |
