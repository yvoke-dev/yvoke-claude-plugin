# Packaging and installation

## Behaviour

- The repository is its own Claude Code marketplace, named `yvoke`, holding one plugin, `yvoke`. Users
  install it as `yvoke@yvoke`.
- The plugin entry's source is the relative path `./plugins/yvoke`. When the marketplace is added from a
  local folder, Claude Code reads the plugin from that folder itself (`claude plugin list` shows
  `Read from: <clone>/plugins/yvoke`), so edits reach a session with `/reload-plugins`, without a new version.
- The plugin carries a mod (`hooks/register.tsx`) that registers no hooks yet. Every event, tool calls
  included, reaches Claude Code unchanged.
- The plugin has one MCP server, `yvoke` (HTTP), so its tools are named `mcp__plugin_yvoke_yvoke__<tool>`.
  Its address is the setting `serverUrl`.
- Two settings (`userConfig`), set with `claude plugin install yvoke@yvoke --config KEY=VALUE` or
  `/plugin configure yvoke@yvoke`, and stored in the user's `settings.json`, never in the repository:
  - `serverUrl`: the Yvoke MCP endpoint. No default yet (P1-02 adds production). While it is unset the
    `yvoke` server does not connect ("URL is unset or invalid"); the mod still loads.
  - `devMode`, off by default: the plugin always sends `X-Yvoke-Dev-Mode: true|false`. A yvoke-web in
    mock mode accepts `true` in place of a sign-in; any other server ignores it. Off, the normal OAuth
    sign-in applies. `.mcp.json` sets no `Authorization` header, because Claude Code turns the OAuth
    sign-in off whenever one is set.
- How to run it against a local yvoke-web: [README, "Use it locally"](../../README.md#use-it-locally).

## Interfaces

| Piece | File |
| --- | --- |
| Marketplace manifest | `.claude-plugin/marketplace.json` |
| Plugin manifest: name, version, `$.state` contract | `plugins/yvoke/.claude-plugin/plugin.json` |
| Settings `serverUrl`, `devMode` | `userConfig` in `plugins/yvoke/.claude-plugin/plugin.json` |
| MCP server `yvoke` | `plugins/yvoke/.mcp.json` |
| Dev-mode header on the server | yvoke-web `SecurityConfig.mcpBearerTokenResolver` (mock mode only) |
| Hooks module list | `plugins/yvoke/hooks/hooks.json` |
| Mod entry point | `plugins/yvoke/hooks/register.tsx` |
| `$.state` contract (`PluginState.yvoke`, empty) | `plugins/yvoke/types/index.d.ts` |
| Type declarations for `tsc`, laid by `npm run types` | `plugins/yvoke/.claude-plugin/types/` (not committed) |

## Tests

- `plugins/yvoke/tests/scaffold.test.ts`: the plugin loads and leaves a tool call unchanged.
- The verification commands in [AGENTS.md](../../AGENTS.md#verification) check both manifests and the types.
- The `.mcp.json` settings and headers are checked by hand against Claude Code (no automated test can
  load an MCP server): see the P0-11 pull request. yvoke-web pins the server side in
  `McpSecurityGatingIT`, `SecurityGatingIT` and `SecurityConfigMockAuthGuardTest`.

## History

- P0-01 ([#7](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/7)): marketplace, plugin, empty mod,
  type-checking and the first test.
- P0-11 ([#10](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/10)): settings `serverUrl` and
  `devMode`, the `yvoke` MCP server, and the README's "Use it locally".
