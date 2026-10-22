// P3-01: the citation markers in a reply. Ported from yvoke-desktop's `citationRehype.ts`.
//
// The desktop rewrote markers on react-markdown's parsed tree, so code and real links were whole nodes it
// never descended into. A mod cannot load a Markdown library, so a small scanner finds the three things that
// matter instead: fenced code blocks, inline code spans and inline links (images too). Everything else is
// searched for markers with the desktop's own patterns.
//
// Two kinds of marker:
//   - `[<uuid>]`: a bare id, what the server tells models to write. Labelled with its first 8 characters,
//     because the system prompt promises the reader a short link, not the raw 36 characters.
//   - `[chunk_id=…]`, `[document_id=…]`, `[file=…]`: older answers. Labelled with the whole marker.
// One bracket may hold several, separated by commas. `[1]`-style numbers are not citations: the desktop drew
// them as superscripts, which neither surface here has, so they stay text. Indented code blocks, reference
// links and HTML `<code>` are not recognised (docs/tasks/v1/P3-01/plan.md, Risks).

export type Citation = {
  /** `id` for a bare uuid: it does not say whether it names a passage or a document. */
  kind: 'id' | 'chunk' | 'document' | 'file'
  /** The full id, what the lookup takes. */
  id: string
  /** What the reader sees: `[274b9610]`, or `[chunk_id=abc123]` for the older forms. */
  label: string
}

/** A piece of a reply. Joining every `text` and `raw` gives back the reply exactly. */
export type Segment =
  | { type: 'text'; text: string }
  | { type: 'citations'; raw: string; citations: Citation[] }

// The desktop's patterns, unchanged. The prefix is matched with its case as written, as there.
const UUID_HEX =
  '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32}'
const CITE_ITEM = `(?:(?:chunk_id|document_id|file)=[a-zA-Z0-9_.-]+|${UUID_HEX})`
const CITE_GROUP = new RegExp(`\\[\\s*${CITE_ITEM}(?:\\s*,\\s*${CITE_ITEM})*\\s*\\]`, 'g')
const KIND_ITEM = /^(chunk_id|document_id|file)=([a-zA-Z0-9_.-]+)$/
const SCHEME = { chunk_id: 'chunk', document_id: 'document', file: 'file' } as const

/** The citations in one matched bracket; the bracket pattern guarantees every item is one. */
const citationsIn = (bracket: string): Citation[] =>
  bracket
    .slice(1, -1)
    .split(',')
    .map((item) => item.trim())
    .map((s): Citation => {
      const kinded = KIND_ITEM.exec(s)
      if (kinded) {
        return { kind: SCHEME[kinded[1] as keyof typeof SCHEME], id: kinded[2]!, label: `[${s}]` }
      }
      // The bracket pattern lets nothing else through, so this item is a bare id.
      return { kind: 'id', id: s, label: `[${s.slice(0, 8)}]` }
    })

/** An opening fence on its own line: up to any indentation, then 3+ backticks or tildes. */
const FENCE_OPEN = /^[ \t]*(`{3,}|~{3,})/

/**
 * The parts of the reply that may hold markers: everything outside fenced code blocks, as [start, end)
 * ranges. A fence is closed by the same character, at least as long, alone on its line; an unclosed fence
 * runs to the end.
 */
const outsideFences = (md: string): [number, number][] => {
  const ranges: [number, number][] = []
  let blockStart = 0
  let fence: string | undefined
  let pos = 0
  while (pos <= md.length) {
    const lineEnd = md.indexOf('\n', pos) === -1 ? md.length : md.indexOf('\n', pos)
    const line = md.slice(pos, lineEnd)
    if (fence === undefined) {
      const open = FENCE_OPEN.exec(line)
      if (open) {
        if (pos > blockStart) ranges.push([blockStart, pos])
        fence = open[1]!
      }
    } else {
      const close = line.trim()
      if (close.length >= fence.length && close === fence[0]!.repeat(close.length)) {
        fence = undefined
        blockStart = lineEnd + 1
      }
    }
    pos = lineEnd + 1
  }
  if (fence === undefined && blockStart < md.length) ranges.push([blockStart, md.length])
  return ranges
}

/** The end of the backtick run starting at `i`. */
const runEnd = (s: string, i: number): number => {
  let j = i
  while (s[j] === '`') j++
  return j
}

/** The index just past the `close` matching the `open` at `i`, counting nested pairs, or -1. */
const matching = (s: string, i: number, open: string, close: string, end: number): number => {
  let depth = 0
  for (let j = i; j < end; j++) {
    const c = s[j]
    if (c === open) depth++
    else if (c === close && --depth === 0) return j + 1
  }
  return -1
}

/**
 * Inside one range, the spans that are code or a link, as [start, end). A code span is a backtick run closed
 * by a run of the same length; an unmatched run is literal, and so is an escaped backtick. A link is
 * `[label](destination)` or the same after `!`.
 */
const opaqueSpans = (s: string, start: number, end: number): [number, number][] => {
  const spans: [number, number][] = []
  let i = start
  while (i < end) {
    const c = s[i]
    if (c === '\\') {
      i += 2
    } else if (c === '`') {
      const open = runEnd(s, i)
      const n = open - i
      let j = open
      let closed = -1
      while (j < end) {
        const k = s.indexOf('`', j)
        if (k === -1 || k >= end) break
        const e = runEnd(s, k)
        if (e - k === n) {
          closed = e
          break
        }
        j = e
      }
      if (closed === -1) {
        i = open
      } else {
        spans.push([i, closed])
        i = closed
      }
    } else if (c === '[' || (c === '!' && s[i + 1] === '[')) {
      const label = c === '!' ? i + 1 : i
      const labelEnd = matching(s, label, '[', ']', end)
      const linkEnd = labelEnd !== -1 && s[labelEnd] === '(' ? matching(s, labelEnd, '(', ')', end) : -1
      if (linkEnd === -1) {
        i = label + 1
      } else {
        spans.push([i, linkEnd])
        i = linkEnd
      }
    } else {
      i++
    }
  }
  return spans
}

/** Splits a reply into its text and its citation markers. */
export const parseCitations = (markdown: string): Segment[] => {
  const segments: Segment[] = []
  let pos = 0
  const text = (to: number) => {
    if (to > pos) segments.push({ type: 'text', text: markdown.slice(pos, to) })
  }
  const searchable: [number, number][] = []
  for (const [start, end] of outsideFences(markdown)) {
    let from = start
    for (const [a, b] of opaqueSpans(markdown, start, end)) {
      if (a > from) searchable.push([from, a])
      from = b
    }
    if (end > from) searchable.push([from, end])
  }
  for (const [start, end] of searchable) {
    const part = markdown.slice(start, end)
    for (const m of part.matchAll(CITE_GROUP)) {
      const at = start + m.index!
      text(at)
      segments.push({ type: 'citations', raw: m[0], citations: citationsIn(m[0]) })
      pos = at + m[0].length
    }
  }
  text(markdown.length)
  return segments
}
