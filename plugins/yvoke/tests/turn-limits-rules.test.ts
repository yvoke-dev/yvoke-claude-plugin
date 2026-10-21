import { test, expect, describe } from 'claude-code/testing'
import { limitsFrom, limitFor, isWrapUp, isLast, DEFAULT_LIMITS, flagLine, WRAP_UP } from '../src/turn-limits'

// P2-08: the pure rules behind the turn ceilings. The hooks are tested through the engine in
// turn-limits.test.ts.

describe('limitsFrom', () => {
  test('no settings: the defaults (single agent 25, lead 60, every subagent 20)', () => {
    expect(DEFAULT_LIMITS).toEqual({ single: 25, lead: 60, agent: 20 })
    expect(limitsFrom({})).toEqual({ single: 25, lead: 60, agent: 20 })
  })

  test('each setting is read on its own', () => {
    expect(limitsFrom({ turnLimitSingle: 5, turnLimitLead: 7, turnLimitAgent: 9 })).toEqual({
      single: 5,
      lead: 7,
      agent: 9,
    })
  })

  test('0 turns a ceiling off', () => {
    expect(limitsFrom({ turnLimitSingle: 0 })).toEqual({ single: null, lead: 60, agent: 20 })
  })

  test('1 and negative values count as 2, the least the mechanism needs', () => {
    expect(limitsFrom({ turnLimitSingle: 1, turnLimitLead: -3 })).toEqual({ single: 2, lead: 2, agent: 20 })
  })

  test('a fraction is rounded down', () => {
    expect(limitsFrom({ turnLimitAgent: 7.9 })).toEqual({ single: 25, lead: 60, agent: 7 })
  })

  test('a number written as text is read; anything else keeps the default', () => {
    expect(limitsFrom({ turnLimitSingle: '12', turnLimitLead: 'many', turnLimitAgent: true })).toEqual({
      single: 12,
      lead: 60,
      agent: 20,
    })
  })
})

describe('limitFor', () => {
  const limits = { single: 25, lead: 60, agent: 20 }

  test('the main loop with no setup or in single-agent mode: the single-agent ceiling', () => {
    expect(limitFor(limits, undefined, undefined)).toBe(25)
    expect(limitFor(limits, undefined, 'single')).toBe(25)
  })

  test('the main loop in any other mode: the lead ceiling', () => {
    expect(limitFor(limits, undefined, 'multi')).toBe(60)
  })

  test('a subagent, whatever the mode: the subagent ceiling', () => {
    expect(limitFor(limits, 'agent-1', undefined)).toBe(20)
    expect(limitFor(limits, 'agent-1', 'multi')).toBe(20)
  })

  test('a ceiling turned off stays off', () => {
    expect(limitFor({ single: null, lead: 60, agent: 20 }, undefined, 'single')).toBeNull()
  })
})

describe('where a request stands', () => {
  // With a ceiling of 5, requests have index 0 to 4: index 3 is warned, index 4 is the answer.
  const cases: [number, boolean, boolean][] = [
    [2, false, false],
    [3, true, false],
    [4, true, true],
    [5, true, true],
  ]
  for (const [index, wrapUp, last] of cases) {
    test(`index ${index} of 5: wrap-up ${wrapUp}, last ${last}`, () => {
      expect(isWrapUp(index, 5)).toBe(wrapUp)
      expect(isLast(index, 5)).toBe(last)
    })
  }

  test('the smallest ceiling, 2: the first request is warned, the second is the answer', () => {
    expect(isWrapUp(0, 2)).toBe(true)
    expect(isLast(0, 2)).toBe(false)
    expect(isLast(1, 2)).toBe(true)
  })
})

describe('texts', () => {
  test('the wrap-up tells the model to stop calling tools and answer now', () => {
    expect(WRAP_UP).toBe(
      'Yvoke: this question has reached its turn limit. Do not call any more tools. Answer now with what you ' +
        'have found, and say plainly that the answer may be incomplete.',
    )
  })

  test('the flag line names the ceiling', () => {
    expect(flagLine(25)).toBe('Yvoke: stopped at the turn limit (25 model requests). The answer may be incomplete.')
  })
})
