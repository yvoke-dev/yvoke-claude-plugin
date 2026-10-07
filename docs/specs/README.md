# Specs

What the plugin does **today**, one file per area (for example `setup.md`, `tool-policy.md`,
`citations.md`, `multi-agent.md`, `server-tools.md`). A spec describes merged behaviour only. The pull
request that changes the behaviour updates the spec in the same change (see [../sdlc.md](../sdlc.md)).

| Spec | What it covers |
| --- | --- |
| [packaging.md](packaging.md) | The marketplace, the plugin and how it is installed |
| [server-client.md](server-client.md) | How the mod calls the Yvoke server and reports its failures |
| [session.md](session.md) | What makes a session a Yvoke session: `/yvoke`, `/clear`, `/resume`, `/branch` |
| [system-prompt.md](system-prompt.md) | The base instructions and playbook a Yvoke session's system prompt carries |

What v1 is meant to do is in [../tasks/v1/requirements.md](../tasks/v1/requirements.md).

Each spec has these sections:

- **Behaviour**: what a user sees and what the plugin enforces, as testable statements.
- **Interfaces**: hooks, commands, settings (`userConfig`) and server tools involved.
- **Tests**: where the tests that pin this behaviour live.
- **History**: the tasks and pull requests that shaped it.
