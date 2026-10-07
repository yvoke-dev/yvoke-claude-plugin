// The yvoke mod's $.state contract. `claude plugin validate` holds every $.state key the module names to
// this file. It reads the keys only where they are written inline in PluginState, and the file may not
// import anything, so YvokeState repeats them for code that wants the type by name.
//
// yvokeSession: true in a session the user started with /yvoke (P1-10). Written only by src/session.ts.
// instructions: the base instructions and playbook text loaded for one session id (P1-07). Written only
//   by src/system-prompt.ts; P1-08 reads it as "this session's setup is locked".
// setup: the area, mode and playbook the user picked (P1-08 writes it); unset means the defaults.
export type YvokeState = {
  yvokeSession: boolean
  instructions: { sessionId: string; text: string } | null
  setup: { area: string; mode: string; playbook: string | null } | null
}

declare module 'claude-code' {
  interface PluginState {
    yvoke: {
      yvokeSession: boolean
      instructions: { sessionId: string; text: string } | null
      setup: { area: string; mode: string; playbook: string | null } | null
    }
  }
}
