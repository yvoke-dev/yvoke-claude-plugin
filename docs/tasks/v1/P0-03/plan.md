# P0-03 Contributor guide

**Release:** v1 · **Size:** S · **Type:** feature · **Status:** in progress

A developer who has never worked on the plugin gets from a clean machine to a mod that hot-reloads in
their own Claude Code, in the terminal and in the Desktop app's Code tab, by following one page:
`docs/contributing.md`. Task entry: [plan.md, P0-03](../plan.md#phase-0--foundation-and-spikes).

## What the guide covers, and what it links to instead

Most of the facts already exist. The guide puts them in the order a newcomer needs them and links to the
place that owns each one, so nothing is written twice.

| Topic | In the guide | Owned by (linked, not copied) |
| --- | --- | --- |
| What you need: Claude Code (oldest supported 2.1.287), Node 22 and `npm install`, a trusted folder, mods switched on | Short list | CI matrix in `.github/workflows/ci.yml` |
| Load the mod for one session: `claude --plugin-dir plugins/yvoke` | Steps | [design 4.5](../design.md#45-development-loop) moves here (see below) |
| Hot reload in the terminal: saving a file runs `register` again; `$.state` and `$.store` stay | Steps and what you see | same |
| Desktop Code tab: `CLAUDE_CODE_PLUGIN_DIRS` (absolute path) and `CLAUDE_CODE_PLUGIN_DIR_WATCH=1` in the `env` block of `~/.claude/settings.json`, then a new session; or the local marketplace and `/reload-plugins` | Steps, with the `settings.json` snippet | marketplace route: [README, "Use it locally"](../../../../README.md#use-it-locally) |
| Connecting to a local yvoke-web | One line | README, "Use it locally" |
| Type-checking: `npm run typecheck` lays `.claude-plugin/types/` and runs `tsc`; where to grep the API; never commit the folder | Short, with why | [design 4.2](../design.md#42-the-mod-api-where-the-truth-is), `scripts/lay-types.mjs` |
| Reading `claude --debug` (and `--debug-file <path>`): the dim `yvoke: … refused` line, where a skipped hook or a module that did not load shows up | Steps and what to look for | design 4.5 |
| Why nothing shows: untrusted folder, `--safe-mode`, WSL in the Desktop app, `-p`, VS Code, cloud sessions, "hooks modules are turned off" | Troubleshooting list | design 4.5, [AGENTS.md](../../../../AGENTS.md#verification) |
| Running the checks before a push | One line: `npm run check` | AGENTS.md verification table |
| How a change is made: start-task, plan approval, tests first, finish-task | Two lines | [docs/sdlc.md](../../../sdlc.md), the skills |
| Mod rules that bite (`$` across files, no wrappers, `.catch` on enforcing hooks) | One line | [design 4.4](../design.md#44-rules-that-are-easy-to-get-wrong) |

## Files that change

| File | Change |
| --- | --- |
| `docs/contributing.md` | New. The guide above. |
| `docs/tasks/v1/design.md` | Section 4.5 shrinks to a link to the guide, so the development loop lives in one place. Nothing else in design.md changes. |
| `README.md` | One row in the "Documents" table, and the "Quick look without installing" line points to the guide for hot reload. |
| `AGENTS.md` | One line under "Where to start": how to run the mod while working, see `docs/contributing.md`. |
| `docs/tasks/v1/plan.md` | Tick P0-03 with the PR link (finish-task). |
| `docs/tasks/v1/P0-03/plan.md` | This plan; status and anything learned while checking the steps. |

No code, tests or specs change: the guide describes how to develop, not how the plugin behaves.

**Shared files.** `README.md` and `AGENTS.md` may also be edited by the threads running P1-07, P1-09,
P3-01, P6-03 and P2-05 at the same time. My edits there are one line each, in places those tasks are
unlikely to touch (the Documents table, "Where to start"). If one of them merges first, I merge `main`
into this branch and keep both.

## Order of work

1. **Check each step before writing it**, on the Claude Code in this cloud session (2.1.293), and note
   the version in the guide:
   - `claude --plugin-dir plugins/yvoke` loads the mod (`--debug-file` shows the module loading);
   - saving `hooks/register.tsx` while that session runs reloads it (seen in the debug log, run under
     `tmux`, since a cloud session has no screen);
   - `npm install` and `npm run check` from a fresh clone and a fresh `CLAUDE_CONFIG_DIR`;
   - what `--debug` prints for a hook that throws and for a module that does not load (a throwaway
     change, never committed).
   What needs a screen (the Desktop Code tab, the dim line in the transcript) is written from design 4.5
   and checked in step 4.
2. **Write `docs/contributing.md`** from the table above.
3. **Move design 4.5 into the guide** and make the README and AGENTS.md one-line edits. Run
   `node scripts/check-docs.mjs` for the new links.
4. **Second-developer check (Done when).** Eduard, or someone he names, follows the guide on a machine
   with a fresh `CLAUDE_CONFIG_DIR` (as near to a clean machine as is practical) in the terminal and in
   the Desktop Code tab, edits a line in `register.tsx` and sees the change without restarting. Whatever
   they trip over is fixed in the guide in this PR. I guide it one step per message if wanted.
5. **finish-task**: all five checks, tick the plan, mark the PR ready.

## Risks

- **The Desktop and hot-reload steps can only be confirmed on a real machine.** A cloud session draws no
  mod UI. Mitigation: step 1 checks what the debug log can show, and step 4 is the real proof.
- **Facts go stale as Claude Code changes.** The guide names the version it was checked on, and links to
  the owner of each fact rather than copying it, so one edit fixes it.
- **Overlap with the README.** The README stays the place for installing against a local server (P0-11);
  the guide is for changing the mod. Considered merging them into the README and rejected: the README is
  for anyone who looks at the repo, and would grow by a page most readers do not need.
- **Considered keeping design 4.5 and only linking to it** from the guide. Rejected: a newcomer would read
  two pages for one loop, and the two would drift. Moving it leaves design section 4 about the code, not
  the workstation.

## Proof

- Step 4: a second developer on a fresh config gets a hot-reloading mod by following the guide, in the
  terminal and the Desktop Code tab. Recorded in this plan with the Claude Code version.
- `node scripts/check-docs.mjs` passes (links and anchors), and the other four checks in
  [AGENTS.md](../../../../AGENTS.md#verification) still pass (`npm run check`), shown in the PR.
