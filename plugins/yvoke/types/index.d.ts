// The yvoke mod's $.state contract. `claude plugin validate` holds every $.state key the module names to
// this file. It reads the keys only where they are written inline in PluginState, and the file may not
// import anything, so YvokeState repeats them for code that wants the type by name.
//
// yvokeSession: true in a session the user started with /yvoke (P1-10). Written only by src/session.ts.
export type YvokeState = { yvokeSession: boolean }

declare module 'claude-code' {
  interface PluginState {
    yvoke: { yvokeSession: boolean }
  }
}
