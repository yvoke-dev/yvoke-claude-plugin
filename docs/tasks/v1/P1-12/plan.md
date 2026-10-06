# P1-12 Areas: `list_areas` and `list_playbooks(area)`

**Release:** v1 · **Size:** M · **Type:** feature · **Status:** planned

The code lives in [`yvoke-dev/yvoke-web`](https://github.com/yvoke-dev/yvoke-web) and follows that
repository's rules (its `CLAUDE.md`: strict TDD, unit tests in `src/test`, integration tests `*IT.java` in
`src/it`, additive-only migrations, its `spec/` chapter updated in the same change). Facts below were read
from yvoke-web `main` at `d3bde30` on 2026-10-06.

## What the task delivers

An **area** is a knowledge base (D-15): one row of yvoke-web's `orchestrator_profiles` table, such as
*OIM*. Its name is the profile's name. Each area offers *Single agent* plus that one multi-agent profile.
Today there is one (OIM); nothing below assumes a single area.

- **Playbooks get an `area`.** A new nullable column `playbooks.area` holds the name of the area a
  playbook belongs to. One area per playbook. A playbook with no area belongs to no area.
- **Areas get a default playbook.** A new nullable column `orchestrator_profiles.default_playbook` holds
  the playbook a new single-agent session starts with (OIM: `oim-full`).
- **`list_areas()`** returns a JSON array, one object per area, sorted by name:
  `name`, `prototype`, `multiAgentProfile` (the profile's name, today always the same as `name`),
  `defaultPlaybook` (or `null`), and `playbooks`, the names of the pickable playbooks in that area (as
  `list_playbooks(area)`, orchestrator and reviewer playbooks left out). Read live on every call.
- **`list_playbooks(area?)`**: with no area, unchanged (every pickable playbook). With an area, only that
  area's playbooks; the match ignores case and surrounding spaces, and an unknown area gives `[]`. Each
  item gains an `area` field. `get_playbook` gains the same field.
- **Admin screens.** The playbook editor gets an *Area* select (the profile names, plus *none*); the
  profile editor gets a *Default playbook* select. Playbook Markdown import and export carry `area:` in the
  frontmatter, and profile JSON import and export carry `defaultPlaybook`, so the yvoke-exports files can
  hold both.
- The desktop's REST answers (`GET /playbooks`, `GET /orchestrator/profiles`) gain the same fields. Adding
  a field does not break the desktop.

## Files that change

In yvoke-web:

| File | Change |
| --- | --- |
| `docker/db/migration/V10__areas.sql` (new) | `ALTER TABLE playbooks ADD COLUMN area VARCHAR(255)`, index on it; `ALTER TABLE orchestrator_profiles ADD COLUMN default_playbook VARCHAR(255)`. Both nullable, no backfill (additive only). |
| `rag/prompt/Playbook.java`, `PlaybookRepository.java`, `PlaybookService.java`, `PlaybookMarkdownParser.java` | Carry `area`; `PlaybookRepository.findSpecializedByArea(area)`, uncached. Parser reads and writes `area:`. |
| `chat/orchestration/OrchestratorProfile.java`, `OrchestratorProfileRepository.java` | Carry `defaultPlaybook`. |
| `chat/api/model/PlaybookDto.java`, `OrchestratorProfileDto.java` | Add the field. |
| `mcp/tools/AreaTools.java` (new) | `list_areas`, `@McpTool` and `@Tool` with the same name and description. Reads `OrchestratorProfileRepository.findAll` and the playbook table directly, so it is live. |
| `mcp/tools/PlaybookTools.java` | Optional `area` parameter on `list_playbooks`; `area` in both answers. |
| `rag/web/admin/RagAdminController.java`, `templates/admin/playbooks.html` | *Area* select. |
| `chat/web/admin/OrchestratorAdminController.java`, `templates/admin/orchestrators.html` | *Default playbook* select. |
| Tests | `AreaToolsTest` (new), `PlaybookToolsTest`, `PlaybookMarkdownParserTest`, `PlaybookServiceTest`, the two admin controller tests, `McpToolCatalogueParityTest` (add `AreaTools`), `PlaybookRepositoryIT`/`OrchestratorProfileRepositoryIT` (the new columns round trip), `McpServerEndpointsIT` and `JsonObjectsToolsIT` (the new tool name, a `tools/call` of `list_areas` over the wire). |
| `spec/07_using_the_assistant_from_other_tools.md`, `spec.md` if it lists playbook fields | Areas, the filter, the two admin fields. |

In this repository: this plan; the P1-12 entry ticked in `docs/tasks/v1/plan.md`; design 4.1 and 5.3
updated to say what was built. `docs/specs/` does not change: nothing in the plugin changes.

## Order of work

Each step starts with a test seen failing, then the code that makes it pass.

1. **Columns.** Repository ITs: a playbook saved with an area reads back with it; a profile saved with a
   default playbook reads back with it. Then the migration and the record, repository and DTO changes.
2. **Markdown and JSON.** Parser test: `area: OIM` in the frontmatter is read and written back; a file
   without it gives `null`. Profile import test: `defaultPlaybook` survives export then import.
3. **Filter.** `PlaybookToolsTest`: no area lists all pickable playbooks with `area` in each item; `OIM`
   (and ` oim `) lists only OIM's; an unknown area gives `[]`; orchestrator and reviewer playbooks stay out.
4. **`list_areas`.** `AreaToolsTest`: no profiles gives `[]`; one area (OIM, default `oim-full`, two
   playbooks); two areas, each with only its own playbooks and its own default; a default playbook that is
   not set is `null`; a failure gives the generic `ERROR:` message. Then `AreaTools`.
5. **Admin.** Controller tests: the posted *Area* and *Default playbook* reach the saved rows (the
   pitfall about form fields no controller binds). Then the two forms.
6. **Over the wire.** Parity test with `AreaTools`; `McpServerEndpointsIT` lists and calls `list_areas`
   with a seeded profile and playbook, in the existing context. Full `./mvnw verify -Pit-tests` once.
7. **Spec and docs**, then `finish-task`.

## Risks

- **No playbook has an area after deploy.** The migration cannot know which playbooks are OIM's, and
  playbook data lives outside the repository. Until an admin sets the area (admin screen, or `area:` in the
  yvoke-exports files then an import), `list_areas` shows OIM with no playbooks and the plugin's setup band
  (P1-08) has nothing to offer. Nothing is released yet, so this is a data step before the pilot, noted in
  the pull request. The yvoke-exports import scripts are outside both repositories; if they write
  playbooks with a fixed column list, they need `area` added there.
- **Where the link lives.** Rejected alternative: a list of single-agent playbooks on the profile instead of
  a column on the playbook. It would let one playbook sit in two areas and keep an area's setup on one
  admin page, but the release plan names an attribute on each playbook, and a playbook's area travels with
  its Markdown file in yvoke-exports, which a profile list would not.
- **No foreign key** from `playbooks.area` or `default_playbook` to the profile. The admin form saves a
  renamed profile as a new row, so `ON DELETE SET NULL` would silently drop every playbook's area, and
  playbooks may be imported before their profile. The existing playbook references on a profile have no
  foreign key either. A playbook naming an unknown area simply shows under no area.
- **Profiles defined only in `application.yml`.** The desktop endpoint falls back to them when the table
  is empty. `list_areas` reads the table only: the shipped configuration defines none, and they could not
  hold a default playbook.
- **The in-app assistant sees `list_areas`** in its catalogue, as with the P1-06 tools; hiding them is
  P1-13. The description says to call it only when a Yvoke playbook skill or the plugin says so.

## Proof

- `AreaToolsTest` and `PlaybookToolsTest` show **Done when**: one area, two areas, and a playbook listed
  under its area.
- `McpServerEndpointsIT` shows `list_areas` and the filtered `list_playbooks` over `/mcp`.
- yvoke-web checks, each must pass: `./mvnw test` and `./mvnw verify -Pit-tests`.
- This repository: `npm run check` (docs check plus the plugin rows, which must stay green though the
  plugin does not change).
