// The yvoke mod's $.state contract. `claude plugin validate` holds every $.state key the module names to
// this file. It reads the keys only where they are written inline in PluginState, and the file may not
// import anything, so YvokeState repeats them for code that wants the type by name.
//
// yvokeSession: true in a session the user started with /yvoke (P1-10). Written only by src/session.ts.
// instructions: the base instructions and playbook text loaded for one session id (P1-07). Written only
//   by src/system-prompt.ts.
// setup: the area, mode, playbook (with its version) or profile the session runs on, and the id of the
//   session it locked in, null before the first question (P1-08). Written only by src/setup.ts; P1-07
//   reads area, mode and playbook.
// catalogue: the areas and playbooks the band offers, or why they could not be loaded (P1-08). A playbook's
//   `tools` are the names it declares, null when it declares none (an empty list), 'unreadable' when the
//   server sent something else; the tool gate reads them (P2-02).
// loopStep: a family, one member per loop (`id`: the subagent's id, or `main`): the turn and request it
//   stands at and its ceiling, or null when its ceiling is off (P2-08). Written only by
//   src/turn-limits.ts. YvokeState gives the member's value, which is what `$.state.get` with an `id`
//   answers; PluginState marks the key as a family.
export type YvokeSetup = {
  area: string
  mode: 'single' | 'multi'
  playbook: string | null
  version: number | null
  profile: string | null
  lockedSessionId: string | null
}
export type YvokeCatalogue =
  | {
      ok: true
      catalogue: {
        areas: {
          name: string
          title: string
          playbooks: { name: string; version: number; tools: string[] | null | 'unreadable' }[]
          defaultPlaybook: string | null
          profiles: string[]
          defaultProfile: string | null
        }[]
      }
    }
  | { ok: false; error: string }

export type YvokeState = {
  yvokeSession: boolean
  instructions: { sessionId: string; text: string } | null
  setup: YvokeSetup | null
  catalogue: YvokeCatalogue | null
  loopStep: { turnId: string; index: number; n: number } | null
}

declare module 'claude-code' {
  interface PluginState {
    yvoke: {
      yvokeSession: boolean
      instructions: { sessionId: string; text: string } | null
      setup: YvokeSetup | null
      catalogue: YvokeCatalogue | null
      loopStep: StateFamily<{ turnId: string; index: number; n: number } | null>
    }
  }
}
