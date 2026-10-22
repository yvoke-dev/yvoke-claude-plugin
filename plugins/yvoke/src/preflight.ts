// P2-06, D-27: the playbook preflight check. On the first question of a single-agent Yvoke session, before
// the setup locks, the model is asked whether the chosen playbook fits the question. When another playbook
// of the area clearly fits better, a dialog offers to switch; either answer sends the question, and the
// setup band (P1-08) then locks what was chosen. Every failure sends the question as it was: the check is
// an assist, not a gate. Ported from yvoke-desktop 1.3.0 (`playbookValidation.ts`, `PlaybookValidator.ts`,
// `AppCore.validatePlaybook`), with yvoke-web's rule that the area's default playbook skips the check and
// is never suggested.
//
// This part holds the rules, with no `$` in it, so they can be tested without the engine.
import type { CatalogueState, PlaybookEntry, Setup } from './setup'

/** How long the model may take. A slow model holds the question this long, with the checking line shown. */
export const VALIDATION_TIMEOUT_MS = 45_000
/** The reply's token cap, yvoke-web's. */
export const MAX_TOKENS = 500

export const CHECKING = 'Yvoke: checking the playbook…'
export const SEND_ANYWAY = 'Send anyway'
export const DIALOG_HEADER = 'Playbook'

/** The playbook checked in a session, so a question sent again is not checked twice (`yvoke.preflight`). */
export type Checked = { sessionId: string; playbook: string }

export type CheckInput = {
  setup: Setup | null
  catalogue: CatalogueState | null
  sessionId: string
  checked: Checked | null
  text: string
  /** The submission runs a slash command (`e.command`). */
  isCommand: boolean
  /** A prompt the person does not see as typed (`e.isMeta`). */
  isMeta: boolean
}

/** What to check: the chosen playbook and the playbooks the model may pick from, the chosen one included. */
export type Check = { selected: PlaybookEntry; candidates: PlaybookEntry[] }

/**
 * Whether this question gets a check, and against which playbooks; null sends it straight on. Only a typed
 * question in an unlocked single-agent setup whose playbook is not the area's default, in an area offering
 * at least one other playbook besides the default, and not checked before in this session.
 */
export const shouldCheck = (input: CheckInput): Check | null => {
  const { setup, catalogue } = input
  if (input.isCommand || input.isMeta || input.text.trim() === '') return null
  if (!setup || setup.lockedSessionId !== null || setup.mode !== 'single' || setup.playbook === null) return null
  if (!catalogue || !catalogue.ok) return null
  const area = catalogue.catalogue.areas.find((a) => a.name === setup.area)
  if (!area || setup.playbook === area.defaultPlaybook) return null
  if (input.checked?.sessionId === input.sessionId && input.checked.playbook === setup.playbook) return null
  const candidates = area.playbooks.filter((p) => p.name !== area.defaultPlaybook)
  const selected = candidates.find((p) => p.name === setup.playbook)
  return selected && candidates.length >= 2 ? { selected, candidates } : null
}

/**
 * The validator's instructions, yvoke-desktop's domain-neutral wording: the playbook list carries the
 * domain. The question itself is the completion's prompt.
 */
export const buildValidatorPrompt = (candidates: PlaybookEntry[], selectedName: string): string => {
  const list = candidates
    .map((p) => `${p.name} | ${p.title} | ${p.description}`)
    .join('\n')
    .trim()
  return `You are a playbook validator for a knowledge-base assistant.
Your task is to analyze the user's question, evaluate if their selected playbook is the most appropriate one, and if not, suggest the single best playbook from the list of available playbooks.

Available Playbooks (name | title | description):
${list}

Selected Playbook Name:
${selectedName}

Decide:
1. Is the selected playbook appropriate and correct for this user question?
2. If not, which playbook name from the list of available playbooks is the best match? (Return null if the selected one is appropriate).
3. Provide a concise explanation (1-2 sentences) of why the selected playbook is wrong and why the recommended one is better.

Rules:
- Be conservative: only answer false when the selected playbook is clearly a poor fit and a DIFFERENT listed playbook is clearly better. A playbook that merely overlaps with a better one is still appropriate.
- If the selected playbook is appropriate or the best match, you MUST set "plausible": true, "suggestedPlaybookName": null, and "reason": "".
- Never suggest the currently selected playbook in "suggestedPlaybookName". Only set "plausible": false if a DIFFERENT playbook from the available list is clearly a better fit.

You MUST respond ONLY with a raw JSON object matching the schema below. Do not wrap the JSON in markdown fences.
JSON Schema:
{
  "plausible": boolean (true if the selected playbook is correct, false if incorrect),
  "reason": string (explanation if incorrect, otherwise an empty string),
  "suggestedPlaybookName": string (the name of the suggested DIFFERENT playbook from the available list if incorrect, otherwise null)
}`
}

export type Verdict = { fits: true } | { fits: false; reason: string; suggested: PlaybookEntry }

const FITS: Verdict = { fits: true }

/** The JSON object in a reply: models add fences or a sentence around it despite being told not to. */
const jsonObject = (raw: string): Record<string, unknown> | undefined => {
  const text = raw.trim()
  const start = text.indexOf('{')
  const end = text.lastIndexOf('}')
  if (start === -1 || end <= start) return undefined
  try {
    const parsed: unknown = JSON.parse(text.slice(start, end + 1))
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : undefined
  } catch {
    return undefined
  }
}

/**
 * The model's reply as a verdict. Only `plausible: false` with a suggestion that names a candidate (by name
 * or title, any case) other than the selected playbook is a better match; everything else fits.
 */
export const parseValidation = (raw: string, candidates: PlaybookEntry[], selectedName: string): Verdict => {
  const obj = jsonObject(raw)
  if (!obj || obj.plausible !== false || typeof obj.suggestedPlaybookName !== 'string') return FITS
  const wanted = obj.suggestedPlaybookName.trim().toLowerCase()
  if (!wanted) return FITS
  const suggested =
    candidates.find((p) => p.name.toLowerCase() === wanted) ?? candidates.find((p) => p.title.toLowerCase() === wanted)
  if (!suggested || suggested.name === selectedName) return FITS
  return { fits: false, reason: typeof obj.reason === 'string' ? obj.reason.trim() : '', suggested }
}

/** The dialog for a better match: the question and its two answers. */
export const dialogFor = (verdict: Extract<Verdict, { fits: false }>) => {
  const { reason, suggested } = verdict
  const why = reason || 'Another playbook may fit this question better.'
  const which = suggested.title === suggested.name ? suggested.name : `${suggested.title} (${suggested.name})`
  const switchLabel = `Switch to ${suggested.name}`
  return { question: `${why} Switch to ${which} for this session?`, switchLabel, options: [switchLabel, SEND_ANYWAY] }
}
