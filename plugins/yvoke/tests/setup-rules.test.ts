import { test, expect, describe } from 'claude-code/testing'
import {
  toCatalogue,
  defaults,
  pick,
  modesFor,
  lineFor,
  movedOn,
  type Catalogue,
  type Setup,
} from '../src/setup'

// P1-08: the setup band's rules, without the engine. The band, the lock and the server calls are in
// setup.test.ts.

const area = (over: Record<string, unknown> = {}) => ({
  name: 'OIM',
  title: 'OIM',
  description: 'Oracle Identity Manager',
  prototype: false,
  defaultSystemPrompt: 'oim-base',
  defaultPlaybook: 'oim-full',
  defaultProfile: 'OIM',
  systemPrompts: ['oim-base'],
  collections: ['oim-docs'],
  playbooks: ['oim-full', 'oim-quick'],
  profiles: ['OIM', 'OIM-deep'],
  ...over,
})

const playbook = (name: string, over: Record<string, unknown> = {}) => ({
  name,
  title: name,
  description: '',
  tools: [],
  codeExecution: true,
  targetAgent: null,
  prototype: false,
  area: 'OIM',
  systemPrompt: null,
  version: 1,
  ...over,
})

const AREAS = JSON.stringify([
  area(),
  area({ name: 'PING', title: 'PingID', defaultPlaybook: 'ping-main', defaultProfile: null, playbooks: ['ping-main', 'ping-other'], profiles: [] }),
])
const PLAYBOOKS = JSON.stringify([
  playbook('oim-quick', { version: 2 }),
  playbook('oim-full', { version: 3 }),
  playbook('ping-other', { area: 'PING' }),
  playbook('ping-main', { area: 'PING', version: 5 }),
])

const catalogue = (areas = AREAS, playbooks = PLAYBOOKS, showPrototypes = false): Catalogue => {
  const answer = toCatalogue(areas, playbooks, showPrototypes)
  if (!answer.ok) throw new Error(answer.error)
  return answer.catalogue
}

describe('toCatalogue', () => {
  test('areas with their playbooks, versions, profiles and defaults', () => {
    expect(catalogue()).toEqual({
      areas: [
        {
          name: 'OIM',
          title: 'OIM',
          playbooks: [
            { name: 'oim-quick', version: 2, tools: [] },
            { name: 'oim-full', version: 3, tools: [] },
          ],
          defaultPlaybook: 'oim-full',
          profiles: ['OIM', 'OIM-deep'],
          defaultProfile: 'OIM',
        },
        {
          name: 'PING',
          title: 'PingID',
          playbooks: [
            { name: 'ping-other', version: 1, tools: [] },
            { name: 'ping-main', version: 5, tools: [] },
          ],
          defaultPlaybook: 'ping-main',
          profiles: [],
          defaultProfile: null,
        },
      ],
    })
  })

  test('prototype areas and playbooks are hidden unless shown', () => {
    const areas = JSON.stringify([area(), area({ name: 'LAB', title: 'Lab', prototype: true })])
    const playbooks = JSON.stringify([playbook('oim-full', { version: 3 }), playbook('oim-new', { prototype: true })])

    expect(catalogue(areas, playbooks).areas.map((a) => a.name)).toEqual(['OIM'])
    expect(catalogue(areas, playbooks).areas[0]!.playbooks.map((p) => p.name)).toEqual(['oim-full'])
    expect(catalogue(areas, playbooks, true).areas.map((a) => a.name)).toEqual(['OIM', 'LAB'])
    expect(catalogue(areas, playbooks, true).areas[0]!.playbooks.map((p) => p.name)).toEqual(['oim-full', 'oim-new'])
  })

  test('a default that is not among the shown playbooks or profiles falls back to the first one', () => {
    const areas = JSON.stringify([area({ defaultPlaybook: 'oim-new', defaultProfile: 'gone' })])
    const playbooks = JSON.stringify([playbook('oim-new', { prototype: true }), playbook('oim-quick')])

    const [oim] = catalogue(areas, playbooks).areas
    expect(oim!.defaultPlaybook).toBe('oim-quick')
    expect(oim!.defaultProfile).toBe('OIM')
  })

  test('an area with no playbooks is kept, with no default', () => {
    const [, ping] = catalogue(AREAS, JSON.stringify([playbook('oim-full')])).areas
    expect(ping!.playbooks).toEqual([])
    expect(ping!.defaultPlaybook).toBeNull()
  })

  test('a playbook with no version counts as version 1', () => {
    const playbooks = JSON.stringify([{ ...playbook('oim-full'), version: undefined }])
    expect(catalogue(AREAS, playbooks).areas[0]!.playbooks).toEqual([{ name: 'oim-full', version: 1, tools: [] }])
  })

  // P2-02: the gate scopes the session to the tools its playbook declares.
  test("a playbook's declared tools are kept, anything that is not a name dropped", () => {
    const playbooks = JSON.stringify([playbook('oim-full', { tools: ['search_corpus', 3, null, 'WebSearch'] })])
    expect(catalogue(AREAS, playbooks).areas[0]!.playbooks[0]!.tools).toEqual(['search_corpus', 'WebSearch'])
  })

  test('a playbook that declares no tools, or not a list, has null', () => {
    const playbooks = JSON.stringify([
      { ...playbook('oim-full'), tools: undefined },
      playbook('oim-quick', { tools: 'search_corpus' }),
    ])
    expect(catalogue(AREAS, playbooks).areas[0]!.playbooks.map((p) => p.tools)).toEqual([null, null])
  })

  test('a server with no areas is an error', () => {
    expect(toCatalogue('[]', PLAYBOOKS, false)).toEqual({ ok: false, error: 'Yvoke Backend: the server has no areas.' })
    expect(toCatalogue(JSON.stringify([area({ prototype: true })]), PLAYBOOKS, false)).toEqual({
      ok: false,
      error: 'Yvoke Backend: the server has no areas.',
    })
  })

  test('an answer that is not the expected JSON is an error naming the tool', () => {
    expect(toCatalogue('not json', PLAYBOOKS, false)).toEqual({
      ok: false,
      error: 'Yvoke Backend: list_areas gave an answer the mod cannot read.',
    })
    expect(toCatalogue('{"a":1}', PLAYBOOKS, false)).toEqual({
      ok: false,
      error: 'Yvoke Backend: list_areas gave an answer the mod cannot read.',
    })
    expect(toCatalogue(AREAS, '[1]', false)).toEqual({
      ok: false,
      error: 'Yvoke Backend: list_playbooks gave an answer the mod cannot read.',
    })
  })
})

describe('defaults', () => {
  test('a new session: OIM, single agent, the area default playbook with its version', () => {
    expect(defaults(catalogue())).toEqual({
      area: 'OIM',
      mode: 'single',
      playbook: 'oim-full',
      version: 3,
      profile: null,
      lockedSessionId: null,
    })
  })

  test('with no OIM, the first area', () => {
    const areas = JSON.stringify([area({ name: 'PING', title: 'PingID', defaultPlaybook: 'ping-main' })])
    expect(defaults(catalogue(areas))).toMatchObject({ area: 'PING', playbook: 'ping-main', version: 5 })
  })

  test('the defaults of a named area', () => {
    expect(defaults(catalogue(), 'PING')).toMatchObject({ area: 'PING', playbook: 'ping-main', version: 5 })
  })
})

describe('pick', () => {
  const cat = catalogue()
  const start = defaults(cat)

  test('another playbook, with its version', () => {
    expect(pick(start, cat, 'playbook', 'oim-quick', false)).toEqual({ ...start, playbook: 'oim-quick', version: 2 })
  })

  test('another area resets mode, playbook and profile to its defaults', () => {
    const multi = pick(start, cat, 'mode', 'multi', true)
    expect(pick(multi, cat, 'area', 'PING', true)).toEqual({
      area: 'PING',
      mode: 'single',
      playbook: 'ping-main',
      version: 5,
      profile: null,
      lockedSessionId: null,
    })
    const quick = pick(start, cat, 'playbook', 'oim-quick', false)
    expect(pick(pick(quick, cat, 'area', 'PING', false), cat, 'area', 'OIM', false)).toEqual(start)
  })

  test('switching to multi-agent selects the area default profile; back to single, the default playbook', () => {
    const multi = pick(start, cat, 'mode', 'multi', true)
    expect(multi).toEqual({ ...start, mode: 'multi', playbook: null, version: null, profile: 'OIM' })
    expect(pick(multi, cat, 'profile', 'OIM-deep', true)).toMatchObject({ profile: 'OIM-deep' })
    expect(pick(multi, cat, 'mode', 'single', true)).toEqual(start)
  })

  test('multi-agent cannot be picked while it is not offered', () => {
    expect(pick(start, cat, 'mode', 'multi', false)).toEqual(start)
    const ping = defaults(cat, 'PING')
    expect(pick(ping, cat, 'mode', 'multi', true)).toEqual(ping)
  })

  test('an unknown value changes nothing', () => {
    expect(pick(start, cat, 'area', 'NOPE', false)).toEqual(start)
    expect(pick(start, cat, 'playbook', 'ping-main', false)).toEqual(start)
    expect(pick(start, cat, 'profile', 'OIM', true)).toEqual(start)
  })

  test('a locked setup changes for nothing', () => {
    const locked: Setup = { ...start, lockedSessionId: 'session-a' }
    expect(pick(locked, cat, 'playbook', 'oim-quick', false)).toEqual(locked)
    expect(pick(locked, cat, 'area', 'PING', false)).toEqual(locked)
  })
})

describe('modesFor', () => {
  test('single agent only while multi-agent is not built', () => {
    expect(modesFor(catalogue(), 'OIM', false)).toEqual(['single'])
  })

  test('multi-agent once built, but never for an area with no profiles', () => {
    expect(modesFor(catalogue(), 'OIM', true)).toEqual(['single', 'multi'])
    expect(modesFor(catalogue(), 'PING', true)).toEqual(['single'])
  })
})

describe('lineFor', () => {
  test('area, mode and playbook with its version', () => {
    expect(lineFor(defaults(catalogue()), catalogue())).toBe('OIM · Single agent · oim-full v3')
    expect(lineFor(defaults(catalogue(), 'PING'), catalogue())).toBe('PingID · Single agent · ping-main v5')
  })

  test('multi-agent shows the profile', () => {
    const multi = pick(defaults(catalogue()), catalogue(), 'mode', 'multi', true)
    expect(lineFor(multi, catalogue())).toBe('OIM · Multi-agent · OIM')
  })

  test('the version it was locked with, even when the server has moved on', () => {
    const locked: Setup = { ...defaults(catalogue()), lockedSessionId: 'session-a' }
    const newer = catalogue(AREAS, JSON.stringify([playbook('oim-full', { version: 4 })]))
    expect(lineFor(locked, newer)).toBe('OIM · Single agent · oim-full v3')
  })

  test('without the catalogue, the area name', () => {
    expect(lineFor(defaults(catalogue()), null)).toBe('OIM · Single agent · oim-full v3')
  })
})

describe('movedOn (D-20)', () => {
  const locked: Setup = { ...defaults(catalogue()), lockedSessionId: 'session-a' }

  test('a newer version on the server: the setup takes it, naming both', () => {
    const newer = catalogue(AREAS, JSON.stringify([playbook('oim-full', { version: 4 })]))
    expect(movedOn(locked, newer)).toEqual({ setup: { ...locked, version: 4 }, from: 3, to: 4 })
  })

  test('the same version, a missing playbook, or multi-agent: nothing', () => {
    expect(movedOn(locked, catalogue())).toBeNull()
    expect(movedOn(locked, catalogue(AREAS, JSON.stringify([playbook('oim-quick')])))).toBeNull()
    const multi = { ...pick(defaults(catalogue()), catalogue(), 'mode', 'multi', true), lockedSessionId: 'x' }
    expect(movedOn(multi, catalogue())).toBeNull()
  })
})
