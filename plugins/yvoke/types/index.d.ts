// The yvoke mod's $.state contract. `claude plugin validate` holds every $.state key the module names to
// this file. It reads the keys only where they are written inline in PluginState, and the file may not
// import anything, so YvokeState repeats them for code that wants the type by name.
//
// yvokeSession: true in a session the user started with /yvoke (session.md). Written only by src/session.ts.
// instructions: the base instructions and playbook text loaded for one session id, and what they were
//   loaded for: the area and mode, the playbook loaded and the version get_playbook gave, both null in
//   multi-agent mode, the version null when the server sent none (system-prompt.md). Written only by
//   src/system-prompt.ts; src/setup.ts reads it to lock the setup with the version that loaded.
// setup: the area, mode, playbook (with its version) or profile the session runs on, and the id of the
//   session it locked in, null before the first question (setup.md). Written only by src/setup.ts;
//   src/system-prompt.ts reads area, mode, playbook and version, and lockedSessionId to refuse a locked
//   turn that has no instructions.
// catalogue: the areas and playbooks the band offers, or why they could not be loaded (setup.md). A
//   playbook's `tools` are the names it declares, null when it declares none (an empty list or no field),
//   'unreadable' when the server sent something else; the tool gate reads them (tool-policy.md).
// loopStep: a family, one member per loop (`id`: the subagent's id, or `main`): the turn and request it
//   stands at and its ceiling, or null when its ceiling is off (turn-limits.md). Written only by
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
  instructions: {
    sessionId: string
    text: string
    loadedFor: { area: string; mode: string; playbook: string | null; version: number | null }
  } | null
  setup: YvokeSetup | null
  catalogue: YvokeCatalogue | null
  loopStep: { turnId: string; index: number; n: number } | null
}

declare module 'claude-code' {
  interface PluginState {
    yvoke: {
      yvokeSession: boolean
      instructions: {
        sessionId: string
        text: string
        loadedFor: { area: string; mode: string; playbook: string | null; version: number | null }
      } | null
      setup: YvokeSetup | null
      catalogue: YvokeCatalogue | null
      loopStep: StateFamily<{ turnId: string; index: number; n: number } | null>
    }
  }
}
