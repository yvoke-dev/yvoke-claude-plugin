// P1-09, D-28: the Yvoke server's `ask_clarifying_question` reports success without asking anyone, so in a
// Yvoke session the policy gate (src/policy.ts) answers it itself: it puts the question to the user in Claude
// Code's own question dialog (`$.ui.ask`) and hands the model the answer, as yvoke-desktop does
// (`isClarificationTool`, `buildCanUseTool`). This file holds the parts that need no `$`.

/** The server tool's bare name; the gate matches it under the Yvoke server's own prefix only. */
export const CLARIFY_TOOL = 'ask_clarifying_question'

/** yvoke-desktop's `CLARIFICATION_ANSWER_PREFIX`. */
export const ANSWER_PREFIX = 'User answered: '

/** What the model reads when the dialog was dismissed, interrupted or cannot open (yvoke-desktop's text). */
export const CANNOT_ASK = 'Clarifying questions cannot be asked in this execution context.'

export const NO_QUESTION =
  'ask_clarifying_question needs a question. Call it again with the question to ask the user.'

/** The chip beside the question in the dialog (12 characters at most). */
export const HEADER = 'Yvoke'

/** The dialog shows 2 to 4 options; more are dropped, and the user can still type one under "Other". */
const MAX_OPTIONS = 4

/**
 * The call's `question` and `options` as the dialog takes them, or null when there is no question to ask.
 * Options that are not text or are blank are dropped, the rest trimmed.
 */
export const clarifyingQuestion = (e: { question?: unknown; options?: unknown }): { question: string; options: string[] } | null => {
  const question = typeof e.question === 'string' ? e.question.trim() : ''
  if (question === '') return null
  const options = (Array.isArray(e.options) ? e.options : [])
    .filter((o): o is string => typeof o === 'string')
    .map((o) => o.trim())
    .filter((o) => o !== '')
    .slice(0, MAX_OPTIONS)
  return { question, options }
}
