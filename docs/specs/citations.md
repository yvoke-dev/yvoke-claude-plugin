# Citations

## Behaviour

Today the plugin only recognises the citation markers in a reply. Nothing draws them yet: P3-02 turns them
into links or source buttons, and P3-03 opens the cited passage.

- `parseCitations(markdown)` splits a reply into pieces: `{ type: 'text', text }` and, for each marker,
  `{ type: 'citations', raw, citations }`. Joining every `text` and `raw` gives back the reply exactly.
- **A bare id is a citation.** `[274b9610-9148-4621-a5a1-089e807210c1]`, or the same 32 hex digits without
  hyphens, gives a citation of kind `id`, labelled with its first 8 characters: `[274b9610]`. The kind is
  `id` because the marker does not say whether it names a passage or a document.
- **The older prefixed forms are citations.** `[chunk_id=…]`, `[document_id=…]` and `[file=…]` (value of
  letters, digits, `_`, `.` and `-`) give kinds `chunk`, `document` and `file`, labelled with the whole
  marker. The prefix is matched in lower case only, as in yvoke-desktop.
- **One bracket may hold several**, separated by commas with or without spaces, and may mix the forms.
  Adjacent brackets `[a][b]` are two markers. The same id twice is two citations.
- **Not a marker:** `[1]`-style numbers (they stay text, also in a References list), a truncated id such as
  `[274b9610]`, an empty bracket, and any bracket holding something that is not a citation.
- **Code and links are left alone.** Nothing is a marker inside a fenced code block (three or more
  backticks or tildes at the start of a line, closed by the same character at least as long, or running to
  the end of the reply when unclosed), inside an inline code span (a backtick run closed by a run of the
  same length; an escaped backtick opens nothing), or inside a link or image `[label](destination)`, label
  and destination alike.
- **Not recognised as code or links:** indented code blocks, reference-style links and HTML `<code>`. Markers
  inside them are found.

## Interfaces

| Piece | File |
| --- | --- |
| `parseCitations`, `Segment`, `Citation` | `plugins/yvoke/src/citations.ts` |

The module registers no hook and calls no `$`.

## Tests

- `plugins/yvoke/tests/citations.test.ts`: the 29 citation tests of yvoke-desktop's
  `tests/components/Markdown.test.tsx`, ported with the same inputs (numbers now stay text); the scanner's
  own cases (tilde and longer fences, indented and unclosed fences, code spans of different lengths, escaped
  backticks, links holding brackets or parentheses, images); and a round trip for each kind of reply.

## History

- P3-01 ([#16](https://github.com/yvoke-dev/yvoke-claude-plugin/pull/16)): the parser, ported from
  yvoke-desktop's `citationRehype.ts`.
