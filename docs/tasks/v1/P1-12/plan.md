# P1-12 Areas

**Release:** v1 · **Size:** L · **Type:** feature · **Status:** done

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

1. Can an item belong to several areas? **Decided 2026-10-06 (Eduard): no, one area per item.** A
   prompt or collection two areas need is duplicated.
2. Does an area have one multi-agent profile or several? **Decided 2026-10-06 (Eduard): several**, with
   a default one; the setup band (P1-08) offers *Single agent* plus each of the area's profiles.
3. Does an area restrict anything at run time? **Decided 2026-10-06 (Eduard): playbooks should search
   only the collections in their own area, but that can come later.** P1-12 groups and lists; the limit is
   its own task, [P1-14](../plan.md).

## What the task delivers

- **An `areas` table** (domain package `area`): `name` (key), `title`, `description`, `prototype`, and
  three nullable defaults: `default_system_prompt`, `default_playbook`, `default_profile`. An admin page
  creates, edits and deletes areas and picks the defaults.
- **Members.** `system_prompts`, `collections`, `playbooks` and `orchestrator_profiles` each get a
  required `area` column (`NOT NULL`), a foreign key to `areas(name)` (`ON UPDATE CASCADE ON DELETE
  RESTRICT`): nothing exists outside an area (Eduard, 2026-10-06). Renaming an area carries over to its
  members; an area that still has members cannot be deleted. Each item's admin form gets a required *Area*
  select. Playbook Markdown (`area:` in the frontmatter) and profile JSON import and export carry it, so
  the yvoke-exports files can hold it; a file without one is imported into the area picked on the import
  form, and a file naming an unknown area is refused with that name.
- **Existing rows.** The migration creates one area, `OIM`, and puts every existing system prompt,
  collection, playbook and profile in it, so the columns can be `NOT NULL` from the start. An admin moves
  anything that is not OIM's (or renames the area) afterwards.
- **Collections created by the ingest API.** The upload and KG-process APIs create a missing collection
  on the fly. They gain an `area` parameter, required only when the collection does not exist yet; without
  it the request is refused with a message saying so. Existing collections are unaffected.
- **`list_areas()`** MCP tool: a JSON array, one object per area, sorted by name: `name`, `title`,
  `description`, `prototype`, `defaultSystemPrompt`, `defaultPlaybook`, `defaultProfile`, and the names
  of its members: `systemPrompts` (chat prompts only), `collections`, `playbooks` (pickable ones, as
  `list_playbooks`), `profiles`. Read live on every call.
- **Filters.** `list_playbooks(area?)` lists only that area's playbooks. `get_system_prompt` gains an
  optional `area`: with no name, it returns the area's default system prompt (falling back to the *Active
  Default Chat System Prompt*, D-16, when the area sets none). Matching ignores case and surrounding spaces;
  an unknown area is an `ERROR:` line for `get_system_prompt` and `[]` for lists. `list_playbooks` and
  `get_playbook` items gain an `area` field. The profile list is P6-01's; it reads the same column.
- The desktop's REST answers (`GET /playbooks`, `GET /orchestrator/profiles`) gain the `area` field.
  Adding a field does not break the desktop.

## Files that change

In yvoke-web (paths under `src/main/java/de/palsoftware/yvoke/` unless shown):

| File | Change |
| --- | --- |
| `docker/db/migration/V10__areas.sql` (new) | `CREATE TABLE areas …`; `INSERT` the `OIM` area; on each of the four tables add `area VARCHAR(255) NOT NULL DEFAULT 'OIM'` with the foreign key (`ON UPDATE CASCADE ON DELETE RESTRICT`), each indexed. Only additions, so the previous release still runs against it. |
| `area/core/…` (new) | `Area` record, `AreaRepository` (JdbcClient), `AreaService`. |
| `area/web/admin/AreaAdminController.java`, `templates/admin/areas.html` (new), admin nav | The area admin page. |
| `rag/prompt/Playbook*.java`, `SystemPrompt*.java` | Carry `area`; repository finders by area, uncached. |
| `collection/core/model/Collection.java`, `CollectionRepository.java` | Carry `area`. |
| `chat/orchestration/OrchestratorProfile*.java` | Carry `area`. |
| `chat/api/model/PlaybookDto.java`, `OrchestratorProfileDto.java` | Add `area`. |
| `mcp/tools/AreaTools.java` (new), `PlaybookTools.java`, `GetSystemPromptTool.java` | `list_areas`; the `area` parameters. `@McpTool` and `@Tool` kept identical. |
| `ingest/core/service/IngestService.java` and the ingest API controllers | The `area` parameter for a collection created on the fly. |
| The four existing admin controllers and templates | *Area* select. |
| Tests | Unit tests for each of the above; repository ITs for the new columns; `McpToolCatalogueParityTest`; `McpServerEndpointsIT` and `JsonObjectsToolsIT` for the new tool and parameters, in their existing contexts. |
| `spec/07_using_the_assistant_from_other_tools.md`, `spec/04_…` (curating content) | Areas, the filters, the admin page. |

In this repository: this plan; D-15 in `design.md` updated with the new answer and the decisions above;
design 4.1 and 5.3 updated to what was built; the P1-12 entry ticked in `plan.md`. `docs/specs/` does not
change: nothing in the plugin changes.

## Order of work

Each step starts with a test seen failing, then the code that makes it pass.

1. **Areas table.** `AreaRepositoryIT`: create, read, update, delete, list sorted. Then migration and
   `area/core`.
2. **Member columns.** Repository ITs for the four tables: `area` round trips; finding by area ignores
   case; deleting an area with members is refused; renaming carries over. Then the columns in each record and repository.
3. **Import and export.** Parser test: `area: OIM` in playbook frontmatter is read and written back.
   Profile JSON keeps `area`. An unknown area is refused with its name.
4. **`list_areas`.** `AreaToolsTest`: no areas gives `[]`; one area with its members and defaults; two
   areas, each listing only its own members; an unset default is `null`; a
   failure gives the generic `ERROR:` message.
5. **Filters.** `PlaybookToolsTest` (area filter, `area` field, unknown area `[]`) and
   `GetSystemPromptToolTest` (area default, fallback to D-16's prompt, unknown area `ERROR:`).
6. **Admin.** Controller tests: posted areas and defaults reach the saved rows (the pitfall about form
   fields no controller binds). Then the forms and the area page.
7. **Over the wire.** Parity test; `McpServerEndpointsIT` lists and calls `list_areas` and the filters
   with seeded rows. Full `./mvnw verify -Pit-tests` once.
8. **Spec and docs**, then `finish-task`.

## Risks

- **Everything lands in OIM.** The migration cannot tell which items belong to which area, so it puts all
  of them in `OIM`. Anything else (for example a PingID profile, if one exists) is moved by an admin after
  deploy. OIM's defaults (`oim-full` and so on) are set on the area page.
- **The previous release during a rolling deploy** (yvoke-web pitfall: old code runs against the new
  schema). Old code inserts rows without `area`, which a `NOT NULL` column refuses. The column therefore
  gets `DEFAULT 'OIM'` as well, which keeps old inserts working; new code always passes the area. A later
  release can drop the default once no old code is left.
- **The yvoke-exports import scripts** are outside both repositories. If they write a fixed list of
  columns, rows they insert get the `OIM` default until `area` is added there.
- **Foreign keys.** One area per item allows a real foreign key, so renaming an area carries over to its
  members, with no cleanup code. Rejected: a list of area names per item (no foreign key possible; Eduard
  chose one area). Rejected: a nullable column (first draft); Eduard: nothing exists outside an area.
- **Rejected: area as a column on the profile only** (D-15's first answer, the first draft of this plan).
  It could not hold system prompts or collections.
- **Package rules.** `area` is a new domain package that depends on no other domain. The member domains
  (`collection`, `rag.prompt`, `chat.orchestration`) call `area.core` to check an area on save, and
  `AreaTools` in `mcp.tools` reads all of them, so there is no cycle (`ArchitectureTest` passes).
- **The in-app assistant sees `list_areas`** in its catalogue, as with the P1-06 tools; hiding them is
  P1-13. Its description says to call it only when a Yvoke playbook skill or the plugin says so.

## Proof

- `AreaToolsTest` and `PlaybookToolsTest` show **Done when**: one area, two areas, and a playbook listed
  under its area.
- `McpServerEndpointsIT` shows `list_areas` and the filters over `/mcp`.
- yvoke-web checks, each must pass: `./mvnw test` and `./mvnw verify -Pit-tests`.
- This repository: `npm run check`.
- Delivered in [yvoke-web#7](https://github.com/yvoke-dev/yvoke-web/pull/7). Local runs on yvoke-web
  `3a8793c`: `./mvnw verify -Pit-tests` ran 1860 unit tests, 347 JS tests and 567 integration tests (1
  skipped), all passing, with the coverage checks met; `./mvnw verify -Pe2e-tests` ran 59 browser tests,
  all passing. `npm run check` here: `check-docs: OK`, both validations passed, `lay-types: OK`, 1 pass,
  0 fail.

## What differed from the plan

- **One membership rule.** What counts as an area's chat prompt, pickable playbook and profile is one set
  of queries in `AreaRepository.findAllMembers`, mirroring `PlaybookService.listSpecializedPlaybooks`.
  `list_areas`, `get_system_prompt(area)` and the admin page all read it, so they cannot disagree.
  `AreaMembersIT` pins it.
- **Defaults must be members.** A default is reported only when it is a pickable member of the same area,
  so a default that was moved to another area, or is not a chat prompt, reads as `null` (and
  `get_system_prompt` falls back to D-16's prompt). The admin page refuses to save such a default, so the
  case arises only when a member is moved afterwards.
- **A name wins over an area** in `get_system_prompt`, so an explicit prompt is never silently replaced.
- **Member domains call `area.core`** to refuse an unknown area on save, rather than only storing names
  (Risks updated). The foreign key would refuse it too, but with a database error instead of the area's
  name.
- **Collections can be moved** from a per-collection *Area* select on the collections page, besides being
  created in an area.
- **File import on the admin pages** happens in the browser, so the page reads `area:` (playbook
  frontmatter) or the profile JSON's `area` and sets the *Area* select; a file without one keeps the
  select's current choice. An unknown name is reported in an alert and leaves the select alone; the
  server-side import (`POST` of a file) refuses it with the area's name, as planned.
- **Spec wording.** yvoke-web's spec already used *knowledge area* for one collection. Its glossary now has
  an *Area* row and says a knowledge area belongs to one area. Renaming *knowledge area* is left to Eduard.
- **Browser tests in a cloud session.** The installed Chromium is older than the one Playwright for Java
  wants; a browsers folder linking the expected name to the installed one runs the e2e tests. CI is
  unaffected.
- **Older tests.** Seven yvoke-web integration tests posted or saved items without an area and now pass
  one. `IngestApiControllerIT` relied on another test seeding its KG prompt and now seeds its own.
