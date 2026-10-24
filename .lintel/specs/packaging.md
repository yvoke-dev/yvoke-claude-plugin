# Packaging and installation

## Behaviour

- The repository is its own Claude Code marketplace, named `yvoke`, holding one plugin, `yvoke`. Users
  install it as `yvoke@yvoke`.
- The plugin entry's source is the relative path `./plugins/yvoke`. When the marketplace is added from a
  local folder, Claude Code reads the plugin from that folder itself (`claude plugin list` shows
  `Read from: <clone>/plugins/yvoke`), so edits reach a session with `/reload-plugins`, without a new version.
- The plugin carries a mod (`hooks/register.tsx`). In a session the user did not start with `/yvoke`, every
  event, tool calls included, reaches Claude Code unchanged: see [session.md](session.md).
- The plugin has one MCP server, `yvoke` (HTTP), so its tools are named `mcp__plugin_yvoke_yvoke__<tool>`.
  Its address is the setting `serverUrl`.
- Six settings (`userConfig`), set with `claude plugin install yvoke@yvoke --config KEY=VALUE` or
  `/plugin configure yvoke@yvoke`, and stored in the user's `settings.json`, never in the repository:
  - `serverUrl`: the Yvoke MCP endpoint. No default yet (P1-02 adds production). While it is unset the
    `yvoke` server does not connect ("URL is unset or invalid"); the mod still loads.
  - `devMode`, off by default: the plugin always sends `X-Yvoke-Dev-Mode: true|false`. A yvoke-web in
    mock mode accepts `true` in place of a sign-in; any other server ignores it. Off, the normal OAuth
    sign-in applies. `.mcp.json` sets no `Authorization` header, because Claude Code turns the OAuth
    sign-in off whenever one is set.
  - `showPrototypes`, off by default: on, the setup band also offers prototype areas and playbooks
    ([setup.md](setup.md)).
  - `turnLimitSingle` (25), `turnLimitLead` (60), `turnLimitAgent` (20): the turn ceilings per question,
    see [turn-limits.md](turn-limits.md).
- How to run it against a local yvoke-web: [README, "Use it locally"](../../README.md#use-it-locally).

## Interfaces

| Piece | File |
| --- | --- |
| Marketplace manifest | `.claude-plugin/marketplace.json` |
| Plugin manifest: name, version, `$.state` contract | `plugins/yvoke/.claude-plugin/plugin.json` |
| Settings `serverUrl`, `devMode`, `showPrototypes`, `turnLimitSingle`, `turnLimitLead`, `turnLimitAgent` | `userConfig` in `plugins/yvoke/.claude-plugin/plugin.json` |
| MCP server `yvoke` | `plugins/yvoke/.mcp.json` |
| Dev-mode header on the server | yvoke-web `SecurityConfig.mcpBearerTokenResolver` (mock mode only) |
| Hooks module list | `plugins/yvoke/hooks/hooks.json` |
| Mod entry point | `plugins/yvoke/hooks/register.tsx` |
| `$.state` contract (`PluginState.yvoke`) | `plugins/yvoke/types/index.d.ts` |
| Type declarations for `tsc`, laid by `npm run types` | `plugins/yvoke/.claude-plugin/types/` (not committed) |


## History

- P0-01 ([#7](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/7)): marketplace, plugin, empty mod,
  type-checking and the first test.
- P0-02 ([#9](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/9)): CI runs the plugin checks on the
  oldest and newest Claude Code.
- P0-11 ([#10](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/10)): settings `serverUrl` and
  `devMode`, the `yvoke` MCP server, and the README's "Use it locally".
- P1-10 ([#12](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/12)): the mod's first hooks, for the
  `/yvoke` session start.
