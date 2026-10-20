# P1-13 Keep plugin-control tools away from models that should not call them

**Release:** v1 · **Size:** S · **Type:** feature · **Status:** done

The code lives in [`yvoke-dev/yvoke-web`](https://github.com/yvoke-dev/yvoke-web) and follows that
repository's rules (its `CLAUDE.md`: strict TDD, unit tests in `src/test`, integration tests `*IT.java` in
`src/it`, its `spec/` chapter updated in the same change). This plan stays in the plugin repository, as
every task plan does. Facts below were read from yvoke-web `main` at `123045e` (release 1.3.0) on
2026-10-20.

**Done when:** the web's in-app assistant cannot be given `get_system_prompt`, `list_areas`,
`list_playbooks` or `get_playbook` (they are not in its tool registry, so no playbook can grant them and
the admin playbook editor does not offer them), while every MCP client still finds all four in
`tools/list` on `/mcp`. Later plugin-only tools (`list_profiles`, `submit_feedback`, the sync tools) land
on the same side by one annotation.

## How tools reach each model today

- yvoke-web builds **one** list, the `mcpToolCallbacks` bean in `mcp/McpToolsConfig`: three tools added by
  hand, then every `@Component` in `mcp.tools` turned into callbacks with `ToolCallbacks.from(bean)` (its
  `@Tool` methods).
- The MCP server publishes exactly that list. The MCP annotation scanner is off
  (`spring.ai.mcp.server.annotation-scanner.enabled: false`), so `@McpTool` is not what publishes a tool;
  spring-ai 2.0.0-RC1's `ToolCallbackUtils.aggregateToolCallbacks` collects `List<ToolCallback>` beans
  **and** `ToolCallbackProvider` beans.
- `RagService` takes the same list (`List<ToolCallback>` constructor argument) as its `toolRegistry`. The
  in-app assistant is deny-by-default: a turn offers only the tools the playbook's `tools` list names. But
  the admin playbook editor offers every registry name as a choice (`RagAdminController.viewPlaybooks`,
  `availableTools`), so an admin can tick `get_playbook` and the web model then calls it.

## What changes

D-23 (Eduard, 2026-10-20), the recommended option of [design 5.4](../design.md#54-which-model-sees-which-tool-p1-13): the tools stay
on the one `/mcp` server (one sign-in) and leave the in-app assistant's tool set.

- A new annotation `@ClientOnlyTools` (package `mcp`) marks a tool class whose tools are for MCP clients
  only. `AreaTools`, `PlaybookTools` and `GetSystemPromptTool` carry it.
- `McpToolsConfig` sorts each scanned class by the annotation:
  - unmarked classes go into `mcpToolCallbacks` as today (renamed `sharedToolCallbacks`, since it now
    means "the web assistant and MCP clients");
  - marked classes go into a new `ToolCallbackProvider` bean, `clientOnlyToolCallbacks`. The MCP server
    publishes it; `RagService` never sees it, because it takes only the `List<ToolCallback>`.
  - A name already registered on one side is not registered again on the other (one shared name set).
- The tools themselves, their descriptions and their behaviour do not change. Their descriptions already
  say to call them only when a Yvoke playbook skill says so, which covers Chat and Cowork, where the model
  does see them (D-04).
- `spec/07_using_the_assistant_from_other_tools.md` says these tools are offered to connected clients
  only, never to the in-app assistant, and that a playbook naming one gets nothing for it.

Out of scope: the desktop app's own model, which reads `/mcp` like any client and filters by playbook
itself; a second endpoint (the alternative in design 5.4); the sync and feedback tools, which do not exist
yet.

## Files that change

In yvoke-web (paths under `src/main/java/de/palsoftware/yvoke/` unless shown):

| File | Change |
| --- | --- |
| `mcp/ClientOnlyTools.java` | New type-level annotation, with a comment saying why. |
| `mcp/McpToolsConfig.java` | Splits the scan into the shared list and the client-only provider. |
| `mcp/tools/AreaTools.java`, `PlaybookTools.java`, `GetSystemPromptTool.java` | Annotated `@ClientOnlyTools`. |
| `src/it/.../mcp/JsonObjectsToolsIT.java` | The registered-list test splits into the shared list (10 tools) and the client-only provider (4 tools). |
| `src/it/.../mcp/McpServerEndpointsIT.java` | New test: the `RagService` registry holds none of the four; the existing `tools/list` test keeps asserting all fourteen. |
| `spec/07_using_the_assistant_from_other_tools.md` | As above. |

In this repository: this plan, and at close-out the P1-13 tick in [plan.md](../plan.md) and a line in
design 5.4 saying the recommended option was built.

## Order of work

1. **Test first:** in `McpServerEndpointsIT` (an existing context, so no new Spring context is minted),
   assert `ragService.getToolRegistry()` holds `search_corpus` and none of the four. Run it; see it fail.
2. Change `JsonObjectsToolsIT`'s exact-match test to the split; see it fail.
3. Add the annotation, mark the three classes, split `McpToolsConfig`. Both tests go green; the existing
   `tools/list` test stays green, which proves MCP clients still get all fourteen.
4. Mutation check: remove `@ClientOnlyTools` from one class, see step 1's test go red; drop the provider
   bean, see the `tools/list` test go red. Restore from a saved copy and `diff -q`.
5. Spec chapter 7. The playbook editor's choices are the registry's key set
   (`RagAdminController.viewPlaybooks`), so step 1's test covers them; `RagAdminControllerTest` mocks
   `RagService`, so a test there would prove nothing about the real registry.
6. `./mvnw verify -Pit-tests`, `python3 .antigravity/scripts/check_steering.py`, open the yvoke-web PR.

## Risks

- **A playbook that already names one of the four** loses it silently: the tool is no longer in the
  registry, so the turn just does not offer it. None of the shipped playbooks does, and the web assistant
  had no use for them. The admin editor stops offering the name; on the next save it is dropped.
- **spring-ai changing how it aggregates tools.** If a later spring-ai stops reading
  `ToolCallbackProvider` beans, the four vanish from `/mcp`. The existing `tools/list` IT fails then.
- **Alternatives rejected.** A second endpoint (design 5.4) means a second sign-in for every plugin user.
  A name list in `RagService` would make `rag` know about `mcp` (a package cycle `ArchitectureTest`
  refuses) and a new tool would need a second edit. Removing `@Tool` from the methods would leave them
  unpublished, since `@Tool` is what `ToolCallbacks.from` reads.

## Proof

- `McpServerEndpointsIT`: the new registry test and the existing `toolsListPublishesTheWholeToolCatalogue`.
- `JsonObjectsToolsIT.everyDocumentedToolIsPresentInTheRegisteredCallbackList` (split).
- yvoke-web: `./mvnw verify -Pit-tests` green, `check_steering.py` clean.
- This repository: `npm run check` (docs links; nothing else changes here).

## What differed from the plan

- The shared list starts from the client-only names, so a shared class can never add one of them back.
- `McpToolCatalogueParityTest`'s comment now says that, on a `@ClientOnlyTools` class, `@Tool` builds the
  MCP-only callback.
- `JsonObjectsToolsIT.theInAppChatAgentIsFedTheSameToolCallbackInstancesAsTheMcpServer` now reads the
  renamed bean, and its exact-match assertion also pins that the registry holds none of the four.
- Delivered in [yvoke-web#23](https://github.com/yvoke-dev/yvoke-web/pull/23). Unit run: 2144 tests, 0
  failures. IT run: 622 tests, 0 failures, 1 skipped. Mutation checks: removing the mark from
  `PlaybookTools` turns the registry test red; an empty provider makes `tools/list` miss all four.
