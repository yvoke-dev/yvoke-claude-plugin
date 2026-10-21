// P1-08, D-11, D-18, D-20: the session setup band. In a Yvoke session the band above the prompt picks the
// area first, then the mode, then the playbook (single agent) or the profile (multi-agent), each defaulting
// to what the area names. The choice locks when the first question enters the session.
//
// This part holds the rules, with no `$` in it (a function from another file cannot take `$`), so they can
// be tested without the engine.
import type { ElementConstructor, On, PluginOptions, RenderElement, SelectProps } from 'claude-code'
import { callYvoke, type ServerIo } from './server'

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
    // toCatalogue never gives a catalogue with no areas.
    catalogue.areas[0]!
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

// ---------------------------------------------------------------------------------------------------------
// The hooks. Every $.state key is written inline (the engine refuses a reference imported from another
// file), and every enforcing hook opens with the Yvoke check (design 4.4).

export const LOADING = 'Loading the Yvoke setup…'
export const DROP_FAILED = 'Yvoke Backend: the session could not be set up, so the question was not sent.'

/** The `$.store` key holding one session's setup, next to P1-10's `session:<id>`. */
export const setupKey = (sessionId: string) => `setup:${sessionId}`

export type CatalogueState = { ok: true; catalogue: Catalogue } | { ok: false; error: string }

const isSetup = (x: unknown): x is Setup => {
  if (typeof x !== 'object' || x === null) return false
  const s = x as Record<string, unknown>
  const nullOr = (v: unknown, t: string) => v === null || typeof v === t
  return (
    typeof s.area === 'string' &&
    (s.mode === 'single' || s.mode === 'multi') &&
    nullOr(s.playbook, 'string') &&
    nullOr(s.version, 'number') &&
    nullOr(s.profile, 'string') &&
    nullOr(s.lockedSessionId, 'string')
  )
}

/** The setup to show for a catalogue: the saved picks when they still fit it, else the defaults. */
const fitted = (saved: Setup | null | undefined, catalogue: Catalogue): Setup => {
  if (!saved) return defaults(catalogue)
  if (saved.lockedSessionId !== null) return saved
  return catalogue.areas.some((a) => a.name === saved.area) ? saved : defaults(catalogue)
}

const noPlaybooks = (area: string) => `Yvoke Backend: the area ${area} has no playbooks, so the question was not sent.`

export function registerSetup(on: On, options: PluginOptions) {
  const showPrototypes = options.showPrototypes === true

  // Loads both lists, writes the catalogue and the setup it implies, and returns the catalogue state.
  // Written as a hook-local closure each time it is needed would repeat it four times; the engine lets a
  // same-file function take closures, so the hooks hand it `io` and the state writers.
  const load = async (
    io: ServerIo,
    getSetup: () => Promise<Setup | null | undefined>,
    write: (catalogue: CatalogueState, setup: Setup | null) => Promise<void>,
  ): Promise<CatalogueState> => {
    const [areas, playbooks] = await Promise.all([callYvoke(io, 'list_areas'), callYvoke(io, 'list_playbooks')])
    const answer: CatalogueState = !areas.ok
      ? { ok: false, error: areas.error }
      : !playbooks.ok
        ? { ok: false, error: playbooks.error }
        : toCatalogue(areas.text, playbooks.text, showPrototypes)
    await write(answer, answer.ok ? fitted(await getSetup(), answer.catalogue) : ((await getSetup()) ?? null))
    return answer
  }

  on('command.run', { command: 'yvoke' }, async ($, e, next) => {
    const answer = await next(e)
    const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
    if (isYvoke !== true) return answer
    const { value: had } = await $.state.get({ plugin: 'yvoke', key: 'catalogue' })
    if (had) return answer
    const io: ServerIo = {
      connect: (server) => $.mcp.connect(server),
      call: (server, tool, args) => $.mcp.call(server, tool, args),
      sleep: (ms, signal) => $.clock.sleep(ms, { signal }),
    }
    await load(
      io,
      async () => (await $.state.get({ plugin: 'yvoke', key: 'setup' })).value,
      async (catalogue, setup) => {
        await $.state.set({ plugin: 'yvoke', key: 'catalogue' }, catalogue)
        await $.state.set({ plugin: 'yvoke', key: 'setup' }, setup)
      },
    )
    return answer
  })

  on('classic.SessionStart', { source: /^(clear|resume|fork)$/ }, async ($, e, next) => {
    // The session's hook beneath decides whether this is a Yvoke session (P1-10); its answer comes first.
    const started = await next(e)
    // The status line is one per plugin and stays until replaced: clear it for every new session, and let
    // a locked setup set it again below.
    await $.ui.status(undefined)
    if (e.source === 'clear') return started
    let saved: Setup | null = null
    if (e.source === 'resume') {
      // As P1-10: nothing first, so a store read that fails leaves the resumed session without a setup.
      await $.state.set({ plugin: 'yvoke', key: 'setup' }, null)
      await $.state.set({ plugin: 'yvoke', key: 'catalogue' }, null)
    } else {
      saved = (await $.state.get({ plugin: 'yvoke', key: 'setup' })).value ?? null
    }
    const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
    if (isYvoke !== true) return started
    if (e.source === 'resume') {
      const stored = await $.store.get(setupKey(e.session_id))
      saved = isSetup(stored) ? stored : null
    }
    // The setup belongs to the session it is in now: /branch gives it a new id.
    if (saved && saved.lockedSessionId !== null) saved = { ...saved, lockedSessionId: e.session_id }
    await $.state.set({ plugin: 'yvoke', key: 'setup' }, saved)
    if (saved) await $.store.set(setupKey(e.session_id), saved)

    const io: ServerIo = {
      connect: (server) => $.mcp.connect(server),
      call: (server, tool, args) => $.mcp.call(server, tool, args),
      sleep: (ms, signal) => $.clock.sleep(ms, { signal }),
    }
    const catalogue = await load(
      io,
      async () => saved,
      async (c, setup) => {
        await $.state.set({ plugin: 'yvoke', key: 'catalogue' }, c)
        await $.state.set({ plugin: 'yvoke', key: 'setup' }, setup)
      },
    )
    if (saved && saved.lockedSessionId !== null) {
      let shown = saved
      // D-20: a resumed or branched session carries on with the playbook version the server has now. Both
      // have a new session id to P1-07, so their first question fetches the server's current text.
      const moved = catalogue.ok ? movedOn(saved, catalogue.catalogue) : null
      if (moved) {
        shown = moved.setup
        await $.state.set({ plugin: 'yvoke', key: 'setup' }, shown)
        await $.store.set(setupKey(e.session_id), shown)
        await $.ui.toast(
          `${shown.playbook} moved from v${moved.from} to v${moved.to} since this session started; ` +
            `answers from now on use v${moved.to}.`,
        )
      }
      await $.ui.status(lineFor(shown, catalogue.ok ? catalogue.catalogue : null))
    }
    return started
  })

  // The matcher takes every prompt. It is there because the engine refuses a second hook on one event
  // with no matcher in the same plugin, and P1-07's prompt.submit has none.
  on('prompt.submit', { text: /^/ }, async ($, e, next) => {
    const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
    if (isYvoke !== true) return next(e)

    const sessionId = await $.session.id()
    let { value: setup } = await $.state.get({ plugin: 'yvoke', key: 'setup' })
    if (setup && setup.lockedSessionId === sessionId) return next(e)

    let { value: catalogue } = await $.state.get({ plugin: 'yvoke', key: 'catalogue' })
    if (!catalogue || !catalogue.ok) {
      const io: ServerIo = {
        connect: (server) => $.mcp.connect(server),
        call: (server, tool, args) => $.mcp.call(server, tool, args),
        sleep: (ms, signal) => $.clock.sleep(ms, { signal }),
      }
      catalogue = await load(
        io,
        async () => setup,
        async (c, s) => {
          await $.state.set({ plugin: 'yvoke', key: 'catalogue' }, c)
          await $.state.set({ plugin: 'yvoke', key: 'setup' }, s)
        },
      )
      setup = (await $.state.get({ plugin: 'yvoke', key: 'setup' })).value
    }
    if (!catalogue.ok) return { drop: catalogue.error }
    const chosen = fitted(setup, catalogue.catalogue)
    if (chosen.mode === 'single' && chosen.playbook === null) return { drop: noPlaybooks(chosen.area) }

    // Saved before the question goes down, so P1-07's hook reads this setup whichever side of it runs.
    await $.state.set({ plugin: 'yvoke', key: 'setup' }, chosen)
    await $.store.set(setupKey(sessionId), chosen)
    const entered = await next(e)
    if (entered.drop !== undefined) return entered

    // The question has entered: nothing from here on may turn it into a drop, which would tell the user
    // it was not sent. A lock that cannot be saved still holds for this session; only /resume misses it.
    const locked: Setup = { ...chosen, lockedSessionId: sessionId }
    await $.state.set({ plugin: 'yvoke', key: 'setup' }, locked)
    try {
      await $.ui.status(lineFor(locked, catalogue.catalogue))
      await $.store.set(setupKey(sessionId), locked)
    } catch {
      // As above.
    }
    return entered
  // Once the question has gone down, pass on what it settled to: a drop would say it was not sent.
  }).catch((_$, e, next) => (next.called ? next(e) : { drop: DROP_FAILED }))

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next): Promise<RenderElement> => {
    const { value: isYvoke } = await $.state.get({ plugin: 'yvoke', key: 'yvokeSession' })
    if (isYvoke !== true || e.props.hasSurvey) return next(e)
    const { Box, Text, Button, Select } = $.ui.resolve(e) as ReturnType<typeof $.ui.resolve> & {
      Select?: ElementConstructor<SelectProps>
    }
    const { value: catalogue } = await $.state.get({ plugin: 'yvoke', key: 'catalogue' })
    const { value: setup } = await $.state.get({ plugin: 'yvoke', key: 'setup' })
    const sessionId = await $.session.id()

    if (setup && setup.lockedSessionId === sessionId) {
      return h(Text, {}, lineFor(setup, catalogue?.ok ? catalogue.catalogue : null)) as RenderElement
    }
    if (!catalogue) return h(Text, { dimColor: true }, LOADING) as RenderElement
    if (!catalogue.ok) {
      const retry = async () => {
        const io: ServerIo = {
          connect: (server) => $.mcp.connect(server),
          call: (server, tool, args) => $.mcp.call(server, tool, args),
          sleep: (ms, signal) => $.clock.sleep(ms, { signal }),
        }
        await load(
          io,
          async () => (await $.state.get({ plugin: 'yvoke', key: 'setup' })).value,
          async (c, s) => {
            await $.state.set({ plugin: 'yvoke', key: 'catalogue' }, c)
            await $.state.set({ plugin: 'yvoke', key: 'setup' }, s)
          },
        )
      }
      return h(
        Box,
        { gap: 1 },
        h(Text, { color: 'red' }, catalogue.error),
        h(Button, { key: 'yvoke-retry', label: 'Retry', onPress: retry }),
      ) as RenderElement
    }

    const cat = catalogue.catalogue
    const current = fitted(setup, cat)
    if (!Select) return h(Text, {}, lineFor(current, cat)) as RenderElement
    const choose = (select: Select) => async (value: string) => {
      const { value: now } = await $.state.get({ plugin: 'yvoke', key: 'setup' })
      // pick changes nothing once the setup is locked.
      const after = pick(fitted(now, cat), cat, select, value, MULTI_AGENT_READY)
      await $.state.set({ plugin: 'yvoke', key: 'setup' }, after)
      try {
        await $.store.set(setupKey(await $.session.id()), after)
      } catch {
        // The pick stands for this session; only /resume of an unlocked setup would miss it.
      }
    }
    const area = cat.areas.find((a) => a.name === current.area) ?? cat.areas[0]!
    const third =
      current.mode === 'multi'
        ? h(Select, {
            key: 'yvoke-profile',
            label: 'Profile',
            options: area.profiles.map((p) => ({ value: p, label: p })),
            value: current.profile ?? undefined,
            onSelect: choose('profile'),
          })
        : area.playbooks.length === 0
          ? h(Text, { color: 'red' }, `The area ${area.title} has no playbooks.`)
          : h(Select, {
              key: 'yvoke-playbook',
              label: 'Playbook',
              options: area.playbooks.map((p) => ({ value: p.name, label: playbookLabel(p.name, p.version) })),
              value: current.playbook ?? undefined,
              onSelect: choose('playbook'),
            })
    return h(
      Box,
      { gap: 2 },
      h(Select, {
        key: 'yvoke-area',
        label: 'Area',
        options: cat.areas.map((a) => ({ value: a.name, label: a.title })),
        value: current.area,
        onSelect: choose('area'),
      }),
      h(Select, {
        key: 'yvoke-mode',
        label: 'Mode',
        options: modesFor(cat, current.area, MULTI_AGENT_READY).map((m) => ({ value: m, label: MODE_LABEL[m] })),
        value: current.mode,
        onSelect: choose('mode'),
      }),
      third,
    ) as RenderElement
  })
}
