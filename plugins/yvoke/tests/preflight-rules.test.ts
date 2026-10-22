import { test, expect, describe } from 'claude-code/testing'
import {
  buildValidatorPrompt,
  parseValidation,
  shouldCheck,
  dialogFor,
  SEND_ANYWAY,
  type CheckInput,
} from '../src/preflight'
import type { Catalogue, PlaybookEntry, Setup } from '../src/setup'

// P2-06: the preflight check's rules, without the engine. Prompt and parse cases are ported from
// yvoke-desktop's tests/playbookValidation.test.ts (1.3.0); the SDK call and the exclusion list do not
// carry over (the mod calls $.model.complete, and the area's default playbook replaces the list).

const pb = (name: string, title = name, description = ''): PlaybookEntry => ({ name, version: 1, tools: null, title, description })

const PLAYBOOKS = [
  pb('oim-getting-started', 'Getting started', 'Onboarding and end-user questions.'),
  pb('oim-schema', 'Schema', 'Table and column definitions.'),
  pb('oim-customers', 'Customers', 'Customer deployments and versions.'),
]
const SELECTED = 'oim-getting-started'

describe('buildValidatorPrompt', () => {
  test('lists every candidate as name | title | description', () => {
    const prompt = buildValidatorPrompt(PLAYBOOKS, SELECTED)
    expect(prompt).toContain('oim-schema | Schema | Table and column definitions.')
    expect(prompt).toContain('oim-customers | Customers | Customer deployments and versions.')
  })

  test('names the selected playbook and asks for raw JSON', () => {
    const prompt = buildValidatorPrompt(PLAYBOOKS, SELECTED)
    expect(prompt).toContain(`Selected Playbook Name:\n${SELECTED}`)
    expect(prompt).toContain('"suggestedPlaybookName"')
    expect(prompt).toContain('respond ONLY with a raw JSON object')
  })
})

describe('parseValidation', () => {
  test('a plausible verdict fits', () => {
    const raw = '{"plausible": true, "reason": "", "suggestedPlaybookName": null}'
    expect(parseValidation(raw, PLAYBOOKS, SELECTED)).toEqual({ fits: true })
  })

  test('a better match gives the reason and the suggested playbook', () => {
    const raw = JSON.stringify({
      plausible: false,
      reason: 'The question is about table columns.',
      suggestedPlaybookName: 'oim-schema',
    })
    expect(parseValidation(raw, PLAYBOOKS, SELECTED)).toEqual({
      fits: false,
      reason: 'The question is about table columns.',
      suggested: PLAYBOOKS[1],
    })
  })

  test('reads a verdict wrapped in a markdown fence or trailing prose', () => {
    const fenced = '```json\n{"plausible": false, "reason": "Wrong area.", "suggestedPlaybookName": "oim-schema"}\n```'
    expect(parseValidation(fenced, PLAYBOOKS, SELECTED)).toMatchObject({ fits: false, suggested: { name: 'oim-schema' } })
    const chatty =
      'Here is my verdict:\n{"plausible": false, "reason": "Wrong area.", "suggestedPlaybookName": "oim-schema"}\nHope that helps.'
    expect(parseValidation(chatty, PLAYBOOKS, SELECTED)).toMatchObject({ fits: false, reason: 'Wrong area.' })
  })

  test('a suggestion naming a playbook that is not a candidate fits', () => {
    const raw = JSON.stringify({ plausible: false, reason: 'Try something else.', suggestedPlaybookName: 'oim-invented' })
    expect(parseValidation(raw, PLAYBOOKS, SELECTED)).toEqual({ fits: true })
  })

  test('a suggestion of the selected playbook, by name or by title, fits', () => {
    for (const name of [SELECTED, 'Getting started', 'GETTING STARTED']) {
      const raw = JSON.stringify({ plausible: false, reason: 'This is actually fine.', suggestedPlaybookName: name })
      expect(parseValidation(raw, PLAYBOOKS, SELECTED)).toEqual({ fits: true })
    }
  })

  test('a suggestion by title or in another case gives the canonical playbook', () => {
    for (const name of ['Schema', 'OIM-Schema', '  oim-schema  ']) {
      const raw = JSON.stringify({ plausible: false, reason: 'Table question.', suggestedPlaybookName: name })
      expect(parseValidation(raw, PLAYBOOKS, SELECTED)).toEqual({ fits: false, reason: 'Table question.', suggested: PLAYBOOKS[1] })
    }
  })

  test('a better match without a reason keeps an empty reason', () => {
    const raw = JSON.stringify({ plausible: false, reason: 7, suggestedPlaybookName: 'oim-schema' })
    expect(parseValidation(raw, PLAYBOOKS, SELECTED)).toEqual({ fits: false, reason: '', suggested: PLAYBOOKS[1] })
  })

  ;[
    ['prose instead of JSON', 'I think the playbook is fine, honestly.'],
    ['an empty reply', ''],
    ['broken JSON', '{"plausible": false, "reason":'],
    ['a non-boolean verdict', '{"plausible": "no", "reason": "Wrong.", "suggestedPlaybookName": "oim-schema"}'],
    ['a rejection with nothing to show', '{"plausible": false, "reason": "  ", "suggestedPlaybookName": null}'],
    ['a rejection naming no string', '{"plausible": false, "reason": "x", "suggestedPlaybookName": 3}'],
  ].forEach(([label, raw]) =>
    test(`fits on ${label}`, () => {
      expect(parseValidation(raw!, PLAYBOOKS, SELECTED)).toEqual({ fits: true })
    }),
  )
})

describe('shouldCheck', () => {
  const catalogue: Catalogue = {
    areas: [
      {
        name: 'OIM',
        title: 'OIM',
        playbooks: [pb('oim-full', 'Full'), ...PLAYBOOKS],
        defaultPlaybook: 'oim-full',
        profiles: ['OIM'],
        defaultProfile: 'OIM',
      },
      {
        name: 'PING',
        title: 'PingID',
        playbooks: [pb('ping-main'), pb('ping-other')],
        defaultPlaybook: 'ping-main',
        profiles: [],
        defaultProfile: null,
      },
    ],
  }
  const setup = (over: Partial<Setup> = {}): Setup => ({
    area: 'OIM',
    mode: 'single',
    playbook: SELECTED,
    version: 1,
    profile: null,
    lockedSessionId: null,
    ...over,
  })
  const input = (over: Partial<CheckInput> = {}): CheckInput => ({
    setup: setup(),
    catalogue: { ok: true, catalogue },
    sessionId: 's1',
    checked: null,
    text: 'Which columns does Person have?',
    isCommand: false,
    isMeta: false,
    ...over,
  })

  test("a non-default playbook in an area with a choice is checked against the area's other playbooks", () => {
    expect(shouldCheck(input())).toEqual({ selected: PLAYBOOKS[0], candidates: PLAYBOOKS })
  })

  test("the area's default playbook is never a candidate", () => {
    const check = shouldCheck(input())
    expect(check?.candidates.map((p) => p.name)).not.toContain('oim-full')
  })

  ;([
    ["the area's default playbook", { setup: setup({ playbook: 'oim-full' }) }],
    ['an area with no other playbook to suggest', { setup: setup({ area: 'PING', playbook: 'ping-other' }) }],
    ['multi-agent mode', { setup: setup({ mode: 'multi', playbook: null, profile: 'OIM' }) }],
    ['no playbook', { setup: setup({ playbook: null }) }],
    ['no setup', { setup: null }],
    ['a setup locked in this session', { setup: setup({ lockedSessionId: 's1' }) }],
    ['no catalogue', { catalogue: null }],
    ['a catalogue that failed to load', { catalogue: { ok: false, error: 'Yvoke Backend: down' } }],
    ['an area the catalogue does not list', { setup: setup({ area: 'GONE' }) }],
    ['a playbook the area does not list', { setup: setup({ playbook: 'oim-gone' }) }],
    ['a slash command', { isCommand: true }],
    ['a meta prompt', { isMeta: true }],
    ['a blank question', { text: '  \n ' }],
    ['the same playbook checked before in this session', { checked: { sessionId: 's1', playbook: SELECTED } }],
  ] as [string, Partial<CheckInput>][]).forEach(([label, over]) =>
    test(`no check for ${label}`, () => {
      expect(shouldCheck(input(over))).toBeNull()
    }),
  )

  test('a check made for another playbook or in another session does not count', () => {
    expect(shouldCheck(input({ checked: { sessionId: 's1', playbook: 'oim-schema' } }))).not.toBeNull()
    expect(shouldCheck(input({ checked: { sessionId: 's0', playbook: SELECTED } }))).not.toBeNull()
  })

  // /resume and /branch give a locked setup the new session's id (P1-08); one under any id was decided.
  test('a setup locked under another session id is not checked either', () => {
    expect(shouldCheck(input({ setup: setup({ lockedSessionId: 's0' }) }))).toBeNull()
  })
})

describe('dialogFor', () => {
  test('the reason, the suggestion by title and name, and the two answers', () => {
    expect(dialogFor({ fits: false, reason: 'The question is about table columns.', suggested: PLAYBOOKS[1]! })).toEqual({
      question: 'The question is about table columns. Switch to Schema (oim-schema) for this session?',
      switchLabel: 'Switch to oim-schema',
      options: ['Switch to oim-schema', SEND_ANYWAY],
    })
  })

  test('without a reason, and for a playbook whose title is its name', () => {
    expect(dialogFor({ fits: false, reason: '', suggested: pb('oim-schema') }).question).toBe(
      'Another playbook may fit this question better. Switch to oim-schema for this session?',
    )
  })
})
