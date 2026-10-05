# Yvoke for Claude v1: intent

**Author:** Eduard · **Status:** accepted (decisions D-01 to D-14 taken 2026-10-05) · **Updated:** 2026-10-05

Why we are building the plugin, who it is for, and what they will do with it. What the plugin must do is in
[requirements.md](requirements.md), how it is built is in [design.md](design.md), and the work and its
progress are in [plan.md](plan.md).

**What we are building:** one Claude plugin that brings Yvoke Desktop's capabilities into Claude. It
works fully in **Claude Code** (the Desktop app's **Code** tab and the terminal) through a **mod**. In v1,
**Chat** and **Cowork** get a reduced form through the same plugin's playbook skills and the Yvoke
connector (D-10).

**Reference implementation:** [`yvoke-dev/yvoke-desktop`](https://github.com/yvoke-dev/yvoke-desktop). Its
`spec/` chapters say *what* each feature is for; its `tests/` say *exactly* how it behaves.

## 1. Problem

Consultants and support engineers get grounded, cited answers from the Yvoke knowledge base through Yvoke
Desktop, a separate Electron app that runs its own agent loop and has to be installed, signed in and kept
up to date on its own. Many of them already work in Claude, on their organization's Claude plan. Today they
switch apps to ask Yvoke, and in Claude itself they have no playbooks, no enforced tool rules and no
verified citations.

## 2. Goal

Consultants and support engineers should get the same grounded, cited, playbook-scoped answers from the
Yvoke knowledge base inside Claude that they get in Yvoke Desktop, on their own Claude subscription, without
installing a separate app.

**Success looks like:**

- A consultant installs one plugin (or IT installs it for them), opens the Code tab in Claude Desktop, accepts
  or changes the session's area, mode and playbook (defaults: OIM, single agent, `oim-full`), asks a
  question, and gets an answer whose citations open the cited passage.
- The playbook decides which Yvoke tools the assistant may use, and that rule is enforced in code, not only
  requested in a prompt.
- A multi-agent profile runs lead → specialists → reviewer, with review enforced in code.
- Behaviour is pinned by automated tests in this repo, as it is in yvoke-desktop.
- After v1, once conversations sync: 👍/👎 feedback on an answer reaches yvoke-web (D-07, D-08).

## 3. Users

- **Consultants and support engineers** ask questions about OIM and get cited answers. Some of them also use
  Claude Code for coding on the same machine.
- **An organization admin** adds the Yvoke connector and pushes the plugin. Users are on Claude Team or
  Enterprise (D-01).
- **The yvoke-web team** owns the server, its playbooks, profiles and base instructions.

## 4. Use cases

Each use case names the tasks in [plan.md](plan.md) that deliver it.

| # | Who | Use case | What they see | Tasks |
| --- | --- | --- | --- | --- |
| U1 | Admin, once | **Roll it out** | Adds the Yvoke connector for the organization and pushes the plugin; the pilot installs it from the Git marketplace instead. Nothing else on the machine is locked down. | P7-03, P7-04 |
| U2 | Consultant, first use | **Install and sign in** | The first time the plugin talks to Yvoke, a browser window opens for the usual Microsoft sign-in. After that the token refreshes on its own. | P0-09, P1-02 |
| U3 | Consultant, daily | **Ask with the defaults** | Opens the Code tab in any folder, types `/yvoke`, then just types the question. OIM, single agent and `oim-full` apply, and the answer comes back with citations. | P1-07, P1-08, P1-10 |
| U4 | Consultant | **Pick a specific playbook** | Changes the playbook in the setup band before the first question. The preflight may suggest a better fit; the user decides. | P1-08, P2-06 |
| U5 | Consultant | **Check a source** | Clicks a citation to read the exact passage, with the surrounding section behind a collapsed control. | P3-01 – P3-03 |
| U6 | Consultant | **Run an investigation** | Chooses a multi-agent profile as the mode. Cards show each specialist's sub-question and answer, the reviewer's verdict, and a banner if the final answer was not approved. | Phase 6 |
| U7 | Consultant | **Answer a clarifying question** | When the question is ambiguous, Claude asks with its native question prompt, then continues. | P1-09, P6-08 |
| U8 | Consultant | **Start over with another setup** | Area, mode and playbook are fixed once the first question is sent. `/clear` ends the Yvoke session, and `/yvoke` starts a new one with a fresh setup band; `/resume` brings back an old session with its setup. | P1-08 |
| U9 | Developer on the same laptop | **Code as usual** | In any session where they did not type `/yvoke`, Claude Code behaves exactly as before. | P1-10 |
| U10 | Chat or Cowork user | **Use a playbook outside Claude Code** | Invokes a Yvoke playbook skill. Claude fetches the playbook from the server and answers from the knowledge base, without the setup band, the tool rules or the source pane. | P1-01, P1-04, P1-06, P8-01 |
| U11 | Anyone | **Server unreachable** | The question fails with a `Yvoke Backend:` message. There is no stale cached answer. | P1-03, P1-07 |
| U12 | Consultant | **Very long question** | A loop that hits the turn ceiling still delivers what it has, marked *stopped at the turn limit*. | P2-08 |

After v1: rate an answer with 👍/👎 (Phase 4) and find Claude conversations in the Yvoke account (Phase 5).

## 5. Scope and target surfaces

One plugin, three surfaces. Each surface loads only the parts it supports.

| Surface | Loads from this plugin | v1 (D-10) | Later |
| --- | --- | --- | --- |
| **Claude Code**: Desktop **Code** tab, terminal, JetBrains | connector, mod (the playbook skills load but are not offered, D-11) | **Full parity**, the primary target | Rating and sync |
| **Cowork** (Desktop) | playbook skills, connector (hooks and agents possible, no mod) | Playbook stubs over the organization connector | Tool scoping through hooks (P8-02), MCP App widgets (P8-03) |
| **Chat** (Desktop, web, mobile) | playbook skills, connector (no agents, hooks or mod) | Playbook stubs over the organization connector | Widgets and citation links (P8-03, P8-04); anything enforced must live on the server |

Not targeted: VS Code extension UI (mod hooks run there, but nothing it draws is shown), WSL sessions in the
Desktop app (no plugins), cloud sessions (hooks run, nothing is drawn).

## 6. Not in v1

- Conversation sync into the Yvoke account (D-07) and rating answers, which builds on sync (D-08).
- Cowork hooks, MCP App widgets and citation URLs for Chat and Cowork (P8-02 to P8-04).
- An IT lockdown of shell and file tools (D-09). Claude Code's own permission prompts still apply.

## 7. Constraints

- Users are on Claude Team or Enterprise; an admin adds the connector and can push the plugin (D-01).
- The marketplace repository is public, so it never holds playbook text, secrets or customer data (D-02).
- yvoke-web stays the source of truth; there is no cached fallback when it is unreachable.
- yvoke-desktop's Entra registration and REST API do not change.
- The mod API is early access and needs Claude Code ≥ v2.1.287; it changes between releases.
- Using Yvoke is opt-in; there is no IT lockdown (D-09).

## 8. Open questions

- What to call the new "area" concept on yvoke-web, and whether its "profiles (knowledge bases)" already
  are areas (P1-12).
- The production API scope (the desktop's ends in `/desktop`) and the claude.ai connector callback URL for
  the new Entra client (P0-09).
- Mod API behaviour the spikes settle: tool names per surface, drawing in the Desktop Code tab, `/branch`,
  dynamic subagents (P0-04 to P0-08).

## 9. Not planned and known gaps

Write things down here when they are decided against, so nobody "fixes" them by accident.

- **Hiding playbook text from the user.** Skills are readable in Claude's skill viewer; playbooks are also
  readable by any connected client of the server. Not a secret today either.
- **Isolation from the user's own Claude setup.** In Claude, the user's other plugins, skills, memory and
  connectors can affect answers; Yvoke Desktop excluded them. Managed settings can narrow this, not remove it.
  The other direction is handled: outside a Yvoke session the mod changes nothing (P1-10), but the
  playbook skills and the Yvoke connector are still offered in every session.
- **Company cost reporting.** Model usage stays on each user's Claude subscription, as with Yvoke Desktop.
- **Removing a rejected draft from the record.** Claude Code keeps every turn; the mod only collapses
  rejected drafts on screen (P6-07). The transcript, `/resume` and copy still contain them.
- **Behaviour in VS Code, WSL and cloud sessions.** Hooks may run there, but no UI is drawn (or no plugin
  loads at all); not supported.
