import { test, expect, describe } from 'claude-code/testing'
import { declaredTools, isGrantedServerTool, isOwnServerCall, isPlainAsk, loadedTools, mayGrant } from '../src/policy'
import type { CatalogueState, Setup } from '../src/setup'

// tool-policy.md: the pure rule behind the gate's pass for the mod's own calls to its Yvoke server. The gate
// and the `tool.check` hook are tested through the engine in policy.test.ts.

const PREFIX = 'mcp__plugin_yvoke_yvoke__'
const TOOL = `${PREFIX}list_areas`

describe('isOwnServerCall', () => {
  test("a call this plugin made to the Yvoke server's tool", () => {
    expect(isOwnServerCall({ plugin: 'yvoke' }, TOOL, PREFIX)).toBe(true)
  })

  test("the model's call (origin engine) is not the mod's own", () => {
    expect(isOwnServerCall({ plugin: 'engine' }, TOOL, PREFIX)).toBe(false)
  })

  test("another plugin's call to the Yvoke server is not the mod's own", () => {
    expect(isOwnServerCall({ plugin: 'other' }, TOOL, PREFIX)).toBe(false)
    expect(isOwnServerCall({ plugin: 'yvoke2' }, TOOL, PREFIX)).toBe(false)
  })

  test('an origin the engine did not give is not the mod', () => {
    expect(isOwnServerCall(undefined, TOOL, PREFIX)).toBe(false)
  })

  test("the mod's call to any other tool stays under the policy", () => {
    expect(isOwnServerCall({ plugin: 'yvoke' }, 'Bash', PREFIX)).toBe(false)
    expect(isOwnServerCall({ plugin: 'yvoke' }, 'mcp__github__create_issue', PREFIX)).toBe(false)
    expect(isOwnServerCall({ plugin: 'yvoke' }, 'mcp__plugin_yvoke_yvoke2__list_areas', PREFIX)).toBe(false)
  })

  test('the server not connected: nothing is its tool', () => {
    expect(isOwnServerCall({ plugin: 'yvoke' }, TOOL, null)).toBe(false)
  })
})

// tool-policy.md: the pure rules behind the `tool.check` hook, which grants what the gate has let through
// (no permission prompt).
describe('mayGrant', () => {
  test('no ceiling: the hook may grant', () => {
    expect(mayGrant(undefined)).toBe(true)
  })

  test('a ceiling of ask or deny, set by the organisation, is not lifted by the hook', () => {
    expect(mayGrant('ask')).toBe(false)
    expect(mayGrant('deny')).toBe(false)
  })

  test('a ceiling of allow caps nothing', () => {
    expect(mayGrant('allow')).toBe(true)
  })
})

describe('isGrantedServerTool', () => {
  const GRANTED = [`${PREFIX}search_corpus`, `${PREFIX}ask_clarifying_question`]

  test('a server tool the playbook grants', () => {
    expect(isGrantedServerTool(`${PREFIX}search_corpus`, PREFIX, GRANTED)).toBe(true)
  })

  test('a server tool the playbook does not grant', () => {
    expect(isGrantedServerTool(`${PREFIX}get_toc`, PREFIX, GRANTED)).toBe(false)
  })

  test('nothing granted: nothing passes', () => {
    expect(isGrantedServerTool(`${PREFIX}search_corpus`, PREFIX, [])).toBe(false)
  })

  test('ask_clarifying_question is answered by the gate, so the hook never grants it', () => {
    expect(isGrantedServerTool(`${PREFIX}ask_clarifying_question`, PREFIX, GRANTED)).toBe(false)
  })

  test('only by the exact name: a tool outside the granted list is not matched by prefix', () => {
    expect(isGrantedServerTool(`${PREFIX}search_corpus_all`, PREFIX, GRANTED)).toBe(false)
    expect(isGrantedServerTool('Bash', PREFIX, ['Bash'])).toBe(false)
  })
})

describe('isPlainAsk', () => {
  test("the engine's own ask, with no rule, setting, hook or plugin behind it", () => {
    expect(isPlainAsk({ decision: 'ask' })).toBe(true)
    expect(isPlainAsk({ decision: 'ask', reason: 'Claude requested permissions to use a tool.' })).toBe(true)
    expect(isPlainAsk({ decision: 'ask', ceiling: 'ask' })).toBe(true)
  })

  test('an allow or a deny is not an ask, and stays as it is', () => {
    expect(isPlainAsk({ decision: 'allow' })).toBe(false)
    expect(isPlainAsk({ decision: 'deny' })).toBe(false)
    expect(isPlainAsk({ decision: 'deny', rule: 'mcp__plugin_yvoke_yvoke__search_corpus' })).toBe(false)
  })

  test("an ask that somebody chose is theirs: a settings rule, an option, a classic hook or another plugin's hook", () => {
    expect(isPlainAsk({ decision: 'ask', rule: 'mcp__plugin_yvoke_yvoke__search_corpus' })).toBe(false)
    expect(isPlainAsk({ decision: 'ask', setting: 'permissions.blockReadsOutsideWorkingDirectories' })).toBe(false)
    expect(isPlainAsk({ decision: 'ask', hook: 'PreToolUse' })).toBe(false)
    expect(isPlainAsk({ decision: 'ask', plugin: 'other' })).toBe(false)
  })
})

// tool-policy.md: whose `tools` the gate reads. The gate itself is tested through the engine in policy.test.ts.

const SETUP: Setup = { area: 'OIM', mode: 'single', playbook: 'oim-full', version: 3, profile: null, lockedSessionId: 'session-a' }
const loaded = (tools: unknown, over: Record<string, unknown> = {}) => ({
  sessionId: 'session-a',
  text: 'TEXT',
  loadedFor: { area: 'OIM', mode: 'single', playbook: 'oim-full', version: 3 },
  tools,
  ...over,
})
const CATALOGUE: CatalogueState = {
  ok: true,
  catalogue: {
    areas: [
      {
        name: 'OIM',
        title: 'OIM',
        playbooks: [{ name: 'oim-full', version: 3, tools: ['search_corpus'] }],
        defaultPlaybook: 'oim-full',
        profiles: [],
        defaultProfile: null,
      },
    ],
  },
}

describe('loadedTools', () => {
  test('what loaded for this session and the setup is playbook: its tools, whatever the version', () => {
    expect(loadedTools(loaded(['get_toc']), 'session-a', SETUP)).toEqual(['get_toc'])
    expect(loadedTools(loaded(null), 'session-a', SETUP)).toBeNull()
    expect(loadedTools(loaded('unreadable'), 'session-a', SETUP)).toBe('unreadable')
    expect(loadedTools(loaded(['get_toc'], { loadedFor: { area: 'OIM', mode: 'single', playbook: 'oim-full', version: 4 } }), 'session-a', SETUP)).toEqual(['get_toc'])
  })

  test("another session's text, another area or playbook, or a setup naming none: nothing loaded for it", () => {
    expect(loadedTools(loaded(['get_toc']), 'session-b', SETUP)).toBeUndefined()
    expect(loadedTools(loaded(['get_toc']), 'session-a', { ...SETUP, area: 'PING' })).toBeUndefined()
    expect(loadedTools(loaded(['get_toc']), 'session-a', { ...SETUP, playbook: 'oim-quick' })).toBeUndefined()
    expect(loadedTools(loaded(['get_toc']), 'session-a', { ...SETUP, mode: 'multi', playbook: null })).toBeUndefined()
    expect(loadedTools(loaded(['get_toc']), 'session-a', null)).toBeUndefined()
  })

  test('an entry the plugin did not write: no tools means nothing loaded, tools it cannot read grant nothing', () => {
    const { tools: _, ...withoutTools } = loaded(null)
    expect(loadedTools(withoutTools, 'session-a', SETUP)).toBeUndefined()
    for (const entry of [null, undefined, 'TEXT', { sessionId: 'session-a', tools: ['get_toc'] }, loaded(['get_toc'], { loadedFor: null })]) {
      expect(loadedTools(entry, 'session-a', SETUP)).toBeUndefined()
    }
    for (const tools of ['search_corpus', ['search_corpus', 3], 'oops']) {
      expect(loadedTools(loaded(tools), 'session-a', SETUP)).toBe('unreadable')
    }
  })
})

describe('declaredTools', () => {
  test('nothing loaded: the catalogue, as before', () => {
    expect(declaredTools(SETUP, CATALOGUE)).toEqual(['search_corpus'])
    expect(declaredTools(SETUP, CATALOGUE, undefined)).toEqual(['search_corpus'])
    expect(declaredTools({ ...SETUP, playbook: null }, CATALOGUE)).toBeNull()
  })

  test('what loaded decides, and the catalogue is not read', () => {
    expect(declaredTools(SETUP, CATALOGUE, ['get_toc'])).toEqual(['get_toc'])
    expect(declaredTools(SETUP, CATALOGUE, null)).toBeNull()
    expect(declaredTools(SETUP, null, ['get_toc'])).toEqual(['get_toc'])
    expect(declaredTools(SETUP, { ok: false, error: 'down' }, ['get_toc'])).toEqual(['get_toc'])
  })

  test('unreadable tools in what loaded: nothing can be granted', () => {
    expect(declaredTools(SETUP, CATALOGUE, 'unreadable')).toBeUndefined()
  })

  test('no setup: nothing can be granted, loaded or not', () => {
    expect(declaredTools(null, CATALOGUE)).toBeUndefined()
    expect(declaredTools(undefined, CATALOGUE, ['get_toc'])).toBeUndefined()
  })
})
