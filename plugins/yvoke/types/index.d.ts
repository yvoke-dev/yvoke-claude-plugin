// The yvoke mod's $.state contract. `claude plugin validate` holds every $.state key the module names to
// this file. It reads the keys only where they are written inline in PluginState, and the file may not
// import anything, so YvokeState repeats them for code that wants the type by name.
//
// yvokeSession: true in a session the user started with /yvoke (P1-10). Written only by src/session.ts.
// setup: the area, mode and playbook the user picked (P1-08 writes it); unset means the defaults.
// loopStep: per loop (`id`: the subagent's id, or `main`), the turn and request it stands at and its
//   ceiling, or null when its ceiling is off (P2-08). Written only by src/turn-limits.ts.
export type YvokeState = {
  yvokeSession: boolean
  setup: { area: string; mode: string; playbook: string | null } | null
  loopStep: { turnId: string; index: number; n: number } | null
}

declare module 'claude-code' {
  interface PluginState {
    yvoke: {
      yvokeSession: boolean
      setup: { area: string; mode: string; playbook: string | null } | null
      loopStep: StateFamily<{ turnId: string; index: number; n: number } | null>
    }
  }
}
