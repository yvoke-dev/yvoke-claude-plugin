import { test, expect, describe } from 'claude-code/testing'
import { KEEP_SESSIONS, staleKeys } from '../src/session'

// session.md, D-28: which `$.store` keys /yvoke prunes, without the engine. The hook is tested in
// session.test.ts.

describe('staleKeys', () => {
  test('/yvoke keeps the newest 5,000 sessions', () => {
    expect(KEEP_SESSIONS).toBe(5000)
  })

  test('no more sessions than it keeps: nothing goes', () => {
    expect(staleKeys([], 2)).toEqual([])
    expect(staleKeys(['session:a', 'setup:a', 'session:b', 'setup:b'], 2)).toEqual([])
  })

  test('one session over: the oldest goes, both its keys together', () => {
    const keys = ['session:a', 'setup:a', 'session:b', 'setup:b', 'session:c', 'setup:c']
    expect(staleKeys(keys, 2)).toEqual(['session:a', 'setup:a'])
  })

  test('a session with only one of its keys goes all the same', () => {
    expect(staleKeys(['session:a', 'setup:b', 'session:c'], 1)).toEqual(['session:a', 'setup:b'])
  })

  test('a session is as old as its first key, whichever prefix it has', () => {
    // b's setup comes before a's session entry, so b is the oldest; a's later setup does not make it newer.
    const keys = ['setup:b', 'session:a', 'session:b', 'setup:a', 'session:c']
    expect(staleKeys(keys, 2)).toEqual(['setup:b', 'session:b'])
    expect(staleKeys(keys, 1)).toEqual(['setup:b', 'session:a', 'session:b', 'setup:a'])
  })

  test('ended and keys of any other shape are never returned', () => {
    const keys = ['ended', 'session:a', 'setup:a', 'sessions', 'other:a', 'Session:b', 'session:b', 'setup:c']
    expect(staleKeys(keys, 1)).toEqual(['session:a', 'setup:a', 'session:b'])
    expect(staleKeys(['ended', 'other'], 0)).toEqual([])
  })

  test('keep 0: every session goes', () => {
    expect(staleKeys(['session:a', 'ended', 'setup:a', 'session:b'], 0)).toEqual(['session:a', 'setup:a', 'session:b'])
  })

  test('at the real limit: the one session past 5,000 goes', () => {
    const keys = Array.from({ length: KEEP_SESSIONS + 1 }, (_, i) => [`session:s${i}`, `setup:s${i}`]).flat()
    expect(staleKeys(['ended', ...keys], KEEP_SESSIONS)).toEqual(['session:s0', 'setup:s0'])
    expect(staleKeys(keys.slice(2), KEEP_SESSIONS)).toEqual([])
  })
})
