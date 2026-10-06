# Packaging and installation

## Behaviour

- The repository is its own Claude Code marketplace, named `yvoke`, holding one plugin, `yvoke`. Users
  install it as `yvoke@yvoke`.
- The plugin entry's source is the relative path `./plugins/yvoke`. When the marketplace is added from a
  local folder, Claude Code reads the plugin from that folder itself (`claude plugin list` shows
  `Read from: <clone>/plugins/yvoke`), so edits reach a session with `/reload-plugins`, without a new version.
- The plugin carries a mod (`hooks/register.tsx`) that registers no hooks yet. Every event, tool calls
  included, reaches Claude Code unchanged.
- The plugin has no settings (`userConfig`) and no MCP server yet.

## Interfaces

| Piece | File |
| --- | --- |
| Marketplace manifest | `.claude-plugin/marketplace.json` |
| Plugin manifest: name, version, `$.state` contract | `plugins/yvoke/.claude-plugin/plugin.json` |
| Hooks module list | `plugins/yvoke/hooks/hooks.json` |
| Mod entry point | `plugins/yvoke/hooks/register.tsx` |
| `$.state` contract (`PluginState.yvoke`, empty) | `plugins/yvoke/types/index.d.ts` |
| Type declarations for `tsc`, laid by `npm run types` | `plugins/yvoke/.claude-plugin/types/` (not committed) |

## Tests

- `plugins/yvoke/tests/scaffold.test.ts`: the plugin loads and leaves a tool call unchanged.
- The verification commands in [AGENTS.md](../../AGENTS.md#verification) check both manifests and the types.

## History

- P0-01 ([#7](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/7)): marketplace, plugin, empty mod,
  type-checking and the first test.
