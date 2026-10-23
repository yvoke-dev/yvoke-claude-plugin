# Product Intent & Principles

**Yvoke for Claude** (`yvoke-claude-plugin`) is a Claude Code plugin and in-process mod that brings [Yvoke Desktop](https://github.com/yvoke-dev/yvoke-desktop)'s capabilities into Claude Code (both the Claude Desktop **Code** tab and the terminal).

How the system is built is in [architecture.md](architecture.md), what is implemented today is in [../specs/index.md](../specs/index.md), and the feature roadmap lives in [../../PLAN.md](../../PLAN.md).

## 1. Problem & Goal

Consultants and support engineers rely on Yvoke for grounded, cited answers from the Yvoke knowledge base, while already working daily in Claude on their organization's Claude Team or Enterprise plan. Without this plugin, they must switch to a separate Electron app (Yvoke Desktop), or ask plain Claude without playbooks, enforced tool policies, or verified citations.

**Goal:** Deliver grounded, playbook-scoped answers and multi-agent investigations inside Claude Code on the user's existing Claude subscription:
- **Implemented today:** Users start a Yvoke session with `/yvoke`, accept or change the session's **area** and versioned **playbook** in the setup band above the prompt (locked on the first question and preserved across `/resume` and `/branch`), receive answers guided by the server's base instructions and playbook, and run under a deny-by-default tool gate (scoped to the playbook's knowledge-base tools) and per-question turn ceilings.
- **Planned in [`PLAN.md`](../../PLAN.md):** Production Entra ID sign-in defaults, playbook preflight checks, domain-restricted web access (`WebSearch`/`WebFetch`), sandboxed `execute_code`, clickable citations opening a source passage pane, multi-agent investigations (**lead → specialists → reviewer**), and `/yvoke-doctor` diagnostics.

## 2. Target Users & Surface

- **Consultants and support engineers:** Ask domain questions (e.g., OIM) and verify cited passages, while also using plain Claude Code for normal coding work on the same machine.
- **Organization administrators:** Configure the plugin via `userConfig` or managed settings without locking down non-Yvoke sessions.
- **Target surface (v1):** **Claude Code** (Desktop **Code** tab and terminal). Chat and Cowork surfaces, conversation sync, and 👍/👎 answer ratings are deferred until after v1.

## 3. Core Product Principles

1. **The server is the source of truth:** Base instructions, areas, playbooks, profiles, and knowledge-base passages are fetched live from `yvoke-web` over MCP. No playbook text is stored in this public repository, and no stale cached fallback is ever used when the server is unreachable (questions fail clearly with a `Yvoke Backend:` message).
2. **Opt-in per session (`/yvoke`):** Yvoke works in any trusted folder. The mod's UI, system prompt, and tool restrictions apply **only** in a session started with `/yvoke` before the first question. Every other Claude Code session behaves as if the plugin were not installed.
3. **One session, one setup:** A session's area, mode, and playbook or profile are selected before the first question and lock once the first question enters the session. Choosing a different setup requires starting a new session (`/clear`, then `/yvoke`). `/resume` and `/branch` preserve the session's setup and upgrade a locked playbook to the latest server version with a notice toast.
4. **Deny by default in a Yvoke session:** Only the knowledge-base tools declared by the active playbook (or the default knowledge-base tool set), `ToolSearch`, and `AskUserQuestion` are permitted today (with domain-restricted `WebSearch`/`WebFetch` and sandboxed `execute_code` planned in [`PLAN.md`](../../PLAN.md)). When the model calls `ask_clarifying_question` and the playbook grants it, the plugin catches the call and shows the question in Claude Code's own dialog.
5. **Policy hooks fail closed; preflight fails open:** Because the Claude Code engine skips a hook that throws or exceeds its timeout budget, every enforcing hook (`tool.call`, `prompt.submit`, `prompt.compose`, `turn.step`) registers a `.catch` handler returning the safe refusal (`{ deny }`, `{ drop }`, or refusal section). Only the planned playbook preflight suggestion check fails open.
