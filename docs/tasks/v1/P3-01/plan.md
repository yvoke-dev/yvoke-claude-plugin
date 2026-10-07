# P3-01 Citation marker parser

**Release:** v1 · **Size:** M · **Type:** feature · **Status:** planned

**Done when** (from [plan.md](../plan.md)): the parser is ported from yvoke-desktop's `citationRehype.ts`
and its tests, and tests cover:

- bare `[<uuid>]` → clickable, labelled with the first 8 characters;
- legacy `[chunk_id=…]`, `[document_id=…]`, `[file=…]` → clickable, full label;
- `[1]`-style numbers → not clickable; a truncated id → not a marker;
- markers inside code blocks and inside real links `[2](https://…)` → left alone.

No blockers. Nothing here calls the server or draws anything: P3-02 turns the result into links or source
buttons, and P3-03 looks the id up.

## What the module does

`plugins/yvoke/src/citations.ts` takes the Markdown text of one reply and splits it into pieces:

```ts
parseCitations(markdown) // → Segment[]

type Segment =
  | { type: 'text'; text: string }              // the reply's own Markdown, unchanged
  | { type: 'citations'; raw: string; citations: Citation[] } // one bracket: `[a]` or `[a, b]`

type Citation = {
  kind: 'id' | 'chunk' | 'document' | 'file' // 'id' = a bare uuid: it does not say what it names
  id: string                                  // the full id, what P3-03 looks up
  label: string                               // what the user sees: '[274b9610]' or '[chunk_id=abc123]'
}
```

Joining every piece's `text` or `raw` gives back the input exactly. P3-02 can replace a bracket with one
link per citation, or, for a row of buttons under the reply, just collect the citations.

The rules are the desktop's, recorded in its spec (`spec/01_asking_questions.md`, "A source marker is a
bare id, shown short" and the four bullets after it; `spec/02_…`, "Citation markers are rewritten after
the answer is parsed"):

1. **Bare id.** `[274b9610-9148-4621-a5a1-089e807210c1]`, or the same 32 hex digits without hyphens, is a
   citation of kind `id`, labelled with its first 8 characters: `[274b9610]`. The same id twice gives two
   citations; nothing is merged.
2. **Legacy prefixed forms.** `[chunk_id=…]`, `[document_id=…]` and `[file=…]` (value `[a-zA-Z0-9_.-]+`)
   become kinds `chunk`, `document` and `file`, labelled with the whole marker. The prefix is matched
   without regard to case, as in the desktop.
3. **Groups.** `[a, b]` and `[a,b]` with any mix of the two forms above give one citation per item.
   Adjacent brackets `[a][b]` give two.
4. **Numbers stay text.** `[1]` and `[1, 2]` are left as text. The desktop drew them as plain superscripts;
   neither surface here has a superscript, and they are not clickable either way. This also makes the
   desktop's "References block" rule unnecessary, since that rule only decided when not to superscript.
5. **Not a marker:** a truncated id (`[decade00]`, 8 hex digits are also ordinary prose), any bracket whose
   items are not all citations, and an empty bracket.
6. **Code and links are left alone.** Nothing inside a fenced block (```` ``` ```` or `~~~`, of any
   length, closed by a fence at least as long; an unclosed fence runs to the end), inside an inline code
   span (a backtick run closed by a run of the same length), or inside a real link `[text](url)` (the
   label and the url) is rewritten.

### What is copied from yvoke-desktop

- **Copied as is:** the patterns (`UUID_HEX`, `CITE_ITEM`, `CITE_GROUP`, `NUMBERED_GROUP`) and the
  per-bracket logic of `splitText`: splitting a group on commas, the kind from the prefix, the 8-character
  label. This is most of `citationRehype.ts`.
- **Replaced:** the tree walk (`walk`, `OPAQUE`), which needs react-markdown's parsed tree, by the scanner
  below; and the References rule (`REF_DEF_BLOCK`), which numbers no longer need.
- **Tests:** the desktop has 29 citation tests in `tests/components/Markdown.test.tsx`. Every one is
  ported with the same input. They render React and click buttons there, so each assertion is rewritten
  against the returned pieces. The 7 about `[N]` superscripts and References lists keep their inputs and
  now check that the numbers stay text and that the citations next to them are still found. The scanner
  gets tests of its own on top (tildes, longer fences, unclosed fences, double backticks). The lookup and
  pane tests (`citationLookup`, `CitationModal`, `sectionView`, 31 more) belong to P3-03.

### Why a scanner and not a Markdown parser

The desktop works on the parsed tree (rehype), which is what made code and links safe there. The mod has no
npm packages and no dynamic `import()` (design 4.4), so a Markdown library would have to be copied into the
repo. A small scanner that knows only the three things that matter here (fences, code spans, inline links)
is enough to keep those cases safe, and the tests pin each of them.

## Files that change

| File | Change |
| --- | --- |
| `plugins/yvoke/src/citations.ts` (new) | `parseCitations`, the `Segment` and `Citation` types. No `$`, no hooks. |
| `plugins/yvoke/tests/citations.test.ts` (new) | The tests below, ported from the desktop's `tests/components/Markdown.test.tsx` ("bare id citations", "Markdown citations", "citation rewriting never corrupts real content"). |
| `docs/specs/citations.md` (new) and `docs/specs/README.md` | Which markers are recognised and what is left alone. P3-02 and P3-03 extend it. |
| `docs/tasks/v1/plan.md` | Tick P3-01; reword its third bullet to "numbers stay text". |

`hooks/register.tsx` does not change: the module registers no hook. P3-02 imports it.

**Shared files with other threads:** P1-07, P0-03, P1-09, P6-03 and P2-05 are running now. Only
`docs/specs/README.md` (one table row) and `docs/tasks/v1/plan.md` (one tick) are shared; both are
one-line merges. No source file is shared.

## Order of work

Each step starts with a test that is seen failing, then the code that makes it pass.

1. **Plain text.** A reply with no markers is one text piece, unchanged; an empty reply gives no pieces.
2. **Bare id.** Hyphenated and 32-hex spellings; the label is the first 8 characters; the id is whole;
   the text around it is kept; the same id twice gives two citations.
3. **Legacy forms.** `chunk_id`, `document_id`, `file`, each with its full label and kind; prefix case.
4. **Groups.** `[a, b]`, `[a,b]`, mixed kinds, adjacent `[a][b]`.
5. **Not markers.** `[1]`, `[1, 2]`, `[decade00]`, `[274b9610]`, `[]`, a bracket mixing an id and a
   word, a number and an id in one sentence (the id is a citation, the number stays text).
6. **Code.** An id in a fenced block (backticks and tildes, a longer fence holding a shorter one, an
   unclosed fence), in an inline code span (single and double backticks), and `args[1]` in code; an id in
   prose next to a code block is still found.
7. **Links.** `[2](https://ex.com)` intact; an id used as a link label or inside a url is left alone; an
   id right after a link is found.
8. **Round trip.** For every case above, joining `text` and `raw` gives back the input.

## Risks

- **The scanner misses a Markdown form the parser would have caught.** Indented code blocks (4 spaces),
  reference-style links `[text][ref]` and HTML `<code>` are not handled. Models write fenced code, and the
  server forbids a References section, so these are rare; `[a][b]` must stay two citations, which a
  reference-link rule would break. Considered and rejected: copying a Markdown library into the repo
  (thousands of lines for three cases).
- **The desktop kept numbers as superscripts.** If Eduard wants them drawn differently, that is P3-02's
  rendering choice; the parser leaves them as text either way.
- **What P3-02 needs is not known until spike P0-05.** The pieces fit both outcomes: links in the reply
  (rewrite each citation) or a button row (list the citations). If P0-05 shows something else is needed,
  P3-02 changes this module and its spec.

## Proof

- `plugins/yvoke/tests/citations.test.ts`, each test seen failing first.
- The five checks from `AGENTS.md` (`npm run check`), output pasted in the pull request: docs, both
  `validate --strict` runs (the `calls:` line names no `$.fs`, `$.process` or `$.http`), types, tests.
