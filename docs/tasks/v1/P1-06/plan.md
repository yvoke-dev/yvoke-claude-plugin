# P1-06 `list_playbooks` and `get_playbook` MCP tools

**Release:** v1 · **Size:** S · **Type:** feature · **Status:** done

The code lives in [`yvoke-dev/yvoke-web`](https://github.com/yvoke-dev/yvoke-web) and follows that
repository's rules (its `CLAUDE.md`: strict TDD, unit tests in `src/test`, integration tests `*IT.java` in
`src/it`, its `spec/` chapter updated in the same change). This plan stays in the plugin repository, as
every task plan does. Facts below were read from yvoke-web `main` at `2a4b67e` on 2026-10-05.

## What the two tools do

Two read-only tools on the existing `/mcp` server, so the mod can fetch playbooks with `$.mcp.call` (it
cannot read MCP prompts) and so the Chat and Cowork stubs can tell the model to call `get_playbook` (D-04).

- **`list_playbooks()`** returns a JSON array, one object per playbook that a user can pick: `name`,
  `title`, `description`, `tools`, `codeExecution`, `targetAgent`, `prototype`. Same selection and fields
  as the desktop's `GET /api/chat/v1/playbooks` (`PlaybookService.listSpecializedPlaybooks`), so the
  orchestrator and reviewer playbooks are left out. Read live from the database on every call, so a new or
  deleted playbook shows at once (unlike MCP prompts, design 5.5).
- **`get_playbook(name)`** returns one JSON object: the same seven fields plus `text`, the playbook's full
  prompt. It looks the name up in all playbooks, as MCP `prompts/get` does today, with the name trimmed.
- **Errors** start with `ERROR:`, as the other tools' do (P1-03 relies on it): a blank name, an unknown
  name (`ERROR: playbook '<name>' not found.`), or a failure inside the tool (the shared
  `McpToolUtils.toolError` message, which never carries exception detail).
- **Descriptions** say the tools are for Yvoke clients and that a model should call them only when a Yvoke
  playbook skill tells it to (design 5.4).

**Area is not in this task.** The release plan's P1-06 entry names `list_playbooks(area?)` and an `area`
field, but no playbook has an area today; P1-12 adds the attribute and the filter. This task ships
`list_playbooks()` without the parameter, and the same pull request corrects the P1-06 entry in
[plan.md](../plan.md) so P1-12 owns `area` alone. Adding an optional parameter later does not break a caller.

## Files that change

In yvoke-web:

| File | Change |
| --- | --- |
| `src/main/java/de/palsoftware/yvoke/mcp/tools/PlaybookTools.java` (new) | One `@Component` with the two methods, each annotated `@McpTool` and `@Tool` with identical names and descriptions (the repository's parity rule). `list_playbooks` reads through `PlaybookService.listSpecializedPlaybooks`; `get_playbook` reads `PlaybookRepository.findByName` directly, because `PlaybookService.getPlaybook` is cached for 60 s. Both write JSON with the shared `ObjectMapper`. Being in `mcp.tools`, it is picked up by the existing classpath scan in `McpToolsConfig`, so no registration code changes. The single-playbook answer is a small record next to the tool (`PlaybookDto` fields plus `text`). |
| `src/main/java/de/palsoftware/yvoke/rag/prompt/PlaybookService.java` | Takes the MCP server as an `ObjectProvider` and looks it up only when a playbook changes, which breaks the startup cycle the new tool would close (see "What differed"). |
| `src/main/java/de/palsoftware/yvoke/mcp/prompts/PromptsService.java` | A cast on its `new PlaybookService(repository, null)` call, which became ambiguous with the new constructor. |
| `src/test/java/de/palsoftware/yvoke/mcp/tools/PlaybookToolsTest.java` (new) | Unit tests, `PlaybookService` and `PlaybookRepository` mocked. |
| `src/test/java/de/palsoftware/yvoke/mcp/tools/McpToolCatalogueParityTest.java` | Add `PlaybookTools.class` to its `TOOLS` list. |
| `src/it/java/de/palsoftware/yvoke/mcp/McpServerEndpointsIT.java` | Add `list_playbooks` and `get_playbook` to the names `tools/list` must contain, and `tools/call`s of both over the real endpoint with a seeded playbook, before and after its row is deleted. Reuses the existing context (no new context, per its pitfall on the TestContext cache). |
| `src/it/java/de/palsoftware/yvoke/mcp/JsonObjectsToolsIT.java` | Add both names to the documented tools that must be in the registered callback list. |
| `spec/07_using_the_assistant_from_other_tools.md` | Add the two tools under what a connected client can do, and that they are live while the prompt list is not. |

`PlaybookDto.java` is reused as the list item and does not change, so the REST API's shape stays the same.

In this repository: this plan, and the P1-06 entry in `docs/tasks/v1/plan.md` (area moved to P1-12).
`docs/specs/` does not change: it describes the plugin, and nothing in the plugin changes here.

## Order of work

Each step starts with a test that is seen failing, then the code that makes it pass.

1. **List.** Test: two specialist playbooks and one orchestrator playbook in the mock; `list_playbooks`
   returns valid JSON with the two specialists and all seven fields with their values, a missing `tools`
   as `[]` and a missing `targetAgent` as `specialist`. Then write `PlaybookTools.listPlaybooks`.
2. **Get.** Tests: a known name returns `text` plus the seven fields; `" oim-full "` is trimmed; an
   orchestrator playbook can be fetched by name. Then write `getPlaybook`.
3. **Errors.** Tests: unknown name, blank name and `null` each return a body starting with `ERROR:`; a
   repository exception returns the generic `ERROR:` message and no exception text. Then the error paths.
4. **Parity.** Add the class to `McpToolCatalogueParityTest`; it must pass unchanged otherwise. Check it
   bites by removing one `@ToolParam` and seeing it fail.
5. **Over the wire.** Extend `McpServerEndpointsIT`: see it fail before the class exists (stash the class),
   then pass. Run the full `./mvnw verify -Pit-tests` once, since this IT shares the `RANDOM_PORT` context.
6. **Spec and docs.** Update yvoke-web's `spec/07…` and this repository's `plan.md`; finish with
   `finish-task`.

## Risks

- **The web's own assistant sees the tools in its catalogue.** yvoke-web gives the in-app assistant and MCP
  clients one tool list (`mcpToolCallbacks`). The in-app assistant is deny-by-default (a playbook's `tools`
  decides), so it cannot call them unless an admin ticks them on a playbook, but they will appear as
  choices in the admin playbook editor. Hiding them there is P1-13, not this task.
- **Models in Chat and Cowork may call them unprompted.** They are read-only and return what MCP prompts
  already hand any connected client, so nothing new is exposed. The description limits it further.
- **Which list.** `listAllPlaybooks` would include orchestrator and reviewer playbooks, which a user must
  not pick as a single-agent playbook. Rejected for the list; kept for `get_playbook`, which matches
  `prompts/get`.
- **Returning objects instead of JSON strings.** Spring AI would serialise a record itself, but every
  existing tool returns a `String`, and the `ERROR:` convention needs a string body. Rejected.
- **A second endpoint for client-only tools** (design 5.4 alternative). Rejected there; one sign-in.
- **Merge with P1-01**, which runs in parallel and adds `get_system_prompt`: both touch the `TOOLS` list in
  the parity test and the expected names in `McpServerEndpointsIT`. One-line conflicts, resolved by
  keeping both names. Each task adds its own tool class, so no other shared code changes.

## Proof

- `PlaybookToolsTest` shows **Done when**: the list with `tools`, `codeExecution`, `targetAgent`,
  `prototype`; a known playbook; an unknown name answered with `ERROR:`.
- `McpServerEndpointsIT` shows both tools are listed and callable over `/mcp`.
- yvoke-web checks, each must pass: `./mvnw test` (unit and JS tier) and `./mvnw verify -Pit-tests`
  (needs Docker; if the cloud session has none, the pull request says so and CI runs it).
- This repository: `node scripts/check-docs.mjs` prints `check-docs: OK (N Markdown files)`. The plugin
  rows of the verification table do not apply yet (no scaffold, and nothing in the plugin changes).
- Delivered in [yvoke-web#4](https://github.com/yvoke-dev/yvoke-web/pull/4).

## What differed from the plan

- **A startup cycle.** `PlaybookService` needs the `McpSyncServer`, which needs the tool list built from
  `PlaybookTools`. The bean failed to build, `McpToolsConfig` only logged it, and both tools were missing
  from `tools/list`. Unit tests could not see it; the two catalogue ITs caught it in CI. First fixed with
  `@Lazy` on the tool; after review, fixed at the root: `PlaybookService` now looks the server up through an
  `ObjectProvider` only when a playbook changes, so no tool bean can close the cycle again.
- **`get_playbook` reads the table directly.** `PlaybookService.getPlaybook` is cached for 60 s, so a
  deleted or edited playbook lingered (found in review). The tool now calls `PlaybookRepository.findByName`;
  the integration test fetches a playbook after deleting its row and expects `ERROR: … not found.`. The
  unit tests also pin the exact JSON keys of both answers.
- **The end-to-end test deletes the row directly.** `deletePlaybook` also pushes `prompts/list_changed`
  down the test session's open SSE stream, which nothing reads, and closing the HTTP client then hung until
  the 15 s timeout. Deleting the row with `PlaybookRepository.delete` is also the realistic out-of-band case.
- **Integration tests in a cloud session.** The real test database image cannot be built there (its
  Debian and GitHub downloads are blocked). A local stand-in built from `paradedb/paradedb:0.24.0-pg16`,
  with its init scripts removed, runs the whole suite. CI uses the real image.
- **Merge with P1-01** conflicted in four files: the two lists the plan named (`McpToolCatalogueParityTest`,
  `McpServerEndpointsIT`), plus `JsonObjectsToolsIT` and `spec/07`. Each was resolved by keeping both sides.

Final run on the merged head (yvoke-web `15db208`, after the review fixes): 1806 unit tests and 550 integration tests pass,
coverage checks met; CI green on the same commit.
