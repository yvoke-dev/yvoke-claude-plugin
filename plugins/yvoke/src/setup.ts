// P1-08, D-11, D-18, D-20: the session setup band. In a Yvoke session the band above the prompt picks the
// area first, then the mode, then the playbook (single agent) or the profile (multi-agent), each defaulting
// to what the area names. The choice locks when the first question enters the session.
//
// This part holds the rules, with no `$` in it (a function from another file cannot take `$`), so they can
// be tested without the engine.

/** Multi-agent mode is offered once Phase 6 is built. Until then only single agent runs. */
export const MULTI_AGENT_READY = false

export type Mode = 'single' | 'multi'
export type PlaybookEntry = { name: string; version: number }
export type AreaEntry = {
  name: string
  title: string
  playbooks: PlaybookEntry[]
  defaultPlaybook: string | null
  profiles: string[]
  defaultProfile: string | null
}
export type Catalogue = { areas: AreaEntry[] }

/**
 * The session's choice. `playbook` and `version` are null in multi-agent mode, `profile` in single agent.
 * `lockedSessionId` is the id of the session the setup locked in, null before the first question.
 */
export type Setup = {
  area: string
  mode: Mode
  playbook: string | null
  version: number | null
  profile: string | null
  lockedSessionId: string | null
}

export type Select = 'area' | 'mode' | 'playbook' | 'profile'

const DEFAULT_AREA = 'OIM'
const unreadable = (tool: string) => `Yvoke Backend: ${tool} gave an answer the mod cannot read.`

type RawArea = {
  name: string
  title?: unknown
  prototype?: unknown
  defaultPlaybook?: unknown
  defaultProfile?: unknown
  profiles?: unknown
}
type RawPlaybook = { name: string; area?: unknown; prototype?: unknown; version?: unknown }

const objectsWithName = (text: string): Record<string, unknown>[] | null => {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return null
  }
  if (!Array.isArray(parsed)) return null
  const named = (x: unknown) => typeof x === 'object' && x !== null && typeof (x as { name?: unknown }).name === 'string'
  return parsed.every(named) ? (parsed as Record<string, unknown>[]) : null
}

const strings = (x: unknown) => (Array.isArray(x) ? x.filter((s): s is string => typeof s === 'string') : [])
const orFirst = (wanted: unknown, names: string[]) =>
  typeof wanted === 'string' && names.includes(wanted) ? wanted : (names[0] ?? null)

/** The answers of `list_areas` and `list_playbooks` as the band shows them. */
export const toCatalogue = (
  areasText: string,
  playbooksText: string,
  showPrototypes: boolean,
): { ok: true; catalogue: Catalogue } | { ok: false; error: string } => {
  const rawAreas = objectsWithName(areasText) as RawArea[] | null
  if (!rawAreas) return { ok: false, error: unreadable('list_areas') }
  const rawPlaybooks = objectsWithName(playbooksText) as RawPlaybook[] | null
  if (!rawPlaybooks) return { ok: false, error: unreadable('list_playbooks') }

  const shown = (item: { prototype?: unknown }) => showPrototypes || item.prototype !== true
  const areas = rawAreas.filter(shown).map((a): AreaEntry => {
    const playbooks = rawPlaybooks
      .filter((p) => p.area === a.name && shown(p))
      .map((p) => ({ name: p.name, version: typeof p.version === 'number' ? p.version : 1 }))
    const profiles = strings(a.profiles)
    return {
      name: a.name,
      title: typeof a.title === 'string' && a.title !== '' ? a.title : a.name,
      playbooks,
      defaultPlaybook: orFirst(a.defaultPlaybook, playbooks.map((p) => p.name)),
      profiles,
      defaultProfile: orFirst(a.defaultProfile, profiles),
    }
  })
  if (areas.length === 0) return { ok: false, error: 'Yvoke Backend: the server has no areas.' }
  return { ok: true, catalogue: { areas } }
}

const areaOf = (catalogue: Catalogue, name: string) => catalogue.areas.find((a) => a.name === name)

/** A new session's setup, or the setup after the area changed: single agent with the area's default playbook. */
export const defaults = (catalogue: Catalogue, areaName?: string): Setup => {
  const area =
    (areaName === undefined ? undefined : areaOf(catalogue, areaName)) ??
    areaOf(catalogue, DEFAULT_AREA) ??
    catalogue.areas[0]
  const playbook = area.playbooks.find((p) => p.name === area.defaultPlaybook) ?? null
  return {
    area: area.name,
    mode: 'single',
    playbook: playbook?.name ?? null,
    version: playbook?.version ?? null,
    profile: null,
    lockedSessionId: null,
  }
}

/** The modes an area offers: single agent, and multi-agent once built when the area has profiles. */
export const modesFor = (catalogue: Catalogue, areaName: string, multiAgent: boolean): Mode[] =>
  multiAgent && (areaOf(catalogue, areaName)?.profiles.length ?? 0) > 0 ? ['single', 'multi'] : ['single']

/** The setup after the user picked `value` in one select. A locked setup, or a value not offered, changes nothing. */
export const pick = (setup: Setup, catalogue: Catalogue, select: Select, value: string, multiAgent: boolean): Setup => {
  if (setup.lockedSessionId !== null) return setup
  const area = areaOf(catalogue, setup.area)
  if (!area) return setup
  if (select === 'area') return areaOf(catalogue, value) ? defaults(catalogue, value) : setup
  if (select === 'mode') {
    if (value === 'single') return setup.mode === 'single' ? setup : defaults(catalogue, area.name)
    if (value === 'multi' && modesFor(catalogue, area.name, multiAgent).includes('multi')) {
      return { ...setup, mode: 'multi', playbook: null, version: null, profile: area.defaultProfile }
    }
    return setup
  }
  if (select === 'playbook' && setup.mode === 'single') {
    const playbook = area.playbooks.find((p) => p.name === value)
    return playbook ? { ...setup, playbook: playbook.name, version: playbook.version } : setup
  }
  if (select === 'profile' && setup.mode === 'multi') {
    return area.profiles.includes(value) ? { ...setup, profile: value } : setup
  }
  return setup
}

export const MODE_LABEL: Record<Mode, string> = { single: 'Single agent', multi: 'Multi-agent' }

/** A playbook as the band shows it: `oim-full v3`. */
export const playbookLabel = (name: string, version: number | null) => (version === null ? name : `${name} v${version}`)

/** The read-only line: `OIM · Single agent · oim-full v3`. The version is the setup's own, not the server's. */
export const lineFor = (setup: Setup, catalogue: Catalogue | null) => {
  const title = (catalogue && areaOf(catalogue, setup.area)?.title) || setup.area
  const what = setup.mode === 'single' ? playbookLabel(setup.playbook ?? '—', setup.version) : (setup.profile ?? '—')
  return `${title} · ${MODE_LABEL[setup.mode]} · ${what}`
}

/**
 * D-20: after /resume, a locked single-agent setup takes the version the server now has for its playbook.
 * Null when nothing moved, the playbook is gone (its fetch then fails on the next question), or no
 * playbook applies.
 */
export const movedOn = (setup: Setup, catalogue: Catalogue): { setup: Setup; from: number | null; to: number } | null => {
  if (setup.mode !== 'single' || setup.playbook === null) return null
  const current = areaOf(catalogue, setup.area)?.playbooks.find((p) => p.name === setup.playbook)
  if (!current || current.version === setup.version) return null
  return { setup: { ...setup, version: current.version }, from: setup.version, to: current.version }
}
