# Review of PLAN.md draft v3

**Date:** 2026-10-05 · **Reviewed:** `PLAN.md` at `a274e6a` · **Checked against:** Claude Code 2.1.289 (its
generated mod API declarations, `claude-code.d.ts`), the public docs pages listed in PLAN.md section 9, and a
clone of `yvoke-dev/yvoke-desktop` at `main`.

The plan is solid: the phase order, the spikes-first approach, the explicit decisions and the "port the tests
first" rule are right, and most platform statements check out against the current build. The findings below
are the places where the plan relies on something the platform does not do, or misses a constraint that
changes a decision. Each one says what to change. Fixes that are plain corrections are already applied to
PLAN.md in the same pull request; the rest need a decision from the plan's owner.

Severity: **High** changes a decision or breaks a guarantee the plan promises. **Medium** changes a task's
design. **Low** is a detail an implementer would otherwise trip on.

---

## High

### H1. Policy hooks fail open unless they are written not to

Principle 3.3 says "everything else that enforces policy fails closed". The engine does the opposite by
default:

- A hook that throws, returns a shape the event refuses, or runs past its budget (10 s of its own time;
  `$` calls and `next` do not count) **is skipped, and the hooks beneath and the engine run in its place**.
  For `tool.call` that means the tool runs. Only a `.catch` handler registered on the hook can answer
  instead, and it has a 1 s grace (`HookBudget.catchMs`).
- `skill.prompt`: "A hook that fails passes it through", so a throwing live-playbook fetch (P1-07) leaves
  the generated stub as the skill's text and the model answers anyway.
- If the hooks worker crashes three times, Claude Code unloads every installed mod for the rest of the
  session (until `/reload-plugins`). `--safe-mode`, `--bare` and a user's own `"disableAllHooks": true`
  also turn installed mods off, without disabling the plugin. Its skills and connector keep loading.

**Change:**

- Add to 3.3: every enforcing hook (`tool.call`, `prompt.submit` gate, `agent.spawn`, `skill.prompt`) is
  registered with a `.catch` that answers the safe result (`{ deny }`, `{ drop }`, or a refusal text), and
  each of those tasks' **Done when** includes a test for the throw and the timeout path.
- P1-07: on failure, **return** `{ text }` telling the model to stop and report `Yvoke Backend: …`; never throw.
- 3.4 and the risk table: list `disableAllHooks`, `--safe-mode` and the worker crash next to "user disables
  the plugin". D-09's managed `permissions.deny` is the only floor that survives all of them.

### H2. A mod installed from Git, URL or claude.ai sync is always a *user* mod

From the admin docs: "A plugin that Claude Code copies into its cache counts as a user's, even when managed
`enabledPlugins` enables it. That covers every plugin from a GitHub, git, URL, or npm source." The same goes
for a plugin an organization turns on for its members on claude.ai. Only a plugin loaded in place from a
**directory marketplace that MDM copied to the machine** counts as the organization's.

Consequences the plan does not draw yet:

- Route B (Git marketplace pushed by managed settings) and route C (claude.ai org sync) both produce a user
  mod. Under `allowManagedModsOnly` it does not load at all (the plan has this), and it can never be listed in
  `prependPlugins`, so **D-09's "organization-managed policy mod" option needs the directory route too**.
- The built-in guard that makes `deny` rules win over user mods loads only on machines with managed settings
  or for Team/Enterprise sign-ins. Pro/Max users without managed settings have no guard.

**Change:** D-01, D-09 and P7-04 are one decision in practice ("how locked down, and therefore which install
route"). Merge them, or make D-09 and P7-04 explicitly depend on each other, and add the directory route as
route D in P7-04.

### H3. D-03's premise is out of date: the mod *can* call a claude.ai connector

The plan says `$.mcp.connect` only reaches servers in the plugin's own manifest, "so unless P0-04 shows
`$.mcp.call` reaching a claude.ai connector, the answer is `.mcp.json`". The current declarations split the
two:

- `$.mcp.call(server, tool, args)` "Calls `tool` on one of the engine's connected MCP servers with the
  engine's own connection and credentials", where `server` is the name `/mcp` lists. The doc example is
  `$.mcp.call("claude.ai Gmail", "create_draft", …)`. A `cached` server is dialed on first use.
- `$.mcp.connect(server)` is the one limited to the plugin's own manifest (refusal `unlisted`).

So reachability is not the deciding factor. D-03 should be decided on **sign-in** (Entra through a claude.ai
connector vs MCP OAuth from `.mcp.json`), **naming** (the connector's name is not known at build time, so
P1-03 discovers it, for example by matching the server's tool names in `$.tool.list()`), and **Chat/Cowork**
(where only the connector route exists). Corrected in PLAN.md (D-03, P0-04, risk table); P0-04 should still
confirm it on the Desktop Code tab.

### H4. `skill.prompt` cannot tell who invoked a playbook, and carries no metadata

`skill.prompt` fires for `/yvoke:<skill>`, for the model's own `Skill` tool call, and for a preload. Its
input is only `{ skill, text }`. Two consequences:

1. **The model can switch the active playbook on its own** by invoking another playbook skill, and P1-08
   cannot tell that apart from the user picking one. Yvoke Desktop only lets the user pick.
2. **The mod cannot read `tools` / `codeExecution` from the skill's metadata** through the event (P1-04
   writes them into SKILL.md "so the mod can read them"). Reading the file would need `$.fs`, which 3.4
   wants to keep out of the mod.

**Change:**

- Generated playbook skills set `disable-model-invocation: true`, so only the user starts one (🔍 check what
  that does in Chat/Cowork, where the model choosing a skill is the main way a skill runs; P8-01 may need
  the opposite setting).
- P2-01: say explicitly whether `Skill` is allowed in a Yvoke session. It is missing from the allow list
  today, which denies model-invoked skills. That is probably right, but it should be written down.
- Scoping metadata comes from the server (`get_playbook`, P1-06) in the live design. In the copied design the
  generator also writes a `src/playbooks.generated.ts` map that the mod imports. Mods can import plugin
  files, and that needs no `$.fs`.

---

## Medium

### M1. P1-01 names the wrong event for adding the base instructions

`prompt.section` fires once per *existing* named section and can rewrite or drop it. It cannot add one.
Adding a section is `prompt.compose`: append `{ id, text, scope: 'session' }` to `next(e)`'s `sections`.
Either answer is cached until `$.ui.invalidate(...)`. Corrected in PLAN.md.

### M2. P0-07's main open question is already answered by the current build

`tool.call`'s input includes `agentId` (absent on the main loop, set for a subagent's call). `agent.spawn`
carries `parentAgentId` and `subagentType`, and can return `{ deny }`. `turn.complete` carries `agentId` and
`isAborted`, and its `{ text }` result is shown *beneath* the answer and never rewrites the record. P6-04 can
therefore tell the lead from a specialist. Keep P0-07 to confirm the behaviour, but Phase 6 design does not
need to wait for it. Noted in PLAN.md.

### M3. The Yvoke-folder check (P1-10) cannot be done once at session start

`$.session.root()` moves during a session on `/cd`, on a directory change by the host (the Desktop app), or
on a worktree move. A shell `cd` does not move it. `$.session.cwd()` is "the directory the session runs in".
**Change:** re-evaluate the scope in each enforcing hook (one cheap `$` call, outside the hook's budget),
not once in `session.start`. Compare paths on segment boundaries **case-insensitively on macOS and Windows,
with `\` and `/` treated alike**. Decide whether symlinks matter. Resolving them needs
`$.fs.stat(path, { resolve: true })`, which adds `$.fs.stat` to the security review's `calls:` line.

### M4. P1-08 / P0-06: the hook for `/clear`, `/resume` and `/branch` exists

Function hooks can hook the settings-hook events as `classic.<Event>`. `classic.SessionStart` carries
`source: 'startup' | 'resume' | 'clear' | 'compact' | 'fork'`, and `session.end` carries
`reason: 'clear' | 'resume' | …` with the ending session's id. That is the signal P1-08 needs to drop,
restore or keep the active playbook. P0-06 should confirm that `fork` is what `/branch` raises.

### M5. Deployment configuration in `userConfig` is user-editable unless managed settings win

Every non-secret `userConfig` field is a row in the `/config` menu, and `$.config.set` exists. The Yvoke
folder (P1-10), web domains (P2-04), role models and budgets (P6-02) can therefore be changed by the user
unless managed `pluginConfigs` override them. P2-04 already flags this with 🔍. Add the same 🔍 to P1-10,
and have a fallback ready: serve the security-relevant values (web allow-list) from yvoke-web instead of
from `userConfig`.

### M6. The repository is already public

`yvoke-dev/yvoke-claude-plugin` is public (as are `yvoke-desktop` and `yvoke-web`). That settles most of D-02
and has a direct consequence for D-04: **copied playbooks would be published**. If any playbook text is
confidential, D-04 must be "live", or the copied text must live in a different, private marketplace.

### M7. `$.store` is shared across concurrent sessions

`$.store` is one JSON file per plugin, 4 MiB in total, shared by every open session on the machine. A
read-modify-write of one key from two open sessions loses an update. P5-02's queue and P1-08's
"playbook per session id" map should use one key per session or per turn (`queue/<turnId>`), not one shared
array.

---

## Low

- **L1. Citations on the Desktop surface (P0-05/P3-02).** `Markdown`'s `onLinkPress` raises `ui.press` only
  "where the surface reports clicks"; the reference names the fullscreen terminal. Plan for the source-button
  row as the default on `desktop` and treat clickable inline links as a bonus.
- **L2. Remote Control** (driving a local session from claude.ai or the phone): hooks run, the UI draws only
  in the local terminal, and the surface can be `mobile`, which has no `Input` or `Select`. Add it to
  section 2's "not targeted" list, or the feedback form needs a command fallback there.
- **L3. Server tool inventory.** The Yvoke MCP server this project is connected to exposes today:
  `search_corpus`, `get_section`, `get_toc`, `list_documents`, `get_graph_neighbors`,
  `search_graph_entities`, `query_json_objects`, `get_json_schema`, `verify_citations`,
  `ask_clarifying_question`. None of `get_playbook`, `submit_feedback`, `list_profiles` or the sync tools exist
  yet, which confirms that the `server` tasks sit on the critical path (risk table).
- **L4. P2-06 preflight cost.** `$.model.complete` spends the user's own plan or API quota on every first
  message and every playbook switch. Say so in the user guide and the security pack.
- **L5. Default Yvoke folder on Windows.** `~/Yvoke` needs a defined Windows spelling
  (`%USERPROFILE%\Yvoke`) for P1-10, P7-03 and P7-08.
- **L6. Claude Desktop extensions (`.mcpb`).** These package *local* MCP servers. The Yvoke server is remote,
  so they are not needed. Worth one line in section 7 so nobody proposes them as a distribution route.

## Checked and correct

These statements in the plan match the current build and docs: mods need Claude Code ≥ 2.1.287; hooks run
but nothing draws in VS Code, `-p` and cloud sessions; no plugins in WSL sessions; trust prompt before any mod
loads; `allowManagedModsOnly`, `allowModsToOverrideDenyRules` and managed `PreToolUse` running first; command
names are letters, digits, `_`, `-` (no `:`); `Markdown`/`Text` strings at most 10,000 characters; `$.store`
4 MiB; `$.model.complete` resolving `isAnswered: false` instead of rejecting, with time inside it not
counting against the hook budget; `$.prompt.submit` waiting for an idle session; `/clear` firing no new
`session.start`; `prompt.submit` `{ drop }`; `tool.describe` only rewording or deferring; `agent.offer` hiding
agent types; `$.tool.register` tools named `mcp__<plugin>__<name>`; test timeouts of 5,000 ms by default with
`timeoutMs`; the Chat/Cowork component matrix, including `${user_config.*}` URLs being ignored in Chat;
`CLAUDE.md` = `@AGENTS.md` (Claude Code never reads `AGENTS.md` twice). Every yvoke-desktop file and symbol the
plan names exists at the paths listed in PLAN.md section 10.
