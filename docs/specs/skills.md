# Playbook skills for Chat and Cowork

## Behaviour

- The plugin ships one skill per Yvoke playbook a user can pick, in `plugins/yvoke/skills/<name>/SKILL.md`.
  Each is a **live stub** (D-04): it holds the playbook's name, title and description, never its text. The
  playbook stays on the server behind sign-in, so the public repository holds no playbook text (D-02).
- Invoking a stub tells Claude, in this order, to:
  1. call the Yvoke connector's `get_system_prompt` and take its text as the base instructions;
  2. call `get_playbook` with the stub's name and follow the `text` it returns, the playbook winning any
     conflict;
  3. stop and say the server could not be reached if either call fails or answers `ERROR:`, rather than
     answer from general knowledge.
- Which base instructions step 1 names is worked out when the stub is generated, by the rule the mod uses
  (P1-07): the playbook's own system prompt when any area lists it as a chat prompt; else the playbook's
  area, when that area has a default system prompt; else no argument, which is the server's active
  default. Only the name is in the stub; the text is always fetched live.
- A stub exists for every playbook in every area, except orchestrator and reviewer playbooks and prototypes.
- Every stub carries `disable-model-invocation: true`, so in Claude Code the model never starts a playbook
  by itself (D-11) and the stubs do not crowd the skill list of a coding session. A Yvoke session picks its
  playbook in the setup band instead (P1-08).
- The description is the server's (D-22), prefixed with `Yvoke playbook (<area>): `, on one line, with `<`
  and `>` replaced by `(` and `)`, and cut on a word boundary to 1,024 characters ending in `…`.
- Stubs are generated, not written by hand. Nothing else may live under `plugins/yvoke/skills/` as a
  folder: the generator removes any folder it did not write.
- In Chat and Cowork nothing enforces the two calls; whether Claude makes them is checked by P0-08 and
  P8-01. Whether those surfaces accept `disable-model-invocation` is checked there too.

## Interfaces

| Piece | Where |
| --- | --- |
| Stubs | `plugins/yvoke/skills/<playbook name>/SKILL.md` |
| Generator | `node scripts/generate-skills.mjs` (`npm run skills -- …`), pure logic in `scripts/skills.mjs` |
| Server tools it reads | `list_playbooks` (name, title, description, `targetAgent`, `prototype`, `area`, `systemPrompt`) and `list_areas` (`name`, `defaultSystemPrompt`, `systemPrompts`) |
| Server tools a stub calls | `get_system_prompt`, `get_playbook` |

The generator's options:

| Option | Meaning |
| --- | --- |
| `--url <endpoint>` | The Yvoke MCP endpoint; default `http://localhost:8080/mcp` |
| `--dev-mode` | Sends `X-Yvoke-Dev-Mode: true`, for a local yvoke-web in mock mode (P0-11) |
| `YVOKE_TOKEN` (environment) | A bearer token for any other server; never printed |
| `--from <file>` | Reads a saved `{ "playbooks": [...], "areas": [...] }` instead of asking a server |
| `--out <dir>` | The skills folder; default `plugins/yvoke/skills` |

It prints one line per added, changed and removed stub, or `no changes`. A run with the same catalogue
changes nothing: stubs carry no timestamp, version or tool list. It stops without touching the folder when
the server is unreachable, answers `ERROR:`, lists no playbook to generate, or lists a name that is not a
valid skill name (lowercase letters, digits and single hyphens, at most 64 characters) or that two
playbooks share.

## Tests

- `scripts/skills.test.mjs`: reading the catalogue from a live fixture (`scripts/fixtures/catalogue.json`),
  wrong field types, which playbooks get a stub, the base-instructions rule, the exact stub text, the
  description cut and characters, invalid and duplicate names, an empty catalogue.
- `scripts/generate-skills.test.mjs`: adding, changing and removing stub folders; a second run with no
  changes; a failed run leaving the folder alone; the MCP exchange over JSON and SSE answers, the session
  header, dev mode, the bearer token kept out of errors, `ERROR:` answers and a server that is down.

Run them with `npm run test:scripts`.

## History

- P1-04 ([#24](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/24)): generator and the first 16
  stubs, from the server's catalogue of 2026-10-20.
