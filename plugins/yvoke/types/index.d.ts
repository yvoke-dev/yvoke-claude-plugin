// The yvoke mod's $.state contract. `claude plugin validate` holds every $.state key the module names to
// this file. Empty until a task stores a session value (P1-10 adds the Yvoke-session flag).
export type YvokeState = Record<string, never>

declare module 'claude-code' {
  interface PluginState {
    yvoke: YvokeState
  }
}
