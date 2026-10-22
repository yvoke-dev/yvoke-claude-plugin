import { test, expect, describe } from 'claude-code/testing'
import { parseCitations, type Citation, type Segment } from '../src/citations'

// P3-01: the citation markers in a reply. Ported from yvoke-desktop's `tests/components/Markdown.test.tsx`
// (the "Markdown citations", "bare id citations", "numbered reference markers" and "citation rewriting never
// corrupts real content" blocks), with the same inputs. The desktop rendered React and pressed buttons; here
// each test checks the pieces the parser returns. Numbers like `[1]` were superscripts there and stay text here.

const UUID = '274b9610-9148-4621-a5a1-089e807210c1'
const UUID2 = '9ab3009d-2057-49db-a03f-27aeb336ff49'

/** Every citation in the reply, in order. */
const cites = (md: string): Citation[] =>
  parseCitations(md).flatMap((s) => (s.type === 'citations' ? s.citations : []))

/** The text pieces only, joined: what is left when every marker is taken out. */
const prose = (md: string): string =>
  parseCitations(md)
    .map((s) => (s.type === 'text' ? s.text : ''))
    .join('')

/** Text and markers joined back: must give the input. */
const rejoin = (segments: Segment[]): string =>
  segments.map((s) => (s.type === 'text' ? s.text : s.raw)).join('')

const id = (value: string): Citation => ({ kind: 'id', id: value, label: `[${value.slice(0, 8)}]` })

describe('Markdown citations', () => {
  test('a chunk_id marker is a citation of kind chunk, with its full label', () => {
    expect(cites('See [chunk_id=abc123] for details.')).toEqual([
      { kind: 'chunk', id: 'abc123', label: '[chunk_id=abc123]' },
    ])
  })

  test('a file marker has the right kind', () => {
    expect(cites('[file=guide.md]')).toEqual([{ kind: 'file', id: 'guide.md', label: '[file=guide.md]' }])
  })

  test('a document_id marker has the right kind', () => {
    expect(cites('See [document_id=doc-7].')).toEqual([
      { kind: 'document', id: 'doc-7', label: '[document_id=doc-7]' },
    ])
  })

  test('the prefix is matched with its case, as in the desktop', () => {
    expect(cites('See [Chunk_ID=abc123].')).toEqual([])
  })

  test('plain markdown is one text piece, unchanged', () => {
    expect(parseCitations('**bold words**')).toEqual([{ type: 'text', text: '**bold words**' }])
  })

  test('an empty reply gives no pieces', () => {
    expect(parseCitations('')).toEqual([])
  })
})

// The format the server's prompts mandate: a bare uuid, no `chunk_id=` prefix, no numbering.
describe('bare id citations', () => {
  test('a bare uuid is a citation of kind id, labelled with its first 8 characters', () => {
    // `id`, not `chunk`: the marker does not say which table it names.
    expect(parseCitations(`A mapping groups the rules. [${UUID}]`)).toEqual([
      { type: 'text', text: 'A mapping groups the rules. ' },
      { type: 'citations', raw: `[${UUID}]`, citations: [id(UUID)] },
    ])
  })

  test('the hyphen-free 32-hex spelling is recognised', () => {
    const bare = UUID.replace(/-/g, '')
    expect(cites(`Claim [${bare}].`)).toEqual([{ kind: 'id', id: bare, label: '[274b9610]' }])
  })

  test('the same id twice is two citations: a repeat is the same source, not a duplicate to collapse', () => {
    expect(cites(`First claim [${UUID}]. Second claim [${UUID}].`)).toEqual([id(UUID), id(UUID)])
  })

  test('a bare id inside a code fence stays text', () => {
    const md = `\`\`\`\nget_section(chunk_id="${UUID}")\n\`\`\``
    expect(parseCitations(md)).toEqual([{ type: 'text', text: md }])
  })

  test('a truncated id is not a marker: 8 hex characters is also ordinary prose', () => {
    expect(parseCitations('Error code [decade00].')).toEqual([{ type: 'text', text: 'Error code [decade00].' }])
    expect(cites('See [274b9610].')).toEqual([])
  })

  test('a bare id and a [N] marker in one sentence are kept apart', () => {
    const md = `Claim [1] and claim [${UUID}].`
    expect(cites(md)).toEqual([id(UUID)])
    expect(prose(md)).toBe('Claim [1] and claim .')
  })

  test('adjacent brackets [uuid1][uuid2] are two citations', () => {
    expect(parseCitations(`Adjacent [${UUID}][${UUID2}].`)).toEqual([
      { type: 'text', text: 'Adjacent ' },
      { type: 'citations', raw: `[${UUID}]`, citations: [id(UUID)] },
      { type: 'citations', raw: `[${UUID2}]`, citations: [id(UUID2)] },
      { type: 'text', text: '.' },
    ])
  })

  test('comma-separated bare ids in one bracket are one piece with a citation each, with or without a space', () => {
    expect(parseCitations(`Multiple sources [${UUID}, ${UUID2}].`)[1]).toEqual({
      type: 'citations',
      raw: `[${UUID}, ${UUID2}]`,
      citations: [id(UUID), id(UUID2)],
    })
    expect(cites(`Multiple sources [${UUID},${UUID2}].`)).toEqual([id(UUID), id(UUID2)])
  })

  test('grouped chunk_ids in one bracket are a citation each', () => {
    expect(cites('Sources [chunk_id=abc123, chunk_id=def456].')).toEqual([
      { kind: 'chunk', id: 'abc123', label: '[chunk_id=abc123]' },
      { kind: 'chunk', id: 'def456', label: '[chunk_id=def456]' },
    ])
  })

  test('a group may mix a bare id and a legacy marker', () => {
    expect(cites(`Sources [${UUID}, file=guide.md].`)).toEqual([
      id(UUID),
      { kind: 'file', id: 'guide.md', label: '[file=guide.md]' },
    ])
  })

  test('grouped numbered markers [1, 2] stay text', () => {
    expect(parseCitations('Claim [1, 2].')).toEqual([{ type: 'text', text: 'Claim [1, 2].' }])
  })

  test('a bracket is not a marker unless every item in it is a citation', () => {
    expect(cites(`See [${UUID}, page 4].`)).toEqual([])
    expect(cites(`See [${UUID}, 2].`)).toEqual([])
    expect(cites('See [].')).toEqual([])
    expect(cites('See [ ].')).toEqual([])
  })

  test('the text around a marker is kept as it is', () => {
    expect(prose(`Claim [${UUID}].`)).toBe('Claim .')
  })
})

// [N] markers are typography, not navigation. The desktop drew them as superscripts and kept the [N] of a
// References list literal; neither surface here has a superscript, so every one of them stays text, and the
// citations beside them are still found.
describe('numbered reference markers', () => {
  const REFS = '\n\n## References\n[1] [chunk_id=abc123]\n'
  const chunk = { kind: 'chunk', id: 'abc123', label: '[chunk_id=abc123]' }

  test('an inline [N] is not a citation', () => {
    expect(prose(`Claim [1].${REFS}`)).toContain('Claim [1].')
  })

  test('the [N] keeps its brackets: it is shown as written', () => {
    expect(prose('Claim [1].')).toBe('Claim [1].')
  })

  test('the [N] of a plain References line stays text; the citation on it is found', () => {
    expect(cites(`Claim [1].${REFS}`)).toEqual([chunk])
    expect(prose(`Claim [1].${REFS}`)).toContain('\n[1] ')
  })

  test('the [N] of a bulleted References list stays text', () => {
    const md = 'Claim [1].\n\n## References\n- [1] [chunk_id=abc123]\n'
    expect(cites(md)).toEqual([chunk])
    expect(prose(md)).toContain('- [1] ')
  })

  test('every [N] of a multi-entry References block stays text', () => {
    const md = 'Claim [1].\n\n## References\n[1] [chunk_id=a]\n[2] [chunk_id=b]\n[3] [chunk_id=c]\n'
    expect(cites(md).map((c) => c.id)).toEqual(['a', 'b', 'c'])
    expect(prose(md)).toBe('Claim [1].\n\n## References\n[1] \n[2] \n[3] \n')
  })

  test('the [N] of a numbered References list stays text', () => {
    const md = 'Claim [1].\n\n## References\n1. [1] [chunk_id=abc123]\n'
    expect(cites(md)).toEqual([chunk])
    expect(prose(md)).toContain('1. [1] ')
  })

  test('a real markdown link is left alone', () => {
    const md = 'See [the docs](https://example.com).'
    expect(parseCitations(md)).toEqual([{ type: 'text', text: md }])
  })
})

// The cases the desktop's old string replace destroyed. Code and links are skipped by the scanner here, as
// they were whole nodes the desktop's tree walk did not descend into.
describe('citation rewriting never corrupts real content', () => {
  test('a real markdown link whose label is a number is left intact', () => {
    const md = 'See [2](https://example.com).'
    expect(parseCitations(md)).toEqual([{ type: 'text', text: md }])
  })

  test('an array index in a fenced code block is left alone', () => {
    const md = '```java\nString s = args[1];\n```'
    expect(parseCitations(md)).toEqual([{ type: 'text', text: md }])
  })

  test('an array index in inline code is left alone', () => {
    const md = 'Use `items[2]` here.'
    expect(parseCitations(md)).toEqual([{ type: 'text', text: md }])
  })

  test('a citation token inside a code fence stays text', () => {
    const md = '```\nlookup[chunk_id=abc123]\n```'
    expect(parseCitations(md)).toEqual([{ type: 'text', text: md }])
  })

  test('citations in ordinary prose beside a code block are still found', () => {
    const md = 'Before [chunk_id=abc123].\n\n```\nargs[1]\n[chunk_id=zzz]\n```\n\nAfter [chunk_id=def456].'
    expect(cites(md).map((c) => c.id)).toEqual(['abc123', 'def456'])
  })

  test('nothing inside an existing link is rewritten', () => {
    const md = `See [docs [1] here](https://example.com) and [${UUID}](https://example.com/${UUID}).`
    expect(parseCitations(md)).toEqual([{ type: 'text', text: md }])
  })

  test('a citation is plain text in a reply that has no other markers', () => {
    expect(prose('Claim [chunk_id=abc123].')).toBe('Claim .')
  })
})

// The scanner that replaces the desktop's parsed tree: the forms of code and link it has to know.
describe('code and links', () => {
  test('a tilde fence is code', () => {
    const md = `~~~\n[${UUID}]\n~~~`
    expect(cites(md)).toEqual([])
  })

  test('a fence is closed only by a fence of the same character at least as long', () => {
    const md = `\`\`\`\`\n\`\`\`\n[${UUID}]\n~~~~\n\`\`\`\`\nAfter [${UUID2}].`
    expect(cites(md)).toEqual([id(UUID2)])
  })

  test('a fence may be indented up to three spaces, and its closing fence too', () => {
    const md = `   \`\`\`\n[${UUID}]\n  \`\`\`\nAfter [${UUID2}].`
    expect(cites(md)).toEqual([id(UUID2)])
  })

  test('a fence inside a list item is code', () => {
    const md = `- step:\n  \`\`\`\n  args[${UUID}]\n  \`\`\`\n- next [${UUID2}]`
    expect(cites(md)).toEqual([id(UUID2)])
  })

  test('an unclosed fence runs to the end of the reply', () => {
    const md = `Before [${UUID}].\n\`\`\`\n[${UUID2}]\nstill code`
    expect(cites(md)).toEqual([id(UUID)])
  })

  test('three backticks in the middle of a line do not open a fence', () => {
    const md = `Write \`\`\` to open a block. See [${UUID}].`
    expect(cites(md)).toEqual([id(UUID)])
  })

  test('a double-backtick code span holding a backtick is code', () => {
    const md = `Use \`\` a\`[${UUID}] \`\` here, then [${UUID2}].`
    expect(cites(md)).toEqual([id(UUID2)])
  })

  test('a code span is closed only by a run of the same length', () => {
    const md = `\`\`one \` [${UUID}] \`\` then [${UUID2}].`
    expect(cites(md)).toEqual([id(UUID2)])
  })

  test('an unmatched backtick is literal text, so a citation after it is still found', () => {
    expect(cites(`It costs 5\` more [${UUID}].`)).toEqual([id(UUID)])
  })

  test('an escaped backtick does not open a code span', () => {
    expect(cites(`Type \\\`[${UUID}]\` here.`)).toEqual([id(UUID)])
  })

  test('a code span may run over a line break', () => {
    expect(cites(`Use \`a\n[${UUID}]\` here.`)).toEqual([])
  })

  test('a link whose url holds brackets or parentheses is left whole', () => {
    const md = `[x](https://ex.com/a_(b)?q=[${UUID}]) then [${UUID2}].`
    expect(cites(md)).toEqual([id(UUID2)])
  })

  test('an image is left alone like a link', () => {
    expect(cites(`![chart [${UUID}]](img.png)`)).toEqual([])
  })

  test('a citation right after a link is found', () => {
    expect(cites(`See [docs](https://ex.com)[${UUID}].`)).toEqual([id(UUID)])
  })

  test('a citation followed by text in parentheses is still a citation', () => {
    expect(cites(`See [${UUID}] (section 4).`)).toEqual([id(UUID)])
  })
})

describe('round trip', () => {
  const replies = [
    '',
    'plain',
    `A [${UUID}] b [${UUID}, ${UUID2}] c [chunk_id=x][file=y.md] d [1, 2].`,
    `\`\`\`\n[${UUID}]\n\`\`\`\nafter [${UUID}]`,
    `Use \`items[2]\` and [x](https://ex.com/[${UUID}]) and [${UUID2}]`,
    'Claim [1].\n\n## References\n[1] [chunk_id=a]\n[2] [chunk_id=b]\n',
    '```\nunclosed [chunk_id=a]',
  ]
  for (const md of replies) {
    test(`text and markers joined give back the input: ${JSON.stringify(md).slice(0, 40)}`, () => {
      expect(rejoin(parseCitations(md))).toBe(md)
    })
  }

  test('no two text pieces are next to each other', () => {
    const segments = parseCitations(`a \`x\` b [y](z) c [${UUID}] d`)
    for (let i = 1; i < segments.length; i++) {
      expect(segments[i - 1]!.type === 'text' && segments[i]!.type === 'text').toBe(false)
    }
  })
})
