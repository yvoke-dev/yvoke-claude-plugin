# Decisions

The decisions behind the plugin and why they were made; where an entry disagrees with the specs or PLAN.md, those win.

To add one, append `### D-28: <short title>` (the next free number) with status, date, decision and why; keep IDs stable and never reuse one.

### D-01: Claude plans and install route

- **Status:** changed. Managed organisations now install the plugin through managed settings or an MDM
  directory marketplace ([system-prompt.md](../specs/system-prompt.md), D-25), not by claude.ai
  organisation sync.
- **Decided:** 2026-10-05
- **Decision:** Users are on their organisation's Claude Team or Enterprise plan. The pilot installs the
  plugin from this Git marketplace; the rollout was to push it by claude.ai organisation sync or an MDM
  directory marketplace.
- **Why:** Team and Enterprise let an admin push the plugin to everyone. A plugin from Git, a URL or
  claude.ai sync always runs as a user mod, so it stops loading once IT sets `allowManagedModsOnly`.

### D-02: Public marketplace repository

- **Status:** live
- **Decided:** 2026-10-05
- **Decision:** The repository is public. It never holds playbook text, secrets or customer data.
- **Why:** Playbooks load live from the server (D-11), so the repository needs nothing secret. Users need no
  GitHub access to install, and the knowledge stays behind the server's sign-in.

### D-03: How Claude Code reaches the Yvoke server

- **Status:** changed by D-26. Only the plugin's `.mcp.json` entry ships; an organisation connector comes
  back only with Chat and Cowork.
- **Decided:** 2026-10-05
- **Decision:** Claude Code connects through the plugin's own `.mcp.json` server `yvoke`, which signs in
  with OAuth. Chat and Cowork were to use an organisation connector with the same URL.
- **Why:** A fixed server name gives the mod a known tool prefix, and Claude Code holds the token, so the
  mod never handles one. One URL on every surface means a user with both sees one set of tools.

### D-04: Live playbook stubs for Chat and Cowork

- **Status:** superseded by D-26. No stubs ship in v1; this applies only if Chat and Cowork return.
- **Decided:** 2026-10-05
- **Decision:** Chat and Cowork get skill stubs that hold only a playbook's name and description and tell
  Claude to fetch the playbook with `get_playbook`. Claude Code takes playbooks live through the mod (D-11).
- **Why:** Live text keeps Yvoke Desktop's rule of no stale instructions, and no playbook text ever lands
  in the public repository (D-02).

### D-05: Plugin and server names

- **Status:** live
- **Decided:** 2026-10-05
- **Decision:** The plugin is `yvoke` and its MCP server is `yvoke`, so the server's tools are
  `mcp__plugin_yvoke_yvoke__<tool>`. Do not rename either after rollout.
- **Why:** Neither name collides with the mod's own tools (`mcp__yvoke__…`). Users' permission rules name
  these tools, so a rename would break them.

### D-06: Multi-agent profiles registered live

- **Status:** live (not built yet, PLAN.md Milestone 4)
- **Decided:** 2026-10-05
- **Decision:** In multi-agent mode the mod reads the profile from the server and registers its specialists
  and reviewer with `$.agent.register`. No `agents/*.md` files are generated.
- **Why:** The server stays the source of truth, and role models and budgets can come from configuration.
  Generated files would fix model, effort and tools at build time.

### D-07: No conversation sync in v1

- **Status:** live
- **Decided:** 2026-10-05
- **Decision:** Conversations stay in Claude's own history. Sync into the user's Yvoke account comes after
  v1.
- **Why:** Sync needs a retry queue that keeps questions and answers unencrypted on disk until they are
  sent, which needs its own privacy review. It also needs new server work.

### D-08: Answer ratings come after sync

- **Status:** live
- **Decided:** 2026-10-05
- **Decision:** v1 has no answer ratings. Once conversations sync, a rating attaches to the synced message
  id, as in Yvoke Desktop.
- **Why:** The server's feedback store is keyed to that id, so `submit_feedback` can wrap it with no new
  storage. A self-contained rating payload would be built only to be replaced.

### D-09: No IT lockdown

- **Status:** live
- **Decided:** 2026-10-05
- **Decision:** Only the mod enforces Yvoke's rules, inside Yvoke sessions. IT does not deny shell or file
  tools through managed settings, and a user can disable the plugin.
- **Why:** Using Yvoke is opt-in, so the rules keep answers grounded rather than act as a security
  boundary. A managed deny would apply to every Claude Code session on the machine, coding included.
  Claude Code's own permission prompts still guard shell and file tools.

### D-10: Chat and Cowork with playbook stubs in v1

- **Status:** superseded by D-26
- **Decided:** 2026-10-05
- **Decision:** v1 was built for Claude Code, and Chat and Cowork got only the live playbook stubs (D-04)
  over the Yvoke connector.
- **Why:** It promised knowledge-base answers on the other surfaces at a small cost, without the setup
  band, tool scoping or multi-agent mode there.

### D-11: Playbooks are session state, chosen once

- **Status:** live
- **Decided:** 2026-10-05
- **Decision:** In Claude Code the setup band picks the area, mode and playbook once per session. The system
  prompt is the server's base instructions plus the playbook's text, and the setup cannot change after the
  first question.
- **Why:** It keeps Yvoke Desktop's rule of one session, one setup. Nothing has to be restored or switched
  mid-conversation, and the model cannot start a playbook by itself.

### D-12: Base instructions through a tool

- **Status:** live
- **Decided:** 2026-10-05
- **Decision:** yvoke-web serves the base instructions through `get_system_prompt`, not as MCP server
  `instructions`. The mod adds them in Yvoke sessions only.
- **Why:** The plugin's MCP server loads in every Claude Code session. Server `instructions` would reach
  users' coding sessions, and Yvoke sessions would get them twice.

### D-13: Any folder, started with /yvoke

- **Status:** live
- **Decided:** 2026-10-05
- **Decision:** There is no Yvoke folder. A session becomes a Yvoke session when the user types `/yvoke`
  before its first question, and stays one until `/clear`; `/resume` and `/branch` keep it.
- **Why:** A mod's hooks run in every session that loads the plugin, so without scoping the rules would
  block the user's other work. Starting before the first question means Yvoke answers never build on coding
  turns.

### D-14: Server URL and dev mode are settings

- **Status:** live
- **Decided:** 2026-10-05; dev mode 2026-10-06
- **Decision:** `.mcp.json` reads the server URL from the `serverUrl` setting. The `devMode` setting sends
  `X-Yvoke-Dev-Mode`, which only a yvoke-web in mock mode accepts in place of a sign-in.
- **Why:** Developers need to point the plugin at a local yvoke-web. The dev switch cannot be an
  `Authorization` header: Claude Code turns the OAuth sign-in off whenever `.mcp.json` sets one.

### D-15: What an area is

- **Status:** live
- **Decided:** 2026-10-05; revised 2026-10-06
- **Decision:** An area is a collection of system prompts, collections, playbooks and multi-agent profiles,
  kept in its own table on yvoke-web. OIM is the only area today.
- **Why:** Each knowledge base needs its own prompts, playbooks and profiles. More areas will follow, so
  nothing may assume there is only one.

### D-16: The default base instructions

- **Status:** live
- **Decided:** 2026-10-06
- **Decision:** `get_system_prompt` with no name (or `default-chat`) returns the prompt an admin marks as
  the Active Default Chat System Prompt.
- **Why:** yvoke-web's own single-agent mode uses that prompt, so an admin's choice applies everywhere. The
  stored `default-chat` prompt can differ once an admin picks another one.

### D-17: The reviewer's tools are fixed

- **Status:** live
- **Decided:** 2026-10-07; reversed 2026-10-20
- **Decision:** The reviewer always has exactly `verify_citations` and `submit_review`. Tools listed on its
  playbook are ignored.
- **Why:** The first answer took the tools from the reviewer playbook, but yvoke-web and yvoke-desktop both
  ignore tools on reviewer playbooks. Fixed tools keep the three apps alike, with no server change.

### D-18: A new session starts in single agent

- **Status:** live
- **Decided:** 2026-10-20
- **Decision:** The band shows area, then mode, then playbook or profile. A new session starts in single
  agent with the area's default playbook; switching to multi-agent selects the area's default profile.
- **Why:** yvoke-web and yvoke-desktop both start in single agent. One list of modes that defaults to the
  profile would start multi-agent whenever an area has one.

### D-19: Compute through a wrapped Bash

- **Status:** live (not built yet, PLAN.md Milestone 2)
- **Decided:** 2026-10-20
- **Decision:** The mod registers its own `execute_code` and runs the code with `node -e` through Claude
  Code's `Bash`, under Claude Code's sandbox. It ships only if a spike shows the sandbox blocks environment
  secrets, files, child processes and the network; `calculate`, `statistics` and `date_diff` are not ported.
- **Why:** A mod's worker has no `eval`, `new Function` or `WebAssembly`, so Yvoke Desktop's in-process
  `node:vm` cannot be ported. The model itself still never gets `Bash` (D-09).

### D-20: Resumed and branched sessions take the new playbook version

- **Status:** live
- **Decided:** 2026-10-20; extended to `/branch` 2026-10-21
- **Decision:** After `/resume` or `/branch`, a locked setup moves to the playbook version the server has
  now, and the setup line, the status line and one toast say so. Within one session the text is fetched
  once.
- **Why:** yvoke-web keeps no old playbook text, so a new session id can only fetch the current one. The
  band should show the version the answers use; [setup.md](../specs/setup.md) applies the same rule at the
  first lock.

### D-22: Playbook descriptions in the public stubs

- **Status:** superseded by D-26 (no stubs ship)
- **Decided:** 2026-10-20, in the stub generator's plan; never merged
- **Decision:** The Chat and Cowork stubs would carry the server's playbook descriptions as they are, capped
  at 1,024 characters.
- **Why:** The server stays the only source. A description that should not be public is reworded on the
  server, which fixes it for every app.

### D-23: Client-only tools stay on /mcp

- **Status:** live
- **Decided:** 2026-10-20
- **Decision:** yvoke-web serves `get_system_prompt`, `list_areas`, `list_playbooks` and `get_playbook` on
  its usual `/mcp` endpoint, and leaves them out of its own assistant and playbook editor.
- **Why:** One endpoint means one sign-in. A second endpoint would make every plugin user sign in twice.

### D-24: Every subagent gets the same turn ceiling

- **Status:** live
- **Decided:** 2026-10-20
- **Decision:** Specialists and the reviewer share one setting, `turnLimitAgent`, default 20.
- **Why:** The mod need not tell the reviewer from a specialist. A reviewer stopped at Yvoke Desktop's 6
  cannot call `submit_review`, and no verdict counts as a rejection, so it would cause extra revision
  rounds.

### D-25: A personal install in a managed organisation is unsupported

- **Status:** live
- **Decided:** 2026-10-21
- **Decision:** Where an organisation manages Claude Code, the plugin must be installed the organisation's
  way: managed settings or an MDM directory marketplace. Outside such an organisation a personal install is
  supported.
- **Why:** There Claude Code skips a user-installed plugin's `prompt.compose` and `classic.SessionStart`
  hooks but still runs its `prompt.submit`. The playbook would load and lock but never reach the model, and
  a plugin cannot tell which tier it runs in.

### D-26: v1 is Claude Code only

- **Status:** live
- **Decided:** 2026-10-21
- **Decision:** Chat and Cowork leave v1. The plugin ships no skills, and the repository stores nothing
  about playbooks; those surfaces are listed after v1 in PLAN.md.
- **Why:** The stubs needed a generator and a sync job only to copy server data into the repository, and
  they published playbook descriptions in a public repo. In Claude Code the mod reads everything live.

### D-27: ask_clarifying_question stays an MCP tool, caught by every client

- **Status:** live
- **Decided:** 2026-10-23
- **Decision:** `ask_clarifying_question` stays an MCP tool on yvoke-web, and every client catches the call
  before it reaches the server: the web chat in-process, yvoke-desktop in `canUseTool`, and this plugin's
  tool gate with `$.ui.ask` ([tool-policy.md](../specs/tool-policy.md)). This replaces the old plan to
  deny it in favour of `AskUserQuestion`.
- **Why:** Over MCP the tool asks nobody, so the client has to. Prompts and playbooks name the same tool
  on every surface. Claude Code removes the built-in `AskUserQuestion` from every subagent but keeps MCP
  tools, so only an MCP tool lets a specialist ask the user.

## Known gaps and not planned

Written down so nobody "fixes" them by accident.

- **Playbook text is not hidden from the user.** Any client connected to the server can read playbooks;
  they are not a secret today either.
- **No isolation from the user's own Claude setup.** The user's other plugins, skills, memory and MCP
  servers can affect answers, where Yvoke Desktop excluded them; managed settings narrow this but do not
  remove it. Outside a Yvoke session the mod changes nothing, but the plugin's `yvoke` server still offers
  its tools in every session.
- **No company cost reporting.** Model usage stays on each user's Claude subscription, as with Yvoke
  Desktop.
- **A rejected draft stays in the record.** Claude Code keeps every turn, so the planned team UI (PLAN.md
  Milestone 4) can only collapse rejected drafts on screen; the transcript, `/resume` and copy keep them.
- **VS Code, WSL and cloud sessions are not supported.** Hooks may run there, but no UI is drawn, or no
  plugin loads at all.
- **A subagent cannot use `AskUserQuestion`.** Claude Code removes it from every subagent, so a specialist
  can ask the user only through an MCP tool, the server's `ask_clarifying_question` (D-27).
- **`/branch` can take the wrong parent.** A branch finds its parent through the `$.store` key `ended`. If
  another Claude Code process ends a session in between, or that write fails, the branch takes the wrong
  session's flag and setup ([session.md](../specs/session.md)).
