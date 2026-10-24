# Technology Stack & Runtime Environment

## Overview

**Yvoke for Claude** (`yvoke-claude-plugin`) is a Claude Code plugin and in-process mod (`hooks/register.tsx`) that brings Yvoke Desktop capabilities into Claude Code (both the Claude Desktop **Code** tab and the terminal): playbook-scoped, cited answers from the Yvoke knowledge base, and multi-agent investigations with enforced review.

## Runtime & Tooling

- **Target Platform**: Claude Code `>= 2.1.287` (the oldest version with mods) up to `latest`.
- **Mod Environment**:
  - Runs in-process inside the Claude Code mod engine (`hooks/register.tsx`).
  - **Not a Node.js runtime**: has no `require`, no dynamic `import()`, no DOM, and no `process`.
  - Static ES module `import` of the plugin's own `.ts` / `.tsx` files is supported.
  - JSX compiles against the global `h` factory provided by Claude Code's mod types.
  - Zero runtime `dependencies` in `package.json`.
- **Development & Type-Checking**:
  - **Node.js 22** and `npm` (used for development scripts and TypeScript type-checking only).
  - **TypeScript 7.0.2** (`devDependencies`).
  - **Mod API Type Declarations**: Claude Code writes its own mod API type declarations into `plugins/yvoke/.claude-plugin/types/claude-code/index.d.ts` when loading the mod from a watched folder (`scripts/lay-types.mjs` runs `claude --plugin-dir plugins/yvoke -p "lay types"` with `CLAUDE_CODE_PLUGIN_DIR_WATCH=1`). That folder is gitignored and never committed.
- **Backend Integration**:
  - Connects to `yvoke-web` MCP server (`/mcp`) via `plugins/yvoke/.mcp.json` (`serverUrl` and `devMode` settings in `userConfig`).
  - All mod-to-server calls go through `callYvoke(io, tool, args)` in `plugins/yvoke/src/server.ts` using `$.mcp.connect` and `$.mcp.call`.
