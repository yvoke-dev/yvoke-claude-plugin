# P1-12 Areas

**Release:** v1 · **Size:** L · **Type:** feature · **Status:** planned

The code lives in [`yvoke-dev/yvoke-web`](https://github.com/yvoke-dev/yvoke-web) and follows that
repository's rules (its `CLAUDE.md`: strict TDD, unit tests in `src/test`, integration tests `*IT.java` in
`src/it`, additive-only migrations, vertical domain packages checked by `ArchitectureTest`, its `spec/`
chapter updated in the same change). Facts below were read from yvoke-web `main` at `d3bde30` on
2026-10-06.

## What an area is

Eduard, 2026-10-06, replacing D-15's first answer (an area is a multi-agent profile): **an area is a
collection of system prompts, collections, playbooks and orchestrator profiles.** Today there is one area
(OIM); more will follow, so nothing may assume a single area.

Open decisions, asked one at a time, with the default this plan follows until answered:

1. Can an item belong to several areas? **Default: yes** (asked 2026-10-06). Each item holds a list of
   area names, like the `tags` arrays yvoke-web already uses, so a shared base prompt is not copied.
2. Does an area have one multi-agent profile or several? **Default: several are allowed**; the setup band
   (P1-08) offers *Single agent* plus each of the area's profiles.
3. Does an area restrict anything at run time (for example, a playbook only searching its area's
   collections)? **Default: no.** P1-12 groups and lists; what a playbook searches stays the playbook's
   choice.

## What the task delivers

- **An `areas` table** (domain package `area`): `name` (key), `title`, `description`, `prototype`, and
  three nullable defaults: `default_system_prompt`, `default_playbook`, `default_profile`. An admin page
  creates, edits and deletes areas and picks the defaults.
- **Members.** `system_prompts`, `collections`, `playbooks` and `orchestrator_profiles` each get an
  `areas TEXT[] NOT NULL DEFAULT '{}'` column. Each item's admin form gets an *Areas* multi-select.
  Playbook Markdown and profile JSON import and export carry the list, so the yvoke-exports files can hold
  it.
- **`list_areas()`** MCP tool: a JSON array, one object per area, sorted by name: `name`, `title`,
  `description`, `prototype`, `defaultSystemPrompt`, `defaultPlaybook`, `defaultProfile`, and the names
  of its members: `systemPrompts` (chat prompts only), `collections`, `playbooks` (pickable ones, as
  `list_playbooks`), `profiles`. Read live on every call.
- **Filters.** `list_playbooks(area?)` lists only that area's playbooks. `get_system_prompt` gains an
  optional `area`: with no name, it returns the area's default system prompt (falling back to the *Active
  Default Chat System Prompt*, D-16, when the area sets none). Matching ignores case and surrounding spaces;
  an unknown area is an `ERROR:` line for `get_system_prompt` and `[]` for lists. `list_playbooks` and
  `get_playbook` items gain an `areas` field. The profile list is P6-01's; it reads the same column.
- The desktop's REST answers (`GET /playbooks`, `GET /orchestrator/profiles`) gain the `areas` field.
  Adding a field does not break the desktop.

## Files that change

In yvoke-web (paths under `src/main/java/de/palsoftware/yvoke/` unless shown):

| File | Change |
| --- | --- |
| `docker/db/migration/V10__areas.sql` (new) | `CREATE TABLE areas …`; `ADD COLUMN areas TEXT[] NOT NULL DEFAULT '{}'` on the four tables, with GIN indexes. No backfill. |
| `area/core/…` (new) | `Area` record, `AreaRepository` (JdbcClient), `AreaService`. |
| `area/web/admin/AreaAdminController.java`, `templates/admin/areas.html` (new), admin nav | The area admin page. |
| `rag/prompt/Playbook*.java`, `SystemPrompt*.java` | Carry `areas`; repository finders by area, uncached. |
| `collection/core/model/Collection.java`, `CollectionRepository.java` | Carry `areas`. |
| `chat/orchestration/OrchestratorProfile*.java` | Carry `areas`. |
| `chat/api/model/PlaybookDto.java`, `OrchestratorProfileDto.java` | Add `areas`. |
| `mcp/tools/AreaTools.java` (new), `PlaybookTools.java`, `GetSystemPromptTool.java` | `list_areas`; the `area` parameters. `@McpTool` and `@Tool` kept identical. |
| The four existing admin controllers and templates | *Areas* multi-select. |
| Tests | Unit tests for each of the above; repository ITs for the new columns; `McpToolCatalogueParityTest`; `McpServerEndpointsIT` and `JsonObjectsToolsIT` for the new tool and parameters, in their existing contexts. |
| `spec/07_using_the_assistant_from_other_tools.md`, `spec/04_…` (curating content) | Areas, the filters, the admin page. |

In this repository: this plan; D-15 in `design.md` updated with the new answer and the decisions above;
design 4.1 and 5.3 updated to what was built; the P1-12 entry ticked in `plan.md`. `docs/specs/` does not
change: nothing in the plugin changes.

## Order of work

Each step starts with a test seen failing, then the code that makes it pass.

1. **Areas table.** `AreaRepositoryIT`: create, read, update, delete, list sorted. Then migration and
   `area/core`.
2. **Member columns.** Repository ITs for the four tables: `areas` round trips; finding by area ignores
   case. Then the columns in each record and repository.
3. **Import and export.** Parser test: `areas: [OIM]` in playbook frontmatter is read and written back
   (and a single `area: OIM` is read too). Profile JSON keeps `areas`.
4. **`list_areas`.** `AreaToolsTest`: no areas gives `[]`; one area with its members and defaults; two
   areas sharing one system prompt, each listing only its own playbooks; an unset default is `null`; a
   failure gives the generic `ERROR:` message.
5. **Filters.** `PlaybookToolsTest` (area filter, `areas` field, unknown area `[]`) and
   `GetSystemPromptToolTest` (area default, fallback to D-16's prompt, unknown area `ERROR:`).
6. **Admin.** Controller tests: posted areas and defaults reach the saved rows (the pitfall about form
   fields no controller binds). Then the forms and the area page.
7. **Over the wire.** Parity test; `McpServerEndpointsIT` lists and calls `list_areas` and the filters
   with seeded rows. Full `./mvnw verify -Pit-tests` once.
8. **Spec and docs**, then `finish-task`.

## Risks

- **Nothing belongs to an area after deploy.** The migration cannot know which items are OIM's, and
  playbook and prompt data live outside the repository. An admin creates the OIM area and ticks its
  members (admin screens, or `areas:` in the yvoke-exports files then an import) before the pilot. If the
  yvoke-exports import scripts write a fixed list of columns, they need `areas` added there; those scripts
  are outside both repositories.
- **Lists of names, not foreign keys.** A `TEXT[]` cannot carry a foreign key, so deleting or renaming an
  area leaves its name on members. The area service removes the name from all four tables in the same
  transaction when an area is deleted or renamed. A member naming an unknown area shows under no area.
  Rejected alternative: four join tables with foreign keys. Stricter, but four more tables and every
  import path rewritten, for a list an admin edits by hand.
- **Rejected: area as a column on the profile only** (D-15's first answer, the first draft of this plan).
  It could not hold system prompts or collections.
- **Package rules.** `area` is a new domain package. The member domains (`collection`, `rag.prompt`,
  `chat.orchestration`) store only area names and never import `area`, so no cycle; `AreaTools` in
  `mcp.tools` reads all of them, as other tools already read several domains.
- **The in-app assistant sees `list_areas`** in its catalogue, as with the P1-06 tools; hiding them is
  P1-13. Its description says to call it only when a Yvoke playbook skill or the plugin says so.

## Proof

- `AreaToolsTest` and `PlaybookToolsTest` show **Done when**: one area, two areas, and a playbook listed
  under its area.
- `McpServerEndpointsIT` shows `list_areas` and the filters over `/mcp`.
- yvoke-web checks, each must pass: `./mvnw test` and `./mvnw verify -Pit-tests`.
- This repository: `npm run check`.
