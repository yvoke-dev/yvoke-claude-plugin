# P1-01 get_system_prompt MCP tool

**Release:** v1 · **Size:** S · **Type:** feature · **Status:** in progress

The code lives in [`yvoke-dev/yvoke-web`](https://github.com/yvoke-dev/yvoke-web); this plan lives here.

**Done when** (from [plan.md](../plan.md)): the tool returns the `default-chat` text, and the server's
`initialize` result carries no base instructions.

## What the tool does

- Name `get_system_prompt`, one optional argument `name` (default `default-chat`).
- It resolves the name exactly as `GET /api/chat/v1/prompts/system/{name}` does today: a prompt stored
  under that name wins; otherwise `default-chat` means the prompt an admin picked as the default chat
  prompt (`SystemPromptService.getDefaultChatPromptName()`).
- It returns the prompt text as plain text, the same text the desktop gets in `systemPrompt`.
- Unlike the REST endpoint, it does not answer an unknown name with an empty string. It returns
  `ERROR: system prompt '<name>' does not exist.` (design.md 5.3: errors start with `ERROR:`). An empty
  base instruction would let P1-07 start a session silently without them; an error lets it fail closed.
- It returns only **chat** prompts. Prompts share one namespace with the ingest prompts (types `KG` and
  `SUMMARIZE`), which are not base instructions and are not meant for clients. A name of another type
  gets the same "does not exist" error, so the tool does not reveal which ingest prompts exist.
- The description tells a model to call it only when a Yvoke playbook skill says so (design.md 5.4).
- The base instructions are **not** sent as MCP `instructions` (D-12). Nothing sets them today; a test
  pins that it stays so.

The REST endpoint (`DesktopSyncController`) and the desktop are unchanged (design.md 5.1). The new
service method copies its lookup rather than replacing it, because the endpoint must keep returning any
type and `""` for an unknown name.

## Files that change

In yvoke-web:

| File | Change |
| --- | --- |
| `rag/prompt/SystemPromptService.java` | New `findChatPrompt(String name)`: the name resolution above, moved out of the controller, typed `CHAT`. |
| `mcp/tools/GetSystemPromptTool.java` (new) | The `@Component` with `@McpTool` and `@Tool` on one method, as the other tools do. Registered by the existing classpath scan in `McpToolsConfig`, so `McpToolsConfig` does not change; P1-06 adds its own class beside it without touching shared code. |
| `src/test/.../mcp/tools/GetSystemPromptToolTest.java` (new) | Unit tests (below). |
| `src/test/.../rag/prompt/SystemPromptServiceTest.java` | Tests for `findChatPrompt`. |
| `src/test/.../mcp/tools/McpToolCatalogueParityTest.java` | Add the new class to `TOOLS`. |
| `src/it/.../mcp/McpServerEndpointsIT.java` | `get_system_prompt` in the full-catalogue list; a `tools/call` that returns a stored default prompt; `initialize` carries no `instructions`. |
| `src/it/.../mcp/JsonObjectsToolsIT.java` | Add the name to the documented-tools list. |
| `spec/07_using_the_assistant_from_other_tools.md` | A row: AI clients can fetch the base instructions; how unknown names behave. |

In this repository: this plan, then `finish-task` ticks P1-01 in [plan.md](../plan.md) with the PR link.
`docs/specs/` has nothing for P1-01: no plugin behaviour changes until P1-07.

## Order of work

Each step starts with a test, seen red before the code that makes it green.

1. `SystemPromptServiceTest`: a stored chat prompt by name; `default-chat` falls back to the admin's
   default; blank or missing name means `default-chat`; an unknown name and a `KG`/`SUMMARIZE` prompt are
   empty. Then `findChatPrompt`.
2. `GetSystemPromptToolTest`: text returned as is; default name; unknown and wrong-type names return the
   `ERROR:` line; a service exception returns `McpToolUtils.toolError`. Then the tool class.
3. Parity test and the two IT lists: add the name, see them fail (the parity test by its list, the ITs by
   the missing registration before the class exists), then green.
4. `McpServerEndpointsIT`: `tools/call get_system_prompt` with a prompt the test stores and selects as
   default returns its text. `initialize` has no `instructions` key; seen red by setting
   `spring.ai.mcp.server.instructions` in the test once, then removed.
5. `DesktopSyncControllerTest` stays green unchanged, which proves the REST endpoint did not move.
6. Spec chapter 7, then the full verification below.

## Risks

- **The in-app assistant sees the tool.** It shares the MCP tool list (`RagService.toolRegistry`). It is
  only offered tools a playbook's `allowedTools` names, so no playbook gets it by accident. Hiding it for
  good is P1-13.
- **IT context cache** (yvoke-web pitfall): the new IT cases reuse `McpServerEndpointsIT`'s existing
  context and add no new `@SpringBootTest` configuration.
- **Rejected: serving the prompt as MCP `instructions`.** D-12: every connecting session, including
  coding sessions, would get it, and Yvoke sessions twice.
- **Rejected: exposing the REST endpoint to the plugin.** The mod has no token for yvoke-web REST
  (design.md 4.1).
- **Rejected: returning `""` for unknown names, as REST does.** That serves the desktop, which runs on
  with no prompt; for the plugin it would hide a misconfiguration.
- **Rejected: returning any prompt type.** It would hand ingest prompts to every signed-in client for no
  use.

## Proof

- Done-when half 1: `McpServerEndpointsIT` `tools/call` case, plus `GetSystemPromptToolTest`.
- Done-when half 2: `McpServerEndpointsIT` `initialize` case.
- yvoke-web checks, output in the yvoke-web PR:
  - `./mvnw test` (unit and JS tiers, Spotless)
  - `./mvnw verify -Pit-tests` (needs Docker; if the cloud session cannot run Docker, yvoke-web CI runs
    it on the PR and the PR says so)
- This repository: `node scripts/check-docs.mjs` prints `check-docs: OK (N Markdown files)`. The plugin
  rows of the verification table do not apply yet (no scaffold).
