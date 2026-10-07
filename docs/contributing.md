# Working on the plugin

This page gets you from a clean machine to a mod that reloads every time you save a file, in the terminal
and in the Claude Desktop app's **Code** tab. Checked with Claude Code 2.1.293 on 2026-10-07.

How a change is made (task plan, tests first, pull request) is in [sdlc.md](sdlc.md). The rules every
change follows, and the five checks it must pass, are in [AGENTS.md](../AGENTS.md).

## 1. What you need

- **Claude Code 2.1.287 or newer**, signed in. 2.1.287 is the oldest version with mods; CI runs every
  check on it and on the latest release ([ci.yml](../.github/workflows/ci.yml)). `claude --version`
  tells you which one you have; note it in your pull request.
- **Node 22 and npm**, for the type checker only. The mod itself has no npm dependencies.
- **A clone of this repository.** Run `npm install` once in it.

## 2. Load the mod in the terminal

From the clone:

```sh
claude --plugin-dir plugins/yvoke
```

Accept the "Is this a project you trust?" question. Mods do not load in a folder you have not trusted,
and the question is about the folder you start `claude` in, not the plugin folder.

Check that it loaded: type `/yvoke`. It should answer *Yvoke session started…*.

**Hot reload.** Leave the session open and save any file under `plugins/yvoke/`. The transcript shows:

```text
● yvoke: reloaded (3 hooks: session.start, command.run, classic.SessionStart)
```

The module's `register` ran again with your change. `$.state` and `$.store` are kept; module variables
are not. If the file does not parse, you see this instead, and the old version keeps running:

```text
● yvoke: reload failed, the previous version stays loaded: …/register.tsx does not parse: …
```

`--plugin-dir` loads the plugin for that session only. Nothing is installed and no settings are written.

## 3. Load the mod in the Desktop Code tab

The Code tab has no command line, so point Claude Code at the folder from your user settings. Add an
`env` block to your user settings, `~/.claude/settings.json`, so it applies in every folder you open:

```json
{
  "env": {
    "CLAUDE_CODE_PLUGIN_DIRS": "/absolute/path/to/yvoke-claude-plugin/plugins/yvoke",
    "CLAUDE_CODE_PLUGIN_DIR_WATCH": "1"
  }
}
```

The path must be absolute. Then start a new session in the Code tab. The plugin loads exactly as with
`--plugin-dir`, and saving a file reloads it. `CLAUDE_CODE_PLUGIN_DIR_WATCH=1` is what turns on
reload-on-save in the Desktop app; the terminal does it without the setting.

These settings also apply to every terminal session. Remove the `env` block when you are done, or the
mod keeps loading from your clone.

**Without the `env` block:** install the clone as a local marketplace, as in the README's
[Use it locally](../README.md#use-it-locally), and run `/reload-plugins` after each change instead of
relying on reload-on-save. That is also the route when you need the plugin connected to a Yvoke server.

## 4. Connect to a Yvoke server

`--plugin-dir` and `CLAUDE_CODE_PLUGIN_DIRS` load the mod but leave the `serverUrl` setting empty, so the
`yvoke` MCP server does not connect (`--debug` shows "URL is unset or invalid"). Everything that runs on
`claude plugin test`, with the server stubbed, works without one. To use a real server, follow
[Use it locally](../README.md#use-it-locally): a yvoke-web on your machine in mock mode, and the plugin
installed with `serverUrl` and `devMode`.

## 5. Types

```sh
npm run typecheck
```

The mod API's type declarations are written by Claude Code itself, into
`plugins/yvoke/.claude-plugin/types/`, when it loads the mod. `npm run typecheck` loads it once with no
sign-in and no model call ([lay-types.mjs](../scripts/lay-types.mjs)), then runs `tsc`. The folder is
ignored by git: never commit it, since it changes with every Claude Code version.

The declarations are the truth about the API for your version. Grep
`plugins/yvoke/.claude-plugin/types/claude-code/index.d.ts` for an event name (`'tool.call'`) or a type
(`HookBudget`). [design.md 4.2 and 4.3](tasks/v1/design.md#42-the-mod-api-where-the-truth-is) map the
plan's ideas onto that API.

## 6. Tests and checks

```sh
npm test        # claude plugin test plugins/yvoke
npm run check   # all five checks from AGENTS.md, in CI's order
```

Tests run against the real engine with no session, network or server: see
[design.md 4.2](tasks/v1/design.md#42-the-mod-api-where-the-truth-is) for how a test answers the mod's
`$` calls and stubs the Yvoke server. Run `npm run check` before every push; CI runs the same commands.

## 7. Reading the debug log

```sh
claude --plugin-dir plugins/yvoke --debug-file /tmp/yvoke-debug.log
```

`--debug` prints the log in the terminal instead. Useful lines, all with `yvoke` in them:

| Line | Meaning |
| --- | --- |
| `hooks module yvoke@inline loaded (…); events: …` | The mod loaded, with these hooks. |
| `hooks modules not loaded until workspace trust is accepted: yvoke` | The folder is not trusted yet. |
| `plugin-dir watch: watching yvoke` | Reload-on-save is on. `watching nothing` means it is off. |
| `hooks module yvoke@inline reloaded in …` | A save was picked up. |
| `yvoke: ui.render (<Component>) refused: …` | A UI tree did not validate. While hot-reloading it also shows as a dim line in the transcript. |
| `[yvoke] $.ui.log: …` | The mod's own `$.ui.log` output. |
| `hook failed closed: yvoke: errorKind=Error errorChars=4 (command.run; its .catch answered)` | A hook threw, and its `.catch` gave the safe answer. |
| `MCP server yvoke invalid: URL is unset or invalid` | No `serverUrl` (section 4). Harmless for mod work. |

When a hook throws, the log names only the error's kind and length, never its message. To see the
message while you work, write it with `$.ui.log` and take the line out before you commit: whatever
`$.ui.log` writes ends up in support tickets, so it must never carry tokens or answers (AGENTS.md).

## 8. When the mod does not show up

- **The folder is not trusted.** Start `claude` in a folder you trust, or answer the trust question.
- **Mods are off in this process.** `claude plugin test` says "hooks modules are turned off": start
  `claude` once with network access, or use a throwaway `CLAUDE_CONFIG_DIR`
  ([AGENTS.md](../AGENTS.md#verification)). Settings `disableAllHooks` or `allowManagedHooksOnly` also turn
  them off.
- **Mods never load** under `--safe-mode` or in WSL sessions of the Desktop app. They load but draw
  nothing in VS Code, with `-p`, or in cloud sessions.
- **The change does not appear.** Check the log for `reload failed` or `watching nothing`. In the
  Desktop app, `CLAUDE_CODE_PLUGIN_DIR_WATCH` must be `1`.
- **The module loads but a feature is missing.** The engine refuses some code shapes that TypeScript
  accepts: `$` passed into another file, a hook built by a wrapper, a `$.state` key imported from another
  file. See [design.md 4.4](tasks/v1/design.md#44-rules-that-are-easy-to-get-wrong).
  `claude plugin validate --strict plugins/yvoke` names the problem.
